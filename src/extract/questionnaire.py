"""Questionnaire and gate extraction (additive: uses the same model, verifier and escalation pattern as price extraction, and changes none of it).

  attachments (certificate PDFs)  -> kind, holder, issuer, valid-to date            (model reads, code parses the date and checks the words are in the file)
  a vendor's response document    -> per RFx question: the answer, a stance, and any certificates it mentions with their dates
  deterministic gate evaluation   -> each answer is a claim; a claim is only as good as the document that supports it

Per answer the five-state model applies: extracted, needs_review (tried twice, still not sure), missing (no answer), claimed_unsupported (the vendor claims it
holds something and no valid attachment supports it: none attached, expired, or not issued to the vendor). claimed_unsupported lives here, never on bid_fields.
Per mandatory gate the outcome is pass, fail or not_answered, decided from document content, not from the yes/no. Nothing is imputed: an unanswered gate does not clear.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import sqlite3

from .. import metering
from . import llm
from .pipeline import normalize_anchors
from .sources import DocxSource, EmailSource, PdfSource, _norm
from .workbook import Workbook, split_anchor

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE_CODES = ("G1", "G2", "G3")
DATE_FORMATS = ("%d-%b-%Y", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%B-%Y", "%d.%m.%Y")
PASS_COLUMNS = {"state": "TEXT CHECK (state IN ('extracted','needs_review','not_quoted','missing','claimed_unsupported'))",
                "gate_status": "TEXT CHECK (gate_status IN ('pass','fail','not_answered'))", "reason_code": "TEXT", "stance": "TEXT", "anchor": "TEXT",
                "snippet": "TEXT", "expiry_date": "TEXT", "expiry_source": "TEXT", "evidence_source": "TEXT", "resolving_question": "TEXT"}
QUESTIONS = {
    "no_attachment": "{vendor}: you state you hold {evidence} but no supporting document was attached. Please send the current certificate, showing its expiry date.",
    "certificate_expired": "{vendor}: the {evidence} you attached expired on {expiry}. Please send the renewed certificate.",
    "not_answered": "{vendor} gave no answer to question {q_no} ({short}). Please answer it and attach the evidence.",
    "stance_unclear": "{vendor}: please answer question {q_no} ({short}) with a clear yes or no, and attach the evidence.",
    "date_unparseable": "{vendor}: please restate the expiry date of the certificate in your answer to question {q_no} as DD-Mon-YYYY.",
    "unreadable": "{vendor}: your answer to question {q_no} ({short}) could not be read reliably from the file. Please resend it as text.",
}
KIND_LABEL = {"brcgs_packaging_cert": "BRCGS packaging certificate", "bct_calibration_cert": "box compression tester calibration certificate",
              "fsc_coc_cert": "FSC chain-of-custody certificate", "bis_is2771_report": "BIS IS:2771 test report", "iso9001_cert": "ISO 9001 certificate",
              "third_party_lab_report": "third-party laboratory report"}
FAIL_TO_QUESTION = {"stance_unclear": "stance_unclear", "date_unparseable": "date_unparseable"}


def parse_date(text):
    """Dates as written ('30-Nov-2026', '31/01/2027', '28 February 2027') to ISO. Anything partial or unrecognised is None: code never guesses a day."""
    t = re.sub(r"\s+", " ", (text or "").strip().strip(".,;:")).replace("Sept", "Sep")
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(t, f).date().isoformat()
        except ValueError:
            pass
    return None


SOURCES = {".xlsx": Workbook, ".pdf": PdfSource, ".docx": DocxSource, ".txt": EmailSource, ".eml": EmailSource}


def make_source(path):
    path = pathlib.Path(path)
    return SOURCES[path.suffix.lower()](path)


def full_text(src):
    if src.kind == "xlsx":
        return " ".join(str(c.value) for ws in src.wb for row in ws.iter_rows() for c in row if c.value is not None)
    if src.kind == "pdf":
        return " ".join(t for t, _ in src.free.values())
    if src.kind == "docx":
        return " ".join(src.paras.values())
    return " ".join(src.lines.values())


def context(src, anchor):
    """The text around an answer where its question would be: the whole row, the whole paragraph, or (PDF) the answer line, the line before it and the two after
    (a table's answer sits level with its question's first line; a wrapped question continues just below). A wider window would take in the neighbouring question."""
    if src.kind == "xlsx":
        sheet, ref = split_anchor(anchor)
        if sheet in src.wb.sheetnames and re.fullmatch(r"[A-Z]+[0-9]+", ref):
            return " ".join(str(c.value) for c in src.wb[sheet][int(re.sub(r"[A-Z]+", "", ref))] if c.value is not None)
    if src.kind == "pdf":
        m = re.fullmatch(r"(p\d+):L(\d+)", anchor)
        if m:
            k = int(m.group(2))
            return " ".join(src.free.get(f"{m.group(1)}:L{i}", ("",))[0] for i in range(max(1, k - 1), k + 3))
    return src.text_at(anchor) or ""


def contained(src, cell):
    """A certificate label, number or date is a piece of the answer's own text: it must be in the text at that locator."""
    text = src.text_at(cell["anchor"])
    return bool(text and cell["snippet"].strip() and _norm(cell["snippet"]) in _norm(text))


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= 4}


