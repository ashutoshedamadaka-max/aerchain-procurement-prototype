"""Check the portfolio routes and the boundary of its API-free tool explorer."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
from app.case_study import TOOL_EXAMPLES, execute_tool_example
from app.test_shell import ROOT, empty_db


def test_case_study_buttons_navigate_and_new_pages_handle_empty_data(empty_db):
    before = empty_db.read_bytes()
    app = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=30).run()
    next(b for b in app.button if b.label == 'Read the product thinking').click().run()
    assert app.sidebar.radio[0].value == 'Strategy'
    assert not app.exception and not app.error
    app.sidebar.radio[0].set_value('Architecture').run()
    app.button(key='run_architecture_tool').click().run()
    assert not app.exception and not app.error
    assert 'not_found' in app.json[0].value
    assert empty_db.read_bytes() == before


def test_tool_explorer_refuses_missing_and_truth_sources(tmp_path):
    example = next(iter(TOOL_EXAMPLES))
    missing = tmp_path / 'missing.db'
    with pytest.raises(FileNotFoundError):
        execute_tool_example(missing, example)
    assert not missing.exists()
    with pytest.raises(ValueError, match='frozen truth'):
        execute_tool_example(ROOT / 'dataset/truth/truth.sqlite', example)


def test_tool_examples_read_recorded_data_without_calling_a_model(monkeypatch):
    db = ROOT / 'procurement.db'
    if not db.is_file():
        pytest.skip('Recorded pipeline data not distributed with the source repository')
    from src.analyst import Analyst
    def reject_model(*args, **kwargs):
        raise AssertionError('The tool explorer must not initialize an AI provider')
    monkeypatch.setattr(Analyst, '__init__', reject_model)
    before = db.read_bytes()
    results = {choice: execute_tool_example(db, choice) for choice in TOOL_EXAMPLES}
    assert results['Inspect an arithmetic mismatch']['record']['reason_code'] == 'arith_mismatch'
    assert 'passing_vendors' in results['Check supplier eligibility']
    assert results['Summarize the sourcing event']['line_count'] > 0
    assert db.read_bytes() == before


def test_no_data_keeps_case_study_and_architecture_available(tmp_path, monkeypatch):
    db = tmp_path / 'absent.db'
    monkeypatch.setenv('PROCUREMENT_DB', str(db))
    app = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=30).run()
    assert not app.exception and not app.error
    app.sidebar.radio[0].set_value('Architecture').run()
    assert app.button(key='run_architecture_tool').disabled
    assert not app.exception and not app.error
    assert not db.exists()
