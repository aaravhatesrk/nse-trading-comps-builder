"""Peer screening: ticker normalising, Auto criteria, and filter application."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from helper.metrics import net_debt_to_ebitda
from helper.models import Criteria, Exclusion, Profile, Range, ScreenResult, UniverseRow

# (Profile/Criteria attribute, label used in reasons)
_RANGE_CRITERIA = (
    ("market_cap_cr", "market cap"),
    ("revenue_cr", "revenue"),
    ("ebitda_margin", "ebitda margin"),
    ("revenue_cagr_3y", "revenue cagr"),
    ("roce", "roce"),
    ("insider_pct", "insider holding"),
)
_SCALAR_KEYS = ("max_net_debt_ebitda", "min_daily_value_cr", "min_listing_years")
_LIST_KEYS = ("include", "exclude")
_DAYS_PER_YEAR = 365.25
REVIEW_SIZE_FLOOR = 0.15  # Auto only: peers from 0.15x up to the normal 0.5x are scored but always need the user's review


class UnsupportedTarget(ValueError):
    """The target cannot be analysed; the message is shown to the user."""


def normalize_ticker(raw: str) -> str:
    symbol = raw.strip().upper()
    for suffix in (".NS", ".BO"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
    return symbol.strip()


def check_target_supported(row: UniverseRow | None, profile: Profile | None) -> None:
    if row is None:
        raise UnsupportedTarget(
            "That ticker is not in the Nifty Total Market list, so industry peers cannot be screened."
        )
    if row.industry == "Financial Services":
        raise UnsupportedTarget("Financial companies (banks and NBFCs) are not supported: EV/EBITDA is not meaningful for them.")
    if profile is None or profile.ebitda_cr is None or profile.ebitda_cr <= 0:
        raise UnsupportedTarget("The target's EBITDA is missing or not positive, so EBITDA-based comps are not meaningful.")


def _band(value: float | None, low_mult: float, high_mult: float) -> Range:
    return Range() if value is None else Range(value * low_mult, value * high_mult)


def _plus_minus(value: float | None, delta: float) -> Range:
    return Range() if value is None else Range(value - delta, value + delta)


def _as_range(value: Any) -> Range:
    if value is None:
        return Range()
    lo, hi = value
    return Range(lo, hi)


def auto_criteria(target: Profile, overrides: Mapping[str, Any]) -> Criteria:
    allowed = {attr for attr, _ in _RANGE_CRITERIA} | set(_SCALAR_KEYS) | set(_LIST_KEYS)
    unknown = set(overrides) - allowed
    if unknown:
        raise ValueError(f"unknown criteria: {sorted(unknown)}")
    fields: dict[str, Any] = {
        "market_cap_cr": _band(target.market_cap_cr, 0.5, 5.0),
        "ebitda_margin": _plus_minus(target.ebitda_margin, 0.10),
        "max_net_debt_ebitda": 3.0,
    }
    for key, value in overrides.items():
        if key in _LIST_KEYS:
            fields[key] = tuple(normalize_ticker(v) for v in value)
        elif key in _SCALAR_KEYS:
            fields[key] = value
        else:
            fields[key] = _as_range(value)
    return Criteria(**fields)


def _years_since(listed: date, today: date) -> float:
    return (today - listed).days / _DAYS_PER_YEAR


def industry_candidates(
    target_row: UniverseRow, universe: Iterable[UniverseRow], min_listing_years: float, today: date | None = None
) -> list[UniverseRow]:
    """Cheap pre-filter run before any Yahoo fetch. Unknown listing dates are kept for apply_filters to judge."""
    today = today or date.today()
    return [
        r for r in universe
        if r.symbol != target_row.symbol
        and r.industry == target_row.industry
        and (r.listing_date is None or _years_since(r.listing_date, today) >= min_listing_years)
    ]


def _gate(target: Profile, p: Profile, criteria: Criteria, today: date) -> str | None:
    if p.nse_industry != target.nse_industry:
        return "different industry"
    if p.listing_date is None:
        return "missing data: listing date"
    if _years_since(p.listing_date, today) < criteria.min_listing_years:
        return f"listed under {criteria.min_listing_years:g} years"
    if p.ebitda_cr is None:
        return "missing data: ebitda"
    if p.ebitda_cr <= 0:
        return "negative EBITDA"
    return None


def _leverage(p: Profile) -> float | None:
    return net_debt_to_ebitda(p.net_debt_cr, 0.0, p.ebitda_cr) if p.net_debt_cr is not None else None


def _active_checks(criteria: Criteria) -> list[tuple[str, Any]]:
    """(label, value getter) for each active criterion, paired with its test below."""
    checks = [(label, attr) for attr, label in _RANGE_CRITERIA if getattr(criteria, attr).is_active]
    if criteria.max_net_debt_ebitda is not None:
        checks.append(("net debt", "_leverage"))
    if criteria.min_daily_value_cr is not None:
        checks.append(("liquidity", "avg_daily_value_cr"))
    return checks


def _value(p: Profile, attr: str) -> float | None:
    return _leverage(p) if attr == "_leverage" else getattr(p, attr)


def _breach(criteria: Criteria, label: str, attr: str, value: float) -> str | None:
    if attr == "_leverage":
        cap = criteria.max_net_debt_ebitda
        return f"net debt/EBITDA above {cap:g}x" if value > cap else None
    if attr == "avg_daily_value_cr":
        floor = criteria.min_daily_value_cr
        return f"liquidity below {floor:g} cr per day" if value < floor else None
    return None if getattr(criteria, attr).contains(value) else f"outside {label} range"


def apply_filters(
    target: Profile, candidates: Sequence[Profile], criteria: Criteria, today: date | None = None
) -> ScreenResult:
    today = today or date.today()
    excluded: list[Exclusion] = []
    pool: list[Profile] = []
    forced: set[str] = set()
    for p in candidates:
        if p.symbol == target.symbol:
            continue
        if p.symbol in criteria.exclude:
            excluded.append(Exclusion(p.symbol, "excluded by user"))
        elif p.symbol in criteria.include:
            forced.add(p.symbol)
        elif (reason := _gate(target, p, criteria, today)) is not None:
            excluded.append(Exclusion(p.symbol, reason))
        else:
            pool.append(p)

    checks = _active_checks(criteria)
    dropped: list[str] = []
    live_checks = []
    for label, attr in checks:
        missing = sum(1 for p in pool if _value(p, attr) is None)
        if pool and missing * 2 > len(pool):
            dropped.append(f"{label} filter dropped: data missing for {missing} of {len(pool)} candidates")
        else:
            live_checks.append((label, attr))

    kept_by_symbol: dict[str, Profile] = {}
    for p in pool:
        reason = None
        for label, attr in live_checks:
            value = _value(p, attr)
            reason = f"missing data: {label}" if value is None else _breach(criteria, label, attr, value)
            if reason:
                break
        if reason:
            excluded.append(Exclusion(p.symbol, reason))
        else:
            kept_by_symbol[p.symbol] = p
    kept = tuple(p for p in candidates if p.symbol in kept_by_symbol or (p.symbol in forced and p.symbol != target.symbol))
    return ScreenResult(kept=kept, excluded=tuple(excluded), dropped_criteria=tuple(dropped))


def size_review_candidates(
    target: Profile, candidates: Sequence[Profile], criteria: Criteria, today: date | None = None,
    user_set_market_cap: bool = False,
) -> list[Profile]:
    """Peers too small for the normal market-cap band but passing every other filter. Auto mode only."""
    band = criteria.market_cap_cr
    if user_set_market_cap or target.market_cap_cr is None or band.lo is None:
        return []
    normal = {p.symbol for p in apply_filters(target, candidates, criteria, today).kept}
    wider = replace(criteria, market_cap_cr=Range(target.market_cap_cr * REVIEW_SIZE_FLOOR, band.hi))
    return [p for p in apply_filters(target, candidates, wider, today).kept if p.symbol not in normal]
