#!/usr/bin/env python3
"""Grade norm_prices in procurement.db against truth (norm_inr_pc, comparability) and check V3's spec-adjusted prices against
what V3's own pricing formula gives at the specified GSM. Truth is read here only, never by src/. Run: python scripts/grade_normalization.py"""
import collections
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_dataset as gd  # noqa: E402

pdb, tdb = sqlite3.connect(ROOT / "procurement.db"), sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
got = {(s, n): (i, c, a, bw) for s, n, i, c, a, bw in pdb.execute(
    "SELECT submission_id,rfx_line_no,inr_per_piece,comparability,adjusted_inr_per_piece,board_weight_ratio FROM norm_prices")}
exp = {(s, n): (i, c) for s, n, i, c in tdb.execute(
    "SELECT submission_id,rfx_line_no,norm_inr_pc,comparability FROM bid_fields WHERE field_name='unit_price' AND submission_id IN (%s)" %
    ",".join(f"'{s}'" for s in {k[0] for k in got}))}
worst, comp_bad, price_bad, per_vendor = 0.0, [], [], collections.defaultdict(lambda: [0, 0, 0.0])
for k, (ei, ec) in sorted(exp.items()):
    gi, gc = got[k][0], got[k][1]
    pv = per_vendor[k[0]]
    pv[0] += 1
    if (gi is None) != (ei is None) or (gi is not None and abs(gi - ei) > 1e-3):
        price_bad.append((k, gi, ei))
    elif gi is not None:
        pv[2] = max(pv[2], abs(gi - ei))
    if gc != ec:
        comp_bad.append((k, gc, ec))
        pv[1] += 1
print(f"fields compared: {len(exp)}   price differences: {len(price_bad)} {price_bad[:4]}   comparability differences: {len(comp_bad)} {comp_bad[:4]}")
for s, (n, cb, mx) in sorted(per_vendor.items()):
    print(f"  {s}: {n} fields, comparability diffs {cb}, max |INR/pc diff| {mx:.6f}")

# V3 spec variance: adjusted price vs V3's own formula at the specified GSM
lines = gd.build_lines()
rows = pdb.execute("SELECT rfx_line_no, stated_value, inr_per_piece, board_weight_ratio, adjusted_inr_per_piece, declared_liner_gsm FROM norm_prices "
                   "WHERE submission_id='S3' AND adjusted_inr_per_piece IS NOT NULL ORDER BY rfx_line_no").fetchall()
v1 = dict(pdb.execute("SELECT rfx_line_no, inr_per_piece FROM norm_prices WHERE submission_id='S1'"))
v2 = dict(pdb.execute("SELECT rfx_line_no, inr_per_piece FROM norm_prices WHERE submission_id='S2'"))
print(f"\nV3 spec-variance lines: {len(rows)}   (line: quoted -> adjusted vs V3's own price at spec [ratio]; V1, V2 for scale)")
errs, vs = [], []
for n, stated, inr, ratio, adj, decl in rows:
    at_spec = round(gd.raw("V3", lines[n]), 2)
    errs.append(adj / at_spec - 1)
    vs.append(adj / ((v1[n] + v2[n]) / 2) - 1)
    if n in (1, 5, 17, 30):
        print(f"  line {n:>2}: {stated:.2f} ({decl} GSM) -> adjusted {adj:.2f}  vs V3 at spec {at_spec:.2f}  ratio {ratio:.4f} | V1 {v1[n]:.2f} V2 {v2[n]:.2f}")
print(f"  adjusted vs V3's own price at spec: mean error {100*sum(errs)/len(errs):+.2f}%, worst {100*max(errs, key=abs):+.2f}%")
print(f"  stated price vs mean(V1,V2):   mean {100*sum(s/((v1[n]+v2[n])/2)-1 for n,s,*_ in rows)/len(rows):+.1f}%   |   adjusted vs mean(V1,V2): mean {100*sum(vs)/len(vs):+.1f}%")
