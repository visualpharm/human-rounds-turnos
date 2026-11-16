// Labels that name a value or a control in the panel (the caption under a
// report tile's number, the «Período»/«Centro» filter labels, form field
// labels, the column name a table row shows as a card on the phone) are key
// information: they take the dark --hint-ink of tokens.css, never the
// translucent --text-2/--text-3 scale, which the humanrounds theme renders as
// light grey (the product owner 2026-11-04: panel help text in dark grey; the translucent
// scale is only for descriptive small print of the public site).
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const read = (p) => fs.readFileSync(new URL('../' + p, import.meta.url), 'utf8');

const platformDir = new URL('../static/platform/', import.meta.url);
const PANEL_SOURCES = ['static/root/admin-direct.css', 'static/platform/admin-queue.css'].concat(
  fs.readdirSync(platformDir)
    .filter((f) => f.startsWith('admin-') && (f.endsWith('.mjs') || f.endsWith('.css')))
    .map((f) => 'static/platform/' + f),
);

// Every CSS rule `selector{body}` of a source (CSS files and the CSS strings
// the panel modules inject).
function rules(src) {
  const out = [];
  const re = /([^{}"'`]+)\{([^{}]*)\}/g;
  src = src.replace(/\/\*[\s\S]*?\*\//g, ' ');
  let m;
  while ((m = re.exec(src))) out.push({ sel: m[1].trim(), body: m[2] });
  return out;
}

// The last compound of each selector names a label of a value or a control.
// Also a table's column header (`th`), the key of a key/value row (`.k`,
// `-k`, «Sistema»'s `-lab`), a calendar's weekday header (`-dow`) and a chart
// legend (`-legend`). A rule whose content is `attr(data-label)` writes a
// column name on a phone card, whatever its selector.
const LABEL_TARGET = /(^|[\s>+~])(label|th(?![\w-])|\.k(?![\w-])|[\w.#-]*(-lbl|-label|-lab|-ctl|-k|-dow|-legend)(?![\w-])|[\w.#-]*\[data-label\]::before)[^\s>+~]*$/;
const TRANSLUCENT = /(^|;)\s*color:var\(--text-[23]\b/;
// Deliberately faint: the «Libre» word of an empty slot row in «Pacientes de
// hoy» belongs to the row that is dimmed as a whole (hour and text alike).
const DIMMED_ON_PURPOSE = new Set(['.queue-free-k']);

test('panel labels never use the translucent --text-2/--text-3 scale as their colour', () => {
  const offenders = [];
  for (const file of PANEL_SOURCES) {
    for (const { sel, body } of rules(read(file))) {
      if (DIMMED_ON_PURPOSE.has(sel)) continue;
      const target = sel.split(',').some((s) => LABEL_TARGET.test(s.trim())) || /attr\(data-label\)/.test(body);
      if (target && TRANSLUCENT.test(body)) offenders.push(file + ': ' + sel);
    }
  }
  assert.deepEqual(offenders, [], 'use color:var(--hint-ink,var(--text-2)) for these labels');
});

// The places fixed on 2026-11-07, named so a rename does not silently drop
// them out of the generic check above.
const PINNED = [
  ['static/root/admin-direct.css', '.rep-kpi-label'],
  ['static/root/admin-direct.css', '.rep-controls label'],
  ['static/root/admin-direct.css', '.rep-bar-sub'],
  ['static/root/admin-direct.css', '.rep-sumcard-find'],
  ['static/root/admin-direct.css', '.bulk-reject-field label'],
  ['static/root/admin-direct.css', '.overturn-ctl'],
  ['static/root/admin-direct.css', '.usr-totp-lbl'],
  ['static/root/admin-direct.css', '.pat-rank-lbl'],
  ['static/root/admin-direct.css', '#sec-usuarios .cnt-table td[data-label]::before'],
  ['static/root/admin-direct.css', '.cnt-row>label'],
  ['static/root/admin-direct.css', '.cnt-cupos label'],
  ['static/root/admin-direct.css', '.cnt-centro-pair>label,.cnt-franja-svc label'],
  // 2026-11-08: the doctor cards are gone (one flat list); the doctor named
  // on a free hour of that list is the label left on the screen.
  ['static/platform/admin-queue.css', '.queue-free-who'],
  ['static/root/admin-direct.css', '.solmod-label'],
  ['static/platform/admin-patients.mjs', '#sec-pacientes .pat-table td[data-label]::before'],
  ['static/platform/admin-solicitudes.mjs', '.sol-lbl'],
  ['static/platform/admin-solicitudes.mjs', '.sol-res .overturn-ctl'],
  ['static/platform/admin-settings.mjs', '.set-row>label'],
  ['static/platform/admin-hours-editor.mjs', '.hor-lbl'],
  ['static/platform/admin-create-appointment.mjs', '.ct-lbl'],
  ['static/platform/admin-create-appointment.mjs', '.ct-sobre-lbl'],
  // 2026-11-08: «Sistema» row labels and card summaries, then the same sweep
  // over every panel screen.
  ['static/platform/admin-system.mjs', '.sis-td-lab'],
  ['static/platform/admin-account.mjs', '.acct-k'],
  ['static/root/admin-direct.css', '.solmod-k'],
  ['static/platform/admin-queue.css', '.queue-table th.cnt-th'],
  ['static/platform/admin-knowledge.mjs', '.kn-field'],
  ['static/platform/admin-knowledge.mjs', '.kn-row>span'],
  ['static/platform/admin-create-appointment.mjs', '.ct-cal-dow'],
  ['static/platform/admin-charts.mjs', '.sana-legend'],
  ['static/root/admin-direct.css', '.cnt-table th.cnt-th'],
  ['static/root/admin-direct.css', '.campos .k'],
  // 2026-11-08: the login form's field labels («Usuario o email», «Contraseña»).
  ['static/root/admin-gate.css', '.gate-row>label'],
];

test('report tile captions, filter labels and panel field labels are dark (--hint-ink)', () => {
  for (const [file, sel] of PINNED) {
    const found = rules(read(file)).filter((r) => r.sel === sel || r.sel.endsWith(' ' + sel) || r.sel.endsWith('}' + sel));
    const own = found.filter((r) => r.sel === sel && /(^|;)\s*color:/.test(r.body));
    assert.ok(own.length, file + ': rule ' + sel + ' with a colour not found');
    for (const r of own) {
      assert.match(r.body, /(^|;)\s*color:var\(--hint-ink,/, file + ': ' + sel + ' must use --hint-ink');
    }
    // Contextual overrides may go darker (--text), never back to the translucent scale.
    for (const r of found) assert.doesNotMatch(r.body, TRANSLUCENT, file + ': ' + r.sel);
  }
});

// The panel menu (sidebar, the phone «Menú» disclosure, a doctor's row of
// tabs, «Cerrar sesión» under it) is navigation, not small print: every item
// and group title is opaque and dark. The current screen keeps --text.
const MENU = [
  '.adm5-nav a',
  '.adm5-side--tabs .adm5-nav a',
  '.adm5-side .adm-logout',
];

test('panel menu items are dark (--hint-ink), never the translucent scale', () => {
  const all = rules(read('static/root/admin-direct.css'));
  for (const sel of MENU) {
    const own = all.filter((r) => r.sel === sel && /(^|;)\s*color:/.test(r.body));
    assert.ok(own.length, 'admin-direct.css: rule ' + sel + ' with a colour not found');
    for (const r of own) assert.match(r.body, /(^|;)\s*color:var\(--hint-ink,/, sel + ' must use --hint-ink');
  }
  const menuRule = (s) => s.split(',').some((part) => /\.adm5-(nav|menu|side)\b/.test(part));
  const offenders = all.filter((r) => menuRule(r.sel) && /(^|;)\s*color:var\(--text-[234]\b/.test(r.body)).map((r) => r.sel);
  assert.deepEqual(offenders, [], 'menu rules on the translucent scale');
  const titles = all.filter((r) => r.sel === '.adm5-nav-group-title' && /(^|;)\s*color:/.test(r.body));
  assert.ok(titles.length, '.adm5-nav-group-title colour not found');
  for (const r of titles) assert.match(r.body, /(^|;)\s*color:var\(--(text|hint-ink)[,)]/);
  for (const sel of ['.adm5-nav a.on', '.adm5-side--tabs .adm5-nav a.on']) {
    const on = all.filter((r) => r.sel === sel);
    assert.ok(on.some((r) => /(^|;)\s*color:var\(--text\)/.test(r.body)), sel + ' stays --text');
  }
});

// Tabs and switchers inside the screens are navigation too (the product owner's hub,
// 2026-11-08): the tabs of a doctor/service form in Contenido, the patient
// summary's «Pendientes / Historia», the Hoja de ruta views, the prompt
// editor's language tabs, the login page's «Humor / Consejos», the page
// numbers under every list and the Wiki tree. The ones not selected are dark
// --hint-ink; the selected one keeps its own colour (--text or the accent).
// The panel modules split long CSS strings over several JS literals
// ("a;" + "b;" or an array join), so the pieces are glued before parsing.
const joinJs = (src) => src.replace(/["']\s*[+,]\s*\n?\s*["']/g, '');
const TABS = [
  ['static/platform/admin-content.mjs', '.cnt-tab', '.cnt-tab.on'],
  ['static/platform/admin-patient-summary.mjs', '.rp-tab', '.rp-tab.on'],
  ['static/platform/admin-patient-summary.mjs', '.rp-tab-n', null],
  ['static/platform/admin-roadmap.mjs', '.rm-tabs a', '.rm-tabs a.on'],
  ['static/platform/admin-prompts.mjs', '.pr-tab', '.pr-tab.on'],
  ['static/platform/admin-knowledge.mjs', '.kn-node', '.kn-node.on'],
  ['static/root/admin-gate.css', '.gp-tabs a', '.gp-tabs a.on'],
  ['static/root/admin-direct.css', '.pgr-num', '.pgr-num.on'],
  // Solicitudes' column names sort the list: the sorted one is `.sol-ch-on`.
  ['static/platform/admin-solicitudes.mjs', '.sol-ch', '.sol-ch-on'],
];

test('tabs and switchers inside panel screens are dark (--hint-ink); the selected one keeps its colour', () => {
  for (const [file, sel, onSel] of TABS) {
    const all = rules(joinJs(read(file)));
    const own = all.filter((r) => r.sel === sel && /(^|;)\s*color:/.test(r.body));
    assert.ok(own.length, file + ': rule ' + sel + ' with a colour not found');
    for (const r of own) assert.match(r.body, /(^|;)\s*color:var\(--hint-ink,/, file + ': ' + sel + ' must use --hint-ink');
    // No state of the tab (empty, count, hover…) goes back to the translucent scale.
    const variants = all.filter((r) => r.sel.split(',').some((s) => s.trim().startsWith(sel)));
    for (const r of variants) assert.doesNotMatch(r.body, /(^|;)\s*color:var\(--text-[234]\b/, file + ': ' + r.sel);
    if (onSel) {
      const on = all.filter((r) => r.sel === onSel && /(^|;)\s*color:/.test(r.body));
      assert.ok(on.length, file + ': ' + onSel + ' colour not found');
      for (const r of on) assert.match(r.body, /(^|;)\s*color:var\(--(text|accent|accent-hover|select)[,)]/, onSel);
    }
  }
});

// Any rule of the panel whose selector has a selected sibling (`X.on`, a
// selected-state class `X-on` next to `X`, `X[aria-selected…]`,
// `X[aria-current…]`, `X[aria-pressed…]`) or names a tab (`…-tab`,
// `…-tabs a`) is a switcher: none may sit on the translucent scale.
test('no panel tab or switcher sits on the translucent --text-2/--text-3/--text-4 scale', () => {
  const SELECTED = /(\.on|\[aria-(selected|current|pressed)[^\]]*\])(?![\w-])/;
  const SELECTED_CLASS = /\.([\w-]+)-on(?![\w-])/;
  const offenders = [];
  for (const file of PANEL_SOURCES.concat(['static/root/admin-gate.css'])) {
    const all = rules(joinJs(read(file)));
    const switchers = new Set();
    for (const r of all) for (const part of r.sel.split(',')) {
      if (SELECTED.test(part)) switchers.add(part.trim().replace(SELECTED, ''));
      const m = part.trim().match(SELECTED_CLASS);
      if (m) switchers.add(part.trim().replace(SELECTED_CLASS, '.' + m[1]));
    }
    for (const r of all) {
      if (!/(^|;)\s*color:var\(--text-[234]\b/.test(r.body)) continue;
      const parts = r.sel.split(',').map((s) => s.trim());
      if (parts.some((p) => switchers.has(p) || /\.[\w-]*-tabs?( a)?$/.test(p))) offenders.push(file + ': ' + r.sel);
    }
  }
  assert.deepEqual(offenders, [], 'tabs/switchers use color:var(--hint-ink,var(--text-2))');
});

test('--hint-ink is defined in both panel themes', () => {
  const tokens = read('static/root/tokens.css');
  assert.equal((tokens.match(/--hint-ink:#[0-9a-f]{6};/g) || []).length, 2);
});
