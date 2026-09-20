"""Buyer-editable settings that are not vendor assumptions.

They live in their own table because `assumptions` is rebuilt by every normalization run and would lose them. Reading never fails: no table or no row means the
default. Writing creates the table and validates the value. The award engine reads `review_block_threshold_pct` when a scenario is loaded.

  python -m src.settings                              show every setting
  python -m src.settings review_block_threshold_pct 8  set one
"""
import datetime as dt
import sqlite3
import sys

DDL = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL NOT NULL, unit TEXT, updated_at TEXT)"
SETTINGS = {
    "review_block_threshold_pct": dict(default=5.0, unit="%", min=0.0, max=100.0, label="Review block threshold",
                                       note="A needs_review line above this share of the awarded value blocks a recommendation. Below it the scenario still runs, with the line listed under Warnings."),
}


def get(con, key):
    """The stored value, or the default when the table or the row does not exist."""
    try:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    return row[0] if row else SETTINGS[key]["default"]


def set_value(db_path, key, value):
    """Validate and store one setting. Raises ValueError (unknown key, not a number, out of range) without writing anything."""
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key!r}; known: {sorted(SETTINGS)}")
    spec = SETTINGS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, got {value!r}") from None
    if v != v or not spec["min"] <= v <= spec["max"]:
        raise ValueError(f"{key} must be between {spec['min']:g} and {spec['max']:g} {spec['unit']}, got {value!r}")
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute(DDL)
        con.execute("INSERT INTO settings (key, value, unit, updated_at) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, v, spec["unit"], dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()
    return v


def describe(con):
    """Every setting with its current value, for a settings row in a UI."""
    return [dict(key=k, label=s["label"], value=get(con, k), unit=s["unit"], default=s["default"], is_default=get(con, k) == s["default"], min=s["min"], max=s["max"], note=s["note"])
            for k, s in SETTINGS.items()]


def main():
    db = "procurement.db"
    if len(sys.argv) == 3:
        print(f"{sys.argv[1]} = {set_value(db, sys.argv[1], sys.argv[2]):g}")
        return
    con = sqlite3.connect(db)
    for s in describe(con):
        print(f"{s['key']} = {s['value']:g}{s['unit']} ({'default' if s['is_default'] else 'set'}; {s['min']:g}-{s['max']:g})  {s['note']}")


if __name__ == "__main__":
    main()
