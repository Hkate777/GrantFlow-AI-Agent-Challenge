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

    # app002：預算前後矛盾（150,000 vs 95,000）——核對穩定值：類型 + 來源，不綁字串簡繁
    f2 = res["APP-2027-002"][1]
    hits = [f for f in f2 if f.finding_type.value == "inconsistent"
            and str(getattr(f, "source", "")).startswith("cross_verify.budget")]
    assert hits, f2

    # app003：人數超標 + 預算超上限 + 利益衝突（CUHK）——核對類型 + source 穩定值
    f3 = res["APP-2027-003"][1]
    sources = {(f.finding_type.value, str(getattr(f, "source", ""))) for f in f3}
    assert ("eligibility", "eligibility") in sources      # 團隊人數超出範圍
    assert ("eligibility", "budget_cap") in sources       # 預算超出計劃上限
    assert any(ftype == "conflict" for ftype, _ in sources)  # 評審員利益衝突


def test_batch_checks():
    _, pipe, apps = _setup()
    batch = pipe.batch_checks(apps)
    # 核對穩定值：類型 + application_ids，不綁字串簡繁
    dup = [f for f in batch if f.finding_type.value == "duplicate"]
    assert any(set(getattr(f, "application_ids", [])) == {"APP-2027-001", "APP-2027-003"}
               for f in dup), batch
    anomaly = [f for f in batch if f.finding_type.value == "anomaly"]
    assert any("APP-2027-003" in getattr(f, "application_ids", []) for f in anomaly), batch


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
