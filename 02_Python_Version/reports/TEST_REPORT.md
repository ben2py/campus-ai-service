# 长安知行 V2 测试报告

- 测试时间：2026-10-06 至 2026-10-07
- 测试对象：`02_Python_Version`（Flask 后端 + 静态前端），当前工作区未提交版本
- 环境：macOS，Python venv（Flask 3.1.3），Node v22.21.1，Playwright 1.55.0，本机 Google Chrome（headless，SwiftShader WebGL）
- 隔离方式：将仓库复制到 `/tmp` 后启动服务，使用全新 `workbench.db`；原仓库的数据库、截图与评测证据未被改动，临时文件已清理

## 1. 结论

| 类别 | 结果 |
|---|---|
| Python 单元测试 | 61 / 61 通过 |
| API 端到端测试（真实 HTTP 服务） | 82 / 82 通过 |
| 仓库自带前端冒烟脚本 | 9 / 15 通过；6 个失败均为脚本过期，非产品缺陷 |
| 补充前端交互测试（按当前 7 地点版本编写） | 23 / 24 通过；唯一失败为 `/favicon.ico` 404 |

未发现功能性缺陷。发现 1 个过期测试集问题、1 处未生效代码、1 个缺失资源，以及联网搜索在无 Tavily Key 时不可用（见第 5 节）。

## 2. 单元测试

命令：`python -m unittest discover -s tests -v`，61 个用例全部通过，耗时 0.3 秒。

| 文件 | 覆盖内容 |
|---|---|
| `test_agent.py` | 意图路由、多轮指代、状态工具调用、未知问题拒绝编造 |
| `test_app.py` | 健康检查、空问题澄清、首页与工作台可访问表单、离线/API 生成器 |
| `test_evaluation.py` | 检索与 Agent 评测指标计算 |
| `test_ingestion_retrieval.py` | 文档加载、分块、嵌入、检索来源与分数 |
| `test_llm_client.py` | OpenAI 兼容客户端请求与解析 |
| `test_school_scope.py` | 长安大学官方资料范围、来源元数据、范围隔离 |
| `test_tools.py` | 申请进度查询、转人工工单、参数校验 |
| `test_workbench.py` | 四种协议工具回合、CSRF/DNS 重绑定防护、取消、历史隔离、密钥脱敏、上传校验、循环上限、SSE |

## 3. API 端到端测试

以真实服务（`127.0.0.1:7860`）+ Cookie 会话测试 `reports/WORKBENCH_API.md` 中列出的全部 15 个接口。API 模式使用本地 Mock 的 OpenAI 兼容服务（`/v1/chat/completions`），先返回 `search_knowledge` 工具调用，再返回含一个真实引用与一个虚构引用的回答。

