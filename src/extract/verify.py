"""Deterministic verifier. Arithmetic, provenance, plausibility, line matching and cross-vendor checks; no model involved."""
import json
import re
import statistics
from dataclasses import dataclass, field

SIZE = re.compile(r"(\d+)\s*[x×X*]\s*(\d+)\s*[x×X*]\s*(\d+)")
BAND = {3: (5, 25), 5: (25, 80), 7: (80, 220)}         # INR per piece, bulk (category pack); indicative, so checked with a tolerance
BAND_TOL = (0.8, 1.25)                                  # still catches a x10 / x100 basis or decimal error
PER_PIECE = {"per_piece": 1.0, "per_100_pieces": 0.01}  # per_kg needs a board weight, so it cannot be checked here
PEERS_MIN, OUTLIER_LOW, OUTLIER_HIGH = 2, 0.6, 1.6
CELL_FIELD = {"rate": "unit_price", "amount": "line_total", "gsm_stack": "declared_liner_gsm"}
ALL_FIELDS = ("unit_price", "line_total", "declared_liner_gsm")


def parse_number(text):
    t = re.sub(r"(?i)₹|rs\.?|inr|/-|,|\s", "", text or "")
    return float(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


NUMWORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                       "sixteen seventeen eighteen nineteen twenty".split())}
NUMWORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90})


def parse_int(text):
    m = re.search(r"\d[\d,]*", text or "")
    if m:
        return int(m.group().replace(",", ""))
    w = re.search(r"\b(" + "|".join(NUMWORDS) + r")\b", (text or "").lower())
    return NUMWORDS[w.group(1)] if w else None


def single_int(text):
    """A condition's number, only when the snippet states exactly one; two numbers means ambiguity and code will not pick one."""
    nums = re.findall(r"\d[\d,]*", text or "")
    return None if len(nums) > 1 else parse_int(text)


