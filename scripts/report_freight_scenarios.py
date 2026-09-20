#!/usr/bin/env python3
"""Rerun the award scenarios ex-freight and landed with per-vendor freight overrides, on a scratch copy (procurement.db is not modified).
Gate results are supplied (V1, V2, V5). Run: python scripts/report_freight_scenarios.py [V1=7 V2=4 V5=5]"""
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import award  # noqa: E402
from src.normalize import Assumptions, normalize_all  # noqa: E402

GATES = ["V1", "V2", "V5"]
over = tuple((k, float(v)) for k, v in (x.split("=") for x in (sys.argv[1:] or ["V1=7", "V2=4", "V5=5"])))
tmp = pathlib.Path(tempfile.mkdtemp()) / "scratch.db"
shutil.copy(ROOT / "procurement.db", tmp)
con = sqlite3.connect(tmp)
normalize_all(con, Assumptions(freight_by_vendor=over))

print("overrides as stored assumptions (impact per 1%):")
for aid, val, blob, note in con.execute("SELECT assumption_id, value, value_text, note FROM assumptions WHERE assumption_id LIKE 'freight_estimate_pct:%' ORDER BY 1"):
    i = json.loads(blob)
    print(f"  {aid:<24} {val:g}%   applies to Rs {i['applies_to_value_inr']:>13,.0f}   +1 percentage point = Rs {i['impact_per_1_percentage_point_inr']:>9,.0f}   "
          f"1% relative change = Rs {i['impact_per_1pct_relative_change_inr']:>7,.0f}")

ex, ld = award.load(con, "ex_freight"), award.load(con, "landed")
rows = {}
for name, fn in (("single_vendor", lambda c: award.single_vendor(c, gates=GATES)), ("gated_split", lambda c: award.gated_split(c, GATES)),
                 ("max_vendors(2)", lambda c: award.max_vendors(c, 2, GATES)), ("max_share(0.6)", lambda c: award.max_share(c, 0.6, GATES))):
    rows[name] = (fn(ex), fn(ld))
print("\nstrategy          basis        vendors            naive          re-priced   saving vs baseline (re-priced)")
for name, (a, b) in rows.items():
    for label, r in (("ex-freight", a), ("landed", b)):
        s = r["saving"]
        base = s["vs"].split("(")[1].split(")")[0] if s else None
        text = f"Rs {s['repriced']:>9,.0f} ({s['repriced_pct']:.2f}%) vs {base}" if s else "-"
        print(f"{name:<17} {label:<12} {'+'.join(r['vendors_used']):<14} {r['naive_total']:>14,.0f} {r['repriced_total']:>14,.0f}   {text}")

print("\nsingle-vendor ranking (re-priced total, gated pool):")
for label, r in (("ex-freight", rows["single_vendor"][0]), ("landed", rows["single_vendor"][1])):
    print(f"  {label:<11}", "  ".join(f"{x['vendor']} Rs {x['repriced_total']:,.0f}" for x in r["alternatives"]))

g0, g1 = rows["gated_split"][0]["allocation"], rows["gated_split"][1]["allocation"]
moved = {n: (g0[n], g1[n]) for n in g0 if g0[n] != g1.get(n)}
print(f"\ngated cheapest-per-line: {len(moved)} of {len(g0)} lines change vendor when freight is applied: {moved if moved else 'none'}")
for n, (a, b) in moved.items():
    pa, pb = ex.prices, ld.prices
    print(f"  line {n}: ex-freight {a} {pa[(a, n)]['rank']:.2f} vs {b} {pa[(b, n)]['rank']:.2f}   |   landed {a} {pb[(a, n)]['rank']:.2f} vs {b} {pb[(b, n)]['rank']:.2f}")
print("vendors by lines: ex-freight", {v: sum(x == v for x in g0.values()) for v in GATES}, " landed", {v: sum(x == v for x in g1.values()) for v in GATES})

u = award.cheapest_per_line(ld)
print(f"\nungated cheapest_per_line on the landed basis covers {u['coverage']['covered']}/30 lines using {u['vendors_used']}; V3 has no freight estimate, so its lines are not landed-comparable.")
