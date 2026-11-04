"""Peer statistics and implied valuation. Pure functions; None means "not meaningful"."""
from __future__ import annotations

from typing import Sequence

from helper.models import Profile

_CRORE = 1e7
_MIN_VALUES_TO_TRIM = 4
_IQR_FENCE = 1.5
_EV_BASES = {"ev_ebitda": "ebitda_cr", "ev_sales": "revenue_cr"}
_PRICE_BASES = {"pe": "pe", "pb": "pb"}


def _percentile(sorted_values: Sequence[float], p: float) -> float:
    """Linear interpolation between closest ranks (same as Excel QUARTILE.INC)."""
    position = (len(sorted_values) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def quartiles(values: Sequence[float]) -> tuple[float, float, float]:
    if not values:
        raise ValueError("quartiles needs at least one value")
    ordered = sorted(values)
    return _percentile(ordered, 0.25), _percentile(ordered, 0.5), _percentile(ordered, 0.75)


def trim_outliers(values: Sequence[float]) -> tuple[list[float], list[int]]:
    """Drop values outside 1.5x IQR of the quartiles. Returns (kept values, removed input indexes)."""
    if len(values) < _MIN_VALUES_TO_TRIM:
        return list(values), []
    q1, _, q3 = quartiles(values)
    spread = (q3 - q1) * _IQR_FENCE
    low, high = q1 - spread, q3 + spread
    kept = [v for v in values if low <= v <= high]
    removed = [i for i, v in enumerate(values) if not low <= v <= high]
    return kept, removed


def implied_price(multiple: str, peer_multiple: float, target: Profile) -> float | None:
    """Per-share price implied for the target by a peer multiple; None when inputs are missing or meaningless.

    EV multiples are bridged to equity with the target's net debt (minorities ignored) and shares outstanding.
    """
    if multiple in _PRICE_BASES:
        own = getattr(target, _PRICE_BASES[multiple])
        if target.price is None or own is None or own <= 0:
            return None
        return target.price * peer_multiple / own
    if multiple in _EV_BASES:
        metric = getattr(target, _EV_BASES[multiple])
        if metric is None or metric <= 0 or target.net_debt_cr is None or not target.shares_out:
            return None
        equity_cr = peer_multiple * metric - target.net_debt_cr
        return equity_cr * _CRORE / target.shares_out if equity_cr > 0 else None
    raise ValueError(f"unknown multiple: {multiple}")
