from __future__ import annotations

import unittest
from pathlib import Path

from src.ingestion.chunker import chunk_documents
from src.ingestion.loader import load_documents
from src.retrieval.embedder import HashingEmbedder, cosine_similarity
from src.retrieval.retriever import HybridRetriever


ROOT = Path(__file__).resolve().parents[1]


class IngestionRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = load_documents(ROOT / "docs")

    def test_loads_at_least_ten_documents_and_two_topics(self):
        self.assertGreaterEqual(len(self.documents), 10)
        self.assertGreaterEqual(len({document.topic for document in self.documents}), 2)

    def test_chunks_never_mix_documents(self):
        chunks = chunk_documents(self.documents, 160, 20)
        valid_ids = {document.document_id for document in self.documents}
        self.assertTrue(chunks)
        self.assertTrue(all(chunk.document_id in valid_ids for chunk in chunks))

    def test_chunker_rejects_invalid_overlap(self):
        with self.assertRaises(ValueError):
            chunk_documents(self.documents, 100, 100)

    def test_embedding_batch_dimension_and_similarity(self):
        embedder = HashingEmbedder(128)
        vectors = embedder.embed_batch(["图书馆开馆", "图书馆开放时间", "宿舍报修"])
        self.assertEqual([len(vector) for vector in vectors], [128, 128, 128])
        self.assertGreater(cosine_similarity(vectors[0], vectors[1]), cosine_similarity(vectors[0], vectors[2]))

    def test_retrieval_keeps_source_and_score(self):
        chunks = chunk_documents(self.documents, 260, 40)
        retriever = HybridRetriever(chunks, HashingEmbedder(256))
        results = retriever.search("图书馆周末开放时间", top_k=5)
        self.assertEqual(len(results), 5)
        self.assertEqual(results[0].chunk.document_id, "D08")
        self.assertGreaterEqual(results[0].score, results[-1].score)


if __name__ == "__main__":
    unittest.main()
