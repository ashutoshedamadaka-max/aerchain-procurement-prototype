"""Normalization: put every extracted unit price on INR per piece, with a derivation chain, visible assumptions and a comparability flag.

Ladder implemented (bid-normalization-engine): basis conversion -> currency -> spec comparability. Discounts are stored as
condition + effect (tier_rules) and evaluated against a scenario, never pre-applied. Freight: landed cost is UNKNOWN by default. A vendor's
stated figure is used; freight-inclusive terms are unaffected; where terms are "extra" with no figure, a buyer-supplied estimate (unset by
default, never defaulted) moves the row to comparable_with_assumptions. `comparability` stays the ex-freight price flag; `landed_comparability`
is the flag a landed-cost comparison must use. Not here: MOQ and validity feasibility, award strategies, anything conversational.

All arithmetic is in src/conversions.py; this module only chooses which conversion applies and records why.
Reads and writes procurement.db; adds two tables (assumptions, norm_prices) and leaves the pricing schema untouched.
"""
import argparse
import collections
import json
import pathlib
import re
import sqlite3
from dataclasses import dataclass, field, replace

from .conversions import (blank_area_m2, board_weight_kg, convert_currency, effective_gsm, price_per_piece_from_per_100,
                          price_per_piece_from_per_kg)

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLUTE_TAKE_UP = {"A": 1.53, "B": 1.40, "C": 1.50}     # B and C from the category pack (1.4-1.5); A is this project's assumption
LEVELS = ("comparable", "comparable_with_assumptions", "not_comparable")

DDL = """
CREATE TABLE IF NOT EXISTS assumptions (
  assumption_id TEXT PRIMARY KEY, label TEXT NOT NULL, value REAL, value_text TEXT, unit TEXT, source TEXT, as_of TEXT,
  editable INTEGER NOT NULL, note TEXT);
DROP TABLE IF EXISTS norm_prices;
CREATE TABLE norm_prices (
  submission_id TEXT NOT NULL, rfx_line_no INTEGER NOT NULL, field_id INTEGER, extraction_state TEXT,
  stated_value REAL, stated_unit TEXT, stated_basis TEXT, stated_currency TEXT,
  orig_currency_per_piece REAL, inr_per_piece REAL, fx_rate REAL, fx_source TEXT, fx_date TEXT,
  comparability TEXT CHECK (comparability IN ('comparable','comparable_with_assumptions','not_comparable')),
  comparability_reasons TEXT, derivation TEXT, assumptions_used TEXT,
  declared_liner_gsm INTEGER, spec_liner_gsm INTEGER, board_weight_declared_kg REAL, board_weight_spec_kg REAL,
  board_weight_ratio REAL, adjusted_inr_per_piece REAL, adjustment_note TEXT,
  freight_terms TEXT, freight_status TEXT NOT NULL DEFAULT 'not_evaluated', freight_pct REAL, freight_inr_per_piece REAL, freight_source TEXT,
  landed_inr_per_piece REAL,
  landed_comparability TEXT CHECK (landed_comparability IN ('comparable','comparable_with_assumptions','not_comparable')),
  landed_reasons TEXT,
  PRIMARY KEY (submission_id, rfx_line_no));
"""


@dataclass(frozen=True)
class Assumptions:
    """Buyer-editable inputs. The FX rate is supplied, never fetched: a bid tab whose totals move on reload is not an audit artifact."""
    fx_usd_inr: float = 88.50
    fx_source: str = "RBI reference rate"
    fx_date: str = "2026-03-11"
    take_up: tuple = tuple(FLUTE_TAKE_UP.items())
    glue_lap_mm: float = 40.0
    freight_estimate_pct: float | None = None        # % of order value; UNSET by default and never defaulted: unset means landed cost is unknown
    freight_by_vendor: tuple = ()                    # ((vendor_id, %), ...) per-vendor overrides. Precedence: stated figure > vendor override > global > nothing

    def estimate_for(self, vendor_id):
        return dict(self.freight_by_vendor).get(vendor_id, self.freight_estimate_pct)

    def take_up_for(self, flute):
        t = dict(self.take_up)
        return sum(t[c] for c in flute) / len(flute)

    def scaled(self, name, k):
        if name == "fx":
            return replace(self, fx_usd_inr=self.fx_usd_inr * k)
        if name == "glue_lap":
            return replace(self, glue_lap_mm=self.glue_lap_mm * k)
        if name == "freight":
            return replace(self, freight_estimate_pct=None if self.freight_estimate_pct is None else self.freight_estimate_pct * k)
        return replace(self, take_up=tuple((f, v * k) for f, v in self.take_up))


