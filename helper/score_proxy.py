"""Caching, steadying proxy between the workflow's Groq node and Groq itself.

The workflow keeps its own Groq credential and header. This module only decides how many times to ask, whether a
saved answer can be reused, and which answer to hand back. It never does any similarity maths of its own beyond
reusing helper.scoring.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from helper.scoring import BadLlmOutput, validate_llm_output, weighted_total

BORDERLINE_LOW = 55  # first-pass totals in this range are unstable, so they are asked again
BORDERLINE_HIGH = 75
EXTRA_PASSES = 2  # median of three
MIN_GAP_SECONDS = 15.0  # about 4 calls a minute keeps ~1.9k-token calls under 8,000 tokens a minute


@dataclass(frozen=True)
class GroqReply:
    status: int
    body: dict


def _parsed(reply: GroqReply) -> tuple[dict, int | None] | None:
    """(model JSON, weighted total) when the reply follows the rubric, else None."""
    if reply.status != 200:
        return None
    try:
        data = json.loads(reply.body["choices"][0]["message"]["content"])
        return data, weighted_total(validate_llm_output(data))
    except (KeyError, IndexError, TypeError, ValueError, BadLlmOutput):
        return None


class ScoreProxy:
    def __init__(
        self,
        upstream: Callable[[dict], GroqReply],
        cache_dir: Path,
        today: str,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._upstream, self._today, self._sleep, self._clock = upstream, today, sleep, clock
        self._folder = Path(cache_dir) / "scores"
        self._folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_call: float | None = None

    def _path(self, payload: dict) -> Path:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:40]
        return self._folder / f"{self._today}_{digest}.json"

    def _ask(self, payload: dict) -> GroqReply:
        if self._last_call is not None:
            wait = MIN_GAP_SECONDS - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._upstream(payload)
        finally:
            self._last_call = self._clock()

    def _ask_valid(self, payload: dict) -> GroqReply:
        """One request, re-sent once if the reply breaks the rubric. Errors are returned immediately."""
        first = self._ask(payload)
        if first.status != 200 or _parsed(first) is not None:
            return first
        return self._ask(payload)

    def score(self, payload: dict) -> GroqReply:
        with self._lock:
            path = self._path(payload)
            if path.exists():
                return GroqReply(200, json.loads(path.read_text(encoding="utf-8")))
            first = self._ask_valid(payload)
            parsed = _parsed(first)
            if parsed is None:
                return first
            replies = [(first, parsed[1])]
            if parsed[1] is not None and BORDERLINE_LOW <= parsed[1] <= BORDERLINE_HIGH:
                for _ in range(EXTRA_PASSES):
                    extra = self._ask_valid(payload)
                    if (p := _parsed(extra)) is not None:
                        replies.append((extra, p[1]))
            replies.sort(key=lambda r: -1 if r[1] is None else r[1])
            chosen = replies[len(replies) // 2][0]
            path.write_text(json.dumps(chosen.body), encoding="utf-8")
            return chosen
