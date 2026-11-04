"""Immutable data objects shared across the helper."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping


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


@dataclass(frozen=True)
class Range:
    """Inclusive numeric range; either end can be open. Range() is inactive (no filter)."""

    lo: float | None = None
    hi: float | None = None

    @property
    def is_active(self) -> bool:
        return self.lo is not None or self.hi is not None

    def contains(self, value: float) -> bool:
        return (self.lo is None or value >= self.lo) and (self.hi is None or value <= self.hi)


@dataclass(frozen=True)
class Criteria:
    market_cap_cr: Range = Range()
    revenue_cr: Range = Range()
    ebitda_margin: Range = Range()
    revenue_cagr_3y: Range = Range()
    roce: Range = Range()
    insider_pct: Range = Range()
    max_net_debt_ebitda: float | None = None
    min_daily_value_cr: float | None = None
    min_listing_years: float = 3.0
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class Exclusion:
    symbol: str
    reason: str


@dataclass(frozen=True)
class ScreenResult:
    kept: tuple[Profile, ...]
    excluded: tuple[Exclusion, ...]
    dropped_criteria: tuple[str, ...] = ()


@dataclass(frozen=True)
class PeerRow:
    profile: Profile
    similarity: int
    reason: str
    mismatch_note: str = ""


@dataclass(frozen=True)
class Commentary:
    summary: str
    per_peer: Mapping[str, str]


@dataclass(frozen=True)
class SourcesInfo:
    fetched_on: date
    criteria_lines: tuple[str, ...] = ()
    excluded: tuple[Exclusion, ...] = ()
    dropped_criteria: tuple[str, ...] = ()
