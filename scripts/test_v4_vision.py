#!/usr/bin/env python3
"""Two independent vision passes over the degraded V4 rate card, graded against truth.

The model sees only the image and the RFx line list (no prices). truth.sqlite is read after extraction, for grading only.
Key comes from .env (OPENAI_API_KEY). Run: python scripts/test_v4_vision.py [--dry]
"""
import argparse
import base64
import json
import os
import pathlib
import sqlite3
import struct
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import metering  # noqa: E402
IMG = ROOT / "dataset" / "artifacts" / "V4_Meridian_RateCard.jpg"
DB = ROOT / "dataset" / "truth" / "truth.sqlite"
OUT = ROOT / "dataset" / "eval"
MODEL = os.environ.get("VISION_MODEL", "gpt-5.5")

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "footer"],
    "properties": {
        "rows": {"type": "array", "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["printed_no", "size_mm", "ply", "gsm", "print", "rate_text", "rate", "unit", "digit_confidence",
                             "ambiguous_digits", "mapped_rfx_line", "match_note"],
                "properties": {
                    "printed_no": {"type": ["integer", "null"], "description": "Serial number printed in the No. column"},
                    "size_mm": {"type": "string", "description": "Length x width x height exactly as printed"},
                    "ply": {"type": "integer"},
                    "gsm": {"type": "integer"},
                    "print": {"type": "string", "enum": ["plain", "1 colour", "2 colour", "other"]},
                    "rate_text": {"type": "string", "description": "The rate exactly as printed, character for character"},
                    "rate": {"type": ["number", "null"]},
                    "unit": {"type": "string", "enum": ["per_pc", "per_kg", "unreadable"]},
                    "digit_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "ambiguous_digits": {"type": ["string", "null"], "description": "If any digit could be read two ways, name the candidates"},
                    "mapped_rfx_line": {"type": ["integer", "null"], "description": "RFx line matched on dimensions, ply, GSM and print; null if none"},
                    "match_note": {"type": "string"},
                }}},
            "footer": {"type": "object", "additionalProperties": False, "required": ["moq", "validity", "other_terms"], "properties": {
                "moq": {"type": ["string", "null"]}, "validity": {"type": ["string", "null"]},
                "other_terms": {"type": "array", "items": {"type": "string"}}}},
    },
}
TOOL = {"type": "function", "function": {"name": "submit_rate_card", "strict": True,
        "description": "Submit every price row read from the rate card image, mapped to the RFx line list.", "parameters": SCHEMA}}


def rfx_context():
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT line_no,length_mm,width_mm,height_mm,ply,gsm_stack,print_spec FROM rfx_lines ORDER BY line_no").fetchall()
    return "\n".join(f"line {n}: {L} x {W} x {H} mm, {p}-ply, GSM {g}, print {pr}" for n, L, W, H, p, g, pr in rows)


def prompt():
    return ("This is a phone photo of a vendor's printed rate card. Read every price row. The photo is angled, so follow each row's "
            "own line across the table and do not pair a rate with the wrong size. Transcribe rates character for character. "
            "If a digit is not clear, say so and lower digit_confidence; never guess a plausible digit.\n\n"
            "Map each row to one line of our RFx by dimensions (length x width x height), then ply, GSM and print. "
            "Set mapped_rfx_line to null if no RFx line matches all of them.\n\n"
            f"Our RFx lines:\n{rfx_context()}\n\nSubmit your reading with the submit_rate_card function.")


def jpeg_size(b):
    i = 2
    while i < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        m = b[i + 1]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h
        i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]


def request(img_b64):
    return dict(model=MODEL, max_completion_tokens=20000, tools=[TOOL],
                tool_choice={"type": "function", "function": {"name": "submit_rate_card"}},
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"}},
                    {"type": "text", "text": prompt()}]}])


def run_pass(client, img_b64):
    t0 = time.perf_counter()
    r = client.chat.completions.create(**request(img_b64))
    metering.record("openai", MODEL, r.usage.prompt_tokens, r.usage.completion_tokens, time.perf_counter() - t0, "photo:v4_ratecard", effort=None)
    call = r.choices[0].message.tool_calls[0]
    return json.loads(call.function.arguments), r.usage.prompt_tokens


