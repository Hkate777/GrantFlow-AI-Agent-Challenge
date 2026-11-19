#!/usr/bin/env python3
"""GrantFlow CLI：一條命令跑通場景16全流程。

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
from kkg.region import Region                      # noqa: E402


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
    ap = argparse.ArgumentParser(description="GrantFlow - 申請評審與項目管理流程自動化Agent")
    ap.add_argument("--program", default="configs/program_demo.yaml")
    ap.add_argument("--apps", default="data/demo/applications")
    ap.add_argument("--reviews", default="data/demo/reviews")
    ap.add_argument("--out", default="output")
    ap.add_argument("--llm", default="mock", choices=["mock", "openai", "auto"])
    ap.add_argument("--pass-threshold", type=float, default=60.0)
    ap.add_argument("--region", default="HK", help="申請地區：HK / TW / CN / SG / US")
    args = ap.parse_args()

    prog = Program.load(args.program)
    llm = get_llm(args.llm)
    regions = Region.load_all(Path(__file__).parent / "configs" / "regions")
    region = regions.get(args.region.upper())
    if region is None:
        raise SystemExit(f"未知地區：{args.region}，可選：{'、'.join(regions)}")
    pipe = Pipeline(prog, llm=llm, pass_threshold=args.pass_threshold, region=region)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] 加載計劃配置：{prog.name}  |  地區：{region.name}  |  LLM：{llm.name}")
    apps = load_applications(args.apps)
    print(f"[2/6] 受理申請{len(apps)}份，解析附件 …")

    extracts, findings = {}, {}
    for a in apps:
        ex, fd = pipe.intake(a)
        extracts[a.app_id] = ex
        findings[a.app_id] = fd
        n_err = sum(1 for f in fd if f.severity.value in ("error", "critical"))
        print(f"      {a.app_id} «{a.title}»：發現{len(fd)}項（嚴重{n_err}）")

    batch_fd = pipe.batch_checks(apps)
    if batch_fd:
        print("[3/6] 批次檢查：")
        for f in batch_fd:
            print(f"      [注意] {f.message}")
    else:
        print("[3/6] 批次檢查：無重複/離羣申請")

    packs = pipe.build_packs(apps, extracts, findings, out / "reviewer_packs")
    print(f"[4/6] 已生成評審資料包{len(packs)}套 至{out / 'reviewer_packs'}")

    reviews = _load_reviews(args.reviews)
    review_dir = out / "review"
    review_dir.mkdir(exist_ok=True)
    print("[5/6] 評審彙總：")
    for a in apps:
        agg = pipe.review(a, reviews.get(a.app_id, []))
        (review_dir / f"{a.app_id}_result.md").write_text(agg["notice"], encoding="utf-8")
        (review_dir / f"{a.app_id}_todos.md").write_text("\n".join(agg["todos"]), encoding="utf-8")
        if agg.get("weighted_total") is not None:
            flags = f"  [注意] {'; '.join(agg['flags'])}" if agg["flags"] else ""
            print(f"      {a.app_id}加權總分{agg['weighted_total']}（{agg['n_reviewers']}人）{flags}")
        else:
            print(f"      {a.app_id}暫無評分")

    track_dir = out / "tracking"
    track_dir.mkdir(exist_ok=True)
    demo_ms = [
        Milestone("啓動報告", "2026-12-15", "done", 50000),
        Milestone("中期成果", "2027-02-01", "pending", 80000),
        Milestone("結題驗收", "2027-06-30", "pending", 120000),
    ]
    print("[6/6] 項目追蹤與管理報告：")
    for a in apps[:1]:  # demo：對首份申請生成管理報告
        report = pipe.track(a, [Milestone(**vars(m)) for m in demo_ms],
                            periodic_reports=["2026-11月度報告：已提交", "2026-12月度報告：未提交 [注意]"])
        (track_dir / f"{a.app_id}_management_report.md").write_text(report, encoding="utf-8")
        print(f"      已生成{a.app_id}管理報告")

    (out / "audit_log.json").write_text(pipe.audit_json(), encoding="utf-8")
    print(f"\n[通過] 全流程完成。輸出目錄：{out.resolve()}")
    print(f"   審計日誌：{out / 'audit_log.json'}")


if __name__ == "__main__":
    main()
