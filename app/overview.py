"""Overview page: explains the product to someone who has never seen procurement software. Numbers come from the database and eval snapshot when present."""
from __future__ import annotations

from contextlib import closing
from html import escape
import json
from pathlib import Path
import sqlite3

import streamlit as st

from app import fmt

FORMAT_LABELS = {"xlsx": ("📊", "Excel sheet"), "pdf": ("📄", "PDF with a footnote"), "docx": ("📝", "Word document"),
                 "jpg": ("📷", "Phone photo of a rate card"), "txt": ("✉️", "One-line email")}

CSS = """
<style>
.ov {--accent:#2563eb; --muted:rgba(128,128,128,.95); --line:rgba(128,128,128,.25); --soft:rgba(37,99,235,.07)}
.ov-kicker {font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700;margin:0 0 .6rem}
.ov-hero h1 {font-size:clamp(2rem,4.2vw,3.1rem);line-height:1.08;letter-spacing:-.02em;margin:0 0 1rem;padding:0}
.ov-lede {font-size:1.15rem;line-height:1.6;max-width:46rem;margin:0}
.ov-section {margin:2.6rem 0 .9rem}
.ov-section h2 {font-size:1.6rem;letter-spacing:-.01em;margin:0 0 .35rem;padding:0}
.ov-section p {color:var(--muted);margin:0;max-width:46rem;line-height:1.55}
.ov-grid {display:grid;gap:12px;grid-template-columns:repeat(var(--cols,4),minmax(0,1fr))}
@media (max-width:900px){.ov-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:520px){.ov-grid{grid-template-columns:1fr}}
.ov-card {border:1px solid var(--line);border-radius:12px;padding:16px 18px;background:var(--soft)}
.ov-card .icon {font-size:1.5rem}
.ov-card h4 {margin:.35rem 0 .3rem;font-size:1rem;padding:0}
.ov-card p {margin:0;font-size:.9rem;line-height:1.5;color:var(--muted)}
.ov-card .tag {display:inline-block;font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--accent);margin-bottom:.2rem}
.ov-flow {display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));counter-reset:step}
.ov-step {border:1px solid var(--line);border-radius:12px;padding:14px 16px;position:relative}
.ov-step b {display:block;font-size:1rem;margin-bottom:.25rem}
.ov-step span {font-size:.88rem;line-height:1.45;color:var(--muted)}
.ov-step::before {counter-increment:step;content:counter(step);display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;
 background:var(--accent);color:#fff;font-weight:700;font-size:.85rem;margin-bottom:.5rem}
.ov-stat {font-size:2rem;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.ov-compare {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px}
.ov-compare div {border-radius:12px;padding:14px 18px}
.ov-compare .paper {background:rgba(22,163,74,.12)}
.ov-compare .real {background:rgba(217,119,6,.16)}
.ov-compare small {display:block;color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.08em}
.ov-quote {border-left:4px solid var(--accent);padding:.4rem 0 .4rem 1rem;font-size:1.15rem;line-height:1.5;margin:1rem 0;max-width:46rem}
.ov-note {font-size:.82rem;color:var(--muted);margin-top:.5rem}
</style>
"""


def _html(body: str) -> None:
    st.markdown(f'<div class="ov">{body}</div>', unsafe_allow_html=True)


def _section(title: str, intro: str = "") -> None:
    _html(f'<div class="ov-section"><h2>{title}</h2>{f"<p>{intro}</p>" if intro else ""}</div>')


def _cards(items: list[tuple[str, str, str]], tag: str = "", cols: int = 4) -> None:
    _html(f'<div class="ov-grid" style="--cols:{cols}">' + "".join(
        f'<div class="ov-card">{f"<span class=tag>{escape(tag)}</span>" if tag else ""}<div class="icon">{i}</div><h4>{t}</h4><p>{d}</p></div>'
        for i, t, d in items) + "</div>")


def _go(page: str) -> None:
    st.session_state["page"] = page


def _vendors(db: Path) -> list[tuple[str, str]]:
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            return con.execute("SELECT name,response_format FROM vendors ORDER BY vendor_id").fetchall()
    except (sqlite3.Error, ValueError):
        return []


