"""检索、上下文注入、有据回答、引用和拒答。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.llm.client import DeterministicGroundedClient, LLMResponse
from src.retrieval.retriever import HybridRetriever


@dataclass(frozen=True)
class RAGResponse:
    answer: str
    citations: list[dict[str, str]]
    retrieval: list[dict[str, object]]
    unknown: bool
    model: str
    trace: list[dict[str, object]]


class RAGPipeline:
    def __init__(
        self,
        retriever: HybridRetriever,
        generator: DeterministicGroundedClient,
        top_k: int = 3,
        threshold: float = 0.15,
    ):
        self.retriever = retriever
        self.generator = generator
        self.top_k = top_k
        self.threshold = threshold
        self.logger = logging.getLogger("campus_agent.rag")

    def ask(self, question: str) -> RAGResponse:
        trace: list[dict[str, object]] = [{"step": "input", "question": question}]
        results = self.retriever.search(question, top_k=max(self.top_k, 5))
        trace.append(
            {
                "step": "retrieval",
                "top": [(item.chunk.chunk_id, round(item.score, 4)) for item in results],
            }
        )
        reliable = [item for item in results[: self.top_k] if item.score >= self.threshold]
        if not reliable:
            answer = "当前知识库没有足够可靠的依据。我不会编造答案，建议转人工服务中心核实。"
            trace.append({"step": "unknown", "reason": "score_below_threshold"})
            return RAGResponse(
                answer=answer,
                citations=[],
                retrieval=[item.to_dict() for item in results],
                unknown=True,
                model=self.generator.model,
                trace=trace,
            )
        # 生成阶段仅使用排名最高的证据块，避免相邻主题污染回答。
        # 完整 Top-K 仍保留在 retrieval trace 中用于评测。
        used_evidence = reliable[:1]
        contexts = [item.to_dict() for item in used_evidence]
        response: LLMResponse = self.generator.answer(question, contexts)
        citations: list[dict[str, str]] = []
        for item in used_evidence:
            citation = {
                "source_id": item.chunk.document_id,
                "title": item.chunk.title,
                "section": item.chunk.section,
                "chunk_id": item.chunk.chunk_id,
            }
            if citation not in citations:
                citations.append(citation)
        trace.append({"step": "generation", "model": response.model, "citation_count": len(citations)})
        self.logger.info("question=%r unknown=false citations=%s", question, citations)
        return RAGResponse(
            answer=response.text,
            citations=citations,
            retrieval=[item.to_dict() for item in results],
            unknown=False,
            model=response.model,
            trace=trace,
        )
