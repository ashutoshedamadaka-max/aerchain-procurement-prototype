"""Award engine: five plain strategy functions over the normalized store, each followed by one re-pricing step.

    single_vendor  cheapest_per_line  gated_split  max_vendors  max_share

A strategy chooses which vendor takes each line using the price on the chosen basis (ex_freight, or landed once freight is known).
The re-pricing step then re-evaluates that allocation once at the allocated volume: quantity breaks, slabs and thresholds, MOQ
overbuy and freight. The result reports the naive total and the re-priced total side by side, and says what it is compared against.

Guardrails, from the design docs: no recommendation without gate results (gated strategies refuse without them; an ungated run is
labelled and never recommendable); expired validity, unresolved review lines and prices resting on assumptions are called out
in the result, not in a footnote; a line no eligible vendor prices is reported as uncovered, never estimated.
Gate results are an INPUT: questionnaire and attachment extraction is not built yet, so the caller supplies which vendors passed.
"""
import argparse
import collections
import itertools
import json
import pathlib
import sqlite3
from dataclasses import dataclass, field

from . import settings
from .normalize import evaluate_scenario

ROOT = pathlib.Path(__file__).resolve().parents[1]
REVIEW_SHARE = 0.05       # a needs_review line above this share of the awarded value blocks a recommendation
MATERIALITY_PCT = 1.0     # a saving below this percent of the baseline is called not worth extra supplier relationships
BASES = ("ex_freight", "landed")
MOQ_POLICIES = ("overbuy", "exclude")


@dataclass
class Context:
    con: sqlite3.Connection
    basis: str
    moq_policy: str
    lines: dict
    vendors: dict
    prices: dict
    eval_date: str
    _baseline: dict = field(default_factory=dict, repr=False)
    review_share: float = REVIEW_SHARE                       # the editable block threshold (settings.review_block_threshold_pct), as a fraction


def load(con, basis="ex_freight", moq_policy="overbuy"):
    assert basis in BASES and moq_policy in MOQ_POLICIES
    lines = dict(con.execute("SELECT line_no, annual_qty FROM rfx_lines ORDER BY line_no"))
    vendors = {v: dict(name=n, moq=m, valid_until=u, expired=bool(e), eval_date=d) for v, n, m, u, e, d in con.execute(
        "SELECT v.vendor_id, v.name, v.moq_pcs, s.valid_until, s.expired_at_eval, s.eval_date FROM vendors v JOIN submissions s USING(vendor_id)")}
    prices = {}
    for (v, n, inr, landed, pc, lc, state, terms, fstat, fpct, finr, pr, lr) in con.execute(
            "SELECT s.vendor_id, p.rfx_line_no, p.inr_per_piece, p.landed_inr_per_piece, p.comparability, p.landed_comparability, p.extraction_state, "
            "p.freight_terms, p.freight_status, p.freight_pct, p.freight_inr_per_piece, p.comparability_reasons, p.landed_reasons "
            "FROM norm_prices p JOIN submissions s USING(submission_id)"):
        landed_basis = basis == "landed"
        prices[(v, n)] = dict(rank=landed if landed_basis else inr, level=lc if landed_basis else pc, state=state, terms=terms, freight_status=fstat,
                              freight_pct=fpct, freight_inr=finr, derived=pc == "comparable_with_assumptions",
                              why=json.loads(lr if landed_basis else pr))
    return Context(con, basis, moq_policy, lines, vendors, prices, next(iter(vendors.values()))["eval_date"] if vendors else None,
                   review_share=settings.get(con, "review_block_threshold_pct") / 100)


def eligible(ctx, v, n):
    p = ctx.prices.get((v, n))
    if not p or p["rank"] is None or p["level"] in (None, "not_comparable"):
        return False
    moq = ctx.vendors[v]["moq"]
    return not (ctx.moq_policy == "exclude" and moq and ctx.lines[n] < moq)


