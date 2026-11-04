"""Immutable data objects shared across the helper."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    name: str
    industry: str
    listing_date: date | None


@dataclass(frozen=True)
class Profile:
    """One company. Money fields are rupees crore. Ratios are fractions (0.17 = 17%).
    Any field can be None when the source does not provide it; None is never zero."""

    symbol: str
    name: str | None = None
    nse_industry: str | None = None
    listing_date: date | None = None
    summary: str | None = None
    price: float | None = None
    shares_out: float | None = None
    market_cap_cr: float | None = None
    revenue_cr: float | None = None
    ebitda_cr: float | None = None
    ebitda_margin: float | None = None
    net_debt_cr: float | None = None
    ev_cr: float | None = None
    pe: float | None = None
    pb: float | None = None
    roce: float | None = None
    revenue_cagr_3y: float | None = None
    avg_daily_value_cr: float | None = None
    insider_pct: float | None = None
    fiscal_year_end: str | None = None
