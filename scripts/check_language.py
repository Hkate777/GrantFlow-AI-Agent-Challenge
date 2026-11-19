#!/usr/bin/env python3
"""語言規範檢查：確保本倉庫所有文字檔案只使用繁體中文（香港標準）或英文。

兩項檢查：
1. 簡體字：以 OpenCC s2hk（香港繁體）轉換原文，結果不同即代表含簡體字。
2. 香港標準術語：禁止台灣慣用語、大陸書面語等非香港用字（見 HK_TERMS）。

用法：
    python scripts/check_language.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import opencc
except ImportError:
    sys.exit("缺少 opencc-python-reimplemented，請先 pip install -r requirements.txt")

# 禁用詞 -> 香港慣用寫法（禁用詞本身必須是繁體，否則由 s2hk 檢查攔截）
HK_TERMS: dict[str, str] = {
    "軟體": "軟件",
    "網路": "網絡",
    "視訊": "視頻",
    "行動電話": "手提電話",
    "智慧財產": "知識產權",
    "人工智慧": "人工智能",
}

ROOT = Path(__file__).resolve().parent.parent
converter = opencc.OpenCC("s2hk")

files = subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines()
errors: list[str] = []

for rel in files:
    path = ROOT / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue  # 二進位或無法讀取的檔案略過

    if converter.convert(text) != text:
        errors.append(f"{rel}：含簡體字，請改用繁體中文（香港標準）")

    # 政策文件本身會列出禁用詞作示例，豁免術語檢查（簡體字檢查仍適用）
    if rel not in {"README.md", "scripts/check_language.py"}:
        for banned, recommended in HK_TERMS.items():
            if banned in text:
                errors.append(f"{rel}：出現非香港標準術語「{banned}」，建議改用「{recommended}」")

if errors:
    print("語言規範檢查失敗：", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)

print(f"語言檢查通過：{len(files)} 個檔案均為繁體中文（香港標準）或英文 ✅")
