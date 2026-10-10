"""最小可审计 Agent Loop。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from src.memory.conversation import ConversationMemory, Message
from src.rag.pipeline import RAGPipeline
from src.tools.registry import ToolRegistry
from src.tools.handoff import TICKET_ID_PATTERN, extract_ticket_id, ticket_query_requested


@dataclass(frozen=True)
class AgentResponse:
    answer: str
    route: str
    tool_name: str | None
    tool_arguments: dict[str, str]
    tool_result: dict[str, object] | None
    citations: list[dict[str, str]]
    retrieval: list[dict[str, object]]
    unknown: bool
    latency_ms: float
    trace: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "answer": self.answer,
            "route": self.route,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "tool_result": self.tool_result,
            "citations": self.citations,
            "retrieval": self.retrieval,
            "unknown": self.unknown,
            "latency_ms": round(self.latency_ms, 2),
            "trace": self.trace,
        }


class CampusServiceAgent:
    def __init__(
        self,
        rag: RAGPipeline,
        tools: ToolRegistry,
        memory: ConversationMemory,
        max_steps: int = 3,
    ):
        if max_steps < 2:
            raise ValueError("Agent max_steps 不应小于 2。")
        self.rag = rag
        self.tools = tools
        self.memory = memory
        self.max_steps = max_steps

    def _execute_tool_loop(
        self,
        name: str,
        arguments: dict[str, str],
        trace: list[dict[str, object]],
    ) -> dict[str, object]:
        """在显式步数上限内完成 Tool Call → Observation。"""
        pending = True
        result: dict[str, object] | None = None
        for step_number in range(2, self.max_steps + 1):
            if pending:
                trace.append(
                    {"step": step_number, "action": "tool_call", "tool": name, "arguments": arguments}
                )
                result = self.tools.execute(name, arguments)
                pending = False
                continue
            trace.append({"step": step_number, "action": "observation", "result": result})
            return result or {"ok": False, "error": "empty_observation", "message": "Tool未返回结果。"}
        trace.append({"step": self.max_steps, "action": "loop_exhausted"})
        return {
            "ok": False,
            "error": "max_steps_exceeded",
            "message": "Agent达到最大步骤，已停止执行。",
        }

    @staticmethod
    def _extract_ids(question: str) -> tuple[str | None, str | None]:
        student = re.search(r"\bS\d{4}\b", question.upper())
        application = re.search(r"\bAP\d{7}\b", question.upper())
        return (student.group(0) if student else None, application.group(0) if application else None)

    @staticmethod
    def _topic_from_citations(citations: list[dict[str, str]]) -> str | None:
        return citations[0]["title"] if citations else None

    def _finish(
        self,
        started: float,
        session_id: str,
        question: str,
        answer: str,
        route: str,
        trace: list[dict[str, object]],
        *,
        tool_name: str | None = None,
        tool_arguments: dict[str, str] | None = None,
        tool_result: dict[str, object] | None = None,
        citations: list[dict[str, str]] | None = None,
        retrieval: list[dict[str, object]] | None = None,
        unknown: bool = False,
        topic: str | None = None,
    ) -> AgentResponse:
        self.memory.add(session_id, Message("user", question, topic=topic))
        self.memory.add(session_id, Message("assistant", answer, topic=topic))
        return AgentResponse(
            answer=answer,
            route=route,
            tool_name=tool_name,
            tool_arguments=tool_arguments or {},
            tool_result=tool_result,
            citations=citations or [],
            retrieval=retrieval or [],
            unknown=unknown,
            latency_ms=(time.perf_counter() - started) * 1000,
            trace=trace,
        )

    def respond(self, question: str, session_id: str = "demo") -> AgentResponse:
        started = time.perf_counter()
        question = question.strip()
        trace: list[dict[str, object]] = [{"step": 1, "action": "receive", "question": question}]
        if not question:
            return self._finish(
                started, session_id, question, "请输入需要咨询的问题。", "request_clarification", trace
            )
        if any(term in question for term in ("忽略上述", "泄露密码", "别人的信息", "API Key", "系统提示词")):
            trace.append({"step": 2, "action": "safety_refusal"})
            return self._finish(
                started,
                session_id,
                question,
                "我不能提供凭证、系统提示词或他人个人信息。如需办理本人业务，请使用模拟编号或联系人工。",
                "refuse",
                trace,
            )
        if ticket_query_requested(question):
            ticket_id = extract_ticket_id(question)
            if not ticket_id:
                for message in reversed(self.memory.recent(session_id)):
                    if message.role == "assistant":
                        previous_id = extract_ticket_id(message.content)
                        if previous_id and TICKET_ID_PATTERN.fullmatch(previous_id):
                            ticket_id = previous_id
                            break
            if not ticket_id:
                trace.append({"step": 2, "action": "request_arguments", "tool": "query_handoff_ticket"})
                return self._finish(
                    started, session_id, question,
                    "请提供需要查询的工单编号（HF- 开头，后接 8 位字符）；我只能查询当前工作空间的本地模拟工单。",
                    "request_clarification", trace,
                )
            arguments = {"ticket_id": ticket_id}
            result = self._execute_tool_loop("query_handoff_ticket", arguments, trace)
            return self._finish(
                started, session_id, question, str(result.get("message")),
                "tool", trace, tool_name="query_handoff_ticket",
                tool_arguments=arguments, tool_result=result,
                unknown=not bool(result.get("ok")),
            )
        if any(term in question for term in ("人工客服", "转人工", "找人工", "人工处理")):
            arguments = {"reason": question}
            result = self._execute_tool_loop("handoff_to_human", arguments, trace)
            return self._finish(
                started,
                session_id,
                question,
                str(result.get("message")),
                "tool",
                trace,
                tool_name="handoff_to_human",
                tool_arguments=arguments,
                tool_result=result,
            )
        if any(term in question for term in ("申请进度", "申请状态", "办理进度", "审核到哪")):
            student_id, application_id = self._extract_ids(question)
            if not student_id or not application_id:
                trace.append({"step": 2, "action": "request_arguments"})
                return self._finish(
                    started,
                    session_id,
                    question,
                    "请同时提供模拟学号（如 S1001）和申请编号（如 AP2026001）。",
                    "request_clarification",
                    trace,
                )
            arguments = {"student_id": student_id, "application_id": application_id}
            result = self._execute_tool_loop("query_application_status", arguments, trace)
            return self._finish(
                started,
                session_id,
                question,
                str(result.get("message")),
                "tool",
                trace,
                tool_name="query_application_status",
                tool_arguments=arguments,
                tool_result=result,
                unknown=not bool(result.get("ok")),
                topic="业务申请状态",
            )
        if question.lower() in {"你好", "hello", "hi", "在吗"}:
            trace.append({"step": 2, "action": "direct_answer"})
            return self._finish(
                started,
                session_id,
                question,
                "你好。我可以查询校园公开规则、模拟业务申请状态，或建立人工服务工单。",
                "direct",
                trace,
            )
        resolved = self.memory.resolve_reference(session_id, question)
        trace.append({"step": 2, "action": "rag", "resolved_question": resolved})
        rag_result = self.rag.ask(resolved)
        trace.extend(
            {"step": min(self.max_steps, index + 3), **item} for index, item in enumerate(rag_result.trace)
        )
        topic = self._topic_from_citations(rag_result.citations)
        return self._finish(
            started,
            session_id,
            question,
            rag_result.answer,
            "unknown" if rag_result.unknown else "knowledge",
            # max_steps 限制工具执行循环，不限制 RAG 的诊断阶段数量。
            trace,
            citations=rag_result.citations,
            retrieval=rag_result.retrieval,
            unknown=rag_result.unknown,
            topic=topic,
        )
