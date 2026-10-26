"""The guided story: one buyer's sourcing event told in six steps, with original vendor files beside what the product read from them."""
from __future__ import annotations

from contextlib import closing
from html import escape
from pathlib import Path
import sqlite3

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "dataset" / "artifacts"

# (page key, short label, title, what is happening, what to look at)
STEPS = [
    ("RFx", "Brief", "Start with the buying rules.",
     "Priya, a buyer, needs cardboard boxes for the coming year: 30 sizes, about 20 lakh boxes in all. She sends five suppliers one "
     "request for quotation (RFQ): every box she needs, plus a questionnaire. Three of its questions are must-haves.",
     "The 30 box sizes below, and the questionnaire at the bottom with the three must-haves marked."),
    ("Replies", "Evidence", "Five formats. One evidence trail.",
     "No two replies look alike: a spreadsheet, a PDF, a letter in Word, a phone photo and a one-line email. The product reads each file "
     "and copies out every price together with the exact words it came from.",
     "Pick a supplier. Its original file is on the left; what the product read from it is on the right."),
    ("Event", "Review", "Put human attention where it matters.",
     "Some replies have totals that don't add up, prices hidden by glare, missing sizes or claims with no certificate. The product does not "
     "guess. It checks every supplier against the three must-haves and flags every doubtful value.",
     "Who passes the must-haves, then the list of doubtful values, sorted by how much money is at stake."),
    ("Clarify", "Clarify", "Turn uncertainty into a next step.",
     "Instead of the buyer writing to each supplier from scratch, every open point is collected per supplier and drafted as one ready-to-send email.",
     "Pick a supplier to see its questions, then copy or download the email."),
    ("Comparison", "Compare", "A fair comparison starts here.",
     "Prices came per box, per 100 boxes and per kilogram, in rupees and in dollars. The product converts all of them to rupees per box, "
     "before delivery, so the quotes can finally be compared fairly.",
     "Hover any price to see the supplier's original words. Green = read cleanly, amber = needs a check, red = missing."),
    ("Award", "Decide", "A recommendation you can defend.",
     "One supplier, or split the boxes across several? Only suppliers who passed the must-haves are considered, and each supplier's "
     "conditions (volume surcharges, discounts, minimum orders) are priced in before anything is compared.",
     "Switch between 'Single vendor' and 'Split across vendors' to see why splitting looks cheaper on paper but isn't."),
]
STORY_PAGES = [s[0] for s in STEPS]

VENDORS = {
    "V1": ("📊", "Excel spreadsheet", "V1_response.xlsx", [
        "Its own layout and its own product codes, not the buyer's.",
        "Prices assume it wins **all 30 sizes**. A separate sheet adds 3–9% if it gets less.",
        "On one line, quantity × rate doesn't equal the total it wrote."]),
    "V2": ("📄", "PDF quotation", "V2_response.pdf", [
        "Some sizes priced **per 100 boxes**, others per box.",
        "A 2% discount sits in a small-print footnote, only if the order is over ₹50 lakh.",
        "On line 20, 43,500 × ₹31.93 doesn't match the total it printed."]),
    "V3": ("📝", "Word letter", "V3_response.docx", [
        "Prices are written in sentences, not a table.",
        "Quoted **thinner paper** (120 GSM) than asked (150 GSM) on most sizes, which is why it looks cheap.",
        "Declined one size, never mentioned two others, and says 'yes' to certificates without attaching them."]),
    "V4": ("📷", "Phone photo of a price list", "V4_Meridian_RateCard.jpg", [
        "Shot at an angle; **glare hides several prices**.",
        "Prices in **US dollars**, some per box and some per kilogram.",
        "Didn't answer the questionnaire, so its must-haves can't be confirmed."]),
    "V5": ("✉️", "One-line email", "V5_response.txt", [
        "Two prices for 30 sizes: ₹42/kg for 5-ply and ₹38/kg for 3-ply.",
        "'Rest same as last year' leaves the 7-ply boxes with **no price at all**.",
        "No paper specification given."]),
}

