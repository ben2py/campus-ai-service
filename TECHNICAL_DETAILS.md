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
│  ├─ public_corpus/chd_2026_10/docs/   DCHD01–06 长安大学官方页面释义摘要
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

### 5.1 文档加载（`ingestion/loader.py`）

- 读取目录下 `.md` / `.txt`，解析 `---` 包裹的简易 front matter（`id`、`title`、`topic`、`source` 等）。
- 缺 `id` 时取文件名前缀；缺 `title` 时取第一个 Markdown 标题。
- 编码错误或空文件跳过，并写入 `logs/loader_errors.log`。

知识来源有三类，由"资料范围（scope）"隔离：

| scope | 内容 | 来源编号 |
|---|---|---|
| `chd_public`（工作台默认） | 6 份长安大学官方页面释义摘要，带 URL、发布日期、整理日期 | `DCHD01`–`DCHD06` |
| `simulation` | 12 份课程模拟资料（7 个主题）+ 当前浏览器的上传资料 | `D01`–`D12`、`U…` |
| 联网 | 搜索摘要，每轮临时编号 | `W1`… |

同一会话不能切换 scope，避免混用不同学校规则；切换需新开会话。

### 5.2 分块（`ingestion/chunker.py`）

1. 按 Markdown 标题切成章节，标题作为 `section`（无标题时为"概述"）。
2. 章节不超过 `chunk_size` 时整体成块；否则按 `。！？；\n` 句子边界累加，超长时把上一块末尾 `overlap` 个字符带入下一块。
3. 无标点的长文本会被硬切到 `chunk_size`，防止上传资料产生无上限的上下文块。
4. 块编号为 `{文档ID}-C{序号:02d}`，如 `D08-C01`。

12 份模拟资料共 2132 字，切成 24 块，平均 71 字。

### 5.3 向量化

哈希向量 `HashingEmbedder`（离线、可复现）：

- 分词：中文单字 + 相邻二元组 + 英文数字词。
- 每个 token 做 `blake2b`（8 字节），对维度取模定位，末位决定 ±1（signed hashing trick），最后 L2 归一化。
- 只能体现字面重叠，不具备语义能力，例如"图书馆开放时间"与"宿舍报修流程"余弦为 0。

云端向量 `CloudEmbedder`：

- 调用 OpenAI 兼容 `/embeddings`，按 `EMBEDDING_BATCH_SIZE` 分批，校验返回条数和维度后做 L2 归一化。
- `EmbeddingCache` 是线程安全的 LRU（上限 20000），键为 `sha256(模型 + 维度 + 文本)`，同一文本只请求一次，上传新资料只请求新增块。
- 密钥只在进程内存中，不落库、不写日志。

### 5.4 混合检索（`retrieval/retriever.py`）

```text
score = w × 向量余弦 + (1 − w) × 词法覆盖率
词法覆盖率 = |查询 token ∩ 块 token| / |查询 token|
```

- 离线 `w = 0.62`，云端语义 `w = 0.7`。
- 查询先做同义词扩展，如"一卡通 → 校园卡"、"wifi → 校园网 无线网络"、"修理 → 报修 维修"。
- 向量库为全量线性扫描（数据量为几十块，无需 ANN 索引）。
- 每次检索记录 `chunk_id` 与分数到日志。

### 5.5 RAG 流程（`rag/pipeline.py`，离线模式）

1. 检索 `max(top_k, 5)` 条，保留完整结果用于 trace 与评测。
2. 取前 `top_k` 条中分数 ≥ 阈值的块；全部低于阈值时直接拒答："当前知识库没有足够可靠的依据……"。
3. 生成阶段只使用排名第一的块，避免相邻主题污染答案。
4. `DeterministicGroundedClient` 把证据按句切开，按与问题的 token 覆盖率排序，摘取最多 2 句，末尾附 `[来源ID 章节]`。它是证据摘取器，不是语言模型，结果完全可复现。

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

- 向量索引在内存中，每次请求按当前文档重建分块与索引；数据量大时需要换成持久化向量库与增量索引。
- 哈希向量没有语义能力，口语改写召回差（2/12）；语义检索依赖外部服务，会把资料文本与检索词发送出去。
- 长安大学官方资料只有 6 份，未达到指导书要求的至少 10 份，也未做独立评测。
- 业务数据和工单全部为模拟，未接入任何真实校园系统。
- 公开搜索可能限流或返回无关结果，商用需改为有许可的搜索 API。
- Flask 开发服务器只适合本机演示。
