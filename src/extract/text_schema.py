"""Schemas and prompts for the PDF, Word and email handlers. Same contract as the Excel one: the model returns locators and
verbatim snippets; code parses numbers, converts nothing, and decides which RFx lines a statement covers."""
LOC = "The locator exactly as it appears in the dump, e.g. p2:r13:desc, p2:L4, para12 or L6. No sheet prefix."
CELL = {"type": "object", "additionalProperties": False, "required": ["anchor", "snippet"], "properties": {
    "anchor": {"type": "string", "description": LOC},
    "snippet": {"type": "string", "description": "A contiguous piece of the text at that locator, copied exactly"}}}
OPT_CELL = {"anyOf": [CELL, {"type": "null"}]}
CONDITION = {"type": "object", "additionalProperties": False, "required": ["kind", "anchor", "snippet"], "properties": {
    "kind": {"type": "string", "enum": ["validity", "payment_terms", "freight", "moq", "bundle"]},
    "anchor": {"type": "string", "description": LOC},
    "snippet": {"type": "string", "description": "The shortest phrase that states the term, copied exactly, e.g. 'Payment: 30 days net.'"}}}

ROW_T = {"type": "object", "additionalProperties": False,
         "required": ["row", "sheet", "size", "ply", "gsm_stack", "printing", "annual_qty", "rate_status", "rate", "basis_text", "amount", "basis", "currency"],
         "properties": {
             "row": {"type": "integer", "description": "PDF: the printed item number. Word: the paragraph number."},
             "sheet": {"type": "string", "description": "PDF: the page tag from the locator, e.g. p2. Word: doc."},
             "size": CELL, "ply": CELL, "gsm_stack": OPT_CELL, "printing": OPT_CELL, "annual_qty": OPT_CELL,
             "rate_status": {"type": "string", "enum": ["priced", "declined", "blank"]},
             "rate": OPT_CELL, "basis_text": OPT_CELL, "amount": OPT_CELL,
             "basis": {"type": "string", "enum": ["per_piece", "per_100_pieces", "per_kg", "unclear"]},
             "currency": {"type": "string", "enum": ["INR", "USD", "other", "unclear"]}}}
DISCOUNT = {"type": "object", "additionalProperties": False, "required": ["effect", "threshold", "effect_kind"], "properties": {
    "effect": CELL, "threshold": CELL, "effect_kind": {"type": "string", "enum": ["discount_pct", "uplift_pct"]}}}
QTY_BREAK = {"type": "object", "additionalProperties": False, "required": ["size", "min_qty", "effect", "effect_kind"], "properties": {
    "size": CELL, "min_qty": CELL, "effect": CELL, "effect_kind": {"type": "string", "enum": ["discount_pct", "uplift_pct"]}}}

DOC_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["vendor_name", "quotation_date", "incoterm", "rows", "conditions", "order_discounts", "qty_breaks"],
              "properties": {"vendor_name": CELL, "quotation_date": OPT_CELL, "incoterm": OPT_CELL, "rows": {"type": "array", "items": ROW_T},
                             "conditions": {"type": "array", "items": CONDITION},
                             "order_discounts": {"type": "array", "items": DISCOUNT}, "qty_breaks": {"type": "array", "items": QTY_BREAK}}}

STATEMENT = {"type": "object", "additionalProperties": False, "required": ["kind", "ply", "rate", "basis_text", "basis", "currency", "scope_text"],
             "properties": {"kind": {"type": "string", "enum": ["rate", "prior_year_reference"]},
                            "ply": OPT_CELL, "rate": OPT_CELL, "basis_text": OPT_CELL,
                            "basis": {"type": "string", "enum": ["per_piece", "per_100_pieces", "per_kg", "unclear"]},
                            "currency": {"type": "string", "enum": ["INR", "USD", "other", "unclear"]},
                            "scope_text": {"anyOf": [CELL, {"type": "null"}]}}}
EMAIL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["vendor_name", "quotation_date", "incoterm", "statements", "conditions"],
                "properties": {"vendor_name": CELL, "quotation_date": OPT_CELL, "incoterm": OPT_CELL,
                               "statements": {"type": "array", "items": STATEMENT}, "conditions": {"type": "array", "items": CONDITION}}}

