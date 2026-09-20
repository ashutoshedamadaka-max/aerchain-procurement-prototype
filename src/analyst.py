"""Analyst layer: a buyer's question in plain English, answered from the normalized store through read-only evidence tools.

    run_sql(query)                                  read-only SELECT against procurement.db (bid_fields, norm_prices, assumptions, ...)
    run_award(scenario_name, params, baseline_vendor)   invokes src/award.py; the model never supplies gate results
    explain_field(vendor_id, line_no, field)        the full record: value, state, reason, derivation, provenance, resolving question

The model interprets and narrates; every number comes from a tool result. If the store cannot answer, the answer says what is missing and
offers the drafted resolving question. Every answer returns prose, the tool calls (for a collapsed expander) and, where useful, a small table.
"""
import argparse
import json
import pathlib
import re
import sqlite3
import time
import math
import uuid
from statistics import median
from dataclasses import dataclass, field

from . import award, metering
from .extract.llm import _load_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = {"anthropic": "claude-opus-5", "openai": "gpt-5.5"}
MODEL = MODELS["anthropic"]
MAX_ROWS = 100
MAX_ROUNDS = 10
MAX_TOOL_CALLS = 10
REQUEST_SECONDS = 60
FINAL_RESERVE_SECONDS = 15
CALL_SECONDS = 25
ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
FIELDS = ("unit_price", "line_total", "declared_liner_gsm")
SCENARIOS = ("single_vendor", "cheapest_per_line", "gated_split", "max_vendors", "max_share")


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


