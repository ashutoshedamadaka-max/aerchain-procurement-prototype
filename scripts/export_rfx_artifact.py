#!/usr/bin/env python3
"""Export the RFx as the pipeline sees it: spec columns only, no prices, no should-cost. Run: python scripts/export_rfx_artifact.py"""
import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from dataset_text import EVAL_DATE, EVIDENCE_KIND, QUESTIONS  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
cols = ["line_no", "sku", "style", "length_mm", "width_mm", "height_mm", "ply", "flute", "liner_gsm", "gsm_stack", "bf", "print_spec", "annual_qty", "uom"]
lines = [dict(zip(cols, r)) for r in con.execute(f"SELECT {','.join(cols)} FROM rfx_lines ORDER BY line_no")]
vendors = [{"vendor_id": v, "name": n} for v, n in con.execute("SELECT vendor_id,name FROM vendors ORDER BY vendor_id")]
questions = [{"q_no": n, "gate_code": g, "is_gate": bool(g), "text": t, "evidence_kind": EVIDENCE_KIND.get(n)} for n, g, t in QUESTIONS]
(ROOT / "dataset" / "artifacts" / "rfx.json").write_text(json.dumps(
    {"rfx_id": "RFX-2026-PKG-0412", "evaluation_date": EVAL_DATE, "currency": "INR", "invited_vendors": vendors, "lines": lines, "questions": questions}, indent=1))
print(f"rfx.json: {len(lines)} lines, {len(vendors)} invited vendors, {len(questions)} questions ({sum(q['is_gate'] for q in questions)} mandatory gates)")
