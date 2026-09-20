#!/usr/bin/env python3
"""Grade questionnaire_answers and attachments in procurement.db against truth. Truth is read here only, never by src/.
Run: python scripts/grade_questionnaire.py [path/to/procurement.db]

Per answer, 'wrong' = state, gate_status, or (questions 1-5) stance or expiry date differs from truth; 'flagged' = the pipeline left the answer in any state other
than extracted (needs_review, missing, claimed_unsupported): it asked the buyer for something. Also grades attachments (kind, holder's vendor, valid-to) and the gate outcome."""
import collections
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATES = ("G1", "G2", "G3")


def grade(pdb_path=ROOT / "procurement.db", tdb_path=ROOT / "dataset" / "truth" / "truth.sqlite"):
    pdb, tdb = sqlite3.connect(pdb_path), sqlite3.connect(tdb_path)
    cols = "vendor_id,q_no,state,gate_status,stance,expiry_date,evidence_source,reason_code"
    got = {(v, q): r for v, q, *r in pdb.execute(f"SELECT {cols} FROM questionnaire_answers")}
    vendors = sorted({v for v, _ in got})
    exp = {(v, q): r for v, q, *r in tdb.execute(f"SELECT {cols} FROM questionnaire_answers WHERE vendor_id IN ({','.join('?' * len(vendors))})", vendors)}
    out = {"vendors": {}, "diffs": collections.defaultdict(list)}
    total = collections.Counter()
    for v in vendors:
        cells = collections.Counter()
        for (vv, q), (es, eg, est, eex, eev, _) in sorted(exp.items()):
            if vv != v:
                continue
            gs, gg, gst, gex, gev, _ = got.get((v, q), (None,) * 6)
            diffs = [n for n, a, b in (("state", gs, es), ("gate_status", gg, eg)) if a != b]
            if q <= 5:
                diffs += [n for n, a, b in (("stance", gst, est), ("expiry", gex, eex)) if a != b]
            if diffs:
                out["diffs"][v].append((q, diffs, (gs, gg, gst, gex), (es, eg, est, eex)))
            cells[("flagged" if gs != "extracted" else "not flagged", "wrong" if diffs else "right")] += 1
        out["vendors"][v] = cells
        total += cells
    out["overall"] = total
    got_g = {v: {r[0]: r[1] for r in pdb.execute("SELECT gate_code, gate_status FROM questionnaire_answers WHERE vendor_id=? AND is_gate=1", (v,))} for v in vendors}
    exp_g = {v: {r[0]: r[1] for r in tdb.execute("SELECT gate_code, gate_status FROM questionnaire_answers WHERE vendor_id=? AND is_gate=1", (v,))} for v in vendors}
    out["gates"] = {v: (got_g[v], exp_g[v]) for v in vendors}
    out["gate_pass"] = ({v for v in vendors if all(got_g[v].get(g) == "pass" for g in GATES)}, {v for v in vendors if all(exp_g[v].get(g) == "pass" for g in GATES)})
    att_cols = "vendor_id,kind,valid_to,expired_at_eval"
    tk = {(v, k): (vt, ex) for v, k, vt, ex in tdb.execute(f"SELECT {att_cols} FROM attachments")}
    gk = {(v, k): (vt, ex) for v, k, vt, ex in pdb.execute(f"SELECT {att_cols} FROM attachments")}
    out["attachments"] = dict(expected=len([k for k in tk if k[0] in vendors]), got=len(gk), missing=sorted(k for k in tk if k[0] in vendors and k not in gk),
                              extra=sorted(k for k in gk if k not in tk), wrong=[(k, gk[k], tk[k]) for k in gk if k in tk and gk[k] != tk[k]])
    return out


def table(c):
    f, n = "flagged", "not flagged"
    return (f"                 actually wrong | actually right\n  flagged        {c[(f, 'wrong')]:>13} | {c[(f, 'right')]:>14}\n"
            f"  not flagged    {c[(n, 'wrong')]:>13} | {c[(n, 'right')]:>14}")


if __name__ == "__main__":
    r = grade(*(sys.argv[1:2] or [ROOT / "procurement.db"]))
    for v, c in r["vendors"].items():
        print(f"\n=== {v}: {sum(c.values())} answers")
        print(table(c))
        for d in r["diffs"].get(v, []):
            print("   DIFF q", d[0], d[1], "got", d[2], "expected", d[3])
        print("   gates got/expected:", r["gates"][v])
    print("\n=== OVERALL")
    print(table(r["overall"]))
    print(f"  answers {sum(r['overall'].values())}; wrong {r['overall'][('flagged','wrong')] + r['overall'][('not flagged','wrong')]}; silent wrong {r['overall'][('not flagged','wrong')]}")
    print("gate clearance got:", sorted(r["gate_pass"][0]), "expected:", sorted(r["gate_pass"][1]))
    print("attachments:", {k: v for k, v in r["attachments"].items()})
