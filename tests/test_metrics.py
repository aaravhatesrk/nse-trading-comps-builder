import copy
from datetime import date

import pytest

from helper.metrics import net_debt_to_ebitda, revenue_cagr, roce
from helper.models import UniverseRow
from helper.yahoo import fetch_profile, parse_statements
from tests.conftest import load_json

ROW = UniverseRow("MARICO", "Marico Ltd.", "Fast Moving Consumer Goods", date(1996, 5, 1))


def test_roce_basic():
    assert roce(ebit=100, total_assets=1000, current_liabilities=200) == pytest.approx(0.125)


@pytest.mark.parametrize("ebit,assets,cl", [(None, 1000, 200), (100, None, 200), (100, 1000, None), (100, 200, 200), (100, 100, 200)])
def test_roce_none_when_input_missing_or_capital_not_positive(ebit, assets, cl):
    assert roce(ebit, assets, cl) is None


def test_cagr_three_years_newest_first():
    assert revenue_cagr([133.1, 121.0, 110.0, 100.0]) == pytest.approx(0.10)


def test_cagr_none_when_fewer_than_four_values():
    assert revenue_cagr([121.0, 110.0, 100.0]) is None


def test_cagr_none_when_endpoint_missing_or_non_positive():
    assert revenue_cagr([133.1, 121.0, 110.0, None]) is None
    assert revenue_cagr([133.1, 121.0, 110.0, 0.0]) is None


def test_net_debt_negative_when_net_cash():
    assert net_debt_to_ebitda(debt=100, cash=300, ebitda=100) == pytest.approx(-2.0)


@pytest.mark.parametrize("ebitda", [0, -5, None])
def test_zero_or_negative_ebitda_returns_none(ebitda):
    assert net_debt_to_ebitda(100, 10, ebitda) is None


def test_parse_statements_returns_newest_first():
    series = parse_statements(load_json("yahoo_ts_MARICO.json"))
    dates = [d for d, _ in series["annualTotalRevenue"]]
    assert dates == sorted(dates, reverse=True)


def test_profile_roce_and_cagr_come_from_timeseries(tmp_path):
    quote, ts = load_json("yahoo_MARICO.json"), load_json("yahoo_ts_MARICO.json")
    series = parse_statements(ts)
    ebit, assets, cl = (series[k][0][1] for k in ("annualEBIT", "annualTotalAssets", "annualCurrentLiabilities"))
    revenues = [v for _, v in series["annualTotalRevenue"]]
    profile = fetch_profile("MARICO", tmp_path, row=ROW, fetcher=lambda s: (quote, ts))
    assert profile.roce == pytest.approx(ebit / (assets - cl))
    assert profile.revenue_cagr_3y == pytest.approx((revenues[0] / revenues[3]) ** (1 / 3) - 1)
    assert profile.fiscal_year_end == series["annualEBIT"][0][0]


def test_profile_has_no_roce_when_timeseries_empty(tmp_path):
    quote = copy.deepcopy(load_json("yahoo_MARICO.json"))
    profile = fetch_profile("MARICO", tmp_path, row=ROW, fetcher=lambda s: (quote, {}))
    assert profile.roce is None
    assert profile.revenue_cagr_3y is None
