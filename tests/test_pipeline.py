"""端到端测试：用 demo 数据验证审查发现、批次检查、评审汇总、追踪预警。"""
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

    # app001：应基本干净（可能有 demo 视频缺失等 info 级提示）
    f1 = res["APP-2027-001"][1]
    assert not any(f.severity.value in ("error", "critical") for f in f1), f1

    # app002：预算前后矛盾（150,000 vs 95,000）
    f2 = res["APP-2027-002"][1]
    assert any(f.finding_type.value == "inconsistent" and "预算" in f.message for f in f2)

    # app003：人数超标 + 预算超上限 + 利益冲突（CUHK）
    f3 = res["APP-2027-003"][1]
    msgs = " | ".join(f.message for f in f3)
    assert "团队人数" in msgs
    assert "超出计划上限" in msgs
    assert "利益冲突" in msgs


def test_batch_checks():
    _, pipe, apps = _setup()
    batch = pipe.batch_checks(apps)
    msgs = " | ".join(f.message for f in batch)
    assert "疑似重复申请" in msgs          # app001 与 app003 重复
    assert "显著偏离" in msgs              # 2,000,000 预算离群


def test_review_aggregation():
    prog = Program.load(BASE / "configs" / "program_demo.yaml")
    data = json.loads((BASE / "data" / "demo" / "reviews" / "reviews_demo.json").read_text(encoding="utf-8"))
    from kkg.models import ReviewScore
    scores = [ReviewScore(reviewer=d["reviewer"], criteria=d["criteria"])
              for d in data if d["app_id"] == "APP-2027-002"]
    agg = aggregate(scores, prog)
    assert agg["weighted_total"] is not None
    assert any("分歧" in f for f in agg["flags"])   # 两位评审差异 > 15 → 触发复议


def test_tracking_alerts():
    from datetime import date
    from kkg.track import check_milestones
    ms = [
        Milestone("启动", "2020-01-01", "pending", 10000),   # 已逾期
        Milestone("中期", "2099-01-01", "pending", 20000),   # 正常
    ]
    alerts = check_milestones(ms, today=date(2026, 6, 1))
    assert any("逾期" in a for a in alerts)
    assert ms[0].status == "overdue"
