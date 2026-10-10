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


class HandoffTicketChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings = {"TESTING": True, "WORKBENCH_DB": root / "workbench.db", "TOOLS_DB": root / "tools.db"}
        self.app = create_app(self.settings)
        self.client = self.app.test_client()
        self.other = self.app.test_client()
        self.identity = self.client.post("/api/conversations", json={}).json["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def chat(self, question, **extra):
        return self.client.post("/api/chat", json={"question": question, "session_id": self.identity, **extra})

    def create_ticket(self):
        result = self.chat("请转人工客服，我需要复核奖学金材料").json
        self.assertEqual(result["tool_name"], "handoff_to_human")
        return result["tool_result"]["ticket_id"]

    def test_offline_query_returns_details_without_creating_another_ticket(self):
        ticket = self.create_ticket()
        result = self.chat(f"查询人工客服工单 {ticket} 的状态").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_result"]["ticket_id"], ticket)
        self.assertEqual(result["tool_result"]["status"], "queued")
        self.assertFalse(result["unknown"])
        self.assertIn("模拟", result["answer"])
        with closing(sqlite3.connect(self.settings["TOOLS_DB"])) as db, db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_tickets").fetchone()[0], 1)

    def test_query_can_use_ticket_from_previous_assistant_turn(self):
        ticket = self.create_ticket()
        result = self.chat("查询刚才那个工单的进度").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_arguments"]["ticket_id"], ticket)

    def test_human_service_progress_followup_queries_existing_ticket(self):
        ticket = self.create_ticket()
        result = self.chat("人工客服处理到哪了").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_arguments"]["ticket_id"], ticket)

    def test_application_words_in_ticket_query_do_not_replace_ticket_id(self):
        ticket = self.create_ticket()
        result = self.chat(f"查询奖学金申请状态的人工工单 {ticket}").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_result"]["ticket_id"], ticket)

    def test_unrelated_chat_does_not_reinitialize_application_records(self):
        with closing(sqlite3.connect(self.settings["TOOLS_DB"])) as db, db:
            db.execute("UPDATE applications SET status='已取消' WHERE application_id='AP2026001'")
        self.chat("你好")
        with closing(sqlite3.connect(self.settings["TOOLS_DB"])) as db:
            row = db.execute("SELECT status FROM applications WHERE application_id='AP2026001'").fetchone()
        self.assertEqual(row[0], "已取消")

    def test_missing_ticket_requests_id_and_next_turn_accepts_it(self):
        missing = self.chat("查询工单状态").json
        self.assertEqual(missing["route"], "request_clarification")
        self.assertIn("工单编号", missing["answer"])
        result = self.chat("HF-12345678").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_result"]["error"], "not_found")

    def test_invalid_id_does_not_fall_back_to_an_older_valid_ticket(self):
        self.create_ticket()
        result = self.chat("查询工单 HF-123 的状态").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_result"]["error"], "invalid_ticket_id")

    def test_incomplete_id_does_not_fall_back_to_previous_ticket(self):
        self.create_ticket()
        result = self.chat("查询工单 HF-").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_result"]["error"], "invalid_ticket_id")

    def test_other_browser_cannot_query_ticket(self):
        ticket = self.create_ticket()
        result = self.other.post("/api/chat", json={"question": f"查询工单 {ticket}"}).json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertEqual(result["tool_result"]["error"], "not_found")
        self.assertNotIn("需要复核奖学金材料", result["answer"])

    def test_same_workspace_can_query_from_another_conversation(self):
        ticket = self.create_ticket()
        result = self.client.post("/api/chat", json={"question": f"查询工单 {ticket}"}).json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertTrue(result["tool_result"]["ok"])

    def test_local_ticket_query_works_when_web_search_is_enabled(self):
        ticket = self.create_ticket()
        with patch("src.workbench.service.search_web", side_effect=AssertionError("本地工单不应发送到搜索服务")):
            result = self.chat(f"查询工单 {ticket}", web=True).json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertTrue(result["tool_result"]["ok"])

    def test_cookie_owner_and_ticket_survive_application_restart(self):
        ticket = self.create_ticket()
        restarted = create_app(self.settings).test_client()
        cookie = self.client.get_cookie("session")
        restarted.set_cookie("session", cookie.value)
        result = restarted.post("/api/chat", json={"question": f"查询工单 {ticket}"}).json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertTrue(result["tool_result"]["ok"])

    def test_model_can_call_query_tool_and_observe_database_details(self):
        ticket = self.create_ticket()
        self.client.post("/api/settings", json=config())
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "ticket-query", "type": "function", "function": {"name": "query_handoff_ticket", "arguments": json.dumps({"ticket_id": ticket})}}]}}]}
        answer = {"choices": [{"message": {"role": "assistant", "content": "模拟工单当前排队中。"}}]}
        with patch("src.llm.providers.post_json", side_effect=[call, answer]) as http:
            response = self.chat(f"查询工单 {ticket}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["trace"][0]["tool"], "query_handoff_ticket")
        self.assertTrue(response.json["trace"][0]["result"]["ok"])
        payload = http.call_args_list[1].args[1]
        observation = json.loads(payload["messages"][-1]["content"])
        self.assertEqual(observation["ticket_id"], ticket)
        self.assertEqual(observation["status"], "queued")

    def test_model_cannot_override_workspace_owner(self):
        ticket = self.create_ticket()
        self.other.post("/api/settings", json=config())
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "ticket-query", "type": "function", "function": {"name": "query_handoff_ticket", "arguments": json.dumps({"ticket_id": ticket})}}]}}]}
        answer = {"choices": [{"message": {"role": "assistant", "content": "当前工作空间没有可查询的该工单。"}}]}
        with patch("src.llm.providers.post_json", side_effect=[call, answer]):
            response = self.other.post("/api/chat", json={"question": f"查询工单 {ticket}"})
        self.assertEqual(response.status_code, 200)
        result = response.json["trace"][0]["result"]
        self.assertEqual(result["error"], "not_found")
        self.assertNotIn("reason", result)

    def test_model_cannot_create_another_ticket_when_user_only_queries(self):
        ticket = self.create_ticket()
        self.client.post("/api/settings", json=config())
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "wrong-tool", "type": "function", "function": {"name": "handoff_to_human", "arguments": '{"reason":"模型误选转人工工具"}'}}]}}]}
        answer = {"choices": [{"message": {"role": "assistant", "content": "当前请求只查询已有工单。"}}]}
        with patch("src.llm.providers.post_json", side_effect=[call, answer]):
            response = self.chat(f"查询人工客服工单 {ticket} 的状态")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["trace"][0]["result"]["ok"])
        with closing(sqlite3.connect(self.settings["TOOLS_DB"])) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM handoff_tickets").fetchone()[0], 1)

    def test_api_created_ticket_can_be_queried_in_offline_mode(self):
        self.client.post("/api/settings", json=config())
        call = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "create-ticket", "type": "function", "function": {"name": "handoff_to_human", "arguments": '{"reason":"奖学金材料需要人工复核"}'}}]}}]}
        answer = {"choices": [{"message": {"role": "assistant", "content": "已创建本地模拟工单。"}}]}
        with patch("src.llm.providers.post_json", side_effect=[call, answer]):
            created = self.chat("请转人工客服").json
        self.assertTrue(created["trace"][0]["result"]["ok"])
        ticket = created["trace"][0]["result"]["ticket_id"]
        self.client.post("/api/settings", json=config(enabled=False))
        result = self.chat(f"查询工单 {ticket}").json
        self.assertEqual(result.get("tool_name"), "query_handoff_ticket")
        self.assertTrue(result["tool_result"]["ok"])



if __name__ == "__main__":
    unittest.main()
