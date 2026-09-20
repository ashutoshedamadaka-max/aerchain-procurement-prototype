"""Settings save, validation and dependent export refresh using temporary data."""
import sqlite3
from streamlit.testing.v1 import AppTest
from app.test_award_note import award_db
from app.test_shell import empty_db, ROOT
from src import settings
from app.award_note import generate_award_note


def test_save_refreshes_export_and_both_pages(award_db):
    app=AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=30).run()
    assert app.number_input[0].value == 5
    assert app.number_input[0].proto.help.startswith('The award engine blocks a recommendation when any single line worth more than X% of event value')
    app.sidebar.radio[0].set_value('Comparison').run()
    note,gaps=generate_award_note(award_db,'single_vendor','landed')                     # no export button any more: seed the cached note the save refreshes
    app.session_state['award_note_export']=(str(award_db),'single_vendor','landed',note,gaps)
    app.run()
    assert 'recommendable: True' in app.session_state['award_note_export'][3]
    app.number_input[0].set_value(0.5).run()
    next(b for b in app.button if b.label=='Save').click().run()
    assert not app.exception and not app.error
    assert 'recommendable: False' in app.session_state['award_note_export'][3]
    assert 'threshold: 0.50%' in app.session_state['award_note_export'][3]
    with sqlite3.connect(award_db) as con:
        assert settings.get(con,'review_block_threshold_pct') == 0.5
    app.sidebar.radio[0].set_value('Event').run()
    assert app.number_input[0].value == 0.5
    app.number_input[0].set_value(8).run()
    next(b for b in app.button if b.label=='Save').click().run()
    app.sidebar.radio[0].set_value('Comparison').run()
    assert app.number_input[0].value == 8
    assert 'recommendable: True' in app.session_state['award_note_export'][3]


def test_validation_error_inline_without_write(empty_db,monkeypatch):
    def reject(*args):
        raise ValueError('Threshold rejected by settings API')
    monkeypatch.setattr(settings,'set_value',reject)
    before=empty_db.read_bytes()
    app=AppTest.from_file(str(ROOT/'app/main.py'),default_timeout=30).run()
    next(b for b in app.button if b.label=='Save').click().run()
    assert not app.exception
    assert app.error[0].value == 'Threshold rejected by settings API'
    assert empty_db.read_bytes()==before
