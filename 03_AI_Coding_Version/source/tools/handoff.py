"""人工转接 Tool，工单写入 SQLite 便于审计。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def initialize_handoff_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS handoff_tickets (
                ticket_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
            """
        )


def handoff_to_human(database_path: Path, reason: str) -> dict[str, object]:
    reason = reason.strip()
    if len(reason) < 2:
        return {"ok": False, "error": "invalid_reason", "message": "请说明需要人工处理的原因。"}
    ticket_id = f"HF-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO handoff_tickets VALUES (?, ?, ?, ?)",
            (ticket_id, reason[:500], "queued", timestamp),
        )
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "reason": reason[:500],
        "status": "queued",
        "timestamp": timestamp,
        "message": f"已建立人工服务工单 {ticket_id}，当前状态为 queued。",
    }


HANDOFF_SCHEMA = {
    "name": "handoff_to_human",
    "description": "当用户明确要求人工、知识不足或业务需要人工审核时创建转接工单",
    "parameters": {
        "type": "object",
        "properties": {"reason": {"type": "string", "minLength": 2, "maxLength": 500}},
        "required": ["reason"],
        "additionalProperties": False,
    },
}