| 分组 | 用例数 | 验证要点 |
|---|---|---|
| 页面与静态资源 | 9 | `/`、`workbench.html`、主要 JS/CSS/three.js 可加载；不存在资源 404；CSP、nosniff 响应头 |
| 健康检查 | 2 | 离线模式、12+ 文档；API 响应 `Cache-Control: no-store` |
| 安全防护 | 6 | 非本机 Host 403；跨站 Origin 403；`Sec-Fetch-Site: cross-site` 403；非 JSON 415；JSON 非对象 400；超 1MB 413 |
| 会话 CRUD | 4 | 初始列表为空；创建会话；新会话无消息；不存在会话 404 |
| 对话 `/api/chat` | 9 | RAG 回答带引用；返回 latency/session_id/scope；空问题澄清；超 4000 字 400；非字符串 400；会话 ID 过长 400；伪造会话 ID 拒绝；库外问题不编造；省略 session_id 自动建会话 |
| 业务工具 | 3 | `S1001/AP2026001` 返回审核中；跨两轮补全学号与申请号返回已通过；不存在记录不编造 |
| 转人工 | 2 | “不要转人工”不建工单；明确“请转人工客服”创建模拟工单 |
| SSE | 1 | `text/event-stream`，首个事件 `status`、末个事件 `done` |
| 历史持久化 | 2 | user/assistant 成对保存；会话出现在列表中 |
| 用户隔离 | 2 | 另一浏览器看不到、读不到他人会话 |
| 重命名/删除/reset | 5 | PATCH 重命名；空标题 400；DELETE 删除；`/api/reset` 删除；无 session_id 也成功 |
| 取消 | 1 | 空闲时 `/api/cancel` 返回 `ok=false` |
| 知识库查询 | 4 | 内置文档列表；关键词过滤；`chd_public` 文档均带 URL；非法 scope 400 |
| 资料范围对话 | 2 | 官方范围回答带提示前缀；同一会话切换范围被拒 |
| 上传与删除 | 14 | 缺 `X-Campus-Request` 403；上传 .md 成功；可检索；进入 RAG 回答；他人不可见；不进入官方范围；拒绝 .pdf、GBK 编码、空文件、含 NUL、超 500KB；他人不能删；内置文档不能删；本人删除成功 |
| 模型设置 | 15 | 设置脱敏且含预设；拒绝远程 HTTP、地址内嵌凭据、未知协议、非法模型 ID；保存 Mock 设置且 Key 不回显；health 切到 api；连接测试成功；ReAct 工具调用→回答；虚构引用替换为“来源未核验”；请求携带工具 schema；切换地址未填 Key 时清空旧 Key；无 Key 远程 API 明确报错；连接失败返回可读错误；切回离线 |
| 联网搜索 | 1 | 无 Tavily Key 时返回明确 400 提示，不返回编造内容、不 500 |
| 合计 | 82 | 全部通过 |

## 4. 前端测试

### 4.1 仓库自带 Playwright 冒烟脚本

运行方式：`NODE_PATH=<playwright> CAMPUS_EVIDENCE_NAME=kiro-run node tests/<name>.cjs`，逐个执行。

| 脚本 | 结果 | 说明 |
|---|---|---|
| `browser_smoke.cjs` | 通过（19 项） | 工作台对话、SSE、引用与工具轨迹、刷新恢复、重命名、Markdown 导出、三轮槽位记忆、知识过滤、上传与 XSS 安全查看、删除、供应商预设、8 个服务快捷入口、深色主题、375/768/1024/1440px 无横向溢出、移动导航与 Esc |
| `navigation_smoke.cjs` | 通过（13 项） | 首页与工作台同页切换、草稿传递、前进后退、挂载工作台真实 RAG、主题隔离、hash 直达恢复 |
| `landmark_film_smoke.cjs` | 通过（13 项） | 7 个照片地标、目录分组、相机推进与照片转场、反向恢复、快速连点取消旧动画、390px 移动端、减少动态、图片失败与无 WebGL 回退 |
| `ion_assistant_smoke.cjs` | 通过（10 项） | 3D 粒子助手、拖拽与方向键旋转、暂停/恢复、离开后停止渲染、RAG 状态联动、移动端降级、无 WebGL 回退 |
| `ion_recovery_smoke.cjs` | 通过（3 项） | WebGL 上下文丢失与恢复、no-webgl 与模块加载失败下仍可完成 RAG |
| `school_scope_smoke.cjs` | 通过（8 项） | 默认长安大学资料、上传隔离、官方引用链接、范围切换新开会话、375px 适配 |
| `branding_smoke.cjs` | 通过（8 项） | 产品名、标题、导出文件名、390px 布局 |
| `campus_motion_profile.cjs` | 通过 | 帧间隔采样，无超过 50ms 的帧与长任务（软件渲染，仅供参考） |
| `navigation_motion.cjs` | 通过 | 导航动效流程 |
| `campus_places_smoke.cjs` | 失败 | 断言 10 个地点，当前为 7 个 |
| `open_day_smoke.cjs` | 失败 | 目录断言 10 个入口，当前为 7 个 |
| `weishui_layout_smoke.cjs` | 失败 | 断言 `clinic` 等已移除地点及旧坐标关系 |
| `immersive_smoke.cjs` | 失败 | 依赖固定坐标 (800, 355) 点击图书馆，当前布局下未命中 |
| `map_explorer_smoke.cjs` | 失败 | 等待初始缩放 `30.00`，当前默认半径为 34 |
| `interaction_smoke.cjs` | 失败 | 测试工作台“服务探索”标签页，该区块已被 CSS 隐藏 |

