from __future__ import annotations

import unittest

from app import create_app


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        app.config.update(TESTING=True)
        cls.client = app.test_client()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_chat_rejects_empty_with_clarification(self):
        response = self.client.post("/api/chat", json={"question": "", "session_id": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["route"], "request_clarification")

    def test_index_has_accessible_form(self):
        response = self.client.get("/")
        try:
            body = response.get_data(as_text=True)
            self.assertIn('label for="message-input"', body)
            self.assertIn('aria-live="polite"', body)
        finally:
            response.close()


if __name__ == "__main__":
    unittest.main()