def _snapshot(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_overview(db: Path, snapshot_path: Path, processing: list[tuple[str, float, float]] | None) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    _html('<div class="ov-hero"><p class="ov-kicker">AI prototype · Procurement · Built solo in 2 days</p>'
          '<h1>Five suppliers sent quotes in five different formats.<br>Who should win the order?</h1>'
          '<p class="ov-lede">This workspace reads every quote, puts all the prices on the same basis, '
          '<b>flags anything it isn\'t sure about</b>, and recommends who should get the order. Every number links back to the line in the supplier\'s document it came from.</p></div>')
    st.write("")
    a, b, _ = st.columns([1, 1, 2])
    a.button("Start the 2-minute tour →", type="primary", use_container_width=True, on_click=_go, args=("Comparison",))
    b.button("See the test results", use_container_width=True, on_click=_go, args=("Evals",))

    _section("The situation", "A buyer, Priya, needs about 20 lakh cardboard boxes a year across 30 sizes, worth roughly ₹4 crore. "
             "She sends one request for quotation (RFQ) to five suppliers. Each replies in its own way:")
    vendors = _vendors(db)
    _cards([(FORMAT_LABELS.get(f, ("📎", f))[0], escape(n), FORMAT_LABELS.get(f, ("", f))[1]) for n, f in vendors] or
           [(i, l, "") for i, l in FORMAT_LABELS.values()], cols=5)
    _html('<p class="ov-note">Today a buyer retypes all of this into a spreadsheet by hand, guesses at units, and can easily miss a footnote that changes the price. '
          'When finance later asks "why this supplier?", the reasoning lives only in her head.</p>')

    _section("How it works", "Five steps. The AI only <b>reads</b>. Plain code does all of the maths, so a wrong total is a bug that can be found and fixed, not a guess.")
    _html('<div class="ov-flow">' + "".join(f'<div class="ov-step"><b>{t}</b><span>{d}</span></div>' for t, d in (
        ("Read", "An AI model reads each file, including the photo, and copies out every price with the exact text it came from."),
        ("Double-check", "Code re-checks every value: does quantity × rate match the total? Do two separate reads of the photo agree?"),
        ("Put on one basis", "Prices per piece, per 100 and per kilogram, in rupees or dollars, all become ₹ per box."),
        ("Apply the rules", "Suppliers missing a required certificate are excluded. Discounts and volume conditions are priced in."),
        ("Recommend & explain", "One supplier or a split? The answer comes with the evidence, the trade-off and questions to send back."),
    )) + "</div>")
    _html('<p class="ov-note">Every value carries one of four labels: <b>✓ read cleanly</b>, <b>⚠ needs a human check</b>, <b>✗ missing</b> (ask the supplier) or <b>– not quoted</b> (find someone else).</p>')

    _section("Two things it caught that a spreadsheet would miss")
    left, right = st.columns(2, gap="large")
    with left:
        _html('<div class="ov-card"><span class="tag">Finding 1 · Like-for-like</span><h4>The cheapest quote wasn\'t for the same box</h4>'
              '<p>Om Sai looked 20% cheaper than the other suppliers. But on those boxes it had quoted thinner paper (120 GSM instead of the 150 GSM asked for). '
              'Compared like-for-like, the real gap was 8.7%. It also failed all three mandatory requirements, so it can\'t win at all.</p></div>')
    with right:
        split = next((s for s in (_snapshot(snapshot_path) or {}).get("award", {}).get("scenarios", []) if s["scenario"] == "gated_split"), None)
        naive, real, pct = (f"₹{split['naive'] / 1e5:.1f} lakh", f"₹{split['repriced'] / 1e5:.1f} lakh", f"{split['pct']:.2f}%") if split else ("₹11.9 lakh", "₹1.0 lakh", "0.26%")
        _html('<div class="ov-card"><span class="tag">Finding 2 · Hidden conditions</span><h4>Splitting the order looked smart. It wasn\'t.</h4>'
              f'<div class="ov-compare" style="margin:.6rem 0"><div class="paper"><small>Saving on paper</small><span class="ov-stat">{naive}</span></div>'
              f'<div class="real"><small>Saving in reality</small><span class="ov-stat">{real}</span></div></div>'
              f'<p>Each supplier\'s price depended on how much it was awarded. Once those conditions are applied, the split saves {pct}, below the 1% threshold, so the system says <b>don\'t split</b>.</p></div>')

    _html('<div class="ov-quote">"The system\'s job is not to always be right. It is to always be right about whether it is right."</div>')

    _section("Can you trust it?", "I built a hidden answer key for every quote, then graded the system against it. "
             "What matters most is a <b>silent error</b>: a wrong number the system presented as certain.")
    snap = _snapshot(snapshot_path)
    if snap:
        ex, ph, qn, tt = snap["extraction"], snap["photo"], snap["questionnaire"], snap["tests"]
        cols = st.columns(4)
        for col, (value, label) in zip(cols, (
            (f"{ex['overall']['silent_wrong']} / {ex['fields']}", "silent errors in document fields"),
            (f"{ph['cells']['silent_wrong']} / {ph['rows']}", "silent errors on the phone photo. Every misread price was flagged."),
            (f"{qn['overall']['silent_wrong']} / {qn['answers']}", "silent errors in questionnaire answers (known, listed on Evals)"),
            (f"{tt['passed']}", "automated tests passing when recorded"),
        )):
            with col.container(border=True):
                _html(f'<div class="ov-stat">{value}</div><p class="ov-note" style="margin-top:.3rem">{label}</p>')
        st.caption(f"Recorded {snap['generated_at'][:10]} on a synthetic event. The full breakdown, including what went wrong, is on the Evals page.")
    else:
        st.caption("No eval snapshot is available in this deployment.")

    _section("Take the tour", "Each page shows one stage of a buyer's day. Open them in order.")
    tour = (("RFx", "1 · What was asked", "The 30 box sizes, quantities and the supplier questionnaire, including three mandatory requirements."),
            ("Event", "2 · What came back", "Who replied, how long processing took, and the queue of values the system wants a human to check, ranked by ₹ at stake."),
            ("Comparison", "3 · Who should win", "All quotes side by side. Hover any cell to see the source text. Try the 'Split across vendors' recommendation."),
            ("Ask", "4 · Ask in plain English", "Questions like \"should we split the order?\" answered from the data, with every lookup it made shown."),
            ("Evals", "5 · How well it works", "The graded results: what it got right, what it flagged, and what it got wrong."))
    for row in (tour[:3], tour[3:]):
        for col, (page, title, text) in zip(st.columns(3), row):
            with col.container(border=True):
                st.markdown(f"**{title}**")
                st.caption(text)
                st.button(f"Open {page} →", key=f"tour_{page}", on_click=_go, args=(page,), use_container_width=True)

    _section("How I built it", "Solo, in two days, as a product take-home. I made the scope cuts deliberately; each one is written up in the decision log.")
    if processing:
        cost, minutes = sum(c for _, _, c in processing if c == c), sum(t for _, t, _ in processing) / 60
        run = f"~₹{fmt.indian(cost, 0)} and ~{minutes:.0f} minutes"
    else:
        run = "a few minutes and about ₹100"
    _cards([
        ("🧠", "AI reads, code computes", "Language models (OpenAI GPT-5.5 and GPT-5.4-mini) only read documents and pick tools. Python does every conversion and total."),
        ("🔁", "Two reads of the photo", "The photographed rate card is read twice, independently. Where the reads disagree, the value is flagged instead of guessed."),
        ("🧾", "Evidence for every number", "Each value stores its cell, page or line and the exact text it came from. That's what the hover cards on Comparison show."),
        ("🧪", "A hidden answer key", "The five quotes were generated from box physics (dimensions, paper weight, ply), with planted traps and a separate answer key for grading."),
        ("💸", "Cheap to run", f"Processing all five quotes plus a scripted analyst session cost {run}."),
        ("🧰", "Simple stack", "Python, SQLite and Streamlit. Plain functions for the five award strategies, and SQL as the analyst's query plan."),
    ], cols=3)
    st.write("")
    _cards([
        ("✂️", "No supplier portal", "Forcing suppliers into a template means fewer of them bid, and fewer bids cost more than messy files do. So the system absorbs the mess."),
        ("✂️", "No AI-drafted RFQ", "Not part of what was being evaluated, and the quotes were written against a fixed RFQ anyway."),
        ("✂️", "No voice input", "Trade terms like ply, GSM and bursting factor transcribe badly. Typed questions exercise the same reasoning."),
        ("⏭️", "Price history: next", "Needs many events to be useful. The data model already supports a second event without changes."),
    ], tag="Cut or deferred, on purpose")

    with st.expander("Glossary: the procurement terms used in this app"):
        st.markdown(
            "- **RFx / RFQ:** a request for quotation, the document a buyer sends suppliers listing what they need.\n"
            "- **Event:** one round of quoting: the RFQ plus every reply to it.\n"
            "- **Ply:** layers of cardboard (3, 5 or 7). More ply means a stronger, pricier box.\n"
            "- **GSM:** grams per square metre, how heavy (and strong) the paper is. Thinner paper is cheaper.\n"
            "- **Gate:** a mandatory requirement, such as a packaging-safety certificate or in-house strength testing. Failing one rules a supplier out whatever its price.\n"
            "- **Ex-freight / landed cost:** price before delivery charges, versus the total including delivery.\n"
            "- **Normalisation:** converting every quote to the same unit (₹ per box) so they can be compared.\n"
            "- **Split award:** giving different box sizes to different suppliers instead of one supplier getting everything.")
    st.caption("All companies, people and quotes in this demo are synthetic. This is a prototype, not a production approval system.")
