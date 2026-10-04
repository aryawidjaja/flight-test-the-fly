// Flight-test the fly: replay workbench. Reads the flight recordings listed in runs/manifest.json (format: README, "Recording format").
// Loaded as a classic script (not type="module") so that opening index.html from disk works: browsers block
// module scripts on file://, but a classic script may still import() three.js from the CDN (via the import map).
//
// Frames. Recordings are NED (x north, y east, z down; body x fwd, y right, z down).
// three.js is Y-up, right-handed, so we map NED (n, e, d) -> three (e, -d, -n): east = +X, up = +Y, north = -Z.
// Attitude: NED 3-2-1 Euler (psi, theta, phi) becomes three Euler order 'YXZ' with (x = theta, y = -psi, z = -phi).
// The aircraft mesh is built nose along -Z, right wing along +X, so body axes map the same way.
// gust [u, v, w] is drawn in the HEADING frame (yaw only), as scripts/plant.py records it.
// Rudder: + command = nose-right yaw moment = trailing edge RIGHT seen from above. 1.0 <-> 16 deg (c172x).
'use strict';
(async () => {
const THREE = await import('three');
const [orbit, l2, lgeo, lmat] = await Promise.allSettled(['controls/OrbitControls.js', 'lines/Line2.js', 'lines/LineGeometry.js', 'lines/LineMaterial.js'].map((p) => import('three/addons/' + p)));
const OrbitControls = orbit.value && orbit.value.OrbitControls;
const fat = l2.value && lgeo.value && lmat.value ? { Line2: l2.value.Line2, LineGeometry: lgeo.value.LineGeometry, LineMaterial: lmat.value.LineMaterial } : null;
if (!OrbitControls || !fat) console.warn('three.js add-ons failed to load', orbit.reason || l2.reason || lgeo.reason || lmat.reason);

const GROUPS = ['T4T5_L', 'T4T5_R', 'HS_L', 'HS_R', 'VS_L', 'VS_R', 'DNa02_L', 'DNa02_R'];
const GNAME = { T4T5_L: 'T4/T5 L', T4T5_R: 'T4/T5 R', HS_L: 'HS L', HS_R: 'HS R', VS_L: 'VS L', VS_R: 'VS R', DNa02_L: 'DNa02 L', DNa02_R: 'DNa02 R' };
const DEG = 180 / Math.PI, RUDDER_SIGN = 1, RUDDER_MAX_DEG = 16, SIDE_OFFSET = 14, SMOOTH_S = 0.1, TRAIL_S = 15;
const SAT = 0.99, RELAY_PCT = 90; // |rudder cmd| >= SAT counts as saturated; > RELAY_PCT % of the flight flags a relay (same 90 % rule as the paper)
const MODES = ['chase', 'top', 'front', 'side', 'free'];
const CAMNAME = { chase: 'Chase', top: 'Top', front: 'Front', side: 'Side', free: 'Free', fit: 'Fit' };
const $ = (id) => document.getElementById(id);
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const sgn = (v, d = 1) => { const a = Math.abs(v).toFixed(d); return (+a === 0 ? '' : v < 0 ? '−' : '+') + a; };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
// Rudder, one definition for every display: cmd > 0 = nose-right moment = trailing edge to the aircraft's RIGHT.
const rudderDeg = (cmd) => cmd * RUDDER_SIGN * RUDDER_MAX_DEG;
const rudderRad = (cmd) => rudderDeg(cmd) / DEG; // hinge rotation.y: + swings the trailing edge to +X (right wing side)
const rudderText = (deg) => (Math.abs(deg) < 0.05 ? '0.0°' : `${Math.abs(deg).toFixed(1)}° ${deg > 0 ? 'R' : 'L'}`);
let css = {};
const readCss = () => { const s = getComputedStyle(document.documentElement); for (const k of ['bg', 'panel', 'panel2', 'ink', 'muted', 'line', 'line2', 'a', 'b', 'gust', 'sky', 'ground', 'grid', 'chip', 'flag', 'sans', 'mono']) css[k] = s.getPropertyValue('--' + k).trim(); };
readCss();

// ---------- data ----------
async function source() {
  if (location.protocol !== 'file:') {
    try {
      const r = await fetch('runs/manifest.json', { cache: 'no-cache' });
      if (r.ok) {
        const man = await r.json();
        return { man, get: async (f) => { const x = await fetch('runs/' + f); if (!x.ok) throw new Error('Could not load a flight recording (' + x.status + ').'); return x.json(); } };
      }
    } catch (e) { /* fall back to the bundle */ }
  }
  await new Promise((ok, bad) => {
    const s = document.createElement('script');
    s.src = 'runs/bundle.js'; s.onload = ok;
    s.onerror = () => { console.error('Serve the app folder over http, or rebuild runs/bundle.js (make_mock_runs.py --bundle-only).'); bad(new Error('No flight recordings found.')); };
    document.head.appendChild(s);
  });
  return { man: window.RUN_BUNDLE.manifest, get: async (f) => window.RUN_BUNDLE.runs[f] };
}

function lowerIdx(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m + 1; else hi = m; } return lo; }
const idxAt = (run, T) => Math.max(0, Math.min(run.n - 1, lowerIdx(run.t, T + 1e-9) - 1));

// Labels from recording metadata (controller ids: fly_real, fly_shuffle_NN[_signflip][_gX], yaw_damper, bare).
function lesionText(l) {
  if (!l || !l.kind || l.kind === 'none') return '';
  const pct = Math.round((l.fraction || 0) * 100);
  const T = { random: `${pct}% neurons silenced`, hubs_topk: `${pct}% best-connected neurons silenced`, all_HS: 'HS cells silenced', HS: 'HS cells silenced', T4_only: 'T4 cells silenced', T5_only: 'T5 cells silenced' };
  return T[l.kind] || `${String(l.kind).replace(/_/g, ' ')} cells silenced${pct ? ` (${pct}%)` : ''}`;
}
function runLabel(m) {
  const c = String(m.controller || ''), sh = c.match(/shuffle_?(\d+)/), g = c.match(/_g([\d.]+)$/);
  let s = sh ? `Fly brain, scrambled wiring #${+sh[1]}` : /^fly/.test(c) ? 'Fly brain (real wiring)' : /damper/.test(c) ? 'Classical yaw damper' : /^bare$|^none$/.test(c) ? 'No controller' : c;
  if (/signflip/.test(c)) s += ', rudder sign reversed';
  if (g) s += `, eye input ×${g[1]}`;
  return s;
}
// Manifest label first. A short label without a controller name ("40% neurons silenced") gets one from meta,
// and a lesion is always named, from meta.lesion, unless the label already says so.
function labelFor(entry, meta, i) {
  let s = entry.label || (meta ? runLabel(meta) : `Recording ${i + 1}`);
  if (meta && entry.label && !/fly|brain|damper|controller/i.test(s)) s = runLabel(meta) + ', ' + s;
  const les = meta ? lesionText(meta.lesion) : '';
  if (les && !/silenc|lesion|removed/i.test(s)) s += ', ' + les;
  return s + (meta && meta.mock ? ' (mock)' : '');
}

// Raster rows: grouped by class, then side, then cell type.
function rowGroup(type) {
  if (/^T[45]/.test(type)) return [0, 'T4/T5'];
  if (/^HS|^H2/.test(type)) return [1, 'HS·H2'];
  if (/^VS/.test(type)) return [2, 'VS'];
  if (/^DN/.test(type)) return [3, 'DN'];
  return [4, 'other'];
}

// Whole-flight numbers computed from the recording itself (shown next to the pipeline's metrics).
function derive(rec) {
  const n = rec.t.length, u = rec.rudder, e = rec.euler_rad;
  let su = 0, sat = 0, sb = 0, dpsi = 0;
  for (let i = 0; i < n; i++) {
    su += u[i]; sb += rec.beta[i]; if (Math.abs(u[i]) >= SAT) sat++;
    if (i) { const d = e[i][2] - e[i - 1][2]; dpsi += Math.atan2(Math.sin(d), Math.cos(d)); }
  }
  const p0 = rec.pos_ned_m[0], pN = rec.pos_ned_m[n - 1], psi0 = e[0][2], dn = pN[0] - p0[0], de = pN[1] - p0[1];
  return {
    meanRudderDeg: (su / n) * RUDDER_MAX_DEG, satPct: (100 * sat) / n, headingDeg: dpsi * DEG, meanBetaDeg: (sb / n) * DEG,
    xtrackM: -Math.sin(psi0) * dn + Math.cos(psi0) * de, // + = right of the initial heading line through the start
  };
}

function prep(rec, entry, i) {
  const n = rec.t.length, t = Float64Array.from(rec.t), pos = new Float32Array(n * 3);
  rec.pos_ned_m.forEach(([x, y, z], k) => { pos[3 * k] = y; pos[3 * k + 1] = -z; pos[3 * k + 2] = -x; });
  const dt = rec.meta.dt || (t[n - 1] - t[0]) / (n - 1);
  const w = Math.max(1, Math.round(SMOOTH_S / dt));
  const ch = { r: Float32Array.from(rec.r, (v) => v * DEG), rud: Float32Array.from(rec.rudder, rudderDeg) };
  const hdg = new Float32Array(n); for (let k = 1; k < n; k++) { const d = rec.euler_rad[k][2] - rec.euler_rad[k - 1][2]; hdg[k] = hdg[k - 1] + Math.atan2(Math.sin(d), Math.cos(d)) * DEG; }
  ch.hdg = hdg;
  const groups = {};
  for (const g of GROUPS) {
    const a = rec.groups && rec.groups[g];
    if (!a || !a.length) continue;
    const cs = new Float64Array(a.length + 1); a.forEach((v, k) => { cs[k + 1] = cs[k] + v; });
    const s = new Float32Array(a.length);
    for (let k = 0; k < a.length; k++) { const lo = Math.max(0, k - w + 1); s[k] = (cs[k + 1] - cs[lo]) / (k + 1 - lo); } // trailing mean, causal
    groups[g] = s; ch[g] = s;
  }
  const neurons = (rec.raster && rec.raster.neurons) || [];
  const order = neurons.map((nn) => ({ nn, g: rowGroup(nn.type) }))
    .sort((a, b) => a.g[0] - b.g[0] || String(a.nn.side).localeCompare(String(b.nn.side)) || String(a.nn.type).localeCompare(String(b.nn.type), 'en', { numeric: true }) || a.nn.idx - b.nn.idx);
  const rowOf = new Map(), blocks = [];
  order.forEach((o, row) => {
    rowOf.set(o.nn.idx, row);
    const name = o.g[1] + ' ' + (o.nn.side === 'left' ? 'L' : o.nn.side === 'right' ? 'R' : '');
    if (!blocks.length || blocks[blocks.length - 1].name !== name) blocks.push({ name, start: row, end: row + 1 }); else blocks[blocks.length - 1].end = row + 1;
  });
  const sp = ((rec.raster && rec.raster.spikes) || []).slice().sort((a, b) => a[0] - b[0]);
  const per = new Map(neurons.map((nn) => [nn.idx, []]));
  for (const [ts, id] of sp) if (per.has(id)) per.get(id).push(ts);
  const byType = new Map(); // 'HSE|left' -> [spike-time arrays]
  for (const nn of neurons) { const k = nn.type + '|' + nn.side; if (!byType.has(k)) byType.set(k, []); byType.get(k).push(Float64Array.from(per.get(nn.idx))); }
  return {
    file: entry.file, label: labelFor(entry, rec.meta, i), rec, meta: rec.meta, n, t, pos, disp: pos, dt, groups, ch, stats: derive(rec),
    sideOnly: rec.gust.every((g) => !g[0] && !g[2]), // 2-state model: side gust only (u = w = 0)
    brain: Object.keys(groups).length > 0, nRows: order.length, blocks, byType,
    spikeT: Float64Array.from(sp, (s) => s[0]), spikeRow: Int32Array.from(sp, (s) => rowOf.has(s[1]) ? rowOf.get(s[1]) : -1),
  };
}
// Mean firing rate (Hz) of the recorded cells whose type matches re, on one side, over the trailing SMOOTH_S window.
function typeRate(run, re, side, T) {
  let cnt = 0, cells = 0;
  for (const [k, arrs] of run.byType) {
    const [type, sd] = k.split('|');
    if (sd !== side || !re.test(type)) continue;
    for (const a of arrs) { cells++; cnt += lowerIdx(a, T + 1e-9) - lowerIdx(a, T - SMOOTH_S + 1e-9); }
  }
  return cells ? { hz: cnt / (cells * SMOOTH_S), cells } : null;
}

// ---------- 3D scene ----------
const view = $('view');
// If WebGL cannot start (old GPU, disabled acceleration), keep everything except the 3D view working: the renderer
// becomes a no-op stand-in and the viewport shows a plain notice.
let renderer, NO_GL = false;
try {
  // probe first: three.js logs console errors of its own when it cannot create a context
  const probe = document.createElement('canvas'), gl = probe.getContext('webgl2') || probe.getContext('webgl');
  if (!gl) throw new Error('no WebGL');
  const lose = gl.getExtension('WEBGL_lose_context'); if (lose) lose.loseContext();
  renderer = new THREE.WebGLRenderer({ antialias: true });
} catch (e) {
  NO_GL = true;
  const d = document.createElement('div');
  d.textContent = '3D view unavailable: this browser could not start WebGL. The timeline, channels, brain panel and metrics still work.';
  d.style.cssText = 'position:absolute;inset:0;display:grid;place-items:center;padding:24px;text-align:center;font:13px system-ui,sans-serif;opacity:.7';
  renderer = new Proxy({ domElement: d }, { get: (t, k) => (k in t ? t[k] : () => {}) });
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.autoClear = false;
view.prepend(renderer.domElement);
renderer.domElement.setAttribute('aria-hidden', 'true');
const V = (x = 0, y = 0, z = 0) => new THREE.Vector3(x, y, z);
const Y = V(0, 1, 0);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 0.5, 90000);
scene.add(new THREE.HemisphereLight(0xffffff, 0x556655, 1.5));
const sun = new THREE.DirectionalLight(0xffffff, 1.7); sun.position.set(300, 800, 200); scene.add(sun);
scene.fog = new THREE.FogExp2(0, 6e-5);
let seed = 12345; const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296); // fixed seed: same scenery every load