def overlap(question, text):
    q = words(question)
    return len(q & words(text)) / len(q) if q else 0.0


# ---------------- schemas and prompts ----------------
LOC = "The locator of the exact line or cell, as the dump shows it: for a workbook the sheet name and the cell (e.g. Questionnaire!C3), never the sheet name alone; otherwise the line tag (e.g. p2:L4, para12, L6)."
CELL = {"type": "object", "additionalProperties": False, "required": ["anchor", "snippet"], "properties": {
    "anchor": {"type": "string", "description": LOC}, "snippet": {"type": "string", "description": "A contiguous piece of the text at that locator, copied exactly"}}}
OPT = {"anyOf": [CELL, {"type": "null"}]}
CERT = {"type": "object", "additionalProperties": False, "required": ["label", "identifier", "date", "date_kind"], "properties": {
    "label": CELL, "identifier": OPT, "date": OPT, "date_kind": {"type": "string", "enum": ["expiry", "issued_or_calibrated", "other", "none"]}}}
ANSWER = {"type": "object", "additionalProperties": False, "required": ["q_no", "answer", "stance", "certificates"], "properties": {
    "q_no": {"type": "integer"}, "answer": OPT,
    "stance": {"type": "string", "enum": ["yes", "no", "partial", "unclear", "not_applicable"]}, "certificates": {"type": "array", "items": CERT}}}
Q_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answers"], "properties": {"answers": {"type": "array", "items": ANSWER}}}
KINDS = ["brcgs_packaging_cert", "bct_calibration_cert", "fsc_coc_cert", "bis_is2771_report", "iso9001_cert", "third_party_lab_report", "other"]
A_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "holder", "reference", "issuer", "valid_to"], "properties": {
    "kind": {"type": "string", "enum": KINDS}, "holder": CELL, "reference": OPT, "issuer": OPT, "valid_to": OPT}}

Q_SYSTEM = """You extract a vendor's answers to a buyer's questionnaire from a dump of the vendor's response document. Each line or cell appears with a locator.

For each numbered question the user lists, find the vendor's answer in the dump.
- answer: the vendor's answer text only, never the question, as one contiguous piece of the text at ONE locator, copied exactly, with that locator. If the vendor gave no answer to that question, answer is null. Never take an answer from text that is not the vendor's answer to that question.
- stance: yes = the vendor says it holds or does what was asked; no = it says it does not, or that others do it (outsourced); partial = in progress, planned, on request, "will send", or otherwise not yet in place; unclear = you cannot tell; not_applicable = the question asks for a description or a figure rather than a yes or no (capacity, recycled content, locations, terms).
- certificates: every certificate, licence, test report or calibration the answer mentions. label = its name or grade as written; identifier = its licence or certificate number as written, else null; date = a date stated for it, else null; date_kind = expiry if that date is when it stops being valid (valid to, expires, valid until, next due), issued_or_calibrated if it is when it was issued or calibrated, other otherwise, none if no date. Copy dates exactly as written; do not convert them.
- Return one entry for every question the user lists, in order."""
A_SYSTEM = """You read one certificate or report from a dump of its text. Each line appears with a locator.
- kind: what it is (BRCGS packaging certificate, box compression tester calibration certificate, FSC chain-of-custody certificate, IS 2771 test report, ISO 9001 certificate, a third-party laboratory test report, or other).
- holder: the certificate holder's name as written; reference: its number as written; issuer: who issued it as written; valid_to: the date after which it is no longer valid (expiry date, valid to, valid until, next calibration due), exactly as written; null if the document states none.
- Every cell you return is a locator plus text copied exactly from the dump. Never convert a date. Never infer anything that is not written."""


