"""对比哈希向量与云端语义向量的检索效果，并给出 SEMANTIC_THRESHOLD 建议值。

仅在配置了 EMBEDDING_API_KEY（或本机 EMBEDDING_BASE_URL）后运行：
    python -m src.evaluation.semantic_check [--output 路径]

测试集：原冻结题集中的 10 道知识题 + tests/paraphrase_v1.json 中 12 道口语改写题与 6 道库外题。
会把 12 份模拟资料的文本块和上述问题发送到配置的 Embedding 服务。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import embedding_settings, settings
from src.ingestion.chunker import chunk_documents
from src.ingestion.loader import load_documents
from src.llm.providers import ProviderError
from src.retrieval.cloud_embedder import CloudEmbedder
from src.retrieval.embedder import HashingEmbedder
from src.retrieval.retriever import HybridRetriever

ROOT = settings.project_root
DEFAULT_OUTPUT = ROOT.parent / "04_Evaluation" / "semantic_retrieval_check.json"


def load_cases() -> dict[str, list[dict]]:
    frozen = json.loads((ROOT / "tests" / "evaluation_v1.json").read_text(encoding="utf-8"))
    extra = json.loads((ROOT / "tests" / "paraphrase_v1.json").read_text(encoding="utf-8"))
    original = [{"id": c["id"], "question": c["question"], "expected_source": c["expected_source"]} for c in frozen if c["category"] == "knowledge"]
    return {"original": original, "paraphrase": extra["paraphrase"], "out_of_scope": extra["out_of_scope"]}


def evaluate(retriever: HybridRetriever, threshold: float, cases: dict[str, list[dict]]) -> dict:
    report: dict[str, object] = {}
    for group, items in cases.items():
        rows = []
        for case in items:
            results = retriever.search(case["question"], 3)
            top = results[0]
            sources = [r.chunk.document_id for r in results]
            expected = case["expected_source"]
            rows.append({
                "id": case["id"], "question": case["question"], "expected_source": expected,
                "top3_sources": sources, "top_score": round(top.score, 4),
                "hit_at_1": expected is not None and sources[0] == expected,
                "hit_at_3": expected is not None and expected in sources,
                "above_threshold": top.score >= threshold,
            })
        n = len(rows)
        summary = {"count": n}
        if group == "out_of_scope":
            summary["correctly_refused"] = sum(not r["above_threshold"] for r in rows)
        else:
            summary["hit_at_1"] = sum(r["hit_at_1"] for r in rows)
            summary["hit_at_3"] = sum(r["hit_at_3"] for r in rows)
            summary["answered_correctly"] = sum(r["hit_at_1"] and r["above_threshold"] for r in rows)
        report[group] = {"summary": summary, "rows": rows}
    return report


def suggest_threshold(report: dict) -> dict:
    """正例取命中 Top1 的最低分，反例取库外题最高分，建议值为两者中点。"""
    positives = [r["top_score"] for g in ("original", "paraphrase") for r in report[g]["rows"] if r["hit_at_1"]]
    negatives = [r["top_score"] for r in report["out_of_scope"]["rows"]]
    low, high = (min(positives) if positives else None), (max(negatives) if negatives else None)
    value = round((low + high) / 2, 3) if low is not None and high is not None else None
    return {"min_positive_score": low, "max_out_of_scope_score": high, "suggested": value,
            "separable": low is not None and high is not None and low > high}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    e = embedding_settings()
    if e is None:
        raise SystemExit("未配置 EMBEDDING_API_KEY；没有发起外部请求，也没有生成结果。")
    chunks = chunk_documents(load_documents(settings.docs_dir), settings.chunk_size, settings.chunk_overlap)
    cases = load_cases()
    hashing = evaluate(HybridRetriever(chunks, HashingEmbedder(settings.embedding_dimension)), settings.similarity_threshold, cases)
    embedder = CloudEmbedder(e.base_url, e.api_key, e.model, e.dimension, e.batch_size, e.timeout_seconds)
    try:
        semantic = evaluate(HybridRetriever(chunks, embedder, e.semantic_weight), e.threshold, cases)
    except ProviderError as exc:
        raise SystemExit(f"Embedding 接口调用失败：{exc}") from None
    payload = {
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "12 份模拟资料；10 道原题 + 12 道口语改写 + 6 道库外题，均为自构探针，不是独立人工标注。",
        "hashing": {"embedder": "LocalHashingEmbedding-v2", "semantic_weight": 0.62, "threshold": settings.similarity_threshold, **hashing},
        "semantic": {"embedder": e.model, "dimension": e.dimension, "semantic_weight": e.semantic_weight, "threshold": e.threshold,
                     "embedding_requests": embedder.requests, **semantic},
        "semantic_threshold_calibration": suggest_threshold(semantic),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'':12}{'哈希向量':>16}{e.model:>24}")
    for group, label in (("original", "原题"), ("paraphrase", "口语改写")):
        h, s = hashing[group]["summary"], semantic[group]["summary"]
        print(f"{label:8}命中且未拒答 {h['answered_correctly']:>3}/{h['count']:<8}{s['answered_correctly']:>12}/{s['count']}")
    h, s = hashing["out_of_scope"]["summary"], semantic["out_of_scope"]["summary"]
    print(f"{'库外题':8}正确拒答     {h['correctly_refused']:>3}/{h['count']:<8}{s['correctly_refused']:>12}/{s['count']}")
    cal = payload["semantic_threshold_calibration"]
    print(f"\n语义检索：命中正例最低分 {cal['min_positive_score']}，库外题最高分 {cal['max_out_of_scope_score']}，"
          f"建议 SEMANTIC_THRESHOLD={cal['suggested']}（当前 {e.threshold}）"
          + ("" if cal["separable"] else "；正反例分数有重叠，阈值只能折中"))
    print(f"详细结果已写入 {args.output}")


if __name__ == "__main__":
    main()