@dataclass
class Result:
    inr: float | None = None
    orig_pc: float | None = None
    steps: list = field(default_factory=list)
    used: set = field(default_factory=set)          # assumption ids: fx | take_up | glue_lap
    level: int = 0
    reasons: list = field(default_factory=list)
    weight_kg: float | None = None


def _raise(res, level, reason):
    res.level = max(res.level, level)
    if reason not in res.reasons:
        res.reasons.append(reason)


def board_weight(spec, liner_gsm, A):
    """Board weight of one RFx box with the given liner GSM (mediums as in the RFx stack). Returns (weight, steps, factor)."""
    area = blank_area_m2(spec["length_mm"], spec["width_mm"], spec["height_mm"], A.glue_lap_mm)
    stack = [int(x) for x in spec["gsm_stack"].split("/")]
    liners, mediums = [liner_gsm] * len(stack[0::2]), stack[1::2]
    factor = A.take_up_for(spec["flute"])
    eff = effective_gsm(liners, mediums, factor)
    w = board_weight_kg(area.value, eff.value)
    return w.value, [{"step": "blank_area", "value": area.value, "unit": "m2", "derivation": area.derivation},
                     {"step": "effective_gsm", "value": eff.value, "unit": "g/m2", "derivation": eff.derivation,
                      "assumption": f"take_up_factor:{spec['flute']}={factor:g}"},
                     {"step": "board_weight", "value": w.value, "unit": "kg/piece", "derivation": w.derivation}], factor


def convert(row, spec, declared_liner, A):
    """Basis conversion then currency, in the ladder's order. row: value, basis, currency."""
    r, v = Result(), row["value"]
    if row["basis"] == "per_piece":
        pc = v
        r.steps.append({"step": "basis", "rule": "per piece: no conversion"})
    elif row["basis"] == "per_100_pieces":
        c = price_per_piece_from_per_100(v)
        pc = c.value
        r.steps.append({"step": "basis", "rule": "per 100 pieces to per piece (lossless)", "value": pc, "derivation": c.derivation})
    elif row["basis"] == "per_kg":
        w, wsteps, factor = board_weight(spec, declared_liner or spec["liner_gsm"], A)
        c = price_per_piece_from_per_kg(v, w)
        pc, r.weight_kg = c.value, w
        r.steps += wsteps + [{"step": "basis", "rule": "per kg to per piece using the derived board weight", "value": pc, "derivation": c.derivation}]
        r.used |= {"take_up", "glue_lap"}
        _raise(r, 1, "assumption:take_up_factor")
    else:
        _raise(r, 2, "basis_unclear")
        return r
    r.orig_pc = pc
    if row["currency"] == "INR":
        r.inr = pc
    elif row["currency"] == "USD":
        c = convert_currency(pc, A.fx_usd_inr, A.fx_source, A.fx_date)
        r.inr = c.value
        r.steps.append({"step": "currency", "value": c.value, "unit": "INR/piece", "derivation": c.derivation, **c.provenance})
        r.used.add("fx")
        _raise(r, 1, "assumption:fx_rate")
    else:
        _raise(r, 2, "currency_unsupported")
    return r


