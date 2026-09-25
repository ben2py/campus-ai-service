# Agent Baseline v1.0

## 固定能力

- Knowledge：12份模拟知识文档的 RAG 问答
- Tool：`query_application_status` 和 `handoff_to_human`
- Memory：最近6轮，支持指代消解和清空
- Unknown：分数低于0.35时拒答
- Loop Guard：最多3个 Agent 步骤

## 原始结果

- Tool Selection Accuracy：100.00%
- Tool Argument Accuracy：100.00%
- Task Completion Rate：85.00%
- Unknown Handling Rate：100.00%

基线在K05、K06和M02上失败，详见`04_Evaluation/failure_cases.json`。
