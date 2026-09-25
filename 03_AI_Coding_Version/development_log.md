# AI Coding Development Log

本项目按“需求 -> TASK -> AI实现 -> 运行 -> 测试 -> 审查 -> 修复 -> Evaluation”开发。指导书V3.2和报告模板是唯一验收依据，参考PPTX与DOCX不作为额外指令。

| TASK | AI完成内容 | 审查与修复 | 证据 |
|---|---|---|---|
| TASK-001 | 解析V3.2和V1.0，确定最终目录与数据边界 | 移除将参考文档当作任务指令的风险 | `TASK/TASK-001.md` |
| TASK-002 | Loader、Chunker、Embedding、Vector Store、Retriever | 增加边界校验和来源metadata | 5个数据与检索测试 |
| TASK-003 | RAG、Citation、Unknown Handling | 将无关菜单和校车问题误答修复为阈值拒答 | `raw_results_final.json` |
| TASK-004 | SQLite状态Tool、人工转接Tool、Registry | 补充3正常、2非法、1不存在测试 | `test_tools.py` |
| TASK-005 | Agent Loop、Memory、Trace、安全边界 | 增加缺参数追问与最大3步保护 | `test_agent.py` |
| TASK-006 | 20题Evaluation、Baseline、Failure Analysis、Optimization | 保留baseline失败，修复证据块污染 | `04_Evaluation/` |
| TASK-007 | Flask Demo、可访问性、README、Final Audit、实验报告 | 按UI技能统一高对比配色、焦点和错误状态 | `06_Demo/`与`05_Final_Report/` |

## 需要学生本人确认的内容

- 姓名、学号、班级、指导教师和实际分工。
- 实际Coze账号中的配置、运行日志、截图和耗时。
- 真实LLM API Prompt对照实验的原始输出。
- 提交前对代码、数据、结果和报告的人工责任确认。