REASONS = {
    None: "", "stored_as_text": "", "basis_per_100": "Priced per 100 boxes", "basis_per_kg": "Priced per kilogram",
    "currency_usd": "Priced in US dollars", "arith_mismatch": "Quantity × rate ≠ stated total", "ocr_unreadable": "Hidden by glare in the photo",
    "ocr_ambiguous_digit": "A digit could be read two ways", "spec_incomplete": "Paper spec not stated", "spec_mismatch_gsm": "Thinner paper than asked",
    "line_absent": "Size not mentioned", "declined_capability": "Supplier can't make this size", "reference_unresolved": "'Same as last year': no price given",
}
STATE_LABELS = {"extracted": "✓ Read", "needs_review": "⚠ Check", "missing": "✗ Missing", "not_quoted": "– Not quoted"}
GATES = {"G1": "Packaging-safety certificate (BRCGS)", "G2": "In-house box strength testing", "G3": "Sustainable-paper certificate (FSC)"}
EVIDENCE = {"attachment": "certificate attached", "vendor_master": "on the buyer's supplier file", "response": "claimed, nothing attached"}

CSS = """
<style>
.st-steps {display:flex;gap:6px;flex-wrap:wrap;margin:0 0 1.1rem}
.st-steps span {font-size:.78rem;padding:5px 11px;border-radius:999px;border:1px solid rgba(128,128,128,.3);opacity:.7}
.st-steps span.done {opacity:.9}
.st-steps span.now {background:#b84925;border-color:#b84925;color:#fff;opacity:1;font-weight:700}
.st-kicker {font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:#b84925;font-weight:700;margin:0}
.st-what {font-size:1.08rem;line-height:1.6;max-width:52rem;margin:.2rem 0 .8rem}
.st-look {border-left:4px solid #b84925;padding:.45rem .9rem;background:rgba(184,73,37,.06);border-radius:0 8px 8px 0;font-size:.95rem;max-width:52rem}
.st-tricky {border:1px solid rgba(217,119,6,.45);background:rgba(217,119,6,.08);border-radius:10px;padding:.7rem 1rem;margin-bottom:.8rem}
.st-tricky b.h {display:block;font-size:.78rem;letter-spacing:.08em;text-transform:uppercase;color:#b45309;margin-bottom:.25rem}
.st-doc {border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:1rem 1.2rem;max-height:520px;overflow:auto;white-space:pre-wrap;
 font-family:Georgia,serif;font-size:.92rem;line-height:1.55;background:rgba(128,128,128,.05)}
.st-mail {border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:1rem 1.2rem;white-space:pre-wrap;font-size:.95rem;line-height:1.6;background:rgba(128,128,128,.05)}
</style>
"""


def go(page: str) -> None:
    st.session_state["page"] = page


def header(page: str) -> None:
    """Progress strip, step title, what is happening and what to look at."""
    i = STORY_PAGES.index(page)
    _, label, title, what, look = STEPS[i]
    chips = "".join(f'<span class="{"now" if j == i else "done" if j < i else ""}">{"✓ " if j < i else ""}{j + 1}. {escape(s[1])}</span>'
                    for j, s in enumerate(STEPS))
    st.markdown(CSS + f'<div class="st-steps">{chips}</div><p class="st-kicker">Step {i + 1} of {len(STEPS)} · {escape(label)}</p>',
                unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<p class="st-what">{escape(what)}</p><div class="st-look"><b>Explore this step:</b> {escape(look)}</div>', unsafe_allow_html=True)
    st.write("")


def footer(page: str) -> None:
    i = STORY_PAGES.index(page)
    st.divider()
    back, _, nxt = st.columns([1, 2, 1])
    if i > 0:
        back.button(f"← {STEPS[i - 1][1]}", key=f"back_{page}", on_click=go, args=(STEPS[i - 1][0],), use_container_width=True)
    if i + 1 < len(STEPS):
        nxt.button(f"Next: {STEPS[i + 1][1]} →", key=f"next_{page}", type="primary", on_click=go, args=(STEPS[i + 1][0],), use_container_width=True)
    else:
        nxt.button("How well does it work? →", key="next_evals", type="primary", on_click=go, args=("Evals",), use_container_width=True)


def _rows(db: Path, sql: str, params: tuple = ()) -> list[dict]:
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(sql, params)]
    except (sqlite3.Error, ValueError):
        return []