def blocked_by(ctx, v, n):
    p = ctx.prices.get((v, n))
    if not p or p["rank"] is None:
        return "no price"
    if p["level"] in (None, "not_comparable"):
        return "not comparable: " + ", ".join(p["why"])
    moq = ctx.vendors[v]["moq"]
    return f"below MOQ ({moq:,})" if ctx.moq_policy == "exclude" and moq and ctx.lines[n] < moq else "eligible (not selected)"


def reprice(ctx, allocation):
    """The one re-pricing step. allocation {line: {vendor: pieces}} -> naive total and re-priced total, with every adjustment shown."""
    naive = sum(q * ctx.prices[(v, n)]["rank"] for n, a in allocation.items() for v, q in a.items())
    ev = evaluate_scenario(ctx.con, allocation)
    vendors, overbuy = {}, []
    for v, x in ev["vendors"].items():
        pct, moq, total, lines = x["order_value_effect_pct"], ctx.vendors[v]["moq"], 0.0, []
        for ln in x["lines"]:
            n, p = ln["line"], ctx.prices[(v, ln["line"])]
            final = ln["pieces"] * ln["net_inr_pc"] * (1 + pct / 100)
            extra = max(0, moq - ln["pieces"]) if moq and ctx.moq_policy == "overbuy" else 0
            over = extra * ln["net_inr_pc"] * (1 + pct / 100)
            freight = 0.0
            if ctx.basis == "landed":
                if p["freight_pct"] is not None:
                    freight = (final + over) * p["freight_pct"] / 100
                elif p["freight_inr"] is not None:
                    freight = (ln["pieces"] + extra) * p["freight_inr"]
            if extra:
                overbuy.append(dict(vendor=v, line=n, extra_pieces=extra, cost=over))
            lines.append(dict(line=n, pieces=ln["pieces"], base_price=p["rank"], net_inr_pc=ln["net_inr_pc"], overbuy_pieces=extra, overbuy_cost=over,
                              freight=freight, total=final + over + freight, extraction_state=p["state"], derived=p["derived"]))
            total += final + over + freight
        vendors[v] = dict(total=total, order_value_effect_pct=pct, lines=lines)
    return dict(naive_total=naive, repriced_total=sum(x["total"] for x in vendors.values()), vendors=vendors, adjustments=ev["adjustments"],
                earned_discount=ev["earned_discount_inr"], unearned=ev["unearned_inr"], overbuy=overbuy)


def baseline(ctx, pool=None):
    """What savings are measured against: the cheapest single vendor able to cover every line, re-priced, drawn from the SAME vendor pool as the
    scenario (gate-passing vendors for a gated scenario, everyone for an 'ignoring gates' view). Stated in every result."""
    key = tuple(sorted(pool)) if pool is not None else None
    if key not in ctx._baseline:
        best = None
        for v in sorted(ctx.vendors):
            if (pool is None or v in pool) and all(eligible(ctx, v, n) for n in ctx.lines):
                rp = reprice(ctx, {n: {v: q} for n, q in ctx.lines.items()})
                if best is None or rp["repriced_total"] < best["repriced_total"]:
                    best = dict(vendor=v, naive_total=rp["naive_total"], repriced_total=rp["repriced_total"], lines=sorted(ctx.lines))
        ctx._baseline[key] = best
    return ctx._baseline[key]


def passing(gates):
    if gates is None:
        return None
    return {v for v, ok in gates.items() if ok} if isinstance(gates, dict) else set(gates)


def warn(code, severity, text):
    return dict(code=code, severity=severity, text=text)


