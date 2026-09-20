"""Plain-language reading of the award engine's own output, for the Comparison preview and the exported award note.
Every figure comes from the engine result (totals, savings, adjustments, warnings); this module only chooses words. Vendor names come from the vendors table."""
import re

from app import fmt

SCENARIOS = {
    "gated_split": "Split by line: cheapest qualified vendor per line",
    "single_vendor": "Single source: cheapest qualified vendor",
    "cheapest_per_line": "Cheapest per line, ignoring vendor qualification",
    "max_vendors": "Dual source: at most 2 vendors",
    "max_share": "Bounded split: no vendor above 70% of the order",
}
BASIS_LINE = {"ex_freight": "Prices exclude freight. Vendors quoted 'freight extra' — the landed cost view is available but incomplete until per-vendor freight figures are supplied.",
              "landed": "Prices include freight at the figures on record."}


def crore(x, d=2):
    return f"₹{x / 1e7:,.{d}f} cr"


def lakh(x, d=2):
    return f"₹{fmt.indian(x / 1e5, d)} lakh"


def short_money(x):
    return crore(x) if x >= 1e7 else lakh(x)


def _amount(n):
    n = float(n.replace(",", ""))
    return f"₹{n / 1e7:g} crore" if n >= 1e7 else f"₹{n / 1e5:g} lakh"


def plain_range(condition):
    """'awarded value in [7,500,000, 15,000,000)' -> 'between ₹75 lakh and ₹1.5 crore'."""
    m = re.match(r"awarded value in \[([\d,]+), ([\d,]+|inf)\)", condition or "")
    if m:
        lo, hi = m.groups()
        if hi == "inf":
            return f"{_amount(lo)} or more"
        return f"less than {_amount(hi)}" if float(lo.replace(",", "")) == 0 else f"between {_amount(lo)} and {_amount(hi)}"
    m = re.match(r"awarded value > ([\d,]+)", condition or "")
    return f"more than {_amount(m.group(1))}" if m else (condition or "")


PLAIN = {"freight_estimate_pct_of_order_value": "a freight estimate as a percentage of order value", "ex_freight": "ex-freight"}


def assumption_label(assumption_id):
    """An assumption's plain name: 'fx_usd_inr' -> 'FX rate (USD to INR)'; freight overrides and flute take-up factors keep the vendor or flute in brackets."""
    labels = {"fx_usd_inr": "FX rate (USD to INR)", "freight_estimate_pct_of_order_value": "Freight estimate (% of order value)", "event_id": "Event ID"}
    if assumption_id in labels:
        return labels[assumption_id]
    kind, _, key = assumption_id.partition(":")
    if kind == "take_up_factor":
        return f"Flute take-up factor ({key})"
    if kind == "freight_estimate_pct":
        return f"Freight override ({key})"
    return assumption_id


def named(text, names):
    """Swap vendor codes for registered names in engine text, leaving file names and other identifiers alone; internal setting names become plain phrases."""
    text = re.sub(r"\bV\d+\b", lambda m: names.get(m.group(0), m.group(0)), text or "")
    for raw, plain in PLAIN.items():
        text = text.replace(raw, plain)
    return text


def named_outside_fences(markdown, names):
    """The same swap over a whole exported note, skipping fenced blocks so verbatim vendor snippets stay exactly as quoted."""
    out, fenced = [], False
    for line in markdown.split(chr(10)):
        if line.lstrip().startswith("~~~") or line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
        else:
            out.append(line if fenced else named(line, names))
    return chr(10).join(out)


def run_scenario(award, ctx, scenario, gates):
    """The engine call for a scenario key; the engine does the work."""
    if scenario == "gated_split":
        return award.gated_split(ctx, gates)
    if scenario == "cheapest_per_line":
        return award.cheapest_per_line(ctx)
    if scenario == "max_vendors":
        return award.max_vendors(ctx, 2, gates)
    if scenario == "max_share":
        return award.max_share(ctx, 0.7, gates)
    return award.single_vendor(ctx, gates=gates)


def build(con, scenario, basis):
    """A display model: header, tone and ordered sections. Sections are ('lines', [str]), ('table', title, columns, rows), ('text', [str]) or ('warnings', [str])."""
    from src import award
    from src.extract.questionnaire import gate_results
    names = dict(con.execute("SELECT vendor_id, name FROM vendors"))
    name = lambda v: names.get(v, v)
    full = lambda v: f"{name(v)} ({v})"
    gates, _ = gate_results(con)
    ctx = award.load(con, basis=basis)
    r = run_scenario(award, ctx, scenario, gates)
    used = r.get("vendors_used") or []
    blocks = [named(w["text"], names) for w in r.get("warnings", []) if w["severity"] == "block"]
    basis_line = BASIS_LINE.get(basis, "")
    if r["status"] == "refused":
        return dict(header="System recommendation: none — the engine refused this scenario", tone="error", sections=[("warnings", blocks or [named(w["text"], names) for w in r["warnings"]])], names=names)
    if r["status"] != "ok" or not used:
        return dict(header="System recommendation: none — this cannot be awarded on this cost basis", tone="error", sections=[("warnings", blocks)], names=names)
    saving = r.get("saving")
    if len(used) == 1:
        return _single(award, ctx, con, r, scenario, gates, names, name, full, basis, basis_line, used[0])
    return _split(r, saving, blocks, names, name, full, basis_line, used)