def q_prompt(dump, questions, focus=None, failures=None):
    listed = "\n".join(f"{q['q_no']}. {q['text']}" for q in questions if focus is None or q["q_no"] in focus)
    task = ("Extract the vendor's answer to each of these questions:" if focus is None else
            f"Re-extract ONLY these questions. An earlier reading failed automated checks ({failures}). Read the document again with care and transcribe exactly; do not adjust anything to make it fit:")
    return f"{task}\n{listed}\n\nDocument dump:\n{dump}"


# ---------------- verification (deterministic) ----------------
def verify_answers(data, src, questions):
    """q_no -> failure codes. The model must return every question; each answer must be verbatim at its locator, belong to its question, and carry parseable expiry dates."""
    qs, text = {q["q_no"]: q["text"] for q in questions}, full_text(src)
    got, fails = {}, {}
    for a in data["answers"]:
        got.setdefault(a["q_no"], a)
    for n, qtext in qs.items():
        codes, a = [], got.get(n)
        quoted = overlap(qtext, text) >= 0.9
        if a is None:
            fails[n] = ["question_not_returned"]
            continue
        if a["answer"] is None:
            if quoted:
                codes.append("answer_missing_but_question_present")
        else:
            ans = a["answer"]
            if not src.verbatim(ans["anchor"], ans["snippet"]):
                codes.append("provenance_unverified")
            elif len(ans["snippet"]) >= 12 and _norm(ans["snippet"]) in _norm(qtext):
                codes.append("answer_is_question")
            elif quoted and overlap(qtext, context(src, ans["anchor"])) < 0.9:
                codes.append("answer_not_aligned")
            for c in a["certificates"]:
                for cell in (c["label"], c["identifier"], c["date"]):
                    if cell and (cell["anchor"] != ans["anchor"] or not contained(src, cell)):
                        codes.append("provenance_unverified")
                if c["date"] and c["date_kind"] == "expiry" and parse_date(c["date"]["snippet"]) is None:
                    codes.append("date_unparseable")
            if a["stance"] == "unclear" or (a["stance"] == "not_applicable" and n <= 5):
                codes.append("stance_unclear")
        if codes:
            fails[n] = sorted(set(codes))
    return fails


def escalate(call_pass1, call_pass2, verify):
    """pass 1 -> verify -> pass 2 on failures only -> verify. Returns (data, still_failing, log)."""
    log = {"pass1_failures": {}, "rescued": [], "still_failing": {}, "passes": []}
    data, use = call_pass1()
    log["passes"].append(use)
    fails = verify(data)
    log["pass1_failures"] = {str(k): v for k, v in fails.items()}
    if fails:
        data2, use2 = call_pass2(sorted(fails), sorted({c for v in fails.values() for c in v}))
        log["passes"].append(use2)
        data = merge(data, data2, fails)
        still = verify(data)
        still = {k: v for k, v in still.items() if k in fails}
        log["rescued"] = sorted(str(k) for k in fails if k not in still)
        log["still_failing"] = {str(k): v for k, v in still.items()}
        return data, still, log
    return data, {}, log


def merge(data, data2, fails):
    redo = {a["q_no"]: a for a in data2["answers"] if a["q_no"] in fails}
    keep = [a for a in data["answers"] if a["q_no"] not in redo]
    return {"answers": keep + list(redo.values())}


