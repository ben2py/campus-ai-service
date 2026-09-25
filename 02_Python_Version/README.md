# 基于 RAG 与 Agent 的校园智能客服

该项目按实验指导书 V3.2 实现知识加载、分块、Embedding、混合检索、RAG、业务查询Tool、人工转接Tool、Agent、Memory、Unknown Handling、Citation、Logging、Tests和Evaluation。

## 数据边界

`docs/`中的12份资料和SQLite记录全部为课程实验自构模拟数据，不代表任何真实学校政策，不含真实个人敏感信息。

## 环境

- Python 3.11+
- Flask 3.1.3
- 不需要向量数据库或模型下载

## 安装与启动

```bash
cd 02_Python_Version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python app.py
```

打开 `http://127.0.0.1:7860`。

## 测试

```bash
python -m unittest discover -s tests -v
```

## Evaluation

```bash
python -m src.evaluation.runner
```

脚本会重建SQLite模拟数据并将结果写入`../04_Evaluation/`。冻结测试集共20题。

## LLM API

`src/llm/client.py`包含OpenAI-compatible API客户端。请在本地`.env`中配置`LLM_API_KEY`，禁止将密钥提交到仓库。当前冻结评测使用`deterministic-grounded-v1`，因此结果可复现；真实LLM的Prompt对照须配置API后另行运行，不与离线基线混记。

## 演示编号

- 模拟学号：`S1001`、`S1002`、`S1003`
- 模拟申请：`AP2026001`、`AP2026002`、`AP2026003`

## 局限

- 离线Hashing Embedding适合教学原理复现，不代表生产语义模型效果。
- 未接入真实统一身份认证、校车、食堂或学工系统。
