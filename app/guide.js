// Flight-test the fly: the research paper shown under the replay app. Plain classic script (no modules), works
// from file://. Every result number comes from data/summary.json (or window.APP_SUMMARY from data/summary.js),
// which scripts/export_app_summary.py generates from the result files with a _source entry per number.
// No result is typed in this file; only prose, labels and axis ranges are.
'use strict';
(function () {
const KATEX = 'https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/';

async function loadSummary() {
  if (location.protocol !== 'file:') {
    try {
      const r = await fetch('data/summary.json', { cache: 'no-cache' });
      if (r.ok) return await r.json();
    } catch (e) { /* fall back to the bundle */ }
  }
  if (window.APP_SUMMARY) return window.APP_SUMMARY;
  throw new Error('No summary data found (data/summary.json or data/summary.js).');
}

// ---------------------------------------------------------------- formatting
const MINUS = '\u2212';
const isInf = (x) => x === 'inf' || x === Infinity;
const n = (x, d) => {
  if (isInf(x)) return '∞';
  const v = Number(x), s = Math.abs(v).toFixed(d);
  return (v < 0 && Number(s) !== 0 ? MINUS : '') + s;
};
const sgn = (x, d) => (Number(x) > 0 && Number(Math.abs(x).toFixed(d)) !== 0 ? '+' : '') + n(x, d);
const ciP = (o, d) => `${n(o.mean, d)} [${n(o.lo, d)}, ${n(o.hi, d)}]`;   // plain text (tooltips)
const ci = (o, d) => `${n(o.mean, d)} <span class="ci">[${n(o.lo, d)}, ${n(o.hi, d)}]</span>`;
// signed, with one more decimal wherever a value would otherwise print as 0.00 (|x| < 0.005)
const nz = (x, d) => (Number(x) !== 0 && Math.abs(x) < 0.5 * 10 ** -d ? d + 1 : d);
const cis = (o, d) => { const e = Math.max(nz(o.mean, d), nz(o.lo, d), nz(o.hi, d)); return `${sgn(o.mean, e)} <span class="ci">[${n(o.lo, e)}, ${n(o.hi, e)}]</span>`; };
const cit = (o, d) => `${n(o.mean, d)} [95% CI ${n(o.lo, d)}, ${n(o.hi, d)}]`;
const int = (x) => (x < 0 ? MINUS : '') + Math.abs(Math.round(x)).toLocaleString('en-US');
const pct = (x) => Math.round(x) + '%';
const rng = (a, d) => `${n(a[0], d)}–${n(a[1], d)}`;
const times = (x) => { const h = Math.round(2 * x) / 2, k = Math.floor(h); return (words[k] ?? String(k)) + (h > k ? ' and a half' : ''); };
const words = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve'];
const w = (k) => words[k] ?? String(k);
const Cap = (s) => s[0].toUpperCase() + s.slice(1);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const sid = (name) => name.replace('fly_shuffle_', '').replace('shuf', '');   // fly_shuffle_08 / shuf08 -> 08
const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const nearest = (arr, v) => arr.reduce((b, x, i) => (Math.abs(x - v) < Math.abs(arr[b] - v) ? i : b), 0);

// ---------------------------------------------------------------- references (numbered in order of first citation)
const REFS = {
  dorkenwald: 'Dorkenwald S, et al. (2024). Neuronal wiring diagram of an adult brain. <i>Nature</i> 634:124–138. <a href="https://doi.org/10.1038/s41586-024-07558-y">doi:10.1038/s41586-024-07558-y</a>',
  schlegel: 'Schlegel P, et al. (2024). Whole-brain annotation and multi-connectome cell typing of <i>Drosophila</i>. <i>Nature</i> 634:139–152. <a href="https://doi.org/10.1038/s41586-024-07686-5">doi:10.1038/s41586-024-07686-5</a>',
  shiu: 'Shiu PK, et al. (2024). A <i>Drosophila</i> computational brain model reveals sensorimotor processing. <i>Nature</i> 634:210–219. <a href="https://doi.org/10.1038/s41586-024-07763-9">doi:10.1038/s41586-024-07763-9</a>',
  maisak: 'Maisak MS, et al. (2013). A directional tuning map of <i>Drosophila</i> elementary motion detectors. <i>Nature</i> 500:212–216. <a href="https://doi.org/10.1038/nature12320">doi:10.1038/nature12320</a>',
  shinomiya: 'Shinomiya K, et al. (2019). Comparisons between the ON- and OFF-edge motion pathways in the <i>Drosophila</i> brain. <i>eLife</i> 8:e40025. <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC6338461/">PMC6338461</a>',
  schnell: 'Schnell B, et al. (2010). Processing of horizontal optic flow in three visual interneurons of the <i>Drosophila</i> brain. <i>J Neurophysiol</i> 103:1646–1657. <a href="https://doi.org/10.1152/jn.00950.2009">doi:10.1152/jn.00950.2009</a>',
  haikala: 'Haikala V, et al. (2013). Optogenetic control of fly optomotor responses. <i>J Neurosci</i> 33:13927–13934. <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC6618655/">PMC6618655</a>',
  rayshubskiy: 'Rayshubskiy A, et al. (2025). Neural circuit mechanisms for steering control in walking <i>Drosophila</i>. <i>eLife</i> 13:RP102230. <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC12279373/">PMC12279373</a>',
  namiki: 'Namiki S, et al. (2018). The functional organization of descending sensory-motor pathways in <i>Drosophila</i>. <i>eLife</i> 7:e34272. <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC6019073/">PMC6019073</a>',
  theobald: 'Theobald JC, Ringach DL, Frye MA (2010). Dynamics of optomotor responses in <i>Drosophila</i> to perturbations in optic flow. <i>J Exp Biol</i> 213:1366–1375. <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC2846167/">PMC2846167</a>',
  leibbrandt: 'Leibbrandt R, Nicholas S, Nordström K (2021). The impulse response of optic flow-sensitive descending neurons to roll m-sequences. <i>J Exp Biol</i> 224:jeb242833. <a href="https://doi.org/10.1242/jeb.242833">doi:10.1242/jeb.242833</a>',
  clutch: 'ClutchMedia775 (2026). fly-brain-drone. Software repository. <a href="https://github.com/ClutchMedia775/fly-brain-drone">github.com/ClutchMedia775/fly-brain-drone</a>',
  leohio: 'leohio (2026). connectome-fly-closedloop. Software repository. <a href="https://github.com/leohio/connectome-fly-closedloop">github.com/leohio/connectome-fly-closedloop</a>',
  flygm: 'Jin Z, Zhu Y, Zhang C, Sui Y (2026). Whole-brain connectomic graph model enables whole-body locomotion control in fruit fly (FlyGM). arXiv:2602.17997. <a href="https://arxiv.org/abs/2602.17997">arxiv.org/abs/2602.17997</a>',
  milf8785: 'US Department of Defense (5 November 1980). MIL-F-8785C, <i>Military Specification: Flying Qualities of Piloted Airplanes</i>. <a href="https://everyspec.com/MIL-SPECS/MIL-SPECS-MIL-F/MIL-F-8785C_5295/">everyspec.com/MIL-SPECS/MIL-SPECS-MIL-F/MIL-F-8785C_5295</a>',
  milf9490: 'US Air Force (6 June 1975). MIL-F-9490D, <i>Flight Control Systems – Design, Installation and Test of Piloted Aircraft, General Specification for</i>, §3.1.3.6.1, Table III.',
  boeing9490: 'Boeing Co. (1975). <i>Background Information and User’s Guide for MIL-F-9490D</i>. AFFDL-TR-74-116 (NTIS AD-A029 074); reproduces the stability-margin table of the specification. <a href="https://www.acgsc.org/history/Early%20Fly-By-Wire%20Redundancy%20Studies/User%27s%20Guide%20to%20MIL-F-9490D.pdf">acgsc.org</a>',
  mansur: 'Mansur MH, Lusardi JA, Tischler MB, Berger T (2009). Achieving the best compromise between stability margins and disturbance rejection performance. <i>American Helicopter Society 65th Annual Forum</i>, Grapevine, TX. <a href="https://www.sjsu.edu/researchfoundation/docs/AHS_2009_Mansur.pdf">sjsu.edu/researchfoundation/docs/AHS_2009_Mansur.pdf</a>',
  jsbsim: 'Berndt JS, Coconnier B, De Marco A, McLeod S (2026). JSBSim – An Open Source Flight Dynamics Software Library, v1.3.1. Software. <a href="https://doi.org/10.5281/zenodo.20258622">doi:10.5281/zenodo.20258622</a>; <a href="https://github.com/JSBSim-Team/jsbsim">github.com/JSBSim-Team/jsbsim</a>',
  yeager: 'Yeager JC (1998). Implementation and testing of turbulence models for the F18-HARV simulation. NASA/CR-1998-206937. <a href="https://ntrs.nasa.gov/citations/19980028448">ntrs.nasa.gov/citations/19980028448</a>',
  phipson: 'Phipson B, Smyth GK (2010). Permutation P-values should never be zero: calculating exact P-values when permutations are randomly drawn. <i>Stat Appl Genet Mol Biol</i> 9(1):Article 39. <a href="https://doi.org/10.2202/1544-6115.1585">doi:10.2202/1544-6115.1585</a>',
};
let refOrder = [];
function cite(...keys) {
  const nums = keys.map((k) => {
    if (!REFS[k]) throw new Error('unknown reference ' + k);
    let i = refOrder.indexOf(k);
    if (i < 0) { refOrder.push(k); i = refOrder.length - 1; }
    return i + 1;
  }).sort((a, b) => a - b);
  return `<span class="cite">[${nums.map((x) => `<a href="#ref-${x}">${x}</a>`).join(', ')}]</span>`;
}

// ---------------------------------------------------------------- equations (KaTeX, with an HTML fallback)
let eqs = [];
function eq(tex, html) {
  eqs.push(tex);
  const k = eqs.length;
  return `<div class="eq" id="eq-${k}"><div class="eq-body" data-tex="${k - 1}"><span class="eq-fallback">${html}</span></div><span class="eq-no">(${k})</span></div>`;
}
const frac = (a, b) => `<span class="frac"><span>${a}</span><span>${b}</span></span>`;

function katex(root) {
  const apply = () => root.querySelectorAll('.eq-body[data-tex]').forEach((el) => {
    try { window.katex.render(eqs[+el.dataset.tex], el, { displayMode: true, throwOnError: true }); } catch (e) { /* keep the fallback */ }
  });
  if (window.katex) return apply();
  const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = KATEX + 'katex.min.css';
  const js = document.createElement('script'); js.src = KATEX + 'katex.min.js'; js.onload = apply;
  document.head.append(css, js);   // offline: the HTML fallback stays
}

// ---------------------------------------------------------------- SVG plotting (drawn at the container's width)
const lin = (d0, d1, r0, r1) => (v) => r0 + (v - d0) / (d1 - d0) * (r1 - r0);
const lg = (d0, d1, r0, r1) => { const a = Math.log10(d0), b = Math.log10(d1); return (v) => r0 + (Math.log10(v) - a) / (b - a) * (r1 - r0); };
const f1 = (v) => Math.round(v * 10) / 10;

// One axes box: frame, light grid, outward ticks, tick labels, axis labels. Returns scales and the SVG string.
function axes(o) {
  const X = (o.x.log ? lg : lin)(o.x.min, o.x.max, o.x0, o.x1);
  const Y = (o.y.log ? lg : lin)(o.y.min, o.y.max, o.y1, o.y0);
  let s = '';
  for (const t of o.x.ticks) s += `<line class="grid" x1="${f1(X(t))}" x2="${f1(X(t))}" y1="${o.y0}" y2="${o.y1}"/>`;
  for (const t of o.y.ticks) s += `<line class="grid" x1="${o.x0}" x2="${o.x1}" y1="${f1(Y(t))}" y2="${f1(Y(t))}"/>`;
  s += `<rect class="frame" x="${o.x0}" y="${o.y0}" width="${o.x1 - o.x0}" height="${o.y1 - o.y0}"/>`;
  let lastX = -1e9;
  for (const t of o.x.ticks) {
    s += `<line class="tk" x1="${f1(X(t))}" x2="${f1(X(t))}" y1="${o.y1}" y2="${o.y1 + 4}"/>`;
    const lab = (o.x.fmt || String)(t), wl = 6.5 * String(lab).replace(/<[^>]+>/g, '').length;
    if (!o.x.hide && X(t) - lastX > wl + 4) { s += `<text class="tick" x="${f1(X(t))}" y="${o.y1 + 16}" text-anchor="middle">${lab}</text>`; lastX = X(t); }
  }
  for (const t of o.y.ticks) {
    s += `<line class="tk" x1="${o.x0 - 4}" x2="${o.x0}" y1="${f1(Y(t))}" y2="${f1(Y(t))}"/>`;
    s += `<text class="tick" x="${o.x0 - 7}" y="${f1(Y(t)) + 4}" text-anchor="end">${(o.y.fmt || String)(t)}</text>`;
  }
  if (o.x.label && !o.x.hide) s += `<text class="axlab" x="${(o.x0 + o.x1) / 2}" y="${o.y1 + 34}" text-anchor="middle">${o.x.label}</text>`;
  if (o.y.label) {
    const cy = (o.y0 + o.y1) / 2;
    s += `<text class="axlab" transform="translate(${o.yl ?? 14},${cy}) rotate(-90)" text-anchor="middle">${o.y.label}</text>`;
  }
  if (o.tag) s += `<text class="panel-tag" x="${o.x1 - 6}" y="${o.y0 + 15}" text-anchor="end">${o.tag}</text>`;
  return { X, Y, s, clip: `<clipPath id="${o.id}"><rect x="${o.x0}" y="${o.y0}" width="${o.x1 - o.x0}" height="${o.y1 - o.y0}"/></clipPath>` };
}
const path = (xs, ys, X, Y) => xs.map((x, i) => `${i ? 'L' : 'M'}${f1(X(x))},${f1(Y(ys[i]))}`).join('');
const band = (xs, lo, hi, X, Y) => path(xs, hi, X, Y) + xs.slice().reverse().map((x, i) => `L${f1(X(x))},${f1(Y(lo[lo.length - 1 - i]))}`).join('') + 'Z';
const pt = (x, y, cls, tip, r = 3.2, shape = 'c') => {
  const m = shape === 's' ? `<rect class="${cls} mk" x="${f1(x - r)}" y="${f1(y - r)}" width="${f1(2 * r)}" height="${f1(2 * r)}"/>`
    : shape === 't' ? `<path class="${cls} mk" d="M${f1(x)},${f1(y - r * 1.25)}L${f1(x + r * 1.15)},${f1(y + r * .85)}L${f1(x - r * 1.15)},${f1(y + r * .85)}Z"/>`
      : `<circle class="${cls} mk" cx="${f1(x)}" cy="${f1(y)}" r="${r}"/>`;
  return `<g class="pt"><title>${esc(tip)}</title><circle class="hit" cx="${f1(x)}" cy="${f1(y)}" r="${r + 5}"/>${m}</g>`;
};

// Legend box below the axes: items {kind: line|band|dot|sq|tri|open, cls, dash, label}; wraps to the width.
function legend(items, x0, x1, y0) {
  const cw = 6.3, sw = 26, pad = 8, gap = 18, rowH = 18;
  const iw = items.map((it) => sw + 6 + it.label.length * cw);
  const rows = [[]]; let cur = 0;
  items.forEach((it, i) => {
    if (cur > 0 && cur + iw[i] > x1 - x0 - 2 * pad) { rows.push([]); cur = 0; }
    rows[rows.length - 1].push([it, cur]); cur += iw[i] + gap;
  });
  const h = rows.length * rowH + 2 * pad - 4;
  let s = `<rect class="lgbox" x="${x0}" y="${y0}" width="${x1 - x0}" height="${h}"/>`;
  rows.forEach((row, r) => row.forEach(([it, dx]) => {
    const x = x0 + pad + dx, y = y0 + pad + r * rowH + 6;
    if (it.kind === 'band') s += `<rect class="${it.cls}" x="${x}" y="${y - 5}" width="${sw}" height="10"/>`;
    if (it.kind === 'line' || it.kind === 'bandline') {
      if (it.kind === 'bandline') s += `<rect class="${it.band}" x="${x}" y="${y - 5}" width="${sw}" height="10"/>`;
      s += `<line class="ln ${it.cls} ${it.dash || ''}" x1="${x}" x2="${x + sw}" y1="${y}" y2="${y}"/>`;
      if (it.mark) s += `<circle class="${it.mark} mk" cx="${x + sw / 2}" cy="${y}" r="3"/>`;
    }
    if (it.kind === 'dot') s += `<circle class="${it.cls} mk" cx="${x + sw / 2}" cy="${y}" r="3.4"/>`;
    if (it.kind === 'open') s += `<circle class="${it.cls} open" cx="${x + sw / 2}" cy="${y}" r="3.4"/>`;
    if (it.kind === 'sq') s += `<rect class="${it.cls} mk" x="${x + sw / 2 - 3.2}" y="${y - 3.2}" width="6.4" height="6.4"/>`;
    if (it.kind === 'tri') s += `<path class="${it.cls} mk" d="M${x + sw / 2},${y - 4}L${x + sw / 2 + 3.7},${y + 2.7}L${x + sw / 2 - 3.7},${y + 2.7}Z"/>`;
    s += `<text x="${x + sw + 6}" y="${y + 4}">${it.label}</text>`;
  }));
  return { s, h };
}
const svg = (W, H, label, body) => `<svg viewBox="0 0 ${W} ${f1(H)}" width="${W}" height="${f1(H)}" role="img" aria-label="${esc(label)}">${body}</svg>`;

const mounts = [];
function figure(id, draw, caption, table) {
  mounts.push([id, draw]);
  return `<figure id="${id}"><div class="plot"></div><figcaption>${caption}</figcaption>` +
    (table ? `<details class="data"><summary>Show data table</summary><div class="tbl">${table}</div></details>` : '') + '</figure>';
}
function mountAll(root) {
  for (const [id, draw] of mounts) {
    const host = root.querySelector(`#${id} .plot`);
    let last = 0;
    const go = () => { const W = Math.round(host.clientWidth); if (W && W !== last) { last = W; host.innerHTML = draw(W); } };
    go();
    // re-render in the next frame (not inside the observer callback, which would resize the observed element);
    // go() itself skips widths it has already drawn
    if (window.ResizeObserver) { let q = 0; new ResizeObserver(() => { if (!q) q = requestAnimationFrame(() => { q = 0; go(); }); }).observe(host); }
    else addEventListener('resize', go);
  }
}
const tbl = (head, rows, opts = {}) => `<table>${opts.grp || ''}<thead><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>` +
  rows.map((r) => (r.group ? `<tr class="grouprow${r.mid ? ' midrule' : ''}"><td colspan="${head.length}"><i>${r.group}</i></td></tr>`
    : `<tr${r.mid ? ' class="midrule"' : ''}>${(r.cells || r).map((c) => `<td>${c}</td>`).join('')}</tr>`)).join('') + '</tbody></table>';

// ---------------------------------------------------------------- figures
const sup = { '-3': '10<tspan dy="-5" font-size="9">−3</tspan>', '-2': '10<tspan dy="-5" font-size="9">−2</tspan>', '-1': '10<tspan dy="-5" font-size="9">−1</tspan>', '0': '1' };
const fHz = (t) => (t < 1 ? String(t).replace(/^0/, '0') : String(t));

function figBode(S) {
  const B = S.bode, f = B.freqs, R = B.real, Ns = B.shuffle, Nt = B.type_shuffle;
  return (W) => {
    const ml = 60, mr = 10, ph = Math.max(120, Math.min(175, W * 0.27)), gap = 12, top = 6;
    const xs = { min: 0.08, max: 25, log: true, ticks: [0.1, 0.2, 0.5, 1, 2, 5, 10, 20], fmt: fHz, label: 'Drive frequency (Hz)' };
    const P = [];
    const y0 = (i) => top + i * (ph + gap);
    const A = axes({ id: 'cb1', x0: ml, x1: W - mr, y0: y0(0), y1: y0(0) + ph, tag: '(a)', x: { ...xs, hide: true },
      y: { min: 1e-3, max: 3, log: true, ticks: [1e-3, 1e-2, 1e-1, 1], fmt: (t) => sup[String(Math.round(Math.log10(t)))], label: 'Gain (Hz per °/s)' } });
    const Bx = axes({ id: 'cb2', x0: ml, x1: W - mr, y0: y0(1), y1: y0(1) + ph, tag: '(b)', x: { ...xs, hide: true },
      y: { min: 0, max: 200, ticks: [0, 45, 90, 135, 180], fmt: (t) => t + '°', label: 'Phase of Δ relative to <tspan font-style="italic">r</tspan>' } });
    const C = axes({ id: 'cb3', x0: ml, x1: W - mr, y0: y0(2), y1: y0(2) + ph, tag: '(c)', x: xs,
      y: { min: 0, max: 1.08, ticks: [0, 0.25, 0.5, 0.75, 1], fmt: (t) => (t === 0 || t === 1 ? String(t) : t.toFixed(2)), label: 'Coherence' } });
    let s = `<defs>${A.clip}${Bx.clip}${C.clip}</defs>` + A.s + Bx.s + C.s;
    const series = (ax, id, key, real, tipf) => {
      let g = `<g clip-path="url(#${id})">`;
      if (key) {
        g += `<path class="b-null" d="${band(f, Ns[key + '_min'], Ns[key + '_max'], ax.X, ax.Y)}"/>`;
        g += `<path class="b-type" d="${band(f, Nt[key + '_min'], Nt[key + '_max'], ax.X, ax.Y)}"/>`;
        g += `<path class="ln thin s-null" d="${path(f, Ns[key + '_median'], ax.X, ax.Y)}"/>`;
        g += `<path class="ln thin s-type" d="${path(f, Nt[key + '_median'], ax.X, ax.Y)}"/>`;
      }
      return g;
    };
    // ±1 SD over seeds for the real wiring (clipped to the axes)
    const sd = (ax, ys, sds, lo, hi) => f.map((x, i) => `<line class="sd s-real" x1="${f1(ax.X(x))}" x2="${f1(ax.X(x))}" y1="${f1(ax.Y(Math.max(lo, ys[i] - sds[i])))}" y2="${f1(ax.Y(Math.min(hi, ys[i] + sds[i])))}"/>`).join('');
    // (a) gain
    s += series(A, 'cb1', 'gain');
    s += sd(A, R.gain, R.gain_std, 1e-3, 3);
    s += `<path class="ln s-real" d="${path(f, R.gain, A.X, A.Y)}"/></g>`;
    f.forEach((x, i) => { s += pt(A.X(x), A.Y(R.gain[i]), 'f-real', `${n(x, 3)} Hz: gain ${n(R.gain[i], 3)} Hz per °/s (real); degree-null median ${n(Ns.gain_median[i], 3)}; type-null median ${n(Nt.gain_median[i], 3)}`); });
    // (b) phase
    s += `<g clip-path="url(#cb2)"><line class="ref" x1="${ml}" x2="${W - mr}" y1="${f1(Bx.Y(180))}" y2="${f1(Bx.Y(180))}"/>`;
    s += `<path class="ln thin s-type" d="${path(f, Nt.phase_median, Bx.X, Bx.Y)}"/>`;
    s += sd(Bx, R.phase_deg, R.phase_circstd_deg, 0, 200);
    s += `<path class="ln s-real" d="${path(f, R.phase_deg, Bx.X, Bx.Y)}"/></g>`;
    f.forEach((x, i) => { s += pt(Bx.X(x), Bx.Y(R.phase_deg[i]), 'f-real', `${n(x, 3)} Hz: phase ${n(R.phase_deg[i], 1)}° (real); type-null median ${n(Nt.phase_median[i], 1)}°`); });
    // (c) coherence
    s += series(C, 'cb3', 'coh');
    s += `<path class="ln thin s-ink dash" d="${path(f, B.noise_q95, C.X, C.Y)}"/>`;
    s += `<line class="ref" x1="${ml}" x2="${W - mr}" y1="${f1(C.Y(B.H1_coh_threshold))}" y2="${f1(C.Y(B.H1_coh_threshold))}"/>`;
    s += sd(C, R.coh, R.coh_std, 0, 1.08);
    s += `<path class="ln s-real" d="${path(f, R.coh, C.X, C.Y)}"/></g>`;
    f.forEach((x, i) => { s += pt(C.X(x), C.Y(R.coh[i]), 'f-real', `${n(x, 3)} Hz: coherence ${n(R.coh[i], 3)} (real); degree-null median ${n(Ns.coh_median[i], 2)}; type-null median ${n(Nt.coh_median[i], 3)}; noise 95th pct ${n(B.noise_q95[i], 2)}`); });
    const lgY = y0(2) + ph + 46;
    const L = legend([
      { kind: 'line', cls: 's-real', mark: 'f-real', label: `Real wiring (bars: ±1 SD over ${B.n_seeds} seeds)` },
      { kind: 'bandline', band: 'b-type', cls: 's-type thin', label: `Cell-type-preserving null (n = ${B.n_type_shuffles})` },
      { kind: 'bandline', band: 'b-null', cls: 's-null thin', label: `Degree-preserving null (n = ${B.n_shuffles})` },
      { kind: 'line', cls: 's-ink thin', dash: 'dash', label: 'Noise-only coherence, 95th pct.' },
    ], ml, W - mr, lgY);
    return svg(W, lgY + L.h + 2, 'Bode plot of the real wiring against both null models', s + L.s);
  };
}

const PANELS = [['lin2', '(a) Two-state yaw–sideslip model'], ['jsbsim', '(b) JSBSim six-degree-of-freedom Cessna 172']];
function figPaired(S) {
  const pls = PANELS;
  const rows = [['fly_minus_bare', 'Real − bare', 'f-real'], ['real_minus_median_shuffle', 'Real − median scrambled', 'f-real'], ['damper_minus_bare', 'Yaw damper − bare', 'f-damper']];
  const all = pls.flatMap(([p]) => rows.flatMap(([k]) => S.closed_loop[p].per_seed[k]));
  const xmin = Math.floor((Math.min(...all) - 0.1) * 2) / 2, xmax = 0.25;
  return (W) => {
    const ml = W < 520 ? 118 : 158, mr = 10, rh = 38, ph = rows.length * rh + 8, head = 22;
    let s = '', y = 0, defs = '';
    const ticks = []; for (let t = Math.ceil(xmin * 2) / 2; t <= xmax + 1e-9; t += 0.5) ticks.push(+t.toFixed(1));
    pls.forEach(([p, title], pi) => {
      const y0 = y + head, last = pi === pls.length - 1;
      const A = axes({ id: `cp${pi}`, x0: ml, x1: W - mr, y0, y1: y0 + ph, x: { min: xmin, max: xmax, ticks, fmt: (t) => n(t, 1), label: 'Paired difference in RMS yaw rate (°/s)', hide: !last }, y: { min: 0, max: 1, ticks: [] } });
      defs += A.clip;
      s += `<text class="panel-tag" x="0" y="${y + 14}">${title}</text>` + A.s;
      s += `<line class="zero" x1="${f1(A.X(0))}" x2="${f1(A.X(0))}" y1="${y0}" y2="${y0 + ph}"/>`;
      rows.forEach(([k, lab, cls], ri) => {
        const yc = y0 + 4 + rh * ri + rh / 2, v = S.closed_loop[p].per_seed[k], c = S.closed_loop[p].paired[k];
        s += `<text x="${ml - 8}" y="${yc + 4}" text-anchor="end">${W < 520 ? lab.replace('median scrambled', 'median scr.').replace('Yaw damper', 'Damper') : lab}</text>`;
        v.forEach((x, i) => { s += pt(A.X(x), yc - 13 + ((i * 7) % 5) * 3, cls, `test seed ${S.tuning.test_seeds[i]}: ${sgn(x, 2)} °/s`, 2.4); });
        const yb = yc + 9;
        s += `<line class="ci" x1="${f1(A.X(c.lo))}" x2="${f1(A.X(c.hi))}" y1="${yb}" y2="${yb}"/>`;
        for (const e of [c.lo, c.hi]) s += `<line class="ci" x1="${f1(A.X(e))}" x2="${f1(A.X(e))}" y1="${yb - 4}" y2="${yb + 4}"/>`;
        s += pt(A.X(c.mean), yb, 'f-ink', `${lab}: mean ${ciP(c, 2)} °/s (95% percentile bootstrap CI)`, 2.4, 's');
      });
      y = y0 + ph + (last ? 42 : 12);
    });
    return svg(W, y, 'Per-seed paired differences in RMS yaw rate', `<defs>${defs}</defs>` + s);
  };
}

function figScatter(S) {
  const pls = PANELS;
  return (W) => {
    const ml = 56, mr = 12, head = 20, ph = Math.max(150, Math.min(230, W * 0.36));
    let s = '', y = 0, defs = '';
    pls.forEach(([p, title], pi) => {
      const c = S.closed_loop[p], C = c.controllers, relay = c.relay_shuffles;
      const pts = [['bare', C.bare.rms_r_deg_s, C.bare.rms_beta_deg], ['yaw_damper', C.yaw_damper.rms_r_deg_s, C.yaw_damper.rms_beta_deg],
        ...Object.keys(c.rms_beta_deg_by_wiring).map((k) => [k, k === 'fly_real' ? C.fly_real.rms_r_deg_s : c.shuffle_rms_r_deg_s[k], c.rms_beta_deg_by_wiring[k]])];
      const xs = pts.map((q) => q[1]), ys = pts.map((q) => q[2]);
      const xlo = Math.floor(Math.min(...xs) * 2) / 2 - 0.25, xhi = Math.ceil(Math.max(...xs) * 2) / 2 + 0.25;
      const yspan = Math.max(...ys) - Math.min(...ys), ystep = yspan > 2 ? 1 : 0.05;
      const ylo = Math.floor(Math.min(...ys) / ystep) * ystep - ystep, yhi = Math.ceil(Math.max(...ys) / ystep) * ystep + ystep;
      const yt = []; for (let t = ylo; t <= yhi + 1e-9; t += ystep) yt.push(+t.toFixed(2));
      const xt = []; for (let t = Math.ceil(xlo * 2) / 2; t <= xhi + 1e-9; t += 0.5) xt.push(+t.toFixed(1));
      const y0 = y + head;
      const A = axes({ id: `cs${pi}`, x0: ml, x1: W - mr, y0, y1: y0 + ph, x: { min: xlo, max: xhi, ticks: xt, fmt: (t) => n(t, 1), label: 'Mean RMS yaw rate (°/s)' },
        y: { min: ylo, max: yhi, ticks: yt, fmt: (t) => n(t, ystep < 1 ? 2 : 0), label: 'Mean RMS sideslip (°)' } });
      defs += A.clip;
      s += `<text class="panel-tag" x="0" y="${y + 13}">${title}</text>` + A.s;
      const sat = c.sat_frac, K = c.K_by_wiring;
      const ord = [...pts.filter((q) => q[0].startsWith('fly_shuffle')), ...pts.filter((q) => !q[0].startsWith('fly_shuffle'))];
      const rel = relay.slice().sort((a, b) => c.shuffle_rms_r_deg_s[a] - c.shuffle_rms_r_deg_s[b]);
      for (const [k, x, yy] of ord) {
        const tip = (k === 'bare' ? 'Bare airframe' : k === 'yaw_damper' ? 'Yaw damper' : k === 'fly_real' ? 'Real wiring' : `Scrambled wiring ${sid(k)}`) +
          `: RMS yaw rate ${n(x, 2)} °/s, RMS sideslip ${n(yy, 2)}°` + (K[k] !== undefined ? `, K = ${n(K[k], 4)} per Hz, rudder saturated ${pct(100 * sat[k])} of steps` : '');
        if (k === 'bare') s += pt(A.X(x), A.Y(yy), 'f-bare', tip, 3.6, 's');
        else if (k === 'yaw_damper') s += pt(A.X(x), A.Y(yy), 'f-damper', tip, 3.8, 't');
        else if (k === 'fly_real') s += pt(A.X(x), A.Y(yy), 'f-real', tip, 4);
        else {
          const isRelay = relay.includes(k);
          s += `<g class="pt"><title>${esc(tip)}</title><circle class="hit" cx="${f1(A.X(x))}" cy="${f1(A.Y(yy))}" r="8"/><circle class="${isRelay ? 'f-null mk' : 's-null open'}" cx="${f1(A.X(x))}" cy="${f1(A.Y(yy))}" r="3.4"/></g>`;
          if (isRelay) { const left = rel.indexOf(k) === 0; s += `<text class="tick" x="${f1(A.X(x) + (left ? -7 : 7))}" y="${f1(A.Y(yy) + 4)}" text-anchor="${left ? 'end' : 'start'}">${sid(k)}</text>`; }
        }
      }
      y = y0 + ph + 44;
    });
    const L = legend([{ kind: 'sq', cls: 'f-bare', label: 'Bare airframe' }, { kind: 'tri', cls: 'f-damper', label: 'Yaw damper' },
      { kind: 'dot', cls: 'f-real', label: 'Real wiring' }, { kind: 'open', cls: 's-null', label: 'Scrambled wiring' },
      { kind: 'dot', cls: 'f-null', label: 'Scrambled, relay (labelled)' }], ml, W - mr, y);
    return svg(W, y + L.h + 2, 'RMS sideslip against RMS yaw rate for every controller', `<defs>${defs}</defs>` + s + L.s);
  };
}

// Plan view seen from above: flight runs left to right, so the right of the path is DOWN (cross-track, right +, is
// plotted with the y axis increasing downward). Both axes share one km-per-pixel scale.
function figTracks(S, order) {
  const R = S.replay.jsbsim;
  return (W) => {
    const ml = 56, mr = 12, head = 4;
    const all = order.map(([k]) => R[k].track_m).flat();
    const xs = all.map((q) => q[0] / 1000), ys = all.map((q) => q[1] / 1000);
    const xlo = Math.min(0, ...xs), xhi = Math.ceil(Math.max(...xs) * 2) / 2;
    let ylo = Math.floor(Math.min(...ys) * 4) / 4 - 0.25, yhi = Math.ceil(Math.max(...ys) * 4) / 4 + 0.25;
    let pw = W - ml - mr, ph = pw * (yhi - ylo) / (xhi - xlo);
    if (ph > 340) { ph = 340; pw = ph * (xhi - xlo) / (yhi - ylo); }
    if (ph < 150) { const pad = (150 / pw * (xhi - xlo) - (yhi - ylo)) / 2; ylo -= pad; yhi += pad; ph = 150; }
    const x0 = ml + (W - ml - mr - pw) / 2;
    const tk = (a, b) => { const t = []; for (let v = Math.ceil(a * 2) / 2; v <= b + 1e-9; v += 0.5) t.push(+v.toFixed(1)); return t; };
    // the y range is given high-to-low so that axes() (which maps min to the bottom) puts right-of-path at the bottom
    const A = axes({ id: 'ct', x0, x1: x0 + pw, y0: head, y1: head + ph, yl: x0 - 42,
      x: { min: xlo, max: xhi, ticks: tk(xlo, xhi), fmt: (t) => n(t, 1), label: 'Along the bare airframe’s path (km) →' },
      y: { min: yhi, max: ylo, ticks: tk(ylo, yhi), fmt: (t) => n(t, 1), label: 'Right of path (km) ↓' } });
    let s = `<defs>${A.clip}</defs>` + A.s + '<g clip-path="url(#ct)">';
    for (const [k, , cls, dash] of order) {
      const tr = R[k].track_m;
      s += `<path class="ln ${cls} ${dash || ''}" d="${path(tr.map((q) => q[0] / 1000), tr.map((q) => q[1] / 1000), A.X, A.Y)}"/>`;
    }
    s += '</g>';
    for (const [k, lab, cls] of order) {
      const r = R[k], e = r.track_m[r.track_m.length - 1];
      s += pt(A.X(e[0] / 1000), A.Y(e[1] / 1000), cls.replace('s-', 'f-'), `${lab}: heading change ${sgn(r.heading_change_deg, 1)}° (+ = right), final offset ${sgn(r.cross_track_m, 0)} m right of the bare path, mean rudder ${sgn(r.mean_rudder_deg, 1)}°`, 3.4);
    }
    const L = legend(order.map(([, lab, cls, dash]) => ({ kind: 'line', cls, dash, label: lab })), ml, W - mr, head + ph + 46);
    return svg(W, head + ph + 46 + L.h + 2, 'Ground tracks of the seed-100 flights, plan view from above', s + L.s);
  };
}

function figLesion(S) {
  const L = S.lesions, fr = L.fractions.map((x) => 100 * x), bare = L.reference_deg_s.bare;
  const sty = { real: ['s-real', '', 'f-real'], shuf00: ['s-null', 'dot', 'f-null'], shuf01: ['s-null', 'longdash', 'f-null'], shuf02: ['s-null', '', 'f-null'] };
  return (W) => {
    const ml = 56, mr = 12, ph = Math.max(170, Math.min(270, W * 0.42));
    const vals = L.wirings.flatMap((k) => [...L.rms_r_deg_s[k], ...L.rms_r_ci_deg_s.real.flat()]);
    const ylo = Math.floor(Math.min(...vals) * 5) / 5 - 0.2, yhi = Math.ceil(Math.max(...vals, bare) * 5) / 5 + 0.1;
    const yt = []; for (let t = Math.ceil(ylo * 2) / 2; t <= yhi + 1e-9; t += 0.5) yt.push(+t.toFixed(1));
    const A = axes({ id: 'cl', x0: ml, x1: W - mr, y0: 4, y1: 4 + ph, x: { min: -3, max: 83, ticks: fr, fmt: (t) => t + '%', label: 'Fraction of subcircuit neurons silenced at random' },
      y: { min: ylo, max: yhi, ticks: yt, fmt: (t) => n(t, 1), label: 'Mean RMS yaw rate (°/s)' } });
    let s = `<defs>${A.clip}</defs>` + A.s + '<g clip-path="url(#cl)">';
    s += `<line class="ln thin s-bare dashdot" x1="${ml}" x2="${W - mr}" y1="${f1(A.Y(bare))}" y2="${f1(A.Y(bare))}"/>`;
    const lo = L.rms_r_ci_deg_s.real.map((q) => q[0]), hi = L.rms_r_ci_deg_s.real.map((q) => q[1]);
    s += `<path class="b-real" d="${band(fr, lo, hi, A.X, A.Y)}"/>`;
    for (const k of [...L.wirings].reverse()) s += `<path class="ln ${k === 'real' ? '' : 'thin'} ${sty[k][0]} ${sty[k][1]}" d="${path(fr, L.rms_r_deg_s[k], A.X, A.Y)}"/>`;
    s += '</g>';
    for (const k of L.wirings) fr.forEach((x, i) => { s += pt(A.X(x), A.Y(L.rms_r_deg_s[k][i]), sty[k][2], `${k === 'real' ? 'Real wiring' : 'Scrambled ' + sid(k)}, ${x}% silenced: ${n(L.rms_r_deg_s[k][i], 2)} °/s`, k === 'real' ? 3.2 : 2.4); });
    const relay = S.closed_loop.jsbsim.relay_shuffles.map(sid);
    const lab = (k) => (k === 'real' ? 'Real wiring (band: 95% CI)' : `Scrambled ${sid(k)}${relay.includes(sid(k)) ? ' (relay)' : ''}`);
    const Lg = legend([...L.wirings.map((k) => (k === 'real' ? { kind: 'bandline', band: 'b-real', cls: sty[k][0], label: lab(k) }
      : { kind: 'line', cls: `${sty[k][0]} thin`, dash: sty[k][1], label: lab(k) })),
      { kind: 'line', cls: 's-bare thin', dash: 'dashdot', label: 'Bare airframe' }], ml, W - mr, 4 + ph + 46);
    return svg(W, 4 + ph + 46 + Lg.h + 2, 'RMS yaw rate against random lesion fraction', s + Lg.s);
  };
}

// ---------------------------------------------------------------- the paper
function render(S) {
  refOrder = []; eqs = []; mounts.length = 0;
  const W = S.wiring, TN = S.wiring.type_null, P = S.plant, M = S.model, T = S.tuning, B = S.bode, H = S.h1, A = S.amplitude, FB = S.fullbrain;
  const L2 = S.closed_loop.lin2, J = S.closed_loop.jsbsim, LES = S.lesions;
  const i1 = nearest(B.freqs, 1), fdb = (f0) => B.rel_db_vs_1hz[nearest(B.freqs, f0)];
  const relayJ = J.relay_shuffles, relayIds = relayJ.map(sid);
  const relayBeta = relayJ.map((k) => J.rms_beta_deg_by_wiring[k]);
  const bestShuf = (c) => Object.entries(c.shuffle_rms_r_deg_s).sort((a, b) => a[1] - b[1])[0];
  const [bestJ, bestJv] = bestShuf(J), [bestL, bestLv] = bestShuf(L2);
  const within = (v, c) => 100 * (v / c.controllers.fly_real.rms_r_deg_s - 1);
  const rep = S.replay && S.replay.jsbsim, repRelay = rep && relayJ.find((k) => rep[k]);
  const nTest = T.test_seeds.length, nTrain = T.train_seeds.length;
  const mp = T.margin_protocol, mr = T.margin_req;
  const dep = P.departure;
  const lesOK = LES && LES.fractions;
  const everySeed = ['lin2', 'jsbsim'].every((p) => S.closed_loop[p].per_seed.fly_minus_bare.every((x) => x < 0));
  const ratio = J.pct_less_swing.yaw_damper / J.pct_less_swing.fly_real;
  const FIG = { bode: 1, paired: 2, scatter: 3, tracks: 4, lesion: 5 }, TAB = { params: 1, h1: 2, cl: 3, replay: 4, targeted: 5 };
  const fig = (k) => `Fig.&nbsp;${FIG[k]}`, tab = (k) => `Table&nbsp;${TAB[k]}`;
  const EQ = {}; const E = (k) => `Eq.&nbsp;(${EQ[k]})`;
  const eqL = (k, tex, html) => { const s = eq(tex, html); EQ[k] = eqs.length; return s; };
  const r1 = (x) => n(x, 1), r2 = (x) => n(x, 2);
  const kgrid = (g) => `${g.length} values: 0 and ${g.length - 1} log-spaced values from ${n(g[1], g[1] < 0.01 ? 4 : 0)} to ${n(g[g.length - 1], g[1] < 0.01 ? 3 : 0)}`;

  // ---------- title, abstract
  const allSup = H.verdict === 'supported' && L2.fly_beats_bare && J.fly_beats_bare && L2.real_beats_shuffles && J.real_beats_shuffles;
  const noDep = lesOK && LES.n_departed_total === 0;
  const rank1 = J.rank_of_real === 1 && L2.rank_of_real === 1;
  const hsAll = lesOK && LES.targeted_equals_bare.all_HS.real, passive = lesOK && !LES.ever_worse_than_bare.real;
  const V = S.meta.versions || {};
  let h = `<article>
<div class="title">
<h1>Flight-testing a fruit-fly connectome as an aircraft yaw damper</h1>
<p class="author">Mutaqin Aryawijaya</p>
<p class="dateline">October 2026</p>
<p class="dateline">Code and data: <a href="${esc(S.meta.repo_url)}">${esc(S.meta.repo_url.replace('https://', ''))}</a></p>
</div>
<section class="abstract" aria-labelledby="abs-h"><h2 id="abs-h">Abstract</h2>
<p>Flight-control laws are qualified by their frequency response, stability margins, gust rejection and behaviour under failure; controllers built from connectome data have so far been shown mainly in time-domain demonstrations. We applied these tests to a spiking model of the fruit fly’s visual yaw pathway, a ${int(W.n_neurons)}-neuron FlyWire subcircuit from the T4/T5 motion detectors to the descending neuron DNa02, and compared it with rewired copies of itself and with a classical yaw damper. Under sinusoidal yaw, the right-minus-left DNa02 firing rate followed the yaw rate with coherence ${r2(H.degree.coherence.real)} at 1&nbsp;Hz and with the stabilizing sign. It ranked first against ${B.n_shuffles} degree-preserving rewirings (median ${r2(H.degree.coherence.median)}; <i>p</i>&nbsp;=&nbsp;${r2(H.degree.coherence.p)}) and narrowly first against ${B.n_type_shuffles} cell-type-preserving rewirings, which leave every input to the HS, VS, H2 and DNa02 cells unchanged. Closed around the rudder of a six-degree-of-freedom Cessna 172 in moderate turbulence and tuned under the same margin rule as the yaw damper, it reduced RMS yaw rate by ${pct(J.pct_less_swing.fly_real)}, against ${pct(J.pct_less_swing.yaw_damper)} for the damper. ${J.rank_of_real === 1 && L2.rank_of_real === 1 && J.rms_beta_rank_of_real === 1 && L2.rms_beta_rank_of_real === 1 ? `Of ${J.n_wirings} wirings it had the lowest RMS yaw rate and the lowest RMS sideslip on both aircraft models.` : `Among ${J.n_wirings} wirings it ranked ${J.rank_of_real} on RMS yaw rate and ${J.rms_beta_rank_of_real} on RMS sideslip on the six-degree-of-freedom model.`} The ${w(relayJ.length)} rewired controllers that came closest on yaw rate in the six-degree-of-freedom model were saturated relays flying skidding turns.${lesOK ? ` In the lesion experiment no flight departed, so the pre-registered departure test could not separate the wirings; under random neuron loss the real wiring’s mean RMS yaw rate rose toward the bare airframe’s${LES.ever_worse_than_bare.real ? '' : ' but never above it'}${LES.targeted_equals_bare.all_HS.real ? `, and silencing its ${w(W.n_HS)} HS cells removed its effect entirely` : ''}.` : ''} Flight-control tests can therefore measure what a connectome’s wiring contributes to a controller, and they expose failure modes, such as relay-like skidding, that a single performance score hides.</p>
</section>
<nav class="toc" aria-label="Contents"><h2>Contents</h2><ol>
${[['s1', 'Introduction'], ['s2', 'Methods'], ['s3', 'Results'], ['s4', 'Discussion'], ['s5', 'Conclusions'], ['decl', 'Declarations'], ['refs', 'References']]
    .map(([id, t], i) => `<li><a href="#${id}">${i < 5 ? `<span class="num">${i + 1}</span>` : ''}${t}</a></li>`).join('')}</ol></nav>`;

  // ---------- 1 Introduction
  h += `<section id="s1"><h2 class="sec"><span class="num">1</span>Introduction</h2>
<p class="noindent">A complete synaptic wiring diagram of an adult fruit-fly brain is now public ${cite('dorkenwald', 'schlegel')}, and a leaky integrate-and-fire network run directly on an earlier release of that diagram (v630), with one free parameter, reproduces a range of measured sensorimotor responses ${cite('shiu')}. A model of this kind can be placed in a feedback loop with a machine. That raises an engineering question that is easy to state and has rarely been tested with engineering tools: how a piece of real wiring behaves as a controller.</p>
<p>Aeronautics has a standard way of answering this question for a new flight-control law. Its frequency response is measured, its gain and phase margins are checked against minimum values ${cite('milf9490')}, its rejection of gusts is evaluated in standard turbulence models ${cite('milf8785')}, and its behaviour is examined as its components fail. We applied this procedure to the yaw axis, which needs a short vocabulary. The <em>yaw rate</em> <i>r</i> is how fast the nose swings left or right. <em>Sideslip</em> <i>β</i> is the angle between the nose and the oncoming air; a plane that turns with its rudder held over “skids” with large sideslip. Gusts excite the <em>Dutch roll</em>, a lightly damped oscillation in which yaw and sideslip trade back and forth. A <em>yaw damper</em> is the classical autopilot that measures <i>r</i> and moves the rudder to oppose it.</p>
<p>The fly’s own yaw stabilization offers a natural test case. When a fly turns, the image of the world slides across both eyes. Direction-selective T4 and T5 neurons in the optic lobe signal this motion, and their a and b subtypes prefer front-to-back and back-to-front motion, respectively ${cite('maisak', 'shinomiya')}. Horizontal-system (HS) tangential cells are excited by front-to-back motion on their own side ${cite('schnell')}, and activating HS cells on one side turns the fly toward that side ${cite('haikala')}. Among the descending neurons that carry commands to the body ${cite('namiki')}, the right-minus-left difference in DNa02 activity predicts turning velocity in walking flies ${cite('rayshubskiy')}. Chained together, these facts predict an optomotor reflex that opposes an imposed rotation. In tethered flight this response has delays of tens of milliseconds ${cite('theobald')}.</p>
<p>Several recent projects place connectome models in a flight loop, usually with time-domain demonstrations and sometimes with shuffled-wiring controls ${cite('clutch', 'leohio', 'flygm')}; shuffled controls are also standard in the connectome-modelling literature ${cite('shiu')}. Descending-neuron dynamics have been measured in real insects ${cite('leibbrandt')}, and the fly’s yaw optomotor response has been identified behaviourally ${cite('theobald')}. We could not find prior work that measures the frequency response of a connectome-model controller, its gain and phase margins by breaking the loop, its behaviour in standard Dryden turbulence ${cite('milf8785')}, or curves of its degradation under graded neuron loss toward departure, nor work that compares a connectome rudder loop with a classical yaw damper under an identical tuning budget.</p>
<p>We tested four hypotheses, fixed before any data were generated: that the circuit tracks yaw rotation coherently and with the stabilizing sign, and better than rewired copies (H1); that, closed around a rudder, it lowers RMS yaw rate against the bare airframe and beats the median rewired copy given the same tuning, on a two-state model (H2) and on a six-degree-of-freedom simulator (H3); and that it degrades more gracefully than rewired copies as neurons are silenced (H4). The goal was not to beat the yaw damper, but to measure how an organic controller behaves and whether its real wiring matters. ${allSup && noDep ? `H1, H2 and H3 were supported: the real wiring encoded yaw rate with the stabilizing sign and, in closed loop, damped yaw motion more than ${rank1 ? 'any' : 'the median'} rewired copy but far less than the classical yaw damper. H4 could not be tested, because no flight departed.` : 'Section 3 reports the outcome of each test.'}</p>
</section>`;

  // ---------- 2 Methods
  const params = [
    ['Resting and reset potential, <i>v</i><sub>0</sub>, <i>v</i><sub>reset</sub>', `${n(M.v_rest_mV, 0)}, ${n(M.v_reset_mV, 0)} mV`],
    ['Threshold, <i>v</i><sub>th</sub>', `${n(M.v_th_mV, 0)} mV`],
    ['Membrane time constant, <i>τ</i><sub>m</sub>', `${n(M.tau_m_ms, 0)} ms`],
    ['Synaptic time constant, <i>τ</i><sub>s</sub>', `${n(M.tau_s_ms, 0)} ms`],
    ['Refractory period, <i>t</i><sub>ref</sub>', `${n(M.t_ref_ms, 1)} ms`],
    ['Synaptic delay, <i>t</i><sub>dly</sub>', `${n(M.t_delay_ms, 1)} ms`],
    ['Weight per synapse, <i>w</i><sub>syn</sub>', `${n(M.w_syn_mV, 3)} mV`],
    ['Integration step, Δ<i>t</i>', `${n(M.sim_dt_ms, 1)} ms`],
    ['T4/T5 baseline rate, <i>r</i><sub>base</sub>', `${n(M.r_base_hz, 0)} Hz`],
    ['Input gain, <i>γ</i>', `${n(M.g_hz_per_deg_s, 0)} Hz per °/s`],
    ['Control step, <i>T</i><sub>c</sub>', `${n(M.ctrl_dt_ms, 0)} ms`],
    ['Washout time constant, <i>τ</i><sub>w</sub> (fly and damper)', `${n(M.washout_tau_s, 0)} s`],
  ];
  h += `<section id="s2"><h2 class="sec"><span class="num">2</span>Methods</h2>
<h3 class="sub"><span class="num">2.1</span>Connectome subcircuit</h3>
<p class="noindent">We used the FlyWire connectome, materialization v783 ${cite('dorkenwald')}, with its cell-type annotations ${cite('schlegel')} and the signed connectivity table distributed with the whole-brain model of Shiu et al. ${cite('shiu')}. Keeping only connections of at least ${W.threshold_synapses} synapses, we took as sources 𝒮 all T4a–d and T5a–d cells and as targets 𝒯 the descending neurons DNa02 and DNg02<sub>a–h</sub>, and kept every neuron that lies on a directed path of at most ${W.max_hops} hops from a source to a target,</p>
${eqL('sel', String.raw`\mathcal{V}=\bigl\{\,i:\ d(\mathcal{S}\!\to\! i)+d(i\!\to\!\mathcal{T})\le ${W.max_hops}\,\bigr\}`, `𝒱 = { <i>i</i> : <i>d</i>(𝒮 → <i>i</i>) + <i>d</i>(<i>i</i> → 𝒯) ≤ ${W.max_hops} }`)}
<p class="noindent">where <i>d</i> is the shortest directed hop count, together with every connection among those neurons, recurrent ones included. The subcircuit holds ${int(W.n_neurons)} neurons: ${int(W.n_sources)} T4/T5 cells and ${int(W.n_lif)} others, among them ${W.n_HS} HS, ${W.n_VS} VS and ${W.n_H2} H2 cells, ${W.n_DNa02} DNa02 cells (one per side) and ${W.n_DNg02} DNg02 cells. It has ${int(W.n_edges)} connections, of which ${int(W.n_sim_edges)} are simulated; the ${int(W.n_edges_into_t4t5)} connections onto T4/T5 cells are dropped because those cells are replaced by input sources (Section 2.3). T4/T5 cells supply ${pct(100 * W.hs_frac_from_t4t5)} of all synapses onto HS cells, and ${n(100 * W.hs_frac_t4t5_from_a, 2)}% of that input comes from the front-to-back a subtypes.</p>

<h3 class="sub"><span class="num">2.2</span>Neuron model</h3>
<p class="noindent">All non-input neurons follow the leaky integrate-and-fire model of Shiu et al. ${cite('shiu')}, unchanged. Each neuron <i>i</i> has a membrane potential <i>v</i><sub><i>i</i></sub> and a synaptic variable <i>g</i><sub><i>i</i></sub>,</p>
${eqL('lif', String.raw`\tau_m\frac{dv_i}{dt}=v_0-v_i+g_i,\qquad \tau_s\frac{dg_i}{dt}=-g_i,`, `<i>τ</i><sub>m</sub> d<i>v<sub>i</sub></i>/d<i>t</i> = <i>v</i><sub>0</sub> − <i>v<sub>i</sub></i> + <i>g<sub>i</sub></i>,&emsp; <i>τ</i><sub>s</sub> d<i>g<sub>i</sub></i>/d<i>t</i> = −<i>g<sub>i</sub></i>,`)}
<p class="noindent">and a spike of presynaptic neuron <i>j</i> arrives after a fixed delay,</p>
${eqL('syn', String.raw`\begin{gathered}g_i\leftarrow g_i+w_{\mathrm{syn}}\,W_{ji}\ \ \text{at}\ \ t_j^{\mathrm{spk}}+t_{\mathrm{dly}},\\ v_i>v_{\mathrm{th}}\ \Rightarrow\ v_i\leftarrow v_{\mathrm{reset}},\ \ g_i\leftarrow 0,\end{gathered}`, `<i>g<sub>i</sub></i> ← <i>g<sub>i</sub></i> + <i>w</i><sub>syn</sub> <i>W<sub>ji</sub></i> at <i>t<sub>j</sub></i><sup>spk</sup> + <i>t</i><sub>dly</sub>,<br><i>v<sub>i</sub></i> &gt; <i>v</i><sub>th</sub> ⇒ <i>v<sub>i</sub></i> ← <i>v</i><sub>reset</sub>, <i>g<sub>i</sub></i> ← 0,`)}
<p class="noindent">where <i>W<sub>ji</sub></i> is the synapse count from <i>j</i> to <i>i</i>, signed by the presynaptic neurotransmitter (GABAergic and glutamatergic neurons inhibitory, others excitatory). After a spike, <i>v</i> and <i>g</i> stop evolving for <i>t</i><sub>ref</sub> (<i>g</i> still receives synaptic input). Parameters are listed in ${tab('params')}. The network was simulated in Brian&nbsp;2 with exact integration of the linear dynamics.</p>
<div class="table" id="t-params"><p class="tcap"><b>Table ${TAB.params}. Model and controller parameters.</b> The neuron parameters are those of the published whole-brain model; the input and readout parameters were fixed before any data were generated.</p>
<div class="tbl">${tbl(['Parameter', 'Value'], params)}</div></div>

<h3 class="sub"><span class="num">2.3</span>Visual input encoding</h3>
<p class="noindent">We take <i>r</i>&nbsp;&gt;&nbsp;0 as a nose-right rotation. A nose-right turn moves the visual world front-to-back across the left eye and back-to-front across the right eye; T4a/T5a prefer front-to-back and T4b/T5b back-to-front motion ${cite('maisak', 'shinomiya')}. Each T4/T5 cell <i>c</i> was therefore replaced by an independent Poisson source with rate</p>
${eqL('input', String.raw`\lambda_c(t)=r_{\mathrm{base}}+\gamma\,\max\bigl(0,\ s_c\,r(t)\bigr),`, `<i>λ<sub>c</sub></i>(<i>t</i>) = <i>r</i><sub>base</sub> + <i>γ</i> max(0, <i>s<sub>c</sub></i> <i>r</i>(<i>t</i>)),`)}
<p class="noindent">with <i>r</i> in degrees per second, <i>s<sub>c</sub></i>&nbsp;=&nbsp;+1 for left T4a/T5a and right T4b/T5b cells, <i>s<sub>c</sub></i>&nbsp;=&nbsp;−1 for left T4b/T5b and right T4a/T5a cells, and <i>s<sub>c</sub></i>&nbsp;=&nbsp;0 for the vertical-motion c and d subtypes. In each integration step Δ<i>t</i> a source fires with probability <i>λ<sub>c</sub></i>Δ<i>t</i>. This encoding is piecewise linear (half-wave rectified) in <i>r</i> for each cell and ignores the temporal-frequency tuning of real motion detectors (Section 4.5).</p>

<h3 class="sub"><span class="num">2.4</span>Readout and controllers</h3>
<p class="noindent">The controller output is the rudder command <i>u</i>&nbsp;∈&nbsp;[−1,&nbsp;1], with <i>u</i>&nbsp;&gt;&nbsp;0 commanding a nose-right moment and |<i>u</i>|&nbsp;=&nbsp;1 equal to ${n(P.rudder_full_deg, 0)}° of rudder. The brain and the aircraft advance in lockstep with control step <i>T</i><sub>c</sub>. In closed loop the eye sees the sampled yaw rate, <i>r</i>(<i>t</i>)&nbsp;=&nbsp;<i>r<sub>k</sub></i> for <i>t</i>&nbsp;∈&nbsp;[<i>kT</i><sub>c</sub>,&nbsp;(<i>k</i>&nbsp;+&nbsp;1)<i>T</i><sub>c</sub>) (sample-and-hold). Let Δ<sub><i>k</i></sub>&nbsp;=&nbsp;<i>ν</i><sub>R,<i>k</i></sub>&nbsp;−&nbsp;<i>ν</i><sub>L,<i>k</i></sub> be the right-minus-left DNa02 firing rate (Hz) over that window, driven by <i>r<sub>k</sub></i>, and <i>b</i> its mean over a ${n(M.warmup_s, 0)}&nbsp;s warm-up without rotation before each flight. The fly controller is</p>
${eqL('readout', String.raw`\begin{gathered}x_k=\Delta_{k-1}-b,\qquad a=e^{-T_c/\tau_w},\\ \ell_k=a\,\ell_{k-1}+(1-a)\,x_k,\\ u_k=\operatorname{sat}\bigl(\varepsilon K\,(x_k-\ell_k)\bigr),\end{gathered}`, `<i>x<sub>k</sub></i> = Δ<sub><i>k</i>−1</sub> − <i>b</i>,&ensp; <i>a</i> = e<sup>−<i>T</i><sub>c</sub>/<i>τ</i><sub>w</sub></sup>,<br>ℓ<sub><i>k</i></sub> = <i>a</i> ℓ<sub><i>k</i>−1</sub> + (1 − <i>a</i>) <i>x<sub>k</sub></i>,<br><i>u<sub>k</sub></i> = sat(<i>εK</i>(<i>x<sub>k</sub></i> − ℓ<sub><i>k</i></sub>)),`)}
<p class="noindent">with ℓ<sub>0</sub>&nbsp;=&nbsp;0 and <i>u</i><sub>0</sub>&nbsp;=&nbsp;0: a discrete washout (high-pass) filter followed by a gain <i>K</i>&nbsp;≥&nbsp;0 and saturation to [−1,&nbsp;1]. The readout is causal: <i>u<sub>k</sub></i> uses window <i>k</i>&nbsp;−&nbsp;1, the one that has just ended, and is held over step <i>k</i>, which adds one step of transport delay. The readout sign <i>ε</i>&nbsp;∈&nbsp;{±1} was fixed at <i>ε</i>&nbsp;=&nbsp;+1 by the biology above, under which Δ&nbsp;&gt;&nbsp;0 commands a nose-right turn ${cite('haikala', 'rayshubskiy')}. The classical yaw damper uses the same washout on the measured yaw rate,</p>
${eqL('damper', String.raw`\begin{gathered}\ell_k=a\,\ell_{k-1}+(1-a)\,r_k,\\ u_k=\operatorname{sat}\bigl(-K\,(r_k-\ell_k)\bigr),\end{gathered}`, `ℓ<sub><i>k</i></sub> = <i>a</i> ℓ<sub><i>k</i>−1</sub> + (1 − <i>a</i>) <i>r<sub>k</sub></i>,<br><i>u<sub>k</sub></i> = sat(−<i>K</i>(<i>r<sub>k</sub></i> − ℓ<sub><i>k</i></sub>)),`)}
<p class="noindent">a pole-matched discretization, <i>C</i>(<i>z</i>)&nbsp;=&nbsp;−<i>K</i><i>a</i>(<i>z</i>&nbsp;−&nbsp;1)/(<i>z</i>&nbsp;−&nbsp;<i>a</i>), of −<i>Kτ</i><sub>w</sub><i>s</i>/(<i>τ</i><sub>w</sub><i>s</i>&nbsp;+&nbsp;1) (before saturation). The two controllers therefore differ only in the sensor, the processing between sensor and washout, and the fly’s one-step (${n(M.ctrl_dt_ms, 0)}&nbsp;ms) readout delay. Each gain was chosen from a grid of the same size: for the fly, ${kgrid(T.K_fly)} per Hz; for the damper, ${kgrid(T.K_damper)} per rad/s (each grid had ${T.K_base_n} values before the widening described in Section 2.9).</p>

<h3 class="sub"><span class="num">2.5</span>Aircraft and turbulence</h3>
<p class="noindent">The aircraft is the JSBSim ${cite('jsbsim')} Cessna 172 model (c172x), trimmed in level flight at ${int(P.h_ft)}&nbsp;ft and ${n(P.V_kts, 0)}&nbsp;kt true airspeed (<i>V</i>&nbsp;=&nbsp;${n(P.V_mps, 1)}&nbsp;m/s) and linearized numerically by JSBSim. Two aircraft models were used. The first is the two-state yaw–sideslip (Dutch-roll) approximation,</p>
${eqL('lin2', String.raw`\begin{aligned}\dot\beta_i&=\frac{Y_\beta}{V}\,\beta-r+\frac{Y_u}{V}\,u,\\ \dot r&=N_\beta\,\beta+N_r\,r+N_u\,u,\\ \beta&=\beta_i-\frac{v_g}{V},\end{aligned}`, `<i>β̇</i><sub>i</sub> = (<i>Y<sub>β</sub></i>/<i>V</i>) <i>β</i> − <i>r</i> + (<i>Y<sub>u</sub></i>/<i>V</i>) <i>u</i>,<br><i>ṙ</i> = <i>N<sub>β</sub></i> <i>β</i> + <i>N<sub>r</sub></i> <i>r</i> + <i>N<sub>u</sub></i> <i>u</i>,<br><i>β</i> = <i>β</i><sub>i</sub> − <i>v<sub>g</sub></i>/<i>V</i>,`)}
<p class="noindent">with <i>Y<sub>β</sub></i>/<i>V</i>&nbsp;=&nbsp;${n(P.Yb_V, 3)}&nbsp;s<sup>−1</sup>, <i>N<sub>β</sub></i>&nbsp;=&nbsp;${n(P.Nb, 2)}&nbsp;s<sup>−2</sup>, <i>N<sub>r</sub></i>&nbsp;=&nbsp;${n(P.Nr, 3)}&nbsp;s<sup>−1</sup>, <i>N<sub>u</sub></i>&nbsp;=&nbsp;${n(P.N_cmd, 3)}&nbsp;s<sup>−2</sup> and <i>Y<sub>u</sub></i>/<i>V</i>&nbsp;=&nbsp;${n(P.Ycmd_V, 4)}&nbsp;s<sup>−1</sup> per unit command, where <i>v<sub>g</sub></i> is the lateral gust velocity. Here <i>β</i><sub>i</sub>&nbsp;=&nbsp;<i>v</i>/<i>V</i> is the inertial sideslip and <i>β</i>&nbsp;=&nbsp;<i>β</i><sub>i</sub>&nbsp;−&nbsp;<i>v<sub>g</sub></i>/<i>V</i> the aerodynamic sideslip used everywhere else (by the controllers and in all metrics). The model is discretized exactly with a zero-order hold. Its Dutch roll has <i>ω<sub>n</sub></i>&nbsp;=&nbsp;${r2(P.dutch_roll.two_state.wn)}&nbsp;rad/s and damping ratio <i>ζ</i>&nbsp;=&nbsp;${n(P.dutch_roll.two_state.zeta, 3)}, against ${r2(P.dutch_roll.six_dof.wn)}&nbsp;rad/s and ${n(P.dutch_roll.six_dof.zeta, 3)} in the full linearization; the bare airframe already meets the MIL-F-8785C Level&nbsp;1, Category&nbsp;B (cruise) Dutch-roll requirements ${cite('milf8785')}, so the comparison is about gust rejection, not about rescuing an unstable aircraft. The lateral gust is Dryden turbulence ${cite('milf8785')}, white noise of unit intensity shaped by</p>
${eqL('dryden', String.raw`\begin{gathered}H_v(s)=\sigma_v\sqrt{T}\,\frac{1+\sqrt{3}\,Ts}{(1+Ts)^2},\\ T=L_v/V,\end{gathered}`, `<i>H<sub>v</sub></i>(<i>s</i>) = <i>σ<sub>v</sub></i> √<i>T</i> (1 + √3 <i>Ts</i>) / (1 + <i>Ts</i>)<sup>2</sup>,<br><i>T</i> = <i>L<sub>v</sub></i>/<i>V</i>,`)}
<p class="noindent">with the moderate-turbulence values at this altitude, <i>σ<sub>v</sub></i>&nbsp;=&nbsp;${r2(P.dryden.sigma_mps)}&nbsp;m/s and <i>L<sub>v</sub></i>&nbsp;=&nbsp;${n(P.dryden.L_m, 0)}&nbsp;m (<i>T</i>&nbsp;=&nbsp;${n(P.dryden.T_s, 1)}&nbsp;s), discretized exactly so that the gust variance is <i>σ<sub>v</sub></i><sup>2</sup>, and started from its stationary distribution. The second is the full nonlinear six-degree-of-freedom (6-DOF) JSBSim model, run at ${n(M.jsbsim_hz, 0)}&nbsp;Hz, with JSBSim’s MIL-spec turbulence at moderate severity. That option implements a first-order Markov approximation on each axis ${cite('yeager')}: it has the same <i>σ</i> but a different spectral shape from Eq.&nbsp;(${EQ.dryden}), and it adds longitudinal, vertical and rotational gusts, and its gusts start from zero, so the two models do not see like-for-like turbulence. In the six-degree-of-freedom runs a fixed aileron PID loop holds the wings level, identically for every controller: <i>δ</i><sub>a</sub>&nbsp;=&nbsp;sat(<i>δ</i><sub>a,trim</sub>&nbsp;−&nbsp;<i>K<sub>P</sub>φ</i>&nbsp;−&nbsp;<i>K<sub>D</sub>p</i>&nbsp;−&nbsp;<i>K<sub>I</sub></i>∫<i>φ</i>&thinsp;d<i>t</i>) with <i>K<sub>P</sub></i>&nbsp;=&nbsp;${M.roll_pid.KP}, <i>K<sub>D</sub></i>&nbsp;=&nbsp;${M.roll_pid.KD}, <i>K<sub>I</sub></i>&nbsp;=&nbsp;${M.roll_pid.KI}, bank angle <i>φ</i>, roll rate <i>p</i>, and sat clipping to [−1,&nbsp;1]; elevator and throttle are fixed at trim, and the controller under test moves only the rudder. A flight counts as a departure from controlled flight if any one of |<i>φ</i>|&nbsp;&gt;&nbsp;${n(dep.phi_deg, 0)}°, |<i>β</i>|&nbsp;&gt;&nbsp;${n(dep.beta_deg, 0)}° or |<i>r</i>|&nbsp;&gt;&nbsp;${n(dep.r_deg_s, 0)}°/s alone holds continuously for more than ${n(dep.hold_s, 0)}&nbsp;s.</p>

<h3 class="sub"><span class="num">2.6</span>Null models</h3>
<p class="noindent">The primary null rewires the simulated graph while preserving its degree sequence and signs. Pairs of connections exchange their postsynaptic partners (double-edge swaps), ${M.n_swaps_per_edge}<i>E</i> accepted swaps in all (<i>E</i>&nbsp;=&nbsp;number of connections), rejecting swaps that would create a self-connection or a duplicate pair. Each neuron keeps its in-degree, its out-degree, the multiset of its outgoing weights and its sign. In the secondary, cell-type-preserving null a swap is allowed only between connections whose postsynaptic neurons share side and cell type (unannotated cells form one class per side), so every presynaptic neuron also keeps its number of connections into each (side, type) class. Many classes have one member (${int(TN.n_singleton_lif_classes)} of ${int(TN.n_lif_classes)}, including every HS, VS and H2 cell and each DNa02); the ${pct(100 * TN.frac_edges_into_singleton_class)} of connections that point into them cannot move, so in each rewiring about ${pct(100 * TN.frac_unchanged_type_null_seed0)} of connections are unchanged, against ${pct(100 * TN.frac_unchanged_degree_null_seed0)} for the degree-preserving null (chance level ${pct(100 * TN.frac_unchanged_chance_degree_null)}; both measured on rewiring seed 0). This null therefore tests only the remaining ~${pct(100 - 100 * TN.frac_unchanged_type_null_seed0)} of the wiring. We built ${B.n_shuffles} rewirings of each kind (seeds 0–${B.n_shuffles - 1}); the closed-loop experiments used the first ${T.n_shuffles_closed_loop} degree-preserving rewirings and the lesion experiment the first ${S.protocol.lesion_n_shuffles}. In the closed-loop and lesion experiments we call the degree-preserving rewirings “scrambled wirings”.</p>

<h3 class="sub"><span class="num">2.7</span>Experimental protocol</h3>
<p class="noindent"><i>Open-loop frequency response.</i> Each wiring was driven by stepped sines <i>r</i>(<i>t</i>)&nbsp;=&nbsp;${n(S.protocol.bode_amp_deg_s, 0)}&nbsp;sin(2π<i>ft</i>)&nbsp;°/s at ${S.protocol.bode_n_freqs} frequencies from ${n(B.freqs[0], 1)} to ${n(B.freqs[B.freqs.length - 1], 0)}&nbsp;Hz (twelve log-spaced values plus 1&nbsp;Hz, each adjusted so a cycle spans a whole number of ${n(M.bin_ms, 0)}&nbsp;ms bins), each after a ${n(S.protocol.bode_warmup_s, 0)}&nbsp;s warm-up and for at least ${S.protocol.bode_min_cycles} cycles and 2&nbsp;s, with ${B.n_seeds} Poisson seeds. Firing rates were binned at ${n(M.bin_ms, 0)}&nbsp;ms. Gain and phase come from a lock-in projection over whole cycles,</p>
${eqL('lockin', String.raw`\begin{gathered}\hat x(f)=\frac{2}{N}\sum_{n=0}^{N-1}\bigl(x_n-\bar x\bigr)\,e^{-j2\pi f t_n},\\ H(f)=\hat\Delta(f)\,/\,\hat r(f),\end{gathered}`, `<i>x̂</i>(<i>f</i>) = (2/<i>N</i>) Σ<sub><i>n</i>=0</sub><sup><i>N</i>−1</sup> (<i>x<sub>n</sub></i> − <i>x̄</i>) e<sup>−j2π<i>ft<sub>n</sub></i></sup>,<br><i>H</i>(<i>f</i>) = <i>Δ̂</i>(<i>f</i>) / <i>r̂</i>(<i>f</i>),`)}
<p class="noindent">where <i>x<sub>n</sub></i> is the signal (Δ or <i>r</i>) in bin <i>n</i>, <i>t<sub>n</sub></i> the bin time, <i>x̄</i> the mean over the window and <i>N</i> the number of bins in a whole number of cycles, so the gain |<i>H</i>| is in Hz of Δ per °/s and a phase of 180° means Δ opposes the rotation. For the figures, seeds were averaged as complex <i>H</i>, <i>H̄</i>&nbsp;=&nbsp;(1/<i>n</i>)&thinsp;Σ<sub><i>s</i></sub><i>H<sub>s</sub></i>; the pre-registered H1 statistics (${tab('h1')}) are the seed means of per-seed coherence and of per-seed gain, (1/<i>n</i>)&thinsp;Σ<sub><i>s</i></sub>|<i>H<sub>s</sub></i>|. Coherence, the fraction of the output’s power at <i>f</i> that is linearly explained by the input (1 for a noiseless linear system, near 0 for an unrelated one), was estimated by Welch’s method with one-cycle Hann segments and 50% overlap,</p>
${eqL('coh', String.raw`C_{r\Delta}(f)=\frac{|P_{r\Delta}(f)|^2}{P_{rr}(f)\,P_{\Delta\Delta}(f)},`, `<i>C</i><sub><i>r</i>Δ</sub>(<i>f</i>) = |<i>P</i><sub><i>r</i>Δ</sub>(<i>f</i>)|<sup>2</sup> / (<i>P<sub>rr</sub></i>(<i>f</i>) <i>P</i><sub>ΔΔ</sub>(<i>f</i>)),`)}
<p class="noindent">and compared with the coherence of a sine against white noise analysed identically (the noise reference is the 95th percentile for a single seed).</p>
<p><i>Loop margins.</i> Gain and phase margins say how much extra gain, or extra delay, the closed loop tolerates before it oscillates. We measured them by breaking the loop at the actuator: a probe <i>d</i> is added to the command and</p>
${eqL('loop', String.raw`\begin{gathered}u_{\mathrm{plant}}=\operatorname{sat}(u_{\mathrm{ctrl}}+d),\\ L(j\omega)=-\,\hat u_{\mathrm{ctrl}}(\omega)\,/\,\hat u_{\mathrm{plant}}(\omega),\end{gathered}`, `<i>u</i><sub>plant</sub> = sat(<i>u</i><sub>ctrl</sub> + <i>d</i>),<br><i>L</i>(j<i>ω</i>) = −<i>û</i><sub>ctrl</sub>(<i>ω</i>) / <i>û</i><sub>plant</sub>(<i>ω</i>),`)}
${eqL('margins', String.raw`\begin{gathered}\mathrm{GM}=-20\log_{10}\bigl|L(j\omega_{180})\bigr|,\\ \mathrm{PM}=180^\circ-\bigl|\operatorname{wrap}_{[-180^\circ,\,180^\circ)}\angle L(j\omega_c)\bigr|,\\ \text{where}\ \ |L(j\omega_c)|=1,\end{gathered}`, `GM = −20 log<sub>10</sub>|<i>L</i>(j<i>ω</i><sub>180</sub>)|,<br>PM = 180° − |wrap<sub>[−180°, 180°)</sub> ∠<i>L</i>(j<i>ω<sub>c</sub></i>)|,&ensp; |<i>L</i>(j<i>ω<sub>c</sub></i>)| = 1,`)}
<p class="noindent">where <i>ω</i><sub>180</sub> is any crossing of −180° (mod 360°) and <i>ω<sub>c</sub></i> a gain crossing; the worst crossing of each kind is reported, and a loop with |<i>L</i>|&nbsp;&lt;&nbsp;1 everywhere has no phase margin to measure (PM&nbsp;=&nbsp;∞). The same protocol was applied to every controller: ${mp.n_sines} sines from ${n(mp.f_min_hz, 2)} to ${n(mp.f_max_sine_hz, 1)}&nbsp;Hz plus the Nyquist frequency (${n(mp.f_nyquist_hz, 0)}&nbsp;Hz), amplitude ${mp.A}, ${n(mp.settle_s, 0)}&nbsp;s settling and at least ${n(mp.min_window_s, 0)}&nbsp;s of whole cycles, on the two-state model without turbulence. Each |<i>L</i>| was compared with a noise floor: the lock-in amplitude of <i>u</i><sub>ctrl</sub> at the two adjacent DFT bins, divided by |<i>û</i><sub>plant</sub>|. If |<i>L</i>|&nbsp;≥&nbsp;1 at the lowest frequency, a crossing could lie below the grid and the gain was declared inadmissible.</p>
<p><i>Tuning and testing.</i> Every controller received the same rule and the same budget. The figure of merit is the RMS yaw rate of a flight,</p>
${eqL('rms', String.raw`\mathrm{RMS}_r=\Bigl(\frac{1}{N}\sum_{k=1}^{N}r_k^2\Bigr)^{1/2}`, `RMS<sub><i>r</i></sub> = ( (1/<i>N</i>) Σ<sub><i>k</i>=1</sub><sup><i>N</i></sup> <i>r<sub>k</sub></i><sup>2</sup> )<sup>1/2</sup>`)}
<p class="noindent">over its <i>N</i> control steps (RMS sideslip is defined the same way). Over the ${nTrain} training seeds (${T.train_seeds[0]}–${T.train_seeds[nTrain - 1]}) of ${n(P.flight_s, 0)}&nbsp;s flights in moderate turbulence, the gain was</p>
${eqL('tune', String.raw`\begin{gathered}K^{\ast}=\arg\min_{K\in\mathcal{A}}\ \frac{1}{${nTrain}}\sum_{s}\mathrm{RMS}_r(K,s),\\ \mathcal{A}=\{0\}\cup\bigl\{K>0:\ \text{no departure},\\ \mathrm{GM}\ge ${n(mr.gm_db, 0)}\ \mathrm{dB},\ \mathrm{PM}\ge ${n(mr.pm_deg, 0)}^\circ\bigr\},\end{gathered}`, `<i>K</i>* = argmin<sub><i>K</i>∈𝒜</sub> (1/${nTrain}) Σ<sub><i>s</i></sub> RMS<sub><i>r</i></sub>(<i>K</i>, <i>s</i>),<br>𝒜 = {0} ∪ {<i>K</i> &gt; 0 : no departure,<br>GM ≥ ${n(mr.gm_db, 0)} dB, PM ≥ ${n(mr.pm_deg, 0)}°},`)}
<p class="noindent">where the margin thresholds are the 6 dB / 45° stability-margin requirements of MIL-F-9490D ${cite('milf9490')}, as reproduced in its background report ${cite('boeing9490')} and stated by Mansur et al. ${cite('mansur')}, applied conservatively at every crossing. Margins measured on the two-state model were reused for the six-degree-of-freedom model. <i>K</i>&nbsp;=&nbsp;0 is the bare airframe and is always admissible. The gain grids could be widened under a pre-registered rule, whose application is described in Section 2.9. All closed-loop results are on ${nTest} held-out test seeds (${T.test_seeds[0]}–${T.test_seeds[nTest - 1]}) never used in tuning.</p>
<p><i>Lesions.</i> Neurons were drawn uniformly from all subcircuit neurons, input sources (${pct(100 * W.n_sources / W.n_neurons)}) and both DNa02 readout cells included, and silenced in nested fractions (${S.protocol.lesion_fractions.map((x) => Math.round(100 * x) + '%').join(', ')}), with the same neurons silenced in every wiring for a given test seed. A silenced neuron never spikes, so it reaches neither its targets nor the readout. The chance that at least one DNa02 cell is silenced is 1&nbsp;−&nbsp;(1&nbsp;−&nbsp;<i>f</i>)<sup>2</sup> at fraction <i>f</i> (${[0.2, 0.4].map((x) => `${pct(100 * (1 - (1 - x) ** 2))} at ${pct(100 * x)}`).join(', ')}), so part of the fade toward the bare airframe is loss of the readout itself. Each wiring kept its tuned gain, with no retuning (only the warm-up offset <i>b</i> is re-measured). Targeted lesions silenced all HS cells, all T4 cells, all T5 cells, or the top ${S.protocol.hub_fractions.map((x) => Math.round(100 * x) + '%').join(', ')} of neurons by betweenness centrality (directed, unweighted, on each wiring’s own simulated graph; ties broken by degree). Flights used the six-degree-of-freedom model.</p>

<h3 class="sub"><span class="num">2.8</span>Statistical analysis</h3>
<p class="noindent">The real wiring was compared with a null model by a one-sided permutation test,</p>
${eqL('perm', String.raw`p=\frac{1+\#\{\,j:\ Z_j\ \text{at least as extreme as}\ Z_{\mathrm{real}}\,\}}{1+n_{\mathrm{null}}},`, `<i>p</i> = (1 + #{<i>j</i> : <i>Z<sub>j</sub></i> at least as extreme as <i>Z</i><sub>real</sub>}) / (1 + <i>n</i><sub>null</sub>),`)}
<p class="noindent">where <i>Z<sub>j</sub></i> is the statistic (for example, 1&nbsp;Hz coherence) of null rewiring <i>j</i> and <i>Z</i><sub>real</sub> that of the real wiring. This counts ties against the real wiring and cannot fall below 1/(1&nbsp;+&nbsp;<i>n</i><sub>null</sub>) ${cite('phipson')}. For two controllers A and B we formed the per-seed paired difference <i>d<sub>s</sub></i>&nbsp;=&nbsp;RMS<sub><i>r</i></sub>(A,&nbsp;<i>s</i>)&nbsp;−&nbsp;RMS<sub><i>r</i></sub>(B,&nbsp;<i>s</i>) and report its mean with a 95% percentile bootstrap interval (${int(M.n_boot)} resamples of seeds). Where no flight departed, we report the Wilson 95% upper bound on the departure probability.</p>
<p><i>Decision rules.</i> H1 required (a) coherence above ${B.H1_coh_threshold} at every frequency up to 2&nbsp;Hz, (b) cos(phase)&nbsp;&lt;&nbsp;0 at every frequency up to 1&nbsp;Hz, and (c) that the real wiring’s 1&nbsp;Hz coherence and gain each exceed those of the ${B.n_shuffles} degree-preserving rewirings in the permutation test of ${E('perm')}, rejecting when <i>p</i>&nbsp;≤&nbsp;<i>α</i>&nbsp;=&nbsp;0.05 (the smallest <i>p</i> ${B.n_shuffles} nulls allow). H2 and H3 required the bootstrap interval for real&nbsp;−&nbsp;bare to lie below 0 on the two-state and the six-degree-of-freedom model, respectively; H2b required the same for real&nbsp;−&nbsp;median of the ${T.n_shuffles_closed_loop} scrambled wirings, each seed’s median taken across wirings. We also report the real wiring’s rank among all ${T.n_shuffles_closed_loop + 1} wirings by mean RMS yaw rate, whose permutation <i>p</i> (${E('perm')}) cannot fall below 1/${T.n_shuffles_closed_loop + 1}. The H4 statistic is the normalized area under the curve of the probability of no departure against lesion fraction <i>f</i>,</p>
${eqL('auc', String.raw`\mathrm{AUC}=\frac{1}{f_{\max}-f_{\min}}\int_{f_{\min}}^{f_{\max}}P_{\text{no dep}}(f)\,df,`, `AUC = (1/(<i>f</i><sub>max</sub> − <i>f</i><sub>min</sub>)) ∫<sub><i>f</i><sub>min</sub></sub><sup><i>f</i><sub>max</sub></sup> <i>P</i><sub>no dep</sub>(<i>f</i>) d<i>f</i>,`)}
<p class="noindent">where <i>P</i><sub>no dep</sub>(<i>f</i>) is the fraction of the ${LES ? LES.n_seeds : nTest} test seeds without departure at fraction <i>f</i>, and <i>f</i><sub>min</sub>, <i>f</i><sub>max</sub> are the smallest and largest fractions; it is computed by the trapezoid rule. H4 required the bootstrap interval of AUC(real)&nbsp;−&nbsp;mean AUC(scrambled) to lie above 0.</p>
<p><i>Secondary and exploratory analyses.</i> Mean RMS yaw rate against lesion fraction and a closed-loop comparison in which each scrambled wiring’s readout sign was taken from its own open-loop response were pre-registered secondary analyses. RMS sideslip was recorded under the pre-registration but not used as a test statistic. The cell-type-preserving null was added later as a secondary analysis (Section 2.9). The 1&nbsp;Hz amplitude sweep, the delay fit and the <i>γ</i>&nbsp;=&nbsp;4 arm were exploratory, and the input-gain sweep and the full-brain comparison were planned checks. None of these analyses enters a hypothesis verdict.</p>

<h3 class="sub"><span class="num">2.9</span>Pre-registration and deviations</h3>
<p class="noindent">The hypotheses, metrics, thresholds, seed splits and departure criterion were written down before any data existed. Two dated addenda, also written before any data, replaced the plan’s one-state yaw model with the two-state model of ${E('lin2')}, made the T4/T5 cells Poisson sources, and fixed the input sign map, the readout with its washout and sign, the tuning rule with its margin constraint, the null size (${B.n_shuffles}), the 1&nbsp;Hz test frequency and the minimum of ${S.protocol.bode_min_cycles} cycles per frequency. A third note, written after the open-loop data and before any closed-loop data, added the exploratory <i>γ</i>&nbsp;=&nbsp;4 arm.</p>
<p>Three independent audits (of the neural model, the flight dynamics and the citations) ran after the first open-loop analysis had been seen. They found six problems, and each fix was logged before the affected experiments were rerun. (1) The original degree-preserving null had been built on all extracted connections, before the connections onto the T4/T5 cells were dropped; rewiring moved this mostly inhibitory input onto simulated neurons, giving a null (shuffle seed 0) a net signed weight of ${int(W.net_weight_superseded)} against ${int(W.net_weight_real)} for the real wiring. The null was rebuilt on the simulated connections only (net weight ${int(W.net_weight_shuffle)}), and only the rebuilt null is reported. (2) The readout used spikes from the same control step, which is not causal; it now uses the previous window (${E('readout')}). (3) Lesioned readout cells had still been read out; they are now silent. (4) The yaw damper had been admitted on analytic margins while the fly controllers were admitted on injected ones; a single injection protocol now serves every controller. (5) JSBSim’s turbulence generator gave identical turbulence for seeds 0 and 1; project seeds are now offset by +1 (Section 2.10). (6) The grid-widening rule was narrowed, as described next. The cell-type-preserving null was added after the audits as a secondary analysis.</p>
<p>The pre-registered grid-widening rule, “if any controller’s unconstrained best gain lies on the upper edge of its grid, widen every grid” (narrowed before the rerun, on the audits’ advice, to apply only to the upper edge, because <i>K</i>&nbsp;=&nbsp;0 is the bare reference), was triggered once during tuning. When it triggered, before any widened run, we fixed the extension at two points at the same log spacing for every grid and allowed no further widening.</p>

<h3 class="sub"><span class="num">2.10</span>Software and reproducibility</h3>
<p class="noindent">The connectome is FlyWire ${esc((S.meta.data_version || '').replace('flywire_', ''))}; simulations used Brian&nbsp;${esc(V.brian2 || '')} and JSBSim&nbsp;${esc(V.jsbsim || '')} under Python&nbsp;${esc(S.meta.python || '')} with NumPy&nbsp;${esc(V.numpy || '')} and SciPy&nbsp;${esc(V.scipy || '')}. Every run is deterministic for a given seed: Poisson seeds ${S.protocol.bode_seeds[0]}–${S.protocol.bode_seeds[S.protocol.bode_seeds.length - 1]} for the open-loop measurements, rewiring seeds 0–${B.n_shuffles - 1}, training seeds ${T.train_seeds[0]}–${T.train_seeds[nTrain - 1]} and test seeds ${T.test_seeds[0]}–${T.test_seeds[nTest - 1]}. JSBSim’s turbulence generator maps seeds 0 and 1 to the same stream, so project seed <i>s</i> is passed to it as <i>s</i>&nbsp;+&nbsp;1; because its random-number generator is implementation-defined, all reported six-degree-of-freedom flights were run on Linux. The experiments were sharded across free GitHub Actions runners; each analysis step re-derives its numbers from the per-job outputs and ends with an assertion-based self-check, and every result number in this paper is read from a generated summary file that records the source of each value (protocol constants appear in the prose).</p>
</section>`;

  // ---------- 3 Results
  const typeOK = S.signs.type_stabilizing;
  h += `<section id="s3"><h2 class="sec"><span class="num">3</span>Results</h2>
<h3 class="sub"><span class="num">3.1</span>Open-loop frequency response (H1)</h3>
<p class="noindent">The open-loop test asked whether the subcircuit’s output follows yaw rotation with the sign a stabilizing reflex needs, and whether this depends on its wiring. The right-minus-left DNa02 rate of the real wiring followed the stimulus closely (${fig('bode')}). Coherence was ${rng(B.coh_range_le_2hz, 3)} at every frequency up to 2&nbsp;Hz, far above both the pre-registered threshold of ${B.H1_coh_threshold} and the noise-only level. The phase was ${rng(B.phase_range_le_1hz, 1)}° up to 1&nbsp;Hz, so Δ falls when the nose swings right: under the readout sign of Section 2.4 this is negative feedback. The gain was flat at ${rng(B.flat_gain_range, 3)}&nbsp;Hz per °/s up to about 5&nbsp;Hz and fell, relative to 1&nbsp;Hz, by ${n(-fdb(7.7), 1)}&nbsp;dB at ${n(B.freqs[nearest(B.freqs, 7.7)], 1)}&nbsp;Hz and ${n(-fdb(20), 1)}&nbsp;dB at ${n(B.freqs[nearest(B.freqs, 20)], 0)}&nbsp;Hz. Above 2&nbsp;Hz the phase lag is consistent with a pure delay of ${n(B.delay_fit.tau_ms, 1)}&nbsp;ms (an exploratory fit to ${B.delay_fit.n_points} points with residual ${n(B.delay_fit.rms_resid_deg, 0)}° RMS). At the aircraft’s Dutch-roll frequency such a delay would cost about ${n(B.delay_lag_at_dutch_roll_deg, 1)}° of phase; the measured lag there was at most ${n(B.measured_lag_near_dutch_roll_deg, 1)}°.</p>
<p>${H.verdict === 'supported' ? 'All three parts of H1 held' : 'H1 was not supported'} (${tab('h1')}). At 1&nbsp;Hz the real wiring ranked first of ${H.degree.coherence.n_null + 1} against the degree-preserving null on both coherence and gain, <i>p</i>&nbsp;=&nbsp;${r2(H.degree.coherence.p)}, the smallest value ${H.degree.coherence.n_null} rewirings allow. Most rewirings lost the response almost entirely: their median gain was ${pct(100 * H.degree.gain.median / H.degree.gain.real)} of the real wiring’s. Under degree-preserving rewiring, ${S.signs.degree_neg} of ${S.signs.degree_n} nulls had the destabilizing sign at low frequency, and among the ${S.signs.degree_coherent} with 1&nbsp;Hz coherence above 0.5, ${S.signs.degree_coherent_neg} did. The cell-type-preserving null, which by construction leaves every input to the HS, VS, H2 and DNa02 cells untouched (about ${pct(100 * TN.frac_unchanged_type_null_seed0)} of connections are unchanged), kept most of the response: all ${typeOK} of its rewirings kept the stabilizing sign, with median coherence ${n(H.type.coherence.median, 3)} and gain ${r2(H.type.gain.median)}. Rewiring the remaining connections within cell type and side lowered gain and coherence by a small but consistent amount; the real wiring still ranked first on both (<i>p</i>&nbsp;=&nbsp;${r2(H.type.gain.p)}). Whether the neuron-level wiring of the core T4/T5&nbsp;→&nbsp;HS&nbsp;→&nbsp;DNa02 pathway matters is not tested by this null.${H.verdict === 'supported' ? ' In open loop, then, the subcircuit behaves as a yaw-rate sensor with the stabilizing sign, and degree-preserving rewiring removes most of that behaviour.' : ''}</p>
${figure('f-bode', figBode(S), `<b>Figure ${FIG.bode}. Open-loop frequency response of the real wiring and of both null models.</b> Right-minus-left DNa02 rate Δ in response to yaw rate <i>r</i> (${n(S.protocol.bode_amp_deg_s, 0)}&nbsp;°/s stepped sines, mean of ${B.n_seeds} seeds). (a) Gain, (b) phase of Δ relative to <i>r</i> (180° opposes the rotation), (c) coherence. Vertical bars on the real wiring: ±1 SD over the ${B.n_seeds} seeds. In (a) and (c), bands span the minimum to maximum of the ${B.n_shuffles} rewirings of each null and thin lines are their medians. Panel (b) shows the cell-type-preserving median only (no band), because most degree-preserving rewirings have too little coherence for a phase to be defined; its dotted line marks 180°. The dotted line in (c) is the H1 threshold.`,
    tbl(['<i>f</i> (Hz)', 'Gain', 'Phase (°)', 'Coherence', 'Degree null, median coh.', 'Type null, median coh.'],
      B.freqs.map((x, i) => [n(x, 3), n(B.real.gain[i], 3), n(B.real.phase_deg[i], 1), n(B.real.coh[i], 3), n(B.shuffle.coh_median[i], 3), n(B.type_shuffle.coh_median[i], 3)])))}
<div class="table" id="t-h1"><p class="tcap"><b>Table ${TAB.h1}. Pre-registered H1 test at ${n(H.f_hz, 0)}&nbsp;Hz.</b> The real wiring against ${H.degree.coherence.n_null} rewirings of each null. Rank 1 is the highest value; <i>p</i> from Eq.&nbsp;(${EQ.perm}).</p>
<div class="tbl">${tbl(['Measure', 'Real', 'Null median', 'Null max.', 'Rank', '<i>p</i>'],
    [['degree', 'Degree-preserving null'], ['type', 'Cell-type-preserving null']].flatMap(([nk, title], gi) => [{ mid: gi > 0, group: title },
      ...[['Coherence', 'coherence'], ['Gain (Hz per °/s)', 'gain']].map(([lab, k]) => [`&ensp;${lab}`, n(H[nk][k].real, 3), n(H[nk][k].median, 3), n(H[nk][k].max, 3), `${H[nk][k].rank} of ${H[nk][k].n_null + 1}`, r2(H[nk][k].p)])]))}</div></div>`;

  // 3.2 closed loop
  const C2 = (c, k, u, d) => c.ci && c.ci[k] ? ci(c.ci[k][u], d) : n(c.controllers[k][u], d);
  const shR = (c) => Object.values(c.shuffle_rms_r_deg_s), shB = (c) => Object.entries(c.rms_beta_deg_by_wiring).filter(([k]) => k !== 'fly_real').map((q) => q[1]);
  const mrg = T.selected.lin2, FL = T.fly_loop;
  h += `<h3 class="sub"><span class="num">3.2</span>Closed-loop gust rejection (H2, H2b and H3)</h3>
<p class="noindent">The closed-loop tests asked whether the circuit, wired to the rudder, reduces the aircraft’s yaw response to gusts, and whether the real wiring does so better than scrambled wirings given the same tuning. The tuning rule selected <i>K</i>&nbsp;=&nbsp;${n(T.selected.jsbsim.fly_real.K, 4)} per Hz for the real wiring and <i>K</i>&nbsp;=&nbsp;${n(T.selected.jsbsim.yaw_damper.K, 1)} per rad/s for the yaw damper, on both models. At this gain the fly loop’s largest measured |<i>L</i>| was ${r2(FL.max_abs_L)} (at ${r2(FL.max_abs_L_f_hz)}&nbsp;Hz, phase ${sgn(FL.max_abs_L_phase_deg, 0)}°), so |<i>L</i>|&nbsp;&lt;&nbsp;1 at every tested frequency and no phase margin applies. Its phase crosses −180° only where |<i>L</i>| is at the noise floor (|<i>L</i>|&nbsp;=&nbsp;${n(FL.abs_L_at_gm, 3)} against a noise floor of up to ${n(FL.noise_at_gm, 3)} near ${n(FL.f_gm_hz, 2)}&nbsp;Hz), so its nominal gain margin of ${n(mrg.fly_real.gm_db, 1)}&nbsp;dB is only a bound: adding the noise to |<i>L</i>| still leaves ${n(FL.gm_bound_db, 0)}&nbsp;dB or more. The damper had ${n(mrg.yaw_damper.gm_db, 1)}&nbsp;dB and ${n(mrg.yaw_damper.pm_deg, 1)}°. The fly loop is admissible because it is gentle, not because it is fast. ${J.n_departed_all + L2.n_departed_all === 0 ? 'No controller departed controlled flight on any test seed.' : `${J.n_departed_all + L2.n_departed_all} test flights departed controlled flight.`}</p>
<p>The real wiring reduced RMS yaw rate on both models (${tab('cl')}, ${fig('paired')}). On the six-degree-of-freedom model the paired difference real&nbsp;−&nbsp;bare was ${cit(J.paired.fly_minus_bare, 2)}&nbsp;°/s, a ${pct(J.pct_less_swing.fly_real)} reduction; on the two-state model it was ${cit(L2.paired.fly_minus_bare, 2)}&nbsp;°/s (${pct(L2.pct_less_swing.fly_real)}). ${L2.fly_beats_bare && J.fly_beats_bare ? 'H2 and H3 were supported.' : 'H2 and H3 were not both supported.'} Real&nbsp;−&nbsp;median scrambled was ${cit(J.paired.real_minus_median_shuffle, 2)}&nbsp;°/s and ${cit(L2.paired.real_minus_median_shuffle, 2)}&nbsp;°/s. ${L2.real_beats_shuffles && J.real_beats_shuffles ? 'The interval excludes 0 on both models, so H2b was supported as pre-registered' : 'The interval does not exclude 0 on both models, so H2b was not supported as pre-registered'}; the real wiring also had the ${J.rank_of_real === 1 && L2.rank_of_real === 1 ? `lowest mean RMS yaw rate of all ${J.n_wirings} wirings on both models` : `rank ${L2.rank_of_real} and ${J.rank_of_real} of ${J.n_wirings} wirings`}, although with ${T.n_shuffles_closed_loop} rewirings that rank cannot reach <i>p</i> below 1/${J.n_wirings}&nbsp;≈&nbsp;${n(J.p, 3)}. Two caveats temper this. The median scrambled controller is close to the bare airframe because the tuning rule set <i>K</i>&nbsp;=&nbsp;0 for ${w(L2.n_shuffles_zero_gain)} of ${T.n_shuffles_closed_loop} scrambled wirings on the two-state model and ${w(J.n_shuffles_zero_gain)} on the six-degree-of-freedom model, so the per-wiring ranking is the more informative comparison. And the scrambled wiring with the lowest mean RMS yaw rate came within ${pct(within(bestJv, J))} of the real wiring on the six-degree-of-freedom model (scrambled ${sid(bestJ)}) and ${pct(within(bestLv, L2))} on the two-state model (scrambled ${sid(bestL)}); Section 3.3 shows how the closest ones achieved this. When each scrambled wiring’s readout sign was instead taken from its own open-loop response, the real wiring still ranked first (real&nbsp;−&nbsp;median ${ci(J.signflip_real_minus_median, 2)} and ${ci(L2.signflip_real_minus_median, 2)}&nbsp;°/s).</p>
<p>The classical yaw damper was far better: ${pct(J.pct_less_swing.yaw_damper)} and ${pct(L2.pct_less_swing.yaw_damper)} less RMS yaw rate than the bare airframe, and damper&nbsp;−&nbsp;real of ${cit(J.damper_minus_fly, 2)} and ${cit(L2.damper_minus_fly, 2)}&nbsp;°/s. On sideslip the picture differs. The real wiring lowered RMS sideslip against the bare airframe on both models (${ci(J.paired_beta.fly_minus_bare, 2)}° and ${ci(L2.paired_beta.fly_minus_bare, 2)}°), and on the two-state model its mean RMS sideslip (${r2(L2.controllers.fly_real.rms_beta_deg)}°) was below the yaw damper’s (${r2(L2.controllers.yaw_damper.rms_beta_deg)}°), whose difference from bare there was ${ci(L2.paired_beta.damper_minus_bare, 2)}°. An exploratory arm with a four-fold stronger eye input (<i>γ</i>&nbsp;=&nbsp;4, two-state model only) reached ${r2(L2.g4.rms_r_deg_s)}&nbsp;°/s, a difference from bare of ${ci(L2.g4.fly_minus_bare, 2)}&nbsp;°/s. The real wiring is therefore a yaw damper${everySeed ? ' that helps on every test seed' : ''}, but a much weaker one than the classical design.</p>
<div class="table" id="t-cl"><p class="tcap"><b>Table ${TAB.cl}. Closed-loop performance in moderate turbulence.</b> Mean RMS yaw rate and RMS sideslip on ${nTest} held-out test seeds, with 95% bootstrap intervals over seeds. For the ${T.n_shuffles_closed_loop} scrambled wirings the range of their means is given.</p>
<div class="tbl">${tbl(['Controller', 'RMS <i>r</i> (°/s)', 'RMS <i>β</i> (°)', 'RMS <i>r</i> (°/s)', 'RMS <i>β</i> (°)'], [
    ['No controller (bare)', C2(L2, 'bare', 'rms_r_deg_s', 2), C2(L2, 'bare', 'rms_beta_deg', 2), C2(J, 'bare', 'rms_r_deg_s', 2), C2(J, 'bare', 'rms_beta_deg', 2)],
    ['Yaw damper', C2(L2, 'yaw_damper', 'rms_r_deg_s', 2), C2(L2, 'yaw_damper', 'rms_beta_deg', 2), C2(J, 'yaw_damper', 'rms_r_deg_s', 2), C2(J, 'yaw_damper', 'rms_beta_deg', 2)],
    ['Fly, real wiring', C2(L2, 'fly_real', 'rms_r_deg_s', 2), C2(L2, 'fly_real', 'rms_beta_deg', 2), C2(J, 'fly_real', 'rms_r_deg_s', 2), C2(J, 'fly_real', 'rms_beta_deg', 2)],
    { mid: true, cells: ['Fly, scrambled wirings', rng([Math.min(...shR(L2)), Math.max(...shR(L2))], 2), rng(L2.shuffle_rms_beta_range_deg, 2), rng([Math.min(...shR(J)), Math.max(...shR(J))], 2), rng(J.shuffle_rms_beta_range_deg, 2)] },
    ['Rank of real among wirings', `${L2.rank_of_real} of ${L2.n_wirings}`, `${L2.rms_beta_rank_of_real} of ${L2.n_wirings}`, `${J.rank_of_real} of ${J.n_wirings}`, `${J.rms_beta_rank_of_real} of ${J.n_wirings}`],
  ], { grp: '<thead><tr class="grp"><th></th><th class="span" colspan="2">Two-state model</th><th class="span" colspan="2">JSBSim 6-DOF</th></tr></thead>' })}</div></div>
${figure('f-paired', figPaired(S), `<b>Figure ${FIG.paired}. Paired differences in RMS yaw rate on the ${nTest} held-out test seeds.</b> Small dots: one per seed. Squares and bars: mean and 95% percentile bootstrap interval. Negative values favour the first-named controller. ${everySeed ? 'The real wiring helps on every seed; the' : 'The'} yaw damper helps much more.`,
    tbl(['Seed', ...[0, 1].flatMap(() => ['R − B', 'R − med.', 'D − B'])],
      T.test_seeds.map((sd, i) => [sd, ...[L2, J].flatMap((c) => ['fly_minus_bare', 'real_minus_median_shuffle', 'damper_minus_bare'].map((k) => sgn(c.per_seed[k][i], 3)))]),
      { grp: '<thead><tr class="grp"><th></th><th class="span" colspan="3">Two-state model</th><th class="span" colspan="3">JSBSim 6-DOF</th></tr></thead>' }) +
    '<p class="tnote">R − B: real − bare; R − med.: real − median scrambled; D − B: yaw damper − bare (°/s).</p>')}`;

  // 3.3 relay
  const trackOrder = rep ? [['bare', 'No controller (bare)', 's-bare', 'dash'], ['yaw_damper', 'Yaw damper', 's-damper'], ['fly_real', 'Fly, real wiring', 's-real'],
    ...(repRelay ? [[repRelay, `Fly, scrambled ${sid(repRelay)} (relay)`, 's-null']] : [])].filter(([k]) => rep[k]) : [];
  const rr = repRelay && rep[repRelay];
  const RORD = (k) => ['bare', 'yaw_damper', 'fly_real'].indexOf(k) + 1 || 4;   // Table 4 rows in Fig. 4's legend order
  h += `<h3 class="sub"><span class="num">3.3</span>Scrambled controllers that reduce yaw rate by skidding (secondary analysis)</h3>
<p class="noindent">The scrambled wirings that came closest to the real wiring on yaw rate were not better yaw dampers. The tuning rule chose each gain by RMS yaw rate on the training gusts alone, and that statistic barely penalizes a slow, steady turn, so a controller can score well by holding the rudder over and letting the aircraft skid round. On the six-degree-of-freedom model, scrambled wirings ${relayIds.join(', ').replace(/, ([^,]*)$/, ' and $1')} selected <i>K</i>&nbsp;=&nbsp;${n(J.K_by_wiring[relayJ[0]], 3)} per Hz, a value reached only after the grid widening. At this gain the command is saturated in ${(() => { const v = relayJ.map((k) => pct(100 * J.sat_frac[k])); return new Set(v).size === 1 ? `${v[0]} of control steps for each` : `${v.join(', ')} of control steps`; })()}: the controller has become a relay that slams the rudder to one stop or the other. In the replayed flights, the sparse DNa02 spike train passed through the washout keeps the relay mostly on one side, holding a steady rudder deflection, so the aircraft flies a slow, skidding turn. RMS yaw rate, the pre-registered statistic, barely penalizes a slow steady turn; RMS sideslip does. Across all test seeds only RMS sideslip was checked: averaged over the seeds, these ${w(relayJ.length)} controllers had RMS sideslip of ${rng([Math.min(...relayBeta), Math.max(...relayBeta)], 1)}°, against ${r2(J.controllers.bare.rms_beta_deg)}° for the bare airframe, ${r2(J.controllers.yaw_damper.rms_beta_deg)}° for the yaw damper and ${r2(J.controllers.fly_real.rms_beta_deg)}° for the real wiring (${fig('scatter')}). By RMS sideslip, a measure recorded under the pre-registration but not used as a test statistic, the real wiring ranks ${J.rms_beta_rank_of_real === 1 && L2.rms_beta_rank_of_real === 1 ? 'first' : `${J.rms_beta_rank_of_real} and ${L2.rms_beta_rank_of_real}`} of ${J.n_wirings} on both models.</p>
${rr ? `<p>The replay of test seed ${S.replay.jsbsim.fly_real.seed} shows the mechanism directly (${fig('tracks')}, ${tab('replay')}; it is also the scenario replayed in the viewer above). Scrambled wiring ${sid(repRelay)} held a mean rudder of ${sgn(rr.mean_rudder_deg, 1)}° with the rudder saturated in ${pct(100 * rr.sat_frac)} of steps, turned the heading by ${sgn(rr.heading_change_deg, 0)}° (${rr.heading_change_deg > 0 ? 'to the right' : 'to the left'}) in ${n(P.flight_s, 0)}&nbsp;s at a mean sideslip of ${n(rr.mean_beta_deg, 1)}°, and ended ${n(Math.abs(rr.cross_track_m) / 1000, 2)}&nbsp;km ${rr.cross_track_m > 0 ? 'right' : 'left'} of the bare airframe’s path. The real wiring and the yaw damper stayed within ${int(Math.max(Math.abs(rep.fly_real.cross_track_m), Math.abs(rep.yaw_damper.cross_track_m)))}&nbsp;m of it. The scrambled controller with the lowest RMS yaw rate is therefore not a usable yaw damper, and the near-tie on RMS yaw rate in Section 3.2 is an artefact of that metric.</p>` : ''}
${figure('f-scatter', figScatter(S), `<b>Figure ${FIG.scatter}. RMS sideslip against RMS yaw rate for every controller.</b> Means over the ${nTest} test seeds. A good yaw damper sits low on both axes. Filled grey points are scrambled wirings whose rudder was saturated in more than 90% of steps (relays); several scrambled wirings with <i>K</i>&nbsp;=&nbsp;0 coincide with the bare airframe. Note the different sideslip scales: the axis in (a) is expanded, while (b) must also hold the relays.`,
    tbl(['Controller', 'RMS <i>r</i>, 2-state', 'RMS <i>β</i>, 2-state', 'RMS <i>r</i>, 6-DOF', 'RMS <i>β</i>, 6-DOF', 'Sat., 6-DOF'],
      [['Bare', 'bare'], ['Yaw damper', 'yaw_damper'], ['Real wiring', 'fly_real'], ...Object.keys(J.shuffle_rms_r_deg_s).map((k) => [`Scrambled ${sid(k)}`, k])].map(([lab, k]) => {
        const v = (c) => [k === 'bare' || k === 'yaw_damper' || k === 'fly_real' ? c.controllers[k].rms_r_deg_s : c.shuffle_rms_r_deg_s[k], k === 'bare' || k === 'yaw_damper' ? c.controllers[k].rms_beta_deg : c.rms_beta_deg_by_wiring[k]];
        const [a, b] = v(L2), [c, d] = v(J);
        return [lab, r2(a), r2(b), r2(c), r2(d), pct(100 * J.sat_frac[k])];
      })))}
${rep ? figure('f-tracks', figTracks(S, trackOrder), `<b>Figure ${FIG.tracks}. Ground tracks of the six-degree-of-freedom flights on test seed ${S.replay.jsbsim.fly_real.seed}.</b> Same gusts for all; plan view from above.${rr ? ` Scrambled wiring ${sid(repRelay)}${repRelay === bestJ ? ` had the lowest training RMS yaw rate of the ${w(T.n_shuffles_closed_loop)} scrambled wirings, but it` : ''} flies as a relay: the rudder sits at its stop, the aircraft skids in a steady ${rr.heading_change_deg > 0 ? 'right' : 'left'} turn and ends ${n(Math.abs(rr.cross_track_m) / 1000, 2)}&nbsp;km off course (Section 3.3).` : ''} The frame is aligned with the bare airframe’s start-to-end path, which runs left to right, so a turn to the right appears as a track bending downward; both axes have the same scale. Dots mark the end of each ${n(P.flight_s, 0)}&nbsp;s flight.`) : ''}
${rep ? `<div class="table" id="t-replay"><p class="tcap"><b>Table ${TAB.replay}. Rudder use and flight path on test seed ${S.replay.jsbsim.fly_real.seed}.</b> Computed from the replayed flights: mean rudder deflection, fraction of steps with the rudder at its stop, heading change over the flight (+ = right), final offset to the right (+) of the bare airframe’s path, and mean sideslip.</p>
<div class="tbl">${tbl(['Flight', 'Mean rudder (°)', 'Saturated', 'Heading (°)', 'Offset (m)', 'Mean <i>β</i> (°)'],
    ['jsbsim', 'lin2'].flatMap((p) => [{ mid: p === 'lin2', group: p === 'jsbsim' ? 'Six-degree-of-freedom model' : 'Two-state model' },
      ...Object.entries(S.replay[p] || {}).sort(([a], [b]) => RORD(a) - RORD(b)).map(([k, r]) => [`&ensp;${k === 'bare' ? 'Bare' : k === 'yaw_damper' ? 'Yaw damper' : k === 'fly_real' ? 'Real wiring' : 'Scrambled ' + sid(k)}`,
        sgn(r.mean_rudder_deg, 2), pct(100 * r.sat_frac), sgn(r.heading_change_deg, 1), sgn(r.cross_track_m, 0), n(r.mean_beta_deg, 2)])]))}</div></div>` : ''}`;

  // 3.4 lesions
  h += `<h3 class="sub"><span class="num">3.4</span>Neuron loss (H4)</h3>`;
  if (!lesOK) {
    h += '<p class="noindent">The lesion experiment is still running; this section will be updated.</p>';
  } else {
    const rr0 = LES.rms_r_deg_s.real, bare = LES.reference_deg_s.bare, inc = LES.rms_increase_0_to_80_deg_s, dpf = LES.diff_per_fraction_deg_s;
    const fr = LES.fractions.map((x) => Math.round(100 * x) + '%');
    const negUpTo = (() => { let j = -1; dpf.forEach((d, i) => { if (d.hi < 0 && j === i - 1) j = i; }); return j; })();
    const lost5 = 100 * (rr0[1] - rr0[0]) / (bare - rr0[0]);
    const atBare = rr0.findIndex((x) => r2(x) === r2(bare)), exc = LES.real_seeds_worse_than_bare || [];
    const relayL = LES.wirings.filter((k) => relayIds.includes(sid(k)));
    const tg = LES.targeted, tr = LES.targeted_rms_r_deg_s, hubs = LES.hub_sets_real, eqb = LES.targeted_equals_bare;
    const tlabel = { all_HS: 'All HS cells', T4_only: 'All T4 cells', T5_only: 'All T5 cells' };
    const tlab = (c) => tlabel[c] || `Hubs, top ${parseInt(c.slice(4), 10)}%`;
    h += `<p class="noindent">The lesion experiment asked how the controller degrades as its neurons are silenced, and whether the real wiring degrades more gracefully than scrambled wirings. No flight departed controlled flight: ${LES.n_departed_total} of the ${int(LES.n_flights)} flights of the lesion experiment (${int(LES.n_flights_lesioned)} with neurons silenced), across ${LES.wirings.length} wirings, every lesion condition and ${LES.n_seeds} test seeds. Every AUC in Eq.&nbsp;(${EQ.auc}) is therefore ${n(LES.auc.real, 0)}, and the difference is ${n(LES.auc_diff, 0)} [${n(LES.auc_ci95[0], 0)}, ${n(LES.auc_ci95[1], 0)}]. The bare Cessna with its wings-level hold does not depart in moderate turbulence, so H4 could not be tested as pre-registered; the Wilson 95% upper bound on the departure probability in each cell is ${r2(LES.wilson_upper_max)}.</p>
<p>The pre-registered secondary measure, mean RMS yaw rate, was informative (${fig('lesion')}). As neurons were silenced at random, the real wiring’s mean RMS yaw rate rose from ${r2(rr0[0])}&nbsp;°/s to the bare airframe’s ${r2(bare)}&nbsp;°/s, losing ${pct(lost5)} of its benefit at ${fr[1]} and reaching the bare value (to two decimals) by ${fr[atBare]}; the increase by ${fr[fr.length - 1]} was ${ci(inc.real, 2)}&nbsp;°/s. Its worst mean in any lesion condition was ${r2(LES.max_rms_r_deg_s.real)}&nbsp;°/s, ${LES.ever_worse_than_bare.real ? 'slightly worse than flying with no controller.' : 'never worse than flying with no controller: on average it fails passive.'}${exc.length ? ` This holds for the mean; individual flights could be slightly worse than bare on the same seed (${exc.map((e) => `seed ${e.seed} at ${pct(100 * e.fraction)}: ${n(e.rms_r_deg_s, 3)} against ${n(e.bare_deg_s, 3)}&nbsp;°/s`).join('; ')}).` : ''} Its difference from the mean of the scrambled wirings stayed below zero up to ${fr[negUpTo]} silenced (95% intervals) and approached zero beyond, as all controllers converged on the bare airframe. ${relayL.length && relayL.every((k) => LES.ever_worse_than_bare[k]) ? `Two of the three scrambled wirings in this experiment (${relayL.map(sid).join(' and ')}) are the relay controllers of Section 3.3, and they behaved differently: under some lesions they became worse than the bare airframe, up to ${relayL.map((k) => r2(LES.max_rms_r_deg_s[k])).join(' and ')}&nbsp;°/s, failing active. ` : ''}The neuron-loss replay in the viewer above shows the real wiring at 0%, 40% and 80% random loss and with its HS cells silenced.</p>
<p>Targeted lesions locate the function (${tab('targeted')}). Silencing the ${w(W.n_HS)} HS cells ${eqb.all_HS.real ? 'returned the real wiring exactly to the bare airframe’s RMS yaw rate' : `raised the real wiring to ${r2(tr.all_HS.real)}&nbsp;°/s`}: its whole closed-loop effect runs through HS. Silencing all T4 or all T5 cells left part of the function (${r2(tr.T4_only.real)} and ${r2(tr.T5_only.real)}&nbsp;°/s), and in both cases the real wiring stayed below the scrambled mean (${cis(tg.T4_only.real_minus_shuffles_deg_s, 2)} and ${cis(tg.T5_only.real_minus_shuffles_deg_s, 2)}&nbsp;°/s). With HS silenced the real wiring was ${tg.all_HS.real_minus_shuffles_deg_s.lo > 0 ? 'worse than' : 'not better than'} the scrambled mean (${cis(tg.all_HS.real_minus_shuffles_deg_s, 2)}&nbsp;°/s), because the relay wirings do not depend on HS. The hub lesions are uninformative by construction: the top-betweenness sets of the real wiring contain ${hubs.hubs05.n_DNa02 === W.n_DNa02 ? 'both' : hubs.hubs05.n_DNa02 + ' of the'} DNa02 readout neurons and ${hubs.hubs05.n_HS === W.n_HS ? 'all' : ''} ${hubs.hubs05.n_HS} HS cells, so silencing them removes the readout itself${Object.keys(eqb).filter((c) => c.startsWith('hubs')).every((c) => Object.values(eqb[c]).every(Boolean)) ? ', and every wiring then flew exactly like the bare airframe' : ''}.${!LES.ever_worse_than_bare.real && eqb.all_HS.real ? ' Under random neuron loss, then, the real wiring degrades toward the bare airframe but, on average, not beyond it, and its effect depends entirely on the HS cells.' : ''}</p>
${figure('f-lesion', figLesion(S), `<b>Figure ${FIG.lesion}. Mean RMS yaw rate against the fraction of neurons silenced at random.</b> Mean over ${LES.n_seeds} test seeds (nested sets, the same neurons in every wiring), on the six-degree-of-freedom model. Each wiring keeps its tuned gain. The shaded band is the 95% bootstrap interval for the real wiring; the dash-dotted line is the bare airframe. The yaw damper, for reference, flies at ${r2(LES.reference_deg_s.yaw_damper)}&nbsp;°/s.`,
      tbl(['Silenced', ...LES.wirings.map((k) => (k === 'real' ? 'Real' : 'Scr. ' + sid(k))), 'Real − scr. mean'],
        fr.map((f, i) => [f, ...LES.wirings.map((k) => r2(LES.rms_r_deg_s[k][i])), cis(dpf[i], 2)])))}
<div class="table" id="t-targeted"><p class="tcap"><b>Table ${TAB.targeted}. Targeted lesions.</b> Mean RMS yaw rate (°/s) over ${LES.n_seeds} test seeds on the six-degree-of-freedom model, and the paired difference between the real wiring and the mean of the scrambled wirings with its 95% bootstrap interval. The bare airframe flies at ${r2(bare)}&nbsp;°/s; no flight departed.</p>
<div class="tbl">${tbl(['Silenced', ...LES.wirings.map((k) => (k === 'real' ? 'Real' : 'Scr. ' + sid(k))), 'Real − scr. mean'],
      [{ cells: ['None', ...LES.wirings.map((k) => r2(LES.rms_r_deg_s[k][0])), cis(dpf[0], 2)] },
        ...Object.keys(tg).map((c, i) => ({ mid: i === 0, cells: [tlab(c), ...LES.wirings.map((k) => r2(tr[c][k])), cis(tg[c].real_minus_shuffles_deg_s, 2)] }))])}</div></div>`;
  }

  // 3.5 amplitude & full brain
  const amps = A.amps_deg_s;
  h += `<h3 class="sub"><span class="num">3.5</span>Amplitude dependence and full-brain check (exploratory and planned checks)</h3>
<p class="noindent">Two further analyses asked whether the open-loop results, measured at large amplitude in an isolated subcircuit, carry over to the conditions of the closed-loop flights. The open-loop measurements used ${n(S.protocol.bode_amp_deg_s, 0)}&nbsp;°/s, but in turbulence the bare airframe yaws at about ${[L2, J].map((c) => n(c.controllers.bare.rms_r_deg_s, 0)).join('–')}&nbsp;°/s RMS (${n(L2.controllers.bare.rms_r_deg_s, 1)} on the two-state, ${n(J.controllers.bare.rms_r_deg_s, 1)} on the six-degree-of-freedom model). In an exploratory sweep at 1&nbsp;Hz, coherence fell from ${r2(A.real.coh[amps.length - 1])} at ${n(amps[amps.length - 1], 0)}&nbsp;°/s to ${r2(A.real.coh[0])} at ${n(amps[0], 0)}&nbsp;°/s, while the lock-in gain rose to ${rng([A.real.gain[0], Math.max(...A.real.gain)], 1)}&nbsp;Hz per °/s, partly a noise bias at low coherence and partly nonlinearity. The real wiring ${A.real_coh_rank1_all ? `still had the highest coherence of the ${B.n_shuffles + 1} wirings (real and degree-preserving) at every amplitude` : 'did not keep the highest coherence at every amplitude'}. At rest, DNa02 fired at ${n(S.resting.DNa02_L_hz, 1)}&nbsp;Hz on the left and ${n(S.resting.DNa02_R_hz, 1)}&nbsp;Hz on the right, so at turbulence-level yaw rates a single readout neuron per side operates near threshold and the signal reaching the rudder is sparse and partly rectified. This is consistent with the low loop gain of Section 3.2 and with the improvement seen at a stronger input gain. In a planned sensitivity check across input gains <i>γ</i> from ${n(S.gsens.g_values[0], 1)} to ${n(S.gsens.g_values[S.gsens.g_values.length - 1], 0)}, coherence up to 2&nbsp;Hz stayed above ${n(Math.floor(100 * S.gsens.min_coh_le_2hz) / 100, 2)}.</p>
<p>In a planned check of what the subcircuit leaves out, the real wiring was also run inside the whole v783 brain at ${FB.freqs.map((x) => n(x, 1)).join(', ').replace(/, ([^,]*)$/, ' and $1')}&nbsp;Hz (${FB.n_seeds} seeds). The subcircuit kept the full brain’s phase to within ${n(Math.max(...FB.phase_gap_deg), 1)}°, but its gain was ${rng([Math.min(FB.gain_shortfall_pct[0], FB.gain_shortfall_pct[1]), Math.max(FB.gain_shortfall_pct[0], FB.gain_shortfall_pct[1])], 0)}% lower at ${n(FB.freqs[0], 1)}–${n(FB.freqs[1], 0)}&nbsp;Hz and ${n(FB.gain_shortfall_pct[2], 0)}% lower at ${n(FB.freqs[2], 0)}&nbsp;Hz. The large-amplitude characterization therefore describes the circuit under strong drive: at turbulence-level yaw rates its output is weaker and noisier, and the isolated subcircuit understates the whole brain’s gain.</p>
</section>`;

  // ---------- 4 Discussion
  h += `<section id="s4"><h2 class="sec"><span class="num">4</span>Discussion</h2>
<h3 class="sub"><span class="num">4.1</span>Principal findings</h3>
<p class="noindent">Treated as a flight-control element and driven at large amplitude (${n(S.protocol.bode_amp_deg_s, 0)}&nbsp;°/s), this model of the fly’s visual yaw pathway behaves like a delayed rate sensor; at the few-degrees-per-second yaw rates of turbulence its output becomes noisy and partly rectified (Section 3.5). It tracks yaw rotation coherently from ${n(B.freqs[0], 1)} to several hertz, with the sign a stabilizing reflex needs and an effective delay of about ${n(B.delay_fit.tau_ms, 0)}&nbsp;ms. That property depends on the wiring: rewiring that keeps every neuron’s degree and sign but scrambles which cell types connect on which side removes most of the response. Most of it survives a rewiring that respects cell type and side, but that rewiring leaves the core T4/T5&nbsp;→&nbsp;HS&nbsp;→&nbsp;DNa02 connections intact, so it shows only that the remaining connections contribute a small, consistent increment.</p>
<p>Closed around a rudder, the circuit is a weak but real yaw damper. It improves on the bare airframe${everySeed ? ' on every test seed' : ' on average'}, and it ranks first among the wirings on yaw rate and on sideslip, but the classical yaw damper, tuned by the same rule with the same budget, removes about ${times(J.damper_over_fly_reduction)} times (six-degree-of-freedom) to ${times(L2.damper_over_fly_reduction)} times (two-state) as much yaw motion.${lesOK ? ' Under neuron loss the real wiring fails passive: as it loses neurons it fades toward the bare airframe, whereas relay-type scrambled controllers can fail active and make things worse.' : ''}</p>

<h3 class="sub"><span class="num">4.2</span>What limits the fly controller</h3>
<p class="noindent">Latency is unlikely to be the main limit: at ${n(S.protocol.bode_amp_deg_s, 0)}&nbsp;°/s the circuit’s phase lag near the Dutch-roll frequency is at most ${n(B.measured_lag_near_dutch_roll_deg, 1)}°, and the loop sits far from instability (a gain margin of at least about ${n(T.fly_loop.gm_bound_db, 0)}&nbsp;dB). The evidence points instead to signal strength: in turbulence the eye input is small, the single DNa02 neuron on each side fires near threshold, and the RMS-optimal gain${!T.selected.lin2.fly_real.margin_bound && !T.selected.jsbsim.fly_real.margin_bound ? ' (which the margin rule did not bind)' : ''} is low because higher gain adds more spike noise to the rudder than it removes yaw motion; the exploratory stronger-input arm is consistent with this.</p>

<h3 class="sub"><span class="num">4.3</span>Comparison with prior work and with flies</h3>
<p class="noindent">The effective delay of the model is of the same order as the delays of tens of milliseconds measured for the optomotor response in tethered flight ${cite('theobald')}. Gain and phase cannot yet be compared directly: we found no published yaw Bode plot of the <i>Drosophila</i> optomotor response, and the impulse responses measured from insect descending neurons were obtained with roll rather than yaw stimuli ${cite('leibbrandt')}. Earlier connectome flight-loop projects reported time-domain behaviour, sometimes against shuffled wiring ${cite('clutch', 'leohio', 'flygm')}. The frequency-response, margin, turbulence and lesion measurements reported here complement such demonstrations, and they use the same kind of shuffled-wiring control.</p>

<h3 class="sub"><span class="num">4.4</span>Evaluating connectome controllers</h3>
<p class="noindent">The relay controllers carry a methodological lesson. Our primary metric, RMS yaw rate, together with a pre-registered rule to widen the gain grid, admitted controllers that reduce yaw rate by holding the rudder over and skidding in a steady turn. The margin constraint did not exclude them: measured by injection on the two-state model, where a saturated loop yields only a describing-function margin, they showed gain margins of ${rng([Math.min(...Object.values(J.relay_gm_db)), Math.max(...Object.values(J.relay_gm_db))], 1)}&nbsp;dB.${lesOK ? ' Similarly, the departure-based lesion test could not discriminate between wirings because nothing departed.' : ''} In both cases the pre-registered statistic was blind to a difference that a second measure, sideslip or mean RMS yaw rate, revealed.</p>

<h3 class="sub"><span class="num">4.5</span>Limitations</h3>
<p class="noindent">The conclusions are about this model of the wiring, not about real flies. The whole-brain model was validated on gustatory and grooming circuits, not on visual neurons ${cite('shiu')}, and on connectome release v630; we run it on v783. The model omits gap junctions, which carry much of the coupling between HS and H2 cells, and it treats graded-potential neurons such as T4/T5, HS and VS cells as spiking ones. Our input encoding is piecewise linear (half-wave rectified) in <i>r</i> for each cell and ignores the temporal-frequency tuning and pattern dependence of motion detectors. DNa02’s steering role is established in walking ${cite('rayshubskiy')}, and its role in flight is inferred from connectivity. The subcircuit underestimates the full brain’s gain, by up to ${n(Math.max(...FB.gain_shortfall_pct), 0)}% at ${n(FB.freqs[2], 0)}&nbsp;Hz. Loop margins were measured on the two-state model only and reused for the six-degree-of-freedom model, whose turbulence model differs in spectral shape. With ${T.n_shuffles_closed_loop} scrambled wirings in closed loop the smallest attainable <i>p</i> is ${n(J.p, 3)}, so the closed-loop ranking is suggestive rather than significant at 0.05 on its own. All flights used one aircraft loading (${int(P.weight_lbs)} lb, centre of gravity ${f1(P.cg_y_in)} in right of the centreline from the default seat masses); moving the centre of gravity changes the fin’s moment arm and hence the yaw damping, which was not varied.</p>

<h3 class="sub"><span class="num">4.6</span>Future work</h3>
<p class="noindent">A future protocol should constrain mean rudder, sideslip or heading drift, and measure margins on the plant being flown.${lesOK ? ' A discriminating lesion test would need a harsher plant or turbulence level, and hub sets that exclude the readout neurons.' : ''} More closed-loop rewirings would let the per-wiring ranking reach conventional significance, and a null that rewires the core T4/T5&nbsp;→&nbsp;HS&nbsp;→&nbsp;DNa02 connections neuron by neuron would test whether their neuron-level wiring matters. Both controllers studied here are inner-loop yaw dampers whose washout passes steady turns, so neither tries to hold heading or track. The natural next study places each inside conventional heading and track loops, measures path error and recovery from discrete disturbances, and tests whether other connectome pathways, such as the VS cells that respond to roll and pitch, can drive the ailerons and elevator, each compared with a conventional controller tuned by the same rule.</p>
</section>`;

  // ---------- 5 Conclusions
  h += `<section id="s5"><h2 class="sec"><span class="num">5</span>Conclusions</h2>
<p class="noindent">A spiking model of the fruit fly’s visual yaw pathway, taken directly from the connectome and tested with the methods used to qualify a flight-control law, behaved as a delayed yaw-rate sensor with the stabilizing sign and, closed around a rudder, as a weak yaw damper${rank1 ? ' that ranked first among the wirings tested, although with too few rewirings for that rank alone to be significant' : ''}. Its function depended on its wiring${hsAll ? ' and ran through the HS cells' : ''}${passive ? ', and under random neuron loss its mean RMS yaw rate rose toward the bare airframe’s value without exceeding it' : ''}. The same tests exposed scrambled controllers that scored well on the primary metric by skidding, which argues for judging connectome controllers, like any flight-control law, on several measures and on the airframe they fly.</p>
</section>`;

  // ---------- Declarations
  h += `<section id="decl"><h2 class="sec">Declarations</h2>
<h3 class="sub">Data and code availability</h3>
<p class="noindent">Code, result files and the replays shown in the viewer are available at <a href="${esc(S.meta.repo_url)}">${esc(S.meta.repo_url.replace('https://', ''))}</a>${S.meta.git_hash_closed_loop ? ` (closed-loop results produced at commit <code>${esc(S.meta.git_hash_closed_loop.slice(0, 7))}</code>)` : ''}. The FlyWire connectome and its annotations are available from their original publications ${cite('dorkenwald', 'schlegel')}.</p>
<h3 class="sub">Acknowledgements and licences</h3>
<p class="noindent ack">Wiring data: the FlyWire Consortium, connectome v783, CC&nbsp;BY&nbsp;4.0 ${cite('dorkenwald', 'schlegel')}. Neuron model and connectivity table: Shiu et al., MIT licence ${cite('shiu')}. Flight dynamics: JSBSim ${cite('jsbsim')}.</p>
<h3 class="sub">Competing interests</h3>
<p class="noindent">The author declares no competing interests.</p>
</section>`;

  // ---------- References
  h += `<section id="refs"><h2 class="sec">References</h2><ol class="refs">${refOrder.map((k, i) => `<li id="ref-${i + 1}">${REFS[k]}</li>`).join('')}</ol></section>
<hr class="end"></article>`;
  return h;
}

// ---------------------------------------------------------------- boot
async function boot() {
  let root = document.getElementById('guide');
  if (!root) {
    root = document.createElement('section'); root.id = 'guide';
    const f = document.querySelector('footer'); f ? f.before(root) : document.body.appendChild(root);
  }
  if (!document.querySelector('link[href$="paper.css"]')) {
    const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'paper.css'; document.head.appendChild(l);
  }
  root.classList.add('paper');
  root.setAttribute('aria-label', 'Research paper');
  let S;
  try { S = await loadSummary(); } catch (e) { root.innerHTML = `<article><p>${esc(e.message)}</p></article>`; root.dataset.error = '1'; return; }
  try {
    root.innerHTML = render(S);
    mountAll(root);
    katex(root);
    root.dataset.ready = '1';
  } catch (e) { root.dataset.error = '1'; throw e; }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
