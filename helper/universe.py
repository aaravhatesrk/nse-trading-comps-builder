"""NSE peer universe: Nifty Total Market list (industry tags) joined with listing dates."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import requests

from helper.models import UniverseRow

TOTAL_MARKET_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def _http_text(url: str) -> str:
    resp = requests.get(url, headers=_UA, timeout=30)
    resp.raise_for_status()
    return resp.text


def _cached_text(cache_dir: Path, name: str, url: str, fetch_text: Callable[[str], str]) -> str:
    folder = cache_dir / "universe"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}_{date.today().isoformat()}.csv"
    if path.exists():
        return path.read_text(encoding="utf-8")
    text = fetch_text(url)
    path.write_text(text, encoding="utf-8")
    return text


def _rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d-%b-%Y").date()
    except ValueError:
        return None


def load_universe(cache_dir: Path, fetch_text: Callable[[str], str] | None = None) -> list[UniverseRow]:
    fetch_text = fetch_text or _http_text
    market = _rows(_cached_text(cache_dir, "total_market", TOTAL_MARKET_URL, fetch_text))
    listing = {
        r["SYMBOL"]: _parse_date(r.get("DATE OF LISTING", ""))
        for r in _rows(_cached_text(cache_dir, "equity_l", EQUITY_URL, fetch_text))
    }
    return [
        UniverseRow(r["Symbol"], r["Company Name"], r["Industry"], listing.get(r["Symbol"]))
        for r in market
        if r.get("Symbol")
    ]


def load_listed(cache_dir: Path, fetch_text: Callable[[str], str] | None = None) -> list[UniverseRow]:
    """Every NSE equity (no industry tag). Used only so Include can reach companies outside the index."""
    fetch_text = fetch_text or _http_text
    return [
        UniverseRow(r["SYMBOL"], r.get("NAME OF COMPANY", ""), "", _parse_date(r.get("DATE OF LISTING", "")))
        for r in _rows(_cached_text(cache_dir, "equity_l", EQUITY_URL, fetch_text))
        if r.get("SYMBOL")
    ]
