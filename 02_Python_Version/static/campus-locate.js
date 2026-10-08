import { toMapXZ } from "./campus-layout.js";

// 浏览器定位的精度处理：
// 1. 平滑：按精度加权的卡尔曼滤波，抑制 Wi-Fi/基站定位的跳动；
// 2. 坐标系：部分国产浏览器/WebView 返回 GCJ-02（国测局）坐标，渭水校区附近与 WGS-84 相差约 450 米；
// 3. 校准：用户在地图上点出真实位置，自动判断是 GCJ-02 还是固定偏差并保存，后续定位自动修正。
const STORE = "campus-locate-calibration";
const WALK_SPEED = 1.6; // m/s，用于估计两次定位之间可能的移动
const MAX_CALIBRATION_M = 2000;

// ---------- GCJ-02 ⇄ WGS-84（公开的国测局偏移算法） ----------
const A = 6378245.0,
  EE = 0.00669342162296594323,
  PI = Math.PI;
function shiftLat(x, y) {
  let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  r += ((20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2) / 3;
  r += ((20 * Math.sin(y * PI) + 40 * Math.sin((y / 3) * PI)) * 2) / 3;
  r += ((160 * Math.sin((y / 12) * PI) + 320 * Math.sin((y * PI) / 30)) * 2) / 3;
  return r;
}
function shiftLon(x, y) {
  let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  r += ((20 * Math.sin(6 * x * PI) + 20 * Math.sin(2 * x * PI)) * 2) / 3;
  r += ((20 * Math.sin(x * PI) + 40 * Math.sin((x / 3) * PI)) * 2) / 3;
  r += ((150 * Math.sin((x / 12) * PI) + 300 * Math.sin((x / 30) * PI)) * 2) / 3;
  return r;
}
export function wgsToGcj(lat, lon) {
  let dLat = shiftLat(lon - 105, lat - 35),
    dLon = shiftLon(lon - 105, lat - 35);
  const rad = (lat / 180) * PI,
    magic = 1 - EE * Math.sin(rad) ** 2,
    sq = Math.sqrt(magic);
  dLat = (dLat * 180) / (((A * (1 - EE)) / (magic * sq)) * PI);
  dLon = (dLon * 180) / ((A / sq) * Math.cos(rad) * PI);
  return [lat + dLat, lon + dLon];
}
export function gcjToWgs(lat, lon) {
  let wLat = lat,
    wLon = lon;
  for (let i = 0; i < 5; i++) {
    const [gLat, gLon] = wgsToGcj(wLat, wLon);
    wLat -= gLat - lat;
    wLon -= gLon - lon;
  }
  return [wLat, wLon];
}

function loadCalibration() {
  try {
    const c = JSON.parse(localStorage.getItem(STORE) || "null");
    if (c && (c.mode === "gcj02" || (c.mode === "offset" && Number.isFinite(c.dx) && Number.isFinite(c.dz)))) return c;
  } catch {
    /* 损坏的本地数据直接忽略 */
  }
  return null;
}

export function createLocator(meta, { onFix, onError }) {
  const unit = meta.unit_m;
  let watch = null,
    raw = null, // 最近一次原始定位 { lat, lon, accuracy, time }
    est = null, // 滤波结果 { x, z, variance(m²), time }
    calibration = loadCalibration();

  // 原始经纬度 → 地图坐标（应用坐标系换算与校准偏移）
  function project(lat, lon, cal = calibration) {
    const [la, lo] = cal?.mode === "gcj02" ? gcjToWgs(lat, lon) : [lat, lon];
    const p = toMapXZ(meta, la, lo);
    if (cal?.mode === "offset") {
      p.x += cal.dx;
      p.z += cal.dz;
    }
    return p;
  }
  function filter(m, accuracy, time) {
    const r2 = accuracy * accuracy;
    if (!est) return (est = { ...m, variance: r2, time });
    const dt = Math.max(0, (time - est.time) / 1000);
    est.variance += (WALK_SPEED * dt) ** 2 + 1;
    const jump = Math.hypot(m.x - est.x, m.z - est.z) * unit;
    // 明显移动（超出 3σ）且新定位较准时直接跟随，避免“拖尾”。
    if (jump > 3 * Math.sqrt(est.variance + r2) && accuracy < 60) return (est = { ...m, variance: r2, time });
    const k = est.variance / (est.variance + r2);
    est.x += k * (m.x - est.x);
    est.z += k * (m.z - est.z);
    est.variance *= 1 - k;
    est.time = time;
    return est;
  }
  function emit() {
    const p = filter(project(raw.lat, raw.lon), raw.accuracy, raw.time);
    // Wi-Fi 定位误差往往是系统性偏差，平滑后也不应显示得比设备报告的一半更准。
    const accuracy = Math.max(Math.sqrt(p.variance), raw.accuracy * 0.5, 3);
    onFix({ x: p.x, z: p.z, accuracy, rawAccuracy: raw.accuracy, calibration: calibration?.mode || null });
  }
  function handle(position) {
    const { latitude: lat, longitude: lon, accuracy } = position.coords;
    // 极粗的 IP 定位（> 3 km）只在还没有任何结果时采用。
    if (accuracy > 3000 && est) return;
    raw = { lat, lon, accuracy, time: position.timestamp || Date.now() };
    emit();
  }
  return {
    start() {
      this.stop();
      watch = navigator.geolocation.watchPosition(handle, onError, {
        enableHighAccuracy: true,
        timeout: 20000,
        maximumAge: 0,
      });
    },
    stop() {
      if (watch !== null) navigator.geolocation.clearWatch(watch);
      watch = null;
      raw = est = null;
    },
    get running() {
      return watch !== null;
    },
    get calibration() {
      return calibration;
    },
    // 用户在地图上点出真实位置后调用；返回校准方式与修正距离（米）。
    calibrate(x, z) {
      if (!raw) return null;
      const plain = project(raw.lat, raw.lon, null),
        gcj = project(raw.lat, raw.lon, { mode: "gcj02" }),
        offset = Math.hypot(x - plain.x, z - plain.z) * unit,
        gcjError = Math.hypot(x - gcj.x, z - gcj.z) * unit;
      if (offset > MAX_CALIBRATION_M) return { error: `与定位结果相差 ${Math.round(offset)} 米，超过校准范围，请确认点选位置。` };
      // 若换算 GCJ-02 后几乎重合，说明浏览器返回的是国测局坐标，换算比固定偏移更准确。
      if (offset > 150 && gcjError < Math.max(60, raw.accuracy))
        calibration = { mode: "gcj02", at: Date.now() };
      else calibration = { mode: "offset", dx: x - plain.x, dz: z - plain.z, at: Date.now() };
      localStorage.setItem(STORE, JSON.stringify(calibration));
      est = null; // 以校准后的坐标重新开始滤波
      raw.time = Date.now();
      emit();
      return { mode: calibration.mode, meters: Math.round(offset) };
    },
    clearCalibration() {
      calibration = null;
      localStorage.removeItem(STORE);
      est = null;
      if (raw) emit();
    },
  };
}