6 个失败脚本的修改时间（10-01 17:04–19:26）都早于当前地点布局 `campus-layout.js`（10-01 20:03）。README 与 `landmark_film_smoke.cjs` 都以 7 个地点为准，因此判定为脚本过期。

### 4.2 补充前端交互测试（当前版本）

脚本按当前 7 地点页面编写，覆盖首页 3D 校园与迷你助手。视口为 1440×960 和 390×844，开启减少动态。

| # | 用例 | 结果 |
|---|---|---|
| 1 | 首页 7 个地点按钮、3D 画布、模型状态 | 通过 |
| 2 | 进入校园 | 通过 |
| 3 | 分区切换（总览 7 / 学习 5 / 生活 1） | 通过 |
| 4 | 旋转、缩放、复位相机 | 通过 |
| 5 | 俯视地图切换 | 通过 |
| 6 | 地图标签显示/隐藏 | 通过 |
| 7 | 展开/收起地图，Esc 收起 | 通过 |
| 8 | 日/夜景切换 | 通过 |
| 9 | 暂停/恢复动态 | 通过 |
| 10 | 点击地点打开详情面板（标题、资料、进度 1/7） | 通过 |
| 11 | 上一个/下一个地点循环 | 通过 |
| 12 | 未收录地点显示“尚未收录”并隐藏提问按钮 | 通过 |
| 13 | Esc 关闭地点面板 | 通过 |
| 14 | 地图搜索：命中、无结果、回车定位 | 通过 |
| 15 | 3D 场景射线拾取点击地标打开面板 | 通过 |
| 16 | 地点目录：7 个入口、3 组、Esc 关闭 | 通过 |
| 17 | 从目录选择地点打开详情 | 通过 |
| 18 | 照片场景“返回地图”关闭地点 | 通过 |
| 19 | 办事手册搜索：过滤与空结果 | 通过 |
| 20 | 迷你助手真实问答并显示来源 | 通过 |
| 21 | 迷你助手空输入不发送 | 通过 |
| 22 | 返回首页照片 | 通过 |
| 23 | 无浏览器控制台错误 | 失败：`/favicon.ico` 404 |
| 24 | 390px 移动端无横向溢出且可进入校园 | 通过 |

说明：地点打开后进入全屏照片场景，此时 `#close-place` 隐藏，由 `#landmark-back` 返回，属于设计行为。第一版补充脚本在这里点错了按钮，修正后通过。

## 5. 发现的问题与建议

| 级别 | 问题 | 建议 |
|---|---|---|
| 中 | 6 个前端冒烟脚本按旧版 10 地点/旧坐标编写，持续失败 | 按 7 地点布局更新断言，或删除已被 `landmark_film_smoke.cjs` 覆盖的脚本 |
| 低 | 工作台“今天，想去哪里？”服务探索区块被 `ion-assistant.css` 中 `.ion-enabled #welcome { display: none }` 隐藏，`explore.js` 仍在挂载 | 如果是有意下线，删除对应 HTML/JS/CSS；否则恢复显示 |
| 低 | `/favicon.ico` 返回 404，控制台出现资源错误 | 在 `index.html` 与 `workbench.html` 中添加 `<link rel="icon">`，或提供 favicon 文件 |
| 提示 | 无 Tavily Key 时 DuckDuckGo 与 Bing RSS 兜底均无结果，联网搜索返回 400（提示明确，无编造） | 演示前配置 Tavily Key |

## 6. 未覆盖范围

- 真实大模型供应商：API 模式仅用本地 Mock 验证了 openai 协议；responses、anthropic、gemini 只有单元测试中的协议 Mock
- Tavily 真实联网搜索
- 回答进行中的取消：只有单元测试覆盖，未在真实服务上测试
- 无障碍：仅检查键盘操作与 ARIA 状态，未使用读屏软件测试，不构成 WCAG 合规结论
- 性能数据来自 headless 软件渲染，不代表真实设备帧率