class Store:
    """The only door to procurement.db. Reads only; gate results are whatever the buyer supplied, never the model's."""

    def __init__(self, db_path=ROOT / "procurement.db", gates=None, gates_source=None):
        self.db_path = str(db_path)
        if gates:                                   # an explicit override, for what-ifs and tests; by default the pipeline decides
            self.gates = sorted(gates)
            self.gates_source = gates_source or "supplied by the buyer for this session; NOT evaluated from questionnaire documents"
        else:
            self.gates, self.gates_source = self._pipeline_gates()

    def _pipeline_gates(self):
        """Gate results from questionnaire_answers. (None, None) when the questionnaire has not been evaluated; ([], ...) when it has and nobody cleared."""
        from .extract.questionnaire import gate_results
        con = self.connect()
        try:
            passing, detail = gate_results(con)
        finally:
            con.close()
        if passing is None:
            return None, None
        master = sorted(v for v in passing if any(g["evidence"] == "vendor_master" for g in detail[v].values()))
        note = f"; {', '.join(master)} clear on the buyer's vendor-master record because the response carried no questionnaire" if master else ""
        return sorted(passing), "evaluated by the pipeline from questionnaire_answers (pass on all three mandatory gates against attached documents)" + note

    def connect(self):
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
        con.set_authorizer(_authorizer)
        return con

    # ---- tools ----
    def run_sql(self, query):
        q = (query or "").strip().rstrip(";").strip()
        if not re.match(r"(?is)^(select|with)\b", q):
            return {"error": "read-only: only a single SELECT (or WITH ... SELECT) statement is allowed"}
        con, deadline = self.connect(), time.time() + 5
        con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20000)
        try:
            cur = con.execute(q)
            rows = cur.fetchmany(MAX_ROWS + 1)
            return {"columns": [d[0] for d in cur.description], "rows": [list(r) for r in rows[:MAX_ROWS]], "row_count": min(len(rows), MAX_ROWS),
                    "truncated": len(rows) > MAX_ROWS}
        except Exception as e:                                          # a bad query is information for the model, not a crash
            return {"error": f"{type(e).__name__}: {e}"}
        finally:
            con.close()

    def explain_field(self, vendor_id, line_no, field):
        if field not in FIELDS:
            return {"error": f"field must be one of {FIELDS}"}
        con = self.connect()
        con.row_factory = sqlite3.Row
        try:
            r = con.execute("SELECT b.*, s.vendor_id, s.file_name, s.received_on, s.valid_until, s.expired_at_eval, s.incoterm FROM bid_fields b "
                            "JOIN submissions s USING(submission_id) WHERE s.vendor_id=? AND b.rfx_line_no=? AND b.field_name=?", (vendor_id, line_no, field)).fetchone()
            if r is None:
                return {"error": "not_found", "detail": f"no {field} record for {vendor_id} on line {line_no} (vendor not extracted, or line not in the RFx)"}
            rec = {k: r[k] for k in ("value", "unit", "basis", "currency", "state", "reason_code", "anchor", "snippet", "derivation", "resolving_question")}
            out = {"vendor_id": vendor_id, "line_no": line_no, "field": field, "record": rec,
                   "source": {k: r[k] for k in ("file_name", "received_on", "valid_until", "expired_at_eval", "incoterm")},
                   "rfx_line": dict(con.execute("SELECT line_no, length_mm, width_mm, height_mm, ply, flute, liner_gsm, gsm_stack, print_spec, annual_qty "
                                                "FROM rfx_lines WHERE line_no=?", (line_no,)).fetchone())}
            if field == "unit_price":
                n = con.execute("SELECT * FROM norm_prices WHERE submission_id=? AND rfx_line_no=?", (r["submission_id"], line_no)).fetchone()
                if n is not None:
                    n = dict(n)
                    for k in ("derivation", "assumptions_used", "comparability_reasons", "landed_reasons"):
                        n[k] = json.loads(n[k]) if n[k] else None
                    out["normalized"] = {k: v for k, v in n.items() if k not in ("field_id", "submission_id")}
            return out
        finally:
            con.close()

    def event_summary(self):
        """Current submissions and field states, with explicit denominators and normalized review exposure."""
        con = self.connect()
        con.row_factory = sqlite3.Row
        try:
            vendors = [dict(r) for r in con.execute("SELECT vendor_id,name FROM vendors ORDER BY vendor_id")]
            submissions = [dict(r) for r in con.execute("SELECT vendor_id,submission_id,file_name,received_on FROM submissions ORDER BY vendor_id,received_on")]
            counts = dict(con.execute("SELECT b.state,COUNT(*) FROM bid_fields b JOIN submissions s USING(submission_id) WHERE " + self._latest() + " GROUP BY b.state"))
            total = sum(counts.values())
            exposure = self.review_exposure()
            exposure.pop('lines')
            gates = self.gate_summary()
            gates.pop('gates')
            return {"line_count": con.execute("SELECT COUNT(*) FROM rfx_lines").fetchone()[0],
                    "registered_vendors": vendors, "registered_vendor_count": len(vendors),
                    "recorded_submissions": submissions, "vendors_with_recorded_submissions": len({r['vendor_id'] for r in submissions}),
                    "field_states": counts, "field_count": total,
                    "extracted_pct": counts.get("extracted", 0) * 100 / total if total else None,
                    "state_note": "Counts cover recorded bid fields in each vendor's latest submission. Extracted is a workflow state, not a probability of correctness. A recorded submission is not proof every field was read.",
                    "review_exposure": exposure, "gates": gates,
                    "recorded_assumptions": [dict(r) for r in con.execute("SELECT assumption_id,value,value_text,unit,source,as_of FROM assumptions ORDER BY assumption_id")]}
        finally:
            con.close()

    @staticmethod
    def _latest():
        return "s.submission_id=(SELECT s2.submission_id FROM submissions s2 WHERE s2.vendor_id=s.vendor_id ORDER BY s2.received_on DESC,s2.submission_id DESC LIMIT 1)"

    def gate_summary(self):
        """Return pipeline gate decisions and their actual document/vendor-master evidence."""
        con = self.connect()
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute("SELECT vendor_id,q_no,gate_code,gate_status,state,reason_code,evidence_source,attachment_id,anchor,snippet,resolving_question FROM questionnaire_answers WHERE is_gate=1 ORDER BY vendor_id,q_no")]
            return {"passing_vendors": self.gates, "source": self.gates_source, "gates": rows,
                    "note": "No evaluated gate rows means unavailable, not a failed gate. Vendor-master evidence is not a certificate supplied with the response."}
        finally:
            con.close()

    def review_exposure(self):
        """Annual ex-freight INR exposure, once per vendor-line; never add raw per-kg, per-100 or USD rates."""
        con = self.connect()
        con.row_factory = sqlite3.Row
        try:
            fields = [dict(r) for r in con.execute("SELECT b.field_id,b.field_name,b.rfx_line_no,b.state,b.reason_code,b.anchor,b.snippet,b.resolving_question,s.vendor_id,s.submission_id,s.file_name,l.annual_qty FROM bid_fields b JOIN submissions s USING(submission_id) JOIN rfx_lines l ON l.line_no=b.rfx_line_no WHERE " + self._latest() + " AND (b.state='needs_review' OR (b.state='missing' AND b.reason_code='reference_unresolved')) ORDER BY s.vendor_id,b.rfx_line_no,b.field_id")]
            prices = [dict(r) for r in con.execute("SELECT p.*,s.vendor_id FROM norm_prices p JOIN submissions s USING(submission_id) WHERE " + self._latest())]
            by_pair = {(r['vendor_id'],r['rfx_line_no']): r for r in prices}
            groups = {}
            for f in fields:
                groups.setdefault((f['vendor_id'],f['rfx_line_no']), []).append(f)
            rows = []
            for (v, line), fs in groups.items():
                own = by_pair.get((v,line), {})
                rate = own.get('inr_per_piece')
                source = 'own normalized quote'
                peer_vendors = []
                used_prices = [own] if own else []
                if rate is None or not math.isfinite(rate):
                    peers = [r for r in prices if r['vendor_id'] != v and r['rfx_line_no'] == line and r['inr_per_piece'] is not None and math.isfinite(r['inr_per_piece']) and r['extraction_state'] == 'extracted' and r['comparability'] in ('comparable','comparable_with_assumptions')]
                    rate = median(r['inr_per_piece'] for r in peers) if peers else None
                    peer_vendors = [r['vendor_id'] for r in peers]
                    used_prices = peers
                    source = 'median of other vendors normalized comparable extracted prices' if peers else 'value unknown'
                qty = fs[0]['annual_qty']
                value = abs(rate * qty) if rate is not None and qty is not None else None
                rows.append({'vendor_id': v, 'line_no': line, 'annual_qty': qty, 'inr_per_piece': rate,
                             'exposure_inr': value, 'valuation_source': source, 'peer_vendors': peer_vendors,
                             'fields': fs, 'normalization_assumptions': [{'vendor_id': p.get('vendor_id'), 'assumptions': json.loads(p.get('assumptions_used') or '[]')} for p in used_prices]})
            rows.sort(key=lambda r: (r['exposure_inr'] is not None, -(r['exposure_inr'] or 0), r['vendor_id'], r['line_no']))
            known = [r['exposure_inr'] for r in rows if r['exposure_inr'] is not None]
            return {'review_field_count': len(fields), 'vendor_line_count': len(rows), 'known_exposure_inr': sum(known) if known or not rows else None,
                    'unknown_vendor_lines': sum(r['exposure_inr'] is None for r in rows), 'lines': rows,
                    'basis': 'INR, annual RFx quantity, ex-freight, before award discounts; each vendor-line counted once',
                    'note': 'Exposure is the value of affected quotes, not an expected loss or an award total. Alternative vendor quotes can cover the same demand. Peer-median values are labelled proxies, not missing vendor quotes. Spec-variant own prices are unadjusted quoted rates, not compliant award prices.'}
        finally:
            con.close()

    def run_award(self, scenario_name, params=None, baseline_vendor=None):
        p = {k: v for k, v in (params or {}).items() if v is not None}
        if scenario_name not in SCENARIOS:
            return {"error": f"scenario_name must be one of {SCENARIOS}"}
        if "gates" in p:
            return {"error": "gate results cannot be supplied in params; set gated=true to use the buyer's supplied gate results"}
        con = self.connect()
        try:
            ctx = award.load(con, p.get("basis", "ex_freight"), p.get("moq_policy", "overbuy"))
            gated = bool(p.get("gated")) or scenario_name == "gated_split"
            if gated and self.gates is None:
                return {"status": "refused", "reason": "gate_results_not_supplied",
                        "detail": "This scenario needs the vendors that cleared the mandatory gates (BRCGS packaging, in-house compression testing, FSC chain-of-custody). "
                                  "No evaluated gate results are recorded in this store and none were supplied for this session."}
            gates = self.gates if gated else None
            r = {"single_vendor": lambda: award.single_vendor(ctx, p.get("vendor"), gates), "cheapest_per_line": lambda: award.cheapest_per_line(ctx),
                 "gated_split": lambda: award.gated_split(ctx, gates), "max_vendors": lambda: award.max_vendors(ctx, int(p.get("n", 2)), gates),
                 "max_share": lambda: award.max_share(ctx, float(p.get("share", 0.7)), gates)}[scenario_name]()
            out = compact(r, self)
            if baseline_vendor:
                out["saving_vs_named_baseline"] = named_baseline(ctx, r, baseline_vendor, gates)
            return out
        finally:
            con.close()


