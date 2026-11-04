import pytest

from helper.models import Profile
from helper.stats import implied_price, quartiles, trim_outliers


def target(**kw) -> Profile:
    base = dict(
        symbol="T", price=100.0, pe=25.0, pb=10.0, ebitda_cr=1000.0, revenue_cr=5000.0,
        net_debt_cr=500.0, shares_out=1e9,
    )
    base.update(kw)
    return Profile(**base)


def test_outlier_removed():
    kept, removed = trim_outliers([10, 11, 12, 11, 60])
    assert kept == [10, 11, 12, 11]
    assert removed == [4]


def test_no_outliers_unchanged():
    kept, removed = trim_outliers([10.0, 11.0, 12.0, 13.0])
    assert kept == [10.0, 11.0, 12.0, 13.0] and removed == []


def test_fewer_than_four_values_are_never_trimmed():
    kept, removed = trim_outliers([1.0, 2.0, 100.0])
    assert kept == [1.0, 2.0, 100.0] and removed == []


def test_quartiles_match_linear_interpolation():
    q1, median, q3 = quartiles([1, 2, 3, 4, 5, 6])
    assert (q1, median, q3) == pytest.approx((2.25, 3.5, 4.75))


def test_quartiles_of_empty_raises():
    with pytest.raises(ValueError):
        quartiles([])


def test_implied_price_pe():
    assert implied_price("pe", 20.0, target()) == pytest.approx(80.0)


def test_implied_price_pb():
    assert implied_price("pb", 5.0, target()) == pytest.approx(50.0)


def test_implied_price_ev_ebitda_bridges_net_debt():
    # EV = 10 x 1000 = 10,000 cr; equity = 10,000 - 500 = 9,500 cr; 9,500 cr / 100 crore shares = 95
    assert implied_price("ev_ebitda", 10.0, target()) == pytest.approx(95.0)


def test_implied_price_ev_sales_and_net_cash():
    assert implied_price("ev_sales", 2.0, target(net_debt_cr=-500.0)) == pytest.approx(105.0)


@pytest.mark.parametrize(
    "multiple,kw",
    [
        ("ev_ebitda", {"shares_out": None}),
        ("ev_ebitda", {"net_debt_cr": None}),
        ("ev_ebitda", {"ebitda_cr": -5.0}),
        ("ev_sales", {"revenue_cr": None}),
        ("pe", {"pe": None}),
        ("pe", {"pe": -3.0}),
        ("pb", {"price": None}),
    ],
)
def test_implied_price_none_when_inputs_missing_or_not_meaningful(multiple, kw):
    assert implied_price(multiple, 10.0, target(**kw)) is None


def test_implied_price_none_when_equity_value_not_positive():
    assert implied_price("ev_ebitda", 0.2, target()) is None  # EV 200 cr < net debt 500 cr


def test_unknown_multiple_is_rejected():
    with pytest.raises(ValueError, match="unknown multiple"):
        implied_price("ev_fantasy", 1.0, target())
