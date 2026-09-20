"""Questionnaire and gate extraction. The model is replaced by a labelled STAND-IN that reads the real artifacts perfectly, so these tests exercise everything
around the model (verifier, alignment on the real PDF layout, escalation, gate evaluation, database) but say nothing about how the real model will read them."""
import importlib
import json
import pathlib
import re
import shutil
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from src.extract import questionnaire as Q  # noqa: E402

dataset_text = importlib.import_module("dataset_text")
grade_mod = importlib.import_module("grade_questionnaire")
ART = ROOT / "dataset" / "artifacts"
RFX = json.loads((ART / "rfx.json").read_text()) if (ART / "rfx.json").exists() else None
QS = RFX["questions"] if RFX else []
needs_artifacts = pytest.mark.skipif(not (ART / "attachments").exists() or not (ROOT / "procurement.db").exists(), reason="artifacts or procurement.db not generated")


# ---------------- parsing and evaluation (no artifacts needed) ----------------
def test_dates_are_parsed_as_written_and_never_guessed():
    assert [Q.parse_date(x) for x in ("30-Nov-2026", "31/01/2027", "28 February 2027", "15 Dec 2026", "2026-12-31", "14-Sep-2025")] == [
        "2026-11-30", "2027-01-31", "2027-02-28", "2026-12-15", "2026-12-31", "2025-09-14"]
    assert Q.parse_date("Jan-2026") is None and Q.parse_date("soon") is None and Q.parse_date(None) is None


def ans(text, stance="yes", certs=()):
    return {"q_no": 0, "answer": {"anchor": "A1", "snippet": text}, "stance": stance, "certificates": list(certs)}


def att(kind, valid_to, vendor="V1"):
    return {"attachment_id": f"{vendor}-{kind}", "vendor_id": vendor, "kind": kind, "file_name": "f.pdf", "valid_to": valid_to, "supports": None, "expired_at_eval": 0}


def rows_for(answers, attachments, master=None, still=None, vendor="V1"):
    a = {n: {**v, "q_no": n} for n, v in answers.items()}
    return {r["q_no"]: r for r in Q.evaluate(vendor, "Vendor", a, still or {}, attachments, QS, "2026-03-16", master or {})}


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_a_valid_attachment_supports_the_claim_and_clears_the_gate():
    r = rows_for({1: ans("Yes. BRCGS Grade A")}, [att("brcgs_packaging_cert", "2026-11-30")])[1]
    assert (r["state"], r["gate_status"], r["evidence_source"], r["expiry_date"], r["expiry_source"]) == ("extracted", "pass", "attachment", "2026-11-30", "attachment")


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_a_claim_with_no_attachment_is_claimed_unsupported_and_the_gate_fails():
    r = rows_for({3: ans("Yes, FSC material is available on request.")}, [])[3]
    assert (r["state"], r["gate_status"], r["reason_code"]) == ("claimed_unsupported", "fail", "no_attachment") and "no supporting document" in r["resolving_question"]


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_an_expired_attachment_does_not_support_the_claim():
    r = rows_for({3: ans("Yes. FSC-C1")}, [att("fsc_coc_cert", "2026-03-15")])[3]              # expired the day before the evaluation date
    assert (r["state"], r["gate_status"], r["reason_code"], r["expiry_date"]) == ("claimed_unsupported", "fail", "certificate_expired", "2026-03-15")
    ok = rows_for({3: ans("Yes. FSC-C1")}, [att("fsc_coc_cert", "2026-03-16")])[3]              # valid through the evaluation date
    assert (ok["state"], ok["gate_status"]) == ("extracted", "pass")


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_negative_and_in_progress_answers_fail_the_gate_without_being_claims():
    no, partial = rows_for({2: ans("Testing is done at an outside test house.", "no")}, [])[2], rows_for({1: ans("In progress.", "partial")}, [])[1]
    assert (no["state"], no["gate_status"], no["reason_code"]) == ("extracted", "fail", "answer_negative")
    assert (partial["state"], partial["gate_status"], partial["reason_code"]) == ("extracted", "fail", "not_yet_in_place")


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_an_unanswered_gate_does_not_clear_unless_the_buyer_holds_it_on_file():
    r = rows_for({}, [], vendor="V5")
    assert (r[1]["state"], r[1]["gate_status"], r[1]["reason_code"]) == ("missing", "not_answered", "not_answered") and "gave no answer" in r[1]["resolving_question"]
    master = {"vendors": {"V5": {"gates_on_file": ["G1", "G2", "G3"]}}}
    m = rows_for({}, [], master=master, vendor="V5")
    assert [(m[n]["state"], m[n]["gate_status"], m[n]["evidence_source"]) for n in (1, 2, 3)] == [("missing", "pass", "vendor_master")] * 3
    assert m[4]["gate_status"] is None and m[4]["state"] == "missing"


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_a_certificate_answer_still_unclear_after_two_passes_is_needs_review_and_the_gate_stays_shut():
    r = rows_for({1: ans("maybe", "unclear")}, [], still={1: ["stance_unclear"]})[1]
    assert (r["state"], r["gate_status"]) == ("needs_review", "not_answered") and "clear yes or no" in r["resolving_question"]


