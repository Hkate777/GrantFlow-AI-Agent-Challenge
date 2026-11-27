"""GrantFlow local operator workstation."""
from __future__ import annotations

import argparse
import io
import ipaddress
import json
import os
import sqlite3
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

from kkg import __version__
from kkg.access import CaseNotVisible, grant_key, normalize_grants
from kkg.auth import AccountDisabled, AuthError, AuthStore, Forbidden, Identity
from kkg.guard import review_output, scan_prompt_injection
from kkg.llm import MockLLM, get_llm
from kkg.models import Finding, FindingType, Milestone, ReviewScore, Severity, identity_key
from kkg.output_gate import gate_bundle, gate_directory, gate_text
from kkg.pipeline import Pipeline, load_applications
from kkg.privacy import scan_sensitive
from kkg.program import Program
from kkg.region import Region
from kkg import script as script_std
from kkg.serve import (IPReputation, RateLimiter, RuntimeConfig,
                       apply_security_headers, client_ip, security_event)
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
    reviewer_id: str | None = Field(default=None, max_length=120)
    criteria: dict[str, Any]
    comment: str = Field(default="", max_length=4000)


class ScoresInput(Revision):
    scores: list[ScoreRow] = Field(max_length=100)
    actor: str = "本機操作員"


class OwnScoreInput(Revision):
    """P1-04：評審本人單行評分——評委名以服務端身份為準，不接受前端指定。"""
    criteria: dict[str, Any]
    comment: str = Field(default="", max_length=4000)


class AssignInput(Revision):
    reviewer_id: str = Field(max_length=120)


class ConflictInput(Revision):
    has_conflict: bool
    note: str = Field(default="", max_length=2000)


class ResolutionInput(Revision):
    actor: str
    reason: str


class DecisionInput(ResolutionInput):
    outcome: Literal["approved", "declined"]


class ScriptAuditInput(Input):
    text: str = Field(max_length=200_000)
    region: str = Field(max_length=8)


class SecurityScanInput(Input):
    text: str = Field(max_length=200_000)
    source: str = Field(default="", max_length=200)
    use_llm: bool = False  # 語義端點可選啟用 LLM 專門模型；Mock 如實降級


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
    funding_release: StrictFloat = 0  # 里程碑關聯撥款，供台賬合計校驗


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
    if request.url.path in {"/", "/healthz", "/readyz", "/api/auth/login"} or request.url.path.startswith("/static"):
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


def _require_role(*roles: str):
    """P0-07 角色守衛工廠：admin 可執行一切，其餘角色須在允許清單內。"""
    def dependency(identity: Identity = Depends(_require_identity)) -> Identity:
        if identity.role != "admin" and identity.role not in roles:
            raise Forbidden("此操作需要角色：" + "、".join(roles) +
                            f"（當前角色：{identity.role}）。")
        return identity
    return dependency


# P0-07 角色矩陣（職責分離）：經辦=收件/補件/複議/指派評審；評委=評分；主管=簽發；財務=台帳。
ROLE_SUBMIT = ("operator",)
ROLE_MATERIALS = ("operator",)
ROLE_SCORES = ("reviewer",)
ROLE_RESOLVE = ("operator",)
ROLE_ASSIGN = ("operator",)   # P1-04：評審指派（經辦）
ROLE_SIGN = ("approver",)
ROLE_PROJECT = ("finance",)


def _blind(detail: dict, identity: Identity) -> dict:
    """真盲評投影（見 ``kkg.blind``）：評審在簽發前不見申請人身份與他人評分。

    身份（機構/電郵/團隊/聯絡人/檔名）代號化，衝突判定由服務端規則自動比對；
    簽發後回傳全量。四個讀取/導出端點共用此投影，避免 Blind 只做一半。
    """
    from kkg.blind import blind_projection
    return blind_projection(detail, identity)


def _blind_app(app, detail: dict, identity: Identity):
    """評審包導出的盲評視圖：與詳情投影共用身份詞表，避免導出時泄露身份。"""
    from kkg.blind import blind_application
    return blind_application(app, detail, identity)


