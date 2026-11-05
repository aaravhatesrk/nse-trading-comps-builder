"""Business-similarity scoring maths. The LLM only supplies per-criterion scores; this module does the rest."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

CRITERIA: tuple[tuple[str, int], ...] = (
    ("product", 30),
    ("customer_channel", 20),
    ("business_model", 20),
    ("value_drivers", 10),
    ("operating_model", 10),
    ("geography_positioning", 10),
)
AUTO_CUTOFF = 70  # at or above: pre-ticked on the approval page
REVIEW_FLOOR = 60  # 60 to 69: shown unticked for the user to decide; below: dropped
_MAX_SCORE = 3


class BadLlmOutput(ValueError):
    """The model's JSON did not match the rubric shape."""


@dataclass(frozen=True)
class Decision:
    total: int | None
    kept: bool
    reason: str
    tier: str = "out"  # "auto", "review" or "out"


def weighted_total(scores: Mapping[str, int | None]) -> int | None:
    """Weighted score out of 100. Unknown (None) criteria share their weight across the rest."""
    known = [(w, scores[name]) for name, w in CRITERIA if scores.get(name) is not None]
    weight_sum = sum(w for w, _ in known)
    if not weight_sum:
        return None
    # Exact integer arithmetic so ties (e.g. 37.5) round half-up deterministically, never by float noise.
    numerator = 100 * sum(w * s for w, s in known)
    denominator = _MAX_SCORE * weight_sum
    return (2 * numerator + denominator) // (2 * denominator)


def decide(scores: Mapping[str, int | None]) -> Decision:
    total = weighted_total(scores)
    if total is None:
        return Decision(None, False, "no criterion could be scored")
    if scores.get("product") == 0:
        return Decision(total, False, "no product overlap (product score 0)")
    if total < REVIEW_FLOOR:
        return Decision(total, False, f"score {total} below review floor {REVIEW_FLOOR}")
    if total < AUTO_CUTOFF:
        return Decision(total, True, f"score {total} needs your review ({REVIEW_FLOOR} to {AUTO_CUTOFF - 1})", "review")
    return Decision(total, True, f"score {total} meets auto-select cutoff {AUTO_CUTOFF}", "auto")


def _valid_score(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_SCORE


def validate_llm_output(raw: Mapping) -> dict[str, int | None]:
    """Check the model's JSON against the rubric and return {criterion: score or None} in rubric order."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("scores"), Mapping):
        raise BadLlmOutput("output has no 'scores' object")
    result: dict[str, int | None] = {}
    for name, _ in CRITERIA:
        entry = raw["scores"].get(name)
        if not isinstance(entry, Mapping) or "score" not in entry:
            raise BadLlmOutput(f"missing criterion: {name}")
        score = entry["score"]
        if score is not None and not _valid_score(score):
            raise BadLlmOutput(f"{name}: score must be 0 to {_MAX_SCORE} or null, got {score!r}")
        if score is not None and not str(entry.get("evidence") or "").strip():
            raise BadLlmOutput(f"{name}: a score needs a one-line evidence note")
        result[name] = score
    return result
