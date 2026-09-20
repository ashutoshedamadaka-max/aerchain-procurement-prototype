"""Extraction for every format: model pass 1 -> deterministic verify -> stronger pass 2 on failures only -> verify again ->
needs_review only for what still fails. Then build field records and write procurement.db.
Row-based formats (xlsx, pdf, docx) share one core; the email path works on statements about a ply class and expands them in code."""
import datetime as dt
import pathlib
import re

from .. import metering
from . import llm, store
from .excel_schema import SCHEMA as XL_SCHEMA, SYSTEM as XL_SYSTEM, user_prompt as xl_prompt
from .records import Field, question
from .sources import SIZE, DocxSource, EmailSource, PdfSource
from .text_schema import DOC_SCHEMA, DOCX_SYSTEM, EMAIL_SCHEMA, EMAIL_SYSTEM, PDF_SYSTEM, doc_prompt, email_prompt
from .verify import Failure, Rfx, _basis_of, parse_amount_inr, parse_int, parse_number, single_int, verify_row, verify_terms
from .workbook import Workbook, split_anchor

UNIT = {"per_piece": "piece", "per_100_pieces": "100pcs", "per_kg": "kg"}
BASIS_INFO = {"per_100_pieces": "basis_per_100", "per_kg": "basis_per_kg"}     # informational: the basis needs converting later, no doubt about the read
INCOTERM = re.compile(r"(?i)\b(ex[- ]?works|exw|fob|cif|ddp)\b")
INCOTERM_CODE = {"ex-works": "EXW", "ex works": "EXW", "exw": "EXW", "fob": "FOB", "cif": "CIF", "ddp": "DDP"}
PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
ROW_HANDLERS = {
    ".xlsx": (Workbook, XL_SCHEMA, XL_SYSTEM, lambda d, todo, t, f: xl_prompt(d, {n for _, n in todo} if todo else None, t, f)),
    ".pdf": (PdfSource, DOC_SCHEMA, PDF_SYSTEM, doc_prompt),
    ".docx": (DocxSource, DOC_SCHEMA, DOCX_SYSTEM, doc_prompt),
}
TERM_KEYS = ("vendor_name", "quotation_date", "incoterm", "conditions", "tiers", "order_discounts", "qty_breaks")


def normalize_anchors(node, src, sheet=None, count=None):
    """Canonicalise every locator the model returned. Only the reference is repaired; the snippet must still be verbatim at it."""
    count = count if count is not None else [0]
    if isinstance(node, dict):
        sheet = node.get("sheet", sheet) if isinstance(node.get("sheet"), str) else sheet
        if "anchor" in node and "snippet" in node:
            fixed = src.canonical(node["anchor"], sheet)
            count[0] += fixed != node["anchor"]
            node["anchor"] = fixed
        for v in node.values():
            normalize_anchors(v, src, sheet, count)
    elif isinstance(node, list):
        for v in node:
            normalize_anchors(v, src, sheet, count)
    return count[0]


def _key(r):
    return (r["sheet"], r["row"])


def _q(reason, vendor, line, detail):
    try:
        return question(reason, vendor=vendor, line=line, **detail)
    except KeyError as e:
        raise KeyError(f"question template for {reason} needs {e}") from e


