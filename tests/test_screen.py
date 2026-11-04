from datetime import date, timedelta

import pytest

from helper.models import Profile, Range, UniverseRow
from helper.screen import (
    UnsupportedTarget,
    apply_filters,
    auto_criteria,
    check_target_supported,
    industry_candidates,
    normalize_ticker,
)

TODAY = date(2026, 11, 4)
FMCG = "Fast Moving Consumer Goods"


def prof(symbol="PEER", **overrides) -> Profile:
    base = dict(
        symbol=symbol, name=symbol, nse_industry=FMCG, listing_date=date(2000, 1, 1),
        market_cap_cr=100000.0, revenue_cr=15000.0, ebitda_cr=2500.0, ebitda_margin=0.17,
        net_debt_cr=-1500.0, roce=0.39, revenue_cagr_3y=0.08, avg_daily_value_cr=50.0, insider_pct=0.6,
    )
    base.update(overrides)
    return Profile(**base)


TARGET = prof("MARICO")


def run(candidates, **override_kwargs):
    criteria = auto_criteria(TARGET, override_kwargs)
    return apply_filters(TARGET, candidates, criteria, today=TODAY)


def reasons(result):
    return {e.symbol: e.reason for e in result.excluded}


def test_normalize_ticker():
    assert normalize_ticker("  marico.ns ") == "MARICO"
    assert normalize_ticker("Marico") == "MARICO"


def test_auto_market_cap_band_is_half_to_three_times():
    criteria = auto_criteria(prof(market_cap_cr=100000.0), {})
    assert criteria.market_cap_cr.lo == pytest.approx(50000.0)
    assert criteria.market_cap_cr.hi == pytest.approx(300000.0)


def test_auto_defaults_match_spec():
    c = auto_criteria(prof(ebitda_margin=0.17, roce=0.39), {})
    assert (c.ebitda_margin.lo, c.ebitda_margin.hi) == pytest.approx((0.07, 0.27))
    assert (c.roce.lo, c.roce.hi) == pytest.approx((0.29, 0.49))
    assert c.max_net_debt_ebitda == 3.0 and c.min_listing_years == 3.0
    assert not c.revenue_cr.is_active and not c.revenue_cagr_3y.is_active
    assert c.min_daily_value_cr is None and not c.insider_pct.is_active


def test_override_replaces_auto_range_and_none_none_switches_off():
    c = auto_criteria(TARGET, {"market_cap_cr": (1000, 5000), "roce": (None, None)})
    assert (c.market_cap_cr.lo, c.market_cap_cr.hi) == (1000, 5000)
    assert not c.roce.is_active


def test_peer_inside_all_ranges_is_kept():
    result = run([prof("DABUR", market_cap_cr=80000.0)])
    assert [p.symbol for p in result.kept] == ["DABUR"]


def test_market_cap_outside_band_is_excluded_with_reason():
    result = run([prof("TINY", market_cap_cr=10000.0)])
    assert "market cap" in reasons(result)["TINY"]


def test_different_industry_excluded():
    result = run([prof("ITCO", nse_industry="Information Technology")])
    assert "industry" in reasons(result)["ITCO"]


def test_negative_ebitda_peer_excluded_with_reason():
    result = run([prof("LOSS", ebitda_cr=-50.0)])
    assert "negative EBITDA" in reasons(result)["LOSS"]


def test_recent_ipo_excluded_by_listing_age():
    young = prof("NEWCO", listing_date=TODAY - timedelta(days=365))
    assert "listed" in reasons(run([young]))["NEWCO"]


def test_missing_active_criterion_excludes_with_missing_data_reason():
    candidates = [prof("A"), prof("B"), prof("C"), prof("NOROCE", roce=None)]
    result = run(candidates)
    assert reasons(result)["NOROCE"] == "missing data: roce"
    assert {p.symbol for p in result.kept} == {"A", "B", "C"}


def test_criterion_dropped_when_most_candidates_lack_data():
    candidates = [prof("A", roce=None), prof("B", roce=None), prof("C")]
    result = run(candidates)
    assert {p.symbol for p in result.kept} == {"A", "B", "C"}
    assert any("roce" in note for note in result.dropped_criteria)


def test_net_cash_peer_passes_leverage_cap_and_high_leverage_fails():
    ok = prof("CASHRICH", net_debt_cr=-9000.0)
    levered = prof("LEVERED", net_debt_cr=10000.0)
    result = run([ok, levered])
    assert [p.symbol for p in result.kept] == ["CASHRICH"]
    assert "net debt" in reasons(result)["LEVERED"]


def test_include_list_forces_peer_and_exclude_list_removes():
    outside = prof("FORCED", market_cap_cr=10.0)
    removed = prof("DROPPED")
    result = run([outside, removed], include=("FORCED",), exclude=("DROPPED",))
    assert [p.symbol for p in result.kept] == ["FORCED"]
    assert reasons(result)["DROPPED"] == "excluded by user"


def test_target_is_never_its_own_peer():
    result = run([prof("MARICO"), prof("DABUR")])
    assert [p.symbol for p in result.kept] == ["DABUR"]


def test_industry_candidates_filters_industry_and_young_listings():
    target_row = UniverseRow("MARICO", "Marico", FMCG, date(1996, 5, 1))
    universe = [
        target_row,
        UniverseRow("DABUR", "Dabur", FMCG, date(1999, 4, 28)),
        UniverseRow("NEWCO", "NewCo", FMCG, TODAY - timedelta(days=100)),
        UniverseRow("INFY", "Infosys", "Information Technology", date(1993, 1, 1)),
        UniverseRow("NODATE", "NoDate", FMCG, None),
    ]
    rows = industry_candidates(target_row, universe, min_listing_years=3.0, today=TODAY)
    assert [r.symbol for r in rows] == ["DABUR", "NODATE"]


def test_unknown_ticker_refused_with_message():
    with pytest.raises(UnsupportedTarget, match="not in the Nifty Total Market"):
        check_target_supported(None, None)


def test_bank_target_refused():
    row = UniverseRow("HDFCBANK", "HDFC Bank", "Financial Services", date(1995, 11, 8))
    with pytest.raises(UnsupportedTarget, match="banks and NBFCs"):
        check_target_supported(row, prof("HDFCBANK", nse_industry="Financial Services"))


def test_negative_ebitda_target_refused():
    row = UniverseRow("LOSSCO", "LossCo", FMCG, date(2010, 1, 1))
    with pytest.raises(UnsupportedTarget, match="EBITDA"):
        check_target_supported(row, prof("LOSSCO", ebitda_cr=-5.0))


def test_supported_target_passes():
    row = UniverseRow("MARICO", "Marico", FMCG, date(1996, 5, 1))
    check_target_supported(row, TARGET)


def test_range_helpers():
    r = Range(1.0, 3.0)
    assert r.is_active and r.contains(2.0) and not r.contains(4.0)
    assert Range(None, 5.0).contains(-100.0) and not Range().is_active