def compact(r, store):
    """The engine result reduced to what an answer needs; figures are passed through untouched."""
    by_vendor = {}
    for line, v in (r.get("allocation") or {}).items():
        by_vendor.setdefault(v, []).append(line)
    keep = ("strategy", "params", "basis", "moq_policy", "status", "recommendable", "gates_applied", "vendors_used", "naive_total", "repriced_total",
            "repricing_effect", "earned_discount", "unearned_discount", "saving", "per_vendor", "alternatives", "needs_review_warnings", "review_block_threshold_pct")
    out = {k: r[k] for k in keep if k in r}
    out.update(coverage=dict(covered=r["coverage"]["covered"], of=r["coverage"]["of"], uncovered_lines=[u["line"] for u in r["coverage"]["uncovered"]]),
               lines_by_vendor=by_vendor, warnings=[dict(severity=w["severity"], code=w["code"], text=w["text"]) for w in r["warnings"]],
               adjustments=[a for a in r.get("adjustments", []) if a["applied"] or a["unearned_inr"]],
               gates_passed=r.get("gates_passed"), gates_source=store.gates_source if r.get("gates_applied") else None)
    return out


def named_baseline(ctx, r, vendor, gates):
    b = award.single_vendor(ctx, vendor, gates)
    if not b["allocation"]:
        return {"available": False, "vendor": vendor, "reason": f"{vendor} cannot cover every line on this basis, so a single-source {vendor} total does not exist; no saving is stated against it."}
    if set(r["allocation"]) != set(b["allocation"]):
        return {"available": False, "vendor": vendor, "reason": "the scenario does not cover the same lines as the baseline"}
    return {"available": True, "vendor": vendor, "baseline_naive_total": b["naive_total"], "baseline_repriced_total": b["repriced_total"],
            "naive_saving": b["naive_total"] - r["naive_total"], "repriced_saving": b["repriced_total"] - r["repriced_total"]}


