"""地區字形標準化與審計：確保申請內容在各中文地區「字形正確」。

問題背景
--------
港澳台內地同屬漢字文化圈，但字形標準各異：香港用「軟件/網絡/資訊」，
台灣用「軟體/網路/資訊」，內地用「软件/网络/信息」。同一份申請若字形混用，
輕則觀感不佳，重則被誤讀為不同文件。粗略的「數幾個差異字」判斷不足以支撐
「絕對字形正確」的要求，本模組改以 OpenCC 做逐字符標準化與定位。

設計原則
--------
1. **逐字符可定位**：用 difflib 對齊原文與標準化結果，精確指出每個字形的
   位置、原字、建議字與所屬行號，而非只給「字形不符」的籠統提示。
2. **誤判白名單**：OpenCC s2hk 會把香港標準字誤轉（說明→説明、用戶→用户、
   脫敏→脱敏、離群值→離羣值）。這些詞先遮保護再轉換，避免「改正確為錯誤」。
3. **建議性不強制**：輸出的是修正建議與標準化文本，是否套用由人工決定；
   符合「人機協同、關鍵決策留給人」的項目原則。
4. **確定性與高效**：相同輸入必得相同輸出；OpenCC 轉換器按配置緩存複用，
   14,000 字轉換約 40ms，可安全用於批次申請。
"""
from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from functools import lru_cache

try:
    import opencc
except ImportError:  # 可選依賴：缺失時降級為「不轉換、只回報」
    opencc = None  # type: ignore[assignment]


# 地區 -> (OpenCC 配置, 人類可讀的目標字形標籤)
# HK 用 s2hk（香港標準）；TW 用 s2twp（台灣標準 + 詞組轉換）；
# CN 用 t2s（繁轉簡）；SG 等以英文為主，中文部分按簡體包容處理。
_REGION_CONVERTER: dict[str, tuple[str, str]] = {
    "HK": ("s2hk", "香港繁體"),
    "TW": ("s2twp", "台灣繁體"),
    "CN": ("t2s", "簡體"),
    "SG": ("t2s", "簡體"),
}

# OpenCC s2hk 對以下香港標準字會誤轉；轉換前先以佔位符保護，轉換後還原。
# 每項：(香港標準寫法, 防止被誤轉的中間佔位符)
_HK_PROTECTED = (
    ("說明", "\ue000"),
    ("用戶", "\ue001"),
    ("脫敏", "\ue002"),
    ("離群值", "\ue003"),
    ("資訊", "\ue004"),
)


@lru_cache(maxsize=None)
def _converter(config: str):
    """按配置緩存 OpenCC 實例，避免每次申請重建（批次效能關鍵）。"""
    if opencc is None:
        return None
    return opencc.OpenCC(config)


@dataclass
class ScriptEdit:
    """一處字形修正建議。"""

    line: int          # 行號（由 1 起）
    column: int        # 行內字元位置（由 1 起）
    original: str      # 原文片段
    suggested: str     # 建議字形
    start: int         # 全文字元起始索引（便於程式化套用）
    end: int           # 全文字元結束索引

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScriptReport:
    """字形審計結果。"""

    region: str
    target_label: str
    line_count: int
    mixed_scripts: bool                      # 是否偵測到繁簡混用
    edits: list[ScriptEdit] = field(default_factory=list)
    normalized_text: str = ""
    available: bool = True                   # OpenCC 是否可用

    @property
    def change_count(self) -> int:
        return len(self.edits)

    def summary(self) -> str:
        if not self.available:
            return "字形檢查不可用（缺少 opencc 依賴）"
        if self.change_count == 0:
            return f"{self.region} 字形符合{self.target_label}標準，無需修正"
        head = f"{self.region} 發現 {self.change_count} 處字形建議（目標：{self.target_label}）"
        samples = "、".join(
            f"第{e.line}行「{e.original}→{e.suggested}」" for e in self.edits[:5]
        )
        return f"{head}：{samples}" + ("…" if self.change_count > 5 else "")

    def as_dict(self) -> dict:
        return {
            "region": self.region,
            "target_label": self.target_label,
            "line_count": self.line_count,
            "mixed_scripts": self.mixed_scripts,
            "change_count": self.change_count,
            "edits": [e.as_dict() for e in self.edits],
            "normalized_text": self.normalized_text,
            "available": self.available,
        }


def supported_regions() -> list[str]:
    """回傳可做字形標準化的地區代碼。"""
    return sorted(_REGION_CONVERTER)


def target_label(region: str) -> str:
    """回傳地區的目標字形標籤（如「香港繁體」）；不支援時回傳空字串。"""
    entry = _REGION_CONVERTER.get(region)
    return entry[1] if entry else ""


def _protect(text: str, region: str) -> tuple[str, dict[str, str]]:
    """轉換前保護香港標準字，回傳 (保護後文字, 佔位符->原字映射)。"""
    if region != "HK":
        return text, {}
    restore: dict[str, str] = {}
    out = text
    for word, token in _HK_PROTECTED:
        if word in out:
            out = out.replace(word, token)
            restore[token] = word
    return out, restore


def _restore(text: str, restore: dict[str, str]) -> str:
    for token, word in restore.items():
        text = text.replace(token, word)
    return text


