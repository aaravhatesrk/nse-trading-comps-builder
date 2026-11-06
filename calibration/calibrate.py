"""Blind similarity-scoring calibration against a reference list.

Usage:
  python -m calibration.calibrate --reference calibration/marico_reference.json \
      --profiles <profiles.json> --key-file <file holding your Groq key> --runs 2 --out calibration/marico_stability.html

The scorer is never told the reference list. The rubric must stay identical to the
'Build Score Prompts' node in workflow/comps-builder.json (tests/test_calibrate.py guards this).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import time
from pathlib import Path
from typing import Mapping, Sequence

import requests

from helper.scoring import AUTO_CUTOFF, REVIEW_FLOOR, BadLlmOutput, validate_llm_output, weighted_total

MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PAUSE_SECONDS = 3.0
MAX_ATTEMPTS = 8

RUBRIC = "\n".join([
    "You compare a CANDIDATE company to a TARGET company for use in a trading-comparables (comps) analysis.",
    "Score the candidate on each criterion from 0 to 3, or null if the business descriptions do not contain enough information to judge.",
    "Scale: 3 = near-identical, 2 = substantial overlap, 1 = partial overlap, 0 = no overlap or opposite.",
    "Criteria:",
    "- product: do they sell the same kinds of products or services (category overlap)?",
    "- customer_channel: same kind of customers and routes to market (consumer vs business, retail vs institutional, distributors vs e-commerce)?",
    "- business_model: similar business model and revenue mix (branded vs contract, single product vs diversified, recurring vs one-off)?",
    "- value_drivers: do the same things move earnings (raw material costs, regulation, seasonality, commodity exposure)?",
    "- operating_model: similar operating model (manufacturer, asset-light, trader, mixed)?",
    "- geography_positioning: similar geographic mix and market positioning (domestic vs export, premium vs mass)?",
    "Rules:",
    "- Use only the two descriptions provided. If a criterion cannot be judged from them, return null, not 0.",
    "- Give a one-line evidence note per criterion quoting or paraphrasing the descriptions.",
    '- Also give "reason": one sentence on why this candidate is or is not a fair comp.',
    '- Also give "mismatch_note": name any criterion where the candidate clearly differs from the target and say whether it likely affects the valuation multiple. Use an empty string if there is no meaningful mismatch.',
    "Return ONLY a JSON object of this shape:",
    '{"scores":{"product":{"score":0,"evidence":""},"customer_channel":{"score":0,"evidence":""},"business_model":{"score":0,"evidence":""},"value_drivers":{"score":0,"evidence":""},"operating_model":{"score":0,"evidence":""},"geography_positioning":{"score":0,"evidence":""}},"reason":"","mismatch_note":""}',
])

Run = Mapping[str, int | None]


class DailyLimitReached(RuntimeError):
    """Groq's tokens-per-day cap is spent; retrying within minutes cannot succeed."""


def is_daily_limit(body: str) -> bool:
    return "tokens per day" in body.lower()


def spread(runs: Sequence[Run]) -> dict[str, int]:
    """Max minus min total per name across runs, over names scored in every run."""
    names = set(runs[0]).intersection(*[set(r) for r in runs[1:]])
    out = {}
    for name in names:
        values = [r[name] for r in runs if r[name] is not None]
        if len(values) == len(runs):
            out[name] = max(values) - min(values)
    return out


def crossings(runs: Sequence[Run], cutoff: int = REVIEW_FLOOR) -> list[str]:
    """Names that are in (>= cutoff) in at least one run and out in at least one other."""
    result = []
    for name in sorted(set().union(*[set(r) for r in runs])):
        sides = {r[name] >= cutoff for r in runs if r.get(name) is not None}
        if len(sides) == 2:
            result.append(name)
    return result


def _describe(profile: Mapping) -> str:
    return profile.get("summary") or profile.get("name") or ""


