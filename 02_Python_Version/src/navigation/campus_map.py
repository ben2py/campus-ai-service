"""渭水校区地图：地点目录、步行路网、最短路径与问题→地点匹配。

数据来自 static/campus-map.json（scripts/build_campus_map.py 由 OpenStreetMap 快照生成）。
坐标为局部平面坐标：1 单位 = meta.unit_m 米，x 向东，z 向南。
"""
from __future__ import annotations

import heapq
import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "static" / "campus-map.json"
WALK_M_PER_MIN = 75.0  # 约 4.5 km/h
BIKE_M_PER_MIN = 200.0  # 约 12 km/h
MAX_START_DISTANCE_M = 5000.0
GATE_RADIUS_M = 60.0
ROAD_LABEL = {"footway": "步行道", "path": "小路", "pedestrian": "步行街", "steps": "台阶", "cycleway": "骑行道", "service": "校内支路"}
TURNS = ((25, "直行"), (60, "稍向{side}"), (150, "{side}转"), (181, "掉头"))
COMPASS = ("东", "东北", "北", "西北", "西", "西南", "南", "东南")
ZONES_X = ((-8, "西区"), (8, "中部"), (math.inf, "东区"))


class RouteError(ValueError):
    """起点、终点无法解析或不可达。"""


@dataclass
class _Graph:
    points: list[tuple[float, float]] = field(default_factory=list)
    adj: list[list[tuple[int, float, str]]] = field(default_factory=list)
    index: dict[tuple[float, float], int] = field(default_factory=dict)
    segments: list[tuple[int, int, str]] = field(default_factory=list)

    def node(self, p: tuple[float, float]) -> int:
        key = (round(p[0], 3), round(p[1], 3))
        if key not in self.index:
            self.index[key] = len(self.points)
            self.points.append(key)
            self.adj.append([])
        return self.index[key]


def _inside(x: float, z: float, polygon: list[list[float]]) -> bool:
    hit = False
    for (x1, z1), (x2, z2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            hit = not hit
    return hit


def _project(px: float, pz: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float]:
    """点到线段的最近点；返回 (x, z, t)。"""
    dx, dz = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (pz - a[1]) * dz) / length2))
    return a[0] + dx * t, a[1] + dz * t, t


