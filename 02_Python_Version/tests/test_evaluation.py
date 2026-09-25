from __future__ import annotations

import unittest

from src.evaluation.runner import FINAL, agent_metrics, build_system, embedding_experiment, load_dataset, retrieval_evaluation, run_cases


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset()
        cls.agent, cls.retriever, _ = build_system(FINAL)

    def test_retrieval_reports_overall_and_category_metrics(self):
        result = retrieval_evaluation(self.retriever, self.dataset)
        self.assertEqual(result["metrics"]["recall_at_3"], 100.0)
        self.assertEqual(result["by_category"]["knowledge"]["recall_at_3"], 100.0)

    def test_agent_reports_category_metrics(self):
        rows = run_cases(self.agent, self.dataset, "evaluation-test")
        result = agent_metrics(rows)
        self.assertEqual(result["metrics"]["task_completion_rate"], 100.0)
        self.assertEqual(result["by_category"]["status"]["tool_argument_accuracy"], 100.0)
        self.assertEqual(result["by_category"]["unknown"]["unknown_handling_rate"], 100.0)

    def test_embedding_experiment_uses_twenty_chunks(self):
        result = embedding_experiment()
        self.assertEqual(result["selected_chunk_count"], 20)
        self.assertEqual(result["dimension"], 512)
        self.assertGreater(
            result["similarity_checks"][0]["cosine"],
            result["similarity_checks"][1]["cosine"],
        )


if __name__ == "__main__":
    unittest.main()
