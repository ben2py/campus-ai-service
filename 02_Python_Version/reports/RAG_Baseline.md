# RAG Baseline v1.0

## 冻结配置

| 项目 | 值 |
|---|---:|
| 文档版本 | 12份课程实验自构模拟资料 |
| Chunk Size | 520 |
| Overlap | 0 |
| Embedding | LocalHashingEmbedding-v2，512维 |
| Retrieval | 纯向量，Top-K=3 |
| Similarity Threshold | 0.35 |
| Generator | deterministic-grounded-v1 |

## 原始结果

- Recall@1/3/5：100.00% / 100.00% / 100.00%
- Answer Correctness：85.00%
- Faithfulness：100.00%
- Citation Accuracy：90.00%
- 原始数据：`04_Evaluation/raw_results_baseline.json`

该基线保留了失败结果，没有通过修改冻结测试集提高指标。
