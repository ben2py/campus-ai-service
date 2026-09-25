# Agent Evaluation

最终版使用20题冻结测试集，包含10题Knowledge QA、4题Status Query、2组Multi-turn、2题Unknown和2题Boundary。

| 指标 | 结果 |
|---|---:|
| Tool Selection Accuracy | 100.00% |
| Tool Argument Accuracy | 100.00% |
| Task Completion Rate | 100.00% |
| Unknown Handling Rate | 100.00% |

逐题路由、Tool参数、Trace和检索证据保存在`04_Evaluation/agent_metrics.json`和`raw_results_final.json`。该结果只代表当前自构数据与冻结测试集，不代表真实校园部署精度。
