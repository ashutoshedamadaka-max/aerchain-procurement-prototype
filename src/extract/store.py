"""procurement.db writer. Uses the existing scripts/schema.sql; truth-only columns stay NULL."""
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "scripts" / "schema.sql"
BASIS_TO_PC = {"per_piece": 1.0, "per_100_pieces": 0.01}


def connect(db_path, rfx=None):
    con = sqlite3.connect(db_path, timeout=60)
    con.execute("PRAGMA foreign_keys = ON")
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='rfx_lines'").fetchone():     # not "no file": metering may have created the file first
        con.executescript(SCHEMA.read_text())
    if rfx:
        cols = ["line_no", "sku", "style", "length_mm", "width_mm", "height_mm", "ply", "flute", "liner_gsm", "gsm_stack", "bf", "print_spec", "annual_qty", "uom"]
        con.executemany(f"INSERT OR IGNORE INTO rfx_lines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[ln[c] for c in cols] for ln in rfx.lines])
        con.commit()
    return con


def peers(con, exclude_file):
    """Other vendors' extracted per-piece INR rates by line, for the cross-vendor sanity check."""
    out = {}
    for line, value, basis in con.execute(
            "SELECT b.rfx_line_no,b.value,b.basis FROM bid_fields b JOIN submissions s USING(submission_id) "
            "WHERE s.file_name<>? AND b.field_name='unit_price' AND b.state='extracted' AND b.currency='INR'", (exclude_file,)):
        if basis in BASIS_TO_PC and value:
            out.setdefault(line, []).append(value * BASIS_TO_PC[basis])
    return out


def write(con, vendor, submission, fields, conditions, tiers):
    sid = submission["submission_id"]
    with con:
        con.execute("DELETE FROM bid_fields WHERE submission_id=?", (sid,))
        con.execute("DELETE FROM conditions WHERE submission_id=?", (sid,))
        con.execute("DELETE FROM tier_rules WHERE vendor_id=?", (vendor["vendor_id"],))
        con.execute("DELETE FROM submissions WHERE submission_id=?", (sid,))
        con.execute("INSERT OR REPLACE INTO vendors (vendor_id,name,response_format,moq_pcs) VALUES (?,?,?,?)",
                    (vendor["vendor_id"], vendor["name"], vendor["response_format"], vendor.get("moq_pcs")))
        con.execute("INSERT INTO submissions (submission_id,vendor_id,file_name,format,received_on,validity_days,valid_until,expired_at_eval,"
                    "eval_date,incoterm,currency) VALUES (:submission_id,:vendor_id,:file_name,:format,:received_on,:validity_days,"
                    ":valid_until,:expired_at_eval,:eval_date,:incoterm,:currency)", submission)
        con.executemany(
            "INSERT INTO bid_fields (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,reason_code,anchor,snippet,"
            "derivation,resolving_question) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(sid, f.line_no, f.field_name, f.value, f.unit, f.basis, f.currency, f.state, f.reason_code, f.anchor, f.snippet,
              f.derivation, f.resolving_question) for f in fields])
        con.executemany("INSERT INTO conditions (submission_id,kind,value_num,value_text,anchor,snippet) VALUES (?,?,?,?,?,?)",
                        [(sid, c["kind"], c["value_num"], c.get("value_text"), c["anchor"], c["snippet"]) for c in conditions])
        con.executemany("INSERT INTO tier_rules (vendor_id,kind,min_value_inr,max_value_inr,rfx_line_no,min_qty,effect_pct,anchor,snippet) VALUES (?,?,?,?,?,?,?,?,?)",
                        [(vendor["vendor_id"], t["kind"], t.get("min_value_inr"), t.get("max_value_inr"), t.get("rfx_line_no"), t.get("min_qty"),
                          t["effect_pct"], t["anchor"], t["snippet"]) for t in tiers])
