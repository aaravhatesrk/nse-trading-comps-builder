from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from helper.models import Profile, UniverseRow, profile_to_dict
from helper.server import create_app
from helper.store import ApprovalStore

TODAY = date(2026, 11, 4)
OLD = date(2000, 1, 1)
UNIVERSE = [
    UniverseRow("TGT", "Target Ltd", "FMCG", OLD),
    UniverseRow("P1", "Peer One", "FMCG", OLD),
    UniverseRow("P2", "Peer Two", "FMCG", OLD),
    UniverseRow("BANK", "Some Bank", "Financial Services", OLD),
    UniverseRow("NEG", "Negative Co", "FMCG", OLD),
]


def prof(symbol, mcap=10000.0, ebitda=1000.0, **kw):
    return Profile(symbol=symbol, name=f"{symbol} Ltd", price=100.0, market_cap_cr=mcap, net_debt_cr=0.0,
                   revenue_cr=5000.0, ebitda_cr=ebitda, ebitda_margin=0.2, pe=20.0, pb=5.0, shares_out=1e9,
                   listing_date=OLD, **kw)


PROFILES = {"TGT": prof("TGT"), "P1": prof("P1", 12000), "P2": prof("P2", 9000), "NEG": prof("NEG", 11000, ebitda=-5.0),
            "BANK": prof("BANK")}


def fake_fetch(symbol, row=None):
    if symbol == "DOWN":
        raise RuntimeError("yahoo down")
    return PROFILES.get(symbol)


@pytest.fixture
def parts(tmp_path):
    notified = []
    store = ApprovalStore(tmp_path / "approvals", clock=lambda: datetime(2026, 11, 4, 12, 0))
    app = create_app(store=store, load_universe=lambda: UNIVERSE, fetch_profile=fake_fetch,
                     notify_resume=lambda url, data: notified.append((url, data)),
                     build_dir=tmp_path / "out", today=TODAY)
    return app.test_client(), store, notified


def good_llm(product=3):
    names = ["product", "customer_channel", "business_model", "value_drivers", "operating_model", "geography_positioning"]
    scores = {n: {"score": 3, "evidence": "same"} for n in names}
    scores["product"] = {"score": product, "evidence": "same" if product else "different"}
    return {"scores": scores, "reason": "sells the same things", "mismatch_note": ""}


def test_screen_returns_candidates_and_exclusions(parts):
    client, _, _ = parts
    res = client.post("/screen", json={"ticker": "tgt.ns"})
    body = res.get_json()
    assert res.status_code == 200
    assert body["target"]["symbol"] == "TGT"
    symbols = {c["symbol"] for c in body["candidates"]}
    assert symbols == {"P1", "P2"}
    assert any(e["symbol"] == "NEG" for e in body["excluded"])
    assert body["criteria_lines"]


def test_screen_unknown_ticker_is_422_with_message(parts):
    res = parts[0].post("/screen", json={"ticker": "NOPE"})
    assert res.status_code == 422 and "Nifty Total Market" in res.get_json()["error"]


def test_screen_bad_override_is_422(parts):
    res = parts[0].post("/screen", json={"ticker": "TGT", "overrides": {"bogus": 1}})
    assert res.status_code == 422


def test_screen_missing_ticker_is_400(parts):
    assert parts[0].post("/screen", json={}).status_code == 400


def test_screen_yahoo_failure_is_502(parts):
    client, _, _ = parts
    UNIVERSE.append(UniverseRow("DOWN", "Down Co", "FMCG", OLD))
    try:
        res = client.post("/screen", json={"ticker": "DOWN"})
    finally:
        UNIVERSE.pop()
    assert res.status_code == 502


def test_rank_sorts_and_drops_below_cutoff(parts):
    client, _, _ = parts
    weak = {"scores": {n: {"score": 1, "evidence": "x"} for n in good_llm()["scores"]}}
    res = client.post("/rank", json={"target": profile_to_dict(PROFILES["TGT"]), "scored": [
        {"profile": profile_to_dict(PROFILES["P2"]), "llm": weak},
        {"profile": profile_to_dict(PROFILES["P1"]), "llm": good_llm()},
    ]})
    body = res.get_json()
    assert [p["profile"]["symbol"] for p in body["shortlist"]] == ["P1"]
    assert body["shortlist"][0]["similarity"] == 100
    assert body["rejected"][0]["symbol"] == "P2"


def test_rank_product_zero_is_rejected(parts):
    client, _, _ = parts
    res = client.post("/rank", json={"target": {}, "scored": [
        {"profile": profile_to_dict(PROFILES["P1"]), "llm": good_llm(product=0)}]})
    assert res.get_json()["shortlist"] == []


def test_rank_invalid_llm_json_marks_peer_unscored_not_crash(parts):
    client, _, _ = parts
    res = client.post("/rank", json={"target": {}, "scored": [
        {"profile": profile_to_dict(PROFILES["P1"]), "llm": {"nonsense": True}},
        {"profile": profile_to_dict(PROFILES["P2"]), "llm": good_llm()}]})
    body = res.get_json()
    assert res.status_code == 200
    assert [p["profile"]["symbol"] for p in body["shortlist"]] == ["P2"]
    assert "unscored" in body["rejected"][0]["reason"]


