#!/usr/bin/env python3
"""語言規範檢查：提示本倉庫文字檔是否符合「繁體中文（香港標準）或英文」。

檢查兩項：
1. 簡體字：以OpenCC s2hk轉換原文，結果不同即提示可能含簡體字。
2. 香港標準術語：提示台灣慣用語、大陸書面語等非香港用字（見HK_TERMS）。

預設為**警告模式**：只列印提示，不退出的非零狀態，不阻塞CI。
使用 --strict才會在有問題時以狀態碼1結束（適合本地提交前自查）。

注意：OpenCC轉換規則偶有誤判（例如把香港標準「說明」轉為「説明」），
因此CI採用警告模式；隊友可自行判斷是否為誤報。

用法：
    python scripts/check_language.py           # 警告模式（CI預設）
    python scripts/check_language.py --strict  # 嚴格模式（本地可選）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import opencc
except ImportError:
    # 缺少依賴時同樣只警告，不阻塞
    print("警告：缺少opencc-python-reimplemented，略過語言檢查。", file=sys.stderr)
    sys.exit(0)

# 禁用詞 -> 香港慣用寫法
HK_TERMS: dict[str, str] = {
    "軟體": "軟件",
    "網路": "網絡",
    "視訊": "視頻",
    "行動電話": "手提電話",
    "智慧財產": "知識產權",
    "人工智慧": "人工智能",
}

# 香港標準寫法但 OpenCC s2hk 會誤轉的詞：比較前先正規化，避免誤報
HK_SPELLING_ALLOWED: dict[str, str] = {
    "說明": "説明",
    "用戶": "用户",
    "離群值": "離羣值",
    "脫敏": "脱敏",
}

ROOT = Path(__file__).resolve().parent.parent
converter = opencc.OpenCC("s2hk")

parser = argparse.ArgumentParser(description="語言規範檢查（預設只警告）")
parser.add_argument("--strict", action="store_true", help="發現問題時以狀態碼1結束")
args = parser.parse_args()

files = subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines()
errors: list[str] = []

for rel in files:
    path = ROOT / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue  # 二進位或無法讀取的檔案略過

    normalized = text
    for hk_form, cc_form in HK_SPELLING_ALLOWED.items():
        normalized = normalized.replace(hk_form, cc_form)
    # 地區配置為多語言資料，豁免簡體檢查
    if rel.startswith("configs/regions/"):
        continue
    # region.py 的字形檢測邏輯必須同時包含簡體與繁體樣本常量，豁免簡體檢查
    if rel == "src/kkg/region.py":
        continue
    if converter.convert(normalized) != normalized:
        errors.append(f"{rel}：可能含簡體字或非香港用字，請人工確認")

    # 政策文件本身會列出禁用詞作示例，豁免術語檢查
    if rel not in {"README.md", "scripts/check_language.py",
                            "configs/regions/CN.yaml", "configs/regions/SG.yaml",
                            "configs/regions/US.yaml", "configs/regions/TW.yaml"}:
        for banned, recommended in HK_TERMS.items():
            if banned in text:
                errors.append(f"{rel}：出現非香港標準術語「{banned}」，建議改用「{recommended}」")

if errors:
    print("[注意] 語言規範提示（不阻塞CI）：", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    print("   如確認為OpenCC誤判（例如「說明」），可忽略本提示。", file=sys.stderr)
    if args.strict:
        sys.exit(1)
else:
    print(f"語言檢查通過：{len(files)}個檔案均為繁體中文（香港標準）或英文 [通過]")
