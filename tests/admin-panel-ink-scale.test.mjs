// The panel draws no text with the translucent --text-2/--text-3/--text-4
// scale: the humanrounds theme mixes it at 38-50 % opacity, which reads as
// light grey on the demo (audit 08.11: meta lines, empty states, the crumb,
// the account's meta, the legal paragraph, Solicitudes' empty states).
// Captions and secondary text take the dark --hint-ink, key text --text
// (the product owner 2026-11-04); the translucent scale stays for the public site's
// descriptive small print (the product owner 2026-09-16).
//
// Outside this guard on purpose: input placeholders and disabled controls
// (deliberately faint) and modules shared with the public site
// (calendar-grid.mjs, week.mjs). «Pacientes de hoy» joined the guard with its
// own change (admin-queue.css, admin-create-appointment.mjs, the queue's rules
// in admin-direct.css). The doctor's phone tab row is pinned at the end.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const read = (p) => fs.readFileSync(new URL('../' + p, import.meta.url), 'utf8');

const PANEL = [
  'static/root/admin-direct.css', 'static/root/admin-gate.css',
  'static/platform/admin-account.mjs', 'static/platform/admin-alerts.mjs',
  'static/platform/admin-content.mjs', 'static/platform/admin-hours-editor.mjs',
  'static/platform/admin-knowledge.mjs', 'static/platform/admin-legal.mjs',
  'static/platform/admin-patient-summary.mjs', 'static/platform/admin-prompts.mjs',
  'static/platform/admin-roadmap.mjs', 'static/platform/admin-settings.mjs',
  'static/platform/admin-solicitudes.mjs', 'static/platform/doctor-workspace.css',
  'app/modules/intake/admin-entrevistas.mjs',
  'static/platform/admin-queue.css', 'static/platform/admin-create-appointment.mjs',
];

// Deliberately faint (see the header).
const EXEMPT = /placeholder|disabled/;
// The text colour itself, not a background/border/fill; a --text-N that is
// only the fallback of --hint-ink (`var(--hint-ink,var(--text-2))`) is fine.
const TRANSLUCENT_TEXT = /(?<![-\w])color\s*:\s*var\(\s*--text-[234]\b/;

// Every `selector{body}` of a source: CSS files and the CSS strings the panel
// modules inject (a JS string split over `" +` lines is joined first).
function rules(src) {
  src = src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/["'`]\s*\+\s*\n\s*["'`]/g, '');
  const out = [];
  const re = /([^{}"'`]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(src))) out.push({ sel: m[1].trim().replace(/\s+/g, ' '), body: m[2] });
  return out;
}

test('panel text never uses the translucent grey scale', () => {
  const found = [];
  for (const file of PANEL) {
    for (const r of rules(read(file))) {
      if (TRANSLUCENT_TEXT.test(r.body) && !EXEMPT.test(r.sel)) found.push(`${file}: ${r.sel.slice(-80)}`);
    }
  }
  assert.deepEqual(found, [], 'use var(--hint-ink) for captions, var(--text) for key text');
});

test('the swept captions are dark: crumb, account meta, legal paragraph, empty states', () => {
  const css = read('static/root/admin-direct.css');
  const hint = (sel) => rules(css).some((r) => r.sel.split(',').map((s) => s.trim()).includes(sel)
    && /(?<![-\w])color:var\(--hint-ink\)/.test(r.body));
  for (const sel of ['.adm-crumb', '.rep-centro-rating']) assert.ok(hint(sel), `${sel} takes --hint-ink`);
});

test('a doctor\'s phone tabs carry one word; the full name stays the link\'s name', () => {
  const base = read('templates/admin/base.html');
  for (const [id, key, word] of [
    ['cola', 'cola', 'Hoy'], ['mis-turnos', 'mis_turnos', 'Turnos'],
    ['mis-pacientes', 'mis_pacientes', 'Pacientes'], ['my-schedules', 'my_schedules', 'Agendas'],
  ]) {
    assert.ok(base.includes(`'${id}': t('admin.shell.nav.tab.${key}', '${word}')`), `${id} → «${word}»`);
    for (const lang of ['es', 'en']) {
      const cat = JSON.parse(read(`locales/${lang}/admin.shell.json`));
      assert.ok(cat[`admin.shell.nav.tab.${key}`], `${lang} has admin.shell.nav.tab.${key}`);
    }
  }
  assert.match(base, /aria-label="\{\{ item\.label \}\}"><span class="adm5-tab-full">\{\{ item\.label \}\}<\/span><span class="adm5-tab-short" aria-hidden="true">\{\{ tab_short \}\}<\/span>/);
  const css = read('static/root/admin-direct.css');
  // Phone: the short word shows, the full name hides; elsewhere the reverse.
  assert.match(css, /\.adm5-side--tabs \.adm5-tab-full\{display:none;\}\s*\.adm5-side--tabs \.adm5-tab-short\{display:inline;\}/);
  assert.match(css, /\n {2}\.adm5-tab-short\{display:none;\}/);
  // «Cerrar sesión» closes the Account screen instead of widening the row.
  assert.match(base, /tabs \? document\.querySelector\("\.adm5-main"\) : nav/);
  assert.doesNotMatch(css, /\.adm5-side--tabs \.adm5-nav \.adm-logout\{/);
});
