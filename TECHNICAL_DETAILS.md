# 长安知行 · 技术细节文档

本文档描述 `02_Python_Version/`（当前唯一运行源码）的技术实现。内容依据源码整理，截至 2026-10-08。功能介绍与使用方式见 [README](README.md)，接口清单见 [WORKBENCH_API](02_Python_Version/reports/WORKBENCH_API.md)，测试记录见 [TEST_REPORT](02_Python_Version/reports/TEST_REPORT.md)。

## 1. 技术栈概览

| 层 | 技术 | 说明 |
|---|---|---|
| 后端 | Python 3.11+，Flask 3.1.3 | 唯一第三方运行依赖；HTTP 调用全部用标准库 `urllib` |
| 存储 | SQLite（标准库 `sqlite3`） | 会话、上传资料、非敏感设置、模拟业务数据 |
| 向量索引 | 自研 `InMemoryVectorStore` | 进程内存，不是向量数据库 |
| Embedding | 本地 Hashing（512 维）/ 云端 OpenAI 兼容 `/embeddings`（默认百炼 `text-embedding-v4`，1024 维） | 云端仅在 API 模式且配置密钥时启用 |
| LLM | 四种原生协议：Chat Completions、Responses、Anthropic Messages、Gemini generateContent | 无厂商 SDK |
| 前端 | 原生 ES Module、Three.js（本地托管）、Shadow DOM | 无构建步骤、无 CDN |
| 测试 | `unittest`（Python）、Playwright（`.cjs` 浏览器冒烟） | |

## 2. 目录结构

```text
02_Python_Version/
├─ app.py                  Flask 入口、路由、安全中间件
├─ src/
│  ├─ config.py            .env 加载、Settings / EmbeddingSettings、日志
│  ├─ ingestion/           loader.py 文档加载 · chunker.py 分块
│  ├─ retrieval/           embedder.py 哈希向量 · cloud_embedder.py 云端向量 · vector_store.py · retriever.py 混合检索
│  ├─ rag/pipeline.py      检索 → 阈值 → 生成 → 引用 / 拒答
│  ├─ llm/                 client.py 离线生成器与旧版客户端 · providers.py 四协议适配
│  ├─ agent/agent.py       离线规则路由 Agent
│  ├─ tools/               registry.py · status.py 进度查询 · handoff.py 人工工单
│  ├─ memory/conversation.py  限长会话记忆与指代消解
│  ├─ workbench/           service.py 主编排 · store.py 持久化 · web_search.py · knowledge.py 资料范围
│  └─ evaluation/          runner / semantic_check / workbench_check / prompt_runner / public_corpus_check
├─ docs/                   D01–D12 模拟校园资料（Markdown + front matter）
├─ data/
│  ├─ public_corpus/chd_2026_10/docs/   DCHD01–37 长安大学官方页面释义摘要
│  ├─ campus.db            模拟业务表与工单
│  └─ workbench.db         会话、上传、设置、会话签名密钥（私有运行数据）
├─ static/                 前端页面与脚本
└─ tests/                  Python 单测 + Playwright 冒烟脚本
```

## 3. 整体架构

```text
浏览器（index.html / workbench.html）
   │  同源 JSON / SSE
   ▼
Flask app.py ── 安全中间件（Host 白名单、同源校验、CSP）
   │
   ▼
Workbench.run()  ─┬─ 离线模式：CampusServiceAgent（规则路由）→ RAGPipeline → DeterministicGroundedClient
                  └─ API 模式：ModelClient（四协议）→ 有界 Tool Loop
                         ├─ search_knowledge → HybridRetriever（哈希 / 云端语义）
                         ├─ search_web       → Tavily / DuckDuckGo / Bing RSS
                         ├─ query_application_status → campus.db
                         └─ handoff_to_human          → campus.db
   │
   ▼
Store（workbench.db）：保存问答、trace、引用
```

两种模式共用同一套知识文档、分块器、工具注册表和持久化层，差别在于"谁做决策"：离线模式由关键词规则决定路由，API 模式由大模型自主选择工具。

## 4. 配置

`src/config.py` 启动时自行解析 `02_Python_Version/.env`（不依赖 python-dotenv），用 `os.environ.setdefault` 写入，因此已存在的环境变量优先于 `.env`。

