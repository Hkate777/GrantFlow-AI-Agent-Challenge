"""端到端測試：用demo數據驗證審查發現、批次檢查、評審彙總、追蹤預警。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kkg.llm import MockLLM                              # noqa: E402
from kkg.models import Milestone                          # noqa: E402
from kkg.pipeline import Pipeline, load_applications      # noqa: E402
from kkg.program import Program                           # noqa: E402
from kkg.review import aggregate                          # noqa: E402

BASE = Path(__file__).parent.parent


def _setup():
    prog = Program.load(BASE / "configs" / "program_demo.yaml")
    pipe = Pipeline(prog, llm=MockLLM())
    apps = load_applications(BASE / "data" / "demo" / "applications")
    return prog, pipe, apps


def test_intake_findings():
    _, pipe, apps = _setup()
    res = {a.app_id: pipe.intake(a) for a in apps}

    # app001：應基本乾淨（可能有 demo 視頻缺失等 info 級提示）
    f1 = res["APP-2027-001"][1]
    assert not any(f.severity.value in ("error", "critical") for f in f1), f1

    # app002：預算前後矛盾（150,000 vs 95,000）
    f2 = res["APP-2027-002"][1]
    assert any(f.finding_type.value == "inconsistent" and "預算" in f.message for f in f2)

    # app003：人數超標 + 預算超上限 + 利益衝突（CUHK）
    f3 = res["APP-2027-003"][1]
    msgs = " | ".join(f.message for f in f3)
    assert "團隊人數" in msgs
    assert "超出計劃上限" in msgs
    assert "利益衝突" in msgs


def test_batch_checks():
    _, pipe, apps = _setup()
    batch = pipe.batch_checks(apps)
    msgs = " | ".join(f.message for f in batch)
    assert "疑似重複申請" in msgs          # app001與app003重複
    assert "顯著偏離" in msgs              # 2,000,000預算離羣


def test_review_aggregation():
    prog = Program.load(BASE / "configs" / "program_demo.yaml")
    data = json.loads((BASE / "data" / "demo" / "reviews" / "reviews_demo.json").read_text(encoding="utf-8"))
    from kkg.models import ReviewScore
    scores = [ReviewScore(reviewer=d["reviewer"], criteria=d["criteria"])
              for d in data if d["app_id"] == "APP-2027-002"]
    agg = aggregate(scores, prog)
    assert agg["weighted_total"] is not None
    assert any("分歧" in f for f in agg["flags"])   # 兩位評審差異 > 15至 觸發複議


def test_tracking_alerts():
    from datetime import date
    from kkg.track import check_milestones
    ms = [
        Milestone("啓動", "2020-01-01", "pending", 10000),   # 已逾期
        Milestone("中期", "2099-01-01", "pending", 20000),   # 正常
    ]
    alerts = check_milestones(ms, today=date(2026, 6, 1))
    assert any("逾期" in a for a in alerts)
    assert ms[0].status == "overdue"


def test_region_awareness():
    from kkg.check import check_region
    from kkg.region import Region
    prog, _, apps = _setup()
    app = apps[0]

    hk = Region.load(BASE / 'configs' / 'regions' / 'HK.yaml')
    hk_findings = check_region(app, hk)
    assert any('個人資料' in f.message for f in hk_findings)   # 香港法規關鍵詞

    cn = Region.load(BASE / 'configs' / 'regions' / 'CN.yaml')
    cn_findings = check_region(app, cn)
    assert any('繁體' in f.message or '簡體' in f.message for f in cn_findings)  # 字形不一致提示

    us = Region.load(BASE / 'configs' / 'regions' / 'US.yaml')
    assert isinstance(check_region(app, us), list)  # 英文地區亦可執行


def test_pipeline_region_in_extracted():
    from kkg.region import Region
    prog = Program.load(BASE / 'configs' / 'program_demo.yaml')
    hk = Region.load(BASE / 'configs' / 'regions' / 'HK.yaml')
    pipe = Pipeline(prog, llm=MockLLM(), region=hk)
    app = load_applications(BASE / 'data' / 'demo' / 'applications')[0]
    extracted, findings = pipe.intake(app)
    assert extracted['_region'] == '香港'
    assert any(f.source == 'region.legal' for f in findings)


def test_all_region_configs_load():
    from kkg.region import Region
    regions = Region.load_all(BASE / 'configs' / 'regions')
    expected = {'HK', 'TW', 'CN', 'SG', 'US', 'GB', 'AU', 'AE', 'SA', 'MY', 'TH', 'CA', 'EG'}
    assert set(regions) == expected
    for code, r in regions.items():
        assert r.code == code
        assert r.currency and r.legal_keywords, code


def test_new_regions_legal_keywords():
    from kkg.check import check_region
    from kkg.region import Region
    _, _, apps = _setup()
    app = apps[0]
    # 英文地區：示範申請未含當地法規關鍵詞，應觸發合規提示
    for code, kw in [('GB', 'UK GDPR'), ('AU', 'Privacy Act'),
                     ('AE', 'PDPL'), ('CA', 'PIPEDA')]:
        r = Region.load(BASE / 'configs' / 'regions' / f'{code}.yaml')
        findings = check_region(app, r)
        assert any(kw in f.message for f in findings), (code, kw)


def test_privacy_redaction_before_llm():
    """送LLM前必須已去識別化：原始電郵/電話/身份證不出現在LLM收到的文本中。"""
    from kkg.privacy import Redactor, scan_sensitive
    raw = ("聯絡電郵 owner@example.com，電話 91234567，"
           "身份證 A1234567(3)，合約 CT-2026-00123，預算500000")
    r = Redactor()
    safe = r.redact(raw)
    assert "owner@example.com" not in safe
    assert "91234567" not in safe
    assert "A1234567(3)" not in safe
    assert "CT-2026-00123" not in safe
    assert "[EMAIL_" in safe and "[PHONE_" in safe and "[CONTRACT_" in safe
    # 原始值可在本機還原
    assert r.restore(safe) == raw
    # 報告只記類型與次數，不含原始值
    assert r.report.mask_count >= 4
    assert "owner@example.com" not in r.report.summary()
    assert scan_sensitive(raw).get("email") == 1


def test_pipeline_sends_redacted_text_to_llm():
    """間諜LLM捕獲實際收到的文本，驗證敏感值未外洩。"""
    from kkg.privacy import Redactor
    from kkg.models import Application, Document, DocType

    class SpyLLM:
        name = "spy"
        def __init__(self): self.seen = ""
        def complete(self, system, user):
            self.seen += user
            return "{}"

    spy = SpyLLM()
    app = Application(
        app_id="APP-P", program="P", title="測試",
        applicant="張三", email="zhangsan@example.com",
        fields={"budget": "100000", "contact": "91112222"},
        docs=[Document(path="a.md", name="a.md", doc_type=DocType.MARKDOWN,
                       text="申請人電郵 zhangsan@example.com，身份證 A1234567(3)，電話91112222")],
    )
    prog = Program.load(BASE / 'configs' / 'program_demo.yaml')
    pipe = Pipeline(prog, llm=spy, privacy=True)
    pipe.intake(app)
    assert "zhangsan@example.com" not in spy.seen
    assert "91112222" not in spy.seen
    assert "A1234567(3)" not in spy.seen
    # 審計日誌亦不得含原始敏感值
    assert "zhangsan@example.com" not in pipe.audit_json()
