#!/usr/bin/env python3
"""Grade procurement.db against truth.sqlite. Truth is read here only, never by src/.
Run: python scripts/grade_extraction.py [S1 S2 ...]   (default: every submission in procurement.db)

Calibration 2x2, per vendor and overall, over bid_fields:
  actually wrong = value differs from truth, or a field is present on one side only
  flagged        = the pipeline left the field needs_review or missing (it asked the buyer for something)
"""
import collections
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
pdb, tdb = sqlite3.connect(ROOT / "procurement.db"), sqlite3.connect(ROOT / "dataset" / "truth" / "truth.sqlite")
sids = sys.argv[1:] or [r[0] for r in pdb.execute("SELECT submission_id FROM submissions ORDER BY 1")]
Q = "SELECT rfx_line_no,field_name,value,state,reason_code,anchor,resolving_question FROM bid_fields WHERE submission_id=?"
FLAG = ("needs_review", "missing")
COMMERCIAL = ("validity", "payment_terms", "freight", "moq", "bundle", "discount_footnote", "prior_year_reference")


def grade(sid):
    got = {(n, f): r for n, f, *r in pdb.execute(Q, (sid,))}
    exp = {(n, f): r for n, f, *r in tdb.execute(Q, (sid,))}
    cells = collections.Counter()
    detail = collections.defaultdict(list)
    for k in sorted(exp.keys() | got.keys()):
        g, e = got.get(k), exp.get(k)
        if g is None or e is None:
            wrong, flagged = True, g is not None and g[1] in FLAG
            detail["field present on one side only"].append((k, "pipeline" if g else "truth"))
        else:
            (gv, gs, gr, ga, gq), (ev, es, er, ea, eq) = g, e
            wrong = (gv is None) != (ev is None) or (gv is not None and abs(gv - ev) > 1e-6)
            flagged = gs in FLAG
            for name, a, b in (("value", gv, ev), ("state", gs, es), ("reason", gr, er), ("anchor", ga, ea)):
                if (a != b) and not (name == "value" and not wrong):
                    detail[name].append((k, a, b))
            if gs in FLAG and not gq:
                detail["question missing"].append(k)
            if es in FLAG and gs not in FLAG:
                detail["truth doubt not flagged"].append((k, gs, es))
            if gs in FLAG and es not in FLAG:
                detail["flagged but truth is settled"].append((k, gs, es))
        cells[("flagged" if flagged else "not flagged", "wrong" if wrong else "right")] += 1
    return cells, detail, len(exp), len(got)


def cond_tier(sid):
    vid = "V" + sid[1:]
    out = []
    cq = "SELECT kind,value_num FROM conditions WHERE submission_id=?"
    gc, ec = set(pdb.execute(cq, (sid,))), {c for c in tdb.execute(cq, (sid,)) if c[0] in COMMERCIAL}
    out.append(f"conditions (kind, value): missing {sorted(ec - gc, key=str)} | extra {sorted(gc - ec, key=str)}")
    tq = "SELECT kind,min_value_inr,max_value_inr,rfx_line_no,min_qty,effect_pct FROM tier_rules WHERE vendor_id=?"
    gt, et = set(pdb.execute(tq, (vid,))), set(tdb.execute(tq, (vid,)))
    out.append(f"tiers: missing {sorted(et - gt, key=str)} | extra {sorted(gt - et, key=str)}")
    sq = "SELECT received_on,validity_days,valid_until,expired_at_eval,incoterm,currency FROM submissions WHERE submission_id=?"
    g, e = pdb.execute(sq, (sid,)).fetchone(), tdb.execute(sq, (sid,)).fetchone()
    out.append("submission: " + ("match" if g == e else f"DIFF got {g} expected {e}"))
    return out


def table(c):
    f, n = ("flagged", "not flagged")
    return (f"                 actually wrong | actually right\n  flagged        {c[(f, 'wrong')]:>13} | {c[(f, 'right')]:>14}\n"
            f"  not flagged    {c[(n, 'wrong')]:>13} | {c[(n, 'right')]:>14}")


total = collections.Counter()
for sid in sids:
    vname = pdb.execute("SELECT v.vendor_id, v.name FROM submissions s JOIN vendors v USING(vendor_id) WHERE submission_id=?", (sid,)).fetchone()
    cells, detail, ne, ng = grade(sid)
    total += cells
    print(f"\n=== {vname[0]} {vname[1]}  ({sid}): {ne} expected fields, {ng} produced")
    print(table(cells))
    for name in ("value", "state", "reason", "anchor", "field present on one side only", "question missing", "truth doubt not flagged", "flagged but truth is settled"):
        items = detail.get(name, [])
        print(f"  {name}: {len(items)}" + (f"  {items[:5]}" if items else ""))
    for line in cond_tier(sid):
        print("  " + line)
print("\n=== OVERALL (" + ", ".join(sids) + ")")
print(table(total))
n = sum(total.values())
print(f"  fields {n}; wrong {total[('flagged','wrong')] + total[('not flagged','wrong')]}; of those flagged {total[('flagged','wrong')]}; "
      f"silent wrong {total[('not flagged','wrong')]}; flagged and actually right {total[('flagged','right')]}")
