"""The analyst model must never be shown a raw float: money, rates and percentages are formatted at the tool boundary (src/present.py)."""
import json
import pathlib
import types

import pytest

from src import present
from src.analyst import Analyst, RULES, Store

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / "procurement.db"
live = pytest.mark.skipif(not DB.exists(), reason="procurement.db is not part of the public repository")


def numeric_fields(node, path=""):
    """Every int/float in a JSON-decoded tool response, with its path. Strings (pre-formatted money, as-quoted rates) are not numeric fields."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from numeric_fields(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from numeric_fields(v, f"{path}[{i}]")
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield path, node


def decimal_digits(x):
    if isinstance(x, int):
        return 0
    text = repr(x)
    return 99 if "e" in text.lower() else len(text.split(".")[1])


def test_formatters():
    assert present.rupees(18712301.1404928) == "₹1,87,12,301"
    assert present.rupees(-480827.18) == "−₹4,80,827"
    assert present.rupees(40200000) == "₹4,02,00,000" and present.rupees(348000) == "₹3,48,000"
    assert present.rate(6.726) == "₹6.73"
    assert present.percent(0.25749096498976093) == "0.26%" and present.percent(-1.257228) == "−1.26%" and present.percent(6.0) == "6%" and present.percent(0.001) == "0%"
    assert present.quoted(0.076) == "0.076" and present.quoted(31.93) == "31.93" and present.quoted(38.0) == "38.00"


def test_for_model_formats_by_kind_and_leaves_the_rest():
    raw = {"repriced_total": 38941265.34176001, "saving": {"naive": 1188818.37824, "repriced": -480827.18, "repriced_pct": -1.257228111096526},
           "per_vendor": {"V1": {"total": 10190702.200000001, "share": 0.2616941722505265, "lines": 4, "order_value_effect_pct": 6.0}},
           "params": {"share": 0.7}, "needs_review_warnings": [{"value": 1838955.0, "unit": "INR", "share_of_award": 0.03495458835386882},
                                                             {"value": 31.93, "unit": "INR/piece", "value_at_risk_inr": 1361175.9}],
           "lines": 30, "note": "text stays", "weight_kg": 0.12345678}
    out = present.for_model(raw)
    assert out["repriced_total"] == "₹3,89,41,265" and out["saving"] == {"naive": "₹11,88,818", "repriced": "−₹4,80,827", "repriced_pct": "−1.26%"}
    assert out["per_vendor"]["V1"] == {"total": "₹1,01,90,702", "share": "26.17%", "lines": 4, "order_value_effect_pct": "6%"}
    assert out["params"]["share"] == 0.7                                             # an input parameter, not an award share
    assert out["needs_review_warnings"][0] == {"value": "₹18,38,955", "unit": "INR", "share_of_award": "3.5%"}
    assert out["needs_review_warnings"][1]["value"] == 31.93 and out["needs_review_warnings"][1]["value_at_risk_inr"] == "₹13,61,176"
    assert out["lines"] == 30 and out["note"] == "text stays" and out["weight_kg"] == 0.12


def test_the_prompt_states_the_money_and_percentage_rule():
    assert "use ₹X,XX,XXX (Indian comma grouping) with no more than 2 decimal places. Percentages: max 2 decimal places." in RULES


@pytest.fixture(scope="module")
def analyst():
    return Analyst(store=Store(DB), client=types.SimpleNamespace(), provider="openai")


@live
def test_every_tool_response_on_the_current_database_has_no_numeric_field_beyond_two_decimals(analyst):
    calls = [("event_summary", {}), ("gate_summary", {}), ("review_exposure", {}),
             ("run_sql", {"query": "SELECT inr_per_piece, landed_inr_per_piece, adjusted_inr_per_piece, fx_rate FROM norm_prices LIMIT 20"}),
             ("run_sql", {"query": "SELECT value, unit, currency FROM bid_fields WHERE currency='USD' LIMIT 20"})]
    calls += [("run_award", {"scenario_name": sc, "params": {"basis": "ex_freight", "moq_policy": None, "gated": g, "vendor": None, "n": None, "share": None}, "baseline_vendor": None})
              for sc, g in (("single_vendor", True), ("cheapest_per_line", False), ("gated_split", True), ("max_vendors", True), ("max_share", True))]
    calls += [("explain_field", {"vendor_id": v, "line_no": n, "field": "unit_price"}) for v, n in (("V1", 12), ("V2", 20), ("V3", 13), ("V4", 27), ("V4", 1), ("V5", 1))]
    for name, args in calls:
        response = json.loads(json.dumps(analyst._dispatch(name, args)))              # what the model receives, as JSON
        too_long = [(p, v) for p, v in numeric_fields(response) if decimal_digits(v) > 2]
        assert not too_long, (name, args, too_long[:5])


@live
def test_the_headline_figures_are_pre_formatted_and_match_the_raw_tools(analyst):
    store = analyst.store
    exposure = analyst._dispatch("review_exposure", {})
    raw = store.review_exposure()["known_exposure_inr"]
    assert exposure["known_exposure_inr"] == f"₹{present.indian(round(raw), 0)}"
    award = analyst._dispatch("run_award", {"scenario_name": "gated_split", "params": {"basis": "ex_freight", "gated": True}, "baseline_vendor": None})
    raw_award = store.run_award("gated_split", {"basis": "ex_freight", "gated": True})
    assert award["saving"]["repriced_pct"] == present.percent(raw_award["saving"]["repriced_pct"])
    assert award["repriced_total"] == present.rupees(raw_award["repriced_total"]) and award["repriced_total"].startswith("₹")
