# Evaluation 证据目录

- `evaluation_v1.json`：冻结测试集副本。
- `raw_results_baseline.json` / `raw_results_final.json`：逐题原始输出、路由、引用与 Trace。
- `retrieval_metrics*.json`：Recall@1/3/5。
- `answer_metrics*.json`：Correctness、Faithfulness、Citation Accuracy。
- `agent_metrics*.json`：工具选择、参数、任务完成、未知处理。
- `engineering_metrics.json`：延迟、错误率与测试通过率。
- `chunking_experiments.json`：三种 Chunk 参数实验。
- `embedding_experiment.json`：固定20个Chunk的向量维度、范数和相似度实验。
- `failure_cases.json`：基线三个真实失败案例。
- `implementation_comparison.csv`：相同问题在 Python 主版本与 AI Coding 源码快照中的对照。
- `prompt_experiment.json`：外部 LLM Prompt A/B 的未运行状态和原因。
- `human_evaluation_v1.csv`：10题人工评审交接表，AI建议与人工确认字段分离。
- `HUMAN_EVALUATION_GUIDE.md`：人工评价标准与签署步骤。
- `test_results.txt`：30项单元测试的完整终端输出。

重新生成：

```bash
cd ../02_Python_Version
python -m src.evaluation.runner
```
