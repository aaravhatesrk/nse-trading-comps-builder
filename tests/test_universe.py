from datetime import date

from helper.universe import EQUITY_URL, TOTAL_MARKET_URL, load_universe
from tests.conftest import load_text


def _fetcher(calls=None, equity="equity_l.csv"):
    texts = {TOTAL_MARKET_URL: load_text("total_market.csv"), EQUITY_URL: load_text(equity)}

    def fetch(url: str) -> str:
        if calls is not None:
            calls.append(url)
        return texts[url]

    return fetch


def test_loads_industry_and_listing_date(tmp_path):
    rows = {r.symbol: r for r in load_universe(tmp_path, fetch_text=_fetcher())}
    marico = rows["MARICO"]
    assert marico.industry == "Fast Moving Consumer Goods"
    assert marico.listing_date == date(1996, 5, 1)
    assert rows["HDFCBANK"].industry == "Financial Services"


def test_symbol_missing_from_equity_list_has_no_listing_date(tmp_path):
    fetch_text = _fetcher()
    original = fetch_text(EQUITY_URL)
    trimmed = "\n".join(l for l in original.splitlines() if not l.startswith("DABUR"))

    def fetch(url):
        return trimmed if url == EQUITY_URL else fetch_text(url)

    rows = {r.symbol: r for r in load_universe(tmp_path, fetch_text=fetch)}
    assert rows["DABUR"].listing_date is None
    assert rows["MARICO"].listing_date is not None


def test_second_call_uses_cache(tmp_path):
    calls = []
    load_universe(tmp_path, fetch_text=_fetcher(calls))
    first = len(calls)
    load_universe(tmp_path, fetch_text=_fetcher(calls))
    assert first == 2
    assert len(calls) == first
