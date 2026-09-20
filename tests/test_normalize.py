"""Normalization tests on a small synthetic database (no model calls, no truth)."""
import json
import pathlib
import sqlite3

import pytest

from src.normalize import Assumptions, evaluate_scenario, normalize_all, whole_lines

SCHEMA = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "schema.sql").read_text()
SPECS = {1: (300, 200, 150, 3, "C", 150, "150/120/150"), 17: (500, 350, 300, 5, "BC", 150, "150/120/150/120/150")}


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for n, (L, W, H, ply, fl, g, stack) in SPECS.items():
        con.execute("INSERT INTO rfx_lines (line_no,sku,length_mm,width_mm,height_mm,ply,flute,liner_gsm,gsm_stack,bf,print_spec,annual_qty) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (n, f"S{n}", L, W, H, ply, fl, g, stack, 18, "plain", 100000))
    for i in range(1, 6):
        con.execute("INSERT INTO vendors (vendor_id,name,response_format) VALUES (?,?,?)", (f"V{i}", f"Vendor {i}", "x"))
        con.execute("INSERT INTO submissions (submission_id,vendor_id,file_name,format,received_on,expired_at_eval,eval_date,currency) VALUES (?,?,?,?,?,?,?,?)",
                    (f"S{i}", f"V{i}", f"f{i}", "x", "2026-03-01", 0, "2026-03-16", "INR"))
    return con


def price(con, sid, line, value, basis="per_piece", cur="INR", state="extracted"):
    con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,anchor,snippet) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, line, "unit_price", value, f"{cur}/x", basis, cur, state, "A1", str(value)))


def gsm(con, sid, line, value, state="extracted"):
    con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,state,anchor,snippet) VALUES (?,?,?,?,?,?,?)",
                (sid, line, "declared_liner_gsm", value, state, "A1", str(value)))


def norm(con, sid, line):
    con.row_factory = sqlite3.Row
    r = dict(con.execute("SELECT * FROM norm_prices WHERE submission_id=? AND rfx_line_no=?", (sid, line)).fetchone())
    con.row_factory = None
    return r


def test_per_100_is_lossless_and_comparable(db):
    price(db, "S2", 1, 652.22, "per_100_pieces")
    gsm(db, "S2", 1, 150)
    normalize_all(db)
    r = norm(db, "S2", 1)
    assert r["inr_per_piece"] == pytest.approx(6.5222) and r["comparability"] == "comparable" and r["stated_basis"] == "per_100_pieces"


def test_per_kg_uses_derived_weight_and_states_the_take_up_assumption(db):
    price(db, "S5", 17, 42.0, "per_kg")
    normalize_all(db)
    r = norm(db, "S5", 17)
    assert r["inr_per_piece"] == pytest.approx(42 * 0.902538, rel=1e-5)          # reference weight, 500x350x300 5-ply at take-up 1.45
    assert r["comparability"] == "comparable_with_assumptions"
    used = {a["id"]: a for a in json.loads(r["assumptions_used"])}
    assert used["take_up_factor:BC"]["impact_if_changed_1pct_inr_pc"] > 0
    assert any(s["step"] == "board_weight" for s in json.loads(r["derivation"]))
    value, editable = db.execute("SELECT value, editable FROM assumptions WHERE assumption_id='take_up_factor:BC'").fetchone()
    assert value == pytest.approx(1.45) and editable == 1


def test_usd_is_stamped_and_the_original_stays_visible(db):
    price(db, "S4", 1, 0.076, "per_piece", "USD")
    gsm(db, "S4", 1, 150)
    normalize_all(db)
    r = norm(db, "S4", 1)
    assert r["inr_per_piece"] == pytest.approx(0.076 * 88.5) and r["orig_currency_per_piece"] == 0.076
    assert (r["stated_value"], r["stated_currency"], r["fx_rate"], r["fx_source"], r["fx_date"]) == (0.076, "USD", 88.5, "RBI reference rate", "2026-03-11")
    assert r["comparability"] == "comparable_with_assumptions"
    normalize_all(db, Assumptions(fx_usd_inr=90.0))
    assert norm(db, "S4", 1)["inr_per_piece"] == pytest.approx(0.076 * 90) and norm(db, "S4", 1)["fx_rate"] == 90.0


def test_spec_variance_carries_ratio_and_adjusted_price(db):
    price(db, "S3", 1, 5.52)
    gsm(db, "S3", 1, 120)
    normalize_all(db)
    r = norm(db, "S3", 1)
    assert r["comparability"] == "not_comparable" and "spec_variance" in r["comparability_reasons"]
    assert r["board_weight_ratio"] == pytest.approx(420 / 480)                      # 120+120+120*1.5 against 150+150+120*1.5
    assert r["adjusted_inr_per_piece"] == pytest.approx(5.52 / 0.875) and r["inr_per_piece"] == 5.52
    assert "estimate" in r["adjustment_note"]


def test_unstated_gsm_is_not_comparable_and_never_adjusted(db):
    price(db, "S3", 1, 7.14, state="needs_review")
    gsm(db, "S3", 1, None, state="missing")
    normalize_all(db)
    r = norm(db, "S3", 1)
    assert r["comparability"] == "not_comparable" and "spec_incomplete" in r["comparability_reasons"] and r["adjusted_inr_per_piece"] is None


def test_silent_vendor_gets_a_visible_spec_assumption(db):
    price(db, "S5", 1, 38.0, "per_kg")
    normalize_all(db)
    assert norm(db, "S5", 1)["comparability"] == "comparable_with_assumptions"
    assert db.execute("SELECT count(*) FROM assumptions WHERE assumption_id='spec_assumed_as_rfx:V5'").fetchone()[0] == 1


def test_unpriced_lines_get_no_price_and_no_flag(db):
    price(db, "S3", 1, None, state="not_quoted")
    normalize_all(db)
    r = norm(db, "S3", 1)
    assert r["inr_per_piece"] is None and r["comparability"] is None


def test_freight_is_never_costed(db):
    price(db, "S1", 1, 10.0)
    gsm(db, "S1", 1, 150)
    normalize_all(db)
    assert norm(db, "S1", 1)["freight_status"] == "not_evaluated"


def slab_db(db):
    for pct, lo, hi in ((0.0, 25e6, None), (3.0, 15e6, 25e6), (6.0, 7.5e6, 15e6), (9.0, 0.0, 7.5e6)):
        db.execute("INSERT INTO tier_rules (vendor_id,kind,min_value_inr,max_value_inr,effect_pct) VALUES ('V1','order_value_uplift',?,?,?)", (lo, hi, pct))
    db.execute("INSERT INTO tier_rules (vendor_id,kind,min_value_inr,effect_pct) VALUES ('V2','order_value_discount',5000000,-2.0)")
    db.execute("INSERT INTO tier_rules (vendor_id,kind,rfx_line_no,min_qty,effect_pct) VALUES ('V3','line_qty_break',1,200000,-2.4)")
    for sid in ("S1", "S2", "S3"):
        price(db, sid, 1, 10.0)
        gsm(db, sid, 1, 150)
    normalize_all(db)


def test_slab_is_evaluated_against_awarded_value_and_the_loss_is_reported(db):
    slab_db(db)
    full = evaluate_scenario(db, {1: {"V1": 3_000_000}})            # 30,000,000: top slab, rates as quoted
    split = evaluate_scenario(db, {1: {"V1": 1_000_000}})           # 10,000,000: +6%
    assert full["vendors"]["V1"]["order_value_effect_pct"] == 0.0 and full["unearned_inr"] == 0.0
    assert split["vendors"]["V1"]["order_value_effect_pct"] == 6.0
    assert split["vendors"]["V1"]["net_value"] == pytest.approx(10_600_000) and split["unearned_inr"] == pytest.approx(600_000)


def test_threshold_discount_is_strict_and_unearned_value_is_shown(db):
    slab_db(db)
    at, over = evaluate_scenario(db, {1: {"V2": 500_000}}), evaluate_scenario(db, {1: {"V2": 500_001}})
    assert at["vendors"]["V2"]["order_value_effect_pct"] == 0.0 and at["unearned_inr"] == pytest.approx(100_000)
    assert over["vendors"]["V2"]["order_value_effect_pct"] == -2.0 and over["earned_discount_inr"] > 0 and over["unearned_inr"] == 0


def test_quantity_break_is_evaluated_at_allocated_volume(db):
    slab_db(db)
    earned, missed = evaluate_scenario(db, {1: {"V3": 200_001}}), evaluate_scenario(db, {1: {"V3": 100_000}})
    assert earned["vendors"]["V3"]["lines"][0]["pct"] == -2.4 and earned["earned_discount_inr"] == pytest.approx(200_001 * 10 * 0.024)
    assert missed["vendors"]["V3"]["lines"][0]["pct"] == 0.0 and missed["unearned_inr"] == pytest.approx(100_000 * 10 * 0.024)


def test_not_comparable_and_unpriced_lines_are_excluded_not_estimated(db):
    slab_db(db)
    db.execute("UPDATE norm_prices SET comparability='not_comparable' WHERE submission_id='S3'")
    r = evaluate_scenario(db, {1: {"V3": 500_000}, 17: {"V3": 1000}})
    assert r["vendors"]["V3"]["value_before_order_rules"] == 0
    assert {(e["line"], e["reason"]) for e in r["excluded"]} == {(1, "not_comparable"), (17, "no price")}
    assert whole_lines(db, {1: "V1"}) == {1: {"V1": 100000}}
