"""GrantFlow local operator workstation."""
from __future__ import annotations

import argparse
import io
import ipaddress
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictFloat
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / "src"))

from kkg.auth import AccountDisabled, AuthError, AuthStore, Forbidden, Identity
from kkg.llm import MockLLM, get_llm
from kkg.models import Finding, FindingType, Milestone, ReviewScore, Severity
from kkg.pipeline import Pipeline, load_applications
from kkg.program import Program
from kkg.region import Region
from kkg import script as script_std
from kkg.storage import CaseStore, MissingCase, WorkspaceConflict
from kkg.submission import MAX_FILES, MAX_FILE_BYTES, MAX_METADATA_BYTES, MAX_UPLOAD_BYTES, read_message, read_metadata
from kkg.summarize import reviewer_pack
from kkg.track import Disbursement, PeriodicReport, management_report
from kkg.workspace import Workspace


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Revision(Input):
    revision: int = Field(ge=1, strict=True)


class ScoreRow(Input):
    reviewer: str = Field(max_length=120)
    criteria: dict[str, Any]
    comment: str = Field(default="", max_length=4000)


class ScoresInput(Revision):
    scores: list[ScoreRow] = Field(max_length=100)
    actor: str = "本機操作員"


class ResolutionInput(Revision):
    actor: str
    reason: str


class DecisionInput(ResolutionInput):
    outcome: Literal["approved", "declined"]


class ScriptAuditInput(Input):
    text: str = Field(max_length=200_000)
    region: str = Field(max_length=8)


class LoginInput(Input):
    username: str = Field(max_length=120)
    password: str = Field(max_length=200)


class DisableInput(Input):
    username: str = Field(max_length=120)
    restore: bool = False


class MilestoneRow(Input):
    name: str = Field(max_length=200)
    due: str
    status: Literal["pending", "done"] = "pending"


class ReportRow(Input):
    name: str = Field(max_length=200)
    due: str
    submitted_at: str = ""


class PaymentRow(Input):
    name: str = Field(max_length=200)
    due: str
    amount: StrictFloat
    paid_amount: StrictFloat = 0
    paid_at: str = ""


class ProjectInput(Revision):
    actor: str
    milestones: list[MilestoneRow] = Field(max_length=200)
    periodic_reports: list[ReportRow] = Field(max_length=200)
    disbursements: list[PaymentRow] = Field(max_length=200)


def _programs(directory: Path) -> dict[str, Program]:
    programs = {}
    for config in sorted(directory.glob("*.yaml")):
        program = Program.load(config)
        if program.program_id in programs:
            raise ValueError(f"計劃編號重複：{program.program_id}")
        programs[program.program_id] = program
    if not programs:
        raise ValueError("找不到任何計劃設定。")
    return programs


def _loopback(request: Request) -> bool:
    try:
        return request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


async def _uploaded_file(upload, limit: int) -> bytes:
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise ValueError("請選擇檔案。")
    blob = await upload.read(limit + 1)
    if len(blob) > limit:
        raise ValueError(f"檔案過大：{upload.filename}")
    return blob


async def _intake_form(request: Request) -> tuple[dict, list[tuple[str, bytes]], dict]:
    async with request.form(max_files=MAX_FILES, max_fields=MAX_FILES + 3, max_part_size=MAX_METADATA_BYTES) as form:
        raw = form.get("metadata")
        if not isinstance(raw, str):
            raise ValueError("缺少申請表單。")
        metadata = read_metadata(raw)
        files = []
        for upload in form.getlist("files"):
            payload = await _uploaded_file(upload, MAX_FILE_BYTES)
            files.append((upload.filename, payload))
        options = {"actor": form.get("actor", ""), "revision": form.get("revision"),
                   "removed": form.getlist("removed_files")}
    return metadata, files, options


def _demo_scores() -> dict[str, list[ReviewScore]]:
    data = BASE / "data/demo/reviews/reviews_demo.json"
    reviews: dict[str, list[ReviewScore]] = {}
    for row in json.loads(data.read_text(encoding="utf-8")):
        reviews.setdefault(row["app_id"], []).append(ReviewScore(
            reviewer=row["reviewer"], criteria=row["criteria"], comment=row.get("comment", "")))
    return reviews


