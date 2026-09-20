#!/usr/bin/env python3
"""Ask the analyst layer a fixed set of questions against procurement.db and save every answer (prose, table, refusal fields, tool calls).
Run: python scripts/run_analyst_questions.py [refusal|canonical|all]"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import metering  # noqa: E402
from src.analyst import Analyst, Store  # noqa: E402

metering.configure(ROOT / "procurement.db")

GATES = ["V1", "V2", "V5"]      # supplied by the buyer for the session; questionnaire extraction is not built
REFUSAL = [
    ("R1 unanswerable", "What will kraft paper cost in June?", None),
    ("R2 gates missing", "Split it cheapest per line, only among vendors who cleared the quality questionnaire.", None),
    ("R3 unasked estimate", "V5 said 'same as last year' on the 7-ply lines. What is V5's price on line 27?", None),
]
CANONICAL = [
    ("C1 VP question", "Split it cheapest per line, only among vendors who cleared the quality questionnaire.", GATES),
    ("C2 spec variance", "Why is V3 cheaper on the small boxes?", GATES),
    ("C3 least sure", "What are you least sure about?", GATES),
]
which = (sys.argv[1] if len(sys.argv) > 1 else "all")
runs = (REFUSAL if which in ("refusal", "all") else []) + (CANONICAL if which in ("canonical", "all") else [])
out = []
for label, q, gates in runs:
    t = time.time()
    with metering.stage("analyst:" + label.split()[0]):
        a = Analyst(Store(gates=gates)).ask(q)
    dt = time.time() - t
    print(f"\n{'=' * 100}\n{label}: {q}   [{dt:.0f}s, {len(a.tool_calls)} tool calls, refused={a.refused}]\n{'=' * 100}")
    print(a.markdown().split("<details>")[0].rstrip())
    print("\ntool calls:", [(c["tool"], json.dumps(c["arguments"])[:160]) for c in a.tool_calls])
    out.append(dict(label=label, question=q, gates_supplied=gates, seconds=round(dt), **a.to_dict(), tool_calls=a.tool_calls))
(ROOT / "dataset" / "eval" / f"analyst_answers_{which}.json").write_text(json.dumps(out, indent=1, default=str))
