"""真实 LLM 的双 Prompt 五题实验；仅在用户主动配置密钥后运行。"""

from __future__ import annotations

import json
import time

from src.config import settings
from src.evaluation.runner import DATASET_PATH, OUTPUT_DIR, build_system, FINAL
from src.llm.client import OpenAICompatibleClient


PROMPTS = {
    "prompt_a": "根据上下文直接回答。",
    "prompt_b": "仅使用上下文；证据不足时拒答；回答末尾附来源。",
}


def main() -> None:
    if not settings.llm_api_key:
        raise SystemExit("未配置 LLM_API_KEY；没有发起外部请求，也没有生成伪造结果。")
    client = OpenAICompatibleClient(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.llm_timeout_seconds,
    )
    _, retriever, _ = build_system(FINAL)
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    questions = [item for item in dataset if item["category"] == "knowledge"][:5]
    rows = []
    for item in questions:
        result = retriever.search(str(item["question"]), top_k=1)[0]
        context = result.to_dict()
        for prompt_name, prompt in PROMPTS.items():
            started = time.perf_counter()
            response = client.answer(str(item["question"]), [context], system_prompt=prompt)
            rows.append(
                {
                    "id": item["id"],
                    "question": item["question"],
                    "prompt": prompt_name,
                    "prompt_text": prompt,
                    "answer": response.text,
                    "model": response.model,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "expected_answer": item["expected_answer"],
                    "expected_source": item["expected_source"],
                }
            )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "prompt_experiment.json").write_text(
        json.dumps(
            {
                "status": "completed_with_external_llm_api",
                "model": settings.llm_model,
                "base_url": settings.llm_base_url,
                "results": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已保存 {len(rows)} 条真实 Prompt 对照结果。")


if __name__ == "__main__":
    main()
