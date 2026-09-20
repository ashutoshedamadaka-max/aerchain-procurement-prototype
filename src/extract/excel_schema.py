"""Output schema and prompts for the Excel extraction call. Everything the model returns is a cell reference plus the
cell's verbatim text; parsing numbers and dates is left to deterministic code."""

CELL = {"type": "object", "additionalProperties": False, "required": ["anchor", "snippet"], "properties": {
    "anchor": {"type": "string", "description": "Sheet name, '!', cell reference, e.g. Quotation!H10"},
    "snippet": {"type": "string", "description": "The cell's text exactly as shown in the dump, between the quotes"}}}
OPT_CELL = {"anyOf": [CELL, {"type": "null"}]}

ROW = {"type": "object", "additionalProperties": False,
       "required": ["row", "sheet", "size", "ply", "gsm_stack", "printing", "annual_qty", "rate_status", "rate", "amount",
                    "uom_text", "basis", "currency"],
       "properties": {
           "row": {"type": "integer"}, "sheet": {"type": "string"},
           "size": CELL, "ply": CELL, "gsm_stack": OPT_CELL, "printing": OPT_CELL, "annual_qty": OPT_CELL,
           "rate_status": {"type": "string", "enum": ["priced", "declined", "blank"],
                           "description": "priced = a rate is present; declined = the vendor says no quote / regret / cannot supply; blank = nothing there"},
           "rate": OPT_CELL, "amount": OPT_CELL,
           "uom_text": {"type": ["string", "null"], "description": "The unit-of-measure text for the row, verbatim"},
           "basis": {"type": "string", "enum": ["per_piece", "per_100_pieces", "per_kg", "unclear"]},
           "currency": {"type": "string", "enum": ["INR", "USD", "other", "unclear"]}}}

CONDITION = {"type": "object", "additionalProperties": False, "required": ["kind", "anchor", "snippet"], "properties": {
    "kind": {"type": "string", "enum": ["validity", "payment_terms", "freight", "moq", "bundle"]},
    "anchor": {"type": "string"}, "snippet": {"type": "string"}}}
TIER = {"type": "object", "additionalProperties": False, "required": ["min_value", "max_value", "effect", "effect_kind"], "properties": {
    "min_value": OPT_CELL, "max_value": OPT_CELL, "effect": CELL,
    "effect_kind": {"type": "string", "enum": ["uplift_pct", "discount_pct", "none"]}}}

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["vendor_name", "quotation_date", "incoterm", "rows", "conditions", "tiers"],
          "properties": {"vendor_name": CELL, "quotation_date": OPT_CELL, "incoterm": OPT_CELL,
                         "rows": {"type": "array", "items": ROW},
                         "conditions": {"type": "array", "items": CONDITION},
                         "tiers": {"type": "array", "items": TIER}}}

SYSTEM = """You extract a vendor's commercial offer from a dump of an Excel workbook. Each cell appears as: COORDINATE [type] 'text'.

Rules:
- Return references, not interpretations of numbers. For every cell you cite, give its anchor (Sheet!Cell) and its text copied exactly from the dump.
- Never compute, round, correct or fill in a number. If the cell says 12,450 return 12,450, even if it looks wrong.
- rows: one entry for each line-item row that carries a box size, on the sheet(s) holding the quotation. Skip header rows, totals rows and blank rows.
  Do not return rows from a pure specification sheet that has no rate column.
- The rate is the unit price per box; the amount is that row's line total. Use the column headers to decide which is which.
- basis comes from the rate column header and the row's UOM text (Nos, PCS, nos. all mean per piece). Use unclear only if the sheet does not say.
- conditions: the vendor's commercial terms: validity, payment terms, freight, MOQ, and any statement that the rates depend on the value or number of items awarded (bundle).
  Ignore numbered question-and-answer blocks (a buyer questionnaire answered by the vendor); those are handled separately and are not commercial conditions.
- tiers: each row of a volume slab table (awarded value bounds and the adjustment), one entry per row.
- If something is not in the workbook, return null. Never invent a cell."""


def user_prompt(dump, focus_rows=None, focus_terms=True, failures=None):
    if focus_rows is None:
        task = "Extract everything: vendor name, quotation date, incoterm, all rows, all conditions and all tiers."
    else:
        task = (f"Re-extract ONLY rows {sorted(focus_rows)} of the quotation sheet"
                + (" and all conditions and tiers" if focus_terms else "; return empty conditions and tiers")
                + ". An earlier reading of these failed automated checks"
                + (f" ({failures}). " if failures else ". ")
                + "Read the cells again with care and transcribe exactly what each cell says. Do not adjust any value to make it consistent.")
    return f"{task}\n\nWorkbook dump:\n{dump}"