# ---------------- extraction ----------------
def extract_answers(path, questions):
    src = make_source(path)
    dump = src.dump()
    p1 = lambda: llm.call_structured(llm.PASS1_MODEL, Q_SYSTEM, q_prompt(dump, questions), Q_SCHEMA, effort="none")
    p2 = lambda focus, codes: llm.call_structured(llm.PASS2_MODEL, Q_SYSTEM, q_prompt(dump, questions, set(focus), ", ".join(codes)), Q_SCHEMA, effort="high")

    def repaired(fn):
        def wrapped(*a):
            d, u = fn(*a)
            u["anchors_repaired"] = normalize_anchors(d, src)
            return d, u
        return wrapped
    with metering.stage("questionnaire:answers"):
        data, still, log = escalate(repaired(p1), repaired(p2), lambda d: verify_answers(d, src, questions))
    return data, still, log


def extract_attachment(path, vendors):
    """One certificate PDF: model reads, code checks the words are in the file and parses the date; a second pass on failure. Returns a record or None."""
    src = make_source(path)
    dump = src.dump()

    def verify(d):
        codes = []
        for k in ("holder", "reference", "issuer", "valid_to"):
            if d[k] and not src.verbatim(d[k]["anchor"], d[k]["snippet"]):
                codes.append("provenance_unverified")
        if d["valid_to"] and parse_date(d["valid_to"]["snippet"]) is None:
            codes.append("date_unparseable")
        if d["kind"] == "other":
            codes.append("kind_unclear")
        return {0: sorted(set(codes))} if codes else {}
    log = {"passes": []}
    with metering.stage("questionnaire:attachment"):
        d, use = llm.call_structured(llm.PASS1_MODEL, A_SYSTEM, f"Read this document.\n\nDocument dump:\n{dump}", A_SCHEMA, effort="none")
    normalize_anchors(d, src)
    log["passes"].append(use)
    fails = verify(d)
    if fails:
        with metering.stage("questionnaire:attachment"):
            d, use2 = llm.call_structured(llm.PASS2_MODEL, A_SYSTEM, f"Read this document with care; an earlier reading failed checks ({fails[0]}).\n\nDocument dump:\n{dump}", A_SCHEMA, effort="high")
        normalize_anchors(d, src)
        log["passes"].append(use2)
        fails = verify(d)
    holder = d["holder"]["snippet"].strip()
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    vendor = next((v["vendor_id"] for v in vendors if norm(v["name"]) == norm(holder.split(":", 1)[-1] if holder.lower().startswith("certificate holder") else holder)), None)
    return dict(kind=d["kind"], vendor_id=vendor, file_name=pathlib.Path(path).name, issuer=(d["issuer"] or {}).get("snippet"), entity_name=holder,
                valid_to=parse_date(d["valid_to"]["snippet"]) if d["valid_to"] else None, note="needs_review: " + ", ".join(fails[0]) if fails else None), log