| 变量 | 默认 | 作用 |
|---|---|---|
| `LLM_MODE` | `offline` | `offline` / `api`，决定新工作空间的初始模式 |
| `LLM_PROTOCOL` / `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | openai / OpenAI / gpt-4.1-mini / 空 | API 模式初始值，网页设置优先 |
| `TAVILY_API_KEY` | 空 | 联网搜索 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 260 / 40 | 分块 |
| `EMBEDDING_DIMENSION` | 512 | 哈希向量维度 |
| `TOP_K` / `SIMILARITY_THRESHOLD` | 3 / 0.25 | 离线 RAG 召回数与拒答阈值 |
| `MAX_AGENT_STEPS` / `MEMORY_TURNS` | 3 / 6 | 离线 Agent 步数上限、记忆轮数 |
| `EMBEDDING_API_KEY` 等 | 空 | 云端语义向量；空则不启用 |
| `SEMANTIC_WEIGHT` / `SEMANTIC_THRESHOLD` | 0.7 / 0.40 | 语义检索融合权重与阈值 |

`EMBEDDING_BASE_URL` 指向本机（127.0.0.1 / localhost）时允许无密钥。日志写入 `logs/campus-agent.log`。

## 5. 知识处理与检索

整条链路是"加载 → 切片 → 向量化 → 混合打分 → 阈值过滤 → 生成/交给大模型"。下面按代码执行顺序说明，数字均为当前仓库实测（2026-10-08）。

```text
Markdown 文件 ──loader──▶ Document ──chunker──▶ Chunk ──embedder──▶ 向量（内存）
                                                     └──tokenize──▶ token 集合（内存）
问题 ──同义词扩展──▶ 向量 + token 集合 ──逐块打分──▶ 排序 Top-K ──阈值──▶ 证据
```

### 5.1 文档加载（`ingestion/loader.py`）

- 读取目录下的 `.md` / `.txt`（按文件名排序，不递归），UTF-8 解码。
- 解析 `---` 包裹的简易 front matter：逐行按第一个冒号拆成键值，不支持多行值或嵌套。正文是第二个 `---` 之后的内容。
- 生成 `Document(document_id, title, topic, text, source_path, source_note)`：
  - `document_id` 取 front matter 的 `id`，没有就取文件名第一个下划线前的部分，统一转大写（如 `D08_library_hours.md` → `D08`）。
  - `title` 取 `title`，没有就取正文第一个 `#` 标题，再没有就用文件名。
  - `topic` 缺省为"未分类"，`source_note` 缺省为"课程实验自构模拟资料"。
- 解码失败或空文件跳过，原因写入 `logs/loader_errors.log`，不会中断加载。
- 用户上传不走文件加载：`Workbench.documents()` 从 `workbench.db` 的 `uploads` 表读出正文，直接构造 `Document`，`topic` 固定为"我的资料"。

知识来源有三类，由"资料范围（scope）"隔离：

| scope | 参与检索的文档 | 来源编号 |
|---|---|---|
| `chd_public`（工作台默认） | 37 份长安大学官方页面释义摘要，带 URL、发布日期、整理日期；**不含**用户上传 | `DCHD01`–`DCHD37` |
| `simulation` | 12 份课程模拟资料（7 个主题）+ 当前浏览器的上传资料 | `D01`–`D12`、`U…` |
| 联网 | 搜索摘要，每轮临时编号，不切片、不向量化 | `W1`… |

官方资料在 `Workbench` 初始化时由 `PublicKnowledge` 一次性读入内存，新增文件需要重启服务。front matter 里的 `url`、`school`、`source_date`、`retrieved_at` 不参与检索，只在结果返回前由 `PublicKnowledge.enrich()` 按 `source_id` 补到引用上。同一会话不能切换 scope，切换需新开会话。

### 5.2 切片（`ingestion/chunker.py`）

参数：`CHUNK_SIZE=260`、`CHUNK_OVERLAP=40`（按字符计，不是 token）。要求 `chunk_size > 0` 且 `0 ≤ overlap < chunk_size`，否则抛 `ValueError`。

**第一步：按标题切章节（`_sections`）**

1. 逐行处理正文，每行内部的连续空白压成一个空格，空行丢弃。
2. 以 `#` 开头的行视为标题（任意级别都一样，不区分 `#` 和 `##`），去掉 `#` 和空格后作为后续内容的 `section`。
3. 标题行本身不进入正文。紧跟着另一个标题、没有内容的标题（例如文档的一级标题）不产生章节。
4. 第一个标题之前的内容归入 `section = "概述"`。
5. 同一章节内的多行用 `\n` 连接。

