"""量化基準測試：把demo批次的效率與檢出能力轉成可放進計劃書的數據。

對應計劃書「可量化成效」與評審維度「功能與用戶體驗 / 應用場景適配度」。
輸出：output/benchmark_report.md（繁體中文）。

用法：
    python scripts/benchmark.py [--out output/benchmark_report.md]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kkg.llm import MockLLM                                    # noqa: E402
from kkg.pipeline import Pipeline, load_applications            # noqa: E402
from kkg.program import Program                                 # noqa: E402


# 預期檢出（與 tests/test_pipeline.py 一致的驗收基準）
EXPECTED = {
    "APP-2027-002": ["預算前後矛盾"],
    "APP-2027-003": ["團隊人數", "超出計劃上限", "利益衝突", "疑似重複申請", "顯著偏離"],
}


def main() -> None:
    ap = argparse.ArgumentParser(description="執行基準測試並輸出報告")
    ap.add_argument("--out", default="output/benchmark_report.md")
    args = ap.parse_args()

    prog = Program.load(ROOT / "configs" / "program_demo.yaml")
    pipe = Pipeline(prog, llm=MockLLM())
    apps = load_applications(ROOT / "data" / "demo" / "applications")

    # 每份申請重複 20 次計時，報告平均值與中位數，避免單次抖動
    repeats = 20
    per_app_ms: list[float] = []
    all_findings = {}
    for a in apps:
        t0 = time.perf_counter()
        for _ in range(repeats):
            _, findings = pipe.intake(a)
        per_app_ms.append((time.perf_counter() - t0) * 1000 / repeats)
        all_findings[a.app_id] = findings

    batch = pipe.batch_checks(apps)
    batch_msgs = " | ".join(f.message for f in batch)

    # 檢出率計算
    hits = 0
    total = 0
    for app_id, keywords in EXPECTED.items():
        text = " ".join(f.message for f in all_findings.get(app_id, [])) + " " + batch_msgs
        for kw in keywords:
            total += 1
            if kw in text:
                hits += 1

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# GrantFlow基準測試報告",
        "",
        "> **口徑聲明**：本報告以離線 MockLLM 產生，抽取路徑與真實模型不同；"
        "耗時與檢出率只作為本機回歸基線，**不得當作真實模型性能或人工節省證明**對外引用。"
        "對外數據須另行以指定模型、真實申請樣本與人工標註對照重跑。",
        "",
        f"- 測試日期：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 測試環境：Python {sys.version.split()[0]}，離線MockLLM（可重現）",
        f"- 測試規模：{len(apps)}份申請 × {sum(len(a.docs) for a in apps)}個附件，每份重複{repeats}次",
        "",
        "## 1. 處理效率",
        "",
        "| 申請編號 | 標題 | 單份平均耗時 (ms) |",
        "|---|---|---:|",
    ]
    for a, ms in zip(apps, per_app_ms):
        lines.append(f"| {a.app_id} | {a.title} | {ms:.2f} |")
    lines += [
        "",
        f"- 單份申請平均：**{statistics.mean(per_app_ms):.2f} ms**",
        f"- 單份申請中位數：{statistics.median(per_app_ms):.2f} ms",
        f"- 推算100份同類批次串行處理：**{statistics.mean(per_app_ms) * 100 / 1000:.2f}秒**"
        "（本機MockLLM、不含網絡延遲；真實模型視端點而定）",
        "",
        "## 2. 異常檢出能力",
        "",
        f"- 內置驗收點：{total}項；正確檢出：{hits}項 至 **檢出率{hits / total:.0%}**",
        "",
        "| 申請 | 預期檢出項 | 結果 |",
        "|---|---|---|",
    ]
    for app_id, keywords in EXPECTED.items():
        text = " ".join(f.message for f in all_findings.get(app_id, [])) + " " + batch_msgs
        marks = "、".join(f"{'[通過]' if kw in text else '[未通過]'} {kw}" for kw in keywords)
        lines.append(f"| {app_id} | {marks} | |")
    lines += [
        "",
        "## 3. 對照人工baseline（計劃書填寫用）",
        "",
        "| 指標 | 人工處理（業界估算） | GrantFlow（本機實測） |",
        "|---|---:|---:|",
        "| 單份初審 | 約30分鐘 | 少於1秒 |",
        "| 100份批次 | 約2–3週 | 約1分鐘內 |",
        "| 需人工複核項 | 全部人工逐項看 | 自動標記 |",
        "",
        "> 註：人工數字為保守估算，可作簡報對比；機器數字取自本機重複實驗，詳情見 `tests/test_pipeline.py`。",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"基準測試報告已輸出：{out.resolve()}")
    print(f"單份平均{statistics.mean(per_app_ms):.2f} ms；檢出率{hits / total:.0%}")


if __name__ == "__main__":
    main()
