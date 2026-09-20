#!/usr/bin/env python3
"""Seeded, re-runnable ground-truth generator. Writes dataset/truth/truth.sqlite and outcomes.json.

Nothing in the pipeline may import dataset/truth. Run: python scripts/generate_dataset.py
"""
import json
import pathlib
import random
import sqlite3
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from dataset_text import ANSWERS, ATTACHMENTS, EVAL_DATE, QTRUTH, QUESTIONS, SUBMISSIONS, VENDORS  # noqa: E402

SEED = 20260311
ROOT = pathlib.Path(__file__).resolve().parents[1]
TRUTH = ROOT / "dataset" / "truth"
FX = 88.50
FX_SRC, FX_DATE = "RBI reference rate", "2026-03-11"
TAKEUP = {"A": 1.53, "B": 1.40, "C": 1.50}
FLUTES = {3: ["C"], 5: ["B", "C"], 7: ["A", "B", "C"]}
KG_RATE = {3: 39.5, 5: 43.0, 7: 47.0}          # INR per kg of finished box board, market
PRINT_UPLIFT = 0.025                            # per flexo colour
POSTURE = {"V1": {3: 1.04, 5: 1.02, 7: 0.97}, "V2": {3: 0.955, 5: 0.975, 7: 1.06},
           "V3": {3: 0.92, 5: 0.92, 7: 0.92}, "V4": {3: 1.00, 5: 1.01, 7: 1.02}}
V5_KG = {3: 38.0, 5: 42.0}
QSCALE = 1.45                                   # scales quantities >= 5000 to land the event at ~Rs 4 crore
GATED = ["V1", "V2", "V5"]                      # vendors that truly clear all three gates
V2_DISCOUNT, V2_DISCOUNT_MIN = 0.02, 5_000_000
V1_UPLIFT = [(25_000_000, None, 0.0), (15_000_000, 25_000_000, 3.0), (7_500_000, 15_000_000, 6.0), (0, 7_500_000, 9.0)]

# n: (ply, L, W, H, colours, qty, liner_gsm)
SPEC = {
    1: (3, 300, 200, 150, 1, 240000, 150), 2: (3, 320, 240, 180, 0, 180000, 150),
    3: (3, 350, 250, 200, 1, 150000, 150), 4: (3, 380, 260, 220, 0, 120000, 150),
    5: (3, 400, 300, 200, 2, 95000, 150), 6: (3, 300, 220, 250, 0, 80000, 150),
    7: (3, 420, 280, 230, 1, 60000, 150), 8: (3, 360, 260, 160, 0, 48000, 150),
    9: (3, 320, 240, 160, 0, 36000, 120), 10: (3, 450, 300, 250, 1, 24000, 150),
    11: (3, 330, 230, 280, 0, 15000, 120), 12: (3, 280, 210, 140, 0, 9000, 120),
    13: (3, 400, 260, 300, 1, 6500, 150), 14: (3, 340, 240, 140, 0, 2600, 150),
    15: (3, 390, 290, 210, 1, 1900, 120), 16: (3, 310, 250, 190, 2, 1400, 150),
    17: (5, 500, 350, 300, 1, 60000, 150), 18: (5, 550, 380, 320, 2, 45000, 150),
    19: (5, 600, 400, 350, 0, 38000, 150), 20: (5, 480, 320, 280, 0, 30000, 150),
    21: (5, 520, 360, 300, 1, 24000, 120), 22: (5, 450, 300, 260, 0, 18000, 150),
    23: (5, 560, 400, 360, 1, 15000, 150), 24: (5, 580, 390, 340, 0, 10000, 150),
    25: (5, 500, 330, 320, 0, 2800, 120), 26: (5, 620, 420, 300, 2, 2400, 150),
    27: (7, 600, 400, 400, 1, 20000, 180), 28: (7, 650, 450, 420, 0, 16000, 180),
    29: (7, 700, 500, 450, 1, 14000, 180), 30: (7, 580, 420, 380, 0, 11000, 180),
}
V3_MISMATCH = {1, 2, 3, 4, 5, 6, 7, 8, 10, 17, 18, 19, 30}    # declares 120 GSM liners vs 150 (180 on line 30)
V3_VAGUE = {13, 14, 16, 20, 22}                               # GSM not stated; 120 actually supplied
V3_NOQUOTE = {27}                                             # explicitly regretted
V3_ABSENT = {28, 29}                                          # never appear in the document
V2_PER100 = set(range(9, 17))
V4_PER_KG = set(range(17, 27))
V4_OCR = {3, 27}
V4_OUTLIER = 24
ARITH = {("V1", 12): 0, ("V2", 20): 1}                        # (vendor, line) -> digit index to swap


