"""Export checks with a temporary DB and the real award engine."""
import sqlite3
import pytest
from streamlit.testing.v1 import AppTest
from app.test_shell import empty_db, ROOT
from app.award_note import generate_award_note
from src.normalize import DDL


@pytest.fixture
def award_db(empty_db):
    with sqlite3.connect(empty_db) as c:
        c.executescript(DDL)
        c.execute("INSERT INTO vendors(vendor_id,name,response_format) VALUES ('V1','Test vendor','xlsx')")
        c.execute("""INSERT INTO submissions(submission_id,vendor_id,file_name,format,received_on,expired_at_eval,eval_date,currency)
                     VALUES ('S1','V1','quote.xlsx','xlsx','2026-01-01',0,'2026-01-02','INR')""")
        for n,qty in [(1,1),(2,99)]:
            c.execute("""INSERT INTO rfx_lines(line_no,sku,length_mm,width_mm,height_mm,ply,flute,liner_gsm,gsm_stack,bf,print_spec,annual_qty,should_cost_inr_pc)
                         VALUES (?, 'test',500,350,300,5,'BC',150,'150/120/150/120/150',20,'plain',?,12)""",(n,qty))
            c.execute("""INSERT INTO bid_fields(field_id,submission_id,rfx_line_no,field_name,value,state,anchor,snippet,resolving_question,reason_code)
                         VALUES (?,'S1',?,'unit_price',10,?,'Sheet!A1','verbatim | <rate>','Confirm price?','arith_mismatch')""",(n,n,'needs_review' if n==1 else 'extracted'))
            c.execute("""INSERT INTO norm_prices(submission_id,rfx_line_no,field_id,extraction_state,inr_per_piece,landed_inr_per_piece,comparability,landed_comparability,freight_status,comparability_reasons,landed_reasons)
                         VALUES ('S1',?,?,?,10,10,'comparable','comparable','included','[]','[]')""",(n,n,'needs_review' if n==1 else 'extracted'))
        for n in (1,2,3):
            c.execute("""INSERT INTO questionnaire_answers(vendor_id,q_no,question,is_gate,gate_code,answer_text,state,gate_status,evidence_source)
                         VALUES ('V1',?,'Gate',1,?,'Yes','extracted','pass','vendor_master')""",(n,f'G{n}'))
        c.execute("""INSERT INTO questionnaire_answers(vendor_id,q_no,question,is_gate,answer_text,state,reason_code,resolving_question)
                     VALUES ('V1',4,'Claim',0,'Certified','claimed_unsupported','certificate_expired','Send renewal')""")
        for key,value,text in [('event_id',None,'EVENT-TEST'),('fx_usd_inr',88.5,None),('take_up_factor:BC',1.45,None)]:
            c.execute("INSERT INTO assumptions(assumption_id,label,value,value_text,source,as_of,editable) VALUES (?,?,?,?, 'test source','2026-01-01',0)",(key,key,value,text))
    return empty_db


def test_populated_award_note_and_read_only(award_db):
    before=award_db.read_bytes()
    note,gaps=generate_award_note(award_db)
    for heading in ['Header','Recommendation','Gates evaluated','Assumptions in force','Exceptions','Audit trail']:
        assert '## '+heading in note
    for text in ['EVENT-TEST','INR 1,200.00','INR 1,000.00','Award to Test vendor — single source','vendor_master','certificate_expired','Confirm price?','quote.xlsx','Sheet!A1','verbatim | <rate>','1.00%']:
        assert text in note
    assert not gaps
    assert award_db.read_bytes()==before


def test_missing_data_explicit(empty_db):
    note,gaps=generate_award_note(empty_db)
    assert len(gaps)>5
    assert 'No awarded lines' in note
    assert 'Total landed cost: INR 0' not in note


def test_export_button(award_db):
    app=AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=30).run()
    app.sidebar.radio[0].set_value('Comparison').run()
    assert not app.exception
    assert not app.error
    assert not [b for b in app.button if b.label=='Export award note']      # the export button is gone; the live preview remains
    note,_=generate_award_note(award_db,'single_vendor','landed')
    assert '## Audit trail' in note


def test_ex_freight_never_labelled_landed(award_db):
    note,gaps=generate_award_note(award_db,basis='ex_freight')
    assert 'its total is not a landed cost' in note
    assert gaps

def test_above_threshold_not_shown_as_allowed_exception(award_db):
    with sqlite3.connect(award_db) as c:
        c.execute("UPDATE bid_fields SET state='needs_review',resolving_question='HIGH BLOCK QUESTION' WHERE field_id=2")
        c.execute("UPDATE norm_prices SET extraction_state='needs_review' WHERE field_id=2")
    note,gaps=generate_award_note(award_db)
    section=note.split('## Exceptions')[1].split('## Audit trail')[0]
    assert 'Confirm price?' in section
    assert 'HIGH BLOCK QUESTION' not in section
    assert 'recommendable: False' in note
    assert any('resolve before recommending' in g.lower() for g in gaps)


def test_threshold_uses_database_setting(award_db):
    from src.settings import DDL as SETTINGS_DDL
    with sqlite3.connect(award_db) as c:
        c.executescript(SETTINGS_DDL)
        c.execute("INSERT INTO settings(key,value) VALUES ('review_block_threshold_pct',0.5)")
    note,gaps=generate_award_note(award_db)
    assert 'threshold: 0.50%' in note
    assert 'source: settings.review_block_threshold_pct' in note
    assert 'No qualifying review fields' in note
    assert 'recommendable: False' in note