def _auth_guard(request: Request) -> Identity:
    """全局認證閘：放行首頁/靜態/登入端點，其餘要求已認證身份（P0-06）。"""
    if request.url.path in {"/", "/api/auth/login"} or request.url.path.startswith("/static"):
        return Identity("anonymous", "匿名", "anonymous")
    auth_store: AuthStore = request.app.state.auth
    try:
        identity = auth_store.identity_from_header(request.headers.get("authorization"))
    except AccountDisabled as exc:
        raise HTTPException(403, str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(401, str(exc),
                            headers={"WWW-Authenticate": 'Basic realm="GrantFlow", charset="UTF-8"'}) from exc
    request.state.identity = identity
    return identity


def _require_identity(request: Request) -> Identity:
    """寫操作與導出強制已認證身份；操作者以服務端身分為準（前端自填 actor 不作授權/審計）。"""
    identity = getattr(request.state, "identity", None) or _auth_guard(request)
    if identity.account_id == "anonymous":
        raise HTTPException(401, "請先登入。",
                            headers={"WWW-Authenticate": 'Basic realm="GrantFlow", charset="UTF-8"'})
    return identity


def create_app(database: str | Path | None = None, configs: Path | None = None) -> FastAPI:
    database = database or os.environ.get("GRANTFLOW_DB") or BASE / "output/grantflow.sqlite3"
    auth_store = AuthStore(Path(database).parent / "auth.sqlite3")
    created = auth_store.ensure_defaults(os.environ.get("GRANTFLOW_BOOTSTRAP_PASSWORD"))
    if "admin" in created:
        print(f"[auth] 初始管理員：admin / {created['admin']}（請立即記錄並更換口令）")
        print(f"[auth] 演示賬號：demo / {created['demo']}（正式環境請停用）")
    workspace = Workspace(CaseStore(database), _programs(configs or BASE / "configs"))
    api = FastAPI(title="GrantFlow", version="0.2.0", docs_url=None, redoc_url=None,
                  dependencies=[Depends(_auth_guard)])
    api.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    api.state.auth = auth_store  # P0-06：guard 與 auth 端點共用身份存儲

    @api.middleware("http")
    async def local_requests(request: Request, call_next):
        if not _loopback(request):
            return JSONResponse({"detail": "工作台只接受本機連線。"}, status_code=403)
        origin = request.headers.get("origin")
        local_origin = f"{request.url.scheme}://{request.url.netloc}"
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin is not None and origin != local_origin:
            return JSONResponse({"detail": "請在工作台內操作。"}, status_code=403)
        length = request.headers.get("content-length")
        if length and (not length.isdecimal() or len(length) > 10 or int(length) > MAX_UPLOAD_BYTES + 256 * 1024):
            return JSONResponse({"detail": "請求大小超出上限。"}, status_code=413)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @api.exception_handler(ValueError)
    async def invalid_input(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @api.exception_handler(WorkspaceConflict)
    async def state_conflict(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @api.exception_handler(MissingCase)
    async def case_missing(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @api.exception_handler(Forbidden)
    async def role_denied(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @api.get("/", response_class=FileResponse)
    def index():
        return FileResponse(BASE / "web/index.html")

    @api.get("/api/programs")
    def programs():
        return [{**asdict(program), "rule_version": program.rule_version} for program in workspace.programs.values()]

    @api.get("/api/cases")
    def cases(limit: int | None = None, offset: int = 0):
        # 不帶分頁參數 → 舊形狀（純陣列，相容既有前端）；帶 limit → 分頁對象。
        result = workspace.list(limit=limit, offset=offset)
        return result["items"] if limit is None else result

    @api.get("/api/cases/{case_id}")
    def case(case_id: str):
        return workspace.get(case_id)[0]

    @api.post("/api/demo")
    def seed():
        return {"added": workspace.seed(load_applications(BASE / "data/demo/applications"), _demo_scores())}

    @api.post("/api/cases", status_code=201)
    async def submit(request: Request, identity: Identity = Depends(_require_identity)):
        metadata, files, _ = await _intake_form(request)
        return await run_in_threadpool(workspace.submit, metadata, files, "web",
                                       actor=identity.display_name)

    @api.post("/api/mail", status_code=201)
    async def mail(request: Request, identity: Identity = Depends(_require_identity)):
        async with request.form(max_files=1, max_fields=0) as form:
            upload = form.get("message")
            raw = await _uploaded_file(upload, MAX_UPLOAD_BYTES)
            if not upload.filename.casefold().endswith(".eml"):
                raise ValueError("請選擇 .eml 郵件檔。")
        metadata, files = await run_in_threadpool(read_message, raw)
        return await run_in_threadpool(workspace.submit, metadata, files, "email",
                                       actor=identity.display_name)

    @api.put("/api/cases/{case_id}/materials")
    async def amend(case_id: str, request: Request, identity: Identity = Depends(_require_identity)):
        metadata, files, options = await _intake_form(request)
        raw_revision = options["revision"]
        if not isinstance(raw_revision, str) or not raw_revision.isdecimal() or len(raw_revision) > 12:
            raise ValueError("缺少有效的個案版本。")
        return await run_in_threadpool(workspace.amend, case_id, int(raw_revision),
                                       identity.display_name, metadata, files, options["removed"])

    @api.put("/api/cases/{case_id}/scores")
    def score(case_id: str, data: ScoresInput, identity: Identity = Depends(_require_identity)):
        return workspace.score(case_id, data.revision, [ReviewScore(**row.model_dump()) for row in data.scores],
                               identity.display_name)

    @api.post("/api/cases/{case_id}/resolution")
    def resolve(case_id: str, data: ResolutionInput, identity: Identity = Depends(_require_identity)):
        return workspace.resolve(case_id, data.revision, identity.display_name, data.reason)

    @api.post("/api/cases/{case_id}/decision")
    def sign(case_id: str, data: DecisionInput, identity: Identity = Depends(_require_identity)):
        return workspace.sign(case_id, data.revision, identity.display_name, data.reason, data.outcome)

    @api.put("/api/cases/{case_id}/project")
    def project(case_id: str, data: ProjectInput, identity: Identity = Depends(_require_identity)):
        return workspace.track(case_id, data.revision, identity.display_name,
                               data.model_dump(exclude={"revision", "actor"}))

    @api.get("/api/cases/{case_id}/pack")
    def pack(case_id: str, identity: Identity = Depends(_require_identity)):
        detail, app = workspace.get(case_id)
        # 計劃定義優先序：簽發時快照（版本鎖定）→ 收件時快照 → 當前配置。
        # 避免同名計劃改版後出現「舊結果配新維度」的歷史包。
        decision = detail["decision"] or {}
        snapshot = decision.get("program_snapshot")
        if snapshot is None:
            with workspace.store.transaction() as connection:
                snapshot = workspace.store.snapshot(connection, case_id)
        if snapshot is not None:
            program = Program.from_snapshot(snapshot)
        else:
            program = workspace.program(app.program)
        with TemporaryDirectory(prefix="grantflow-pack-") as output:
            findings = [Finding(**{**row, "finding_type": FindingType(row["finding_type"]),
                                   "severity": Severity(row["severity"])}) for row in detail["findings"]]
            folder = reviewer_pack(app, program, detail["extracted"], findings, output)
            buffer = io.BytesIO()
            with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
                for path in sorted(folder.glob("*.md")):
                    archive.write(path, path.name)
                archive.writestr("review.json", json.dumps(detail["assessment"], ensure_ascii=False, indent=2))
                if detail["assessment"]["notice"]:
                    archive.writestr("result.md", detail["assessment"]["notice"])
        return Response(buffer.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="reviewer-pack.zip"'})

    @api.get("/api/cases/{case_id}/report", response_class=PlainTextResponse)
    def report(case_id: str, identity: Identity = Depends(_require_identity)):
        detail, app = workspace.get(case_id)
        if detail["assessment"]["status"] != "approved":
            raise HTTPException(409, "請先簽發通過結果。")
        current = detail["project"]
        milestones = [Milestone(**{key: item[key] for key in ("name", "due", "status", "funding_release")})
                      for item in current["milestones"]]
        reports = [PeriodicReport(item["name"], item["due"], item["submitted_at"])
                   for item in current["periodic_reports"]]
        payments = [Disbursement(item["name"], item["due"], item["amount"], item["paid_amount"], item["paid_at"])
                    for item in current["disbursements"]]
        return management_report(app.app_id, app.title, milestones, reports, disbursements=payments)

    # Keep the existing read-only demo API available to earlier integrations.
    regions = Region.load_all(BASE / "configs" / "regions")

    @api.get("/api/program")
    def demo_program():
        program = workspace.program("HK-ACC-2027")
        return {key: value for key, value in asdict(program).items() if key in {"program_id", "name", "deadline", "criteria"}}

    @api.get("/api/regions")
    def demo_regions() -> list[dict]:
        return [{"code": region.code, "name": region.name} for region in regions.values()]

    @api.post("/api/script/audit")
    def script_audit(payload: ScriptAuditInput, identity: Identity = Depends(_require_identity)):
        """字形審計：精確定位不符合地區標準的字形並給出逐字修正建議。"""
        region = regions.get(payload.region.upper())
        if region is None:
            raise HTTPException(404, f"不支援的地區：{payload.region}")
        report = script_std.audit_text(payload.text, region.code)
        return report.as_dict()

    @api.get("/api/script/regions")
    def script_regions(identity: Identity = Depends(_require_identity)):
        """列出可做字形標準化的地區及其目標字形標籤。"""
        return [{"code": code, "target": script_std.target_label(code)}
                for code in script_std.supported_regions()]

    @api.get("/api/run")
    def demo_run(region: str = "HK"):
        active_region = regions.get(region.upper(), regions.get("HK"))
        program = workspace.program("HK-ACC-2027")
        pipe = Pipeline(program, llm=get_llm("auto"), region=active_region)
        apps = load_applications(BASE / "data/demo/applications")
        results = [{"app_id": app.app_id, "title": app.title, "applicant": app.applicant,
                    "findings": [{"type": finding.finding_type.value, "severity": finding.severity.value,
                                  "message": finding.message, "needs_human": finding.needs_human}
                                 for finding in pipe.intake(app)[1]]}
                   for app in apps]
        return {"applications": results,
                "batch_findings": [{"severity": finding.severity.value, "message": finding.message}
                                   for finding in pipe.batch_checks(apps)],
                "audit": json.loads(pipe.audit_json())}

    # ---------- P0-06：身份端點 ----------
    @api.get("/api/auth/me")
    def me(identity: Identity = Depends(_require_identity)):
        return {"account_id": identity.account_id, "display_name": identity.display_name, "role": identity.role}

    @api.post("/api/auth/login")
    def login(data: LoginInput):
        try:
            identity = auth_store.verify_login(data.username, data.password)
        except AuthError as exc:
            raise HTTPException(401, str(exc),
                                headers={"WWW-Authenticate": 'Basic realm="GrantFlow", charset="UTF-8"'}) from exc
        token = auth_store.create_session(identity)
        return {"token": token, "account_id": identity.account_id,
                "display_name": identity.display_name, "role": identity.role}

    @api.get("/api/auth/accounts")
    def auth_accounts(identity: Identity = Depends(_require_identity)):
        if identity.role != "admin":
            raise Forbidden("僅管理員可查看賬戶清單。")
        return auth_store.list_accounts()

    @api.post("/api/auth/disable")
    def auth_disable(data: DisableInput, identity: Identity = Depends(_require_identity)):
        if identity.role != "admin":
            raise Forbidden("僅管理員可停用賬戶。")
        if data.username.casefold() == identity.account_id:
            raise ValueError("不可停用當前登入的管理員賬號。")
        found = auth_store.set_disabled(data.username, disabled=not data.restore)
        if not found:
            raise MissingCase("找不到該賬號。")
        return {"username": data.username.casefold(), "disabled": not data.restore}

    api.mount("/static", StaticFiles(directory=BASE / "web", check_dir=False), name="static")
    return api


app = create_app()


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="啓動 GrantFlow 本機工作台")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False)
