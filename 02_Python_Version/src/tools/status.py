"""模拟业务状态查询 Tool。"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path


def initialize_status_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    application_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    application_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submit_time TEXT NOT NULL,
                    review_stage TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.executemany(
                "INSERT OR REPLACE INTO applications VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("AP2026001", "S1001", "国家励志奖学金", "审核中", "2026-09-10 09:12", "学院复审", "2026-09-24 16:30"),
                    ("AP2026002", "S1002", "宿舍调整", "已通过", "2026-09-08 14:05", "学工处完结", "2026-09-23 10:20"),
                    ("AP2026003", "S1003", "校园网开通", "待补充材料", "2026-09-20 11:40", "信息中心初审", "2026-09-25 08:45"),
                    ("AP2026004", "S1001", "困难补助", "已驳回", "2026-09-11 13:50", "学院初审", "2026-09-21 17:10"),
                ],
            )


def query_application_status(
    database_path: Path, student_id: str, application_id: str
) -> dict[str, object]:
    student_id = student_id.strip().upper()
    application_id = application_id.strip().upper()
    if not re.fullmatch(r"S\d{4}", student_id):
        return {"ok": False, "error": "invalid_student_id", "message": "演示学号应为 S 加 4 位数字。"}
    if not re.fullmatch(r"AP\d{7}", application_id):
        return {"ok": False, "error": "invalid_application_id", "message": "申请编号应为 AP 加 7 位数字。"}
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            """
            SELECT application_type, status, submit_time, review_stage, updated_at
            FROM applications WHERE student_id = ? AND application_id = ?
            """,
            (student_id, application_id),
        ).fetchone()
    if not row:
        return {
            "ok": False,
            "error": "not_found",
            "message": "未找到与该演示学号匹配的申请记录。",
        }
    keys = ("application_type", "status", "submit_time", "review_stage", "updated_at")
    data = dict(zip(keys, row))
    return {
        "ok": True,
        "student_id": student_id,
        "application_id": application_id,
        **data,
        "message": (
            f"申请 {application_id} 的业务类型为{data['application_type']}，"
            f"当前状态为“{data['status']}”，处于{data['review_stage']}。"
            f"最后更新时间：{data['updated_at']}。"
        ),
    }


STATUS_SCHEMA = {
    "name": "query_application_status",
    "description": "使用模拟学号和申请编号查询校园业务办理进度",
    "parameters": {
        "type": "object",
        "properties": {
            "student_id": {"type": "string", "pattern": "^S\\d{4}$"},
            "application_id": {"type": "string", "pattern": "^AP\\d{7}$"},
        },
        "required": ["student_id", "application_id"],
        "additionalProperties": False,
    },
}
