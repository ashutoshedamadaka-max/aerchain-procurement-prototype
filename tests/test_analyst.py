"""Analyst layer: the tools, the prompt, the answer contract and the tool-call loop (with a scripted stand-in client, no model calls).
Live model tests run only with RUN_LIVE_ANALYST=1 and API credit."""
import json
import os
import pathlib
import re
import sqlite3
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from src.analyst import Analyst, Answer, Store, system_prompt, tool_schemas
from src.normalize import Assumptions, normalize_all

SCHEMA = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "schema.sql").read_text()


@pytest.fixture
def dbfile(tmp_path):
    """V1 and V2 cover lines 1-3 (V1 with a needs_review price and a resolving question); V3 covers line 1 only, at 120 GSM."""
    path = tmp_path / "p.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for n, q in ((1, 100000), (2, 50000), (3, 2000)):
        con.execute("INSERT INTO rfx_lines (line_no,sku,length_mm,width_mm,height_mm,ply,flute,liner_gsm,gsm_stack,bf,print_spec,annual_qty) "
                    "VALUES (?,?,300,200,150,3,'C',150,'150/120/150',18,'plain',?)", (n, f"S{n}", q))
    prices = {"V1": {1: 10.0, 2: 10.0, 3: 10.0}, "V2": {1: 10.4, 2: 9.0, 3: 9.0}, "V3": {1: 5.5}}
    for i, v in enumerate(prices, 1):
        con.execute("INSERT INTO vendors (vendor_id,name,response_format,moq_pcs) VALUES (?,?,?,?)", (v, f"Vendor {i}", "xlsx", None))
        con.execute("INSERT INTO submissions (submission_id,vendor_id,file_name,format,received_on,valid_until,expired_at_eval,eval_date,currency) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"S{i}", v, f"f{i}.xlsx", "xlsx", "2026-03-01", "2026-04-01", 0, "2026-03-16", "INR"))
        con.execute("INSERT INTO conditions (submission_id,kind,snippet,anchor) VALUES (?,'freight','Freight extra.','B5')", (f"S{i}",))
        for n, p in prices[v].items():
            state, q = ("needs_review", "Vendor 1: which is right, the rate or the total?") if (v, n) == ("V1", 2) else ("extracted", None)
            con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,reason_code,anchor,snippet,derivation,resolving_question) "
                        "VALUES (?,?,'unit_price',?,'INR/piece','per_piece','INR',?,?,?,?,?,?)", (f"S{i}", n, p, state, "arith_mismatch" if q else None, f"H{n}", str(p), None, q))
            con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,state,anchor,snippet) VALUES (?,?,'declared_liner_gsm',?,'extracted','E1','x')",
                        (f"S{i}", n, 120 if v == "V3" else 150))
    normalize_all(con)
    con.commit()
    con.close()
    return path


def test_run_sql_reads_and_cannot_write(dbfile):
    s = Store(dbfile)
    r = s.run_sql("SELECT v.vendor_id, COUNT(*) n FROM norm_prices p JOIN submissions v USING(submission_id) GROUP BY 1 ORDER BY 1")
    assert r["rows"] == [["V1", 3], ["V2", 3], ["V3", 1]] and r["columns"] == ["vendor_id", "n"]
    for bad in ("INSERT INTO vendors (vendor_id,name,response_format) VALUES ('X','x','x')", "UPDATE bid_fields SET value=1", "DELETE FROM bid_fields",
                "DROP TABLE vendors", "PRAGMA writable_schema=1", "ATTACH DATABASE 'x.db' AS x", "SELECT 1; DROP TABLE vendors", "WITH t AS (SELECT 1) DELETE FROM vendors"):
        assert "error" in s.run_sql(bad), bad
    assert s.run_sql("SELECT COUNT(*) FROM bid_fields")["rows"][0][0] > 0                      # nothing was deleted
    big = s.run_sql("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<500) SELECT x FROM c")
    assert big["truncated"] and big["row_count"] == 100


