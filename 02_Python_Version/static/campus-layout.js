// 渭水校区地图数据：由 scripts/build_campus_map.py 从 OpenStreetMap 快照生成（ODbL，© OpenStreetMap contributors）。
// 坐标为局部平面坐标：1 单位 = meta.unit_m 米，x 向东，z 向南。
export const mapSource = "https://www.openstreetmap.org/#map=16/34.3716/108.8975";
export const layout = {
  library: { mapName: "逸夫图书馆" },
  study: { mapName: "修远教学楼" },
  life: { mapName: "鸿翔园生活社区" },
  activities: { mapName: "大学生活动中心" },
  highway: { mapName: "公路学院" },
  materials: { mapName: "弘毅园实验中心" },
  information: { mapName: "明德园科研组团" },
};
let pending = null;
export function loadCampusMap() {
  pending ||= fetch("/static/campus-map.json").then((r) => {
    if (!r.ok) throw Error("校园地图数据加载失败");
    return r.json();
  });
  pending.catch(() => (pending = null));
  return pending;
}
export function insideCampus(x, z, polygon) {
  let hit = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [x1, z1] = polygon[i],
      [x2, z2] = polygon[j];
    if (z1 > z !== z2 > z && x < ((x2 - x1) * (z - z1)) / (z2 - z1) + x1) hit = !hit;
  }
  return hit;
}
// 浏览器定位为 WGS84 经纬度，与 OSM 坐标系一致。
export function toMapXZ(meta, lat, lon) {
  return {
    x: ((lon - meta.origin.lon) * meta.m_per_deg.lon) / meta.unit_m,
    z: (-(lat - meta.origin.lat) * meta.m_per_deg.lat) / meta.unit_m,
  };
}
