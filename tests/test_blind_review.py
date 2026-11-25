"""真盲評回歸：評審在簽發前看不到申請人身份與他人評分聚合。

衝突判定由服務端規則自動比對（check._conflict_findings），不依賴評委
「看得見申請人是誰」才會申報；投影只影響展示，不影響規則與審計。
"""
import json

from kkg.blind import blind_projection, blind_traceability
from kkg.auth import Identity


def _detail():
    return {
        "id": "case-1", "app_id": "APP-2027-001", "applicant": "啓明科技有限公司",
        "email": "contact@qiming-tech.hk", "team": ["陳曉峯", "李嘉穎"],
        "title": "面向中小企的 AI 合規文書助手（啓明科技有限公司）",
        "fields": {"contact": "陳曉峯", "email": "contact@qiming-tech.hk",
                   "budget": 280000, "duration_m": 12},
        "extracted": {"objectives": "與啓明科技有限公司合作，聯絡 contact@qiming-tech.hk",
                      "_evidence": {"objectives": [{"quote": "啓明科技有限公司 提供服務"}]}},
        "documents": [{"name": "qiming_proposal.pdf", "doc_type": "pdf", "error": None}],
        "findings": [{"finding_type": "conflict", "message": "啓明科技有限公司 與評審有關"}],
        "scores": [
            {"reviewer_id": "rev_a", "reviewer": "評委甲", "comment": "甲意見", "criteria": {}},
            {"reviewer_id": "rev_b", "reviewer": "評委乙", "comment": "乙意見", "criteria": {}},
        ],
        "assessment": {"status": "待評分", "holds": [], "todos": [],
                       "weighted_total": 88, "flags": ["分歧"]},
        "decision": None,
    }


def _reviewer():
    return Identity("rev_a", "評委甲", "reviewer")


def test_blind_hides_applicant_email_team_and_contact():
    out = blind_projection(_detail(), _reviewer())
    assert out["applicant"].startswith("Applicant ")
    assert "啓明科技有限公司" not in out["applicant"]
    assert out["email"] == "[電郵已遮蔽]"
    assert out["team"] == ["[團隊成員已遮蔽]"]
    assert out["fields"]["contact"] == out["applicant"]  # 代號一致
    assert out["fields"]["budget"] == 280000             # 評分依據保留


def test_blind_scrubs_identity_terms_in_free_text():
    out = blind_projection(_detail(), _reviewer())
    assert "啓明科技有限公司" not in out["title"]
    assert "啓明科技有限公司" not in out["extracted"]["objectives"]
    assert "contact@qiming-tech.hk" not in out["extracted"]["objectives"]
    assert "啓明科技有限公司" not in out["extracted"]["_evidence"]["objectives"][0]["quote"]
    assert "啓明科技有限公司" not in out["findings"][0]["message"]


def test_blind_hides_other_scores_and_aggregate():
    out = blind_projection(_detail(), _reviewer())
    assert len(out["scores"]) == 1
    assert out["scores"][0]["reviewer_id"] == "rev_a"
    assert "weighted_total" not in out["assessment"]
    assert "flags" not in out["assessment"]
    assert out["assessment"]["status"] == "待評分"


def test_blind_alias_stable_across_calls():
    d = _detail()
    assert blind_projection(d, _reviewer())["applicant"] == blind_projection(d, _reviewer())["applicant"]


def test_blind_lifted_after_decision():
    d = _detail()
    d["decision"] = {"outcome": "approved"}
    out = blind_projection(d, _reviewer())
    assert out["applicant"] == "啓明科技有限公司"
    assert len(out["scores"]) == 2
    assert "weighted_total" in out["assessment"]


def test_non_reviewer_unaffected():
    out = blind_projection(_detail(), Identity("op", "經辦", "operator"))
    assert out["applicant"] == "啓明科技有限公司"
    assert len(out["scores"]) == 2


def test_traceability_withheld_for_reviewer_before_signoff():
    result = {"cards": [], "deterministic_score": {"total": 88}, "signed": False}
    out = blind_traceability(result, _reviewer())
    assert out["deterministic_score"]["withheld"] is True
    assert "total" not in out["deterministic_score"]
    assert result["deterministic_score"]["total"] == 88  # 不變更原物件


def test_traceability_visible_after_signoff_and_for_operator():
    signed = {"deterministic_score": {"total": 88}, "signed": True}
    assert blind_traceability(signed, _reviewer())["deterministic_score"]["total"] == 88
    unsigned = {"deterministic_score": {"total": 88}, "signed": False}
    assert blind_traceability(unsigned, Identity("op", "經辦", "operator"))["deterministic_score"]["total"] == 88
