"""多格式文档摄取：PDF / DOCX / PPTX / XLSX / CSV / 图片(OCR 可选) / 视频(元数据+转写可选)。

设计原则：
- 每种格式一个解析器，统一返回 Document(text=...)，解析失败不中断流水线，记录 error。
- 依赖均为可选：装了就用，没装就降级为提示，保证最小环境可运行 demo。
- 图片/视频走「提取器」接口，默认元数据提取；接入多模态模型后可做 OCR / 视频转写。
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from .models import Document, DocType

log = logging.getLogger("kkg.ingest")

_EXT_MAP = {
    ".md": DocType.MARKDOWN, ".txt": DocType.TEXT,
    ".pdf": DocType.PDF, ".docx": DocType.DOCX, ".pptx": DocType.PPTX,
    ".xlsx": DocType.XLSX, ".xls": DocType.XLSX, ".csv": DocType.CSV,
    ".png": DocType.IMAGE, ".jpg": DocType.IMAGE, ".jpeg": DocType.IMAGE,
    ".webp": DocType.IMAGE, ".gif": DocType.IMAGE,
    ".mp4": DocType.VIDEO, ".mov": DocType.VIDEO, ".webm": DocType.VIDEO,
}


def _read_pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
        r = PdfReader(str(p))
        return "\n".join((pg.extract_text() or "") for pg in r.pages)
    except ImportError:
        return ""


def _read_docx(p: Path) -> str:
    try:
        import docx
        d = docx.Document(str(p))
        return "\n".join(par.text for par in d.paragraphs)
    except ImportError:
        return ""


def _read_pptx(p: Path) -> str:
    try:
        from pptx import Presentation
        prs = Presentation(str(p))
        out = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [s.text for s in slide.shapes if s.has_text_frame]
            out.append(f"[Slide {i}] " + " | ".join(texts))
        return "\n".join(out)
    except ImportError:
        return ""


def _read_xlsx(p: Path) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(p), data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"[Sheet: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                vals = [str(c) if c is not None else "" for c in row]
                if any(vals):
                    out.append(" | ".join(vals))
        return "\n".join(out)
    except ImportError:
        return ""


def _read_csv(p: Path) -> str:
    buf = io.StringIO()
    with p.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        for row in csv.reader(f):
            buf.write(" | ".join(row) + "\n")
    return buf.getvalue()


def _read_image(p: Path) -> tuple[str, dict]:
    meta: dict = {}
    try:
        from PIL import Image
        with Image.open(p) as im:
            meta = {"width": im.width, "height": im.height, "format": im.format}
    except ImportError:
        pass
    # OCR 留接口：接入多模态模型后在此处生成文字描述
    return f"[图片 {p.name}：{meta or '无法读取元数据'}，待多模态模型/OCR 解析]", meta


def _read_video(p: Path) -> tuple[str, dict]:
    size_mb = p.stat().st_size / 1024 / 1024
    # 视频转写留接口：可接 Whisper / 多模态模型
    return f"[视频 {p.name}：{size_mb:.1f}MB，待转写模型解析]", {"size_mb": round(size_mb, 1)}


def ingest_file(path: str | Path) -> Document:
    p = Path(path)
    dt = _EXT_MAP.get(p.suffix.lower(), DocType.OTHER)
    doc = Document(path=str(p), name=p.name, doc_type=dt)
    try:
        if dt in (DocType.TEXT, DocType.MARKDOWN):
            doc.text = p.read_text(encoding="utf-8", errors="replace")
        elif dt is DocType.PDF:
            doc.text = _read_pdf(p)
        elif dt is DocType.DOCX:
            doc.text = _read_docx(p)
        elif dt is DocType.PPTX:
            doc.text = _read_pptx(p)
        elif dt is DocType.XLSX:
            doc.text = _read_xlsx(p)
        elif dt is DocType.CSV:
            doc.text = _read_csv(p)
        elif dt is DocType.IMAGE:
            doc.text, doc.meta = _read_image(p)
        elif dt is DocType.VIDEO:
            doc.text, doc.meta = _read_video(p)
        else:
            doc.text = f"[不支持的格式 {p.suffix}，已跳过内容解析]"
    except Exception as e:  # noqa: BLE001
        log.warning("解析失败 %s: %s", p.name, e)
        doc.error = str(e)
    if not doc.text and not doc.error:
        doc.error = "empty_or_dependency_missing"
    return doc


def ingest_dir(path: str | Path) -> list[Document]:
    root = Path(path)
    files = sorted(f for f in root.rglob("*") if f.is_file() and not f.name.startswith("."))
    return [ingest_file(f) for f in files]
