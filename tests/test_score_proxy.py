import json

import pytest

from helper.score_proxy import BORDERLINE_HIGH, BORDERLINE_LOW, GroqReply, ScoreProxy
from helper.scoring import CRITERIA

PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "target X vs peer Y"}]}


def reply(score: int | None, evidence: str = "e") -> GroqReply:
    scores = {name: {"score": score, "evidence": evidence} for name, _ in CRITERIA}
    content = json.dumps({"scores": scores, "reason": f"all {score}", "mismatch_note": ""})
    return GroqReply(200, {"choices": [{"message": {"content": content}}]})


def raw(text: str) -> GroqReply:
    return GroqReply(200, {"choices": [{"message": {"content": text}}]})


class Upstream:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def __call__(self, payload):
        self.calls += 1
        return self.replies.pop(0)


def make(tmp_path, upstream, sleeps=None, clock=None):
    ticks = iter(range(0, 10_000, 1))
    return ScoreProxy(upstream, tmp_path, today="2026-11-06",
                      sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
                      clock=clock or (lambda: float(next(ticks))))


def content(r: GroqReply) -> dict:
    return json.loads(r.body["choices"][0]["message"]["content"])


def test_bounds_cover_the_review_band():
    assert BORDERLINE_LOW <= 60 and BORDERLINE_HIGH >= 69


def test_clear_match_is_scored_once(tmp_path):
    up = Upstream(reply(3))
    out = make(tmp_path, up).score(PAYLOAD)
    assert up.calls == 1 and out.status == 200 and content(out)["reason"] == "all 3"


def test_same_request_is_served_from_cache(tmp_path):
    up = Upstream(reply(3))
    proxy = make(tmp_path, up)
    proxy.score(PAYLOAD)
    again = proxy.score(PAYLOAD)
    assert up.calls == 1 and content(again)["reason"] == "all 3"


def test_cache_survives_a_new_proxy_instance(tmp_path):
    make(tmp_path, Upstream(reply(3))).score(PAYLOAD)
    up = Upstream()
    assert content(make(tmp_path, up).score(PAYLOAD))["reason"] == "all 3" and up.calls == 0


def test_borderline_is_scored_three_times_and_the_median_reply_wins(tmp_path):
    up = Upstream(reply(2), reply(3), reply(1))  # totals 67, 100, 33 -> median 67
    out = make(tmp_path, up).score(PAYLOAD)
    assert up.calls == 3 and content(out)["reason"] == "all 2"


def test_invalid_reply_is_resent_once(tmp_path):
    up = Upstream(raw("not json"), reply(3))
    out = make(tmp_path, up).score(PAYLOAD)
    assert up.calls == 2 and content(out)["reason"] == "all 3"


def test_two_invalid_replies_are_returned_as_is_and_not_cached(tmp_path):
    up = Upstream(raw("nope"), raw("still nope"), reply(3))
    proxy = make(tmp_path, up)
    out = proxy.score(PAYLOAD)
    assert up.calls == 2 and out.body["choices"][0]["message"]["content"] == "still nope"
    assert content(proxy.score(PAYLOAD))["reason"] == "all 3"  # not cached, so it asks again


def test_upstream_errors_pass_through_and_are_not_cached(tmp_path):
    up = Upstream(GroqReply(429, {"error": {"message": "rate limit"}}), reply(3))
    proxy = make(tmp_path, up)
    assert proxy.score(PAYLOAD).status == 429
    assert proxy.score(PAYLOAD).status == 200 and up.calls == 2


def test_calls_are_spaced_to_respect_the_token_limit(tmp_path):
    sleeps, now = [], [0.0]
    up = Upstream(reply(2), reply(2), reply(2))
    proxy = make(tmp_path, up, sleeps=sleeps, clock=lambda: now[0])
    proxy.score(PAYLOAD)
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)