**第二步：章节内再切块（`_split`）**

1. 章节长度 ≤ 260：整个章节就是一块。当前语料绝大多数章节走这条路径，所以"一块 ≈ 一个小节"。
2. 章节长度 > 260：先在 `。！？；` 和换行之后断开，得到句子单元，然后依次累加：
   - 加入下一句会超过 260 时，把当前块输出，新块以**上一块末尾 40 个字符**开头，再接上这一句。重叠是按字符截取的，可能从半句话开始。
   - 单句本身超过 260（无标点的长文本）时，按 260 硬切，下一段从 `260 − 40` 处继续，保证上传资料不会产生无上限的大块。
3. 块编号为 `{文档ID}-C{序号:02d}`，序号在整篇文档内连续递增、跨章节不重置，如 `DCHD20-C01`、`DCHD20-C02`。

每个 `Chunk` 保存 `chunk_id、document_id、title、topic、section、text、source_path`，引用卡片上的"章节"就是这里的 `section`。

**当前切片结果**

| 资料范围 | 文档 | 块数 | 平均 | 最短 | 最长 |
|---|---|---|---|---|---|
| 长安大学官方 | 37 | 134 | 97 字 | 28 字 | 250 字 |
| 模拟资料 | 12 | 24 | 71 字 | 53 字 | 90 字 |

模拟资料的三组分块参数实验（120/0、260/40 等）结果相同，原因就是章节都短于 120 字，切分没有真正发生（见 `04_Evaluation/chunking_experiments.json`）。因此整理官方摘要时，会把容易被问到的单个事实拆成独立小节（如 DCHD08 的"重修次数上限"、DCHD20 的"门诊报销比例与限额"），让它单独成块、单独被检索到。

### 5.3 Embedding

两种 Embedding 实现接口相同（`name`、`dimension`、`embed(text)`、`embed_batch(texts)`），都输出 L2 归一化向量，所以余弦相似度直接用点积计算（`cosine_similarity`）。

**块的向量化输入**是 `"{title} {section} {text}"`，把文档标题和小节标题拼进去，问"校园卡"时，标题里含"校园卡"的块会得到加成。问题的向量化输入是同义词扩展后的问题（见 5.4）。

#### 5.3.1 本地哈希向量 `HashingEmbedder`（`retrieval/embedder.py`）

离线模式始终使用；API 模式未配置云端 Embedding 或云端失败时也用它。

1. **分词 `tokenize()`**：先转小写、去掉所有空白，然后
   - 取出全部中文字符（`\u4e00–\u9fff`）作为单字 token；
   - 把这些中文字符按出现顺序连起来，取相邻二元组。中间夹着的数字、标点会被跳过，所以"周末08:30开馆"会产生跨越数字的"末开"；
   - 再用 `[a-z0-9_-]+` 取英文和数字串。

   例：`"周末08:30开馆，Wi-Fi"` → `周 末 开 馆 周末 末开 开馆 08 30 wi-fi`
2. **哈希到 512 维**：每个 token 做 `blake2b(digest_size=8)`，得到 64 位整数 `v`，`v % 512` 决定落在哪一维，`v` 的最低位决定 +1 或 −1，同一 token 重复出现会累加（保留词频）。
3. **L2 归一化**，全零向量原样返回。

特性和局限：

- 完全确定、无网络、可复现，评测结果可以冻结。
- 只反映字面重叠，没有语义："图书馆开放时间"与"宿舍报修流程"余弦为 0；口语改写题只能命中 2/12（见 12.2）。
- 实现上的一个缺陷：维度 512 是偶数，`v % 512` 的奇偶性和 `v` 的最低位相同，所以符号其实由所在维度决定（偶数维恒为 +1、奇数维恒为 −1），哈希冲突不会像标准 signed hashing 那样相互抵消。由于块和问题用同一套规则，排序依然稳定，只是冲突时会略微高估相似度。修复方法是用另一段哈希位决定符号，但这会改变冻结评测的分数，目前没有改。

#### 5.3.2 云端语义向量 `CloudEmbedder`（`retrieval/cloud_embedder.py`）

