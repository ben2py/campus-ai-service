"""API 模式云端语义检索：接口协议、缓存、回退与工作台集成（全部使用本地 Mock，不发起网络请求）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from src.config import EmbeddingSettings
from src.llm.providers import ProviderError
from src.retrieval.cloud_embedder import CloudEmbedder, EmbeddingCache

# 用"概念词表"模拟语义向量：寝室≈宿舍、饭卡≈校园卡，字面不同但落在同一维度。
CONCEPTS = [
    ("宿舍", "寝室", "门禁", "晚归"),
    ("图书馆", "开馆", "借阅"),
    ("校园卡", "饭卡", "挂失"),
    ("选课", "退补选", "课程"),
    ("奖学金", "补助", "困难"),
    ("考试", "缓考", "期末"),
    ("网络", "上网", "校园网"),
]
DIMENSION = len(CONCEPTS) + 1


def fake_vector(text):
    return [float(sum(text.count(word) for word in group)) for group in CONCEPTS] + [0.2]


class FakeEmbeddingAPI:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def __call__(self, url, payload, headers, timeout=30):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        if self.fail:
            raise ProviderError("无法连接 API 或请求超时，请检查网络与接口地址。")
        return {"data": [{"index": i, "embedding": fake_vector(t)} for i, t in reversed(list(enumerate(payload["input"])))]}


EMBEDDING = EmbeddingSettings(
    api_key="mock-embedding-key", base_url="https://embedding.invalid/v1", model="mock-embedding",
    dimension=DIMENSION, batch_size=10, timeout_seconds=5, semantic_weight=0.7, threshold=0.35,
)
LLM = {"provider": "custom", "protocol": "openai", "base_url": "https://llm.invalid/v1", "model": "mock-llm", "api_key": "mock-llm-key", "enabled": True}


def tool_call(query):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "search_knowledge", "arguments": json.dumps({"query": query}, ensure_ascii=False)}}]}}]}


def final(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class CloudEmbedderTests(unittest.TestCase):
    def embedder(self, **kwargs):
        return CloudEmbedder("https://embedding.invalid/v1", "mock-key", "mock-embedding", DIMENSION, kwargs.pop("batch_size", 10), cache=EmbeddingCache())

    def test_request_shape_batching_normalization_and_cache(self):
        api = FakeEmbeddingAPI()
        embedder = self.embedder(batch_size=3)
        texts = [f"宿舍规则{i}" for i in range(7)] + ["宿舍规则0"]
        with patch("src.retrieval.cloud_embedder.post_json", api):
            vectors = embedder.embed_batch(texts)
            embedder.embed_batch(texts)
        self.assertEqual([len(c["payload"]["input"]) for c in api.calls], [3, 3, 1])  # 去重后 7 条，按 3 条分批
        call = api.calls[0]
        self.assertTrue(call["url"].endswith("/embeddings"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer mock-key")
        self.assertEqual(call["payload"]["dimensions"], DIMENSION)
        self.assertAlmostEqual(sum(v * v for v in vectors[0]), 1.0)
        self.assertEqual(vectors[0], vectors[-1])
        self.assertEqual(embedder.requests, 3)  # 第二次全部命中缓存

    def test_malformed_responses_raise_provider_error(self):
        embedder = self.embedder()
        for response in ({"data": []}, {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}, {"data": [{"index": 0}]}):
            with self.subTest(response=response), patch("src.retrieval.cloud_embedder.post_json", return_value=response):
                with self.assertRaises(ProviderError):
                    embedder.embed("文本")

    def test_endpoint_validation(self):
        for url in ("http://example.com/v1", "https://user:pw@example.com/v1", "https://example.com/v1?key=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                CloudEmbedder(url, "k", "text-embedding-v4")
        with self.assertRaises(ValueError):
            CloudEmbedder("https://example.com/v1", "k", "带空格 的名称")
        CloudEmbedder("http://127.0.0.1:11434/v1", "", "bge-m3")


class SemanticWorkbenchTests(unittest.TestCase):
    def make(self, embedding):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        app = create_app({"TESTING": True, "WORKBENCH_DB": root / "w.db", "TOOLS_DB": root / "t.db", "EMBEDDING": embedding})
        self.addCleanup(self.tmp.cleanup)
        self.client = app.test_client()
        self.client.post("/api/settings", json=LLM)
        self.identity = self.client.post("/api/conversations", json={}).json["id"]

    def ask(self, question, model_query, api):
        llm = [tool_call(model_query), final("宿舍楼开放到 23:30 [D06]。")]
        with patch("src.llm.providers.post_json", side_effect=llm), patch("src.retrieval.cloud_embedder.post_json", api):
            return self.client.post("/api/chat", json={"question": question, "session_id": self.identity})

    def test_api_mode_uses_semantic_vectors_for_paraphrase(self):
        self.make(EMBEDDING)
        api = FakeEmbeddingAPI()
        response = self.ask("几点之前必须回寝室？", "几点之前必须回寝室", api).json
        self.assertEqual(response["retrieval_backend"], "semantic:mock-embedding")
        tool = response["trace"][0]["result"]
        self.assertTrue(tool["ok"])
        self.assertEqual(tool["results"][0]["source_id"], "D06")
        self.assertEqual({c["source_id"] for c in response["citations"]}, {"D06"})
        self.assertEqual(self.client.get("/api/health").json["retrieval"], "semantic:mock-embedding")
        # 第二轮：文本块向量已缓存，只为新的检索词请求一次。
        before = len(api.calls)
        self.ask("那访客呢？", "宿舍访客登记", api)
        self.assertEqual(len(api.calls) - before, 1)

    def test_chunk_id_citations_keep_cards_and_unknown_chunks_are_flagged(self):
        self.make(EMBEDDING)
        llm = [tool_call("几点之前必须回寝室"), final("23:30 关门 [D06-C01]，另见 [D99-C02]。")]
        with patch("src.llm.providers.post_json", side_effect=llm), patch("src.retrieval.cloud_embedder.post_json", FakeEmbeddingAPI()):
            response = self.client.post("/api/chat", json={"question": "几点回寝室", "session_id": self.identity}).json
        self.assertEqual({c["source_id"] for c in response["citations"]}, {"D06"})
        self.assertIn("[D06]", response["answer"])
        self.assertIn("[来源未核验]", response["answer"])
        self.assertNotIn("D99", response["answer"])

    def test_without_embedding_key_api_mode_keeps_hashing(self):
        self.make(None)
        response = self.ask("几点之前必须回寝室？", "几点之前必须回寝室", FakeEmbeddingAPI()).json
        self.assertEqual(response["retrieval_backend"], "hashing")
        self.assertFalse(response["trace"][0]["result"]["ok"])  # 字面检索找不到"寝室"对应的"宿舍"规则
        self.assertEqual(self.client.get("/api/health").json["retrieval"], "hashing")

    def test_embedding_outage_falls_back_explicitly(self):
        self.make(EMBEDDING)
        llm = [tool_call("宿舍几点关门"), final("宿舍 23:30 关门 [D06]。")]
        with patch("src.llm.providers.post_json", side_effect=llm), patch("src.retrieval.cloud_embedder.post_json", FakeEmbeddingAPI(fail=True)):
            response = self.client.post("/api/chat", json={"question": "宿舍几点关门", "session_id": self.identity}, headers={"Accept": "text/event-stream"})
            body = response.get_data(as_text=True)  # SSE 是惰性生成，必须在 patch 生效期间读完
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        self.assertTrue(any("回退字面检索" in e.get("message", "") for e in events))
        done = events[-1]["data"]
        self.assertEqual(done["retrieval_backend"], "hashing")
        self.assertEqual(done["trace"][0]["action"], "retrieval_fallback")
        self.assertTrue(done["trace"][1]["result"]["ok"])

    def test_offline_mode_never_calls_embedding_api(self):
        self.make(EMBEDDING)
        self.client.post("/api/settings", json={**LLM, "enabled": False})
        api = FakeEmbeddingAPI()
        with patch("src.retrieval.cloud_embedder.post_json", api):
            response = self.client.post("/api/chat", json={"question": "图书馆周末几点开馆？", "session_id": self.identity})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.calls, [])
        self.assertEqual(self.client.get("/api/health").json["retrieval"], "hashing")


if __name__ == "__main__":
    unittest.main()
