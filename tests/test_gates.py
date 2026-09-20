"""Regression: gated_split before and after the pipeline supplies the gate results. Before, the gates were a manual parameter; after, the analyst reads them from
questionnaire_answers. The questionnaire rows here come from the perfect-reader STAND-IN in test_questionnaire (not the model), so this proves the wiring and the
logic on the real artifacts, not the model's reading."""
import shutil
import sqlite3

import pytest

from src.analyst import Store
from src.extract import questionnaire as Q
from test_questionnaire import ART, ROOT, StandIn, needs_artifacts, run_all

MANUAL = ["V1", "V2", "V5"]


@pytest.fixture
def before_after(monkeypatch, tmp_path):
    """(db before any questionnaire extraction, db after). Same prices and normalization in both."""
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    shutil.copy(ROOT / "procurement.db", before)
    con = sqlite3.connect(before)
    con.execute("DELETE FROM questionnaire_answers")
    con.execute("DELETE FROM attachments")
    con.commit()
    con.close()
    shutil.copy(before, after)
    StandIn(monkeypatch)
    run_all(after, tmp_path)
    return before, after


def gated(db, gates=None):
    return Store(db, gates=gates).run_award("gated_split", {"gated": True})


@needs_artifacts
def test_before_the_questionnaire_the_gated_scenario_has_no_gates_and_refuses(before_after):
    before, _ = before_after
    r = gated(before)
    assert Store(before).gates is None and r["status"] == "refused" and r["reason"] == "gate_results_not_supplied"


@needs_artifacts
def test_after_the_questionnaire_the_pipeline_supplies_the_same_gates_the_buyer_did(before_after):
    _, after = before_after
    manual, pipeline = gated(after, MANUAL), gated(after)                                       # same database; only the source of the gates differs
    assert pipeline["gates_passed"] == MANUAL == manual["gates_passed"]
    assert "V3" not in pipeline["vendors_used"] and set(pipeline["vendors_used"]) == {"V1", "V2", "V5"}
    assert pipeline["lines_by_vendor"] == manual["lines_by_vendor"] and pipeline["naive_total"] == manual["naive_total"] and pipeline["repriced_total"] == manual["repriced_total"]
    assert pipeline["saving"] == manual["saving"] and pipeline["coverage"] == manual["coverage"]
    assert "NOT evaluated" in manual["gates_source"]
    assert "questionnaire_answers" in pipeline["gates_source"] and "vendor-master" in pipeline["gates_source"] and "NOT evaluated" not in pipeline["gates_source"]


@needs_artifacts
def test_v3_is_excluded_because_the_gates_are_evaluated_and_not_because_it_was_left_out(before_after):
    _, after = before_after
    con = sqlite3.connect(after)
    v3 = con.execute("SELECT gate_code, gate_status, state, reason_code FROM questionnaire_answers WHERE vendor_id='V3' AND is_gate=1 ORDER BY q_no").fetchall()
    assert v3 == [("G1", "fail", "extracted", "not_yet_in_place"), ("G2", "fail", "extracted", "answer_negative"), ("G3", "fail", "claimed_unsupported", "no_attachment")]
    ungated = Store(after).run_award("cheapest_per_line", {})                                   # ungated, V3 wins lines: the exclusion is doing real work
    assert "V3" in ungated["vendors_used"] and ungated["gates_applied"] is False
    assert "V3" not in Store(after).gates and set(Store(after).gates) == {"V1", "V2", "V5"}


@needs_artifacts
def test_a_gate_that_stops_being_supported_changes_the_award_without_touching_the_award_engine(before_after):
    _, after = before_after
    con = sqlite3.connect(after)
    con.execute("UPDATE questionnaire_answers SET gate_status='fail', state='claimed_unsupported' WHERE vendor_id='V1' AND q_no=1")   # e.g. V1's BRCGS certificate found expired
    con.commit()
    con.close()
    r = gated(after)
    assert r["gates_passed"] == ["V2", "V5"] and "V1" not in r["vendors_used"]


@pytest.mark.skipif(not (ROOT / "procurement.db").exists() or Q.gate_results(sqlite3.connect(ROOT / "procurement.db"))[0] is None,
                    reason="procurement.db has no questionnaire rows yet: the live extraction has not run (needs model credit)")
def test_the_live_database_supplies_the_same_gates():
    """V1, V2 and V5 clear all three gates and V3 fails all three (V1's questionnaire has its own sheet; V5 clears on the vendor-master record)."""
    assert Store(ROOT / "procurement.db").gates == MANUAL
    _, detail = Q.gate_results(sqlite3.connect(ROOT / "procurement.db"))
    assert {g: r["status"] for g, r in detail["V3"].items()} == {"G1": "fail", "G2": "fail", "G3": "fail"}
    for v in MANUAL:
        assert {g: r["status"] for g, r in detail[v].items()} == {"G1": "pass", "G2": "pass", "G3": "pass"}