// Decorative scenery only. It is not part of the recording: the ground sits at 0 m (sea level), the recorded flights
// at ~1219 m (4000 ft). Positions and attitudes of the aircraft always come from the recording.
const SCN = {
  dark: { zen: '#0a0f17', hor: '#2c3644', tint: '#626a73', cloud: '#8592a3', cloudA: 0.28, body: '#c4c9d0', glass: '#18212c' },
  light: { zen: '#5b8cc2', hor: '#dde6ee', tint: '#ffffff', cloud: '#ffffff', cloudA: 0.7, body: '#eef0f2', glass: '#2b3a4a' },
};
// Terrain: one procedural 4.1 km tile (2 m per pixel) repeated; the ground mesh jumps by whole tiles so it never ends.
const TILE = 4096, TILES = 16;
function terrainTexture() {
  const N = 2048, cv = document.createElement('canvas'); cv.width = cv.height = N;
  const x = cv.getContext('2d');
  const wrap = (f) => { for (const dx of [-N, 0, N]) for (const dy of [-N, 0, N]) { x.save(); x.translate(dx, dy); f(); x.restore(); } };
  const FIELD = ['#7d8f50', '#93a35c', '#6a7e46', '#b3a868', '#9f8a5e', '#88995a', '#5f7341', '#c0b37c', '#7b6b4c', '#a3ad6a'];
  const field = (x0, y0, w, h) => { // fields: a recursive split of the square, so the tile edges are field edges
    x.fillStyle = FIELD[(rnd() * FIELD.length) | 0]; x.fillRect(x0, y0, w, h);
    if (rnd() < 0.45) {
      x.save(); x.beginPath(); x.rect(x0, y0, w, h); x.clip(); x.globalAlpha = 0.1; x.strokeStyle = rnd() < 0.5 ? '#000' : '#fff'; x.beginPath();
      const v = w > h; for (let k = 2; k < (v ? w : h); k += 4) { if (v) { x.moveTo(x0 + k, y0); x.lineTo(x0 + k, y0 + h); } else { x.moveTo(x0, y0 + k); x.lineTo(x0 + w, y0 + k); } }
      x.stroke(); x.restore();
    }
    x.globalAlpha = 0.35; x.strokeStyle = '#3c4a2b'; x.lineWidth = 1.5; x.strokeRect(x0 + 0.75, y0 + 0.75, w - 1.5, h - 1.5); x.globalAlpha = 1;
  };
  const split = (x0, y0, w, h) => {
    const lim = 70 + rnd() * 160;
    if (w < lim && h < lim) return field(x0, y0, w, h);
    const k = Math.round((w >= h ? w : h) * (0.3 + 0.4 * rnd()));
    if (w >= h) { split(x0, y0, k, h); split(x0 + k, y0, w - k, h); } else { split(x0, y0, w, k); split(x0, y0 + k, w, h - k); }
  };
  split(0, 0, N, N);
  for (let k = 0; k < 34; k++) { // broad tone variation
    const cx = rnd() * N, cy = rnd() * N, r = 150 + rnd() * 420, dark = rnd() < 0.6;
    wrap(() => { const g = x.createRadialGradient(cx, cy, 0, cx, cy, r); g.addColorStop(0, dark ? 'rgba(20,30,10,.22)' : 'rgba(255,250,230,.14)'); g.addColorStop(1, 'rgba(0,0,0,0)'); x.fillStyle = g; x.fillRect(cx - r, cy - r, 2 * r, 2 * r); });
  }
  for (let k = 0; k < 28; k++) { // woods
    const cx = rnd() * N, cy = rnd() * N, blobs = Array.from({ length: 8 + ((rnd() * 22) | 0) }, () => [cx + (rnd() - 0.5) * 120, cy + (rnd() - 0.5) * 90, 7 + rnd() * 22]);
    wrap(() => { x.fillStyle = '#3e5131'; for (const [bx, by, br] of blobs) { x.beginPath(); x.arc(bx, by, br, 0, 7); x.fill(); } });
  }
  const P2 = (2 * Math.PI) / N, ph = () => rnd() * 6.283;
  const curve = (fx, w, col) => { wrap(() => { x.strokeStyle = col; x.lineWidth = w; x.lineJoin = 'round'; x.beginPath(); for (let s = 0; s <= N; s += 8) { const [px, py] = fx(s); s ? x.lineTo(px, py) : x.moveTo(px, py); } x.stroke(); }); };
  const [r1, r2, r3] = [ph(), ph(), ph()]; // river: periodic meander (seamless across tiles)
  const river = (s) => [0.31 * N + 120 * Math.sin(2 * P2 * s + r1) + 45 * Math.sin(5 * P2 * s + r2) + 14 * Math.sin(13 * P2 * s + r3), s];
  curve(river, 16, '#6f7f5a'); curve(river, 9, '#4b6472');
  for (const [a, horiz] of [[0.12, 0], [0.64, 0], [0.22, 1], [0.78, 1]]) { // roads: gentle periodic wiggles
    const p = ph(), amp = 20 + rnd() * 40;
    curve((s) => { const q = a * N + amp * Math.sin(P2 * s + p); return horiz ? [s, q] : [q, s]; }, 3.5, '#cfc9b8');
  }
  const d0 = rnd() * N; curve((s) => [d0 + s, s], 5, '#d8d3c4'); // highway: slope 1 wraps seamlessly
  for (let k = 0; k < 9; k++) { // villages
    const cx = rnd() * N, cy = rnd() * N, houses = Array.from({ length: 14 + ((rnd() * 30) | 0) }, () => [cx + (rnd() - 0.5) * 70, cy + (rnd() - 0.5) * 70, 3 + rnd() * 4, rnd() < 0.5 ? '#b9a99a' : '#9b6a58']);
    wrap(() => { for (const [hx, hy, hs, c] of houses) { x.fillStyle = c; x.fillRect(hx, hy, hs, hs * 0.8); } });
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping; tex.repeat.set(TILES, TILES); tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = renderer.capabilities ? renderer.capabilities.getMaxAnisotropy() : 1;
  return tex;
}
const ground = new THREE.Mesh(new THREE.PlaneGeometry(TILE * TILES, TILE * TILES), new THREE.MeshLambertMaterial({ map: NO_GL ? null : terrainTexture() }));
ground.rotation.x = -Math.PI / 2; scene.add(ground);
// Sky dome: zenith-to-horizon gradient with a faint bright band at the horizon (attitude cue); follows the camera.
const sky = new THREE.Mesh(new THREE.SphereGeometry(60000, 32, 16), new THREE.ShaderMaterial({
  side: THREE.BackSide, depthWrite: false, fog: false, uniforms: { zen: { value: new THREE.Color() }, hor: { value: new THREE.Color() } },
  vertexShader: 'varying vec3 vd; void main() { vd = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: 'uniform vec3 zen, hor; varying vec3 vd; void main() { float h = normalize(vd).y; vec3 c = mix(hor, zen, pow(clamp(h, 0.0, 1.0), 0.5)); c += 0.05 * exp(-abs(h) * 120.0); gl_FragColor = vec4(c, 1.0);\n#include <colorspace_fragment>\n}',
}));
sky.renderOrder = -1; sky.frustumCulled = false; scene.add(sky);
// Cloud puffs in a fixed band (950-1600 m), wrapped around the camera in the vertex shader: they stand still in the
// world, so the aircraft's 51 m/s shows as puffs streaming past. Faded near the camera and at the wrap edge.
const CLOUD_N = 170, CBOX = 4200;
const clouds = (() => {
  const p = new Float32Array(CLOUD_N * 3), sz = new Float32Array(CLOUD_N * 2);
  for (let k = 0; k < CLOUD_N; k++) { p.set([rnd() * CBOX, 950 + rnd() * 650, rnd() * CBOX], 3 * k); sz.set([45 + rnd() * 110, rnd()], 2 * k); }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(p, 3)); g.setAttribute('sz', new THREE.BufferAttribute(sz, 2));
  const m = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { ctr: { value: V() }, box: { value: CBOX }, scale: { value: 500 }, col: { value: new THREE.Color() }, alpha: { value: 0.5 }, fogc: { value: new THREE.Color() }, dens: { value: 6e-5 } },
    vertexShader: `uniform vec3 ctr; uniform float box, scale; attribute vec2 sz; varying float va, vd, vs;
      void main() { vec3 p = position; p.xz = ctr.xz + mod(p.xz - ctr.xz + 0.5 * box, box) - 0.5 * box;
        vec4 mv = modelViewMatrix * vec4(p, 1.0); float d = -mv.z; vd = d; vs = sz.y;
        va = smoothstep(80.0, 260.0, d) * (1.0 - smoothstep(0.3 * box, 0.5 * box, length(p.xz - ctr.xz)));
        gl_PointSize = clamp(sz.x * scale / max(d, 1.0), 0.0, 480.0); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: `uniform vec3 col, fogc; uniform float alpha, dens; varying float va, vd, vs;
      void main() { vec2 q = gl_PointCoord - 0.5; q.x *= vs > 0.5 ? 1.0 : -1.0;
        float m = min(min(length((q - vec2(-0.17, 0.04)) * vec2(1.0, 1.7)), length((q - vec2(0.15, 0.07)) * vec2(1.0, 1.6))), length((q - vec2(0.0, -0.04)) * vec2(0.8, 1.9)));
        float a = smoothstep(0.3, 0.04, m) * alpha * va; if (a < 0.01) discard;
        gl_FragColor = vec4(mix(col, fogc, 1.0 - exp(-dens * dens * vd * vd)), a);
        #include <colorspace_fragment>
      }`,
  });
  const pts = new THREE.Points(g, m); pts.frustumCulled = false; scene.add(pts); return pts;
})();

// Procedural Cessna-172-like aircraft, metres: nose along -Z, right wing +X, up +Y, origin near the wing's quarter chord.
// loft(): a closed surface through rings of points (same count per ring), capped at both ends, flat shaded.
function loft(rings) {
  const v = [], push = (...ps) => ps.forEach((p) => v.push(p.x, p.y, p.z)), n = rings[0].length;
  for (let s = 0; s + 1 < rings.length; s++) for (let k = 0; k < n; k++) { const a = rings[s][k], b = rings[s][(k + 1) % n], c = rings[s + 1][(k + 1) % n], d = rings[s + 1][k]; push(a, b, c, a, c, d); }
  const cap = (r, flip) => { for (let k = 1; k + 1 < n; k++) flip ? push(r[0], r[k + 1], r[k]) : push(r[0], r[k], r[k + 1]); };
  cap(rings[0], true); cap(rings[rings.length - 1], false);
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3)); g.computeVertexNormals(); return g;
}
// airfoil-ish ring in a plane: le = leading-edge point, chord along +Z, thickness t along `up`
const foil = (le, c, t, up, lo = 0.35) => [le.clone(), le.clone().add(V(0, 0, 0.3 * c)).addScaledVector(up, t), le.clone().add(V(0, 0, c)), le.clone().add(V(0, 0, 0.3 * c)).addScaledVector(up, -lo * t)];
const DIH = Math.tan((1.7 * Math.PI) / 180);
function makePlane() {
  const std = (o) => new THREE.MeshStandardMaterial({ flatShading: true, side: THREE.DoubleSide, roughness: 0.55, metalness: 0.05, ...o }); // double-sided: loft winding never matters
  const team = std(), body = std(), glass = std({ roughness: 0.15, metalness: 0.4 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x24272b, roughness: 0.8 });
  const g = new THREE.Group(), add = (geo, m, parent = g) => { const o = new THREE.Mesh(geo, m); parent.add(o); return o; };
  const rod = (a, b, r, m) => { const d = b.clone().sub(a), o = add(new THREE.CylinderGeometry(r, r, d.length(), 6), m); o.position.copy(a).addScaledVector(d, 0.5); o.quaternion.setFromUnitVectors(Y, d.normalize()); return o; };
  // fuselage: body of revolution (squashed sideways), cowling at the nose, slim tail cone
  const prof = [[0.05, -4.05], [0.36, -3.95], [0.5, -3.6], [0.58, -3.0], [0.63, -2.2], [0.64, -1.2], [0.6, -0.2], [0.48, 0.8], [0.34, 1.9], [0.22, 3.0], [0.14, 3.75], [0.04, 3.95]].map(([r, s]) => new THREE.Vector2(r, s));
  const fg = new THREE.LatheGeometry(prof, 10); fg.rotateX(Math.PI / 2); fg.scale(0.86, 1.08, 1); add(fg, body);
  add(new THREE.ConeGeometry(0.2, 0.42, 10).rotateX(-Math.PI / 2).translate(0, 0, -4.25), team); // spinner
  // cabin under the wing: glass sides (box faces +x, -x, +y, -y, +z, -z), tilted windshield
  const cab = add(new THREE.BoxGeometry(0.96, 0.5, 1.6), [glass, glass, body, body, body, body]); cab.position.set(0, 0.5, -1.15);
  const ws = add(new THREE.BoxGeometry(0.9, 0.03, 0.74), glass); ws.position.set(0, 0.615, -2.28); ws.rotation.x = -0.47;
  // high wing: constant-chord inner panel, tapered outer panel, 1.7 deg dihedral, team colour
  const wy = 0.78, wing = (s) => [[0, 1.63, -2.0], [2.6, 1.63, -2.0], [5.5, 1.12, -1.82]].map(([y, c, z]) => foil(V(s * y, wy + y * DIH, z), c, 0.17 - y * 0.012, Y));
  for (const s of [-1, 1]) {
    add(loft(wing(s)), team);
    rod(V(s * 0.5, -0.38, -1.25), V(s * 2.55, wy + 2.55 * DIH - 0.04, -1.55), 0.035, body); // strut
  }
  // horizontal tail: stabiliser + elevator, light grey
  const ht = (s, z0, c0, c1) => [[0, c0, z0], [1.72, c1, z0 + 0.12]].map(([y, c, z]) => foil(V(s * y, 0.04, z), c, 0.07, Y));
  const elevH = new THREE.Group(); elevH.position.set(0, 0.04, 3.55); g.add(elevH);
  for (const s of [-1, 1]) {
    add(loft(ht(s, 2.85, 0.7, 0.55)), body);
    add(loft([[0, 0.42], [1.72, 0.33]].map(([y, c]) => foil(V(s * y, 0, y ? -0.03 : 0), c, 0.04, Y))), body, elevH);
  }
  // fin: swept, ends at a vertical hinge line at z = 3.5 m
  const side = V(1, 0, 0), finR = (y, le) => foil(V(0, y, le), 3.5 - le, 0.06, side, 1);
  add(loft([finR(0.15, 2.3), finR(1.62, 3.12)]), body);
  // rudder: hinged on the fin trailing edge; + rotation.y swings its trailing edge to +X (right). Team colour.
  const hinge = new THREE.Group(); hinge.position.set(0, 0, 3.5); g.add(hinge);
  add(loft([foil(V(0, -0.05, 0), 0.5, 0.05, side, 1), foil(V(0, 1.66, 0), 0.32, 0.04, side, 1)]), team, hinge);
  const te = new THREE.Object3D(); te.position.set(0, 0.8, 0.42); hinge.add(te); // rudder trailing-edge marker (self-check)
  // propeller: translucent disc + two blades (blade angle is decorative), fixed tricycle gear
  const disc = add(new THREE.CircleGeometry(0.95, 24), new THREE.MeshBasicMaterial({ color: 0x9aa0a8, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }));
  disc.position.z = -4.2;
  const prop = new THREE.Group(); prop.position.z = -4.18; g.add(prop);
  add(new THREE.BoxGeometry(1.9, 0.11, 0.03), dark, prop);
  for (const s of [-1, 1]) {
    rod(V(s * 0.42, -0.5, -0.65), V(s * 1.2, -1.22, -0.55), 0.04, dark);
    add(new THREE.CylinderGeometry(0.22, 0.22, 0.13, 12).rotateZ(Math.PI / 2).translate(s * 1.24, -1.25, -0.55), dark);
  }
  rod(V(0, -0.45, -3.45), V(0, -1.25, -3.55), 0.035, dark);
  add(new THREE.CylinderGeometry(0.19, 0.19, 0.11, 12).rotateZ(Math.PI / 2).translate(0, -1.27, -3.55), dark);
  scene.add(g);
  return { g, hinge, te, prop, team, body, glass };
}
function makeTrail() {
  const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial());
  line.frustumCulled = false; scene.add(line); return line;
}
function makePath() { // full flight path: screen-space fat line if the add-on loaded, else a 1 px line
  const mat = fat ? new fat.LineMaterial({ linewidth: 2.5, worldUnits: false }) : new THREE.LineBasicMaterial();
  const obj = { mat, line: null };
  obj.set = (arr) => {
    if (obj.line) { scene.remove(obj.line); obj.line.geometry.dispose(); }
    let geo;
    if (fat) { geo = new fat.LineGeometry(); geo.setPositions(arr); obj.line = new fat.Line2(geo, mat); }
    else { geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(arr, 3)); obj.line = new THREE.Line(geo, mat); }
    obj.line.frustumCulled = false; obj.line.renderOrder = 1; scene.add(obj.line);
  };
  return obj;
}
// Gust around each aircraft, in the heading frame (yaw only; scripts/plant.py _JSBSimRun.rec rotates JSBSim's
// turbulence north/east/down by psi): u + = air moving forward along the heading, v + = toward the right wing,
// w + = downward. The side component v (what drives yaw) is the primary outlined arrow (GUST_M m per m/s), with faint
// streaks drifting sideways at WEX times its speed. Optional: a thinner amber arrow of the full (u, v, w) vector.
const WN = 300, WBOX = [36, 14, 44], WEX = 3, GUST_M = 2.5, GUST_COL = 0xffd23f, GUST3_COL = 0xffa13d; // GUST_COL = --gust (dark theme)
function makeWind() {
  const g = new THREE.Group(), pos = new Float32Array(WN * 6), col = new Float32Array(WN * 8), p = new Float32Array(WN * 3);
  for (let i = 0; i < WN * 3; i++) p[i] = (rnd() - 0.5) * WBOX[i % 3];
  const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(pos, 3)); geo.setAttribute('color', new THREE.BufferAttribute(col, 4));
  const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false }));
  lines.frustumCulled = false; g.add(lines);
  // side-gust arrow: solid, horizontal, centred over the cabin, pointing across the aircraft; a dark inverted-hull
  // copy behind it gives a thin outline so it reads on terrain and sky alike
  const sm = new THREE.MeshBasicMaterial({ color: GUST_COL }), om = new THREE.MeshBasicMaterial({ color: 0x111111, side: THREE.BackSide }), side = new THREE.Group(); g.add(side);
  const shaftG = new THREE.CylinderGeometry(0.13, 0.13, 1, 8).rotateZ(-Math.PI / 2).translate(0.5, 0, 0), headG = new THREE.ConeGeometry(0.48, 1.2, 12).rotateZ(-Math.PI / 2).translate(-0.6, 0, 0);
  const shaft = new THREE.Group(), head = new THREE.Group(); side.add(shaft, head);
  shaft.add(new THREE.Mesh(shaftG, sm), new THREE.Mesh(shaftG, om)); shaft.children[1].scale.set(1, 1.6, 1.6);
  head.add(new THREE.Mesh(headG, sm), new THREE.Mesh(headG, om)); head.children[1].scale.set(1.12, 1.18, 1.18); head.children[1].position.x = 0.07;
  // full 3D gust arrow: same shapes, thinner (shaft and head scaled), amber; aimed with a quaternion each frame
  const fm = new THREE.MeshBasicMaterial({ color: GUST3_COL }), full = new THREE.Group(), fshaft = new THREE.Group(), fhead = new THREE.Group(); g.add(full); full.add(fshaft, fhead);
  fshaft.add(new THREE.Mesh(shaftG, fm), new THREE.Mesh(shaftG, om)); fshaft.children[1].scale.set(1, 1.6, 1.6);
  fhead.add(new THREE.Mesh(headG, fm), new THREE.Mesh(headG, om)); fhead.children[1].scale.set(1.12, 1.18, 1.18); fhead.children[1].position.x = 0.07; fhead.scale.setScalar(0.55);
  scene.add(g);
  return { g, lines, p, side, shaft, head, full, fshaft, fhead, rgb: new THREE.Color(GUST_COL), T: null };
}
const P = { A: makePlane(), B: makePlane() };
const trail = { A: makeTrail(), B: makeTrail() };
const path = { A: makePath(), B: makePath() };
const wind = { A: makeWind(), B: makeWind() };
// round dots: start point + where each aircraft is now (only in the overhead "fit" view, where planes are sub-pixel)
const dotTex = (() => { const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d'); x.fillStyle = '#fff'; x.beginPath(); x.arc(32, 32, 28, 0, 7); x.fill(); return new THREE.CanvasTexture(c); })();
const dots = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({ size: 10, sizeAttenuation: false, vertexColors: true, map: dotTex, alphaTest: 0.5, depthTest: false }));
dots.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(9), 3));
dots.geometry.setAttribute('color', new THREE.BufferAttribute(new Float32Array(9), 3));
dots.frustumCulled = false; dots.renderOrder = 2; scene.add(dots);

// ---------- rudder inset: its own little scene, top-down orthographic camera, drawn with a scissor viewport ----------
const iscene = new THREE.Scene();
const icam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
icam.position.set(0, 5, 0); icam.up.set(0, 0, -1); icam.lookAt(0, 0, 0); // nose (-Z) is screen-up, right wing (+X) is screen-right
function makeTail() {
  const g = new THREE.Group(), flat = (c) => new THREE.MeshBasicMaterial({ color: c });
  const mStab = flat(0), mFin = flat(0), mRud = flat(0);
  const box = (w, d, x, z, y, m, parent = g) => { const b = new THREE.Mesh(new THREE.BoxGeometry(w, 0.01, d), m); b.position.set(x, y, z); parent.add(b); return b; };
  box(1.7, 0.42, 0, -0.32, 0, mStab);     // horizontal tail
  box(0.26, 0.85, 0, -0.5, 0.01, mStab);  // tail cone
  box(0.07, 0.95, 0, -0.48, 0.02, mFin);  // fin
  const hinge = new THREE.Group(); hinge.position.set(0, 0.03, 0); g.add(hinge);
  box(0.09, 0.62, 0, 0.31, 0, mRud, hinge);
  const te = new THREE.Object3D(); te.position.set(0, 0, 0.62); hinge.add(te); // trailing-edge marker (self-check)
  const neutral = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0.04, 0), new THREE.Vector3(0, 0.04, 0.7)]), new THREE.LineDashedMaterial({ dashSize: 0.05, gapSize: 0.04 }));
  neutral.computeLineDistances(); g.add(neutral);
  iscene.add(g);
  return { g, hinge, te, mStab, mFin, mRud, neutral };
}
const tail = { A: makeTail(), B: makeTail() };

function applyTheme() {
  readCss();
  const S = SCN[document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'];
  scene.background = new THREE.Color(S.hor); scene.fog.color.set(S.hor);
  sky.material.uniforms.zen.value.set(S.zen); sky.material.uniforms.hor.value.set(S.hor);
  ground.material.color.set(S.tint);
  const cu = clouds.material.uniforms; cu.col.value.set(S.cloud); cu.alpha.value = S.cloudA; cu.fogc.value.set(S.hor); cu.dens.value = scene.fog.density;
  for (const k of ['A', 'B']) {
    const c = k === 'A' ? css.a : css.b;
    P[k].team.color.set(c); P[k].body.color.set(S.body); P[k].glass.color.set(S.glass);
    trail[k].material.color.set(c); path[k].mat.color.set(c);
    tail[k].mStab.color.set(css.line2); tail[k].mFin.color.set(css.muted); tail[k].mRud.color.set(c); tail[k].neutral.material.color.set(css.muted);
  }
  iscene.background = new THREE.Color(css.panel);
  const col = dots.geometry.attributes.color.array;
  [css.muted, css.a, css.b].forEach((c, k) => new THREE.Color(c).toArray(col, 3 * k));
  dots.geometry.attributes.color.needsUpdate = true;
}
applyTheme();

// ---------- camera ----------
const controls = OrbitControls ? new OrbitControls(camera, renderer.domElement) : null; // camera.up is +Y here, as OrbitControls requires
if (controls) { controls.enabled = false; controls.enableDamping = !reduced.matches; controls.dampingFactor = 0.12; controls.minDistance = 4; controls.maxDistance = 20000; controls.addEventListener('change', () => { dirty = true; }); }
renderer.domElement.style.touchAction = 'auto'; // let the page scroll on phones except in Free mode
const cam = { mode: 'chase', prev: 'chase', from: null, t0: 0, target: V(), focus: V(), followAt: V(), head: null, fitKey: '', fit: null };
const ease = (k) => 1 - Math.pow(1 - k, 3);

function hfov() { return 2 * Math.atan(Math.tan((camera.fov * Math.PI) / 360) * camera.aspect); }
function followPose(mode) {
  const h = cam.head, f = V(Math.sin(h), 0, -Math.cos(h)), r = V(Math.cos(h), 0, Math.sin(h));
  const halfW = sideBySide() ? 13 + SIDE_OFFSET / 2 : 11;
  const fov = Math.min(hfov(), (camera.fov * Math.PI) / 180);
  const d = Math.max(22, halfW / Math.tan(fov / 2));
  const t = cam.focus, pos = t.clone(), up = Y.clone();
  if (mode === 'chase') pos.addScaledVector(f, -d).addScaledVector(Y, d / 4);
  else if (mode === 'top') { pos.addScaledVector(Y, d * 1.1); up.copy(f); }
  else if (mode === 'front') pos.addScaledVector(f, d).addScaledVector(Y, d * 0.12);
  else if (mode === 'side') pos.addScaledVector(r, -d).addScaledVector(Y, d * 0.27);
  return { pos, target: t.clone(), up };
}
// Overhead view framing both full paths, rotated so the flight runs along the long side of the screen.
function fitPose() {
  const runs = [state.A, state.B].filter(Boolean), top = Math.min(0.45, 48 / Math.max(1, view.clientHeight)), key = runs.map((r) => r.file).join() + state.xt + camera.aspect.toFixed(3) + top.toFixed(2);
  if (cam.fitKey !== key) {
    const a = runs[0].disp;
    let ex = 0, ez = 0; for (const r of runs) { ex += r.disp[3 * r.n - 3] / runs.length; ez += r.disp[3 * r.n - 1] / runs.length; }
    let ux = ex - a[0], uz = ez - a[2]; const L = Math.hypot(ux, uz) || 1; ux /= L; uz /= L;
    const land = camera.aspect >= 1;
    const right = land ? V(ux, 0, uz) : V(-uz, 0, ux), up = land ? V(uz, 0, -ux) : V(ux, 0, uz);
    let r0 = Infinity, r1 = -Infinity, u0 = Infinity, u1 = -Infinity, ys = 0;
    for (const r of runs) for (let i = 0; i < r.n; i++) {
      const x = r.disp[3 * i], z = r.disp[3 * i + 2], pr = x * right.x + z * right.z, pu = x * up.x + z * up.z;
      if (pr < r0) r0 = pr; if (pr > r1) r1 = pr; if (pu < u0) u0 = pu; if (pu > u1) u1 = pu;
    }
    for (const r of runs) ys += r.disp[1] / runs.length;
    const cr = (r0 + r1) / 2, cu = (u0 + u1) / 2;
    const target = V(right.x * cr + up.x * cu, ys, right.z * cr + up.z * cu);
    const tv = Math.tan((camera.fov * Math.PI) / 360), th = Math.tan(hfov() / 2);
    // keep the paths clear of the camera buttons: fit them into the view height below them, shifted down by half of it
    const H = Math.max(60, ((r1 - r0) / 2) * 1.12 / th, ((u1 - u0) / 2) * 1.2 / (tv * (1 - top)));
    target.addScaledVector(up, H * tv * top);
    cam.fit = { pos: target.clone().addScaledVector(Y, H), target, up };
    cam.fitKey = key;
  }
  return { pos: cam.fit.pos.clone(), target: cam.fit.target.clone(), up: cam.fit.up.clone() };
}
function setCam(mode, instant) {
  if (mode === 'free' && !controls) return;
  if (mode === cam.mode) return;
  if (cam.mode !== 'fit') cam.prev = cam.mode;
  if (cam.mode === 'free') { controls.enabled = false; renderer.domElement.style.touchAction = 'auto'; }
  cam.from = instant || reduced.matches ? null : { pos: camera.position.clone(), target: cam.target.clone(), up: camera.up.clone() };
  cam.t0 = performance.now(); cam.mode = mode;
  if (mode === 'free') {
    cam.from = null; camera.up.copy(Y);
    if (Math.abs(camera.position.x - cam.target.x) + Math.abs(camera.position.z - cam.target.z) < 0.5) camera.position.z += 2; // leave the exact vertical
    controls.target.copy(cam.target); cam.followAt.copy(cam.focus); controls.enabled = true; controls.update();
    renderer.domElement.style.touchAction = 'none';
  }
  if (mode !== 'fit') state.autoFit = false;
  document.querySelectorAll('#cams button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.cam === mode)));
  $('followcb').disabled = mode !== 'free';
  updateLegend(); dirty = true;
}
function toggleFit() { if (cam.mode === 'fit') setCam(cam.prev || 'chase'); else { $('paths').checked = true; setCam('fit'); } }
function placeCamera(now) {
  if (cam.mode === 'free') {
    if ($('followcb').checked) { const d = cam.focus.clone().sub(cam.followAt); camera.position.add(d); controls.target.add(d); }
    cam.followAt.copy(cam.focus); cam.target.copy(controls.target); camera.lookAt(controls.target);
    return false;
  }
  const p = cam.mode === 'fit' ? fitPose() : followPose(cam.mode);
  let moving = false;
  if (cam.from) {
    const k = Math.min(1, (now - cam.t0) / 750), e = ease(k);
    p.pos.lerpVectors(cam.from.pos, p.pos, e); p.target.lerpVectors(cam.from.target, p.target, e); p.up.lerpVectors(cam.from.up, p.up, e).normalize();
    if (k >= 1) cam.from = null; else moving = true;
  }
  camera.position.copy(p.pos); camera.up.copy(p.up); camera.lookAt(p.target); cam.target.copy(p.target);
  return moving;
}

// ---------- state ----------
const state = { src: null, cache: new Map(), A: null, B: null, T: 0, dur: 1, playing: !reduced.matches, speed: 1, lastI: -1, pos: 'true', xt: 1, autoPaths: false, autoFit: false, bsrc: 'A', win: 0, follow: false, reveal: false, scale: {} };
const q = new URLSearchParams(location.search);
if (q.has('t')) state.T = +q.get('t') || 0;
if (q.get('play') === '0') state.playing = false;
if (q.get('pos') === 'side') state.pos = 'side'; // default: true positions
if (q.has('xt')) state.xt = +q.get('xt') || 1;
if (q.get('brain') === 'B') state.bsrc = 'B';
const winOf = (s) => ({ win: +String(s).replace(/^f/, '') || 0, follow: /^f/.test(s) }); // '10' = centred 10 s, 'f10' = follow 10 s
if (q.has('win')) Object.assign(state, winOf(q.get('win')));
let dirty = true;
const sideBySide = () => !!state.B && state.pos === 'side';

async function getRun(entry, i) {
  if (!entry) return null;
  if (!state.cache.has(entry.file)) state.cache.set(entry.file, prep(await state.src.get(entry.file), entry, i));
  return state.cache.get(entry.file);
}
const scenario = () => state.src.man.scenarios[$('scenario').value | 0];

function fillRunSelects() {
  const sc = scenario(), opts = sc.runs.map((r, i) => `<option value="${i}">${esc(r.label || (state.cache.has(r.file) ? state.cache.get(r.file).label : labelFor(r, r.meta, i)))}</option>`).join('');
  $('runA').innerHTML = opts; $('runB').innerHTML = '<option value="-1">None</option>' + opts;
  $('runA').value = 0; $('runB').value = sc.runs.length > 1 ? 1 : -1;
}

function applyXT() {
  // Optional, always-labelled exaggeration: sideways distance from the mean straight track, times N. Along-track is true.
  const A = state.A, runs = [A, state.B].filter(Boolean), N = state.xt;
  if (N === 1) { runs.forEach((r) => { r.disp = r.pos; }); return; }
  const o = A.pos; let ex = 0, ez = 0;
  for (const r of runs) { ex += r.pos[3 * r.n - 3] / runs.length; ez += r.pos[3 * r.n - 1] / runs.length; }
  let ux = ex - o[0], uz = ez - o[2]; const L = Math.hypot(ux, uz) || 1; ux /= L; uz /= L;
  for (const r of runs) {
    const d = new Float32Array(r.pos.length);
    for (let i = 0; i < r.n; i++) {
      const x = r.pos[3 * i] - o[0], z = r.pos[3 * i + 2] - o[2], al = x * ux + z * uz, cr = -x * uz + z * ux;
      d[3 * i] = o[0] + al * ux - N * cr * uz; d[3 * i + 1] = r.pos[3 * i + 1]; d[3 * i + 2] = o[2] + al * uz + N * cr * ux;
    }
    r.disp = d;
  }
}
function rebuildGeometry() {
  applyXT();
  for (const k of ['A', 'B']) {
    const r = state[k];
    if (r) { trail[k].geometry.setAttribute('position', new THREE.BufferAttribute(r.disp, 3)); path[k].set(r.disp); }
  }
  cam.fitKey = ''; dirty = true;
}

async function selectRuns() {
  const sc = scenario(), ia = $('runA').value | 0, b = $('runB').value | 0;
  state.A = await getRun(sc.runs[ia], ia);
  state.B = b >= 0 ? await getRun(sc.runs[b], b) : null;
  if (state.B === state.A) state.B = null;
  // a label derived from meta (no manifest label) is only known once the run is loaded
  for (const [k, i] of [['A', ia], ['B', b]]) if (state[k] && !sc.runs[i].label) { $('runA').options[i].text = state[k].label; $('runB').options[i + 1].text = state[k].label; }
  const runs = [state.A, state.B].filter(Boolean);
  state.dur = Math.max(...runs.map((r) => r.t[r.n - 1]));
  state.T = Math.min(state.T, state.dur);
  $('track').setAttribute('aria-valuemax', state.dur.toFixed(1));
  $('mock').classList.toggle('on', runs.some((r) => r.meta.mock));
  rebuildGeometry();
  computeScales();
  fillNowTable(); fillMetrics(); buildGustro(); updateLegend(); updatePosUI(); updateBrainSrc(); updateCase();
  $('scopekey').innerHTML = runs.map((r, k) => `<span class="sw sw-${k ? 'b' : 'a'}"></span>${k ? 'B' : 'A'} ${esc(r.label)}`).join('&ensp;');
  scopeKey = ''; cam.head = null; state.lastI = -1; dirty = true;
}
function updateCase() {
  const m = state.A.meta, plant = { jsbsim: 'JSBSim C172', lin2: 'linear yaw model' }[m.plant] || m.plant || '';
  const turb = String(m.turbulence || '').replace(/^dryden_/, 'Dryden ').replace(/_/g, ' ');
  $('sb-case').textContent = [plant, m.seed != null ? 'gust seed ' + m.seed : '', turb, String(m.data_version || '').replace('flywire_', 'FlyWire ')].filter(Boolean).join(' · ');
}

// ---------- overlays + dock ----------
const RELAY_TXT = 'relay (rudder saturated), steady skid';
const isRelay = (r) => r.stats.satPct > RELAY_PCT;
const relayTip = (r) => (/lowest training yaw rate/.test(r.label) ? 'Selected for the lowest RMS yaw rate on training gusts, a score that barely penalizes a steady turn. ' : '')
  + 'This controller is a relay: the rudder sits at its stop and the aircraft skids in a steady turn, so it drifts far off course.';
function updateLegend() { // one-line strip along the bottom of the viewport; the details live in its tooltip
  if (!state.A) return;
  const A = state.A, B = state.B, item = (k, r) => `<span class="it"><span class="sw sw-${k.toLowerCase()}"></span><b>${k}</b> ${esc(r.label)}${isRelay(r) ? ` <span class="flag" title="${esc(relayTip(r))}">relay · steady skid</span>` : ''}</span>`;
  const tips = [`A: ${A.label}`]; if (B) tips.push(`B: ${B.label}`);
  for (const [k, r] of [['A', A], ['B', B]]) if (r && isRelay(r)) tips.push(`${k}: ${relayTip(r)}`);
  if (cam.mode === 'fit') {
    tips.push('Full paths from above. Dots: position now; grey dot: start.');
    if (B && sameGust()) {
      const ia = A.n - 1, ib = B.n - 1, sep = Math.hypot(A.pos[3 * ia] - B.pos[3 * ib], A.pos[3 * ia + 2] - B.pos[3 * ib + 2]);
      tips.push(`Paths end ${sep < 10 ? sep.toFixed(1) : Math.round(sep)} m apart${state.xt !== 1 ? ' (true distance)' : ''}.` + (state.xt === 1 && sep < 100 ? ' Small differences hide under the line width at true scale (Display → Sideways).' : ''));
    }
  } else {
    tips.push(`Yellow arrow: side gust v (horizontal, across the aircraft; R = blowing toward the right wing), ${GUST_M} m per m/s. Faint streaks drift with it at ×${WEX} speed.`);
    tips.push($('g3d').checked ? `Thin amber arrow: the full gust vector (u forward, v side, w down; heading frame), same scale.` : 'Head/tail (u) and vertical (w) gusts are in the readout; Display → Full 3D gust arrow draws them.');
    tips.push(sideBySide() ? `Side by side: B drawn ${SIDE_OFFSET} m right of A, not at its true position.` : B ? 'True positions; the camera follows A. Tags mark A and B.' : 'True position.');
  }
  if ([A, B].some((r) => r && !r.sideOnly)) tips.push('Altitude changes because vertical and head/tail gusts act on the aircraft while elevator and throttle stay fixed at trim.');
  tips.push('Ground, sky and clouds are decorative; position and attitude come from the recording.');
  const diff = B && !sameGust();
  if (diff) tips.push('These flights met different gusts: not a like-for-like comparison.');
  const bits = [cam.mode === 'fit' ? 'full paths from above' : `<span class="gk">→</span> side gust (${GUST_M} m per m/s)` + ($('g3d').checked ? ' + <span class="gk" style="color:#ffa13d">→</span> full 3D' : ''), `positions: ${sideBySide() ? 'B offset ' + SIDE_OFFSET + ' m' : 'true'}`, 'scenery decorative'];
  const el = $('legend');
  el.innerHTML = item('A', A) + (B ? item('B', B) : '') + (diff ? ' <span class="flag">different gusts</span>' : '') + bits.map((x) => `<span class="dot">·</span>${x}`).join('');
  el.title = tips.join('\n');
  $('badge').textContent = `Sideways ×${state.xt}`;
  $('badge').classList.toggle('on', state.xt !== 1);
}
const sameGust = () => !state.B || (state.A.meta.seed === state.B.meta.seed && state.A.meta.turbulence === state.B.meta.turbulence);
function updatePosUI() {
  $('posmode').value = state.pos; $('posmode').disabled = !state.B;
  const xt = state.xt !== 1 ? ` Sideways distances from the mean straight track are stretched ×${state.xt}; along-track distances and all angles are true.` : '';
  $('posnote').textContent = (!state.B ? 'Only one aircraft selected.' : state.pos === 'side'
    ? `Side by side: B is drawn ${SIDE_OFFSET} m right of A to compare attitude. Its full path is true.`
    : 'True positions: the close-up cameras follow A; B may drift out of view.') + xt;
}

const NOW_ROWS = [
  ['Yaw rate r', '°/s', 'Nose-right positive', (r, i) => sgn(r.ch.r[i], 2)],
  ['Sideslip β', '°', 'Air from the right positive', (r, i) => sgn(r.rec.beta[i] * DEG, 2)],
  ['Bank φ', '°', 'Right wing down positive', (r, i) => sgn(r.rec.euler_rad[i][0] * DEG, 1)],
  ['Heading Δψ', '°', 'Heading change since the start, nose-right positive', (r, i) => sgn(r.ch.hdg[i], 1)],
  ['Rudder δr', '', 'Trailing edge right (R, nose-right moment) or left (L); stops at 16°', (r, i) => rudderText(r.ch.rud[i])],
  ['Side gust v', 'm/s', 'Side gust in the heading frame; + = toward the right wing', (r, i) => sgn(r.rec.gust[i][1], 2)],
];
const head = () => `<thead><tr><th></th><th><span class="sw sw-a"></span> A</th>${state.B ? '<th><span class="sw sw-b"></span> B</th>' : ''}</tr></thead>`;
function fillNowTable() {
  $('now').innerHTML = head() + '<tbody>' + NOW_ROWS.map(([n, u, d], k) => `<tr><td title="${esc(d)}">${n} <span class="u">${u}</span></td><td class="num" id="nowA${k}"></td>${state.B ? `<td class="num" id="nowB${k}"></td>` : ''}</tr>`).join('') + '</tbody>';
}
function updateNow(iA, iB) {
  NOW_ROWS.forEach(([, , , f], k) => { $('nowA' + k).textContent = f(state.A, iA); if (state.B) $('nowB' + k).textContent = f(state.B, iB); });
}

const MET = [
  ['RMS yaw rate', '°/s', 'Root-mean-square yaw rate over the flight (pipeline metric). Main score: lower is steadier.', (r) => fx(r.rec.metrics.rms_r, DEG, 2)],
  ['RMS sideslip', '°', 'Root-mean-square sideslip over the flight (pipeline metric).', (r) => fx(r.rec.metrics.rms_beta, DEG, 2)],
  ['Max |bank|', '°', 'Largest bank angle (pipeline metric). The simple yaw model has no roll axis.', (r) => r.meta.plant === 'lin2' ? '–' : fx(r.rec.metrics.max_abs_phi, DEG, 1)],
  ['Departed', '', 'Bank > 60°, sideslip > 20° or yaw rate > 60°/s for more than 1 s (pipeline metric).', (r) => r.rec.metrics.departed == null ? '–' : r.rec.metrics.departed ? '<span class="yes">yes</span>' : 'no'],
  null,
  ['Mean rudder', '°', 'Mean rudder deflection over the flight, computed from the recording. + = nose-right.', (r) => sgn(r.stats.meanRudderDeg, 2)],
  ['Rudder saturated', '%', `Share of the flight with |rudder command| ≥ ${SAT} (at the ±16° stop), computed from the recording.`, (r) => r.stats.satPct.toFixed(1)],
  ['Heading change', '°', 'Heading at the end minus heading at the start (unwrapped), computed from the recording.', (r) => sgn(r.stats.headingDeg, 1)],
  ['Mean sideslip', '°', 'Signed mean sideslip over the flight, computed from the recording. A large mean is a steady skid.', (r) => sgn(r.stats.meanBetaDeg, 2)],
  ['Cross-track offset', 'm', 'Final distance from the line through the start along the initial heading; + = right. Computed from the recording.', (r) => sgn(r.stats.xtrackM, Math.abs(r.stats.xtrackM) < 100 ? 1 : 0)],
];
const fx = (v, s, d) => (v == null ? '–' : (v * s).toFixed(d));
function fillMetrics() {
  const runs = [['A', state.A], ['B', state.B]].filter((x) => x[1]);
  const flags = runs.some(([, r]) => isRelay(r)) ? `<tr class="flags"><td></td>${runs.map(([, r]) => `<td>${isRelay(r) ? `<span class="flag" title="${esc(relayTip(r))}">${RELAY_TXT}</span>` : ''}</td>`).join('')}</tr>` : '';
  $('met').innerHTML = head() + '<tbody>' + flags + MET.map((m) => m
    ? `<tr><td title="${esc(m[2])}">${m[0]} <span class="u">${m[1]}</span></td>${runs.map(([, r]) => `<td class="num">${m[3](r)}</td>`).join('')}</tr>`
    : `<tr class="sub"><td colspan="${runs.length + 1}">Computed from the recording</td></tr>`).join('') + '</tbody>';
  $('metnote').innerHTML = runs.filter(([, r]) => isRelay(r)).map(([k, r]) => {
    const s = r.stats;
    return `<b>${k}</b>: rudder at its stop ${s.satPct.toFixed(0)}% of the flight (mean ${sgn(s.meanRudderDeg, 1)}°), so this controller acts as a relay. The aircraft holds a steady skidding turn: mean sideslip ${sgn(s.meanBetaDeg, 1)}°, heading change ${sgn(s.headingDeg, 0)}°, ending ${Math.abs(s.xtrackM).toFixed(0)} m ${s.xtrackM >= 0 ? 'right' : 'left'} of the initial track.`;
  }).join('<br>') || 'Hover a metric name for its definition.';
}

// ---------- brain schematic ----------
// [cx, cy, w, name, code line, plain description, group key or raster rule]
const NODES = {
  T4T5_L: [85, 32, 130, 'T4/T5 L', 'T4a/T5a · T4b/T5b', 'T4/T5 (motion detectors), left side'],
  T4T5_R: [235, 32, 130, 'T4/T5 R', 'T4a/T5a · T4b/T5b', 'T4/T5 (motion detectors), right side'],
  HS_L: [40, 92, 72, 'HS L', 'HSE HSN HSS', 'HS (turn sensors), left side'],
  VS_L: [120, 92, 72, 'VS L', 'VS1–VS8', 'VS (roll sensors), left side'],
  VS_R: [200, 92, 72, 'VS R', 'VS1–VS8', 'VS (roll sensors), right side'],
  HS_R: [280, 92, 72, 'HS R', 'HSE HSN HSS', 'HS (turn sensors), right side'],
  H2_L: [134, 146, 48, 'H2 L', '', 'H2 (cross-brain cell), left side', /^H2$/, 'left'],
  H2_R: [186, 146, 48, 'H2 R', '', 'H2 (cross-brain cell), right side', /^H2$/, 'right'],
  DNa02_L: [40, 206, 72, 'DNa02 L', 'steering', 'DNa02 (steering neuron), left side'],
  DNg02_L: [120, 206, 72, 'DNg02 L', 'DNg02_a–h', 'DNg02 (descending neurons, types a–h), left side', /^DNg02/, 'left'],
  DNg02_R: [200, 206, 72, 'DNg02 R', 'DNg02_a–h', 'DNg02 (descending neurons, types a–h), right side', /^DNg02/, 'right'],
  DNa02_R: [280, 206, 72, 'DNa02 R', 'steering', 'DNa02 (steering neuron), right side'],
  rudder: [160, 262, 132, 'Rudder', 'K · (R − L)', 'Rudder = K · washout(DNa02 R − DNa02 L), clipped to ±16°'],
};
const NODE_H = 38;
const CODES = { HS: /^HS[ENS]$/ }; // per-type breakdown shown in the node tooltip
const ROWCAP = [[8, 'Motion detectors · optic lobe'], [68, 'Turn (HS) and roll (VS) sensors'], [122, 'Cross-brain (H2)'], [182, 'Descending neurons']];
const EDGES = [['T4T5_L', 'HS_L'], ['T4T5_L', 'VS_L'], ['T4T5_R', 'VS_R'], ['T4T5_R', 'HS_R'], ['HS_L', 'DNa02_L'], ['VS_L', 'DNa02_L'], ['VS_R', 'DNa02_R'], ['HS_R', 'DNa02_R'], ['DNa02_L', 'rudder'], ['DNa02_R', 'rudder']];
const svgEl = (tag, attrs, parent) => { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const nodeEls = {};
(function drawPathwayStatic() {
  const eg = $('edges'), ng = $('nodes');
  for (const [a, b] of EDGES) svgEl('line', { class: 'edge', x1: NODES[a][0], y1: NODES[a][1] + NODE_H / 2, x2: NODES[b][0], y2: NODES[b][1] - NODE_H / 2 }, eg);
  for (const [y, t] of ROWCAP) svgEl('text', { x: 4, y, class: 'row' }, eg).textContent = t;
  for (const k in NODES) {
    const [x, y, w, name, codes, , re] = NODES[k], g = svgEl('g', { class: 'node' + (re && /DNg02|H2/.test(k) ? ' dim' : '') }, ng);
    const title = svgEl('title', {}, g);
    const rect = svgEl('rect', { x: x - w / 2, y: y - NODE_H / 2, width: w, height: NODE_H, rx: 2 }, g);
    const glow = svgEl('rect', { class: 'glow', x: x - w / 2 + 0.5, y: y - NODE_H / 2 + 0.5, width: w - 1, height: NODE_H - 1, rx: 2, 'fill-opacity': 0 }, g);
    svgEl('text', { x, y: codes ? y - 6 : y - 2, class: 'nm' }, g).textContent = name;
    if (codes) svgEl('text', { x, y: y + 5, class: 'cd' }, g).textContent = codes;
    const hz = svgEl('text', { x, y: codes ? y + 15 : y + 11, class: 'hz' }, g);
    nodeEls[k] = { glow, hz, title };
  }
})();
const glowOf = (hz) => Math.sqrt(clamp(hz / 200, 0, 1)); // absolute scale, the same for every node
function updatePathway(run, i, T, tag) {
  const col = tag === 'A' ? 'var(--a)' : 'var(--b)';
  const set = (k, hz, extra) => {
    const e = nodeEls[k], [, , , , , plain] = NODES[k];
    if (hz == null) { e.hz.textContent = 'n/a'; e.glow.setAttribute('fill-opacity', 0); e.glow.style.filter = ''; e.title.textContent = plain + ': not recorded in this flight.'; return; }
    const g = glowOf(hz);
    e.glow.style.fill = col; e.glow.setAttribute('fill-opacity', (0.05 + 0.45 * g).toFixed(3));
    e.glow.style.filter = g > 0.05 ? `drop-shadow(0 0 ${(g * 5).toFixed(1)}px ${col})` : '';
    e.hz.textContent = hz.toFixed(0) + ' Hz';
    e.title.textContent = `${plain}: ${hz.toFixed(1)} spikes/s (${Math.round(SMOOTH_S * 1000)} ms trailing mean)${extra || ''}.`;
  };
  for (const g of GROUPS) {
    const s = run.groups[g], side = g.endsWith('_L') ? 'left' : 'right';
    let extra = '';
    if (g.startsWith('HS')) extra = '. Per cell: ' + ['HSE', 'HSN', 'HSS'].map((c) => { const r = typeRate(run, new RegExp('^' + c + '$'), side, T); return r ? `${c} ${r.hz.toFixed(0)}` : `${c} n/a`; }).join(', ') + ' Hz';
    if (g.startsWith('T4T5')) extra = '. Group rate over all horizontal-motion T4/T5 cells on this side';
    set(g, s ? s[i] : null, extra);
  }
  for (const k of ['H2_L', 'H2_R', 'DNg02_L', 'DNg02_R']) {
    const [, , , , , , re, side] = NODES[k], r = typeRate(run, re, side, T);
    set(k, r ? r.hz : null, r ? `, from ${r.cells} recorded cell${r.cells > 1 ? 's' : ''}; not in the rudder law` : '');
  }
  const u = run.ch.rud[i], e = nodeEls.rudder, g = Math.min(1, Math.abs(u) / RUDDER_MAX_DEG);
  e.glow.style.fill = col; e.glow.setAttribute('fill-opacity', (0.05 + 0.55 * g).toFixed(3));
  e.glow.style.filter = g > 0.05 ? `drop-shadow(0 0 ${(g * 5).toFixed(1)}px ${col})` : '';
  e.hz.textContent = rudderText(u);
  e.title.textContent = NODES.rudder[5] + `. Now ${rudderText(u)}.`;
}
function brainRun() {
  const pick = state[state.bsrc];
  if (pick && pick.brain) return [pick, state.bsrc];
  const o = state.bsrc === 'A' ? 'B' : 'A';
  return state[o] && state[o].brain ? [state[o], o] : [null, ''];
}
function updateBrainSrc() {
  const [run, tag] = brainRun();
  $('brain').classList.toggle('off', !run);
  document.querySelectorAll('#bsrc button').forEach((b) => { const r = state[b.dataset.k]; b.disabled = !(r && r.brain); b.setAttribute('aria-pressed', String(b.dataset.k === tag)); });
  $('brainwho').textContent = run ? `${tag} · ${run.label}` : '';
  const what = (r) => (!r ? '' : /damper/.test(r.meta.controller) ? 'classical yaw damper (no neurons)' : /^bare$/.test(r.meta.controller) ? 'no controller (rudder centred)' : 'no brain activity recorded');
  $('nobrain').textContent = run ? '' : `No fly brain in this comparison. A: ${what(state.A)}.` + (state.B ? ` B: ${what(state.B)}.` : '');
  scopeKey = ''; state.lastI = -1; dirty = true;
}

// ---------- canvases: timeline axis + channel scope (shared time axis) ----------
function fitCanvas(c) {
  const dpr = Math.min(devicePixelRatio, 2), w = Math.round(c.clientWidth * dpr), h = Math.round(c.clientHeight * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; return true; }
  return false;
}
function niceStep(span, maxTicks) { const raw = span / Math.max(1, maxTicks), p = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw); }
function niceCeil(v) { const p = 10 ** Math.floor(Math.log10(v)); return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((m) => m * p).find((s) => s >= v * 1.02); }
const fmtT = (t, step) => (step < 1 ? t.toFixed(1) : t.toFixed(0));
// Window: full flight; a fixed-length window centred on the playhead; or Follow, a trailing window (oscilloscope) with
// the playhead at FOLLOW_AT of its width once past the start.
const FOLLOW_AT = 0.9;
const viewWin = (T) => {
  const w = state.win;
  if (!w || (!state.follow && w >= state.dur)) return [0, state.dur];
  const t0 = state.follow ? Math.max(0, T - FOLLOW_AT * w) : clamp(T - w / 2, 0, state.dur - w);
  return [t0, t0 + w];
};

const axis = $('axis'), track = $('track');
function drawTimeline(T) {
  fitCanvas(axis);
  const c = axis.getContext('2d'), dpr = axis.width / Math.max(1, axis.clientWidth), W = axis.clientWidth, H = axis.clientHeight, D = state.dur;
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  const X = (t) => (t / D) * W, step = niceStep(D, W / 70);
  const [w0, w1] = viewWin(T);
  if (state.win) { c.fillStyle = css.chip; c.fillRect(X(w0), 0, X(w1) - X(w0), H); c.strokeStyle = css.line2; c.strokeRect(Math.round(X(w0)) + 0.5, 0.5, Math.round(X(w1) - X(w0)) - 1, H - 1); }
  c.fillStyle = css.line; c.fillRect(0, H - 9, W, 3);
  c.fillStyle = css.a; c.globalAlpha = 0.75; c.fillRect(0, H - 9, X(T), 3); c.globalAlpha = 1;
  c.strokeStyle = css.muted; c.lineWidth = 1; c.fillStyle = css.muted; c.font = `10.5px ${css.mono}`; c.textBaseline = 'top';
  c.beginPath();
  for (let t = 0; t <= D + 1e-9; t += step / 5) { const x = Math.round(X(t)) + 0.5, major = Math.abs(t / step - Math.round(t / step)) < 1e-6; c.moveTo(x, 0); c.lineTo(x, major ? 7 : 3); }
  c.stroke();
  for (let t = 0; t <= D + 1e-9; t += step) { const x = X(t); c.textAlign = t === 0 ? 'left' : x > W - 20 ? 'right' : 'center'; c.fillText(fmtT(t, step) + (t === 0 ? ' s' : ''), t === 0 ? x + 2 : x, 9); }
  const x = Math.round(X(T)) + 0.5;
  c.strokeStyle = css.ink; c.lineWidth = 1.5; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, H); c.stroke();
  c.fillStyle = css.ink; c.beginPath(); c.moveTo(x - 4, 0); c.lineTo(x + 4, 0); c.lineTo(x, 5); c.fill();
}

const scope = $('scope'), scopeBase = document.createElement('canvas'), scopeEmpty = document.createElement('canvas'); // empty = grid and labels only (draw as it plays)
let scopeKey = '', SL = null;
const CHANNELS = [
  { k: 'r', name: 'Yaw rate r', unit: '°/s', sym: true },
  { k: 'rud', name: 'Rudder δr', unit: '°', sym: true, fixed: RUDDER_MAX_DEG, note: '+ = R' },
  ...GROUPS.map((g) => ({ k: g, name: GNAME[g], unit: 'Hz', brain: true })),
];
function computeScales() {
  const runs = [state.A, state.B].filter(Boolean);
  for (const ch of CHANNELS) {
    let m = 0;
    for (const r of runs) { const a = r.ch[ch.k]; if (a) for (const v of a) { const x = ch.sym ? Math.abs(v) : v; if (x > m) m = x; } }
    state.scale[ch.k] = ch.fixed || niceCeil(Math.max(m, ch.sym ? 0.1 : 1));
  }
}
function scopeLayout() {
  const r = scope.getBoundingClientRect(), tr = track.getBoundingClientRect(), W = r.width, H = r.height;
  const x0 = tr.left - r.left, x1 = tr.right - r.left, axisH = 14, gap = 5;
  const rasterH = Math.max(40, Math.round(H * 0.4)), hs = Math.max(9, (H - axisH - rasterH - 2 * gap - 4) / CHANNELS.length);
  let y = 2; const rows = CHANNELS.map((ch, k) => { if (k === 2) y += gap; const row = { ch, y, h: hs }; y += hs; return row; });
  y += gap;
  return { W, H, x0, x1, rows, ry: y, rh: Math.max(20, H - axisH - y), axisY: H - axisH };
}
function traceRun(c, run, arr, t0, t1, L, yOf) {
  const pw = L.x1 - L.x0, dpr = scope.width / Math.max(1, L.W), cols = Math.max(1, Math.round(pw * dpr));
  const i0 = idxAt(run, t0), i1 = Math.min(run.n - 1, idxAt(run, t1) + 1);
  c.beginPath();
  if (i1 - i0 < cols) { // fewer samples than pixels: polyline through the samples
    for (let i = i0; i <= i1; i++) { const x = L.x0 + ((run.t[i] - t0) / (t1 - t0)) * pw, y = yOf(arr[i]); i === i0 ? c.moveTo(x, y) : c.lineTo(x, y); }
  } else { // min/max envelope per device-pixel column
    for (let col = 0; col < cols; col++) {
      const a = idxAt(run, t0 + (col / cols) * (t1 - t0)), b = Math.max(a, idxAt(run, t0 + ((col + 1) / cols) * (t1 - t0)));
      let mn = Infinity, mx = -Infinity; for (let i = a; i <= b; i++) { const v = arr[i]; if (v < mn) mn = v; if (v > mx) mx = v; }
      const x = L.x0 + (col + 0.5) / dpr;
      col ? c.lineTo(x, yOf(mx)) : c.moveTo(x, yOf(mx)); c.lineTo(x, yOf(mn) + 0.01);
    }
  }
  c.stroke();
}
function renderScopeBase(cv, t0, t1, data) {
  cv.width = scope.width; cv.height = scope.height;
  const L = SL, c = cv.getContext('2d'), dpr = scope.width / Math.max(1, L.W), pw = L.x1 - L.x0;
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  const X = (t) => L.x0 + ((t - t0) / (t1 - t0)) * pw, step = niceStep(t1 - t0, pw / 70);
  // time grid
  c.strokeStyle = css.line; c.lineWidth = 1; c.beginPath();
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-9; t += step) { const x = Math.round(X(t)) + 0.5; c.moveTo(x, 0); c.lineTo(x, L.axisY); }
  c.stroke();
  c.textBaseline = 'middle';
  const runs = [['b', state.B], ['a', state.A]].filter((x) => x[1]); // A drawn last, on top
  for (const { ch, y, h } of L.rows) {
    const ym = y + h / 2, s = state.scale[ch.k];
    c.strokeStyle = css.line; c.beginPath(); c.moveTo(0, Math.round(y + h) + 0.5); c.lineTo(L.W, Math.round(y + h) + 0.5); c.stroke();
    // name left, full scale right; stacked when the gutter is too narrow for one line
    const nf = `500 ${Math.min(11, h - 2)}px ${css.sans}`, sf = `${Math.min(10, h - 3)}px ${css.mono}`, st = (ch.sym ? `±${s} ${ch.unit}` : `${s} ${ch.unit}`) + (ch.note ? `, ${ch.note}` : '');
    c.font = nf; const nw = c.measureText(ch.name).width; c.font = sf; const one = 8 + nw + 6 + c.measureText(st).width <= L.x0 - 6;
    const stack = !one && h >= 18;
    c.font = nf; c.fillStyle = css.ink; c.textAlign = 'left'; c.fillText(ch.name, 8, stack ? ym - h / 4 : ym);
    if (one || stack) { c.font = sf; c.fillStyle = css.muted; c.textAlign = stack ? 'left' : 'right'; c.fillText(st, stack ? 8 : L.x0 - 6, stack ? ym + h / 4 : ym); }
    const pad = Math.min(2, h * 0.15), yOf = ch.sym ? (v) => ym - (clamp(v, -s, s) / s) * (h / 2 - pad) : (v) => y + h - pad - (clamp(v, 0, s) / s) * (h - 2 * pad);
    if (ch.sym) { c.strokeStyle = css.line2; c.setLineDash([2, 3]); c.beginPath(); c.moveTo(L.x0, Math.round(ym) + 0.5); c.lineTo(L.x1, Math.round(ym) + 0.5); c.stroke(); c.setLineDash([]); }
    c.save(); c.beginPath(); c.rect(L.x0, y, pw, h); c.clip();
    c.lineWidth = 1; c.lineJoin = 'round';
    let any = false;
    for (const [k, r] of runs) { const a = r.ch[ch.k]; if (!a) continue; any = true; c.strokeStyle = css[k]; if (data) traceRun(c, r, a, t0, t1, L, yOf); }
    c.restore();
    if (!any && ch.brain && ch.k === GROUPS[0]) { c.fillStyle = css.muted; c.textAlign = 'left'; c.font = `11px ${css.sans}`; c.fillText('No fly brain in A or B: no neuron channels.', L.x0 + 8, ym + h); }
  }
  // raster of the selected brain
  const [run, tag] = brainRun(), ry = L.ry, rh = L.rh;
  c.font = `500 10.5px ${css.sans}`; c.textAlign = 'left';
  if (!run || !run.nRows) {
    c.fillStyle = css.muted; c.fillText(run ? 'No individual spikes recorded for this flight.' : 'Spike raster: no fly brain in A or B.', L.x0 + 8, ry + rh / 2);
  } else {
    const rowH = rh / run.nRows;
    run.blocks.forEach((b, k) => { if (k % 2) { c.fillStyle = css.chip; c.fillRect(0, ry + b.start * rowH, L.x1, (b.end - b.start) * rowH); } });
    // labels: one per side block if there is room, else one per class ("HS·H2 L R")
    const cls = new Map(); for (const b of run.blocks) { const [n, s] = b.name.split(' '); if (!cls.has(n)) cls.set(n, []); cls.get(n).push({ ...b, s }); }
    c.fillStyle = css.muted; c.font = `500 ${Math.min(10.5, Math.max(9, rowH * 6))}px ${css.sans}`;
    for (const [n, bs] of cls) {
      if (bs.every((b) => (b.end - b.start) * rowH >= 10)) for (const b of bs) c.fillText(b.name, 8, ry + ((b.start + b.end) / 2) * rowH);
      else if ((bs[bs.length - 1].end - bs[0].start) * rowH >= 7) c.fillText(n + ' ' + bs.map((b) => b.s).join(' '), 8, ry + ((bs[0].start + bs[bs.length - 1].end) / 2) * rowH);
    }
    c.save(); c.beginPath(); c.rect(L.x0, ry, pw, rh); c.clip();
    c.fillStyle = css[tag.toLowerCase()]; c.globalAlpha = 0.9;
    const sx = pw / (t1 - t0), sw = Math.max(1 / dpr, Math.min(2, sx * 0.002)), sh = Math.max(1 / dpr, rowH - (rowH > 3 ? 1 : 0));
    if (data) for (let k = lowerIdx(run.spikeT, t0), e = lowerIdx(run.spikeT, t1); k < e; k++) {
      const row = run.spikeRow[k]; if (row < 0) continue;
      c.fillRect(L.x0 + (run.spikeT[k] - t0) * sx, ry + row * rowH, sw, sh);
    }
    c.restore(); c.globalAlpha = 1;
    c.strokeStyle = css.line; c.beginPath(); c.moveTo(0, Math.round(ry) - 0.5); c.lineTo(L.W, Math.round(ry) - 0.5); c.stroke();
  }
  // time axis labels
  c.fillStyle = css.muted; c.font = `10px ${css.mono}`; c.textBaseline = 'middle';
  for (let t = Math.ceil(t0 / step) * step; t <= t1 + 1e-9; t += step) { const x = X(t); c.textAlign = x < L.x0 + 12 ? 'left' : x > L.x1 - 12 ? 'right' : 'center'; c.fillText(fmtT(t, step), x, L.axisY + 7); }
  c.textAlign = 'right'; c.fillText(state.win ? `${state.follow ? 'follow' : 'window'} ${state.win} s` : 'time, s', L.x0 - 6, L.axisY + 7);
  if (run) { c.textAlign = 'right'; c.font = `500 10px ${css.sans}`; c.fillStyle = css[tag.toLowerCase()]; c.fillText(`spikes · ${tag}`, L.W - 6, ry + 8); c.fillStyle = css.muted; c.fillText(`${run.nRows} cells`, L.W - 6, ry + 21); }
}
function drawScope(T) {
  const resized = fitCanvas(scope);
  if (resized || !SL) { SL = scopeLayout(); scopeKey = ''; }
  const [t0, t1] = viewWin(T), key = `${t0.toFixed(4)}|${t1.toFixed(4)}`;
  if (key !== scopeKey) { renderScopeBase(scopeBase, t0, t1, true); if (state.reveal) renderScopeBase(scopeEmpty, t0, t1, false); scopeKey = key; }
  const c = scope.getContext('2d'), L = SL, dpr = scope.width / Math.max(1, L.W);
  const x = Math.round(L.x0 + ((T - t0) / (t1 - t0)) * (L.x1 - L.x0)) + 0.5;
  c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, scope.width, scope.height);
  if (state.reveal) { // draw as it plays: the data copy only left of the playhead, over the empty copy
    c.drawImage(scopeEmpty, 0, 0);
    const xp = clamp(Math.round(x * dpr), 0, scope.width); if (xp > 0) c.drawImage(scopeBase, 0, 0, xp, scope.height, 0, 0, xp, scope.height);
  } else c.drawImage(scopeBase, 0, 0);
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.strokeStyle = css.ink; c.lineWidth = 1; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, L.axisY); c.stroke();
  // live values in the right gutter: A then B
  c.textBaseline = 'middle'; c.textAlign = 'right';
  const cols = [['A', state.A, L.W - 6 - (L.W - L.x1) / 2], ['B', state.B, L.W - 6]].filter((v) => v[1]);
  for (const { ch, y, h } of L.rows) {
    c.font = `500 ${Math.min(10.5, h - 2)}px ${css.mono}`;
    for (const [k, r, xr] of cols) {
      const a = r.ch[ch.k]; if (!a) continue;
      const v = a[idxAt(r, T)]; c.fillStyle = css[k.toLowerCase()];
      c.fillText(ch.unit === 'Hz' ? v.toFixed(0) : ch.k === 'rud' ? rudderText(v).replace('°', '') : sgn(v, 1), xr, y + h / 2);
    }
  }
}
function invalidateCanvases() { SL = null; scopeKey = ''; state.lastI = -1; dirty = true; }

// ---------- rudder sign self-check (?selftest=1): body.dataset.selftest = 'ok' or the failures ----------
function selfTest() {
  const fail = [], ok = (c, m) => { if (!c) fail.push(m); }, w = V(), h = V();
  ok(rudderText(rudderDeg(0.5)) === '8.0° R', '+0.5 -> 8.0° R');
  ok(rudderText(rudderDeg(-0.5)) === '8.0° L', '-0.5 -> 8.0° L');
  ok(rudderText(rudderDeg(0)) === '0.0°', '0 -> 0.0°');
  // inset (top view, nose up): + rudder puts the trailing edge to the RIGHT of the hinge on screen, and aft (down)
  const t = tail.A, r0 = t.hinge.rotation.y; t.hinge.rotation.y = rudderRad(0.5); iscene.updateMatrixWorld(true); icam.updateMatrixWorld(true); icam.updateProjectionMatrix();
  t.te.getWorldPosition(w).project(icam); t.hinge.getWorldPosition(h).project(icam);
  ok(w.x - h.x > 0.01 && w.y < h.y, `inset TE screen dx ${(w.x - h.x).toFixed(3)} (want > 0), dy ${(w.y - h.y).toFixed(3)} (want < 0)`);
  t.hinge.rotation.y = r0;
  // 3D model, body axes: + rudder puts the trailing edge on the right-wing side (+X)
  const p = P.A, g0 = [p.g.position.clone(), p.g.rotation.clone()], hr = p.hinge.rotation.y;
  p.g.position.set(0, 0, 0); p.g.rotation.set(0, 0, 0); p.hinge.rotation.y = rudderRad(0.5); p.g.updateMatrixWorld(true);
  p.te.getWorldPosition(w); ok(w.x > 0.05 && w.z > 3.5, `3D TE at x ${w.x.toFixed(3)} m (want > 0)`);
  p.g.position.copy(g0[0]); p.g.rotation.copy(g0[1]); p.hinge.rotation.y = hr;
  document.body.dataset.selftest = fail.length ? fail.join('; ') : 'ok';
  console.log('selftest:', document.body.dataset.selftest);
}

// ---------- frame ----------
const gv = V(), XA = V(1, 0, 0);
function poseAircraft(k, run, i, at, T) {
  const p = P[k], W = wind[k]; p.g.visible = W.g.visible = !!run; trail[k].visible = false; if (!run) return;
  const [phi, theta, psi] = run.rec.euler_rad[i];
  p.g.position.copy(at);
  p.g.rotation.set(theta, -psi, -phi, 'YXZ');
  p.hinge.rotation.y = rudderRad(run.rec.rudder[i]);
  p.prop.rotation.z = (T * 9) % (2 * Math.PI); // decorative, slowed down (a real prop turns ~40 rev/s)
  // gust: heading frame (u fwd, v right, w down) -> three local (v, -w, -u)
  W.g.position.copy(at); W.g.rotation.set(0, -psi, 0);
  const v = run.rec.gust[i][1];
  gv.set(v, 0, 0); // side component only: + = toward the right wing = +X here
  const sl = Math.abs(v) * GUST_M, sg = v >= 0 ? 1 : -1; W.side.visible = sl > 0.1;
  if (W.side.visible) {
    W.shaft.scale.x = Math.max(0.01, sl - 1.2); W.head.position.x = sl;
    W.side.position.set(-sg * sl / 2, 2.6, -1.2); W.side.rotation.y = sg > 0 ? 0 : Math.PI;
  }
  const [gu, , gw] = run.rec.gust[i], fd = V(v, -gw, -gu), fl = fd.length() * GUST_M; // full vector, three local
  W.full.visible = $('g3d').checked && fl > 0.1;
  if (W.full.visible) {
    fd.normalize(); W.full.quaternion.setFromUnitVectors(XA, fd);
    W.full.position.set(0, 3.8, -1.2).addScaledVector(fd, -fl / 2); // centred 1.2 m above the side arrow
    W.fshaft.scale.set(Math.max(0.01, fl - 0.66), 0.45, 0.45); W.fhead.position.x = fl;
  }
  // streaks: advect by the playhead step (not wall time), wrap inside the box, fade toward the box edge
  const dT = W.T == null ? 0 : clamp(T - W.T, -0.25, 0.25); W.T = T;
  const pos = W.lines.geometry.attributes.position.array, col = W.lines.geometry.attributes.color.array, q = W.p, c = W.rgb;
  const sx = gv.x * WEX, sy = gv.y * WEX, sz = gv.z * WEX, tl = 0.25;
  for (let n = 0; n < WN; n++) {
    let x = q[3 * n] + sx * dT, y = q[3 * n + 1] + sy * dT, z = q[3 * n + 2] + sz * dT;
    x -= Math.round(x / WBOX[0]) * WBOX[0]; y -= Math.round(y / WBOX[1]) * WBOX[1]; z -= Math.round(z / WBOX[2]) * WBOX[2];
    q[3 * n] = x; q[3 * n + 1] = y; q[3 * n + 2] = z;
    pos.set([x, y, z, x - sx * tl, y - sy * tl, z - sz * tl], 6 * n);
    const r2 = (2 * x / WBOX[0]) ** 2 + (2 * y / WBOX[1]) ** 2 + (2 * z / WBOX[2]) ** 2;
    col.set([c.r, c.g, c.b, 0.32 * Math.max(0, 1 - r2), c.r, c.g, c.b, 0], 8 * n);
  }
  W.lines.geometry.attributes.position.needsUpdate = true; W.lines.geometry.attributes.color.needsUpdate = true;
  const keep = Math.round(TRAIL_S / run.dt), s = Math.max(0, i - keep);
  trail[k].geometry.setDrawRange(s, i - s + 1);
}
const tip = V();
const gustTxt = (v) => `${Math.abs(v).toFixed(1)} ${Math.abs(v) < 0.05 ? '\u2007' : v > 0 ? 'R' : 'L'}`;
const GUST_TIP = ['Gust (air velocity) in the heading frame, rotated by yaw only, m/s. +u: air moving forward along the heading (tailwind).',
  '+v: toward the right wing (yellow arrow). This is what drives yaw.', '+w: downward (sinking air).'];
// fixed gust readout, bottom right: built once per selection, numbers updated each frame
function buildGustro() {
  const h = (t, tip, c = '') => `<span class="h${c}" title="${esc(tip)}">${t}</span>`;
  const row = (k, r) => `<b class="k${k.toLowerCase()}">${k}</b>` + [0, 1, 2].map((j) => j !== 1 && r.sideOnly
    ? '<span class="na" title="this model has side gusts only">–</span>' : `<span id="g${k}${j}"${j === 1 ? ' class="v"' : ''}></span>`).join('');
  $('gustro').innerHTML = h('Gust m/s', GUST_TIP.join(' ')) + h('u fwd', GUST_TIP[0]) + h('v side', GUST_TIP[1], ' v') + h('w down', GUST_TIP[2])
    + row('A', state.A) + (state.B ? row('B', state.B) : '');
}
function updateHud(A, iA, B, iB) {
  const ro = $('gustro'), on = cam.mode !== 'fit'; // hidden in the overhead fit view, which has no arrows
  ro.hidden = !on;
  if (on) for (const [k, r, i] of [['A', A, iA], ['B', B, iB]]) {
    if (!r) continue;
    const g = r.rec.gust[i];
    $(`g${k}1`).textContent = gustTxt(g[1]);
    if (!r.sideOnly) { $(`g${k}0`).textContent = sgn(g[0], 1); $(`g${k}2`).textContent = sgn(g[2], 1); }
  }
  // small A / B tags over the aircraft, side by side so overlapping aircraft stay identifiable
  for (const [k, run] of [['A', A], ['B', B]]) {
    const el = $('tag' + k);
    if (!B || !run || NO_GL || cam.mode === 'fit') { el.hidden = true; continue; }
    tip.copy(P[k].g.position); tip.y += 3; tip.project(camera);
    el.hidden = tip.z > 1 || Math.abs(tip.x) > 1.05 || Math.abs(tip.y) > 1.05;
    el.style.transform = `translate(${Math.round(((tip.x + 1) / 2) * view.clientWidth)}px, ${Math.round(((1 - tip.y) / 2) * view.clientHeight)}px) translate(${k === 'A' ? '-100%' : '0'}, -100%)`;
  }
}
const at = (run, i) => V(run.disp[3 * i], run.disp[3 * i + 1], run.disp[3 * i + 2]);
let insetRect = { w: 0, h: 0 };
const INSET_Y = 30; // px from the viewport bottom: above the 22 px legend strip (matches .vp --strip in index.html)

function frame(T, now) {
  const A = state.A, B = state.B; if (!A) return false;
  const iA = idxAt(A, T), iB = B ? idxAt(B, T) : 0, psi = A.rec.euler_rad[iA][2];
  const pa = at(A, iA), showPaths = $('paths').checked;
  poseAircraft('A', A, iA, pa, T);
  const pbTrue = B ? at(B, iB) : null;
  poseAircraft('B', B, iB, sideBySide() ? pa.clone().add(V(Math.cos(psi) * SIDE_OFFSET, 0, Math.sin(psi) * SIDE_OFFSET)) : pbTrue, T);
  wind.A.g.visible = cam.mode !== 'fit'; wind.B.g.visible = !!B && cam.mode !== 'fit';
  trail.A.visible = !showPaths; trail.B.visible = !!B && !showPaths && !sideBySide();
  path.A.line.visible = showPaths; if (path.B.line) path.B.line.visible = showPaths && !!B;
  // dots (fit view only): start, A now, B now (B always at its true position here)
  const dp = dots.geometry.attributes.position.array;
  dp.set([A.disp[0], A.disp[1], A.disp[2], pa.x, pa.y, pa.z, ...(pbTrue ? [pbTrue.x, pbTrue.y, pbTrue.z] : [pa.x, pa.y, pa.z])]);
  dots.geometry.attributes.position.needsUpdate = true; dots.geometry.setDrawRange(0, B ? 3 : 2);
  dots.visible = cam.mode === 'fit';

  // follow focus: A (and the side-by-side B), heading smoothed and unwrapped
  cam.focus.copy(pa); if (sideBySide()) cam.focus.lerp(P.B.g.position, 0.5); cam.focus.y += 1.5;
  if (cam.head == null || reduced.matches) cam.head = psi; else cam.head += Math.atan2(Math.sin(psi - cam.head), Math.cos(psi - cam.head)) * 0.05;
  const moving = placeCamera(now);
  const dist = camera.position.distanceTo(cam.target);
  // scenery follows the camera: sky dome centred on it, ground jumps by whole texture tiles (stays world-fixed)
  sky.position.copy(camera.position);
  ground.position.set(Math.round(camera.position.x / TILE) * TILE, 0, Math.round(camera.position.z / TILE) * TILE);
  clouds.material.uniforms.ctr.value.copy(camera.position); clouds.visible = cam.mode !== 'fit';

  // render main view, then the rudder inset into a scissored corner
  renderer.setScissorTest(false); renderer.clear(); renderer.render(scene, camera);
  renderInset(A, iA, B, iB);
  updateHud(A, iA, B, iB);

  // scale bar for the overhead view
  const sc = $('scale');
  if (cam.mode === 'fit') {
    const mpp = (2 * dist * Math.tan((camera.fov * Math.PI) / 360)) / Math.max(1, view.clientHeight);
    const nice = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((m) => m / mpp >= 60) || 5000;
    sc.firstChild.style.width = Math.round(nice / mpp) + 'px';
    sc.lastChild.textContent = (nice >= 1000 ? nice / 1000 + ' km' : nice + ' m') + (state.xt !== 1 ? ' along track' : '');
  }
  sc.classList.toggle('on', cam.mode === 'fit');

  $('time').innerHTML = `${T.toFixed(2)} <small>/ ${state.dur.toFixed(0)} s</small>`;
  $('track').setAttribute('aria-valuenow', T.toFixed(1));
  drawTimeline(T); drawScope(T); updateStatus(iA, iB, T);
  const [br, tag] = brainRun(), iBr = br ? idxAt(br, T) : 0;
  if (iA !== state.lastI || !now) {
    updateNow(iA, iB);
    $('nowt').textContent = `t = ${T.toFixed(2)} s`;
    if (br) updatePathway(br, iBr, T, tag);
  }
  state.lastI = iA;
  return moving;
}
function updateStatus(iA, iB, T) {
  const two = (f) => f(state.A, iA) + (state.B ? ' / ' + f(state.B, iB) : '');
  $('sb-t').textContent = T.toFixed(2);
  $('sb-r').textContent = two((r, i) => sgn(r.ch.r[i], 2));
  $('sb-b').textContent = two((r, i) => sgn(r.rec.beta[i] * DEG, 2));
  $('sb-d').textContent = two((r, i) => rudderText(r.ch.rud[i]));
  $('sb-cam').textContent = CAMNAME[cam.mode] || '';
  $('sb-scale').textContent = (state.xt === 1 ? '1:1' : `sideways ×${state.xt}`) + (sideBySide() ? ', B offset' : '');
}

function renderInset(A, iA, B, iB) {
  const { w, h } = insetRect; if (!w) return;
  const two = !!B, span = two ? 4 : 2.2, H = span * (h / w), cz = -0.03;
  icam.left = -span / 2; icam.right = span / 2; icam.top = H / 2 - cz; icam.bottom = -H / 2 - cz; icam.updateProjectionMatrix();
  tail.A.g.position.x = two ? -1 : 0; tail.B.g.visible = two;
  if (two) tail.B.g.position.x = 1;
  tail.A.hinge.rotation.y = rudderRad(A.rec.rudder[iA]);
  if (two) tail.B.hinge.rotation.y = rudderRad(B.rec.rudder[iB]);
  renderer.setViewport(8, INSET_Y, w, h); renderer.setScissor(8, INSET_Y, w, h); renderer.setScissorTest(true);
  renderer.clear(); renderer.render(iscene, icam);
  renderer.setScissorTest(false); renderer.setViewport(0, 0, view.clientWidth, view.clientHeight);
  const cell = (k, r, i) => `<span><small>${k}</small>${rudderText(r.ch.rud[i])}</span>`;
  $('insetvals').innerHTML = cell('A', A, iA) + (two ? cell('B', B, iB) : '');
}

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h, false); camera.aspect = w / Math.max(1, h); camera.updateProjectionMatrix();
  for (const k of ['A', 'B']) if (path[k].mat.resolution) path[k].mat.resolution.set(w, h);
  const iw = Math.round(Math.min(190, w * 0.45));
  insetRect = { w: iw, h: Math.round(iw * 0.66) };
  Object.assign($('inset').style, { width: insetRect.w + 'px', height: insetRect.h + 'px' });
  if (!NO_GL) { const b = renderer.getDrawingBufferSize(new THREE.Vector2()); clouds.material.uniforms.scale.value = b.y / (2 * Math.tan((camera.fov * Math.PI) / 360)); }
  cam.fitKey = ''; invalidateCanvases();
}
// Resize work never runs inside the ResizeObserver callback: it is deferred to the next animation frame and
// coalesced, so nothing the callback triggers can change an observed size within the same notification pass.
let roQueued = false;
const ro = new ResizeObserver(() => { if (roQueued) return; roQueued = true; requestAnimationFrame(() => { roQueued = false; resize(); }); });
for (const el of [view, scope, track]) ro.observe(el);

let last = performance.now();
function loop(now) {
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (state.playing && state.A) {
    state.T += dt * state.speed; dirty = true;
    if (state.T >= state.dur) { state.T = state.dur; onEnd(); }
  }
  if (cam.mode === 'free' && controls.update()) dirty = true;
  if ((dirty || cam.from) && state.A) { dirty = false; frame(state.T, now); }
  emitFrame(now);
  requestAnimationFrame(loop);
}
// ---------- event contract for brain3d.js (and anything else): wb:frame, wb:tab, window.WB ----------
// wb:frame {t, which, run}: t = playhead (s); which = 'A' | 'B' (whose brain is shown); run = that flight's recording object
// recording object (same reference while unchanged). Sent on frames where t or the brain run changed, at most 30 Hz;
// a change inside the throttle window is sent as soon as the window ends. With no fly brain in A or B: which '', run null.
const ev = { t: NaN, run: undefined, at: -1e9 };
function emitFrame(now) {
  const [br, which] = brainRun(), run = br ? br.rec : null;
  if ((state.T === ev.t && run === ev.run) || now - ev.at < 1000 / 30 - 1) return;
  ev.t = state.T; ev.run = run; ev.at = now;
  window.dispatchEvent(new CustomEvent('wb:frame', { detail: { t: state.T, which, run } }));
}
window.WB = {
  get t() { return state.T; },
  get brainRun() { const [br] = brainRun(); return br ? br.rec : null; },
  get brainWhich() { return brainRun()[1]; },
  get tab() { return brainTab; },
};
const PLAY = '<svg viewBox="0 0 16 16"><path d="M4.5 3v10l8-5z" fill="currentColor"/></svg>', PAUSE = '<svg viewBox="0 0 16 16"><path d="M4.5 3h2.2v10H4.5zM9.3 3h2.2v10H9.3z" fill="currentColor"/></svg>';
function setPlaying(p) {
  if (p && state.T >= state.dur) { // restart: undo what the end-of-flight summary switched on
    state.T = 0;
    if (state.autoPaths) { $('paths').checked = false; state.autoPaths = false; }
    if (state.autoFit && cam.mode === 'fit') setCam(cam.prev);
  }
  state.playing = p; $('play').innerHTML = p ? PAUSE : PLAY; $('play').setAttribute('aria-label', p ? 'Pause' : 'Play');
  dirty = true;
}
function onEnd() { // end of flight: show both full paths from above
  setPlaying(false);
  if (!$('paths').checked) { $('paths').checked = true; state.autoPaths = true; }
  if (cam.mode !== 'fit') { setCam('fit'); state.autoFit = true; }
}
const seek = (T) => { state.T = clamp(T, 0, state.dur); dirty = true; };

// ---------- wiring ----------
$('play').onclick = () => setPlaying(!state.playing);
$('speed').onchange = (e) => { state.speed = +e.target.value; };
const reselect = async () => { await selectRuns(); dirty = true; };
$('scenario').onchange = async () => { fillRunSelects(); await reselect(); };
$('runA').onchange = $('runB').onchange = reselect;
const swap = async () => { const a = $('runA').value, b = $('runB').value; if (b === '-1') return; $('runA').value = b; $('runB').value = a; await reselect(); };
$('swap').onclick = swap;
$('cams').onclick = (e) => { const b = e.target.closest('button'); if (!b || b.disabled) return; if (b.dataset.cam === 'fit') toggleFit(); else setCam(b.dataset.cam); };
$('posmode').onchange = (e) => { state.pos = e.target.value; updatePosUI(); updateLegend(); dirty = true; };
$('paths').onchange = () => { state.autoPaths = false; dirty = true; };
const setXT = (v) => { state.xt = v; $('xt').value = String(v); rebuildGeometry(); updateLegend(); updatePosUI(); };
$('xt').onchange = (e) => setXT(+e.target.value);
$('followcb').onchange = () => { dirty = true; };
$('bsrc').onclick = (e) => { const b = e.target.closest('button'); if (!b || b.disabled) return; state.bsrc = b.dataset.k; updateBrainSrc(); };
const winVal = () => (state.follow ? 'f' : '') + state.win;
const setWin = (v) => { Object.assign(state, winOf(v)); $('win').value = winVal(); scopeKey = ''; dirty = true; };
$('win').onchange = (e) => setWin(e.target.value);
const setReveal = (on) => { state.reveal = on; $('reveal').setAttribute('aria-pressed', String(on)); scopeKey = ''; dirty = true; };
$('reveal').onclick = () => setReveal(!state.reveal);
const setXp = (on) => {
  $('wb').classList.toggle('xp', on); $('xpand').setAttribute('aria-pressed', String(on));
  $('xpand').title = on ? 'Restore the channel panel (E or Esc)' : 'Expand the channel panel (E); Esc restores';
  if (on && matchMedia('(max-width: 760px)').matches) document.querySelector('.scope').scrollIntoView({ block: 'start' });
};
$('xpand').onclick = () => setXp(!$('wb').classList.contains('xp'));
$('g3d').onchange = () => { updateLegend(); dirty = true; };
const setTheme = (t) => { document.documentElement.dataset.theme = t; try { localStorage.setItem('ftf-theme', t); } catch (e) { /* private mode */ } applyTheme(); invalidateCanvases(); };
$('theme').onclick = () => setTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
document.addEventListener('click', (e) => { for (const d of document.querySelectorAll('.disp[open], .keys[open]')) if (!d.contains(e.target)) d.open = false; });

// timeline + scope scrubbing: drag on the axis; on the strips, drag scrubs (full flight) or pans (zoomed window)
function scrubber(el, toT) {
  let drag = null;
  el.addEventListener('pointerdown', (e) => { if (e.button) return; drag = { x: e.clientX, T: state.T }; el.setPointerCapture(e.pointerId); seek(toT(e, drag)); });
  el.addEventListener('pointermove', (e) => { if (drag) seek(toT(e, drag)); });
  const end = () => { drag = null; }; el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
}
scrubber(track, (e) => { const r = track.getBoundingClientRect(); return ((e.clientX - r.left) / r.width) * state.dur; });
scrubber(scope, (e, d) => {
  const L = SL || scopeLayout(), r = scope.getBoundingClientRect(), pw = L.x1 - L.x0;
  if (!state.win) return ((e.clientX - r.left - L.x0) / pw) * state.dur;
  if (e.type === 'pointerdown') return state.T;
  return d.T - ((e.clientX - d.x) / pw) * state.win;
});
track.addEventListener('keydown', (e) => { if (e.key === 'Home') { seek(0); e.preventDefault(); } else if (e.key === 'End') { seek(state.dur); e.preventDefault(); } });

// splitter between viewport and channel strips
(() => {
  const wb = $('wb'), sp = $('split');
  try { const h = +localStorage.getItem('ftf-scope-h'); if (h > 100) wb.style.setProperty('--scope-h', h + 'px'); } catch (e) { /* no storage */ }
  let d = null;
  sp.addEventListener('pointerdown', (e) => { d = { y: e.clientY, h: document.querySelector('.scope').offsetHeight }; sp.setPointerCapture(e.pointerId); sp.classList.add('drag'); });
  sp.addEventListener('pointermove', (e) => {
    if (!d) return;
    const h = Math.round(clamp(d.h - (e.clientY - d.y), 130, innerHeight * 0.62));
    wb.style.setProperty('--scope-h', h + 'px'); try { localStorage.setItem('ftf-scope-h', h); } catch (er) { /* no storage */ }
  });
  const end = () => { d = null; sp.classList.remove('drag'); }; sp.addEventListener('pointerup', end); sp.addEventListener('pointercancel', end);
})();

// brain dock tabs: Signal path / 3D brain (WAI-ARIA tabs: arrows, Home, End; remembered in localStorage)
let brainTab = 'path';
const tabs = [...document.querySelectorAll('#btabs [role="tab"]')];
function setBrainTab(t, focus) {
  if (t !== 'path' && t !== '3d') t = 'path';
  const changed = t !== brainTab; brainTab = t;
  for (const b of tabs) { const on = b.dataset.tab === t; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; $(b.getAttribute('aria-controls')).hidden = !on; if (on && focus) b.focus(); }
  try { localStorage.setItem('ftf-brain-tab', t); } catch (e) { /* no storage */ }
  if (changed) window.dispatchEvent(new CustomEvent('wb:tab', { detail: { tab: t } }));
}
$('btabs').addEventListener('click', (e) => { const b = e.target.closest('[role="tab"]'); if (b) setBrainTab(b.dataset.tab); });
$('btabs').addEventListener('keydown', (e) => {
  const i = tabs.indexOf(document.activeElement); if (i < 0) return;
  const j = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: tabs.length - 1 }[e.key];
  if (j == null) return;
  e.preventDefault(); e.stopPropagation(); // keep the arrows from also scrubbing the timeline
  setBrainTab(tabs[(j + tabs.length) % tabs.length].dataset.tab, true);
});
try { setBrainTab(localStorage.getItem('ftf-brain-tab') || 'path'); } catch (e) { setBrainTab('path'); }

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'Escape' && $('wb').classList.contains('xp')) { setXp(false); return; }
  const el = document.activeElement || document.body, tag = el.tagName;
  if (tag === 'SELECT' || tag === 'TEXTAREA' || (tag === 'INPUT' && el.type !== 'checkbox') || el.closest('#guide')) return;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (e.code === 'Space') { if (/BUTTON|INPUT|SUMMARY/.test(tag)) return; e.preventDefault(); setPlaying(!state.playing); return; }
  if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); seek(state.T + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 0.1)); return; }
  const n = '12345'.indexOf(k);
  if (n >= 0) setCam(MODES[n]);
  else if (k === 'f') toggleFit();
  else if (k === 'p') { $('paths').checked = !$('paths').checked; state.autoPaths = false; dirty = true; }
  else if (k === 's' && state.B) { state.pos = state.pos === 'side' ? 'true' : 'side'; updatePosUI(); updateLegend(); dirty = true; }
  else if (k === 'x') { const o = [1, 5, 10, 20]; setXT(o[(o.indexOf(state.xt) + 1) % o.length]); }
  else if (k === 'w') swap();
  else if (k === 'b') { state.bsrc = state.bsrc === 'A' ? 'B' : 'A'; updateBrainSrc(); }
  else if (k === 'z') { const o = [...$('win').options].map((x) => x.value); setWin(o[(o.indexOf(winVal()) + 1) % o.length]); }
  else if (k === 'd') setReveal(!state.reveal);
  else if (k === 'e') $('xpand').click();
  else if (k === 't') $('theme').click();
});
reduced.addEventListener('change', () => { if (controls) controls.enableDamping = !reduced.matches; });
if (!controls) { const b = document.querySelector('[data-cam="free"]'); b.disabled = true; b.title = 'Free camera unavailable: its 3D controls did not load.'; }
document.fonts && document.fonts.ready.then(() => { readCss(); invalidateCanvases(); });

state.src = await source();
$('scenario').innerHTML = state.src.man.scenarios.map((s, i) => `<option value="${i}">${esc(s.label || s.id)}</option>`).join('');
const full = state.src.man.scenarios.findIndex((s) => /jsbsim/.test(s.id));
$('scenario').value = q.has('scenario') ? q.get('scenario') : String(Math.max(0, full));
fillRunSelects();
if (q.has('a')) $('runA').value = q.get('a');
if (q.has('b')) $('runB').value = q.get('b');
$('xt').value = String(state.xt); $('win').value = winVal();
if (q.get('paths') === '1') $('paths').checked = true;
await selectRuns();
setPlaying(state.playing);
resize();
cam.mode = '';
setCam('chase', true); frame(state.T, performance.now()); // place the camera once, so Free/fit start from a real pose
if (MODES.includes(q.get('cam')) || q.get('cam') === 'fit') { setCam(q.get('cam'), true); frame(state.T, performance.now()); }
if (q.has('selftest')) selfTest();
document.body.dataset.ready = '1';
requestAnimationFrame(loop);
})().catch((e) => {
  const el = document.getElementById('err'); el.textContent += String((e && e.message) || e) + '\n'; el.classList.add('on');
  console.error(e); document.body.dataset.error = '1';
});
// ResizeObserver loop notices are benign browser warnings, never shown to the user
window.addEventListener('error', (e) => { if (!e.message || /ResizeObserver loop/.test(e.message)) return; const el = document.getElementById('err'); el.textContent += e.message + '\n'; el.classList.add('on'); document.body.dataset.error = '1'; });
