"""Offline adapter for the existing normalization grader. Never called by the Streamlit page."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "dataset/eval/normalization_snapshot.json"


def fingerprint(db):
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        rows = con.execute("SELECT * FROM norm_prices ORDER BY submission_id,rfx_line_no").fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode()).hexdigest()


def parse(output):
    summary = re.search(r"fields compared: (\d+)\s+price differences: (\d+).*?comparability differences: (\d+)", output)
    if not summary:
        raise ValueError("Normalization grader summary missing; no report generated")
    vendors = [dict(submission=s, fields=int(n), comparability_differences=int(c), max_matching_price_delta=float(d))
               for s,n,c,d in re.findall(r"(S\d+): (\d+) fields, comparability diffs (\d+), max \|INR/pc diff\| ([\d.]+)", output)]
    return dict(fields=int(summary[1]), price_differences=int(summary[2]), comparability_differences=int(summary[3]), vendors=vendors, grader_output=output)


def difference_details(db, truth):
    """Classify disagreements offline; never expose reference truth to the app."""
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        got = {(s,n): (p,c) for s,n,p,c in con.execute("SELECT submission_id,rfx_line_no,inr_per_piece,comparability FROM norm_prices")}
    with closing(sqlite3.connect(Path(truth).resolve().as_uri() + "?mode=ro", uri=True)) as con:
        expected = con.execute("SELECT submission_id,rfx_line_no,norm_inr_pc,comparability FROM bid_fields WHERE field_name='unit_price'").fetchall()
    submissions = {s for s,n in got}
    details = []
    for s,n,p,c in expected:
        if s not in submissions:
            continue
        actual, status = got.get((s,n), (None,None))
        price_diff = (actual is None) != (p is None) or (actual is not None and p is not None and abs(actual-p) > .001)
        comp_diff = status != c
        if price_diff or comp_diff:
            details.append(dict(submission=s, line=n, price_difference=price_diff, comparability_difference=comp_diff,
                                missing_price=actual is None and p is not None))
    return details


def generate():
    db = ROOT / "procurement.db"
    before = fingerprint(db)
    # Inherit the explicit dependency paths of the calling Python runtime.
    code = "import sys,runpy;sys.path[:0]=" + repr(sys.path) + ";runpy.run_path('scripts/grade_normalization.py',run_name='__main__')"
    import os
    run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                         env={**os.environ, "PYTHONIOENCODING":"utf-8"}, check=True)
    report = parse(run.stdout)
    report["differences"] = difference_details(db, ROOT / "dataset/truth/truth.sqlite")
    if fingerprint(db) != before:
        raise RuntimeError("Normalization changed during grading; retry against stable data")
    report.update(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), norm_prices_sha256=before,
                  source="scripts/grade_normalization.py", tolerance_inr_per_piece=0.001)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = generate()
    print(f"Recorded {result['fields']} normalization checks; {result['price_differences']} price differences")
