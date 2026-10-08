import * as THREE from "./vendor/three/three.module.min.js";
import { insideCampus } from "./campus-layout.js";

// 渭水校区三维地图：建筑、道路、水体与场地均来自 OpenStreetMap 快照（scripts/build_campus_map.py）。
// 坐标：1 单位 = map.meta.unit_m 米，x 向东，z 向南。
const VERTICAL = 3; // 建筑高度夸张倍数，便于俯视辨认
const PLINTH = 0.08;
const PALETTE = {
  teach: [0xd3dee6, 0x9fb4c5],
  dorm: [0xb7d0df, 0x7f9fb6],
  dining: [0xe3d2a9, 0xbba57a],
  library: [0xf1f4f6, 0xc5d1da],
  service: [0xd9e6f0, 0x9fbad0],
  health: [0xd4eee9, 0x96c8c0],
  sport: [0xc9d9cf, 0x93ae9f],
  other: [0xc6d1da, 0x93a7b7],
  context: [0x506779, 0x3c5164],
};
const AREA = {
  green: [0x557d76, 0.012],
  sports: [0x5f857c, 0.014],
  pitch: [0x4f8a78, 0.02],
  track: [0xa9857c, 0.018],
  parking: [0x7b8e94, 0.016],
  water: [0x4bafc2, 0.022],
};
const ROAD = {
  major: [0xb9c7ca, 0.036],
  minor: [0xa9b9bc, 0.034],
  foot: [0x92a6aa, 0.032],
  outside: [0x415b6e, 0.006],
};
const MAJOR = new Set(["primary", "secondary", "tertiary", "residential", "unclassified"]);
const FOOT = new Set(["footway", "path", "steps", "cycleway", "track"]);

