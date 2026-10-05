// Export the web paper (app/guide.js rendered into #guide) to submission formats in paper/:
// paper.pdf (Chrome print), figures/fig*.{svg,pdf,png}, paper.html (sanitized DOM), main.tex + main.pdf (pandoc + Tectonic),
// paper.docx (pandoc), references.bib (from guide.js REFS), abstract.txt, README.md.
// Everything comes from the rendered DOM, so the formats cannot drift from the web paper.
// usage: node scripts/export_paper.mjs     (needs Chrome, python3, pandoc, rsvg-convert, tectonic, pdfinfo, pdftotext)
import { spawn, execFileSync } from 'node:child_process';
import { writeFileSync, readFileSync, mkdirSync, mkdtempSync, existsSync, copyFileSync, readdirSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import assert from 'node:assert/strict';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(ROOT, 'app'), OUT = join(ROOT, 'paper'), FIGS = join(OUT, 'figures');
const TMP = mkdtempSync(join(tmpdir(), 'export-paper-'));
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const SITE = 'https://fly.aryawijaya.com';
const MATH = 'html+tex_math_dollars+tex_math_single_backslash';
const PAGE_W_IN = 8.27, PAGE_H_IN = 11.69, MARGIN_X_IN = 0.71, MARGIN_Y_IN = 0.75;   // A4
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const run = (cmd, args, opts = {}) => execFileSync(cmd, args, { encoding: 'utf8', maxBuffer: 1 << 28, ...opts });
const pages = (f) => +run('pdfinfo', [f]).match(/^Pages:\s+(\d+)/m)[1];
mkdirSync(FIGS, { recursive: true });

const freePort = () => new Promise((r) => { const s = createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });

// ---------------------------------------------------------------- in-page extraction (runs inside Chrome)
function extract() {
  const art = document.querySelector('#guide article');
  const tex = (el) => el.querySelector('annotation[encoding="application/x-tex"]').textContent.trim();
  // figures: inline the computed styles so each SVG renders standalone
  const PROPS = ['fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linejoin', 'stroke-linecap', 'stroke-opacity',
    'opacity', 'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor'];
  const figs = [...art.querySelectorAll('figure')].map((f) => {
    const svg = f.querySelector('.plot svg'), c = svg.cloneNode(true);
    const src = [svg, ...svg.querySelectorAll('*')], dst = [c, ...c.querySelectorAll('*')];
    src.forEach((el, i) => {
      const cs = getComputedStyle(el);
      dst[i].setAttribute('style', PROPS.map((p) => `${p}:${cs.getPropertyValue(p)}`).join(';'));
      if (el.classList.contains('hit')) dst[i].setAttribute('data-rm', '');
      dst[i].removeAttribute('class');
    });
    c.querySelectorAll('title, [data-rm]').forEach((e) => e.remove());
    const vb = c.getAttribute('viewBox').split(' ');
    c.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); c.setAttribute('width', vb[2]); c.setAttribute('height', vb[3]); c.removeAttribute('role');
    return { svg: new XMLSerializer().serializeToString(c), label: svg.getAttribute('aria-label') };
  });
  // sanitized paper
  const a = art.cloneNode(true);
  a.querySelectorAll('nav.toc, details.data, hr.end').forEach((e) => e.remove());
  a.querySelector('.title').classList.add('front'); a.querySelector('.abstract').classList.add('front');
  a.querySelectorAll('.eq').forEach((e) => {
    const p = document.createElement('p'); p.className = 'eq';
    p.textContent = `\\[${tex(e)}\\tag{${e.querySelector('.eq-no').textContent.replace(/[()]/g, '')}}\\]`;
    e.replaceWith(p);
  });
  a.querySelectorAll('p.noindent').forEach((p) => { const d = document.createElement('div'); d.className = 'noindent'; p.replaceWith(d); d.append(p); });   // pandoc drops <p> classes
  a.querySelectorAll('.katex').forEach((k) => k.replaceWith(`$${tex(k)}$`));   // any inline math
  a.querySelectorAll('figure').forEach((f, i) => {
    const img = document.createElement('img');
    img.setAttribute('src', `figures/fig${i + 1}.pdf`); img.setAttribute('width', '100%'); img.setAttribute('alt', figs[i].label);
    f.querySelector('.plot').replaceWith(img);
  });
  a.querySelectorAll('.table').forEach((t) => {   // caption inside the table, one <thead>
    const cap = t.querySelector('.tcap'), tb = t.querySelector('table'), c = document.createElement('caption');
    c.innerHTML = cap.innerHTML; tb.prepend(c); cap.remove();
    const th = tb.querySelectorAll('thead'); if (th.length > 1) { th[0].append(...th[1].children); th[1].remove(); }
  });
  const dl = art.querySelectorAll('.title .dateline');
  return {
    figs, html: a.innerHTML, title: art.querySelector('h1').textContent.trim(), author: art.querySelector('.author').textContent.trim(),
    date: dl[0].textContent.trim(), codeUrl: dl[1].querySelector('a').href,
    abstractHtml: [...art.querySelectorAll('.abstract p')].map((p) => p.outerHTML).join(''),
    abstractText: [...art.querySelectorAll('.abstract p')].map((p) => p.textContent).join('\n\n'),
    table3: [...art.querySelectorAll('#t-cl .tcap, #t-cl th, #t-cl td')].map((c) => c.textContent).join(' '), nTables: art.querySelectorAll('.table').length,
    refs: [...art.querySelectorAll('ol.refs li')].map((li) => li.textContent),
    nEqBody: art.querySelectorAll('.eq-body').length, nKatexDisplay: art.querySelectorAll('.eq-body .katex').length,
    hasDollar: art.textContent.includes('$'),
  };
}

