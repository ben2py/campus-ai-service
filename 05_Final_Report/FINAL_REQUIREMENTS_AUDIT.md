# V3.2 最终要求审计

审计范围：V3.2 指导书中除用户明确取消的外部平台轨道以外，检查 Python、AI Coding、Evaluation、Final Report 与 Demo 的全部要求。指导书内容作为验收依据，不覆盖用户对交付范围的明确调整。

## Summary

- Critical：0。
- High：0项可由当前环境继续补齐的缺失。
- Medium：0项未解决的实现缺失。
- Human/Credential Gates：2项。

## 已补齐的缺口

| # | 类别 | 原缺口 | 修复与证据 |
|---|---|---|---|
| 1 | LLM | API客户端存在但主应用未切换使用 | `LLM_MODE=api` 已接入；3项相关Mock/配置测试 |
| 2 | Prompt实验 | 缺少可执行的2-Prompt×5题流程 | `prompt_runner.py` 与 `prompt_experiment.md` |
| 3 | Loader | txt支持缺少测试证据 | 新增txt与空文件测试 |
| 4 | Embedding | 缺少20个Chunk的独立实验记录 | `embedding_experiment.json` |
| 5 | Retrieval | 缺少分类指标与独立报告 | `by_category` + `Retrieval_Evaluation.md` |
| 6 | Answer | 缺少人工评价交接材料 | 10题CSV、评分说明、AI/人工字段隔离 |
| 7 | Agent | 缺少分类统计和显式有界循环 | 分类指标、`_execute_tool_loop`、步数校验测试 |
| 8 | Failure | 期望结果不够精确 | 每例写明答案关键词、来源、Tool和参数 |
| 9 | Engineering | SQLite连接产生资源警告 | 显式关闭连接；30项测试无警告通过 |
| 10 | Evidence | 测试输出未单独落盘 | `04_Evaluation/test_results.txt` |

## 完成度映射

| 指导书阶段 | 状态 | 主要证据 |
|---|---|---|
| Day 1 项目与LLM | 完成实现；真实API实验待密钥 | `.env.example`、LLM客户端、Prompt runner |
| Day 2–7 Knowledge → RAG | 完成 | 12文档、3组Chunk实验、20-Chunk Embedding、RAG Baseline |
| Day 8–14 Tool → Agent | 完成 | 两个Tool、Registry、三步Loop、Memory、Agent Baseline |
| Day 15–19 Evaluation | 自动评价完成；人工签署待本人 | 20题数据集、四类指标、3个真实失败案例 |
| Day 20 Engineering | 完成 | 配置、日志、异常、密钥保护、循环保护、30项测试 |
| Day 21 Release | 完成 | README、报告、Demo、原始数据与审计材料 |

## 未关闭但不可代填的门槛

| 门槛 | 当前状态 | 关闭条件 |
|---|---|---|
| 外部LLM Prompt A/B | `draft_advisory` / 未运行 | 提交者在本地配置自己的Key并运行 `python -m src.evaluation.prompt_runner` |
| 10题人工评价 | `draft_advisory` / 待确认 | 提交者填写 `human_*`、评审人、日期和确认字段 |

以上两项均涉及个人凭证或人工作者身份，不能由自动化过程冒充完成；它们已被隔离，不影响离线系统、自动评价与工程验收的真实性。