def extract_rows(path, rfx, peers=None):
    Src, schema, system, prompt = ROW_HANDLERS[pathlib.Path(path).suffix.lower()]
    src, log = Src(path), {"passes": [], "pass1_failures": {}, "rescued": [], "still_failing": {}}
    check = lambda data: {_key(r): verify_row(r, src, rfx, peers) for r in data["rows"]}
    gaps = lambda data: src.candidate_rows({r["sheet"] for r in data["rows"]}) - {_key(r) for r in data["rows"]}
    data, use = llm.call_structured(llm.PASS1_MODEL, system, prompt(src.dump(), None, True, None), schema, effort="none")
    log["passes"].append(use)
    log["anchors_repaired"] = normalize_anchors(data, src)
    checked, term_probs, missing = check(data), verify_terms(data, src), gaps(data)
    fails = {k for k, c in checked.items() if c.failures}
    fmt = lambda k: f"{k[0]}!{k[1]}"
    log["pass1_failures"] = {fmt(k): [f.code for f in checked[k].failures] for k in sorted(fails)}
    log["pass1_failures"].update({fmt(k): ["row_not_extracted"] for k in sorted(missing)})
    log["pass1_term_problems"] = term_probs
    todo = fails | missing
    if todo or term_probs:
        focus = {}
        for s, n in todo:
            focus.setdefault(s, set()).add(n)
        summary = "; ".join(sorted({f.code for k in fails for f in checked[k].failures} | ({"row_not_extracted"} if missing else set())
                                   | ({"terms: " + ", ".join(term_probs)} if term_probs else set())))
        data2, use2 = llm.call_structured(llm.PASS2_MODEL, system, prompt(src.dump(focus, SIZE), todo, bool(term_probs), summary), schema, effort="high")
        log["passes"].append(use2)
        log["anchors_repaired"] += normalize_anchors(data2, src)
        redo = {_key(r): r for r in data2["rows"] if _key(r) in todo}
        known = {_key(x) for x in data["rows"]}
        data["rows"] = [redo.get(_key(r), r) for r in data["rows"]] + [r for k, r in redo.items() if k not in known]
        if term_probs:
            data.update({k: data2[k] for k in TERM_KEYS if k in data2})
        checked = check(data)
        still = {k for k, c in checked.items() if c.failures}
        log["rescued"] = sorted(fmt(k) for k in fails - still)
        log["still_failing"] = {fmt(k): [f.code for f in checked[k].failures] for k in sorted(still)}
        log["term_problems_after_pass2"] = verify_terms(data, src)
        log["gaps_after_pass2"] = sorted(fmt(k) for k in gaps(data))
    return data, checked, log, src


def build_fields(checked, gap_rows, rfx, vendor, src):
    fields, seen, unmatched = [], set(), []
    for c in checked.values():
        row, ln = c.row, c.line
        where = f"{row['sheet']}!{row['row']}"
        if ln is None:
            unmatched.append({"row": where, "question": f"{vendor}: {where} matches no single RFx line. Which RFx line is it?"})
            continue
        n = ln["line_no"]
        if n in seen:
            unmatched.append({"row": where, "question": f"{vendor}: line {n} appears twice. Which one applies?"})
            continue
        seen.add(n)
        rate = row["rate"]
        if row["rate_status"] == "declined":
            anchor = rate["anchor"] if rate else next((cell["anchor"] for cell in (row["size"], row["ply"]) if cell), None)
            fields.append(Field(n, "unit_price", "not_quoted", reason_code="declined_capability", anchor=anchor,
                                snippet=rate["snippet"] if rate else row["size"]["snippet"]))
            continue
        if row["rate_status"] == "blank":
            fields.append(Field(n, "unit_price", "missing", reason_code="rate_blank", resolving_question=_q("rate_blank", vendor, n, {})))
            continue
        cur, basis = row["currency"], row["basis"]
        unit = f"{cur}/{UNIT[basis]}" if basis in UNIT and cur in ("INR", "USD") else None
        for name, cell, value in (("unit_price", rate, c.rate), ("line_total", row["amount"], c.amount), ("declared_liner_gsm", row["gsm_stack"], c.liner_gsm)):
            if not cell:
                continue
            bad = next((f for f in c.failures if name in f.fields), None)
            info = BASIS_INFO.get(basis) if name == "unit_price" else None
            reason = bad.code if bad else c.deviations.get(name) or info or ("stored_as_text" if name == "unit_price" and c.text_number else None)
            deriv = (f"qty {bad.detail['qty']:,.0f} x {bad.detail['rate']:,.2f} = {bad.detail['expected']:,.2f}; vendor states {bad.detail['stated']:,.2f}"
                     if bad and bad.code == "arith_mismatch" else None)
            fields.append(Field(
                n, name, "needs_review" if bad else "extracted", value=value,
                unit={"unit_price": unit, "line_total": cur, "declared_liner_gsm": "gsm"}[name],
                basis={"unit_price": basis, "line_total": "line_total", "declared_liner_gsm": "spec"}[name],
                currency=cur if name != "declared_liner_gsm" else None, reason_code=reason, anchor=cell["anchor"], snippet=cell["snippet"],
                derivation=deriv, resolving_question=_q(bad.code, vendor, n, {**bad.detail}) if bad else None))
        inc = next((f for f in c.failures if f.code == "spec_incomplete"), None)
        if inc:
            fields.append(Field(n, "declared_liner_gsm", "missing", unit="gsm", basis="spec", reason_code="spec_incomplete",
                                anchor=rate["anchor"] if rate else None, snippet=None,
                                resolving_question=_q("spec_incomplete", vendor, n, inc.detail)))
    for sheet, r in gap_rows:
        hits = []
        if src.kind == "xlsx":
            cells = [x for x in src.wb[sheet][r] if isinstance(x.value, str) and SIZE.search(x.value)]
            dims = tuple(map(int, SIZE.search(cells[0].value).groups())) if cells else None
            hits = [ln for ln in rfx.lines if dims == (ln["length_mm"], ln["width_mm"], ln["height_mm"])]
        if len(hits) == 1 and hits[0]["line_no"] not in seen:
            n = hits[0]["line_no"]
            seen.add(n)
            fields.append(Field(n, "unit_price", "needs_review", reason_code="row_not_extracted", resolving_question=_q("row_not_extracted", vendor, n, {})))
    for ln in rfx.lines:
        if ln["line_no"] not in seen:
            fields.append(Field(ln["line_no"], "unit_price", "missing", reason_code="line_absent", resolving_question=_q("line_absent", vendor, ln["line_no"], {})))
    return sorted(fields, key=lambda f: (f.line_no, f.field_name)), unmatched


