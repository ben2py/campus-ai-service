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

## 模拟人工工单查询

新增工具 `query_handoff_ticket(ticket_id)`，离线和 API 模式均可查询当前浏览器工作空间创建的工单。返回数据库中的状态、创建时间和转接原因；查询不会创建新工单，也不会修改状态。

可在同一对话中测试：

1. 输入“请转人工客服，我需要复核奖学金材料”，获得 `HF-` 开头的工单编号。
2. 输入“查询刚才那个工单的进度”，或“查询工单 HF-XXXXXXXX”（将示例编号替换为实际编号）。
3. 未提供编号且会话中没有可用工单时，助手先请求补充编号；另一个浏览器工作空间不能读取该工单。

这些是本地教学模拟工单，未通知真实客服，状态不会自动更新。升级前没有归属信息的旧工单保留在数据库中，但不会向任意工作空间开放查询。

工单保存在本地 SQLite 数据库，同一浏览器工作空间可在新会话、刷新页面或重启服务后查询。API 模式的创建和查询回复以实际工具执行结果为准：模型未调用工具时，系统对明确的工单请求补执行；模型自行编写的编号不作为创建凭据。一轮转接只创建一个工单。

## API 模式语义检索

在 `.env` 中填写 `EMBEDDING_API_KEY`（默认阿里云百炼 `text-embedding-v4`，可改为任意 OpenAI 兼容 `/embeddings` 服务）后重启。启用大模型时，`search_knowledge` 改用「云端语义向量 70% + 关键词 30%」混合检索；未配置密钥、离线模式或接口失败时使用本地哈希向量，并在状态栏和 trace 中注明回退。`/api/health` 的 `retrieval` 字段显示当前检索方式。

文本块向量只在内存中缓存，同一文本只请求一次。资料文本（含个人上传）与检索词会发送到 Embedding 服务。配置后运行下面的命令，对比两种检索在原题、口语改写和库外题上的表现，并按输出调整 `SEMANTIC_THRESHOLD`：

```bash
python -m src.evaluation.semantic_check
```

不要将 `.env`、`data/*.db` 或个人密钥提交到仓库。

## 校园地图、步行路线与定位
三维校园由 `static/campus-map.json` 渲染。这份文件由 `scripts/build_campus_map.py` 根据 `data/osm/` 中的 OpenStreetMap 离线快照（2026-10-08）生成，包括建筑轮廓、路网、校门和设施点，数据许可为 ODbL，© OpenStreetMap contributors。运行时不访问外网。如需更新快照，替换 `data/osm/` 里的文件后执行：
```bash
python scripts/build_campus_map.py
```
- 回答涉及线下地点时（如卡务中心、师生服务大厅、校医院、菜鸟驿站），结果里会带上 `map_targets`，回答下方会出现“在地图中查看路线”。地点由应用按问题和回答识别，不由模型生成。API 模式下，模型还可以调用 `locate_campus_place` 工具，说明地点所在片区和最近的校门。
- `POST /api/map/route` 的参数为 `{"from": {"poi"|"lat","lon"|"x","z"}, "to": {"poi"|"group"|"x","z"}}`。它在路网上用 Dijkstra 算法求最短步行路线，返回折线、距离、步行和骑行时间以及分步指引。路线只能经校门进出围墙；`group` 表示前往最近的一处（如最近的餐厅）。
- “我的位置”使用浏览器定位（WGS84）。浏览器只在 `127.0.0.1` 或 HTTPS 下允许定位。位置只发送到本机服务计算路线，不写入日志或数据库。离校区较远或无法定位时，路线会从校门出发，也可以在地图上点选起点。连续定位会经过按精度加权的平滑处理。浏览器若返回国测局 GCJ-02 坐标（偏差约 450 米），或 Wi-Fi 定位有固定偏差，可点“校准”，在地图上点出实际位置：系统会自动判断属于哪种情况，并把修正保存在本机浏览器中。
