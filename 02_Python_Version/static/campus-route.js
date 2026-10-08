import { insideCampus } from "./campus-layout.js";
import { createLocator } from "./campus-locate.js";

// 路线规划面板 + 浏览器定位。路线由本机服务 /api/map/route 计算；定位只发送到本机服务。
const $ = (id) => document.getElementById(id);
const CATEGORY = {
  service: "办事服务",
  study: "教学科研",
  life: "生活住宿",
  dining: "餐饮",
  shop: "商业快递",
  health: "医疗",
  sports: "运动",
  activity: "活动展馆",
  gate: "校门",
  transit: "公交",
  building: "其他已命名建筑",
};
// 场景地标 → 可导航地点
export const PLACE_POI = {
  library: "library",
  study: "xiuyuan",
  life: "hongxiang",
  activities: "activity_center",
  highway: "highway_school",
  materials: "hongyi_labs",
  information: "mingde",
};

export function createRoutePlanner({ getWorld, getMap, reveal }) {
  let map = null,
    fix = null, // { lat, lon, x, z, accuracy, onCampus, distance }
    watch = null,
    pending = null,
    lastPlan = null,
    picked = null,
    custom = null,
    request = 0,
    lastReplan = 0;
  const panel = $("route-panel"),
    from = $("route-from"),
    to = $("route-to"),
    status = $("route-status"),
    locateStatus = $("locate-status");

  function option(parent, value, text) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    parent.append(o);
    return o;
  }
  function fill(select, includeGroups) {
    select.replaceChildren();
    if (select === from) {
      option(select, "me", "我的位置（浏览器定位）");
      option(select, "pick", "在地图上点选…");
    } else option(select, "", "选择目的地…");
    if (includeGroups) {
      const g = document.createElement("optgroup");
      g.label = "就近前往";
      for (const [key, group] of Object.entries(map.groups)) option(g, "group:" + key, group.name);
      select.append(g);
    }
    const byCat = new Map();
    for (const p of map.pois) {
      if (!byCat.has(p.category)) byCat.set(p.category, []);
      byCat.get(p.category).push(p);
    }
    for (const cat of Object.keys(CATEGORY)) {
      if (!byCat.has(cat)) continue;
      const g = document.createElement("optgroup");
      g.label = CATEGORY[cat];
      byCat
        .get(cat)
        .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))
        .forEach((p) => option(g, "poi:" + p.id, p.name));
      select.append(g);
    }
  }
  function ensure() {
    if (map) return true;
    map = getMap();
    if (!map) return false;
    fill(from, false);
    fill(to, true);
    from.value = "me";
    return true;
  }
  const meters = (m) => (m >= 1000 ? (m / 1000).toFixed(1) + " km" : Math.round(m) + " m");

  // ---------- 定位 ----------
  let hideTimer = 0,
    locator = null;
  function setLocateState(state, text, transient = false) {
    clearTimeout(hideTimer);
    if (transient) hideTimer = setTimeout(() => (locateStatus.hidden = true), 6000);
    $("map-locate").dataset.state = state;
    $("map-locate").setAttribute("aria-pressed", String(state === "on"));
    locateStatus.textContent = text;
    locateStatus.hidden = !text;
    $("locate-actions").hidden = state !== "on";
    $("locate-uncalibrate").hidden = !locator?.calibration;
  }
  // 精度分级提示：笔记本没有 GPS，只能靠 Wi-Fi/IP，误差常在几十到数百米。
  function quality(accuracy) {
    if (accuracy <= 15) return "精度高";
    if (accuracy <= 50) return "精度一般（多为 Wi-Fi 定位）";
    if (accuracy <= 500) return "精度较低，建议到室外或打开 Wi-Fi，也可点“校准”";
    return "可能是 IP 粗略定位，误差很大，建议点“校准”或在地图上点选起点";
  }
  function describe(f) {
    const cal = f.calibration === "gcj02" ? " · 已按 GCJ-02 换算" : f.calibration === "offset" ? " · 已校准" : "";
    return f.onCampus
      ? `已定位 · 约 ±${Math.round(f.accuracy)} 米 · ${quality(f.accuracy)}${cal}`
      : `你当前不在渭水校区内（距校区中心约 ${meters(f.distance)}，约 ±${Math.round(f.accuracy)} 米${cal}）。规划路线时将从校门出发；若实际在校内，可点“校准”。`;
  }
  let lastText = "";
  function onFix(p) {
    const onCampus = insideCampus(p.x, p.z, map.campus),
      distance = Math.hypot(p.x, p.z) * map.meta.unit_m;
    const first = !fix;
    fix = { ...p, onCampus, distance };
    const world = getWorld();
    // 校区 3 km 以外不在三维地图上绘制，避免把镜头拉到空白区域。
    if (distance < 3000) world?.setUser({ x: p.x, z: p.z, accuracy: p.accuracy });
    else world?.setUser(null);
    // 文案变化（精度档位、校内外、校准状态）时才更新提示，避免持续定位时反复播报。
    const text = describe(fix);
    if (text.replace(/±\d+/, "") !== lastText.replace(/±\d+/, "") || first)
      setLocateState("on", text, onCampus && p.accuracy <= 50);
    lastText = text;
    if (first && distance < 3000) world?.focusPoint(p.x, p.z, onCampus ? 12 : 20);
    if (first && pending) {
      const resolve = pending;
      pending = null;
      resolve(fix);
    }
    // 跟随移动：使用“我的位置”作为起点时，位置变化超过 15 米且距上次 5 秒以上则重新规划。
    if (!first && lastPlan?.fromMe && Date.now() - lastReplan > 5000) {
      const moved = Math.hypot(p.x - lastPlan.x, p.z - lastPlan.z) * map.meta.unit_m;
      if (moved > 15) plan({ quiet: true });
    }
  }
  function onError(error) {
    // 已有定位结果时，偶发超时不打断使用。
    if (fix && error.code === 3) return;
    const text =
      error.code === 1
        ? "未获得定位权限。可在浏览器地址栏允许“位置信息”（macOS 还需在“系统设置 → 隐私与安全性 → 定位服务”中允许浏览器），或在地图上点选起点。"
        : error.code === 3
          ? "定位超时，请到开阔处重试，或在地图上点选起点。"
          : "暂时无法获取位置（设备未提供定位信息）。可在地图上点选起点。";
    stopWatch();
    setLocateState("error", text);
    if (pending) {
      const resolve = pending;
      pending = null;
      resolve(null);
    }
  }
  function stopWatch() {
    locator?.stop();
  }
  function locate() {
    if (!ensure()) return Promise.resolve(null);
    if (!("geolocation" in navigator)) {
      setLocateState("error", "此浏览器不支持定位，可在地图上点选起点。");
      return Promise.resolve(null);
    }
    if (!window.isSecureContext) {
      setLocateState("error", "浏览器只允许在 HTTPS 或本机地址下定位，可在地图上点选起点。");
      return Promise.resolve(null);
    }
    if (fix && locator?.running) {
      const world = getWorld();
      if (fix.distance < 3000) world?.focusPoint(fix.x, fix.z, 12);
      return Promise.resolve(fix);
    }
    locator ||= createLocator(map.meta, { onFix, onError });
    setLocateState("busy", "正在获取你的位置…（首次定位可能较粗，几秒后会逐步变准）");
    const result = new Promise((resolve) => (pending = resolve));
    fix = null;
    lastText = "";
    locator.start();
    return result;
  }
  function stopLocate() {
    stopWatch();
    fix = null;
    getWorld()?.setUser(null);
    setLocateState("off", "");
  }
  // 校准：在地图上点出自己真实所在的位置。
  function calibrate() {
    const world = getWorld();
    if (!world || !fix) return;
    setLocateState("on", "请在地图上点击你实际所在的位置（Esc 取消）。");
    world.pick((p) => {
      const result = locator.calibrate(p.x, p.z);
      if (!result) return;
      if (result.error) {
        setLocateState("on", result.error);
        return;
      }
      lastText = describe(fix);
      setLocateState(
        "on",
        result.mode === "gcj02"
          ? `已校准：检测到浏览器返回的是国测局 GCJ-02 坐标（偏差约 ${result.meters} 米），之后会自动换算。`
          : `已校准：定位结果整体修正约 ${result.meters} 米，之后的定位会沿用该修正。`,
      );
      if (lastPlan?.fromMe) plan({ quiet: true });
    });
  }
  $("locate-calibrate").onclick = calibrate;
  $("locate-uncalibrate").onclick = () => {
    locator?.clearCalibration();
    lastText = fix ? describe(fix) : "";
    setLocateState("on", "已清除校准，恢复使用浏览器原始定位。");
  };

  // ---------- 规划 ----------
  function nearestGate(dest) {
    const gates = map.pois.filter((p) => p.category === "gate");
    let point = dest.poi ? map.pois.find((p) => p.id === dest.poi) : dest;
    if (dest.group) point = map.pois.find((p) => p.id === map.groups[dest.group].members[0]);
    return gates.reduce((best, g) =>
      Math.hypot(g.x - point.x, g.z - point.z) < Math.hypot(best.x - point.x, best.z - point.z) ? g : best,
    );
  }
  function destination() {
    const v = to.value;
    if (v.startsWith("poi:")) return { poi: v.slice(4) };
    if (v.startsWith("group:")) return { group: v.slice(6) };
    if (v === "custom" && custom) return { ...custom };
    return null;
  }
  async function origin(dest) {
    const v = from.value;
    if (v.startsWith("poi:")) return { spec: { poi: v.slice(4) } };
    if (v === "pick") return picked ? { spec: { ...picked } } : null;
    // 我的位置：没有定位或距离过远时，从离目的地最近的校门出发。
    const here = fix || (await locate());
    // 使用平滑、校准后的地图坐标作为起点。
    if (here && here.distance < 5000) return { spec: { x: here.x, z: here.z, name: "我的位置" }, me: here };
    const gate = nearestGate(dest);
    return {
      spec: { poi: gate.id },
      fallback: here
        ? `你不在校区附近，已从${gate.name}开始规划。`
        : `未获取到位置，已暂从${gate.name}开始规划，可在“起点”中修改。`,
    };
  }
  async function plan({ quiet = false } = {}) {
    if (!ensure()) return;
    const dest = destination();
    if (!dest) {
      status.textContent = "请选择目的地。";
      return;
    }
    if (from.value === "pick" && !picked) {
      startPick();
      return;
    }
    const ticket = ++request;
    if (!quiet) status.textContent = "正在规划步行路线…";
    const start = await origin(dest);
    if (ticket !== request || !start) return;
    try {
      const r = await fetch("/api/map/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from: start.spec, to: dest }),
      });
      const data = await r.json();
      if (ticket !== request) return;
      if (!r.ok || !data.ok) throw Error(data.error || "路线规划失败，请稍后重试。");
      lastReplan = Date.now();
      lastPlan = { fromMe: Boolean(start.me), x: start.me?.x, z: start.me?.z, data };
      render(data, start.fallback, quiet);
    } catch (e) {
      if (ticket !== request) return;
      status.textContent = e.message;
      $("route-result").hidden = true;
      getWorld()?.clearRoute();
    }
  }
  function render(data, fallback, quiet) {
    $("route-result").hidden = false;
    $("route-distance").textContent = meters(data.distance_m);
    $("route-time").textContent = `步行约 ${data.walk_min} 分钟 · 骑行约 ${data.bike_min} 分钟`;
    const name = data.group ? `${data.to.name}（${data.group.name}，比较了 ${data.group.candidates} 处）` : data.to.name;
    $("route-target").textContent = "前往 " + name;
    const steps = $("route-steps");
    steps.replaceChildren();
    for (const s of data.steps) {
      const li = document.createElement("li"),
        b = document.createElement("button");
      b.type = "button";
      b.textContent = s.text;
      b.onclick = () => getWorld()?.focusPoint(s.at[0], s.at[1], 9);
      li.append(b);
      steps.append(li);
    }
    const note = map.pois.find((p) => p.id === data.to.poi)?.note;
    $("route-note").textContent = [fallback, data.approach_m > 80 ? `起点距最近道路约 ${meters(data.approach_m)}，请先前往附近道路。` : "", note]
      .filter(Boolean)
      .join(" ");
    status.textContent = quiet ? "已按最新位置更新路线。" : `已规划路线：${meters(data.distance_m)}，步行约 ${data.walk_min} 分钟。`;
    getWorld()?.showRoute(data, { fitView: !quiet });
  }

  // ---------- 面板 ----------
  function open(dest = null, { autoPlan = true } = {}) {
    if (!ensure()) return false;
    reveal();
    panel.hidden = false;
    $("campus").classList.add("is-routing");
    $("map-route-toggle").setAttribute("aria-pressed", "true");
    getWorld()?.measure();
    if (dest?.type === "poi" || dest?.poi) to.value = "poi:" + (dest.id || dest.poi);
    else if (dest?.type === "group" || dest?.group) to.value = "group:" + (dest.id || dest.group);
    else if (dest?.x !== undefined) {
      custom = { x: dest.x, z: dest.z, name: dest.name || "地图选点" };
      [...to.querySelectorAll('option[value="custom"]')].forEach((o) => o.remove());
      to.prepend(new Option(custom.name, "custom"));
      to.value = "custom";
    }
    if (dest && autoPlan) plan();
    else $("route-title").focus({ preventScroll: true });
    return true;
  }
  function close() {
    panel.hidden = true;
    $("campus").classList.remove("is-routing");
    $("map-route-toggle").setAttribute("aria-pressed", "false");
    request++;
    cancelPick();
    getWorld()?.clearRoute();
    getWorld()?.measure();
    $("route-result").hidden = true;
    status.textContent = "";
    lastPlan = null;
  }
  function startPick() {
    const world = getWorld();
    if (!world) {
      status.textContent = "三维地图未就绪，无法点选起点。";
      return;
    }
    $("route-pick").setAttribute("aria-pressed", "true");
    status.textContent = "请在地图上点击起点位置（Esc 取消）。";
    world.pick((p) => {
      $("route-pick").setAttribute("aria-pressed", "false");
      picked = { x: p.x, z: p.z, name: "地图选点" };
      from.value = "pick";
      plan();
    });
  }
  function cancelPick() {
    getWorld()?.pick(null);
    $("route-pick").setAttribute("aria-pressed", "false");
  }
  from.onchange = () => {
    if (from.value === "pick") startPick();
    else plan();
  };
  to.onchange = () => plan();
  $("route-pick").onclick = startPick;
  $("route-swap").onclick = () => {
    const a = from.value,
      b = to.value;
    if (!a.startsWith("poi:") || !b.startsWith("poi:")) {
      status.textContent = "起点和终点都是具体地点时才能交换；“我的位置”与“就近前往”不能互换。";
      return;
    }
    from.value = b;
    to.value = a;
    plan();
  };
  $("route-close").onclick = close;
  $("map-locate").onclick = () => {
    if ($("map-locate").dataset.state === "on") stopLocate();
    else locate();
  };
  $("map-route-toggle").onclick = () => (panel.hidden ? open() : close());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && locateStatus.textContent.startsWith("请在地图上点击你实际")) {
      e.stopImmediatePropagation();
      getWorld()?.pick(null);
      setLocateState("on", lastText);
      return;
    }
    if (e.key === "Escape" && $("route-pick").getAttribute("aria-pressed") === "true") {
      e.stopImmediatePropagation();
      cancelPick();
      status.textContent = "已取消点选。";
    }
  }, true);
  return { open, close, plan, locate, get isOpen() { return !panel.hidden; } };
}

// 回答下方的“在地图中查看路线”入口。
export function targetChips(targets, onRoute) {
  const wrap = document.createElement("div");
  wrap.className = "map-targets";
  for (const t of targets || []) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "map-target";
    b.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0"/></svg>';
    const label = document.createElement("span");
    label.textContent = t.name + " · 在地图中查看路线";
    b.append(label);
    b.onclick = () => onRoute(t);
    wrap.append(b);
  }
  return wrap;
}