只在 API 模式、且 `.env` 配置了 `EMBEDDING_API_KEY`（或本机地址）时启用。默认阿里云百炼 `text-embedding-v4`，1024 维，任何 OpenAI 兼容 `/embeddings` 服务都能用。

1. **配置校验**：启动时检查地址（必须 HTTPS，本机可 HTTP，不能带用户名、密码、query）和模型 ID，配置错误立即报出。
2. **缓存查找**：每段文本的键为 `sha256(模型 \0 维度 \0 文本)`，先查 `EmbeddingCache`。同一批里重复的文本只算一次。
3. **分批请求**：未命中的文本按 `EMBEDDING_BATCH_SIZE=10` 分批 POST，请求体为 `{"model", "input": [...], "encoding_format": "float", "dimensions": 1024}`，超时 20 秒，复用 `post_json` 的安全限制（禁止重定向、响应 ≤ 4 MB）。
4. **响应校验**：返回条数必须等于请求条数，按 `index` 字段排序后取 `embedding`，所有向量维度必须等于 1024，否则抛 `ProviderError`。
5. **归一化并写缓存**，再按原顺序返回。`requests` 计数器记录实际发起的接口调用次数，供测试断言"命中缓存时不调用接口"。

缓存是 `Workbench` 级别共享的线程安全 LRU，上限 20000 条，只在进程内存中：

- 同一进程内，同一文本、同一模型只付费一次；上传新资料只请求新增块；同一检索词重复检索也命中缓存。
- 服务重启后缓存清空，第一次检索会重新为全部 134 块请求向量（约 14 次请求）。
- 密钥只在内存里，不写库、不写日志。启用后，资料文本块（含个人上传）和检索词会发送到 Embedding 服务。

### 5.4 混合检索（`retrieval/retriever.py`）

`HybridRetriever(chunks, embedder, semantic_weight)` 在**构造时**完成索引：

1. 用 embedder 为全部块算向量，存进 `InMemoryVectorStore`（一个 `VectorRecord(chunk, vector)` 列表，没有任何 ANN 索引）。
2. 为每个块预先算一份 token 集合：`set(tokenize(title + section + text))`，用于词法打分。

`search(query, top_k)` 的步骤：

1. **同义词扩展 `_expand`**：问题里出现关键字时，在末尾追加对应词。当前词表：

   | 出现 | 追加 |
   |---|---|
   | 一卡通 | 校园卡 |
   | 开门 | 开馆 开放 |
   | 选课 | 课程选择 退补选 |
   | 补助 | 奖助学金 困难补助 |
   | 修理 | 报修 维修 |
   | wifi（不区分大小写） | 校园网 无线网络 |

   扩展后的问题同时用于向量和词法两路。
2. **向量分**：对扩展后的问题求向量，和每个块的向量做点积，负数截为 0。这是对全部块的线性扫描。
3. **词法分**：`|问题 token 集合 ∩ 块 token 集合| / |问题 token 集合|`，即问题里的单字、二元组有多大比例出现在块里。用的是集合，不计词频；向量分是计词频的，两者互补。
4. **加权合成**：

   ```text
   score = w × 向量分 + (1 − w) × 词法分
   ```

   哈希向量 `w = 0.62`；云端语义向量 `w = SEMANTIC_WEIGHT = 0.7`。
5. 按 `score` 降序取前 `top_k`，把 `query`、`top_k` 和 `(chunk_id, score)` 写入日志 `campus_agent.retrieval`。

返回的 `RetrievalResult` 同时带 `score`、`vector_score`、`lexical_score`，trace 和评测里能看到三项分数。

**实例**（官方范围，离线哈希）："一卡通丢了怎么办" 扩展为 "一卡通丢了怎么办 校园卡"：

| 排名 | 块 | score | 向量分 | 词法分 |
|---|---|---|---|---|
| 1 | DCHD01-C02（统一身份认证常见问题·校园卡挂失与电子卡） | 0.426 | 0.473 | 0.35 |
| 2 | DCHD16-C04（校园卡新办·办理地点与电话） | 0.397 | 0.426 | 0.35 |
| 3 | DCHD17-C01（校园卡挂失·挂失与补办） | 0.376 | 0.423 | 0.30 |

验算：0.62 × 0.473 + 0.38 × 0.35 = 0.426。三块都与校园卡相关，但最直接回答"挂失"的 DCHD17 排第三，说明字面检索区分不了"挂失"和"办理"这类相近意图，这是语义向量要解决的问题。