FIGURE_UNITS = ("pct_of_order_value", "inr_per_piece")     # how a stated freight figure is recorded in conditions (value_num + value_text)
FREIGHT_INCLUDED = re.compile(r"(?i)\b(?:freight|transport|delivery|carriage)\b[^.]{0,20}\b(?:included|inclusive|free|paid|prepaid)\b|\bfree delivery\b|\bdelivered\b|\bfor destination\b")
FREIGHT_NOT_INCLUDED = re.compile(r"(?i)\bnot\s+(?:included|inclusive)\b")
FREIGHT_EXTRA = re.compile(r"(?i)\b(?:extra|excluded|separately|at actuals|to (?:your|buyer'?s) account|on buyer)\b")
INCOTERM_FREIGHT = {"DDP": "included", "EXW": "extra", "FOB": "extra"}     # CIF and others leave inland freight open: unknown


def classify_freight(incoterm, conds):
    """One vendor's freight terms. conds: (value_num, value_text, snippet, anchor) per freight condition.
    stated  = a figure is on record; included = freight is in the price; extra = buyer pays and no figure is stated;
    unknown = silent, ambiguous or contradictory (never guessed into another class)."""
    for value, unit, snippet, anchor in conds:
        if value is not None and unit in FIGURE_UNITS:
            return dict(terms="stated", figure=value, unit=unit, source=f"{anchor}: {snippet}")
    said = set()
    for _, _, snippet, _ in conds:
        if FREIGHT_EXTRA.search(snippet or "") or FREIGHT_NOT_INCLUDED.search(snippet or ""):
            said.add("extra")
        elif FREIGHT_INCLUDED.search(snippet or ""):
            said.add("included")
    from_incoterm = INCOTERM_FREIGHT.get(incoterm)
    kinds = said | ({from_incoterm} if from_incoterm else set())
    source = "; ".join(f"{a}: {s}" for _, _, s, a in conds) or (f"incoterm {incoterm}" if from_incoterm else None)
    if len(kinds) == 1:
        return dict(terms=kinds.pop(), figure=None, unit=None, source=source)
    return dict(terms="unknown", figure=None, unit=None, source=source, conflict=len(kinds) > 1)


def apply_freight(inr, ft, A):
    """(status, pct of order value, freight INR per piece, landed INR per piece). No price, or terms that are extra with no figure and no
    buyer estimate, mean landed cost is unknown (status not_evaluated, landed None). Nothing is ever defaulted."""
    if inr is None:
        return "not_evaluated", None, None, None
    if ft["terms"] == "included":
        return "included", None, 0.0, inr
    if ft["terms"] == "stated":
        if ft["unit"] == "inr_per_piece":
            return "stated", None, ft["figure"], inr + ft["figure"]
        return "stated", ft["figure"], inr * ft["figure"] / 100, inr * (1 + ft["figure"] / 100)
    if ft["terms"] == "extra" and A.freight_estimate_pct is not None:
        return "estimated", A.freight_estimate_pct, inr * A.freight_estimate_pct / 100, inr * (1 + A.freight_estimate_pct / 100)
    return "not_evaluated", None, None, None


def freight_impact(base_value, pct):
    """What a freight estimate is worth on the value it applies to (INR at RFx quantity): one percentage point, and a 1 percent relative change."""
    return json.dumps({"applies_to_value_inr": round(base_value, 2), "impact_per_1_percentage_point_inr": round(base_value / 100, 2),
                       "impact_per_1pct_relative_change_inr": round(base_value * (pct or 0) / 100 * 0.01, 2)})