@pytest.mark.skipif(not QS, reason="rfx.json not generated")
def test_claimed_unsupported_never_touches_the_pricing_states():
    schema = (ROOT / "scripts" / "schema.sql").read_text()
    bid = schema[schema.index("CREATE TABLE bid_fields"):schema.index("CREATE TABLE tier_rules")]
    assert "claimed_unsupported" not in bid and "claimed_unsupported" in schema


def test_gate_results_distinguish_not_evaluated_from_nobody_passed():
    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "scripts" / "schema.sql").read_text())
    assert Q.gate_results(con) == (None, {})                                                     # no gate rows: not evaluated
    for v in ("V1", "V2"):
        con.execute("INSERT INTO vendors (vendor_id,name,response_format) VALUES (?,?,?)", (v, v, "x"))
        for g, n in (("G1", 1), ("G2", 2), ("G3", 3)):
            st = "fail" if (v, g) == ("V2", "G3") else "pass"
            con.execute("INSERT INTO questionnaire_answers (vendor_id,q_no,question,is_gate,gate_code,answer_text,gate_status,evidence_source) VALUES (?,?,'q',1,?,'a',?,'attachment')", (v, n, g, st))
    passing, detail = Q.gate_results(con)
    assert passing == {"V1"} and detail["V2"]["G3"]["status"] == "fail"


def test_the_pass_columns_are_added_to_an_older_database():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE questionnaire_answers (answer_id INTEGER PRIMARY KEY, vendor_id TEXT, q_no INTEGER)")
    Q.ensure_columns(con)
    Q.ensure_columns(con)                                                                        # idempotent
    assert {"state", "gate_status", "expiry_date", "resolving_question"} <= {r[1] for r in con.execute("PRAGMA table_info(questionnaire_answers)")}


# ---------------- the stand-in model, on the real artifacts ----------------
class StandIn:
    """A perfect reader of the real artifacts. NOT the model. Optional sabotage makes a chosen answer wrong on pass 1 only, or on both passes."""

    def __init__(self, monkeypatch, wrong_once=(), wrong_always=()):
        self.path, self.wrong_once, self.wrong_always, self.calls = None, set(wrong_once), set(wrong_always), []
        real = Q.make_source

        def spy(p):
            self.path = pathlib.Path(p)
            return real(p)
        monkeypatch.setattr(Q, "make_source", spy)
        monkeypatch.setattr(Q.llm, "call_structured", self.call)

    def call(self, model, system, user, schema, effort="none", name=None):
        self.calls.append((self.path.name, "pass1" if model == Q.llm.PASS1_MODEL else "pass2"))
        data = self.answers() if schema is Q.Q_SCHEMA else self.attachment()
        return data, {"model": model, "effort": effort}

    def find(self, src, text, qtext=""):
        if src.kind == "xlsx":
            for ws in src.wb:
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and c.value == text:
                            return f"{ws.title}!{c.coordinate}"
        elif src.kind == "pdf":
            return next((k for k, (t, _) in src.free.items() if text in t), None)
        elif src.kind == "docx":
            return next((f"para{i}" for i, t in src.paras.items() if text in t and qtext in t), None)
        return None

    def answers(self):
        src, vendor, out = Q.make_source(self.path), self.path.name[:2], []
        wrong = self.wrong_always | (self.wrong_once if self.calls[-1][1] == "pass1" else set())
        for q in QS:
            n, text = q["q_no"], dataset_text.ANSWERS[vendor][q["q_no"]][0]
            anchor = self.find(src, text, q["text"])
            stance = (dataset_text.QTRUTH[vendor][n][2] or "not_applicable")
            if anchor is None:
                out.append({"q_no": n, "answer": None, "stance": "not_applicable", "certificates": []})
                continue
            if (vendor, n) in wrong:                                                              # answer taken from the neighbouring question
                other = self.find(src, dataset_text.ANSWERS[vendor][n % 14 + 1][0], QS[n % 14]["text"])
                anchor, text = other, dataset_text.ANSWERS[vendor][n % 14 + 1][0]
            certs = [{"label": {"anchor": anchor, "snippet": text[:12]}, "identifier": None, "date": {"anchor": anchor, "snippet": m.group(2)},
                      "date_kind": "expiry" if "valid" in m.group(1) else "issued_or_calibrated"} for m in re.finditer(r"(valid to|calibrated) (\d{2}-\w{3}-\d{4}|\w{3}-\d{4})", text)]
            out.append({"q_no": n, "answer": {"anchor": anchor, "snippet": text}, "stance": stance, "certificates": certs})
        return {"answers": out}

    def attachment(self):
        src = Q.make_source(self.path)
        lines = {k: t for k, (t, _) in src.free.items()}
        title = lines["p1:L1"].lower()
        kind = ("brcgs_packaging_cert" if "brcgs" in title else "fsc_coc_cert" if "fsc" in title else "bct_calibration_cert" if "calibration" in title else
                "iso9001_cert" if "iso" in title else "bis_is2771_report" if "is 2771" in title else "third_party_lab_report" if "test report" in title else "other")
        cell = lambda prefix: next(({"anchor": k, "snippet": t} for k, t in lines.items() if t.startswith(prefix)), None)
        date = next(({"anchor": k, "snippet": t.split(": ", 1)[1]} for k, t in lines.items() if re.match(r"(Expiry date|Certificate valid to|Next calibration due|Valid until|Report valid to):", t)), None)
        return {"kind": kind, "holder": cell("Certificate holder"), "reference": cell("Reference"), "issuer": cell("Issued by"), "valid_to": date}


