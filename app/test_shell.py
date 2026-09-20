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
    html = shell.comparison_html(lines, vendors, fields)
    assert html.count('<span class="status">') == 5
    for glyph, label in shell.STATES.values():
        assert f">{glyph} {label}</span>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "INR 0.00" in html and "INR 12.00" in html
    assert "987" not in html and "654" not in html
    assert html.count("not ingested") == 1
    assert "per &lt;piece&gt;" in html


def test_event_grid_retains_five_columns_and_thirty_rows():
    lines = [{"line_no": n, "sku": f"BOX-{n}"} for n in range(1, 31)]
    vendors = shell.comparison_vendors([{"vendor_id": "V1", "name": "Loaded vendor"}])
    fields = [{"rfx_line_no": 1, "vendor_id": "V1", "state": "extracted",
               "value": 42, "field_name": "unit_price", "currency": "INR"}]
    html = shell.comparison_html(lines, vendors, fields)
    assert html.count('<th scope="row">') == 30
    assert html.count('<td class=') == 150
    assert html.count("not ingested") == 120
    assert len(vendors) == 5 and vendors[0]["name"] == "Loaded vendor"


def test_other_fields_do_not_masquerade_as_unit_prices():
    fields = [{"rfx_line_no": 1, "vendor_id": "V1", "field_name": "line_total",
               "state": "extracted", "value": 999}]
    html = shell.comparison_html([{"line_no": 1, "sku": "BOX"}],
                                [{"vendor_id": "V1", "name": "Vendor"}], fields)
    assert "not ingested" not in html
    assert "∅ Missing" in html and "999" not in html


def test_truth_database_rejected():
    with pytest.raises(ValueError, match="frozen truth"):
        shell.read_table(ROOT / "dataset/truth/truth.sqlite", "SELECT 1")


def test_populated_provenance_dropdowns(empty_db):
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
    assert app.sidebar.radio[0].value == "Comparison"
    assert app.selectbox(key="bid_vendor").value == "V1" and app.selectbox(key="bid_line").value == 1
    assert app.code[0].value == "qty 1,000 × 42 = 42,000; vendor states <40,000>"
    assert not app.exception and not app.error
    assert [s.label for s in app.selectbox] == ["Award scenario", "Award cost basis", "Vendor", "Line", "Draft clarifications for"]
    assert app.code[-1].value == "<raw quote> 42.00"
    assert any("Quotation!H10" in t.value for t in app.text)
    assert any('class="needs_review"' in m.value for m in app.markdown)


    assert any("arith_mismatch" in m.value for m in app.markdown)
    assert any("Confirm rate?" in m.value and 'class="clarification"' in m.value for m in app.markdown)
    grid = next(m.value for m in app.markdown if 'class="quote-grid"' in m.value)
    assert grid.count('<td class=') == 5
    app.selectbox(key="bid_vendor").set_value("V2").run()
    assert not app.exception
    assert any("not ingested" in i.value for i in app.info)
    assert not app.code
    assert next(m.value for m in app.markdown if 'class="quote-grid"' in m.value) == grid

    # A newer vendor submission must drive both price and source evidence.
    with sqlite3.connect(empty_db) as conn:
        conn.execute("""INSERT INTO submissions
            (submission_id,vendor_id,file_name,format,received_on,expired_at_eval,eval_date,currency)
            VALUES ('REV','V1','revision.pdf','pdf','2026-03-12',0,'2026-03-12','INR')""")
        conn.execute("""INSERT INTO bid_fields
            (submission_id,rfx_line_no,field_name,value,unit,basis,currency,state,anchor,snippet)
            VALUES ('REV',1,'unit_price',43,'INR/piece','per_piece','INR',
                    'extracted','p2:bbox(1,2,3,4)','revised 43')""")
    app.selectbox(key="bid_vendor").set_value("V1").run()
    assert app.code[0].value == "revised 43"
    assert any("p2:bbox(1,2,3,4)" in t.value for t in app.text)
    grid = next(m.value for m in app.markdown if 'class="quote-grid"' in m.value)
    assert "43.00" in grid and "42.00" not in grid
    assert grid.count('<span class="status">') == 5


    # A cell URL changes both widgets; manual selections still work afterward.
    app.query_params["bid_vendor"] = "V2"
    app.query_params["bid_line"] = "1"
    app.run()
    assert app.selectbox(key="bid_vendor").value == "V2" and app.selectbox(key="bid_line").value == 1
    assert any("not ingested" in i.value for i in app.info)
    app.selectbox(key="bid_vendor").set_value("V1").run()
    assert app.selectbox(key="bid_vendor").value == "V1"
    assert app.code[0].value == "revised 43"
    assert not any('class="clarification"' in m.value for m in app.markdown)
    assert not any('class="evidence-badges"' in m.value and "Reason:" in m.value
                   for m in app.markdown)
    app.sidebar.radio[0].set_value("Event").run()
    assert app.sidebar.radio[0].value == "Event"


@pytest.mark.parametrize("vendor,line", [("UNKNOWN","1"),("V1","bad"),("V1","999"),("V1","")])
def test_invalid_cell_links_are_ignored(empty_db,vendor,line):
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15)
    app.query_params["bid_vendor"] = vendor
    app.query_params["bid_line"] = line
    app.run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Event"


def test_cell_links_are_local_escaped_and_keyboard_accessible():
    from html import unescape
    from urllib.parse import urlsplit, parse_qs
    import re
    grid = shell.comparison_html([{"line_no":12,"sku":"BOX"}],
                                [{"vendor_id":"V1","name":"<Vendor>"}], [])
    link = re.search(r'href="([^"]+)"',grid).group(1)
    url = urlsplit(unescape(link))
    assert not url.netloc and not url.scheme
    assert parse_qs(url.query) == {"bid_vendor":["V1"],"bid_line":["12"]}
    assert url.fragment == "source-evidence"
    assert 'target="_self"' in grid and "aria-label=" in grid
    assert "<Vendor>" not in grid


def test_exact_status_glyphs():
    assert {k: v[0] for k, v in shell.STATES.items()} == {
        "extracted": "✓", "needs_review": "?", "not_quoted": "—", "missing": "∅"}


def test_settings_row_edits_threshold_on_both_pages(empty_db):
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=15).run()
    for page in ("Event", "Comparison"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
        assert app.number_input[0].value == 5.0 and "5%" in app.number_input[0].label
    app.number_input[0].set_value(9.0)
    app.button(key="setting_Comparison_review_block_threshold_pct_save").click().run()
    assert not app.exception and app.number_input[0].value == 9.0
    with sqlite3.connect(empty_db) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key='review_block_threshold_pct'").fetchone()[0] == 9.0
    app.sidebar.radio[0].set_value("Event").run()
    assert app.number_input[0].value == 9.0


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
