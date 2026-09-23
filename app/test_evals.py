"""Evals presentation must retain limitations and never imply a fresh model evaluation."""
import json
from app.test_shell import shell, ROOT
from streamlit.testing.v1 import AppTest


def test_recorded_evals_are_scoped_and_expose_known_errors():
    snapshot = ROOT / "dataset/eval/eval_snapshot.json"
    if not snapshot.exists():
        import pytest
        pytest.skip("Snapshot is not distributed in the source-only repository")
    before = snapshot.read_bytes()
    snap = json.loads(before)
    app = AppTest.from_file(str(ROOT / "app/main.py"), default_timeout=30).run()
    app.sidebar.radio[0].set_value("Evals").run()
    assert not app.exception and not app.error
    metrics = {m.label: m.value for m in app.metric}
    q = snap["questionnaire"]
    assert metrics["Questionnaire answers"] == f"{q['overall']['silent_wrong']} / {q['answers']}"
    assert metrics["Recorded tests skipped"] == str(snap["tests"]["skipped"])
    text = "\n".join(x.value for x in [*app.markdown, *app.caption])
    assert "Recorded snapshot:" in text and "Not a live evaluation" in text
    assert "claimed_unsupported" in text and "Expected state" in text
    assert "Not yet measured in this report" in [x.value for x in app.subheader]
    assert "Precision proven" not in text
    assert snapshot.read_bytes() == before


def test_matrix_does_not_label_correct_flags_as_over_cautious():
    row = shell.calibration_table({"Vendor": dict(flagged_wrong=1, flagged_right=2, silent_wrong=3, not_flagged_right=4)})[0]
    assert row["Correct · flagged for review"] == 2
    assert row["Incorrect · not flagged"] == 3
    assert row["Checked"] == 10
