"""RAG 阶段接口、证据边界与异常分支的回归测试。"""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.ingestion.chunker import Chunk
from src.llm.client import DeterministicGroundedClient, LLMError, LLMResponse
from src.rag.pipeline import RAGPipeline
from src.retrieval.embedder import HashingEmbedder
from src.retrieval.retriever import HybridRetriever, RetrievalResult


class RAGPipelineTests(unittest.TestCase):
    def setUp(self):
        self.chunk = Chunk(
            "D08-C01", "D08", "图书馆服务", "图书馆", "开放时间",
            "图书馆周末08:30开馆，21:00闭馆。", "D08_library.md",
        )
        self.second = replace(
            self.chunk, chunk_id="D08-C02", section="借阅规则",
            text="本科生最多借阅10本图书，借期30天。",
        )
        self.retriever = HybridRetriever(
            [self.chunk, self.second], HashingEmbedder(128),
        )
        self.generator = DeterministicGroundedClient()
        self.pipeline = RAGPipeline(self.retriever, self.generator, threshold=0.25)

    def evidence(self, score=0.8, chunk=None):
        return RetrievalResult(chunk or self.chunk, score, score, score)

    def test_invalid_configuration_is_rejected(self):
        for value in (0, -1, True, 1.5, "3"):
            with self.subTest(top_k=value), self.assertRaises(ValueError):
                RAGPipeline(self.retriever, self.generator, top_k=value)
        for value in (-0.1, 1.1, float("nan"), float("inf"), True, "0.25"):
            with self.subTest(threshold=value), self.assertRaises(ValueError):
                RAGPipeline(self.retriever, self.generator, threshold=value)

    def test_evidence_limit_must_fit_top_k(self):
        for value in (0, -1, 4, True, 1.5):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                RAGPipeline(self.retriever, self.generator, top_k=3, max_evidence=value)

    def test_empty_question_is_rejected_before_retrieval(self):
        with patch.object(self.retriever, "search") as search:
            for question in ("", " \n\t"):
                with self.subTest(question=question), self.assertRaises(ValueError):
                    self.pipeline.ask(question)
            search.assert_not_called()

    def test_non_string_question_is_rejected(self):
        for question in (None, 42, []):
            with self.subTest(question=question), self.assertRaises(TypeError):
                self.pipeline.ask(question)

    def test_retrieve_normalizes_input_and_retains_five_candidates(self):
        chunks = [replace(self.chunk, chunk_id=f"D08-C{i:02d}") for i in range(1, 7)]
        pipeline = RAGPipeline(HybridRetriever(chunks, HashingEmbedder(128)), self.generator)
        self.assertEqual(len(pipeline.retrieve("  图书馆周末几点开馆？  ")), 5)
        result = pipeline.ask("  图书馆周末几点开馆？  ")
        self.assertEqual(result.trace[0]["question"], "图书馆周末几点开馆？")

    def test_evidence_deduplicates_chunks_and_uses_inclusive_threshold(self):
        pipeline = RAGPipeline(self.retriever, self.generator, threshold=0.25, max_evidence=2)
        evidence = pipeline.select_evidence([
            self.evidence(0.8), self.evidence(0.8), self.evidence(0.25, self.second),
        ])
        self.assertEqual([item.chunk.chunk_id for item in evidence], ["D08-C01", "D08-C02"])

    def test_evidence_excludes_nonfinite_scores_and_empty_text(self):
        for item in (
            self.evidence(float("nan")), self.evidence(float("inf")),
            self.evidence(0.24), self.evidence(0.8, replace(self.chunk, text=" \n")),
        ):
            with self.subTest(item=item):
                self.assertEqual(self.pipeline.select_evidence([item]), [])

    def test_evidence_never_promotes_candidates_outside_top_k(self):
        pipeline = RAGPipeline(self.retriever, self.generator, top_k=1, threshold=0.25)
        self.assertEqual(pipeline.select_evidence([
            self.evidence(0.1), self.evidence(0.8, self.second),
        ]), [])

    def test_contexts_preserve_text_and_source_metadata(self):
        contexts = self.pipeline.build_contexts([self.evidence()])
        self.assertEqual(contexts[0]["source_id"], "D08")
        self.assertEqual(contexts[0]["chunk_id"], "D08-C01")
        self.assertEqual(contexts[0]["source_path"], "D08_library.md")
        self.assertEqual(contexts[0]["text"], "图书馆周末08:30开馆，21:00闭馆。")
        self.assertTrue(all(isinstance(value, str) for value in contexts[0].values()))

    def test_citations_deduplicate_chunks_without_merging_sections(self):
        citations = self.pipeline.build_citations([
            self.evidence(), self.evidence(), self.evidence(chunk=self.second),
        ])
        self.assertEqual([item["chunk_id"] for item in citations], ["D08-C01", "D08-C02"])
        self.assertEqual([item["section"] for item in citations], ["开放时间", "借阅规则"])

    def test_default_answer_uses_only_best_evidence_and_records_context(self):
        result = self.pipeline.ask("图书馆周末几点开馆？")
        self.assertFalse(result.unknown)
        self.assertIn("08:30", result.answer)
        self.assertEqual([item["chunk_id"] for item in result.citations], ["D08-C01"])
        context_step = next(item for item in result.trace if item["step"] == "context")
        self.assertEqual(context_step["chunk_ids"], ["D08-C01"])
        self.assertEqual(len(result.retrieval), 2)

    def test_explicit_multi_evidence_answer_can_cover_two_sections(self):
        pipeline = RAGPipeline(self.retriever, self.generator, threshold=0, max_evidence=2)
        result = pipeline.ask("图书馆周末几点开馆，本科生借期多久？")
        self.assertFalse(result.unknown)
        self.assertIn("08:30", result.answer)
        self.assertIn("30天", result.answer)
        self.assertEqual({item["chunk_id"] for item in result.citations}, {"D08-C01", "D08-C02"})

    def test_no_results_refuses_without_calling_generator(self):
        pipeline = RAGPipeline(HybridRetriever([], HashingEmbedder(128)), self.generator)
        with patch.object(self.generator, "answer") as answer:
            result = pipeline.ask("图书馆几点开馆？")
            answer.assert_not_called()
        self.assertTrue(result.unknown)
        self.assertEqual(result.citations, [])
        self.assertEqual(result.trace[-1]["reason"], "no_results")

    def test_low_scores_refuse_but_keep_candidates(self):
        pipeline = RAGPipeline(self.retriever, self.generator, threshold=1)
        result = pipeline.ask("明天一食堂菜单是什么？")
        self.assertTrue(result.unknown)
        self.assertTrue(result.retrieval)
        self.assertEqual(result.citations, [])
        self.assertEqual(result.trace[-1]["reason"], "score_below_threshold")

    def test_deterministic_generator_refusal_is_not_marked_success(self):
        pipeline = RAGPipeline(self.retriever, self.generator, threshold=0)
        result = pipeline.ask("zzz")
        self.assertTrue(result.unknown)
        self.assertEqual(result.citations, [])
        self.assertEqual(result.trace[-1]["reason"], "generator_unknown")

    def test_empty_generation_is_unknown_without_citations(self):
        with patch.object(self.generator, "answer", return_value=LLMResponse(" \n", "test-model", "api")):
            result = self.pipeline.ask("图书馆几点开馆？")
        self.assertTrue(result.unknown)
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.citations, [])
        self.assertEqual(result.trace[-1]["reason"], "empty_generation")

    def test_llm_failure_is_distinct_from_missing_knowledge_and_redacts_details(self):
        with patch.object(self.generator, "answer", side_effect=LLMError("secret-token")):
            with self.assertLogs("campus_agent.rag", level="WARNING") as logs:
                result = self.pipeline.ask("图书馆几点开馆？")
        self.assertTrue(result.unknown)
        self.assertIn("暂时不可用", result.answer)
        self.assertEqual(result.citations, [])
        self.assertTrue(result.retrieval)
        self.assertEqual(result.trace[-1]["reason"], "generator_error")
        self.assertNotIn("secret-token", str(result.trace) + str(logs.output))


if __name__ == "__main__":
    unittest.main()