### 5.5 两种模式下检索结果怎么用

**每轮都会重建索引**：`Workbench.retriever()` 和 `knowledge_index()` 每次回答都重新加载文档列表、重新切片、重新构造 `HybridRetriever`。离线哈希模式下官方范围每轮约 22 ms（检索本身约 2 ms）；云端模式下块向量命中内存缓存，不重复付费，但仍有重新切片和组装的开销。改为持久化索引的方案见 13 节。

#### 离线模式（`rag/pipeline.py`）

1. 记忆模块先做指代消解：问题含"它、那里、该项、这个、这项、还有呢"时，在前面拼上一轮第一个引用的文档标题，例如"它工作日呢？"→"图书馆开放时间与入馆须知；它工作日呢？"。
2. 检索 `max(top_k, 5) = 5` 条，全部写入 trace。
3. 取前 `TOP_K = 3` 条中 `score ≥ SIMILARITY_THRESHOLD = 0.25` 的作为可靠证据；一条都没有就拒答："当前知识库没有足够可靠的依据……"。
4. 生成只用排名第一的那一块，避免相邻主题混进答案。
5. `DeterministicGroundedClient` 把这块按 `。！？；` 切句，丢掉少于 5 字的碎片，按"问题 token 在句子中的覆盖率"排序，取覆盖率大于 0 的前 2 句拼成回答，末尾附 `来源：[文档ID 章节]`。它是证据摘取器，不是语言模型，结果完全可复现。
6. 官方范围的回答加前缀"【长安大学资料范围；官方摘要请核对原文，业务工具仍为模拟】"，引用补上 URL 和日期。

#### API 模式（`workbench/service.py`）

1. `knowledge_index()` 选后端：配置了云端 Embedding 就构造语义检索器（阈值 `SEMANTIC_THRESHOLD = 0.40`），否则用哈希检索器（阈值 0.25）。构造时为块请求向量失败，整轮回退哈希检索，并在状态栏和 trace 写入 `retrieval_fallback`。
2. 检索由大模型通过 `search_knowledge(query)` 工具触发，检索词由模型自己改写，可以在一轮里调用多次（同参数会去重缓存）。
3. 每次调用取 Top-5，只保留 `score ≥ 阈值` 的块，补上 `knowledge_scope` 和官方元数据后返回给模型，同时附注资料范围说明和"无结果请明确未知"。
4. 如果检索词本身的向量请求失败，只把这一次改用同一批块的哈希检索，并在 `note` 里告诉模型"本次为字面检索结果"。
5. 返回过的块记入 `sources`；最终回答里的 `[DCHD20-C03]` 会折算为 `[DCHD20]`，没有返回过的编号替换成 `[来源未核验]`，引用卡片只展示真实检索到的来源。

#### 阈值是怎么定的

- 0.25：哈希检索的固定配置，冻结评测（20 题模拟资料）使用该值，库外题和边界题都能正确拒答。它没有在 37 份官方资料上单独校准。
- 0.40：`python -m src.evaluation.semantic_check` 对 `text-embedding-v4` 校准，库内正例（含 12 道口语改写）最低分 0.415；库外 6 题中 4 题低于 0.40。分布有重叠，阈值偏向保召回，相关性由大模型二次判断。换 Embedding 模型后需要重新运行该脚本校准。

## 6. Agent

### 6.1 离线规则 Agent（`agent/agent.py`）

按优先级路由：

| 顺序 | 触发 | 路由 |
|---|---|---|
| 1 | 空输入 | `request_clarification` |
| 2 | "忽略上述""API Key""系统提示词"等 | `refuse` 安全拒答 |
| 3 | "转人工""人工客服"等 | `handoff_to_human` 工具 |
| 4 | "申请进度""办理进度"等 | 提取 `S\d{4}` 和 `AP\d{7}`，缺参数则追问，齐全则调用 `query_application_status` |
| 5 | 问候语 | `direct` |
| 6 | 其他 | 指代消解后走 RAG，结果为 `knowledge` 或 `unknown` |

工具执行在 `_execute_tool_loop` 中以显式步数上限完成"Tool Call → Observation"，每一步写入 trace。

Workbench 在调用离线 Agent 之前还做两件事：

