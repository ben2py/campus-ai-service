"""云端语义向量：调用 OpenAI 兼容的 /embeddings 接口（默认阿里云百炼 text-embedding-v4）。

与 HashingEmbedder 接口一致（name / dimension / embed / embed_batch），可直接交给
HybridRetriever 使用。向量按「模型 + 维度 + 文本」缓存在进程内存中，同一文本只请求一次；
密钥只保存在内存，不写入数据库或日志。
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import OrderedDict
from urllib import parse

from src.llm.providers import ProviderError, post_json


class EmbeddingCache:
    """线程安全的有界缓存，超出上限时淘汰最久未使用的向量。"""

    def __init__(self, limit: int = 20000):
        self.limit = limit
        self._items: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> list[float] | None:
        with self._lock:
            vector = self._items.get(key)
            if vector is not None:
                self._items.move_to_end(key)
            return vector

    def put(self, key: str, vector: list[float]) -> None:
        with self._lock:
            self._items[key] = vector
            self._items.move_to_end(key)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


def validate_endpoint(base_url: str, model: str) -> str:
    base = base_url.strip().rstrip("/")
    url = parse.urlsplit(base)
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if not url.hostname or url.username or url.password or url.query or url.fragment or (
        url.scheme != "https" and not (local and url.scheme == "http")
    ):
        raise ValueError("EMBEDDING_BASE_URL 需要 HTTPS（本机服务可用 HTTP），且不能在地址中包含密钥。")
    if not model or len(model) > 160 or not re.fullmatch(r"[\w./:@+-]+", model):
        raise ValueError("EMBEDDING_MODEL 需要填写模型 ID，例如 text-embedding-v4。")
    return base


class CloudEmbedder:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int = 1024,
        batch_size: int = 10,
        timeout: int = 20,
        cache: EmbeddingCache | None = None,
    ):
        self.base_url = validate_endpoint(base_url, model)
        if dimension < 0 or not 1 <= batch_size <= 100:
            raise ValueError("EMBEDDING_API_DIMENSION 不能为负，EMBEDDING_BATCH_SIZE 需在 1–100 之间。")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension  # 0 表示不传 dimensions，使用模型默认维度
        self.batch_size = batch_size
        self.timeout = timeout
        self.cache = cache if cache is not None else EmbeddingCache()
        self.name = f"cloud:{model}"
        self.requests = 0  # 便于测试与排查实际发起的接口调用次数

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{self.dimension}\0{text}".encode("utf-8")).hexdigest()

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float] | None] = [None] * len(texts)
        missing: dict[str, list[int]] = {}
        for index, text in enumerate(texts):
            key = self._key(text)
            cached = self.cache.get(key)
            if cached is not None:
                results[index] = cached
            else:
                missing.setdefault(key, []).append(index)
        pending = list(missing.items())
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start : start + self.batch_size]
            vectors = self._request([texts[indexes[0]] for _, indexes in batch])
            for (key, indexes), vector in zip(batch, vectors):
                self.cache.put(key, vector)
                for index in indexes:
                    results[index] = vector
        return results  # type: ignore[return-value]

    def _request(self, inputs: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self.model, "input": inputs, "encoding_format": "float"}
        if self.dimension:
            payload["dimensions"] = self.dimension
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self.requests += 1
        data = post_json(self.base_url + "/embeddings", payload, headers, timeout=self.timeout)
        items = data.get("data")
        if not isinstance(items, list) or len(items) != len(inputs):
            raise ProviderError("Embedding 接口返回的条数与请求不一致，请检查模型 ID 与接口地址。")
        try:
            ordered = sorted(items, key=lambda item: item.get("index", 0))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError("Embedding 接口响应格式不匹配 OpenAI 兼容格式。") from None
        lengths = {len(vector) for vector in vectors}
        if len(lengths) != 1 or 0 in lengths or (self.dimension and lengths != {self.dimension}):
            raise ProviderError("Embedding 返回的向量维度与 EMBEDDING_API_DIMENSION 不一致。")
        return [self._normalize(vector) for vector in vectors]

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector
