#!/usr/bin/env python3
"""Collect the existing graders' output into one file for the app's Evals page: dataset/eval/eval_snapshot.json.
Nothing here is a new evaluation. It runs the existing graders and the test suite once, records what they say, and copies the V4 vision calibration.
The app only reads this file; it never runs a grader.  Run: python scripts/snapshot_evals.py   (takes about a minute: it runs the test suite)"""
import collections
import datetime as dt
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / "dataset" / "eval" / "eval_snapshot.json"
DB = ROOT / "procurement.db"
DOC_SUBMISSIONS = ["S1", "S2", "S3", "S5"]           # the four document formats; V4 is a photo and has its own section
CELL = {("flagged", "wrong"): "flagged_wrong", ("flagged", "right"): "flagged_right", ("not flagged", "wrong"): "silent_wrong", ("not flagged", "right"): "not_flagged_right"}


def cells(counter):
    return {name: counter[key] for key, name in CELL.items()}


def extraction():
    text = subprocess.run([sys.executable, str(ROOT / "scripts" / "grade_extraction.py"), *DOC_SUBMISSIONS], capture_output=True, text=True, encoding="utf-8", cwd=ROOT).stdout
    (ROOT / "dataset" / "eval" / "grade_extraction.txt").write_text(text, encoding="utf-8")
    vendors, current = {}, None
    for line in text.splitlines():
        if m := re.match(r"=== (V\d) (.+?)\s+\((S\d)\): (\d+) expected fields", line):
            current = m.group(1)
            vendors[current] = dict(name=m.group(2), submission=m.group(3), expected=int(m.group(4)))
        elif m := re.match(r"\s+(flagged|not flagged)\s+(\d+) \|\s+(\d+)", line):
            if current:
                vendors[current][m.group(1) == "flagged" and "flagged" or "not_flagged"] = (int(m.group(2)), int(m.group(3)))
        elif line.startswith("=== OVERALL"):
            current = None
    rows = {}
    for v, d in vendors.items():
        rows[v] = dict(name=d["name"], flagged_wrong=d["flagged"][0], flagged_right=d["flagged"][1], silent_wrong=d["not_flagged"][0], not_flagged_right=d["not_flagged"][1])
    overall = {k: sum(r[k] for r in rows.values()) for k in ("flagged_wrong", "flagged_right", "silent_wrong", "not_flagged_right")}
    return dict(vendors=rows, overall=overall, fields=sum(overall.values()))


def photo():
    r = json.loads((ROOT / "dataset" / "eval" / "v4_vision_report.json").read_text(encoding="utf-8"))
    cal = r["calibration"]
    flagged, wrong = set(cal["flagged"]), set(cal["wrong_in_any_pass"])
    rows = r["pass2"]["rows_returned"]
    return dict(rows=rows, pass1={k: r["pass1"][k] for k in ("mapped", "mapped_correctly", "prices_transcribed", "prices_exactly_right")},
                pass2={k: r["pass2"][k] for k in ("mapped", "mapped_correctly", "prices_transcribed", "prices_exactly_right")},
                flagged=sorted(flagged), expected_uncertain=cal["expected_uncertain"], disagree=cal["disagree"], wrong_in_any_pass=sorted(wrong),
                cells=dict(flagged_wrong=len(flagged & wrong), flagged_right=len(flagged - wrong), silent_wrong=len(wrong - flagged), not_flagged_right=rows - len(flagged | wrong)))


