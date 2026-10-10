"""工单查询的持久化、归属隔离以及离线／模型工具链回归。"""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import create_app
from src.tools.registry import build_default_registry
from tests.test_workbench import config


class HandoffTicketToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Path(self.tmp.name) / "tools.db"
        self.registry = build_default_registry(self.database, owner="workspace-a")
        self.created = self.registry.execute("handoff_to_human", {"reason": "需要复核奖学金材料"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_created_ticket_can_be_queried_after_registry_restart(self):
        restarted = build_default_registry(self.database, owner="workspace-a")
        result = restarted.execute("query_handoff_ticket", {"ticket_id": self.created["ticket_id"].lower()})
        self.assertTrue(result["ok"])
        for field in ("ticket_id", "reason", "status", "timestamp"):
            self.assertEqual(result[field], self.created[field])
        self.assertIn("排队中", result["message"])
        self.assertIn("模拟", result["message"])
        self.assertNotIn("owner", result)

    def test_query_reads_current_database_status(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE handoff_tickets SET status='in_progress' WHERE ticket_id=?", (self.created["ticket_id"],))
        result = self.registry.execute("query_handoff_ticket", {"ticket_id": self.created["ticket_id"]})
        self.assertEqual(result["status"], "in_progress")
        self.assertIn("处理中", result["message"])

    def test_other_owner_and_nonexistent_ticket_have_same_error(self):
        other = build_default_registry(self.database, owner="workspace-b")
        foreign = other.execute("query_handoff_ticket", {"ticket_id": self.created["ticket_id"]})
        missing = other.execute("query_handoff_ticket", {"ticket_id": "HF-00000000"})
        self.assertEqual(foreign, missing)
        self.assertEqual(foreign["error"], "not_found")
        self.assertNotIn("reason", foreign)
        self.assertNotIn("timestamp", foreign)

    def test_invalid_ticket_ids_are_rejected(self):
        for identity in ("", "HF-123", "HF-GGGGGGGG", "HF-00000000' OR 1=1--", None, []):
            with self.subTest(identity=identity):
                result = self.registry.execute("query_handoff_ticket", {"ticket_id": identity})
                self.assertEqual(result["error"], "invalid_ticket_id")

    def test_owner_cannot_be_supplied_as_tool_argument(self):
        result = self.registry.execute("query_handoff_ticket", {"ticket_id": self.created["ticket_id"], "owner": "workspace-a"})
        self.assertEqual(result["error"], "unexpected_arguments")
        missing = self.registry.execute("query_handoff_ticket", {})
        self.assertEqual(missing["error"], "missing_arguments")

    def test_query_intent_does_not_swallow_explicit_creation_requests(self):
        from src.tools.handoff import ticket_query_requested
        for question in (
            "请转人工客服，我的工单查询有问题",
            "我想找人工客服，工单查不到",
        ):
            with self.subTest(question=question):
                self.assertFalse(ticket_query_requested(question))
        for question in ("查询人工客服工单状态", "帮我看看工单", "我的工单处理完了吗", "HF-"):
            with self.subTest(question=question):
                self.assertTrue(ticket_query_requested(question))

    def test_old_database_is_preserved_without_exposing_unowned_tickets(self):
        legacy = Path(self.tmp.name) / "legacy.db"
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute("CREATE TABLE handoff_tickets(ticket_id TEXT PRIMARY KEY,reason TEXT NOT NULL,status TEXT NOT NULL,timestamp TEXT NOT NULL)")
            db.execute("INSERT INTO handoff_tickets VALUES ('HF-12345678','旧工单内容','queued','2026-10-01')")
        registry = build_default_registry(legacy, owner="workspace-a")
        old = registry.execute("query_handoff_ticket", {"ticket_id": "HF-12345678"})
        self.assertEqual(old["error"], "not_found")
        new = registry.execute("handoff_to_human", {"reason": "新建模拟工单"})
        self.assertTrue(registry.execute("query_handoff_ticket", {"ticket_id": new["ticket_id"]})["ok"])
        with closing(sqlite3.connect(legacy)) as db, db:
            self.assertEqual(db.execute("SELECT reason FROM handoff_tickets WHERE ticket_id='HF-12345678'").fetchone()[0], "旧工单内容")



if __name__ == "__main__":
    unittest.main()