def band(q):
    return 1.03 if q < 3000 else 1.015 if q < 10000 else 1.0 if q < 50000 else 0.985 if q < 150000 else 0.97


def build_lines():
    lines = {}
    for n, (ply, L, W, H, col, qty, liner) in SPEC.items():
        qty = round(qty * QSCALE / 500) * 500 if qty >= 5000 else qty
        med = 100 if liner == 120 else 120
        layers = [liner if i % 2 == 0 else med for i in range(ply)]
        area = (2 * (L + W) + 40) * (H + W) / 1e6
        ln = dict(n=n, ply=ply, L=L, W=W, H=H, col=col, qty=qty, liner=liner, med=med, area=area,
                  flute="".join(FLUTES[ply]), stack="/".join(map(str, layers)),
                  bf=(18 if liner == 150 else 16) if ply == 3 else (20 if liner == 150 else 18) if ply == 5 else 24,
                  print="plain" if col == 0 else f"{col}-colour flexo")
        ln["eff"], ln["weight"] = weight(ln)
        ln["sku"] = f"CB-{ply}P-{L}x{W}x{H}" + (f"-P{col}" if col else "")
        ln["should"] = ln["weight"] * KG_RATE[ply] * (1 + PRINT_UPLIFT * col) * band(qty)
        lines[n] = ln
    return lines


def weight(ln, liner=None):
    liner = liner or ln["liner"]
    n_liner, fl = (ln["ply"] + 1) // 2, FLUTES[ln["ply"]]
    eff = n_liner * liner + sum(ln["med"] * TAKEUP[f] for f in fl)
    return eff, ln["area"] * eff / 1000


def wdesc(ln, liner=None):
    eff, w = weight(ln, liner)
    return (f"board weight {w:.4f} kg = blank {ln['area']:.4f} m2 x eff GSM {eff:.1f} / 1000 "
            f"(liners {liner or ln['liner']}, medium {ln['med']} x take-up {'/'.join(str(TAKEUP[f]) for f in FLUTES[ln['ply']])})")


def raw(v, ln, liner=None):
    r = random.Random(f"{SEED}:{v}:{ln['n']}")
    noise = max(-0.03, min(0.03, r.gauss(0, 0.015)))
    w = weight(ln, liner)[1]
    return w * KG_RATE[ln["ply"]] * (1 + PRINT_UPLIFT * ln["col"]) * band(ln["qty"]) * POSTURE[v][ln["ply"]] * (1 + noise)


def swap_digits(x, i):
    s = list(str(int(round(x))))
    while s[i] == s[i + 1]:
        i += 1
    s[i], s[i + 1] = s[i + 1], s[i]
    return float("".join(s))


def desc(ln):
    return f"RSC {ln['L']}x{ln['W']}x{ln['H']} mm, {ln['ply']}-ply {ln['flute']} flute, {ln['stack']} GSM, {ln['print']}"


