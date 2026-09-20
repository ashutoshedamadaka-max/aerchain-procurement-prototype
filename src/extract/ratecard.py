"""Persist V4's photographed rate card into procurement.db from the two independent vision passes (dataset/eval/v4_vision_pass1.json, pass2.json).

Nothing new is decided by the model here. Code merges the two readings per printed row and never keeps a number it cannot stand behind:
  extracted     both passes read the same rate and unit with high confidence, and the row's size and ply match the mapped RFx line on dimensions
  needs_review  a pass unreadable or not high confidence, the passes disagree, or the row does not match its RFx line. The value is kept ONLY when both
                passes read the same number (a crease or a shadow the reader was unsure about); if a rate is unreadable or disputed it stays NULL
Prices stay in USD as printed (USD/piece or USD/kg); normalization converts at the stamped FX. The header (vendor, date, incoterm) is not in the passes, so two
further vision reads take it, and the run stops if they disagree. The card carries no line totals, so only unit_price and declared_liner_gsm are written.

  python -m src.extract.ratecard [--db procurement.db]
"""
import argparse
import base64
import collections
import datetime as dt
import json
import pathlib
import re
import time

from .. import metering
from . import store
from .llm import _load_env
from .records import Field, question
from .verify import Rfx

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVAL = ROOT / "dataset" / "eval"
IMG = ROOT / "dataset" / "artifacts" / "V4_Meridian_RateCard.jpg"
MODEL = "gpt-5.5"
BASIS = {"per_pc": ("per_piece", "USD/piece"), "per_kg": ("per_kg", "USD/kg")}
INCOTERM_CODE = {"ex-works": "EXW", "ex works": "EXW", "exw": "EXW", "fob": "FOB", "cif": "CIF", "ddp": "DDP"}
INCOTERM = re.compile(r"(?i)\b(ex[- ]?works|exw|fob|cif|ddp)\b")

HEADER_TOOL = {"type": "function", "function": {"name": "submit_header", "strict": True, "description": "The header block of the rate card.", "parameters": {
    "type": "object", "additionalProperties": False, "required": ["vendor_name", "date_line", "incoterm_text"], "properties": {
        "vendor_name": {"type": "string", "description": "The company name at the top, exactly as printed"},
        "date_line": {"type": "string", "description": "The quotation date as printed, e.g. 'Date: 07 March 2026' (exactly as printed)"},
        "incoterm_text": {"type": ["string", "null"], "description": "The delivery-terms wording as printed (e.g. 'EXW Navi Mumbai'), else null"}}}}}