@pytest.fixture
def scratch(tmp_path):
    path = tmp_path / "p.db"
    shutil.copy(ROOT / "procurement.db", path)
    return path


def run_all(db, tmp_path):
    return Q.run(db, ART / "rfx.json", ART, ART / "vendor_master.json", None, tmp_path / "log.json")


@needs_artifacts
def test_end_to_end_with_a_perfect_reader_matches_truth_and_clears_the_right_vendors(monkeypatch, scratch, tmp_path):
    StandIn(monkeypatch)
    log = run_all(scratch, tmp_path)
    r = grade_mod.grade(scratch)
    assert r["overall"][("flagged", "wrong")] + r["overall"][("not flagged", "wrong")] == 0, dict(r["diffs"])
    assert r["gate_pass"][0] == r["gate_pass"][1] == {"V1", "V2", "V5"}
    assert r["gates"]["V3"][0] == {"G1": "fail", "G2": "fail", "G3": "fail"} and r["attachments"]["missing"] == [] and r["attachments"]["wrong"] == []
    assert Q.gate_results(sqlite3.connect(scratch))[0] == {"V1", "V2", "V5"} and log["unattributed_attachments"] == []
    assert log["vendors"]["V4"]["skipped"]                                                    # the photographed rate card has no questionnaire to read
    assert all(v["pass1_failures"] == {} for v in log["vendors"].values() if "skipped" not in v)                       # the verifier accepts a correct reading everywhere, incl. the wrapped PDF questions
    v2 = sqlite3.connect(scratch).execute("SELECT state, reason_code FROM questionnaire_answers WHERE vendor_id='V2' AND q_no IN (4,5) ORDER BY q_no").fetchall()
    assert v2 == [("claimed_unsupported", "no_attachment")] * 2


@needs_artifacts
def test_a_misaligned_answer_is_caught_and_rescued_by_the_second_pass(monkeypatch, scratch, tmp_path):
    StandIn(monkeypatch, wrong_once={("V1", 3), ("V2", 3), ("V3", 3)})
    log = run_all(scratch, tmp_path)
    assert all(log["vendors"][v]["pass1_failures"].get("3") == ["answer_not_aligned"] and "3" in log["vendors"][v]["rescued"] for v in ("V1", "V2", "V3"))
    r = grade_mod.grade(scratch)
    assert r["overall"][("flagged", "wrong")] + r["overall"][("not flagged", "wrong")] == 0


@needs_artifacts
def test_an_answer_wrong_on_both_passes_becomes_needs_review_with_a_question(monkeypatch, scratch, tmp_path):
    StandIn(monkeypatch, wrong_always={("V1", 3)})
    run_all(scratch, tmp_path)
    row = sqlite3.connect(scratch).execute("SELECT state, gate_status, reason_code, resolving_question FROM questionnaire_answers WHERE vendor_id='V1' AND q_no=3").fetchone()
    assert row[:2] == ("needs_review", "not_answered") and row[2] == "answer_not_aligned" and row[3]
    assert Q.gate_results(sqlite3.connect(scratch))[0] == {"V2", "V5"}                          # a gate that cannot be read reliably does not clear