def build_rows(lines):
    rows, P, COMP = [], {v: {} for v in ["V1", "V2", "V3", "V4", "V5"]}, {v: {} for v in ["V1", "V2", "V3", "V4", "V5"]}

    def add(v, n, field, value, unit, basis, cur, state, reason, anchor, snippet, deriv=None, q=None, norm=None, comp=None):
        rows.append(dict(sub=f"S{v[1]}", n=n, field=field, value=value, unit=unit, basis=basis, cur=cur, state=state,
                         reason=reason, anchor=anchor, snippet=snippet, deriv=deriv, q=q, norm=norm, comp=comp))
        if field == "unit_price":
            P[v][n] = norm
            COMP[v][n] = comp

    for n, ln in lines.items():
        qty, r = ln["qty"], 9 + n
        # V1 - Excel, own layout, rupees per piece
        p = round(raw("V1", ln), 2)
        bad = ("V1", n) in ARITH
        tot = round(qty * p, 2)
        stated = swap_digits(tot, ARITH[("V1", n)]) if bad else tot
        add("V1", n, "unit_price", p, "INR/piece", "per_piece", "INR", "needs_review" if bad else "extracted",
            "arith_mismatch" if bad else "stored_as_text", f"Quotation!H{r}", f"{p:.2f}",
            "truth: unit price intended; stated line total has transposed digits" if bad else None, norm=p, comp="comparable")
        add("V1", n, "line_total", stated, "INR", "line_total", "INR", "needs_review" if bad else "extracted",
            "arith_mismatch" if bad else None, f"Quotation!K{r}", f"{stated:,.2f}",
            f"qty {qty} x {p:.2f} = {tot:,.2f}; vendor states {stated:,.2f}" if bad else None)
        add("V1", n, "declared_liner_gsm", ln["liner"], "gsm", "spec", None, "extracted", None, f"Quotation!E{r}", ln["stack"])

        # V2 - PDF letterhead; lines 9-16 per 100 pieces; discount lives in the footnote
        pg, ra = 1 + (n - 1) // 12, raw("V2", ln)
        per100 = n in V2_PER100
        p = round(ra * 100, 2) if per100 else round(ra, 2)
        norm = p / 100 if per100 else p
        bad = ("V2", n) in ARITH
        tot = round(p * qty / 100 if per100 else p * qty, 2)
        stated = swap_digits(tot, ARITH[("V2", n)]) if bad else tot
        unit_txt = "per 100 pcs" if per100 else "per pc"
        add("V2", n, "unit_price", p, "INR/100pcs" if per100 else "INR/piece", "per_100_pieces" if per100 else "per_piece", "INR",
            "needs_review" if bad else "extracted", "arith_mismatch" if bad else ("basis_per_100" if per100 else None),
            f"p{pg}:r{n}:desc", f"{desc(ln)} - Rs. {p:,.2f} {unit_txt}",
            (f"{p:,.2f} / 100 = {norm:.4f} INR/pc" if per100 else None), norm=norm, comp="comparable")
        add("V2", n, "line_total", stated, "INR", "line_total", "INR", "needs_review" if bad else "extracted",
            "arith_mismatch" if bad else None, f"p{pg}:r{n}:total", f"{stated:,.2f}",
            f"qty {qty} x {p:,.2f}{'/100' if per100 else ''} = {tot:,.2f}; vendor states {stated:,.2f}" if bad else None)
        add("V2", n, "declared_liner_gsm", ln["liner"], "gsm", "spec", None, "extracted", None, f"p{pg}:r{n}:desc", desc(ln))

        # V3 - Word prose
        if n in V3_NOQUOTE:
            add("V3", n, "unit_price", None, None, None, "INR", "not_quoted", "declined_capability", "para29",
                "For the 600x400x400 7-ply carton, we are unable to take this size up at present.")
        elif n in V3_ABSENT:
            add("V3", n, "unit_price", None, None, None, "INR", "missing", "line_absent", None, None, None,
                q=f"Om Sai Box Works neither priced nor declined line {n}. Ask them whether they will quote it.")
        else:
            para = f"para{2 + n}" if n <= 26 else "para30"
            mis, vag = n in V3_MISMATCH, n in V3_VAGUE
            liner = 120 if (mis or vag) else ln["liner"]
            p = round(raw("V3", ln, liner), 2)
            spec_txt = "" if vag else f" with {liner} GSM kraft liners"
            sent = f"For the {ln['L']}x{ln['W']}x{ln['H']} {ln['ply']}-ply carton{spec_txt} we can offer Rs. {p:.2f} per box at your indicated volume."
            add("V3", n, "unit_price", p, "INR/piece", "per_piece", "INR", "needs_review" if vag else "extracted",
                "spec_incomplete" if vag else ("spec_mismatch_gsm" if mis else None), para, sent,
                (f"truth: {liner} GSM liners actually supplied; " + wdesc(ln, liner)) if (mis or vag) else None,
                norm=p, comp="not_comparable" if (mis or vag) else "comparable")
            if vag:
                add("V3", n, "declared_liner_gsm", None, "gsm", "spec", None, "missing", "spec_incomplete", para, sent, None,
                    q=f"Line {n}: what liner GSM and BF are you quoting? Spec is {ln['liner']} GSM.")
            else:
                add("V3", n, "declared_liner_gsm", liner, "gsm", "spec", None, "extracted", "spec_mismatch_gsm" if mis else None, para, sent)

        # V4 - trader, USD, photo of a rate card
        ra = raw("V4", ln) * (1.32 if n == V4_OUTLIER else 1.0)
        kg = n in V4_PER_KG
        usd = round(ra / ln["weight"] / FX, 3) if kg else round(ra / FX, 3)
        norm = usd * FX * (ln["weight"] if kg else 1)
        ocr = n in V4_OCR
        alt = str(usd)[:-1] + {"8": "3", "3": "8", "5": "6", "6": "5", "1": "7", "7": "1", "0": "6", "9": "4", "4": "9", "2": "2"}[str(usd)[-1]]
        reason = "ocr_ambiguous_digit" if ocr else ("price_outlier" if n == V4_OUTLIER else "currency_usd")
        deriv = f"USD {usd:.3f}{'/kg' if kg else '/pc'} x {FX} INR/USD ({FX_SRC}, {FX_DATE})"
        deriv += f" x {ln['weight']:.4f} kg; {wdesc(ln)}" if kg else ""
        deriv += f" = INR {norm:.2f}/pc" + (f"; last digit reads {usd:.3f} or {alt} under compression" if ocr else "")
        add("V4", n, "unit_price", usd, "USD/kg" if kg else "USD/piece", "per_kg" if kg else "per_piece", "USD",
            "needs_review" if ocr else "extracted", reason, f"ratecard:r{n}:c4", f"{usd:.3f}", deriv, norm=norm,
            comp="comparable_with_assumptions")
        add("V4", n, "declared_liner_gsm", ln["liner"], "gsm", "spec", None, "extracted", None, f"ratecard:r{n}:c2", f"{ln['ply']} ply {ln['liner']} GSM")

    # V5 - incumbent email
    sentence = "Rs 42/kg for the 5-ply, 38 for the 3-ply, rest same as last year, freight extra."
    for n, ln in lines.items():
        if ln["ply"] == 7:
            add("V5", n, "unit_price", None, None, None, None, "missing", "reference_unresolved", "L6",
                "rest same as last year",
                "truth: these 7-ply SKUs are new this year, so no FY25 price exists to resolve the reference",
                q=f"Line {n} (7-ply {ln['L']}x{ln['W']}x{ln['H']}): does FY25 PO history hold a price for this SKU? "
                  "If not, V5 must quote it afresh; 'same as last year' cannot resolve it.")
            continue
        kg = V5_KG[ln["ply"]]
        norm = kg * ln["weight"]
        add("V5", n, "unit_price", kg, "INR/kg", "per_kg", "INR", "extracted", "basis_per_kg", "L6", sentence,
            f"INR {kg:.1f}/kg x {ln['weight']:.4f} kg = INR {norm:.2f}/pc; {wdesc(ln)}; assumes rate is all-in for printed lines (email silent)",
            norm=norm, comp="comparable_with_assumptions")
    return rows, P, COMP


