"""Deterministic budget and normalization report tests. No provider requests."""
import sqlite3
import pytest
from app.eval_budgets import budget_result
from app.normalization_report import parse, fingerprint
from app.test_shell import empty_db, ROOT
from streamlit.testing.v1 import AppTest


def test_budget_boundaries_and_missing_usage():
    row = dict(elapsed_s=60, cost_usd=.10, model_requests=2, metered_calls=2)
    assert budget_result(row)['overall'] == 'pass'
    assert budget_result({**row, 'elapsed_s':60.001})['latency'] == 'over budget'
    assert budget_result({**row, 'cost_usd':.10001})['cost'] == 'over budget'
    assert budget_result({**row, 'cost_usd':None})['overall'] == 'unknown'
    assert budget_result({**row, 'metered_calls':1})['cost'] == 'unknown'
    assert budget_result({**row, 'metered_calls':1, 'cost_usd':.11})['cost'] == 'over budget'
    assert budget_result({**row, 'model_requests':None})['cost'] == 'unknown'
    with pytest.raises(ValueError): budget_result(row, seconds=float('nan'))


def test_grader_parser_rejects_missing_summary():
    with pytest.raises(ValueError): parse('Traceback: grader failed')
    r = parse('fields compared: 2   price differences: 1 []   comparability differences: 0 []\n  S1: 2 fields, comparability diffs 0, max |INR/pc diff| 0.000040')
    assert r['fields'] == 2 and r['price_differences'] == 1
    assert r['vendors'][0]['max_matching_price_delta'] == .00004


def test_normalization_fingerprint_ignores_unrelated_activity(empty_db):
    with sqlite3.connect(empty_db) as c:
        c.execute('CREATE TABLE norm_prices(submission_id TEXT,rfx_line_no INTEGER,price REAL)')
        c.execute("INSERT INTO norm_prices VALUES ('S1',1,5)")
    original = fingerprint(empty_db)
    with sqlite3.connect(empty_db) as c:
        c.execute('CREATE TABLE analyst_activity(request_id TEXT)')
    assert fingerprint(empty_db) == original
    with sqlite3.connect(empty_db) as c: c.execute('UPDATE norm_prices SET price=6')
    assert fingerprint(empty_db) != original


def test_empty_activity_has_targets_not_a_false_pass(empty_db):
    app = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=30).run()
    app.sidebar.radio[0].set_value('Evals').run()
    assert not app.exception and not app.error
    assert app.number_input(key='eval_latency_budget').value == 60
    assert app.number_input(key='eval_cost_budget').value == .10
    assert any('not evaluated' in c.value for c in app.caption)
    assert not any(m.label == 'Within both targets' for m in app.metric)
