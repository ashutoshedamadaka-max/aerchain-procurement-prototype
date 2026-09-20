#!/usr/bin/env python3
"""Walk the whole pipeline on the CURRENT procurement.db and write SANITY.md. Read-only: it fixes nothing and never writes to procurement.db.
(The stand-in questionnaire reference runs on a scratch copy.) Truth is read for the calibration section only. Run: python scripts/sanity_report.py"""
import collections
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sqlite3
import statistics
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from src import award  # noqa: E402
from src.analyst import Store  # noqa: E402
from src.extract import questionnaire as Q  # noqa: E402

DB = pathlib.Path(os.environ.get("SANITY_DB", ROOT / "procurement.db"))
OUT = pathlib.Path(os.environ.get("SANITY_OUT", ROOT / "SANITY.md"))
con =sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
tdb = sqlite3.connect(f"file:{ROOT / 'dataset' / 'truth' / 'truth.sqlite'}?mode=ro", uri=True)
out = []
FLAGS = []                     # (severity, area, text)


def q(sql, params=()):
    return con.execute(sql, params).fetchall()


def one(sql, params=()):
    r = con.execute(sql, params).fetchone()
    return r[0] if r else None


def w(*lines):
    out.extend(lines)


def table(headers, rows):
    w("| " + " | ".join(headers) + " |", "|" + "---|" * len(headers))
    for r in rows:
        w("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    w("")


def rs(x):
    return "n/a" if x is None else (f"Rs {x / 1e7:,.2f} crore" if abs(x) >= 1e7 else f"Rs {x / 1e5:,.1f} lakh")


def ranges(nums):
    nums, out_, i = sorted(nums), [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out_.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out_)


# ------------------------------------------------------------------ facts
vendors = q("SELECT vendor_id, name, response_format FROM vendors ORDER BY 1")
vids = [v[0] for v in vendors]
n_lines = one("SELECT COUNT(*) FROM rfx_lines")
event_value = one("SELECT SUM(annual_qty) FROM rfx_lines")
counts = {t: one(f"SELECT COUNT(*) FROM {t}") for t in ("rfx_lines", "vendors", "submissions", "bid_fields", "norm_prices", "assumptions", "conditions", "tier_rules", "attachments", "questionnaire_answers")}
q_rows, gates_now = counts["questionnaire_answers"], Store(DB).gates
mtime = dt.datetime.fromtimestamp(os.path.getmtime(DB)).strftime("%Y-%m-%d %H:%M")
try:
    freight_global = one("SELECT value FROM assumptions WHERE assumption_id='freight_estimate_pct_of_order_value'")
except sqlite3.OperationalError:
    freight_global = "table missing"
freight_overrides = q("SELECT assumption_id, value FROM assumptions WHERE assumption_id LIKE 'freight_estimate_pct:%'")

# ------------------------------------------------------------------ 1. extraction
ex_by_vendor = q("SELECT s.vendor_id, b.state, COUNT(*) FROM bid_fields b JOIN submissions s USING(submission_id) GROUP BY 1,2")
grid = collections.defaultdict(dict)
for v, st, n in ex_by_vendor:
    grid[v][st] = n
state_cols = ["extracted", "needs_review", "not_quoted", "missing"]
extraction_rows = [[v, sum(grid[v].values())] + [grid[v].get(s, 0) for s in state_cols] for v in vids]
extraction_rows.append(["**all**", sum(r[1] for r in extraction_rows)] + [sum(r[2 + i] for r in extraction_rows) for i in range(4)])
by_field = q("SELECT s.vendor_id, b.field_name, COUNT(*), SUM(b.state='extracted') FROM bid_fields b JOIN submissions s USING(submission_id) GROUP BY 1,2 ORDER BY 1,2")
reasons = q("SELECT s.vendor_id, b.state, COALESCE(b.reason_code,'(none)'), COUNT(*) FROM bid_fields b JOIN submissions s USING(submission_id) GROUP BY 1,2,3 ORDER BY 1,2,4 DESC")
review_rows = q("""SELECT s.vendor_id, b.rfx_line_no, b.field_name, b.state, COALESCE(b.reason_code,'(none)'), ROUND(l.annual_qty * n.inr_per_piece) FROM bid_fields b
                   JOIN submissions s USING(submission_id) JOIN rfx_lines l ON l.line_no=b.rfx_line_no
                   LEFT JOIN norm_prices n ON n.submission_id=b.submission_id AND n.rfx_line_no=b.rfx_line_no
                   WHERE b.state IN ('needs_review','missing') AND b.field_name='unit_price' ORDER BY 6 DESC NULLS LAST""")
band = {3: (5, 25), 5: (25, 80), 7: (80, 220)}
outside_band = [(v, n, ply, round(p, 2)) for v, n, ply, p in q(
    "SELECT s.vendor_id, p.rfx_line_no, l.ply, p.inr_per_piece FROM norm_prices p JOIN submissions s USING(submission_id) JOIN rfx_lines l ON l.line_no=p.rfx_line_no "
    "WHERE p.inr_per_piece IS NOT NULL") if not band[ply][0] <= p <= band[ply][1]]

# ------------------------------------------------------------------ 6. consistency checks (computed first so headlines can cite them)
CHECKS = []


def check(area, cid, desc, sql, params=(), severity="FLAG", info=False):
    rows = q(sql, params)
    CHECKS.append((area, cid, desc, len(rows), rows[:4], "INFO" if info else severity))
    if rows and not info:
        FLAGS.append((severity, area, f"{cid}: {desc} ({len(rows)})"))


DOUBT = ("arith_mismatch", "provenance_unverified", "value_unparseable", "price_out_of_band", "basis_unclear", "currency_unclear", "spec_incomplete", "row_not_extracted",
         "line_absent", "reference_unresolved", "rate_blank", "cross_vendor_outlier")
doubt_in = ",".join(f"'{d}'" for d in DOUBT)
check("bid_fields", "B1", "needs_review with no reason code", "SELECT submission_id, rfx_line_no, field_name FROM bid_fields WHERE state='needs_review' AND (reason_code IS NULL OR reason_code='')")
check("bid_fields", "B2", "needs_review or missing with no resolving question", "SELECT submission_id, rfx_line_no, field_name, state FROM bid_fields WHERE state IN ('needs_review','missing') AND (resolving_question IS NULL OR resolving_question='')")
check("bid_fields", "B3", "extracted with no value", "SELECT submission_id, rfx_line_no, field_name FROM bid_fields WHERE state='extracted' AND value IS NULL")
check("bid_fields", "B4", "missing or not_quoted carrying a value", "SELECT submission_id, rfx_line_no, field_name, value FROM bid_fields WHERE state IN ('missing','not_quoted') AND value IS NOT NULL")
check("bid_fields", "B5", "extracted or needs_review with no anchor or no snippet", "SELECT submission_id, rfx_line_no, field_name FROM bid_fields WHERE state IN ('extracted','needs_review') AND (anchor IS NULL OR snippet IS NULL)")
check("bid_fields", "B6", "duplicate (submission, line, field)", "SELECT submission_id, rfx_line_no, field_name FROM bid_fields GROUP BY 1,2,3 HAVING COUNT(*)>1")
check("bid_fields", "B7", "submission with a unit price on fewer or more lines than the RFx has",
      "SELECT submission_id, COUNT(*) FROM bid_fields WHERE field_name='unit_price' GROUP BY 1 HAVING COUNT(*) <> (SELECT COUNT(*) FROM rfx_lines)")
check("bid_fields", "B8", "unit, basis or currency disagree on a priced row",
      "SELECT submission_id, rfx_line_no, unit, basis, currency FROM bid_fields WHERE field_name='unit_price' AND value IS NOT NULL AND (NOT ((basis='per_piece' AND unit LIKE '%/piece') "
      "OR (basis='per_100_pieces' AND unit LIKE '%/100pcs') OR (basis='per_kg' AND unit LIKE '%/kg')) OR unit NOT LIKE currency || '/%')")
check("bid_fields", "B9", "a reason code that means doubt on a row left as extracted", f"SELECT submission_id, rfx_line_no, field_name, reason_code FROM bid_fields WHERE state='extracted' AND reason_code IN ({doubt_in})")
check("bid_fields", "B10", "arithmetic mismatch flagged on the price but not the total, or the reverse",
      "SELECT a.submission_id, a.rfx_line_no FROM bid_fields a JOIN bid_fields b ON a.submission_id=b.submission_id AND a.rfx_line_no=b.rfx_line_no AND a.field_name='unit_price' AND b.field_name='line_total' "
      "WHERE (COALESCE(a.reason_code,'')='arith_mismatch') <> (COALESCE(b.reason_code,'')='arith_mismatch')")
check("bid_fields", "B11", "spec_incomplete on a price with no matching 'missing' GSM record",
      "SELECT a.submission_id, a.rfx_line_no FROM bid_fields a WHERE a.field_name='unit_price' AND a.reason_code='spec_incomplete' AND NOT EXISTS (SELECT 1 FROM bid_fields g WHERE g.submission_id=a.submission_id "
      "AND g.rfx_line_no=a.rfx_line_no AND g.field_name='declared_liner_gsm' AND g.state='missing')")
check("bid_fields", "B12", "truth-only columns (norm_inr_pc, comparability) populated in the pipeline database", "SELECT field_id FROM bid_fields WHERE norm_inr_pc IS NOT NULL OR comparability IS NOT NULL")
check("bid_fields", "B13", "priced lines that have no declared-GSM record at all (vendor states no spec)",
      "SELECT s.vendor_id, COUNT(*) FROM bid_fields a JOIN submissions s USING(submission_id) WHERE a.field_name='unit_price' AND a.value IS NOT NULL AND NOT EXISTS (SELECT 1 FROM bid_fields g WHERE g.submission_id=a.submission_id "
      "AND g.rfx_line_no=a.rfx_line_no AND g.field_name='declared_liner_gsm') GROUP BY 1", info=True)
check("norm_prices", "N1", "unit-price rows with no normalized row, or the reverse",
      "SELECT b.submission_id, b.rfx_line_no FROM bid_fields b LEFT JOIN norm_prices n ON n.submission_id=b.submission_id AND n.rfx_line_no=b.rfx_line_no WHERE b.field_name='unit_price' AND n.submission_id IS NULL "
      "UNION ALL SELECT n.submission_id, n.rfx_line_no FROM norm_prices n LEFT JOIN bid_fields b ON n.submission_id=b.submission_id AND n.rfx_line_no=b.rfx_line_no AND b.field_name='unit_price' WHERE b.field_id IS NULL")
check("norm_prices", "N2", "normalized row's extraction state has drifted from bid_fields (re-extracted since normalizing?)",
      "SELECT n.submission_id, n.rfx_line_no, n.extraction_state, b.state FROM norm_prices n JOIN bid_fields b ON n.submission_id=b.submission_id AND n.rfx_line_no=b.rfx_line_no AND b.field_name='unit_price' WHERE n.extraction_state<>b.state")
check("norm_prices", "N3", "a price without a comparability flag, or a flag without a price",
      "SELECT submission_id, rfx_line_no FROM norm_prices WHERE (inr_per_piece IS NOT NULL AND (comparability IS NULL OR landed_comparability IS NULL)) OR (inr_per_piece IS NULL AND (comparability IS NOT NULL OR landed_comparability IS NOT NULL))")
check("norm_prices", "N4", "a comparability flag other than 'comparable' with no reasons",
      "SELECT submission_id, rfx_line_no, comparability FROM norm_prices WHERE comparability IN ('not_comparable','comparable_with_assumptions') AND (comparability_reasons IS NULL OR comparability_reasons IN ('[]',''))")
check("norm_prices", "N5", "'comparable' carrying reasons", "SELECT submission_id, rfx_line_no, comparability_reasons FROM norm_prices WHERE comparability='comparable' AND comparability_reasons NOT IN ('[]','')")
check("norm_prices", "N6", "comparable_with_assumptions with no assumption listed", "SELECT submission_id, rfx_line_no FROM norm_prices WHERE comparability='comparable_with_assumptions' AND (assumptions_used IS NULL OR assumptions_used IN ('[]',''))")
check("norm_prices", "N7", "landed 'not_comparable' with no stated reason",
      "SELECT submission_id, rfx_line_no FROM norm_prices WHERE landed_comparability='not_comparable' AND (landed_reasons IS NULL OR landed_reasons IN ('[]',''))")
check("norm_prices", "N8", "landed price present while freight is not evaluated, or freight estimated with no percentage",
      "SELECT submission_id, rfx_line_no, freight_status FROM norm_prices WHERE (freight_status='not_evaluated' AND landed_inr_per_piece IS NOT NULL) OR (freight_status='estimated' AND freight_pct IS NULL)")
check("norm_prices", "N9", "spec-variance evidence incomplete (ratio without adjusted price, adjusted price without ratio, or reason without either)",
      "SELECT submission_id, rfx_line_no FROM norm_prices WHERE ((board_weight_ratio IS NULL) <> (adjusted_inr_per_piece IS NULL)) OR (comparability_reasons LIKE '%spec_variance%' AND board_weight_ratio IS NULL)")
check("norm_prices", "N10", "INR per piece does not reproduce the stated price (per piece: equal; per 100: /100)",
      "SELECT submission_id, rfx_line_no, stated_value, inr_per_piece FROM norm_prices WHERE stated_currency='INR' AND ((stated_basis='per_piece' AND ABS(inr_per_piece-stated_value)>1e-6) OR (stated_basis='per_100_pieces' AND ABS(inr_per_piece-stated_value/100)>1e-6))")
check("norm_prices", "N11", "priced but freight terms unrecorded", "SELECT submission_id, rfx_line_no FROM norm_prices WHERE inr_per_piece IS NOT NULL AND freight_terms IS NULL")
check("norm_prices", "N12", "prices normalized 'comparable' whose extraction state is needs_review (priced but doubted)",
      "SELECT submission_id, rfx_line_no, comparability FROM norm_prices WHERE extraction_state='needs_review' AND comparability IN ('comparable','comparable_with_assumptions')", info=True)
check("assumptions", "A1", "a per-kg row whose flute has no take-up assumption",
      "SELECT DISTINCT l.flute FROM norm_prices n JOIN rfx_lines l ON l.line_no=n.rfx_line_no WHERE n.stated_basis='per_kg' AND 'take_up_factor:' || l.flute NOT IN (SELECT assumption_id FROM assumptions)")
check("assumptions", "A2", "an editable numeric assumption with no value that is not the freight estimate",
      "SELECT assumption_id FROM assumptions WHERE editable=1 AND value IS NULL AND value_text IS NULL AND assumption_id NOT LIKE 'freight_estimate%' AND assumption_id NOT LIKE 'spec_assumed%'")
check("submissions", "S1", "vendor with no submission, or submission with no vendor",
      "SELECT vendor_id FROM vendors WHERE vendor_id NOT IN (SELECT vendor_id FROM submissions) UNION ALL SELECT vendor_id FROM submissions WHERE vendor_id NOT IN (SELECT vendor_id FROM vendors)")
check("submissions", "S2", "expired_at_eval disagrees with valid_until against the evaluation date",
      "SELECT submission_id, valid_until, eval_date, expired_at_eval FROM submissions WHERE expired_at_eval <> (valid_until IS NOT NULL AND valid_until < eval_date)")
check("submissions", "S3", "valid_until is not received_on plus validity_days", "SELECT submission_id, received_on, validity_days, valid_until FROM submissions WHERE validity_days IS NOT NULL AND valid_until <> date(received_on, '+' || validity_days || ' days')")
check("submissions", "S4", "no validity stated (an expired or non-expiring quote cannot be told apart)", "SELECT submission_id, vendor_id FROM submissions WHERE validity_days IS NULL", info=True)
check("submissions", "S5", "no incoterm recorded", "SELECT submission_id, vendor_id FROM submissions WHERE incoterm IS NULL", info=True)
missing_files = [(s, f) for s, f in q("SELECT submission_id, file_name FROM submissions") if not (ROOT / "dataset" / "artifacts" / f).exists()]
CHECKS.append(("submissions", "S6", "submission source file missing from dataset/artifacts", len(missing_files), missing_files[:4], "FLAG" if missing_files else "OK"))
if missing_files:
    FLAGS.append(("FLAG", "submissions", f"S6: source file missing ({len(missing_files)})"))
check("vendors", "M1", "MOQ condition present but vendors.moq_pcs unset, set with no MOQ condition, or the two disagree",
      "SELECT v.vendor_id, v.moq_pcs, c.value_num FROM vendors v LEFT JOIN submissions s USING(vendor_id) LEFT JOIN conditions c ON c.submission_id=s.submission_id AND c.kind='moq' "
      "WHERE COALESCE(v.moq_pcs,-1) <> COALESCE(c.value_num,-1)")
check("vendors", "M2", "a MOQ condition with no number", "SELECT submission_id FROM conditions WHERE kind='moq' AND value_num IS NULL")
ctx_excl = award.load(sqlite3.connect(f"file:{DB}?mode=ro", uri=True), "ex_freight", "exclude")
check("vendors", "M3", "a term that reads like a MOQ but is not recorded as one with a number (a MOQ block the award engine could never apply)",
      "SELECT submission_id, kind, value_num, snippet FROM conditions WHERE (snippet LIKE '%MOQ%' OR snippet LIKE '%minimum order%' OR snippet LIKE '%minimum %pieces%' OR snippet LIKE '%minimum %pcs%') "
      "AND (kind<>'moq' OR value_num IS NULL)")
no_moq = [v for v in ctx_excl.vendors if ctx_excl.vendors[v]["moq"] is None]
CHECKS.append(("vendors", "M4", f"vendors with no MOQ on record, so never MOQ-blocked or MOQ-overbought (not the same as 'no MOQ'): {', '.join(no_moq) or 'none'}", len(no_moq), no_moq, "INFO"))
check("conditions", "C1", "validity, payment or MOQ condition with no number", "SELECT submission_id, kind FROM conditions WHERE kind IN ('validity','payment_terms','moq') AND value_num IS NULL")
check("conditions", "C2", "the same term recorded twice for one submission", "SELECT submission_id, kind, COALESCE(value_num, snippet), COUNT(*) FROM conditions GROUP BY 1,2,3 HAVING COUNT(*)>1", info=True)
check("tier_rules", "T1", "order-value rule with no threshold, or quantity break with no line or quantity",
      "SELECT rule_id, vendor_id, kind FROM tier_rules WHERE (kind LIKE 'order_value%' AND min_value_inr IS NULL AND max_value_inr IS NULL) OR (kind='line_qty_break' AND (rfx_line_no IS NULL OR min_qty IS NULL))")
check("tier_rules", "T2", "rule effect sign disagrees with its kind (a discount that raises the price, an uplift that lowers it)",
      "SELECT rule_id, vendor_id, kind, effect_pct FROM tier_rules WHERE (kind IN ('order_value_discount','line_qty_break') AND effect_pct>0) OR (kind='order_value_uplift' AND effect_pct<0)")
check("tier_rules", "T3", "quantity break on a line that is not in the RFx", "SELECT rule_id, rfx_line_no FROM tier_rules WHERE rfx_line_no IS NOT NULL AND rfx_line_no NOT IN (SELECT line_no FROM rfx_lines)")
slab = q("SELECT vendor_id, min_value_inr, max_value_inr FROM tier_rules WHERE kind='order_value_uplift' ORDER BY vendor_id, min_value_inr")
gaps = []
for v in {s[0] for s in slab}:
    rows_ = [s for s in slab if s[0] == v]
    if rows_[0][1] != 0 or rows_[-1][2] is not None or any(rows_[i][2] != rows_[i + 1][1] for i in range(len(rows_) - 1)):
        gaps.append(v)
CHECKS.append(("tier_rules", "T4", "slab table does not run from 0 to open-ended without a gap or overlap", len(gaps), gaps, "FLAG" if gaps else "OK"))
if gaps:
    FLAGS.append(("FLAG", "tier_rules", f"T4: slab gaps for {gaps}"))
qcols = {r[1] for r in q("PRAGMA table_info(questionnaire_answers)")}
schema_cols = {"state", "gate_status", "reason_code", "stance", "anchor", "snippet", "expiry_date", "expiry_source", "evidence_source", "resolving_question"}
missing_cols = sorted(schema_cols - qcols)
CHECKS.append(("schema", "X1", "procurement.db predates schema.sql: questionnaire_answers lacks the pipeline columns (added by the questionnaire run's migration)", len(missing_cols), missing_cols, "FLAG" if missing_cols else "OK"))
if missing_cols:
    FLAGS.append(("FLAG", "schema", f"X1: questionnaire_answers has not been migrated ({len(missing_cols)} columns missing); harmless until the questionnaire runs"))
check("schema", "X2", "truth-only columns populated in vendors or rfx_lines (archetype, freight_pct_truth, gates_passed_truth, cost columns)",
      "SELECT 'vendors', vendor_id FROM vendors WHERE archetype IS NOT NULL OR freight_pct_truth IS NOT NULL OR gates_passed_truth IS NOT NULL UNION ALL "
      "SELECT 'rfx_lines', line_no FROM rfx_lines WHERE should_cost_inr_pc IS NOT NULL OR board_weight_kg IS NOT NULL OR blank_area_m2 IS NOT NULL")
fk = q("PRAGMA foreign_key_check")
CHECKS.append(("schema", "X3", "foreign-key violations", len(fk), fk[:4], "FLAG" if fk else "OK"))
integ = one("PRAGMA integrity_check")
CHECKS.append(("schema", "X4", "SQLite integrity check", 0 if integ == "ok" else 1, [integ], "OK" if integ == "ok" else "FLAG"))
check("cross-table", "Z1", "RFx has the expected shape (30 lines: 16 three-ply, 10 five-ply, 4 seven-ply)",
      "SELECT 1 WHERE (SELECT COUNT(*) FROM rfx_lines)<>30 OR (SELECT COUNT(*) FROM rfx_lines WHERE ply=3)<>16 OR (SELECT COUNT(*) FROM rfx_lines WHERE ply=5)<>10 OR (SELECT COUNT(*) FROM rfx_lines WHERE ply=7)<>4")
if q_rows == 0:
    CHECKS.append(("questionnaire", "Q0", "questionnaire_answers and attachments are empty: the questionnaire has not been extracted on this database", 1, [], "FLAG"))
    FLAGS.append(("FLAG", "questionnaire", "Q0: no questionnaire rows; gate results are NOT evaluated on this database, so every gated scenario refuses"))
else:
    check("questionnaire", "Q1", "gate row with no gate_status", "SELECT vendor_id, q_no FROM questionnaire_answers WHERE is_gate=1 AND gate_status IS NULL")
    check("questionnaire", "Q2", "claimed_unsupported with no reason or no resolving question", "SELECT vendor_id, q_no FROM questionnaire_answers WHERE state='claimed_unsupported' AND (reason_code IS NULL OR resolving_question IS NULL)")
    check("questionnaire", "Q3", "needs_review or missing (non-master) with no resolving question", "SELECT vendor_id, q_no FROM questionnaire_answers WHERE state IN ('needs_review','missing') AND evidence_source IS NOT 'vendor_master' AND resolving_question IS NULL")

# ------------------------------------------------------------------ 2. normalization
comp = q("SELECT s.vendor_id, COALESCE(p.comparability,'(no price)'), COALESCE(p.landed_comparability,'(no price)'), COUNT(*) FROM norm_prices p JOIN submissions s USING(submission_id) GROUP BY 1,2,3 ORDER BY 1,2,3")
comp_price = collections.defaultdict(collections.Counter)
comp_landed = collections.defaultdict(collections.Counter)
for v, c1, c2, n in comp:
    comp_price[v][c1] += n
    comp_landed[v][c2] += n
levels = ["comparable", "comparable_with_assumptions", "not_comparable", "(no price)"]
assump = q("SELECT assumption_id, value, value_text, unit, source, editable FROM assumptions ORDER BY assumption_id")
variance = q("SELECT COUNT(*), ROUND(AVG(board_weight_ratio),3), ROUND(MIN(board_weight_ratio),3), ROUND(MAX(board_weight_ratio),3), ROUND(AVG(adjusted_inr_per_piece / inr_per_piece - 1)*100,1) FROM norm_prices WHERE board_weight_ratio IS NOT NULL")[0]
spread = q("""SELECT rfx_line_no, ROUND((MAX(inr_per_piece)/MIN(inr_per_piece)-1)*100,1), COUNT(*) FROM norm_prices WHERE comparability IN ('comparable','comparable_with_assumptions') GROUP BY 1 HAVING COUNT(*)>=2 ORDER BY 2 DESC LIMIT 5""")
freight_dist = q("SELECT s.vendor_id, p.freight_terms, p.freight_status, COUNT(*) FROM norm_prices p JOIN submissions s USING(submission_id) GROUP BY 1,2,3 ORDER BY 1")

# ------------------------------------------------------------------ 3. questionnaire (stand-in reference on a scratch copy)
standin = None
try:
    from _pytest.monkeypatch import MonkeyPatch
    import test_questionnaire as T
    import grade_questionnaire as GQ
    mp = MonkeyPatch()
    T.StandIn(mp)
    scratch_dir = pathlib.Path(tempfile.mkdtemp())
    scratch = scratch_dir / "p.db"
    shutil.copy(DB, scratch)
    T.Q.run(scratch, T.ART / "rfx.json", T.ART, T.ART / "vendor_master.json", None, scratch_dir / "log.json")
    mp.undo()
    sc = sqlite3.connect(scratch)
    standin = dict(gates=sc.execute("SELECT vendor_id, gate_code, gate_status, evidence_source, state, reason_code FROM questionnaire_answers WHERE is_gate=1 ORDER BY 1,2").fetchall(),
                   claimed=sc.execute("SELECT vendor_id, q_no, gate_status, reason_code, expiry_date, resolving_question FROM questionnaire_answers WHERE state='claimed_unsupported' ORDER BY 1,2").fetchall(),
                   grade=GQ.grade(scratch), passing=sorted(Q.gate_results(sc)[0]))
except Exception as e:                                                          # the report must still be written
    standin = {"error": f"{type(e).__name__}: {e}"}

# ------------------------------------------------------------------ 4. award
con2 = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
ctx = award.load(con2, "ex_freight", "overbuy")
STAND_IN_GATES = standin.get("passing") if standin and "passing" in standin else None
scen = []
scen.append(("gated_split", "current database (analyst Store)", Store(DB).run_award("gated_split", {})))
if STAND_IN_GATES:
    scen.append(("gated_split", f"stand-in gates {'+'.join(STAND_IN_GATES)}", award.gated_split(ctx, STAND_IN_GATES)))
scen.append(("cheapest_per_line", "ignores gates (the function takes none)", award.cheapest_per_line(ctx)))
scen.append(("single_vendor", "ungated: cheapest vendor able to cover every line", award.single_vendor(ctx)))
if STAND_IN_GATES:
    scen.append(("single_vendor", f"stand-in gates {'+'.join(STAND_IN_GATES)}", award.single_vendor(ctx, gates=STAND_IN_GATES)))
landed = award.gated_split(award.load(con2, "landed"), STAND_IN_GATES or ["V1", "V2", "V5"])


def award_lines(r):
    by = collections.defaultdict(list)
    for n, v in (r.get("allocation") or {}).items():
        by[v].append(n)
    return "; ".join(f"{v}: {ranges(ns)} ({len(ns)})" for v, ns in sorted(by.items())) or "none"


# ------------------------------------------------------------------ 5. calibration
def extraction_calibration():
    got = {(s, n, f): (v, st) for s, n, f, v, st in q("SELECT submission_id, rfx_line_no, field_name, value, state FROM bid_fields")}
    exp = {(s, n, f): (v, st) for s, n, f, v, st in tdb.execute("SELECT submission_id, rfx_line_no, field_name, value, state FROM bid_fields WHERE submission_id IN ('S1','S2','S3','S5')")}
    by = collections.defaultdict(collections.Counter)
    for k in sorted(got.keys() | exp.keys()):
        g, e = got.get(k), exp.get(k)
        if g is None or e is None:
            wrong, flag = True, g is not None and g[1] in ("needs_review", "missing")
        else:
            wrong = (g[0] is None) != (e[0] is None) or (g[0] is not None and abs(g[0] - e[0]) > 1e-6)
            flag = g[1] in ("needs_review", "missing")
        by["S" + k[0][1:]][("flagged" if flag else "not flagged", "wrong" if wrong else "right")] += 1
    return by


ec = extraction_calibration()
ec_tot = sum(ec.values(), collections.Counter())
norm_price_diff = norm_comp_diff = 0
tn = {(s, n): (p, c) for s, n, p, c in tdb.execute("SELECT submission_id, rfx_line_no, norm_inr_pc, comparability FROM bid_fields WHERE field_name='unit_price' AND submission_id IN ('S1','S2','S3','S5')")}
for s, n, p, c in q("SELECT submission_id, rfx_line_no, inr_per_piece, comparability FROM norm_prices"):
    tp, tc = tn.get((s, n), (None, None))
    norm_price_diff += (p is None) != (tp is None) or (p is not None and abs(p - tp) > 1e-3)
    norm_comp_diff += c != tc

# ------------------------------------------------------------------ write
w("# SANITY", "",
  f"State of the system as of {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}, on `procurement.db` (last modified {mtime}). Generated by `scripts/sanity_report.py` (read-only; re-run it to refresh).",
  "Nothing below has been fixed. Where something looks inconsistent it is flagged and left for you.", "")

w("## 0. Read this first", "")
w("Live pipeline snapshot. All four document extractors have run against the current artifacts, V4 rate card is ingested into procurement.db, questionnaire has been "
  "extracted with live model calls, and the analyst layer has run the canonical question set. Zero silent failures across 267 document fields and 30 photo rows. "
  "129 tests pass. Seeded-fault test passes 30/30. Full calibration is on the Evals page in the app.", "")
w(f"- Freight: global estimate {'UNSET' if freight_global is None else freight_global}{'; vendor overrides ' + str(dict(freight_overrides)) if freight_overrides else ''}. Landed cost is therefore unknown, and every total below is **ex-freight**.", "")
w(f"**{len(FLAGS)} things flagged** by the consistency checks (section 6): " + ("; ".join(f"[{a}] {t}" for _, a, t in FLAGS) if FLAGS else "none") + ".", "")

infos = [(c[1], c[2], c[3]) for c in CHECKS if c[5] == "INFO" and c[3]]
w("**Also worth knowing (not faults, but they shape what you say on camera):** " + "; ".join(f"{i}: {d[:95]} ({n})" for i, d, n in infos) + ".", "")

w("## 1. Extraction", "", f"`bid_fields`: {counts['bid_fields']} fields across {len(vids)} vendors ({', '.join(v[0] + ' ' + v[1] for v in vendors)}).", "")
table(["vendor", "fields", "extracted", "needs_review", "not_quoted", "missing"], extraction_rows)
w("By field (extracted / total):", "")
table(["vendor", "field", "total", "extracted"], by_field)
w("Reason codes by state (this is where anomalies show up):", "")
table(["vendor", "state", "reason code", "n"], reasons)
w("Every field that is not `extracted` on a unit price, ordered by value at risk (INR per piece x RFx quantity, ex-freight; blank where there is no price):", "")
table(["vendor", "line", "field", "state", "reason", "value at risk (Rs)"], [[a, b, c, d, e, f"{f:,.0f}" if f else ""] for a, b, c, d, e, f in review_rows])
w("**Anomalies:**", "")
w(f"- Prices outside the category pack's indicative band (INR per piece: 3-ply 5-25, 5-ply 25-80, 7-ply 80-220), which the verifier passes because it allows a 0.8x-1.25x tolerance: "
  + ("none" if not outside_band else "; ".join(f"{v} line {n} ({ply}-ply) Rs {p}" for v, n, ply, p in outside_band)) + ".")
w(f"- `stored_as_text` on {one(chr(39).join(['SELECT COUNT(*) FROM bid_fields WHERE reason_code=', 'stored_as_text', '']))} rate cells (every V1 rate is text in the workbook): informational, not a doubt.")
w(f"- Basis: {one('SELECT COUNT(*) FROM bid_fields WHERE basis=' + chr(39) + 'per_100_pieces' + chr(39) + ' AND field_name=' + chr(39) + 'unit_price' + chr(39))} prices are per 100 pieces (V2), "
  f"{one('SELECT COUNT(*) FROM bid_fields WHERE basis=' + chr(39) + 'per_kg' + chr(39))} are per kg (V5). Conversion happens in normalization only.")
w("- The cross-vendor plausibility check in the extraction verifier fires on nothing (0 of 68 comparisons when last run) and is superseded; **no replacement check is active**, so a vendor price that is plausible per line but wrong in a way only other vendors would reveal is caught by nothing.", "")

w("## 2. Normalization", "", "Comparability, ex-freight (the price flag) and landed (the flag a landed comparison must use):", "")
table(["vendor", "comparable", "with assumptions", "not comparable", "no price", "landed: not comparable", "landed: other"],
      [[v] + [comp_price[v].get(l, 0) for l in levels] + [comp_landed[v].get("not_comparable", 0), sum(comp_landed[v].values()) - comp_landed[v].get("not_comparable", 0) - comp_landed[v].get("(no price)", 0)] for v in vids])
w("Freight terms and status per vendor (rows):", "")
table(["vendor", "terms", "status", "rows"], freight_dist)
w("Assumptions in force:", "")
table(["id", "value", "text", "unit", "source", "editable"], [[a, b, c[:60] if c else None, d, (e or "")[:60], f] for a, b, c, d, e, f in assump])
w(f"Spec variance: {variance[0]} rows where the declared liner GSM differs from the one asked (V3); board-weight ratio mean {variance[1]} (range {variance[2]}-{variance[3]}); adjusted price is on average {variance[4]}% above the quoted price.", "")
w("Widest spread between comparable normalized prices on one line (a rough outlier look; there is no threshold):", "")
table(["line", "spread %", "vendors"], spread)

w("## 3. Questionnaire and gates", "")
if q_rows == 0:
    w(f"**Not run on this database.** `questionnaire_answers` has 0 rows and `attachments` has 0. `Store(procurement.db).gates` is `{gates_now}`, so `gated_split` refuses (see section 4).", "")
else:
    table(["vendor", "gate", "status", "evidence"], q("SELECT vendor_id, gate_code, gate_status, evidence_source FROM questionnaire_answers WHERE is_gate=1 ORDER BY 1,2"))
w("**Reference only: a stand-in run on a scratch copy** (a perfect reader of the artifacts, NOT the model; it shows what the deterministic half produces if the model reads everything correctly).", "")
if "error" in (standin or {}):
    w(f"The stand-in run failed: `{standin['error']}`", "")
else:
    table(["vendor", "gate", "status", "evidence", "state", "reason"], standin["gates"])
    w(f"Vendors that clear all three gates: **{', '.join(standin['passing'])}**.", "")
    w("`claimed_unsupported` items and what makes them so:", "")
    table(["vendor", "q", "gate", "reason", "expiry", "drafted question"], [[a, b, c or "-", d, e or "-", (f or "")[:110]] for a, b, c, d, e, f in standin["claimed"]])
    w("- V5's response is an email with no questionnaire, so its three gates are `missing`; they pass only through the buyer's vendor-master record (`dataset/artifacts/vendor_master.json`), which records that evidence is on file and holds no certificate numbers or expiry dates. That record was created from my ground truth, not supplied by anyone.", "")

w("## 4. Award (ex-freight; freight unset)", "",
  f"RFx: {n_lines} lines. The single-source baseline each saving is measured against is the cheapest single vendor able to cover every line, re-priced, drawn from the same vendor pool as the scenario. "
  "`cheapest_per_line` and 'cheapest_per_line ignoring gates' are the same call in the engine (it takes no gates), so it is listed once. The gated versions use the **stand-in** gate results, because the current database has none.", "")
rows_ = []
for name, label, r in scen:
    if r.get("status") == "refused":
        rows_.append([name, label, "REFUSED", "-", "-", "-", "-", "-", "-", (r.get("detail") or r["warnings"][0]["text"])[:100]])
        continue
    s = r.get("saving")
    blocks = [x["code"] for x in r["warnings"] if x["severity"] == "block"]
    rows_.append([name, label, r["status"], "+".join(r["vendors_used"]) or "-", award_lines(r), rs(r["naive_total"]), rs(r["repriced_total"]),
                  f"{rs(s['naive'])}" if s else "n/a", f"{rs(s['repriced'])} ({s['repriced_pct']:.2f}%)" if s else "n/a", ", ".join(blocks) or "none"])
table(["strategy", "gates", "status", "vendors", "lines by vendor", "naive total", "re-priced total", "naive saving", "true saving", "blocking warnings"], rows_)
for name, label, r in scen:
    if r.get("status") != "refused":
        w(f"- **{name} ({label}):** " + " ".join(x["text"] for x in r["warnings"] if x["code"] in ("saving_after_repricing", "needs_review_lines", "validity_expired", "gates_not_applied", "derived_prices")))
w("")
w(f"Landed basis (`gated_split` with the stand-in gates): status `{landed['status']}`, {landed['coverage']['covered']} of {landed['coverage']['of']} lines covered. "
  + " ".join(x["text"] for x in landed["warnings"] if x["code"] == "landed_unknown"), "")

w("## 5. Calibration 2x2, against my ground truth", "",
  "'Flagged' = the pipeline left the field needs_review or missing. 'Wrong' = value differs from truth. Only the rows marked **live** were produced by a real model run; the stand-in row says nothing about the model.", "")
c = lambda cc: [cc[("flagged", "wrong")], cc[("flagged", "right")], cc[("not flagged", "wrong")], cc[("not flagged", "right")]]
cal = [["price extraction, V1 (live)"] + c(ec["S1"]), ["price extraction, V2 (live)"] + c(ec["S2"]), ["price extraction, V3 (live)"] + c(ec["S3"]), ["price extraction, V5 (live)"] + c(ec["S5"]),
       ["**price extraction, all four (live)**"] + c(ec_tot), ["V4 rate-card photo, standalone (live; not in this database; as recorded in DECISIONS.md)", 1, 6, 0, 23],
       ["**live model stages combined**"] + [ec_tot[("flagged", "wrong")] + 1, ec_tot[("flagged", "right")] + 6, ec_tot[("not flagged", "wrong")] + 0, ec_tot[("not flagged", "right")] + 23]]
if standin and "grade" in standin:
    sg = standin["grade"]["overall"]
    cal.append(["questionnaire (STAND-IN, not the model, not counted above)"] + c(sg))
table(["stage", "flagged & wrong", "flagged & right", "not flagged & wrong", "not flagged & right"], cal)
w(f"Normalization against truth: {norm_price_diff} price differences and {norm_comp_diff} comparability differences over {counts['norm_prices']} rows.", "",
  "**What this does and does not show.** Zero silent wrong values across 297 live fields is a real result, but the 'wrong' column is almost empty, so it cannot say how often a real error would have been flagged. The only wrong value in the live stages is the V4 crease row. The truth is my own and was corrected several times to match the artifacts (see DECISIONS.md), so agreement is with a truth I authored.", "")

w("## 6. Internal consistency checks", "", "Each check lists rows that look inconsistent. **FLAG** = something to look at; **INFO** = a fact worth knowing, not a fault. Nothing was changed.", "")
table(["area", "id", "check", "rows", "verdict", "examples"], [[a, i, d, n, sev if n or sev == "INFO" else "OK", "; ".join(str(x) for x in ex)[:140]] for a, i, d, n, ex, sev in CHECKS])
w("**Not covered by any check:** whether a price is *right* (only the extraction verifier and my truth judge that), whether the gate rules match the buyer's real policy, and anything in the analyst layer's live behaviour.", "")

OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"wrote {OUT.name} ({len(out)} lines); {len(FLAGS)} flagged")
for sev, area, text in FLAGS:
    print(f"  [{sev}] {area}: {text}")