def grade(p1, p2):
    con = sqlite3.connect(DB)
    dims = {(L, W, H, p, g, pr): n for n, L, W, H, p, g, pr in con.execute(
        "SELECT line_no,length_mm,width_mm,height_mm,ply,liner_gsm,print_spec FROM rfx_lines")}
    truth = {n: (v, u, s) for n, v, u, s in con.execute(
        "SELECT rfx_line_no,value,unit,state FROM bid_fields b JOIN submissions s USING(submission_id) "
        "WHERE vendor_id='V4' AND field_name='unit_price'")}
    pr = {"plain": "plain", "1 colour": "1-colour flexo", "2 colour": "2-colour flexo"}

    def by_line(p):
        d = {}
        for r in p["rows"]:
            if r["mapped_rfx_line"] is not None:
                d[r["mapped_rfx_line"]] = r
        return d

    def true_line(r):
        try:
            L, W, H = (int(x) for x in r["size_mm"].lower().replace("x", " ").split())
        except ValueError:
            return None
        return dims.get((L, W, H, r["ply"], r["gsm"], pr.get(r["print"])))

    res = {}
    for name, p in (("pass1", p1), ("pass2", p2)):
        rows = p["rows"]
        mapped = [r for r in rows if r["mapped_rfx_line"] is not None]
        right = [r for r in mapped if r["mapped_rfx_line"] == true_line(r)]
        priced = [r for r in mapped if r["rate"] is not None]
        exact = [r for r in right if r["rate"] is not None and abs(r["rate"] - truth[r["mapped_rfx_line"]][0]) < 5e-4
                 and r["unit"] == ("per_kg" if truth[r["mapped_rfx_line"]][1] == "USD/kg" else "per_pc")]
        res[name] = dict(rows_returned=len(rows), mapped=len(mapped), mapped_correctly=len(right), prices_transcribed=len(priced),
                         prices_exactly_right=len(exact),
                         wrong_prices=[(r["mapped_rfx_line"], r["rate_text"], truth[r["mapped_rfx_line"]][0], r["digit_confidence"])
                                       for r in right if r not in exact and r["mapped_rfx_line"] in truth],
                         low_confidence_lines=[r["mapped_rfx_line"] for r in mapped if r["digit_confidence"] != "high"],
                         footer=p["footer"])
    a, b = by_line(p1), by_line(p2)
    res["disagreements"] = [dict(line=n, pass1=(a[n]["rate_text"], a[n]["digit_confidence"]) if n in a else None,
                                 pass2=(b[n]["rate_text"], b[n]["digit_confidence"]) if n in b else None, truth=truth[n][0])
                            for n in sorted(set(a) | set(b)) if (n in a) != (n in b) or (n in a and a[n]["rate_text"] != b[n]["rate_text"])]
    res["planted_ocr_lines"] = {n: (truth[n][0], [(a.get(n) or {}).get("rate_text"), (b.get(n) or {}).get("rate_text")])
                                for n in truth if truth[n][2] == "needs_review"}
    exp = set(json.loads((ROOT / "dataset" / "truth" / "v4_degradation.json").read_text())["expected_uncertain"])
    low = set(res["pass1"]["low_confidence_lines"]) | set(res["pass2"]["low_confidence_lines"])
    wrong = {w[0] for k in ("pass1", "pass2") for w in res[k]["wrong_prices"]}
    flagged = {d["line"] for d in res["disagreements"]} | low | wrong
    res["calibration"] = dict(expected_uncertain=sorted(exp), flagged=sorted(flagged), disagree=sorted(d["line"] for d in res["disagreements"]),
                              low_confidence=sorted(low), wrong_in_any_pass=sorted(wrong), flagged_and_expected=sorted(flagged & exp),
                              expected_not_flagged=sorted(exp - flagged), flagged_not_expected=sorted(flagged - exp),
                              wrong_but_not_flagged=sorted(wrong - low - {d["line"] for d in res["disagreements"]}))
    return res


def load_env():
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    dry = ap.parse_args().dry
    raw_bytes = IMG.read_bytes()
    print(f"sending {IMG.name}: {len(raw_bytes):,} bytes, {jpeg_size(raw_bytes)} px, untouched (no resize, no re-encode)")
    b64 = base64.standard_b64encode(raw_bytes).decode()
    if dry:
        req = request(b64)
        req["messages"][0]["content"][0]["image_url"]["url"] = f"data:image/jpeg;base64,<{len(b64):,} chars>"
        print(json.dumps(req, indent=1)[:3500])
        return
    from openai import OpenAI
    metering.configure(ROOT / "procurement.db")
    client = OpenAI()
    OUT.mkdir(parents=True, exist_ok=True)
    passes = []
    for i in (1, 2):
        p, tok = run_pass(client, b64)
        (OUT / f"v4_vision_pass{i}.json").write_text(json.dumps(p, indent=2))
        print(f"pass {i}: {len(p['rows'])} rows, {tok:,} input tokens")
        passes.append(p)
    res = grade(*passes)
    (OUT / "v4_vision_report.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