def make_request(client, resume_url=None):
    shortlist = [{"profile": profile_to_dict(PROFILES[s]), "similarity": 80, "reason": f"{s} reason", "mismatch_note": ""}
                 for s in ("P1", "P2")]
    body = {"target": profile_to_dict(PROFILES["TGT"]), "shortlist": shortlist, "email": "a@example.com"}
    if resume_url:
        body["resume_url"] = resume_url
    res = client.post("/approvals", json=body)
    assert res.status_code == 200
    return res.get_json()


def test_approval_page_lists_peers_and_escapes_html(parts):
    client, store, _ = parts
    out = make_request(client)
    assert out["url"].endswith(f"/approve/{out['id']}")
    page = client.get(f"/approve/{out['id']}")
    assert page.status_code == 200 and b"P1 Ltd" in page.data and b'type="checkbox"' in page.data
    rid = store.create({"target": {"symbol": "X", "name": "<script>alert(1)</script>"}, "shortlist": [], "email": "e"})
    assert b"<script>alert(1)</script>" not in client.get(f"/approve/{rid}").data


def test_approval_unknown_is_404(parts):
    assert parts[0].get("/approve/doesnotexist1234567890").status_code == 404


def test_submit_records_choice_and_notifies_resume_url(parts):
    client, store, notified = parts
    out = make_request(client, resume_url="http://n8n/resume/1")
    res = client.post(f"/approve/{out['id']}", data={"peer": ["P1"]})
    assert res.status_code == 200
    assert store.get(out["id"]).chosen == ("P1",)
    assert notified and notified[0][0] == "http://n8n/resume/1"
    status = client.get(f"/approvals/{out['id']}/status").get_json()
    assert status == {"state": "submitted", "chosen": ["P1"]}


def test_submit_zero_peers_is_400_and_stays_pending(parts):
    client, store, _ = parts
    out = make_request(client)
    assert client.post(f"/approve/{out['id']}", data={}).status_code == 400
    assert client.get(f"/approvals/{out['id']}/status").get_json()["state"] == "pending"


def test_double_submit_is_409(parts):
    client, _, _ = parts
    out = make_request(client)
    client.post(f"/approve/{out['id']}", data={"peer": ["P1"]})
    assert client.post(f"/approve/{out['id']}", data={"peer": ["P2"]}).status_code == 409
    assert client.get(f"/approve/{out['id']}").status_code == 409


def test_submit_ignores_symbols_not_on_the_shortlist(parts):
    client, store, _ = parts
    out = make_request(client)
    assert client.post(f"/approve/{out['id']}", data={"peer": ["EVIL"]}).status_code == 400


def test_expired_link_is_410(tmp_path):
    now = [datetime(2026, 11, 4, 12, 0)]
    store = ApprovalStore(tmp_path / "a", clock=lambda: now[0])
    client = create_app(store=store, load_universe=lambda: UNIVERSE, fetch_profile=fake_fetch,
                        build_dir=tmp_path / "o", today=TODAY).test_client()
    out = make_request(client)
    now[0] = datetime(2026, 11, 6, 12, 0)
    assert client.get(f"/approve/{out['id']}").status_code == 410
    assert client.post(f"/approve/{out['id']}", data={"peer": ["P1"]}).status_code == 410
    assert client.get(f"/approvals/{out['id']}/status").get_json()["state"] == "expired"


BUILD_BODY = {"commentary": {"summary": "Premium.", "per_peer": {"P1": "closest"}},
              "sources": {"criteria_lines": ["market cap"], "excluded": [{"symbol": "NEG", "reason": "negative EBITDA"}],
                          "dropped_criteria": []}}


def test_build_before_approval_is_409(parts):
    client, _, _ = parts
    out = make_request(client)
    assert client.post("/build", json={"id": out["id"], **BUILD_BODY}).status_code == 409


def test_build_uses_only_chosen_peers_and_is_idempotent(parts):
    client, store, _ = parts
    out = make_request(client)
    client.post(f"/approve/{out['id']}", data={"peer": ["P2"]})
    first = client.post("/build", json={"id": out["id"], **BUILD_BODY})
    assert first.status_code == 200 and "spreadsheetml" in first.content_type
    ws = load_workbook(BytesIO(first.data))["Comps"]
    symbols = [ws[f"A{r}"].value for r in range(6, 12)]
    assert "P2" in symbols and "P1" not in symbols
    second = client.post("/build", json={"id": out["id"], **BUILD_BODY})
    assert second.status_code == 200 and second.data == first.data
    assert store.get(out["id"]).built_path


def test_build_works_with_a_relative_build_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = ApprovalStore(tmp_path / "a", clock=lambda: datetime(2026, 11, 4, 12, 0))
    client = create_app(store=store, load_universe=lambda: UNIVERSE, fetch_profile=fake_fetch,
                        build_dir=Path("cache/out"), today=TODAY).test_client()
    out = make_request(client)
    client.post(f"/approve/{out['id']}", data={"peer": ["P1"]})
    res = client.post("/build", json={"id": out["id"], **BUILD_BODY})
    assert res.status_code == 200 and "spreadsheetml" in res.content_type