export async function createWorld(places, onSelect, map, hooks = {}) {
  const host = document.getElementById("scene"),
    labels = document.getElementById("scene-labels");
  const unit = map.meta.unit_m;
  const renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: true,
    powerPreference: "low-power",
  });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.needsUpdate = true;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.domElement.setAttribute("aria-hidden", "true");
  host.append(renderer.domElement);
  const scene = new THREE.Scene(),
    root = new THREE.Group(),
    camera = new THREE.PerspectiveCamera(34, 1, 0.1, 260);
  scene.add(root);
  const hemi = new THREE.HemisphereLight(0xe7f3ff, 0x25415a, 2.7),
    sun = new THREE.DirectionalLight(0xffead5, 4);
  sun.position.set(-18, 30, 14);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -30,
    right: 30,
    top: 22,
    bottom: -22,
    far: 90,
  });
  sun.shadow.bias = -0.0008;
  scene.add(hemi, sun);
  const clickable = [];
  const color = new THREE.Color();

  // ---------- 几何合并：同一材质的所有要素合成一个网格，保持低绘制调用 ----------
  function merged(parts, material, owners = null) {
    let count = 0;
    const list = parts.map((p) => {
      const g = p.geo.index ? p.geo.toNonIndexed() : p.geo;
      if (g !== p.geo) p.geo.dispose();
      count += g.attributes.position.count;
      return { ...p, geo: g };
    });
    const pos = new Float32Array(count * 3),
      nor = new Float32Array(count * 3),
      col = new Float32Array(count * 3),
      owner = owners ? new Int32Array(count / 3) : null;
    let o = 0;
    for (const p of list) {
      const g = p.geo,
        n = g.attributes.position.count,
        a = g.attributes.position.array;
      pos.set(a, o * 3);
      nor.set(g.attributes.normal.array, o * 3);
      const top = new THREE.Color(p.top),
        side = new THREE.Color(p.side ?? p.top);
      const groups = g.groups.length ? g.groups : [{ start: 0, count: n, materialIndex: 0 }];
      for (const grp of groups)
        for (let v = grp.start; v < grp.start + grp.count; v++) {
          if (grp.materialIndex === 0) top.toArray(col, (o + v) * 3);
          else {
            // 墙面自下而上提亮，模拟环境光遮蔽。
            const k = p.height ? 0.72 + (0.28 * (a[v * 3 + 1] - p.base)) / p.height : 1;
            color.copy(side).multiplyScalar(k).toArray(col, (o + v) * 3);
          }
        }
      if (owner) owner.fill(p.id, o / 3, (o + n) / 3);
      o += n;
      g.dispose();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.computeBoundingSphere();
    const mesh = new THREE.Mesh(geo, material);
    if (owner) mesh.userData.owner = owner;
    return mesh;
  }
  const shape = (ring) => new THREE.Shape(ring.map(([x, z]) => new THREE.Vector2(x, -z)));
  function flat(ring, y) {
    const g = new THREE.ShapeGeometry(shape(ring));
    g.rotateX(-Math.PI / 2);
    g.translate(0, y, 0);
    return g;
  }
  const centroidOf = (ring) => [
    ring.reduce((s, p) => s + p[0], 0) / ring.length,
    ring.reduce((s, p) => s + p[1], 0) / ring.length,
  ];
  const lit = (extra = {}) =>
    new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.85, ...extra });

  // ---------- 地面：校外背景 + 抬高的校区底座 ----------
  const xs = map.campus.map((p) => p[0]),
    zs = map.campus.map((p) => p[1]);
  const extent = {
    minX: Math.min(...xs) - 3,
    maxX: Math.max(...xs) + 3,
    minZ: Math.min(...zs) - 3,
    maxZ: Math.max(...zs) + 3,
  };
  const within = ([x, z]) => x > extent.minX && x < extent.maxX && z > extent.minZ && z < extent.maxZ;
  const inExtent = (pts) => pts.some(([x, z]) => x > extent.minX && x < extent.maxX && z > extent.minZ && z < extent.maxZ);
  const ground = new THREE.Mesh(
    new THREE.BoxGeometry(extent.maxX - extent.minX, 0.3, extent.maxZ - extent.minZ),
    new THREE.MeshStandardMaterial({ color: 0x2b4459, roughness: 0.95 }),
  );
  ground.position.set((extent.minX + extent.maxX) / 2, -0.15, (extent.minZ + extent.maxZ) / 2);
  ground.receiveShadow = true;
  ground.userData.ground = true;
  root.add(ground);
  const plinthGeo = new THREE.ExtrudeGeometry(shape(map.campus), {
    depth: PLINTH + 0.3,
    bevelEnabled: false,
  });
  plinthGeo.rotateX(-Math.PI / 2);
  plinthGeo.translate(0, -0.3, 0);
  const plinth = merged(
    [{ geo: plinthGeo, top: 0x667f87, side: 0x324d64 }],
    lit({ roughness: 0.95 }),
  );
  plinth.receiveShadow = true;
  plinth.userData.ground = true;
  root.add(plinth);

  // ---------- 绿地、场地、水体 ----------
  const areaParts = [],
    waterParts = [],
    greenRings = [];
  for (const a of map.areas) {
    if (!a.p.every(within)) continue;
    const c = centroidOf(a.p),
      base = insideCampus(c[0], c[1], map.campus) ? PLINTH : 0,
      [hex, lift] = AREA[a.k];
    const part = { geo: flat(a.p, base + lift), top: hex };
    (a.k === "water" ? waterParts : areaParts).push(part);
    if (a.k === "green" && base) greenRings.push(a.p);
  }
  const areaMesh = merged(areaParts, lit({ polygonOffset: true, polygonOffsetFactor: -1 }));
  areaMesh.receiveShadow = true;
  root.add(areaMesh);
  if (waterParts.length) {
    const water = merged(waterParts, lit({ roughness: 0.25, metalness: 0.15 }));
    water.receiveShadow = true;
    root.add(water);
  }

  // ---------- 道路：按等级生成平面条带 ----------
  function ribbon(points, width, y, pos, uv = null) {
    let run = 0;
    for (let i = 0; i < points.length - 1; i++) {
      const [ax, az] = points[i],
        [bx, bz] = points[i + 1];
      const len = Math.hypot(bx - ax, bz - az);
      if (len < 1e-5) continue;
      const dx = (bx - ax) / len,
        dz = (bz - az) / len,
        nx = (-dz * width) / 2,
        nz = (dx * width) / 2,
        e = uv ? 0 : width * 0.45; // 端点外延，填补转角缝隙
      const sx = ax - dx * e,
        sz = az - dz * e,
        ex = bx + dx * e,
        ez = bz + dz * e;
      // 逆时针（自上而下看）绕序，法线朝上。
      pos.push(
        sx + nx, y, sz + nz, ex + nx, y, ez + nz, sx - nx, y, sz - nz,
        sx - nx, y, sz - nz, ex + nx, y, ez + nz, ex - nx, y, ez - nz,
      );
      if (uv) {
        const u0 = run,
          u1 = run + len;
        uv.push(u0, 1, u1, 1, u0, 0, u0, 0, u1, 1, u1, 0);
      }
      run += len;
    }
    return run;
  }
  const roadParts = [],
    outsideParts = [];
  for (const r of map.roads) {
    if (!inExtent(r.p)) continue;
    const cls = r.in < 0.5 ? "outside" : MAJOR.has(r.k) ? "major" : FOOT.has(r.k) ? "foot" : "minor";
    const [hex, lift] = ROAD[cls],
      width = (cls === "outside" ? Math.max(r.w * 0.8, 3) : Math.max(r.w * 1.35, cls === "foot" ? 3.4 : 5)) / unit,
      pos = [];
    // 校外道路只保留落在背景底板内的路段。
    const runs = [[]];
    r.p.forEach((p, i) => {
      const keep = within(p) || (i > 0 && within(r.p[i - 1])) || (i < r.p.length - 1 && within(r.p[i + 1]));
      if (keep) runs[runs.length - 1].push(p.map((v, k) => THREE.MathUtils.clamp(v, k ? extent.minZ : extent.minX, k ? extent.maxZ : extent.maxX)));
      else if (runs[runs.length - 1].length) runs.push([]);
    });
    for (const run of runs) if (run.length > 1) ribbon(run, width, (cls === "outside" ? 0 : PLINTH) + lift, pos);
    if (!pos.length) continue;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(pos.map((_, i) => (i % 3 === 1 ? 1 : 0)), 3));
    (cls === "outside" ? outsideParts : roadParts).push({ geo: g, top: hex });
  }
  const roads = merged(
    roadParts,
    lit({ side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2 }),
  );
  roads.receiveShadow = true;
  root.add(roads);
  // 校外道路与背景底板随昼夜切换颜色。
  const outsideMaterial = new THREE.MeshStandardMaterial({ color: ROAD.outside[0], roughness: 0.95, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1 });
  if (outsideParts.length) {
    const outside = merged(outsideParts, outsideMaterial);
    outside.receiveShadow = true;
    root.add(outside);
  }

  // ---------- 建筑：按 OSM 轮廓挤出 ----------
  const buildingParts = [],
    contextParts = [],
    buildingInfo = [];
  map.buildings.forEach((b) => {
    if (!b.p.every(within)) return;
    const c = centroidOf(b.p),
      campus = b.c !== "context",
      base = campus ? PLINTH : 0,
      height = ((b.h * VERTICAL) / unit) * (campus ? 1 : 0.55);
    const g = new THREE.ExtrudeGeometry(shape(b.p), { depth: height, bevelEnabled: false });
    g.rotateX(-Math.PI / 2);
    g.translate(0, base, 0);
    const [top, side] = PALETTE[b.c] || PALETTE.other;
    const id = buildingInfo.length;
    buildingInfo.push({ name: b.n || "", cls: b.c, x: c[0], z: c[1] });
    (campus ? buildingParts : contextParts).push({ geo: g, top, side, base, height, id });
  });
  const buildings = merged(buildingParts, lit({ roughness: 0.78 }), true);
  buildings.castShadow = buildings.receiveShadow = true;
  buildings.userData.buildings = true;
  root.add(buildings);
  clickable.push(buildings);
  if (contextParts.length) {
    const context = merged(contextParts, lit({ roughness: 0.95 }));
    context.receiveShadow = true;
    root.add(context);
  }

  // ---------- 树木：在校内绿地上按网格点缀 ----------
  const trees = [];
  for (const ring of greenRings) {
    const bx = ring.map((p) => p[0]),
      bz = ring.map((p) => p[1]);
    for (let x = Math.min(...bx) + 0.25; x < Math.max(...bx); x += 0.7)
      for (let z = Math.min(...bz) + 0.25; z < Math.max(...bz); z += 0.7) {
        const jx = x + Math.sin(x * 12.9 + z * 78.2) * 0.18,
          jz = z + Math.cos(x * 39.3 + z * 11.1) * 0.18;
        if (insideCampus(jx, jz, ring) && trees.length < 900) trees.push([jx, jz]);
      }
  }
  if (trees.length) {
    const treeMesh = new THREE.InstancedMesh(
      new THREE.IcosahedronGeometry(0.17, 0),
      new THREE.MeshStandardMaterial({ color: 0x497e80, roughness: 0.9, flatShading: true }),
      trees.length,
    );
    const dummy = new THREE.Object3D();
    trees.forEach(([x, z], i) => {
      const s = 0.8 + ((Math.sin(x * 7.1 + z * 3.3) + 1) / 2) * 0.6;
      dummy.position.set(x, PLINTH + 0.16 * s, z);
      dummy.scale.set(s, s * 1.15, s);
      dummy.updateMatrix();
      treeMesh.setMatrixAt(i, dummy.matrix);
    });
    treeMesh.castShadow = true;
    root.add(treeMesh);
  }

  host.dataset.locations = String(Object.values(places).filter((p) => !p.virtual).length);
  host.dataset.layout = "osm-" + map.meta.osm_base.slice(0, 10);
  host.dataset.buildings = String(buildingParts.length);
  const marker = new THREE.Mesh(
    new THREE.RingGeometry(1.25, 1.32, 48),
    new THREE.MeshBasicMaterial({ color: 0x83e9ef, side: THREE.DoubleSide }),
  );
  marker.rotation.x = -Math.PI / 2;
  marker.visible = false;
  root.add(marker);

  // ---------- 路线与“我的位置” ----------
  const arrowCanvas = document.createElement("canvas");
  arrowCanvas.width = 128;
  arrowCanvas.height = 32;
  {
    const ctx = arrowCanvas.getContext("2d");
    ctx.fillStyle = "#2f9dff";
    ctx.fillRect(0, 0, 128, 32);
    ctx.strokeStyle = "#e9f7ff";
    ctx.lineWidth = 5;
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(48, 8);
    ctx.lineTo(70, 16);
    ctx.lineTo(48, 24);
    ctx.stroke();
  }
  const arrowTexture = new THREE.CanvasTexture(arrowCanvas);
  arrowTexture.wrapS = THREE.RepeatWrapping;
  arrowTexture.colorSpace = THREE.SRGBColorSpace;
  arrowTexture.anisotropy = 4;
  const routeGroup = new THREE.Group();
  root.add(routeGroup);
  let routeFlow = null;
  function pin(colorHex) {
    const g = new THREE.Group(),
      m = new THREE.MeshStandardMaterial({ color: colorHex, roughness: 0.4, emissive: colorHex, emissiveIntensity: 0.25 });
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.22, 20, 14), m),
      tip = new THREE.Mesh(new THREE.ConeGeometry(0.15, 0.42, 20), m),
      dot = new THREE.Mesh(new THREE.SphereGeometry(0.08, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    tip.rotation.x = Math.PI;
    tip.position.y = 0.32;
    head.position.y = 0.6;
    dot.position.set(0, 0.6, 0.19);
    head.castShadow = tip.castShadow = true;
    g.add(tip, head, dot);
    return g;
  }
  const endPin = pin(0xff6b57);
  const startDot = new THREE.Group();
  {
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(0.2, 28),
      new THREE.MeshBasicMaterial({ color: 0x2ec27e }),
    );
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.2, 0.27, 28),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
    );
    disc.rotation.x = ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.002;
    startDot.add(disc, ring);
  }
  const user = new THREE.Group();
  const userAccuracy = new THREE.Mesh(
    new THREE.CircleGeometry(1, 48),
    new THREE.MeshBasicMaterial({ color: 0x3b8bff, transparent: true, opacity: 0.16, depthWrite: false }),
  );
  const userPulse = new THREE.Mesh(
    new THREE.RingGeometry(0.85, 1, 48),
    new THREE.MeshBasicMaterial({ color: 0x3b8bff, transparent: true, opacity: 0.5, depthWrite: false, side: THREE.DoubleSide }),
  );
  const userDot = new THREE.Mesh(
    new THREE.SphereGeometry(0.16, 20, 14),
    new THREE.MeshStandardMaterial({ color: 0x2f7dff, emissive: 0x2f7dff, emissiveIntensity: 0.45 }),
  );
  const userRim = new THREE.Mesh(
    new THREE.RingGeometry(0.16, 0.24, 32),
    new THREE.MeshBasicMaterial({ color: 0xffffff, side: THREE.DoubleSide }),
  );
  userAccuracy.rotation.x = userPulse.rotation.x = userRim.rotation.x = -Math.PI / 2;
  userAccuracy.position.y = 0.14;
  userPulse.position.y = 0.15;
  userRim.position.y = 0.17;
  userDot.position.y = 0.25;
  user.add(userAccuracy, userPulse, userRim, userDot);
  // 定位点与起点始终绘制在最上层，不被树木或建筑遮挡。
  for (const m of [userAccuracy, userPulse, userRim, userDot, ...startDot.children]) {
    m.material.depthTest = false;
    m.renderOrder = 10;
  }
  user.visible = false;
  root.add(user);

  // ---------- 标签：场景地标、设施、道路名 ----------
  const labelItems = [];
  function addLabel(item) {
    const el = document.createElement(item.kind === "road" ? "span" : "button");
    el.className = "map-label" + (item.kind === "place" ? "" : " " + item.kind + "-label");
    if (item.cat) el.dataset.cat = item.cat;
    el.textContent = item.name;
    if (item.kind === "place") {
      el.dataset.place = item.key;
      el.setAttribute("aria-label", "探索" + item.name);
      el.onclick = () => onSelect(item.key, true, [item.x, 0, item.z]);
    } else if (item.kind === "poi") {
      el.dataset.poi = item.key;
      el.setAttribute("aria-label", item.name + "：查看并规划路线");
      el.onclick = () => hooks.onPoi?.(item.key);
    } else el.setAttribute("aria-hidden", "true");
    labels.append(el);
    const small = item.kind !== "place";
    labelItems.push({
      ...item,
      el,
      point: new THREE.Vector3(item.x, item.kind === "road" ? 0.2 : item.y ?? 1.6, item.z),
      width: Math.max(small ? 48 : 78, item.name.length * (small ? 11 : 11) + (small ? 18 : 24)),
      height: small ? 28 : 38,
      offset: item.kind === "road" ? -6 : 18,
    });
  }
  for (const [key, p] of Object.entries(places))
    if (!p.virtual && p.position)
      addLabel({ kind: "place", key, name: p.mapName, x: p.position[0], z: p.position[2], y: 2.0 });
  const placeNames = new Set(Object.values(places).map((p) => p.mapName));
  for (const p of map.pois)
    if (p.category !== "building" && !placeNames.has(p.name))
      addLabel({ kind: "poi", key: p.id, cat: p.category, name: p.name, x: p.x, z: p.z, y: 1.1 });
  {
    const longest = new Map();
    for (const r of map.roads) {
      if (!r.n || r.in < 0.5) continue;
      let len = 0;
      for (let i = 1; i < r.p.length; i++) len += Math.hypot(r.p[i][0] - r.p[i - 1][0], r.p[i][1] - r.p[i - 1][1]);
      if (len > (longest.get(r.n)?.len || 1.5)) longest.set(r.n, { len, r });
    }
    for (const [name, { r }] of longest) {
      const mid = r.p[Math.floor(r.p.length / 2)];
      addLabel({ kind: "road", key: name, name, x: mid[0], z: mid[1] });
    }
  }
  let poiFilter = new Set();

  // ---------- 相机 ----------
  let angle = 0.06,
    targetAngle = 0.06,
    radius = 40,
    targetRadius = 40,
    elevation = 0.98,
    targetElevation = 0.98,
    focus = new THREE.Vector3(0, 0.4, 0.5),
    targetFocus = focus.clone(),
    paused = false,
    active = true,
    visible = true,
    frame = 0,
    lastTime = 0,
    t = 0,
    selected = null,
    activePlaces = null,
    drag = null,
    moved = false,
    contextLost = false,
    pickHandler = null,
    routeEnds = null;
  let bounds = { width: 1, height: 1, left: 0, top: 0 },
    parent = { width: 1, left: 0, top: 0 },
    panelRects = [];
  const projected = new THREE.Vector3(),
    probe = new THREE.Vector3();
  const compass = document.getElementById("map-compass-needle"),
    scaleBar = document.getElementById("map-scale-bar"),
    scaleText = document.getElementById("map-scale-text");
  function resize() {
    bounds = host.getBoundingClientRect();
    parent = host.parentElement.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    renderer.setSize(bounds.width, bounds.height);
    camera.aspect = bounds.width / bounds.height;
    camera.updateProjectionMatrix();
    measurePanel();
    draw(0);
  }
  function measurePanel() {
    panelRects = ["place-panel", "route-panel"]
      .map((id) => document.getElementById(id))
      .filter((p) => p && !p.hidden)
      .map((p) => p.getBoundingClientRect());
  }
  const animated = () => !paused && (routeGroup.children.length > 0 || user.visible);
  function moving() {
    return (
      Math.abs(angle - targetAngle) > 0.001 ||
      Math.abs(radius - targetRadius) > 0.005 ||
      Math.abs(elevation - targetElevation) > 0.001 ||
      focus.distanceTo(targetFocus) > 0.005
    );
  }
  function screen(point) {
    projected.copy(point).project(camera);
    return [
      bounds.left - parent.left + (projected.x * 0.5 + 0.5) * bounds.width,
      bounds.top - parent.top + (-projected.y * 0.5 + 0.5) * bounds.height,
      projected,
    ];
  }
  function updateScale() {
    if (!scaleBar) return;
    const right = new THREE.Vector3(Math.cos(angle), 0, -Math.sin(angle));
    probe.copy(targetFocus).setY(PLINTH);
    const [x1, y1] = screen(probe);
    probe.addScaledVector(right, 100 / unit);
    const [x2, y2] = screen(probe);
    const per100 = Math.hypot(x2 - x1, y2 - y1);
    if (!per100) return;
    const options = [25, 50, 100, 200, 250, 500, 1000];
    const meters = options.find((m) => (per100 * m) / 100 >= 56) || 1000;
    scaleBar.style.width = Math.round((per100 * meters) / 100) + "px";
    scaleText.textContent = meters >= 1000 ? meters / 1000 + " km" : meters + " m";
  }
  function draw(dt) {
    if (contextLost) return;
    const ease = paused ? 1 : 1 - Math.exp(-dt * 11);
    angle += (targetAngle - angle) * ease;
    radius += (targetRadius - radius) * ease;
    elevation += (targetElevation - elevation) * ease;
    focus.lerp(targetFocus, ease);
    const fit = Math.max(1, bounds.height / Math.min(bounds.width, parent.width)),
      distance = radius * 1.23 * fit;
    camera.position.set(
      focus.x + Math.sin(angle) * distance * Math.cos(elevation),
      focus.y + distance * Math.sin(elevation),
      focus.z + Math.cos(angle) * distance * Math.cos(elevation),
    );
    camera.lookAt(focus);
    camera.updateMatrixWorld();
    if (routeFlow && !paused) routeFlow.offset.x -= dt * 0.9;
    if (!paused) {
      endPin.position.y = endPin.userData.base + Math.abs(Math.sin(t * 2.4)) * 0.12;
      const k = (t * 0.6) % 1;
      userPulse.scale.setScalar(userAccuracy.scale.x * (0.35 + k * 0.65) || 1);
      userPulse.material.opacity = 0.55 * (1 - k);
    }
    renderer.render(scene, camera);
    host.dataset.renderCount = String(Number(host.dataset.renderCount || 0) + 1);
    host.dataset.drawCalls = String(renderer.info.render.calls);
    const occupied = [],
      priority = (item) =>
        item.key === selected ? 0 : item.kind === "place" ? 1 : item.kind === "poi" ? (routeEnds?.has(item.key) ? 0 : 2) : 3;
    const sorted = [...labelItems].sort((a, b) => priority(a) - priority(b));
    const top = targetElevation > 1.3,
      near = targetRadius < 22;
    for (const item of sorted) {
      let hide = false;
      if (item.kind === "poi")
        hide = !(poiFilter.has(item.cat) || routeEnds?.has(item.key) || (near && poiFilter.size === 0 && item.cat !== "building"));
      else if (item.kind === "road") hide = !(top || near);
      else hide = activePlaces && !activePlaces.includes(item.key);
      if (!hide) {
        const [x, sy, p] = screen(item.point),
          y = sy - item.offset;
        const rect = {
          l: x - item.width / 2 - 4,
          r: x + item.width / 2 + 4,
          t: y - item.height - 4,
          b: y + 5,
        };
        hide =
          p.z > 1 ||
          Math.abs(p.x) > 1 ||
          Math.abs(p.y) > 1 ||
          rect.l < 12 ||
          rect.r > parent.width - 12 ||
          panelRects.some(
            (r) => rect.r > r.left - parent.left && rect.l < r.right - parent.left && rect.b > r.top - parent.top && rect.t < r.bottom - parent.top,
          ) ||
          occupied.some((o) => rect.l < o.r && rect.r > o.l && rect.t < o.b && rect.b > o.t);
        if (!hide) {
          occupied.push(rect);
          item.el.style.transform = `translate3d(${x}px,${y}px,0) translate(-50%,-100%)`;
        }
      }
      item.el.hidden = !!hide;
    }
    if (compass) compass.style.transform = `rotate(${(angle * 180) / Math.PI}deg)`;
    updateScale();
    host.dataset.angle = angle.toFixed(3);
    host.dataset.zoom = radius.toFixed(2);
    host.dataset.view = targetElevation > 1.3 ? "top" : "perspective";
    host.dataset.time = t.toFixed(3);
  }
  function tick(time) {
    frame = 0;
    if (!active || !visible || document.hidden || contextLost) return;
    const dt = Math.min((time - lastTime) / 1000 || 0.016, 0.05);
    lastTime = time;
    if (!paused) t += dt;
    draw(dt);
    if (moving() || animated()) frame = requestAnimationFrame(tick);
  }
  function wake() {
    if (!frame && active && visible && !document.hidden && !contextLost) {
      lastTime = performance.now();
      frame = requestAnimationFrame(tick);
    }
  }
  new ResizeObserver(resize).observe(host);
  new IntersectionObserver(
    (entries) => {
      visible = entries[0].isIntersecting;
      if (visible) wake();
      else {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    },
    { threshold: 0.01 },
  ).observe(host);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(frame);
      frame = 0;
    } else wake();
  });
  function clampFocus() {
    targetFocus.x = THREE.MathUtils.clamp(targetFocus.x, extent.minX, extent.maxX);
    targetFocus.z = THREE.MathUtils.clamp(targetFocus.z, extent.minZ, extent.maxZ);
  }

  // ---------- 交互：左键旋转，右键/Shift 平移，展开地图后滚轮缩放 ----------
  const ray = new THREE.Raycaster(),
    pointer = new THREE.Vector2();
  function hitAt(e, objects) {
    const r = renderer.domElement.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, (-(e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(pointer, camera);
    return ray.intersectObjects(objects)[0];
  }
  renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
  renderer.domElement.addEventListener("pointerdown", (e) => {
    drag = {
      x: e.clientX,
      y: e.clientY,
      angle: targetAngle,
      focus: targetFocus.clone(),
      pan: e.button === 2 || e.shiftKey,
    };
    moved = false;
    renderer.domElement.setPointerCapture(e.pointerId);
  });
  renderer.domElement.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x,
      dy = e.clientY - drag.y;
    if (Math.abs(dx) > 5 || Math.abs(dy) > 5) moved = true;
    if (!moved) return;
    if (drag.pan) {
      const k = (radius * 1.6) / Math.max(bounds.height, 1);
      const right = new THREE.Vector3(Math.cos(angle), 0, -Math.sin(angle)),
        forward = new THREE.Vector3(-Math.sin(angle), 0, -Math.cos(angle));
      targetFocus.copy(drag.focus).addScaledVector(right, -dx * k).addScaledVector(forward, dy * k);
      clampFocus();
      focus.copy(targetFocus);
    } else targetAngle = drag.angle - dx * 0.004;
    wake();
  });
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (drag && !moved) {
      if (pickHandler) {
        const hit = hitAt(e, [plinth, ground, buildings]);
        if (hit) {
          const handler = pickHandler;
          pickHandler = null;
          renderer.domElement.style.cursor = "";
          handler({ x: hit.point.x, z: hit.point.z });
        }
      } else {
        const hit = hitAt(e, clickable);
        if (hit) {
          const info = buildingInfo[hit.object.userData.owner?.[hit.faceIndex]];
          const near = Object.entries(places).find(
            ([, p]) => p.position && Math.hypot(p.position[0] - hit.point.x, p.position[2] - hit.point.z) < 1.4,
          );
          if (near) onSelect(near[0], true, [near[1].position[0], 0, near[1].position[2]]);
          else if (info) hooks.onBuilding?.({ ...info, x: hit.point.x, z: hit.point.z });
        }
      }
    }
    drag = null;
  });
  renderer.domElement.addEventListener("pointercancel", () => (drag = null));
  // 滚轮缩放：鼠标在地图（含地图标签）上时生效，以光标所指位置为中心缩放。
  const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -PLINTH),
    wheelPoint = new THREE.Vector3();
  function onWheel(e) {
    if (!active || contextLost) return;
    e.preventDefault();
    // 统一鼠标滚轮（按行/页）与触控板（按像素）；触控板双指捏合会带 ctrlKey，灵敏度更高。
    const delta = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? bounds.height : 1);
    const factor = Math.exp(THREE.MathUtils.clamp(delta, -300, 300) * (e.ctrlKey ? 0.01 : 0.0015));
    const next = THREE.MathUtils.clamp(targetRadius * factor, 6, 60),
      applied = next / targetRadius;
    if (applied === 1) return;
    const r = renderer.domElement.getBoundingClientRect();
    pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, (-(e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(pointer, camera);
    if (ray.ray.intersectPlane(groundPlane, wheelPoint)) {
      // 光标下的地面点在缩放前后保持在原处。
      targetFocus.x += (wheelPoint.x - targetFocus.x) * (1 - applied);
      targetFocus.z += (wheelPoint.z - targetFocus.z) * (1 - applied);
      clampFocus();
    }
    targetRadius = next;
    activePlaces = null;
    wake();
  }
  renderer.domElement.addEventListener("wheel", onWheel, { passive: false });
  labels.addEventListener("wheel", onWheel, { passive: false });
  renderer.domElement.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    contextLost = true;
    cancelAnimationFrame(frame);
    frame = 0;
    document.getElementById("scene-fallback").hidden = false;
    document.getElementById("scene-fallback").textContent = "三维渲染已暂停，请刷新恢复。服务目录仍可使用。";
    labels.hidden = true;
    document.getElementById("map-view").disabled = true;
    document.getElementById("map-labels-toggle").disabled = true;
  });

  // ---------- 章节镜头 ----------
  const pos = (key) => places[key]?.position || [0, 0, 0];
  const views = {
    overview: { focus: [0, 0.4, 0.5], angle: 0.06, radius: 40, elevation: 0.98, places: null },
    learning: {
      focus: [-6.5, 0.4, 2.2],
      angle: 0.14,
      radius: 31,
      elevation: 1.04,
      places: ["library", "study", "highway", "materials", "information"],
    },
    living: { focus: [pos("life")[0], 0.4, pos("life")[2]], angle: -0.1, radius: 24, elevation: 1.02, places: ["life"] },
    connection: {
      focus: [pos("activities")[0], 0.4, pos("activities")[2]],
      angle: 0.18,
      radius: 20,
      elevation: 1.0,
      places: ["activities"],
    },
  };
  function turnTo(a) {
    targetAngle = angle + Math.atan2(Math.sin(a - angle), Math.cos(a - angle));
  }
  function preset(key) {
    const v = views[key] || views.overview;
    selected = null;
    marker.visible = false;
    activePlaces = v.places;
    targetFocus.set(...v.focus);
    targetRadius = v.radius;
    targetElevation = v.elevation;
    turnTo(v.angle);
    host.dataset.chapter = key;
    measurePanel();
    wake();
  }
  // 把一组点完整放进可见区域；右侧面板遮挡的宽度不计入可用画面。
  function fit(points, pad = 1.5) {
    measurePanel();
    const a = targetAngle,
      right = [Math.cos(a), -Math.sin(a)],
      fwd = [-Math.sin(a), -Math.cos(a)];
    const us = points.map(([x, z]) => x * right[0] + z * right[1]),
      vs = points.map(([x, z]) => x * fwd[0] + z * fwd[1]);
    const u0 = Math.min(...us),
      u1 = Math.max(...us),
      v0 = Math.min(...vs),
      v1 = Math.max(...vs);
    const panel = panelRects.length && innerWidth > 640 ? Math.max(0, bounds.right - panelRects[0].left + 16) : 0,
      availW = Math.max(bounds.width - panel, bounds.width * 0.45),
      h = Math.max(bounds.height, 1);
    targetElevation = Math.max(targetElevation, 1.22);
    const tanV = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)),
      fitK = Math.max(1, h / Math.min(bounds.width, parent.width));
    const needW = ((u1 - u0) * pad + 3) / (2 * tanV * (availW / h)),
      needD = ((v1 - v0) * pad * Math.sin(targetElevation) + 3) / (2 * tanV);
    targetRadius = THREE.MathUtils.clamp(Math.max(needW, needD) / 1.23 / fitK, 10, 48);
    // 画面中心右移半个面板宽度，使路线落在面板左侧的可见区域中央。
    const visibleW = 2 * targetRadius * 1.23 * fitK * tanV * (bounds.width / h),
      shift = (panel / 2 / bounds.width) * visibleW,
      cu = (u0 + u1) / 2 + shift,
      cv = (v0 + v1) / 2;
    targetFocus.set(cu * right[0] + cv * fwd[0], 0.4, cu * right[1] + cv * fwd[1]);
    activePlaces = null;
    clampFocus();
  }
  resize();
  wake();
  return {
    snapshot() {
      return {
        angle: targetAngle,
        radius: targetRadius,
        elevation: targetElevation,
        focus: targetFocus.toArray(),
        places: activePlaces ? [...activePlaces] : null,
      };
    },
    restore(view) {
      selected = null;
      marker.visible = false;
      targetAngle = view.angle;
      targetRadius = view.radius;
      targetElevation = view.elevation;
      targetFocus.fromArray(view.focus);
      activePlaces = view.places;
      measurePanel();
      wake();
    },
    active(value) {
      active = value;
      if (!value) {
        cancelAnimationFrame(frame);
        frame = 0;
      } else {
        measurePanel();
        wake();
      }
    },
    focus(key, anchor = null, cinematic = false) {
      selected = key;
      measurePanel();
      const p = places[key];
      if (p.virtual || !p.position) {
        marker.visible = false;
        activePlaces = null;
        wake();
        return;
      }
      activePlaces = null;
      const point = anchor || p.position;
      targetFocus.set(point[0], 0.5, point[2]);
      host.dataset.focusPosition = JSON.stringify(point);
      targetRadius = cinematic ? 11 : 17;
      targetElevation = cinematic ? 0.76 : 1.05;
      marker.position.set(point[0], PLINTH + 0.06, point[2]);
      marker.visible = true;
      wake();
    },
    focusPoint(x, z, zoom = 14) {
      selected = null;
      marker.visible = false;
      activePlaces = null;
      targetFocus.set(x, 0.4, z);
      targetRadius = zoom;
      clampFocus();
      wake();
    },
    reset() {
      preset("overview");
    },
    chapter: preset,
    rotate(v) {
      targetAngle += v;
      wake();
    },
    view(top) {
      targetElevation = top ? 1.53 : 0.98;
      if (top) turnTo(0);
      wake();
    },
    zoom(v) {
      targetRadius = THREE.MathUtils.clamp(targetRadius + v, 6, 60);
      wake();
    },
    pause(value) {
      paused = value;
      wake();
    },
    night(value) {
      hemi.intensity = value ? 1.65 : 2.7;
      sun.intensity = value ? 2.6 : 4;
      sun.color.set(value ? 0xb8d9ef : 0xffead5);
      renderer.toneMappingExposure = value ? 1.1 : 1.15;
      ground.material.color.set(value ? 0x2b4459 : 0x8ea3b2);
      outsideMaterial.color.set(value ? ROAD.outside[0] : 0xb3c2cc);
      renderer.shadowMap.needsUpdate = true;
      wake();
    },
    measure: measurePanel,
    setPoiFilter(categories) {
      poiFilter = new Set(categories || []);
      wake();
      requestAnimationFrame(() => draw(0));
    },
    showRoute(route, { fitView = true } = {}) {
      this.clearRoute();
      const pts = route.points,
        y = PLINTH + 0.07;
      const glowPos = [];
      ribbon(pts, 0.42, y - 0.004, glowPos);
      const glowGeo = new THREE.BufferGeometry();
      glowGeo.setAttribute("position", new THREE.Float32BufferAttribute(glowPos, 3));
      const glow = new THREE.Mesh(
        glowGeo,
        new THREE.MeshBasicMaterial({ color: 0x9fd6ff, transparent: true, opacity: 0.45, depthWrite: false, side: THREE.DoubleSide }),
      );
      const linePos = [],
        uv = [];
      ribbon(pts, 0.24, y, linePos, uv);
      const lineGeo = new THREE.BufferGeometry();
      lineGeo.setAttribute("position", new THREE.Float32BufferAttribute(linePos, 3));
      // 每 0.5 单位（25 m）一个箭头。
      lineGeo.setAttribute("uv", new THREE.Float32BufferAttribute(uv.map((v, i) => (i % 2 === 0 ? v / 0.5 : v)), 2));
      routeFlow = arrowTexture;
      routeFlow.offset.x = 0;
      const line = new THREE.Mesh(
        lineGeo,
        new THREE.MeshBasicMaterial({ map: arrowTexture, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4 }),
      );
      // 路线始终可见：穿过建筑遮挡时也绘制在最上层。
      glow.material.depthTest = line.material.depthTest = false;
      glow.renderOrder = 8;
      line.renderOrder = 9;
      const [sx, sz] = pts[0],
        [ex, ez] = pts[pts.length - 1];
      startDot.position.set(sx, y + 0.01, sz);
      endPin.position.set(ex, PLINTH, ez);
      endPin.userData.base = PLINTH;
      routeGroup.add(glow, line, startDot, endPin);
      routeEnds = new Set([route.to?.poi, route.from?.poi].filter(Boolean));
      host.dataset.route = String(route.distance_m);
      if (fitView) fit(pts, 1);
      wake();
    },
    clearRoute() {
      for (const m of [...routeGroup.children]) {
        routeGroup.remove(m);
        if (m !== startDot && m !== endPin) {
          m.geometry.dispose();
          m.material.dispose();
        }
      }
      routeFlow = null;
      routeEnds = null;
      delete host.dataset.route;
      wake();
      draw(0);
    },
    setUser(position) {
      if (!position) {
        user.visible = false;
        delete host.dataset.user;
        wake();
        draw(0);
        return;
      }
      user.visible = true;
      user.position.set(position.x, PLINTH, position.z);
      const r = THREE.MathUtils.clamp((position.accuracy || 15) / unit, 0.3, 8);
      userAccuracy.scale.setScalar(r);
      userPulse.scale.setScalar(r);
      host.dataset.user = position.x.toFixed(2) + "," + position.z.toFixed(2);
      wake();
    },
    pick(handler) {
      pickHandler = handler;
      renderer.domElement.style.cursor = handler ? "crosshair" : "";
    },
  };
}
