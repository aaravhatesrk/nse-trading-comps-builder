import copy

import pytest

from helper.models import UniverseRow
from helper.yahoo import YahooClient, fetch_profile
from tests.conftest import load_json
from datetime import date

ROW = UniverseRow("MARICO", "Marico Ltd.", "Fast Moving Consumer Goods", date(1996, 5, 1))


def _fetcher(quote, ts=None, calls=None):
    def fetch(symbol: str):
        if calls is not None:
            calls.append(symbol)
        return quote, ts or {}

    return fetch


def _res(quote):
    return quote["quoteSummary"]["result"][0]


def test_fetch_profile_maps_fields_to_crore(tmp_path):
    quote = load_json("yahoo_MARICO.json")
    profile = fetch_profile("MARICO", tmp_path, row=ROW, fetcher=_fetcher(quote))
    res = _res(quote)
    assert profile.market_cap_cr == pytest.approx(res["price"]["marketCap"]["raw"] / 1e7)
    assert profile.revenue_cr == pytest.approx(res["financialData"]["totalRevenue"]["raw"] / 1e7)
    assert profile.nse_industry == "Fast Moving Consumer Goods"
    assert profile.listing_date == date(1996, 5, 1)
    assert profile.summary and "Marico" in profile.summary


def test_net_debt_is_total_debt_minus_cash(tmp_path):
    quote = load_json("yahoo_MARICO.json")
    fd = _res(quote)["financialData"]
    profile = fetch_profile("MARICO", tmp_path, row=ROW, fetcher=_fetcher(quote))
    assert profile.net_debt_cr == pytest.approx((fd["totalDebt"]["raw"] - fd["totalCash"]["raw"]) / 1e7)


def test_missing_field_is_none_not_zero(tmp_path):
    quote = copy.deepcopy(load_json("yahoo_MARICO.json"))
    del _res(quote)["financialData"]["ebitda"]
    _res(quote)["defaultKeyStatistics"]["priceToBook"] = {}
    profile = fetch_profile("MARICO", tmp_path, row=ROW, fetcher=_fetcher(quote))
    assert profile.ebitda_cr is None
    assert profile.pb is None
    assert profile.market_cap_cr is not None


def test_second_call_uses_cache(tmp_path):
    quote = load_json("yahoo_MARICO.json")
    calls = []
    fetch_profile("MARICO", tmp_path, row=ROW, fetcher=_fetcher(quote, calls=calls))
    again = fetch_profile("MARICO", tmp_path, row=ROW, fetcher=_fetcher(quote, calls=calls))
    assert calls == ["MARICO"]
    assert again.symbol == "MARICO"


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload

    @property
    def cookies(self):
        return {}


class _Session:
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), 0

    def get(self, url, **kwargs):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        return _Resp(status, {"ok": True}, text="crumb123")


def test_retries_429_then_succeeds():
    session = _Session([429, 429, 200])
    client = YahooClient(session=session, sleep=lambda s: None)
    assert client.get_json("https://example.test/x") == {"ok": True}
    assert session.calls == 3


def test_gives_up_after_three_429s():
    client = YahooClient(session=_Session([429, 429, 429, 429]), sleep=lambda s: None)
    with pytest.raises(RuntimeError, match="429"):
        client.get_json("https://example.test/x")


@pytest.mark.live
def test_live_marico_smoke(tmp_path):
    profile = fetch_profile("MARICO", tmp_path)
    assert profile.market_cap_cr and profile.market_cap_cr > 1000
    assert profile.summary


def test_expired_crumb_is_refreshed_once():
    # fc.yahoo.com, getcrumb, quote=401 (expired), fc.yahoo.com, getcrumb, quote=200
    session = _Session([200, 200, 401, 200, 200, 200])
    client = YahooClient(session=session, sleep=lambda s: None)
    quote, _ = client.fetch_symbol("MARICO")
    assert quote == {"ok": True}
    assert session.calls == 6