def build(ctx, strategy, assignment, params, gates=None, status="ok", notes=()):
    allocation = {n: {v: ctx.lines[n]} for n, v in sorted(assignment.items())}
    rp = reprice(ctx, allocation) if allocation else dict(naive_total=0.0, repriced_total=0.0, vendors={}, adjustments=[], earned_discount=0.0, unearned=0.0, overbuy=[])
    uncovered = [dict(line=n, blocked_by={v: blocked_by(ctx, v, n) for v in sorted(ctx.vendors)}) for n in ctx.lines if n not in assignment]
    used = sorted(rp["vendors"])
    total = rp["repriced_total"] or 1.0
    w = list(notes)
    if not assignment and status == "ok":
        status = "infeasible"
    if ctx.basis == "landed" and not assignment:
        unknown = sorted({v for (v, _), p in ctx.prices.items() if "freight_unknown" in p["why"]})
        if unknown:
            w.append(warn("landed_unknown", "block", f"Landed cost is unknown for {', '.join(unknown)}: freight terms are 'extra' with no figure and no buyer estimate, so no line is "
                          "comparable on a landed basis. Set freight_estimate_pct_of_order_value, obtain the vendors' figures, or run on the ex_freight basis."))
    if gates is None and strategy != "gated_split":
        w.append(warn("gates_not_applied", "block", "Mandatory gates were NOT applied. This is an 'ignoring gates' view and is not a recommendable award."))
    if ctx.basis == "ex_freight":
        open_ = sorted({v for v in used for n in assignment if assignment[n] == v and ctx.prices[(v, n)]["freight_status"] == "not_evaluated"})
        if open_:
            w.append(warn("landed_unknown", "warn", "Ex-freight totals. Landed cost is unknown: " + ", ".join(
                f"{v} ({ctx.prices[next((v, n) for n in assignment if assignment[n] == v)]['terms']})" for v in open_) +
                ". Supply a freight estimate or vendor figures to compare landed cost."))
    else:
        est = sorted({v for n, v in assignment.items() if ctx.prices[(v, n)]["freight_status"] == "estimated"})
        if est:
            w.append(warn("freight_estimated", "info", "Landed totals use the buyer's freight estimate for " + ", ".join(est) +
                          ". One uniform percentage cannot change the ranking among vendors that all quote freight as extra."))
    for v in used:
        if ctx.vendors[v]["expired"]:
            w.append(warn("validity_expired", "block", f"{v}'s quote validity ended {ctx.vendors[v]['valid_until']}, before the evaluation date {ctx.eval_date}. Reconfirm before any award."))
    review = [(v, x["line"], x["total"] / total) for v in used for x in rp["vendors"][v]["lines"] if x["extraction_state"] == "needs_review"]
    if review:
        big = [r for r in review if r[2] > ctx.review_share]
        w.append(warn("needs_review_lines", "block" if big else "warn", "Awarded lines still needs_review after two extraction passes: " +
                      ", ".join(f"line {n} ({v}, {s:.1%} of value)" for v, n, s in review) +
                      (f". Above the {ctx.review_share:.0%} block threshold: resolve before recommending." if big else f". Under the {ctx.review_share:.0%} block threshold, so the scenario still runs; see the Warnings section.")))
    caveats = needs_review_warnings(ctx, rp, total)
    derived = sum(x["total"] for v in used for x in rp["vendors"][v]["lines"] if x["derived"])
    if derived:
        w.append(warn("derived_prices", "info", f"{derived / total:.1%} of the awarded value rests on prices made comparable by stated assumptions (per kg, FX or an unstated spec)."))
    if uncovered:
        w.append(warn("uncovered_lines", "warn", f"{len(uncovered)} lines have no eligible vendor and are not in any total: " + ", ".join(str(u["line"]) for u in uncovered)))
    if rp["overbuy"]:
        w.append(warn("moq_overbuy", "info", "MOQ overbuy priced in: " + ", ".join(f"line {o['line']} {o['vendor']} +{o['extra_pieces']:,} pcs" for o in rp["overbuy"]) +
                      f" (Rs {sum(o['cost'] for o in rp['overbuy']):,.0f})."))
    if rp["unearned"] > 0:
        w.append(warn("discount_not_captured", "info", f"Rs {rp['unearned']:,.0f} of vendor discount or full-award pricing is not captured in this scenario."))
    base = baseline(ctx, gates)
    cover = base and set(assignment) == set(base["lines"])
    saving = None
    if cover:
        saving = dict(vs=f"cheapest single vendor ({base['vendor']}), re-priced, {ctx.basis.replace('_', ' ')}", baseline_naive=base["naive_total"],
                      baseline_repriced=base["repriced_total"], naive=base["naive_total"] - rp["naive_total"], repriced=base["repriced_total"] - rp["repriced_total"],
                      repriced_pct=100 * (base["repriced_total"] - rp["repriced_total"]) / base["repriced_total"])
        if len(used) > 1 and status == "ok":
            s = saving
            w.append(warn("saving_after_repricing", "info" if s["repriced_pct"] >= MATERIALITY_PCT else "warn",
                          f"Saves Rs {s['naive']:,.0f} naively but Rs {s['repriced']:,.0f} ({s['repriced_pct']:.2f}% of the baseline) after re-pricing at allocated volume, against {s['vs']}, using "
                          f"{len(used)} suppliers instead of 1." + (" That is below the " + f"{MATERIALITY_PCT:g}% materiality threshold: probably not worth the extra supplier relationships." if s["repriced_pct"] < MATERIALITY_PCT else "")))
    elif base and assignment:
        w.append(warn("baseline_not_comparable", "info", f"Covers {len(assignment)} of {len(ctx.lines)} lines, so no saving is stated against the {base['vendor']} baseline."))
    gates_applied = gates is not None
    return dict(strategy=strategy, params=params, basis=ctx.basis, moq_policy=ctx.moq_policy, status=status, gates_applied=gates_applied,
                gates_passed=sorted(gates) if gates_applied else None, vendors_used=used, coverage=dict(covered=len(assignment), of=len(ctx.lines), uncovered=uncovered),
                naive_total=rp["naive_total"], repriced_total=rp["repriced_total"], repricing_effect=rp["repriced_total"] - rp["naive_total"],
                earned_discount=rp["earned_discount"], unearned_discount=rp["unearned"], saving=saving,
                allocation={n: v for n, v in sorted(assignment.items())}, per_vendor={v: dict(total=x["total"], share=x["total"] / total, lines=len(x["lines"]),
                            order_value_effect_pct=x["order_value_effect_pct"]) for v, x in rp["vendors"].items()},
                adjustments=rp["adjustments"], warnings=w, needs_review_warnings=caveats, review_block_threshold_pct=ctx.review_share * 100,
                recommendable=gates_applied and status == "ok" and not any(x["severity"] == "block" for x in w) and bool(assignment))


