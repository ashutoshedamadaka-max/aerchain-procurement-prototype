"""Per-call model usage, persisted to model_calls in procurement.db.

Every model call in the codebase goes through one of three doors (extract/llm.call_structured, the analyst's two client wrappers, the V4 vision script) and each
records: provider, model, stage, input tokens, output tokens, elapsed seconds. Recording is OFF until an entry point calls configure(db_path), so tests and
imports never write to the real database. A recording failure never breaks a model call: it is reported on stderr and the call's result is returned.
"""
import contextlib
import contextvars
import sqlite3
import sys
import time

DDL = """CREATE TABLE IF NOT EXISTS model_calls (
  call_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, stage TEXT NOT NULL, effort TEXT,
  input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cached_input_tokens INTEGER NOT NULL DEFAULT 0, elapsed_s REAL NOT NULL, detail TEXT)"""
_db = None
_stage = contextvars.ContextVar("model_stage", default="unstaged")


def configure(db_path):
    """Turn recording on for this process, writing to db_path (created if needed)."""
    global _db
    _db = str(db_path)
    con = sqlite3.connect(_db, timeout=60)
    try:
        con.execute(DDL)
        con.commit()
    finally:
        con.close()


@contextlib.contextmanager
def stage(name):
    """Label every model call made inside the block, e.g. `with metering.stage('extraction:pdf'):`."""
    token = _stage.set(name)
    try:
        yield
    finally:
        _stage.reset(token)


def current():
    return _stage.get()


def record(provider, model, input_tokens, output_tokens, elapsed_s, stage_name=None, effort=None, cached=0, detail=None):
    if _db is None:
        return
    try:
        con = sqlite3.connect(_db, timeout=60)
        try:
            con.execute("INSERT INTO model_calls (ts,provider,model,stage,effort,input_tokens,output_tokens,cached_input_tokens,elapsed_s,detail) "
                        "VALUES (datetime('now','localtime'),?,?,?,?,?,?,?,?,?)",
                        (provider, model, stage_name or current(), effort, int(input_tokens or 0), int(output_tokens or 0), int(cached or 0), round(elapsed_s, 3), detail))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        print(f"metering: could not record a {provider} call: {e}", file=sys.stderr)


def usage(resp):
    """(input, output, cached) tokens from an OpenAI Responses or Anthropic Messages response; zeros if the response carries none (test doubles)."""
    u = getattr(resp, "usage", None)
    d = getattr(u, "input_tokens_details", None)
    return getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0, getattr(d, "cached_tokens", 0) or getattr(u, "cache_read_input_tokens", 0) or 0


def timed(fn, *a, **kw):
    """Run fn and return (result, elapsed seconds)."""
    t = time.perf_counter()
    r = fn(*a, **kw)
    return r, time.perf_counter() - t