def questionnaire():
    import grade_questionnaire as gq
    r = gq.grade(DB)
    vendors = {v: cells(c) for v, c in r["vendors"].items()}
    diffs = []
    for v, items in r["diffs"].items():
        for q, fields, got, exp in items:
            diffs.append(dict(vendor=v, q_no=q, fields=fields, got_state=got[0], expected_state=exp[0], silent=got[0] == "extracted"))
    by_q = collections.defaultdict(lambda: dict(answers=0, wrong=0, silent=0, vendors=[]))
    tdb = sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
    for (q,) in tdb.execute("SELECT q_no FROM questionnaire_answers WHERE vendor_id IN (%s)" % ",".join("?" * len(vendors)), list(vendors)):
        by_q[q]["answers"] += 1
    for d in diffs:
        by_q[d["q_no"]]["wrong"] += 1
        by_q[d["q_no"]]["silent"] += d["silent"]
        by_q[d["q_no"]]["vendors"].append(d["vendor"])
    gates = {v: dict(got=g[0], expected=g[1]) for v, g in r["gates"].items()}
    return dict(vendors=vendors, overall=cells(r["overall"]), answers=sum(r["overall"].values()), diffs=diffs, by_question={str(q): d for q, d in sorted(by_q.items())},
                gates=gates, gate_clearance=dict(got=sorted(r["gate_pass"][0]), expected=sorted(r["gate_pass"][1])),
                attachments={k: (v if not isinstance(v, list) else len(v)) for k, v in r["attachments"].items()})


def award():
    from src import award as A
    from src.analyst import Store
    gates = Store(DB).gates or []
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ctx = A.load(con, "ex_freight", "overbuy")
    runs = [("single_vendor", "Cheapest single vendor, gated", A.single_vendor(ctx, None, gates)), ("cheapest_per_line", "Cheapest per line, gates ignored", A.cheapest_per_line(ctx)),
            ("gated_split", "Gated split", A.gated_split(ctx, gates)), ("max_vendors", "At most 2 vendors, gated", A.max_vendors(ctx, 2, gates)),
            ("max_share", "No vendor above 70%, gated", A.max_share(ctx, 0.7, gates))]
    out = []
    for key, label, r in runs:
        s = r.get("saving") or {}
        out.append(dict(scenario=key, label=label, vendors=r.get("vendors_used", []), naive=s.get("naive"), repriced=s.get("repriced"), pct=s.get("repriced_pct"),
                        recommendable=r.get("recommendable"), blockers=[w["code"] for w in r.get("warnings", []) if w["severity"] == "block"]))
    return dict(basis="ex_freight", gates=gates, baseline=(runs[2][2].get("saving") or {}).get("vs"), scenarios=out)


def tests():
    with tempfile.TemporaryDirectory() as tmp:
        xml = pathlib.Path(tmp) / "junit.xml"
        subprocess.run([sys.executable, "-m", "pytest", "tests", "app", "-q", f"--junitxml={xml}"], capture_output=True, cwd=ROOT)
        cases = ET.parse(xml).getroot().iter("testcase")
        status, counts = {}, collections.Counter()
        for c in cases:
            s = "failed" if c.find("failure") is not None or c.find("error") is not None else "skipped" if c.find("skipped") is not None else "passed"
            status[f"{c.get('classname')}::{c.get('name')}"] = s
            counts[s] += 1
    seeded = re.search(r"expected = \{(.*?)\}", (ROOT / "tests" / "test_sanity_report.py").read_text(encoding="utf-8"), re.S)
    pick = lambda frag: {k.split("::")[1]: v for k, v in status.items() if frag in k}
    return dict(passed=counts["passed"], skipped=counts["skipped"], failed=counts["failed"], gates=pick("test_gates"), sanity=pick("test_sanity_report"),
                seeded_faults=len(re.findall(r'"[A-Z]\d+"', seeded.group(1))) if seeded else None)


def sanity():
    text = (ROOT / "SANITY.md").read_text(encoding="utf-8")
    m = re.search(r"\*\*(\d+) things? flagged\*\*", text)
    return dict(flagged=int(m.group(1)) if m else None, generated=(re.search(r"as of ([0-9-]+ [0-9:]+)", text) or [None, None])[1])


if __name__ == "__main__":
    snap = dict(generated_at=dt.datetime.now().isoformat(timespec="seconds"), extraction=extraction(), photo=photo(), questionnaire=questionnaire(), award=award(),
                sanity=sanity(), tests=tests())
    OUT.write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: extraction {snap['extraction']['fields']} fields, questionnaire {snap['questionnaire']['answers']} answers, "
          f"tests {snap['tests']['passed']} passed / {snap['tests']['skipped']} skipped / {snap['tests']['failed']} failed, sanity {snap['sanity']['flagged']} flagged")