def test_explain_field_returns_the_whole_record(dbfile):
    e = Store(dbfile).explain_field("V1", 2, "unit_price")
    assert e["record"]["state"] == "needs_review" and e["record"]["reason_code"] == "arith_mismatch" and e["record"]["resolving_question"].startswith("Vendor 1:")
    assert e["record"]["anchor"] == "H2" and e["source"]["file_name"] == "f1.xlsx" and e["rfx_line"]["annual_qty"] == 50000
    assert e["normalized"]["inr_per_piece"] == 10.0 and e["normalized"]["comparability"] == "comparable" and isinstance(e["normalized"]["derivation"], list)
    assert "error" in Store(dbfile).explain_field("V9", 1, "unit_price") and "error" in Store(dbfile).explain_field("V1", 1, "colour")


def test_the_model_cannot_supply_gate_results(dbfile):
    s = Store(dbfile, gates=["V1", "V2"])
    assert "cannot be supplied" in s.run_award("gated_split", {"gates": ["V3"]})["error"]
    refused = Store(dbfile).run_award("gated_split", {"gated": True})
    assert refused["status"] == "refused" and refused["reason"] == "gate_results_not_supplied" and "BRCGS" in refused["detail"]
    assert Store(dbfile).run_award("gated_split", {})["status"] == "refused"                   # gated_split is always gated


def test_a_gated_award_reports_the_supplied_gates_and_a_named_baseline(dbfile):
    s = Store(dbfile, gates=["V1", "V2"])
    r = s.run_award("gated_split", {"gated": True, "basis": None}, "V2")
    assert r["gates_passed"] == ["V1", "V2"] and "NOT evaluated" in r["gates_source"] and set(r["lines_by_vendor"]) <= {"V1", "V2"}
    assert r["saving_vs_named_baseline"]["available"] and r["saving_vs_named_baseline"]["vendor"] == "V2"
    assert s.run_award("gated_split", {"gated": True}, "V3")["saving_vs_named_baseline"]["available"] is False        # V3 prices one line only
    assert s.run_award("cheapest_per_line", {})["gates_applied"] is False                                        # ungated stays labelled
    assert any(w["code"] == "landed_unknown" for w in s.run_award("cheapest_per_line", {})["warnings"])


def test_prompt_states_the_rule_first_and_the_store_inventory(dbfile):
    p = system_prompt(Store(dbfile))
    assert p.index("THE RULE") < p.index("How to answer") < p.index("CREATE TABLE") and "drafted resolving question" in p and "Never estimate" in p
    assert "Gate results: NOT available" in p and "Freight: global estimate unset" in p
    assert "supplied by the buyer, not evaluated from documents" not in p
    override = system_prompt(Store(dbfile, gates=["V1", "V2"]))
    assert "Gate results: V1, V2 cleared all mandatory gates; source: supplied by the buyer" in override
    normalize_all(sqlite3.connect(dbfile), Assumptions(freight_by_vendor=(("V1", 7.0),)))
    assert "freight_estimate_pct:V1" in system_prompt(Store(dbfile))
    assert [t["name"] for t in tool_schemas()] == ["event_summary", "gate_summary", "review_exposure", "run_sql", "run_award", "explain_field"] and all("input_schema" in t and "strict" not in t for t in tool_schemas())


