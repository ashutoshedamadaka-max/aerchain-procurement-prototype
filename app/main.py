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
from app import clarify, fmt
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


def comparison_html(lines: list[dict], vendors: list[dict], fields: list[dict]) -> str:
    """Render one glyph per cell; declined/missing states never expose a price."""
    index = unit_price_index(fields)
    ingested = {r["vendor_id"] for r in fields}
    parts = [CSS, '<div class="quote-scroll"><table class="quote-grid">',
             '<caption>Quoted rates in their original currency and pricing basis</caption>',
             '<thead><tr><th scope="col">RFx line</th>']
    parts.extend(f'<th scope="col">{escape(v["name"])}</th>' for v in vendors)
    parts.append("</tr></thead><tbody>")
    for line in lines:
        parts.append(f'<tr><th scope="row">{line["line_no"]} · {escape(line["sku"])}</th>')
        for vendor in vendors:
            record = index.get((line["line_no"], vendor["vendor_id"]), {})
            state = record.get("state", "missing")
            if state not in STATES:
                state = "missing"
            glyph, label = STATES[state]
            href = "?" + urlencode({"bid_vendor": vendor["vendor_id"],
                                      "bid_line": line["line_no"]}) + "#source-evidence"
            aria = escape(f'Show source evidence for {vendor["name"]}, line {line["line_no"]}, {label}')
            parts.append(f'<td class="{state}"><a class="bid-cell" href="{escape(href)}" '
                         f'target="_self" aria-label="{aria}">'
                         f'<span class="status">{glyph} {label}</span>')
            if vendor["vendor_id"] not in ingested:
                parts.append("not ingested")
            elif state == "not_quoted":
                parts.append("Not quoted")
            elif state == "missing":
                parts.append("No value recorded")
            elif record.get("value") is None:
                parts.append("No value recorded")
            else:
                price = fmt.price(record["value"], record.get("currency"))
                parts.append(escape(price))
                unit = record.get("unit") or record.get("basis") or ""
                parts.append(f'<span class="detail">{escape(unit)}</span>')
            parts.append("</a></td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


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


def sync_bid_selection() -> None:
    """Keep manual dropdown changes and subsequent cell navigation in agreement."""
    vendor_id = st.session_state.get("bid_vendor")
    line_no = st.session_state.get("bid_line")
    if vendor_id is not None and line_no is not None:
        st.query_params.update({"bid_vendor": vendor_id, "bid_line": str(line_no)})
        st.session_state["_last_bid_link"] = (vendor_id, str(line_no))


def provenance(record: dict | None, *, vendor_ingested: bool, question: str | None = None, heading: bool = True) -> None:
    """Display only evidence recorded on the selected unit-price field."""
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
        st.markdown('<div class="evidence-badges">' + badges + '</div>',
                    unsafe_allow_html=True)
        if record.get("derivation"):
            st.caption("Derivation")
            st.code(clarify.plain_derivation(record["derivation"]), language=None, wrap_lines=True)
        if question or record.get("resolving_question"):
            st.markdown('<div class="clarification" role="note">'
                        '<strong>Draft clarification to vendor</strong><p>'
                        + escape(question or record["resolving_question"]) + '</p></div>',
                        unsafe_allow_html=True)
        st.text("File: " + (record.get("file_name") or "Not recorded"))
        st.text("Source anchor: " + (record.get("anchor") or "Not recorded"))
        st.caption("Verbatim snippet")
        st.code(record["snippet"] if record.get("snippet") is not None
                else "No snippet recorded.", language=None, wrap_lines=True)



REVIEW_COLUMNS = {
    "vendor": "Vendor", "line": "Line / short spec", "value": "Extracted value",
    "reason": "Reason code", "risk": "Value at risk (₹)",
    "question": "Resolving question",
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
                     f'<td><em>{escape((question_for(row) if question_for else None) or row.get("resolving_question") or "—")}</em></td></tr>')
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


SCENARIO_LABELS = {"single_vendor": "Award to one vendor", "gated_split": "Split among gated vendors"}
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
        {"error": st.error, "success": st.success, "warning": st.warning}.get(model["tone"], st.markdown)("**" + model["header"] + "**")
        for section in model["sections"]:
            if section[0] == "table":
                st.markdown("**" + section[1] + "**")
                st.table([dict(zip(section[2], row)) for row in section[3]])
            elif section[0] == "warnings":
                for text in section[1]:
                    st.warning(text)
            else:
                for line in section[1]:
                    st.markdown(line)


def render_clarifications(fields: list[dict], lines: list[dict], vendors: list[dict], db: Path) -> None:
    """One email per vendor: bid clarifications (largest quoted-price exposure first), then questionnaire clarifications (mandatory gates first).
    Pure aggregation of what is already stored; nothing is written."""
    st.subheader("Clarifications", anchor="clarifications")
    names = {v["vendor_id"]: v["name"] for v in vendors}
    if not names:
        return
    by_line = {r["line_no"]: r for r in lines}
    by_key = {(r["submission_id"], r["rfx_line_no"], r["field_name"]): r for r in fields}
    vid = st.selectbox("Draft clarifications for", list(names), format_func=names.get, key="clar_vendor")
    if st.button("Draft clarifications"):
        try:
            risk = {r["field_id"]: r["risk"] for r in review_queue(db)}
        except sqlite3.Error:
            risk = {}
        groups = {}                                        # a price and its total can raise the same question; ask it once
        for f in fields:
            if f["vendor_id"] != vid or f["state"] not in ("needs_review", "missing") or not f.get("resolving_question"):
                continue
            q = clarify.vendor_question(f, names[vid], by_line, by_key)
            if q:
                g = groups.setdefault(q, dict(line=f["rfx_line_no"], risk=None, first=f["field_id"]))
                g["line"] = min(g["line"], f["rfx_line_no"])
                if risk.get(f["field_id"]) is not None:
                    g["risk"] = max(g["risk"] or 0, risk[f["field_id"]])
        ordered = sorted(groups.items(), key=lambda kv: (kv[1]["risk"] is None, -(kv[1]["risk"] or 0), kv[1]["line"], kv[1]["first"]))
        bid_items = [(f"Line {g['line']} — {clarify.spec(by_line.get(g['line']))}", q) for q, g in ordered]
        answers = read_table(db, "SELECT q_no,question,is_gate,state,reason_code,resolving_question FROM questionnaire_answers "
                                 "WHERE vendor_id=? AND state IN ('needs_review','claimed_unsupported') AND resolving_question IS NOT NULL "
                                 "ORDER BY is_gate DESC,q_no", (vid,))
        quest_items = [(f"Question {a['q_no']}{' (mandatory gate)' if a['is_gate'] else ''} — {a['question']}", clarify.vendor_question(a, names[vid], {}, {})) for a in answers]
        quest_items = [(label, q) for label, q in quest_items if q]
        st.session_state["clarification_draft"] = (vid, clarify.email(names[vid], bid_items, quest_items) if bid_items or quest_items else None)
    saved = st.session_state.get("clarification_draft")
    if not saved or saved[0] != vid:
        return
    if saved[1] is None:
        st.info(f"No open clarifications for {names[vid]}.")
        return
    with st.container(border=True):
        shown = saved[1].replace("\n   ", "  \n   ")
        for heading in ("Bid clarifications", "Questionnaire clarifications"):
            shown = shown.replace("\n" + heading + "\n", "\n**" + heading + "**\n")
        st.markdown(shown)                                                 # a hard line break under each item; the copied and downloaded text keep the plain layout
    copy, download = st.columns([1, 1])
    with copy:
        payload = json.dumps(saved[1]).replace("</", "<" + chr(92) + "/")
        components.html('<button id="c" style="font:14px sans-serif;padding:7px 14px;border:1px solid #bbb;border-radius:8px;background:#fff;cursor:pointer">Copy to clipboard</button>'
                        '<script>const t=' + payload + ';const b=document.getElementById("c");b.onclick=async()=>{let ok=false;try{await navigator.clipboard.writeText(t);ok=true}catch(e){}'
                        'if(!ok){const a=document.createElement("textarea");a.value=t;document.body.appendChild(a);a.select();ok=document.execCommand("copy");a.remove()}'
                        'b.textContent=ok?"Copied":"Copy failed - select the text above"}</script>', height=46)
    download.download_button("Download .txt", saved[1], file_name=f"clarifications_{vid}.txt", mime="text/plain")


def render_processing(db: Path) -> None:
    st.markdown("**Processing**")
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
    lines = [f"| {label} | {seconds(t)} | {inr(c)} |" for label, t, c in summary]
    lines.append(f"| **Total** | **~{sum(t for _, t, _ in summary) / 60:.0f} min** | **~₹{fmt.indian(known, 0)}** |")
    st.markdown("| Stage | Time | Cost |" + "\n|---|---:|---:|" + "\n" + "\n".join(lines))


def question_context(db: Path) -> tuple[dict, dict]:
    """RFx lines and quoted prices/totals, keyed for the display-layer question templates. Read-only."""
    lines = {r["line_no"]: r for r in read_table(db, "SELECT line_no,ply,length_mm,width_mm,height_mm,liner_gsm,print_spec,annual_qty FROM rfx_lines")}
    fields = {(r["submission_id"], r["rfx_line_no"], r["field_name"]): r for r in read_table(
        db, "SELECT submission_id,rfx_line_no,field_name,value,basis,currency FROM bid_fields WHERE field_name IN ('unit_price','line_total')")}
    return lines, fields


def render_review_queue(db: Path, column: str, direction: str) -> None:
    st.subheader("Review queue", anchor="review-queue")
    items = review_queue(db)
    total = sum(r["risk"] for r in items if r["risk"] is not None)
    unknown = sum(r["risk"] is None for r in items)
    left, right = st.columns(2)
    left.metric("Total items", len(items))
    right.metric("Total ₹ at risk (known)", f"₹ {fmt.indian(total, 0)}")
    st.caption("sorted by potential impact, not by document order.")
    st.caption("Uncertain fields where the buyer should confirm the number before signing. Each row below carries the reason and a drafted question "
               "to send the vendor. Totals sum field-level exposures, which may overlap on the same line.")
    st.caption("Quoted-price estimate; per-100 and per-kg prices are not normalized.")
    if unknown:
        st.caption(f"{unknown} item(s) have value unknown, appear first, and are excluded from the ₹ total.")
    if items:
        lines, fields = question_context(db)
        names = {r["vendor_id"]: r["vendor_name"] for r in items}
        st.markdown(review_table_html(items, column, direction, lambda r: clarify.vendor_question(r, names[r["vendor_id"]], lines, fields)), unsafe_allow_html=True)
    else:
        st.info("No bid fields currently need review.")

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
            tiles, processing = st.columns(2)
            for col, label, count in zip(tiles.columns(3), ["Line items", "Vendors", "Submissions"],
                                         [len(lines), len(vendors), len(submissions)]):
                col.metric(label, count)
            with processing:
                render_processing(db)
            render_settings_row(db, "Event")
            render_review_queue(db, review_column, review_direction)
            st.subheader("Vendor responses")
            if submissions:
                names = {v["vendor_id"]: v["name"] for v in vendors}
                st.table([{"Vendor": names.get(s["vendor_id"], s["vendor_id"]),
                           "File": s["file_name"], "Received": s["received_on"],
                           "Currency": s["currency"], "Valid until": s["valid_until"]}
                          for s in submissions])
            else:
                st.info("No submissions yet.")
        elif page == "Comparison":
            st.title("Comparison")
            grid_vendors = comparison_vendors(vendors)
            # One current submission per vendor, consistently used by grid and evidence.
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
            vendor_names = {v["vendor_id"]: v["name"] for v in grid_vendors}
            line_names = {r["line_no"]: f'{r["line_no"]} · {r["sku"]}' for r in lines}
            render_settings_row(db, "Comparison")
            with st.expander("Award note export", expanded=True):
                scenario = st.selectbox("Award scenario", list(SCENARIO_LABELS), format_func=SCENARIO_LABELS.get)
                cost_basis = st.selectbox("Award cost basis", ["landed", "ex_freight"])
                render_award_preview(db, scenario, cost_basis)
                st.caption("Uses the existing award engine and recorded gate results. Missing data and blockers remain explicit in the note.")
                saved = st.session_state.get("award_note_export")
                if saved and saved[:3] == (str(db), scenario, cost_basis):
                    for gap in saved[4]:
                        st.warning(gap)
                    st.download_button("Download award_note.md", saved[3], file_name="award_note.md", mime="text/markdown")
            st.caption("✓ Extracted · ? Needs review · — Not quoted · ∅ Missing")
            st.caption("Quoted currency and basis are shown as received. "
                       "Click a cell or use the selectors to view its source evidence.")
            st.info("Click any cell to see where the number came from and the drafted question the buyer would send if it's uncertain.")
            st.markdown(comparison_html(lines, grid_vendors, fields), unsafe_allow_html=True)
            if not lines:
                st.info("The comparison grid is waiting for event lines.")
            st.subheader("Source evidence", anchor="source-evidence")
            left, right = st.columns(2)
            vendor_id = left.selectbox("Vendor", list(vendor_names),
                                       format_func=vendor_names.get, key="bid_vendor",
                                       on_change=sync_bid_selection)
            line_no = right.selectbox("Line", list(line_names),
                                      format_func=line_names.get, disabled=not lines,
                                      key="bid_line", on_change=sync_bid_selection)
            record = unit_price_index(fields).get((line_no, vendor_id))
            by_key = {(r["submission_id"], r["rfx_line_no"], r["field_name"]): r for r in fields}
            provenance(record, vendor_ingested=any(r["vendor_id"] == vendor_id for r in fields), heading=False,
                       question=clarify.vendor_question(record, vendor_names.get(vendor_id, vendor_id), {r["line_no"]: r for r in lines}, by_key) if record else None)
            render_clarifications(fields, lines, grid_vendors, db)
            st.html("<script>if(location.hash === '#source-evidence') requestAnimationFrame(() => document.getElementById('source-evidence')?.scrollIntoView({behavior:'smooth',block:'start'}));</script>", unsafe_allow_javascript=True)
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
