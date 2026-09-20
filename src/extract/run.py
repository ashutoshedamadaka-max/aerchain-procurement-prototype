"""CLI: python -m src.extract.run <file.xlsx|.pdf|.docx|.txt> [--db procurement.db]"""
import argparse
import collections
import json
import pathlib

from .. import metering
from .pipeline import run

ROOT = pathlib.Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--db", default=str(ROOT / "procurement.db"))
    ap.add_argument("--rfx", default=str(ROOT / "dataset" / "artifacts" / "rfx.json"))
    a = ap.parse_args()
    metering.configure(a.db)
    sub, fields, log = run(a.file, a.db, a.rfx)
    out = ROOT / "dataset" / "eval" / f"{sub['vendor_id'].lower()}_extraction_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"submission": sub, **log}, indent=2))
    print(json.dumps({"submission": sub, "state_counts": collections.Counter(f.state for f in fields),
                      **{k: log[k] for k in ("passes", "pass1_failures", "rescued", "still_failing", "unmatched_rows") if k in log}}, indent=1))


if __name__ == "__main__":
    main()
