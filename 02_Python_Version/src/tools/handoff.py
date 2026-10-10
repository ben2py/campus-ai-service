"""人工转接 Tool，工单写入 SQLite 便于审计。"""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


LOCAL_DEMO_OWNER = "local-demo"
TICKET_ID_PATTERN = re.compile(r"HF-[0-9A-F]{8}", re.IGNORECASE)


def extract_ticket_id(question: str) -> str | None:
    """保留用户显式提供的编号（含错误格式），交给查询工具校验。"""
    match = re.search(r"(?<![A-Z0-9_-])HF-[A-Z0-9_-]*(?![A-Z0-9_-])", question, re.IGNORECASE)
    return match.group(0).upper() if match else None


def ticket_query_requested(question: str) -> bool:
    """识别工单查询与单独补充编号，避免误触发创建人工工单。"""
    text = question.strip()
    # 明确的转接命令优先，后面的原因可能包含“工单查不到”等词。
    if re.match(
        r"^(?:(?:请|帮我|麻烦|我要|我想|现在|立即|给我)\s*)*"
        r"(?:转人工(?:客服)?|找人工(?:客服)?|联系人工(?:客服)?)(?=$|[，,。！!\s]|创建|帮|处理)",
        text,
    ):
        return False
    if re.fullmatch(r"HF-[A-Z0-9_-]*", text, re.IGNORECASE):
        return True
    progress_words = ("状态", "进度", "进展", "处理到", "处理完", "处理了吗", "受理", "排到")
    if any(word in text for word in ("人工客服", "人工处理", "转人工")) and any(word in text for word in progress_words):
        return True
    return ("工单" in text or "HF-" in text.upper()) and any(
        word in text for word in ("查", "看看", "结果", "怎么样", "跟进", *progress_words)
    )


def initialize_handoff_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            # 串行化建表／升级，避免同时启动多个进程时重复 ALTER TABLE。
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS handoff_tickets (
                    ticket_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    owner TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(handoff_tickets)")}
            if "owner" not in columns:
                # 旧记录保留为 NULL，不能凭工单号擅自归属给任意工作空间。
                connection.execute("ALTER TABLE handoff_tickets ADD COLUMN owner TEXT")


def handoff_to_human(
    database_path: Path, reason: str, *, owner: str = LOCAL_DEMO_OWNER,
) -> dict[str, object]:
    reason = reason.strip()
    if len(reason) < 2:
        return {"ok": False, "error": "invalid_reason", "message": "请说明需要人工处理的原因。"}
    ticket_id = f"HF-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO handoff_tickets(ticket_id,reason,status,timestamp,owner) VALUES (?, ?, ?, ?, ?)",
                (ticket_id, reason[:500], "queued", timestamp, owner),
            )
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "reason": reason[:500],
        "status": "queued",
        "timestamp": timestamp,
        "message": f"已建立人工服务工单 {ticket_id}，当前状态为 queued。",
    }


def query_handoff_ticket(
    database_path: Path, ticket_id: str, *, owner: str = LOCAL_DEMO_OWNER,
) -> dict[str, object]:
    """只读取当前工作空间的模拟工单，不改变状态、不触发转人工。"""
    if not isinstance(ticket_id, str) or not TICKET_ID_PATTERN.fullmatch(ticket_id.strip()):
        return {"ok": False, "error": "invalid_ticket_id", "message": "工单编号格式无效，应为 HF- 加 8 位十六进制字符。"}
    ticket_id = ticket_id.strip().upper()
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT ticket_id,reason,status,timestamp FROM handoff_tickets WHERE ticket_id=? AND owner=?",
            (ticket_id, owner),
        ).fetchone()
    if row is None:
        # 不区分不存在／归属其他用户，避免泄露他人工单是否存在。
        return {"ok": False, "error": "not_found", "message": "当前工作空间未找到该工单，请核对编号并使用创建工单时的浏览器工作空间。"}
    record = dict(row)
    status = {"queued": "排队中", "in_progress": "处理中", "resolved": "已完成", "closed": "已关闭"}.get(record["status"], "未知状态")
    return {
        "ok": True,
        **record,
        "message": (
            f"本地模拟人工工单 {record['ticket_id']}：{status}（{record['status']}）。\n"
            f"创建时间：{record['timestamp']}\n转接原因：{record['reason']}\n"
            "这是本地教学模拟工单，未连接真实客服；状态不会自动更新。"
        ),
    }


HANDOFF_SCHEMA = {
    "name": "handoff_to_human",
    "description": "仅当用户当前明确要求转人工时创建本地模拟工单；查询已有工单用 query_handoff_ticket",
    "parameters": {
        "type": "object",
        "properties": {"reason": {"type": "string", "minLength": 2, "maxLength": 500}},
        "required": ["reason"],
        "additionalProperties": False,
    },
}


QUERY_HANDOFF_SCHEMA = {
    "name": "query_handoff_ticket",
    "description": "凭 HF- 开头的工单编号，查询当前工作空间的本地模拟人工工单状态、创建时间和转接原因；不会创建新工单",
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "minLength": 11, "maxLength": 11, "pattern": "^[Hh][Ff]-[0-9A-Fa-f]{8}$"},
        },
        "required": ["ticket_id"],
        "additionalProperties": False,
    },
}
