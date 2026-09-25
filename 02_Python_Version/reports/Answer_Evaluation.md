# Answer Evaluation

## 自动评价

| 指标 | 总体结果 |
|---|---:|
| Answer Correctness | 100% |
| Faithfulness | 100% |
| Citation Accuracy | 100% |

Knowledge、Status、Multi-turn、Unknown、Boundary 五类分类结果均为100%。逐题回答、证据、引用与判断保存在 `04_Evaluation/answer_metrics.json` 和 `raw_results_final.json`。

## 人工评价门槛

`04_Evaluation/human_evaluation_v1.csv` 已选取10道 Knowledge QA，并分离 `ai_suggested_*` 与 `human_*` 字段。AI预填值属于 `draft_advisory`，不能作为人工结论；提交者需依照 `HUMAN_EVALUATION_GUIDE.md` 逐行复核、填写姓名和日期并确认。

## 失败案例

基线的3个真实失败案例保存在 `04_Evaluation/failure_cases.json`，均包含原始问题、系统输出、精确期望结果、失败类型、事实、分析和改进方案。