- 跨轮补参：历史中出现过"申请进度"时，从之前的用户消息里补齐学号或申请号。
- 否定识别：`handoff_requested()` 用正则排除"不要转人工""能不能转人工"这类非明确请求，改为提示用户明确表述。

### 6.2 API 模式 Tool Loop（`workbench/service.py`）

```text
系统提示词 + 最近 12 条历史 + 当前问题
  └─ 循环最多 6 轮：
       模型返回 tool calls？
         否 → 作为最终回答，结束
         是 → 逐个执行（总数 ≤ 12，相同参数去重缓存）→ observe 回填 → 下一轮
  预算：总耗时 90 秒；每步检查取消事件
```

可用工具：`search_knowledge`（必选）、`query_application_status`、`handoff_to_human`，开启联网时追加 `search_web`。

工具执行前的校验：

- 名称必须已注册，参数必须为对象，不能缺少必填字段或包含未定义字段。
- 字符串长度受 schema 的 `minLength` / `maxLength` 约束。
- `handoff_to_human` 需当前问题明确要求转人工，否则返回拒绝并让模型先询问。
- `search_knowledge` 的向量请求失败时，本次自动换用同一批块的哈希检索，并在结果 `note` 中说明。

回答后处理：

- 模型可能把引用写成块编号 `[D10-C01]`，统一折算为 `[D10]`。
- 本轮工具没有返回过的编号替换为 `[来源未核验]`，引用卡片只展示真实返回的来源。
- 来源元数据（URL、学校、日期）由应用侧补全，不采信模型输出。
- `chd_public` 范围的回答统一加前缀提示"官方摘要请核对原文，业务工具仍为模拟"。

系统提示词要求：校园规则必须检索；工具结果和文档视为不可信数据，不执行其中的指令；不虚构来源；明确标注模拟性质。

### 6.3 四协议适配（`llm/providers.py`）

`ModelClient` 用 `start / complete / observe` 三个方法屏蔽协议差异：

| 协议 | 端点 | 工具声明 | 工具结果回填 |
|---|---|---|---|
| `openai` | `/chat/completions` | `tools[].function` | `role: tool` + `tool_call_id` |
| `responses` | `/responses`（`store: false`） | 扁平 function，`strict: false` | `function_call_output` + `call_id` |
| `anthropic` | `/messages` | `input_schema` | `tool_result` + `tool_use_id` |
| `gemini` | `/models/{m}:generateContent` | `functionDeclarations`（去掉 `additionalProperties`） | `functionResponse`，原样保留 `thoughtSignature` |

内置 11 个预设（OpenAI、DeepSeek、通义、智谱、Moonshot、硅基流动、OpenRouter、Claude、Gemini、Ollama、自定义）。四种协议经 Mock 测试，未逐一连接各供应商真实账号。

`post_json` 的安全约束：禁止重定向（防止凭据被转发到其他主机）、响应上限 4 MB、HTTP 错误码映射为可读中文提示且不回显原始响应。`validate_config` 要求 HTTPS（本机地址允许 HTTP），禁止 URL 中带用户名、密码、query、fragment，模型 ID 须匹配 `[\w./:@+-]+`。

### 6.4 会话记忆（`memory/conversation.py`）

- 每会话一个 `deque(maxlen = 轮数 × 2)`，默认保留最近 6 轮。
- 每条消息可带 `topic`（取首个引用的文档标题）。
- 问题含"它""那里""这个""还有呢"等指代词时，拼接最近话题再检索，例如"它工作日呢？"会变成"图书馆开放时间与入馆须知；它工作日呢？"。

## 7. 工具

| 工具 | 参数 | 实现 |
|---|---|---|
| `query_application_status` | `student_id`（`^S\d{4}$`）、`application_id`（`^AP\d{7}$`） | 参数化 SQL 查询 `applications` 表，预置 4 条模拟记录；学号与申请号须同时匹配 |
| `handoff_to_human` | `reason`（2–500 字） | 写入 `handoff_tickets`，生成 `HF-XXXXXXXX` 工单，状态 `queued` |
| `search_knowledge` | `query`（1–400 字） | 当前 scope 的混合检索，取 5 条并按阈值过滤 |
| `search_web` | `query`（1–400 字） | 见第 8 节 |

`ToolRegistry` 负责注册（名称不能为空或重复）、必填与多余参数校验、捕获异常并只返回异常类型名。所有业务工具结果附注"本地教学模拟"。

## 8. 联网搜索（`workbench/web_search.py`）