def assumption_rows(A, flutes, spec_assumed_vendors, fbase=None, vterms=None):
    fbase, vterms = fbase or {}, vterms or {}
    rows = [("fx_usd_inr", "USD to INR exchange rate", A.fx_usd_inr, None, "INR per USD", A.fx_source, A.fx_date, 1,
             "Supplied, not fetched. FX exposure is a risk annotation, not a cost line."),
            ("glue_lap_mm", "RSC glue lap used in blank area", A.glue_lap_mm, None, "mm", "category pack", None, 1, None),
            ("medium_gsm_as_rfx", "Vendor's medium GSM assumed equal to the RFx stack", None, "assumed", None, "vendors state liner GSM only", None, 0, None),
            ("freight_estimate_pct_of_order_value", "Freight estimate applied where terms are 'extra' and no figure is stated, for vendors without an override", A.freight_estimate_pct,
             freight_impact(fbase["freight_estimate_pct_of_order_value"], A.freight_estimate_pct) if fbase.get("freight_estimate_pct_of_order_value") else None,
             "% of order value", "buyer-supplied; unset by default, never defaulted", None, 1,
             "Unset means landed cost is unknown. A vendor's stated figure and a per-vendor override both take precedence; freight-inclusive terms are unaffected."),
            ("discount_operator", "Order-value discounts read as 'exceeding' (strictly greater); slabs read lower-bound inclusive", None, "strict > for discounts, >= for slabs",
             None, "V2 says 'exceeding'; V1's sheet says lower bound inclusive", None, 0, "tier_rules stores the threshold, not the operator")]
    for v, pct in A.freight_by_vendor:
        ident, applies = f"freight_estimate_pct:{v}", fbase.get(f"freight_estimate_pct:{v}", 0.0)
        note = (f"Overrides the global estimate for {v}. Precedence: stated figure, then this override, then the global estimate, then nothing." if applies else
                f"Applies to no rows: {v}'s freight terms are '{vterms.get(v, 'not in the store')}', and an estimate only applies to 'extra' with no stated figure.")
        rows.append((ident, f"Freight estimate for {v}", pct, freight_impact(applies, pct) if applies else None, "% of order value",
                     "buyer-supplied per-vendor override", None, 1, note))
    rows += [(f"take_up_factor:{f}", f"Flute take-up factor, {f}", A.take_up_for(f), None, "ratio", "B, C from the category pack; A is a project assumption",
              None, 1, "Composite flutes use the mean of their constituents; the medium is longer than the liner") for f in sorted(flutes)]
    rows += [(f"spec_assumed_as_rfx:{v}", f"{v} states no board specification; assumed to meet the RFx specification", None, "assumed", None,
              "vendor document is silent on GSM", None, 1, "Unverified until the vendor confirms") for v in sorted(spec_assumed_vendors)]
    return rows


