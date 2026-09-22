"""Shell tests use temporary databases, never frozen truth."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("procurement_shell", ROOT / "app/main.py")
shell = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shell)


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    path = tmp_path / "procurement.db"
    with sqlite3.connect(path) as conn:
        conn.executescript((ROOT / "scripts/schema.sql").read_text())
    monkeypatch.setenv("PROCUREMENT_DB", str(path))
    return path


def test_all_pages_with_empty_schema(empty_db):
    before = empty_db.read_bytes()
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15).run()
    for page in ("Event", "Comparison", "Ask", "RFx"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
        assert not app.error
    assert empty_db.read_bytes() == before


def test_missing_database_does_not_create_it(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setenv("PROCUREMENT_DB", str(path))
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15).run()
    assert not app.exception
    assert not path.exists()


def test_grid_states_escape_text_and_suppress_unquoted_prices():
    lines = [{"line_no": 1, "sku": "<script>bad</script>"}]
    vendors = [{"vendor_id": v, "name": v + "<&>"} for v in ("a", "b", "c", "d", "e")]
    fields = [{"rfx_line_no": 1, "vendor_id": v, "state": state, "value": value,
               "currency": "INR", "unit": "per <piece>"}
              for v, state, value in [("a", "extracted", 0), ("b", "needs_review", 12),
                                      ("c", "not_quoted", 987), ("d", "missing", 654)]]
    html = shell.comparison_html(lines, vendors, fields, mode="quoted")
    assert html.count('type="button" class="cmp-cell"') == 5
    for glyph, _ in shell.COMPARISON_GLYPHS.values():
        assert f">{glyph} " in html
    assert "<script>bad</script>" not in html and "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "INR 0.00" in html and "INR 12.00" in html
    assert ">– Not quoted</button>" in html and ">✗ No value</button>" in html
    assert html.count(">✗ not ingested</button>") == 1
    assert "per &lt;piece&gt;" in html                          # now in the cell's tooltip (title attribute), still escaped


def test_event_grid_retains_five_columns_and_thirty_rows():
    lines = [{"line_no": n, "sku": f"BOX-{n}"} for n in range(1, 31)]
    vendors = shell.comparison_vendors([{"vendor_id": "V1", "name": "Loaded vendor"}])
    fields = [{"rfx_line_no": 1, "vendor_id": "V1", "state": "extracted",
               "value": 42, "field_name": "unit_price", "currency": "INR"}]
    html = shell.comparison_html(lines, vendors, fields)
    assert html.count('<th scope="row">') == 30
    assert html.count('<td class=') == 150
    assert html.count(">✗ not ingested</button>") == 120
    assert len(vendors) == 5 and vendors[0]["name"] == "Loaded vendor"


def test_other_fields_do_not_masquerade_as_unit_prices():
    fields = [{"rfx_line_no": 1, "vendor_id": "V1", "field_name": "line_total",
               "state": "extracted", "value": 999}]
    html = shell.comparison_html([{"line_no": 1, "sku": "BOX"}],
                                [{"vendor_id": "V1", "name": "Vendor"}], fields)
    glyph, _ = shell.COMPARISON_GLYPHS["missing"]
    assert "not ingested" not in html                           # V1 is ingested (it has a line_total field); it just has no unit_price
    assert f"{glyph} No value" in html and "Quoted value: 999" not in html


def test_truth_database_rejected():
    with pytest.raises(ValueError, match="frozen truth"):
        shell.read_table(ROOT / "dataset/truth/truth.sqlite", "SELECT 1")


def test_legacy_deep_link_opens_evidence_and_selects_vendor(empty_db):
    with sqlite3.connect(empty_db) as conn:
        conn.execute("""INSERT INTO rfx_lines VALUES
            (1,'TEST-SKU','RSC',500,350,300,5,'BC',150,'150/120/150/120/150',
             20,'plain',1000,'piece',1.131,0.902538,0)""")
        conn.execute("""INSERT INTO vendors VALUES
            ('V1','Test vendor','test','xlsx','test','test',0,0,0,0)""")
        conn.execute("""INSERT INTO submissions VALUES
            ('TEST-SUB','V1','sample.xlsx','xlsx','2026-03-11',NULL,NULL,0,
             '2026-03-11',NULL,'INR',NULL,NULL,NULL)""")
        conn.execute("""INSERT INTO bid_fields
            (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,reason_code,anchor,snippet,resolving_question)
            VALUES ('TEST-SUB',1,'unit_price',42,'INR/piece','per_piece','INR',
                    'needs_review','arith_mismatch','Quotation!H10','<raw quote> 42.00','Confirm rate?')""")
    with sqlite3.connect(empty_db) as conn:
        conn.execute("UPDATE bid_fields SET derivation=?",
                     ("qty 1,000 × 42 = 42,000; vendor states <40,000>",))
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15)
    app.query_params["bid_vendor"] = "V1"
    app.query_params["bid_line"] = "1"
    app.run()
    assert not app.exception and not app.error
    assert app.sidebar.radio[0].value == "Comparison"
    assert len(app.get("dialog")) == 1                                           # a fresh click opens the evidence dialog, not a scroll
    assert app.code[0].value == "qty 1,000 × 42 = 42,000; vendor states <40,000>"
    assert app.code[-1].value == "<raw quote> 42.00"
    assert any("Quotation!H10" in t.value for t in app.text)
    assert any('class="needs_review"' in m.proto.body for m in app.get("html"))
    assert any("arith_mismatch" in m.value for m in app.markdown)
    assert [s.label for s in app.selectbox] == ["Select vendor to review"]        # no more Vendor/Line pair; one shared dropdown below
    assert app.selectbox(key="bid_vendor").value == "V1"
    grid = next(m.proto.body for m in app.get("html") if 'class="cmp-grid"' in m.proto.body)
    assert grid.count('<td class=') == 5

    # A rerun closes the one-shot dialog; the page always shows normalized rates.
    app.run()
    assert not app.get("dialog")
    assert not any(r.label == "View" for r in app.radio)
    assert not any(n.label == "Jump to line" for n in app.number_input)
    assert not any(b.label == "Go" for b in app.button)

    # A second click, on a vendor with no data at all: the dialog reopens and says so, and the shared dropdown follows it.
    app.query_params["bid_vendor"] = "V2"
    app.query_params["bid_line"] = "1"
    app.run()
    assert not app.exception
    assert len(app.get("dialog")) == 1
    assert any("not ingested" in i.value for i in app.info)
    assert app.selectbox(key="bid_vendor").value == "V2"
    assert any("No open clarifications for" in i.value for i in app.info)

    # A newer submission must drive the dialog's price and its own evidence, and clear the (now-extracted) clarification.
    with sqlite3.connect(empty_db) as conn:
        conn.execute("""INSERT INTO submissions
            (submission_id,vendor_id,file_name,format,received_on,expired_at_eval,eval_date,currency)
            VALUES ('REV','V1','revision.pdf','pdf','2026-03-12',0,'2026-03-12','INR')""")
        conn.execute("""INSERT INTO bid_fields
            (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,anchor,snippet)
            VALUES ('REV',1,'unit_price',43,'INR/piece','per_piece','INR',
                    'extracted','p2:bbox(1,2,3,4)','revised 43')""")
    app.query_params["bid_vendor"] = "V1"
    app.query_params["bid_line"] = "1"
    app.run()
    assert app.code[0].value == "revised 43"
    assert any("p2:bbox(1,2,3,4)" in t.value for t in app.text)
    grid = next(m.proto.body for m in app.get("html") if 'class="cmp-grid"' in m.proto.body)
    assert "43.00" not in grid and "42.00" not in grid  # no normalized record in this fixture; raw rate stays in evidence
    assert any("No open clarifications for" in i.value for i in app.info)         # the only flagged field was superseded by the extracted revision

    # Choosing a vendor manually (not via a click) does not reopen the dialog.
    app.selectbox(key="bid_vendor").set_value("V2").run()
    assert not app.get("dialog")
    app.sidebar.radio[0].set_value("Event").run()
    assert app.sidebar.radio[0].value == "Event"


def test_vendor_clarifications_aggregate_every_open_item_and_the_email_has_no_state_jargon(empty_db):
    """Bid-field and questionnaire items for one vendor, in one list; the copyable email is plain business language."""
    with sqlite3.connect(empty_db) as conn:
        for n, qty in ((1, 1000), (2, 2000)):
            conn.execute("""INSERT INTO rfx_lines VALUES
                (?,?,'RSC',500,350,300,5,'BC',150,'150/120/150/120/150',20,'plain',?,'piece',1.131,0.902538,0)""", (n, f"SKU-{n}", qty))
        conn.execute("INSERT INTO vendors VALUES ('V1','Sahyadri Test Ltd','test','xlsx','test','test',0,0,0,0)")
        conn.execute("""INSERT INTO submissions VALUES
            ('S1','V1','sample.xlsx','xlsx','2026-03-11',NULL,NULL,0,'2026-03-11',NULL,'INR',NULL,NULL,NULL)""")
        conn.executemany("""INSERT INTO bid_fields
            (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,reason_code,anchor,snippet,resolving_question)
            VALUES ('S1',?,?,?,?,?,?,?,?,?,?,?)""", [
            (1, "unit_price", 42, "INR/piece", "per_piece", "INR", "needs_review", "arith_mismatch", "Quotation!H10", "42.00", "Confirm rate?"),
            (2, "unit_price", None, None, None, None, "missing", "reference_unresolved", "Quotation!H11", "same as last year", "What was FY25 rate?"),
            (1, "line_total", 999, None, None, "INR", "extracted", None, "Quotation!K10", "999", None),   # not open: not a factor in the count
        ])
        conn.execute("""INSERT INTO questionnaire_answers
            (vendor_id,q_no,question,is_gate,gate_code,answer_text,state,reason_code,resolving_question)
            VALUES ('V1',3,'Do you hold FSC Chain-of-Custody certification?',1,'G3','','claimed_unsupported','no_attachment',
                    'V1: you state you hold FSC chain-of-custody certificate but no supporting document was attached. Please send the current certificate, showing its expiry date.')""")
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15).run()
    app.sidebar.radio[0].set_value("Comparison").run()
    assert not app.exception and not app.error
    assert app.selectbox(key="bid_vendor").value == "V1"
    items = [m.value for m in app.markdown if 'class="clar-item' in m.value]
    assert len(items) == 3                                                       # 2 bid_fields (not the extracted line_total) + 1 questionnaire answer
    assert any("Line 1" in i and "Unit price" in i for i in items)
    assert any("Line 2" in i and "Unit price" in i for i in items)
    assert any("Question 3" in i for i in items)
    jargon = ("needs_review", "not_quoted", "claimed_unsupported", "reference_unresolved", "arith_mismatch", "no_attachment")
    assert not any(term in i for i in items for term in jargon)

    assert any(b.label == "Download as .txt" for b in app.download_button)       # "Copy full email" is a JS button inside a components.html iframe, not an st.button

    # AppTest cannot read a download_button's file bytes; check the same email text through the functions the page calls to build it.
    with sqlite3.connect(empty_db) as conn:
        conn.row_factory = sqlite3.Row
        db_fields = [dict(r) for r in conn.execute("""SELECT b.field_id,b.field_name,b.rfx_line_no,b.value,b.unit,b.basis,b.currency,b.state,b.reason_code,
                                                              b.anchor,b.snippet,b.resolving_question,s.vendor_id,s.submission_id,s.file_name
                                                       FROM bid_fields b JOIN submissions s USING(submission_id)""")]
        db_lines = [dict(r) for r in conn.execute("SELECT * FROM rfx_lines")]
    from app.main import open_items_for_vendor, clarification_email
    email_items = open_items_for_vendor(empty_db, "V1", "Sahyadri Test Ltd", db_fields, db_lines)
    assert len(email_items) == 3
    email = clarification_email("Sahyadri Test Ltd", email_items, "Priya Nair")
    assert "Clarifications required" in email and "Priya Nair" in email
    assert not any(term in email for term in jargon)
    assert email.count("V1: ") == 0                                              # the redundant per-item vendor prefix is stripped


@pytest.mark.parametrize("vendor,line", [("UNKNOWN","1"),("V1","bad"),("V1","999"),("V1","")])
def test_invalid_cell_links_are_ignored(empty_db,vendor,line):
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15)
    app.query_params["bid_vendor"] = vendor
    app.query_params["bid_line"] = line
    app.run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Event"


def test_cell_cards_are_local_escaped_and_keyboard_accessible():
    grid = shell.comparison_html([{"line_no":12,"sku":"BOX"}],
                                [{"vendor_id":"V1","name":"<Vendor>"}], [])
    assert 'href=' not in grid and 'target=' not in grid
    assert 'type="button" class="cmp-cell" aria-expanded="false"' in grid
    assert "<Vendor>" not in grid
    assert 'aria-label="Bid source evidence"' in grid
    assert 'pointerenter' in grid and "e.key==='Escape'" in grid


def test_card_includes_escaped_record_evidence():
    card = shell.cell_evidence_html({"name":"Vendor"}, {"line_no":13,"sku":"BOX"},
        {"reason_code":"spec_incomplete", "resolving_question":"Confirm <GSM>?", "snippet":"<script>alert(1)</script>",
         "file_name":"quote.docx", "anchor":"para15", "derivation":"a < b", "value":12.3}, "INR 12.30", "Needs review", [])
    for text in ['Line 13', 'Confirm &lt;GSM&gt;?', 'quote.docx', 'para15', 'a &lt; b', '&lt;script&gt;']:
        assert text in card
    assert '<script>' not in card


def test_exact_status_glyphs():
    assert {k: v[0] for k, v in shell.STATES.items()} == {
        "extracted": "✓", "needs_review": "?", "not_quoted": "—", "missing": "∅"}


def test_settings_row_lives_only_on_event(empty_db):
    """The review block threshold widget is on Event's Settings expander and nowhere else (not duplicated on Comparison)."""
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15).run()
    assert not app.exception
    assert app.number_input[0].value == 5.0 and "5%" in app.number_input[0].label
    app.sidebar.radio[0].set_value("Comparison").run()
    assert not app.exception
    assert not any((n.key or "").startswith("setting_") for n in app.number_input)        # no threshold control on Comparison
    app.sidebar.radio[0].set_value("Event").run()
    app.number_input[0].set_value(9.0)
    app.button(key="setting_Event_review_block_threshold_pct_save").click().run()
    assert not app.exception and app.number_input[0].value == 9.0
    with sqlite3.connect(empty_db) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='review_block_threshold_pct'").fetchone()[0] == 9.0


