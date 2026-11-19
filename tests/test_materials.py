"""Regression coverage for the material-to-reviewer workflow."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kkg.check import check_completeness  # noqa: E402
from kkg.extract import anomaly_scan, cross_verify, extract_fields  # noqa: E402
from kkg.ingest import ingest_dir, ingest_file  # noqa: E402
from kkg.llm import MockLLM  # noqa: E402
from kkg.models import Application  # noqa: E402
from kkg.program import Program  # noqa: E402
from kkg.summarize import reviewer_pack  # noqa: E402


def _application(*docs, budget=95000):
    return Application("APP-EVIDENCE", "demo", "社區閲讀計劃", "社區團隊",
                       email="team@example.org", team=["陳小姐"], fields={"budget": budget}, docs=list(docs))


@pytest.mark.parametrize(("value", "expected"), [
    ("HK$95,000.50", 95000.5), ("港幣 9.5 萬元", 95000), ("9.5\u4e07元", 95000),
    ("ＨＫＤ　９５，０００", 95000), ("95k HKD", 95000), (0, 0),
    ("US$95,000", None), ("95,000 USD", None), ("人民幣 9.5 萬", None),
    ("95,00", None), ("約 95,000 或 100,000", None), ("10e2", None),
    (float("inf"), None), (float("nan"), None), (True, None),
])
def test_hkd_parser_requires_a_finite_amount_in_the_right_currency(value, expected):
    from kkg.extract import parse_hkd

    assert parse_hkd(value) == expected


def test_offline_values_are_grounded_and_order_independent(tmp_path):
    chinese = tmp_path / "申請書.md"
    chinese.write_text("# 項目詳情\n申請預算：港幣 9.5 萬元\n項目名稱：社區閲讀計劃\n",
                       encoding="utf-8")
    legacy = tmp_path / "補充材料.txt"
    legacy.write_text("\u9879目名\u79f0：社區閲讀計劃\n\u7533\u8bf7\u9884算：12\u4e07元\n"
                      "\u8054系\u90ae箱：other@example.org\n",
                      encoding="utf-8")
    financial = tmp_path / "funding.csv"
    financial.write_text("requested_budget,contact_email\nHKD 105000,team@example.org\n", encoding="utf-8")
    itemized = tmp_path / "itemized.csv"
    itemized.write_text("item,amount_hkd,note\n人力,80000,兩位導師\n雜費,15000,交通\n", encoding="utf-8")
    app = _application(*ingest_dir(tmp_path))

    extracted = extract_fields(app, MockLLM())
    evidence = extracted["_evidence"]
    assert extracted["project_title"] == "社區閲讀計劃"
    assert {item["source"]: item["value"] for item in evidence["requested_budget"]} == {
        "申請書.md": 95000, "補充材料.txt": 120000, "funding.csv": 105000,
    }
    assert {item["locator"] for item in evidence["requested_budget"]} == {"第 2 行", "第 2 列"}
    assert any("9.5 萬元" in item["quote"] for item in evidence["requested_budget"])

    findings = cross_verify(app, extracted)
    form_comparisons = [f for f in findings if f.source == "cross_verify.budget" and "表單" in f.message]
    assert len(form_comparisons) == 2
    assert all(f.detail and f.needs_human for f in form_comparisons)
    assert any("附件" in f.message and "申請書.md" in f.detail and "補充材料.txt" in f.detail
               for f in findings if f.source == "cross_verify.budget")
    assert any(f.source == "cross_verify.email" and "補充材料.txt" in f.detail for f in findings)

    app.docs.reverse()
    assert extract_fields(app, MockLLM()) == extracted
    assert cross_verify(app, extracted) == findings


def test_word_table_supplies_budget_with_row_locator(tmp_path):
    docx = pytest.importorskip("docx")
    source = tmp_path / "資助申請.docx"
    word = docx.Document()
    word.add_paragraph("資助申請表")
    row = word.add_table(rows=1, cols=2).rows[0].cells
    row[0].text, row[1].text = "申請資助額", "HK$ 95,000"
    word.save(source)

    material = ingest_file(source)
    evidence = extract_fields(_application(material), MockLLM())["_evidence"]["requested_budget"]
    assert material.error is None
    assert "資助申請表" in material.text
    assert evidence == [{"source": source.name, "locator": "表格 1 第 1 列",
                         "quote": "申請資助額 | HK$ 95,000", "value": 95000.0}]


def test_powerpoint_contains_text_and_uses_slide_and_table_locators(tmp_path):
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    source = tmp_path / "Funding pitch.pptx"
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "Target users: Libraries"
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(0, 1).text = "Requested budget (HKD)", "96,000"
    deck.save(source)

    evidence = extract_fields(_application(ingest_file(source)), MockLLM())["_evidence"]
    assert evidence["target_users"][0]["value"] == "Libraries"
    assert evidence["target_users"][0]["locator"] == "第 1 張投影片"
    assert evidence["requested_budget"][0]["value"] == 96000
    assert evidence["requested_budget"][0]["locator"] == "第 1 張投影片，表格 1 第 1 列"


def test_workbook_evidence_includes_the_actual_sheet_and_row(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "財務資料.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "人手開支"
    wb.active.append(["item", "amount_hkd"])
    wb.active.append(["老師", 50000])
    cover = wb.create_sheet("預算總表")
    cover.append(["名稱", "金額（HKD）"])
    cover.append(["申請預算", "9.5 萬"])
    wb.save(source)

    evidence = extract_fields(_application(ingest_file(source)), MockLLM())["_evidence"]["requested_budget"]
    assert [(item["locator"], item["value"]) for item in evidence] == [("工作表「預算總表」第 2 列", 95000)]


def test_pdf_budget_is_located_on_its_original_page(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    source = tmp_path / "Application letter.pdf"
    writer = pypdf.PdfWriter()
    for line in (b"Funding application", b"Requested budget: HK$95,000"):
        page = writer.add_blank_page(width=400, height=300)
        font = DictionaryObject({NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (" + line + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(source)

    evidence = extract_fields(_application(ingest_file(source)), MockLLM())["_evidence"]["requested_budget"]
    assert [(item["source"], item["locator"], item["value"]) for item in evidence] == [
        (source.name, "第 2 頁", 95000),
    ]


@pytest.mark.parametrize(("extension", "preamble"), [(".srt", "1\n"), (".vtt", "WEBVTT\n\n")])
def test_local_video_transcript_retains_both_filenames_and_timing(tmp_path, extension, preamble):
    video = tmp_path / "計劃示範.mp4"
    video.write_bytes(b"test media")
    captions = video.with_suffix(extension)
    captions.write_text(preamble + "00:00:01.000 --> 00:00:05.000\n服務對象：照顧者\n", encoding="utf-8")

    documents = ingest_dir(tmp_path)
    assert [doc.name for doc in documents] == [video.name]
    assert documents[0].error is None
    evidence = extract_fields(_application(*documents), MockLLM())["_evidence"]["target_users"][0]
    assert evidence["source"] == video.name
    assert captions.name in evidence["locator"] and "00:00:01.000" in evidence["locator"]
    assert evidence["value"] == "照顧者"


def test_media_without_usable_content_is_reviewable(tmp_path, monkeypatch):
    image_module = pytest.importorskip("PIL.Image")
    image_module.init()
    monkeypatch.setenv("KKG_TESSERACT", str(tmp_path / "no-tesseract"))
    image = tmp_path / "掃描圖像.png"
    scan = tmp_path / "掃描文件.pdf"
    for path in (image, scan):
        with image_module.new("RGB", (200, 100), "white") as blank:
            blank.save(path)
    video = tmp_path / "missing captions.mp4"
    video.write_bytes(b"test media")

    app = _application(*(ingest_file(path) for path in (image, scan, video)))
    assert all(not doc.text and not doc.segments for doc in app.docs)
    assert all("OCR" in (doc.error or "") for doc in app.docs[:2])
    assert ".srt" in (app.docs[2].error or "")
    findings = check_completeness(app, Program("demo", "示範計劃"))
    assert {f.source for f in findings if f.needs_human} == {image.name, scan.name, video.name}


def test_corrupt_parser_does_not_discard_readable_attachments(tmp_path):
    pytest.importorskip("docx")
    corrupt = tmp_path / "無法開啟.docx"
    corrupt.write_text("not an Office ZIP", encoding="utf-8")
    readable = tmp_path / "proposal.txt"
    readable.write_text("Requested budget: HKD 95,000", encoding="utf-8")

    app = _application(*ingest_dir(tmp_path))
    findings = check_completeness(app, Program("demo", "示範計劃"))
    assert any(f.source == corrupt.name and f.needs_human for f in findings)
    assert extract_fields(app, MockLLM())["requested_budget"] == 95000


def test_text_limits_are_explicit_and_retain_the_readable_prefix(tmp_path):
    source = tmp_path / "large.txt"
    source.write_text("申請預算：95,000\n" + "x" * 500_000 + "\n申請預算：1,100,000", encoding="utf-8")

    material = ingest_file(source)
    assert material.error and "上限" in material.error
    assert material.text.startswith("申請預算：95,000")
    assert len(material.text) < 500_000 and "1,100,000" not in material.text
    assert extract_fields(_application(material), MockLLM())["requested_budget"] == 95000


def test_model_failure_keeps_deterministic_evidence_and_flags_for_review(tmp_path):
    class UnavailableModel:
        def complete(self, system, user):
            raise TimeoutError("provider secret detail")

    source = tmp_path / "proposal.txt"
    source.write_text("Requested budget: HK$95,000", encoding="utf-8")
    app = _application(ingest_file(source))

    extracted = extract_fields(app, UnavailableModel())
    assert extracted["requested_budget"] == 95000
    findings = cross_verify(app, extracted)
    assert any(f.source == "extract.model" and f.needs_human for f in findings)
    assert "provider secret detail" not in repr(findings)


def test_model_evidence_requires_an_exact_source_and_quote(tmp_path):
    class EvidenceModel:
        def complete(self, system, user):
            return json.dumps({
                "proposal": [{"source": "plan.txt", "locator": "第 1 行",
                              "quote": "Libraries will share a lending app."}],
                "target_users": [{"source": "plan.txt", "locator": "第 1 行",
                                  "quote": "Nonexistent claim"}],
            })

    source = tmp_path / "plan.txt"
    source.write_text("Libraries will share a lending app.", encoding="utf-8")
    app = _application(ingest_file(source))

    extracted = extract_fields(app, EvidenceModel())
    assert extracted["proposal"] == "Libraries will share a lending app."
    assert "target_users" not in extracted
    assert any(f.source == "extract.model" and f.needs_human for f in cross_verify(app, extracted))


def test_batch_anomaly_uses_hkd_amounts_and_explicit_application_ids():
    apps = [_application(budget=amount) for amount in ("9.5萬", 95000, 96000, "HKD 1,500,000", "USD 42")]
    for index, app in enumerate(apps):
        app.app_id = f"APP-{index}"

    assert [f.application_ids for f in anomaly_scan(apps)] == [["APP-3"]]


def test_reviewer_pack_has_structured_brief_and_original_evidence(tmp_path):
    source = tmp_path / "社區方案.md"
    source.write_text(
        "## 項目概述\n為缺乏無障礙圖書的讀者提供支援。\n\n"
        "服務對象：視障讀者\n## 技術方案\n與圖書館共建有聲圖書目錄。\n\n"
        "申請預算：9.5萬\n預期成果：三間圖書館完成試點。\n", encoding="utf-8",
    )
    app = _application(ingest_file(source))
    extracted = extract_fields(app, MockLLM())
    findings = cross_verify(app, extracted)

    pack = reviewer_pack(app, Program("demo", "示範計劃"), extracted, findings, tmp_path / "packs")
    assert {path.name for path in pack.iterdir()} == {"summary.md", "one_pager.md", "checklist.md"}
    brief = (pack / "one_pager.md").read_text(encoding="utf-8")
    for heading, fact in (
        ("項目目的", "為缺乏無障礙圖書的讀者提供支援"), ("服務對象", "視障讀者"),
        ("實施方案", "與圖書館共建有聲圖書目錄"), ("申請預算", "HK$95,000"),
        ("預期成果", "三間圖書館完成試點"),
    ):
        assert f"## {heading}" in brief and fact in brief
    summary = (pack / "summary.md").read_text(encoding="utf-8")
    assert source.name in summary and "第 9 行" in summary and "申請預算：9.5萬" in summary