def needs_review_warnings(ctx, rp, total):
    """The Warnings section: every needs_review field on a line this scenario awards, largest exposure first. This is not a refusal; the scenario has run and the
    recommendation ships with these caveats. Exposure is the awarded line's re-priced total, so two doubted fields on one line each show that line's full exposure
    (they are not additive). blocks_recommendation marks the ones above the block threshold, which the existing needs_review_lines warning already blocks on."""
    at_risk = {(v, x["line"]): x["total"] for v, vx in rp["vendors"].items() for x in vx["lines"]}
    rows = ctx.con.execute("SELECT s.vendor_id, b.rfx_line_no, b.field_name, b.value, b.unit, b.reason_code, b.resolving_question FROM bid_fields b "
                           "JOIN submissions s USING(submission_id) WHERE b.state='needs_review'").fetchall()
    out = [dict(vendor=v, line=n, field=f, value=val, unit=u, reason_code=rc, value_at_risk_inr=at_risk[(v, n)], value_at_risk_lakh=round(at_risk[(v, n)] / 1e5, 2),
                share_of_award=at_risk[(v, n)] / total, blocks_recommendation=at_risk[(v, n)] / total > ctx.review_share, resolving_question=q)
           for v, n, f, val, u, rc, q in rows if (v, n) in at_risk]
    return sorted(out, key=lambda c: (-c["value_at_risk_inr"], c["vendor"], c["line"], c["field"]))


def refused(ctx, strategy, params, why):
    return dict(strategy=strategy, params=params, basis=ctx.basis, status="refused", recommendable=False, gates_applied=False, allocation={}, vendors_used=[],
                warnings=[warn("refused", "block", why)], needs_review_warnings=[], naive_total=0.0, repriced_total=0.0, coverage=dict(covered=0, of=len(ctx.lines), uncovered=[]), saving=None)


