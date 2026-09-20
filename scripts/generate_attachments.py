#!/usr/bin/env python3
"""Render the certificate attachments V1, V2 and V3 sent with their responses as small text-layer PDFs (dataset/artifacts/attachments/).
Holder, issuer and expiry come from truth's attachments table; reference numbers and scope text are set here. V4's attachment is not rendered (V4 is not ingested).
Dates are written in five different formats on purpose. Run: python scripts/generate_attachments.py"""
import pathlib
import sqlite3

import fitz

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "dataset" / "artifacts" / "attachments"
# id -> (title, reference, scope, date label, date text)
DOCS = {
    "A1": ("BRCGS Global Standard Packaging Materials", "BRC-PM-2291", "Grade AA. Manufacture of corrugated fibreboard cartons", "Expiry date", "30 November 2026"),
    "A2": ("FSC Chain of Custody Certificate", "FSC-C104417", "Mix Credit. Corrugated cartons", "Certificate valid to", "31/01/2027"),
    "A3": ("Calibration Certificate - Box Compression Tester", "CAL-0126-V1", "50 kN compression tester, in-house laboratory", "Next calibration due", "15 Dec 2026"),
    "A4": ("ISO 9001:2015 Certificate of Registration", "QMS-V1-4471", "Design and manufacture of corrugated packaging", "Valid until", "30-Jun-2027"),
    "A5": ("IS 2771 Test Report - Corrugated Fibreboard Boxes", "TR-IS2771-V1", "Board grade as quoted", "Report valid to", "31 December 2026"),
    "A6": ("BRCGS Global Standard Packaging Materials", "BRC-PM-3310", "Grade A. Manufacture of corrugated fibreboard cartons", "Expiry date", "28 February 2027"),
    "A7": ("FSC Chain of Custody Certificate", "FSC-C118820", "Mix Credit. Corrugated cartons", "Certificate valid to", "30/09/2026"),
    "A8": ("Calibration Certificate - Box Compression Tester", "CAL-1025-V2", "Compression tester, in-house laboratory", "Next calibration due", "10 Oct 2026"),
    "A9": ("ISO 9001:2015 Certificate of Registration", "QMS-V3-0088", "Manufacture of corrugated boxes", "Valid until", "14-Sep-2025"),
    "A10": ("Compression Test Report", "SRTH-2026-114", "Compression testing carried out at Shri Ram Test House on behalf of the holder", None, None),
}


def render(path, holder, issuer, doc):
    title, ref, scope, label, date = doc
    lines = [(title, 15), (f"Certificate holder: {holder}", 11), (f"Reference: {ref}", 11), (f"Issued by: {issuer}", 11), (f"Scope: {scope}", 11)]
    if label:
        lines.append((f"{label}: {date}", 11))
    d = fitz.open()
    page = d.new_page()
    y = 80
    for text, size in lines:
        page.insert_text((72, y), text, fontsize=size)
        y += size + 14
    d.save(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
    for aid, fname, issuer, entity in con.execute("SELECT attachment_id, file_name, issuer, entity_name FROM attachments"):
        if aid in DOCS:
            render(OUT / fname, entity, issuer, DOCS[aid])
    print(f"wrote {len(list(OUT.glob('*.pdf')))} attachments to {OUT}")


if __name__ == "__main__":
    main()