def tool_schemas():
    """The analyst tools in the Anthropic Messages API `tools` format (name, description, input_schema)."""
    nul = lambda t: {"type": [t, "null"]}
    return [
        *[{"name": name, "description": desc, "input_schema": {"type": "object", "additionalProperties": False, "required": [], "properties": {}}} for name, desc in (
            ("event_summary", "One call for event status: recorded submissions, field-state counts and denominator, review exposure and gate results. Prefer this for event overview questions."),
            ("gate_summary", "All mandatory gate results and actual evidence sources, reasons, anchors and clarification questions."),
            ("review_exposure", "Uncertain fields and annual INR ex-freight exposure, deduplicated per vendor-line, unknown values and labelled peer proxies."))],
        {"name": "run_sql",
         "description": "Run one read-only SELECT against procurement.db (bid_fields, norm_prices, assumptions, submissions, vendors, rfx_lines, conditions, tier_rules). Max 100 rows.",
         "input_schema": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {"query": {"type": "string"}}}},
        {"name": "run_award",
         "description": "Run an award scenario through the award engine. Set params.gated=true to restrict to vendors that cleared the mandatory gates (the buyer's supplied "
                        "results; never name vendors yourself). baseline_vendor measures savings against that vendor as a single source instead of the automatic baseline.",
         "input_schema": {"type": "object", "additionalProperties": False, "required": ["scenario_name", "params", "baseline_vendor"], "properties": {
             "scenario_name": {"type": "string", "enum": list(SCENARIOS)},
             "params": {"type": "object", "additionalProperties": False, "required": ["basis", "moq_policy", "gated", "vendor", "n", "share"], "properties": {
                 "basis": {"type": ["string", "null"], "enum": ["ex_freight", "landed", None]}, "moq_policy": {"type": ["string", "null"], "enum": ["overbuy", "exclude", None]},
                 "gated": nul("boolean"), "vendor": nul("string"), "n": nul("integer"), "share": nul("number")}},
             "baseline_vendor": nul("string")}}},
        {"name": "explain_field",
         "description": "The full record for one extracted field: value, unit, basis, state, reason code, verbatim snippet and anchor, derivation, resolving question, and for prices the normalization chain.",
         "input_schema": {"type": "object", "additionalProperties": False, "required": ["vendor_id", "line_no", "field"], "properties": {
             "vendor_id": {"type": "string"}, "line_no": {"type": "integer"}, "field": {"type": "string", "enum": list(FIELDS)}}}}]