def conditions():
    t1, t2 = "Terms & Conditions", "p3:terms"
    return [
        ("S1", "validity", 30, None, f"{t1}!B3", "Prices valid for 30 days from date of quotation."),
        ("S1", "payment_terms", 45, "days net", f"{t1}!B4", "Payment: 45 days net from invoice date."),
        ("S1", "freight", None, "extra at actuals", f"{t1}!B5", "Freight extra at actuals, ex-works."),
        ("S1", "moq", 1000, "pieces per SKU", f"{t1}!B6", "MOQ 1,000 pcs per SKU."),
        ("S1", "bundle", None, "rates assume award of all 30 items", f"{t1}!B7",
         "Rates quoted are for award of all 30 items. Volume slab below applies to the value actually awarded."),
        ("S1", "bundle", None, "slabs apply to awarded value", f"{t1}!B15",
         "Slabs apply to the value actually awarded; lower bound inclusive, upper bound exclusive."),
        ("S2", "validity", 15, None, t2, "This offer is valid for 15 days from the date of this letter."),
        ("S2", "payment_terms", 30, "days net", t2, "Payment: 30 days net."),
        ("S2", "freight", None, "extra at actuals", t2, "Freight extra."),
        ("S2", "moq", 3000, "pieces per SKU", t2, "Minimum order quantity 3,000 pcs per SKU."),
        ("S2", "discount_footnote", 2.0, "% on order value above 5000000", "p2:footnote",
         "A further 2% settlement discount applies on orders exceeding Rs. 50 lakh."),
        ("S3", "validity", 10, None, "para31", "This offer is valid for ten days."),
        ("S3", "payment_terms", 50, "% advance", "para32", "Payment: 50% advance, balance before dispatch."),
        ("S3", "freight", None, "extra", "para32", "Transport extra."),
        ("S3", "moq", 500, "pieces", "para33", "Minimum 500 pieces per size."),
        ("S4", "validity", 14, None, "ratecard:footer", "Valid 14 days"),
        ("S4", "fx_risk_on_buyer", None, "USD prices; exchange variation on buyer", "ratecard:footer", "Prices in USD. Exchange variation for buyer's account."),
        ("S4", "payment_terms", 30, "% advance", "ratecard:footer", "30% advance, balance against documents"),
        ("S4", "moq", 5000, "pieces per size", "ratecard:footer", "MOQ 5,000 per size"),
        ("S4", "freight", None, "extra", "ratecard:footer", "Freight extra"),
        ("S5", "freight", None, "extra", "L6", "freight extra."),
        ("S5", "prior_year_reference", None, "same as last year", "L6", "rest same as last year"),
        ("S5", "validity_not_stated", None, None, None, None),
    ]


