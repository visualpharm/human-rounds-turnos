// Source guards for the client manifest (Tier 4 slice 6, batch 3 —
// core/static_fingerprint.py CLIENT_LOADED): every STATIC JS file that loads
// another first-party asset by URL at runtime resolves that URL through
// window.SANA_STATIC (the map the shell publishes), fail-closed — the
// islands.mjs resolveUrl() shape: null + console.error on a missing entry,
// NEVER a hardcoded fallback URL and never a `/<name>?v=N` literal. The
// Python side pins the cohort (test_cache_headers.py,
// test_admin_static_fingerprint_rendering.py) and every page that emits a
// loader (test_static_manifest.py); this file pins the loaders themselves.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { patientSiteSource } from './helpers/patient-site-source.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFileSync(join(ROOT, name), 'utf8');

// Mirror of static_fingerprint.CLIENT_LOADED (the Python test pins that set;
// this list is the JS-side twin so a drift on either side fails a suite).
const CLIENT_LOADED = ['platform/directory.mjs', 'platform/account-form.mjs', 'platform/dependents.mjs', 'platform/dni-barcode.mjs', 'platform/dni-scan.mjs', 'platform/age.mjs', 'platform/calendar-grid.mjs', 'platform/hours.mjs', 'platform/dates.mjs', 'platform/format.mjs', 'platform/prep.mjs', 'platform/islands.mjs', 'platform/turnstile.mjs', 'platform/passkeys.mjs', 'platform/admin-combo.mjs', 'platform/admin-filter-controls.mjs', 'platform/admin-patient-summary.mjs', 'platform/admin-preferences.mjs', 'platform/admin-session.mjs', 'platform/admin-table.mjs', 'platform/admin-prompts.mjs', 'platform/admin-legal.mjs', 'platform/admin-knowledge.mjs', 'platform/i18n.mjs', 'platform/week.mjs', 'platform/admin-charts.mjs', 'platform/search.mjs', 'platform/admin-login.mjs', 'platform/listing.mjs', 'platform/admin-solicitudes.mjs', 'platform/admin-hours-editor.mjs', 'platform/admin-users.mjs', 'platform/chat-widget.mjs', 'platform/chat-ui.mjs', 'platform/admin-create-appointment.mjs', 'platform/attendance-forecast.mjs'];

// loader file -> the CLIENT_LOADED names it resolves through the manifest.
CLIENT_LOADED.push('platform/account-requests.mjs', 'platform/account-labs.mjs');
CLIENT_LOADED.push('platform/nav.mjs');
const LOADERS = {
  'static/platform/account.mjs': ['platform/nav.mjs'],
  'static/platform/account-labs.mjs': ['platform/listing.mjs', 'platform/dates.mjs'],
  'static/platform/account-requests.mjs': ['platform/directory.mjs', 'platform/account-form.mjs', 'platform/dates.mjs', 'platform/prep.mjs'],
  'static/platform/chat-widget.mjs': ['platform/account-form.mjs', 'platform/dates.mjs', 'platform/chat-ui.mjs'],
  'app/modules/intake/entrevista.js': ['platform/i18n.mjs', 'platform/chat-ui.mjs'],
  'static/platform/account-form.mjs': ['platform/dni-barcode.mjs'],
  'static/platform/dni-barcode.mjs': ['platform/dni-scan.mjs'],
  'app/modules/turnos/service.mjs': ['platform/age.mjs', 'platform/calendar-grid.mjs', 'platform/dates.mjs', 'platform/format.mjs', 'platform/prep.mjs', 'platform/turnstile.mjs', 'platform/account-form.mjs', 'platform/dependents.mjs'],
  'app/modules/turnos/upload-order.mjs': ['platform/prep.mjs', 'platform/turnstile.mjs', 'platform/search.mjs', 'platform/listing.mjs'],
  'static/platform/admin-login.mjs': ['platform/passkeys.mjs'],
  'static/platform/admin-content.mjs': ['platform/admin-combo.mjs', 'platform/calendar-grid.mjs', 'platform/hours.mjs', 'platform/dates.mjs', 'platform/format.mjs', 'platform/islands.mjs', 'platform/admin-hours-editor.mjs', 'platform/admin-users.mjs', 'platform/admin-create-appointment.mjs'],
};

