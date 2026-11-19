#!/usr/bin/env python3
"""語言規範檢查：確保本倉庫所有文字檔案只使用繁體中文或英文。

做法：以 OpenCC s2hk（香港繁體）轉換每個受版本控制的文字檔，
若轉換結果與原文不同，代表檔案含簡體字 —— 回報並以非零狀態結束。

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

ROOT = Path(__file__).resolve().parent.parent
converter = opencc.OpenCC("s2hk")

files = subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines()
offenders: list[str] = []

for rel in files:
    path = ROOT / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue  # 二進位或無法讀取的檔案略過
    converted = converter.convert(text)
    if converted != text:
        offenders.append(rel)

if offenders:
    print("以下檔案含簡體字，請改用繁體中文或英文：", file=sys.stderr)
    for name in offenders:
        print(f"  - {name}", file=sys.stderr)
    sys.exit(1)

print(f"語言檢查通過：{len(files)} 個檔案均為繁體中文或英文 ✅")
