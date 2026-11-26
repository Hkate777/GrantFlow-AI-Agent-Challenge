"""語義注入分類器留出集評估 CLI（骨架；真實標註資料仍待機構提供）。

用法：
    python scripts/eval_semantic.py                 # 輸出評估報告（JSON）
    python scripts/eval_semantic.py --sweep         # 附門檻掃描（召回／誤報取捨）

報告中的 caveat 如實說明數字基於演示級種子語料，不得當真實性能引用。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from kkg.semantic_eval import holdout_eval, threshold_sweep  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="語義注入分類器留出集評估")
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sweep", action="store_true", help="附帶門檻掃描")
    args = ap.parse_args()

    report = holdout_eval(threshold=args.threshold, seed=args.seed)
    if args.sweep:
        report["threshold_sweep"] = threshold_sweep(seed=args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
