from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.tools.registry import build_default_registry


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = build_default_registry(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_three_normal_status_records(self):
        cases = [
            ("S1001", "AP2026001", "审核中"),
            ("S1002", "AP2026002", "已通过"),
            ("S1003", "AP2026003", "待补充材料"),
        ]
        for student_id, application_id, status in cases:
            result = self.registry.execute(
                "query_application_status",
                {"student_id": student_id, "application_id": application_id},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], status)

    def test_two_illegal_parameter_cases(self):
        bad_student = self.registry.execute(
            "query_application_status",
            {"student_id": "1001", "application_id": "AP2026001"},
        )
        bad_application = self.registry.execute(
            "query_application_status",
            {"student_id": "S1001", "application_id": "AP-1"},
        )
        self.assertEqual(bad_student["error"], "invalid_student_id")
        self.assertEqual(bad_application["error"], "invalid_application_id")

    def test_not_found_record(self):
        result = self.registry.execute(
            "query_application_status",
            {"student_id": "S9999", "application_id": "AP2026999"},
        )
        self.assertEqual(result["error"], "not_found")

    def test_handoff_has_required_fields(self):
        result = self.registry.execute("handoff_to_human", {"reason": "需要人工复核奖学金材料"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["ticket_id"].startswith("HF-"))
        self.assertEqual(result["status"], "queued")
        self.assertIn("timestamp", result)

    def test_registry_rejects_missing_and_extra_arguments(self):
        missing = self.registry.execute("query_application_status", {"student_id": "S1001"})
        extra = self.registry.execute("handoff_to_human", {"reason": "转人工", "password": "secret"})
        self.assertEqual(missing["error"], "missing_arguments")
        self.assertEqual(extra["error"], "unexpected_arguments")


if __name__ == "__main__":
    unittest.main()