def cheapest(ctx, pool):
    out = {}
    for n in ctx.lines:
        c = [v for v in pool if eligible(ctx, v, n)]
        if c:
            out[n] = min(c, key=lambda v: (ctx.prices[(v, n)]["rank"], v))
    return out


def single_vendor(ctx, vendor=None, gates=None):
    """One vendor covers every line. With no vendor named, each vendor able to cover all lines (and, if gates are supplied, to have passed them) is
    evaluated and the cheapest re-priced one is returned."""
    ok = passing(gates)
    cands = [vendor] if vendor else sorted(ctx.vendors)
    cands = [v for v in cands if ok is None or v in ok]
    full = [v for v in cands if all(eligible(ctx, v, n) for n in ctx.lines)]
    params = dict(vendor=vendor)
    if not full:
        return build(ctx, "single_vendor", {}, params, gates=ok, status="infeasible", notes=[warn("no_full_coverage", "warn", "No vendor can cover every line: " + "; ".join(
            f"{v} misses {sum(not eligible(ctx, v, n) for n in ctx.lines)}" for v in cands))])
    alts = sorted(((reprice(ctx, {n: {v: q} for n, q in ctx.lines.items()})["repriced_total"], v) for v in full))
    best = alts[0][1]
    r = build(ctx, "single_vendor", {n: best for n in ctx.lines}, params, gates=ok)
    r["alternatives"] = [dict(vendor=v, repriced_total=t) for t, v in alts]
    return r


def cheapest_per_line(ctx):
    """Per line, the cheapest eligible vendor, no vendor limit and no gates: a lower bound on cost and an 'ignoring gates' view."""
    return build(ctx, "cheapest_per_line", cheapest(ctx, sorted(ctx.vendors)), {})


def gated_split(ctx, gates):
    """Cheapest per line among vendors that cleared the mandatory gates. Refuses without gate results."""
    ok = passing(gates)
    if ok is None:
        return refused(ctx, "gated_split", {}, "Gate results were not supplied. No award scenario is produced before the mandatory gates are evaluated.")
    return build(ctx, "gated_split", cheapest(ctx, sorted(ok & set(ctx.vendors))), dict(gates=sorted(ok)), gates=ok)


def max_vendors(ctx, n, gates=None):
    """The cheapest re-priced allocation using at most n vendors that still covers every line the pool can cover."""
    ok = passing(gates)
    pool = sorted((ok if ok is not None else set(ctx.vendors)) & set(ctx.vendors))
    reach = {ln for ln in ctx.lines if any(eligible(ctx, v, ln) for v in pool)}
    best, tried = None, 0
    for k in range(1, n + 1):
        for subset in itertools.combinations(pool, k):
            a = cheapest(ctx, subset)
            if set(a) != reach:
                continue
            tried += 1
            t = reprice(ctx, {ln: {v: ctx.lines[ln]} for ln, v in a.items()})["repriced_total"]
            if best is None or t < best[0]:
                best = (t, a)
    params = dict(max_vendors=n, subsets_considered=tried)
    if best is None:
        return build(ctx, "max_vendors", {}, params, gates=ok, status="infeasible")
    return build(ctx, "max_vendors", best[1], params, gates=ok)


