"""Every model call is recorded (model, stage, tokens, elapsed) once recording is configured, and never before."""
import sqlite3
import types

from src import metering
from src.extract import llm


def fake_openai(monkeypatch):
    call = types.SimpleNamespace(type="function_call", arguments='{"ok": 1}')
    resp = types.SimpleNamespace(output=[call], usage=types.SimpleNamespace(input_tokens=120, output_tokens=30, input_tokens_details=types.SimpleNamespace(cached_tokens=20)))
    client = types.SimpleNamespace(responses=types.SimpleNamespace(create=lambda **kw: resp))
    monkeypatch.setattr("openai.OpenAI", lambda: client)


def test_structured_call_is_recorded_under_its_stage(tmp_path, monkeypatch):
    fake_openai(monkeypatch)
    db = tmp_path / "m.db"
    monkeypatch.setattr(metering, "_db", None)
    llm.call_structured("gpt-x", "s", "u", {}, effort="high")                # not configured: nothing recorded, nothing created
    assert not db.exists()
    metering.configure(db)
    with metering.stage("extraction:pdf"):
        assert llm.call_structured("gpt-x", "s", "u", {}, effort="high")[0] == {"ok": 1}
    row = sqlite3.connect(db).execute("SELECT provider,model,stage,effort,input_tokens,output_tokens,cached_input_tokens,elapsed_s FROM model_calls").fetchall()
    assert len(row) == 1 and row[0][:7] == ("openai", "gpt-x", "extraction:pdf", "high", 120, 30, 20) and row[0][7] >= 0
    monkeypatch.setattr(metering, "_db", None)


def test_analyst_calls_are_recorded_for_both_providers(tmp_path, monkeypatch):
    from src.analyst import Analyst
    db = tmp_path / "m.db"
    monkeypatch.setattr(metering, "_db", None)
    metering.configure(db)
    a = Analyst.__new__(Analyst)
    a.provider, a.model = "anthropic", "claude-opus-5"
    r = types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=500, output_tokens=40))
    with metering.stage("analyst:C1"):
        assert a._call(lambda **kw: r, x=1) is r
    assert sqlite3.connect(db).execute("SELECT provider,model,stage,input_tokens,output_tokens FROM model_calls").fetchall() == [("anthropic", "claude-opus-5", "analyst:C1", 500, 40)]
    monkeypatch.setattr(metering, "_db", None)