// ---------------------------------------------------------------- 1. serve app/, render in headless Chrome
const port = await freePort();
const server = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1'], { cwd: APP, stdio: 'ignore' });
const cdp = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${cdp}`, '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
  '--hide-scrollbars', `--user-data-dir=${mkdtempSync(join(TMP, 'prof-'))}`, 'about:blank'], { stdio: 'ignore' });
let ex, pdf;
try {
  for (let k = 0; k < 50; k++) { try { if ((await fetch(`http://127.0.0.1:${port}/guide.js`)).ok) break; } catch { await sleep(200); } }
  let tabs;
  for (let k = 0; k < 50 && !tabs; k++) { try { tabs = await (await fetch(`http://127.0.0.1:${cdp}/json`)).json(); } catch { await sleep(200); } }
  const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
  const send = (method, params = {}) => new Promise((r, j) => { const i = ++id; pending.set(i, (d) => (d.error ? j(new Error(`${method}: ${d.error.message}`)) : r(d.result))); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (e) => { const r = await send('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result.value; };
  await send('Page.enable'); await send('Network.enable');
  await send('Network.setBlockedURLs', { urls: ['*gc.zgo.at*'] });   // no analytics hit from the export
  // lay the page out at the PDF's content width in print media, so figures are drawn at the size they print
  await send('Emulation.setDeviceMetricsOverride', { width: Math.round((PAGE_W_IN - 2 * MARGIN_X_IN) * 96), height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Emulation.setEmulatedMedia', { media: 'print', features: [{ name: 'prefers-color-scheme', value: 'light' }] });
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/?theme=light#guide` });
  const t0 = Date.now();
  const READY = `(() => { const g = document.getElementById('guide'); if (!g || g.dataset.error) return 'error';
    if (!g.dataset.ready) return ''; const b = g.querySelectorAll('.eq-body');
    return b.length && [...b].every((e) => e.querySelector('.katex')) && g.querySelectorAll('figure').length === g.querySelectorAll('figure .plot svg').length
      && document.fonts.status === 'loaded' ? 'ok' : ''; })()`;
  let st = '';
  while (Date.now() - t0 < 60000 && !(st = await ev(READY))) await sleep(250);
  assert.equal(st, 'ok', `paper or KaTeX did not finish rendering (${st || 'timeout'})`);
  await ev('document.fonts.ready.then(() => 1)'); await sleep(800);
  ex = await ev(`(${extract.toString()})()`);
  await ev(`document.title = document.querySelector("#guide h1").textContent.trim()`);   // Chrome writes document.title as the PDF Title
  const p = await send('Page.printToPDF', { printBackground: true, paperWidth: PAGE_W_IN, paperHeight: PAGE_H_IN,
    marginTop: MARGIN_Y_IN, marginBottom: MARGIN_Y_IN, marginLeft: MARGIN_X_IN, marginRight: MARGIN_X_IN, displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: '<div style="width:100%;text-align:center;font:8pt Georgia,serif;color:#444"><span class="pageNumber"></span></div>' });
  pdf = Buffer.from(p.data, 'base64');
  ws.close();
} finally { chrome.kill(); server.kill(); }
writeFileSync(join(OUT, 'paper.pdf'), pdf);
assert.equal(ex.nKatexDisplay, ex.nEqBody, 'an equation fell back to HTML');
assert(!ex.hasDollar, 'a literal $ in the text would be read as math by pandoc');

// ---------------------------------------------------------------- 2. figures: standalone SVG -> PDF (vector) and 300-dpi PNG
for (const f of readdirSync(FIGS)) rmSync(join(FIGS, f));
ex.figs.forEach(({ svg }, i) => {
  // the web font is not installed locally; STIX Two Text (macOS) has lining figures and the Greek/math glyphs used
  const s = svg.replaceAll('Georgia, serif', 'STIX Two Text, Times New Roman, serif');
  const base = join(FIGS, `fig${i + 1}`);
  writeFileSync(base + '.svg', '<?xml version="1.0" encoding="UTF-8"?>\n' + s + '\n');
  run('rsvg-convert', ['-f', 'pdf', '-o', base + '.pdf', base + '.svg']);
  run('rsvg-convert', ['-f', 'png', '-z', String(300 / 96), '-o', base + '.png', base + '.svg']);
});

// ---------------------------------------------------------------- 3. paper.html
const html = `<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>${ex.title}</title>\n</head>\n<body>\n<article>\n${ex.html}\n</article>\n</body>\n</html>\n`;
writeFileSync(join(OUT, 'paper.html'), html);

// ---------------------------------------------------------------- 4. references.bib from guide.js REFS, in citation order
const src = readFileSync(join(APP, 'guide.js'), 'utf8');
const REFS = Function(`return {${src.slice(src.indexOf('const REFS = {') + 14, src.indexOf('\n};', src.indexOf('const REFS = {')))}}`)();
const plain = (s) => s.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
const order = ex.refs.map((t) => {
  const k = Object.keys(REFS).filter((key) => plain(REFS[key]).slice(0, 60) === t.replace(/\s+/g, ' ').trim().slice(0, 60));
  assert.equal(k.length, 1, `reference not matched: ${t.slice(0, 60)}`);
  return k[0];
});
const bibTex = (s) => s.replace(/<a [^>]*>.*?<\/a>/g, '').replace(/<i>(.*?)<\/i>/g, '\\emph{$1}').replace(/<[^>]+>/g, '')
  .replace(/&/g, '\\&').replace(/–/g, '--').replace(/’/g, "'").replace(/§/g, '\\S{}').trim();
const bibAuthors = (a) => a.replace(/, et al\.$/, ', others').split(', ').map((x) => {
  if (x === 'others') return 'others';
  const m = x.match(/^(.+) ([A-Z]{1,3})$/);
  return m ? `${m[1]}, ${m[2].split('').join('. ')}.` : `{${x}}`;
}).join(' and ');
const keys = order.map((k) => k + REFS[k].match(/\((?:[^)]*?)(\d{4})\)/)[1]);
const bib = order.map((k, i) => {
  const s = REFS[k], m = s.match(/^(.*?) \(([^)]*)\)\. (.*)$/);
  const year = m[2].match(/\d{4}/)[0], links = [...m[3].matchAll(/<a href="([^"]+)">/g)].map((q) => q[1].replace(/&amp;/g, '&'));
  const doi = links.find((u) => u.includes('doi.org/')), url = links.find((u) => !u.includes('doi.org/'));
  const body = m[3].replace(/<a [^>]*>.*?<\/a>/g, '').replace(/;?\s*$/, '').replace(/\s*\bdoi:\s*$/, '').trim();
  const parts = body.split(/\.\s+(?=<i>|[A-Za-z0-9])/);
  const title = parts[0].replace(/\.$/, ''), rest = parts.slice(1).join('. ').replace(/\.$/, '');
  const f = { author: bibAuthors(m[1]), year, title: `{${bibTex(title)}}` };
  const j = rest.match(/^<i>([^<]+)<\/i>\s*(\d+)(?:\((\d+)\))?:(.+)$/);
  let type = 'misc';
  if (j) { type = 'article'; Object.assign(f, { journal: bibTex(j[1]), volume: j[2], ...(j[3] ? { number: j[3] } : {}), pages: bibTex(j[4]).replace(/(\d)--(\d)/, '$1--$2') }); }
  else if (/^<i>[^<]*Forum<\/i>/.test(rest)) { type = 'inproceedings'; const [b, ...loc] = rest.split('</i>'); Object.assign(f, { booktitle: bibTex(b), address: bibTex(loc.join('').replace(/^,\s*/, '')) }); }
  else if (rest) f.note = bibTex(rest);
  if (m[2] !== year) f.note = [f.note, `Dated ${m[2]}`].filter(Boolean).join('. ');
  if (doi) f.doi = doi.replace('https://doi.org/', '');
  if (url) f.url = url;
  return `@${type}{${keys[i]},\n${Object.entries(f).map(([a, b]) => `  ${a.padEnd(9)} = {${b}}`).join(',\n')}\n}\n`;
}).join('\n');
writeFileSync(join(OUT, 'references.bib'), `% Generated by scripts/export_paper.mjs from app/guide.js (REFS), in order of first citation.\n\n${bib}`);

// ---------------------------------------------------------------- 5. pandoc: main.tex and paper.docx
const lua = `local KEYS = {${keys.map((k) => `'${k}'`).join(', ')}}
local latex = FORMAT:match('latex')
function Span(s)
  if s.classes:includes('num') then return {pandoc.Str(pandoc.utils.stringify(s)), latex and pandoc.RawInline('latex', '\\\\quad ') or pandoc.Space()} end
  if latex and s.classes:includes('cite') then
    local ks = {}
    s:walk({Link = function(l) local i = tonumber(l.target:match('#ref%-(%d+)')); if i then ks[#ks + 1] = KEYS[i] end end})
    return pandoc.RawInline('latex', '\\\\cite{' .. table.concat(ks, ',') .. '}')
  end
end
function Link(l)   -- URL-like link text may break at / and . (keeps the text as written)
  local t = pandoc.utils.stringify(l.content)
  if latex and not t:find('%s') and t:find('[/.]') and #t > 20 and l.target:match('^https?:') then
    return pandoc.RawInline('latex', '\\\\href{' .. l.target:gsub('([%%#])', '\\\\%1') .. '}{\\\\nolinkurl{' .. t .. '}}')
  end
end
function Div(d)
  if d.classes:includes('front') then return {} end
  if latex and d.classes:includes('noindent') and d.content[1] and d.content[1].content then
    d.content[1].content:insert(1, pandoc.RawInline('latex', '\\\\noindent '))
    return d.content
  end
  if latex and d.identifier == 'refs' then
    local out = {pandoc.RawBlock('latex', '\\\\begin{thebibliography}{' .. #KEYS .. '}')}
    d:walk({OrderedList = function(ol) for i, item in ipairs(ol.content) do
      out[#out + 1] = pandoc.RawBlock('latex', '\\\\bibitem{' .. KEYS[i] .. '}'); for _, b in ipairs(item) do out[#out + 1] = b end end end})
    out[#out + 1] = pandoc.RawBlock('latex', '\\\\end{thebibliography}')
    return out
  end
end
function Table(t)   -- booktabs look: first column left, numbers right, spanning headers centred
  for i = 2, #t.colspecs do t.colspecs[i] = {pandoc.AlignRight, t.colspecs[i][2]} end
  for _, row in ipairs(t.head.rows) do for _, c in ipairs(row.cells) do if c.col_span > 1 then c.alignment = pandoc.AlignCenter end end end
  return t
end
`;
writeFileSync(join(TMP, 'paper.lua'), lua);
const abstractMd = run('pandoc', ['-f', 'html', '-t', 'markdown-smart', '--wrap=none'], { input: ex.abstractHtml }).trim();
const meta = (extra) => { const f = join(TMP, `meta-${Math.random().toString(36).slice(2)}.json`); writeFileSync(f, JSON.stringify({ title: ex.title, date: ex.date, abstract: abstractMd, ...extra })); return f; };
const common = ['-f', MATH, '--lua-filter', join(TMP, 'paper.lua'), '--shift-heading-level-by=-1'];
run('pandoc', ['paper.html', ...common, '-t', 'latex', '--template', 'template.tex', '--metadata-file', meta({ author: [ex.author], 'code-url': ex.codeUrl }), '-o', 'main.tex'], { cwd: OUT });
// Word: PNG figures, and the equation number written into the equation (pandoc drops \tag in Word math)
const docxHtml = html.replace(/figures\/fig(\d+)\.pdf/g, 'figures/fig$1.png').replace(/\\tag\{([^}]+)\}\\\]/g, '\\qquad ($1)\\]');
run('pandoc', ['-', ...common, '-t', 'docx', '--resource-path', OUT, '--metadata-file', meta({ author: [`${ex.author}, Independent researcher`], subtitle: `Code and data: ${ex.codeUrl}` }), '-o', join(OUT, 'paper.docx')], { input: docxHtml });

// every non-ASCII character in main.tex must have a mapping in the template (else pdfLaTeX fails / Tectonic drops glyphs)
// combining accents (x̄ in the text) become math accents
const tex = readFileSync(join(OUT, 'main.tex'), 'utf8').replace(/(\w)\u0304/gu, '\\ensuremath{\\bar{$1}}').replace(/(\w)\u0302/gu, '\\ensuremath{\\hat{$1}}');
writeFileSync(join(OUT, 'main.tex'), tex);
const tpl = readFileSync(join(OUT, 'template.tex'), 'utf8');
const mapped = new Set([...tpl.matchAll(/\\newunicodechar\{(.)\}/gu)].map((q) => q[1]));
const unmapped = [...new Set(tex.match(/[^\x00-\x7f]/gu) || [])].filter((c) => !mapped.has(c) && !/[À-ÿ]/.test(c));
assert.deepEqual(unmapped, [], `characters without a \\newunicodechar in template.tex: ${unmapped.map((c) => `${c} U+${c.codePointAt(0).toString(16)}`).join(', ')}`);

// ---------------------------------------------------------------- 6. compile main.tex with Tectonic
const texOut = join(TMP, 'tex');
mkdirSync(texOut);
let texOk = true, log = '';
try { run('tectonic', ['--keep-logs', '--chatter', 'minimal', '-o', texOut, 'main.tex'], { cwd: OUT, stdio: ['ignore', 'pipe', 'pipe'] }); } catch (e) { texOk = false; console.error(e.stdout, e.stderr); }
if (existsSync(join(texOut, 'main.log'))) log = readFileSync(join(texOut, 'main.log'), 'utf8');
if (texOk) copyFileSync(join(texOut, 'main.pdf'), join(OUT, 'main.pdf'));
const overfull = [...log.matchAll(/Overfull \\hbox \(([\d.]+)pt too wide\) (.*)/g)].map((q) => [+q[1], q[2]]);
const missing = [...log.matchAll(/Missing character: (.*)/g)].map((q) => q[1]);

// ---------------------------------------------------------------- 7. abstract.txt
const ASCII = { '−': '-', '–': '-', '’': "'", '‘': "'", '“': '"', '”': '"', '\u00a0': ' ', '\u2009': ' ', '\u202f': ' ', '≤': '<=', '≥': '>=', '→': '->', '×': 'x', '±': '+/-' };
const abstract = ex.abstractText.replace(/[−–’‘“”\u00a0\u2009\u202f≤≥→×±]/g, (c) => ASCII[c]).replace(/[ \t]+/g, ' ').replace(/ ?\n ?/g, '\n').trim();
writeFileSync(join(OUT, 'abstract.txt'), abstract + '\n');

// ---------------------------------------------------------------- 8. README.md
const nPages = texOk ? pages(join(OUT, 'main.pdf')) : 0, nPagesWeb = pages(join(OUT, 'paper.pdf')), nFig = ex.figs.length;
writeFileSync(join(OUT, 'README.md'), `# Paper: submission files

Generated from the web paper (app/guide.js rendered in headless Chrome) by \`node scripts/export_paper.mjs\`, which regenerates every file here except \`template.tex\` (the hand-written LaTeX preamble).
\`paper.pdf\` is the print of the web page (${nPagesWeb} pages); \`main.tex\` (pandoc + \`template.tex\`, pdfLaTeX-compatible) compiles to \`main.pdf\` (${nPages} pages) with \`figures/fig*.pdf\`; \`paper.docx\` is the Word version with \`figures/fig*.png\` (300 dpi) and native equations; \`paper.html\` is the sanitized DOM (TeX math, figure images) that pandoc converts; \`references.bib\` holds the reference list; \`abstract.txt\` is the plain-text abstract (${[...abstract].length} characters).

## Suggested arXiv metadata

- **Title:** ${ex.title}
- **Author:** ${ex.author} (Independent researcher)
- **Abstract:** contents of \`abstract.txt\`
- **Comments:** ${nPages} pages, ${nFig} figures, ${ex.nTables} tables; code and interactive replay at ${SITE}
- **Licence:** CC BY 4.0
`);

// ---------------------------------------------------------------- 9. self-checks (all run; any failure sets a non-zero exit code)
const txt = (f) => run('pdftotext', ['-layout', f, '-']).replace(/\u2212/g, '-');
const nums = (s) => [...new Set(s.replace(/\u2212/g, '-').match(/\d+\.\d+/g))];
const want = [...nums(ex.abstractText), ...nums(ex.table3)];
const missNums = (f) => { const t = txt(join(OUT, f)); return want.filter((v) => !t.includes(v)); };
const nChars = [...abstract].length, figFiles = (ext) => readdirSync(FIGS).filter((f) => f.endsWith(ext)).length;
const checks = [
  [texOk, 'main.tex compiled with Tectonic'],
  [nPagesWeb > 3 && nPages > 3, `paper.pdf (${nPagesWeb}) and main.pdf (${nPages}) have more than 3 pages`],
  [nFig > 0 && [figFiles('.svg'), figFiles('.pdf'), figFiles('.png')].every((k) => k === nFig), `${nFig} figures in the DOM = SVG/PDF/PNG files`],
  [(html.match(/<figure/g) || []).length === nFig && (tex.match(/\\includegraphics/g) || []).length === nFig, 'same figure count in paper.html and main.tex'],
  [nChars <= 1920, `abstract.txt is ${nChars} characters (arXiv limit 1920)`],
  [!tex.includes('\u2014') && !abstract.includes('\u2014'), 'no em dash in main.tex or abstract.txt'],
  [missing.length === 0, `no missing glyphs in main.pdf (${missing.length})`],
  [overfull.every(([w]) => w <= 5), `no overfull box wider than 5pt (${overfull.map(([w, at]) => `${w.toFixed(1)}pt ${at}`).join('; ') || 'none'})`],
  ...['paper.pdf', 'main.pdf'].map((f) => { const m = texOk || f === 'paper.pdf' ? missNums(f) : want; return [m.length === 0, `${f} contains all ${want.length} numbers of the abstract and Table 3${m.length ? ` (missing ${m})` : ''}`]; }),
];
for (const [ok, msg] of checks) console.log(`${ok ? 'ok  ' : 'FAIL'} ${msg}`);
rmSync(TMP, { recursive: true, force: true });
assert(checks.every(([ok]) => ok), 'self-check failed');
console.log('self-check passed');
