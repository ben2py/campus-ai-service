# 当前运行入口

此目录是长安知行 V2 的唯一当前运行源码。完整说明、API 配置、功能边界与验收链接见[项目 README](../README.md)。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

打开 http://127.0.0.1:7860 进入沉浸式校园。点击顶部「智能工作台」进入原完整界面；默认无密钥离线演示，在工作台「模型与连接」配置自己的 API 后启用大模型。

```bash
python -m unittest discover -s tests -v
python -m src.evaluation.workbench_check
```

## API 模式语义检索

在 `.env` 中填写 `EMBEDDING_API_KEY`（默认阿里云百炼 `text-embedding-v4`，可改为任意 OpenAI 兼容 `/embeddings` 服务）后重启。启用大模型时，`search_knowledge` 改用「云端语义向量 70% + 关键词 30%」混合检索；未配置密钥、离线模式或接口失败时使用本地哈希向量，并在状态栏和 trace 中注明回退。`/api/health` 的 `retrieval` 字段显示当前检索方式。

文本块向量只在内存中缓存，同一文本只请求一次。资料文本（含个人上传）与检索词会发送到 Embedding 服务。配置后运行下面的命令，对比两种检索在原题、口语改写和库外题上的表现，并按输出调整 `SEMANTIC_THRESHOLD`：

```bash
python -m src.evaluation.semantic_check
```

不要将 `.env`、`data/*.db` 或个人密钥提交到仓库。
