"""LLM 客户端。

OpenAICompatibleClient 通过 HTTP API 调用真实模型。课程离线评测使用
DeterministicGroundedClient，避免将网络波动和商业 API 成本写入固定实验结果。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    mode: str


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 30):
        if not api_key:
            raise ValueError("缺少 LLM_API_KEY，请仅在本地 .env 中配置。")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> LLMResponse:
        payload = json.dumps(
            {"model": self.model, "messages": messages, "temperature": temperature}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return LLMResponse(data["choices"][0]["message"]["content"], self.model, "api")
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM API 调用失败: {exc}") from exc


class DeterministicGroundedClient:
    """可复现的证据摘取器，只根据检索上下文生成回答。"""

    model = "deterministic-grounded-v1"

    @staticmethod
    def _terms(text: str) -> set[str]:
        chinese = [c for c in text if "\u4e00" <= c <= "\u9fff"]
        terms = set(chinese)
        terms.update("".join(chinese[i : i + 2]) for i in range(max(0, len(chinese) - 1)))
        terms.update(re.findall(r"[a-z0-9_-]+", text.lower()))
        return terms

    def answer(self, question: str, contexts: list[dict[str, str]]) -> LLMResponse:
        if not contexts:
            return LLMResponse("当前知识库没有可靠依据，建议转人工服务中心确认。", self.model, "offline")
        query_terms = self._terms(question)
        candidates: list[tuple[float, str, dict[str, str]]] = []
        for context in contexts:
            for sentence in re.split(r"(?<=[。！？；])", context["text"]):
                sentence = sentence.strip()
                if len(sentence) < 5:
                    continue
                terms = self._terms(sentence)
                coverage = len(query_terms & terms) / max(1, len(query_terms))
                candidates.append((coverage, sentence, context))
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected: list[tuple[str, dict[str, str]]] = []
        seen: set[str] = set()
        for score, sentence, context in candidates:
            if score <= 0 or sentence in seen:
                continue
            selected.append((sentence, context))
            seen.add(sentence)
            if len(selected) == 2:
                break
        if not selected:
            return LLMResponse("当前知识库没有可靠依据，建议转人工服务中心确认。", self.model, "offline")
        body = "".join(sentence for sentence, _ in selected)
        citations = []
        for _, context in selected:
            citation = f"[{context['source_id']} {context['section']}]"
            if citation not in citations:
                citations.append(citation)
        return LLMResponse(f"{body}\n\n来源：{' '.join(citations)}", self.model, "offline")