def read_header():
    """One vision read of the header block. Metered as photo:v4_header."""
    _load_env()
    from openai import OpenAI
    b64 = base64.standard_b64encode(IMG.read_bytes()).decode()
    t0 = time.perf_counter()
    r = OpenAI().chat.completions.create(model=MODEL, max_completion_tokens=4000, tools=[HEADER_TOOL],
                                         tool_choice={"type": "function", "function": {"name": "submit_header"}},
                                         messages=[{"role": "user", "content": [
                                             {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                                             {"type": "text", "text": "Read only the header block at the top of this photographed rate card: company name, the date line, and the "
                                                                      "delivery terms wording. Copy them exactly as printed. Submit with submit_header."}]}])
    metering.record("openai", MODEL, r.usage.prompt_tokens, r.usage.completion_tokens, time.perf_counter() - t0, "photo:v4_header")
    return json.loads(r.choices[0].message.tool_calls[0].function.arguments)


def merge_rows(p1, p2, rfx):
    """Per RFx line: the two readings and the decision. A row that cannot be placed on exactly one RFx line by dimensions and ply, in agreement with both passes, is left out."""
    by = lambda p: {r["printed_no"]: r for r in p["rows"]}
    a, b = by(p1), by(p2)
    out = {}
    for no in sorted(a.keys() & b.keys()):
        r1, r2 = a[no], b[no]
        line = rfx.match(r2["size_mm"], str(r2["ply"]))
        if line is None or not (r1["mapped_rfx_line"] == r2["mapped_rfx_line"] == line["line_no"]):
            continue
        readings = f"pass 1: {r1['rate_text']} ({r1['digit_confidence']}); pass 2: {r2['rate_text']} ({r2['digit_confidence']})"
        same_unit = r1["unit"] == r2["unit"] and r1["unit"] in BASIS
        same_rate = r1["rate"] is not None and r1["rate"] == r2["rate"]
        if same_unit and same_rate and r1["digit_confidence"] == r2["digit_confidence"] == "high":
            state, reason, value = "extracted", "currency_usd", r1["rate"]
        else:
            state, value = "needs_review", r1["rate"] if same_unit and same_rate else None       # keep a number only if both passes read the same one
            reason = "ocr_unreadable" if r1["rate"] is None and r2["rate"] is None else "ocr_ambiguous_digit"
        unit = next((u for u in (r2["unit"], r1["unit"]) if u in BASIS), None)
        out[line["line_no"]] = dict(state=state, value=value, reason=reason, basis=BASIS[unit][0] if unit else None, unit=BASIS[unit][1] if unit else None, printed_no=no,
                                    rate_text=next((t for t in (r2["rate_text"], r1["rate_text"]) if t != "unreadable"), "unreadable"), readings=readings,
                                    gsm=r2["gsm"] if r1["gsm"] == r2["gsm"] else None, ply=r2["ply"], size=r2["size_mm"],
                                    note=r2["ambiguous_digits"] or r1["ambiguous_digits"] or r2["match_note"])
    return out


def build_fields(merged, vendor):
    fields = []
    for n, m in sorted(merged.items()):
        q = question(m["reason"], vendor=vendor, line=n, readings=m["readings"], note=m["note"]) if m["state"] == "needs_review" else None
        fields.append(Field(n, "unit_price", m["state"], m["value"], m["unit"], m["basis"], "USD", m["reason"], f"ratecard:r{m['printed_no']}:c4", m["rate_text"],
                            derivation=("two independent vision passes read the same rate. " if m["state"] == "extracted" else "") + m["readings"], resolving_question=q))
        ok = m["gsm"] is not None
        fields.append(Field(n, "declared_liner_gsm", "extracted" if ok else "needs_review", m["gsm"], "gsm", "spec", None, None if ok else "provenance_unverified",
                            f"ratecard:r{m['printed_no']}:c2", f"{m['ply']} ply {m['gsm']} GSM" if ok else m["size"],
                            resolving_question=None if ok else question("provenance_unverified", vendor=vendor, field="declared liner GSM", line=n, anchor=f"ratecard:r{m['printed_no']}:c2")))
    return fields


def build_conditions(footer):
    """Commercial terms from the footer strings the passes returned; the numbers are parsed by code."""
    conds, a = [], "ratecard:footer"
    if footer.get("moq") and (m := re.search(r"(\d[\d,]*)", footer["moq"])):
        conds.append(dict(kind="moq", value_num=float(m.group(1).replace(",", "")), value_text="pieces per size", anchor=a, snippet=footer["moq"]))
    if footer.get("validity") and (m := re.search(r"(\d+)\s*days", footer["validity"])):
        conds.append(dict(kind="validity", value_num=float(m.group(1)), value_text=None, anchor=a, snippet=footer["validity"]))
    for t in footer.get("other_terms", []):
        if re.search(r"(?i)freight", t):
            conds.append(dict(kind="freight", value_num=None, value_text="extra" if re.search(r"(?i)extra", t) else t, anchor=a, snippet=t))
        elif re.search(r"(?i)advance", t) and (m := re.search(r"(\d+)\s*%", t)):
            conds.append(dict(kind="payment_terms", value_num=float(m.group(1)), value_text="% advance", anchor=a, snippet=t))
        elif re.search(r"(?i)exchange", t):
            conds.append(dict(kind="fx_risk_on_buyer", value_num=None, value_text="USD prices; exchange variation on buyer", anchor=a, snippet=t))
    return conds


def run(db_path, rfx_path, pass1, pass2):
    metering.configure(db_path)
    rfx = Rfx(rfx_path)
    p1, p2 = (json.loads(pathlib.Path(p).read_text(encoding="utf-8")) for p in (pass1, pass2))
    h1, h2 = read_header(), read_header()
    norm = lambda t: re.sub(r"\s+", " ", (t or "").strip().casefold())
    if any(norm(h1[k]) != norm(h2[k]) for k in ("vendor_name", "date_line", "incoterm_text")):
        raise ValueError(f"the two header reads disagree; not persisting V4. read 1: {h1}; read 2: {h2}")
    alnum = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()                                  # the card prints the name in capitals with a full stop
    vendor = next((v for v in rfx.vendors if alnum(v["name"]) == alnum(h1["vendor_name"])), None)
    if vendor is None:
        raise ValueError(f"vendor '{h1['vendor_name']}' is not on the RFx invite list")
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", h1["date_line"])
    if not m:
        raise ValueError("quotation date not found on the card; the pipeline will not guess one")
    received = dt.datetime.strptime(" ".join(m.groups()), "%d %B %Y").date()
    merged = merge_rows(p1, p2, rfx)
    fields, conds = build_fields(merged, vendor["name"]), build_conditions(p2["footer"])
    validity = next((c["value_num"] for c in conds if c["kind"] == "validity"), None)
    until = received + dt.timedelta(days=int(validity)) if validity else None
    inc = INCOTERM.search(h1["incoterm_text"] or "")
    sub = {"submission_id": f"S{vendor['vendor_id'][1:]}", "vendor_id": vendor["vendor_id"], "file_name": IMG.name, "format": "jpg", "received_on": received.isoformat(),
           "validity_days": int(validity) if validity else None, "valid_until": until.isoformat() if until else None,
           "expired_at_eval": int(bool(until and until.isoformat() < rfx.eval_date)), "eval_date": rfx.eval_date,
           "incoterm": INCOTERM_CODE[inc.group(1).lower().replace("  ", " ")] if inc else None, "currency": "USD"}
    con = store.connect(db_path, rfx)
    store.write(con, {"vendor_id": vendor["vendor_id"], "name": vendor["name"], "response_format": "jpg", "moq_pcs": next((c["value_num"] for c in conds if c["kind"] == "moq"), None)},
                sub, fields, conds, [])
    con.close()
    return sub, fields, merged, sorted({ln["line_no"] for ln in rfx.lines} - merged.keys()), h1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "procurement.db"))
    ap.add_argument("--rfx", default=str(ROOT / "dataset" / "artifacts" / "rfx.json"))
    ap.add_argument("--pass1", default=str(EVAL / "v4_vision_pass1.json"))
    ap.add_argument("--pass2", default=str(EVAL / "v4_vision_pass2.json"))
    a = ap.parse_args()
    sub, fields, merged, unplaced, header = run(a.db, a.rfx, a.pass1, a.pass2)
    print(json.dumps({"submission": sub, "header_read": header,
                      "unit_price_states": collections.Counter(f.state for f in fields if f.field_name == "unit_price"),
                      "gsm_states": collections.Counter(f.state for f in fields if f.field_name == "declared_liner_gsm"),
                      "needs_review_lines": {n: (m["reason"], m["value"]) for n, m in merged.items() if m["state"] == "needs_review"}, "lines_not_placed": unplaced}, indent=1, default=str))


if __name__ == "__main__":
    main()
