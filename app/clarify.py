"""Display-layer wording for clarifications. Stored resolving questions and derivations are never rewritten: these functions build the text the buyer sees.
Vendor-facing questions carry no internal terms (no passes, OCR, confidence or reason codes). Reason codes without a template here keep their stored wording."""
import re

from app import fmt

PASSES = re.compile(r"pass 1: (?P<a>.+?) \((?P<ca>high|medium|low)\); pass 2: (?P<b>.+?) \((?P<cb>high|medium|low)\)")
HOLD = re.compile(r"you state you hold (?P<cert>.+?) but no supporting document")


def spec(line):
    """The RFx line in words, e.g. '3-ply 300×200×150 mm, 150 GSM liner, 1-colour flexo'."""
    if not line:
        return "this line"
    return f"{line['ply']}-ply {line['length_mm']}×{line['width_mm']}×{line['height_mm']} mm, {line['liner_gsm']} GSM liner, {line['print_spec']}"


def plain_derivation(text):
    """Rewrite the vision-reading derivation for the buyer; anything else is returned as stored."""
    m = PASSES.search(text or "")
    if not m:
        return text
    a, b, ca, cb = m["a"], m["b"], m["ca"], m["cb"]
    unreadable = lambda t: t.startswith("unreadable") or "[unreadable]" in t
    if a == b and not unreadable(a):
        tail = f"Both returned {a}" + (f" with {ca} confidence." if ca == cb else f"; pass 1 marked it {ca} confidence and pass 2 marked it {cb}.")
        if ca == cb == "high":
            tail = f"Both returned {a} with high confidence."
        return "Two independent vision passes read this cell. " + tail
    return ("Two independent vision passes read this cell and did not agree. "
            + " ".join(f"Pass {n} {'could not read it clearly' if unreadable(t) else 'returned ' + t}, marked {c} confidence." for n, t, c in ((1, a, ca), (2, b, cb))))


def vendor_question(row, vendor, lines, fields):
    """The drafted question for a flagged field, from a reason-code template. row: a bid_fields or questionnaire_answers record (reason_code, resolving_question,
    line and value keys); lines: {line_no: rfx line}; fields: {(submission_id, line, field_name): bid_fields record}. Falls back to the stored question."""
    stored, reason = row.get("resolving_question"), row.get("reason_code")
    n = row.get("rfx_line_no")
    line = lines.get(n)
    s = spec(line)
    if reason == "ocr_unreadable":
        return f"{vendor}: line {n} on the rate card could not be read reliably. Please confirm the rate for {s}."
    if reason == "ocr_ambiguous_digit":
        seen = row.get("value") if row.get("value") is not None else row.get("snippet")
        shown = fmt.price(seen, row.get("currency")) if isinstance(seen, (int, float)) else f"{row.get('currency') or ''} {seen}".strip()
        return (f"{vendor}: please confirm the rate for line {n} ({s}). Our reading of your rate card gave {shown} "
                f"but a digit is partially obscured on the image received.")
    if reason == "arith_mismatch":
        price, total = (fields.get((row.get("submission_id"), n, f)) for f in ("unit_price", "line_total"))
        if price and total and price.get("value") is not None and total.get("value") is not None and line:
            product = price["value"] * line["annual_qty"] / (100 if price.get("basis") == "per_100_pieces" else 1)
            cur = "₹" if (price.get("currency") or "INR") == "INR" else price["currency"] + " "
            return (f"{vendor}: on line {n}, unit rate {cur}{fmt.indian(price['value'], 2)} × quantity {fmt.indian(line['annual_qty'])} gives {cur}{fmt.indian(product, 0)}, "
                    f"but the stated total is {cur}{fmt.indian(total['value'], 0)}. Please confirm which figure is correct.")
    if reason == "reference_unresolved":
        return f"{vendor}: line {n} was quoted as 'same as last year'. Please share the FY25 rate for {s}, or a fresh quote."
    if reason == "spec_incomplete":
        return f"{vendor}: line {n} does not state paper GSM. Please confirm the GSM stack for {s}."
    if reason == "no_attachment" and stored and (m := HOLD.search(stored)):
        return (f"{vendor}: you stated you hold {m['cert']} but no supporting document was attached. "
                f"Please share the current certificate showing its expiry date.")
    if reason in ("line_absent", "rate_blank"):
        return f"{vendor}: your quotation did not include a price for line {n} ({s}). Please confirm whether you would like to quote for this item."
    return whole_question(stored, row.get("question"))


def whole_question(stored, canonical):
    """Display fallback for a stored question that quotes a truncated question text, e.g. '(... board grade - at)': show the RFx questionnaire's own wording instead."""
    if not stored or not canonical:
        return stored
    full = canonical.split("?")[0].strip().rstrip(".;: -")
    def repair(m):
        cut = m.group(1)
        return f"({full})" if len(cut) < len(full) and full.startswith(cut) else m.group(0)
    return re.sub(r"\(([^()]+)\)", repair, stored)


def email(vendor_name, bid_items, questionnaire_items):
    """The clarification email as plain text. Each list is [(label, question)], already ordered; an empty list drops its heading. Numbering runs across both."""
    body = [f"Clarifications required — {vendor_name}", "",
            "The items below could not be finalised on our side. Please review each and confirm the requested details.", ""]
    n = 0
    for title, items in (("Bid clarifications", bid_items), ("Questionnaire clarifications", questionnaire_items)):
        if items:
            body += [title, ""]
        for label, question in items:
            n += 1
            body += [f"{n}. {label}", f"   {question}", ""]
    return "\n".join(body).rstrip() + "\n"
