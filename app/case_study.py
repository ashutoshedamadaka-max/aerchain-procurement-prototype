"""Product rationale and an interactive, API-free view of the analyst tools."""
from __future__ import annotations
from html import escape
from pathlib import Path
import sqlite3
import streamlit as st
from app.design import card, footer, go, html, page_header, section


def render_strategy() -> None:
    page_header('The product thinking', 'Design around the buyer’s decision.', 'Start with the buyer’s decision, work backwards to the evidence, and automate only what can be verified. These are the choices behind Vendor Quote Intelligence.')
    html('<div class="thesis"><h3>The job to be done</h3><p>“When suppliers send inconsistent quotes, help me identify a qualified, commercially sound option and explain my choice, so I can move an award forward without rebuilding every quote by hand.”</p></div>')
    section('01', 'Choose a problem with real friction.', 'A focused category exposes the difficult cases without making the product story abstract.')
    html('<div class="editorial-grid">' + ''.join([
        card('User / Buyer', 'Owns the recommendation.', 'The buyer needs comparable prices, a short review queue and a rationale they can defend. The synthetic persona is Priya, sourcing corrugated packaging.'),
        card('Stakeholder / Approver', 'Needs the reasoning.', 'The approval conversation needs a trail from original quote to normalized price, supplier eligibility and award conditions.'),
        card('Category / Packaging', 'A useful stress test.', 'Packaging combines per-piece, per-100 and per-kilogram pricing. Box dimensions and paper specifications make normalization a real product problem.'),
    ]) + '</div>')
    st.caption('Persona and workflow are design hypotheses informed by the project brief, not claims of completed customer research.')
    section('02', 'The choices that define the MVP.', 'Each scope decision trades breadth for a more inspectable core workflow.')
    decisions = [
        ('Accept the mess', 'Support suppliers’ existing files instead of requiring a portal or template.', 'Bet: fewer submission constraints preserve participation. Next: validate with buyers and suppliers; this is not a measured adoption result.'),
        ('Make review actionable', 'Distinguish missing, not quoted and needs review; pair uncertainty with a resolving question.', 'A missing price calls for clarification. A declined line calls for another supplier. The workflow should reflect that difference.'),
        ('Keep arithmetic in code', 'Use models for interpretation, then deterministic conversions, eligibility checks and repricing.', 'More explicit business logic to maintain, in exchange for reproducible numbers and an auditable decision trail.'),
        ('Narrow the surface area', 'Defer RFQ generation, voice, supplier shortlisting and cross-event price history.', 'Focus the prototype on quote-to-award. History-based features need a broader dataset; voice and portals add a different set of product risks.'),
    ]
    for title, choice, tradeoff in decisions:
        html(f'<div class="decision-row"><h3>{escape(title)}</h3><div><small>The decision</small><p>{escape(choice)}</p></div><div><small>Trade-off / hypothesis</small><p>{escape(tradeoff)}</p></div></div>')
    section('03', 'Measure useful trust.', 'Separate what this prototype demonstrates from what a customer pilot still needs to establish.')
    built, next_step = st.tabs(['Demonstrated in the prototype', 'Proposed pilot scorecard'])
    with built:
        html('<div class="editorial-grid">' + ''.join([
            card('Evaluation', 'Unflagged errors', 'Grade extracted fields against a separate reference. Surface silent errors and known failures instead of collapsing everything into one accuracy score.'),
            card('Decision quality', 'Commercial conditions', 'Compare award options after applying supplier gates, discount thresholds and volume-dependent pricing. Keep the evidence and assumptions visible.'),
            card('Operability', 'Cost and latency', 'Record model usage and execution time. Show recorded runs separately from current interactions, with missing usage left explicit.'),
        ]) + '</div>')
        st.button('View the recorded evaluation →', on_click=go, args=('Evals',))
    with next_step:
        st.dataframe([
            {'Measure': 'Time to a defensible recommendation', 'Method': 'Paired spreadsheet and assisted tasks on equivalent quote sets', 'Decision it informs': 'Does the workflow reduce total work, including verification?'},
            {'Measure': 'Unflagged material errors', 'Method': 'Independent review of prices, gates and final recommendations', 'Decision it informs': 'Can scope expand without increasing decision risk?'},
            {'Measure': 'Review burden', 'Method': 'Flags inspected, useful flags and time spent per event', 'Decision it informs': 'Are we directing attention to the right exceptions?'},
            {'Measure': 'Unit economics', 'Method': 'Cost per event and per successful analyst answer', 'Decision it informs': 'Which reads need a larger model or another verification pass?'},
        ], hide_index=True, use_container_width=True)
        st.caption('Proposed measures, not achieved customer outcomes. Set targets after observing the manual baseline.')
    section('04', 'What I would do next.', 'Sequence the next investment around uncertainty, not feature count.')
    html('<div class="editorial-grid">' + ''.join([
        card('Now / Close the known gaps', 'Fix questionnaire reliability.', 'Use the two recorded silent questionnaire failures as regression cases. Re-evaluate unsupported claims and missing-answer handling before widening scope.'),
        card('Next / Test transfer', 'Try unseen supplier documents.', 'Evaluate varied layouts, revised quotes and repeated runs. Add dataset fingerprints and code revisions to snapshots so results are reproducible.'),
        card('Then / Validate the workflow', 'Run a buyer pilot.', 'Observe buyers on comparable tasks. Measure decision time and review effort, then decide whether collaboration, price history or integrations deserve investment.'),
    ]) + '</div>')
    with st.expander('Project context and implementation notes'):
        st.markdown('Originally built solo in two days for a product-role assignment, then reframed as a portfolio case study. The prototype uses Python, SQLite and Streamlit. The files, companies and sourcing event are synthetic. It demonstrates product and technical choices; it does not establish production adoption or realized savings.')
        decision_log = Path(__file__).resolve().parents[1] / 'DECISIONS.md'
        if decision_log.is_file():
            st.download_button('Download the implementation decision log', decision_log.read_text(encoding='utf-8'), file_name='vqi-implementation-decisions.md', mime='text/markdown')
        else:
            st.caption('The full implementation log is available in the source repository.')
    footer()