def terms(data, rfx):
    conds = [{"kind": c["kind"], "value_num": single_int(c["snippet"]) if c["kind"] in ("validity", "payment_terms", "moq") else None,
              "anchor": c["anchor"], "snippet": c["snippet"]} for c in data.get("conditions", [])]
    tiers = []
    for t in data.get("tiers", []):
        pct = PCT.search(t["effect"]["snippet"])
        eff = 0.0 if t["effect_kind"] == "none" or not pct else float(pct.group(1)) * (-1 if t["effect_kind"] == "discount_pct" else 1)
        first = split_anchor((t["min_value"] or t["effect"])["anchor"])[1]
        sheet, last = split_anchor(t["effect"]["anchor"])
        tiers.append({"kind": "order_value_discount" if t["effect_kind"] == "discount_pct" else "order_value_uplift",
                      "min_value_inr": parse_number(t["min_value"]["snippet"]) if t["min_value"] else None,
                      "max_value_inr": parse_number(t["max_value"]["snippet"]) if t["max_value"] else None,
                      "effect_pct": eff, "anchor": f"{sheet}!{first}:{last}", "snippet": t["effect"]["snippet"]})
    for d in data.get("order_discounts", []):
        pct, thr = float(PCT.search(d["effect"]["snippet"]).group(1)), parse_amount_inr(d["threshold"]["snippet"])
        sign = -1 if d["effect_kind"] == "discount_pct" else 1
        tiers.append({"kind": "order_value_discount" if sign < 0 else "order_value_uplift", "min_value_inr": thr, "max_value_inr": None,
                      "effect_pct": sign * pct, "anchor": d["effect"]["anchor"], "snippet": d["effect"]["snippet"]})
        conds.append({"kind": "discount_footnote", "value_num": pct, "value_text": f"% on order value above {int(thr)}",
                      "anchor": d["effect"]["anchor"], "snippet": d["effect"]["snippet"]})
    for q in data.get("qty_breaks", []):
        m = SIZE.search(q["size"]["snippet"])
        hits = [ln for ln in rfx.lines if m and tuple(map(int, m.groups())) == (ln["length_mm"], ln["width_mm"], ln["height_mm"])]
        sign = -1 if q["effect_kind"] == "discount_pct" else 1
        tiers.append({"kind": "line_qty_break", "rfx_line_no": hits[0]["line_no"] if len(hits) == 1 else None, "min_qty": parse_int(q["min_qty"]["snippet"]),
                      "effect_pct": sign * float(PCT.search(q["effect"]["snippet"]).group(1)), "anchor": q["effect"]["anchor"], "snippet": q["effect"]["snippet"]})
    return conds, tiers