def _single(award, ctx, con, r, scenario, gates, names, name, full, basis, basis_line, vendor):
    label = "ex-freight" if basis == "ex_freight" else "landed"
    lines = [f"Total spend: {crore(r['repriced_total'])} ({label})"]
    earned = r.get("earned_discount") or 0
    if earned > 0:
        ranges = sorted({plain_range(a["condition"]) for a in r["adjustments"] if a["vendor"] == vendor and a["applied"] and a["effect_inr"] < 0})
        lines.append(f"Volume discount earned: {lakh(earned)}" + (f" ({name(vendor)}'s discount for orders worth {' and '.join(ranges)} applies)" if ranges else ""))
    rows = []
    for v in names:
        rr = award.single_vendor(ctx, v, gates)
        rows.append((v, rr["repriced_total"] if rr["status"] == "ok" and rr["repriced_total"] else None))
    priced = sorted((t, v) for v, t in rows if t)
    cheapest = priced[0][0] if priced else None
    table_rows = [[full(v), crore(t), "—" if t == cheapest else f"+{(t / cheapest - 1) * 100:.1f}%"] for t, v in priced]
    table_rows += [[full(v), "excluded", "gates" if v not in (gates or []) else "coverage"] for v, t in rows if not t]
    sections = [("lines", lines), ("table", "Cheapest single-source option", ["Vendor", "Total", "Gap vs cheapest"], table_rows),
                ("text", ["System recommends the cheapest qualified single source above."] +
                 ([f"No split of the order across two or more vendors beats awarding everything to {name(vendor)}."] if scenario != "single_vendor" else []) + [basis_line])]
    return dict(header=f"Award to {name(vendor)} — single source", tone="info", sections=sections, names=names)


def _split(r, saving, blocks, names, name, full, basis_line, used):
    per = r["per_vendor"]
    total_lines = sum(d["lines"] for d in per.values())
    table_rows = [[full(v), d["lines"], crore(d["total"])] for v, d in per.items()] + [["TOTAL", total_lines, crore(r["repriced_total"])]]
    sections = []
    if blocks:
        sections.append(("warnings", blocks))
    sections.append(("table", "How the split would divide the order", ["Vendor", "Lines", "Est. cost"], table_rows))
    warn = any(w["code"] == "saving_after_repricing" and w["severity"] == "warn" for w in r["warnings"])
    header, tone = None, "success"
    if saving:
        m = re.search(r"\((V\d+)\)", saving["vs"])
        base = name(m.group(1)) if m else "the cheapest single vendor"
        naive, real, pct = saving["naive"], saving["repriced"], saving["repriced_pct"]
        compare = [f"On paper the split looks {lakh(abs(naive), 1)} {'cheaper' if naive >= 0 else 'more expensive'}.",
                   ("In reality — after each vendor's volume pricing is recalculated at what they would actually ship — the split costs " + lakh(-real, 1) + " MORE."
                    if real < 0 else "In reality — after each vendor's volume pricing is recalculated at what they would actually ship — the split still saves " + lakh(real, 1) + "."),
                   f"That's a {abs(pct):.1f}% {'loss' if real < 0 else 'saving'} on this event."]
        sections.append(("lines", [f"**Compared with awarding everything to {base}:**"] + compare))
        why = _why_different(r, names, name, per)
        if why:
            sections.append(("text", ["**Why the difference**"] + why))
        if warn:
            n = len(used)
            event = short_money(saving["baseline_repriced"])
            sections.append(("text", ["**Why this is not recommended**",
                                      ("The split does not save money once each vendor's volume pricing is applied, so there is nothing to weigh against the extra work of managing "
                                       f"{n} vendor relationships instead of one." if real <= 0 else
                                       f"The saving is below {1:g}% of the {event} event. The operational cost of managing {n} vendor relationships instead of one likely exceeds any theoretical saving.")]))
        if blocks:
            header, tone = "System recommendation: DO NOT USE THIS OPTION", "error"
        elif warn:
            header, tone = f"System recommendation: DO NOT SPLIT — single-source {base}", "error"
        else:
            header = f"System recommends this split — real saving of {lakh(real, 1)} ({pct:.1f}%)."
    else:
        header, tone = "System recommendation: no comparison with single-sourcing is available for this split", "warning"
    sections.append(("text", [basis_line]))
    return dict(header=header, tone=tone, sections=sections, names=names)


def _why_different(r, names, name, per):
    """One sentence per vendor price rule that changed the split's cost, from the engine's own adjustments."""
    grouped = {}
    for a in r["adjustments"]:
        if a["applied"] and a["effect_inr"]:
            g = grouped.setdefault((a["vendor"], plain_range(a["condition"]), a["effect_inr"] > 0), dict(pct=0.0, inr=0.0, actual=a["actual"]))
            g["pct"] += a["effect_pct"]
            g["inr"] += a["effect_inr"]
    out = []
    for (v, rng, up), g in grouped.items():
        if up:
            out.append(f"{name(v)}'s price list adds {g['pct']:g}% when the order it wins is worth {rng}. In this split it would win {short_money(g['actual'])}, so its prices rise by {lakh(g['inr'])}.")
        else:
            out.append(f"{name(v)} gives a {abs(g['pct']):g}% volume discount on orders worth {rng}. This split still earns it: {lakh(-g['inr'])} off.")
    if r.get("unearned_discount"):
        out.append(f"In total, {lakh(r['unearned_discount'])} of full-award pricing is lost by splitting the order.")
    return out


def markdown(model):
    """The same model as Markdown, for the exported note."""
    out = [f"**{model['header']}**", ""]
    for s in model["sections"]:
        if s[0] == "table":
            _, title, cols, rows = s
            out += [f"**{title}**", "", "| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"] + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows] + [""]
        elif s[0] == "warnings":
            out += [f"> {w}" for w in s[1]] + [""]
        else:
            out += [line for line in s[1]] + [""] if s[0] == "text" else [f"{line}  " for line in s[1]] + [""]
    return chr(10).join(out).rstrip()
