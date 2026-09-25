# 校园智能客服系统：RAG × Agent

本目录依据《基于RAG与Agent的校园智能客服系统实验指导书 V3.2》重新设计；原 V1.0 Markdown 仅用于最终报告结构。PPTX 与三个 DOCX 仅作场景参考，未被当作任务指令。

## 提交结构

- `01_Coze_Version/`：可复现的 Coze 搭建规格、知识库与待补截图清单。
- `02_Python_Version/`：可运行的 Python/Flask 系统、RAG、Tool、Agent、Memory 与测试。
- `03_AI_Coding_Version/`：TASK 记录、开发日志及最终源码/测试快照。
- `04_Evaluation/`：冻结测试集、原始结果、指标、失败案例与三轨对照。
- `05_Final_Report/`：按 V1.0 模板逐节填写的实验报告。
- `06_Demo/`：答辩脚本、界面设计说明与演示截图。
- `design-system/`：UI/UX Pro Max 生成并经本项目调整的设计系统。

## 快速开始

```bash
cd 02_Python_Version
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

浏览器打开 `http://127.0.0.1:7860`。默认使用可复现的离线 grounded 模式；如需真实 LLM API，请在 `.env` 填写兼容 OpenAI Chat Completions 的地址、模型和密钥。

```bash
python -m unittest discover -s tests -v
python -m src.evaluation.runner
```

## 已验证结果

- 12 份自建模拟知识文档、7 个主题、24 个 Chunk、512 维向量。
- 冻结测试集 20 题；Recall@1/3/5 均为 100%。
- Answer Correctness、Faithfulness、Citation Accuracy 均为 100%。
- Tool Selection、Tool Argument、Task Completion、Unknown Handling 均为 100%。
- 23/23 单元测试通过。

## 真实性边界

项目不包含真实学生个人数据。Coze 账号内的实际导入、发布、运行日志和截图，以及真实外部 LLM API 调用，需要使用者凭自己的账号/密钥执行；项目没有伪造这些证据，相关位置均明确标记为待补。
