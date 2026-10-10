"""校园 RAG：检索 → 证据筛选 → Context 注入 → 生成 → 引用／拒答。

文档加载、分块和 Embedding 由 ingestion/retrieval 层负责，Prompt 与模型
协议由 llm 层负责；本模块编排在线问答过程，不重复实现这些组件。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from numbers import Real

from src.llm.client import GroundedGenerator, LLMError
from src.retrieval.retriever import HybridRetriever, RetrievalResult


@dataclass(frozen=True)
class RAGResponse:
    """统一问答结果；retrieval 保留候选，citations 只列生成所用证据。"""

    answer: str
    citations: list[dict[str, str]]
    retrieval: list[dict[str, object]]
    unknown: bool
    model: str
    trace: list[dict[str, object]]


class RAGPipeline:
    """可分阶段检查的有据问答流程。

    top_k 控制参与筛选的候选数，检索至少保留五个候选供评测。
    max_evidence 控制实际送入生成器的片段数，默认 1，沿用实验报告的
    离线最佳片段策略；多片段实验可显式配置，不能超过 top_k。
    检索分数是相似度而非答案正确概率，阈值需在独立题集上评测。
    """

    UNKNOWN_ANSWER = "当前知识库没有足够可靠的依据。我不会编造答案，建议转人工服务中心核实。"
    GENERATION_ERROR_ANSWER = "回答服务暂时不可用，请稍后重试，或联系人工服务中心核实。"

    def __init__(
        self,
        retriever: HybridRetriever,
        generator: GroundedGenerator,
        top_k: int = 3,
        threshold: float = 0.15,
        *,
        max_evidence: int = 1,
    ):
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k 必须是正整数。")
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, Real)
            or not math.isfinite(threshold)
            or not 0 <= threshold <= 1
        ):
            raise ValueError("threshold 必须是 [0, 1] 内的有限数值。")
        if (
            isinstance(max_evidence, bool)
            or not isinstance(max_evidence, int)
            or not 1 <= max_evidence <= top_k
        ):
            raise ValueError("max_evidence 必须是 [1, top_k] 内的整数。")
        self.retriever = retriever
        self.generator = generator
        self.top_k = top_k
        self.threshold = float(threshold)
        self.max_evidence = max_evidence
        self.logger = logging.getLogger("campus_agent.rag")

    @staticmethod
    def _validate_question(question: str) -> str:
        """拒绝无效输入，并统一去除问题首尾空白。"""
        if not isinstance(question, str):
            raise TypeError("question 必须是字符串。")
        question = question.strip()
        if not question:
            raise ValueError("question 不能为空。")
        return question

    def retrieve(self, question: str) -> list[RetrievalResult]:
        """返回至少 Top-5 的候选窗口（资料不足时返回全部或空列表）。"""
        question = self._validate_question(question)
        return self.retriever.search(question, top_k=max(self.top_k, 5))

    def select_evidence(self, results: list[RetrievalResult]) -> list[RetrievalResult]:
        """按原排名筛选前 top_k：阈值、有效正文、Chunk 去重、证据数量。"""
        evidence: list[RetrievalResult] = []
        seen: set[str] = set()
        for item in results[: self.top_k]:
            if (
                not math.isfinite(item.score)
                or item.score < self.threshold
                or not item.chunk.text.strip()
                or item.chunk.chunk_id in seen
            ):
                continue
            evidence.append(item)
            seen.add(item.chunk.chunk_id)
            if len(evidence) >= self.max_evidence:
                break
        return evidence

    @staticmethod
    def build_contexts(evidence: list[RetrievalResult]) -> list[dict[str, str]]:
        """组织生成器所需的正文和溯源字段；分数留在 retrieval 中分析。

        证据约束和 Prompt 由 GroundedGenerator.answer() 实现。
        """
        return [
            {
                "source_id": item.chunk.document_id,
                "chunk_id": item.chunk.chunk_id,
                "title": item.chunk.title,
                "topic": item.chunk.topic,
                "section": item.chunk.section,
                "text": item.chunk.text,
                "source_path": item.chunk.source_path,
            }
            for item in evidence
        ]

    @staticmethod
    def build_citations(evidence: list[RetrievalResult]) -> list[dict[str, str]]:
        """记录注入生成器的证据，保留同一文档的不同章节／Chunk。

        这是证据来源清单，不是对模型每句话的事实核验。
        """
        citations: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in evidence:
            if item.chunk.chunk_id in seen:
                continue
            seen.add(item.chunk.chunk_id)
            citations.append(
                {
                    "source_id": item.chunk.document_id,
                    "title": item.chunk.title,
                    "section": item.chunk.section,
                    "chunk_id": item.chunk.chunk_id,
                }
            )
        return citations

    def _unknown_response(
        self,
        results: list[RetrievalResult],
        trace: list[dict[str, object]],
        reason: str,
        *,
        answer: str | None = None,
        model: str | None = None,
    ) -> RAGResponse:
        """统一拒答／服务失败结果；保留候选，不把它们当作答案引用。"""
        trace.append({"step": "unknown", "reason": reason})
        self.logger.info("unknown=true reason=%s", reason)
        return RAGResponse(
            answer=answer or self.UNKNOWN_ANSWER,
            citations=[],
            retrieval=[item.to_dict() for item in results],
            unknown=True,
            model=model or self.generator.model,
            trace=trace,
        )

    def ask(self, question: str) -> RAGResponse:
        """完整问答入口，兼容现有 Agent／工作台／评测的调用与返回结构。

        输入错误直接抛出 TypeError／ValueError。已知 LLMError 转为明确
        服务失败，不吞掉其他编程异常，也不会自动创建人工工单。
        """
        question = self._validate_question(question)
        trace: list[dict[str, object]] = [{"step": "input", "question": question}]
        results = self.retrieve(question)
        trace.append(
            {
                "step": "retrieval",
                "top": [(item.chunk.chunk_id, round(item.score, 4)) for item in results],
                "top_k": self.top_k,
                "threshold": self.threshold,
            }
        )
        evidence = self.select_evidence(results)
        if not evidence:
            return self._unknown_response(
                results, trace, "no_results" if not results else "score_below_threshold",
            )

        contexts = self.build_contexts(evidence)
        trace.append(
            {
                "step": "context",
                "chunk_ids": [item.chunk.chunk_id for item in evidence],
                "context_count": len(contexts),
                "max_evidence": self.max_evidence,
            }
        )
        try:
            response = self.generator.answer(question, contexts)
        except LLMError as exc:
            # 异常文本可能包含请求详情或凭证，只记录异常类型。
            trace.append({"step": "generation", "status": "error", "error_type": type(exc).__name__})
            self.logger.warning("generation_failed error_type=%s", type(exc).__name__)
            return self._unknown_response(
                results, trace, "generator_error", answer=self.GENERATION_ERROR_ANSWER,
            )

        answer = response.text.strip()
        trace.append(
            {"step": "generation", "model": response.model, "mode": response.mode,
             "status": "unknown" if response.unknown or not answer else "ok"}
        )
        if response.unknown or not answer:
            return self._unknown_response(
                results, trace, "generator_unknown" if response.unknown else "empty_generation",
                answer=answer, model=response.model,
            )

        citations = self.build_citations(evidence)
        trace[-1]["citation_count"] = len(citations)
        self.logger.info("question=%r unknown=false citations=%s", question, citations)
        return RAGResponse(
            answer=answer,
            citations=citations,
            retrieval=[item.to_dict() for item in results],
            unknown=False,
            model=response.model,
            trace=trace,
        )
