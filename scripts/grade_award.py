#!/usr/bin/env python3
"""Check the award engine against an independent computation from truth: normalized prices, comparability, MOQs and gate results
come from truth.sqlite; the discount rules come from the generator's own constants. Truth is read here only, never by src/.
Run: python scripts/grade_award.py"""
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import generate_dataset as gd  # noqa: E402
from src import award  # noqa: E402

tdb = sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
gates = [v for (v,) in tdb.execute("SELECT vendor_id FROM vendors WHERE gates_passed_truth=1")]
moq = dict(tdb.execute("SELECT vendor_id, moq_pcs FROM vendors"))
qty = dict(tdb.execute("SELECT line_no, annual_qty FROM rfx_lines"))
T = {(v, n): (p, c) for v, n, p, c in tdb.execute(
    "SELECT s.vendor_id, b.rfx_line_no, b.norm_inr_pc, b.comparability FROM bid_fields b JOIN submissions s USING(submission_id) WHERE b.field_name='unit_price'")}
ok = lambda v, n: T.get((v, n), (None, None))[0] is not None and T[(v, n)][1] != "not_comparable" and v != "V4"   # V4 is not in the pipeline database


def independent(alloc):
    """Re-price an allocation {line: vendor} from truth prices with the generator's rules: V1 slab, V2 threshold discount, MOQ overbuy."""
    total = 0.0
    for v in set(alloc.values()):
        ns = [n for n, a in alloc.items() if a == v]
        value = sum(qty[n] * T[(v, n)][0] for n in ns)
        pct = gd.v1_uplift(value) if v == "V1" else (-2.0 if v == "V2" and value > gd.V2_DISCOUNT_MIN else 0.0)
        over = sum(max(0, moq[v] - qty[n]) * T[(v, n)][0] for n in ns) if moq.get(v) else 0
        total += (value + over) * (1 + pct / 100)
    return total


ctx = award.load(sqlite3.connect(ROOT / "procurement.db"))
g = award.gated_split(ctx, gates)
alloc = {n: min((v for v in gates if ok(v, n)), key=lambda v: (T[(v, n)][0], v)) for n in qty if any(ok(v, n) for v in gates)}
print(f"gates from truth: {gates}")
print(f"gated allocation identical to an independent cheapest-per-line over truth prices: {g['allocation'] == alloc}")
ind = independent(alloc)
print(f"gated_split re-priced: engine Rs {g['repriced_total']:,.2f}  independent Rs {ind:,.2f}  difference Rs {g['repriced_total'] - ind:,.4f}")
base = {v: independent({n: v for n in qty}) for v in gates if all(ok(v, n) for n in qty)}
b = min(base, key=base.get)
print(f"baseline: engine {g['saving']['vs']}  | independent best single vendor {b} Rs {base[b]:,.2f}  (engine Rs {g['saving']['baseline_repriced']:,.2f})")
s = award.single_vendor(ctx, gates=gates)
print(f"single_vendor picks {s['vendors_used']}; truth outcome true_cheapest_landed = ", end="")
import json  # noqa: E402
print(json.load(open(ROOT / "dataset" / "truth" / "outcomes.json"))["true_cheapest_landed"])
ug, gt = award.cheapest_per_line(ctx)["allocation"], g["allocation"]
removed = [n for n in qty if ug.get(n) and ug[n] not in gates]
print(f"lines where the gates remove the cheapest eligible vendor: {len(removed)} (designed outcome: at least 6) -> {removed}")
print(f"  by vendor in the ungated view: " + str({v: sum(1 for x in ug.values() if x == v) for v in sorted(set(ug.values()))}))
print(f"naive vs re-priced gated saving against the baseline: Rs {g['saving']['naive']:,.0f} -> Rs {g['saving']['repriced']:,.0f} "
      f"({100 * (1 - g['saving']['repriced'] / g['saving']['naive']):.0f}% of the naive saving lost); as % of baseline {g['saving']['repriced_pct']:.2f}%")
