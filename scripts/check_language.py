#!/usr/bin/env python3
"""展示語言一致性輔助工具（可選）：提示本倉庫文字檔的用字風格。

本項目展示語言為香港繁體或英文皆可、以嚴謹清晰為準，不強制一律繁體。
本腳本只作「一致性參考」，不是合規閘門，也不約束 Agent 的處理能力
（Agent 的字形標準化由 src/kkg/script.py 負責，要求是絕對正確）。

檢查兩項：
1. 簡體字：以OpenCC s2hk轉換原文，結果不同即提示可能含簡體字。
2. 非香港術語：提示台灣地區慣用語、內地書面語（見HK_TERMS）。

預設為**警告模式**：只列印提示，不返回非零狀態，不阻塞CI。
使用 --strict才會在有問題時以狀態碼1結束（適合本地自查）。

注意：OpenCC轉換規則偶有誤判（例如把香港標準「說明」轉為「説明」），
因此提示僅供人工參考；英文技術文件與地區配置無須強行轉繁。

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
    "客戶端": "客户端",
    "離群值": "離羣值",
    "脫敏": "脱敏",
    "守衛": "守衞",     # 香港用「衛」，OpenCC s2hk 誤轉為「衞」
}

ROOT = Path(__file__).resolve().parent.parent
converter = opencc.OpenCC("s2hk")

parser = argparse.ArgumentParser(description="展示語言一致性輔助檢查（可選，預設只警告）")
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
    # semantic_guard.py 的注入語料必須含簡體樣本，才能偵測簡體輸入的注入攻擊，豁免簡體檢查
    if rel == "src/kkg/semantic_guard.py":
        continue
    # blind.py 的身份詞表必須含簡體機構後綴樣本，才能遮蔽簡體寫法的機構簡稱，豁免簡體檢查
    if rel == "src/kkg/blind.py":
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
    print(f"一致性提示檢查完成：{len(files)}個檔案未發現簡體或非香港用字 [通過]")
