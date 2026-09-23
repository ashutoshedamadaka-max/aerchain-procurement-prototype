"""Live activity checks use temporary SQLite and scripted answers; no API calls."""
import sqlite3
from types import SimpleNamespace
from app import live_activity as activity
from app.test_shell import empty_db, ROOT
from streamlit.testing.v1 import AppTest
from src import metering


def answer(status="complete", refused=False):
    return SimpleNamespace(status=status, refused=refused, model_requests=1,
                           tool_calls=[{"result": '{"anchor":"quote!B2","snippet":"PRIVATE SNIPPET"}'}], prose="PRIVATE ANSWER")


def test_read_is_non_mutating_and_missing_usage_unknown(empty_db):
    before = empty_db.read_bytes()
    assert activity.read(empty_db) == []
    assert before == empty_db.read_bytes()
    activity.record(empty_db, "a", answer(), 2.5)
    r = activity.read(empty_db)[0]
    assert r["status"] == "answered" and r["anchors_retrieved"] == 1
    assert r["cost_usd"] is None and r["input_tokens"] is None
    assert b"PRIVATE" not in empty_db.read_bytes()


def test_request_usage_is_isolated_idempotent_and_unknown_models_not_free(empty_db):
    with sqlite3.connect(empty_db) as con:
        con.execute(metering.DDL)
        for request, model, tokens in [("a", "gpt-5.4-mini", 100), ("b", "unknown-model", 999)]:
            con.execute("INSERT INTO model_calls(ts,provider,model,stage,input_tokens,output_tokens,elapsed_s) VALUES ('now','test',?,?,?,10,1)",
                        (model, "analyst:streamlit:" + request, tokens))
    activity.record(empty_db, "a", answer(), 2)
    activity.record(empty_db, "a", answer(), 9)
    activity.record(empty_db, "b", answer(refused=True), 3)
    rows = {r['request_id']: r for r in activity.read(empty_db)}
    assert len(rows) == 2 and rows['a']['elapsed_s'] == 2
    assert rows['a']['input_tokens'] == 100 and rows['a']['cost_usd'] > 0
    assert rows['b']['cost_usd'] is None and rows['b']['status'] == 'refused'
    activity.record(empty_db, "failed", None, 1)
    assert activity.read(empty_db)[0]['status'] == 'failed'


def test_no_live_call_needed_for_evals(empty_db):
    activity.record(empty_db, "partial", answer(status="partial"), 2)
    before = empty_db.read_bytes()
    app = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=30).run()
    app.sidebar.radio[0].set_value('Evals').run()
    assert not app.exception and not app.error
    text = '\n'.join(x.value for x in [*app.markdown, *app.caption])
    assert 'Correctness not assessed' in text and 'Latest interaction: **Partial**' in text
    assert 'PRIVATE' not in text
    assert before == empty_db.read_bytes()


def test_normal_ask_records_one_attempt_and_rerun_does_not_duplicate(empty_db, monkeypatch):
    from src import analyst
    monkeypatch.setattr(metering, 'configure', lambda db: None)
    class FakeAnalyst:
        def __init__(self, store): pass
        def ask(self, question, progress=None):
            assert metering.current().startswith('analyst:streamlit:')
            return analyst.Answer('PRIVATE ANSWER', status='complete', model_requests=1)
    monkeypatch.setattr(analyst, 'Analyst', FakeAnalyst)
    app = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=30).run()
    app.sidebar.radio[0].set_value('Ask').run()
    app.text_area[0].set_value('PRIVATE QUESTION')
    next(b for b in app.button if b.label == 'Ask').click().run()
    assert not app.exception and not app.error
    assert len(activity.read(empty_db)) == 1
    app.run()
    assert len(activity.read(empty_db)) == 1
    assert b'PRIVATE' not in empty_db.read_bytes()
