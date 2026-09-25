# 一致性审计报告（2026-09-25）

## 结论

- Critical：0项。
- High：0项代码或文档缺失。
- Medium：0项未解决的一致性问题。
- 人工/凭证门槛：2项，已显式标记为未完成，未伪造结果。

## 已核对项目

| 检查项 | 结果 | 证据 |
|---|---|---|
| 文档、主题、Chunk数量 | 一致：12 / 7 / 24 | `retrieval_metrics.json` |
| 测试集规模与分类 | 一致：20题、5类 | `evaluation_v1.json` |
| Retrieval指标 | 一致：Recall@1/3/5均100% | `retrieval_metrics.json` |
| Answer指标 | 一致：三项均100% | `answer_metrics.json` |
| Agent指标 | 一致：四项均100% | `agent_metrics.json` |
| 工程指标 | 一致：平均0.26ms、P95 0.39ms、错误率0% | `engineering_metrics.json` |
| 单元测试 | 一致：30/30通过，无ResourceWarning | `test_results.txt` |
| Python与AI Coding源码快照 | 一致 | 两目录逐文件比对 |
| 外部平台内容 | 无残留 | 全仓关键词检查 |

## 证据门槛

1. 外部 LLM 的2种Prompt×5题真实对照需要提交者提供自己的 API Key；当前状态为 `not_run_without_external_llm_api_key`。
2. 10题人工评价已生成交接表，但 `human_*` 字段必须由提交者本人确认，当前不作为最终人工证据。

如果重新运行 Evaluation，本地毫秒级延迟可能轻微波动，应以新生成的 `engineering_metrics.json` 为准同步报告。
