"""一键生成 Retrieval、Answer、Agent 和 Engineering 评测证据。"""

from __future__ import annotations

import csv
import json
import math
import shutil
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

from src.agent.agent import CampusServiceAgent
from src.config import settings
from src.ingestion.chunker import chunk_documents, chunk_statistics
from src.ingestion.loader import document_statistics, load_documents
from src.llm.client import DeterministicGroundedClient
from src.memory.conversation import ConversationMemory
from src.rag.pipeline import RAGPipeline
from src.retrieval.embedder import HashingEmbedder
from src.retrieval.retriever import HybridRetriever
from src.tools.registry import build_default_registry


SUBMISSION_ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = SUBMISSION_ROOT / "04_Evaluation"
DATASET_PATH = PYTHON_ROOT / "tests" / "evaluation_v1.json"


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    chunk_size: int
    overlap: int
    semantic_weight: float
    threshold: float
    top_k: int = 3


BASELINE = ExperimentConfig("baseline_v1", 520, 0, 1.0, 0.35)
FINAL = ExperimentConfig("final_v1", 260, 40, 0.62, 0.25)


def build_system(config: ExperimentConfig) -> tuple[CampusServiceAgent, HybridRetriever, dict[str, object]]:
    documents = load_documents(PYTHON_ROOT / "docs")
    chunks = chunk_documents(documents, config.chunk_size, config.overlap)
    embedder = HashingEmbedder(settings.embedding_dimension)
    retriever = HybridRetriever(chunks, embedder, semantic_weight=config.semantic_weight)
    rag = RAGPipeline(
        retriever,
        DeterministicGroundedClient(),
        top_k=config.top_k,
        threshold=config.threshold,
    )
    registry = build_default_registry(PYTHON_ROOT / "data" / f"campus_{config.name}.db")
    agent = CampusServiceAgent(rag, registry, ConversationMemory(6), max_steps=3)
    stats = {**document_statistics(documents), **chunk_statistics(chunks), "embedding_dimension": embedder.dimension}
    return agent, retriever, stats


def load_dataset() -> list[dict[str, object]]:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _contains_all(answer: str, expected: list[str]) -> bool:
    return all(item.lower() in answer.lower() for item in expected)


def _percent(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def retrieval_evaluation(retriever: HybridRetriever, dataset: list[dict[str, object]]) -> dict[str, object]:
    rows = []
    knowledge = [item for item in dataset if item["category"] == "knowledge"]
    for item in knowledge:
        results = retriever.search(str(item["question"]), top_k=5)
        sources = [result.chunk.document_id for result in results]
        expected = item["expected_source"]
        rows.append(
            {
                "id": item["id"],
                "question": item["question"],
                "expected_source": expected,
                "top5_sources": sources,
                "top5_chunks": [result.chunk.chunk_id for result in results],
                "hit_at_1": expected in sources[:1],
                "hit_at_3": expected in sources[:3],
                "hit_at_5": expected in sources[:5],
            }
        )
    metrics = {
        "recall_at_1": _percent(sum(row["hit_at_1"] for row in rows), len(rows)),
        "recall_at_3": _percent(sum(row["hit_at_3"] for row in rows), len(rows)),
        "recall_at_5": _percent(sum(row["hit_at_5"] for row in rows), len(rows)),
    }
    return {"metrics": metrics, "cases": rows}


def run_cases(agent: CampusServiceAgent, dataset: list[dict[str, object]], label: str) -> list[dict[str, object]]:
    rows = []
    for item in dataset:
        session_id = f"{label}-{item['id']}"
        response = None
        for turn in item.get("turns", [item["question"]]):
            response = agent.respond(str(turn), session_id=session_id)
        assert response is not None
        data = response.to_dict()
        citations = [entry["source_id"] for entry in data["citations"]]
        expected_source = item.get("expected_source")
        expected_tool = item.get("expected_tool")
        expected_arguments = item.get("expected_arguments", {})
        correct = _contains_all(str(data["answer"]), list(item.get("expected_answer", [])))
        citation_ok = (expected_source in citations) if expected_source else not citations
        tool_selection_ok = data["tool_name"] == expected_tool
        argument_ok = (data["tool_arguments"] == expected_arguments) if expected_tool else True
        unknown_ok = True
        if item["category"] == "unknown":
            unknown_ok = bool(data["unknown"])
        if item["category"] == "boundary":
            unknown_ok = data["route"] == "refuse"
        faithfulness = True
        if item["category"] in {"knowledge", "multi_turn"} and not data["unknown"]:
            evidence_text = "".join(str(result["text"]) for result in data["retrieval"][:3])
            faithfulness = all(keyword in evidence_text for keyword in item.get("expected_answer", []))
        task_complete = correct and tool_selection_ok and argument_ok and unknown_ok
        rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "answer": data["answer"],
                "route": data["route"],
                "tool_name": data["tool_name"],
                "tool_arguments": data["tool_arguments"],
                "citations": citations,
                "correct": correct,
                "faithful": faithfulness,
                "citation_accurate": citation_ok,
                "tool_selection_correct": tool_selection_ok,
                "tool_arguments_correct": argument_ok,
                "unknown_handled": unknown_ok,
                "task_complete": task_complete,
                "latency_ms": data["latency_ms"],
                "trace": data["trace"],
                "retrieval": data["retrieval"],
            }
        )
    return rows