def _merge_diff_groups(opcodes: list[tuple]) -> list[tuple[int, int, int, int]]:
    """合併相鄰差異 opcodes，回傳 [(src_i1, src_i2, dst_j1, dst_j2), ...]。

    兩個差異之間若只隔最多 1 個未變字元（equal），視為同一處——這對應
    詞組級字形變換被 difflib 拆散的情況；其餘分開以保留逐字定位精度。
    """
    diffs = [op for op in opcodes if op[0] != "equal"]
    if not diffs:
        return []
    merged: list[list[int]] = [[diffs[0][1], diffs[0][2], diffs[0][3], diffs[0][4]]]
    for op in diffs[1:]:
        _, i1, i2, j1, j2 = op
        prev = merged[-1]
        gap = i1 - prev[1]  # 與前一組之間未變字元數
        if gap <= 1:
            prev[1], prev[3] = i2, j2
        else:
            merged.append([i1, i2, j1, j2])
    return [(g[0], g[1], g[2], g[3]) for g in merged]


def _segments(text: str) -> list[tuple[int, str]]:
    """以標點、換行與空白為邊界切分文字，回傳 [(起始索引, 段落), ...]。

    分隔符歸入前一段末尾，因此所有段落拼接後等於原文；索引可用於全文定位。
    逐段轉換可避免詞組級字形變換（長度變化）干擾全文 diff 對齊。
    """
    import re
    boundaries = re.compile(r"[\s，。！？；：、,.;:!?()（）「」『』《》\[\]【】]+")
    segments: list[tuple[int, str]] = []
    start = 0
    for match in boundaries.finditer(text):
        if match.start() > start:
            segments.append((start, text[start:match.start()]))
        # 分隔符本身併入前一段（若已有），否則單獨成段
        if segments and segments[-1][0] + len(segments[-1][1]) == start:
            s, chunk = segments[-1]
            segments[-1] = (s, chunk + match.group())
        else:
            segments.append((start, match.group()))
        start = match.end()
    if start < len(text):
        segments.append((start, text[start:]))
    return segments


def _line_of(text: str, index: int) -> tuple[int, int]:
    """回傳 (行號, 行內位置)，皆由 1 起。"""
    before = text[:index]
    line = before.count("\n") + 1
    col = index - (before.rfind("\n") + 1) + 1
    return line, col


def normalize_text(text: str, region: str) -> str:
    """把文字標準化到指定地區字形；不支援的地區或缺少依賴時原樣回傳。

    這是冪等轉換：對已標準化的文字再執行一次，結果不變。
    """
    if not text or region not in _REGION_CONVERTER:
        return text
    config, _ = _REGION_CONVERTER[region]
    converter = _converter(config)
    if converter is None:
        return text
    protected, restore = _protect(text, region)
    return _restore(converter.convert(protected), restore)


def audit_text(text: str, region: str) -> ScriptReport:
    """審計文字字形，回傳逐處修正建議與標準化文本。

    只做分析與建議，不改動傳入的文字；是否套用 ``normalized_text``
    由呼叫端（工作流或人工）決定。
    """
    if region not in _REGION_CONVERTER:
        _, label = ("", "原字形")
        return ScriptReport(region, label, text.count("\n") + 1, False,
                            normalized_text=text, available=False)
    config, label = _REGION_CONVERTER[region]
    converter = _converter(config)
    if converter is None:
        return ScriptReport(region, label, text.count("\n") + 1, False,
                            normalized_text=text, available=False)

    protected, restore = _protect(text, region)
    converted = converter.convert(protected)
    normalized = _restore(converted, restore)

    edits: list[ScriptEdit] = []
    if normalized != text:
        # 定位策略：以標點/換行/空白為邊界逐段獨立轉換後再對齊。
        # 這樣詞組級轉換（台灣 s2twp「用戶」→「使用者」，2字變3字）在段內
        # 長度對應是局部正確的，可合併成一條完整建議，避免 difflib 在全文
        # 長度不一致時切出「件→體」「空白→使」這類錯位片段。
        for seg_start, seg_text in _segments(text):
            seg_protected, seg_restore = _protect(seg_text, region)
            seg_norm = _restore(converter.convert(seg_protected), seg_restore)
            if seg_norm == seg_text or not seg_text.strip():
                continue
            matcher = difflib.SequenceMatcher(None, seg_text, seg_norm, autojunk=False)
            # 合併相鄰差異：若兩個差異之間只隔最多 1 個未變字元，視為同一處
            # （詞組變換如「網絡資訊」被拆成多段時能還原成整詞），其餘分開，
            # 讓純字形轉換（如繁轉簡）保持逐字精確定位。
            groups = _merge_diff_groups(matcher.get_opcodes())
            for i1, i2, j1, j2 in groups:
                original, suggested = seg_text[i1:i2], seg_norm[j1:j2]
                if original == suggested or not original.strip():
                    continue
                abs_start = seg_start + i1
                line, col = _line_of(text, abs_start)
                edits.append(ScriptEdit(line, col, original, suggested, abs_start, seg_start + i2))

    return ScriptReport(
        region=region,
        target_label=label,
        line_count=text.count("\n") + 1,
        mixed_scripts=bool(edits),
        edits=edits,
        normalized_text=normalized,
        available=True,
    )
