"""Award engine tests on a small synthetic event where the naive split loses money once its conditions are re-applied."""
import pathlib
import sqlite3

import pytest

from src import award
from src.normalize import Assumptions, normalize_all

SCHEMA = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "schema.sql").read_text()
QTY = {1: 100_000, 2: 50_000, 3: 2_000, 4: 10_000}
PRICES = {"V1": {1: 10.0, 2: 10.0, 3: 10.0, 4: 10.0}, "V2": {1: 10.4, 2: 9.0, 3: 9.0, 4: 12.0}, "V3": {1: 9.0, 2: 9.5, 3: 9.5, 4: 9.5}}
GATES = ["V1", "V2"]                        # V3 is the cheapest vendor and fails the gates


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.executescript(SCHEMA)
    for n, q in QTY.items():
        con.execute("INSERT INTO rfx_lines (line_no,sku,length_mm,width_mm,height_mm,ply,flute,liner_gsm,gsm_stack,bf,print_spec,annual_qty) "
                    "VALUES (?,?,300,200,150,3,'C',150,'150/120/150',18,'plain',?)", (n, f"S{n}", q))
    for i, (v, moq, until, expired) in enumerate((("V1", 1000, "2026-04-01", 0), ("V2", 3000, "2026-04-01", 0), ("V3", 500, "2026-03-14", 1)), 1):
        con.execute("INSERT INTO vendors (vendor_id,name,response_format,moq_pcs) VALUES (?,?,?,?)", (v, f"Vendor {i}", "x", moq))
        con.execute("INSERT INTO submissions (submission_id,vendor_id,file_name,format,received_on,valid_until,expired_at_eval,eval_date,currency) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"S{i}", v, f"f{i}", "x", "2026-03-01", until, expired, "2026-03-16", "INR"))
        con.execute("INSERT INTO conditions (submission_id,kind,snippet,anchor) VALUES (?,'freight','Freight extra.','B5')", (f"S{i}",))
        for n, p in PRICES[v].items():
            con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,anchor,snippet) VALUES (?,?,'unit_price',?,'INR/piece','per_piece','INR','extracted','A1','x')", (f"S{i}", n, p))
            con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,state,anchor,snippet) VALUES (?,?,'declared_liner_gsm',150,'extracted','A1','150')", (f"S{i}", n))
    con.execute("INSERT INTO tier_rules (vendor_id,kind,min_value_inr,effect_pct) VALUES ('V1','order_value_uplift',1500000,0)")
    con.execute("INSERT INTO tier_rules (vendor_id,kind,min_value_inr,max_value_inr,effect_pct) VALUES ('V1','order_value_uplift',0,1500000,10)")
    normalize_all(con)
    return con


def codes(r):
    return {w["code"]: w for w in r["warnings"]}


def test_cheapest_per_line_is_an_ignoring_gates_view_and_never_recommendable(db):
    r = award.cheapest_per_line(award.load(db))
    assert r["allocation"] == {1: "V3", 2: "V2", 3: "V2", 4: "V3"} and not r["gates_applied"] and not r["recommendable"]
    assert codes(r)["gates_not_applied"]["severity"] == "block" and codes(r)["validity_expired"]["severity"] == "block"


def test_gated_split_refuses_without_gate_results(db):
    r = award.gated_split(award.load(db), None)
    assert r["status"] == "refused" and r["allocation"] == {} and not r["recommendable"]


def test_gates_exclude_the_cheapest_vendor(db):
    r = award.gated_split(award.load(db), GATES)
    assert r["allocation"] == {1: "V1", 2: "V2", 3: "V2", 4: "V1"} and "V3" not in r["vendors_used"] and r["gates_applied"]


def test_the_naive_split_loses_money_after_repricing_at_allocated_volume(db):
    r = award.gated_split(award.load(db), GATES)
    assert r["naive_total"] == pytest.approx(1_568_000)
    # V1 falls under its Rs 15 lakh slab (+10%) and V2 must overbuy 1,000 pieces on line 3 to reach its MOQ
    assert r["repriced_total"] == pytest.approx(1_100_000 * 1.10 + 468_000 + 1_000 * 9.0)
    assert r["saving"]["naive"] == pytest.approx(52_000) and r["saving"]["repriced"] == pytest.approx(-67_000)
    assert "costs" not in r["saving"]["vs"] and "V1" in r["saving"]["vs"]
    assert codes(r)["moq_overbuy"] and codes(r)["discount_not_captured"] and codes(r)["saving_after_repricing"]["severity"] == "warn"


def test_max_vendors_prefers_fewer_vendors_when_splitting_is_punished(db):
    r = award.max_vendors(award.load(db), 2, GATES)
    assert set(r["allocation"].values()) == {"V1"} and r["params"]["subsets_considered"] == 3 and r["repriced_total"] == pytest.approx(1_620_000)


def test_single_vendor_picks_the_cheapest_repriced_and_lists_alternatives(db):
    r = award.single_vendor(award.load(db), gates=GATES)
    assert r["vendors_used"] == ["V1"] and [a["vendor"] for a in r["alternatives"]] == ["V1", "V2"]
    assert r["alternatives"][1]["repriced_total"] == pytest.approx(1_628_000 + 9_000)


def test_max_share_moves_lines_and_reports_when_the_cap_cannot_be_met(db):
    ctx = award.load(db)
    ok, no = award.max_share(ctx, 0.75, GATES), award.max_share(ctx, 0.6, GATES)
    assert ok["status"] == "ok" and ok["allocation"] == award.gated_split(ctx, GATES)["allocation"]
    assert no["status"] == "cap_not_met" and codes(no)["cap_not_met"]["severity"] == "block" and not no["recommendable"]


