# Agent Evaluation

最终版使用20题冻结测试集，包含10题Knowledge QA、4题Status Query、2组Multi-turn、2题Unknown和2题Boundary。

| 指标 | 结果 |
|---|---:|
| Tool Selection Accuracy | 100.00% |
| Tool Argument Accuracy | 100.00% |
| Task Completion Rate | 100.00% |
| Unknown Handling Rate | 100.00% |

## 分类结果

| 类别 | 样本数 | Tool选择 | Tool参数 | 任务完成 | Unknown处理 |
|---|---:|---:|---:|---:|---:|
| Knowledge | 10 | 100% | 不适用 | 100% | 不适用 |
| Status | 4 | 100% | 100% | 100% | 不适用 |
| Multi-turn | 2 | 100% | 不适用 | 100% | 不适用 |
| Unknown | 2 | 100% | 不适用 | 100% | 100% |
| Boundary | 2 | 100% | 不适用 | 100% | 不适用 |

不适用项在 JSON 中保存为 `null`，不以 100% 代替。

逐题路由、Tool参数、Trace和检索证据保存在`04_Evaluation/agent_metrics.json`和`raw_results_final.json`。该结果只代表当前自构数据与冻结测试集，不代表真实校园部署精度。