def submit_tool():
    """The schema-forced answer: the final call is made with tool_choice pinned to this tool, so the reply is always shaped like ANSWER_SCHEMA."""
    return {"name": "submit_answer", "description": "Submit the final answer to the buyer. Always the last step.", "input_schema": ANSWER_SCHEMA}


ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["prose", "table", "refused", "missing", "drafted_question"], "properties": {
    "prose": {"type": "string"},
    "table": {"anyOf": [{"type": "object", "additionalProperties": False, "required": ["title", "columns", "rows"], "properties": {
        "title": {"type": "string"}, "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}}, {"type": "null"}]},
    "refused": {"type": "boolean"}, "missing": {"type": ["string", "null"]}, "drafted_question": {"type": ["string", "null"]}}}

RULES = """You are the analyst layer of a sourcing tool. A buyer asks questions about one RFx event (corrugated cartons, 30 lines) and the vendor bids that have been extracted into a database.

THE RULE, before anything else. If the store cannot answer the question, say exactly what is missing and offer the drafted resolving question that would fill the gap. Never estimate, forecast, infer or fill in anything that was not asked for and is not in the store. A clean refusal is a correct answer; a plausible guess is a failure.

How to answer
- Numbers come only from tool results. Do not do arithmetic yourself: use SQL for sums, ratios and comparisons, and run_award for scenarios. Quote figures as the tool returned them (Indian format is fine: Rs 1.0 lakh, Rs 3.9 crore).
- Every numeric answer states, in the prose itself: the basis (ex-freight or landed; per piece or total), the coverage (how many of the 30 lines, and which are excluded and why), the assumptions in force that materially affect this number, and any low-confidence input (needs_review or derived prices). Do not bury these.
- Cite where figures came from: the vendor, line and the source anchor for extracted values (explain_field gives anchor, snippet and file); for scenarios, the strategy and parameters.
- Answer the question actually asked. If something material that was not asked shows up in a tool result (an expired quote, an unresolved review line, gates not applied), say so briefly. That is reporting, not estimating.
- If a scenario needs gate results and the tool says they were not supplied, that is a refusal: say which vendors' gate status is missing and draft the question ("Which vendors cleared BRCGS packaging certification, in-house compression testing and FSC chain-of-custody?"). Never pass or invent gate results. When gate results are used, say where they came from exactly as the tool reports it in gates_source, and never describe the source any other way.
- If a saving is small, say so plainly, and say what it is measured against. Do not talk the buyer into or out of anything beyond what the numbers show.
- Keep prose short and specific: a few sentences or a short list. Use a table only when it helps (at most about 12 rows), all cells as text.

Output: your final answer has these fields (submitted through the submit_answer tool when it is offered, otherwise as the JSON reply): prose, table (or null), refused (true only when you declined to answer the core question), missing (what the store lacks, else null) and drafted_question (the question to send or ask, else null). When refused is true you must fill missing and drafted_question, and the prose must not contain any estimate.

Store semantics
- bid_fields: one row per vendor, RFx line and field (unit_price, line_total, declared_liner_gsm). value/unit/basis/currency are AS STATED (per piece, per 100 pieces, per kg; INR or USD). state is one of extracted, needs_review (tried twice, still not sure), not_quoted (vendor declined that line), missing (should be there, is not). reason_code explains it (arith_mismatch, spec_mismatch_gsm, spec_incomplete, reference_unresolved, line_absent, declined_capability, basis_per_100, basis_per_kg, stored_as_text ...). anchor + snippet are the verbatim source. resolving_question is the drafted clarification for the vendor. Vendor comes from submissions (join on submission_id).
- norm_prices: the same price rows normalized to INR per piece (inr_per_piece), with derivation, assumptions_used (each with its impact per 1 percent), comparability (comparable | comparable_with_assumptions | not_comparable) and comparability_reasons. Ex-freight. spec_variance rows carry declared_liner_gsm, spec_liner_gsm, board_weight_ratio and adjusted_inr_per_piece (quoted price divided by the board-weight ratio: an estimate that assumes price is proportional to board weight). Landed cost: landed_inr_per_piece and landed_comparability exist only where freight is stated or the buyer set an estimate; otherwise freight_status is not_evaluated and landed cost is unknown.
- assumptions: every editable assumption (FX, take-up factors, freight estimates and their impacts).
- tier_rules and conditions: each vendor's discount and commercial terms as condition plus effect; run_award applies them at the allocated volume.
- Line 1..30 are RFx lines; rfx_lines holds the spec and annual_qty. Small boxes are the low line numbers with the smallest dimensions: check rfx_lines rather than assuming.
- questionnaire_answers and attachments hold recorded answers, gate decisions and evidence. Read their current contents; do not assume a vendor is absent. Inventory below reports what exists now.
- Last year's prices, forecasts and performance history must not be assumed available. Verify the schema and records before claiming availability.
- Prefer event_summary, gate_summary and review_exposure over reconstructing these reports through many SQL calls. Use explain_field for individual evidence and run_award for allocations.
- Stop once enough evidence answers the question. Never repeat an identical tool call. After a query error, make at most one correction.
- When asked to finalize, answer from evidence already returned. Clearly label any unanswered part; execution limits are NOT missing vendor data. Do not draft a vendor clarification for a technical failure."""


def system_prompt(store):
    con = store.connect()
    try:
        ddl = "\n".join(s for (s,) in con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name"))
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()}
        vendors = ", ".join(f"{v} ({n}, {f})" for v, n, f in con.execute("SELECT v.vendor_id, v.name, v.response_format FROM vendors v ORDER BY 1"))
        fr = con.execute("SELECT value FROM assumptions WHERE assumption_id='freight_estimate_pct_of_order_value'").fetchone()
        over = con.execute("SELECT assumption_id, value FROM assumptions WHERE assumption_id LIKE 'freight_estimate_pct:%'").fetchall()
    finally:
        con.close()
    facts = (f"Store inventory now: registered vendors: {vendors}. Row counts: {counts}. "
             f"Freight: global estimate {'unset' if not fr or fr[0] is None else str(fr[0]) + '%'}"
             f"{'; vendor overrides ' + str(dict(over)) if over else ''}. "
             f"Gate results: {'NOT available: none evaluated from questionnaire_answers and none supplied' if store.gates is None else (', '.join(store.gates) or 'no vendor') + ' cleared all mandatory gates; source: ' + store.gates_source}.")
    return f"{RULES}\n\nDatabase schema (SQLite):\n{ddl}\n\n{facts}"


@dataclass
class Answer:
    prose: str
    table: dict | None = None
    refused: bool = False
    missing: str | None = None
    drafted_question: str | None = None
    tool_calls: list = field(default_factory=list)
    model: str = MODEL
    status: str = "complete"
    execution_note: str | None = None
    elapsed_seconds: float = 0.0
    model_requests: int = 0

    @property
    def expander(self):
        """What a UI renders as a collapsed expander under the prose."""
        return {"label": f"How this was computed ({len(self.tool_calls)} tool call{'s' if len(self.tool_calls) != 1 else ''})", "collapsed": True, "calls": self.tool_calls}

    def to_dict(self):
        return {"prose": self.prose, "table": self.table, "refused": self.refused, "missing": self.missing, "drafted_question": self.drafted_question,
                "expander": self.expander, "model": self.model, "status": self.status, "execution_note": self.execution_note,
                "elapsed_seconds": self.elapsed_seconds, "model_requests": self.model_requests}

    def markdown(self):
        out = [self.prose]
        if self.refused or self.missing:
            out += ["", f"**Missing from the store:** {self.missing}"] if self.missing else []
            out += [f"**Drafted question:** {self.drafted_question}"] if self.drafted_question else []
        if self.table:
            t = self.table
            out += ["", f"**{t['title']}**", "", "| " + " | ".join(t["columns"]) + " |", "|" + "---|" * len(t["columns"])]
            out += ["| " + " | ".join(r) + " |" for r in t["rows"]]
        calls = "\n\n".join(f"`{c['tool']}` {json.dumps(c['arguments'])}\n```\n{c['result'][:1500]}\n```" for c in self.tool_calls)
        out += ["", f"<details><summary>{self.expander['label']}</summary>\n\n{calls}\n\n</details>"]
        return "\n".join(out)


class Analyst:
    """Provider is chosen by the key available (ANALYST_PROVIDER overrides): Anthropic tool use, or OpenAI Responses tool calling. Same tools, prompt and answer contract."""

    def __init__(self, store=None, model=None, client=None, provider=None):
        import os
        if client is None:
            _load_env()
            provider = provider or os.environ.get("ANALYST_PROVIDER") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai" if os.environ.get("OPENAI_API_KEY") else None)
            if provider is None:
                raise RuntimeError("No model key found: set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(timeout=CALL_SECONDS, max_retries=0)
            else:
                from openai import OpenAI
                client = OpenAI(timeout=CALL_SECONDS, max_retries=0)
        provider = provider or ("anthropic" if hasattr(client, "messages") else "openai")
        assert provider in MODELS, provider
        self.provider, self.store, self.model, self.client = provider, store or Store(), model or MODELS[provider], client

    def _dispatch(self, name, args):
        s = self.store
        return {"event_summary": s.event_summary, "gate_summary": s.gate_summary, "review_exposure": s.review_exposure, "run_sql": lambda: s.run_sql(args["query"]), "run_award": lambda: s.run_award(args["scenario_name"], args.get("params"), args.get("baseline_vendor")),
                "explain_field": lambda: s.explain_field(args["vendor_id"], args["line_no"], args["field"])}[name]()

    def _call(self, api, effort=None, **kw):
        remaining = getattr(self, "_deadline", time.monotonic() + REQUEST_SECONDS) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Analyst request time budget exhausted")
        owner = getattr(api, "__self__", None)
        # SDK requests get the remaining deadline; scripted clients need no transport options.
        if owner is not None and hasattr(owner, "_client"):
            kw["timeout"] = min(CALL_SECONDS, remaining)
        self._requests = getattr(self, "_requests", 0) + 1
        if getattr(self, "_progress", None):
            self._progress(f"Checking evidence · model request {self._requests}")
        t = time.perf_counter()
        resp = api(**kw)
        i, o, c = metering.usage(resp)
        stage = metering.current()
        metering.record(self.provider, self.model, i, o, time.perf_counter() - t,
                        stage if stage.startswith("analyst") else "analyst", effort, c,
                        detail=json.dumps({"request_id": getattr(self, "_request_id", None), "request_number": self._requests}))
        return resp

    def ask(self, question, progress=None):
        self._deadline = time.monotonic() + REQUEST_SECONDS
        self._requests, self._progress, self._request_id = 0, progress, uuid.uuid4().hex
        self._calls, self._cache, self._errors, self._stop = [], {}, 0, None
        started = time.perf_counter()
        try:
            answer = self._ask_anthropic(question) if self.provider == "anthropic" else self._ask_openai(question)
        except Exception as exc:
            # Do not turn provider timeouts, malformed output or a tool failure into missing source data.
            answer = self._fallback(f"Analysis could not finish ({type(exc).__name__}).")
        answer.elapsed_seconds = round(time.perf_counter() - started, 2)
        answer.model_requests = self._requests
        if answer.refused and answer.status == "complete":
            answer.status = "unavailable"
        return answer

    def _execute(self, name, args):
        key = json.dumps([name, args], sort_keys=True)
        if key in self._cache:
            self._stop = "Repeated tool request; using evidence already collected."
            return self._cache[key]
        if self._stop or len(self._calls) >= MAX_TOOL_CALLS or time.monotonic() >= self._deadline - FINAL_RESERVE_SECONDS:
            self._stop = "Evidence budget reached."
            return {"error": "Evidence budget reached; finalize with available evidence."}
        try:
            result = self._dispatch(name, args)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        self._calls.append({"tool": name, "arguments": args, "result": json.dumps(result, default=str)})
        self._cache[key] = result
        if isinstance(result, dict) and result.get("error"):
            self._errors += 1
            if self._errors >= 2:
                self._stop = "Two tool errors; finalize with available evidence."
        return result

    def _finish_now(self):
        return bool(self._stop or len(self._calls) >= MAX_TOOL_CALLS or time.monotonic() >= self._deadline - FINAL_RESERVE_SECONDS)

    def _answer(self, d):
        if not isinstance(d, dict) or not isinstance(d.get("prose"), str) or not d['prose'].strip():
            raise ValueError("No usable final answer")
        return Answer(d["prose"], d.get("table"), bool(d.get("refused")), d.get("missing"), d.get("drafted_question"), self._calls, self.model,
                      status="partial" if d.get("missing") and not d.get("refused") else "complete",
                      execution_note=self._stop)

    def _fallback(self, reason):
        good = []
        for call in self._calls:
            result = json.loads(call['result'])
            if isinstance(result, dict) and not result.get('error'):
                good.append(call)
        table = None
        # Preserve verified tabular facts without asking another model or inventing a conclusion.
        sql = next((json.loads(c['result']) for c in reversed(good) if c['tool'] == 'run_sql'), None)
        if sql and sql.get('columns'):
            table = {'title': 'Retrieved records — partial evidence, not a completed conclusion',
                     'columns': sql['columns'], 'rows': [[str(v) if v is not None else 'not available' for v in row] for row in sql['rows'][:12]]}
        summary = next((json.loads(c['result']) for c in reversed(good) if c['tool'] == 'event_summary'), None)
        if summary:
            exposure = summary['review_exposure']
            table = {'title': 'Verified event summary — narration incomplete', 'columns': ['Measure', 'Recorded value'], 'rows': [
                ['Vendors with recorded submissions', str(summary['vendors_with_recorded_submissions'])],
                ['Recorded fields in latest submissions', str(summary['field_count'])],
                ['Extracted fields (workflow state, not accuracy probability)', str(summary['field_states'].get('extracted', 0))],
                ['Known review exposure (INR, annual quantity, ex-freight)', str(exposure['known_exposure_inr']) if exposure['known_exposure_inr'] is not None else 'not available'],
                ['Vendor-lines with unknown exposure', str(exposure['unknown_vendor_lines'])],
                ['Exposure scope', exposure['basis']], ['Exposure limitation', exposure['note']]]}
        prose = "I retrieved supporting evidence, but could not complete the answer. The retrieved results are available below." if good else "The analysis could not complete. No usable evidence was retrieved; please retry."
        return Answer(prose, table=table, tool_calls=self._calls, model=self.model,
                      status="partial" if good else "unavailable", execution_note=reason)

    def _ask_openai(self, question):
        tools = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"], "strict": True} for t in tool_schemas()]
        kw = dict(model=self.model, instructions=system_prompt(self.store), tools=tools, reasoning={"effort": "medium"}, max_output_tokens=20000,
                  text={"format": {"type": "json_schema", "name": "analyst_answer", "strict": True, "schema": ANSWER_SCHEMA}})
        resp = self._call(self.client.responses.create, effort="medium", input=[{"role": "user", "content": question}], **kw)
        for round_no in range(MAX_ROUNDS + 1):
            fcalls = [o for o in resp.output if o.type == "function_call"]
            if not fcalls:
                return self._answer(json.loads(resp.output_text))
            if round_no == MAX_ROUNDS:
                return self._fallback("The provider did not return a final answer after the reserved finalization step.")
            outputs = []
            for c in fcalls:
                try:
                    args = json.loads(c.arguments)
                except ValueError:
                    args = {}
                result = self._execute(c.name, args)
                outputs.append({"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(result, default=str)})
            finalizing = self._finish_now() or round_no == MAX_ROUNDS - 1
            if finalizing:
                kw['tool_choice'] = 'none'
                kw['instructions'] += "\nFinalize now from the returned evidence. State any unanswered part explicitly; do not request more tools."
            resp = self._call(self.client.responses.create, effort="medium", previous_response_id=resp.id, input=outputs, **kw)
            if finalizing:
                return self._answer(json.loads(resp.output_text))
        return self._fallback("Evidence budget reached.")

    @staticmethod
    def _blocks(content):
        return [{"type": "text", "text": b.text} if b.type == "text" else {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                for b in content if b.type in ("text", "tool_use")]

    def _ask_anthropic(self, question):
        system, tools = system_prompt(self.store), tool_schemas() + [submit_tool()]
        messages = [{"role": "user", "content": question}]
        for _ in range(MAX_ROUNDS):
            resp = self._call(self.client.messages.create, model=self.model, max_tokens=8000, system=system, tools=tools, messages=messages)
            uses = [b for b in resp.content if b.type == "tool_use"]
            submitted = next((b for b in uses if b.name == 'submit_answer'), None)
            if submitted and all(b.name == 'submit_answer' for b in uses):
                return self._answer(submitted.input)
            messages.append({"role": "assistant", "content": self._blocks(resp.content)})
            if not uses:
                break
            results = []
            for u in uses:
                result = ({"error": "Submit the answer after all evidence tools have returned."} if u.name == 'submit_answer' else self._execute(u.name, u.input))
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(result, default=str)})
            messages.append({"role": "user", "content": results})
            if self._finish_now():
                break
        messages.append({"role": "user", "content": "Give your final answer to the buyer now, by calling submit_answer. Use available evidence; explicitly state any unanswered part."})
        final = self._call(self.client.messages.create, model=self.model, max_tokens=4000, system=system, tools=[submit_tool()], messages=messages,
                           tool_choice={"type": "tool", "name": "submit_answer"})
        d = next(b.input for b in final.content if b.type == "tool_use" and b.name == "submit_answer")
        return self._answer(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--db", default=str(ROOT / "procurement.db"))
    ap.add_argument("--gates", help="override the gate results with these comma-separated vendors (default: read from questionnaire_answers)")
    a = ap.parse_args()
    metering.configure(a.db)
    print(Analyst(Store(a.db, a.gates.split(",") if a.gates else None)).ask(a.question).markdown())


if __name__ == "__main__":
    main()
