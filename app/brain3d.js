// 3D brain: every FlyWire v783 neuron as a point at its soma (else its anchor point), with the simulated
// subcircuit highlighted and the model's spikes flashing in sync with the replay.
// Classic script (file:// safe); three.js comes in through the page's import map with dynamic import().
// Data: window.BRAIN3D (data/brain3d.js, base64) or data/brain3d.json + .bin; format in scripts/export_brain_points.py.
// Listens: window 'wb:tab' {tab} (init + pause), window 'wb:frame' {t, which, run}.
(() => {
  'use strict';
  const FLASH_WIN = 0.05, FLASH_TAU = 0.02, T4_BIN = 0.01, SLAB_UM = 24;
  const CLASS = [ // [label, color dark, color light]
    ['other brain', '#7d8590', '#6b7280'], ['subcircuit', '#c9d1d9', '#39424e'], ['T4/T5 left', '#f08a3c', '#d2600e'],
    ['T4/T5 right', '#4f9dff', '#1c6fd6'], ['HS', '#ffd36b', '#a67700'], ['VS', '#7ee0b5', '#16835a'], ['H2', '#e98ce8', '#a63fa4'],
    ['DNa02', '#ff6f61', '#c42c1e'], ['DNg02', '#b48cf2', '#7d44c8'], ['other DN', '#9fb3c8', '#4c6278'],
  ];
  let el, state = null, starting = false;

  const css = `
#brain3d{position:relative;display:flex;flex-direction:column;height:clamp(380px,56vh,640px);background:var(--sky,#121417);overflow:hidden;font:11px/1.35 var(--mono,monospace);color:var(--ink,#e4e6e9);border:1px solid var(--line,#2b2f35);border-radius:2px}
#brain3d .b3-stage{position:relative;flex:1;min-height:0}
#brain3d canvas{display:block;width:100%;height:100%;touch-action:none}
#brain3d .b3-ov{position:absolute;background:color-mix(in srgb,var(--panel,#1a1d21) 86%,transparent);border:1px solid var(--line,#2b2f35);border-radius:3px}
#brain3d .b3-tools{display:flex;flex-wrap:wrap;gap:4px 6px;align-items:center;padding:4px 6px;background:var(--panel,#1a1d21);border-bottom:1px solid var(--line,#2b2f35)}
#brain3d .b3-lab{font:600 11px/1 var(--sans,sans-serif);font-variant-caps:all-small-caps;letter-spacing:.06em;color:var(--muted,#8d939b)}
#brain3d .b3-sep{width:1px;height:16px;background:var(--line2,#3a3f46);margin:0 2px}
#brain3d .b3-seg{display:inline-flex;border:1px solid var(--line2,#3a3f46);border-radius:3px;overflow:hidden;background:var(--panel2,#202328)}
#brain3d .b3-seg button{font:500 10.5px/1 var(--mono,monospace);letter-spacing:.03em;height:22px;min-width:30px;padding:0 6px;border:0;border-radius:0;background:transparent;color:var(--muted,#8d939b);cursor:pointer}
#brain3d .b3-seg button+button{border-left:1px solid var(--line2,#3a3f46)}
#brain3d .b3-seg button:hover{color:var(--ink,#e4e6e9);background:var(--hover,#262a30)}
#brain3d .b3-seg button[aria-pressed=true]{color:var(--ink,#e4e6e9);background:var(--chip,#23272c);box-shadow:inset 0 -2px 0 var(--a,#f08a3c)}
#brain3d .b3-slab{display:flex;align-items:center;gap:6px;flex:1 1 130px;min-width:130px}
#brain3d .b3-slab[hidden]{display:none}
#brain3d .b3-slab input{-webkit-appearance:none;appearance:none;flex:1;min-width:50px;height:22px;margin:0;background:transparent;cursor:pointer;--p:50%}
#brain3d .b3-slab input::-webkit-slider-runnable-track{height:2px;background:linear-gradient(to right,var(--a,#f08a3c) var(--p),var(--line2,#3a3f46) var(--p))}
#brain3d .b3-slab input::-moz-range-track{height:2px;background:linear-gradient(to right,var(--a,#f08a3c) var(--p),var(--line2,#3a3f46) var(--p))}
#brain3d .b3-slab input::-webkit-slider-thumb{-webkit-appearance:none;width:8px;height:14px;margin-top:-6px;border-radius:1px;background:var(--panel2,#202328);border:1px solid var(--muted,#8d939b)}
#brain3d .b3-slab input::-moz-range-thumb{width:6px;height:12px;border-radius:1px;background:var(--panel2,#202328);border:1px solid var(--muted,#8d939b)}
#brain3d .b3-slab input:hover::-webkit-slider-thumb{border-color:var(--ink,#e4e6e9)}
#brain3d .b3-slab input:hover::-moz-range-thumb{border-color:var(--ink,#e4e6e9)}
#brain3d .b3-sl{font:500 10.5px/1 var(--mono,monospace);font-variant-numeric:tabular-nums;color:var(--ink,#e4e6e9);min-width:9ch;text-align:right;white-space:nowrap}
#brain3d .b3-leg{position:relative;padding:3px 3px 3px 6px;border-bottom:1px solid var(--line,#2b2f35);background:var(--panel,#1a1d21);font:10.5px/1.4 var(--sans,sans-serif);color:var(--ink,#e4e6e9)}
#brain3d .b3-key{display:flex;flex-wrap:wrap;align-items:center;gap:0 5px;white-space:nowrap;font-size:10px}
#brain3d .b3-key span{display:inline-flex;align-items:center;gap:3px}
#brain3d .b3-key i{width:6px;height:6px;border-radius:1px;flex:none}
#brain3d .b3-info{margin-left:auto;flex:none;border:0}
#brain3d .b3-info>summary::before{display:none}
#brain3d .b3-info>summary{position:absolute;right:3px;bottom:1px;display:grid;place-items:center;list-style:none;cursor:pointer;color:var(--muted,#8d939b);width:20px;height:20px;padding:0;border:1px solid transparent;border-radius:3px}
#brain3d .b3-info>summary svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.4;stroke-linecap:round}
#brain3d .b3-info>summary::-webkit-details-marker{display:none}
#brain3d .b3-info>summary:hover,#brain3d .b3-info[open]>summary{color:var(--ink,#e4e6e9);background:var(--chip,#23272c);border-color:var(--line2,#3a3f46)}
#brain3d .b3-pop{position:absolute;right:6px;top:calc(100% + 4px);width:min(290px,calc(100% - 12px));padding:6px 9px;white-space:normal;color:var(--ink,#e4e6e9);background:var(--panel,#1a1d21);border:1px solid var(--line2,#3a3f46);border-radius:3px;box-shadow:0 6px 18px rgba(0,0,0,.25);z-index:3}
#brain3d .b3-pop p{margin:0 0 4px}#brain3d .b3-pop p:last-child{margin:0}
#brain3d .b3-pop b{font-weight:600}
#brain3d .b3-st{padding-right:22px;font:10px/1.4 var(--mono,monospace);color:var(--muted,#8d939b);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#brain3d .b3-tip{display:none;padding:3px 6px;pointer-events:none;white-space:nowrap;z-index:2}
#brain3d .b3-lr{position:absolute;color:var(--muted,#8d939b);pointer-events:none;transform:translate(-50%,-50%)}
#brain3d .b3-msg{left:50%;top:50%;transform:translate(-50%,-50%);padding:6px 10px;color:var(--muted,#8d939b)}
@media (pointer:coarse){#brain3d .b3-seg button{height:44px;min-width:44px}#brain3d .b3-slab input{height:44px}#brain3d .b3-info>summary{width:44px;height:44px}#brain3d .b3-st{padding-right:46px;line-height:40px}}`;

  function visible() { return el && el.isConnected && el.clientWidth > 0 && el.clientHeight > 0 && !document.hidden; }
  function maybeStart() {
    el = el && el.isConnected ? el : document.getElementById('brain3d');
    if (!state && !starting && visible()) { starting = true; start().catch((e) => { console.error('brain3d', e); note('3D brain unavailable: ' + (e && e.message || e)); }); }
  }
  function note(msg) { const d = document.createElement('div'); d.className = 'b3-ov b3-msg'; d.textContent = msg; el.appendChild(d); }

  async function loadData() {
    let meta, buf;
    if (window.BRAIN3D) {
      meta = window.BRAIN3D.meta;
      const s = atob(window.BRAIN3D.bin); buf = new Uint8Array(s.length);
      for (let i = 0; i < s.length; i++) buf[i] = s.charCodeAt(i);
      buf = buf.buffer;
    } else {
      [meta, buf] = await Promise.all([fetch('data/brain3d.json').then((r) => r.json()), fetch('data/brain3d.bin').then((r) => r.arrayBuffer())]);
    }
    return { meta, q: new Uint16Array(buf, meta.layout.pos.offset, meta.layout.pos.count), cb: new Uint8Array(buf, meta.layout.cls.offset, meta.layout.cls.count) };
  }

  async function start() {
    const style = document.createElement('style'); style.textContent = css; document.head.appendChild(style);
    const probe = document.createElement('canvas');
    if (!probe.getContext('webgl2')) { note('3D brain needs WebGL 2, which this browser did not provide.'); return; }
    const [THREE, { OrbitControls }, D] = await Promise.all([import('three'), import('three/addons/controls/OrbitControls.js'), loadData()]);
    const { meta, q, cb } = D, n = meta.n;

    // positions in um, centred; three axes: X = FlyWire x (fly's right = +X), Y = -y (dorsal up), Z = -z (anterior = +Z).
    // (x, y, z) -> (x, -y, -z) is a rotation, so nothing is mirrored relative to FlyWire's frontal view.
    const pos = new Float32Array(n * 3), lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) { const v = meta.offset[k] + q[3 * i + k] * meta.scale; pos[3 * i + k] = v; if (v < lo[k]) lo[k] = v; if (v > hi[k]) hi[k] = v; }
    const c = lo.map((v, k) => (v + hi[k]) / 2), sgn = [1, -1, -1];
    for (let i = 0; i < n; i++) for (let k = 0; k < 3; k++) pos[3 * i + k] = sgn[k] * (pos[3 * i + k] - c[k]);
    const half = hi.map((v, k) => (v - lo[k]) / 2); // half extents in um: x, y, z

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('cb', new THREE.BufferAttribute(Float32Array.from(cb), 1));
    const flash = new Float32Array(n), flashAttr = new THREE.BufferAttribute(flash, 1).setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('flash', flashAttr);
    geo.computeBoundingSphere();

    const uniforms = {
      uPx: { value: 1 }, uColors: { value: CLASS.map(() => new THREE.Color()) }, uLight: { value: 0 },
      uT4: { value: new THREE.Vector2(0, 0) }, uBin: { value: 0 },
      uSlice: { value: -1 }, uLo: { value: -1e9 }, uHi: { value: 1e9 },
    };
    const mat = new THREE.ShaderMaterial({
      uniforms, transparent: true, depthWrite: false, depthTest: false,
      vertexShader: `
        attribute float cb; attribute float flash;
        uniform float uPx, uBin, uLo, uHi, uSlice, uLight; uniform vec2 uT4; uniform vec3 uColors[10];
        varying vec3 vCol; varying float vA;
        float hash(float a, float b) { return fract(sin(a * 12.9898 + b * 78.233) * 43758.5453); }
        void main() {
          float cls = mod(cb, 16.0);
          float s = uSlice < 0.0 ? 0.0 : (uSlice < 0.5 ? position.x : (uSlice < 1.5 ? position.y : position.z));
          if (uSlice >= 0.0 && (s < uLo || s > uHi)) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }
          int ci = int(cls + 0.5);
          vec3 col = uColors[0];
          for (int k = 1; k < 10; k++) if (k == ci) col = uColors[k];
          float a = 0.07, sz = 1.3;
          if (ci == 1) { a = 0.22; sz = 1.6; }
          else if (ci == 2 || ci == 3) {
            // population-rate flicker: P(spike in this 10 ms bin) = 1 - exp(-rate * 10 ms), drawn per point and bin
            float p = ci == 2 ? uT4.x : uT4.y;
            bool on = hash(float(gl_VertexID), uBin) < p;
            a = on ? 0.95 : 0.16; sz = on ? 2.6 : 1.6;
          }
          else if (ci >= 4) { a = 0.75; sz = 6.0; }
          a = clamp(a + flash, 0.0, 1.0); sz += 7.0 * flash;
          if (uSlice >= 0.0 && ci <= 1) a = min(1.0, a * 2.5);
          vCol = mix(col, uLight > 0.5 ? col * 0.4 : vec3(1.0), 0.6 * flash); vA = a;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = sz * uPx;
        }`,
      fragmentShader: `
        varying vec3 vCol; varying float vA;
        void main() {
          vec2 d = gl_PointCoord - 0.5; float r = dot(d, d);
          if (r > 0.25) discard;
          gl_FragColor = vec4(vCol, vA * (1.0 - smoothstep(0.12, 0.25, r)));
        }`,
    });
    const scene = new THREE.Scene();
    scene.add(new THREE.Points(geo, mat));

    const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: false, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    const stage = document.createElement('div'); stage.className = 'b3-stage';
    stage.appendChild(renderer.domElement);
    const cam = new THREE.PerspectiveCamera(35, 1, 1, 20000);
    const controls = new OrbitControls(cam, renderer.domElement);
    controls.enableDamping = false; controls.screenSpacePanning = true;

    // ---------- overlay UI
    const tools = document.createElement('div'); tools.className = 'b3-tools'; tools.setAttribute('role', 'toolbar'); tools.setAttribute('aria-label', '3D brain view');
    tools.innerHTML = '<span class="b3-lab" aria-hidden="true">View</span><span class="b3-seg" role="group" aria-label="View">' +
      '<button type="button" data-v="front" aria-label="Coronal view" title="Coronal view: looking along the anterior-posterior axis (L/R markers show the fly\'s sides)">COR</button>' +
      '<button type="button" data-v="top" aria-label="Horizontal view" title="Horizontal view: looking along the dorsal-ventral axis">HOR</button>' +
      '<button type="button" data-v="side" aria-label="Sagittal view" title="Sagittal view: looking along the left-right axis">SAG</button></span>' +
      '<span class="b3-sep" aria-hidden="true"></span><span class="b3-lab" aria-hidden="true">Slice</span><span class="b3-seg" role="group" aria-label="Slice axis">' +
      '<button type="button" data-ax="-1" aria-label="No slice" title="No slice: show the whole brain">Off</button>' +
      '<button type="button" data-ax="2" aria-label="Slice anterior-posterior" title="Show a ' + SLAB_UM + ' µm slab across the anterior-posterior axis">A–P</button>' +
      '<button type="button" data-ax="1" aria-label="Slice dorsal-ventral" title="Show a ' + SLAB_UM + ' µm slab across the dorsal-ventral axis">D–V</button></span>' +
      '<span class="b3-slab" hidden><input type="range" min="0" max="1000" value="500" aria-label="Slab position" disabled><output class="b3-sl"></output></span>';
    const leg = document.createElement('div'); leg.className = 'b3-leg';
    const tip = document.createElement('div'); tip.className = 'b3-ov b3-tip';
    const lr = ['L', 'R'].map((s) => { const d = document.createElement('div'); d.className = 'b3-lr'; d.textContent = s; d.title = s === 'L' ? "fly's left" : "fly's right"; return d; });
    stage.append(tip, ...lr);
    el.append(tools, leg, stage);
    const rng = tools.querySelector('input'), slLab = tools.querySelector('.b3-sl'), slab = tools.querySelector('.b3-slab');
    let sliceAx = -1;
    const fmt = new Intl.NumberFormat('en', { maximumFractionDigits: 0 });
    // one-line colour key + an (i) popover with the provenance, then one muted status line
    const KEY = [[2, 'T4/T5 L'], [3, 'T4/T5 R'], [4, 'HS'], [5, 'VS'], [6, 'H2'], [7, 'DNa02'], [8, 'DNg02']];
    leg.innerHTML = '<div class="b3-key">' + KEY.map(([k, t]) => `<span title="${CLASS[k][0]}"><i data-k="${k}"></i>${t}</span>`).join('') +
      '<details class="b3-info"><summary title="What the points show" aria-label="What the points show"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M8 7.2v4M8 4.9v.1"/></svg></summary><div class="b3-pop">' +
      `<p><b>Points:</b> soma positions of all ${fmt.format(n)} FlyWire v783 neurons.</p>` +
      `<p><b>Highlighted:</b> the ${fmt.format(meta.n_sub)}-neuron subcircuit the model simulates.</p>` +
      '<p><b>Flashes:</b> spikes simulated by the model for recorded cells. T4/T5 brightness shows population rate.</p></div></details></div>' +
      '<div class="b3-st"></div>';
    const status = leg.querySelector('.b3-st'), info = leg.querySelector('.b3-info');
    document.addEventListener('click', (e) => { if (info.open && !info.contains(e.target)) info.open = false; });

    function theme() {
      const light = document.documentElement.dataset.theme === 'light';
      CLASS.forEach((cl, k) => uniforms.uColors.value[k].set(light ? cl[2] : cl[1]));
      uniforms.uLight.value = light ? 1 : 0;
      mat.blending = light ? THREE.NormalBlending : THREE.AdditiveBlending; mat.needsUpdate = true;
      const bg = getComputedStyle(el).getPropertyValue('--sky').trim() || (light ? '#e3e7ec' : '#121417');
      renderer.setClearColor(new THREE.Color(bg));
      leg.querySelectorAll('i[data-k]').forEach((i) => { i.style.background = CLASS[+i.dataset.k][light ? 2 : 1]; });
      dirty = true;
    }
    function view(v) {
      // fit the visible extents (screen-horizontal, screen-vertical half sizes in um) to the stage's aspect
      const [ex, ey] = { front: [half[0], half[1]], top: [half[0], half[2]], side: [half[2], half[1]] }[v];
      const tn = Math.tan((cam.fov / 2) * Math.PI / 180), R = 1.12 * Math.max(ey / tn, ex / (tn * cam.aspect)) + half[0] * 0.3;
      const d = { front: [0, 0, R], top: [0, R, 0.001 * R], side: [R, 0, 0] }[v];
      curView = v;
      cam.position.set(...d); controls.target.set(0, 0, 0); cam.up.set(0, 1, 0); cam.lookAt(0, 0, 0); controls.update();
      tools.querySelectorAll('button[data-v]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.v === v)));
      dirty = true;
    }
    function slice() {
      const ax = sliceAx; rng.disabled = ax < 0; slab.hidden = ax < 0; uniforms.uSlice.value = ax;
      tools.querySelectorAll('button[data-ax]').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.ax === ax)));
      rng.style.setProperty('--p', rng.value / 10 + '%');
      if (ax >= 0) {
        const h = half[ax], cen = -h + (2 * h * rng.value) / 1000;
        uniforms.uLo.value = cen - SLAB_UM / 2; uniforms.uHi.value = cen + SLAB_UM / 2;
        // readout in FlyWire coordinates (um), as neuroglancer shows them; three's Y and Z are FlyWire -y and -z
        const fw = c[ax] - cen, edge = `${fmt.format(cen + h)} µm from the ${ax === 2 ? 'posterior' : 'ventral'} edge`;
        slLab.textContent = `${ax === 2 ? 'z' : 'y'} ${fmt.format(fw)} µm`;
        slab.title = `${SLAB_UM} µm slab centred at FlyWire ${ax === 2 ? 'z' : 'y'} = ${fmt.format(fw)} µm, ${edge}`;
        rng.setAttribute('aria-valuetext', `${slLab.textContent}, ${edge}`);
      } else slLab.textContent = '';
      dirty = true;
    }
    tools.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (b && b.dataset.v) view(b.dataset.v); else if (b && b.dataset.ax) { sliceAx = +b.dataset.ax; slice(); }
    });
    rng.addEventListener('input', slice);
    controls.addEventListener('change', () => { dirty = true; });
    controls.addEventListener('start', () => { curView = ''; tools.querySelectorAll('button[data-v]').forEach((b) => b.setAttribute('aria-pressed', 'false')); });
    new MutationObserver(theme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    // ---------- hover labels for the labelled cells (screen-space nearest, 69 projections per move: cheap)
    const v3 = new THREE.Vector3();
    renderer.domElement.addEventListener('pointermove', (e) => {
      const rc = renderer.domElement.getBoundingClientRect(), mx = e.clientX - rc.left, my = e.clientY - rc.top;
      let best = -1, bd = 100;
      meta.cells.forEach((cc, k) => {
        const i = cc.local, sv = sliceAx;
        if (sv >= 0) { const s = pos[3 * i + sv]; if (s < uniforms.uLo.value || s > uniforms.uHi.value) return; }
        v3.set(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]).project(cam);
        const dx = (v3.x + 1) / 2 * rc.width - mx, dy = (1 - v3.y) / 2 * rc.height - my, d2 = dx * dx + dy * dy;
        if (d2 < bd) { bd = d2; best = k; }
      });
      if (best < 0) { tip.style.display = 'none'; return; }
      const cc = meta.cells[best];
      tip.textContent = `${cc.type} · ${cc.side} · root ${cc.root_id}`;
      tip.style.display = 'block'; tip.style.left = Math.min(mx + 10, rc.width - tip.offsetWidth - 4) + 'px'; tip.style.top = Math.max(2, my - 24) + 'px';
    });
    renderer.domElement.addEventListener('pointerleave', () => { tip.style.display = 'none'; });

    // ---------- live firing
    const prepCache = new WeakMap();
    function prep(rec) {
      if (prepCache.has(rec)) return prepCache.get(rec);
      const r = rec.raster || {}, neurons = r.neurons || [];
      const pt = new Map(neurons.map((nn) => [nn.idx, Number.isInteger(nn.local) ? nn.local : meta.raster_default[nn.idx]]));
      const per = new Map();
      for (const [ts, id] of r.spikes || []) { const p = pt.get(id); if (p == null) continue; if (!per.has(p)) per.set(p, []); per.get(p).push(ts); }
      const cells = [...per].map(([p, a]) => ({ p, t: Float64Array.from(a).sort() }));
      const t = rec.t || [], dt = (rec.meta && rec.meta.dt) || (t.length > 1 ? t[1] - t[0] : 0.01);
      const g = rec.groups || {};
      const out = { cells, pts: [...pt.values()], t0: t.length ? t[0] : 0, dt, L: g.T4T5_L, R: g.T4T5_R, brain: !!(neurons.length || g.T4T5_L) };
      prepCache.set(rec, out); return out;
    }
    function lastLE(a, x) { let l = 0, h = a.length; while (l < h) { const m = (l + h) >> 1; if (a[m] <= x) l = m + 1; else h = m; } return l - 1; }
    let lastPts = [], fmax = 0;
    function frame(d) {
      if (!d) return;
      const rec = d.run && (d.run.rec || d.run), t = +d.t;
      for (const p of lastPts) flash[p] = 0;
      lastPts = [];
      let nFlash = 0, rl = 0, rr = 0;
      const P = rec && prep(rec);
      if (P && P.brain && Number.isFinite(t)) {
        for (const c of P.cells) {
          const k = lastLE(c.t, t);
          if (k >= 0 && t - c.t[k] < FLASH_WIN) { flash[c.p] = Math.exp(-(t - c.t[k]) / FLASH_TAU); lastPts.push(c.p); nFlash++; }
        }
        const j = Math.max(0, Math.round((t - P.t0) / P.dt));
        if (P.L && P.L.length) rl = P.L[Math.min(j, P.L.length - 1)] || 0;
        if (P.R && P.R.length) rr = P.R[Math.min(j, P.R.length - 1)] || 0;
      }
      uniforms.uT4.value.set(1 - Math.exp(-rl * T4_BIN), 1 - Math.exp(-rr * T4_BIN));
      uniforms.uBin.value = Math.floor(t / T4_BIN) % 4096;
      const hiP = Math.max(fmax, ...lastPts, 0);
      flashAttr.clearUpdateRanges(); flashAttr.addUpdateRange(0, hiP + 1); flashAttr.needsUpdate = true;
      fmax = Math.max(...lastPts, 0);
      const who = d.which ? `${d.which} · ` : '';
      status.textContent = !P ? '' : !P.brain ? `${who}t ${t.toFixed(2)} s · no fly brain in this flight` :
        `${who}t ${t.toFixed(2)} s · ${nFlash} flashing · T4/T5 L ${rl.toFixed(0)} R ${rr.toFixed(0)} Hz`;
      status.title = P && P.brain ? `${nFlash} recorded cells flashing now; T4/T5 population rate left ${rl.toFixed(0)} Hz, right ${rr.toFixed(0)} Hz` : '';
      dirty = true;
    }

    // ---------- loop: rAF only while visible; render only when something changed
    let dirty = true, raf = 0, lastW = 0, lastH = 0, curView = '';
    function tick() {
      raf = 0;
      if (!visible()) return;
      const w = stage.clientWidth, h = stage.clientHeight;
      if (w !== lastW || h !== lastH) {
        lastW = w; lastH = h; renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
        uniforms.uPx.value = renderer.getPixelRatio(); if (curView) view(curView); dirty = true;
      }
      if (dirty) {
        renderer.render(scene, cam); dirty = false;
        const hw = w / 2, hh = h / 2;
        [[-half[0] * 1.04, 0], [half[0] * 1.04, 0]].forEach(([x, y], k) => {
          v3.set(x, y, 0).project(cam); lr[k].style.left = (v3.x + 1) * hw + 'px'; lr[k].style.top = (1 - v3.y) * hh + 'px';
          lr[k].style.display = v3.z < 1 ? 'block' : 'none';
        });
        const gap = Math.abs(parseFloat(lr[0].style.left) - parseFloat(lr[1].style.left));
        if (gap < 40) lr.forEach((d) => { d.style.display = 'none'; }); // side view: both project to the middle
      }
      raf = requestAnimationFrame(tick);
    }
    function wake() { dirty = true; if (!raf && visible()) raf = requestAnimationFrame(tick); }
    state = { frame, wake };
    theme(); view('front'); slice();
    window.addEventListener('resize', wake);
    document.addEventListener('visibilitychange', wake);
    new ResizeObserver(wake).observe(el);
    if (pendingFrame) frame(pendingFrame);
    wake();
  }

  let pendingFrame = null;
  window.addEventListener('wb:frame', (e) => {
    pendingFrame = e.detail;
    if (state) { state.frame(e.detail); state.wake(); } else maybeStart();
  });
  window.addEventListener('wb:tab', (e) => {
    if (e.detail && e.detail.tab === '3d') setTimeout(() => (state ? state.wake() : maybeStart()), 0);
  });
  document.addEventListener('toggle', () => (state ? state.wake() : maybeStart()), true); // e.g. a <details> opened
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', maybeStart); else maybeStart();
})();