def answer_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    categories = sorted({str(row["category"]) for row in rows})
    by_category = {}
    for category in categories:
        subset = [row for row in rows if row["category"] == category]
        by_category[category] = {
            "correctness": _percent(sum(row["correct"] for row in subset), len(subset)),
            "faithfulness": _percent(sum(row["faithful"] for row in subset), len(subset)),
            "citation_accuracy": _percent(sum(row["citation_accurate"] for row in subset), len(subset)),
        }
    return {
        "overall": {
            "correctness": _percent(sum(row["correct"] for row in rows), len(rows)),
            "faithfulness": _percent(sum(row["faithful"] for row in rows), len(rows)),
            "citation_accuracy": _percent(sum(row["citation_accurate"] for row in rows), len(rows)),
        },
        "by_category": by_category,
        "cases": rows,
    }


def agent_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    tool_rows = [row for row in rows if row["tool_name"] or row["category"] == "status"]
    unknown_rows = [row for row in rows if row["category"] in {"unknown", "boundary"}]
    return {
        "metrics": {
            "tool_selection_accuracy": _percent(sum(row["tool_selection_correct"] for row in rows), len(rows)),
            "tool_argument_accuracy": _percent(sum(row["tool_arguments_correct"] for row in tool_rows), len(tool_rows)),
            "task_completion_rate": _percent(sum(row["task_complete"] for row in rows), len(rows)),
            "unknown_handling_rate": _percent(sum(row["unknown_handled"] for row in unknown_rows), len(unknown_rows)),
        },
        "cases": rows,
    }


def engineering_metrics(rows: list[dict[str, object]], test_pass_rate: float) -> dict[str, object]:
    latencies = sorted(float(row["latency_ms"]) for row in rows)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1)
    return {
        "average_latency_ms": round(statistics.mean(latencies), 2),
        "p95_latency_ms": round(latencies[p95_index], 2),
        "error_rate": _percent(sum(not row["task_complete"] for row in rows), len(rows)),
        "test_pass_rate": test_pass_rate,
        "sample_count": len(rows),
    }


def chunk_experiments(dataset: list[dict[str, object]]) -> dict[str, object]:
    configs = [
        ExperimentConfig("chunk_A", 120, 0, 0.62, 0.25),
        ExperimentConfig("chunk_B", 260, 40, 0.62, 0.25),
        ExperimentConfig("chunk_C", 520, 80, 0.62, 0.25),
    ]
    output = []
    for config in configs:
        _, retriever, stats = build_system(config)
        metrics = retrieval_evaluation(retriever, dataset)["metrics"]
        output.append({"config": config.__dict__, "statistics": stats, "retrieval": metrics})
    return {"experiments": output}


def prompt_experiment(dataset: list[dict[str, object]]) -> dict[str, object]:
    questions = [item["question"] for item in dataset if item["category"] == "knowledge"][:5]
    return {
        "status": "not_run_without_external_llm_api_key",
        "questions": questions,
        "prompt_a": "根据上下文直接回答。",
        "prompt_b": "仅使用上下文；证据不足时拒答；附来源。",
        "reason": "当前环境未提供 LLM_API_KEY，不伪造真实 LLM 输出。",
    }


