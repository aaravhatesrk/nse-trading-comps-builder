"""Pure financial metrics. Every function returns None when an input is missing or unusable."""
from __future__ import annotations

from typing import Sequence


def roce(ebit: float | None, total_assets: float | None, current_liabilities: float | None) -> float | None:
    """Return on capital employed = EBIT / (total assets - current liabilities)."""
    if ebit is None or total_assets is None or current_liabilities is None:
        return None
    capital = total_assets - current_liabilities
    return ebit / capital if capital > 0 else None


def revenue_cagr(revenues_newest_first: Sequence[float | None], years: int = 3) -> float | None:
    """Compound annual growth over `years`; needs years + 1 annual values, newest first."""
    if len(revenues_newest_first) < years + 1:
        return None
    newest, oldest = revenues_newest_first[0], revenues_newest_first[years]
    if newest is None or oldest is None or newest <= 0 or oldest <= 0:
        return None
    return (newest / oldest) ** (1 / years) - 1


def net_debt_to_ebitda(debt: float | None, cash: float | None, ebitda: float | None) -> float | None:
    """Net debt / EBITDA; negative means net cash. Undefined for non-positive EBITDA."""
    if debt is None or cash is None or ebitda is None or ebitda <= 0:
        return None
    return (debt - cash) / ebitda