# ---- email: statements about a ply class, expanded to RFx lines by code ----
def verify_statements(data, src):
    out = {}
    for i, s in enumerate(data["statements"]):
        fails = []
        for k in ("ply", "rate", "basis_text", "scope_text"):
            if s[k] and not src.verbatim(s[k]["anchor"], s[k]["snippet"]):
                fails.append(Failure("provenance_unverified", ("unit_price",), {"field": k, "anchor": s[k]["anchor"]}))
        if s["kind"] == "rate":
            if not s["ply"] or parse_int(s["ply"]["snippet"]) is None:
                fails.append(Failure("value_unparseable", ("unit_price",), {"field": "ply", "snippet": (s["ply"] or {}).get("snippet", "")}))
            if not s["rate"] or parse_number(s["rate"]["snippet"]) is None:
                fails.append(Failure("value_unparseable", ("unit_price",), {"field": "rate", "snippet": (s["rate"] or {}).get("snippet", "")}))
            if s["currency"] in ("unclear", "other"):
                fails.append(Failure("currency_unclear", ("unit_price",)))
            vb = _basis_of(s["basis_text"]["snippet"]) if s["basis_text"] else None
            if s["basis"] == "unclear" or (vb and vb != s["basis"]):
                fails.append(Failure("basis_unclear", ("unit_price",)))
        if fails:
            out[i] = fails
    return out


def extract_email(path, rfx):
    src, log = EmailSource(path), {"passes": [], "pass1_failures": {}, "rescued": [], "still_failing": {}}
    data, use = llm.call_structured(llm.PASS1_MODEL, EMAIL_SYSTEM, email_prompt(src.dump()), EMAIL_SCHEMA, effort="none")
    log["passes"].append(use)
    log["anchors_repaired"] = normalize_anchors(data, src)
    bad, tprobs = verify_statements(data, src), verify_terms(data, src)
    log["pass1_failures"] = {f"statement {i}": [f.code for f in fs] for i, fs in bad.items()}
    log["pass1_term_problems"] = tprobs
    if bad or tprobs:
        summary = "; ".join(sorted({f.code for fs in bad.values() for f in fs} | set(tprobs)))
        data, use2 = llm.call_structured(llm.PASS2_MODEL, EMAIL_SYSTEM, email_prompt(src.dump(), summary), EMAIL_SCHEMA, effort="high")
        log["passes"].append(use2)
        log["anchors_repaired"] += normalize_anchors(data, src)
        bad2 = verify_statements(data, src)
        log["rescued"] = [f"statement {i}" for i in bad if i not in bad2]
        log["still_failing"] = {f"statement {i}": [f.code for f in fs] for i, fs in bad2.items()}
        log["term_problems_after_pass2"] = verify_terms(data, src)
        bad = bad2
    return data, bad, log, src


