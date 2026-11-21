"""持久化工作流：收件、評審、人工簽發與資助後追蹤。"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .check import check_completeness
from .extract import cross_verify, extract_fields
from .llm import MockLLM
from .money import parse_hkd
from .models import Application, Document, DocType, EvidenceSpan, Finding, FindingType, Milestone, ReviewScore, Severity
from .output_gate import gate_text
from .pipeline import Pipeline
from .program import Program
from .review import aggregate, assess, followup_todos, result_notice
from .storage import CaseStore, WorkspaceConflict
from .submission import application_from, attachment_key, fingerprint, parse_attachments, safe_filename
from .track import Disbursement, PeriodicReport, project_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _application(record: dict) -> Application:
    data = record["application"]
    documents = [Document(**{**doc, "doc_type": DocType(doc["doc_type"]),
                             "segments": [EvidenceSpan(**segment) for segment in doc.get("segments", [])]})
                 for doc in data["docs"]]
    return Application(**{**data, "docs": documents})


def _event(case: dict, action: str, actor: str, detail: str) -> None:
    case["audit"].append({"ts": _now(), "action": action, "actor": actor, "detail": detail})


def _actor(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ValueError("請填寫有效的操作員名稱。")
    return value.strip()


def _reason(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("請填寫操作依據，長度不超過 2,000 個字元。")
    return value.strip()


def _project(data: dict) -> tuple[list[Milestone], list[PeriodicReport], list[Disbursement]]:
    return ([Milestone(**item) for item in data["milestones"]],
            [PeriodicReport(**item) for item in data["periodic_reports"]],
            [Disbursement(**item) for item in data["disbursements"]])


class Workspace:
    def __init__(self, store: CaseStore, programs: dict[str, Program]):
        self.store = store
        self.programs = programs

    def program(self, program_id: str) -> Program:
        try:
            return self.programs[program_id]
        except KeyError as exc:
            raise ValueError(f"找不到計劃「{program_id}」。") from exc

    @staticmethod
    def _prepare(app: Application, program: Program) -> dict:
        extracted = extract_fields(app, MockLLM())
        extracted["_program_name"] = program.name
        extracted["_criteria_hint"] = " / ".join(rule.name for rule in program.criteria)
        return extracted

    def _new_case(self, app: Application, channel: str, digest: str,
                  scores: list[ReviewScore] | None = None, *, actor: str | None = None) -> dict:
        program = self.program(app.program)
        case = {
            "id": uuid.uuid4().hex, "application": asdict(app), "channel": channel,
            "received_at": _now(), "revision": 1, "receipt": digest,
            "rule_version": program.rule_version, "extracted": self._prepare(app, program),
            "program_snapshot": asdict(program),
            "scores": [asdict(score) for score in scores or []], "resolution": None, "decision": None,
            "project": {"milestones": [], "periodic_reports": [], "disbursements": []}, "audit": [],
        }
        # 收件事件綁定服務端提交者身份（P0-06）；本機/歷史路徑無身份時記「系統」。
        _event(case, "received", actor or "系統",
               f"{app.app_id} · {channel} · {app.program}@{program.rule_version}")
        return case

    def _batch_findings(self, records: list[dict]) -> dict[tuple[str, str], list[Finding]]:
        grouped: dict[str, list[Application]] = {}
        for record in records:
            app = _application(record)
            grouped.setdefault(app.program, []).append(app)
        findings: dict[tuple[str, str], list[Finding]] = {}
        for program_id, applications in grouped.items():
            # A removed plan remains readable in records signed under it.
            if program_id not in self.programs:
                continue
            pipeline = Pipeline(self.program(program_id), MockLLM())
            for finding in pipeline.batch_checks(applications):
                for app_id in finding.application_ids:
                    findings.setdefault((program_id, app_id), []).append(finding)
        return findings

    def _evaluate(self, case: dict, batch: dict[tuple[str, str], list[Finding]]) -> tuple[dict, list[dict], str]:
        if case["decision"]:
            signed = case["decision"]
            return signed["assessment"], signed["findings"], ""
        app = _application(case)
        program = self.programs.get(app.program)
        if program is None:
            findings = [asdict(Finding(FindingType.ELIGIBILITY, Severity.ERROR,
                                       "這份申請的計劃設定目前無法使用。",
                                       source="program_id", needs_human=True))]
            result = assess([], Program(app.program, app.program), [], pass_threshold=60)
            result.update(status="needs_compliance", holds=["needs_compliance"],
                          approval_allowed=False, decline_allowed=False)
            result["todos"] = followup_todos(app.app_id, result)
            return result, findings, ""
        findings = check_completeness(app, program) + cross_verify(app, case["extracted"])
        findings.extend(batch.get((app.program, app.app_id), []))
        if case["rule_version"] != program.rule_version:
            findings.append(Finding(FindingType.ELIGIBILITY, Severity.WARNING, "計劃規則已更新，請按新規則覆核。",
                                    source="rule_version", needs_human=True))
        # 截止時間以服務端收件時間為準（缺陷修復：不再依賴申請人自報的 submitted_at）。
        try:
            received_dt = datetime.fromisoformat(case.get("received_at") or "")
        except ValueError:
            received_dt = None
        if program.deadline and received_dt and received_dt.astimezone().date() > date.fromisoformat(program.deadline):
            findings.append(Finding(FindingType.ELIGIBILITY, Severity.ERROR,
                                    f"服務端收件時間 {received_dt.astimezone().date().isoformat()} 已超過計劃截止日期 "
                                    f"{program.deadline}，須人工裁定是否受理。",
                                    source="deadline", needs_human=True))
        rows = [asdict(finding) for finding in findings]
        encoded = json.dumps([case["scores"], rows, program.rule_version], sort_keys=True, ensure_ascii=False)
        basis = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        reviewed = bool(case["resolution"] and case["resolution"]["basis"] == basis)
        scores = [ReviewScore(**score) for score in case["scores"]]
        result = assess(scores, program, findings, pass_threshold=program.pass_threshold, reviewed=reviewed)
        result["todos"] = followup_todos(app.app_id, result)
        return result, rows, basis

    def _view(self, case: dict, batch: dict[tuple[str, str], list[Finding]]) -> dict:
        app = case["application"]
        assessment, findings, basis = self._evaluate(case, batch)
        resolution = case["resolution"]
        decision = case["decision"]
        if resolution:
            resolution = {**{key: resolution[key] for key in ("actor", "reason", "at")},
                          "current": bool(decision or resolution["basis"] == basis)}
        if decision:
            # 保留簽發時的計劃快照：歷史包與個案查閱必須對應簽發時的規則版本
            # （審評第三輪實測：投影丟棄快照會令導出出現 V1 清單配 V2 評估）。
            decision = {key: decision[key] for key in ("actor", "reason", "outcome", "at", "rule_version",
                                                       "program_snapshot")}
        program = self.programs.get(app["program"])
        return {
            "id": case["id"], "app_id": app["app_id"], "program_id": app["program"], "title": app["title"],
            "applicant": app["applicant"], "email": app["email"], "team": app["team"], "fields": app["fields"],
            "channel": case["channel"], "revision": case["revision"], "received_at": case["received_at"],
            "rule_version": (decision["rule_version"] if decision else
                             program.rule_version if program else case["rule_version"]),
            "documents": [{key: doc[key] for key in ("name", "doc_type", "error")} for doc in app["docs"]],
            "findings": findings, "extracted": case["extracted"], "scores": case["scores"],
            "assessment": assessment, "resolution": resolution, "decision": decision,
            "project": project_status(*_project(case["project"])), "audit": case["audit"],
        }

    def list(self, *, limit: int | None = None, offset: int = 0) -> dict:
        """個案列表。默認全量（舊行為）；傳 limit/offset 時分頁並回傳總數。

        注意：批次檢查（重複/離羣）目前僅對當前頁的個案計算——跨頁查重
        在大批次場景下需配合 P1 的持久化工作佇列與索引改造。
        """
        with self.store.transaction() as connection:
            records = self.store.list(connection, limit=limit, offset=offset)
            total = self.store.count(connection)
        batch = self._batch_findings(records)
        fields = ("id", "channel", "revision", "received_at")
        app_fields = ("app_id", "title", "applicant")
        items = [
            {**{key: case[key] for key in fields}, **{key: case["application"][key] for key in app_fields},
             "program_id": case["application"]["program"],
             "status": assessment["status"], "weighted_total": assessment["weighted_total"]}
            for case in records for assessment, _, _ in [self._evaluate(case, batch)]
        ]
        if limit is None:
            return {"items": items, "total": len(items)}
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get(self, case_id: str) -> tuple[dict, Application]:
        with self.store.transaction() as connection:
            case = self.store.get(connection, case_id)
            records = self.store.list(connection, case["application"]["program"])
        return self._view(case, self._batch_findings(records)), _application(case)

    def traceability(self, case_id: str) -> dict:
        """為單一個案即時生成可追溯物料：依據卡、合規分級、確定性評分與評測。

        與 CLI 的 ``[7/7]`` 共用 ``Pipeline.traceability()``，差別只在於資料
        取自已入庫的個案而非批次目錄——Web 操作員因此無需跑 CLI 也能看到
        同一套物料。全部規則式、零 LLM、零新數字，且對外輸出經輸出閘門
        去識別化，避免依據卡摘錄夾帶敏感值。

        已簽發個案沿用簽發時的評分；未簽發個案用目前暫存的評分作示範，
        兩者皆標明來源。發現不足時仍回傳空卡片與聲明，絶不虛構判定。
        """
        from .models import ReviewScore
        from .pipeline import Pipeline

        detail, app = self.get(case_id)
        program_id = app.program
        program = self.programs.get(program_id)
        if program is None:
            program = Program(program_id, program_id)
        findings = [
            Finding(**{**row, "finding_type": FindingType(row["finding_type"]),
                       "severity": Severity(row["severity"])})
            for row in detail["findings"]
        ]
        scores = [ReviewScore(**row) for row in detail["scores"]]
        # traceability 是零 LLM 流程；MockLLM 只作為 Pipeline 佔位，不發出請求。
        result = Pipeline(program, MockLLM()).traceability(app, findings, scores)
        result["signed"] = bool(detail["decision"])
        result["score_source"] = "簽發時評分" if detail["decision"] else "目前暫存評分（示範）"
        # 對外物料一律經輸出閘門：整份 JSON 去識別化後才回傳前端。
        from .output_gate import gate_text
        safe, hits = gate_text(json.dumps(result, ensure_ascii=False))
        gated = json.loads(safe)
        if hits:
            gated["gate_hits"] = hits
        return gated

    def submit(self, metadata: dict, files: list[tuple[str, bytes]], channel: str,
               *, actor: str | None = None) -> dict:
        digest = fingerprint(metadata, files)
        app = application_from(metadata, [], fallback_id=f"APP-{digest[:12].upper()}", require_identity=True)
        self.program(app.program)
        with self.store.transaction() as connection:
            previous = self.store.find(connection, app.program, app.app_id)
        if previous is not None:
            if previous["receipt"] != digest:
                raise WorkspaceConflict("此計劃已有相同申請編號，請在現有個案補交或更正。")
            return self.get(previous["id"])[0]
        app.docs = parse_attachments(files)
        case = self._new_case(app, channel, digest, actor=actor)
        with self.store.transaction(write=True) as connection:
            previous = self.store.find(connection, app.program, app.app_id)
            if previous is None:
                self.store.insert(connection, case)
            elif previous["receipt"] != digest:
                raise WorkspaceConflict("此計劃已有相同申請編號，請在現有個案補交或更正。")
            else:
                case = previous
        return self.get(case["id"])[0]

    def seed(self, apps: list[Application], scores: dict[str, list[ReviewScore]]) -> int:
        candidates = [self._new_case(app, "demo", "demo:" + app.app_id, scores.get(app.app_id)) for app in apps]
        added = 0
        with self.store.transaction(write=True) as connection:
            for case in candidates:
                app = case["application"]
                if self.store.find(connection, app["program"], app["app_id"]) is None:
                    self.store.insert(connection, case)
                    added += 1
        return added

    def _edit(self, case_id: str, revision: int, operation, *, post_award: bool = False) -> dict:
        with self.store.transaction(write=True) as connection:
            case = self.store.get(connection, case_id)
            if case["revision"] != revision:
                raise WorkspaceConflict("此個案已更新。請重新載入後再操作。")
            if case["decision"] and not post_award:
                raise WorkspaceConflict("結果已簽發，評審記錄已鎖定。")
            if post_award and (not case["decision"] or case["decision"]["outcome"] != "approved"):
                raise WorkspaceConflict("請先簽發通過結果，再更新項目台帳。")
            records = self.store.list(connection, case["application"]["program"])
            batch = self._batch_findings(records)
            operation(case, batch)
            case["revision"] += 1
            self.store.update(connection, case)
            records = [case if record["id"] == case_id else record for record in records]
            return self._view(case, self._batch_findings(records))

    def amend(self, case_id: str, revision: int, actor: str, metadata: dict,
              files: list[tuple[str, bytes]], removed: list[str]) -> dict:
        actor = _actor(actor)
        additions = parse_attachments(files)
        removal_keys = {attachment_key(safe_filename(name)) for name in removed}

        def apply(case, _batch):
            app = _application(case)
            docs = {attachment_key(doc.name): doc for doc in app.docs if attachment_key(doc.name) not in removal_keys}
            docs.update({attachment_key(doc.name): doc for doc in additions})
            amended = application_from(metadata, list(docs.values()), fallback_id=app.app_id,
                                       default_program=app.program, require_identity=True)
            if (amended.app_id, amended.program) != (app.app_id, app.program):
                raise ValueError("補件時請保留原有計劃及申請編號。")
            program = self.program(app.program)
            case.update(application=asdict(amended), extracted=self._prepare(amended, program),
                        scores=[], resolution=None, rule_version=program.rule_version)
            _event(case, "materials_updated", actor, f"資料更新，新增或更換 {len(additions)} 份附件。")

        return self._edit(case_id, revision, apply)

    def score(self, case_id: str, revision: int, scores: list[ReviewScore], actor: str) -> dict:
        actor = _actor(actor)

        def apply(case, _batch):
            program = self.program(case["application"]["program"])
            errors = aggregate(scores, program)["validation_errors"]
            if errors:
                raise ValueError(" ".join(errors))
            case.update(scores=[asdict(score) for score in scores], resolution=None)
            names = "、".join(score.reviewer for score in scores) or "未有評分"
            _event(case, "scores_saved", actor, f"更新評審員：{names}。")

        return self._edit(case_id, revision, apply)

    def resolve(self, case_id: str, revision: int, actor: str, reason: str) -> dict:
        actor, reason = _actor(actor), _reason(reason)

        def apply(case, batch):
            assessment, _, basis = self._evaluate(case, batch)
            if not basis or not set(assessment["holds"]) & {"needs_review", "needs_compliance", "pending_deliberation"}:
                raise WorkspaceConflict("此個案目前毋須記錄人工覆核或複議。")
            if assessment["active_conflicts"]:
                raise WorkspaceConflict("請安排有利益衝突的評審員迴避並更正評分。")
            case["resolution"] = {"actor": actor, "reason": reason, "basis": basis, "at": _now()}
            _event(case, "resolution_recorded", actor, reason)

        return self._edit(case_id, revision, apply)

    def sign(self, case_id: str, revision: int, actor: str, reason: str, outcome: str) -> dict:
        actor, reason = _actor(actor), _reason(reason)

        def apply(case, batch):
            assessment, findings, _ = self._evaluate(case, batch)
            if not assessment["approval_allowed" if outcome == "approved" else "decline_allowed"]:
                raise WorkspaceConflict("個案仍有待處理事項，或分數未達通過門檻。")
            app = _application(case)
            program = self.program(app.program)
            # 數字台賬：原始申請 + 規則計算出的 assessment（總分/人數/閾值均為可信數字）。
            # 通知中若有台賬外的金額/分數，即視為幻覺並記入閘門統計，交人工覆核。
            from .guard import build_ledger
            notice_ledger = build_ledger(app, assessment, program.budget_cap)
            notice, notice_hits = gate_text(
                result_notice(app.app_id, app.title, assessment, outcome == "approved"),
                ledger=notice_ledger)
            if "數字幻覺" in notice_hits:
                _event(case, "output_gate", actor,
                       f"結果通知含台賬外數字 {notice_hits['數字幻覺']} 處，需人工覆核")
            assessment.update(status=outcome, holds=[], approval_allowed=False, decline_allowed=False,
                              notice=notice)
            assessment["todos"] = followup_todos(app.app_id, assessment)
            case["decision"] = {
                "actor": actor, "reason": reason, "outcome": outcome, "at": _now(),
                "rule_version": program.rule_version, "assessment": assessment, "findings": findings,
                "program_snapshot": asdict(program),  # 簽發版本鎖定：歷史包對應簽發時的規則
            }
            _event(case, "decision_signed", actor, f"{outcome} · {program.rule_version} · {reason}")

        return self._edit(case_id, revision, apply)

    def track(self, case_id: str, revision: int, actor: str, project: dict) -> dict:
        actor = _actor(actor)
        milestones, reports, payments = _project(project)
        for item in milestones:
            if not item.name.strip() or item.status not in {"pending", "done"}:
                raise ValueError("請填寫里程碑名稱及有效狀態。")
            if date.fromisoformat(item.due).isoformat() != item.due:
                raise ValueError("里程碑須使用 YYYY-MM-DD 日期。")
        project_status(milestones, reports, payments)

        def apply(case, _batch):
            # P0-11：撥款計劃合計不得超出核准額度（申請表單的 budget）。
            # 分錄級的日期/金額/實付≤計劃校驗由 Disbursement/PeriodicReport 構造完成。
            scheduled = (sum(Decimal(str(item.amount)) for item in payments) if payments
                         else sum(Decimal(str(item.funding_release)) for item in milestones))
            approved = parse_hkd(case["application"]["fields"].get("budget"))
            if approved is not None and scheduled > Decimal(str(approved)):
                raise ValueError(
                    f"撥款計劃合計 HK${scheduled:,.0f} 超出核准額度 HK${approved:,.0f}，請調整分期金額。")
            case["project"] = project
            _event(case, "project_updated", actor,
                   f"{len(milestones)} 項里程碑、{len(payments)} 筆付款記錄及 {len(reports)} 份報告。")

        return self._edit(case_id, revision, apply, post_award=True)
