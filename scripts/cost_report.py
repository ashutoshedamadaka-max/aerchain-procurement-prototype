#!/usr/bin/env python3
"""Cost of one full run, from model_calls in procurement.db, as markdown. Run: python scripts/cost_report.py [db] [--out COST_REPORT.md]
Every model call is metered (src/metering.py). Cost = tokens x the PRICES below; input_tokens includes cached tokens, which are billed at the cached rate."""
import argparse
import collections
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# USD per 1M tokens, standard processing, from https://developers.openai.com/api/docs/pricing as of 2026-09-20. A SNAPSHOT: prices change; update it before quoting a cost.
# gpt-5.5 also has a long-context tier ($10 / $1 / $45) above 272K input tokens; no call in this project comes near that, so it is not modelled.
PRICES = {
    "gpt-5.5": dict(input=5.00, cached=0.50, output=30.00),
    "gpt-5.4-mini": dict(input=0.75, cached=0.075, output=4.50),
}
FX_INR = 88.50      # the event's stamped USD/INR rate (RBI reference 2026-03-11), so costs read in the same currency as the award
LINES = 30
STAGES = ("extraction", "questionnaire", "analyst", "photo")


def cost(model, inp, out, cached):
    if model not in PRICES:
        sys.exit(f"no price for model {model!r} in PRICES; add it (with today's date) before reporting cost")
    p = PRICES[model]
    return ((inp - cached) * p["input"] + cached * p["cached"] + out * p["output"]) / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default=str(ROOT / "procurement.db"))
    ap.add_argument("--out")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    rows = con.execute("SELECT stage, model, input_tokens, output_tokens, cached_input_tokens, elapsed_s FROM model_calls").fetchall()
    if not rows:
        sys.exit("model_calls is empty: run the pipeline first")
    fam = lambda s: s.split(":")[0]
    agg = collections.defaultdict(lambda: dict(calls=0, inp=0, out=0, usd=0.0, sec=0.0))
    for stage, model, i, o, c, sec in rows:
        for key in ((fam(stage),), (fam(stage), stage), ("__models__", model), ("__total__",)):
            x = agg[key]
            x["calls"] += 1; x["inp"] += i; x["out"] += o; x["usd"] += cost(model, i, o, c); x["sec"] += sec
    total = agg[("__total__",)]
    fmt = lambda x, label: f"| {label} | {x['calls']} | {x['inp']:,} | {x['out']:,} | ${x['usd']:.3f} | Rs {x['usd'] * FX_INR:,.1f} | {x['sec']:.0f}s | {x['sec'] / x['calls']:.1f}s |"
    head = "| {} | Calls | Input tokens | Output tokens | Cost (USD) | Cost (INR) | Total latency | Mean latency / call |\n|---|---:|---:|---:|---:|---:|---:|---:|"
    out = ["# Cost of one full run", "", f"Source: `model_calls` in `{pathlib.Path(a.db).name}`, {len(rows)} calls. Prices are a snapshot as of 2026-09-20 (see PRICES in scripts/cost_report.py); INR at {FX_INR} per USD.", "",
           "## By stage", "", head.format("Stage")]
    out += [fmt(agg[(s,)], s) for s in STAGES if (s,) in agg] + [fmt(agg[k], k[0]) for k in agg if len(k) == 1 and k[0] not in STAGES + ("__total__", "__models__")]
    out += [fmt(total, "**Total for the event**"), "", "## By handler within a stage", "", head.format("Stage / handler")]
    out += [fmt(agg[k], k[1]) for k in sorted(k for k in agg if len(k) == 2 and k[0] != "__models__")]
    out += ["", "## By model", "", head.format("Model")] + [fmt(agg[k], k[1]) for k in sorted(k for k in agg if k[0] == "__models__")]
    out += ["", "## Per line item", "", f"| Measure | Value |\n|---|---:|", f"| Event total | ${total['usd']:.3f} (Rs {total['usd'] * FX_INR:,.1f}) |",
            f"| Per line item (total / {LINES}) | **${total['usd'] / LINES:.4f}** (Rs {total['usd'] * FX_INR / LINES:,.2f}) |"]
    out += [f"| {s} per line item | ${agg[(s,)]['usd'] / LINES:.4f} |" for s in STAGES if (s,) in agg]
    text = "\n".join(out) + "\n"
    print(text)
    if a.out:
        pathlib.Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