# ---------------- gate evaluation (deterministic) ----------------
def evaluate(vendor_id, vendor_name, answers, still, attachments, questions, eval_date, master):
    """answers: {q_no: answer dict or None}; still: {q_no: failure codes after two passes}. Returns one row per RFx question."""
    on_file = set(master.get("vendors", {}).get(vendor_id, {}).get("gates_on_file", []))
    mine = [a for a in attachments if a["vendor_id"] == vendor_id]
    rows = []
    for q in questions:
        n, gate, ev_kind = q["q_no"], q["gate_code"], q["evidence_kind"]
        short = q["text"].split("?")[0][:60]
        row = dict(vendor_id=vendor_id, q_no=n, question=q["text"], is_gate=int(q["is_gate"]), gate_code=gate, answer_text="", answer_bool=None, attachment_id=None,
                   max_score=None if q["is_gate"] else 5, state="extracted", gate_status=None, reason_code=None, stance=None, anchor=None, snippet=None,
                   expiry_date=None, expiry_source=None, evidence_source=None, resolving_question=None)
        ask = lambda reason, **kw: QUESTIONS[reason].format(vendor=vendor_name, q_no=n, short=short, **kw)
        a = answers.get(n)
        master_ok = gate in on_file
        if a is None or a["answer"] is None:
            row.update(state="missing", reason_code="not_answered", resolving_question=ask("not_answered"))
            if gate:
                row.update(gate_status="pass" if master_ok else "not_answered", evidence_source="vendor_master" if master_ok else None,
                           reason_code="vendor_master_on_file" if master_ok else "not_answered")
                if master_ok:
                    row["resolving_question"] = None
            rows.append(row)
            continue
        ans, stance = a["answer"], a["stance"]
        row.update(answer_text=ans["snippet"], anchor=ans["anchor"], snippet=ans["snippet"], stance=stance, evidence_source="response",
                   answer_bool={"yes": 1, "no": 0}.get(stance))
        if n in still:
            code = next((c for c in still[n] if c in FAIL_TO_QUESTION), "unreadable")
            row.update(state="needs_review", reason_code=still[n][0], resolving_question=ask(FAIL_TO_QUESTION.get(code, "unreadable")))
            if gate:
                row["gate_status"] = "not_answered"
            rows.append(row)
            continue
        answer_expiry = next((parse_date(c["date"]["snippet"]) for c in a["certificates"] if c["date"] and c["date_kind"] == "expiry"), None)
        if answer_expiry:
            row.update(expiry_date=answer_expiry, expiry_source="answer")
        if ev_kind and stance == "yes":
            att = next((x for x in mine if x["kind"] == ev_kind), None)
            expired = bool(att and att["valid_to"] and att["valid_to"] < eval_date)
            if att:
                row.update(attachment_id=att["attachment_id"], expiry_date=att["valid_to"] or row["expiry_date"], expiry_source="attachment" if att["valid_to"] else row["expiry_source"])
            if att and not expired:
                row.update(evidence_source="attachment")
                gstat, greason = "pass", None
            elif att and expired:
                row.update(state="claimed_unsupported", reason_code="certificate_expired", evidence_source="attachment", resolving_question=ask("certificate_expired", evidence=KIND_LABEL.get(ev_kind, ev_kind), expiry=att["valid_to"]))
                gstat, greason = "fail", "certificate_expired"
            elif master_ok:
                row.update(evidence_source="vendor_master", reason_code="vendor_master_on_file")
                gstat, greason = "pass", "vendor_master_on_file"
            else:
                row.update(state="claimed_unsupported", reason_code="no_attachment", resolving_question=ask("no_attachment", evidence=KIND_LABEL.get(ev_kind, ev_kind)))
                gstat, greason = "fail", "no_attachment"
            if gate:
                row["gate_status"] = gstat
        elif gate:
            row.update(gate_status="fail", reason_code={"no": "answer_negative", "partial": "not_yet_in_place"}.get(stance, "answer_negative"))
        rows.append(row)
    return rows


# ---------------- database ----------------
def ensure_columns(con):
    have = {r[1] for r in con.execute("PRAGMA table_info(questionnaire_answers)")}
    for col, decl in PASS_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE questionnaire_answers ADD COLUMN {col} {decl}")


def write(con, vendor_id, rows, attachments):
    ensure_columns(con)
    with con:
        con.execute("DELETE FROM questionnaire_answers WHERE vendor_id=?", (vendor_id,))
        con.execute("DELETE FROM attachments WHERE vendor_id=?", (vendor_id,))
        con.executemany("INSERT INTO attachments (attachment_id,vendor_id,kind,file_name,issuer,entity_name,valid_to,expired_at_eval,supports,note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [(a["attachment_id"], vendor_id, a["kind"], a["file_name"], a["issuer"], a["entity_name"], a["valid_to"], a["expired_at_eval"], a["supports"], a["note"])
                         for a in attachments if a["vendor_id"] == vendor_id])
        cols = list(rows[0])
        con.executemany(f"INSERT INTO questionnaire_answers ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})", rows)