def tiers():
    out = []
    for lo, hi, pct in V1_UPLIFT:
        rng = f"Rs {lo/1e5:.0f} lakh and above" if hi is None else f"Rs {lo/1e5:.0f} to {hi/1e5:.0f} lakh" if lo else f"below Rs {hi/1e5:.0f} lakh"
        out.append(("V1", "order_value_uplift", lo, hi, None, None, pct, "Terms & Conditions!B10:D13",
                    f"Awarded value {rng}: {'rates as quoted' if pct == 0 else f'add {pct:.1f}% to quoted rates'}"))
    out.append(("V2", "order_value_discount", V2_DISCOUNT_MIN, None, None, None, -2.0, "p2:footnote",
                "A further 2% settlement discount applies on orders exceeding Rs. 50 lakh."))
    out.append(("V3", "line_qty_break", None, None, 3, 200000, -2.4, "para5",
                "The 350x250x200 carton drops 2.4% if the annual commitment exceeds 200,000 pieces."))
    out.append(("V3", "line_qty_break", None, None, 4, 150000, -2.0, "para6",
                "The 380x260x220 carton drops 2% if the annual commitment exceeds 150,000 pieces."))
    return out


def v1_uplift(value):
    return next(p for lo, hi, p in V1_UPLIFT if value >= lo and (hi is None or value < hi))