@pytest.mark.parametrize("result_status", ["complete", "partial", "unavailable"])
def test_ask_displays_execution_status_and_evidence(empty_db, monkeypatch, result_status):
    from src import analyst, metering
    from src.analyst import Answer
    monkeypatch.setattr(metering, 'configure', lambda db: None)
    class FakeAnalyst:
        def __init__(self, store):
            pass
        def ask(self, question, progress=None):
            assert question == 'Show the event status'
            progress('Checking evidence')
            return Answer('Recorded facts.', tool_calls=[{'tool': 'event_summary', 'arguments': {}, 'result': '{"line_count": 3}'}],
                          status=result_status, execution_note='Provider timed out.' if result_status != 'complete' else None,
                          elapsed_seconds=1.5, model_requests=2)
    monkeypatch.setattr(analyst, 'Analyst', FakeAnalyst)
    app = AppTest.from_file(str(ROOT / 'app/main.py'), default_timeout=15).run()
    app.sidebar.radio[0].set_value('Ask').run()
    app.text_area(key='analyst_q').set_value('Show the event status')
    next(b for b in app.button if b.label == 'Ask').click().run()
    assert not app.exception and not app.error
    assert any(m.value == 'Recorded facts.' for m in app.markdown)
    assert any(result_status.capitalize() in c.value and '2 model requests' in c.value for c in app.caption)
    assert not any('Missing from the store' in w.value for w in app.warning)
    assert any(e.label == 'How this was computed (1 tool call)' for e in app.expander)


