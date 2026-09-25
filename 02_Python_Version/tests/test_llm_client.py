from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from src.llm.client import OpenAICompatibleClient


class _FakeHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"mock grounded answer"}}]}'


class LLMClientTests(unittest.TestCase):
    def test_api_key_is_required(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleClient("https://example.invalid/v1", "", "model")

    @patch("urllib.request.urlopen", return_value=_FakeHTTPResponse())
    def test_openai_compatible_request_is_parsed(self, mocked_open):
        client = OpenAICompatibleClient("https://example.invalid/v1", "test-key", "test-model")
        result = client.chat([{"role": "user", "content": "hello"}])
        self.assertEqual(result.text, "mock grounded answer")
        self.assertEqual(result.model, "test-model")
        request = mocked_open.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        self.assertNotIn(b"test-key", request.data)

    @patch("urllib.request.urlopen", return_value=_FakeHTTPResponse())
    def test_openai_compatible_grounded_answer_builds_context(self, mocked_open):
        client = OpenAICompatibleClient("https://example.invalid/v1", "test-key", "test-model")
        result = client.answer(
            "图书馆周末几点开馆？",
            [{"source_id": "D08", "section": "开放时间", "text": "周末08:30开馆。"}],
        )
        self.assertEqual(result.mode, "api")
        request = mocked_open.call_args.args[0]
        self.assertIn("D08".encode(), request.data)
        self.assertIn("08:30".encode(), request.data)


if __name__ == "__main__":
    unittest.main()