def outcomes(lines, P, COMP, rows):
    q = {n: l["qty"] for n, l in lines.items()}
    fr = {v[0]: v[7] for v in VENDORS}
    moq = {v[0]: v[8] for v in VENDORS}
    vs = list(P)
    quoted = {v: [n for n in lines if P[v].get(n) is not None] for v in vs}
    headline = {v: sum(q[n] * P[v][n] for n in quoted[v]) for v in vs}
    cheapest = {n: min((v for v in vs if P[v].get(n) is not None), key=lambda v: P[v][n]) for n in lines}
    won = {v: sum(1 for c in cheapest.values() if c == v) for v in vs}
    full = [v for v in vs if len(quoted[v]) == 30 and all(COMP[v][n] != "not_comparable" for n in lines)]
    landed = {v: sum(q[n] * P[v][n] for n in lines) * (1 + fr[v]) for v in full}
    gate_removed = [n for n, c in cheapest.items() if c not in GATED]

    def naive_price(v, n):
        return P[v][n] * (1 - V2_DISCOUNT) if v == "V2" else P[v][n]

    def cost(alloc, cond):
        total = 0.0
        for v in set(alloc.values()):
            ns = [n for n, a in alloc.items() if a == v]
            stated = sum(q[n] * P[v][n] for n in ns)
            if v == "V1":
                total += stated * (1 + (v1_uplift(stated) / 100 if cond else 0))
            elif v == "V2":
                disc = 1 - V2_DISCOUNT if (not cond or stated >= V2_DISCOUNT_MIN) else 1.0
                over = sum(max(0, moq["V2"] - q[n]) * P[v][n] for n in ns) if cond else 0
                total += (stated + over) * disc
            else:
                total += stated
        return total

    base = {n: "V2" for n in lines}
    split = {n: min((v for v in GATED if P[v].get(n) is not None), key=lambda v: naive_price(v, n)) for n in lines}
    alloc_val = {v: sum(q[n] * P[v][n] for n in lines if split[n] == v) for v in GATED}
    naive_sav = cost(base, False) - cost(split, False)
    real_sav = cost(base, True) - cost(split, True)
    disp = [max(P[v][n] for v in vs if P[v].get(n) and COMP[v][n] != "not_comparable" and not (v == "V4" and n == V4_OUTLIER))
            / min(P[v][n] for v in vs if P[v].get(n) and COMP[v][n] != "not_comparable") - 1 for n in lines]
    return dict(
        event_value_should_cost_inr=round(sum(q[n] * l["should"] for n, l in lines.items())),
        headline_total_inr={v: round(x) for v, x in headline.items()}, lines_quoted={v: len(quoted[v]) for v in vs},
        lines_cheapest_by_vendor=won, headline_cheapest=min(headline, key=headline.get),
        landed_total_full_coverage_compliant_inr={v: round(x) for v, x in landed.items()},
        true_cheapest_landed=min(landed, key=landed.get),
        lines_where_cheapest_vendor_fails_gates=gate_removed,
        v3_mismatch_lines=sorted(V3_MISMATCH), v3_vague_lines=sorted(V3_VAGUE),
        lines_below_v2_moq=[n for n in lines if q[n] < moq["V2"]], lines_below_v4_moq=[n for n in lines if q[n] < moq["V4"]],
        gated_split_allocation=split, gated_split_value_by_vendor_inr={v: round(x) for v, x in alloc_val.items()},
        baseline_single_source_v2_naive_inr=round(cost(base, False)), baseline_single_source_v2_repriced_inr=round(cost(base, True)),
        naive_split_saving_inr=round(naive_sav), repriced_split_saving_inr=round(real_sav),
        saving_retained_pct=round(100 * real_sav / naive_sav, 1) if naive_sav else None,
        v1_uplift_pct_on_split=v1_uplift(alloc_val["V1"]), v2_discount_earned_on_split=alloc_val["V2"] >= V2_DISCOUNT_MIN,
        line_dispersion_median_pct=round(100 * statistics.median(disp), 1),
        line_dispersion_min_max_pct=[round(100 * min(disp), 1), round(100 * max(disp), 1)],
    )


def check(o, lines):
    plies = [l["ply"] for l in lines.values()]
    assert (plies.count(3), plies.count(5), plies.count(7)) == (16, 10, 4)
    assert 35e6 <= o["event_value_should_cost_inr"] <= 45e6, o["event_value_should_cost_inr"]
    assert o["headline_cheapest"] == "V3", o["headline_total_inr"]
    assert o["true_cheapest_landed"] != o["headline_cheapest"]
    assert len(o["lines_where_cheapest_vendor_fails_gates"]) >= 6
    assert len(o["lines_below_v2_moq"]) >= 3
    assert o["saving_retained_pct"] is not None and 0 < o["saving_retained_pct"] < 50, o["saving_retained_pct"]
    assert sum(1 for n in V3_MISMATCH if lines[n]["liner"] == 150) >= 10


