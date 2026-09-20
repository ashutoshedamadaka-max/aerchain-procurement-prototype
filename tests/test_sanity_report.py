"""The sanity report's consistency checks must be able to fire. Seed a scratch copy of the database with known faults and require the right check ids to flag them;
a clean, fully run copy must flag nothing. (The report itself is read-only; these mutations happen on a copy.)"""
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (ROOT / "procurement.db").exists(), reason="procurement.db not generated")


def run_report(db, tmp_path):
    env = {**os.environ, "SANITY_DB": str(db), "SANITY_OUT": str(tmp_path / "SANITY.md")}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sanity_report.py")], cwd=ROOT, env=env, capture_output=True, text=True, timeout=280)
    assert r.returncode == 0, r.stderr[-800:]
    md = (tmp_path / "SANITY.md").read_text(encoding="utf-8")
    flagged = set(re.findall(r"\| (?:[a-z_ -]+) \| ([A-Z]\d+) \| .*? \| \d+ \| FLAG \|", md))
    return flagged, md


def test_a_clean_database_flags_nothing(tmp_path):
    flagged, md = run_report(ROOT / "procurement.db", tmp_path)
    assert flagged == set(), flagged
    assert "Nothing below has been fixed" in md and "**Not run on this database.**" not in md


def test_seeded_faults_are_all_caught(tmp_path):
    db = tmp_path / "seeded.db"
    shutil.copy(ROOT / "procurement.db", db)
    con = sqlite3.connect(db)
    one = lambda sql: con.execute(sql)
    one("UPDATE bid_fields SET reason_code=NULL WHERE submission_id='S1' AND rfx_line_no=12 AND field_name='unit_price'")                         # B1
    one("UPDATE bid_fields SET resolving_question=NULL WHERE field_id=(SELECT MIN(field_id) FROM bid_fields WHERE state='missing')")                # B2
    one("UPDATE bid_fields SET value=NULL WHERE field_id=(SELECT MIN(field_id) FROM bid_fields WHERE state='extracted' AND field_name='unit_price')")  # B3
    one("UPDATE bid_fields SET value=9.9 WHERE field_id=(SELECT MIN(field_id) FROM bid_fields WHERE state='not_quoted')")                              # B4
    one("UPDATE bid_fields SET anchor=NULL WHERE field_id=(SELECT MAX(field_id) FROM bid_fields WHERE state='extracted')")                             # B5
    one("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,state) SELECT submission_id,rfx_line_no,field_name,value,state FROM bid_fields WHERE field_id=(SELECT MIN(field_id) FROM bid_fields)")   # B6
    one("UPDATE bid_fields SET unit='USD/kg' WHERE submission_id='S1' AND rfx_line_no=5 AND field_name='unit_price'")                                # B8
    one("UPDATE bid_fields SET reason_code='arith_mismatch' WHERE submission_id='S1' AND rfx_line_no=2 AND field_name='unit_price'")                 # B9 and B10
    one("UPDATE bid_fields SET norm_inr_pc=1.0 WHERE field_id=(SELECT MIN(field_id) FROM bid_fields)")                                                                                     # B12
    one("UPDATE norm_prices SET comparability_reasons='[]' WHERE comparability='not_comparable' AND rowid=(SELECT MIN(rowid) FROM norm_prices WHERE comparability='not_comparable')")   # N4
    one("UPDATE norm_prices SET landed_inr_per_piece=5 WHERE rowid=(SELECT MIN(rowid) FROM norm_prices WHERE freight_status='not_evaluated' AND inr_per_piece IS NOT NULL)")            # N8
    one("UPDATE norm_prices SET extraction_state='extracted' WHERE submission_id='S1' AND rfx_line_no=12")                                            # N2
    one("UPDATE norm_prices SET inr_per_piece=inr_per_piece*2 WHERE submission_id='S1' AND rfx_line_no=1")                                            # N10
    one("UPDATE norm_prices SET comparability='comparable_with_assumptions', comparability_reasons='[\"x\"]', assumptions_used='[]' WHERE submission_id='S2' AND rfx_line_no=1")   # N6
    one("UPDATE conditions SET value_num=NULL WHERE kind='moq' AND submission_id='S2'")                                                               # C1, M1, M2
    one("UPDATE conditions SET kind='bundle' WHERE kind='moq' AND submission_id='S1'")                                                                 # M3 (a MOQ statement no longer recorded as one)
    one("UPDATE tier_rules SET effect_pct=5 WHERE kind='order_value_discount'")                                                                       # T2
    one("UPDATE tier_rules SET max_value_inr=8000000 WHERE kind='order_value_uplift' AND min_value_inr=7500000")                                       # T4 (gap)
    one("UPDATE tier_rules SET rfx_line_no=99 WHERE kind='line_qty_break' AND rule_id=(SELECT MIN(rule_id) FROM tier_rules WHERE kind='line_qty_break')")   # T3
    one("UPDATE submissions SET expired_at_eval=1 WHERE submission_id='S1'")                                                                          # S2
    one("UPDATE submissions SET valid_until='2030-01-01' WHERE submission_id='S2'")                                                                   # S3
    one("UPDATE submissions SET file_name='does_not_exist.xlsx' WHERE submission_id='S5'")                                                            # S6
    one("UPDATE vendors SET archetype='leaked' WHERE vendor_id='V1'")                                                                                 # X2
    one("DELETE FROM assumptions WHERE assumption_id='take_up_factor:BC'")                                                                            # A1
    one("DELETE FROM norm_prices WHERE submission_id='S5' AND rfx_line_no=1")                                                                         # N1
    from src.extract import questionnaire as Q                                                                                                        # Q1, Q2 (need the pipeline columns)
    Q.ensure_columns(con)
    one("INSERT INTO questionnaire_answers (vendor_id,q_no,question,is_gate,gate_code,answer_text,state) VALUES ('V1',91,'q',1,'G1','a','extracted')")
    one("INSERT INTO questionnaire_answers (vendor_id,q_no,question,is_gate,answer_text,state) VALUES ('V1',92,'q',0,'a','claimed_unsupported')")
    con.commit()
    con.close()
    flagged, md = run_report(db, tmp_path)
    expected = {"B1", "B2", "B3", "B4", "B5", "B6", "B8", "B9", "B10", "B12", "N1", "N2", "N4", "N6", "N8", "N10", "A1", "C1", "M1", "M2", "M3", "T2", "T3", "T4", "S2", "S3", "S6", "X2", "Q1", "Q2"}
    assert expected <= flagged, sorted(expected - flagged)
