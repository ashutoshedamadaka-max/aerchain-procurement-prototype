"""Privacy-minimal operational telemetry. No prompts, answers or tool payloads are stored here."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

DDL = """CREATE TABLE IF NOT EXISTS analyst_activity (
 request_id TEXT PRIMARY KEY, finished_at TEXT NOT NULL, status TEXT NOT NULL,
 elapsed_s REAL NOT NULL, tool_calls INTEGER, model_requests INTEGER,
 anchors_retrieved INTEGER, metered_calls INTEGER NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL)"""


def has_anchor(value):
    """Presence check only; this does not establish valid citations or factual accuracy."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return False
    if isinstance(value, dict):
        return any(bool(v) if k in ("anchor", "source_anchor") else has_anchor(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_anchor(v) for v in value)
    return False


def record(db: Path, request_id: str, answer, elapsed: float) -> None:
    """Write one completed attempt, joining usage only by its unique metering stage."""
    from scripts.cost_report import PRICES, cost
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)) as con:
        con.execute(DDL)
        try:
            usage = con.execute("SELECT model,input_tokens,output_tokens,cached_input_tokens FROM model_calls WHERE stage=?",
                                ("analyst:streamlit:" + request_id,)).fetchall()
        except sqlite3.OperationalError:
            usage = []
        status = "failed"
        calls = requests = anchors = None
        if answer is not None:
            status = "refused" if answer.refused else {"complete": "answered", "partial": "partial"}.get(answer.status, "failed")
            calls = len(answer.tool_calls)
            requests = answer.model_requests
            anchors = int(any(has_anchor(c.get("result")) for c in answer.tool_calls))
        # Unknown or partially metered usage is not silently reported as zero cost.
        cost_value = sum(cost(*r) for r in usage) if usage and all(r[0] in PRICES for r in usage) else None
        con.execute("INSERT OR IGNORE INTO analyst_activity VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (request_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), status, round(elapsed, 3),
                     calls, requests, anchors, len(usage), sum(r[1] for r in usage) if usage else None,
                     sum(r[2] for r in usage) if usage else None, cost_value))
        con.commit()


def read(db: Path) -> list[dict]:
    """Read operational records without creating a database or changing the benchmark."""
    if not db.is_file():
        return []
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyst_activity'").fetchone()
        return [dict(r) for r in con.execute("SELECT * FROM analyst_activity ORDER BY finished_at DESC,rowid DESC")] if exists else []
