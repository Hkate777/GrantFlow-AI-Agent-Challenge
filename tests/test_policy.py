import json
from dataclasses import replace
from pathlib import Path

import pytest

from kkg.check import check_completeness
from kkg.llm import MockLLM, get_llm
from kkg.models import DocType, Document, FindingType, ReviewScore
from kkg.pipeline import Pipeline, load_applications
from kkg.program import Program, RequiredDoc
from kkg.review import aggregate


BASE = Path(__file__).resolve().parents[1]


@pytest.fixture
def program():
    return Program.load(BASE / "configs/program_demo.yaml")


@pytest.fixture
def applications():
    return load_applications(BASE / "data/demo/applications")


def test_demo_conflict_holds_result(program, applications):
    scores = [ReviewScore(row["reviewer"], row["criteria"], row["comment"])
              for row in json.loads((BASE / "data/demo/reviews/reviews_demo.json").read_text(encoding="utf-8"))
              if row["app_id"] == "APP-2027-002"]
    result = Pipeline(program, MockLLM()).review(applications[1], scores)
    assert result["weighted_total"] == 62.5
    assert result["spread"] == 41
    assert result["status"] == "pending_deliberation"
    assert "needs_materials" in result["holds"]
    assert result["notice"] is None


def test_no_scores_is_pending(program, applications):
    result = Pipeline(program, MockLLM()).review(applications[0], [])
    assert result["status"] == "pending_scores"
    assert result["weighted_total"] is None
    assert result["notice"] is None


@pytest.mark.parametrize("kind", ["duplicate", "above_max", "missing", "nonfinite"])
def test_invalid_reviews_never_produce_a_total(kind, program):
    valid = {entry.name: entry.max_score for entry in program.criteria}
    first_dimension = next(iter(valid))
    if kind == "duplicate":
        scores = [ReviewScore("Reviewer A", valid), ReviewScore(" reviewer a ", valid)]
    else:
        payload = dict(valid)
        if kind == "missing":
            del payload[first_dimension]
        else:
            payload[first_dimension] = 125 if kind == "above_max" else float("nan")
        scores = [ReviewScore("Reviewer A", payload)]
    result = aggregate(scores, program)
    assert result["validation_errors"]
    assert result["weighted_total"] is None


def test_explicit_negative_eligibility_and_format_rules_are_applied(program, applications):
    selected = replace(program, required_docs=[RequiredDoc("proposal", "pdf")])
    application = replace(applications[0], program="OTHER-PLAN",
                          fields={**applications[0].fields, "org_type": "ineligible"},
                          docs=[Document("proposal.txt", "proposal.txt", DocType.TEXT, "Ordinary text")])
    findings = check_completeness(application, selected)
    assert {"program_id", "allowed_org_types", "required_docs"} <= {finding.source for finding in findings}
    assert any(finding.finding_type == FindingType.FORMAT and finding.needs_human for finding in findings)


def test_program_rejects_zero_score_range(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("program_id: P\nname: Example\ncriteria: [{name: Score, max_score: 0}]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_score"):
        Program.load(path)


def test_web_default_model_stays_local_with_a_key_in_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused-test-value")
    assert isinstance(get_llm("auto"), MockLLM)