def _preview(vendor_id: str) -> None:
    icon, kind, name, _ = VENDORS[vendor_id]
    path = ARTIFACTS / name
    st.markdown(f"**{icon} What the supplier sent** · `{name}`")
    try:
        if vendor_id == "V1":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            for tab, ws in zip(st.tabs([ws.title for ws in wb.worksheets]), wb.worksheets):
                rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
                rows = [r for r in rows if any(r)]
                width = max(map(len, rows), default=0)
                tab.dataframe([dict(zip([chr(65 + k) for k in range(width)], r)) for r in rows], hide_index=True, height=480)
        elif vendor_id == "V2":
            pages = sorted((ARTIFACTS / "previews").glob("V2_p*.png"))
            if not pages:
                raise FileNotFoundError(name)
            for tab, img in zip(st.tabs([f"Page {k}" for k in range(1, len(pages) + 1)]), pages):
                tab.image(str(img), use_container_width=True)
        elif vendor_id == "V3":
            text = (ARTIFACTS / "previews" / "V3_response.txt").read_text(encoding="utf-8")
            st.markdown(f'<div class="st-doc">{escape(text)}</div>', unsafe_allow_html=True)
        elif vendor_id == "V4":
            st.image(str(path), use_container_width=True)
        else:
            st.markdown(f'<div class="st-mail">{escape(path.read_text(encoding="utf-8"))}</div>', unsafe_allow_html=True)
    except (OSError, ValueError, KeyError):
        st.caption("The original file is not included in this deployment.")


def render_replies(db: Path) -> None:
    header("Replies")
    vendors = _rows(db, "SELECT vendor_id,name FROM vendors ORDER BY vendor_id")
    if not vendors:
        st.info("No vendor replies recorded yet.")
        footer("Replies")
        return
    lines = {r["line_no"]: r for r in _rows(db, "SELECT line_no,ply,length_mm,width_mm,height_mm FROM rfx_lines")}
    fields = _rows(db, """SELECT s.vendor_id,b.rfx_line_no,b.value,b.unit,b.state,b.reason_code,b.snippet,p.inr_per_piece
        FROM bid_fields b JOIN submissions s USING(submission_id)
        LEFT JOIN norm_prices p ON p.field_id=b.field_id
        WHERE b.field_name='unit_price' ORDER BY s.vendor_id,b.rfx_line_no""") or _rows(db, """SELECT s.vendor_id,b.rfx_line_no,b.value,b.unit,b.state,
        b.reason_code,b.snippet,NULL AS inr_per_piece FROM bid_fields b JOIN submissions s USING(submission_id) WHERE b.field_name='unit_price'""")
    tabs = st.tabs([f"{VENDORS.get(v['vendor_id'], ('📎',))[0]} {v['name']}" for v in vendors])
    for tab, v in zip(tabs, vendors):
        vid = v["vendor_id"]
        with tab:
            icon, kind, _, tricky = VENDORS.get(vid, ("📎", "File", "", []))
            st.markdown(f'<div class="st-tricky"><b class="h">Why this {escape(kind.lower())} is hard to read</b>'
                        + "".join(f"<div>• {fmt_md(t)}</div>" for t in tricky) + "</div>", unsafe_allow_html=True)
            left, right = st.columns([5, 6], gap="large")
            with left:
                _preview(vid)
            with right:
                mine = [f for f in fields if f["vendor_id"] == vid]
                clean = sum(f["state"] == "extracted" for f in mine)
                st.markdown(f"**🔎 What the product read** · {clean} of {len(mine)} prices read cleanly")
                st.dataframe([{
                    "Line": f["rfx_line_no"],
                    "Supplier wrote": f["snippet"] or "",
                    "₹ per box": "—" if f["inr_per_piece"] is None else f"₹{f['inr_per_piece']:.2f}",
                    "Status": STATE_LABELS.get(f["state"], f["state"]) + (f" · {REASONS.get(f['reason_code'], f['reason_code'])}" if REASONS.get(f["reason_code"], f["reason_code"]) else ""),
                    "Read as": "—" if f["value"] is None else f"{f['value']:g} {f['unit'] or ''}",
                    "Box": (lambda l: f"{l['ply']}-ply {l['length_mm']}×{l['width_mm']}×{l['height_mm']}" if l else "")(lines.get(f["rfx_line_no"])),
                } for f in mine], hide_index=True, height=480)
                st.caption("'₹ per box' is the price after converting units and currency, so all suppliers can be compared on step 5.")
    footer("Replies")


