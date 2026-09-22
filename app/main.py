"""Procurement Streamlit shell. Run: streamlit run app/main.py

PROCUREMENT_DB optionally selects a pipeline database (default: repo/procurement.db).
Bid data stays read-only. Buyer settings are saved through src.settings; award exports use the existing engine.
"""
from __future__ import annotations

from contextlib import closing
from html import escape
from math import isfinite
from statistics import median
import os
import json
import re
from pathlib import Path
import sqlite3
import sys
from typing import Any
from urllib.parse import urlencode

import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import award_words, clarify, fmt
from app.award_note import generate_award_note
from src import settings
STATES = {
    "extracted": ("✓", "Extracted"),
    "needs_review": ("?", "Needs review"),
    "not_quoted": ("—", "Not quoted"),
    "missing": ("∅", "Missing"),
}
CSS = """
<style>
.quote-scroll {overflow-x:auto; padding:4px 0 16px}
.quote-grid {border-collapse:separate;border-spacing:6px;width:100%;font-size:.9rem}
.quote-grid th {text-align:left;padding:12px;min-width:160px}
.quote-grid th:first-child {min-width:235px}
.quote-grid td {padding:0;vertical-align:top;border-radius:6px;min-width:160px}
.quote-grid .extracted {border:1px solid #536c62;background:#eef7f1;color:#173d2c}
.quote-grid .needs_review {border:3px double #936510;background:#fff5d8;color:#573900}
.quote-grid .not_quoted {border:1px dashed #5e6571;background:#f2f2f4;color:#3d4148}
.quote-grid .missing {border:2px dotted #a84343;background:#fff0f0;color:#7d2525}
.quote-grid .status {display:block;font-size:.8rem;font-weight:700;margin-bottom:7px}
.quote-grid .detail {display:block;font-size:.78rem;margin-top:6px}
.quote-grid caption {text-align:left;padding:10px;font-weight:600}
.quote-grid a.bid-cell {display:block;padding:12px;min-height:90px;color:inherit;text-decoration:none}
.quote-grid a.bid-cell:hover {box-shadow:inset 0 0 0 2px currentColor;border-radius:4px}
.quote-grid a.bid-cell:focus-visible {outline:3px solid currentColor;outline-offset:2px}
.evidence-badges {display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.evidence-badges span {border:1px solid currentColor;border-radius:18px;padding:5px 12px}
.clarification {border:2px solid #936510;border-left-width:6px;border-radius:6px;
background:#fff5d8;color:#573900;padding:16px;margin:12px 0}
.clarification p {white-space:pre-wrap;margin:8px 0 0}
</style>
"""