COMMON = """Rules:
- Return references, not interpretations of numbers. Every cell you cite is a locator plus text copied exactly from the dump; the snippet must be a
  contiguous piece of the text at that locator. Never compute, convert, round, correct or fill in a number.
- The rate snippet is only the amount, exactly the characters that appear (for example 'Rs. 1,573.09', 'Rs 42', or just '38' when that clause has no currency mark),
  and never includes the unit words after it. Never add a currency mark or any other character that is not in the text at that locator.
  Put the unit words (for example 'per 100 pcs', '/kg') in basis_text, then choose the matching basis value. Do not convert between bases.
- conditions: the vendor's commercial terms: validity, payment terms, freight or transport, MOQ, and bundle (rates that hold only if all items are awarded).
  A discount tied to an order value is NOT a condition: put it in order_discounts. One term per entry, each with its own shortest snippet. Ignore the questionnaire (numbered questions and the vendor's answers to them); it is handled separately.
- A currency or unit stated once for a list of prices (for example 'Rs 42/kg for the 5-ply, 38 for the 3-ply') applies to every price in that list: set the currency and basis
  fields on each statement accordingly, but the snippets still contain only the characters actually present.
- Copy locators exactly as shown in the dump, including any page tag.
- If something is not in the document, return null or an empty list. Never invent a locator."""

PDF_SYSTEM = f"""You extract a vendor's commercial offer from a text dump of a PDF quotation. Table rows appear as pN:rK with cells pN:rK:desc, :qty and :total;
all other lines appear as pN:Lk with their font size.

{COMMON}
- rows: one entry per table row. row = the printed item number, sheet = the page tag (p1, p2, p3). The description cell holds the box size, ply, GSM stack,
  print and the rate. size = the dimensions (e.g. 300x200x150), ply = the ply text (e.g. 3-ply), gsm_stack = the GSM stack (e.g. 150/120/150),
  printing = the print text. annual_qty = the :qty cell, amount = the :total cell. Use the :desc locator for size, ply, gsm_stack, printing, rate and basis_text.
- Small print (lines shown at 7pt, or any line placed under or between tables) is commercially significant. If it grants a discount or sets a condition on
  order value, return it in order_discounts: effect = the percentage as printed (e.g. 2%), threshold = the order value as printed (e.g. Rs. 50 lakh).
- qty_breaks: leave empty unless a sentence ties a price change to an annual quantity for a named size."""

DOCX_SYSTEM = f"""You extract a vendor's commercial offer from a Word document whose commercials are written as prose. Each paragraph appears as paraN: 'text'.

{COMMON}
- rows: one entry per paragraph that names a carton size and either offers a price or declines the size. row = the paragraph number, sheet = doc.
  size = the dimensions (e.g. 300x200x150), ply = the ply text (e.g. 3-ply), rate = the price with its currency mark (e.g. Rs. 5.52), basis_text = the words giving the unit
  (e.g. per box). gsm_stack = only what the text states about liner GSM (e.g. 120 GSM); if the text does not state a GSM, return null. Never infer one.
  printing, annual_qty and amount are null unless the paragraph states them. A paragraph that says the size cannot be supplied is rate_status declined with rate null.
  A size that has no paragraph at all gets no row.
- qty_breaks: sentences that change the price of a named size if the annual commitment exceeds a quantity. size = the dimensions, min_qty = the quantity as printed,
  effect = the percentage as printed.
- order_discounts: empty unless the document ties a discount to total order value."""

EMAIL_SYSTEM = f"""You extract a vendor's commercial offer from a short email. Each line appears as Ln: 'text'.

{COMMON}
- statements: kind rate = a price for a whole ply class (ply = the ply words e.g. 5-ply, rate = the number with any currency mark, basis_text = the words giving the unit
  if stated, otherwise null; choose basis from the wording, carrying a basis stated once across the list). kind prior_year_reference = a statement that other items are priced
  as in an earlier period (scope_text = those words; ply, rate, basis_text null). Do not turn a reference into a number.
- The vendor name is in the signature or header; the date is the message date."""


def doc_prompt(dump, todo=None, focus_terms=True, failures=None):
    if not todo:
        task = "Extract everything: vendor name, date, all rows, all conditions, discounts and quantity breaks."
    else:
        rows = ", ".join(f"{s} row {n}" for s, n in sorted(todo))
        task = (f"Re-extract ONLY these rows: {rows}" + (", and all conditions, discounts and quantity breaks" if focus_terms else "; return empty conditions, discounts and quantity breaks")
                + f". An earlier reading of these failed automated checks ({failures}). Read them again with care and transcribe exactly what the text says. "
                  "Do not adjust or infer any value to make it consistent.")
    return f"{task}\n\nDocument dump:\n{dump}"


def email_prompt(dump, failures=None):
    return (("Extract the offer." if not failures else f"Extract the offer again with care. An earlier reading failed automated checks ({failures}). Transcribe exactly.")
            + f"\n\nEmail dump:\n{dump}")