def gate_results(con):
    """Which vendors cleared every mandatory gate, read from questionnaire_answers. Returns (passing set or None, per-vendor detail).
    None means gates have not been evaluated (no gate rows), which is different from 'nobody passed'."""
    try:
        rows = con.execute("SELECT vendor_id, gate_code, gate_status, evidence_source FROM questionnaire_answers WHERE is_gate=1 AND gate_status IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return None, {}
    if not rows:
        return None, {}
    detail = {}
    for v, g, st, ev in rows:
        detail.setdefault(v, {})[g] = {"status": st, "evidence": ev}
    return {v for v, gs in detail.items() if all(gs.get(g, {}).get("status") == "pass" for g in GATE_CODES)}, detail


# ---------------- orchestration ----------------
def run(db_path, rfx_path, artifacts, master_path=None, files=None, log_path=None):
    rfx = json.loads(pathlib.Path(rfx_path).read_text(encoding="utf-8"))
    questions, vendors, eval_date = rfx["questions"], rfx["invited_vendors"], rfx["evaluation_date"]
    master = json.loads(pathlib.Path(master_path).read_text(encoding="utf-8")) if master_path and pathlib.Path(master_path).exists() else {}
    artifacts = pathlib.Path(artifacts)
    con = sqlite3.connect(db_path, timeout=60)
    con.execute("PRAGMA foreign_keys = ON")
    supports = {q["evidence_kind"]: q["q_no"] for q in questions if q["evidence_kind"]}
    attachments, log = [], {"attachments": {}, "vendors": {}}
    for f in sorted((artifacts / "attachments").glob("*.pdf")):
        rec, alog = extract_attachment(f, vendors)
        rec["attachment_id"] = f"{rec['vendor_id']}-{rec['kind']}"
        rec["supports"] = str(supports[rec["kind"]]) if rec["kind"] in supports else None
        rec["expired_at_eval"] = int(bool(rec["valid_to"] and rec["valid_to"] < eval_date))
        attachments.append(rec)
        log["attachments"][f.name] = {"record": {k: rec[k] for k in ("kind", "vendor_id", "valid_to", "note")}, **alog}
    log["unattributed_attachments"] = [a["file_name"] for a in attachments if a["vendor_id"] is None]
    for vid, name, fname in con.execute("SELECT s.vendor_id, v.name, s.file_name FROM submissions s JOIN vendors v USING(vendor_id) ORDER BY 1").fetchall():
        if files and fname not in files:
            continue
        if pathlib.Path(fname).suffix.lower() not in SOURCES:          # a photographed rate card (V4) carries no questionnaire to read
            log["vendors"][vid] = {"skipped": f"{fname}: no questionnaire source for this file type"}
            continue
        data, still, vlog = extract_answers(artifacts / fname, questions)
        answers = {a["q_no"]: a for a in data["answers"]}
        rows = evaluate(vid, name, answers, {int(k): v for k, v in still.items()}, attachments, questions, eval_date, master)
        write(con, vid, rows, attachments)
        log["vendors"][vid] = {**vlog, "states": {s: sum(r["state"] == s for r in rows) for s in sorted({r["state"] for r in rows})},
                               "gates": {r["gate_code"]: r["gate_status"] for r in rows if r["is_gate"]}}
    con.close()
    if log_path:
        pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(log_path).write_text(json.dumps(log, indent=1, default=str))
    return log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "procurement.db"))
    ap.add_argument("--rfx", default=str(ROOT / "dataset" / "artifacts" / "rfx.json"))
    ap.add_argument("--artifacts", default=str(ROOT / "dataset" / "artifacts"))
    ap.add_argument("--master", default=str(ROOT / "dataset" / "artifacts" / "vendor_master.json"))
    ap.add_argument("files", nargs="*", help="response files to process (default: every submission in the database)")
    a = ap.parse_args()
    metering.configure(a.db)
    log = run(a.db, a.rfx, a.artifacts, a.master, set(a.files) or None, ROOT / "dataset" / "eval" / "questionnaire_run.json")
    for vid, v in log["vendors"].items():
        if "skipped" in v:
            print(vid, "skipped:", v["skipped"])
            continue
        print(vid, v["states"], "gates", v["gates"], "| pass-1 failures:", v["pass1_failures"], "| still failing:", v["still_failing"])
    print("unattributed attachments:", log["unattributed_attachments"])


if __name__ == "__main__":
    main()