def build_email_fields(data, bad, rfx, vendor):
    fields, ref = [], next((s for s in data["statements"] if s["kind"] == "prior_year_reference"), None)
    by_ply = {}
    for i, s in enumerate(data["statements"]):
        if s["kind"] == "rate" and s["ply"] and parse_int(s["ply"]["snippet"]) is not None:
            by_ply.setdefault(parse_int(s["ply"]["snippet"]), (i, s))
    for ln in rfx.lines:
        n = ln["line_no"]
        hit = by_ply.get(ln["ply"])
        if hit:
            i, s = hit
            fail = bad.get(i, [None])[0]
            cur, basis = s["currency"], s["basis"]
            fields.append(Field(n, "unit_price", "needs_review" if fail else "extracted", value=parse_number(s["rate"]["snippet"]) if s["rate"] else None,
                                unit=f"{cur}/{UNIT[basis]}" if basis in UNIT and cur in ("INR", "USD") else None, basis=basis, currency=cur,
                                reason_code=fail.code if fail else BASIS_INFO.get(basis), anchor=s["rate"]["anchor"] if s["rate"] else None,
                                snippet=s["rate"]["snippet"] if s["rate"] else None,
                                resolving_question=_q(fail.code, vendor, n, fail.detail) if fail else None))
        elif ref:
            cell = ref["scope_text"]
            fields.append(Field(n, "unit_price", "missing", reason_code="reference_unresolved", anchor=cell["anchor"] if cell else None,
                                snippet=cell["snippet"] if cell else None, resolving_question=_q("reference_unresolved", vendor, n, {})))
        else:
            fields.append(Field(n, "unit_price", "missing", reason_code="line_absent", resolving_question=_q("line_absent", vendor, n, {})))
    conds = [{"kind": c["kind"], "value_num": single_int(c["snippet"]) if c["kind"] in ("validity", "payment_terms", "moq") else None,
              "anchor": c["anchor"], "snippet": c["snippet"]} for c in data["conditions"]]
    if ref and ref["scope_text"]:
        conds.append({"kind": "prior_year_reference", "value_num": None, "value_text": "same as last year", "anchor": ref["scope_text"]["anchor"],
                      "snippet": ref["scope_text"]["snippet"]})
    return fields, conds


HANDLER_NAMES = {".xlsx": "excel", ".pdf": "pdf", ".docx": "word", ".txt": "email", ".eml": "email"}


def run(path, db_path, rfx_path):
    rfx, path = Rfx(rfx_path), pathlib.Path(path)
    con = store.connect(db_path, rfx)
    email = path.suffix.lower() in (".txt", ".eml")
    with metering.stage("extraction:" + HANDLER_NAMES.get(path.suffix.lower(), path.suffix.lstrip("."))):
        if email:
            data, bad, log, src = extract_email(path, rfx)
        else:
            data, checked, log, src = extract_rows(path, rfx, store.peers(con, path.name))
    name = data["vendor_name"]["snippet"].strip()
    vendor = next((v for v in rfx.vendors if v["name"].casefold() == name.casefold()), None)
    if vendor is None:
        raise ValueError(f"vendor '{name}' is not on the RFx invite list")
    m = re.search(r"\d{4}-\d{2}-\d{2}", (data["quotation_date"] or {}).get("snippet", ""))
    if not m:
        raise ValueError("quotation date not found; the pipeline will not guess one")
    received = dt.date.fromisoformat(m.group())
    if email:
        fields, conds = build_email_fields(data, bad, rfx, name)
        tiers, unmatched, currencies = [], [], {s["currency"] for s in data["statements"] if s["kind"] == "rate"}
    else:
        conds, tiers = terms(data, rfx)
        fields, unmatched = build_fields(checked, src.candidate_rows({r["sheet"] for r in data["rows"]}) - {(r["sheet"], r["row"]) for r in data["rows"]},
                                         rfx, name, src)
        currencies = {r["currency"] for r in data["rows"] if r["rate_status"] == "priced"}
    validity = next((c["value_num"] for c in conds if c["kind"] == "validity"), None)
    until = received + dt.timedelta(days=validity) if validity else None
    inc = INCOTERM.search((data.get("incoterm") or {}).get("snippet", ""))
    sub = {"submission_id": f"S{vendor['vendor_id'][1:]}", "vendor_id": vendor["vendor_id"], "file_name": path.name, "format": path.suffix.lstrip("."),
           "received_on": received.isoformat(), "validity_days": validity, "valid_until": until.isoformat() if until else None,
           "expired_at_eval": int(bool(until and until.isoformat() < rfx.eval_date)), "eval_date": rfx.eval_date,
           "incoterm": INCOTERM_CODE[inc.group(1).lower().replace("  ", " ")] if inc else None,
           "currency": currencies.pop() if len(currencies) == 1 else "MIXED"}
    store.write(con, {"vendor_id": vendor["vendor_id"], "name": vendor["name"], "response_format": sub["format"],
                      "moq_pcs": next((c["value_num"] for c in conds if c["kind"] == "moq"), None)}, sub, fields, conds, tiers)
    con.close()
    log["unmatched_rows"] = unmatched
    log["final_data"] = data
    return sub, fields, log
