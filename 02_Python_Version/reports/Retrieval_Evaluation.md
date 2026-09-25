# Retrieval Evaluation

## 配置与数据

- 冻结测试集：20题，其中 Knowledge QA 10题。
- 知识库：12份模拟文档、7类主题、24个Chunk。
- 检索：512维 LocalHashingEmbedding-v2 + 词法混合排序。
- 最终参数：Chunk Size 260、Overlap 40、语义权重0.62、Top-K 3、阈值0.25。

## 结果

| 范围 | Recall@1 | Recall@3 | Recall@5 |
|---|---:|---:|---:|
| 总体 | 100% | 100% | 100% |
| Knowledge | 100% | 100% | 100% |

逐题 Top-5 来源与 Chunk ID 位于 `04_Evaluation/retrieval_metrics.json`，完整回答路径位于 `raw_results_final.json`。该结果只适用于当前自构知识库与冻结测试集。

