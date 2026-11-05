import json
from pathlib import Path

from calibration.calibrate import RUBRIC, crossings, spread

ROOT = Path(__file__).resolve().parent.parent


def test_spread_is_max_minus_min_per_name():
    runs = [{"A": 70, "B": 60}, {"A": 66, "B": 60}, {"A": 72, "B": 61}]
    assert spread(runs) == {"A": 6, "B": 1}


def test_crossing_when_a_name_is_on_both_sides_of_cutoff():
    runs = [{"A": 67, "B": 70}, {"A": 63, "B": 71}]
    assert crossings(runs, 65) == ["A"]


def test_exactly_at_cutoff_counts_as_in():
    assert crossings([{"A": 65}, {"A": 64}], 65) == ["A"]


def test_unscored_name_is_ignored_not_crashing():
    assert crossings([{"A": None}, {"A": 70}], 65) == []


def test_rubric_matches_the_workflow_prompt():
    wf = json.loads((ROOT / "workflow" / "comps-builder.json").read_text(encoding="utf-8"))
    code = next(n for n in wf["nodes"] if n["name"] == "Build Score Prompts")["parameters"]["jsCode"]
    for line in RUBRIC.splitlines():
        escaped = line.replace("\\", "\\\\").replace("'", "\'")
        assert escaped in code, line


def test_daily_limit_error_is_recognised():
    from calibration.calibrate import is_daily_limit
    assert is_daily_limit('Rate limit reached ... on tokens per day (TPD): Limit 200000, Used 199234')
    assert not is_daily_limit('Rate limit reached ... on tokens per minute (TPM): Limit 8000')


def test_run_once_skips_names_already_scored_and_reports_new_ones(monkeypatch):
    from calibration import calibrate
    calls = []
    monkeypatch.setattr(calibrate, "score_one", lambda t, n, c, k: calls.append(n) or 70)
    monkeypatch.setattr(calibrate, "PAUSE_SECONDS", 0)
    saved = []
    out = calibrate.run_once({"symbol": "T"}, {"A": {}, "B": {}}, ["A", "B"], "k",
                             done={"A": 66}, on_score=lambda name, s: saved.append((name, s)))
    assert calls == ["B"] and out == {"A": 66, "B": 70} and saved == [("B", 70)]


def test_daily_limit_stops_the_run_with_a_clear_error(monkeypatch):
    import pytest
    from calibration import calibrate

    def boom(*a):
        raise calibrate.DailyLimitReached("daily cap")
    monkeypatch.setattr(calibrate, "score_one", boom)
    with pytest.raises(calibrate.DailyLimitReached):
        calibrate.run_once({"symbol": "T"}, {"A": {}}, ["A"], "k")


def test_connection_reset_is_retried_not_fatal(monkeypatch):
    import requests
    from calibration import calibrate

    good = {"scores": {n: {"score": 3, "evidence": "same"} for n in
                       ["product", "customer_channel", "business_model", "value_drivers",
                        "operating_model", "geography_positioning"]}, "reason": "r", "mismatch_note": ""}

    class Ok:
        status_code = 200
        headers = {}
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(good)}}]}

    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("reset")
        return Ok()
    monkeypatch.setattr(calibrate.requests, "post", flaky)
    monkeypatch.setattr(calibrate.time, "sleep", lambda s: None)
    assert calibrate.score_one({"symbol": "T"}, "A", {}, "k") == 100
    assert len(calls) == 2