test('native staff Account resolves required dependencies fail-closed and keeps Users optional', () => {
  const account = read('static/platform/admin-account.mjs');
  assert.match(account, /var url = map && map\[name\]/);
  assert.match(account, /return url \? import\(url\) : Promise\.reject\(new Error\(/);
  for (const name of ['platform/admin-session.mjs', 'platform/i18n.mjs', 'platform/passkeys.mjs']) {
    assert.ok(CLIENT_LOADED.includes(name));
    assert.ok(account.includes('importStatic("' + name + '")'));
  }
  assert.match(account, /map && map\["platform\/admin-users\.mjs"\]/);
  assert.doesNotMatch(account, /import\(["']\/?(?:static\/)?platform\//);
  const fingerprint = read('app/core/static_fingerprint.py');
  const cohort = /^CLIENT_LOADED = frozenset\(\{([\s\S]*?)^\}\)/m.exec(fingerprint)?.[1];
  assert.ok(cohort);
  assert.ok(!cohort.includes('"platform/admin-account.mjs"'), 'template entry must not expand shared client cohort');
});

test('Account child controllers are published in the real client manifest cohort', () => {
  const fingerprint = read('app/core/static_fingerprint.py');
  const cohort = /^CLIENT_LOADED = frozenset\(\{([\s\S]*?)^\}\)/m.exec(fingerprint)?.[1];
  assert.ok(cohort, 'must locate the exact client-loaded cohort, not the broader registry');
  for (const name of ['platform/account-requests.mjs', 'platform/account-labs.mjs']) {
    assert.ok(cohort.includes('"' + name + '"'), `${name} must be emitted to real Account pages`);
    assert.ok(read('static/platform/account.mjs').includes('"' + name + '"'));
  }
});

const COMBO_IMPORTERS = ['static/root/admin-app.js', 'static/platform/admin-content.mjs'];
const PATIENT_SUMMARY_IMPORTERS = ['app/modules/intake/admin-consult.mjs'];
const PREFERENCES_IMPORTERS = ['static/root/admin-app.js', 'static/platform/admin-content.mjs', 'static/platform/admin-doctor-patients.mjs',
  'static/platform/admin-patients.mjs', 'static/platform/admin-queue.mjs', 'static/platform/admin-table.mjs'];
const SESSION_IMPORTERS = [
  'static/platform/admin-alerts.mjs', 'static/root/admin-app.js', 'static/platform/admin-account.mjs', 'app/modules/intake/admin-consult.mjs',
  'static/platform/admin-patient-summary.mjs', 'static/platform/admin-content.mjs',
  'static/platform/admin-create-appointment.mjs',
  'static/platform/admin-doctor-patients.mjs', 'static/platform/admin-knowledge.mjs', 'static/platform/admin-legal.mjs',
  'static/platform/admin-my-appointments.mjs', 'static/platform/admin-patients.mjs', 'static/platform/admin-preparations.mjs',
  'static/platform/admin-prompts.mjs', 'static/platform/admin-queue.mjs', 'static/platform/admin-reports.mjs', 'static/platform/admin-settings.mjs',
  'static/platform/admin-system.mjs', 'static/platform/admin-appearance.mjs',
  'app/modules/intake/admin-entrevistas.mjs',
];

test('the home page imports the server-fingerprinted canonical platform/tiles.mjs', () => {
  const src = read('templates/index.html');
  assert.match(src, /var tilesUrl = "\{\{ static\('platform\/tiles\.mjs'\) \}\}"/);
  assert.match(src, /import\(tilesUrl\)/);
  assert.match(src, /mods\[0\]\.renderList\(\$\("#centros"\)/);
  assert.doesNotMatch(src, /SanaTiles|static\(['"]tiles\.js/);
});

test('the home page imports the fingerprinted centers map and falls back to the tiles list without it', () => {
  const src = read('templates/index.html');
  assert.match(src, /var mapUrl = "\{\{ static\('platform\/centers-map\.mjs'\) \}\}"/);
  assert.match(src, /import\(mapUrl\)\.catch\(function \(\) \{ return null; \}\)/);
  assert.match(src, /if \(mods\[1\] && MAPA\) mods\[1\]\.render\(\$\("#centros"\), order, MAPA, T, mods\[0\]\);/);
  assert.match(src, /else mods\[0\]\.renderList\(\$\("#centros"\), order, T\);/);
});

test('patient schedule grids load native Week from the fingerprint manifest with no global or classic fallback', () => {
  const source = patientSiteSource();
  assert.match(source, /SANA_STATIC\["platform\/week\.mjs"\]/);
  assert.match(source, /import\(weekUrl\)\.catch\(showWeekError\)/);
  assert.match(source, /data-semana-error hidden role="alert"/);
  assert.doesNotMatch(source, /static_fingerprint\.url\("week\.js"\)|SanaWeek/);
  const week = read('static/platform/week.mjs');
  assert.match(week, /importDep\("platform\/i18n\.mjs"/);
  assert.match(week, /importDep\("platform\/hours\.mjs"/);
  assert.match(week, /export \{ CSS, activeLang, bloques, init, render \}/);
  assert.doesNotMatch(week, /SanaWeek|module\.exports/);
});

// admin-reports.mjs resolves dates (and its four siblings) through its own
// local importStatic() instead of the inline datesUrl shape — its manifest
// contract is pinned by the dedicated five-dependency test below. The same
// holds for admin-hsi.mjs since the 2026-09-08 native cutover (the classic
// root admin-hsi.js is retired): its four dependencies and its template
// boot are pinned by the dedicated HSI test below.
const DATE_IMPORTERS = [
  'static/platform/admin-alerts.mjs', 'static/root/admin-app.js', 'app/modules/intake/admin-consult.mjs',
  'static/platform/admin-doctor-patients.mjs', 'static/platform/admin-my-appointments.mjs',
  'static/platform/admin-patient-summary.mjs', 'static/platform/admin-patients.mjs', 'static/platform/admin-queue.mjs',
];

const FORMAT_IMPORTERS = [
  'static/platform/admin-alerts.mjs', 'static/root/admin-app.js', 'static/platform/admin-content.mjs',
  'static/platform/admin-doctor-patients.mjs', 'static/platform/admin-hsi.mjs', 'static/platform/admin-legal.mjs',
  'static/platform/admin-my-appointments.mjs', 'static/platform/admin-patients.mjs', 'static/platform/admin-prompts.mjs',
  'static/platform/admin-queue.mjs', 'static/platform/admin-solicitudes.mjs',
  'app/modules/intake/admin-entrevistas.mjs',
];

const RESOLVER = /function staticUrl\(name\) \{\s*(?:var|const) m = \(typeof window !== ["']undefined["']\) && window\.SANA_STATIC;\s*(?:var|const) u = m && m\[name\];\s*if \(!u && typeof console !== ["']undefined["'] && console\.error\) console\.error\(["']\[static\] ["'] \+ name \+ ["'] is not in window\.SANA_STATIC/;

for (const [file, names] of Object.entries(LOADERS)) {
  test(`${file}: resolves ${names.join(' + ')} through window.SANA_STATIC, fail-closed`, () => {
    const src = read(file);
    if (file === 'static/platform/account.mjs') {
      assert.match(src, /function importStatic\(name\) \{\s*var url = window\.SANA_STATIC && window\.SANA_STATIC\[name\];\s*return url \? import\(url\) : Promise\.reject/,
        'Account imports only the manifest URL and rejects a missing key');
    } else {
      assert.match(src, RESOLVER, `${file} must carry the local staticUrl() resolver`);
    }
    for (const name of names) {
      assert.ok(CLIENT_LOADED.includes(name), `${name} must be in CLIENT_LOADED`);
      const quoted = name.replace(/\./g, '\\.');
      assert.match(src, new RegExp(`staticUrl\\(["']${quoted}["']\\)|importStatic\\(["']${quoted}["']\\)|sanaImportStatic\\(["']${quoted}["']\\)`),
        `${file} must look ${name} up by name`);
    }
    // No `?v=` alias for ANY client-loaded asset, no hardcoded root URL as a
    // fallback (a "/listing.js" literal would silently 404 once the real file
    // is only served at its fingerprinted URL).
    for (const name of CLIENT_LOADED) {
      const quoted = name.replace(/\./g, '\\.');
      assert.doesNotMatch(src, new RegExp(`/${quoted}\\?v=`), `${file} must not reference /${name}?v=`);
      assert.doesNotMatch(src, new RegExp(`["']/${quoted}["']`), `${file} must not hardcode /${name}`);
    }
  });
}

test('every remaining date consumer imports canonical dates.mjs through the manifest', () => {
  for (const file of DATE_IMPORTERS) {
    const src = read(file);
    if (file === 'app/modules/intake/admin-consult.mjs') {
      assert.match(src, /importStatic\("platform\/dates\.mjs"\)/);
      assert.match(src, /var map = host && host\.SANA_STATIC/);
      assert.match(src, /var url = map && map\[name\]/);
      assert.match(src, /url \? import\(url\) : Promise\.reject/);
    } else if (file === 'static/platform/admin-alerts.mjs') {
      assert.match(src, /importStatic\("platform\/dates\.mjs"\)/);
      assert.match(src, /var url = \(typeof window !== "undefined" && window\.SANA_STATIC && window\.SANA_STATIC\[name\]\)/);
      assert.match(src, /url \? import\(url\) : Promise\.reject/);
    } else if (file === 'static/platform/admin-patients.mjs') {
      assert.match(src, /importStatic\("platform\/dates\.mjs"\)/);
      assert.match(src, /var url = window\.SANA_STATIC && window\.SANA_STATIC\[name\]/);
      assert.match(src, /url \? import\(url\) : Promise\.reject/);
    } else {
    assert.match(src, /window\.SANA_STATIC\s*&&\s*window\.SANA_STATIC\[["']platform\/dates\.mjs["']\]/,
      `${file} must resolve dates.mjs through the fingerprint manifest`);
    assert.match(src, /import\(datesUrl\)/, `${file} must import the resolved URL`);
    }
    assert.doesNotMatch(src, /["']\/dates\.mjs["']|dates\.mjs\?v=/,
      `${file} must not carry a fallback date URL`);
  }
});

test('every admin formatter consumer uses the shared fail-closed ESM loader', () => {
  const shell = read('static/root/admin-shell.js');
  assert.match(shell, /window\.sanaImportStatic = function \(name\)/);
  assert.match(shell, /window\.SANA_STATIC && window\.SANA_STATIC\[name\]/);
  assert.match(shell, /admin-module-load-error/);
  assert.match(shell, /role", "alert"/);
  for (const file of FORMAT_IMPORTERS) {
    const src = read(file);
    assert.match(src, /(?:sanaImportStatic|importStatic|importDep)\(["']platform\/format\.mjs["'](?:\)|,)|SANA_STATIC\[["']platform\/format\.mjs["']\]/,
      `${file} must load format.mjs through the shared visible boundary`);
    assert.doesNotMatch(src, /SanaFormat|["']\/format\.mjs["']|format\.mjs\?v=/,
      `${file} must not use a compatibility global or fallback formatter URL`);
  }
});

test('requests imports the native renderer through the manifest and has no compatibility global', () => {
  const app = read('static/root/admin-app.js');
  const renderer = read('static/platform/admin-solicitudes.mjs');
  assert.match(app, /SANA_STATIC\s*&&\s*window\.SANA_STATIC\['platform\/admin-solicitudes\.mjs'\]/);
  assert.match(app, /await Promise\.all\([\s\S]*solicitudesReady/);
  assert.match(app, /role="alert"/);
  assert.match(renderer, /importStatic\("platform\/i18n\.mjs"/);
  assert.match(renderer, /importStatic\("platform\/admin-preferences\.mjs"/);
  assert.match(renderer, /export \{ css, columns, render/);
  assert.doesNotMatch(renderer, /SanaSolicitudes|module\.exports/);
});

test('every combo consumer imports canonical admin-combo.mjs and no global compatibility API exists', () => {
  for (const file of COMBO_IMPORTERS) {
    const src = read(file);
    assert.match(src, /(?:sanaImportStatic|importStatic)\(["']platform\/admin-combo\.mjs["']\)/);
    assert.doesNotMatch(src, /SanaCombo|window\.comboHtml/);
  }
  assert.doesNotMatch(read('static/platform/admin-combo.mjs'), /window\.(?:SanaCombo|comboHtml)|module\.exports/);
});

test('every preference consumer imports and awaits the canonical ES module', () => {
  for (const file of PREFERENCES_IMPORTERS) {
    const src = read(file);
    assert.match(src, /(?:sanaImportStatic|importStatic|loadStatic)\(["']platform\/admin-preferences\.mjs["']\)/,
      `${file} must import admin-preferences.mjs through the manifest`);
    assert.match(src, file === 'static/platform/admin-patients.mjs' ? /return Prefs\.load\(\)\.then\([\s\S]*return route\(\)/ : /prefsReady|preferencesReady/,
      `${file} must retain an explicit preference-load gate`);
    assert.doesNotMatch(src, /SanaPreferences/,
      `${file} must not use the retired compatibility global`);
  }
  const module = read('static/platform/admin-preferences.mjs');
  assert.match(module, /export function load/);
  assert.match(module, /export function get/);
  assert.match(module, /export function set/);
  assert.doesNotMatch(module, /window\.SanaPreferences|hydrateNavigationGroups/);
});

test('every session consumer imports and awaits canonical admin-session.mjs with no compatibility global', () => {
  for (const file of SESSION_IMPORTERS) {
    const src = read(file);
    const nativeDep = file === 'static/platform/admin-create-appointment.mjs';
    if (nativeDep) {
      assert.match(src, /await importDep\(["']platform\/admin-session\.mjs["']/,
        `${file} must top-level-await the session module`);
    } else if (['static/platform/admin-preparations.mjs', 'static/platform/admin-account.mjs'].includes(file)) {
      // Native lifecycle controller: one dependencies promise that awaits the
      // session import; .loaded ends the page's load deadline on it and
      // .ready gates the first render on it.
      assert.match(src, /var dependencies = Promise\.all\(\[[\s\S]*?importStatic\(["']platform\/admin-session\.mjs["']\)[\s\S]*?\]\);[\s\S]*?dispose\.loaded = dependencies\.then\([\s\S]*?dispose\.ready = dependencies\.then\(/,
        `${file} must gate first render on the session import`);
    } else if (file === 'app/modules/intake/admin-consult.mjs') {
      // Native lifecycle controller: first render is gated by the mount's
      // ready promise, which awaits the session import inside its Promise.all.
      assert.match(src, /dispose\.ready = Promise\.all\(\[[\s\S]*?importStatic\(["']platform\/admin-session\.mjs["']\)[\s\S]*?\]\)/,
        `${file} must gate first render on the session import`);
    } else {
      assert.match(src, /(?:sanaImportStatic|importStatic|importDep)\(["']platform\/admin-session\.mjs["'](?:\)|,)|SANA_STATIC\[["']platform\/admin-session\.mjs["']\]/,
        `${file} must import admin-session.mjs through the manifest`);
      assert.match(src, file === 'static/platform/admin-patients.mjs' ? /var dependencies = Promise\.all\(\[[\s\S]*importStatic\("platform\/admin-session\.mjs"\)[\s\S]*?dispose\.loaded = dependencies\.then\([\s\S]*?dispose\.ready = dependencies\.then\(/ : /sessionReady/, `${file} must retain an explicit session-load gate`);
    }
    assert.doesNotMatch(src, /SanaAdminSession|admin-session\.js/,
      `${file} must not keep the retired global or classic file`);
  }
  const module = read('static/platform/admin-session.mjs');
  assert.match(module, /export function confirmar/);
  assert.match(module, /export function redirigir/);
  assert.match(module, /export function nextValido/);
  assert.doesNotMatch(module, /window\.SanaAdminSession|module\.exports/);
});

test('the consult screen imports canonical admin-patient-summary.mjs and no global compatibility API exists', () => {
  for (const file of PATIENT_SUMMARY_IMPORTERS) {
    const src = read(file);
    assert.match(src, /importStatic\(["']platform\/admin-patient-summary\.mjs["']\)/);
    assert.match(src, /dispose\.ready = Promise\.all\(\[[\s\S]*?importStatic\("platform\/admin-patient-summary\.mjs"\)/);
    assert.doesNotMatch(src, /SanaResumenPaciente/);
  }
  assert.doesNotMatch(read('static/platform/admin-patient-summary.mjs'), /window\.SanaResumenPaciente|module\.exports/);
});

test('a missing manifest entry takes the same path a failed load takes (no throw, no fallback URL)', () => {
  // import() loaders: a rejected promise, so the pre-existing .catch paths
  // (OCR tier 2 in platform/account-form.mjs, the desk's "No se pudo leer"
  // message, chat/requests "Mi cuenta" recovery) handle it unchanged.
  assert.match(read('static/platform/account-form.mjs'), /function importStatic\(name\) \{\s*var u = staticUrl\(name\);\s*return u \? import\(u\) : Promise\.reject\(/);
  assert.match(read('static/platform/admin-create-appointment.mjs'), /function importDep\(name, nodeUrl\) \{/);
  assert.match(read('static/platform/admin-create-appointment.mjs'), /return Promise\.reject\(new Error\("\[static\] " \+ name \+ " is not in window\.SANA_STATIC"\)\)/);
  assert.match(read('app/modules/turnos/service.mjs'), /function importStatic\(name\) \{\s*var u = staticUrl\(name\);\s*return u \? import\(u\) : Promise\.reject\(/);
  assert.match(read('static/platform/admin-content.mjs'), /var u = staticUrl\(name\);\s*return u \? import\(u\)\.then\(function \(m\) \{\s*if \(disposed\) throw cancelled\(\);\s*return m;\s*\}\) : Promise\.reject\(/);
  assert.match(read('static/platform/account-requests.mjs'), /function importStatic\(name\) \{\s*var u = staticUrl\(name\);\s*return u \? import\(u\) : Promise\.reject\(/);
  assert.match(read('static/platform/chat-widget.mjs'), /function importStatic\(name\) \{\s*var u = staticUrl\(name\);\s*return u \? import\(u\) : Promise\.reject\(/);
  assert.doesNotMatch(read('static/platform/account-requests.mjs'), /function ensureScript\(/);
  assert.doesNotMatch(read('static/platform/chat-widget.mjs'), /function ensureScript\(/);
  assert.match(read('static/platform/account-labs.mjs'), /var src = staticUrl\("platform\/listing\.mjs"\);\s*if \(!src\) \{ cb\(\); return; \}/);
  const barcode = read('static/platform/dni-barcode.mjs');
  assert.match(barcode, /const SCAN = SCAN_URL \? import\(SCAN_URL\) : Promise\.reject\(/);
  assert.match(barcode, /SCAN\.catch\(\(\) => \{\}\);/);
  assert.match(barcode, /const \{ parsePayload \} = await SCAN;/);
});

test('the native chat widget and my-requests.js resolve platform/account-form.mjs lazily, when login opens — not at load', () => {
  const chat = read('static/platform/chat-widget.mjs');
  assert.doesNotMatch(chat, /ACCOUNT_FORM_SRC|SanaAccount|account-form\.js/);
  const abrirAuth = /function abrirAuth\(\) \{[\s\S]*?\n  \}/.exec(chat);
  assert.ok(abrirAuth, 'abrirAuth() must exist');
  assert.match(abrirAuth[0], /loadAccountForm\(\)/);
  assert.match(chat, /function loadAccountForm\(\) \{[\s\S]*?importStatic\(["']platform\/account-form\.mjs["']\)/);

  const requests = read('static/platform/account-requests.mjs');
  assert.doesNotMatch(requests, /SanaAccount|account-form\.js|ensureScript/);
  const renderLogin = /function renderLogin\(\) \{[\s\S]*?\n  \}/.exec(requests);
  assert.ok(renderLogin, 'renderLogin() must exist');
  assert.match(renderLogin[0], /loadAccountForm\(\)/);
  assert.match(requests, /function loadAccountForm\(\) \{[\s\S]*?importStatic\(["']platform\/account-form\.mjs["']\)/);
});

test('the reports screen imports canonical platform/admin-charts.mjs and awaits it before first paint', () => {
  const src = read('static/platform/admin-reports.mjs');
  assert.match(src, /importStatic\(["']platform\/admin-charts\.mjs["']\)/);
  assert.match(src, /chartsReady/);
  assert.match(src, /Promise\.all\(\[i18nReady, datesReady, chartsReady, searchReady, sessionReady, tableReady\]\)/);
  assert.doesNotMatch(src, /SanaCharts|admin-charts\.js/);
  const module = read('static/platform/admin-charts.mjs');
  assert.match(module, /export \{/);
  assert.match(module, /scatter,/);
  assert.doesNotMatch(module, /window\.SanaCharts|module\.exports/);
});

test('listing, reports, combo, create-appointment and upload-order import native search with local readiness and no compatibility global', () => {
  const listing = read('static/platform/listing.mjs');
  assert.match(listing, /importDep\(["']platform\/search\.mjs["']/);
  assert.match(listing, /popularity ranking requires native Search/);
  assert.doesNotMatch(listing, /SanaSearch|search\.js|window\.SanaListing|module\.exports/);

  const reports = read('static/platform/admin-reports.mjs');
  assert.match(reports, /importStatic\(["']platform\/search\.mjs["']\)/);
  assert.match(reports, /searchReady/);
  assert.match(reports, /Search\.buscarConPuntaje/);
  assert.doesNotMatch(reports, /SanaSearch|search\.js/);

  const combo = read('static/platform/admin-combo.mjs');
  assert.match(combo, /SANA_STATIC\[["']platform\/search\.mjs["']\]/);
  assert.match(combo, /Search\.buscarConPuntaje/);
  assert.doesNotMatch(combo, /SanaSearch|search\.js|module\.exports/);

  const create = read('static/platform/admin-create-appointment.mjs');
  assert.match(create, /importDep\(["']platform\/search\.mjs["']/);
  assert.match(create, /Search\.buscar\(/);
  assert.doesNotMatch(create, /SanaSearch|search\.js|window\.SanaCreateAppointment|module\.exports/);

  const upload = read('app/modules/turnos/upload-order.mjs');
  assert.match(upload, /importStatic\(["']platform\/search\.mjs["']\)/);
  assert.match(upload, /searchReady/);
  assert.match(upload, /Search\.buscarFrase/);
  assert.doesNotMatch(upload, /SanaSearch|search\.js/);

  const home = read('templates/index.html');
  assert.match(home, /SANA_STATIC\[["']platform\/search\.mjs["']\]/);
  assert.match(home, /searchReady/);
  assert.doesNotMatch(home, /static\(['"]search\.js['"]\)|SanaSearch/);

  const module = read('static/platform/search.mjs');
  assert.match(module, /export \{/);
  assert.match(module, /buscar,/);
  assert.doesNotMatch(module, /window\.SanaSearch|module\.exports/);
});

test('the Reports screen boots the native module with all five manifest dependencies and lifecycle recovery', () => {
  const tpl = read('templates/admin/reportes.html');
  // Fixed native import: the server-fingerprinted module URL resolved by the
  // template itself, never a classic script tag, never the retired root file.
  assert.match(tpl, /import\("\{\{ static\('platform\/admin-reports\.mjs'\) \}\}"\)/);
  assert.match(tpl, /const host = document\.getElementById\("sec-reportes"\);/);
  assert.match(tpl, /mount: \(Reports, onError\) => Reports\.mountReports\(\{\s*root: host, onError, currency: \{\{ reports_currency_json\|safe \}\},\s*\}\)/);
  assert.doesNotMatch(tpl, /admin-reports\.js|sanaImportStatic/);

  const src = read('static/platform/admin-reports.mjs');
  assert.match(src, /export function mountReports\(options\)/);
  // Local fail-closed resolver: a missing entry rejects, never a fallback URL.
  assert.match(src, /function importStatic\(name\) \{\s*\n\s*var url = window\.SANA_STATIC && window\.SANA_STATIC\[name\];\s*\n\s*return url \? import\(url\) : Promise\.reject\(new Error\("Missing Reports dependency: " \+ name\)\);/);
  for (const dep of ['platform/i18n.mjs', 'platform/dates.mjs', 'platform/admin-charts.mjs', 'platform/search.mjs', 'platform/admin-session.mjs']) {
    assert.ok(src.includes(`importStatic("${dep}")`), `${dep} must be resolved through window.SANA_STATIC`);
    assert.ok(CLIENT_LOADED.includes(dep), `${dep} must be in CLIENT_LOADED`);
  }
  assert.match(src, /var dependencies = Promise\.all\(\[i18nReady, datesReady, chartsReady, searchReady, sessionReady, tableReady\]\);/);
  // The table engine (its «Descargar» button) is optional: a missing module
  // paints the report without the button, never fails the screen.
  assert.match(src, /var tableReady = importStatic\("platform\/admin-table\.mjs"\)\.then\(function \(m\) \{ Table = m; return m; \}, function \(\) \{ return null; \}\);/);
  assert.ok(CLIENT_LOADED.includes('platform/admin-table.mjs'));
  assert.match(src, /dispose\.loaded = dependencies\.then\(function \(\) \{ return true; \}, function \(\) \{ return false; \}\);/);
  assert.match(src, /dispose\.ready = dependencies\.then\(/);
  // Template lifecycle — the late-load notice that ends on .loaded, page-exit
  // disposal, bfcache restart and the reload copy — is the shared loader's
  // (static/platform/screen-boot.mjs, tests/screen-boot.test.mjs).
  assert.match(tpl, /import \{ bootScreen \} from "\{\{ static\('platform\/screen-boot\.mjs'\) \}\}"/);
  assert.match(tpl, /recovery: "reports-load-recovery"/);
  assert.match(tpl, /<template id="reports-load-recovery">/);
  assert.doesNotMatch(tpl, /setTimeout/);
});

test('the native HSI screen resolves its four manifest dependencies and boots from its fingerprinted URL', () => {
  const src = read('static/platform/admin-hsi.mjs');
  // Local fail-closed resolver: a missing entry rejects, never a fallback URL.
  assert.match(src, /function importStatic\(name\) \{\s*var url = window\.SANA_STATIC && window\.SANA_STATIC\[name\];\s*return url \? import\(url\) : Promise\.reject\(new Error\("Missing HSI dependency: " \+ name\)\);/);
  for (const dep of ['platform/i18n.mjs', 'platform/dates.mjs', 'platform/format.mjs', 'platform/admin-session.mjs']) {
    assert.ok(src.includes(`importStatic("${dep}")`), `${dep} must be resolved through window.SANA_STATIC`);
    assert.ok(CLIENT_LOADED.includes(dep), `${dep} must be in CLIENT_LOADED`);
  }
  const tpl = read('templates/admin/hsi.html');
  // Fixed native import: the server-fingerprinted module URL resolved by the
  // template itself, never a classic script tag, never the retired root file.
  assert.match(tpl, /import\("\{\{ static\('platform\/admin-hsi\.mjs'\) \}\}"\)/);
  assert.match(tpl, /mount: \(Hsi, onError\) => Hsi\.mount\(host, \{ status, onError \}\)/);
  assert.match(tpl, /bootScreen\(\{/);
  assert.doesNotMatch(tpl, /admin-hsi\.js|sanaImportStatic/);
});

test('the login screen imports canonical platform/admin-login.mjs from the fingerprint manifest', () => {
  const src = read('templates/admin/login.html');
  assert.match(src, /SANA_STATIC\[["']platform\/admin-login\.mjs["']\]/);
  assert.match(src, /m\.boot\(\{ pendingSubmit: pending \}\)/);
  assert.match(src, /e\.preventDefault\(\)/);
  assert.doesNotMatch(src, /SanaAdminLogin|static\(['"]admin-login\.js['"]\)|sanaImportStatic/);
  const module = read('static/platform/admin-login.mjs');
  assert.match(module, /export \{/);
  assert.match(module, /async function boot\(/);
  assert.match(module, /SANA_STATIC\[["']platform\/i18n\.mjs["']\]/);
  assert.doesNotMatch(module, /window\.SanaAdminLogin|module\.exports/);
});

test('Users and its consumers import canonical platform/admin-users.mjs with no compatibility global', () => {
  const module = read('static/platform/admin-users.mjs');
  assert.match(module, /export function boot/);
  assert.match(module, /export function refresh/);
  assert.match(module, /export \{ renderDoctorUser \}/);
  assert.match(module, /importDep\("platform\/dates\.mjs"/);
  assert.match(module, /importDep\("platform\/format\.mjs"/);
  assert.match(module, /importDep\("platform\/admin-preferences\.mjs"/);
  assert.match(module, /importDep\("platform\/admin-session\.mjs"/);
  assert.match(module, /importDep\("platform\/admin-table\.mjs"/);
  assert.match(module, /importDep\("platform\/i18n\.mjs"/);
  assert.doesNotMatch(module, /window\.SanaUsers|window\.sanaRouteUsuarios|module\.exports/);

  const screen = read('templates/admin/users.html');
  assert.match(screen, /SANA_STATIC\[["']platform\/admin-users\.mjs["']\]/);
  assert.match(screen, /m\.boot\(\)/);
  assert.doesNotMatch(screen, /admin-users\.js|SanaUsers|sanaRouteUsuarios/);

  const content = read('static/platform/admin-content.mjs');
  assert.match(content, /importStatic\("platform\/admin-users\.mjs"\)/);
  assert.match(content, /usersReady/);
  assert.match(content, /Users\.mountDoctorUser/);
  assert.match(content, /ownRouteCleanup\(function \(\) \{ userOwner\.dispose\(\); \}\)/);
  assert.doesNotMatch(content, /window\.SanaUsers|admin-users\.js/);

  const account = read('static/platform/admin-account.mjs');
  assert.match(account, /map\["platform\/admin-users\.mjs"\]/);
  assert.match(account, /mod\.refresh\(\)/);
  assert.doesNotMatch(account, /window\.SanaUsers|admin-users\.js/);
});

test('Requests, the day queue and Doctors import canonical platform/admin-create-appointment.mjs with no compatibility global', () => {
  const module = read('static/platform/admin-create-appointment.mjs');
  assert.match(module, /export \{ abrir \}/);
  assert.match(module, /importDep\("platform\/dates\.mjs"/);
  assert.match(module, /importDep\("platform\/format\.mjs"/);
  assert.match(module, /importDep\("platform\/search\.mjs"/);
  assert.match(module, /importDep\("platform\/admin-session\.mjs"/);
  assert.match(module, /importDep\("platform\/i18n\.mjs"/);
  assert.match(module, /importDep\("platform\/dni-scan\.mjs"/);
  assert.match(module, /importDep\("platform\/dni-barcode\.mjs"/);
  assert.doesNotMatch(module, /window\.SanaCreateAppointment|module\.exports|admin-create-appointment\.js/);

  const app = read('static/root/admin-app.js');
  assert.match(app, /sanaImportStatic\(['"]platform\/admin-create-appointment\.mjs['"]\)/);
  assert.match(app, /function openCreateAppointment\(/);
  assert.match(app, /createAppointmentSeq/);
  assert.doesNotMatch(app, /SanaCreateAppointment|admin-create-appointment\.js/);

  const queue = read('static/platform/admin-queue.mjs');
  assert.match(queue, /sanaImportStatic\(["']platform\/admin-create-appointment\.mjs["']\)/);
  assert.match(queue, /function openCreateAppointment\(/);
  assert.doesNotMatch(queue, /SanaCreateAppointment|admin-create-appointment\.js/);

  const doctors = read('static/platform/admin-content.mjs');
  assert.match(doctors, /importStatic\("platform\/admin-create-appointment\.mjs"\)/);
  assert.match(doctors, /function openCreateAppointment\(/);
  assert.doesNotMatch(doctors, /SanaCreateAppointment|admin-create-appointment\.js/);

  assert.doesNotMatch(read('templates/admin/solicitudes.html'), /admin-create-appointment\.js/);
  assert.doesNotMatch(read('templates/admin/cola.html'), /admin-create-appointment\.js/);
  assert.doesNotMatch(read('templates/admin/content.html'), /admin-create-appointment\.js/);
});

test('home, lab-results, upload-order and server mounts import native listing with local readiness and no compatibility global', () => {
  const module = read('static/platform/listing.mjs');
  assert.match(module, /export \{/);
  assert.match(module, /render,/);
  assert.match(module, /importDep\(["']platform\/hours\.mjs["']/);
  assert.match(module, /importDep\(["']platform\/dates\.mjs["']/);
  assert.match(module, /importDep\(["']platform\/i18n\.mjs["']/);
  assert.match(module, /importDep\(["']platform\/search\.mjs["']/);
  assert.doesNotMatch(module, /window\.SanaListing|module\.exports/);

  const home = read('templates/index.html');
  assert.match(home, /SANA_STATIC\[["']platform\/listing\.mjs["']\]/);
  assert.match(home, /listingReady/);
  assert.match(home, /Listing\.render\(m1/);
  assert.match(home, /Listing\.logSearch/);
  assert.doesNotMatch(home, /static\(['"]listing\.js['"]\)|SanaListing/);

  const lab = read('static/platform/account-labs.mjs');
  assert.match(lab, /staticUrl\(["']platform\/listing\.mjs["']\)/);
  assert.match(lab, /Listing\.render/);
  assert.doesNotMatch(lab, /window\.SanaListing|staticUrl\(["']listing\.js["']\)|["']\/listing\.js["']/);

  const upload = read('app/modules/turnos/upload-order.mjs');
  assert.match(upload, /importStatic\(["']platform\/listing\.mjs["']\)/);
  assert.match(upload, /listingReady/);
  assert.match(upload, /Listing\.render/);
  assert.doesNotMatch(upload, /window\.SanaListing|["']listing\.js["']/);

  const story = read('stories/Listing.stories.js');
  assert.match(story, /from ["']\.\.\/static\/platform\/listing\.mjs["']/);
  assert.match(story, /render\(host, payload\)/);
  assert.doesNotMatch(story, /SanaListing|["']\.\.\/listing\.js["']/);
});

test('chat-widget and entrevista import native ChatUI with local readiness and no compatibility global', () => {
  const module = read('static/platform/chat-ui.mjs');
  assert.match(module, /export \{ escHtml, renderMini, crearLista, crearComposer, seguirViewport \}/);
  assert.match(module, /importDep\(["']platform\/i18n\.mjs["']/);
  assert.doesNotMatch(module, /window\.SanaChatUI|module\.exports/);

  const widget = read('static/platform/chat-widget.mjs');
  assert.doesNotMatch(widget, /window\.SanaChat\s*=|window\.ChatWidget\s*=/);
  assert.match(widget, /importStatic\(["']platform\/chat-ui\.mjs["']\)/);
  assert.match(widget, /ChatUI\.crearLista\(msgsEl/);
  assert.match(widget, /ChatUI\.crearComposer\(formEl/);
  assert.match(widget, /if \(!ChatUI\)/);
  assert.doesNotMatch(widget, /window\.SanaChatUI|["']chat-ui\.js["']/);

  const entrevista = read('app/modules/intake/entrevista.js');
  assert.match(entrevista, /importStatic\(["']platform\/chat-ui\.mjs["']\)/);
  assert.match(entrevista, /ChatUI\.crearLista\(/);
  assert.match(entrevista, /ChatUI\.crearComposer\(/);
  assert.match(entrevista, /if \(!ChatUI\) \{\s*renderError\(\);/);
  assert.doesNotMatch(entrevista, /window\.SanaChatUI|["']chat-ui\.js["']/);

  const story = read('stories/ChatUI.stories.js');
  assert.match(story, /from ["']\.\.\/static\/platform\/chat-ui\.mjs["']/);
  assert.match(story, /crearLista\(host, \{ prefix: "iv" \}\)/);
  assert.doesNotMatch(story, /SanaChatUI|["']\.\.\/chat-ui\.js["']/);
});

test('account pages, wizard, chat, requests and dependents import native Account with local readiness and no compatibility global', () => {
  const module = read('static/platform/account-form.mjs');
  assert.match(module, /export \{/);
  assert.match(module, /renderAuth,/);
  assert.match(module, /renderReset,/);
  assert.match(module, /parseFlexibleDate,/);
  assert.match(module, /dobField,/);
  assert.match(module, /fileToJpeg/);
  assert.match(module, /SANA_STATIC\[["']platform\/i18n\.mjs["']\]/);
  assert.match(module, /importStatic\(["']platform\/dni-barcode\.mjs["']\)/);
  assert.doesNotMatch(module, /window\.SanaAccount|module\.exports|i18nReady/);

  const account = read('static/platform/account.mjs');
  assert.match(account, /SANA_STATIC\[["']platform\/account-form\.mjs["']\]/);
  assert.match(account, /Promise\.all\(\[i18nReady, accountReady\]\)/);
  assert.match(account, /Account\.renderAuth/);
  assert.match(account, /Account\.renderEditar/);
  assert.match(account, /Account\.fileToJpeg/);
  assert.match(account, /account\.error\.formulario/);
  assert.match(account, /account-load-error/);
  assert.match(account, /role="alert"/);
  assert.match(account, /i18nReady\.catch\(function \(\) \{ return null; \}\)\.then\(showAccountLoadError\)/);
  assert.doesNotMatch(account, /color:var\(--accent\).*account\.error\.formulario/);
  assert.doesNotMatch(account, /SanaAccount|static\(['"]account-form\.js['"]\)/);

  const reset = read('templates/account-reset.html');
  assert.match(reset, /SANA_STATIC\[["']platform\/account-form\.mjs["']\]/);
  assert.match(reset, /Account\.renderReset/);
  assert.match(reset, /account\.error\.formulario/);
  assert.match(reset, /account-load-error/);
  assert.match(reset, /role="alert"/);
  assert.match(reset, /i18nReady\.catch\(function \(\) \{ return null; \}\)\.then\(showAccountLoadError\)/);
  assert.doesNotMatch(reset, /color:var\(--accent\).*account\.error\.formulario/);
  assert.doesNotMatch(reset, /SanaAccount|static\(['"]account-form\.js['"]\)/);

  const service = read('app/modules/turnos/service.mjs');
  assert.match(service, /function loadAccountForm\(\)/);
  assert.match(service, /importStatic\(["']platform\/account-form\.mjs["']\)/);
  const mountAuth = /function mountAuth\(\) \{[\s\S]*?\n    \}/.exec(service);
  assert.ok(mountAuth, 'mountAuth() must exist');
  assert.match(mountAuth[0], /wz-booking-summary/);
  assert.match(mountAuth[0], /loadAccountForm\(\)/);
  assert.match(mountAuth[0], /mod\.renderAuth/);
  assert.match(mountAuth[0], /currentStep !== "auth"/);
  assert.match(mountAuth[0], /host\.contains\(authHost\)/);
  assert.match(mountAuth[0], /href="\/account"/);
  assert.doesNotMatch(service, /SanaAccount|account-form\.js/);

  const dependents = read('static/platform/dependents.mjs');
  assert.match(dependents, /importDep\(["']platform\/account-form\.mjs["']/);
  assert.match(dependents, /accountReady/);
  assert.match(dependents, /accountModule\.parseFlexibleDate/);
  assert.match(dependents, /mod\.dobField/);
  assert.match(dependents, /birth_control_unavailable/);
  assert.match(dependents, /save\.disabled = true/);
  assert.match(dependents, /export \{ renderCuenta, renderSelector, renderAgregar, renderElegir/);
  assert.doesNotMatch(dependents, /function parseDate\(/);
  assert.doesNotMatch(dependents, /\.match\(\/\^\(\\d\{1,2\}/);
  assert.doesNotMatch(dependents, /window\.SanaDependents\s*=|module\.exports|SanaAccount|account-form\.js/);

  assert.match(account, /SANA_STATIC\[["']platform\/dependents\.mjs["']\]/);
  assert.match(account, /function mountDependents\(/);
  assert.match(account, /Dep\.renderCuenta/);
  assert.match(account, /account\.mine\.dependents\.load_error/);
  assert.doesNotMatch(account, /window\.SanaDependents\s*=|static\(['"]dependents\.js['"]\)/);

  assert.match(service, /function loadDependents\(\)/);
  assert.match(service, /importStatic\(["']platform\/dependents\.mjs["']\)/);
  assert.match(service, /function dependentsNoDisponibles\(\)/);
  assert.match(service, /service\.dependent\.load_error/);
  assert.match(service, /currentStep !== "paciente-agregar"/);
  assert.match(service, /currentStep !== "paciente-elegir"/);
  assert.match(service, /stillHere && !host\.contains\(stillHere\)/);
  assert.doesNotMatch(service, /\.catch\(dependentsNoDisponibles\)/);
  assert.doesNotMatch(service, /window\.SanaDependents|SanaDependents/);
});

test('classic T() consumers look platform/i18n.mjs up through window.SANA_STATIC', () => {
  for (const file of ['static/platform/nav.mjs', 'static/platform/account-form.mjs', 'app/modules/turnos/service.mjs', 'static/platform/chat-widget.mjs', 'static/root/admin-app.js']) {
    assert.match(read(file), /SANA_STATIC\[["']platform\/i18n\.mjs["']\]/,
      `${file} must resolve platform/i18n.mjs through the fingerprint manifest`);
  }
});