def normalize_all(con, A=Assumptions()):
    con.executescript(DDL)
    con.row_factory = sqlite3.Row
    specs = {r["line_no"]: dict(r) for r in con.execute("SELECT * FROM rfx_lines")}
    vendor = {r["submission_id"]: r["vendor_id"] for r in con.execute("SELECT submission_id, vendor_id FROM submissions")}
    declared = {(r["submission_id"], r["rfx_line_no"]): dict(r) for r in con.execute(
        "SELECT * FROM bid_fields WHERE field_name='declared_liner_gsm'")}
    has_spec_rows = {r["submission_id"] for r in declared.values()}
    fconds = collections.defaultdict(list)
    for r in con.execute("SELECT submission_id, value_num, value_text, snippet, anchor FROM conditions WHERE kind='freight'"):
        fconds[r["submission_id"]].append(tuple(r)[1:])
    freight = {r["submission_id"]: classify_freight(r["incoterm"], fconds[r["submission_id"]]) for r in con.execute("SELECT submission_id, incoterm FROM submissions")}
    out, spec_assumed, flutes = [], set(), set()
    fbase, vterms = collections.defaultdict(float), {}          # value each freight assumption applies to, at RFx quantity; freight terms per vendor
    for b in con.execute("SELECT * FROM bid_fields WHERE field_name='unit_price' ORDER BY submission_id, rfx_line_no"):
        sid, n, spec = b["submission_id"], b["rfx_line_no"], specs[b["rfx_line_no"]]
        base = dict(submission_id=sid, rfx_line_no=n, field_id=b["field_id"], extraction_state=b["state"], stated_value=b["value"], stated_unit=b["unit"],
                    stated_basis=b["basis"], stated_currency=b["currency"], spec_liner_gsm=spec["liner_gsm"], fx_rate=None, fx_source=None, fx_date=None,
                    orig_currency_per_piece=None, inr_per_piece=None, comparability=None, comparability_reasons=json.dumps([b["state"]]),
                    derivation="[]", assumptions_used="[]", declared_liner_gsm=None, board_weight_declared_kg=None, board_weight_spec_kg=None,
                    board_weight_ratio=None, adjusted_inr_per_piece=None, adjustment_note=None,
                    freight_terms=freight[sid]["terms"], freight_status="not_evaluated", freight_pct=None, freight_inr_per_piece=None,
                    freight_source=freight[sid]["source"], landed_inr_per_piece=None, landed_comparability=None, landed_reasons=json.dumps([b["state"]]))
        if b["value"] is None or b["state"] in ("missing", "not_quoted"):
            out.append(base)
            continue
        d = declared.get((sid, n))
        decl = int(d["value"]) if d and d["value"] is not None else None
        r = convert(b, spec, decl, A)
        flutes.add(spec["flute"])
        steps = [{"step": "extracted", "value": b["value"], "unit": b["unit"], "basis": b["basis"], "currency": b["currency"],
                  "source": b["anchor"], "snippet": b["snippet"]}] + r.steps
        base.update(declared_liner_gsm=decl)
        if r.inr is not None and b["currency"] == "USD":
            base.update(fx_rate=A.fx_usd_inr, fx_source=A.fx_source, fx_date=A.fx_date)
        # spec comparability
        if sid not in has_spec_rows:
            spec_assumed.add(vendor[sid])
            r.used.add(f"spec:{vendor[sid]}")
            _raise(r, 1, "assumption:spec_as_rfx")
        elif d is None or d["state"] == "missing" or decl is None:
            _raise(r, 2, "spec_incomplete")
        elif decl != spec["liner_gsm"] and r.inr is not None:
            w_decl, _, _ = board_weight(spec, decl, A)
            w_spec, _, _ = board_weight(spec, spec["liner_gsm"], A)
            ratio = w_decl / w_spec
            base.update(board_weight_declared_kg=w_decl, board_weight_spec_kg=w_spec, board_weight_ratio=ratio, adjusted_inr_per_piece=r.inr / ratio,
                        adjustment_note=(f"{decl} GSM liners quoted against {spec['liner_gsm']} GSM asked. Adjusted price = quoted price / board-weight ratio "
                                         f"({ratio:.4f}); an estimate that assumes price is proportional to board weight. Priced separately, excluded from compliant totals."))
            _raise(r, 2, "spec_variance")
        used, base_pc = [], r.inr
        for a in sorted(r.used):
            if a in ("fx", "take_up", "glue_lap") and base_pc is not None:
                alt = convert(b, spec, decl, A.scaled(a, 1.01)).inr
                ident = {"fx": "fx_usd_inr", "take_up": f"take_up_factor:{spec['flute']}", "glue_lap": "glue_lap_mm"}[a]
                used.append({"id": ident, "impact_if_changed_1pct_inr_pc": alt - base_pc})
            else:
                used.append({"id": a})
        est = A.estimate_for(vendor[sid])
        Av = replace(A, freight_estimate_pct=est)
        vterms[vendor[sid]] = freight[sid]["terms"]
        status, fpct, finr, landed = apply_freight(r.inr, freight[sid], Av)
        lreasons, llevel = list(r.reasons), r.level
        if status == "not_evaluated":
            llevel = 2
            lreasons.append("freight_unknown")
        elif status == "estimated":
            llevel = max(llevel, 1)
            lreasons.append("assumption:freight_estimate")
            override = vendor[sid] in dict(A.freight_by_vendor)
            ident = f"freight_estimate_pct:{vendor[sid]}" if override else "freight_estimate_pct_of_order_value"
            used.append({"id": ident, "impact_if_changed_1pct_inr_pc": apply_freight(r.inr, freight[sid], Av.scaled("freight", 1.01))[3] - landed})
            fbase[ident] += specs[n]["annual_qty"] * r.inr
            base.update(freight_source=f"{base['freight_source']}; estimate {est:g}% ({'vendor override' if override else 'global'})")
        base.update(orig_currency_per_piece=r.orig_pc, inr_per_piece=r.inr, comparability=LEVELS[r.level], comparability_reasons=json.dumps(r.reasons),
                    derivation=json.dumps(steps), assumptions_used=json.dumps(used), freight_status=status, freight_pct=fpct, freight_inr_per_piece=finr,
                    landed_inr_per_piece=landed, landed_comparability=LEVELS[llevel] if r.inr is not None else None, landed_reasons=json.dumps(lreasons))
        out.append(base)
    con.execute("DELETE FROM norm_prices")
    cols = list(out[0])
    con.executemany(f"INSERT INTO norm_prices ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})", out)
    con.execute("DELETE FROM assumptions")
    con.executemany("INSERT INTO assumptions VALUES (?,?,?,?,?,?,?,?,?)", assumption_rows(A, flutes, spec_assumed, fbase, vterms))
    con.commit()
    con.row_factory = None
    return out


