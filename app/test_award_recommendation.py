"""Recommendation UI regressions, including read-only checks of the current demo DB."""
import sqlite3
import pytest
from streamlit.testing.v1 import AppTest
from app import award_words
from app.test_award_note import award_db
from app.test_shell import ROOT, empty_db


def test_recommendation_export_label(award_db):
    with sqlite3.connect(award_db) as con:
        model = award_words.build(con, "single_vendor", "ex_freight")
    note = award_words.markdown(model)
    assert "**Recommendation:**" in note
    assert "System recommendation" not in note
    assert award_words.BASIS_LINE["ex_freight"] not in note  # caption belongs above the preview


@pytest.fixture
def current_comparison(monkeypatch):
    db = ROOT / "procurement.db"
    if not db.exists():
        pytest.skip("Current demo database is not distributed in the source-only repository")
    monkeypatch.setenv("PROCUREMENT_DB", str(db))
    before = db.read_bytes()
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("Comparison").run()
    assert not app.exception and not app.error
    yield app
    assert db.read_bytes() == before


def test_cheapest_overall_current_db_flags_om_sai(current_comparison):
    app = current_comparison
    tile = next(m for m in app.metric if m.label == "Cheapest overall")
    assert "Om Sai" in tile.value and "V3" in tile.value
    assert any(c.value == "⚠ Not eligible — fails gate checks" for c in app.caption)
    assert any(e.label == "Award recommendation" for e in app.expander)
    assert not any(r.label == "View" for r in app.radio)
    assert not any(n.label == "Jump to line" for n in app.number_input)


def test_current_split_callout_and_advisory(current_comparison):
    app = current_comparison
    next(r for r in app.radio if r.label == "Award scenario").set_value("Split across vendors").run()
    assert not app.exception and not app.error
    assert any("Split not recommended." in w.value for w in app.warning)
    rendered = "\n".join(m.value for m in app.markdown)
    with sqlite3.connect(f"file:{ROOT / 'procurement.db'}?mode=ro", uri=True) as con:
        model = award_words.build(con, "gated_split", "ex_freight")
    callout = next(s for s in model["sections"] if s[0] == "saving_callout")
    assert callout[1] in rendered and callout[2] in rendered
    assert "Why the difference" in rendered and "Why this is not recommended" in rendered
    assert sum(c.value == award_words.BASIS_LINE["ex_freight"] for c in app.caption) == 1
    assert {"On paper", "In reality"} <= {c.value for c in app.caption}
    note = award_words.markdown(model)
    assert "Split not recommended." in note
    assert "DO NOT SPLIT" not in note and "System recommendation" not in note
    assert callout[1] in note and callout[2] in note
