# 人工答案评审说明

`human_evaluation_v1.csv` 已包含 10 道 Knowledge QA 的问题、系统回答、引用和首条检索证据。自动判分仅作为建议，证据状态为 `draft_advisory`，不能冒充人工结论。

提交者需要逐行完成：

1. 将 `human_correctness`、`human_faithfulness`、`human_citation_accuracy` 分别填写为 `true` 或 `false`；
2. 在 `notes` 记录错误或边界判断；
3. 填写 `reviewer` 与 `review_date`；
4. 将 `human_confirmed` 改为 `true`，并将 `evidence_state` 改为 `human_final`。

三项判断标准：

- Correctness：回答是否覆盖冻结测试集中的必要事实；
- Faithfulness：回答中的事实是否都能在 evidence_excerpt 中找到依据；
- Citation Accuracy：引用文档是否是期望来源，且引用与答案一致。

此步骤必须由提交者本人完成，AI 预填结果不能替代人工确认。
