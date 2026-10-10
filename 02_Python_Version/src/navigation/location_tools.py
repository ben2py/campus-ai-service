"""让 Agent 读取用户当前位置，并结合位置回答“我在哪、附近有什么、怎么去”。

位置由前端地图（浏览器定位或地图点选）随本轮问题提交，只在内存中使用：
工具输出只给出片区、地点名称和距离，不回传原始坐标，避免坐标写入会话记录。
"""
from __future__ import annotations

import math

from .campus_map import CampusMap, RouteError

MAX_ACCURACY_M = 5000.0
SOURCES = {"gps": "浏览器定位", "pick": "地图点选", "manual": "手动设置的位置"}
CATEGORY_LABEL = {
    "building": "楼宇", "study": "学习", "life": "生活服务", "service": "办事服务", "dining": "餐饮",
    "sports": "运动", "gate": "校门", "activity": "活动场所", "shop": "商店", "health": "医疗", "transit": "交通",
}
NEARBY_LIMIT = 8  # 先按直线距离取的候选数，再逐个计算步行距离
NEARBY_DIRECT_M = 80  # 直线距离在此以内视为“就在旁边”
MAX_STEPS = 10


def parse_user_location(value: object) -> dict | None:
    """校验前端提交的位置。None 表示未提供；格式错误抛出 ValueError。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("位置信息格式无效。")
    x, z = value.get("x"), value.get("z")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) < 500 for v in (x, z)):
        raise ValueError("位置坐标无效。")
    source = value.get("source", "gps")
    if source not in SOURCES:
        raise ValueError("位置来源无效。")
    accuracy = value.get("accuracy")
    if accuracy is not None and not (isinstance(accuracy, (int, float)) and not isinstance(accuracy, bool) and math.isfinite(accuracy) and 0 <= accuracy <= MAX_ACCURACY_M):
        raise ValueError("定位精度无效。")
    return {"x": float(x), "z": float(z), "source": source, "accuracy": None if accuracy is None else round(float(accuracy))}


USER_LOCATION_SCHEMA = {
    "name": "get_user_location",
    "description": "读取用户在校园地图中共享的当前位置：是否在渭水校区内、所在片区、最近的地点与校门、定位精度。用户问“我在哪/附近/离我多远/从这里怎么走”或需要按位置给建议时先调用；未共享时会提示用户开启定位。",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}

NEARBY_SCHEMA = {
    "name": "find_nearby_places",
    "description": "按用户当前位置查找附近地点，按步行距离由近到远排序，返回步行/骑行时间。可按类别或关键词（如“食堂”“快递”“打印”“图书馆”）筛选；需要用户已共享位置。",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": sorted(CATEGORY_LABEL), "description": "地点类别，可选"},
            "query": {"type": "string", "minLength": 1, "maxLength": 40, "description": "关键词，可选"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "description": "返回数量，默认 3"},
        },
        "additionalProperties": False,
    },
}

ROUTE_SCHEMA = {
    "name": "plan_campus_route",
    "description": "规划渭水校区内的步行路线，返回距离、步行/骑行时间和分步指引。起点默认是用户当前位置，也可指定起点地点名；终点可以是具体地点名，也可以是“食堂/快递/浴室/校门/教学楼/宿舍/操场”等类别（自动选最近的一处）。",
    "parameters": {
        "type": "object",
        "properties": {
            "destination": {"type": "string", "minLength": 1, "maxLength": 80, "description": "终点地点名或类别"},
            "origin": {"type": "string", "minLength": 1, "maxLength": 80, "description": "起点地点名；不填则使用用户当前位置"},
        },
        "required": ["destination"],
        "additionalProperties": False,
    },
}

LOCATION_SCHEMAS = [USER_LOCATION_SCHEMA, NEARBY_SCHEMA, ROUTE_SCHEMA]
LOCATION_TOOLS = {s["name"] for s in LOCATION_SCHEMAS}

NO_LOCATION = {
    "ok": False,
    "available": False,
    "message": "用户本轮没有共享位置。请提示用户在校园地图中点击“定位”按钮（或在路线面板中“在地图上点选”起点）后再问；也可以请用户直接说出所在的楼或地点。",
}


def _round_m(d: float) -> int:
    return int(round(d, -1)) if d >= 100 else int(round(d))


def _nearest_pois(m: CampusMap, x: float, z: float, categories=None, limit=1, exclude=("gate",)):
    pois = [p for p in m.pois.values() if (categories is None or p["category"] in categories) and p["category"] not in exclude]
    pois.sort(key=lambda p: math.dist((x, z), (p["x"], p["z"])))
    return pois[:limit]


def describe_user_location(m: CampusMap, loc: dict | None) -> dict:
    if not loc:
        return dict(NO_LOCATION)
    x, z = loc["x"], loc["z"]
    on_campus = m.on_campus(x, z)
    gate = m.nearest_gate(x, z)
    out = {
        "ok": True,
        "available": True,
        "source": SOURCES[loc["source"]],
        "on_campus": on_campus,
        "zone": m.zone(x, z) if on_campus else "校区外",
        "nearest_gate": {"name": gate["name"], "distance_m": _round_m(m.meters((x, z), (gate["x"], gate["z"])))},
    }
    if loc.get("accuracy") is not None:
        out["accuracy_m"] = loc["accuracy"]
    near = _nearest_pois(m, x, z, limit=3)
    out["nearest_places"] = [{"name": p["name"], "category": CATEGORY_LABEL.get(p["category"], p["category"]), "distance_m": _round_m(m.meters((x, z), (p["x"], p["z"])))} for p in near]
    if not on_campus:
        out["campus_distance_m"] = _round_m(math.hypot(x, z) * m.unit)
    notes = []
    if loc["source"] == "gps" and (loc.get("accuracy") or 0) > 100:
        notes.append(f"定位精度约 ±{loc['accuracy']} 米，偏粗，建议向用户确认所在位置。")
    if not on_campus:
        notes.append("用户当前不在渭水校区内，校内路线将从最近的校门开始计算更合适。")
    notes.append("回答中不要输出坐标；按片区、附近地点和距离描述即可。")
    out["note"] = " ".join(notes)
    return out


def _resolve_place(m: CampusMap, text: str) -> dict | None:
    """地点名或类别 → 路线终点规格：{"poi": id} 或 {"group": key}。"""
    q = text.strip()
    exact = next((p for p in m.pois.values() if p["name"] == q), None)
    if exact:
        return {"poi": exact["id"]}
    for key, group in m.groups.items():
        if q == group["name"] or any(k in q for k in group["keywords"]):
            # 用户点名了同类中的具体地点（如“鸿翔园”），则使用该地点。
            named = [mid for mid in group["members"] if m.pois[mid]["name"] in q]
            return {"poi": named[0]} if named else {"group": key}
    found = m.search(q, 1)
    return {"poi": found[0]["id"]} if found else None


def _route_summary(route: dict) -> dict:
    steps = [s["text"] for s in route["steps"]]
    if len(steps) > MAX_STEPS:
        steps = steps[: MAX_STEPS - 1] + [f"……其余 {len(steps) - MAX_STEPS + 1} 步见地图路线"]
    out = {
        "ok": True,
        "from": route["from"]["name"],
        "to": route["to"]["name"],
        "to_id": route["to"].get("poi"),
        "distance_m": route["distance_m"],
        "walk_min": route["walk_min"],
        "bike_min": route["bike_min"],
        "steps": steps,
    }
    if route.get("group"):
        out["chosen_from"] = f"在 {route['group']['candidates']} 处同类地点中最近"
    if route.get("approach_m", 0) > 80:
        out["approach_m"] = route["approach_m"]
    return out


def find_nearby(m: CampusMap, loc: dict | None, category: str | None = None, query: str | None = None, limit: int = 3) -> dict:
    if not loc:
        return dict(NO_LOCATION)
    x, z = loc["x"], loc["z"]
    candidates = list(m.pois.values())
    if category:
        candidates = [p for p in candidates if p["category"] == category]
    if query:
        q = query.strip()
        group_members = {mid for g in m.groups.values() if q == g["name"] or any(k in q for k in g["keywords"]) for mid in g["members"]}
        candidates = [p for p in candidates if p["id"] in group_members or p["name"] in q or q in p["name"] or any(k in q or q in k for k in p["keywords"])]
    if not candidates:
        return {"ok": False, "message": "地图中没有符合条件的地点；可换一个关键词，或调用 locate_campus_place 按名称查询。"}
    # 先按直线距离粗筛，再对候选计算真实步行距离排序。
    candidates.sort(key=lambda p: math.dist((x, z), (p["x"], p["z"])))
    results = []
    for p in candidates[:NEARBY_LIMIT]:
        item = {"id": p["id"], "name": p["name"], "category": CATEGORY_LABEL.get(p["category"], p["category"]), "zone": m.zone(p["x"], p["z"]),
                "straight_m": _round_m(m.meters((x, z), (p["x"], p["z"])))}
        if item["straight_m"] <= NEARBY_DIRECT_M:
            # 已在地点旁边：路网吸附会绕到别的路段，直接按直线距离报告。
            item.update(walk_m=item["straight_m"], walk_min=1, bike_min=1, here=True)
        else:
            try:
                r = m.route({"x": x, "z": z, "name": "我的位置"}, {"poi": p["id"]})
                item.update(walk_m=r["distance_m"], walk_min=r["walk_min"], bike_min=r["bike_min"])
            except RouteError:
                pass
        if p.get("note"):
            item["note"] = p["note"]
        results.append(item)
    results.sort(key=lambda i: i.get("walk_m", i["straight_m"] * 1.4))
    limit = max(1, min(int(limit or 3), 5))
    return {"ok": True, "results": results[:limit], "on_campus": m.on_campus(x, z),
            "note": "距离来自 OpenStreetMap 公开路网的估算；开放时间与是否营业以学校通知和现场为准。回答中不要输出坐标。"}


def plan_route(m: CampusMap, loc: dict | None, destination: str, origin: str | None = None) -> dict:
    dest = _resolve_place(m, destination)
    if not dest:
        return {"ok": False, "message": f"地图中未找到“{destination}”，请换一个名称或类别。"}
    if origin:
        start = _resolve_place(m, origin)
        if not start or "group" in start:
            return {"ok": False, "message": f"起点“{origin}”需要是具体地点名称。"}
    elif loc:
        start = {"x": loc["x"], "z": loc["z"], "name": "你的位置"}
    else:
        return dict(NO_LOCATION, message=NO_LOCATION["message"] + " 或在 origin 中指定起点地点名。")
    if "x" in start and "poi" in dest:
        p = m.pois[dest["poi"]]
        d = _round_m(m.meters((start["x"], start["z"]), (p["x"], p["z"])))
        if d <= NEARBY_DIRECT_M:
            return {"ok": True, "from": "你的位置", "to": p["name"], "to_id": p["id"], "distance_m": d, "walk_min": 1, "bike_min": 1,
                    "steps": [f"{p['name']}就在你附近，直线约 {d} 米"], "note": "界面会附带地图位置，回答中无需给出坐标。"}
    fallback = None
    if "x" in start and not m.on_campus(start["x"], start["z"]) and math.hypot(start["x"], start["z"]) * m.unit > 3000:
        target = m.pois[dest["poi"]] if "poi" in dest else m.pois[m.groups[dest["group"]]["members"][0]]
        gate = m.nearest_gate(target["x"], target["z"])
        start = {"poi": gate["id"]}
        fallback = f"用户距校区较远，已改为从{gate['name']}出发规划校内路线。"
    try:
        out = _route_summary(m.route(start, dest))
    except RouteError as exc:
        return {"ok": False, "message": str(exc)}
    out["note"] = " ".join(filter(None, [fallback, "路线基于公开地图估算，施工或封闭以现场为准；界面会附带地图路线，回答中无需给出坐标。"]))
    return out


def execute_location_tool(m: CampusMap, name: str, args: dict, loc: dict | None) -> dict:
    if name == "get_user_location":
        return describe_user_location(m, loc)
    if name == "find_nearby_places":
        return find_nearby(m, loc, args.get("category"), args.get("query"), args.get("limit", 3))
    if name == "plan_campus_route":
        return plan_route(m, loc, args["destination"], args.get("origin"))
    return {"ok": False, "message": "未注册的位置工具。"}
