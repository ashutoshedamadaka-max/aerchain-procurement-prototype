"""Recruiter-facing case study backed by the recorded evaluation snapshot."""
from __future__ import annotations
from html import escape
import json
from pathlib import Path
import streamlit as st
from app.design import card, footer, go, html, section


def _snapshot(path: Path) -> dict | None:
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else None
    except (OSError, ValueError):
        return None


def render_overview(db: Path, snapshot_path: Path, processing: list | None) -> None:
    html('''<div class="hero"><div>
<p class="eyebrow">Product case study / Applied AI</p>
<h1>Messy quotes.<br>Clear decisions.<br><em>Evidence first.</em></h1>
<p class="hero-lede">A sourcing copilot that turns five very different supplier quotes into one defensible buying decision. Built around the hardest part of enterprise AI: knowing when to ask a human.</p>
<div class="hero-tags"><span>PRODUCT STRATEGY</span><span>AGENT DESIGN</span><span>EVALUATION</span></div>
</div><div class="system-preview" aria-label="Illustration of the quote intelligence workflow">
<div class="preview-top"><span>From inbox to decision</span><span>System overview ↗</span></div>
<div class="format-row"><div class="file-tile"><b>▦</b>XLSX</div><div class="file-tile"><b>≡</b>PDF</div><div class="file-tile"><b>¶</b>DOCX</div><div class="file-tile"><b>▧</b>PHOTO</div><div class="file-tile"><b>✉</b>EMAIL</div></div>
<div class="flow-connector">↓</div><div class="engine"><div><b>Quote intelligence engine</b><small>Read → verify → normalize → evaluate</small></div><span class="engine-symbol">✳</span></div>
<div class="flow-connector">↓</div><div class="decision-preview"><p class="eyebrow">A decision you can inspect</p>
<div class="preview-line"><span>Prices on a common basis</span><span class="preview-pill">₹ / piece</span></div>
<div class="preview-line"><span>Uncertain values surfaced</span><span class="preview-pill amber">Human review</span></div>
<div class="preview-line"><span>Recommendation with provenance</span><span class="preview-pill">Source linked</span></div></div>
<p class="preview-foot">Illustrative workflow · Explore the recorded sourcing event below.</p>
</div></div>''')
    a, b, _ = st.columns([1.15, 1.15, 1.7])
    a.button('Explore the working demo →', type='primary', use_container_width=True, on_click=go, args=('Comparison',))
    b.button('Read the product thinking', use_container_width=True, on_click=go, args=('Strategy',))
    html('''<div class="meta-strip"><div><small>Ownership</small><b>Product + AI implementation</b></div><div><small>Domain</small><b>B2B procurement / Packaging</b></div><div><small>Deliverable</small><b>Working end-to-end prototype</b></div><div><small>Context</small><b>Independent case study</b></div></div>''')
    section('01', 'The problem behind the spreadsheet.', 'A quote is easy to read. Knowing whether it is comparable is the real work.')
    html('<div class="editorial-grid">' + ''.join([
        card('01 / Fragmented inputs', 'Five suppliers. Five formats.', 'The buyer receives a spreadsheet, a PDF, a Word letter, a photographed rate card and an email. Each has its own structure and omissions.'),
        card('02 / Hidden differences', 'The lowest price can mislead.', 'Per-kilogram rates, thinner paper, conditional discounts and volume surcharges make a simple price ranking unreliable.'),
        card('03 / The trust gap', 'A confident error costs more.', 'If the buyer cannot see which values need checking, they must recheck everything. Automation only helps when its uncertainty is actionable.'),
    ]) + '</div>')
    html('<div class="thesis"><h3>Make the decision<br>auditable, from the start.</h3><p>My product bet: accept the supplier’s existing workflow, absorb the format complexity, and make every important number traceable. The AI interprets the evidence. Code computes the commercial outcome. The buyer stays in control.</p></div>')
    snap = _snapshot(snapshot_path)
    split = next((item for item in (snap or {}).get('award', {}).get('scenarios', []) if item.get('scenario') == 'gated_split'), None)
    if split and isinstance(split.get('naive'), (int, float)) and isinstance(split.get('repriced'), (int, float)) and split['naive'] > 0:
        section('02', 'The saving that almost disappeared.', 'A useful AI product changes the quality of a decision, not just the speed of a task.')
        apparent, adjusted = split['naive'], split['repriced']
        bar = max(0, min(100, adjusted / apparent * 100))
        html(f'''<div class="insight-panel"><div><p class="eyebrow">Inside the recorded sourcing event</p><h3>Cheapest by line.<br>More complicated by order.</h3><p>Splitting the order changes how much each supplier wins. Volume surcharges and discount conditions then change the price. The award engine recalculates before recommending.</p><p class="source-note">Synthetic scenario, ex-freight. These are modeled savings, not realized customer impact.</p></div><div class="savings-chart"><div class="savings-label"><span>Apparent split saving</span><b>&#8377;{apparent / 1e5:.1f} lakh</b></div><div class="savings-track"><div style="width:100%"></div></div><div class="savings-label"><span>After commercial conditions</span><b>&#8377;{adjusted / 1e5:.1f} lakh</b></div><div class="savings-track"><div class="adjusted" style="width:{bar:.2f}%"></div></div><p class="source-note">Recorded evaluation: {escape(str(snap.get('generated_at', ''))[:10])}. Inspect the award page for the current recommendation and its conditions.</p></div></div>''')
        st.button('Inspect the award reasoning', on_click=go, args=('Award',))

    section('03', 'Take the product for a spin.', 'Enter at the decision, or follow the buyer through the full workflow. Recorded data needs no API call.')
    tours = [
        ('01', 'Define the buying rules', 'Inspect the 30 line items and mandatory supplier requirements.', 'RFx'),
        ('02', 'See what the AI read', 'View original supplier files beside their extracted prices and evidence.', 'Replies'),
        ('03', 'Focus human attention', 'Inspect uncertainty, supplier eligibility and the review queue.', 'Event'),
        ('04', 'Resolve the open questions', 'Turn incomplete evidence into a clarification draft for each supplier.', 'Clarify'),
        ('05', 'Compare like for like', 'Explore normalized prices. Open any cell to see its source.', 'Comparison'),
        ('06', 'Make the award decision', 'Compare a single supplier with a split, including commercial conditions.', 'Award'),
    ]
    for start in (0, 3):
        for col, (number, title, copy, page) in zip(st.columns(3, gap='medium'), tours[start:start + 3]):
            with col.container(border=True):
                html(f'<div class="tour-number">{number}</div><h3 class="tour-title">{title}</h3><p class="tour-copy">{copy}</p>')
                st.button('Explore →', key=f'tour_{page}', on_click=go, args=(page,), use_container_width=True)
    section('04', 'Evidence over a headline accuracy score.', 'The primary failure to watch: an incorrect value presented without a review flag.')
    snap = _snapshot(snapshot_path)
    if snap and all(k in snap for k in ('extraction', 'photo', 'questionnaire', 'tests')):
        ex, ph, qn, tests = (snap[k] for k in ('extraction', 'photo', 'questionnaire', 'tests'))
        metrics = [
            (f"{ex['overall']['silent_wrong']} / {ex['fields']}", 'unflagged errors · document fields', ex['overall']['silent_wrong'] > 0),
            (f"{ph['cells']['silent_wrong']} / {ph['rows']}", 'unflagged errors · photo rows', ph['cells']['silent_wrong'] > 0),
            (f"{qn['overall']['silent_wrong']} / {qn['answers']}", 'unflagged errors · questionnaire', qn['overall']['silent_wrong'] > 0),
            (str(tests['passed']), 'automated tests · recorded run', False),
        ]
        html('<div class="metric-row">' + ''.join(f'<div class="proof-metric {"warn" if warn else ""}"><strong>{value}</strong><span>{label}</span></div>' for value, label, warn in metrics) + '</div>')
        html(f'<p class="source-note">Recorded {escape(str(snap.get("generated_at", ""))[:10])} · Controlled synthetic benchmark · Different denominators, not a combined accuracy score. These results do not certify the current build.</p>')
    else:
        st.info('The evaluation snapshot is not included here. The evaluation design and product walkthrough are still available; no benchmark results are inferred.')
    st.button('Inspect results and known failures →', on_click=go, args=('Evals',))
    section('05', 'The thinking behind the build.', 'Explore the decisions that shaped both the product and its agent system.')
    left, right = st.columns(2, gap='large')
    with left:
        html(card('Product management', 'What to build. What to leave out.', 'The user problem, prioritization, scope decisions, success measures and the next experiment. A working prototype with explicit product hypotheses.'))
        st.button('Open product strategy →', on_click=go, args=('Strategy',), use_container_width=True)
    with right:
        html(card('AI engineering', 'Give the model a bounded job.', 'Document extraction, deterministic validation, a read-only analyst tool loop and evaluation against a separate reference. Inspect the architecture and try a tool.'))
        st.button('Open agent architecture →', on_click=go, args=('Architecture',), use_container_width=True)
    footer()