- 检索词中的模拟学号与申请号会被移除，避免发送到外部。
- 有 Tavily Key 时调用 Tavily API；否则抓取 DuckDuckGo HTML 结果页，再无结果时尝试 Bing RSS（仅限个人非商业实验）。
- 重定向只允许到固定的 Bing / DuckDuckGo 主机；不抓取任意网页正文，只用搜索摘要。
- 结果最多 5 条，带 `retrieved_at`（检索时间，不是网页发布时间）和服务名；无结果时明确报错，不推断"网上没有"。

## 9. Web 层与 API

### 9.1 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/health` | 模式、模型、文档数、检索后端 |
| POST | `/api/chat` | 提问；`Accept: text/event-stream` 时走 SSE |
| POST | `/api/cancel` | 停止当前回答 |
| POST | `/api/reset` | 删除会话 |
| GET / POST | `/api/conversations` | 列表 / 新建 |
| GET / PATCH / DELETE | `/api/conversations/<id>` | 读取 / 改名 / 删除 |
| GET / POST | `/api/settings` | 读取（脱敏）/ 保存模型设置 |
| POST | `/api/settings/test` | 发一次真实请求测试连接 |
| GET | `/api/knowledge?scope=&q=` | 浏览与过滤资料 |
| POST | `/api/knowledge/upload` | 上传 `.md` / `.txt` |
| DELETE | `/api/knowledge/<id>` | 删除本人上传 |

### 9.2 SSE

`Workbench.run()` 是生成器，依次产出 `status`（执行阶段描述）、`done`（完整结果）或 `error` 事件。前端用 `fetch` + `response.body.getReader()` 读取。推送的是执行状态，不是逐 token 输出。

### 9.3 并发与取消

- 同一浏览器同时只允许一个会话在回答，以限制并发和模型费用。
- 取消通过 `threading.Event` 实现，每轮与每次工具调用前检查；已发出的 HTTP 请求无法立即中断（最长约 30 秒超时），已创建的模拟工单不会回滚。

### 9.4 持久化（`workbench/store.py`）

`workbench.db` 有四张表：`conversations`、`messages`（`ON DELETE CASCADE`，`data` 列保存完整 JSON 结果，含 trace 与引用）、`uploads`、`settings`。所有查询带 `owner` 条件实现浏览器间隔离。限制：每个工作空间最多 30 份上传、会话列表取最近 100 条、标题截断 40–60 字。

## 10. 安全设计

| 方面 | 措施 |
|---|---|
| 访问范围 | `before_request` 只允许 Host 为 127.0.0.1 / localhost（同时防 DNS 重绑定） |
| CSRF | 写请求拒绝 `Sec-Fetch-Site: cross-site` 及 Origin 不同源；除上传外必须是 JSON；上传另需 `X-Campus-Request: 1` |
| 身份 | 首次访问生成 48 位随机 `owner` 写入签名 Session；Cookie `HttpOnly` + `SameSite=Strict`；签名密钥随机生成并保存在 `workbench.db`，可用 `CAMPUS_SESSION_SECRET` 覆盖 |
| 响应头 | 严格 CSP（`default-src 'self'`，禁止内联脚本与 frame 嵌入）、`nosniff`、`no-referrer`；API 响应 `no-store` |
| 密钥 | 模型与 Tavily Key 只存在进程内存，写库前过滤，接口只返回 `has_api_key`；重启后需重填；更换地址或协议时不沿用旧 Key |
| 输入 | 请求体上限 1 MB；问题 ≤ 4000 字；上传仅 UTF-8 `.txt` / `.md`、≤ 500 KB、拒绝 NUL；所有 SQL 参数化 |
| 输出 | 前端纯文本安全渲染（上传 XSS 已有浏览器测试）；错误只返回概要，详情写服务端日志 |
| 提示注入 | 系统提示声明工具结果不可信；引用存在性校验；转人工在服务端二次确认 |

边界：这是本机演示应用，不是生产级权限系统。公网部署需要认证、TLS、限流和真实授权接口。

## 11. 前端