def fmt_md(text: str) -> str:
    """Escape, then turn **bold** into <b>."""
    parts = escape(text).split("**")
    return "".join(f"<b>{p}</b>" if k % 2 else p for k, p in enumerate(parts))


def render_gates(db: Path) -> None:
    """Who passes the three must-haves, with the evidence each pass rests on."""
    st.subheader("Must-haves: who can be awarded at all?")
    rows = _rows(db, "SELECT vendor_id,gate_code,gate_status,evidence_source FROM questionnaire_answers WHERE is_gate=1 AND gate_status IS NOT NULL")
    vendors = _rows(db, "SELECT vendor_id,name FROM vendors ORDER BY vendor_id")
    if not rows or not vendors:
        st.caption("Must-have checks have not been run yet.")
        return
    detail = {}
    for r in rows:
        detail.setdefault(r["vendor_id"], {})[r["gate_code"]] = r
    table = []
    for v in vendors:
        gates = detail.get(v["vendor_id"])
        row = {"Supplier": v["name"]}
        for code, label in GATES.items():
            g = (gates or {}).get(code)
            row[label] = "No answer given" if not g else ("✓ " if g["gate_status"] == "pass" else "✗ ") + EVIDENCE.get(g["evidence_source"], g["evidence_source"] or "")
        row["Can be awarded?"] = "Yes" if gates and all(g["gate_status"] == "pass" for g in gates.values()) and len(gates) == len(GATES) else "No"
        table.append(row)
    st.dataframe(table, hide_index=True)
    st.caption("A 'yes' in a reply isn't enough: a must-have passes only with an attached certificate or a record on the buyer's own supplier file.")


def render_decision_path(db: Path) -> None:
    """The reasoning chain behind the award, in four plain steps, from the award engine's own output (app/award_words.py)."""
    import re
    from app import award_words
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            con.execute("PRAGMA query_only=ON")
            single, split = (award_words.build(con, s, "ex_freight") for s in ("single_vendor", "gated_split"))
    except Exception:                                            # a display panel must not take the page down
        return
    table = next((s[3] for s in single["sections"] if s[0] == "table"), [])
    callout = next((s for s in split["sections"] if s[0] == "saving_callout"), None)
    name = lambda r: re.sub(r"\s*\(V\d+\)$", "", r[0])
    ranked = [r for r in table if r[1] != "excluded"]
    gated = [name(r) for r in table if r[2] == "gates"]
    coverage = [name(r) for r in table if r[2] == "coverage"]
    if not ranked:
        return
    steps = [
        ("Who is allowed to win?", f"{len(table) - len(gated)} of {len(table)} suppliers pass the must-haves." + (f" Out: {', '.join(gated)}." if gated else "")),
        ("Who can supply everything?", f"{', '.join(coverage)} left some sizes unpriced, so can't take the whole order alone." if coverage else "Every qualified supplier priced every size."),
        ("Cheapest single supplier", f"{name(ranked[0])} at {ranked[0][1]}" + (f". Next: {name(ranked[1])} at {ranked[1][1]} ({ranked[1][2]})." if len(ranked) > 1 else ".")),
        ("Would splitting save more?", f"{callout[1]} on paper, but {callout[2]} once each supplier's volume conditions are applied. Below 1%: not worth managing several suppliers."
         if callout else "No split was cheaper."),
    ]
    st.subheader("How the decision was reached")
    for col, (i, (title, text)) in zip(st.columns(4), enumerate(steps, 1)):
        with col.container(border=True):
            st.markdown(f"**{i}. {title}**")
            st.caption(text)
    st.success(f"**Recommendation: award the whole order to {name(ranked[0])}.** Before signing, send it the open questions from step 4.")
    st.write("")
    st.markdown("**See the engine's working for each option:**")
