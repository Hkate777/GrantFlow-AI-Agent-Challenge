"""端到端測試：用 demo 數據驗證審查發現、批次檢查、評審彙總、追蹤預警。"""
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
    assert "疑似重複申請" in msgs          # app001 與 app003 重複
    assert "顯著偏離" in msgs              # 2,000,000 預算離羣


def test_review_aggregation():
    prog = Program.load(BASE / "configs" / "program_demo.yaml")
    data = json.loads((BASE / "data" / "demo" / "reviews" / "reviews_demo.json").read_text(encoding="utf-8"))
    from kkg.models import ReviewScore
    scores = [ReviewScore(reviewer=d["reviewer"], criteria=d["criteria"])
              for d in data if d["app_id"] == "APP-2027-002"]
    agg = aggregate(scores, prog)
    assert agg["weighted_total"] is not None
    assert any("分歧" in f for f in agg["flags"])   # 兩位評審差異 > 15 → 觸發複議


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
