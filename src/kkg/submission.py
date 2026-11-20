"""從入口表單、匯出的郵件或舊有資料夾讀取申請個案。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from email import policy
from email.parser import BytesParser
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from .ingest import ingest_dir
from .models import Application, Document


MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 20
MAX_METADATA_BYTES = 64 * 1024
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_APP_ID = re.compile(r"[\w][\w.-]{0,79}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³")}


def safe_filename(name: str) -> str:
    if (not isinstance(name, str) or not name or name.startswith(".") or len(name) > 180 or name.endswith((".", " "))
            or _INVALID_NAME.search(name) or PureWindowsPath(name).stem.split(".")[0].upper() in _RESERVED):
        raise ValueError("附件名稱無效。請使用單一檔名，長度不超過 180 個字元。")
    return name


def safe_application_id(value: str) -> str:
    if not isinstance(value, str) or not _APP_ID.fullmatch(value):
        raise ValueError("申請編號須以文字或數字起首，並只可包含文字、數字、點、底線及連字號。")
    return safe_filename(value)


def _reject_constant(value: str):
    raise ValueError(f"無效的 JSON 數值：{value}。")


def read_metadata(raw: bytes | str) -> dict:
    if (len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))) > MAX_METADATA_BYTES:
        raise ValueError("申請表單大小超出上限。")
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig")
        data = json.loads(raw.lstrip("\ufeff"), parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("申請表單必須是有效的 UTF-8 JSON 檔。") from exc
    if not isinstance(data, dict):
        raise ValueError("申請表單必須是 JSON 物件。")
    return data


def _text(value, label: str, limit: int = 240) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError(f"{label} 必須是長度不超過 {limit} 的文字。")
    return value.strip()


def application_from(metadata: dict, documents: list[Document], *, fallback_id: str,
                     default_program: str = "", require_identity: bool = False) -> Application:
    fields = metadata.get("fields", {})
    team = metadata.get("team", [])
    if not isinstance(fields, dict) or not isinstance(team, list) or len(team) > 100:
        raise ValueError("fields 須為物件，team 須為文字清單。")
    team = [_text(member, "團隊成員") for member in team]
    if any(not member for member in team):
        raise ValueError("團隊成員姓名不可留空。")
    try:
        json.dumps(fields, allow_nan=False)
    except (ValueError, RecursionError) as exc:
        raise ValueError("申請欄位含有無效資料。") from exc
    plan = _text(metadata.get("program_id", metadata.get("program", default_program)), "計劃編號", 64)
    title = _text(metadata.get("title", ""), "項目名稱")
    applicant = _text(metadata.get("applicant", ""), "申請人")
    if require_identity and not all((plan, title, applicant)):
        raise ValueError("請填寫計劃、項目名稱及申請人。")
    email = _text(metadata.get("email", fields.get("email", "")), "聯絡電郵")
    return Application(safe_application_id(metadata.get("app_id") or fallback_id), plan, title, applicant,
                       email, team, fields, documents)


def fingerprint(metadata: dict, attachments: list[tuple[str, bytes]]) -> str:
    receipts = sorted((name, hashlib.sha256(data).hexdigest()) for name, data in attachments)
    try:
        data = json.dumps([metadata, receipts], sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (ValueError, RecursionError) as exc:
        raise ValueError("申請表單含有無效資料。") from exc
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def attachment_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def parse_attachments(attachments: list[tuple[str, bytes]]) -> list[Document]:
    if len(attachments) > MAX_FILES or sum(len(data) for _, data in attachments) > MAX_UPLOAD_BYTES:
        raise ValueError("附件數量或總大小超出上限。")
    names: set[str] = set()
    for name, data in attachments:
        name = safe_filename(name)
        if attachment_key(name) in names:
            raise ValueError(f"附件名稱重複：{name}")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"附件過大：{name}")
        names.add(attachment_key(name))
    with TemporaryDirectory(prefix="grantflow-attachments-") as directory:
        root = Path(directory)
        for name, data in attachments:
            (root / name).write_bytes(data)
        documents = ingest_dir(root)
        for document in documents:
            document.path = document.name
        return documents


def read_message(raw: bytes) -> tuple[dict, list[tuple[str, bytes]]]:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("郵件檔案大小超出上限。")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    metadata: dict | None = None
    files = []
    for part in message.iter_attachments():
        name = part.get_filename()
        if not name:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            raise ValueError("郵件附件必須是單一檔案。")
        if name.casefold() in {"application.json", "meta.json"}:
            if metadata is not None:
                raise ValueError("郵件只能附上一份申請表單。")
            metadata = read_metadata(payload)
        else:
            files.append((safe_filename(name), payload))
        if len(files) > MAX_FILES:
            raise ValueError("附件數量超出上限。")
    if metadata is None:
        raise ValueError("郵件須附上 application.json 或 meta.json 申請表單。")
    return metadata, files


def load_application(folder: Path, default_program: str = "") -> Application:
    source = folder / "meta.json"
    if source.is_file():
        with source.open("rb") as stream:
            metadata = read_metadata(stream.read(MAX_METADATA_BYTES + 1))
    else:
        metadata = {}
    documents = [doc for doc in ingest_dir(folder) if Path(doc.path).resolve() != source.resolve()]
    return application_from(metadata, documents, fallback_id=folder.name, default_program=default_program)