TOOL_EXAMPLES = {
    'Inspect an arithmetic mismatch': ('explain_field', {'vendor_id': 'V2', 'line_no': 20, 'field': 'line_total'}, 'Retrieve the original value, source anchor and verification reason for Kaveri’s line 20 total.'),
    'Check supplier eligibility': ('gate_summary', {}, 'Read the evaluated mandatory gates and their attached-document or vendor-master evidence.'),
    'Summarize the sourcing event': ('event_summary', {}, 'Read the recorded scope, field states, review exposure and supplier eligibility from the store.'),
}


def execute_tool_example(db: Path, example: str) -> dict:
    """Run a fixed read-only tool; never initialize an LLM or substitute truth data."""
    db = db.resolve()
    truth = Path(__file__).resolve().parents[1] / 'dataset' / 'truth'
    if truth.resolve() in db.parents:
        raise ValueError('Choose the pipeline database, not the frozen truth database.')
    if not db.is_file():
        raise FileNotFoundError('The recorded pipeline database is not available.')
    from src.analyst import Store
    tool, arguments, _ = TOOL_EXAMPLES[example]
    store = Store(db)
    return getattr(store, tool)(**arguments)


def render_architecture(db: Path) -> None:
    page_header('The AI system', 'Bounded agents.\nInspectable decisions.', 'Models handle messy language and documents. Tools own the data and calculations. Every stage has a clear contract, and uncertainty is part of the output.')
    section('01', 'One pipeline. Explicit responsibilities.')
    nodes = [
        ('model', 'Model / Interpret', '01  Extract', 'Read the quote into structured fields with source anchors and verbatim snippets.'),
        ('', 'Code + escalation', '02  Verify', 'Check arithmetic and completeness. Escalate uncertain reads; compare independent photo passes.'),
        ('', 'Code / Calculate', '03  Normalize', 'Convert currency and pricing basis to INR per piece. Preserve derivations and assumptions.'),
        ('', 'Code / Evaluate', '04  Decide', 'Apply supplier gates, coverage and commercial conditions. Reprice award scenarios.'),
        ('model', 'Model + tools', '05  Explain', 'Answer from the normalized store through bounded, inspectable read-only tools.'),
    ]
    html('<div class="architecture">' + ''.join(f'<div class="arch-node {kind}"><small>{label}</small><h3>{title}</h3><p>{body}</p></div>' for kind, label, title, body in nodes) + '</div>')
    html('<div class="thesis"><h3>The model proposes a tool call.<br>The tool supplies the facts.</h3><p>The analyst can query the normalized store, inspect field evidence and run an award scenario. It narrates tool results instead of recalculating prices or re-reading a different source for each question.</p></div>')
    section('02', 'Inspect a real tool result.', 'These examples execute the same local tools available to the analyst. No model call, API key or simulated answer.')
    choice = st.selectbox('Choose an inspection', list(TOOL_EXAMPLES), key='architecture_example')
    tool, arguments, description = TOOL_EXAMPLES[choice]
    left, right = st.columns([1, 1.4], gap='large')
    with left:
        st.markdown(f'**{description}**')
        st.caption('Tool contract')
        st.code(f'{tool}({", ".join(f"{k}={v!r}" for k, v in arguments.items())})', language='python')
        run = st.button('Run this read-only tool →', type='primary', key='run_architecture_tool', disabled=not db.is_file(), use_container_width=True)
    with right:
        with st.container(border=True):
            if run:
                try:
                    result = execute_tool_example(db, choice)
                    st.session_state['architecture_result'] = (str(db.resolve()), choice, result)
                except (sqlite3.Error, ValueError, OSError, ImportError) as exc:
                    st.session_state.pop('architecture_result', None)
                    st.info(f'This tool result is unavailable: {exc}')
            saved = st.session_state.get('architecture_result')
            if saved and saved[:2] == (str(db.resolve()), choice):
                st.caption('Returned by the local pipeline store · Live tool execution')
                st.json(saved[2], expanded=2)
            else:
                st.markdown('**The evidence appears here.**')
                st.caption('Run the tool to inspect the actual structured response, including its sources and limitations.' if db.is_file() else 'This deployment has no pipeline database. The architecture remains available; tool execution needs the recorded data package.')
    section('03', 'Reliability is a product feature.')
    controls, states, evaluation = st.tabs(['Agent boundaries', 'Uncertainty model', 'Evaluation design'])
    with controls:
        html('<div class="editorial-grid">' + ''.join([
            card('Read-only data', 'Constrain the tool surface.', 'SQLite query-only mode and an authorizer restrict analyst queries. The model receives results; it cannot edit bids or invent supplier gate outcomes.'),
            card('Bounded execution', 'Design for a finite answer.', 'The analyst has tool-call and time budgets. Partial or interrupted execution is surfaced, and the tool trace stays visible to the user.'),
            card('Evidence provenance', 'Make a number inspectable.', 'A field carries the source file, cell or page anchor, exact snippet, state and resolving question. Normalization adds its derivation and assumptions.'),
        ]) + '</div>')
    with states:
        st.dataframe([
            {'State': 'Extracted', 'Meaning': 'Read without a review flag', 'Buyer action': 'Inspect supporting evidence as needed; this is not a confidence probability.'},
            {'State': 'Needs review', 'Meaning': 'A verifier or evidence check raised a specific concern', 'Buyer action': 'Check the source or send the resolving question.'},
            {'State': 'Missing', 'Meaning': 'The required value is absent or unresolved', 'Buyer action': 'Ask this supplier; never treat the missing price as zero.'},
            {'State': 'Not quoted', 'Meaning': 'The supplier explicitly declined the line', 'Buyer action': 'Seek another supplier for that line.'},
        ], hide_index=True, use_container_width=True)
        st.caption('Questionnaire answers additionally track unsupported claims. Those are distinct from the four price-field states.')
    with evaluation:
        st.markdown('**Build the failure cases into the benchmark.** Synthetic quotes contain inconsistent totals, mixed units, missing answers and a degraded photo. The extraction system reads supplier documents; separate graders compare its output with the frozen reference.')
        st.markdown('**Grade the failure mode, not just the answer.** Track correct and incorrect values against flagged and unflagged states. Test calculations, eligibility and award behavior separately. The recorded questionnaire errors remain visible.')
        st.button('Explore evaluation results →', on_click=go, args=('Evals',))
    section('04', 'Go from the system to the experience.')
    a, b = st.columns(2)
    a.button('Inspect the comparison workspace →', use_container_width=True, on_click=go, args=('Comparison',))
    b.button('Ask the AI analyst →', use_container_width=True, on_click=go, args=('Ask',))
    st.caption('The AI analyst uses a configured provider and may incur API charges. The read-only tool explorer above does not call a model.')
    footer()