# ---- scenario evaluation: discounts as condition + effect, evaluated against what is actually awarded ----
def whole_lines(con, assignment):
    """{line_no: vendor_id} -> allocation {line_no: {vendor_id: pieces}} at the full RFx quantity. Not an award strategy."""
    q = dict(con.execute("SELECT line_no, annual_qty FROM rfx_lines"))
    return {n: {v: q[n]} for n, v in assignment.items()}


def evaluate_scenario(con, allocation):
    """Apply each vendor's stored discount conditions to the allocation. Order: line-level quantity breaks, then order-value rules
    (slabs and thresholds) on the post-break value. Returns per-vendor lines, every adjustment with whether it applied, and the
    discount NOT captured. Lines that are not_comparable, unpriced or absent are excluded from value and totals, never estimated."""
    prices = {(v, n): dict(zip(("inr", "comp", "state"), (i, c, s))) for v, n, i, c, s in con.execute(
        "SELECT s.vendor_id, p.rfx_line_no, p.inr_per_piece, p.comparability, p.extraction_state FROM norm_prices p JOIN submissions s USING(submission_id)")}
    rules = collections.defaultdict(list)
    for rid, v, kind, lo, hi, line, mq, pct, snip in con.execute("SELECT rule_id,vendor_id,kind,min_value_inr,max_value_inr,rfx_line_no,min_qty,effect_pct,snippet FROM tier_rules"):
        rules[v].append(dict(rule_id=rid, kind=kind, lo=lo, hi=hi, line=line, min_qty=mq, pct=pct, snippet=snip))
    per_vendor, adjustments, excluded = {}, [], []
    for v in sorted({v for lines in allocation.values() for v in lines}):
        lines = []
        for n, alloc in sorted(allocation.items()):
            if v not in alloc:
                continue
            p = prices.get((v, n))
            if not p or p["inr"] is None or p["comp"] in (None, "not_comparable"):
                excluded.append({"vendor": v, "line": n, "reason": "no price" if not p or p["inr"] is None else "not_comparable"})
                continue
            lines.append({"line": n, "pieces": alloc[v], "base_inr_pc": p["inr"], "pct": 0.0, "comparability": p["comp"], "extraction_state": p["state"]})
        for rule in (r for r in rules[v] if r["kind"] == "line_qty_break"):
            ln = next((x for x in lines if x["line"] == rule["line"]), None)
            if ln is None:
                continue
            met = ln["pieces"] > rule["min_qty"]
            value = ln["pieces"] * ln["base_inr_pc"]
            if met:
                ln["pct"] += rule["pct"]
            adjustments.append(dict(vendor=v, rule_id=rule["rule_id"], kind=rule["kind"], line=rule["line"], condition=f"allocated pieces > {rule['min_qty']:,}",
                                    actual=ln["pieces"], applied=met, effect_pct=rule["pct"] if met else 0.0,
                                    effect_inr=value * rule["pct"] / 100 if met else 0.0, unearned_inr=0.0 if met else -value * rule["pct"] / 100))
        for ln in lines:
            ln["net_inr_pc"] = ln["base_inr_pc"] * (1 + ln["pct"] / 100)
        value = sum(x["pieces"] * x["net_inr_pc"] for x in lines)
        order_pct = 0.0
        for rule in (r for r in rules[v] if r["kind"] in ("order_value_uplift", "order_value_discount")):
            if rule["kind"] == "order_value_uplift":
                met = value >= rule["lo"] and (rule["hi"] is None or value < rule["hi"])
                cond = f"awarded value in [{rule['lo']:,.0f}, {'inf' if rule['hi'] is None else format(rule['hi'], ',.0f')})"
            else:
                met, cond = value > rule["lo"], f"awarded value > {rule['lo']:,.0f}"
            effect = value * rule["pct"] / 100
            if rule["kind"] == "order_value_uplift":
                order_pct += rule["pct"] if met else 0.0
                adjustments.append(dict(vendor=v, rule_id=rule["rule_id"], kind=rule["kind"], line=None, condition=cond, actual=value, applied=met,
                                        effect_pct=rule["pct"] if met else 0.0, effect_inr=effect if met else 0.0,
                                        unearned_inr=effect if met and rule["pct"] > 0 else 0.0))
            else:
                order_pct += rule["pct"] if met else 0.0
                adjustments.append(dict(vendor=v, rule_id=rule["rule_id"], kind=rule["kind"], line=None, condition=cond, actual=value, applied=met,
                                        effect_pct=rule["pct"] if met else 0.0, effect_inr=effect if met else 0.0, unearned_inr=0.0 if met else -effect))
        per_vendor[v] = dict(lines=lines, value_before_order_rules=value, order_value_effect_pct=order_pct, net_value=value * (1 + order_pct / 100))
    return dict(vendors=per_vendor, adjustments=adjustments, excluded=excluded,
                net_total=sum(x["net_value"] for x in per_vendor.values()),
                earned_discount_inr=-sum(a["effect_inr"] for a in adjustments if a["effect_inr"] < 0),
                unearned_inr=sum(a["unearned_inr"] for a in adjustments))


