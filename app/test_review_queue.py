"""Review queue arithmetic, selection, sorting and read-only UI checks."""
from pathlib import Path
import sqlite3
import pytest
from src.analyst import Store
from src.normalize import DDL
from streamlit.testing.v1 import AppTest
from app.test_shell import shell, empty_db, ROOT


def test_direct_absolute_zero_median_and_unknown():
    items = [
        {"field_id":1,"vendor_id":"A","rfx_line_no":1,"annual_qty":1000,"unit_price":-5},
        {"field_id":2,"vendor_id":"A","rfx_line_no":1,"annual_qty":1000,"unit_price":0},
        {"field_id":3,"vendor_id":"A","rfx_line_no":1,"annual_qty":1000,"unit_price":None},
        {"field_id":4,"vendor_id":"A","rfx_line_no":2,"annual_qty":1000,"unit_price":None},
        {"field_id":5,"vendor_id":"A","rfx_line_no":1,"annual_qty":None,"unit_price":10},
        {"field_id":6,"vendor_id":"A","rfx_line_no":1,"annual_qty":0,"unit_price":10},
    ]
    prices = [{"vendor_id":v,"rfx_line_no":1,"value":p,"state":"extracted"}
              for v,p in [("A",999),("B",100),("B",7),("C",13)]]
    prices += [{"vendor_id":"D","rfx_line_no":1,"value":999,"state":"not_quoted"}]
    rows = shell.add_review_risk(items,prices)
    assert [r["risk"] for r in rows] == [5000,0,10000,None,None,0]
    assert "median" in rows[2]["risk_basis"]


@pytest.mark.parametrize("bad",[float("nan"),float("inf"),"bad",None])
def test_unusable_prices_are_unknown(bad):
    item={"vendor_id":"A","rfx_line_no":1,"annual_qty":100,"unit_price":bad}
    assert shell.add_review_risk([item],[])[0]["risk"] is None


@pytest.fixture
def review_db(empty_db):
    with sqlite3.connect(empty_db) as c:
        for n,qty in [(1,1000),(2,2000)]:
            c.execute("""INSERT INTO rfx_lines
                (line_no,sku,length_mm,width_mm,height_mm,ply,flute,liner_gsm,gsm_stack,bf,print_spec,annual_qty)
                VALUES (?, ?,500,350,300,5,'BC',150,'150/120/150/120/150',20,'plain',?)""",
                (n,f"BOX-{n}",qty))
        for v in ("V1","V2","V3"):
            c.execute("INSERT INTO vendors(vendor_id,name,response_format) VALUES (?,?,'xlsx')",
                      (v,f"Vendor {v}"))
            c.execute("""INSERT INTO submissions
                (submission_id,vendor_id,file_name,format,received_on,expired_at_eval,eval_date,currency)
                VALUES (?,?,?,'xlsx','2026-03-11',0,'2026-03-11','INR')""",(v,v,f"{v}.xlsx"))
        for fid,v,line,field,value,state,reason in [
            (1,"V1",1,"unit_price",10,"needs_review","arith_mismatch"),
            (2,"V1",1,"line_total",987,"needs_review","arith_mismatch"),
            (3,"V2",1,"unit_price",30,"extracted",None),
            (4,"V3",1,"unit_price",None,"missing","reference_unresolved"),
            (5,"V1",2,"unit_price",None,"missing","reference_unresolved"),
            (6,"V2",2,"unit_price",None,"missing","spec_incomplete"),
            (7,"V3",2,"unit_price",None,"not_quoted","reference_unresolved"),
            (8,"V2",1,"declared_liner_gsm",120,"needs_review","spec_incomplete"),
        ]:
            c.execute("""INSERT INTO bid_fields
                (field_id,submission_id,rfx_line_no,field_name,value,state,reason_code,currency,resolving_question)
                VALUES (?,?,?,?,?,?,?,'INR','Please confirm <rate> & basis.')""",
                (fid,v,line,field,value,state,reason))
        c.executescript(DDL)                                     # one normalized price, so the exposure total is a real number
        c.execute("""INSERT INTO norm_prices(submission_id,rfx_line_no,field_id,extraction_state,inr_per_piece,landed_inr_per_piece,comparability,landed_comparability,freight_status,comparability_reasons,landed_reasons)
                     VALUES ('V1',1,1,'needs_review',10,10,'comparable','comparable','included','[]','[]')""")
    return empty_db


def test_exact_query_and_risk_are_read_only(review_db):
    before=review_db.read_bytes()
    rows=shell.review_queue(review_db)
    assert [r["field_id"] for r in rows] == [1,2,4,5,8]
    assert {r["field_id"]:r["risk"] for r in rows} == {1:10000,2:10000,4:20000,5:None,8:30000}
    assert [r["field_id"] for r in shell.sorted_review_items(rows)] == [5,8,4,1,2]
    assert review_db.read_bytes()==before


def test_sortable_html_unknown_pinned_and_escaping(review_db):
    rows=shell.review_queue(review_db)
    html=shell.review_table_html(rows)
    assert html.count('scope="col"')==6
    assert 'aria-sort="descending"' in html
    assert "review_sort=risk&amp;review_dir=asc" in html
    assert '<td class="risk"' in html
    assert "<em>Please confirm &lt;rate&gt; &amp; basis.</em>" in html
    assert "value unknown" in html
    assert "₹ 30,000" in html
    assert [r["field_id"] for r in shell.sorted_review_items(rows,"risk","asc")] == [5,1,2,4,8]


def test_event_queue_metrics_and_sort_navigation(review_db):
    before=review_db.read_bytes()
    app=AppTest.from_file(str(ROOT/"app/main.py"),default_timeout=15).run()
    assert not app.exception and not app.error
    metrics={m.label:m.value for m in app.metric}
    assert metrics["Fields flagged"]=="5"
    assert "Total ₹ at risk (known)" not in metrics
    expected=Store(review_db).review_exposure()["known_exposure_inr"]
    assert expected and metrics[shell.EXPOSURE_LABEL]==f"₹ {shell.fmt.indian(expected,0)}"      # not the old raw field sum (₹ 70,000)
    assert any(c.value=="sorted by potential impact, not by document order." for c in app.caption)
    app.query_params["review_sort"]="risk"
    app.query_params["review_dir"]="asc"
    app.run()
    assert app.sidebar.radio[0].value=="Event"
    table=next(m.value for m in app.markdown if 'class="review-table"' in m.value)
    assert 'aria-sort="ascending"' in table
    app.sidebar.radio[0].set_value("Comparison").run()
    assert app.sidebar.radio[0].value=="Comparison"
    assert review_db.read_bytes()==before


@pytest.mark.skipif(not (ROOT/"procurement.db").exists(),reason="procurement.db is not part of the public repository")
def test_event_total_matches_review_exposure_for_the_current_database():
    """Regression: the Event page's total is review_exposure()'s known_exposure_inr (the value the analyst reports), formatted, for the current DB."""
    app=AppTest.from_file(str(ROOT/"app/main.py"),default_timeout=30).run()
    assert not app.exception
    shown={m.label:m.value for m in app.metric}[shell.EXPOSURE_LABEL]
    total=Store(ROOT/"procurement.db").review_exposure()["known_exposure_inr"]
    assert total is not None and shown==f"₹ {shell.fmt.indian(total,0)}"