def test_moq_can_be_priced_as_overbuy_or_treated_as_infeasible(db):
    exclude = award.gated_split(award.load(db, moq_policy="exclude"), GATES)
    assert exclude["allocation"][3] == "V1" and "moq_overbuy" not in codes(exclude)


def test_landed_basis_is_unavailable_until_freight_is_known(db):
    r = award.gated_split(award.load(db, "landed"), GATES)
    assert r["status"] == "infeasible" and r["coverage"]["covered"] == 0 and codes(r)["landed_unknown"]["severity"] == "block"
    normalize_all(db, Assumptions(freight_estimate_pct=5.0))
    ex, landed = award.gated_split(award.load(db), GATES), award.gated_split(award.load(db, "landed"), GATES)
    assert landed["allocation"] == ex["allocation"] and landed["repriced_total"] == pytest.approx(ex["repriced_total"] * 1.05)
    assert codes(landed)["freight_estimated"]["severity"] == "info"


def test_a_review_line_above_the_share_threshold_blocks_the_recommendation(db):
    db.execute("UPDATE norm_prices SET extraction_state='needs_review' WHERE submission_id='S1' AND rfx_line_no=1")
    r = award.gated_split(award.load(db), GATES)
    assert codes(r)["needs_review_lines"]["severity"] == "block" and not r["recommendable"]


def test_a_partial_allocation_states_no_saving_against_the_baseline(db):
    db.execute("UPDATE norm_prices SET comparability='not_comparable' WHERE rfx_line_no=4")
    r = award.gated_split(award.load(db), GATES)
    assert r["coverage"]["uncovered"][0]["line"] == 4 and r["saving"] is None and codes(r)["uncovered_lines"]


def test_a_vendor_with_no_moq_never_breaks_the_uncovered_report(db):
    db.execute("UPDATE vendors SET moq_pcs=NULL WHERE vendor_id='V3'")
    r = award.single_vendor(award.load(db), "V3", GATES)            # V3 is not in the gates: infeasible, and every line is reported as uncovered
    assert r["status"] == "infeasible" and r["coverage"]["covered"] == 0
    assert r["coverage"]["uncovered"][0]["blocked_by"]["V3"] == "eligible (not selected)"


# ---- Warnings section and the editable review block threshold ----
def flag(con, sub, line, field="unit_price", reason="arith_mismatch"):
    con.execute("UPDATE bid_fields SET state='needs_review', reason_code=?, resolving_question=? WHERE submission_id=? AND rfx_line_no=? AND field_name=?",
                (reason, f"Which is right on line {line}?", sub, line, field))
    normalize_all(con)


def test_warnings_list_review_fields_on_awarded_lines_largest_first(db):
    flag(db, "S1", 4)                                                 # small line
    flag(db, "S1", 1, reason="unit_mismatch")                         # big line (100,000 pieces)
    flag(db, "S3", 2)                                                 # V3 is not in the gated pool, so never awarded
    r = award.single_vendor(award.load(db), "V1", GATES)
    ws = r["needs_review_warnings"]
    assert [(w["vendor"], w["line"]) for w in ws] == [("V1", 1), ("V1", 4)]
    assert ws[0]["value_at_risk_lakh"] > ws[1]["value_at_risk_lakh"] > 0
    for k in ("vendor", "line", "field", "value", "reason_code", "value_at_risk_lakh", "resolving_question"):
        assert ws[0][k] is not None
    assert "Warnings:" in award.summarize(r) and "question:" in award.summarize(r)


def test_warnings_do_not_stop_the_scenario_and_flag_the_blockers(db):
    flag(db, "S1", 3)                                                 # 2,000 pieces of 162,000: about 1%, under the threshold
    r = award.single_vendor(award.load(db), "V1", GATES)
    assert r["needs_review_warnings"] and not r["needs_review_warnings"][0]["blocks_recommendation"]
    assert r["status"] != "refused" and r["allocation"]
    flag(db, "S1", 1)                                                 # the biggest line: over 5%
    r = award.single_vendor(award.load(db), "V1", GATES)
    assert any(w["blocks_recommendation"] for w in r["needs_review_warnings"])
    assert not r["recommendable"]


def test_threshold_setting_turns_a_blocker_into_a_caveat(db, tmp_path):
    from src import settings
    flag(db, "S1", 1)
    assert not award.single_vendor(award.load(db), "V1", GATES)["recommendable"]
    db.execute(settings.DDL)
    db.execute("INSERT INTO settings VALUES ('review_block_threshold_pct', 90, '%', NULL)")
    r = award.single_vendor(award.load(db), "V1", GATES)
    assert r["review_block_threshold_pct"] == 90 and r["needs_review_warnings"] and not r["needs_review_warnings"][0]["blocks_recommendation"]
    assert r["recommendable"]


def test_settings_default_persist_and_validate(tmp_path):
    from src import settings
    path = tmp_path / "s.db"
    con = sqlite3.connect(path)
    assert settings.get(con, "review_block_threshold_pct") == 5.0            # no table yet: default
    assert settings.set_value(path, "review_block_threshold_pct", "8") == 8.0
    assert settings.get(con, "review_block_threshold_pct") == 8.0
    for bad in (-1, 101, "abc", None, float("nan")):
        with pytest.raises(ValueError):
            settings.set_value(path, "review_block_threshold_pct", bad)
    with pytest.raises(ValueError):
        settings.set_value(path, "nope", 1)
    assert settings.get(con, "review_block_threshold_pct") == 8.0            # rejected writes changed nothing
    assert settings.describe(con)[0]["is_default"] is False