def summary(con):
    return con.execute(
        "SELECT s.vendor_id, p.freight_terms, p.freight_status, p.comparability, p.landed_comparability, COUNT(*) FROM norm_prices p "
        "JOIN submissions s USING(submission_id) GROUP BY 1,2,3,4,5 ORDER BY 1,4").fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "procurement.db"))
    ap.add_argument("--freight-estimate-pct", type=float, default=None,
                    help="buyer's freight estimate as %% of order value; leave unset and landed cost stays unknown")
    ap.add_argument("--freight-vendor", action="append", default=[], metavar="V1=7",
                    help="per-vendor freight estimate, %% of order value; beats the global estimate, loses to a vendor-stated figure (repeatable)")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    over = tuple((k, float(v)) for k, v in (x.split("=") for x in a.freight_vendor))
    out = normalize_all(con, Assumptions(freight_estimate_pct=a.freight_estimate_pct, freight_by_vendor=over))
    print(f"normalized {len(out)} unit-price fields; freight estimate: {a.freight_estimate_pct if a.freight_estimate_pct is not None else 'UNSET (landed cost unknown)'}"
          + (f"; vendor overrides {dict(over)}" if over else ""))
    print("  vendor  freight terms  freight status  price comparability            landed comparability           rows")
    for v, ft, fs, pc, lc, n in summary(con):
        print(f"  {v:<7} {ft or '-':<14} {fs:<15} {pc or 'no price':<30} {lc or '-':<30} {n}")


if __name__ == "__main__":
    main()
