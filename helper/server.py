"""Local HTTP helper the n8n workflow calls: screen, rank, approval pages and workbook build."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests
from flask import Flask, jsonify, render_template_string, request, send_file

from helper import excel
from helper.models import (Commentary, Criteria, Exclusion, PeerRow, Profile, SourcesInfo,
                           UniverseRow, profile_from_dict, profile_to_dict)
from helper.screen import (UnsupportedTarget, apply_filters, auto_criteria, check_target_supported,
                           industry_candidates, normalize_ticker, size_review_candidates)
from helper.scoring import BadLlmOutput, decide, validate_llm_output
from helper.score_proxy import GroqReply, ScoreProxy
from helper.stats import quartiles
from helper.store import ApprovalStore

log = logging.getLogger(__name__)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_RANGE_LABELS = (("market_cap_cr", "market cap (Rs cr)"), ("revenue_cr", "revenue (Rs cr)"),
                 ("ebitda_margin", "EBITDA margin"), ("revenue_cagr_3y", "3y revenue CAGR"),
                 ("roce", "ROCE"), ("insider_pct", "insider holding"))

PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Approve peers</title>
<body style="font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem">
{% if message %}<h2>{{ message }}</h2>{% else %}
<h2>Choose peers for {{ target.name or target.symbol }}</h2>
<p>Untick any company you do not want in the comps. Companies marked "needs your review" are borderline matches and start unticked: tick the ones you want. At least one must be ticked.</p>
<form method="post">
{% for p in shortlist %}
<label style="display:block;margin:.8rem 0"><input type="checkbox" name="peer" value="{{ p.profile.symbol }}"{% if p.tier != "review" %} checked{% endif %}>
<b>{{ p.profile.name or p.profile.symbol }}</b> ({{ p.profile.symbol }}) similarity {{ p.similarity }}{% if p.tier == "review" %} <em>needs your review{% if p.profile.size_review %} (smaller than the usual size range){% endif %}</em>{% endif %}<br>
<small>{{ p.reason }}{% if p.mismatch_note %} Differences: {{ p.mismatch_note }}{% endif %}</small></label>
{% endfor %}
<button type="submit" style="margin-top:1rem;padding:.6rem 1.2rem">Build my comps</button></form>{% endif %}
</body></html>"""


def _criteria_lines(c: Criteria) -> list[str]:
    lines = []
    for attr, label in _RANGE_LABELS:
        r = getattr(c, attr)
        if r.is_active:
            lines.append(f"{label}: {r.lo if r.lo is not None else 'any'} to {r.hi if r.hi is not None else 'any'}")
    if c.max_net_debt_ebitda is not None:
        lines.append(f"net debt / EBITDA at most {c.max_net_debt_ebitda}x")
    if c.min_daily_value_cr is not None:
        lines.append(f"average daily traded value at least Rs {c.min_daily_value_cr} cr")
    lines.append(f"listed at least {c.min_listing_years} years")
    if c.include:
        lines.append("always included: " + ", ".join(c.include))
    if c.exclude:
        lines.append("always excluded: " + ", ".join(c.exclude))
    return lines


def _post_resume(url: str, data: dict) -> None:
    requests.post(url, json=data, timeout=10).raise_for_status()


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _groq_upstream(payload: dict, auth: str) -> GroqReply:
    try:
        resp = requests.post(GROQ_URL, json=payload, headers={"Authorization": auth}, timeout=120)
        return GroqReply(resp.status_code, resp.json())
    except (requests.RequestException, ValueError) as exc:
        return GroqReply(502, {"error": {"message": f"could not reach Groq: {exc}"}})


def create_app(
    store: ApprovalStore,
    load_universe: Callable[[], list[UniverseRow]],
    fetch_profile: Callable[..., Profile | None],
    build_dir: Path,
    load_listed: Callable[[], list[UniverseRow]] = lambda: [],
    groq_upstream: Callable[[dict, str], GroqReply] | None = None,
    score_cache_dir: Path | None = None,
    notify_resume: Callable[[str, dict], None] = _post_resume,
    today: date | None = None,
) -> Flask:
    app = Flask(__name__)
    build_dir = Path(build_dir).resolve()
    auth_holder = {"value": ""}
    upstream = groq_upstream or _groq_upstream
    proxy = ScoreProxy(lambda payload: upstream(payload, auth_holder["value"]),
                       Path(score_cache_dir) if score_cache_dir else build_dir.parent, (today or date.today()).isoformat())

    def error(message: str, status: int):
        return jsonify({"error": message}), status

    @app.post("/groq/chat/completions")
    def groq_chat():
        """Stands in for Groq's endpoint so scores are cached, steadied and paced. The key stays in n8n."""
        auth_holder["value"] = request.headers.get("Authorization", "")
        reply = proxy.score(request.get_json(silent=True) or {})
        return jsonify(reply.body), reply.status

    @app.post("/screen")
    def screen():
        body = request.get_json(silent=True) or {}
        if not str(body.get("ticker") or "").strip():
            return error("ticker is required", 400)
        symbol = normalize_ticker(body["ticker"])
        universe = load_universe()
        row = next((r for r in universe if r.symbol == symbol), None)
        try:
            target = fetch_profile(symbol, row) if row else None
        except Exception as exc:  # noqa: BLE001 - any Yahoo failure maps to 502
            log.exception("target fetch failed")
            return error(f"Could not fetch data for {symbol}: {exc}", 502)
        try:
            check_target_supported(row, target)
            criteria = auto_criteria(target, body.get("overrides") or {})
        except (UnsupportedTarget, ValueError) as exc:
            return error(str(exc), 422)

        fetched, unavailable = [], []
        by_symbol = {r.symbol: r for r in universe}
        listed = {r.symbol: r for r in load_listed()}
        cands = industry_candidates(row, universe, criteria.min_listing_years, today)
        in_industry = {c.symbol for c in cands}
        outside = [by_symbol.get(s) or listed[s] for s in criteria.include
                   if (s in by_symbol or s in listed) and s not in in_industry and s != symbol and s not in criteria.exclude]
        unknown = [Exclusion(s, "not listed on NSE, so it cannot be included")
                   for s in criteria.include if s not in by_symbol and s not in listed and s != symbol]
        for cand in [*cands, *outside]:
            try:
                profile = fetch_profile(cand.symbol, cand)
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping %s: %s", cand.symbol, exc)
                profile = None
            if profile is None:
                unavailable.append(Exclusion(cand.symbol, "data unavailable from Yahoo"))
            else:
                fetched.append(profile)
        result = apply_filters(target, fetched, criteria, today)
        small = size_review_candidates(target, fetched, criteria, today, "market_cap_cr" in (body.get("overrides") or {}))
        small_symbols = {p.symbol for p in small}
        candidates = ([profile_to_dict(p) | {"included": p.symbol in criteria.include} for p in result.kept]
                      + [profile_to_dict(p) | {"size_review": True} for p in small])
        excluded = [*(e for e in result.excluded if e.symbol not in small_symbols), *unavailable, *unknown]
        return jsonify({
            "target": profile_to_dict(target),
            "candidates": candidates,
            "excluded": [{"symbol": e.symbol, "reason": e.reason} for e in excluded],
            "dropped_criteria": list(result.dropped_criteria),
            "criteria_lines": _criteria_lines(criteria),
        })

    @app.post("/rank")
    def rank():
        body = request.get_json(silent=True) or {}
        shortlist, rejected = [], []
        for item in body.get("scored") or []:
            profile = item.get("profile") or {}
            symbol = profile.get("symbol", "?")
            llm = item.get("llm")
            try:
                decision = decide(validate_llm_output(llm))
            except BadLlmOutput as exc:
                rejected.append({"symbol": symbol, "total": None, "reason": f"unscored: {exc}"})
                continue
            if profile.get("included") and decision.total is not None and llm["scores"]["product"]["score"] != 0:
                reason = f"score {decision.total}; you asked for it with Include, so it is shown for review"
                shortlist.append({"profile": profile, "similarity": decision.total, "tier": "review", "reason": reason,
                                  "mismatch_note": str(llm.get("mismatch_note") or "")})
                continue
            if not decision.kept:
                rejected.append({"symbol": symbol, "total": decision.total, "reason": decision.reason})
                continue
            tier = "review" if profile.get("size_review") and decision.tier == "auto" else decision.tier
            reason = str(llm.get("reason") or llm["scores"]["product"].get("evidence") or decision.reason)
            shortlist.append({"profile": profile, "similarity": decision.total, "tier": tier, "reason": reason,
                              "mismatch_note": str(llm.get("mismatch_note") or "")})
        shortlist.sort(key=lambda s: s["similarity"], reverse=True)
        return jsonify({"shortlist": shortlist, "rejected": rejected})

    @app.post("/approvals")
    def create_approval():
        body = request.get_json(silent=True) or {}
        if not body.get("shortlist") or not body.get("target"):
            return error("target and a non-empty shortlist are required", 400)
        payload = {k: body.get(k) for k in ("target", "shortlist", "email", "resume_url")}
        rid = store.create(payload)
        return jsonify({"id": rid, "url": f"{request.host_url.rstrip('/')}/approve/{rid}"})

    def _state_message(req):
        if req is None:
            return "This approval link is not valid.", 404
        if req.expired:
            return "This approval link has expired. Please submit the form again.", 410
        if req.submitted:
            return "This approval was already submitted. Your Excel file is on its way.", 409
        return None

    @app.get("/approve/<rid>")
    def approve_page(rid: str):
        req = store.get(rid)
        if (problem := _state_message(req)) is not None:
            return render_template_string(PAGE, message=problem[0]), problem[1]
        return render_template_string(PAGE, message=None, target=req.payload["target"], shortlist=req.payload["shortlist"])

    @app.post("/approve/<rid>")
    def approve_submit(rid: str):
        req = store.get(rid)
        if (problem := _state_message(req)) is not None:
            return render_template_string(PAGE, message=problem[0]), problem[1]
        allowed = {p["profile"]["symbol"] for p in req.payload["shortlist"]}
        chosen = [s for s in dict.fromkeys(request.form.getlist("peer")) if s in allowed]
        if not chosen:
            return render_template_string(PAGE, message="Please keep at least one peer ticked. Go back and try again."), 400
        if not store.submit(rid, chosen):
            return render_template_string(PAGE, message="This approval was already submitted."), 409
        url = req.payload.get("resume_url")
        if url:
            try:
                notify_resume(url, {"id": rid, "chosen": chosen})
            except Exception:  # noqa: BLE001 - tell the user honestly
                log.exception("resume notification failed")
                return render_template_string(
                    PAGE, message="Your choice was saved, but the workflow could not be notified, so the Excel file may not "
                                  "arrive. Check that n8n is running, then submit the form again."), 502
        return render_template_string(PAGE, message="Thanks. Your Excel file will arrive by email shortly.")

    @app.get("/approvals/<rid>/status")
    def approval_status(rid: str):
        req = store.get(rid)
        if req is None:
            return error("unknown approval", 404)
        if req.submitted:
            return jsonify({"state": "submitted", "chosen": list(req.chosen)})
        return jsonify({"state": "expired" if req.expired else "pending"})

    @app.post("/facts")
    def facts():
        """The numbers the workbook shows, so commentary quotes the sheet and not a second calculation."""
        body = request.get_json(silent=True) or {}
        try:
            target = profile_from_dict(body["target"])
            peers = [profile_from_dict(p) for p in body["peers"]]
        except (KeyError, TypeError, ValueError):
            return error("target and peers are required", 400)
        rnd = lambda v: None if v is None else round(v, 1)  # noqa: E731
        keys = [k for k, _ in excel.MULTIPLES]
        row = lambda p: {k: rnd(excel._python_multiple(p, k)) for k in keys}  # noqa: E731
        trimmed = excel._trimmed([PeerRow(p, 0, "", "") for p in peers])
        stats = {}
        for k in keys:
            values = [m for p in peers if (m := excel._python_multiple(p, k)) is not None and p.symbol not in trimmed[k]]
            if values:
                q1, med, q3 = quartiles(values)
                stats[k] = {"count": len(values), "min": rnd(min(values)), "q1": rnd(q1), "median": rnd(med),
                            "q3": rnd(q3), "max": rnd(max(values))}
        return jsonify({"target": row(target), "peers": {p.symbol: row(p) for p in peers}, "stats": stats,
                        "trimmed": {k: sorted(v) for k, v in trimmed.items() if v}})

    @app.post("/build")
    def build():
        body = request.get_json(silent=True) or {}
        req = store.get(str(body.get("id") or ""))
        if req is None:
            return error("unknown approval", 404)
        if not req.submitted:
            return error("approval not submitted yet", 409)
        if req.built_path and Path(req.built_path).exists():
            return send_file(req.built_path, mimetype=XLSX_MIME, download_name="trading_comps.xlsx")
        target = profile_from_dict(req.payload["target"])
        peers = [PeerRow(profile_from_dict(p["profile"]), p["similarity"], p["reason"], p.get("mismatch_note", ""))
                 for p in req.payload["shortlist"] if p["profile"]["symbol"] in req.chosen]
        commentary = Commentary(str(body.get("commentary", {}).get("summary", "")),
                                body.get("commentary", {}).get("per_peer", {}))
        src = body.get("sources") or {}
        sources = SourcesInfo(
            fetched_on=today or date.today(),
            criteria_lines=tuple(src.get("criteria_lines", ())),
            excluded=tuple(Exclusion(e["symbol"], e["reason"]) for e in src.get("excluded", ())),
            dropped_criteria=tuple(src.get("dropped_criteria", ())),
        )
        build_dir.mkdir(parents=True, exist_ok=True)
        path = excel.build_workbook(target, peers, commentary, sources, build_dir / f"{req.id}.xlsx")
        store.mark_built(req.id, str(path))
        return send_file(path, mimetype=XLSX_MIME, download_name="trading_comps.xlsx")

    return app


def main() -> None:  # pragma: no cover - thin launcher
    from helper.universe import load_listed as _load_listed
    from helper.universe import load_universe as _load
    from helper.yahoo import fetch_profile as _fetch

    cache = Path("cache")
    app = create_app(
        store=ApprovalStore(cache / "approvals"),
        load_universe=lambda: _load(cache),
        load_listed=lambda: _load_listed(cache),
        fetch_profile=lambda symbol, row=None: _fetch(symbol, cache, row),
        build_dir=cache / "out",
    )
    app.run(host="127.0.0.1", port=5057)


if __name__ == "__main__":  # pragma: no cover
    main()
