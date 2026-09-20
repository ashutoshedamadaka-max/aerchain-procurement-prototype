"""Freight decision: landed cost is unknown by default; a stated figure wins; inclusive terms are unaffected; the estimate is opt-in."""
import json
import pathlib
import sqlite3

import pytest

from src.normalize import Assumptions, classify_freight, normalize_all

SCHEMA = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "schema.sql").read_text()


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO rfx_lines (line_no,sku,length_mm,width_mm,height_mm,ply,flute,liner_gsm,gsm_stack,bf,print_spec,annual_qty) "
                "VALUES (1,'S1',300,200,150,3,'C',150,'150/120/150',18,'plain',100000)")
    for i in range(1, 6):
        con.execute("INSERT INTO vendors (vendor_id,name,response_format) VALUES (?,?,?)", (f"V{i}", f"Vendor {i}", "x"))
        con.execute("INSERT INTO submissions (submission_id,vendor_id,file_name,format,received_on,expired_at_eval,eval_date,currency) VALUES (?,?,?,?,?,?,?,?)",
                    (f"S{i}", f"V{i}", f"f{i}", "x", "2026-03-01", 0, "2026-03-16", "INR"))
        con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,anchor,snippet) VALUES (?,1,'unit_price',10,'INR/piece','per_piece','INR','extracted','A1','10')", (f"S{i}",))
        con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,state,anchor,snippet) VALUES (?,1,'declared_liner_gsm',150,'extracted','A1','150')", (f"S{i}",))
    return con


def freight(con, sid, snippet=None, value=None, unit=None, incoterm=None):
    if snippet:
        con.execute("INSERT INTO conditions (submission_id,kind,value_num,value_text,snippet,anchor) VALUES (?,'freight',?,?,?,'B5')", (sid, value, unit, snippet))
    if incoterm:
        con.execute("UPDATE submissions SET incoterm=? WHERE submission_id=?", (incoterm, sid))


def row(con, sid):
    con.row_factory = sqlite3.Row
    r = dict(con.execute("SELECT * FROM norm_prices WHERE submission_id=?", (sid,)).fetchone())
    con.row_factory = None
    return r


def test_default_is_unknown_landed_cost_and_not_comparable(db):
    freight(db, "S1", "Freight extra.")
    normalize_all(db)
    r = row(db, "S1")
    assert (r["freight_terms"], r["freight_status"], r["landed_inr_per_piece"]) == ("extra", "not_evaluated", None)
    assert r["landed_comparability"] == "not_comparable" and "freight_unknown" in r["landed_reasons"]
    assert r["comparability"] == "comparable" and r["inr_per_piece"] == 10            # the ex-freight price flag is unchanged
    value, editable = db.execute("SELECT value, editable FROM assumptions WHERE assumption_id='freight_estimate_pct_of_order_value'").fetchone()
    assert value is None and editable == 1 and Assumptions().freight_estimate_pct is None


def test_estimate_moves_extra_rows_to_comparable_with_assumptions_and_states_its_impact(db):
    freight(db, "S1", "Freight extra.")
    normalize_all(db, Assumptions(freight_estimate_pct=5.0))
    r = row(db, "S1")
    assert (r["freight_status"], r["freight_pct"]) == ("estimated", 5.0)
    assert r["landed_inr_per_piece"] == pytest.approx(10.5) and r["freight_inr_per_piece"] == pytest.approx(0.5)
    assert r["landed_comparability"] == "comparable_with_assumptions" and "assumption:freight_estimate" in r["landed_reasons"]
    used = {a["id"]: a for a in json.loads(r["assumptions_used"])}
    assert used["freight_estimate_pct_of_order_value"]["impact_if_changed_1pct_inr_pc"] == pytest.approx(10 * 0.05 * 0.01)


def test_a_stated_figure_beats_the_estimate(db):
    freight(db, "S1", "Freight 3% of order value.", 3.0, "pct_of_order_value")
    freight(db, "S2", "Freight Rs 0.40 per box.", 0.40, "inr_per_piece")
    normalize_all(db, Assumptions(freight_estimate_pct=9.0))
    a, b = row(db, "S1"), row(db, "S2")
    assert (a["freight_status"], a["landed_inr_per_piece"]) == ("stated", pytest.approx(10.3))
    assert (b["freight_status"], b["landed_inr_per_piece"]) == ("stated", pytest.approx(10.4))
    assert a["landed_comparability"] == "comparable" and "estimate" not in a["landed_reasons"]          # a vendor's own figure is not an assumption