def score_one(target: Mapping, symbol: str, candidate: Mapping, key: str) -> int | None:
    body = {
        "model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": f"TARGET ({target['symbol']}):\n{_describe(target)}\n\n"
                                         f"CANDIDATE ({symbol}, {candidate.get('name', '')}):\n{_describe(candidate)}"},
        ],
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            res = requests.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            wait = 10 * attempt
            print(f"  network error for {symbol}, attempt {attempt}, waiting {wait}s: {type(exc).__name__}", flush=True)
            time.sleep(wait)
            continue
        if res.status_code == 429:
            if is_daily_limit(res.text):
                raise DailyLimitReached(res.json()["error"]["message"])
            wait = max(float(res.headers.get("retry-after", 0)), 10 * attempt)
            print(f"  429 for {symbol}, attempt {attempt}, waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
            continue
        res.raise_for_status()
        raw = json.loads(res.json()["choices"][0]["message"]["content"])
        try:
            scores = validate_llm_output(raw)
        except BadLlmOutput:
            return None
        return weighted_total(scores)
    raise RuntimeError("rate limited after retries")


def run_once(target: Mapping, profiles: Mapping[str, Mapping], names: Sequence[str], key: str,
             done: Mapping[str, int | None] | None = None, on_score=None) -> dict[str, int | None]:
    """Score every name once. Names already in `done` are reused, so a stopped run resumes cheaply."""
    out = dict(done or {})
    for i, name in enumerate(names, 1):
        if name in out:
            continue
        out[name] = score_one(target, name, profiles[name], key)
        if on_score:
            on_score(name, out[name])
        print(f"  scored {i}/{len(names)} {name} = {out[name]}", flush=True)
        time.sleep(PAUSE_SECONDS)
    return out


def render_html(title: str, runs: Sequence[Run], reference: Mapping[str, str]) -> str:
    spreads = spread(runs)
    names = sorted(runs[0], key=lambda n: -(runs[0][n] or -1))
    head = "".join(f"<th>run {i + 1}</th>" for i in range(len(runs)))
    rows = []
    for n in names:
        cells = "".join(f"<td>{r.get(n)}</td>" for r in runs)
        rows.append(f"<tr><td>{html.escape(n)}</td><td>{html.escape(reference.get(n, ''))}</td>{cells}<td>{spreads.get(n, '')}</td></tr>")
    crossed = ", ".join(crossings(runs)) or "none"
    return (f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>"
            f"<h2>{html.escape(title)}</h2><p>Review floor {REVIEW_FLOOR}, auto-select {AUTO_CUTOFF}. Names crossing the review floor between runs: {crossed}</p>"
            f"<table border=1 cellpadding=4><tr><th>ticker</th><th>reference</th>{head}<th>spread</th></tr>{''.join(rows)}</table>")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True)
    ap.add_argument("--profiles", required=True)
    ap.add_argument("--key-file")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    key = Path(args.key_file).read_text().strip() if args.key_file else os.environ["GROQ_API_KEY"]
    ref = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    profiles = json.loads(Path(args.profiles).read_text(encoding="utf-8"))
    target = {"symbol": ref["target"], **profiles[ref["target"]]}
    names = [n for n in profiles if n != ref["target"] and profiles[n].get("summary")]
    progress_path = Path(args.out).with_suffix(".progress.json")
    progress: list[dict] = json.loads(progress_path.read_text()) if progress_path.exists() else []
    progress += [{} for _ in range(args.runs - len(progress))]

    def saver(index: int):
        def save(name: str, score: int | None) -> None:
            progress[index][name] = score
            progress_path.write_text(json.dumps(progress))
        return save

    try:
        runs = [run_once(target, profiles, names, key, done=progress[i], on_score=saver(i)) for i in range(args.runs)]
    except DailyLimitReached as exc:
        raise SystemExit(f"STOPPED, daily token cap reached. Progress saved to {progress_path}; re-run later. {exc}")
    Path(args.out).write_text(render_html(f"{ref['target']} scoring, {args.runs} runs", runs, ref["reference"]), encoding="utf-8")
    print("spread:", dict(sorted(spread(runs).items(), key=lambda kv: -kv[1])[:10]))
    print("crossings:", crossings(runs))
    for name, tier in ref["reference"].items():
        print(f"{name:12} {tier:10}", [r.get(name) for r in runs])


if __name__ == "__main__":
    main()
