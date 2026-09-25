"""内存向量库，保留完整来源元数据。"""

from __future__ import annotations

from dataclasses import dataclass

from src.ingestion.chunker import Chunk

from .embedder import HashingEmbedder, cosine_similarity


@dataclass(frozen=True)
class VectorRecord:
    chunk: Chunk
    vector: list[float]


class InMemoryVectorStore:
    def __init__(self, embedder: HashingEmbedder):
        self.embedder = embedder
        self.records: list[VectorRecord] = []

    def add(self, chunks: list[Chunk]) -> None:
        texts = [f"{chunk.title} {chunk.section} {chunk.text}" for chunk in chunks]
        self.records = [
            VectorRecord(chunk=chunk, vector=vector)
            for chunk, vector in zip(chunks, self.embedder.embed_batch(texts))
        ]

    def similarity_search(self, query: str, top_k: int) -> list[tuple[Chunk, float]]:
        query_vector = self.embedder.embed(query)
        scored = [
            (record.chunk, max(0.0, cosine_similarity(query_vector, record.vector)))
            for record in self.records
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]
