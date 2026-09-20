"""Field records: the four schema states, reason codes, and the resolving question templated per reason code."""
from dataclasses import dataclass

STATES = ("extracted", "needs_review", "not_quoted", "missing")   # enforced by the bid_fields CHECK constraint

# reason -> resolving question. Only uncertain fields (needs_review, missing) get one.
QUESTIONS = {
    "arith_mismatch": "{vendor}: on line {line}, {qty:,.0f} x {rate:,.2f} = {expected:,.2f}, but the stated total is {stated:,.2f}. "
                      "Which is right, the unit rate or the total?",
    "price_out_of_band": "{vendor}: the line {line} rate {rate:,.2f} ({basis_txt}) is outside the plausible INR {lo:g}-{hi:g} per piece for a {ply}-ply box. "
                         "Please confirm the rate and whether it is per piece, per 100 or per kg.",
    "provenance_unverified": "{vendor}: the {field} for line {line} could not be verified against the source cell {anchor}. Please confirm the figure you intended.",
    "value_unparseable": "{vendor}: the {field} cell for line {line} reads '{snippet}', which is not a number. What figure did you intend?",
    "basis_unclear": "{vendor}: is the line {line} rate per piece, per 100 pieces or per kg?",
    "cross_vendor_outlier": "{vendor}: the line {line} rate is far from the other bids. Please confirm it and the specification it covers.",
    "row_not_extracted": "{vendor}: a row for line {line} could not be read reliably from the file. Please re-send that line as text.",
    "line_absent": "{vendor} neither priced nor declined line {line}. Ask them whether they will quote it.",
    "spec_incomplete": "{vendor}: line {line} does not state the liner GSM. What GSM and BF are you quoting? Our spec is {spec_gsm} GSM.",
    "reference_unresolved": "{vendor}: line {line} is priced only as 'same as last year'. What was the FY25 rate for this SKU? Please supply the FY25 PO price, "
                            "or ask {vendor} to quote it afresh.",
    "currency_unclear": "{vendor}: is the line {line} rate in INR or another currency?",
    "ocr_unreadable": "{vendor}: the line {line} rate on the photographed rate card cannot be read ({note}). Please re-send that rate as text or a clearer photo.",
    "ocr_ambiguous_digit": "{vendor}: the line {line} rate on the photographed rate card is uncertain ({readings}). Please confirm the figure.",
    "rate_blank": "{vendor} left the line {line} rate blank without declining. Ask them to price it or decline it.",
}


def question(reason, **kw):
    return QUESTIONS[reason].format(**kw) if reason in QUESTIONS else None


@dataclass
class Field:
    line_no: int
    field_name: str          # unit_price | line_total | declared_liner_gsm
    state: str
    value: float | None = None
    unit: str | None = None
    basis: str | None = None
    currency: str | None = None
    reason_code: str | None = None
    anchor: str | None = None
    snippet: str | None = None
    derivation: str | None = None
    resolving_question: str | None = None

    def __post_init__(self):
        assert self.state in STATES, self.state