# ---- Comparison page stat tiles (pure functions, no DB) ----
def test_all_vendor_clean_requires_every_vendor_extracted():
    lines = [{"line_no": 1, "annual_qty": 10}, {"line_no": 2, "annual_qty": 10}, {"line_no": 3, "annual_qty": 10}]
    fields = [
        {"rfx_line_no": 1, "vendor_id": "A", "field_name": "unit_price", "state": "extracted"},
        {"rfx_line_no": 1, "vendor_id": "B", "field_name": "unit_price", "state": "extracted"},
        {"rfx_line_no": 2, "vendor_id": "A", "field_name": "unit_price", "state": "extracted"},
        {"rfx_line_no": 2, "vendor_id": "B", "field_name": "unit_price", "state": "needs_review"},
        {"rfx_line_no": 3, "vendor_id": "A", "field_name": "unit_price", "state": "extracted"},
        # line 3: B has no unit_price row at all (not ingested for this line)
    ]
    stats = shell.comparison_stats(lines, ["A", "B"], fields, {})
    assert stats["total_lines"] == 3
    assert stats["all_clean"] == 1                    # only line 1: every vendor extracted
    assert stats["any_flag"] == 1                      # only line 2: B is needs_review; line 3's missing cell carries no state at all


def test_any_flag_counts_needs_review_missing_and_not_quoted():
    lines = [{"line_no": n, "annual_qty": 1} for n in (1, 2, 3, 4)]
    fields = [{"rfx_line_no": n, "vendor_id": "A", "field_name": "unit_price", "state": state}
              for n, state in ((1, "needs_review"), (2, "missing"), (3, "not_quoted"), (4, "extracted"))]
    stats = shell.comparison_stats(lines, ["A"], fields, {})
    assert stats["any_flag"] == 3 and stats["all_clean"] == 1


def test_cheapest_overall_sums_normalized_totals_and_excludes_unpriced_vendors():
    lines = [{"line_no": 1, "annual_qty": 100}, {"line_no": 2, "annual_qty": 50}]
    norm_by_pair = {
        ("A", 1): {"inr_per_piece": 10.0}, ("A", 2): {"inr_per_piece": 20.0},      # A: 100*10 + 50*20 = 2,000
        ("B", 1): {"inr_per_piece": 8.0}, ("B", 2): {"inr_per_piece": 30.0},       # B: 100*8 + 50*30 = 2,300
    }
    stats = shell.comparison_stats(lines, ["A", "B", "C"], [], norm_by_pair)      # C has no normalized price anywhere
    assert stats["cheapest_vendor"] == "A" and stats["cheapest_total"] == 2000.0

    stats_none = shell.comparison_stats(lines, ["C"], [], norm_by_pair)
    assert stats_none["cheapest_vendor"] is None and stats_none["cheapest_total"] is None
