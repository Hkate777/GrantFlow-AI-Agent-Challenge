import json
import subprocess
import sys
from pathlib import Path

from kkg.pipeline import load_applications


BASE = Path(__file__).resolve().parents[1]


def test_cli_emits_draft_and_structured_review_without_overwriting_prior_run(tmp_path):
    output = tmp_path / "results"
    command = [sys.executable, "-X", "utf8", str(BASE / "cli.py"), "--out", str(output)]
    first = subprocess.run(command, cwd=BASE, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert first.returncode == 0, first.stderr
    path = output / "review/APP-2027-002_review.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    assert review["status"] == "pending_deliberation"
    assert review["notice"] is None
    assert len(review["comments"]) == 2
    previous = (output / "audit_log.json").read_bytes()
    second = subprocess.run(command, cwd=BASE, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert second.returncode == 0, second.stderr
    assert (output / "audit_log.json").read_bytes() == previous
    assert len(list(output.glob("runs/*/audit_log.json"))) == 1


def test_directory_import_keeps_valid_applications_after_a_broken_manifest(tmp_path):
    bad = tmp_path / "a-invalid"
    good = tmp_path / "b-valid"
    bad.mkdir()
    good.mkdir()
    (bad / "meta.json").write_text("{", encoding="utf-8")
    (good / "meta.json").write_text('{"app_id":"VALID-1"}', encoding="utf-8")
    errors = []
    applications = load_applications(tmp_path, errors=errors)
    assert [app.app_id for app in applications] == ["VALID-1"]
    assert len(errors) == 1
    assert "a-invalid" in errors[0]