def write_db(lines, rows, o):
    db = TRUTH / "truth.sqlite"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.executescript((pathlib.Path(__file__).parent / "schema.sql").read_text())
    con.executemany("INSERT INTO rfx_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(n, l["sku"], "RSC", l["L"], l["W"], l["H"], l["ply"], l["flute"], l["liner"], l["stack"], l["bf"], l["print"],
                      l["qty"], "piece", round(l["area"], 4), round(l["weight"], 4), round(l["should"], 2)) for n, l in lines.items()])
    con.executemany("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?,?,?)", VENDORS)
    for v, (recv, val, inc, fn) in SUBMISSIONS.items():
        d = __import__("datetime").date.fromisoformat(recv)
        until = (d + __import__("datetime").timedelta(days=val)).isoformat() if val else None
        con.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"S{v[1]}", v, fn, fn.rsplit(".", 1)[1], recv, val, until, int(bool(until and until < EVAL_DATE)), EVAL_DATE, inc,
                     "USD" if v == "V4" else "INR", FX if v == "V4" else None, FX_SRC if v == "V4" else None, FX_DATE if v == "V4" else None))
    con.executemany("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,reason_code,anchor,snippet,"
                    "derivation,resolving_question,norm_inr_pc,comparability) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(r["sub"], r["n"], r["field"], r["value"], r["unit"], r["basis"], r["cur"], r["state"], r["reason"], r["anchor"],
                      r["snippet"], r["deriv"], r["q"], None if r["norm"] is None else round(r["norm"], 4), r["comp"]) for r in rows])
    con.executemany("INSERT INTO tier_rules (vendor_id,kind,min_value_inr,max_value_inr,rfx_line_no,min_qty,effect_pct,anchor,snippet) VALUES (?,?,?,?,?,?,?,?,?)", tiers())
    con.executemany("INSERT INTO conditions (submission_id,kind,value_num,value_text,anchor,snippet) VALUES (?,?,?,?,?,?)",
                    [(s, k, vn, vt, a, sn) for s, k, vn, vt, a, sn in conditions()])
    con.executemany("INSERT INTO attachments VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(i, v, k, f, iss, ent, vt, int(bool(vt and vt < EVAL_DATE)), sup, note) for i, v, k, f, iss, ent, vt, sup, note in ATTACHMENTS])
    for v, ans in ANSWERS.items():
        for q_no, gate, text in QUESTIONS:
            a, b, att, score, gs, why = ans[q_no]
            st, gst, stance, exp, ev = QTRUTH.get(v, {}).get(q_no, (None,) * 5)
            if st == "missing":                     # the response document carries no answer to this question
                a, b, att = "", None, None
            con.execute("INSERT INTO questionnaire_answers (vendor_id,q_no,question,is_gate,gate_code,answer_text,answer_bool,attachment_id,"
                        "max_score,truth_score,truth_gate_status,truth_reason,state,gate_status,stance,expiry_date,evidence_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (v, q_no, text, int(bool(gate)), gate, a, b, att, None if gate else 5, score, gs, why, st, gst, stance, exp, ev))
    con.commit()
    con.close()
    (TRUTH / "outcomes.json").write_text(json.dumps(o, indent=2))


def main():
    TRUTH.mkdir(parents=True, exist_ok=True)
    lines = build_lines()
    rows, P, COMP = build_rows(lines)
    o = outcomes(lines, P, COMP, rows)
    print(json.dumps({k: v for k, v in o.items() if k != "gated_split_allocation"}, indent=1))
    check(o, lines)
    write_db(lines, rows, o)
    print("OK: all outcome assertions hold; wrote", TRUTH / "truth.sqlite")


if __name__ == "__main__":
    main()
