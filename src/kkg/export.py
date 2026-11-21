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
from .output_gate import gate_text                         # noqa: E402
from .pipeline import Pipeline, load_applications           # noqa: E402
from .privacy import Redactor                               # noqa: E402
from .program import Program                                # noqa: E402
from .review import STATUS_LABELS, _label, aggregate, assess  # noqa: E402

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def export_workbook(prog: Program, apps: list, findings: dict[str, list[Finding]],
                    scores: dict[str, list[ReviewScore]], out: Path,
                    redactor=None) -> Path:
    """匯出評審總表；所有文字欄位通過輸出閘門後才寫入試算表。"""
    active = redactor or Redactor()

    def _mask(v: str) -> str:
        return gate_text(str(v), active)[0]

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
        rows = scores.get(a.app_id, [])
        # 與工作台/CLI 同一評估口徑：assess 消費計劃門檻、min_reviewers、批次發現與狀態機，
        # 不再用硬編碼 60 分自行判斷「建議入圍」。
        agg = assess(rows, prog, fd, pass_threshold=prog.pass_threshold)
        total = agg.get("weighted_total")
        serious = sum(1 for f in fd if f.severity.value in ("error", "critical"))
        human = sum(1 for f in fd if f.needs_human)
        suggestion = STATUS_LABELS[agg["status"]]
        ws.append([_mask(a.app_id), _mask(prog.name), _mask(a.title), _mask(a.applicant), _mask(a.email),
                   len(fd), serious, human, total if total is not None else "-", _mask(suggestion)])
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
            ws2.append([_mask(a.app_id), f.finding_type.value, f.severity.value,
                        _mask(f.message), _mask(f.source), "是" if f.needs_human else "否"])
    ws2.freeze_panes = "A2"
    for col, width in zip("ABCDEF", [16, 16, 12, 70, 24, 14]):
        ws2.column_dimensions[col].width = width

    # Sheet 3：評分明細（含每位評審的加權分，與總覽口徑一致）
    ws3 = wb.create_sheet("評分明細")
    criteria = [c.name for c in prog.criteria]
    ws3.append(["申請編號", "評審"] + criteria + ["加權總分", "評語"])
    for c in ws3[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for a in apps:
        rows = scores.get(a.app_id, [])
        per_reviewer = aggregate(rows, prog).get("per_reviewer_weighted", {})
        for s in rows:
            normalized = {_label(k): v for k, v in s.criteria.items()}
            ws3.append([_mask(a.app_id), _mask(s.reviewer)] +
                       [normalized.get(_label(k), 0) for k in criteria] +
                       [per_reviewer.get(s.reviewer, "-"), _mask(s.comment)])
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

    from .privacy import Redactor

    prog = Program.load(args.program)
    apps = load_applications(args.apps)
    redactor = Redactor()
    pipe = Pipeline(prog, llm=get_llm("mock"))
    findings = {}
    for a in apps:
        _, findings[a.app_id] = pipe.intake(a)
    findings_all = {k: v + [] for k, v in findings.items()}
    for f in pipe.batch_checks(apps):
        # 批次發現歸到相關申請：優先用 application_ids，訊息匹配作後備
        ids = getattr(f, "application_ids", []) or [a.app_id for a in apps if a.app_id in f.message]
        for app_id in ids:
            findings_all.setdefault(app_id, []).append(f)

    scores: dict[str, list[ReviewScore]] = {}
    for p in Path(args.reviews).glob("*.json"):
        for item in json.loads(p.read_text(encoding="utf-8")):
            scores.setdefault(item["app_id"], []).append(
                ReviewScore(reviewer=item["reviewer"], criteria=item["criteria"],
                            comment=item.get("comment", "")))

    out = export_workbook(prog, apps, findings_all, scores, Path(args.out), redactor=redactor)
    print(f"評審彙總Excel已輸出：{out.resolve()}")


if __name__ == "__main__":
    main()