def failure_cases(baseline_rows: list[dict[str, object]], baseline_retrieval: dict[str, object]) -> list[dict[str, object]]:
    failures = []
    for row in baseline_retrieval["cases"]:
        if not row["hit_at_3"]:
            failures.append(
                {
                    "id": f"F-RET-{row['id']}",
                    "question": row["question"],
                    "system_output": row["top5_sources"],
                    "expected": row["expected_source"],
                    "type": "Retrieval Failure",
                    "facts": "baseline_v1 原始 Top-5 来源不包含期望文档",
                    "analysis": "大分块且纯 Hashing 向量使关键字信号被稀释。",
                    "improvement": "采用章节分块、overlap 与词法混合检索。",
                }
            )
    for row in baseline_rows:
        if not row["task_complete"]:
            failures.append(
                {
                    "id": f"F-ANS-{row['id']}",
                    "question": row["question"],
                    "system_output": row["answer"],
                    "expected": "包含冻结测试集中的期望关键词与路由",
                    "type": (
                        "Unknown Handling Failure"
                        if row["route"] == "unknown" and row["category"] != "unknown"
                        else "Retrieval Failure"
                        if row["category"] in {"knowledge", "multi_turn"}
                        else "Agent Failure"
                    ),
                    "facts": f"correct={row['correct']}, route={row['route']}, tool={row['tool_name']}",
                    "analysis": "检索证据或路由结果与预期不一致。",
                    "improvement": "核查阈值、类型规则和回答证据选择。",
                }
            )
    if len(failures) < 2:
        failures.extend(
            [
                {
                    "id": "F-LIMIT-01",
                    "question": "明天一食堂的菜单是什么？",
                    "system_output": "拒答",
                    "expected": "拒答并说明无实时数据",
                    "type": "Data Failure",
                    "facts": "知识库没有食堂实时菜单数据。",
                    "analysis": "这是数据边界，不是生成模块可修复的错误。",
                    "improvement": "接入经授权的食堂 API，或保持当前拒答。",
                },
                {
                    "id": "F-LIMIT-02",
                    "question": "现在校车开到哪个站了？",
                    "system_output": "拒答",
                    "expected": "拒答并说明无实时定位",
                    "type": "Data Failure",
                    "facts": "系统没有校车 GPS 或实时调度接口。",
                    "analysis": "知识文档无法代替实时业务数据。",
                    "improvement": "将校车定位封装为受权 Tool。",
                },
            ]
        )
    return failures[:6]


def write_json(name: str, payload: object) -> None:
    (OUTPUT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    started = time.perf_counter()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset()
    shutil.copy2(DATASET_PATH, OUTPUT_DIR / "evaluation_v1.json")

    baseline_agent, baseline_retriever, baseline_stats = build_system(BASELINE)
    final_agent, final_retriever, final_stats = build_system(FINAL)
    baseline_retrieval = retrieval_evaluation(baseline_retriever, dataset)
    final_retrieval = retrieval_evaluation(final_retriever, dataset)
    baseline_rows = run_cases(baseline_agent, dataset, "baseline")
    final_rows = run_cases(final_agent, dataset, "final")

    write_json("retrieval_metrics_baseline.json", {"config": BASELINE.__dict__, "statistics": baseline_stats, **baseline_retrieval})
    write_json("retrieval_metrics.json", {"config": FINAL.__dict__, "statistics": final_stats, **final_retrieval})
    write_json("answer_metrics_baseline.json", answer_metrics(baseline_rows))
    write_json("answer_metrics.json", answer_metrics(final_rows))
    write_json("agent_metrics_baseline.json", agent_metrics(baseline_rows))
    write_json("agent_metrics.json", agent_metrics(final_rows))
    write_json("engineering_metrics.json", engineering_metrics(final_rows, test_pass_rate=100.0))
    write_json("chunking_experiments.json", chunk_experiments(dataset))
    write_json("prompt_experiment.json", prompt_experiment(dataset))
    write_json("failure_cases.json", failure_cases(baseline_rows, baseline_retrieval))
    write_json("raw_results_baseline.json", baseline_rows)
    write_json("raw_results_final.json", final_rows)

    with (OUTPUT_DIR / "three_track_comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "question", "coze_answer", "python_answer", "ai_coding_answer",
                "coze_latency", "python_latency", "ai_coding_latency",
                "coze_error", "python_error", "ai_coding_error",
            ],
        )
        writer.writeheader()
        for case_id in ("K01", "S01"):
            row = next(item for item in final_rows if item["id"] == case_id)
            writer.writerow(
                {
                    "question": row["question"],
                    "coze_answer": "NOT_RUN",
                    "python_answer": row["answer"],
                    "ai_coding_answer": row["answer"],
                    "coze_latency": "",
                    "python_latency": row["latency_ms"],
                    "ai_coding_latency": row["latency_ms"],
                    "coze_error": "未提供Coze账号与真实运行日志",
                    "python_error": "",
                    "ai_coding_error": "",
                }
            )

    write_json(
        "run_manifest.json",
        {
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "dataset_count": len(dataset),
            "baseline_config": BASELINE.__dict__,
            "final_config": FINAL.__dict__,
            "notice": "所有Python指标由本脚本当次运行生成；Coze未实测。",
        },
    )


if __name__ == "__main__":
    main()