def max_share(ctx, share, gates=None):
    """Cheapest per line, then move lines off any vendor holding more than `share` of the value until none does. Line-level moves only."""
    ok = passing(gates)
    pool = sorted((ok if ok is not None else set(ctx.vendors)) & set(ctx.vendors))
    a = cheapest(ctx, pool)
    val = lambda ln, v: ctx.lines[ln] * ctx.prices[(v, ln)]["rank"]
    met = False
    for _ in range(len(ctx.lines) * max(len(pool), 1) + 1):
        tot = sum(val(ln, v) for ln, v in a.items()) or 1.0
        by = collections.defaultdict(float)
        for ln, v in a.items():
            by[v] += val(ln, v)
        over = [v for v in by if by[v] > share * tot + 1e-9]
        if not over:
            met = True
            break
        v = max(over, key=lambda x: by[x])
        moves = [(val(ln, w) - val(ln, v), ln, w) for ln, av in a.items() if av == v for w in pool
                 if w != v and eligible(ctx, w, ln) and by[w] + val(ln, w) <= share * tot + 1e-9]
        if not moves:
            break
        _, ln, w = min(moves)
        a[ln] = w
    notes = [] if met else [warn("cap_not_met", "block", f"No allocation keeps every vendor at or under {share:.0%} of value with the eligible vendors.")]
    return build(ctx, "max_share", a, dict(max_share=share), gates=ok, status="ok" if met else "cap_not_met", notes=notes)


def summarize(r):
    lines = [f"{r['strategy']}  params {r['params']}  basis {r['basis']}  status {r['status']}  recommendable {r['recommendable']}"]
    if r["status"] == "refused":
        return "\n".join(lines + [f"  REFUSED: {r['warnings'][0]['text']}"])
    lines.append(f"  vendors {r['vendors_used']}  coverage {r['coverage']['covered']}/{r['coverage']['of']}  gates {r['gates_passed'] if r['gates_applied'] else 'NOT APPLIED'}")
    lines.append(f"  naive Rs {r['naive_total']:,.0f}  re-priced Rs {r['repriced_total']:,.0f}  (re-pricing effect Rs {r['repricing_effect']:+,.0f})")
    if r["saving"]:
        s = r["saving"]
        lines.append(f"  saving vs {s['vs']}: naive Rs {s['naive']:,.0f}, re-priced Rs {s['repriced']:,.0f} ({s['repriced_pct']:.2f}%)")
    for v, x in r["per_vendor"].items():
        lines.append(f"    {v}: {x['lines']} lines, Rs {x['total']:,.0f} ({x['share']:.1%}), order-value effect {x['order_value_effect_pct']:+.1f}%")
    lines += [f"  [{w['severity'].upper()}] {w['text']}" for w in r["warnings"]]
    if r.get("needs_review_warnings"):
        lines.append(f"  Warnings: needs_review fields contributing to this award, largest value at risk first (block threshold {r['review_block_threshold_pct']:g}%; the scenario still ran)")
        for c in r["needs_review_warnings"]:
            value = "no value" if c["value"] is None else f"{c['value']:,.2f} {c['unit'] or ''}".strip()
            lines.append(f"    {c['vendor']} line {c['line']} {c['field']} = {value} | {c['reason_code']} | Rs {c['value_at_risk_lakh']:,.2f} lakh at risk ({c['share_of_award']:.1%} of the award)"
                         + (" | ABOVE THRESHOLD" if c["blocks_recommendation"] else ""))
            lines.append(f"      question: {c['resolving_question']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", choices=["single_vendor", "cheapest_per_line", "gated_split", "max_vendors", "max_share"])
    ap.add_argument("--db", default=str(ROOT / "procurement.db"))
    ap.add_argument("--basis", choices=BASES, default="ex_freight")
    ap.add_argument("--moq-policy", choices=MOQ_POLICIES, default="overbuy")
    ap.add_argument("--gates", help="comma-separated vendors that passed the mandatory gates (supplied, not evaluated)")
    ap.add_argument("--vendor")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--share", type=float, default=0.7)
    a = ap.parse_args()
    ctx = load(sqlite3.connect(a.db), a.basis, a.moq_policy)
    gates = a.gates.split(",") if a.gates else None
    r = {"single_vendor": lambda: single_vendor(ctx, a.vendor, gates), "cheapest_per_line": lambda: cheapest_per_line(ctx), "gated_split": lambda: gated_split(ctx, gates),
         "max_vendors": lambda: max_vendors(ctx, a.n, gates), "max_share": lambda: max_share(ctx, a.share, gates)}[a.strategy]()
    print(summarize(r))


if __name__ == "__main__":
    main()
