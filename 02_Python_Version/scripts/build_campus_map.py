"""把 OpenStreetMap 快照编译为渭水校区本地地图数据（static/campus-map.json）。

输入（均为已提交的离线快照，运行时不访问网络）：
- data/osm/weishui_osm_2026-10-08.json：建筑、道路、水体、场地、校门等 way/node（Overpass API 导出）
- data/osm/weishui_poi_points_2026-10-08.json：餐厅、驿站、卡务中心等点状 POI（Photon 反向地理编码导出）

坐标：以校区外接框中心为原点的局部平面坐标，1 个单位 = 50 m，x 向东、z 向南（与 Three.js 场景一致）。
数据许可：ODbL 1.0，© OpenStreetMap contributors。

用法：python scripts/build_campus_map.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSM = ROOT / "data" / "osm" / "weishui_osm_2026-10-08.json"
POINTS = ROOT / "data" / "osm" / "weishui_poi_points_2026-10-08.json"
OUT = ROOT / "static" / "campus-map.json"

UNIT_M = 50.0
LAT0, LON0 = 34.3716204, 108.8975444  # 校区外接框中心
M_PER_DEG_LAT = 110_950.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))


def to_xz(lat: float, lon: float) -> list[float]:
    return [round((lon - LON0) * M_PER_DEG_LON / UNIT_M, 3), round(-(lat - LAT0) * M_PER_DEG_LAT / UNIT_M, 3)]


def centroid(points):
    xs = [p[0] for p in points]
    zs = [p[1] for p in points]
    return [round(sum(xs) / len(xs), 3), round(sum(zs) / len(zs), 3)]


def inside(point, polygon) -> bool:
    x, z = point
    hit = False
    for (x1, z1), (x2, z2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            hit = not hit
    return hit


LEVELS = {"dormitory": 6, "apartments": 6, "university": 5, "college": 4, "school": 3, "office": 4, "hospital": 4,
          "commercial": 2, "retail": 1, "house": 2, "industrial": 2, "warehouse": 1, "grandstand": 1, "yes": 3}
NAME_LEVELS = {"逸夫图书馆": 6, "图书馆教学楼": 4, "师生服务大厅": 3, "北辰楼": 8, "乾健体育馆": 3, "大学生活动中心": 4}
ROAD_WIDTH = {"primary": 16, "secondary": 14, "tertiary": 12, "residential": 8, "unclassified": 8, "living_street": 6,
              "service": 5, "pedestrian": 6, "footway": 3, "path": 2.5, "cycleway": 3, "steps": 2.5, "track": 3}

# 场景地标（campus.js 中 places 的 key）→ OSM 名称；未收录的组团给出近似说明。
SCENE_PLACES = {
    "library": {"names": ["逸夫图书馆"], "label": "逸夫图书馆"},
    "study": {"names": ["修远教学楼一区", "修远教学楼二区", "修远教学楼三区"], "label": "修远教学楼"},
    "life": {"names": ["鸿翔园1号公寓", "鸿翔园2号公寓", "鸿翔园3号公寓", "鸿翔园4号公寓", "鸿翔园5号公寓", "鸿翔园6号公寓"], "label": "鸿翔园生活社区"},
    "activities": {"names": ["大学生活动中心"], "label": "大学生活动中心", "note": "OSM 未收录“长安文化艺术中心”，此处以大学生活动中心位置示意，请以现场标识为准。"},
    "highway": {"names": ["公路学院"], "label": "公路学院"},
    "materials": {"names": ["弘毅园道路实验中心", "弘毅园桥结实验中心", "弘毅园岩隧实验中心"], "label": "弘毅园实验中心组团", "note": "建工、材料学院实验组团按弘毅园实验中心位置示意。"},
    "information": {"names": ["明德园智通大厦", "明德园创新大厦", "明德园扶轮大厦"], "label": "明德园科研组团", "note": "信息、交通学院组团按明德园大厦群位置示意，具体办公室请核对学院官网。"},
}

# 可导航地点目录：id, 名称, 分类, OSM 名称（多个取中心）, 关键词, 备注
CATALOG = [
    ("library", "逸夫图书馆", "study", ["逸夫图书馆"], "图书馆 借书 还书 借阅 续借 阅览 自习室 数据库 图书", ""),
    ("xiuyuan", "修远教学楼", "study", ["修远教学楼一区", "修远教学楼二区", "修远教学楼三区"], "修远 教学楼 上课 教室", ""),
    ("mingyuan", "明远教学楼", "study", ["明远教学楼A栋", "明远教学楼B栋", "明远教学楼C栋", "明远教学楼D栋", "明远教学楼E栋"], "明远 教学楼 上课 教室", ""),
    ("hongyuan", "鸿远教学楼", "study", ["鸿远教学楼一区", "鸿远教学楼二区"], "鸿远 教学楼 上课 教室", ""),
    ("mingde", "明德园科研组团", "study", ["明德园智通大厦", "明德园创新大厦", "明德园扶轮大厦"], "明德园 智通大厦 创新大厦 扶轮大厦 信息学院 运输工程学院 电控", "学院办公室请核对学院官网。"),
    ("highway_school", "公路学院", "study", ["公路学院"], "公路学院 道路 桥梁", ""),
    ("hongyi_labs", "弘毅园实验中心", "study", ["弘毅园道路实验中心", "弘毅园桥结实验中心", "弘毅园岩隧实验中心"], "弘毅园 实验中心 结构 土工 岩隧 桥结", ""),
    ("beichen", "北辰楼", "service", ["北辰楼"], "北辰楼 会议中心", ""),
    ("service_hall", "师生服务大厅", "service", ["师生服务大厅"], "师生服务大厅 服务大厅 办事大厅 一站式 在读证明 成绩单 证明 盖章 学籍 户籍 档案 办事", "线下业务窗口与开放时间以学校通知为准。"),
    ("card_center", "卡务中心", "service", ["卡务中心"], "卡务中心 校园卡 一卡通 挂失 补卡 补办 解挂 饭卡 卡务", "校园卡线下办理点（OSM 标注）；办理时间以学校通知为准。"),
    ("logistics_west", "西区后勤管理中心", "service", ["西区后勤管理中心"], "后勤 报修 维修 水电 宿舍维修 后勤管理", "宿舍报修通常先走线上平台，线下可咨询后勤。"),
    ("hospital", "长安大学医院", "health", ["长安大学医院"], "校医院 医院 看病 门诊 生病 发烧 体检 医保 受伤 急诊", "紧急情况请直接拨打 120。"),
    ("activity_center", "大学生活动中心", "activity", ["大学生活动中心"], "活动中心 大学生活动中心 社团 活动 演出 晚会 文化艺术中心", ""),
    ("hongxiang", "鸿翔园学生社区", "life", ["鸿翔园学生社区"], "鸿翔园 学生社区 宿舍 公寓 寝室 住宿", ""),
    ("zhaohui", "朝晖园学生社区", "life", ["朝晖园学生社区示范性园区"], "朝晖园 学生社区 宿舍 公寓 寝室 住宿", ""),
    ("hongjian", "鸿渐园学生社区", "life", ["鸿渐园学生社区示范性园区"], "鸿渐园 学生社区 宿舍 公寓 寝室 住宿", ""),
    ("zilan", "滋兰苑餐厅", "dining", ["滋兰苑餐厅"], "滋兰苑 餐厅 食堂 吃饭", ""),
    ("caiqin", "采芹苑餐厅", "dining", ["采芹苑餐厅"], "采芹苑 餐厅 食堂 吃饭", ""),
    ("shuhui", "树蕙园餐厅", "dining", ["树蕙园餐厅"], "树蕙园 餐厅 食堂 吃饭", ""),
    ("tianxingjian", "天行健餐厅", "dining", ["天行健餐厅"], "天行健 餐厅 食堂 吃饭", ""),
    ("express_west", "西区菜鸟驿站", "shop", ["西区菜鸟驿站"], "菜鸟驿站 快递 取件 寄件 包裹 驿站", ""),
    ("express_east", "东区菜鸟驿站", "shop", ["东区菜鸟驿站"], "菜鸟驿站 快递 取件 寄件 包裹 驿站", ""),
    ("bath_west", "西区浴室", "life", ["西区浴室"], "浴室 洗澡", ""),
    ("bath_east", "东区浴室", "life", ["东区浴室"], "浴室 洗澡", ""),
    ("street_1951", "长大1951商业街", "shop", ["长大1951商业街"], "商业街 1951 超市 便利店 购物", ""),
    ("zhaohui_stadium", "朝晖体育场", "sports", ["朝晖体育场"], "朝晖体育场 体育场 操场 跑步 体测 田径", ""),
    ("hongxiang_stadium", "鸿翔体育场", "sports", ["鸿翔体育场"], "鸿翔体育场 体育场 操场 跑步 体测 足球", ""),
    ("qianjian_gym", "乾健体育馆", "sports", ["乾健体育馆"], "乾健体育馆 体育馆 篮球 羽毛球 体育课", ""),
    ("qianjian_pool", "乾健游泳馆", "sports", ["乾健游泳馆"], "游泳馆 游泳", ""),
    ("traffic_museum", "交通馆", "activity", ["交通馆"], "交通馆 博物馆 展馆 参观", ""),
    ("architecture_museum", "建筑馆", "activity", ["建筑馆"], "建筑馆 博物馆 展馆 参观", ""),
    ("gate_east", "东门", "gate", ["东门"], "东门 校门", ""),
    ("gate_north", "北门", "gate", ["北门"], "北门 校门", ""),
    ("gate_south", "南门", "gate", ["南门"], "南门 校门", ""),
    ("gate_southwest", "西南门", "gate", ["西南门"], "西南门 校门", ""),
    ("bus_stop", "尚苑路长安大学渭水校区站", "transit", ["尚苑路长安大学渭水校区"], "公交 公交站 坐车 车站 出行", "公交线路与班次请以实时公交信息为准。"),
]
# 同类多选：回答中提到这一类事项时，导航到离起点最近的一处。
GROUPS = {
    "dining": {"name": "最近的餐厅", "members": ["zilan", "caiqin", "shuhui", "tianxingjian"], "keywords": "食堂 餐厅 吃饭 就餐 饭堂"},
    "express": {"name": "最近的菜鸟驿站", "members": ["express_west", "express_east"], "keywords": "快递 驿站 取件 寄件 包裹 菜鸟"},
    "bath": {"name": "最近的浴室", "members": ["bath_west", "bath_east"], "keywords": "浴室 洗澡"},
    "gate": {"name": "最近的校门", "members": ["gate_east", "gate_north", "gate_south", "gate_southwest"], "keywords": "校门 出校 进校 离校"},
    "teaching": {"name": "最近的教学楼", "members": ["xiuyuan", "mingyuan", "hongyuan"], "keywords": "教学楼 教室 上课"},
    "community": {"name": "最近的学生社区", "members": ["hongxiang", "zhaohui", "hongjian"], "keywords": "学生社区 宿舍 公寓 寝室"},
    "stadium": {"name": "最近的体育场", "members": ["zhaohui_stadium", "hongxiang_stadium"], "keywords": "操场 体育场 跑步 体测"},
}


def main() -> None:
    raw = json.loads(OSM.read_text(encoding="utf-8"))
    points = json.loads(POINTS.read_text(encoding="utf-8"))["points"]
    ways = [e for e in raw["elements"] if e["type"] == "way"]
    nodes = [e for e in raw["elements"] if e["type"] == "node"]
    campus_way = next(w for w in ways if w["tags"].get("amenity") == "university" and "渭水" in w["tags"].get("name", ""))
    campus = [to_xz(lat, lon) for lat, lon in campus_way["geometry"]]
    if campus[0] == campus[-1]:
        campus = campus[:-1]

    named: dict[str, list[list[float]]] = {}
    buildings, areas, roads = [], [], []
    for w in ways:
        t = w["tags"]
        pts = [to_xz(lat, lon) for lat, lon in w["geometry"]]
        closed = len(pts) > 3 and pts[0] == pts[-1]
        name = t.get("name", "")
        if closed:
            ring = pts[:-1]
            c = centroid(ring)
            if name:
                named.setdefault(name, []).append(c)
            if "building" in t:
                kind = t["building"]
                levels = float(t.get("building:levels") or NAME_LEVELS.get(name) or LEVELS.get(kind, 3))
                on_campus = inside(c, campus)
                cls = "context"
                if on_campus:
                    cls = {"dormitory": "dorm", "apartments": "dorm", "university": "teach", "college": "teach", "school": "teach",
                           "hospital": "health", "grandstand": "sport"}.get(kind, "other")
                    if "餐厅" in name:
                        cls = "dining"
                    elif name in ("逸夫图书馆",):
                        cls = "library"
                    elif name in ("师生服务大厅", "大学生活动中心", "北辰楼", "北辰楼会议中心"):
                        cls = "service"
                    elif "体育" in name or "游泳" in name:
                        cls = "sport"
                    elif "教师公寓" in name or "楼" == name[-1:] and "号" in name:
                        cls = "dorm"
                item = {"p": ring, "h": round(levels * 3.3, 1), "c": cls}
                if name:
                    item["n"] = name
                buildings.append(item)
                continue
            kind = None
            if t.get("natural") == "water":
                kind = "water"
            elif t.get("leisure") in ("pitch",):
                kind = "pitch"
            elif t.get("leisure") in ("track",):
                kind = "track"
            elif t.get("leisure") in ("stadium", "sports_centre"):
                kind = "sports"
            elif t.get("landuse") in ("grass", "forest", "meadow") or t.get("leisure") == "park":
                kind = "green"
            elif t.get("amenity") == "parking":
                kind = "parking"
            if kind:
                item = {"p": ring, "k": kind}
                if name:
                    item["n"] = name
                areas.append(item)
                continue
        if "highway" in t and t["highway"] in ROAD_WIDTH:
            on = sum(inside(p, campus) for p in pts) / len(pts)
            item = {"p": pts, "w": ROAD_WIDTH[t["highway"]], "k": t["highway"], "in": round(on, 2)}
            if name:
                item["n"] = name
                named.setdefault(name, []).append(centroid(pts))
            roads.append(item)

    for n in nodes:
        t = n.get("tags", {})
        if t.get("name"):
            named.setdefault(t["name"], []).append(to_xz(n["lat"], n["lon"]))
    for p in points:
        named.setdefault(p["name"], []).append(to_xz(p["lat"], p["lon"]))

    def locate_names(names):
        found = [c for name in names for c in named.get(name, [])[:1]]
        if not found:
            raise SystemExit(f"OSM 快照中找不到：{names}")
        return centroid(found)

    pois = []
    for pid, name, category, osm_names, keywords, note in CATALOG:
        x, z = locate_names(osm_names)
        item = {"id": pid, "name": name, "category": category, "x": x, "z": z, "keywords": keywords.split()}
        if note:
            item["note"] = note
        pois.append(item)
    catalog_names = {n for _, _, _, names, _, _ in CATALOG for n in names}
    for b in buildings:
        # 其余已命名的校内建筑也可按名称导航（不含校外背景建筑）。
        if b.get("n") and b["c"] != "context" and b["n"] not in catalog_names:
            x, z = centroid(b["p"])
            pois.append({"id": "b" + str(len(pois)), "name": b["n"], "category": "building", "x": x, "z": z, "keywords": []})
            catalog_names.add(b["n"])

    places = {}
    for key, spec in SCENE_PLACES.items():
        x, z = locate_names(spec["names"])
        places[key] = {"x": x, "z": z, "label": spec["label"], **({"note": spec["note"]} if "note" in spec else {})}

    data = {
        "meta": {
            "name": "长安大学渭水校区",
            "source": "OpenStreetMap",
            "osm_base": raw["osm_base"],
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors",
            "unit_m": UNIT_M,
            "origin": {"lat": LAT0, "lon": LON0},
            "m_per_deg": {"lat": M_PER_DEG_LAT, "lon": round(M_PER_DEG_LON, 3)},
            "note": "公开地图数据整理，仅供校内步行参考；施工、封闭与开放时间以现场和学校通知为准。",
        },
        "campus": campus,
        "buildings": buildings,
        "areas": areas,
        "roads": roads,
        "pois": pois,
        "groups": {k: {**v, "keywords": v["keywords"].split()} for k, v in GROUPS.items()},
        "places": places,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(buildings)} buildings, {len(areas)} areas, {len(roads)} roads, {len(pois)} pois, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
