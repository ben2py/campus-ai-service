"""可调节的向量或混合检索器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.ingestion.chunker import Chunk

from .embedder import HashingEmbedder, tokenize
from .vector_store import InMemoryVectorStore


@dataclass(frozen=True)
class RetrievalResult:
    chunk: Chunk
    score: float
    vector_score: float
    lexical_score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk.chunk_id,
            "source_id": self.chunk.document_id,
            "title": self.chunk.title,
            "topic": self.chunk.topic,
            "section": self.chunk.section,
            "text": self.chunk.text,
            "source_path": self.chunk.source_path,
            "score": round(self.score, 4),
            "vector_score": round(self.vector_score, 4),
            "lexical_score": round(self.lexical_score, 4),
        }


class HybridRetriever:
    def __init__(
        self,
        chunks: list[Chunk],
        embedder: HashingEmbedder,
        semantic_weight: float = 0.62,
    ):
        if not 0 <= semantic_weight <= 1:
            raise ValueError("semantic_weight 必须在 0 到 1 之间。")
        self.chunks = chunks
        self.embedder = embedder
        self.semantic_weight = semantic_weight
        self.store = InMemoryVectorStore(embedder)
        self.store.add(chunks)
        self._tokens = {
            chunk.chunk_id: set(tokenize(f"{chunk.title}{chunk.section}{chunk.text}"))
            for chunk in chunks
        }
        self.logger = logging.getLogger("campus_agent.retrieval")

    @staticmethod
    def _expand(query: str) -> str:
        synonyms = {
            "一卡通": "校园卡",
            "开门": "开馆 开放",
            "选课": "课程选择 退补选",
            "补助": "奖助学金 困难补助",
            "修理": "报修 维修",
            "wifi": "校园网 无线网络",
        }
        additions = [value for key, value in synonyms.items() if key.lower() in query.lower()]
        return f"{query} {' '.join(additions)}".strip()

    def search(self, query: str, top_k: int = 3) -> list[RetrievalResult]:
        expanded = self._expand(query)
        query_tokens = set(tokenize(expanded))
        vector_scores = dict(
            (chunk.chunk_id, score) for chunk, score in self.store.similarity_search(expanded, len(self.chunks))
        )
        results: list[RetrievalResult] = []
        for chunk in self.chunks:
            vector_score = vector_scores.get(chunk.chunk_id, 0.0)
            lexical = len(query_tokens & self._tokens[chunk.chunk_id]) / max(1, len(query_tokens))
            score = self.semantic_weight * vector_score + (1 - self.semantic_weight) * lexical
            results.append(RetrievalResult(chunk, score, vector_score, lexical))
        selected = sorted(results, key=lambda item: item.score, reverse=True)[:top_k]
        self.logger.info(
            "query=%r top_k=%s results=%s",
            query,
            top_k,
            [(item.chunk.chunk_id, round(item.score, 4)) for item in selected],
        )
        return selected