def test_freight_included_by_incoterm_is_unaffected_by_the_estimate(db):
    freight(db, "S3", incoterm="DDP")
    freight(db, "S4", "Freight included in the price.")
    normalize_all(db, Assumptions(freight_estimate_pct=9.0))
    for sid in ("S3", "S4"):
        r = row(db, sid)
        assert (r["freight_terms"], r["freight_status"], r["landed_inr_per_piece"], r["landed_comparability"]) == ("included", "included", 10, "comparable")


def test_estimate_only_applies_to_extra_not_to_silent_or_contradictory_vendors(db):
    freight(db, "S5", incoterm="DDP")
    freight(db, "S5", "Freight extra.")                                              # contradicts the incoterm
    normalize_all(db, Assumptions(freight_estimate_pct=9.0))
    silent, conflict = row(db, "S1"), row(db, "S5")                                   # S1 says nothing about freight
    assert (silent["freight_terms"], silent["freight_status"]) == ("unknown", "not_evaluated")
    assert (conflict["freight_terms"], conflict["freight_status"]) == ("unknown", "not_evaluated")
    assert silent["landed_comparability"] == "not_comparable"


def test_a_price_that_is_not_comparable_stays_so_for_landed_cost(db):
    freight(db, "S1", "Freight extra.")
    db.execute("UPDATE bid_fields SET value=120 WHERE submission_id='S1' AND field_name='declared_liner_gsm'")
    normalize_all(db, Assumptions(freight_estimate_pct=5.0))
    r = row(db, "S1")
    assert r["comparability"] == "not_comparable" and r["landed_comparability"] == "not_comparable" and "spec_variance" in r["landed_reasons"]


def test_terms_are_classified_conservatively():
    c = lambda *s, inc=None: classify_freight(inc, [(None, None, x, "B5") for x in s])["terms"]
    assert c("Freight extra at actuals, ex-works.") == "extra" and c("Transport extra.") == "extra" and c("Freight not included.") == "extra"
    assert c("Freight included in the price.") == "included" and c("Delivered to your plant.") == "included"
    assert c("Freight as agreed.") == "unknown" and c() == "unknown" and c(inc="EXW") == "extra" and c(inc="CIF") == "unknown"


def test_precedence_is_stated_then_vendor_override_then_global_then_nothing(db):
    freight(db, "S1", "Freight 3% of order value.", 3.0, "pct_of_order_value")      # stated: beats an override and the global estimate
    for sid in ("S2", "S3", "S4"):
        freight(db, sid, "Freight extra.")
    normalize_all(db, Assumptions(freight_estimate_pct=4.0, freight_by_vendor=(("V1", 9.0), ("V2", 7.0))))
    assert [(row(db, s)["freight_status"], row(db, s)["freight_pct"]) for s in ("S1", "S2", "S3")] == [("stated", 3.0), ("estimated", 7.0), ("estimated", 4.0)]
    normalize_all(db, Assumptions(freight_by_vendor=(("V2", 7.0),)))                # no global estimate: only the overridden vendor is estimated
    assert [row(db, s)["freight_status"] for s in ("S2", "S3", "S4")] == ["estimated", "not_evaluated", "not_evaluated"]


def test_each_override_is_a_stored_assumption_with_its_impact_per_1_percent(db):
    freight(db, "S2", "Freight extra.")
    normalize_all(db, Assumptions(freight_by_vendor=(("V2", 4.0),)))
    value, unit, blob = db.execute("SELECT value, unit, value_text FROM assumptions WHERE assumption_id='freight_estimate_pct:V2'").fetchone()
    impact = json.loads(blob)
    assert value == 4.0 and unit == "% of order value" and impact["applies_to_value_inr"] == pytest.approx(100_000 * 10)
    assert impact["impact_per_1_percentage_point_inr"] == pytest.approx(10_000) and impact["impact_per_1pct_relative_change_inr"] == pytest.approx(400)
    used = {a["id"]: a for a in json.loads(row(db, "S2")["assumptions_used"])}
    assert used["freight_estimate_pct:V2"]["impact_if_changed_1pct_inr_pc"] == pytest.approx(10 * 0.04 * 0.01)
    assert "vendor override" in row(db, "S2")["freight_source"]


def test_an_override_on_a_vendor_that_cannot_use_it_says_so(db):
    freight(db, "S3", incoterm="DDP")
    normalize_all(db, Assumptions(freight_by_vendor=(("V3", 5.0),)))
    note = db.execute("SELECT note FROM assumptions WHERE assumption_id='freight_estimate_pct:V3'").fetchone()[0]
    assert row(db, "S3")["freight_status"] == "included" and "Applies to no rows" in note