| 文件 | 作用 |
|---|---|
| `index.html` + `campus.js` | 首页：Three.js 渭水校区简化模型，地点目录、搜索、日夜景、俯视地图 |
| `campus-layout.js` | 依据学校 2023 版校图换算的相对坐标（非 GPS） |
| `campus-world.js` | 场景构建：重复建筑实例化绘制、静态阴影按需更新、静止时停止渲染 |
| `landmark-film.js` | 地标推进 → 官方实景照片转场；双图层避免白帧，版本号机制处理快速连点中断 |
| `agent-portal.js` | 在首页内以 Shadow DOM 挂载 Agent 工作台（隔离样式与 ID，无 iframe），保留草稿与浏览器前进后退 |
| `app.js` | 工作台主逻辑：对话、SSE 读取、会话管理、知识空间、模型设置、Markdown 导出、主题 |
| `ion-assistant.js` | 三维离子核心助手，随请求状态变化；移动端减少粒子 |
| `workbench.html` + `workbench-boot.js` | 独立工作台兼容入口 |

可访问性与降级：支持 `prefers-reduced-motion`、暂停场景、WebGL 不可用或上下文丢失时静态回退、图片加载失败提示。Three.js、字体、图标都在 `static/vendor` 与 `static/assets` 本地托管。

## 12. 评测

### 12.1 离线基线（`python -m src.evaluation.runner`，冻结于 2026-09-25）

20 题，覆盖知识、多轮、业务状态、库外、边界五类，配置为 chunk 260/40、`w = 0.62`、阈值 0.25、Top-K 3。

| 指标 | 结果 |
|---|---|
| Recall@1 / @3 / @5 | 100% / 100% / 100% |
| Correctness / Faithfulness / Citation Accuracy | 100% / 100% / 100% |
| 工具选择 / 参数 / 任务完成 / 未知处理 | 100% / 100% / 100% / 100% |
| 平均 / P95 延迟 | 0.26 ms / 0.39 ms |

这些数字只适用于小规模自构数据加确定性摘录器的条件，不代表真实 LLM 或真实部署质量。另有三组分块参数实验与 Embedding 维度实验（`04_Evaluation/chunking_experiments.json`、`embedding_experiment.json`）。

### 12.2 语义检索对比（`python -m src.evaluation.semantic_check`，2026-10-08）

| 测试集 | 哈希（阈值 0.25） | text-embedding-v4（阈值 0.40） |
|---|---|---|
| 原题 10 道命中 | 10/10 | 10/10 |
| 口语改写 12 道命中 | 2/12 | 12/12 |
| 库外 6 道正确拒答 | 4/6 | 4/6 |

正例最低分 0.415，库外题最高 0.485，分布有重叠，阈值 0.40 偏向保召回，由大模型二次判断相关性。已知问题：DeepSeek 对"电脑上不了网"等看似通用的问题有时不调用 `search_knowledge`。

### 12.3 测试

```bash
cd 02_Python_Version
LLM_MODE=offline python -m unittest discover -s tests -q   # 当前 69 项全部通过
python -m src.evaluation.workbench_check                    # V2 验收，写入 04_Evaluation/v2/
NODE_PATH=<playwright> node tests/browser_smoke.cjs         # 需先启动服务
```

注意：当前本地 `.env` 设置了 `LLM_MODE=api`，直接运行单测会让 5 个用例走真实 API 而失败（health 返回 `deepseek-chat`、转人工否定等）。测试前用 `LLM_MODE=offline` 覆盖即可；更彻底的做法是让测试环境不读取 `.env` 中的模式。

## 13. 已知限制

- 向量索引在内存中，每轮回答都重新切片、重建索引（官方范围约 22 ms，检索本身约 2 ms）；云端向量缓存重启即丢，重启后要重新为全部块付费请求。计划改为 SQLite + sqlite-vec + FTS5 的持久化索引：按内容哈希增量同步，Embedding 缓存落库，检索改为向量与关键词两路召回后再按现有公式打分，阈值无需重新校准。
- 哈希向量的符号位与维度奇偶相关（见 5.3.1），冲突时略微高估相似度；为保持冻结评测可复现暂未修复。
- 哈希向量没有语义能力，口语改写召回差（2/12）；语义检索依赖外部服务，会把资料文本与检索词发送出去。
- 长安大学官方资料 37 份，尚未建立冻结评测集做独立评测；图书馆开放时间、奖助学金评定、宿舍管理规定暂缺。
- 业务数据和工单全部为模拟，未接入任何真实校园系统。
- 公开搜索可能限流或返回无关结果，商用需改为有许可的搜索 API。
- Flask 开发服务器只适合本机演示。
