#!/usr/bin/env python3
"""一鍵答辯演示：跑完整流程，生成決賽演示所需的全套物料。

輸出output/pitch/：
  1. review_packs/        評審資料包（摘要 / 一頁簡介 / 核對清單）
  2. review/              評審結果通知與待辦
  3. tracking/            項目管理報告
  4. review_summary.xlsx評審彙總總表
  5. benchmark_report.md量化基準報告
  6. demo_script.md演示腳本（含建議講解順序與時間分配）

用法：
    python scripts/pitch_demo.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kkg.llm import get_llm                                 # noqa: E402
from kkg.models import Milestone, ReviewScore                # noqa: E402
from kkg.pipeline import Pipeline, load_applications          # noqa: E402
from kkg.program import Program                              # noqa: E402


def main() -> None:
    out = ROOT / "output" / "pitch"
    out.mkdir(parents=True, exist_ok=True)

    # 先跑基準測試與 Excel 匯出（複用現有腳本）
    for cmd in [
        [sys.executable, str(ROOT / "scripts" / "benchmark.py"), "--out", str(out / "benchmark_report.md")],
        [sys.executable, "-m", "kkg.export", "--out", str(out / "review_summary.xlsx")],
    ]:
        env = {"PYTHONPATH": str(ROOT / "src")}
        import os
        r = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **env},
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            sys.exit(1)
        print(r.stdout.strip())

    # 完整流程
    prog = Program.load(ROOT / "configs" / "program_demo.yaml")
    pipe = Pipeline(prog, llm=get_llm("mock"))
    apps = load_applications(ROOT / "data" / "demo" / "applications")
    extracts, findings = {}, {}
    for a in apps:
        extracts[a.app_id], findings[a.app_id] = pipe.intake(a)
    batch_fd = pipe.batch_checks(apps)
    packs = pipe.build_packs(apps, extracts, findings, out / "review_packs")

    scores: dict[str, list[ReviewScore]] = {}
    for p in (ROOT / "data" / "demo" / "reviews").glob("*.json"):
        for item in json.loads(p.read_text(encoding="utf-8")):
            scores.setdefault(item["app_id"], []).append(
                ReviewScore(reviewer=item["reviewer"], criteria=item["criteria"],
                            comment=item.get("comment", "")))

    rev_dir = out / "review"
    rev_dir.mkdir(exist_ok=True)
    for a in apps:
        agg = pipe.review(a, scores.get(a.app_id, []))
        (rev_dir / f"{a.app_id}_result.md").write_text(agg["notice"], encoding="utf-8")
        (rev_dir / f"{a.app_id}_todos.md").write_text("\n".join(agg["todos"]), encoding="utf-8")

    track_dir = out / "tracking"
    track_dir.mkdir(exist_ok=True)
    ms = [Milestone("啓動報告", "2026-12-15", "done", 50000),
          Milestone("中期成果", "2027-02-01", "pending", 80000),
          Milestone("結題驗收", "2027-06-30", "pending", 120000)]
    for a in apps[:1]:
        (track_dir / f"{a.app_id}_management_report.md").write_text(
            pipe.track(a, ms, ["2026-11月度報告：已提交", "2026-12月度報告：未提交 [注意]"]),
            encoding="utf-8")
    (out / "audit_log.json").write_text(pipe.audit_json(), encoding="utf-8")

    script = f"""# 決賽演示腳本（建議8分鐘）

| 環節 | 時間 | 操作 / 講解要點 |
|---|---|---|
| 1. 痛點 | 1 min | 場景 16：異構申請材料、多計劃規則各異、評審後追蹤斷層。 |
| 2. 架構 | 1.5 min | 展示 README 的 Mermaid 流程圖：Ingest 至 Extract 至 Check 至 Verify 至 Summarize 至 Review 至 Track；強調「規則外置 + 人機協同 + 全程審計」。 |
| 3. 審查演示 | 2 min | `python cli.py`，展示 APP-2027-002 預算矛盾（15 萬 vs 9.5 萬）、APP-2027-003 團隊超額 / 預算超限 / 利益衝突 / 重複申請 / 離群值。 |
| 4. 評審資料包 | 1 min | 打開 `review_packs/APP-2027-003/`：標準摘要 + 一頁簡介 + 核對清單。 |
| 5. 評審彙總 | 1 min | 打開 `review_summary.xlsx`：三個工作表（申請總覽 / 發現明細 / 評分明細）；展示 APP-2027-002 分歧 41 分觸發複議。 |
| 6. 量化成效 | 1 min | 展示 `benchmark_report.md`：單份平均 < 1 秒、異常檢出率 100%、100 份推算 1 分鐘內。 |
| 7. 項目追蹤 | 0.5 min | 展示 `tracking/` 管理報告：里程碑逾期與資助發放預警。 |

> 決賽PPT必須英文；本腳本為中文排練指引，英文講稿可據此翻譯。
> 演示提示：MockLLM保證離線可重現；如需展示真模型，設定OPENAI_API_KEY後加 `--llm openai`。

## 本次生成物料
- 評審資料包：{len(packs)}套 至 `review_packs/`
- 評審結果與待辦 至 `review/`
- 管理報告 至 `tracking/`
- 彙總Excel至 `review_summary.xlsx`
- 基準報告 至 `benchmark_report.md`
- 審計日誌 至 `audit_log.json`
"""
    (out / "demo_script.md").write_text(script, encoding="utf-8")
    print(f"答辯物料已全部生成：{out.resolve()}")
    print("演示腳本：" + str(out / "demo_script.md"))


if __name__ == "__main__":
    main()