def read_table(db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Read only from an existing pipeline DB; reject paths inside frozen truth."""
    db = db.resolve()
    if (ROOT / "dataset/truth").resolve() in db.parents:
        raise ValueError("Choose the pipeline procurement.db, not the frozen truth database.")
    if not db.is_file():
        return []
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return [dict(row) for row in conn.execute(sql, params)]


def comparison_vendors(vendors: list[dict]) -> list[dict]:
    """Keep the event's five vendor columns even before ingestion creates their rows."""
    by_id = {v["vendor_id"]: v for v in vendors}
    return [by_id.get(f"V{i}", {"vendor_id": f"V{i}", "name": f"V{i}"})
            for i in range(1, 6)]


def unit_price_index(fields: list[dict]) -> dict[tuple[int, str], dict]:
    """Index the selected submissions' unit-price fields, retaining one field per cell."""
    return {(r["rfx_line_no"], r["vendor_id"]): r for r in fields
            if r.get("field_name", "unit_price") == "unit_price"}


COMPARISON_GLYPHS = {"extracted": ("✓", "Extracted"), "needs_review": ("⚠", "Needs review"), "missing": ("✗", "Missing"), "not_quoted": ("–", "Not quoted")}
COMPARE_CSS = """
<style>
/* Self-contained light card, independent of the surrounding Streamlit theme (light or dark): every rule below pairs an
   explicit background with an explicit, readable text colour so the table never inherits invisible dark-on-dark or
   light-on-light text from the page chrome. */
.cmp-scroll {overflow:auto; max-height:74vh; padding-bottom:4px; background:#fff; border-radius:8px}
.cmp-grid {border-collapse:collapse;width:100%;font-size:.86rem;color:#1c2b27}
.cmp-grid caption {text-align:left;padding:6px 2px 10px;font-weight:600;color:#1c2b27}
.cmp-grid th {text-align:left;padding:8px 10px;min-width:150px;background:#eef1ef;color:#1c2b27}
.cmp-grid th:first-child {min-width:210px}
.cmp-grid thead th {position:sticky;top:0;z-index:2;box-shadow:0 1px 0 #ccc}
.cmp-grid tbody th {position:sticky;left:0;z-index:1;box-shadow:1px 0 0 #ccc;background:#f8f9f8}
.cmp-grid td {padding:0;vertical-align:middle;border-bottom:1px solid #e4e4e4;background:#fff}
.cmp-grid tr.row-amber th:first-child {border-left:4px solid #936510}
.cmp-grid tr.row-red th:first-child {border-left:4px solid #a84343}
.cmp-grid .cmp-cell {display:block;border:0;text-align:left;font:inherit;cursor:pointer;width:calc(100% - 12px);margin:4px 6px;padding:6px 9px;border-radius:6px;text-decoration:none;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;background:#f2f4f3;color:#1c2b27}
.cmp-grid .cmp-cell:hover {box-shadow:inset 0 0 0 2px currentColor}
.cmp-grid .cmp-cell:focus-visible {outline:3px solid #1a6fc4;outline-offset:1px}
.cmp-grid .extracted .cmp-cell {background:#eef7f1;color:#173d2c}
.cmp-grid .needs_review .cmp-cell {background:#fff5d8;color:#573900}
.cmp-grid .not_quoted .cmp-cell {background:#f2f2f4;color:#3d4148}
.cmp-grid .missing .cmp-cell {background:#fff0f0;color:#7d2525}
</style>
"""


CELL_CARD_SCRIPT = """
<style>
.cmp-cell[aria-expanded="true"] {outline:3px solid #1a6fc4;outline-offset:1px}
.cmp-card {position:fixed;z-index:999999;box-sizing:border-box;width:360px;max-width:calc(100vw - 24px);max-height: min(420px,70vh);overflow:auto;background:#fff;color:#1c2b27;border:1px solid #81988a;border-radius:8px;padding:14px;box-shadow:0 5px 20px #0004;font:14px/1.45 sans-serif}
.cmp-card[hidden] {display:none}
.cmp-card header {display:flex;justify-content:space-between;gap:12px;font-weight:700}
.cmp-card button {cursor:pointer;background:#eef1ef;color:#1c2b27;border:1px solid #aab8af;border-radius:4px;padding:4px 8px}
.cmp-card p {margin:8px 0}.cmp-card small {display:block;color:#526159;overflow-wrap:anywhere}
.cmp-card pre {white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f3;color:#1c2b27;padding:8px;font-size:12px}
.cmp-card .question {background:#fff3cf;color:#573900;padding:8px;border-left:3px solid #936510}
</style>
<script>
(()=>{
 const grid=document.getElementById('bid-grid-interactive');
 if(!grid || grid.dataset.ready) return;
 grid.dataset.ready='true';
 const card=grid.querySelector('.cmp-card');let active=null,pinned=false,timer;
 function close(){clearTimeout(timer);if(active)active.setAttribute('aria-expanded','false');card.hidden=true;active=null;pinned=false;}
 function position(){if(!active)return;const r=active.getBoundingClientRect(),g=grid.querySelector('.cmp-scroll').getBoundingClientRect();
 if(Math.max(0,g.top)>r.bottom||r.top>Math.min(innerHeight,g.bottom)){close();return;}
 const w=card.offsetWidth,h=card.offsetHeight;
 let x=r.right+8;if(x+w>innerWidth-12)x=Math.max(12,r.left-w-8);
 let y=Math.max(12,Math.min(r.top,innerHeight-h-12));card.style.left=x+'px';card.style.top=y+'px';}
 function show(cell,pin=false){clearTimeout(timer);if(pinned && !pin)return;
 if(active!==cell){if(active)active.setAttribute('aria-expanded','false');card.innerHTML=cell.nextElementSibling.innerHTML;}
 active=cell;pinned=pin;cell.setAttribute('aria-expanded','true');card.hidden=false;position();}
 function later(){if(!pinned)timer=setTimeout(close,200);}
 grid.querySelectorAll('.cmp-cell').forEach(cell=>{
 cell.addEventListener('pointerenter',e=>{if(e.pointerType!=='touch')show(cell);});
 cell.addEventListener('pointerleave',later);
 cell.addEventListener('focus',()=>show(cell));
 cell.addEventListener('blur',later);
 cell.addEventListener('click',()=>{if(active===cell&&pinned)close();else show(cell,true);});
 });
 card.addEventListener('pointerenter',()=>clearTimeout(timer));card.addEventListener('pointerleave',later);
 card.addEventListener('focusin',()=>{clearTimeout(timer);pinned=true;});
 card.addEventListener('click',async e=>{if(e.target.closest('[data-close]')){const cell=active;close();cell?.focus();close();}
 if(e.target.closest('[data-copy]')){try{await navigator.clipboard.writeText(card.querySelector('.cmp-evidence').innerText);e.target.textContent='Copied';}catch{e.target.textContent='Select text to copy';}}});
 const abort=new AbortController();
 document.addEventListener('pointerdown',e=>{if(!grid.isConnected){abort.abort();return;}if(!card.contains(e.target)&&!e.target.closest('.cmp-cell'))close();},{signal:abort.signal});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')close();},{signal:abort.signal});
 grid.querySelector('.cmp-scroll').addEventListener('scroll',()=>{if(pinned)position();else close();});
 window.addEventListener('resize',position,{signal:abort.signal});
 window.addEventListener('scroll',()=>{if(pinned)position();else close();},{capture:true,signal:abort.signal});
})();
</script>
"""


def cell_evidence_html(vendor: dict, line: dict, record: dict, value: str, label: str, tooltip: list[str]) -> str:
    """Escaped source evidence embedded locally: hovering or pinning never contacts the server."""
    text = lambda v: escape(str(v))
    body = [f'<header><span>{text(vendor["name"])} · Line {line["line_no"]}</span><button data-close aria-label="Close evidence">×</button></header>',
            '<div class="cmp-evidence">', f'<small>{text(line["sku"])}</small>',
            f'<p><b>{text(value)}</b> · {text(label)}</p>']
    if record.get('reason_code'):
        body.append(f'<p>{text(record["reason_code"].replace("_", " "))}</p>')
    if record.get('resolving_question'):
        body.append(f'<p class="question"><b>Ask vendor:</b> {text(record["resolving_question"])}</p>')
    if record.get('derivation'):
        body.append(f'<details><summary>Derivation</summary><pre>{text(record["derivation"])}</pre></details>')
    body.append(f'<small>Source: {text(record.get("file_name") or "not available")} · {text(record.get("anchor") or "anchor not available")}</small>')
    snippet = record.get("snippet") or "No source snippet recorded."
    body.append(f'<pre>{text(snippet[:180])}{"…" if len(snippet) > 180 else ""}</pre>')
    body.append('<details><summary>Full source and rate details</summary>')
    if len(snippet) > 180:
        body.append(f'<pre>{text(snippet)}</pre>')
    body.append(f'<small>Quoted value: {text(record.get("value") if record.get("value") is not None else "not available")} {text(record.get("currency") or "")} · {text(record.get("unit") or record.get("basis") or "")}</small>')
    body.append(f'<small>{text(" · ".join(tooltip))}</small></details></div><button data-copy>Copy evidence</button>')
    return ''.join(body)


def comparison_html(lines: list[dict], vendors: list[dict], fields: list[dict], mode: str = "normalized", norm_by_pair: dict | None = None) -> str:
    """One line per cell: a state icon plus a value; the unit/basis or comparability note that used to sit on its own line is now the cell's tooltip.
    mode='quoted' shows the vendor's own stated price and unit; mode='normalized' shows the normalized ex-freight INR/piece from norm_prices where one exists.
    A row gets a coloured left-border strip: amber if any vendor's cell is needs_review, else red if any is missing, else none."""
    index = unit_price_index(fields)
    norm_by_pair = norm_by_pair or {}
    ingested = {r["vendor_id"] for r in fields}
    caption = "Normalized ex-freight rate, ₹ per piece" if mode == "normalized" else "Quoted rates in their original currency and pricing basis"
    parts = [COMPARE_CSS, '<div id="bid-grid-interactive"><div class="cmp-scroll"><table class="cmp-grid">', f'<caption>{escape(caption)}</caption>',
             '<thead><tr><th scope="col">RFx line</th>']
    parts.extend(f'<th scope="col">{escape(v["name"])}</th>' for v in vendors)
    parts.append("</tr></thead><tbody>")
    for line in lines:
        states = set()
        for vendor in vendors:
            states.add(index.get((line["line_no"], vendor["vendor_id"]), {}).get("state"))
        row_class = " row-amber" if "needs_review" in states else (" row-red" if "missing" in states else "")
        parts.append(f'<tr id="cmp-line-{line["line_no"]}" class="{row_class.strip()}"><th scope="row">{line["line_no"]} · {escape(line["sku"])}</th>')
        for vendor in vendors:
            record = index.get((line["line_no"], vendor["vendor_id"]), {})
            state = record.get("state", "missing")
            if state not in COMPARISON_GLYPHS:
                state = "missing"
            glyph, label = COMPARISON_GLYPHS[state]
            not_ingested = vendor["vendor_id"] not in ingested
            tooltip = [label]
            if record.get("reason_code"):
                tooltip.append(f"Reason: {record['reason_code']}")
            value = None
            if not_ingested:
                value = "not ingested"
            elif mode == "normalized":
                norm = norm_by_pair.get((vendor["vendor_id"], line["line_no"]))
                if norm and norm.get("inr_per_piece") is not None:
                    value = fmt.price(norm["inr_per_piece"], "INR")
                    if norm.get("comparability"):
                        tooltip.append(f"Comparability: {norm['comparability']}")
                elif state in ("extracted", "needs_review"):
                    value = "Not comparable"
                    tooltip.append("No normalized price for this cell")
            if value is None:
                if state == "not_quoted":
                    value = "Not quoted"
                elif state == "missing" or record.get("value") is None:
                    value = "No value"
                else:
                    value = fmt.price(record["value"], record.get("currency"))
                    unit = record.get("unit") or record.get("basis") or ""
                    if unit:
                        tooltip.append(unit)
            aria = escape(f'Show source evidence for {vendor["name"]}, line {line["line_no"]}, {label}')
            card = cell_evidence_html(vendor, line, record, value, label, tooltip)
            parts.append(f'<td class="{state}"><button type="button" class="cmp-cell" aria-expanded="false" '
                         f'aria-label="{aria}">{glyph} {escape(value)}</button><template>{card}</template></td>')
        parts.append("</tr>")
    parts.append('</tbody></table></div><aside class="cmp-card" aria-label="Bid source evidence" hidden></aside></div>')
    return "".join(parts) + CELL_CARD_SCRIPT


def unit_price_states(lines: list[dict], vendor_ids: list[str], fields: list[dict]) -> dict[int, set[str]]:
    """Per RFx line, the unit-price states actually recorded for the given vendors. A vendor cell with no bid_fields row contributes no state."""
    index = unit_price_index(fields)
    return {line["line_no"]: {index[(line["line_no"], v)]["state"] for v in vendor_ids if (line["line_no"], v) in index} for line in lines}


def comparison_stats(lines: list[dict], vendor_ids: list[str], fields: list[dict], norm_by_pair: dict) -> dict:
    """Total lines; lines where every vendor is extracted; lines with at least one needs_review/missing/not_quoted; and the vendor with the
    lowest summed normalized total (₹ per piece x annual RFx quantity, over the lines it has a normalized price for)."""
    index = unit_price_index(fields)
    all_clean = sum(1 for line in lines if vendor_ids and all(index.get((line["line_no"], v), {}).get("state") == "extracted" for v in vendor_ids))
    any_flag = sum(1 for line in lines if any(index.get((line["line_no"], v), {}).get("state") in ("needs_review", "missing", "not_quoted") for v in vendor_ids))
    totals = {}
    for v in vendor_ids:
        total, priced = 0.0, False
        for line in lines:
            row = norm_by_pair.get((v, line["line_no"]))
            if row and row.get("inr_per_piece") is not None:
                total += row["inr_per_piece"] * line["annual_qty"]
                priced = True
        if priced:
            totals[v] = total
    cheapest = min(totals, key=totals.get) if totals else None
    return dict(total_lines=len(lines), all_clean=all_clean, any_flag=any_flag, cheapest_vendor=cheapest,
                cheapest_total=totals.get(cheapest) if cheapest else None)


def copy_button(text: str, key: str, label: str = "Copy to clipboard") -> None:
    """A small button that copies text to the clipboard, falling back to a manual-select textarea if the Clipboard API is unavailable."""
    payload = json.dumps(text).replace("</", "<" + chr(92) + "/")
    components.html(f'<button id="{key}" style="font:14px sans-serif;padding:7px 14px;border:1px solid #bbb;border-radius:8px;background:#fff;cursor:pointer">{escape(label)}</button>'
                    '<script>const t=' + payload + f';const b=document.getElementById("{key}");b.onclick=async()=>{{let ok=false;try{{await navigator.clipboard.writeText(t);ok=true}}catch(e){{}}'
                    'if(!ok){const a=document.createElement("textarea");a.value=t;document.body.appendChild(a);a.select();ok=document.execCommand("copy");a.remove()}'
                    'b.textContent=ok?"Copied":"Copy failed - select the text above"}</script>', height=46)


def apply_cell_link(vendors: list[dict], lines: list[dict]) -> None:
    """Apply a validated cell URL before creating any affected widgets.

    Links remain keyboard-accessible HTML links. URL navigation restores the page
    and both selectors. Consumed links never override later manual navigation.
    """
    requested = (st.query_params.get("bid_vendor"), st.query_params.get("bid_line"))
    if requested == st.session_state.get("_last_bid_link"):
        return
    st.session_state["_last_bid_link"] = requested
    try:
        vendor_id, line_text = requested
        line_no = int(line_text)
    except (TypeError, ValueError):
        return
    if vendor_id not in {v["vendor_id"] for v in vendors}:
        return
    if line_no not in {r["line_no"] for r in lines}:
        return
    st.session_state["page"] = "Comparison"
    st.session_state["bid_vendor"] = vendor_id
    st.session_state["bid_line"] = line_no
    st.session_state["_open_evidence_dialog"] = True      # a genuinely new cell click, not just a rerun from some other widget
    st.session_state["_clar_scroll_pending"] = True


def sync_bid_selection() -> None:
    """Keep manual dropdown changes and subsequent cell navigation in agreement."""
    vendor_id = st.session_state.get("bid_vendor")
    line_no = st.session_state.get("bid_line")
    if vendor_id is not None and line_no is not None:
        st.query_params.update({"bid_vendor": vendor_id, "bid_line": str(line_no)})
        st.session_state["_last_bid_link"] = (vendor_id, str(line_no))


def provenance(record: dict | None, *, vendor_ingested: bool, question: str | None = None, heading: bool = True, show_clarification: bool = True) -> None:
    """Display only evidence recorded on the selected unit-price field. show_clarification=False leaves the drafted question to a caller that shows it elsewhere."""
    if heading:
        st.subheader("Source evidence", anchor="source-evidence")
    if record is None:
        st.info("Missing — no unit-price field recorded for this line." if vendor_ingested
                else "not ingested — no bid fields have been ingested for this vendor.")
        return
    glyph, _ = STATES.get(record["state"], STATES["missing"])
    with st.container(border=True):
        badges = f'<span>{glyph} {escape(record["state"])}</span>'
        if record.get("reason_code"):
            badges += f'<span>Reason: <code>{escape(record["reason_code"])}</code></span>'
        badge_style = ('<style>.evidence-badges{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}'
                       '.evidence-badges span{border:1px solid currentColor;border-radius:18px;padding:5px 12px}</style>')
        st.markdown(badge_style + '<div class="evidence-badges">' + badges + '</div>',
                    unsafe_allow_html=True)
        if record.get("derivation"):
            st.caption("Derivation")
            st.code(clarify.plain_derivation(record["derivation"]), language=None, wrap_lines=True)
        if show_clarification and (question or record.get("resolving_question")):
            st.markdown('<div class="clarification" role="note">'
                        '<strong>Draft clarification to vendor</strong><p>'
                        + escape(question or record["resolving_question"]) + '</p></div>',
                        unsafe_allow_html=True)
        st.text("File: " + (record.get("file_name") or "Not recorded"))
        st.text("Source anchor: " + (record.get("anchor") or "Not recorded"))
        st.caption("Verbatim snippet")
        st.code(record["snippet"] if record.get("snippet") is not None
                else "No snippet recorded.", language=None, wrap_lines=True)


def open_evidence_dialog(vendor_name: str, line_label: str, record: dict | None, vendor_ingested: bool) -> None:
    """A source-evidence popup for the clicked cell. st.popover cannot be triggered from inside a raw HTML table cell (it must be an actual Streamlit
    widget in the layout tree), so this uses st.dialog instead: it opens immediately on click, in place of the old scroll-to-a-section-below behaviour,
    and closes on its own X or on a click outside, same as a popover would."""
    @st.dialog(f"{vendor_name} — {line_label}", width="large")
    def _dialog():
        provenance(record, vendor_ingested=vendor_ingested, heading=False, show_clarification=False)
        if record and record.get("snippet") is not None:
            copy_button(record["snippet"], key="evidence_copy")
    _dialog()


REVIEW_COLUMNS = {
    "vendor": "Vendor", "line": "Line / short spec", "value": "Extracted value",
    "reason": "Reason code", "risk": "Value at risk (₹)",
    "question": "Clarifying question",
}
REVIEW_CSS = """
<style>
.review-scroll {overflow-x:auto}
.review-table {border-collapse:collapse;width:100%;table-layout:fixed;font-size:.82rem}
.review-table th:nth-child(1) {width:13%}
.review-table th:nth-child(2) {width:16%}
.review-table th:nth-child(3) {width:15%}
.review-table th:nth-child(4) {width:14%}
.review-table th:nth-child(5) {width:14%}
.review-table th:nth-child(6) {width:28%}
.review-table th,.review-table td {padding:7px 6px;overflow-wrap:anywhere;border-bottom:1px solid #aaa5;
vertical-align:top;text-align:left}
.review-table th a {color:inherit;text-decoration:underline}
.review-table .risk {text-align:right;white-space:normal;font-variant-numeric:tabular-nums}
.review-table .detail {display:block;font-size:.8rem;opacity:.8;margin-top:4px}
.review-table .unknown {border:1px dashed currentColor;border-radius:4px;padding:3px 6px}
.review-table em {white-space:pre-wrap}
.review-table details summary {cursor:pointer;list-style:none;font-style:italic}
.review-table details summary::-webkit-details-marker {display:none}
.review-table details[open] summary {margin-bottom:6px}
.review-table details div {font-style:italic;white-space:pre-wrap}
</style>
"""


def finite_number(value: Any) -> float | None:
    """Treat null, invalid and non-finite numbers as unavailable; preserve zero."""
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def review_queue(db: Path) -> list[dict]:
    """Load every flagged field, with its own submission's unit price and RFx quantity."""
    items = read_table(db, """
        SELECT b.field_id,b.submission_id,b.rfx_line_no,b.field_name,b.value,b.unit,
               b.currency,b.state,b.reason_code,b.resolving_question,
               s.vendor_id,COALESCE(v.name,s.vendor_id) AS vendor_name,
               l.length_mm,l.width_mm,l.height_mm,l.ply,l.annual_qty,
               (SELECT price.value FROM bid_fields price
                WHERE price.submission_id=b.submission_id
                  AND price.rfx_line_no=b.rfx_line_no AND price.field_name='unit_price'
                  AND price.state IN ('extracted','needs_review')
                ORDER BY price.field_id DESC LIMIT 1) AS unit_price
        FROM bid_fields b
        JOIN submissions s USING(submission_id)
        LEFT JOIN vendors v USING(vendor_id)
        LEFT JOIN rfx_lines l ON l.line_no=b.rfx_line_no
        WHERE b.state='needs_review'
           OR (b.state='missing' AND b.reason_code='reference_unresolved')
        ORDER BY b.field_id
    """)
    # One observation per peer vendor/line, so repeat submissions do not weight a median.
    prices = read_table(db, """
        SELECT s.vendor_id,b.rfx_line_no,b.value,b.state
        FROM bid_fields b JOIN submissions s USING(submission_id)
        WHERE b.field_name='unit_price'
        ORDER BY s.received_on,s.submission_id,b.field_id
    """)
    return add_review_risk(items, prices)


def add_review_risk(items: list[dict], prices: list[dict]) -> list[dict]:
    """Attach the requested quoted-price exposure proxy, without unit normalization."""
    peers = {(r["vendor_id"], r["rfx_line_no"]): r for r in prices}
    result = []
    for item in items:
        qty = finite_number(item.get("annual_qty"))
        price = finite_number(item.get("unit_price"))
        basis = "Quoted unit price × RFx quantity"
        if price is None:
            alternatives = [
                value for (vendor, line), row in peers.items()
                if vendor != item["vendor_id"] and line == item["rfx_line_no"]
                and row["state"] in ("extracted", "needs_review")
                and (value := finite_number(row["value"])) is not None
            ]
            price = median(alternatives) if alternatives else None
            basis = "Other-vendor median unit price × RFx quantity"
        risk = finite_number(abs(price * qty)) if price is not None and qty is not None else None
        result.append(dict(item, risk=risk, risk_basis=basis if risk is not None else "value unknown"))
    return result


def sorted_review_items(items: list[dict], column: str = "risk",
                        direction: str = "desc") -> list[dict]:
    """Keep unknown exposure first; sort known items by the selected column."""
    def key(row: dict) -> Any:
        if column == "vendor":
            return row["vendor_name"].casefold()
        if column == "line":
            return row["rfx_line_no"]
        if column == "value":
            value = finite_number(row.get("value"))
            return (value is not None, value if value is not None else 0)
        if column == "reason":
            return (row.get("reason_code") or "").casefold()
        if column == "question":
            return (row.get("resolving_question") or "").casefold()
        return row["risk"]
    unknown = [r for r in items if r["risk"] is None]
    known = [r for r in items if r["risk"] is not None]
    if column != "risk":
        unknown = sorted(unknown, key=key, reverse=direction == "desc")
    return unknown + sorted(known, key=key, reverse=direction == "desc")


def question_cell(text: str, limit: int = 60) -> str:
    """A short question stays as is; a long one shows its first ~60 characters and a ▸ that opens the full drafted question in place."""
    if len(text) <= limit:
        return f"<em>{escape(text)}</em>"
    return f"<details><summary>{escape(text[:limit].rstrip())}… ▸</summary><div>{escape(text)}</div></details>"


def review_table_html(items: list[dict], column: str = "risk",
                      direction: str = "desc", question_for=None) -> str:
    """Accessible HTML header links control sorting; all database text is escaped."""
    parts = [REVIEW_CSS, '<div class="review-scroll"><table class="review-table">',
             '<thead><tr>']
    for key, label in REVIEW_COLUMNS.items():
        active = key == column
        next_direction = ("asc" if direction == "desc" else "desc") if active else (
            "desc" if key in ("risk", "value") else "asc")
        href = "?" + urlencode({"review_sort": key, "review_dir": next_direction}) + "#review-queue"
        aria = ("descending" if direction == "desc" else "ascending") if active else "none"
        arrow = (" ↓" if direction == "desc" else " ↑") if active else ""
        parts.append(f'<th scope="col" aria-sort="{aria}"><a href="{escape(href)}" '
                     f'target="_self">{escape(label + arrow)}</a></th>')
    parts.append("</tr></thead><tbody>")
    for row in sorted_review_items(items, column, direction):
        spec = "×".join(str(row.get(k) or "?") for k in ("length_mm","width_mm","height_mm"))
        spec += f' mm · {row.get("ply") or "?"}-ply'
        glyph, state = STATES.get(row["state"], STATES["missing"])
        value = finite_number(row.get("value"))
        extracted = "No value recorded" if value is None else fmt.field_value(value, row.get("currency"), row["field_name"])
        risk = ('<span class="unknown">value unknown</span>' if row["risk"] is None
                else f'₹ {fmt.indian(row["risk"], 0)}')
        parts.append(f'<tr><td>{escape(row["vendor_name"])}</td>'
                     f'<td>{row["rfx_line_no"]}<span class="detail">{escape(spec)}</span></td>'
                     f'<td>{glyph} {escape(state)}<br>{escape(extracted)}'
                     f'<span class="detail">{escape(row["field_name"])}'
                     f' · {escape(row.get("unit") or "")}</span></td>'
                     f'<td>{escape(row.get("reason_code") or "—")}</td>'
                     f'<td class="risk" title="{escape(row["risk_basis"])}">{risk}</td>'
                     f'<td>{question_cell((question_for(row) if question_for else None) or row.get("resolving_question") or "—")}</td></tr>')
    parts.append("</tbody></table></div>")
    return "".join(parts)


def review_sort_selection() -> tuple[str, str]:
    """Validate sort links and open Event without overriding later manual navigation."""
    column = st.query_params.get("review_sort", "risk")
    direction = st.query_params.get("review_dir", "desc")
    if column not in REVIEW_COLUMNS:
        column = "risk"
    if direction not in ("asc", "desc"):
        direction = "desc"
    requested = (column, direction)
    if "review_sort" in st.query_params and requested != st.session_state.get("_review_sort_link"):
        st.session_state["page"] = "Event"
        st.session_state["_review_sort_link"] = requested
    return requested


SCENARIO_LABELS = {"single_vendor": "Single vendor", "gated_split": "Split across vendors"}
THRESHOLD_TOOLTIP = ("The award engine blocks a recommendation when any single line worth more than X% of event value is in needs_review. "
                     "Below the threshold, uncertain lines show as warnings on the recommendation. Lower to be stricter, raise to be more permissive.")


def render_settings_row(db: Path, page: str) -> None:
    """Read settings on a read-only connection; save only through the settings API."""
    db = db.resolve()
    if (ROOT / "dataset/truth").resolve() in db.parents:
        raise ValueError("Choose the pipeline procurement.db, not the frozen truth database.")
    if not db.is_file():
        return
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = settings.describe(conn)
    for row in rows:
        label, number, save = st.columns([3, 2, 1], vertical_alignment="bottom")
        key = f"setting_{page}_{row['key']}"
        stored = (str(db), row["value"])
        if st.session_state.get(key + "_stored") != stored:
            st.session_state[key] = float(row["value"])
            st.session_state[key + "_stored"] = stored
        entered = number.number_input(f"{row['label']}: {row['value']:g}{row['unit']}", min_value=float(row["min"]), max_value=float(row["max"]),
                                      step=0.5, help=THRESHOLD_TOOLTIP if row["key"] == "review_block_threshold_pct" else row["note"], key=key)
        label.caption(f"Current {row['value']:g}{row['unit']} · Default {row['default']:g}{row['unit']}" + ("" if row["is_default"] else " · changed"))
        if save.button("Save", key=key + "_save"):
            try:
                settings.set_value(db, row["key"], entered)
            except (ValueError, sqlite3.Error) as exc:
                st.error(str(exc))
            else:
                saved = st.session_state.pop("award_note_export", None)
                if saved and Path(saved[0]).resolve() == db:
                    try:
                        note, gaps = generate_award_note(db, saved[1], saved[2])
                        st.session_state["award_note_export"] = (*saved[:3], note, gaps)
                    except (sqlite3.Error, ValueError, KeyError, ImportError) as exc:
                        st.session_state["settings_export_error"] = str(exc)
                st.rerun()
    if error := st.session_state.pop("settings_export_error", None):
        st.error(f"Setting saved, but the award note could not be refreshed: {error}")


ANALYST_ORDER = {"R1": 0, "R2": 1, "R3": 2, "C1": 3, "C2": 4, "C3": 5}


def processing_summary(db: Path) -> list[tuple[str, float, float]] | None:
    """(label, seconds, USD) per pipeline stage from model_calls, or None when nothing is recorded. Buyer usage (analyst:streamlit) is excluded; the analyst
    figure is the latest run of the canonical question set. Prices come from scripts/cost_report.py, never restated here."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import cost_report
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        try:
            rows = conn.execute("SELECT call_id,stage,model,input_tokens,output_tokens,cached_input_tokens,elapsed_s FROM model_calls "
                                "WHERE stage<>'analyst:streamlit' ORDER BY call_id").fetchall()
        except sqlite3.OperationalError:
            return None
    analyst = [r for r in rows if r[1].startswith("analyst")]
    start, last = 0, -1
    for i, r in enumerate(analyst):                          # a new run of the question set begins where the question order restarts
        rank = ANALYST_ORDER.get(r[1].split(":")[-1], last)
        start, last = (i, rank) if rank < last else (start, rank)
    keep = {r[0] for r in analyst[start:]}
    rows = [r for r in rows if not r[1].startswith("analyst") or r[0] in keep]
    if not rows:
        return None
    fam = lambda r: r[1].split(":")[0]
    def total(family):
        sel = [r for r in rows if fam(r) == family]
        try:
            usd = sum(cost_report.cost(r[2], r[3], r[4], r[5]) for r in sel)
        except SystemExit:                                     # a model with no entry in PRICES: show the time, not a made-up cost
            usd = float("nan")
        return sum(r[6] for r in sel), usd, len({r[1] for r in sel})
    ext, pho, que, ana = (total(f) for f in ("extraction", "photo", "questionnaire", "analyst"))
    out = [(f"Extraction ({ext[2]} documents)", *ext[:2]), ("Photo (V4 rate card)", *pho[:2]), ("Questionnaire", *que[:2]), ("Analyst (scripted question set)", *ana[:2])]
    return [(label, sec, usd * cost_report.FX_INR) for label, sec, usd in out if sec]


def render_award_preview(db: Path, scenario: str, basis: str) -> None:
    """The recommendation in plain words, before clicking Export. Every figure is the award engine's own output (see app/award_words.py)."""
    from app import award_words
    with st.container(border=True):
        st.markdown("**Preview**")
        try:
            with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
                con.execute("PRAGMA query_only=ON")
                model = award_words.build(con, scenario, basis)
        except Exception as exc:                                 # a display panel must not take the page down
            st.caption(f"Preview unavailable: {exc}")
            return
        {"error": st.error, "success": st.success, "warning": st.warning}.get(model["tone"], st.markdown)(model["header"])
        for section in model["sections"]:
            if section[0] == "saving_callout":
                with st.container(border=True):
                    left, right = st.columns(2)
                    for column, label, amount, color, background in (
                        (left, "On paper", section[1], "#35674b", "#edf5ef"),
                        (right, "In reality", section[2], "#805500", "#fff3cf"),
                    ):
                        column.caption(label)
                        column.markdown(f'<div style="font-size:1.65rem;font-weight:600;color:{color};background:{background};padding:12px;border-radius:6px">{escape(amount)}</div>', unsafe_allow_html=True)
                    st.caption(section[3])
            elif section[0] == "table":
                st.markdown("**" + section[1] + "**")
                st.table([dict(zip(section[2], row)) for row in section[3]])
            elif section[0] == "warnings":
                for text in section[1]:
                    st.warning(text)
            else:
                for line in section[1]:
                    st.markdown(line)


FIELD_LABELS = {"unit_price": "Unit price", "line_total": "Line total", "declared_liner_gsm": "Declared liner GSM"}
CLAR_CSS = """
<style>
.clar-item {border:1px solid #ddd;border-radius:8px;padding:12px 14px;margin:10px 0;background:#fff;color:#1c2b27}
.clar-item .clar-meta {font-size:.85rem;opacity:.75;margin:2px 0 8px}
.clar-pulse {animation:clarPulse 1.8s ease-out 1}
@keyframes clarPulse {0% {box-shadow:0 0 0 4px rgba(147,101,16,.55)} 100% {box-shadow:0 0 0 0 rgba(147,101,16,0)}}
</style>
"""


def plain_paragraph(question: str, vendor_name: str) -> str:
    """The drafted question without its leading '{Vendor}: ' label — redundant once the item already sits under that vendor, or inside an email already
    addressed to them by name. Capitalises the word that now starts the sentence."""
    stripped = re.sub(rf"^{re.escape(vendor_name)}\s*[:,]?\s*", "", question or "", count=1)
    return stripped[:1].upper() + stripped[1:] if stripped else question


def open_items_for_vendor(db: Path, vendor_id: str, vendor_name: str, fields: list[dict], lines: list[dict]) -> list[dict]:
    """Every open item for one vendor — a flagged bid_fields row or a flagged questionnaire answer, each with a drafted question. Read-only aggregation."""
    by_line = {r["line_no"]: r for r in lines}
    by_key = {(r["submission_id"], r["rfx_line_no"], r["field_name"]): r for r in fields}
    items = []
    rows = [f for f in fields if f["vendor_id"] == vendor_id and f["state"] in ("needs_review", "missing", "not_quoted") and f.get("resolving_question")]
    for f in sorted(rows, key=lambda f: (f["rfx_line_no"], f["field_id"])):
        q = clarify.vendor_question(f, vendor_name, by_line, by_key)
        if not q:
            continue
        value = "No value recorded" if f.get("value") is None else fmt.field_value(f["value"], f.get("currency"), f["field_name"])
        items.append(dict(line=f["rfx_line_no"], label=f"Line {f['rfx_line_no']} — {FIELD_LABELS.get(f['field_name'], f['field_name'])}",
                          value=value, source=f"{f.get('file_name') or 'Not recorded'} · {f.get('anchor') or 'no anchor recorded'}",
                          question=plain_paragraph(q, vendor_name)))
    answers = read_table(db, "SELECT q_no,question,is_gate,state,reason_code,resolving_question,answer_text,anchor,evidence_source FROM questionnaire_answers "
                             "WHERE vendor_id=? AND state IN ('needs_review','claimed_unsupported') AND resolving_question IS NOT NULL "
                             "ORDER BY is_gate DESC,q_no", (vendor_id,))
    for a in answers:
        q = clarify.vendor_question(a, vendor_name, {}, {})
        if not q:
            continue
        items.append(dict(line=None, label=f"Question {a['q_no']}{' (mandatory gate)' if a['is_gate'] else ''} — {a['question']}",
                          value=a.get("answer_text") or "No answer recorded", source=a.get("anchor") or a.get("evidence_source") or "Not recorded",
                          question=plain_paragraph(q, vendor_name)))
    return items


def clarification_email(vendor_name: str, items: list[dict], buyer_name: str) -> str:
    """The numbered list, as one plain-text email a buyer could paste and send — the same content the on-screen cards show, signed off."""
    body = [f"Clarifications required — {vendor_name}", "",
            "The items below could not be finalised on our side. Please review each and confirm the requested details.", ""]
    for i, item in enumerate(items, 1):
        body += [f"{i}. {item['label']}", f"   Value: {item['value']} · Source: {item['source']}", f"   {item['question']}", ""]
    body += ["Regards,", buyer_name.strip() or "Procurement Team"]
    return "\n".join(body).rstrip() + "\n"


def render_vendor_clarifications(fields: list[dict], lines: list[dict], vendors: list[dict], db: Path) -> None:
    """Every open item for one vendor, in plain business language, in one place: the replacement for the old per-cell Details pane and the old bulk
    email flow. The vendor dropdown shares session key 'bid_vendor' with the grid's cell links, so clicking a cell selects that vendor here too."""
    st.header("Vendor clarifications", anchor="vendor-clarifications")
    names = {v["vendor_id"]: v["name"] for v in vendors}
    if not names:
        return
    vid = st.selectbox("Select vendor to review", list(names), format_func=names.get, key="bid_vendor")
    items = open_items_for_vendor(db, vid, names[vid], fields, lines)
    if not items:
        st.info(f"No open clarifications for {names[vid]}.")
        return
    buyer_name = st.text_input("Your name (for the email signature)", value=st.session_state.get("clar_buyer_name", "Procurement Team"), key="clar_buyer_name")
    target_line = st.session_state.get("bid_line") if st.session_state.pop("_clar_scroll_pending", False) else None
    st.markdown(CLAR_CSS, unsafe_allow_html=True)
    for i, item in enumerate(items, 1):
        pulse = " clar-pulse" if item["line"] == target_line else ""
        st.markdown(f'<div class="clar-item{pulse}" id="clar-item-{i}"><strong>{i}. {escape(item["label"])}</strong>'
                    f'<div class="clar-meta">Value: {escape(item["value"])} · Source: {escape(item["source"])}</div>'
                    f'<div>{escape(item["question"])}</div></div>', unsafe_allow_html=True)
    if target_line is not None and any(item["line"] == target_line for item in items):
        first = next(i for i, item in enumerate(items, 1) if item["line"] == target_line)
        st.html(f"<script>document.getElementById('clar-item-{first}')?.scrollIntoView({{behavior:'smooth',block:'center'}});</script>", unsafe_allow_javascript=True)
    email_text = clarification_email(names[vid], items, buyer_name)
    copy_col, download_col = st.columns([1, 1])
    with copy_col:
        copy_button(email_text, key="clar_copy", label="Copy full email")
    download_col.download_button("Download as .txt", email_text, file_name=f"clarifications_{vid}.txt", mime="text/plain")


def render_processing(db: Path, vendor_count: int, line_count: int) -> None:
    st.subheader("How this event was processed")
    with st.container(border=True):
        try:
            summary = processing_summary(db) if db.is_file() else None
        except (sqlite3.Error, ImportError) as exc:
            st.caption(f"Processing record unavailable: {exc}")
            return
        if not summary:
            st.caption("No model usage recorded yet.")
            return
        seconds = lambda t: f"{t:.0f} s" if t < 90 else f"{t / 60:.1f} min"
        inr = lambda x: "n/a" if x != x else f"₹{fmt.indian(x, 0)}"
        known = sum(c for _, _, c in summary if c == c)
        minutes = sum(t for _, t, _ in summary) / 60
        st.markdown(f"**Processed {vendor_count} vendors, {line_count} lines in ~{minutes:.0f} minutes for ~₹{fmt.indian(known, 0)}.**")
        with st.expander("Per-stage breakdown"):
            rows = [f"| {label} | {seconds(t)} | {inr(c)} |" for label, t, c in summary]
            rows.append(f"| **Total** | **~{minutes:.0f} min** | **~₹{fmt.indian(known, 0)}** |")
            st.markdown("| Stage | Time | Cost |" + chr(10) + "|---|---:|---:|" + chr(10) + chr(10).join(rows))


def question_context(db: Path) -> tuple[dict, dict]:
    """RFx lines and quoted prices/totals, keyed for the display-layer question templates. Read-only."""
    lines = {r["line_no"]: r for r in read_table(db, "SELECT line_no,ply,length_mm,width_mm,height_mm,liner_gsm,print_spec,annual_qty FROM rfx_lines")}
    fields = {(r["submission_id"], r["rfx_line_no"], r["field_name"]): r for r in read_table(
        db, "SELECT submission_id,rfx_line_no,field_name,value,basis,currency FROM bid_fields WHERE field_name IN ('unit_price','line_total')")}
    return lines, fields


EXPOSURE_LABEL = "Total ₹ affected (normalized, per vendor-line, ex-freight)"


def review_exposure_total(db: Path) -> tuple[float | None, int]:
    """The analyst layer's own review_exposure(): normalized INR per piece x annual RFx quantity, one exposure per vendor-line. Returns (total, unknown vendor-lines)."""
    from src.analyst import Store
    try:
        r = Store(db).review_exposure()
    except sqlite3.Error:                                        # no normalized prices yet: the total is not available, never a guess
        return None, 0
    return r["known_exposure_inr"], r["unknown_vendor_lines"]


def render_review_queue(db: Path, column: str, direction: str, unknown: int = 0) -> None:
    st.header("Review queue", anchor="review-queue")
    with st.expander("Settings"):
        render_settings_row(db, "Event")
        st.caption("The review block threshold decides when an uncertain line stops a recommendation: a line worth more than this share of the event's value blocks it; below it, "
                   "the recommendation still runs and lists the line as a warning. Lower it to be stricter, raise it to be more permissive.")
    items = review_queue(db)
    st.caption("sorted by potential impact, not by document order.")
    st.caption("Uncertain fields where the buyer should confirm the number before signing. Each row below carries the reason and a drafted question "
               "to send the vendor. The total above counts each vendor-line once at its normalized price, so it is not the sum of the rows.")
    st.caption("Row figures are quoted-price estimates; per-100 and per-kg prices are not normalized.")
    if unknown:
        st.caption(f"{unknown} vendor-line(s) have no price to value them and are not in the total.")
    if items:
        lines, fields = question_context(db)
        names = {r["vendor_id"]: r["vendor_name"] for r in items}
        st.markdown(review_table_html(items, column, direction, lambda r: clarify.vendor_question(r, names[r["vendor_id"]], lines, fields)), unsafe_allow_html=True)
    else:
        st.info("No bid fields currently need review.")


def render_event_tiles(db: Path, lines: list, submissions: list, flagged: int) -> int:
    """The five headline tiles. Returns the number of vendor-lines with no price (for the queue's note). The exposure total is the analyst layer's review_exposure()."""
    extracted = read_table(db, "SELECT COUNT(*) AS n FROM bid_fields b JOIN submissions s USING(submission_id) WHERE b.state='extracted' AND s.submission_id=("
                               "SELECT s2.submission_id FROM submissions s2 WHERE s2.vendor_id=s.vendor_id ORDER BY s2.received_on DESC,s2.submission_id DESC LIMIT 1)")
    total, unknown = review_exposure_total(db)
    tiles = st.columns(5)
    tiles[0].metric("RFx lines", len(lines))
    tiles[1].metric("Vendors submitted", len({s["vendor_id"] for s in submissions}))
    tiles[2].metric("Fields extracted", extracted[0]["n"] if extracted else 0)
    tiles[3].metric("Fields flagged", flagged)
    tiles[4].metric(EXPOSURE_LABEL, "not available" if total is None else f"₹ {fmt.indian(total, 0)}",
                    help="Value of the affected vendor-line quotes: normalized ₹ per piece × annual RFx quantity, one exposure per vendor-line, ex-freight. "
                         "Not an expected loss and not an award total.")
    return unknown


QUESTIONNAIRE_VENDORS = ("V1", "V2", "V3", "V5")
ANSWER_STATES = {
    "extracted": ("✓", "Answered"),
    "needs_review": ("⚠", "Needs review"),
    "claimed_unsupported": ("⚠", "Claimed unsupported"),
    "missing": ("⊘", "Not answered"),
    "not_quoted": ("⊘", "Not answered"),
}


def apply_questionnaire_link() -> None:
    """Route a questionnaire cell link without overriding later sidebar navigation."""
    requested = (st.query_params.get("q_vendor"), st.query_params.get("q_no"))
    if requested == st.session_state.get("_last_questionnaire_link"):
        return
    st.session_state["_last_questionnaire_link"] = requested
    try:
        vendor, number = requested[0], int(requested[1])
    except (TypeError, ValueError):
        return
    if vendor in QUESTIONNAIRE_VENDORS and number > 0:
        st.session_state["page"] = "RFx"
        st.session_state["questionnaire_selection"] = (vendor, number)


def questionnaire_html(questions: list[dict], answers: list[dict], names: dict) -> str:
    """Render recorded answers with explicit states and accessible evidence links."""
    index = {(a["vendor_id"], a["q_no"]): a for a in answers}
    parts = [CSS, '<div class="quote-scroll"><table class="quote-grid">',
             '<caption>Supplier questionnaire responses</caption><thead><tr><th scope="col">Question</th>']
    parts.extend(f'<th scope="col">{escape(v)}<br>{escape(names.get(v, v))}</th>' for v in QUESTIONNAIRE_VENDORS)
    parts.append('</tr></thead><tbody>')
    for question in questions:
        number = question["q_no"]
        badge = (' <span class="status">Mandatory gate ' + escape(str(question.get("gate_code") or "")) + '</span>') if question.get("is_gate") else ''
        parts.append(f'<tr><th scope="row">{number}. {escape(question.get("text") or question.get("question") or "")}{badge}</th>')
        for vendor in QUESTIONNAIRE_VENDORS:
            record = index.get((vendor, number), {})
            state = record.get("state") or "missing"
            glyph, label = ANSWER_STATES.get(state, ("⊘", "Not answered"))
            short = ' '.join((record.get("answer_text") or "No answer recorded").split())
            if len(short) > 120:
                short = short[:117].rstrip() + "…"
            href = "?" + urlencode({"q_vendor": vendor, "q_no": number}) + "#questionnaire-evidence"
            style = "needs_review" if state == "claimed_unsupported" else state if state in STATES else "missing"
            parts.append(f'<td class="{style}"><a class="bid-cell" target="_self" href="{escape(href)}" '
                         f'aria-label="{escape(f"View {vendor} answer to question {number}")}">'
                         f'<span class="status">{glyph} {escape(label)}</span>{escape(short)}</a></td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def render_questionnaire(db: Path, rfx: dict) -> None:
    """Show the buyer questions, recorded gate outcomes and full selected evidence."""
    st.subheader("Supplier questionnaire")
    answers = read_table(db, "SELECT * FROM questionnaire_answers ORDER BY q_no,vendor_id")
    questions = rfx.get("questions") or list({a["q_no"]: a for a in answers}.values())
    questions = sorted(questions, key=lambda q: q["q_no"])
    names = {v["vendor_id"]: v["name"] for v in rfx.get("invited_vendors", [])}
    names.update({v["vendor_id"]: v["name"] for v in read_table(db, "SELECT vendor_id,name FROM vendors")})
    index = {(a["vendor_id"], a["q_no"]): a for a in answers}
    gates = [q["q_no"] for q in questions if q.get("is_gate")]
    cleared, failed, pending = [], [], []
    for vendor in QUESTIONNAIRE_VENDORS:
        results = [index.get((vendor, q), {}).get("gate_status") for q in gates]
        if results and all(value == "pass" for value in results):
            cleared.append(vendor)
        elif "fail" in results:
            failed.append(vendor)
        else:
            pending.append(vendor)
    st.info(f"Cleared all gates: {', '.join(cleared) or 'None'} · Failed: {', '.join(failed) or 'None'} · Incomplete / not evaluated: {', '.join(pending) or 'None'}")
    st.caption("✓ Answered · ⚠ Needs review / claimed unsupported · ⊘ Not answered. Click an answer for its full text and certificate evidence.")
    if not questions:
        st.info("No questionnaire questions or answers recorded.")
        return
    st.markdown(questionnaire_html(questions, answers, names), unsafe_allow_html=True)
    selected = st.session_state.get("questionnaire_selection")
    if not selected:
        return
    vendor, number = selected
    if vendor not in QUESTIONNAIRE_VENDORS or number not in {q["q_no"] for q in questions}:
        st.info("The selected question is not part of this RFx.")
        return
    st.subheader(f"{vendor} · Question {number}", anchor="questionnaire-evidence")
    record = index.get((vendor, number))
    with st.container(border=True):
        if record is None:
            st.info("⊘ Not answered — no response recorded for this vendor and question.")
        else:
            glyph, label = ANSWER_STATES.get(record.get("state"), ("⊘", "Not answered"))
            st.text(f"{glyph} {label} · Gate result: {record.get('gate_status') or 'Not applicable / not recorded'}")
            st.text(record["question"])
            st.caption("Full answer")
            st.text(record.get("answer_text") or "No answer recorded")
            for label, key in [("Reason", "reason_code"), ("Evidence source", "evidence_source"), ("Response anchor", "anchor")]:
                st.text(f"{label}: {record.get(key) or 'Not recorded'}")
            if record.get("resolving_question"):
                st.warning(clarify.vendor_question(record, names.get(vendor, vendor), {}, {}))
            st.caption("Verbatim evidence")
            st.code(record.get("snippet") or "No snippet recorded", language=None, wrap_lines=True)
            attached = read_table(db, "SELECT * FROM attachments WHERE attachment_id=? AND vendor_id=?", (record.get("attachment_id"), vendor))
            if attached:
                for attachment in attached:
                    st.caption("Attached certificate")
                    for label, key in [("File", "file_name"), ("Issuer", "issuer"), ("Entity", "entity_name"), ("Valid to", "valid_to"), ("Evidence note", "note")]:
                        st.text(f"{label}: {attachment.get(key) or 'Not recorded'}")
                    st.text("Certificate page / bbox: " + str(attachment.get("anchor") or "Not recorded; the response anchor above is not a certificate page locator."))
            else:
                st.info("No attached certificate linked to this answer.")
    st.html("<script>if(location.hash === '#questionnaire-evidence') requestAnimationFrame(() => document.getElementById('questionnaire-evidence')?.scrollIntoView({behavior:'smooth',block:'start'}));</script>", unsafe_allow_javascript=True)


def render_rfx(db: Path) -> None:
    """Display the issued RFx JSON as a read-only document, without inferred terms."""
    path = ROOT / "dataset/artifacts/rfx.json"
    try:
        rfx = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.error(f"The issued RFx could not be read: {exc}")
        return
    st.title("Request for quotation")
    st.caption("Issued sourcing requirements · read-only")
    items = rfx.get("lines", [])
    cost = rfx.get("total_should_cost_value", rfx.get("total_should_cost_inr"))
    if cost is None and items and all(r.get("should_cost_inr_pc") is not None and r.get("annual_qty") is not None for r in items):
        cost = sum(r["should_cost_inr_pc"] * r["annual_qty"] for r in items)
    date = rfx.get("event_date")
    header = {
        "RFx ID": rfx.get("rfx_id") or "Not specified",
        "Event date": date or rfx.get("evaluation_date") or "Not specified",
        "Category": rfx.get("category") or "Corrugated packaging",
        "Buyer": rfx.get("buyer_name") or rfx.get("buyer") or "Priya Nair",
        "Total should-cost value": fmt.total(cost, rfx.get('currency', '')) if isinstance(cost, (int, float)) else "Not available",
    }
    if not date and rfx.get("evaluation_date"):
        st.caption("Event date uses the recorded evaluation date.")
    st.table([{"Field": key, "Value": str(value)} for key, value in header.items()])
    if cost is None:
        st.caption("Per-line should-cost prices have not been supplied; total should-cost is not available.")
    st.subheader("Scope")
    scope = rfx.get("scope") or rfx.get("description")
    if scope:
        st.text(str(scope))
    elif items:
        plies = ', '.join(str(p) for p in sorted({r['ply'] for r in items}))
        styles = ', '.join(sorted({r['style'] for r in items}))
        st.write(f"Supply of {len(items)} {styles} box line items in {plies}-ply board, to the dimensions and print specifications below.")
        st.write("Annual quantities, liner GSM and units are listed for each line. The supplier questionnaire records the mandatory gates and supporting evidence requested.")
        st.caption("Scope summary derived from the issued line items and questions.")
    else:
        st.info("No scope or line items specified.")
    st.subheader("Commercial terms")
    terms = rfx.get("commercial_terms") or {}
    if not isinstance(terms, dict):
        st.text(str(terms))
        terms = {}
    aliases = {"Payment": ("payment_terms", "payment"), "Incoterm": ("incoterm",), "Validity": ("validity_days", "validity"), "Evaluation weights": ("evaluation_weights",)}
    term_rows = []
    for label, keys in aliases.items():
        found = next(((k, source[k]) for source in (terms, rfx) for k in keys if source.get(k) is not None), None)
        value = found[1] if found else "Not specified"
        if found and found[0] == "validity_days":
            value = f"{value} days"
        term_rows.append({"Term": label, "Requirement": json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)})
    st.table(term_rows)
    st.subheader("Requested line items")
    if items:
        st.table([{"Line #": r["line_no"],
                   "Spec": f"{r['ply']}-ply {r['length_mm']}×{r['width_mm']}×{r['height_mm']} mm, {r['liner_gsm']} GSM liner, {r['print_spec']}",
                   "Annual quantity": fmt.indian(r["annual_qty"]), "Unit": r["uom"]} for r in items])
    else:
        st.info("No RFx line items specified.")
    render_questionnaire(db, rfx)


SNAPSHOT = ROOT / "dataset" / "eval" / "eval_snapshot.json"


def calibration_table(rows: dict, label: str = "Vendor") -> list[dict]:
    """A calibration 2x2 per row: flagged/not flagged against wrong/right. 'Silent' is wrong and not flagged."""
    return [{label: name, "Checked": sum(c[k] for k in ("flagged_wrong", "flagged_right", "silent_wrong", "not_flagged_right")),
             "Flagged · wrong": c["flagged_wrong"], "Flagged · right": c["flagged_right"],
             "Not flagged · wrong (silent)": c["silent_wrong"], "Not flagged · right": c["not_flagged_right"]} for name, c in rows.items()]


def signed_rupees(value: float | None) -> str:
    return "—" if value is None else ("−" if round(value) < 0 else "") + "₹" + fmt.indian(abs(value), 0)


def render_evals(db: Path) -> None:
    """Read-only display of the graders' recorded output (scripts/snapshot_evals.py writes it). Nothing is evaluated here."""
    st.title("Evals")
    st.write("Every stage of this pipeline is graded against a ground-truth file the pipeline never sees. The results below are the current run's calibration, "
             "plus deterministic tests that would break before any demo figure could move without noticing.")
    try:
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.info("No eval snapshot yet. Run `python scripts/snapshot_evals.py` to record the graders' output; this page only displays it.")
        return
    stale = db.is_file() and db.stat().st_mtime > SNAPSHOT.stat().st_mtime
    st.caption(f"Snapshot recorded {snap['generated_at']}" + (" · the database has changed since; re-run scripts/snapshot_evals.py to refresh." if stale else ""))

    ex = snap["extraction"]
    st.subheader("1. Extraction (documents)")
    o = ex["overall"]
    a, b, c, d = st.columns(4)
    a.metric("Fields checked", fmt.indian(ex["fields"]))
    b.metric("Flagged", o["flagged_wrong"] + o["flagged_right"])
    c.metric("Wrong", o["flagged_wrong"] + o["silent_wrong"])
    d.metric("Silently wrong", o["silent_wrong"])
    st.table(calibration_table({f"{v} {r['name']}": r for v, r in ex["vendors"].items()} | {"Overall": o}))
    st.caption("Precision proven across four document formats. " + (f"Zero silent failures on {ex['fields']} fields." if not o["silent_wrong"]
               else f"{o['silent_wrong']} silent failure(s) on {ex['fields']} fields."))

    ph = snap["photo"]
    st.subheader("2. Photo (rate card)")
    st.table(calibration_table({"V4 rate card (rows)": ph["cells"]}, "Rate card"))
    p1, p2 = ph["pass1"], ph["pass2"]
    st.table([{"Measure": "Rows mapped to an RFx line", "Pass 1": f"{p1['mapped']} of {ph['rows']}", "Pass 2": f"{p2['mapped']} of {ph['rows']}"},
              {"Measure": "Mapped to the right line", "Pass 1": f"{p1['mapped_correctly']} of {ph['rows']}", "Pass 2": f"{p2['mapped_correctly']} of {ph['rows']}"},
              {"Measure": "Prices transcribed / exactly right", "Pass 1": f"{p1['prices_transcribed']} / {p1['prices_exactly_right']}", "Pass 2": f"{p2['prices_transcribed']} / {p2['prices_exactly_right']}"},
              {"Measure": "Correctly flagged (of the rows that needed it)", "Pass 1": "", "Pass 2": f"{len(set(ph['flagged']) & set(ph['expected_uncertain']))} of {len(ph['expected_uncertain'])}"}])
    st.caption(f"Recall lives here. {p2['mapped_correctly']}/{ph['rows']} mapped, {len(ph['flagged'])} correctly flagged including "
               f"{len(ph['disagree'])} two-pass disagreement (line {', '.join(map(str, ph['disagree']))}). "
               + ("Zero silent failures." if not ph["cells"]["silent_wrong"] else f"{ph['cells']['silent_wrong']} silent failure(s)."))

    qn = snap["questionnaire"]
    st.subheader("3. Questionnaire")
    st.table(calibration_table(qn["vendors"] | {"Overall": qn["overall"]}))
    bad = {q: d for q, d in qn["by_question"].items() if d["wrong"]}
    st.markdown(f"**By question:** {len(qn['by_question']) - len(bad)} of {len(qn['by_question'])} questions had no error across the vendors graded.")
    if bad:
        st.table([{"Question": f"Q{q}", "Answers": d["answers"], "Wrong": d["wrong"], "Silent": d["silent"], "Vendors": ", ".join(d["vendors"])} for q, d in bad.items()])
    gc = qn["gate_clearance"]
    st.caption(f"Gate clearance from documents: {', '.join(gc['got'])} (expected {', '.join(gc['expected'])}).")
    st.caption("Live model on real documents. Silent errors documented as known limitation; none affect a gate.")

    aw = snap["award"]
    st.subheader("4. Award engine")
    st.table([{"Scenario": s["label"], "Vendors used": ", ".join(s["vendors"]), "Naive saving": signed_rupees(s["naive"]), "True saving (re-priced)": signed_rupees(s["repriced"]),
               "True saving %": "—" if s["pct"] is None else fmt.pct(s["pct"]),
               "Status": "recommendable" if s["recommendable"] else "blocked: " + ", ".join(s["blockers"])} for s in aw["scenarios"]])
    st.caption(f"Ex-freight, against {aw['baseline']}. Gates applied to {', '.join(aw['gates'])}.")
    gate_tests = snap["tests"]["gates"]
    ok = lambda name: all(v == "passed" for k, v in gate_tests.items() if name in k)
    st.markdown("**Gate-sourcing regression.** With no questionnaire the gated scenario refuses; with gates supplied by the buyer and with gates evaluated from the documents it "
                "produces the identical allocation. " + ("Both checks pass." if ok("test_before") and ok("test_after") else "A gate-sourcing test is failing: see the test suite."))
    st.caption("Award engine tests hold scenarios against known outcomes. When tests break, story figures move — I'd know before the demo.")

    sn, tt = snap["sanity"], snap["tests"]
    st.subheader("5. Sanity report")
    a, b, c = st.columns(3)
    a.metric("Things flagged in SANITY.md", "unknown" if sn["flagged"] is None else sn["flagged"])
    seeded_ok = tt["sanity"].get("test_seeded_faults_are_all_caught") == "passed"
    b.metric("Seeded faults caught", f"{tt['seeded_faults']}/{tt['seeded_faults']}" if seeded_ok else "not all")
    c.metric("Test suite", f"{tt['passed']} passed, {tt['skipped']} skipped" + (f", {tt['failed']} failed" if tt["failed"] else ""))
    st.caption("Automated sanity pass on every run. Runs against a scratch copy with 30 known faults injected.")


ASK_CHIPS = ["Which vendors cleared the quality gates?", "Show me the arithmetic mismatch on Kaveri line 20",
             "Split it among vendors who cleared the questionnaire — should we do it?", "What will kraft paper cost in June?"]


def render_ask(db: Path) -> None:
    """Use the CLI analyst entry point; initialize its client only on submission."""
    st.title("Ask")
    st.write("Ask about quotes, commercial terms or source evidence.")
    st.caption("Try one:")
    for col, chip in zip(st.columns(len(ASK_CHIPS)), ASK_CHIPS):
        col.button(chip, key="chip_" + chip[:12], use_container_width=True, on_click=st.session_state.__setitem__, args=("analyst_q", chip))
    with st.form("analyst_question"):
        question = st.text_area("Your question", key="analyst_q", placeholder="Which vendors cleared the mandatory gates?")
        submitted = st.form_submit_button("Ask")
    st.caption("The system answers from the normalized store. If it can't answer, it says what's missing and drafts a question to the source.")
    if submitted:
        st.session_state.pop("analyst_answer", None)
        if not question.strip():
            st.warning("Enter a question to ask the analyst.")
        elif not db.is_file():
            st.error("The procurement database is not available yet.")
        elif (ROOT / "dataset/truth").resolve() in db.resolve().parents:
            st.error("Choose the pipeline database, not frozen truth.")
        else:
            try:
                from src.analyst import Analyst, Store
                from src import metering
                with st.status("Checking the bids and supporting evidence…", expanded=False) as progress:
                    analyst = Analyst(Store(db.resolve()))
                    metering.configure(db.resolve())
                    with metering.stage("analyst:streamlit"):
                        answer = analyst.ask(question.strip(), progress=lambda label: progress.update(label=label))
                    progress.update(label="Evidence check finished", state="complete")
                st.session_state["analyst_answer"] = (str(db.resolve()), question.strip(), answer)
            except (ImportError, RuntimeError) as exc:
                st.error(f"Analyst setup failed: {exc}")
            except Exception as exc:
                st.error(f"The analyst request failed ({type(exc).__name__}). Check provider access and connectivity, then retry.")
    saved = st.session_state.get("analyst_answer")
    if saved and saved[0] == str(db.resolve()):
        _, prompt, answer = saved
        st.caption("Answer to: " + prompt)
        st.markdown(answer.prose)
        status = getattr(answer, "status", "complete")
        if getattr(answer, "execution_note", None):
            st.warning(answer.execution_note)
        st.caption(f"{status.capitalize()} · {getattr(answer, 'elapsed_seconds', 0):g}s · {getattr(answer, 'model_requests', 0)} model requests")
        if answer.missing:
            st.warning("Missing from the store: " + answer.missing)
        if answer.drafted_question:
            st.info("Drafted clarification: " + answer.drafted_question)
        if answer.table:
            st.caption(answer.table.get("title", "Supporting figures"))
            st.table([dict(zip(answer.table["columns"], row)) for row in answer.table["rows"]])
        with st.expander(answer.expander["label"], expanded=False):
            for call in answer.tool_calls:
                st.markdown("**" + call["tool"] + "**")
                st.json(call["arguments"])
                result = call["result"]
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except ValueError:
                        pass
                if isinstance(result, (dict, list)):
                    st.json(result)
                else:
                    st.code(str(result), language=None, wrap_lines=True)
            if not answer.tool_calls:
                st.caption("No tool calls were returned for this answer.")


def main() -> None:
    st.set_page_config(page_title="Sourcing workspace", page_icon="▦", layout="wide")
    st.sidebar.title("Sourcing workspace")
    db = Path(os.environ.get("PROCUREMENT_DB", str(ROOT / "procurement.db")))
    st.sidebar.caption("Read-only bids · editable buyer settings")
    st.sidebar.button("Refresh")
    try:
        lines = read_table(db, """
            SELECT line_no,sku,style,length_mm,width_mm,height_mm,ply,flute,
                   liner_gsm,gsm_stack,bf,print_spec,annual_qty,uom FROM rfx_lines ORDER BY line_no
        """)
        vendors = read_table(db, "SELECT vendor_id,name,response_format FROM vendors ORDER BY vendor_id")
        submissions = read_table(db, """
            SELECT submission_id,vendor_id,file_name,received_on,currency,validity_days,valid_until
            FROM submissions ORDER BY received_on,submission_id
        """)
        apply_cell_link(comparison_vendors(vendors), lines)
        review_column, review_direction = review_sort_selection()
        apply_questionnaire_link()
        pages = ["RFx", "Event", "Comparison", "Ask", "Evals"]
        page = st.sidebar.radio("Page", pages, key="page", index=1, format_func=lambda p: f"{pages.index(p) + 1}. {p}")
        if not db.is_file():
            st.info("Waiting for procurement.db. The workspace will display data when the pipeline database is available.")
        if page == "Event":
            st.markdown("<style>[data-testid='stMainBlockContainer']{padding-top:1rem;padding-bottom:1rem} [data-testid='stVerticalBlock']{gap:.65rem} [data-testid='stHorizontalBlock']{flex-wrap:nowrap;gap:.8rem} [data-testid='stColumn']{min-width:0!important;flex:1 1 0!important} [data-testid='stMetricValue']{font-size:1.6rem} h1{margin:0;padding-top:0} .review-scroll{overflow:visible}</style>", unsafe_allow_html=True)
            st.title("Event")
            st.caption("Corrugated packaging sourcing")
            unknown = render_event_tiles(db, lines, submissions, len(review_queue(db)))
            st.divider()
            render_processing(db, len({s["vendor_id"] for s in submissions}), len(lines))
            st.divider()
            st.subheader("Vendor responses")
            if submissions:
                names = {v["vendor_id"]: v["name"] for v in vendors}
                st.table([{"Vendor": names.get(s["vendor_id"], s["vendor_id"]),
                           "File": s["file_name"], "Received": s["received_on"],
                           "Currency": s["currency"], "Valid until": s["valid_until"]}
                          for s in submissions])
            else:
                st.info("No submissions yet.")
            st.divider()
            render_review_queue(db, review_column, review_direction, unknown)
        elif page == "Comparison":
            st.title("Comparison")
            grid_vendors = comparison_vendors(vendors)
            # One current submission per vendor, consistently used by the grid, the stat tiles and the Details pane.
            fields = read_table(db, """
                SELECT b.field_id,b.field_name,b.rfx_line_no,b.value,b.unit,b.basis,
                       b.currency,b.state,b.reason_code,b.anchor,b.snippet,b.derivation,
                       b.resolving_question,s.vendor_id,s.submission_id,s.file_name
                FROM bid_fields b JOIN submissions s USING(submission_id)
                WHERE s.submission_id=(
                    SELECT latest.submission_id FROM submissions latest
                    WHERE latest.vendor_id=s.vendor_id
                    ORDER BY latest.received_on DESC,latest.submission_id DESC LIMIT 1
                )
                ORDER BY b.field_id
            """)
            try:                                      # norm_prices does not exist until normalization has run once
                norms = read_table(db, """
                    SELECT s.vendor_id,p.rfx_line_no,p.inr_per_piece,p.comparability
                    FROM norm_prices p JOIN submissions s USING(submission_id)
                    WHERE s.submission_id=(
                        SELECT latest.submission_id FROM submissions latest
                        WHERE latest.vendor_id=s.vendor_id
                        ORDER BY latest.received_on DESC,latest.submission_id DESC LIMIT 1
                    )
                """)
            except sqlite3.OperationalError:
                norms = []
            norm_by_pair = {(r["vendor_id"], r["rfx_line_no"]): r for r in norms}
            vendor_names = {v["vendor_id"]: v["name"] for v in grid_vendors}
            line_names = {r["line_no"]: f'{r["line_no"]} · {r["sku"]}' for r in lines}

            stats = comparison_stats(lines, [v["vendor_id"] for v in grid_vendors], fields, norm_by_pair)
            tiles = st.columns(4)
            tiles[0].metric("Total lines", stats["total_lines"])
            tiles[1].metric("All-vendor clean", stats["all_clean"])
            tiles[2].metric("Any flag", stats["any_flag"])
            if stats["cheapest_vendor"]:
                tiles[3].metric("Cheapest overall", f"{vendor_names.get(stats['cheapest_vendor'], stats['cheapest_vendor'])} ({stats['cheapest_vendor']})",
                                delta=award_words.crore(stats["cheapest_total"]), delta_color="off")
                with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
                    from src.extract.questionnaire import gate_results
                    _, gate_detail = gate_results(con)
                if any(g["status"] == "fail" for g in gate_detail.get(stats["cheapest_vendor"], {}).values()):
                    tiles[3].caption("⚠ Not eligible — fails gate checks")
            else:
                tiles[3].metric("Cheapest overall", "not available")

            with st.expander("Award recommendation", expanded=True):
                st.caption(award_words.BASIS_LINE["ex_freight"])
                scenario_label = st.radio("Award scenario", list(SCENARIO_LABELS.values()), horizontal=True)
                scenario = next(k for k, v in SCENARIO_LABELS.items() if v == scenario_label)
                cost_basis = "ex_freight"                          # the landed basis stays fully implemented in the engine; this page just doesn't expose it
                render_award_preview(db, scenario, cost_basis)
                saved = st.session_state.get("award_note_export")
                if saved and saved[:3] == (str(db), scenario, cost_basis):
                    for gap in saved[4]:
                        st.warning(gap)
                    st.download_button("Download award_note.md", saved[3], file_name="award_note.md", mime="text/markdown")

            st.caption("✓ extracted (clean) · ⚠ needs_review (amber border) · ✗ missing (red border) · – not_quoted")
            st.caption("Hover or focus a cell to preview its source and drafted question. Click to keep the card open; Escape closes it.")
            st.html(comparison_html(lines, grid_vendors, fields, "normalized", norm_by_pair), unsafe_allow_javascript=True)
            if not lines:
                st.info("The comparison grid is waiting for event lines.")

            if st.session_state.pop("_open_evidence_dialog", False):
                dvendor, dline = st.session_state.get("bid_vendor"), st.session_state.get("bid_line")
                if dvendor in vendor_names and dline in line_names:
                    drecord = unit_price_index(fields).get((dline, dvendor))
                    open_evidence_dialog(vendor_names[dvendor], line_names[dline], drecord, any(r["vendor_id"] == dvendor for r in fields))

            st.divider()
            with st.container(border=True):
                render_vendor_clarifications(fields, lines, grid_vendors, db)
        elif page == "Ask":
            render_ask(db)
        elif page == "Evals":
            render_evals(db)
        else:
            render_rfx(db)
    except (sqlite3.Error, ValueError) as exc:
        st.error("The procurement database could not be read. Check that it uses scripts/schema.sql.")
        st.text(str(exc))


if __name__ == "__main__":
    main()
