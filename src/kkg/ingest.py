"""解析附件，保留原檔名及可供核對的原文位置。"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from html import unescape
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from .models import DocType, Document, EvidenceSpan

log = logging.getLogger("kkg.ingest")

_EXT_MAP = {
    ".md": DocType.MARKDOWN, ".txt": DocType.TEXT,
    ".pdf": DocType.PDF, ".docx": DocType.DOCX, ".pptx": DocType.PPTX,
    ".xlsx": DocType.XLSX, ".xls": DocType.XLSX, ".csv": DocType.CSV,
    ".png": DocType.IMAGE, ".jpg": DocType.IMAGE, ".jpeg": DocType.IMAGE,
    ".webp": DocType.IMAGE, ".gif": DocType.IMAGE,
    ".mp4": DocType.VIDEO, ".mov": DocType.VIDEO, ".webm": DocType.VIDEO,
}
_TEXT_BYTES = 256 * 1024
_CONTENT_LIMIT = 120_000
_SPANS_LIMIT = 2_500
_BINARY_BYTES = 24 * 1024 * 1024
_OFFICE_BYTES = 96 * 1024 * 1024
_PDF_PAGES = 100
_OCR_PAGES = 3
_OCR_SECONDS = 8
_IMAGE_PIXELS = 16_000_000
_MAX_COLUMNS = 64
_MAX_SHEETS = 32
_LIMIT_NOTICE = "已達讀取上限，僅保留可讀取的部分"
_TIME = r"(?:\d{1,2}:)?\d{2}:\d{2}[,.]\d{3}"
_CUE = re.compile(rf"^\s*({_TIME})\s+-->\s+({_TIME})(?:\s.*)?$")


def _bounded_text(path: Path, notes: list[str]) -> str:
    with path.open("rb") as stream:
        data = stream.read(_TEXT_BYTES + 1)
    if len(data) > _TEXT_BYTES:
        notes.append(_LIMIT_NOTICE)
        data = data[:_TEXT_BYTES]
        last_break = max(data.rfind(b"\n"), data.rfind(b"\r"))
        data = data[:last_break + 1]
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        notes.append("部分文字無法以 UTF-8 解碼")
        return data.decode("utf-8-sig", errors="replace")


def _read_text(path: Path, notes: list[str]) -> Iterator[EvidenceSpan]:
    for number, line in enumerate(_bounded_text(path, notes).splitlines(), 1):
        yield EvidenceSpan(f"第 {number} 行", line)


def _row(values: Iterable[object], notes: list[str]) -> str:
    cells = list(islice(values, _MAX_COLUMNS + 1))
    if len(cells) > _MAX_COLUMNS:
        notes.append(_LIMIT_NOTICE)
    texts = [" ".join(str(value).split()) if value is not None else "" for value in cells[:_MAX_COLUMNS]]
    while texts and not texts[-1]:
        texts.pop()
    return " | ".join(texts)


def _read_csv(path: Path, notes: list[str]) -> Iterator[EvidenceSpan]:
    csv_text = io.StringIO(_bounded_text(path, notes), newline="")
    for number, cells in enumerate(csv.reader(csv_text, strict=True), 1):
        yield EvidenceSpan(f"第 {number} 列", _row(cells, notes))


def _read_docx(path: Path, notes: list[str]) -> Iterator[EvidenceSpan]:
    from docx import Document as WordDocument
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = WordDocument(str(path))
    paragraph_count = 0
    table_count = 0
    for element in document.element.body:
        if element.tag == qn("w:p"):
            paragraph_count += 1
            yield EvidenceSpan(f"第 {paragraph_count} 段", Paragraph(element, document).text)
        elif element.tag == qn("w:tbl"):
            table_count += 1
            for number, row in enumerate(Table(element, document).rows, 1):
                yield EvidenceSpan(f"表格 {table_count} 第 {number} 列",
                                   _row((cell.text for cell in row.cells), notes))


def _slide_shapes(shapes):
    for shape in shapes:
        yield shape
        if hasattr(shape, "shapes"):
            yield from _slide_shapes(shape.shapes)


def _read_pptx(path: Path, notes: list[str]) -> Iterator[EvidenceSpan]:
    from pptx import Presentation

    presentation = Presentation(str(path))
    for number, slide in enumerate(presentation.slides, 1):
        locator = f"第 {number} 張投影片"
        table_count = 0
        for shape in _slide_shapes(slide.shapes):
            if shape.has_text_frame:
                yield EvidenceSpan(locator, shape.text)
            elif shape.has_table:
                table_count += 1
                for row_number, row in enumerate(shape.table.rows, 1):
                    yield EvidenceSpan(f"{locator}，表格 {table_count} 第 {row_number} 列",
                                       _row((cell.text for cell in row.cells), notes))


def _read_xlsx(path: Path, notes: list[str]) -> Iterator[EvidenceSpan]:
    from openpyxl import load_workbook

    if path.suffix.lower() == ".xls":
        notes.append("舊版 .xls 格式無法解析，請轉存為 .xlsx")
        return
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) > _MAX_SHEETS:
            notes.append(_LIMIT_NOTICE)
        for sheet in workbook.worksheets[:_MAX_SHEETS]:
            if (sheet.max_column or 0) > _MAX_COLUMNS:
                notes.append(_LIMIT_NOTICE)
            max_column = min(sheet.max_column or _MAX_COLUMNS + 1, _MAX_COLUMNS + 1)
            for number, cells in enumerate(sheet.iter_rows(max_col=max_column, values_only=True), 1):
                yield EvidenceSpan(f"工作表「{sheet.title}」第 {number} 列", _row(cells, notes))
    finally:
        workbook.close()


def _tesseract() -> str | None:
    return shutil.which(os.environ.get("KKG_TESSERACT") or "tesseract")


def _ocr(path: Path, executable: str) -> str:
    result = subprocess.run(
        [executable, str(path.resolve()), "stdout", "-l", os.environ.get("KKG_OCR_LANG", "eng")],
        capture_output=True, encoding="utf-8", errors="replace", timeout=_OCR_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    if result.returncode:
        raise RuntimeError("OCR 讀取失敗，請核對本機語言套件及附件格式")
    return result.stdout.strip()


def _ocr_pdf_page(path: Path, index: int, executable: str) -> str:
    import pymupdf

    with pymupdf.open(path) as scanned, TemporaryDirectory(prefix="kkg-ocr-") as folder:
        page = scanned[index]
        scale = min(3, 1_800 / max(page.rect.width, page.rect.height, 1))
        image = Path(folder) / "page.png"
        page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(image)
        return _ocr(image, executable)


def _read_pdf(path: Path, notes: list[str]) -> Iterator[EvidenceSpan]:
    from pypdf import PdfReader

    pages = PdfReader(path).pages
    total = len(pages)
    if total > _PDF_PAGES:
        notes.append(f"PDF 已達 {_PDF_PAGES} 頁讀取上限")
    executable = None
    ocr_checked = False
    ocr_count = 0
    for index in range(min(total, _PDF_PAGES)):
        locator = f"第 {index + 1} 頁"
        try:
            page = pages[index]
            content = (page.extract_text() or "").strip()
        except Exception:
            notes.append(f"{locator} 無法讀取文字")
            continue
        if content:
            yield EvidenceSpan(locator, content)
            continue
        if page.get("/Contents") is None:
            continue
        if not ocr_checked:
            executable = _tesseract()
            ocr_checked = True
        if executable is None:
            notes.append(f"{locator} 沒有可選取的文字，本機 OCR 未啟用")
            continue
        if ocr_count >= _OCR_PAGES:
            notes.append(f"{locator} 已達每份文件的 OCR 頁數上限")
            continue
        ocr_count += 1
        try:
            content = _ocr_pdf_page(path, index, executable)
        except ImportError:
            notes.append(f"{locator} 的本機 OCR 需要 PyMuPDF")
        except subprocess.TimeoutExpired:
            notes.append(f"{locator} 的本機 OCR 逾時")
        except Exception:
            notes.append(f"{locator} 的本機 OCR 讀取失敗，請核對本機語言套件及附件格式")
        else:
            if content:
                yield EvidenceSpan(f"{locator}（OCR）", content)
            else:
                notes.append(f"{locator} 未能透過本機 OCR 辨識文字")


def _read_image(path: Path, doc: Document, notes: list[str]) -> Iterator[EvidenceSpan]:
    from PIL import Image

    with Image.open(path) as image:
        doc.meta.update(width=image.width, height=image.height, format=image.format)
    if doc.meta["width"] * doc.meta["height"] > _IMAGE_PIXELS:
        notes.append("圖像尺寸超出 OCR 讀取上限")
        return
    executable = _tesseract()
    if executable is None:
        notes.append("本機 OCR 未啟用，無法讀取圖像內容")
        return
    try:
        content = _ocr(path, executable)
    except subprocess.TimeoutExpired:
        notes.append("本機 OCR 逾時")
    except (OSError, RuntimeError):
        notes.append("本機 OCR 讀取失敗，請核對本機語言套件及附件格式")
    else:
        if content:
            yield EvidenceSpan("圖像 OCR", content)
        else:
            notes.append("未能透過本機 OCR 辨識文字")


def _transcript(path: Path) -> Path | None:
    for extension in (".srt", ".vtt"):
        sidecar = path.with_suffix(extension)
        if sidecar.is_file():
            return sidecar
    return None


def _read_video(path: Path, doc: Document, notes: list[str]) -> Iterator[EvidenceSpan]:
    doc.meta["size_mb"] = round(path.stat().st_size / (1024 * 1024), 1)
    transcript = _transcript(path)
    if transcript is None:
        notes.append("影片缺少同名的 .srt 或 .vtt 字幕，無法讀取內容")
        return
    doc.meta["transcript"] = transcript.name
    text = _bounded_text(transcript, notes).replace("\r\n", "\n").replace("\r", "\n")
    found = False
    for group in re.split(r"\n[ \t]*\n+", text.strip()):
        lines = group.splitlines()
        for index, line in enumerate(lines[:2]):
            match = _CUE.fullmatch(line)
            if match:
                cue = unescape(re.sub(r"<[^>\n]*>", "", "\n".join(lines[index + 1:]))).strip()
                if cue:
                    found = True
                    yield EvidenceSpan(f"{transcript.name} · {match[1]}–{match[2]}", cue)
                break
    if not found:
        notes.append(f"字幕「{transcript.name}」沒有可用的文字或時間標記")


_READERS = {
    DocType.TEXT: _read_text, DocType.MARKDOWN: _read_text, DocType.CSV: _read_csv,
    DocType.DOCX: _read_docx, DocType.PPTX: _read_pptx, DocType.XLSX: _read_xlsx, DocType.PDF: _read_pdf,
}


def _spans(path: Path, doc: Document, notes: list[str]) -> Iterator[EvidenceSpan]:
    if doc.doc_type is DocType.OTHER:
        notes.append(f"不支援的附件格式（{path.suffix or '無副檔名'}）")
        return
    if doc.doc_type is DocType.VIDEO:
        yield from _read_video(path, doc, notes)
        return
    if doc.doc_type not in (DocType.TEXT, DocType.MARKDOWN, DocType.CSV):
        if path.stat().st_size > _BINARY_BYTES:
            notes.append("附件大小超出讀取上限")
            return
    if path.suffix.lower() in (".docx", ".pptx", ".xlsx"):
        with ZipFile(path) as archive:
            if sum(part.file_size for part in archive.infolist()) > _OFFICE_BYTES:
                notes.append("Office 附件解壓後大小超出讀取上限")
                return
    if doc.doc_type is DocType.IMAGE:
        yield from _read_image(path, doc, notes)
    else:
        yield from _READERS[doc.doc_type](path, notes)


def ingest_file(path: str | Path) -> Document:
    path = Path(path)
    doc = Document(path=str(path), name=path.name, doc_type=_EXT_MAP.get(path.suffix.lower(), DocType.OTHER))
    notes: list[str] = []
    count = 0
    spans = _spans(path, doc, notes)
    try:
        for span in spans:
            count += len(span.text) + 1
            if count > _CONTENT_LIMIT or len(doc.segments) >= _SPANS_LIMIT:
                notes.append(_LIMIT_NOTICE)
                break
            doc.segments.append(span)
    except ImportError as error:
        notes.append(f"缺少解析套件（{error.name or doc.doc_type.value}）")
    except Exception as error:
        log.warning("無法完整讀取附件 %s (%s)", doc.name, type(error).__name__)
        notes.append(f"無法完整讀取附件（{type(error).__name__}）")
    finally:
        spans.close()
    doc.text = "\n".join(span.text for span in doc.segments).strip()
    if not doc.text:
        doc.segments.clear()
        if not notes:
            notes.append("附件沒有可用的文字")
    doc.error = "；".join(dict.fromkeys(notes)) or None
    return doc


def ingest_dir(path: str | Path) -> list[Document]:
    root = Path(path)
    files = sorted(file for file in root.rglob("*") if file.is_file() and not file.name.startswith("."))
    subtitles = {_transcript(file) for file in files if _EXT_MAP.get(file.suffix.lower()) is DocType.VIDEO}
    return [ingest_file(file) for file in files if file not in subtitles]