def parse_amount_inr(text):
    """'Rs. 50 lakh' -> 5000000, '2.5 crore' -> 25000000, 'Rs 7,50,000' -> 750000. Code does the arithmetic, not the model."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr)?", text or "", re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return v * (1e7 if unit.startswith("cr") else 1e5 if unit else 1)


def norm_print(text):
    t = (text or "").lower()
    if re.search(r"plain|unprinted|nil|no print", t):
        return "plain"
    m = re.search(r"(\d)\s*-?\s*(colou?r|color)", t)
    return f"{m.group(1)}-colour flexo" if m else t.strip()


class Rfx:
    def __init__(self, path):
        d = json.loads(open(path, encoding="utf-8").read())
        self.id, self.eval_date, self.vendors, self.lines = d["rfx_id"], d["evaluation_date"], d["invited_vendors"], d["lines"]
        self.by_dims = {}
        for ln in self.lines:
            self.by_dims.setdefault((ln["length_mm"], ln["width_mm"], ln["height_mm"], ln["ply"]), []).append(ln)

    def match_line(self, n):
        return next((ln for ln in self.lines if ln["line_no"] == n), None)

    def match(self, size_text, ply_text):
        m, ply = SIZE.search(size_text or ""), parse_int(ply_text)
        hits = self.by_dims.get((*map(int, m.groups()), ply), []) if m and ply else []
        return hits[0] if len(hits) == 1 else None


@dataclass
class Failure:
    code: str
    fields: tuple
    detail: dict = field(default_factory=dict)


@dataclass
class Checked:
    row: dict
    line: dict | None = None
    rate: float | None = None
    amount: float | None = None
    qty: float | None = None
    liner_gsm: int | None = None
    failures: list = field(default_factory=list)
    deviations: dict = field(default_factory=dict)       # field name -> reason code, informational (a finding, not an extraction doubt)
    text_number: bool = False


def _cell_ok(src, row, cell):
    """The snippet is verbatim at its locator, and the locator belongs to this row."""
    return src.row_of(cell["anchor"]) == (row["sheet"], row["row"]) and src.verbatim(cell["anchor"], cell["snippet"])


def _basis_of(text):
    t = (text or "").lower()
    return "per_100_pieces" if re.search(r"100", t) else "per_kg" if re.search(r"\bkg\b|/kg", t) else "per_piece" if re.search(r"pc|piece|box|nos|each", t) else None


def verify_row(row, src, rfx, peers=None):
    c = Checked(row)
    keys = ("size", "ply", "gsm_stack", "printing", "annual_qty", "rate", "amount", "basis_text")
    bad = [k for k in keys if row.get(k) and not _cell_ok(src, row, row[k])]
    for k in bad:
        c.failures.append(Failure("provenance_unverified", ALL_FIELDS if k in ("size", "ply", "annual_qty") else (CELL_FIELD.get(k, "unit_price"),),
                                  {"field": CELL_FIELD.get(k, k), "anchor": row[k]["anchor"]}))
    c.line = rfx.match(row["size"]["snippet"], row["ply"]["snippet"])
    if c.line is None:
        c.failures.append(Failure("line_unmatched", ALL_FIELDS))
        return c
    if row["rate_status"] != "priced":
        return c
    if not row["rate"]:
        c.failures.append(Failure("value_unparseable", ("unit_price",), {"field": "unit_price", "snippet": ""}))
        return c
    if row["currency"] in ("unclear", "other"):
        c.failures.append(Failure("currency_unclear", ("unit_price", "line_total")))
    c.rate = parse_number(row["rate"]["snippet"])
    c.text_number = src.is_text_number(row["rate"]["anchor"])
    if c.rate is None:
        c.failures.append(Failure("value_unparseable", ("unit_price",), {"field": "unit_price", "snippet": row["rate"]["snippet"]}))
    if row["amount"]:
        c.amount = parse_number(row["amount"]["snippet"])
        if c.amount is None:
            c.failures.append(Failure("value_unparseable", ("line_total",), {"field": "line_total", "snippet": row["amount"]["snippet"]}))
    if row["annual_qty"]:
        c.qty = parse_number(row["annual_qty"]["snippet"])
    verbatim_basis = _basis_of(row["basis_text"]["snippet"]) if row.get("basis_text") else None
    if row["basis"] == "unclear" or (verbatim_basis and verbatim_basis != row["basis"]):
        c.failures.append(Failure("basis_unclear", ("unit_price",)))
    factor = PER_PIECE.get(row["basis"])
    if c.rate is not None and factor and c.qty is not None and c.amount is not None:
        expected = c.qty * c.rate * factor
        if abs(c.amount - expected) > max(1.0, 0.0005 * c.amount):
            c.failures.append(Failure("arith_mismatch", ("unit_price", "line_total"), {"qty": c.qty, "rate": c.rate, "expected": expected, "stated": c.amount}))
    if c.rate is not None and factor and row["currency"] == "INR":
        lo, hi = BAND[c.line["ply"]]
        if not lo * BAND_TOL[0] <= c.rate * factor <= hi * BAND_TOL[1]:
            c.failures.append(Failure("price_out_of_band", ("unit_price",), {"rate": c.rate, "lo": lo, "hi": hi, "ply": c.line["ply"],
                                                                            "basis_txt": row["basis"].replace("_", " ")}))
        others = (peers or {}).get(c.line["line_no"], [])
        if len(others) >= PEERS_MIN:
            med = statistics.median(others)
            if not OUTLIER_LOW * med <= c.rate * factor <= OUTLIER_HIGH * med:
                c.failures.append(Failure("cross_vendor_outlier", ("unit_price",)))
    if not row["gsm_stack"]:
        c.failures.append(Failure("spec_incomplete", ("unit_price", "declared_liner_gsm"), {"spec_gsm": c.line["liner_gsm"]}))
    if row["gsm_stack"]:
        c.liner_gsm = parse_int(row["gsm_stack"]["snippet"])
        if c.liner_gsm is None:
            c.failures.append(Failure("value_unparseable", ("declared_liner_gsm",), {"field": "declared_liner_gsm", "snippet": row["gsm_stack"]["snippet"]}))
        elif c.liner_gsm != c.line["liner_gsm"]:
            c.deviations["unit_price"] = c.deviations["declared_liner_gsm"] = "spec_mismatch_gsm"
    if row["printing"] and norm_print(row["printing"]["snippet"]) != c.line["print_spec"]:
        c.deviations.setdefault("unit_price", "spec_mismatch_print")
    return c


def verify_terms(data, src):
    """Provenance and parseability of vendor, date, conditions, slabs, discounts and quantity breaks. Empty list = fine."""
    def ok(cell):
        return cell is None or src.verbatim(cell["anchor"], cell["snippet"])
    probs = [f"{k} provenance" for k in ("vendor_name", "quotation_date", "incoterm") if k in data and not ok(data[k])]
    for cond in data.get("conditions", []):
        if not ok(cond) or (cond["kind"] in ("validity", "payment_terms", "moq") and single_int(cond["snippet"]) is None):
            probs.append(f"condition {cond['kind']}")
    for i, t in enumerate(data.get("tiers", [])):
        if not all(ok(t[k]) for k in ("min_value", "max_value", "effect")):
            probs.append(f"tier {i} provenance")
        if t["min_value"] is None or parse_number(t["min_value"]["snippet"]) is None:
            probs.append(f"tier {i} lower bound not a number")
        if t["effect_kind"] != "none" and not re.search(r"\d+(?:\.\d+)?\s*%", t["effect"]["snippet"]):
            probs.append(f"tier {i} effect has no percentage")
    for i, d in enumerate(data.get("order_discounts", [])):
        if not (ok(d["effect"]) and ok(d["threshold"])):
            probs.append(f"discount {i} provenance")
        if parse_amount_inr(d["threshold"]["snippet"]) is None or not re.search(r"\d+(?:\.\d+)?\s*%", d["effect"]["snippet"]):
            probs.append(f"discount {i} threshold or percentage not parseable")
    for i, q in enumerate(data.get("qty_breaks", [])):
        if not all(ok(q[k]) for k in ("size", "min_qty", "effect")):
            probs.append(f"qty break {i} provenance")
        if parse_int(q["min_qty"]["snippet"]) is None or not re.search(r"\d+(?:\.\d+)?\s*%", q["effect"]["snippet"]):
            probs.append(f"qty break {i} quantity or percentage not parseable")
    return probs
