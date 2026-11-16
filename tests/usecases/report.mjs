// report.mjs — self-contained report.html for the 46-case use-case suite.
// Reads artifacts/usecases/results.json (written by run.mjs) and the case
// screenshots that live beside it, and writes artifacts/usecases/report.html.
// Everything is relative: the report sits in the same folder as the PNGs.
//
// Run: node tests/usecases/report.mjs   (after run.mjs)

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART = path.resolve(HERE, '..', '..', 'artifacts', 'usecases');
const results = JSON.parse(fs.readFileSync(path.join(ART, 'results.json'), 'utf8'));

const GROUPS = [
  ['A', 'Administrator creates staff'],
  ['B', 'Permission boundaries'],
  ['C', 'Doctor agendas'],
  ['D', 'Booking by service type + per-person limit'],
  ['E', 'Live regressions from the 2026-09-04 meeting'],
];

const VERDICTS = {
  pass: { label: 'pass', color: '#0a7d33', bg: '#e7f6ec' },
  FAIL: { label: 'FAIL', color: '#b3261e', bg: '#fdeceb' },
  'known-defect': { label: 'known-defect', color: '#8a5a00', bg: '#fdf3e0' },
  blocked: { label: 'blocked', color: '#555', bg: '#eee' },
};

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

const tally = (rows) => rows.reduce((acc, r) => { acc[r.verdict] = (acc[r.verdict] || 0) + 1; return acc; }, {});

function summaryTable() {
  const head = '<tr><th>Group</th><th>pass</th><th>FAIL</th><th>known-defect</th><th>blocked</th></tr>';
  const body = GROUPS.map(([g, name]) => {
    const t = tally(results.filter((r) => r.group === g));
    return `<tr><td><b>${g}</b> · ${esc(name)}</td>`
      + `<td class="v-pass">${t.pass || 0}</td><td class="v-FAIL">${t.FAIL || 0}</td>`
      + `<td class="v-known">${t['known-defect'] || 0}</td><td class="v-blocked">${t.blocked || 0}</td></tr>`;
  }).join('');
  const total = tally(results);
  const totalRow = `<tr class="sum"><td>Total · 46 cases</td><td class="v-pass">${total.pass || 0}</td>`
    + `<td class="v-FAIL">${total.FAIL || 0}</td><td class="v-known">${total['known-defect'] || 0}</td>`
    + `<td class="v-blocked">${total.blocked || 0}</td></tr>`;
  return `<table class="sum-table"><thead>${head}</thead><tbody>${body}${totalRow}</tbody></table>`;
}

// The runner names each capture "<id>-<slug>.png" (plus a "-phone" twin), so the
// report resolves a case's captures by its "<id>-" prefix instead of guessing the
// slug. A renamed case title keeps working; a case with no capture stays empty.
const PNGS = fs.existsSync(ART) ? fs.readdirSync(ART).filter((f) => f.endsWith('.png')) : [];
function capturesFor(id) {
  const p = `${id.toLowerCase()}-`;
  const mine = PNGS.filter((f) => f.startsWith(p));
  return {
    shot: mine.find((f) => !f.endsWith('-phone.png')) || '',
    phone: mine.find((f) => f.endsWith('-phone.png')) || '',
  };
}

function caseRow(r) {
  const v = VERDICTS[r.verdict] || VERDICTS.blocked;
  const { shot, phone } = capturesFor(r.id);
  const hasShot = Boolean(shot);
  const hasPhone = Boolean(phone);
  return `<article class="case" data-id="${esc(r.id)}">
  <div class="case-main">
    <div class="case-head">
      <span class="id-badge">${esc(r.id)}</span>
      <span class="verdict" style="color:${v.color};background:${v.bg}">${esc(v.label)}</span>
      <h3>${esc(r.title)}</h3>
    </div>
    <p class="assertion">${esc(r.assertion)}</p>
    <p class="observed">${esc(r.observed)}</p>
  </div>
  ${hasShot ? `<button type="button" class="thumb" data-shot="${esc(shot)}" data-phone="${hasPhone ? esc(phone) : ''}" data-id="${esc(r.id)}" data-title="${esc(r.title)}">
    <img src="${esc(shot)}" alt="${esc(r.id)} — desktop capture" loading="lazy">
  </button>` : '<div class="thumb thumb--missing">no capture</div>'}
</article>`;
}

