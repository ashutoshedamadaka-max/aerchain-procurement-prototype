#!/usr/bin/env python3
"""Run the pipeline's cross-vendor plausibility check over procurement.db and report where it fires.
Uses the pipeline's own peers query and constants; reads procurement.db only (no truth). Run: python scripts/report_cross_vendor.py"""
import json
import pathlib
import sqlite3
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.extract import store  # noqa: E402
from src.extract.verify import OUTLIER_HIGH, OUTLIER_LOW, PEERS_MIN, PER_PIECE  # noqa: E402

con = sqlite3.connect(ROOT / "procurement.db")
subs = con.execute("SELECT submission_id,vendor_id,file_name FROM submissions ORDER BY 1").fetchall()
mismatch = {(s, n) for s, n in con.execute("SELECT submission_id,rfx_line_no FROM bid_fields WHERE field_name='declared_liner_gsm' AND reason_code='spec_mismatch_gsm'")}
rows, fired, skipped = [], [], {"not per-piece INR": 0, "fewer than 2 peers": 0}
for sid, vid, fname in subs:
    peers = store.peers(con, fname)
    for line, value, basis, cur, state in con.execute(
            "SELECT rfx_line_no,value,basis,currency,state FROM bid_fields WHERE submission_id=? AND field_name='unit_price' AND value IS NOT NULL", (sid,)):
        if basis not in PER_PIECE or cur != "INR":
            skipped["not per-piece INR"] += 1
            continue
        others = peers.get(line, [])
        if len(others) < PEERS_MIN:
            skipped["fewer than 2 peers"] += 1
            continue
        med, pc = statistics.median(others), value * PER_PIECE[basis]
        r = dict(vendor=vid, line=line, price=round(pc, 2), peer_median=round(med, 2), ratio=round(pc / med, 3), peers=len(others),
                 v3_gsm_mismatch=(sid, line) in mismatch, fires=not OUTLIER_LOW * med <= pc <= OUTLIER_HIGH * med)
        rows.append(r)
        if r["fires"]:
            fired.append(r)
v3 = [r for r in rows if r["vendor"] == "V3"]
mis, ok = [r for r in v3 if r["v3_gsm_mismatch"]], [r for r in v3 if not r["v3_gsm_mismatch"]]
avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
out = dict(thresholds=[OUTLIER_LOW, OUTLIER_HIGH], min_peers=PEERS_MIN, checked=len(rows), fired=fired, skipped=skipped,
           by_vendor={v: dict(checked=sum(r["vendor"] == v for r in rows), fired=sum(r["vendor"] == v and r["fires"] for r in rows),
                              mean_ratio=avg([r["ratio"] for r in rows if r["vendor"] == v])) for v in sorted({r["vendor"] for r in rows})},
           v3_gsm_mismatch=dict(checked=len(mis), fired=sum(r["fires"] for r in mis), mean_ratio=avg([r["ratio"] for r in mis]),
                                min_ratio=min((r["ratio"] for r in mis), default=None), max_ratio=max((r["ratio"] for r in mis), default=None)),
           v3_spec_compliant=dict(checked=len(ok), fired=sum(r["fires"] for r in ok), mean_ratio=avg([r["ratio"] for r in ok])),
           v3_mismatch_lines=sorted((r["line"], r["ratio"]) for r in mis))
(ROOT / "dataset" / "eval" / "cross_vendor_check.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "v3_mismatch_lines"}, indent=1))
print("V3 mismatch lines (line, price / median of other vendors):", out["v3_mismatch_lines"])