class ScriptedApi:
    """A scripted Messages API behind the REAL anthropic SDK client (httpx mock transport): the SDK builds, sends and parses everything; only the network is faked."""

    def __init__(self, steps):
        self.steps, self.requests = list(steps), []
        self.client = anthropic.Anthropic(api_key="test-key", http_client=httpx2.Client(transport=httpx2.MockTransport(self._handle)))

    def _handle(self, request):
        self.requests.append(json.loads(request.content))
        step = self.steps.pop(0)
        blocks = ([{"type": "text", "text": step["text"]}] if "text" in step else [])
        blocks += [{"type": "tool_use", "id": f"toolu_{len(self.requests)}_{i}", "name": n, "input": a} for i, (n, a) in enumerate(step.get("calls", []))]
        if "final" in step:
            blocks.append({"type": "tool_use", "id": "toolu_final", "name": "submit_answer", "input": step["final"]})
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return httpx2.Response(200, json={"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-opus-5", "content": blocks, "stop_reason": stop,
                                         "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})


def final(**kw):
    return {"prose": "ok", "table": None, "refused": False, "missing": None, "drafted_question": None, **kw}


def test_the_loop_uses_anthropic_tool_use_and_a_pinned_final_call(dbfile):
    api = ScriptedApi([{"calls": [("run_sql", {"query": "SELECT COUNT(*) c FROM bid_fields"}), ("explain_field", {"vendor_id": "V1", "line_no": 2, "field": "unit_price"})]},
                       {"text": "I have what I need."},
                       {"final": final(prose="Two calls made.", table={"title": "t", "columns": ["a", "b"], "rows": [["1", "2"]]})}])
    a = Analyst(Store(dbfile), client=api.client).ask("anything")
    first, second, last = api.requests
    assert [t["name"] for t in first["tools"]] == ["event_summary", "gate_summary", "review_exposure", "run_sql", "run_award", "explain_field", "submit_answer"] and first["model"] == "claude-opus-5"
    assert all(set(t) == {"name", "description", "input_schema"} for t in first["tools"]) and "THE RULE" in first["system"] and "tool_choice" not in first
    uses = [b for b in second["messages"][1]["content"] if b["type"] == "tool_use"]
    results = second["messages"][2]["content"]
    assert [u["name"] for u in uses] == ["run_sql", "explain_field"] and [r["tool_use_id"] for r in results] == [u["id"] for u in uses]
    assert all(r["type"] == "tool_result" for r in results) and json.loads(results[0]["content"])["rows"][0][0] > 0
    assert last["tool_choice"] == {"type": "tool", "name": "submit_answer"} and [t["name"] for t in last["tools"]][-1] == "submit_answer"
    assert last["messages"][-1]["role"] == "user" and "submit_answer" in last["messages"][-1]["content"]
    assert [c["tool"] for c in a.tool_calls] == ["run_sql", "explain_field"] and api.steps == []
    d = a.to_dict()
    assert d["expander"]["collapsed"] is True and "2 tool calls" in d["expander"]["label"] and d["table"]["columns"] == ["a", "b"] and not d["refused"]
    assert "<details><summary>How this was computed (2 tool calls)" in a.markdown() and "| a | b |" in a.markdown()


def test_a_refusal_carries_what_is_missing_and_the_drafted_question(dbfile):
    api = ScriptedApi([{"text": "The store holds no price index."},
                       {"final": final(prose="I can't say.", refused=True, missing="a kraft paper price index or forecast",
                                       drafted_question="Which kraft index should escalation clauses reference, and can you supply the June series?")}])
    a = Analyst(Store(dbfile), client=api.client).ask("What will kraft paper cost in June?")
    md = a.markdown()
    assert a.refused and a.tool_calls == [] and "**Missing from the store:** a kraft paper price index or forecast" in md and "**Drafted question:**" in md


def test_a_runaway_loop_stops_and_says_so(dbfile):
    api = ScriptedApi([{"calls": [("run_sql", {"query": "SELECT 1"})]}] * 12)
    a = Analyst(Store(dbfile), client=api.client).ask("loop")
    assert a.status == "partial" and not a.missing and not a.drafted_question
    assert len(a.tool_calls) == 1 and len(api.requests) == 3
    assert a.table["rows"] == [["1"]]


class ScriptedOpenAI:
    """Stands in for the OpenAI Responses API: replays outputs and records what it was sent."""

    def __init__(self, steps):
        self.steps, self.sent, self.responses = list(steps), [], SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.sent.append(kw)
        step = self.steps.pop(0)
        calls = [SimpleNamespace(type="function_call", call_id=f"c{len(self.sent)}{i}", name=n, arguments=json.dumps(a)) for i, (n, a) in enumerate(step.get("calls", []))]
        return SimpleNamespace(id=f"r{len(self.sent)}", output=calls, output_text=json.dumps(step["final"]) if "final" in step else "")


def test_the_openai_loop_sends_tools_in_openai_format_and_reads_the_json_reply(dbfile):
    client = ScriptedOpenAI([{"calls": [("run_sql", {"query": "SELECT COUNT(*) c FROM bid_fields"})]}, {"final": final(prose="One call.", refused=True, missing="x", drafted_question="y")}])
    a = Analyst(Store(dbfile), client=client, provider="openai").ask("anything")
    first, second = client.sent
    assert a.model == "gpt-5.5" and [t["name"] for t in first["tools"]] == ["event_summary", "gate_summary", "review_exposure", "run_sql", "run_award", "explain_field"]
    assert all(t["type"] == "function" and t["strict"] is True and "parameters" in t and "input_schema" not in t for t in first["tools"])
    assert first["text"]["format"]["type"] == "json_schema" and "THE RULE" in first["instructions"]
    assert second["previous_response_id"] == "r1" and second["input"][0]["type"] == "function_call_output"
    assert [c["tool"] for c in a.tool_calls] == ["run_sql"] and a.refused and a.missing == "x" and a.drafted_question == "y"


def test_provider_follows_the_key_that_exists(monkeypatch, dbfile):
    monkeypatch.setattr("src.analyst._load_env", lambda: None)                 # do not read the real .env
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANALYST_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="No model key"):
        Analyst(Store(dbfile))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert (Analyst(Store(dbfile)).provider, Analyst(Store(dbfile)).model) == ("openai", "gpt-5.5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
    assert (Analyst(Store(dbfile)).provider, Analyst(Store(dbfile)).model) == ("anthropic", "claude-opus-5")
    monkeypatch.setenv("ANALYST_PROVIDER", "openai")
    assert Analyst(Store(dbfile)).provider == "openai"


LIVE = pytest.mark.skipif(os.environ.get("RUN_LIVE_ANALYST") != "1", reason="live model test; set RUN_LIVE_ANALYST=1 (needs API credit)")


@LIVE
def test_live_refusal_on_a_question_the_store_cannot_answer(dbfile):
    a = Analyst(Store(dbfile)).ask("What will kraft paper cost in June?")
    assert a.refused and a.missing and a.drafted_question
    assert not re.search(r"\d+(\.\d+)?\s*(/\s*kg|per kg|rs\.?|inr|%)", a.prose, re.I), "the refusal must not contain an estimate"


@LIVE
def test_live_gated_question_without_gate_results_asks_for_them(dbfile):
    a = Analyst(Store(dbfile)).ask("Split it cheapest per line, only among vendors who cleared the quality questionnaire.")
    assert a.refused and "gate" in (a.missing or "").lower() and a.drafted_question


def gate_rows(dbfile, passing):
    """Questionnaire gate rows: every vendor in `passing` clears all three gates; V3 fails one."""
    con = sqlite3.connect(dbfile)
    for v in ("V1", "V2", "V3"):
        for n, g in ((1, "G1"), (2, "G2"), (3, "G3")):
            st = "pass" if v in passing else "fail"
            con.execute("INSERT INTO questionnaire_answers (vendor_id,q_no,question,is_gate,gate_code,answer_text,gate_status,evidence_source) VALUES (?,?,'q',1,?,'a',?,?)",
                        (v, n, g, st, "vendor_master" if (v == "V2" and st == "pass") else "attachment"))
    con.commit()
    con.close()


def test_the_prompt_describes_the_actual_gate_source_whatever_it_is(dbfile):
    gate_rows(dbfile, {"V1", "V2"})
    p = system_prompt(Store(dbfile))
    assert "Gate results: V1, V2 cleared all mandatory gates; source: evaluated by the pipeline from questionnaire_answers" in p and "vendor-master" in p
    assert "supplied by the buyer" not in p                                                    # the pipeline is the source now, and the prompt says so
    assert "gates_source" in p                                                                 # the rule tells the model to report the source the tool gives
    override = system_prompt(Store(dbfile, gates=["V3"], gates_source="entered by the CFO for a what-if"))
    assert "Gate results: V3 cleared all mandatory gates; source: entered by the CFO for a what-if" in override
    con = sqlite3.connect(dbfile)
    con.execute("UPDATE questionnaire_answers SET gate_status='fail'")
    con.commit()
    con.close()
    assert "Gate results: no vendor cleared all mandatory gates; source: evaluated by the pipeline" in system_prompt(Store(dbfile))       # evaluated, nobody cleared: not 'NOT available'



@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_budget_reserves_final_answer_and_keeps_last_result(dbfile, provider):
    steps = [{"calls": [("run_sql", {"query": f"SELECT {i} AS value"})]} for i in range(10)]
    steps.append({"final": final(prose="The last retrieved value is 9.")})
    api = ScriptedOpenAI(steps) if provider == 'openai' else ScriptedApi(steps)
    a = Analyst(Store(dbfile), client=api if provider == 'openai' else api.client, provider=provider).ask('inspect')
    assert a.prose == 'The last retrieved value is 9.' and len(a.tool_calls) == 10
    assert a.model_requests == 11 and a.status == 'complete'
    requests = api.sent if provider == 'openai' else api.requests
    if provider == 'openai':
        assert requests[-1]['tool_choice'] == 'none'
    else:
        assert [t['name'] for t in requests[-1]['tools']] == ['submit_answer']


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_repeat_request_is_cached_then_finalized(dbfile, provider):
    steps = [{"calls": [("run_sql", {"query": "SELECT COUNT(*) FROM bid_fields"})]}] * 2 + [{"final": final(prose='Existing records counted.')}]
    api = ScriptedOpenAI(steps) if provider == 'openai' else ScriptedApi(steps)
    a = Analyst(Store(dbfile), client=api if provider == 'openai' else api.client, provider=provider).ask('count')
    assert len(a.tool_calls) == 1 and a.model_requests == 3 and 'Repeated' in a.execution_note
    assert a.prose == 'Existing records counted.'


def test_two_sql_errors_finalize_without_telling_buyer_data_is_missing(dbfile):
    api = ScriptedOpenAI([{'calls': [('run_sql', {'query': 'SELECT nonexistent FROM bid_fields'})]},
                         {'calls': [('run_sql', {'query': 'SELECT also_nonexistent FROM bid_fields'})]},
                         {'final': final(prose='I could not complete this query.')}])
    a = Analyst(Store(dbfile), client=api).ask('bad queries')
    assert api.sent[-1]['tool_choice'] == 'none' and len(a.tool_calls) == 2
    assert not a.missing and not a.drafted_question


def test_timeout_keeps_retrieved_evidence(dbfile):
    api = ScriptedOpenAI([{'calls': [('run_sql', {'query': 'SELECT 42 AS verified'})]}])
    a = Analyst(Store(dbfile), client=api).ask('question')  # exhausted fake transport
    assert a.status == 'partial' and a.table['rows'] == [['42']]
    assert not a.missing and not a.drafted_question and a.execution_note


def test_failure_without_evidence_is_execution_unavailable(dbfile):
    a = Analyst(Store(dbfile), client=ScriptedOpenAI([])).ask('question')
    assert a.status == 'unavailable' and not a.missing and not a.drafted_question


def test_summary_uses_current_inventory_and_explicit_denominator(dbfile):
    r = Store(dbfile).event_summary()
    assert r['line_count'] == 3 and r['registered_vendor_count'] == 3
    assert r['vendors_with_recorded_submissions'] == 3 and r['field_count'] == 14
    assert r['field_states'] == {'extracted': 13, 'needs_review': 1}
    assert r['extracted_pct'] == pytest.approx(13/14*100)
    assert r['review_exposure']['known_exposure_inr'] == 500000
    assert 'probability' in r['state_note']
    prompt = system_prompt(Store(dbfile))
    assert "Not in the store: gate results" not in prompt and 'not yet extracted' not in prompt


def test_exposure_deduplicates_and_uses_normalized_price(dbfile):
    con = sqlite3.connect(dbfile)
    con.execute("INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,state,reason_code) VALUES ('S1',2,'line_total',999,'needs_review','arith_mismatch')")
    con.execute("UPDATE bid_fields SET value=1000,basis='per_100',currency='USD' WHERE submission_id='S1' AND rfx_line_no=2 AND field_name='unit_price'")
    con.commit(); con.close()
    r = Store(dbfile).review_exposure()
    assert r['review_field_count'] == 2 and r['vendor_line_count'] == 1
    assert r['known_exposure_inr'] == 500000  # norm_prices is the canonical INR/piece record
    assert len(r['lines'][0]['fields']) == 2


def test_exposure_peer_proxy_and_unknown_are_explicit(dbfile):
    con = sqlite3.connect(dbfile)
    con.execute("UPDATE norm_prices SET inr_per_piece=NULL WHERE submission_id='S1' AND rfx_line_no=2")
    con.commit(); con.close()
    r = Store(dbfile).review_exposure()
    assert r['known_exposure_inr'] == 450000 and r['lines'][0]['peer_vendors'] == ['V2']
    assert 'median' in r['lines'][0]['valuation_source']
    con = sqlite3.connect(dbfile)
    con.execute("UPDATE norm_prices SET inr_per_piece=NULL WHERE rfx_line_no=2")
    con.commit(); con.close()
    r = Store(dbfile).review_exposure()
    assert r['known_exposure_inr'] is None and r['unknown_vendor_lines'] == 1


def test_gate_summary_preserves_vendor_master_source(dbfile):
    gate_rows(dbfile, {'V1','V2'})
    r = Store(dbfile).gate_summary()
    assert r['passing_vendors'] == ['V1','V2']
    assert all(g['evidence_source'] == 'vendor_master' for g in r['gates'] if g['vendor_id'] == 'V2')


def test_summary_question_finishes_in_two_requests(dbfile):
    api = ScriptedOpenAI([{'calls': [('event_summary', {})]}, {'final': final(prose='Summary from current records.')}])
    updates = []
    a = Analyst(Store(dbfile), client=api).ask('Give me the state of the event', progress=updates.append)
    assert a.model_requests == 2 and len(a.tool_calls) == 1 and len(updates) == 2
    assert json.loads(a.tool_calls[0]['result'])['field_count'] == 14



def test_deadline_reserves_finalization_before_another_tool(dbfile, monkeypatch):
    import src.analyst as module
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    class SlowApi(ScriptedOpenAI):
        def create(self, **kw):
            response = super().create(**kw)
            if len(self.sent) == 1:
                clock[0] = 146.0  # less than the finalization reserve remains
            return response
    api = SlowApi([{'calls': [('event_summary', {})]}, {'final': final(prose='Unable to retrieve evidence before the deadline.')}])
    a = Analyst(Store(dbfile), client=api).ask('status')
    assert api.sent[-1]['tool_choice'] == 'none' and not a.tool_calls
    assert a.model_requests == 2


def test_summary_evidence_survives_narration_failure(dbfile):
    api = ScriptedOpenAI([{'calls': [('event_summary', {})]}])
    a = Analyst(Store(dbfile), client=api).ask('event status')
    assert a.status == 'partial' and a.table['rows'][0][1] == '3'
    assert not a.missing and not a.drafted_question


def test_new_question_has_no_stale_tool_cache(dbfile):
    api = ScriptedOpenAI([{'calls': [('event_summary', {})]}, {'final': final()},
                         {'calls': [('event_summary', {})]}, {'final': final()}])
    analyst = Analyst(Store(dbfile), client=api)
    first = analyst.ask('status')
    con = sqlite3.connect(dbfile)
    con.execute("UPDATE bid_fields SET state='needs_review' WHERE field_name='declared_liner_gsm'")
    con.commit(); con.close()
    second = analyst.ask('status')
    assert json.loads(first.tool_calls[0]['result'])['field_states']['needs_review'] == 1
    assert json.loads(second.tool_calls[0]['result'])['field_states']['needs_review'] == 8
