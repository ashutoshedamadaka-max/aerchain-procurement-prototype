"""Pure procurement conversion arithmetic.

All operations preserve precision; callers choose display rounding. Results unpack as
(value, derivation), except currency results which also carry provenance as item three.
No exchange-rate lookup or ambient clock is used.
"""
from __future__ import annotations

from datetime import date as Date
from math import isfinite
from typing import Iterable, NamedTuple, TypedDict


class Calculation(NamedTuple):
    value: float
    derivation: str


class CurrencyProvenance(TypedDict):
    source: str
    date: str
    rate: float


class CurrencyConversion(NamedTuple):
    value: float
    derivation: str
    provenance: CurrencyProvenance


def _number(value: float, name: str, *, positive: bool = False) -> float:
    result = float(value)
    if not isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")
    return result


def blank_area_m2(length_mm: float, width_mm: float, height_mm: float,
                  glue_lap_mm: float = 40) -> Calculation:
    """RSC blank area: (2*(L+W)+glue lap)*(H+W)/1,000,000, in m²."""
    l = _number(length_mm, "length_mm", positive=True)
    w = _number(width_mm, "width_mm", positive=True)
    h = _number(height_mm, "height_mm", positive=True)
    g = _number(glue_lap_mm, "glue_lap_mm")
    value = (2 * (l + w) + g) * (h + w) / 1_000_000
    return Calculation(value, f"(2 × ({l:g} + {w:g}) + {g:g}) × ({h:g} + {w:g}) / 1000000 = {value:.12g} m²")


def effective_gsm(liner_gsms: Iterable[float], medium_gsms: Iterable[float],
                  take_up_factor: float = 1.45) -> Calculation:
    """Sum liner GSM plus each medium GSM times the positive take-up factor."""
    liners = tuple(_number(x, "liner GSM", positive=True) for x in liner_gsms)
    mediums = tuple(_number(x, "medium GSM", positive=True) for x in medium_gsms)
    if not liners or not mediums:
        raise ValueError("At least one liner and one medium are required")
    factor = _number(take_up_factor, "take_up_factor", positive=True)
    value = sum(liners) + sum(x * factor for x in mediums)
    terms = [f"{x:g}" for x in liners] + [f"({x:g} × {factor:g})" for x in mediums]
    return Calculation(value, f"{' + '.join(terms)} = {value:.12g} g/m²")


def board_weight_kg(blank_area_m2: float, effective_gsm: float) -> Calculation:
    """Return area in m² times effective GSM / 1000, without intermediate rounding."""
    area = _number(blank_area_m2, "blank_area_m2")
    gsm = _number(effective_gsm, "effective_gsm")
    value = area * gsm / 1000
    return Calculation(value, f"{area:.12g} m² × {gsm:.12g} g/m² / 1000 = {value:.12g} kg")


def price_per_piece_from_per_kg(rate_per_kg: float, board_weight_kg: float) -> Calculation:
    """Return currency/kg times kg/piece in the input currency per piece."""
    rate = _number(rate_per_kg, "rate_per_kg")
    weight = _number(board_weight_kg, "board_weight_kg")
    value = rate * weight
    return Calculation(value, f"{rate:.12g}/kg × {weight:.12g} kg/piece = {value:.12g}/piece")


def price_per_piece_from_per_100(rate_per_100: float) -> Calculation:
    """Divide the quoted price for 100 pieces by 100."""
    rate = _number(rate_per_100, "rate_per_100")
    value = rate / 100
    return Calculation(value, f"{rate:.12g} / 100 = {value:.12g}/piece")


def convert_currency(amount: float, rate: float, source: str,
                     date: str | Date) -> CurrencyConversion:
    """Multiply by target-currency units per source unit; stamp the supplied rate source/date.

    Date is an ISO calendar date (YYYY-MM-DD) or datetime.date. No date or rate is
    inferred. The caller chooses the currency pair and supplies the corresponding rate.
    """
    amount = _number(amount, "amount")
    rate = _number(rate, "rate", positive=True)
    if not isinstance(source, str) or not source.strip():
        raise ValueError("A non-empty exchange-rate source is required")
    stamp = date.isoformat() if isinstance(date, Date) else date
    if not isinstance(stamp, str) or Date.fromisoformat(stamp).isoformat() != stamp:
        raise ValueError("date must be an ISO calendar date (YYYY-MM-DD)")
    value = amount * rate
    provenance: CurrencyProvenance = {"source": source, "date": stamp, "rate": rate}
    return CurrencyConversion(value, f"{amount:.12g} × {rate:.12g} = {value:.12g} ({source}, {stamp})", provenance)
