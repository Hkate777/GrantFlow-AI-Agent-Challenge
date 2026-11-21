"""可追溯與質量閉環回歸：數字台賬、依據卡、評測、合規分級、決策日誌。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from kkg.compliance import (COVERAGE_STATEMENT, ComplianceLevel, classify,
                            to_findings)
from kkg.decision_journal import DecisionJournal
from kkg.eval_traces import evaluate_batch, evaluate_trace, eval_markdown, judge_trace
from kkg.explain import (EvidenceBasis, evidence_bundle, evidence_for,
                         cards_markdown, ordinal_label)
from kkg.guard import (build_ledger, check_ledger, normalize_number,
                       review_output)
from kkg.models import Application, Finding, FindingType, Severity
from kkg.program import Program
from kkg.scoring import (deterministic_score, score_markdown, validate_weights,
                         validate_program_weights)


# ---------- ① 數字台賬守衛 ----------

def test_number_normalization():
    assert normalize_number("500,000") == "500000"
    assert normalize_number("0500000") == "500000"
    assert normalize_number("87.50") == "87.5"


def test_ledger_allows_only_known_numbers():
    ledger = build_ledger({"budget": 500000, "score": 87.5})
    ok = check_ledger("總分87.5，金額500000，2027年，第3人", ledger)
    assert ok.ok
    assert ok.checked == 4
    assert ok.exempted == 2  # 年份與序數豁免
    bad = check_ledger("金額999999，評審87分", ledger)
    assert not bad.ok
    assert set(bad.unknown) == {"999999", "87"}


def test_ledger_rejects_thousand_separator_hallucination():
    ledger = build_ledger({"budget": 500000})
    # 台賬內 500,000 視為已知；台賬外 5,000,000 視為幻覺
    assert check_ledger("預算港幣500,000元", ledger).ok
    assert not check_ledger("預算港幣5,000,000元", ledger).ok


def test_ledger_constructed_from_frozen_and_container_sources():
    ledger = build_ledger(["第 4 季", ("人數", 12), {"金額": "1,200"}])
    assert {"4", "12", "1200"}.issubset(set(ledger))


def test_review_output_with_ledger_backward_compatible():
    assert review_output("普通文字").ok
    bad = review_output("金額999999元", ledger=build_ledger({"budget": 500000}))
    assert not bad.ok
    assert any("台賬" in reason for reason in bad.reasons)


# ---------- ② 確定性依據卡與零 LLM 評分 ----------

def _finding() -> Finding:
    return Finding(FindingType.ANOMALY, Severity.ERROR, "預算偏離",
                   detail="budget.csv 第2列", source="budget_cap")


def test_evidence_card_traceable_and_deterministic():
    card = evidence_for(_finding(), rank=1)
    assert card.traceable
    assert card.rank_label == "第一"
    assert card.narrative == evidence_for(_finding(), rank=1).narrative
    assert "規則" in card.rationale


def test_evidence_bundle_orders_critical_first():
    findings = [
        Finding(FindingType.FORMAT, Severity.INFO, "格式提示"),
        Finding(FindingType.ELIGIBILITY, Severity.CRITICAL, "資格不符"),
    ]
    cards = evidence_bundle(findings)
    assert cards[0].severity == "critical"
    assert cards[0].rank == 1
    assert cards[1].rank == 2


def test_evidence_card_imputed_requires_note():
    with pytest.raises(ValueError):
        evidence_for(_finding(), imputed=True)
    card = evidence_for(_finding(), imputed=True, imputed_note="由同批中位數推斷")
    assert card.imputed and card.traceable


def test_ordinal_labels():
    assert ordinal_label(1) == "第一"
    assert ordinal_label(3) == "第三"
    assert ordinal_label(99) == "第99"


def test_evidence_card_markdown_contains_basis():
    card = evidence_for(_finding(), rank=1,
                        basis=[EvidenceBasis("budget.csv", "第2列", "500000")])
    md = cards_markdown([card])
    assert "budget.csv" in md and "第一" in md


def test_weights_strict_requires_unit_sum():
    strict = validate_weights({"a": 3, "b": 3, "c": 2, "d": 2}, mode="strict")
    assert not strict.ok
    assert any("1.0" in issue for issue in strict.issues)
    relative = validate_weights({"a": 3, "b": 3, "c": 2, "d": 2}, mode="relative")
    assert relative.ok
    assert abs(sum(relative.normalized.values()) - 1.0) < 1e-9


def test_weights_reject_nonpositive():
    assert not validate_weights({"a": -1, "b": 2}).ok
    assert not validate_weights({}).ok


def test_deterministic_score_and_missing_values():
    prog = Program.load("configs/program_demo.yaml")
    full = deterministic_score({"創新性": 25, "可行性": 20, "團隊能力": 18, "社會價值": 16}, prog)
    assert full.complete and full.total is not None
    partial = deterministic_score({"創新性": 25}, prog)
    assert partial.total is None
    assert set(partial.missing) == {"可行性", "團隊能力", "社會價值"}
    assert "缺失" in score_markdown(partial)
    assert validate_program_weights(prog).ok  # 現有配置在 relative 模式下通過


# ---------- ③ 評測框架 ----------

def _rich_card(severity=Severity.ERROR, sources=2):
    basis = [EvidenceBasis("proposal.md", "第3行", "資料甲")]
    if sources > 1:
        basis.append(EvidenceBasis("budget.csv", "第2列", "資料乙"))
    return evidence_for(Finding(FindingType.ANOMALY, severity, "判定內容"),
                        rank=1, basis=basis)


def test_rule_checks_all_pass_for_well_sourced_card():
    verdict = evaluate_trace("T1", _rich_card(),
                             known_sources={"proposal.md", "budget.csv"})
    assert verdict.rule_pass and verdict.passed
    assert [rule.passed for rule in verdict.rules] == [True, True, True]


def test_single_source_critical_fails_diversity():
    verdict = evaluate_trace("T2", _rich_card(Severity.CRITICAL, sources=1),
                             known_sources={"proposal.md"})
    assert not verdict.rule_pass
    assert any(rule.name == "來源多樣性" and not rule.passed for rule in verdict.rules)


def test_unknown_source_fails_integrity():
    verdict = evaluate_trace("T3", _rich_card(), known_sources={"other.md"})
    assert any(rule.name == "引用完整性" and not rule.passed for rule in verdict.rules)


class _BrokenJudge:
    def complete(self, prompt):
        raise RuntimeError("judge offline")


class _FailJudge:
    def complete(self, prompt):
        return "fail"


def test_llm_judge_failure_never_affects_rules():
    card = _rich_card()
    baseline = evaluate_trace("B", card, known_sources={"proposal.md", "budget.csv"})
    broken = evaluate_trace("J1", card, llm=_BrokenJudge(),
                            known_sources={"proposal.md", "budget.csv"})
    failing = evaluate_trace("J2", card, llm=_FailJudge(),
                             known_sources={"proposal.md", "budget.csv"})
    assert baseline.rule_pass == broken.rule_pass == failing.rule_pass
    assert baseline.passed == broken.passed == failing.passed
    assert not broken.judge.available
    assert failing.judge.verdict == "fail"
    assert judge_trace(card).available is False


def test_eval_batch_summary_and_markdown():
    summary = evaluate_batch({"T1": _rich_card(), "T2": _rich_card(Severity.CRITICAL, 1)},
                             known_sources={"proposal.md", "budget.csv"})
    assert summary.total == 2
    assert summary.rule_passed == 1
    assert "來源多樣性" in summary.failed_rules
    assert "規則判據" in eval_markdown(summary)


# ---------- ④ 合規三級分級 ----------

def _program() -> Program:
    return Program.load("configs/program_demo.yaml")


def test_compliance_refuse_degrade_note():
    prog = _program()
    app = Application(app_id="APP-X", program=prog.program_id, title="賭博平台研究",
                      applicant="A", team=["X"], fields={"budget": 900000})
    report = classify(app, prog)
    assert report.refuse and report.needs_human
    levels = {hit.level for hit in report.hits}
    assert ComplianceLevel.REFUSE in levels
    assert ComplianceLevel.DEGRADE in levels  # 預算超上限
    assert all(hit.basis for hit in report.hits)


def test_compliance_coverage_statement_always_present():
    prog = _program()
    clean = Application(app_id="APP-Y", program=prog.program_id, title="教育軟件",
                        applicant="B", team=["Y"], fields={"budget": 100000})
    report = classify(clean, prog)
    assert not report.refuse and not report.needs_human
    md = report.markdown()
    assert COVERAGE_STATEMENT in md
    assert "未命中不代表合規" in md


def test_compliance_conflict_is_degrade_not_refuse():
    prog = _program()
    app = Application(app_id="APP-Z", program=prog.program_id, title="CUHK 合作計劃",
                      applicant="C", team=["Z"], fields={"budget": 100000})
    report = classify(app, prog)
    conflict_hits = [h for h in report.hits if h.rule.startswith("利益衝突")]
    assert conflict_hits
    assert all(h.level == ComplianceLevel.DEGRADE for h in conflict_hits)
    assert not report.refuse
    findings = to_findings(report)
    assert any(f.severity == Severity.WARNING for f in findings)


# ---------- ⑤ 決策日誌 ----------

def test_decision_journal_review_and_accuracy(tmp_path):
    journal = DecisionJournal(tmp_path / "journal.sqlite3")
    today = date(2026, 11, 21)
    old = (today - timedelta(days=10)).isoformat() + "T00:00:00+00:00"
    recent = (today - timedelta(days=2)).isoformat() + "T00:00:00+00:00"
    a1 = journal.record("APP-1", "approved", model="model-a", confidence=0.9,
                        basis=["proposal.md"], created_at=old)
    a2 = journal.record("APP-2", "declined", model="model-a", confidence=0.7, created_at=old)
    a3 = journal.record("APP-3", "approved", model="model-b", confidence=0.8, created_at=recent)

    assert len(journal.due_for_review(7, today=today)) == 2
    with pytest.raises(ValueError):
        journal.record("APP-4", "approved", confidence=1.5)

    journal.review(a1.decision_id, "agree")
    journal.review(a2.decision_id, "disagree")
    with pytest.raises(ValueError):
        journal.review(a3.decision_id, "maybe")

    accuracy = {stat.model: stat for stat in journal.accuracy_by_model()}
    assert accuracy["model-a"].reviewed == 2 and accuracy["model-a"].agreed == 1
    assert accuracy["model-a"].accuracy == 0.5
    assert "model-b" not in accuracy  # 未覆核不進分母
    md = journal.journal_markdown(days=7, today=today)
    assert "model-a" in md and "50%" in md


def test_decision_journal_empty_accuracy(tmp_path):
    journal = DecisionJournal(tmp_path / "empty.sqlite3")
    assert journal.accuracy_by_model() == []
    assert len(journal.list()) == 0
