"""評審彙總匯出：把多份申請的審查結論與評分整合成一份Excel總表。

對應場景16「自動生成標準化申請摘要、評審資料包及彙總評語與評分」，
亦支撐評審維度「功能和用戶體驗 / 商業價值與社會影響」。

用法：
    python -m kkg.export --apps data/demo/applications --out output/review_summary.xlsx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
except ImportError:
    sys.exit("需要openpyxl：pip install openpyxl")

from .llm import get_llm                                   # noqa: E402
from .models import Finding, ReviewScore                   # noqa: E402
from .pipeline import Pipeline, load_applications           # noqa: E402
from .program import Program                                # noqa: E402

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def export_workbook(prog: Program, apps: list, findings: dict[str, list[Finding]],
                    scores: dict[str, list[ReviewScore]], out: Path) -> Path:
    wb = openpyxl.Workbook()

    # Sheet 1：申請總覽
    ws = wb.active
    ws.title = "申請總覽"
    headers = ["申請編號", "計劃", "項目名稱", "申請主體", "聯絡電郵", "問題數",
               "嚴重問題數", "需人工複核數", "加權總分", "建議"]
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(horizontal="center")

    pipe = Pipeline(prog)
    for a in apps:
        fd = findings.get(a.app_id, [])
        agg = pipe.review(a, scores.get(a.app_id, []))
        total = agg.get("weighted_total")
        serious = sum(1 for f in fd if f.severity.value in ("error", "critical"))
        human = sum(1 for f in fd if f.needs_human)
        if serious:
            suggestion = "需補正 / 人工複核"
        elif total is not None and total >= 60:
            suggestion = "建議入圍"
        else:
            suggestion = "待評審"
        ws.append([a.app_id, prog.name, a.title, a.applicant, a.email,
                   len(fd), serious, human, total if total is not None else "-", suggestion])
    ws.freeze_panes = "A2"
    for col, width in zip("ABCDEFGHIJ", [16, 28, 34, 22, 28, 10, 12, 14, 12, 20]):
        ws.column_dimensions[col].width = width

    # Sheet 2：發現明細（人工複核清單）
    ws2 = wb.create_sheet("發現明細")
    ws2.append(["申請編號", "類型", "嚴重程度", "說明", "來源", "需人工複核"])
    for c in ws2[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for a in apps:
        for f in findings.get(a.app_id, []):
            ws2.append([a.app_id, f.finding_type.value, f.severity.value,
                        f.message, f.source, "是" if f.needs_human else "否"])
    ws2.freeze_panes = "A2"
    for col, width in zip("ABCDEF", [16, 16, 12, 70, 24, 14]):
        ws2.column_dimensions[col].width = width

    # Sheet 3：評分明細
    ws3 = wb.create_sheet("評分明細")
    criteria = [c.name for c in prog.criteria]
    ws3.append(["申請編號", "評審"] + criteria + ["加權總分", "評語"])
    for c in ws3[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for a in apps:
        for s in scores.get(a.app_id, []):
            ws3.append([a.app_id, s.reviewer] +
                       [s.criteria.get(k, 0) for k in criteria] +
                       [round(s.total, 2), s.comment])
    ws3.freeze_panes = "A2"
    for col in "ABCDEFGH":
        ws3.column_dimensions[col].width = 18
    ws3.column_dimensions["H"].width = 60

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="匯出評審彙總Excel")
    ap.add_argument("--program", default="configs/program_demo.yaml")
    ap.add_argument("--apps", default="data/demo/applications")
    ap.add_argument("--reviews", default="data/demo/reviews")
    ap.add_argument("--out", default="output/review_summary.xlsx")
    args = ap.parse_args()

    prog = Program.load(args.program)
    apps = load_applications(args.apps)
    pipe = Pipeline(prog, llm=get_llm("mock"))
    findings = {}
    for a in apps:
        _, findings[a.app_id] = pipe.intake(a)
    findings_all = {k: v + [] for k, v in findings.items()}
    for f in pipe.batch_checks(apps):
        # 批次發現歸到相關申請（簡單以訊息中包含的編號匹配）
        for a in apps:
            if a.app_id in f.message:
                findings_all.setdefault(a.app_id, []).append(f)

    scores: dict[str, list[ReviewScore]] = {}
    for p in Path(args.reviews).glob("*.json"):
        for item in json.loads(p.read_text(encoding="utf-8")):
            scores.setdefault(item["app_id"], []).append(
                ReviewScore(reviewer=item["reviewer"], criteria=item["criteria"],
                            comment=item.get("comment", "")))

    out = export_workbook(prog, apps, findings_all, scores, Path(args.out))
    print(f"評審彙總Excel已輸出：{out.resolve()}")


if __name__ == "__main__":
    main()