class CampusMap:
    def __init__(self, path: Path = DEFAULT_PATH):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.meta = data["meta"]
        self.unit = float(self.meta["unit_m"])
        self.campus = data["campus"]
        self.pois = {p["id"]: p for p in data["pois"]}
        self.groups = data["groups"]
        self.places = data["places"]
        self.gates = [p for p in data["pois"] if p["category"] == "gate"]
        self.graph = self._build_graph(data["roads"])

    # ---------- 坐标 ----------
    def to_xz(self, lat: float, lon: float) -> tuple[float, float]:
        o, m = self.meta["origin"], self.meta["m_per_deg"]
        return (lon - o["lon"]) * m["lon"] / self.unit, -(lat - o["lat"]) * m["lat"] / self.unit

    def meters(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.dist(a, b) * self.unit

    def on_campus(self, x: float, z: float) -> bool:
        return _inside(x, z, self.campus)

    def zone(self, x: float, z: float) -> str:
        ew = next(name for limit, name in ZONES_X if x < limit)
        ns = "北部" if z < -5 else "南部" if z > 5 else ""
        return ew + ns

    def nearest_gate(self, x: float, z: float) -> dict:
        return min(self.gates, key=lambda g: math.dist((x, z), (g["x"], g["z"])))

    # ---------- 路网 ----------
    def _build_graph(self, roads: list[dict]) -> _Graph:
        g = _Graph()
        gates = [(p["x"], p["z"]) for p in self.gates]
        for road in roads:
            name = road.get("n") or ROAD_LABEL.get(road["k"], "校内道路")
            pts = road["p"]
            for a, b in zip(pts, pts[1:]):
                a_in, b_in = self.on_campus(*a), self.on_campus(*b)
                if a_in != b_in:
                    # 围墙只能经由校门穿越，避免路线“穿墙”。
                    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    if min(self.meters(mid, gate) for gate in gates) > GATE_RADIUS_M:
                        continue
                i, j = g.node(tuple(a)), g.node(tuple(b))
                if i == j:
                    continue
                d = self.meters(g.points[i], g.points[j])
                g.adj[i].append((j, d, name))
                g.adj[j].append((i, d, name))
                g.segments.append((i, j, name))
        # 只在最大连通分量上吸附，避免把起点吸到孤立的小路段。
        component = self._largest_component(g)
        g.segments = [s for s in g.segments if s[0] in component]
        return g

    @staticmethod
    def _largest_component(g: _Graph) -> set[int]:
        seen: set[int] = set()
        best: set[int] = set()
        for start in range(len(g.points)):
            if start in seen:
                continue
            stack, part = [start], {start}
            while stack:
                for j, _, _ in g.adj[stack.pop()]:
                    if j not in part:
                        part.add(j)
                        stack.append(j)
            seen |= part
            if len(part) > len(best):
                best = part
        return best

    def _snap(self, x: float, z: float) -> tuple[tuple[float, float], int, int, str, float]:
        best = None
        for i, j, name in self.graph.segments:
            px, pz, _ = _project(x, z, self.graph.points[i], self.graph.points[j])
            d = (px - x) ** 2 + (pz - z) ** 2
            if best is None or d < best[0]:
                best = (d, (px, pz), i, j, name)
        if best is None:
            raise RouteError("地图路网数据为空。")
        return best[1], best[2], best[3], best[4], math.sqrt(best[0]) * self.unit

    def _shortest(self, start: tuple[float, float, int, int, str], end: tuple[float, float, int, int, str]):
        """在路网上插入起终点虚拟节点后运行 Dijkstra。"""
        g = self.graph
        S, E = -1, -2
        extra: dict[int, list[tuple[int, float, str]]] = {S: [], E: []}
        pos = {S: start[:2], E: end[:2]}

        def link(v: int, spec):
            p, i, j, name = spec[:2], spec[2], spec[3], spec[4]
            for k in (i, j):
                d = self.meters(p, g.points[k])
                extra[v].append((k, d, name))
                extra.setdefault(k, []).append((v, d, name))

        link(S, start)
        link(E, end)
        if {start[2], start[3]} == {end[2], end[3]}:
            d = self.meters(start[:2], end[:2])
            extra[S].append((E, d, start[4]))
        dist, prev = {S: 0.0}, {}
        heap = [(0.0, S)]
        while heap:
            d, v = heapq.heappop(heap)
            if v == E:
                break
            if d > dist.get(v, math.inf):
                continue
            for w, length, name in (g.adj[v] if v >= 0 else []) + extra.get(v, []):
                nd = d + length
                if nd < dist.get(w, math.inf):
                    dist[w], prev[w] = nd, (v, name)
                    heapq.heappush(heap, (nd, w))
        if E not in dist:
            raise RouteError("起点与终点之间没有可步行的连通道路。")
        path, names, v = [E], [], E
        while v != S:
            v, name = prev[v]
            path.append(v)
            names.append(name)
        path.reverse()
        names.reverse()
        points = [pos[v] if v < 0 else g.points[v] for v in path]
        return points, names, dist[E]

    # ---------- 起终点解析 ----------
    def resolve(self, spec: object, label: str) -> dict:
        if not isinstance(spec, dict):
            raise RouteError(f"{label}格式无效。")
        if "poi" in spec:
            poi = self.pois.get(spec["poi"]) if isinstance(spec["poi"], str) else None
            if not poi:
                raise RouteError(f"未找到{label}地点。")
            return {"x": poi["x"], "z": poi["z"], "name": poi["name"], "poi": poi["id"]}
        if "lat" in spec and "lon" in spec:
            lat, lon = spec["lat"], spec["lon"]
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise RouteError(f"{label}经纬度无效。")
            x, z = self.to_xz(float(lat), float(lon))
            return {"x": x, "z": z, "name": spec.get("name") if isinstance(spec.get("name"), str) else "我的位置", "gps": True}
        if "x" in spec and "z" in spec:
            x, z = spec["x"], spec["z"]
            if not all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) < 500 for v in (x, z)):
                raise RouteError(f"{label}坐标无效。")
            return {"x": float(x), "z": float(z), "name": spec.get("name") if isinstance(spec.get("name"), str) else "地图选点"}
        raise RouteError(f"缺少{label}。")

    def route(self, origin: object, destination: object) -> dict:
        start = self.resolve(origin, "起点")
        if isinstance(destination, dict) and "group" in destination:
            group = self.groups.get(destination["group"]) if isinstance(destination["group"], str) else None
            if not group:
                raise RouteError("未找到终点类别。")
            options = []
            for member in group["members"]:
                try:
                    options.append(self.route(origin, {"poi": member}))
                except RouteError:
                    continue
            if not options:
                raise RouteError("附近没有可到达的同类地点。")
            best = min(options, key=lambda r: r["distance_m"])
            best["group"] = {"id": destination["group"], "name": group["name"], "candidates": len(options)}
            return best
        end = self.resolve(destination, "终点")
        sx, sz = start["x"], start["z"]
        approach = min(math.dist((sx, sz), p) for p in self.graph.points) * self.unit if self.graph.points else 0
        if approach > MAX_START_DISTANCE_M:
            raise RouteError(f"起点距离校区道路约 {approach / 1000:.1f} km，超出校内步行导航范围。可改为从校门出发。")
        s_snap = self._snap(sx, sz)
        e_snap = self._snap(end["x"], end["z"])
        s = (*s_snap[0], s_snap[1], s_snap[2], s_snap[3])
        e = (*e_snap[0], e_snap[1], e_snap[2], e_snap[3])
        points, names, network_m = self._shortest(s, e)
        # 起终点到路网的接驳段直接连接（建筑入口、操场中心等）。
        lead_in, lead_out = s_snap[4], e_snap[4]
        points = [(sx, sz), *points, (end["x"], end["z"])]
        names = ["前往道路", *names, "到达目的地"]
        total = network_m + lead_in + lead_out
        steps = self._steps(points, names, start["name"], end["name"], lead_in)
        return {
            "ok": True,
            "from": {**start, "on_campus": self.on_campus(sx, sz)},
            "to": end,
            "points": [[round(x, 3), round(z, 3)] for x, z in self._dedupe(points)],
            "distance_m": round(total),
            "walk_min": max(1, round(total / WALK_M_PER_MIN)),
            "bike_min": max(1, round(total / BIKE_M_PER_MIN)),
            "approach_m": round(lead_in),
            "steps": steps,
            "note": self.meta["note"],
            "attribution": self.meta["attribution"],
        }

    @staticmethod
    def _dedupe(points):
        out = []
        for p in points:
            if not out or math.dist(out[-1], p) > 1e-6:
                out.append(p)
        return out

    # ---------- 分步指引 ----------
    @staticmethod
    def _heading(a, b) -> float:
        return math.degrees(math.atan2(-(b[1] - a[1]), b[0] - a[0]))  # 东=0°，北=90°

    def _steps(self, points, names, start_name: str, end_name: str, lead_in: float) -> list[dict]:
        legs = []  # [name, length_m, first_heading, last_heading, start_point]
        for (a, b), name in zip(zip(points, points[1:]), names):
            d = self.meters(a, b)
            if d < 0.5:
                continue
            h = self._heading(a, b)
            if name in ("前往道路", "到达目的地") and legs and legs[-1][0] not in ("前往道路",):
                legs[-1][1] += d
                continue
            if legs:
                last = legs[-1]
                turn = abs((h - last[3] + 180) % 360 - 180)
                if last[0] == name and (turn < 50 or last[1] < 20):
                    last[1] += d
                    last[3] = h
                    continue
            legs.append([name, d, h, h, a])
        # 合并过短的路段，避免“走 5 米后左转”。
        merged = []
        for leg in legs:
            if merged and leg[1] < 15:
                merged[-1][1] += leg[1]
                merged[-1][3] = leg[3]
            else:
                merged.append(leg)
        steps = []
        for k, (name, d, h0, h1, at) in enumerate(merged):
            road = "" if name == "前往道路" else name
            if k == 0:
                direction = COMPASS[round(h0 / 45) % 8]
                text = f"从{start_name}出发，向{direction}" + (f"沿{road}" if road else "前往最近道路") + f"走约 {self._round(d)} 米"
            else:
                diff = (h0 - merged[k - 1][3] + 180) % 360 - 180
                side = "左" if diff > 0 else "右"
                verb = next(t for limit, t in TURNS if abs(diff) < limit).format(side=side)
                if road and road != merged[k - 1][0]:
                    text = f"{verb}进入{road}，走约 {self._round(d)} 米"
                else:
                    text = f"{verb}，" + (f"沿{road}" if road else "") + f"继续走约 {self._round(d)} 米"
            steps.append({"text": text, "distance_m": round(d), "road": road, "at": [round(at[0], 3), round(at[1], 3)]})
        steps.append({"text": f"到达{end_name}", "distance_m": 0, "road": "", "at": [round(points[-1][0], 3), round(points[-1][1], 3)]})
        return steps

    @staticmethod
    def _round(d: float) -> int:
        return max(10, int(round(d / 10.0)) * 10)

    # ---------- 问题 → 地点 ----------
    def search(self, query: str, limit: int = 5) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        scored = []
        for p in self.pois.values():
            score = 0
            if p["name"] in q or (len(q) >= 2 and q in p["name"]):
                score += 10 + len(p["name"])
            score += sum(3 for k in p["keywords"] if k in q)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda t: (-t[0], t[1]["category"] == "building"))
        return [self.describe(p) for _, p in scored[:limit]]

    def describe(self, p: dict) -> dict:
        gate = self.nearest_gate(p["x"], p["z"])
        out = {"id": p["id"], "name": p["name"], "category": p["category"], "zone": self.zone(p["x"], p["z"]),
               "nearest_gate": gate["name"], "gate_distance_m": round(self.meters((p["x"], p["z"]), (gate["x"], gate["z"])), -1)}
        if p.get("note"):
            out["note"] = p["note"]
        return out

    def match(self, question: str, answer: str = "", limit: int = 3) -> list[dict]:
        """从用户问题（优先）和回答中识别需要到访的地点，用于“在地图中查看路线”。"""
        q = re.sub(r"\s+", "", question or "")
        a = re.sub(r"\s+", "", answer or "")
        scores: dict[tuple[str, str], float] = {}

        def add(kind: str, key: str, value: float):
            scores[(kind, key)] = scores.get((kind, key), 0) + value

        named = sorted(self.pois.values(), key=lambda p: -len(p["name"]))
        claimed = q
        for p in named:
            if len(p["name"]) >= 2 and p["name"] in claimed:
                add("poi", p["id"], 20)
                claimed = claimed.replace(p["name"], "#")
        for p in self.pois.values():
            if p["category"] == "building":
                continue
            hits_q = sum(1 for k in p["keywords"] if k in q)
            hits_a = sum(1 for k in p["keywords"] if k in a)
            if hits_q or hits_a >= 2:
                add("poi", p["id"], hits_q * 3 + min(hits_a, 3))
        for key, group in self.groups.items():
            hits_q = sum(1 for k in group["keywords"] if k in q)
            if hits_q:
                add("group", key, hits_q * 3)
        # 某类别已被成组命中时，去掉同类成员的关键词命中（显式点名的成员保留）。
        # 若用户点名了某个成员，则不再给出“最近的同类”。
        # “点名”包括完整名称，或与该类别通用词无关的专有词（如“鸿翔园”之于“学生社区”）。
        for (kind, key), value in list(scores.items()):
            if kind == "group":
                group = self.groups[key]
                generic = group["keywords"]

                def named_member(m: str) -> bool:
                    if scores.get(("poi", m), 0) >= 20:
                        return True
                    return any(k in q and not any(g in k or k in g for g in generic) for k in self.pois[m]["keywords"])

                chosen = [m for m in group["members"] if named_member(m)]
                if chosen:
                    scores.pop((kind, key))
                for member in group["members"]:
                    if member not in chosen:
                        scores.pop(("poi", member), None)
        # 同分时具体地点优先于“最近的同类”。
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0][0] == "group"))
        targets = []
        for (kind, key), value in ranked:
            if value < 3:
                continue
            if kind == "poi":
                p = self.pois[key]
                targets.append({"type": "poi", "id": key, "name": p["name"], "category": p["category"], **({"note": p["note"]} if p.get("note") else {})})
            else:
                g = self.groups[key]
                targets.append({"type": "group", "id": key, "name": g["name"], "category": self.pois[g["members"][0]]["category"]})
            if len(targets) >= limit:
                break
        return targets


LOCATE_SCHEMA = {
    "name": "locate_campus_place",
    "description": "查询长安大学渭水校区地点的位置（所在片区、最近校门），用于回答“在哪里办理/怎么去”；用户界面会据此提供地图路线。",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 80}}, "required": ["query"], "additionalProperties": False},
}


@lru_cache(maxsize=1)
def campus_map() -> CampusMap:
    return CampusMap()
