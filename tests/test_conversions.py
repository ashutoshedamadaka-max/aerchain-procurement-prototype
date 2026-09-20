from datetime import date
import pytest
from src.conversions import (
    blank_area_m2, effective_gsm, board_weight_kg,
    price_per_piece_from_per_kg, price_per_piece_from_per_100, convert_currency,
)


def test_five_ply_reference_without_premature_rounding():
    area, area_steps = blank_area_m2(500, 350, 300)
    gsm, gsm_steps = effective_gsm([150, 150, 150], [120, 120])
    weight, weight_steps = board_weight_kg(area, gsm)
    price, price_steps = price_per_piece_from_per_kg(42, weight)
    assert area == pytest.approx(1.131)
    assert gsm == 798
    assert weight == pytest.approx(0.902538)
    assert round(weight, 3) == 0.903  # The brief's 0.902 is truncated, not rounded.
    assert price == pytest.approx(37.906596)
    assert round(price, 1) == 37.9
    assert all("=" in s for s in [area_steps, gsm_steps, weight_steps, price_steps])


def test_alternate_geometry_and_board():
    assert blank_area_m2(300, 200, 150, 0).value == pytest.approx(0.35)
    assert effective_gsm(iter([150, 150]), iter([120]), 1.5).value == 480
    assert board_weight_kg(0.35, 480).value == pytest.approx(0.168)


def test_per_hundred_and_zero_prices():
    assert price_per_piece_from_per_100(5240).value == 52.4
    assert price_per_piece_from_per_100(0).value == 0
    assert price_per_piece_from_per_kg(0, 0.9).value == 0


def test_currency_stamp_is_explicit_and_independent():
    result = convert_currency(2, 88.5, "RBI reference rate", date(2026, 3, 11))
    assert result.value == 177
    assert result.provenance == {"rate": 88.5, "source": "RBI reference rate", "date": "2026-03-11"}
    assert "2 × 88.5 = 177" in result.derivation
    result.provenance["source"] = "edited"
    assert convert_currency(2, 88.5, "RBI", "2026-03-11").provenance["source"] == "RBI"


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_invalid_numeric_inputs(bad):
    for call in [
        lambda: blank_area_m2(bad, 350, 300),
        lambda: blank_area_m2(500, 350, 300, bad),
        lambda: effective_gsm([150, bad], [120]),
        lambda: effective_gsm([150], [120], bad),
        lambda: board_weight_kg(bad, 798),
        lambda: price_per_piece_from_per_kg(bad, 0.9),
        lambda: price_per_piece_from_per_100(bad),
        lambda: convert_currency(1, bad, "RBI", "2026-03-11"),
    ]:
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize("source,stamp", [("", "2026-03-11"), ("RBI", "2026-02-30"), ("RBI", "20260311")])
def test_invalid_provenance(source, stamp):
    with pytest.raises(ValueError):
        convert_currency(1, 88.5, source, stamp)


def test_empty_layers_and_zero_dimensions():
    with pytest.raises(ValueError):
        effective_gsm([], [120])
    with pytest.raises(ValueError):
        effective_gsm([150], [])
    with pytest.raises(ValueError):
        blank_area_m2(0, 350, 300)