const sections = GROUPS.map(([g, name]) => {
  const rows = results.filter((r) => r.group === g);
  const t = tally(rows);
  return `<section class="group" id="group-${g}">
  <h2><span class="group-letter">${g}</span>${esc(name)}
    <span class="group-count">${t.pass || 0} pass · ${t.FAIL || 0} FAIL · ${t['known-defect'] || 0} known-defect · ${t.blocked || 0} blocked</span>
  </h2>
  ${rows.map(caseRow).join('\n')}
</section>`;
}).join('\n');

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Use-case suite — Human Rounds Turnos</title>
<style>
  :root {
    --ink: #1c1c1e; --text-2: #444; --line: #d9d9de; --accent: #0a5c9e;
    --accent-hover: #1273c2; --bg: #fafafa;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px clamp(16px, 4vw, 48px) 64px;
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: var(--ink); background: var(--bg);
    text-wrap: pretty; max-width: 1180px;
  }
  h1 { font-size: 26px; margin: 0 0 4px; text-wrap: balance; }
  .lede { color: var(--text-2); margin: 0 0 20px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { color: var(--accent-hover); }
  .sum-table { border-collapse: collapse; margin: 0 0 8px; background: #fff; }
  .sum-table th, .sum-table td { border: 1px solid var(--line); padding: 8px 14px; text-align: right; font-size: 14px; }
  .sum-table th { background: #f1f1f4; }
  .sum-table td:first-child, .sum-table th:first-child { text-align: left; }
  .sum-table tr.sum td { font-weight: 700; background: #f7f7fa; }
  .v-pass { color: #0a7d33; } .v-FAIL { color: #b3261e; font-weight: 700; }
  .v-known { color: #8a5a00; } .v-blocked { color: #555; }
  .group { margin-top: 36px; }
  .group h2 { font-size: 20px; margin: 0 0 14px; display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .group-letter { display: inline-block; min-width: 34px; height: 34px; line-height: 34px; text-align: center; background: var(--ink); color: #fff; border-radius: 8px; font-size: 18px; }
  .group-count { font-size: 14px; color: var(--text-2); font-weight: 400; }
  .case { display: flex; gap: 18px; background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 12px; align-items: flex-start; }
  .case-main { flex: 1 1 auto; min-width: 0; }
  .case-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
  .case-head h3 { font-size: 16px; margin: 0; font-weight: 600; }
  .id-badge { font: 700 15px/1 ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--ink); color: #fff; border-radius: 7px; padding: 7px 10px; letter-spacing: .5px; }
  .verdict { font: 700 13px/1 ui-monospace, SFMono-Regular, Menlo, monospace; border-radius: 999px; padding: 7px 12px; }
  .assertion { margin: 0 0 6px; color: var(--ink); }
  .observed { margin: 0; color: var(--text-2); font-size: 14px; overflow-wrap: break-word; }
  .thumb { flex: 0 0 190px; border: 1px solid var(--line); border-radius: 8px; padding: 0; background: #f1f1f4; cursor: zoom-in; overflow: hidden; }
  .thumb img { display: block; width: 100%; height: auto; }
  .thumb--missing { display: flex; align-items: center; justify-content: center; min-height: 90px; color: var(--text-2); font-size: 14px; }
  .lightbox { position: fixed; inset: 0; background: rgba(12, 12, 14, .88); display: none; align-items: center; justify-content: center; z-index: 40; padding: 20px; }
  .lightbox.open { display: flex; }
  .lb-inner { display: flex; gap: 18px; align-items: flex-start; max-width: min(1240px, 96vw); max-height: 92vh; }
  .lb-inner img.desktop { max-width: min(880px, 68vw); max-height: 86vh; object-fit: contain; background: #fff; }
  .lb-inner img.phone { width: 220px; height: auto; max-height: 86vh; object-fit: contain; background: #fff; }
  .lb-cap { position: fixed; left: 0; right: 0; bottom: 0; display: flex; gap: 16px; align-items: center; justify-content: center; padding: 12px; color: #fff; }
  .lb-cap .id-badge { background: #fff; color: var(--ink); }
  .lb-cap .t { font-size: 15px; }
  .lb-btn { position: fixed; top: 50%; transform: translateY(-50%); font-size: 26px; line-height: 1; background: rgba(255,255,255,.12); color: #fff; border: 1px solid rgba(255,255,255,.4); border-radius: 10px; padding: 14px 18px; cursor: pointer; }
  .lb-btn:hover { background: rgba(255,255,255,.25); }
  .lb-prev { left: 18px; } .lb-next { right: 18px; }
  .lb-close { position: fixed; top: 14px; right: 18px; font-size: 15px; background: none; border: 1px solid rgba(255,255,255,.4); color: #fff; border-radius: 8px; padding: 8px 12px; cursor: pointer; }
  .lb-close:hover { background: rgba(255,255,255,.2); }
  @media (max-width: 720px) {
    .case { flex-direction: column; }
    .thumb { flex-basis: auto; width: 100%; }
    .lb-inner { flex-direction: column; overflow: auto; }
    .lb-inner img.desktop { max-width: 92vw; }
    .lb-inner img.phone { width: 180px; }
  }
</style>
</head>
<body>
<h1>Use-case suite — 46 acceptance cases</h1>
<p class="lede">Human Rounds · Turnos · run against the local server, driven by <code>tests/usecases/run.mjs</code>. Click a capture to open it; ← → moves between cases.</p>
${summaryTable()}
${sections}
<div class="lightbox" id="lightbox">
  <button type="button" class="lb-btn lb-prev" id="lb-prev" aria-label="Previous case">‹</button>
  <div class="lb-inner">
    <img class="desktop" id="lb-desktop" alt="Desktop capture">
    <img class="phone" id="lb-phone" alt="Phone capture">
  </div>
  <button type="button" class="lb-btn lb-next" id="lb-next" aria-label="Next case">›</button>
  <button type="button" class="lb-close" id="lb-close">Close</button>
  <div class="lb-cap"><span class="id-badge" id="lb-id"></span><span class="t" id="lb-title"></span></div>
</div>
<script>
(function () {
  var buttons = [].slice.call(document.querySelectorAll('.thumb'));
  var box = document.getElementById('lightbox');
  var dImg = document.getElementById('lb-desktop');
  var pImg = document.getElementById('lb-phone');
  var capId = document.getElementById('lb-id');
  var capTitle = document.getElementById('lb-title');
  var idx = -1;
  function show(i) {
    idx = (i + buttons.length) % buttons.length;
    var b = buttons[idx];
    dImg.src = b.getAttribute('data-shot');
    var phone = b.getAttribute('data-phone');
    pImg.style.display = phone ? '' : 'none';
    if (phone) pImg.src = phone;
    capId.textContent = b.getAttribute('data-id');
    capTitle.textContent = b.getAttribute('data-title');
    box.classList.add('open');
  }
  function hide() { box.classList.remove('open'); }
  buttons.forEach(function (b, i) { b.addEventListener('click', function () { show(i); }); });
  document.getElementById('lb-prev').addEventListener('click', function () { show(idx - 1); });
  document.getElementById('lb-next').addEventListener('click', function () { show(idx + 1); });
  document.getElementById('lb-close').addEventListener('click', hide);
  box.addEventListener('click', function (e) { if (e.target === box) hide(); });
  document.addEventListener('keydown', function (e) {
    if (!box.classList.contains('open')) return;
    if (e.key === 'Escape') hide();
    if (e.key === 'ArrowLeft') show(idx - 1);
    if (e.key === 'ArrowRight') show(idx + 1);
  });
})();
</script>
</body>
</html>
`;

const out = path.join(ART, 'report.html');
fs.writeFileSync(out, html);
const kb = (fs.statSync(out).size / 1024).toFixed(1);
console.log(`report: artifacts/usecases/report.html (${kb} KB, ${results.length} cases)`);
