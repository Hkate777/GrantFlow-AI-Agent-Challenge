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


def test_blind_scrubs_org_abbreviations_in_project_desc():
    """自由文本只寫簡稱時亦須遮蔽，評分依據同時保留。"""
    d = _detail()
    d["fields"]["project_desc"] = "本項目由啓明科技主導，啓明科技負責合規文書，合作方為啓明。"
    out = blind_projection(d, _reviewer())
    desc = out["fields"]["project_desc"]
    assert "啓明科技" not in desc
    assert "啓明" not in desc
    assert desc.count("Applicant ") == 3
    assert out["fields"]["budget"] == 280000
    assert out["fields"]["duration_m"] == 12


def test_blind_scrubs_cross_script_variants():
    """身份欄位為繁體、自由文本用簡體或台灣異體時仍須遮蔽。"""
    d = _detail()
    simplified = "\u542f\u660e"          # 內地簡體寫法（以轉義構造，避免原始碼出現簡體字）
    taiwan = "\u555f\u660e"             # 啟明：台灣寫法
    d["fields"]["project_desc"] = (
        f"{simplified}科技有限公司與{simplified}科技合作，另見{taiwan}方案。"
    )
    out = blind_projection(d, _reviewer())
    desc = out["fields"]["project_desc"]
    assert simplified not in desc
    assert taiwan not in desc
    assert f"{simplified}科技" not in desc
    assert desc.count("Applicant ") == 3


def test_blind_does_not_scrub_common_words():
    """後綴本身不是身份詞，不得把「科技」「資訊」這類通用詞誤遮。"""
    d = _detail()
    d["fields"]["project_desc"] = "本項目運用科技手段，強調資訊透明。"
    out = blind_projection(d, _reviewer())
    assert out["fields"]["project_desc"] == "本項目運用科技手段，強調資訊透明。"


def test_blind_stops_peeling_at_generic_core():
    """機構核心降解為通用後綴詞時停止，避免誤遮正文。"""
    d = _detail()
    d["applicant"] = "科技發展有限公司"
    d["fields"]["project_desc"] = "本項目以科技手段提升資訊透明度。"
    out = blind_projection(d, _reviewer())
    assert out["fields"]["project_desc"] == "本項目以科技手段提升資訊透明度。"
