"""Yahoo Finance access (unofficial endpoints): crumb session, retries, daily file cache."""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

import requests

from helper.metrics import revenue_cagr, roce
from helper.models import Profile, UniverseRow

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile"
_STATEMENT_TYPES = "annualTotalAssets,annualCurrentLiabilities,annualEBIT,annualTotalRevenue"
_CRORE = 1e7
_ATTEMPTS = 3


class YahooClient:
    def __init__(self, session: Any = None, sleep: Callable[[float], None] = time.sleep):
        self._session = session or requests.Session()
        if hasattr(self._session, "headers"):
            self._session.headers.update(_UA)
        self._sleep = sleep
        self._crumb: str | None = None

    def get_json(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(1, _ATTEMPTS + 1):
            resp = self._session.get(url, params=params, timeout=30)
            if resp.status_code == 429 and attempt < _ATTEMPTS:
                self._sleep(2**attempt)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code} from {url}")
            return resp.json()
        raise RuntimeError(f"HTTP 429 from {url}")  # unreachable; keeps type checkers happy

    def _crumb_value(self) -> str:
        if self._crumb is None:
            self._session.get("https://fc.yahoo.com", allow_redirects=False, timeout=30)
            resp = self._session.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=30)
            self._crumb = resp.text.strip()
        return self._crumb

    def fetch_symbol(self, symbol: str) -> tuple[dict, dict]:
        base = "https://query2.finance.yahoo.com"
        url = f"{base}/v10/finance/quoteSummary/{symbol}.NS"
        try:
            quote = self.get_json(url, {"modules": _MODULES, "crumb": self._crumb_value()})
        except RuntimeError as exc:
            if "HTTP 401" not in str(exc):
                raise
            self._crumb = None  # crumb expired: fetch a new one and retry once
            quote = self.get_json(url, {"modules": _MODULES, "crumb": self._crumb_value()})
        params = {
            "type": _STATEMENT_TYPES,
            "period1": 1500000000,
            "period2": int(time.time()) + 31536000,
            "crumb": self._crumb_value(),
        }
        try:
            ts = self.get_json(f"{base}/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}.NS", params)
        except RuntimeError as exc:
            log.warning("statements unavailable for %s: %s", symbol, exc)
            ts = {}
        return quote, ts


def _raw(res: dict, module: str, key: str) -> float | None:
    value = (res.get(module) or {}).get(key)
    if isinstance(value, dict):
        value = value.get("raw")
    return value if isinstance(value, (int, float)) else None


def _cr(value: float | None) -> float | None:
    return None if value is None else value / _CRORE


def parse_statements(ts: dict) -> dict[str, list[tuple[str, float | None]]]:
    """Annual series per statement type as (as-of date, value) pairs, newest first."""
    series: dict[str, list[tuple[str, float | None]]] = {}
    for item in (ts.get("timeseries") or {}).get("result") or []:
        kind = (item.get("meta") or {}).get("type", [None])[0]
        points = []
        for entry in item.get(kind) or []:
            if not entry:
                continue
            raw = (entry.get("reportedValue") or {}).get("raw")
            points.append((entry["asOfDate"], raw if isinstance(raw, (int, float)) else None))
        series[kind] = sorted(points, key=lambda p: p[0], reverse=True)
    return series


def _statement_metrics(ts: dict) -> tuple[float | None, float | None, str | None]:
    series = parse_statements(ts)
    ebit_points = series.get("annualEBIT") or []
    if not ebit_points:
        return None, None, None
    latest, ebit = ebit_points[0]
    assets = dict(series.get("annualTotalAssets") or []).get(latest)
    liabilities = dict(series.get("annualCurrentLiabilities") or []).get(latest)
    revenues = [v for _, v in series.get("annualTotalRevenue") or []]
    return roce(ebit, assets, liabilities), revenue_cagr(revenues), latest


def parse_profile(symbol: str, quote: dict, row: UniverseRow | None, ts: dict | None = None) -> Profile:
    result = (quote.get("quoteSummary") or {}).get("result") or []
    if not result:
        raise LookupError(f"no Yahoo data for {symbol}")
    res = result[0]
    price = _raw(res, "price", "regularMarketPrice")
    avg_volume = _raw(res, "summaryDetail", "averageVolume")
    statement_roce, cagr, fiscal_year_end = _statement_metrics(ts or {})
    debt = _raw(res, "financialData", "totalDebt")
    cash = _raw(res, "financialData", "totalCash")
    return Profile(
        symbol=symbol,
        name=(res.get("price") or {}).get("longName") or (row.name if row else None),
        nse_industry=row.industry if row else None,
        listing_date=row.listing_date if row else None,
        summary=(res.get("assetProfile") or {}).get("longBusinessSummary"),
        price=price,
        shares_out=_raw(res, "defaultKeyStatistics", "sharesOutstanding"),
        market_cap_cr=_cr(_raw(res, "price", "marketCap")),
        revenue_cr=_cr(_raw(res, "financialData", "totalRevenue")),
        ebitda_cr=_cr(_raw(res, "financialData", "ebitda")),
        ebitda_margin=_raw(res, "financialData", "ebitdaMargins"),
        net_debt_cr=None if debt is None or cash is None else (debt - cash) / _CRORE,
        ev_cr=_cr(_raw(res, "defaultKeyStatistics", "enterpriseValue")),
        pe=_raw(res, "summaryDetail", "trailingPE"),
        pb=_raw(res, "defaultKeyStatistics", "priceToBook"),
        avg_daily_value_cr=None if price is None or avg_volume is None else price * avg_volume / _CRORE,
        insider_pct=_raw(res, "defaultKeyStatistics", "heldPercentInsiders"),
        roce=statement_roce,
        revenue_cagr_3y=cagr,
        fiscal_year_end=fiscal_year_end,
    )


_default_client: YahooClient | None = None


def _default_fetcher(symbol: str) -> tuple[dict, dict]:
    global _default_client
    if _default_client is None:
        _default_client = YahooClient()
    return _default_client.fetch_symbol(symbol)


def fetch_profile(
    symbol: str,
    cache_dir: Path,
    row: UniverseRow | None = None,
    fetcher: Callable[[str], tuple[dict, dict]] | None = None,
) -> Profile:
    folder = cache_dir / "yahoo"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbol}_{date.today().isoformat()}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        quote, ts = cached["quote"], cached.get("ts") or {}
    else:
        quote, ts = (fetcher or _default_fetcher)(symbol)
        path.write_text(json.dumps({"quote": quote, "ts": ts}), encoding="utf-8")
    return parse_profile(symbol, quote, row, ts)
