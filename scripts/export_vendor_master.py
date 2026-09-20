#!/usr/bin/env python3
"""Export the buyer's vendor-master record: which mandatory gates the buyer already holds on file for an incumbent whose response carried no questionnaire.
Truth says V5's gate evidence is 'on file in buyer vendor master, not attached'; this renders that fact as a buyer-side input. No certificate numbers or expiry
dates are invented: the master records that evidence is on file, not what it says. Run: python scripts/export_vendor_master.py"""
import collections
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
gates = collections.defaultdict(list)
for vendor, code in con.execute("SELECT vendor_id, gate_code FROM questionnaire_answers WHERE is_gate=1 AND truth_reason LIKE '%on file in buyer vendor master%' ORDER BY vendor_id, q_no"):
    gates[vendor].append(code)
master = {"source": "Buyer vendor master, FY25 approved-vendor file",
          "note": "Records that gate evidence is on file. Certificate numbers and expiry dates are not recorded here; the buyer must confirm they are current.",
          "vendors": {v: {"status": "approved incumbent", "gates_on_file": g, "certificate_expiry_recorded": False} for v, g in gates.items()}}
(ROOT / "dataset" / "artifacts" / "vendor_master.json").write_text(json.dumps(master, indent=1))
print("vendor_master.json:", {v: x["gates_on_file"] for v, x in master["vendors"].items()})