def create_app(database: str | Path | None = None, configs: Path | None = None,
               runtime: RuntimeConfig | None = None) -> FastAPI:
    database = database or os.environ.get("GRANTFLOW_DB") or BASE / "output/grantflow.sqlite3"
    runtime = runtime or RuntimeConfig.from_env()
    auth_store = AuthStore(Path(database).parent / "auth.sqlite3")
    created = auth_store.ensure_defaults(os.environ.get("GRANTFLOW_BOOTSTRAP_PASSWORD"),
                                         demo=not runtime.strict_identity)
    if runtime.strict_identity:
        # 即使沿用了本機演示資料庫，遠端部署也一律停用弱口令角色賬號。
        for demo_account in ("demo", "reviewer_demo", "reviewer2_demo", "approver_demo", "finance_demo"):
            auth_store.set_disabled(demo_account, disabled=True)
    if "admin" in created:
        print(f"[auth] 初始管理員：admin / {created['admin']}（請立即記錄並更換口令）")
        if "demo" in created:
            print("[auth] 本機演示賬號已建立（口令 demo）；遠端部署會自動停用。")
    workspace = Workspace(CaseStore(database), _programs(configs or BASE / "configs"))

    # P0-02/03 骨架：個案級授權表持久化在 DB 同目錄的 case_grants.json。
    # 檔案不存在 = 未設定任何授權；case_scope 開關決定是否啟用過濾。
    grants_path = Path(database).parent / "case_grants.json"

    def _load_grants() -> dict[str, set[str]] | None:
        # 檔案不存在 = 未設定白名單 → 回 None 走指派模式；檔案存在（哪怕是空表）
        # 才回空 dict 進入白名單模式。兩者語義不同，不能混為一談。
        try:
            raw = grants_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return normalize_grants(json.loads(raw))
        except ValueError:
            return None

    def _save_grants(data: dict) -> dict[str, set[str]]:
        normalized = normalize_grants(data)
        grants_path.parent.mkdir(parents=True, exist_ok=True)
        grants_path.write_text(
            json.dumps({key: sorted(value) for key, value in normalized.items()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        return normalized

    def _scope_args(identity: Identity) -> tuple[object | None, dict | None]:
        """回傳 (viewer, grants)；case_scope 未啟用時皆 None（舊行為）。"""
        if not runtime.case_scope:
            return None, None
        return identity, _load_grants()

    def _reviewer_account(reviewer_id: str) -> dict:
        """P1-04：指派前對 auth 校驗——賬號須存在、為 reviewer 角色且啓用中。"""
        found = [row for row in auth_store.list_accounts()
                 if row["id"] == (reviewer_id or "").strip().casefold()]
        if not found or found[0]["role"] != "reviewer" or found[0]["disabled"]:
            raise ValueError("找不到可指派的評審賬號（須為啓用中的 reviewer 賬號）。")
        return found[0]

    api = FastAPI(title="GrantFlow", version=__version__, docs_url=None, redoc_url=None,
                  dependencies=[Depends(_auth_guard)])
    api.add_middleware(TrustedHostMiddleware, allowed_hosts=list(runtime.allowed_hosts))
    api.state.auth = auth_store  # P0-06：guard 與 auth 端點共用身份存儲
    api.state.runtime = runtime
    limiter = RateLimiter(runtime.rate_per_minute)
    reputation = IPReputation(runtime.auth_fail_limit, runtime.ban_minutes)
    log_directory = Path(database).parent

    def _guard_response(message: str, status: int, retry: int = 0) -> JSONResponse:
        reply = JSONResponse({"detail": message}, status_code=status)
        if retry:
            reply.headers["Retry-After"] = str(retry)
        apply_security_headers(reply, runtime)
        return reply

    @api.middleware("http")
    async def network_guard(request: Request, call_next):
        path = request.url.path
        address = client_ip(request, trust_proxy=runtime.trust_proxy)
        # 健康檢查供負載均衡器使用：不帶業務資料、不做認證，也不受失敗封鎖影響。
        if path in {"/healthz", "/readyz"}:
            response = await call_next(request)
            apply_security_headers(response, runtime)
            return response
        if not runtime.network_allowed(address) or (not runtime.remote_access and not _loopback(request)):
            security_event(log_directory, "denied-network", address, path)
            return _guard_response("工作台不接受此來源的連線。", 403)
        banned, retry = reputation.banned(address)
        if banned:
            security_event(log_directory, "address-banned", address, path)
            return _guard_response("連線已被暫時封鎖，請稍後重試。", 429, retry)
        allowed, retry = limiter.allow(address)
        if not allowed:
            security_event(log_directory, "rate-limited", address, path)
            return _guard_response("請求過於頻繁，請稍後重試。", 429, retry)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin is not None:
            scheme = (request.headers.get("x-forwarded-proto", request.url.scheme)
                      if runtime.trust_proxy else request.url.scheme)
            host = (request.headers.get("x-forwarded-host") if runtime.trust_proxy else None) \
                or request.headers.get("host", request.url.netloc)
            local_origin = f"{scheme}://{host}"
            if origin != local_origin and origin not in runtime.public_origins:
                security_event(log_directory, "origin-rejected", address, path, origin[:200])
                return _guard_response("請在工作台內操作。", 403)
        length = request.headers.get("content-length")
        if length and (not length.isdecimal() or len(length) > 10 or int(length) > MAX_UPLOAD_BYTES + 256 * 1024):
            security_event(log_directory, "oversized-request", address, path)
            return _guard_response("請求大小超出上限。", 413)
        response = await call_next(request)
        if response.status_code in {401, 403}:
            reputation.record_failure(address)
            security_event(log_directory, "auth-failure", address, path, f"status={response.status_code}")
        elif response.status_code < 400:
            reputation.record_success(address)
        elif response.status_code in {413, 429}:
            security_event(log_directory, "throttled", address, path, f"status={response.status_code}")
        apply_security_headers(response, runtime)
        if path.startswith("/api/"):
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

    @api.exception_handler(CaseNotVisible)
    async def case_not_visible(_request, exc):
        # 個案存在但無權查看：與不存在同樣回 404，避免枚舉。
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @api.exception_handler(Forbidden)
    async def role_denied(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @api.get("/healthz")
    def healthz():
        """存活探測：證明進程在線，供容器協調器與反向代理使用。"""
        return {"status": "ok", "version": __version__}

    @api.get("/readyz")
    def readyz():
        """就緒探測：確認個案資料庫可讀寫，否則回 503 讓流量暫停進入。"""
        try:
            with workspace.store.transaction() as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            return JSONResponse({"status": "unavailable", "detail": str(exc)}, status_code=503)
        return {"status": "ready"}

    @api.get("/", response_class=FileResponse)
    def index():
        return FileResponse(BASE / "web/index.html")

    @api.get("/api/programs")
    def programs():
        return [{**asdict(program), "rule_version": program.rule_version} for program in workspace.programs.values()]

    @api.get("/api/cases")
    def cases(limit: int | None = None, offset: int = 0,
              identity: Identity = Depends(_require_identity)):
        # 不帶分頁參數 → 舊形狀（純陣列，相容既有前端）；帶 limit → 分頁對象。
        viewer, grants = _scope_args(identity)
        result = workspace.list(limit=limit, offset=offset, viewer=viewer, grants=grants)
        if identity.role == "reviewer":
            # 盲評：列表對評審代號化申請人、遮蔽標題身份詞並隱去加權總分。
            from kkg.blind import blind_list_item
            result = {**result,
                      "items": [blind_list_item(item, identity) for item in result["items"]]}
        return result["items"] if limit is None else result

    @api.get("/api/cases/{case_id}")
    def case(case_id: str, identity: Identity = Depends(_require_identity)):
        viewer, grants = _scope_args(identity)
        return _blind(workspace.get(case_id, viewer=viewer, grants=grants)[0], identity)

    @api.post("/api/demo")
    def seed(identity: Identity = Depends(_require_role(*ROLE_SUBMIT))):
        return {"added": workspace.seed(load_applications(BASE / "data/demo/applications"), _demo_scores())}

    @api.post("/api/cases", status_code=201)
    async def submit(request: Request, identity: Identity = Depends(_require_role(*ROLE_SUBMIT))):
        metadata, files, _ = await _intake_form(request)
        return await run_in_threadpool(workspace.submit, metadata, files, "web",
                                       actor=identity.display_name)

    @api.post("/api/mail", status_code=201)
    async def mail(request: Request, identity: Identity = Depends(_require_role(*ROLE_SUBMIT))):
        async with request.form(max_files=1, max_fields=0) as form:
            upload = form.get("message")
            raw = await _uploaded_file(upload, MAX_UPLOAD_BYTES)
            if not upload.filename.casefold().endswith(".eml"):
                raise ValueError("請選擇 .eml 郵件檔。")
        metadata, files = await run_in_threadpool(read_message, raw)
        return await run_in_threadpool(workspace.submit, metadata, files, "email",
                                       actor=identity.display_name)

    @api.put("/api/cases/{case_id}/materials")
    async def amend(case_id: str, request: Request,
                    identity: Identity = Depends(_require_role(*ROLE_MATERIALS))):
        metadata, files, options = await _intake_form(request)
        raw_revision = options["revision"]
        if not isinstance(raw_revision, str) or not raw_revision.isdecimal() or len(raw_revision) > 12:
            raise ValueError("缺少有效的個案版本。")
        viewer, grants = _scope_args(identity)
        return await run_in_threadpool(workspace.amend, case_id, int(raw_revision),
                                       identity.display_name, metadata, files, options["removed"],
                                       viewer=viewer, grants=grants)

    @api.put("/api/cases/{case_id}/scores")
    def score(case_id: str, data: ScoresInput, identity: Identity = Depends(_require_identity)):
        # P1-04 修復（舊 PUT 通道繞過）：批量 PUT 一度僅要求 reviewer 角色，評委只要把
        # HTTP 方法從 POST 換成 PUT，就能略過「指派→利益申報→身份綁定評分」三段式流程，
        # 因 workspace.score() 沒有任何前置校驗。遠端部署收窄為經辦/管理員更正通道；
        # 本機演示保留評委便捷通道（前端與文檔標註「僅限本地演示」）。
        remote = api.state.runtime.strict_identity
        allowed = ("operator", "admin") if remote else ROLE_SCORES
        if identity.role != "admin" and identity.role not in allowed:
            scope = "遠端部署僅供經辦或管理員更正" if remote else "僅供本機演示或管理員更正"
            raise Forbidden(f"批量評分通道{scope}使用；評委請透過「我的評審任務」逐筆提交。")
        # 遠端部署：忽略前端自填姓名/ID，強制綁定登入帳號；本機演示保留舊行為以便重現示範。
        # 注意：此綁定令遠端批量通道實際只接受單行（多行會因同名被拒），這是
        # 「防止管理員偽造多行評委評分」的刻意取捨；需要多行committee評分時，
        # 由多名評委各自走 POST /scores 的指派→申報→身份綁定流程。
        rows: list[ReviewScore] = []
        for row in data.scores:
            payload = row.model_dump()
            payload["reviewer"] = identity.display_name if remote else row.reviewer
            payload["reviewer_id"] = (identity.account_id if remote else
                                      (row.reviewer_id or identity_key(row.reviewer)))
            rows.append(ReviewScore(**payload))
        viewer, grants = _scope_args(identity)
        return workspace.score(case_id, data.revision, rows, identity.display_name,
                               viewer=viewer, grants=grants)

    @api.post("/api/cases/{case_id}/scores")
    def score_own(case_id: str, data: OwnScoreInput,
                  identity: Identity = Depends(_require_role(*ROLE_SCORES))):
        # P1-04：評審本人獨立提交——服務端強制評委名/賬號綁定，按 reviewer_id upsert；
        # 須先獲指派並完成利益申報，申報衝突迴避者拒收。
        viewer, grants = _scope_args(identity)
        return _blind(workspace.score_own(case_id, data.revision, identity.account_id,
                                          identity.display_name, data.criteria, data.comment,
                                          viewer=viewer, grants=grants), identity)

    @api.post("/api/cases/{case_id}/assignments")
    def assign(case_id: str, data: AssignInput, identity: Identity = Depends(_require_role(*ROLE_ASSIGN))):
        account = _reviewer_account(data.reviewer_id)
        viewer, grants = _scope_args(identity)
        return workspace.assign(case_id, data.revision, identity.display_name,
                                account["id"], account["display_name"],
                                viewer=viewer, grants=grants)

    @api.post("/api/cases/{case_id}/conflict")
    def declare_conflict(case_id: str, data: ConflictInput,
                         identity: Identity = Depends(_require_role(*ROLE_SCORES))):
        viewer, grants = _scope_args(identity)
        return _blind(workspace.declare(case_id, data.revision, identity.account_id,
                                        identity.display_name, data.has_conflict, data.note,
                                        viewer=viewer, grants=grants), identity)

    @api.get("/api/reviewers")
    def reviewers(identity: Identity = Depends(_require_role(*ROLE_ASSIGN))):
        """可指派的評審賬號（僅回傳賬號與顯示名；經辦指派面板用）。"""
        return [{"reviewer_id": row["id"], "display_name": row["display_name"]}
                for row in auth_store.list_accounts()
                if row["role"] == "reviewer" and not row["disabled"]]

    @api.post("/api/cases/{case_id}/resolution")
    def resolve(case_id: str, data: ResolutionInput, identity: Identity = Depends(_require_role(*ROLE_RESOLVE))):
        viewer, grants = _scope_args(identity)
        return workspace.resolve(case_id, data.revision, identity.display_name, data.reason,
                                 viewer=viewer, grants=grants)

    @api.post("/api/cases/{case_id}/decision")
    def sign(case_id: str, data: DecisionInput, identity: Identity = Depends(_require_role(*ROLE_SIGN))):
        viewer, grants = _scope_args(identity)
        return workspace.sign(case_id, data.revision, identity.display_name, data.reason, data.outcome,
                              viewer=viewer, grants=grants)

    @api.put("/api/cases/{case_id}/project")
    def project(case_id: str, data: ProjectInput, identity: Identity = Depends(_require_role(*ROLE_PROJECT))):
        viewer, grants = _scope_args(identity)
        return workspace.track(case_id, data.revision, identity.display_name,
                               data.model_dump(exclude={"revision", "actor"}),
                               viewer=viewer, grants=grants)

    @api.get("/api/cases/{case_id}/pack")
    def pack(case_id: str, identity: Identity = Depends(_require_identity)):
        viewer, grants = _scope_args(identity)
        raw_detail, app = workspace.get(case_id, viewer=viewer, grants=grants)
        detail = _blind(raw_detail, identity)  # P1-04：評審導出的評審包同樣盲評
        # 修復：評審包由 Application 物件另起一份生成，須用原始細節同步盲評，
        # 否則機構名／團隊成員會在 summary/one_pager 重現，繞過 detail 的盲評。
        app = _blind_app(app, raw_detail, identity)
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
        findings = [Finding(**{**row, "finding_type": FindingType(row["finding_type"]),
                               "severity": Severity(row["severity"])}) for row in detail["findings"]]
        # 對外物料強制經輸出閘門：先去識別化，再對每個檔案套用 ensure_safe_output。
        bundle = gate_bundle(app, detail["extracted"], findings,
                             notice=detail["assessment"].get("notice") or "")
        with TemporaryDirectory(prefix="grantflow-pack-") as output:
            folder = reviewer_pack(bundle["application"], program, bundle["extracted"],
                                   bundle["findings"], output)
            gate_hits = gate_directory(folder, bundle["redactor"])
            review_json, json_hits = gate_text(json.dumps(detail["assessment"], ensure_ascii=False, indent=2),
                                               bundle["redactor"])
            findings_json, finding_hits = gate_text(json.dumps([asdict(f) for f in bundle["findings"]],
                                                               ensure_ascii=False, indent=2),
                                                    bundle["redactor"])
            buffer = io.BytesIO()
            with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
                for path in sorted(folder.glob("*.md")):
                    archive.write(path, path.name)
                archive.writestr("review.json", review_json)
                archive.writestr("findings.json", findings_json)
                if bundle["notice"]:
                    archive.writestr("result.md", bundle["notice"])
                if gate_hits or json_hits or finding_hits or bundle["gate_hits"]:
                    archive.writestr("gate_report.json", json.dumps(
                        {"files": gate_hits, "review": json_hits, "findings": finding_hits,
                         "notice": bundle["gate_hits"]}, ensure_ascii=False, indent=2))
        return Response(buffer.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="reviewer-pack.zip"'})

    @api.get("/api/cases/{case_id}/traceability")
    def traceability(case_id: str, identity: Identity = Depends(_require_identity)):
        """即時可追溯物料：依據卡、合規分級、確定性評分與 Trace 評測。

        與 CLI `[7/7]` 共用同一套規則式生成邏輯；輸出已去識別化。這是能力
        可達性端點——Web 操作員無需跑命令列即可查閱五項可追溯模組的結果。
        """
        viewer, grants = _scope_args(identity)
        raw_detail, _ = workspace.get(case_id, viewer=viewer, grants=grants)
        result = workspace.traceability(case_id)
        from kkg.blind import blind_traceability
        return blind_traceability(result, identity, raw_detail)

    @api.get("/api/cases/{case_id}/report", response_class=PlainTextResponse)
    def report(case_id: str, identity: Identity = Depends(_require_identity)):
        viewer, grants = _scope_args(identity)
        detail, app = workspace.get(case_id, viewer=viewer, grants=grants)
        if detail["assessment"]["status"] != "approved":
            raise HTTPException(409, "請先簽發通過結果。")
        current = detail["project"]
        milestones = [Milestone(**{key: item[key] for key in ("name", "due", "status", "funding_release")})
                      for item in current["milestones"]]
        reports = [PeriodicReport(item["name"], item["due"], item["submitted_at"])
                   for item in current["periodic_reports"]]
        payments = [Disbursement(item["name"], item["due"], item["amount"], item["paid_amount"], item["paid_at"])
                    for item in current["disbursements"]]
        report_text = management_report(app.app_id, app.title, milestones, reports, disbursements=payments)
        safe_report, _ = gate_text(report_text)
        return safe_report

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

    @api.post("/api/security/scan")
    def security_scan(payload: SecurityScanInput, identity: Identity = Depends(_require_identity)):
        """提示注入偵測 + 輸出敏感值掃描：只回傳類型/位置/已去識別化摘錄。"""
        signals = scan_prompt_injection(payload.text, locator=payload.source)
        hits = scan_sensitive(payload.text)
        return {
            "signals": [s.__dict__ for s in signals],
            "sensitive_hits": hits,
            "safe": not signals and not hits,
            "operator": identity.display_name,
        }

    @api.post("/api/security/scan-semantic")
    def security_scan_semantic(payload: SecurityScanInput,
                               identity: Identity = Depends(_require_identity)):
        """語義注入偵測：小型詞彙語義分類器 + 可選 LLM 專門模型雙軌。

        回傳分數、相近樣本與能力邊界聲明；只作人工覆核提示，不自動攔截或放行。
        可選 ``use_llm`` 時才呼叫模型（需真實模型；MockLLM 如實回 unavailable）。
        """
        from kkg.semantic_guard import (CAPABILITY_STATEMENT, llm_semantic_scan,
                                        scan_semantic)
        lexical = scan_semantic(payload.text).as_dict()
        llm_result = None
        if getattr(payload, "use_llm", False):
            llm = get_llm("auto")
            llm_result = llm_semantic_scan(payload.text, llm).as_dict()
        return {
            "lexical": lexical,
            "llm": llm_result,
            "capability_statement": CAPABILITY_STATEMENT,
            "operator": identity.display_name,
        }

    @api.post("/api/security/review-output")
    def security_review_output(payload: SecurityScanInput,
                               identity: Identity = Depends(_require_identity)):
        """模型輸出安全審查：偵測提示洩漏/執行痕跡/明文憑據，回傳審查結論。"""
        verdict = review_output(payload.text)
        return {"ok": verdict.ok, "reasons": verdict.reasons,
                "summary": verdict.summary, "operator": identity.display_name}

    @api.get("/api/security/vault/status")
    def vault_status(identity: Identity = Depends(_require_identity)):
        """密鑰環狀態：是否存在、已存憑據名稱與遮蔽值；絶不回傳明文。"""
        from kkg.secrets import SecretVault, VaultError
        try:
            vault = SecretVault()
            if not vault.exists():
                return {"exists": False, "keys": [], "note": "尚未建立密鑰環"}
            return {"exists": True, "keys": vault.listing()}
        except VaultError as exc:
            return {"exists": False, "keys": [], "note": str(exc)}

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
    def me(request: Request, identity: Identity = Depends(_require_identity)):
        authorization = request.headers.get("authorization") or ""
        method = "bearer" if authorization.lower().startswith("bearer ") else "basic"
        return {"account_id": identity.account_id, "display_name": identity.display_name,
                "role": identity.role, "auth_method": method,
                "demo_mode": not api.state.runtime.strict_identity,
                "identity_enforced": api.state.runtime.strict_identity}

    @api.post("/api/auth/login")
    def login(data: LoginInput):
        try:
            identity = auth_store.verify_login(data.username, data.password)
        except AuthError as exc:
            raise HTTPException(401, str(exc),
                                headers={"WWW-Authenticate": 'Basic realm="GrantFlow", charset="UTF-8"'}) from exc
        token = auth_store.create_session(identity)
        from kkg.auth import SESSION_TTL_HOURS
        return {"token": token, "account_id": identity.account_id,
                "display_name": identity.display_name, "role": identity.role,
                "expires_in_hours": SESSION_TTL_HOURS}

    @api.post("/api/auth/logout")
    def logout(request: Request, identity: Identity = Depends(_require_identity)):
        """登出：撤銷當前 Bearer 會話，使其即時失效（Basic 無服務端會話可撤）。

        前端持 token 登出後須自行清除本機存儲；此端點只保證服務端 token 失效。
        以 Basic 登入時回 ``session_revoked=False``，提示呼叫方改用 Bearer 登入
        才能享有可登出的會話。
        """
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
            revoked = auth_store.revoke_session(token)
            return {"session_revoked": revoked, "auth_method": "bearer"}
        return {"session_revoked": False, "auth_method": "basic",
                "note": "Basic 憑據由瀏覽器快取，服務端無會話可撤；請關閉瀏覽器或改用 Bearer 登入。"}

    @api.get("/api/auth/accounts")
    def auth_accounts(identity: Identity = Depends(_require_identity)):
        if identity.role != "admin":
            raise Forbidden("僅管理員可查看賬戶清單。")
        return auth_store.list_accounts()

    @api.get("/api/access/grants")
    def access_grants(identity: Identity = Depends(_require_identity)):
        """P0-02/03 骨架：讀取個案級授權表（僅管理員）。"""
        if identity.role != "admin":
            raise Forbidden("僅管理員可查看個案授權表。")
        current = _load_grants() or {}
        return {"enabled": runtime.case_scope,
                "mode": "whitelist" if current else "assignment-or-unset",
                "grants": {key: sorted(value) for key, value in current.items()}}

    @api.put("/api/access/grants")
    def access_grants_update(data: dict, identity: Identity = Depends(_require_identity)):
        """管理員設定 ``{帳號: [個案 ID, ...]}``；須同時啟用 GRANTFLOW_CASE_SCOPE 才生效。"""
        if identity.role != "admin":
            raise Forbidden("僅管理員可設定個案授權表。")
        saved = _save_grants(data.get("grants", data))
        return {"enabled": runtime.case_scope,
                "grants": {key: sorted(value) for key, value in saved.items()},
                "note": "授權表已保存；須以 GRANTFLOW_CASE_SCOPE=1 啟動才會過濾。"
                        if not runtime.case_scope else "授權表已保存並生效。"}

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
    from dataclasses import replace

    runtime: RuntimeConfig = app.state.runtime
    parser = argparse.ArgumentParser(description="啓動 GrantFlow 工作台")
    parser.add_argument("--host", default=runtime.host, help="監聽地址；容器部署用 0.0.0.0")
    parser.add_argument("--port", type=int, default=runtime.port)
    args = parser.parse_args()
    # --host 覆蓋監聽地址時必須同步重建 runtime：否則模組導入時按 loopback
    # 構造的 strict_identity 仍為 False，導致演示弱口令賬號未停用、批量評分
    # 仍走本機寬鬆分支，形成「對外監聽但身份綁定缺席」的漏洞。
    if args.host != runtime.host:
        runtime = replace(runtime, host=args.host)
        app = create_app(runtime=runtime)
    uvicorn.run(app, host=args.host, port=args.port,
                proxy_headers=runtime.trust_proxy, timeout_keep_alive=5)
