import json

import pytest

from helper.scoring import AUTO_CUTOFF, CRITERIA, REVIEW_FLOOR, BadLlmOutput, decide, validate_llm_output, weighted_total
from tests.conftest import FIXTURES

ORDER = ("product", "customer_channel", "business_model", "value_drivers", "operating_model", "geography_positioning")


def scores(*values, **named):
    base = dict(zip(ORDER, values)) if values else {k: 3 for k in ORDER}
    base.update(named)
    return base


def llm_entry(score=2, evidence="both sell branded oils"):
    return {"score": score, "evidence": evidence}


def llm_output(**overrides):
    out = {"scores": {k: llm_entry() for k in ORDER}, "mismatch_note": ""}
    out["scores"].update(overrides)
    return out


def test_constants_match_spec():
    assert (AUTO_CUTOFF, REVIEW_FLOOR) == (70, 60)
    assert dict(CRITERIA) == {
        "product": 30, "customer_channel": 20, "business_model": 20,
        "value_drivers": 10, "operating_model": 10, "geography_positioning": 10,
    }


def test_all_threes_is_100():
    assert weighted_total(scores()) == 100


def test_null_weight_is_shared_across_known_criteria():
    only_product = {k: None for k in ORDER} | {"product": 3}
    assert weighted_total(only_product) == 100
    assert weighted_total(scores(product=0)) == 70  # remaining criteria all 3, product 0


def test_all_null_returns_none():
    assert weighted_total({k: None for k in ORDER}) is None


def test_total_rounds_half_up():
    assert weighted_total({k: None for k in ORDER} | {"product": 1}) == 33


def test_product_zero_excluded_despite_high_total():
    d = decide(scores(product=0))
    assert d.total == 70 and d.kept is False and d.tier == "out"
    assert "product" in d.reason


def test_band_edges_59_out_60_review_67_review_70_auto():
    assert (decide(scores(1, 1, 3, 3, 1, 3)).total, decide(scores(1, 1, 3, 3, 1, 3)).tier) == (60, "review")
    assert (decide(scores(1, 1, 3, 2, 3, 3)).total, decide(scores(1, 1, 3, 2, 3, 3)).tier) == (63, "review")
    all_twos = decide(scores(*[2] * 6))
    assert (all_twos.total, all_twos.tier, all_twos.kept) == (67, "review", True)
    seventy = decide(scores(2, 2, 2, 3, 2, 2))
    assert (seventy.total, seventy.tier, seventy.kept) == (70, "auto", True)
    low = decide(scores(1, 2, 2, 2, 2, 2))
    assert (low.total, low.tier, low.kept) == (57, "out", False)


def test_unscoreable_candidate_is_not_kept():
    d = decide({k: None for k in ORDER})
    assert d.total is None and d.kept is False


def test_unknown_product_does_not_trigger_the_gate():
    assert decide(scores(product=None)).kept is True


def test_validate_returns_plain_scores():
    result = validate_llm_output(llm_output(value_drivers={"score": None, "evidence": ""}))
    assert result["product"] == 2 and result["value_drivers"] is None
    assert list(result) == list(ORDER)


@pytest.mark.parametrize("bad", [4, -1, 2.5, "3", True])
def test_validate_rejects_out_of_range_or_wrong_type(bad):
    with pytest.raises(BadLlmOutput, match="product"):
        validate_llm_output(llm_output(product=llm_entry(score=bad)))


def test_validate_rejects_missing_criterion():
    raw = llm_output()
    del raw["scores"]["operating_model"]
    with pytest.raises(BadLlmOutput, match="operating_model"):
        validate_llm_output(raw)


def test_validate_rejects_score_without_evidence():
    with pytest.raises(BadLlmOutput, match="evidence"):
        validate_llm_output(llm_output(product=llm_entry(score=3, evidence="  ")))


def test_validate_rejects_non_mapping():
    with pytest.raises(BadLlmOutput):
        validate_llm_output({"nope": 1})
    with pytest.raises(BadLlmOutput):
        validate_llm_output("garbage")  # type: ignore[arg-type]


def test_reproduces_calibration_run1_totals():
    run1 = json.loads((FIXTURES / "marico_run1_scores.json").read_text(encoding="utf-8"))
    assert len(run1) == 42
    # CUPID is an exact tie (37.5): the JS spike rounded it down through float noise, exact maths gives 38.
    exact_ties = {"CUPID": 38}
    for symbol, entry in run1.items():
        assert weighted_total(entry["scores"]) == exact_ties.get(symbol, entry["total"]), symbol
    assert weighted_total(run1["JYOTHYLAB"]["scores"]) == 83
