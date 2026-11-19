#!/usr/bin/env python3
"""GrantFlow CLI：一条命令跑通场景 16 全流程。

用法：
  python cli.py --program configs/program_demo.yaml --apps data/demo/applications \
      --reviews data/demo/reviews --out output [--llm mock|openai]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from kkg.llm import get_llm                       # noqa: E402
from kkg.models import Milestone, ReviewScore      # noqa: E402
from kkg.pipeline import Pipeline, load_applications  # noqa: E402
from kkg.program import Program                    # noqa: E402


def _load_reviews(root: str | Path) -> dict[str, list[ReviewScore]]:
    out: dict[str, list[ReviewScore]] = {}
    root = Path(root)
    if not root.exists():
        return out
    for f in sorted(root.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for item in data:
            out.setdefault(item["app_id"], []).append(
                ReviewScore(reviewer=item["reviewer"],
                            criteria=item["criteria"],
                            comment=item.get("comment", "")))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="GrantFlow - 申请评审与项目管理流程自动化 Agent")
    ap.add_argument("--program", default="configs/program_demo.yaml")
    ap.add_argument("--apps", default="data/demo/applications")
    ap.add_argument("--reviews", default="data/demo/reviews")
    ap.add_argument("--out", default="output")
    ap.add_argument("--llm", default="mock", choices=["mock", "openai", "auto"])
    ap.add_argument("--pass-threshold", type=float, default=60.0)
    args = ap.parse_args()

    prog = Program.load(args.program)
    llm = get_llm(args.llm)
    pipe = Pipeline(prog, llm=llm, pass_threshold=args.pass_threshold)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] 加载计划配置：{prog.name}  |  LLM：{llm.name}")
    apps = load_applications(args.apps)
    print(f"[2/6] 受理申请 {len(apps)} 份，解析附件 …")

    extracts, findings = {}, {}
    for a in apps:
        ex, fd = pipe.intake(a)
        extracts[a.app_id] = ex
        findings[a.app_id] = fd
        n_err = sum(1 for f in fd if f.severity.value in ("error", "critical"))
        print(f"      {a.app_id} «{a.title}»：发现 {len(fd)} 项（严重 {n_err}）")

    batch_fd = pipe.batch_checks(apps)
    if batch_fd:
        print("[3/6] 批次检查：")
        for f in batch_fd:
            print(f"      ⚠️ {f.message}")
    else:
        print("[3/6] 批次检查：无重复/离群申请")

    packs = pipe.build_packs(apps, extracts, findings, out / "reviewer_packs")
    print(f"[4/6] 已生成评审资料包 {len(packs)} 套 → {out / 'reviewer_packs'}")

    reviews = _load_reviews(args.reviews)
    review_dir = out / "review"
    review_dir.mkdir(exist_ok=True)
    print("[5/6] 评审汇总：")
    for a in apps:
        agg = pipe.review(a, reviews.get(a.app_id, []))
        (review_dir / f"{a.app_id}_result.md").write_text(agg["notice"], encoding="utf-8")
        (review_dir / f"{a.app_id}_todos.md").write_text("\n".join(agg["todos"]), encoding="utf-8")
        if agg.get("weighted_total") is not None:
            flags = f"  ⚠️ {'; '.join(agg['flags'])}" if agg["flags"] else ""
            print(f"      {a.app_id} 加权总分 {agg['weighted_total']}（{agg['n_reviewers']} 人）{flags}")
        else:
            print(f"      {a.app_id} 暂无评分")

    track_dir = out / "tracking"
    track_dir.mkdir(exist_ok=True)
    demo_ms = [
        Milestone("启动报告", "2026-12-15", "done", 50000),
        Milestone("中期成果", "2027-02-01", "pending", 80000),
        Milestone("结题验收", "2027-06-30", "pending", 120000),
    ]
    print("[6/6] 项目追踪与管理报告：")
    for a in apps[:1]:  # demo：对首份申请生成管理报告
        report = pipe.track(a, [Milestone(**vars(m)) for m in demo_ms],
                            periodic_reports=["2026-11 月度报告：已提交", "2026-12 月度报告：未提交 ⚠️"])
        (track_dir / f"{a.app_id}_management_report.md").write_text(report, encoding="utf-8")
        print(f"      已生成 {a.app_id} 管理报告")

    (out / "audit_log.json").write_text(pipe.audit_json(), encoding="utf-8")
    print(f"\n✅ 全流程完成。输出目录：{out.resolve()}")
    print(f"   审计日志：{out / 'audit_log.json'}")


if __name__ == "__main__":
    main()
