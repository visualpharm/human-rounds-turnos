// admin-solicitudes-direct.test.mjs — Tier 5 Requests direct screen source
// contracts (docs/roadmap/tier5-frontend-design.md). Same source-on-disk
// pattern as admin-cola.test.mjs: node --test over the text of admin-app.js
// and templates/admin/solicitudes.html, no browser. Pins the frozen Terra
// findings so a regression on the direct screen can't slip past CI:
//
//   1. real direct boot — bootDirectScreen drives the shared Solicitudes
//      pipeline in dependency order, including prefsReady BEFORE
//      request hydration (finding #3).
//   2. session expiry — a dead session on a server-routed screen redirects to
//      /admin instead of calling mostrarGate on absent gate DOM (finding #5).
//   3. no unrelated fetches — a later requests reload does NOT fire the HSI
//      sync / Sistema/Plan/Prompts/Preparaciones section routes written for
//      the legacy hash shell (finding #4).
//   4. wrappers + dependency order + fingerprint — solicitudes.html restores
//      admin.html's .adm-topbar/.adm-h1row/.adm-content-wrap AND the hidden
//      #adm-sub subtitle, ships platform/photo-zoom.mjs + admin-control-fit.mjs +
//      the canonical admin-preferences.mjs through the manifest, and loads
//      the closed admin set via fingerprinted URLs (finding #1 / this round's
//      #3).
//   5. legacy hash behavior preserved — the gate/menu guards are node-present
//      checks, so legacy admin.html (which carries those nodes) keeps its
//      bindings byte-identical (finding #2). The WebAuthn passkey visibility
//      toggle reuses that same _gatePasskey binding, so a WebAuthn-capable
//      browser never tries to unhide a gate node the direct page never served
//      (this round's #1).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertSharedShellContract, BASE } from './shared-shell-contract.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const appPath = join(ROOT, 'static', 'root', 'admin-app.js');
const loginPath = join(ROOT, 'static', 'platform', 'admin-login.mjs');
const tplPath = join(ROOT, 'templates', 'admin', 'solicitudes.html');
const solicitudesPath = join(ROOT, 'static', 'platform', 'admin-solicitudes.mjs');
const APP = readFileSync(appPath, 'utf8');
const LOGIN = readFileSync(loginPath, 'utf8');
const TPL = readFileSync(tplPath, 'utf8');
const SOLICITUDES = readFileSync(solicitudesPath, 'utf8');

test('filter controls do not expose icon-font fallback words', () => {
  assert.doesNotMatch(TPL, />filter_list</);
});

test('request filters stay in normal flow when their readable controls need another line', () => {
  assert.match(SOLICITUDES, /"#sec-solicitudes \.adm-band\{gap:8px;padding:6px 16px;min-height:40px;box-sizing:border-box;flex:0 0 auto;/);
  assert.match(SOLICITUDES, /"flex-wrap:wrap;border-bottom:1px solid var\(--border\);margin:0;\}" \+/);
  assert.doesNotMatch(SOLICITUDES, /#sec-solicitudes \.adm-band\{[^}]*height:40px;flex:0 0 40px/);
  // The gear is the shared bare 44px header icon, never a 29px one.
  assert.doesNotMatch(SOLICITUDES, /\.cnt-gear-btn\{width:29px/);
});

test('Requests owns readable filter, derivation, and resolution controls', () => {
  // The filter toggle is the shared bare 44px icon of every list (one button
  // system, 2026-11-08): no framed copy of its own here.
  assert.doesNotMatch(SOLICITUDES, /#sec-solicitudes \.adm-filter-toggle\{/);
  assert.match(SOLICITUDES, /\.sol-kv-a \.svc-toggle \.adm-icon\{position:relative;display:block;width:18px;height:14px;font-size:0;/);
  assert.match(SOLICITUDES, /\.sol-res-row \.cmb-in\{font-size:16px;min-height:40px/);
  assert.match(SOLICITUDES, /\.sol-res-row \.adm-sel\{font-size:16px;min-height:40px/);
  assert.match(SOLICITUDES, /\.sol-res \.btn\{flex:0 0 auto;font-size:var\(--adm-btn-font,14px\);min-height:var\(--adm-btn-h,36px\);padding:0 12px;/);
  assert.match(SOLICITUDES, /@media\(pointer:coarse\)\{\.sol-res \.btn\{min-height:44px;\}\}/);
  assert.match(SOLICITUDES, /\.sol-res-aprobar>\.btn\{grid-column:2;\}/);
  // The request closes with the ✕ in its name row (Icons8 sf-regular), 44px
  // to the finger on a phone; no «Volver a la lista» band (2026-11-08).
  assert.doesNotMatch(SOLICITUDES, /sol-det-close/);
  // The icon is the product's one close button (.hr-x, human-rounds.css, 2026-11-09).
  assert.match(SOLICITUDES, /class="sol-det-x hr-x"/);
  assert.match(SOLICITUDES, /\(pointer:coarse\)\{\.sol-det\.card \.sol-det-x\{width:44px;height:44px;/);
  // One left edge for the doctor and the slot (2026-11-07): no column before
  // the slot; the time-of-day preference is its own line, the empty
  // placeholder takes no room.
  assert.match(SOLICITUDES, /\.sol-res-aprobar\{display:grid;grid-template-columns:minmax\(0,1fr\) auto;gap:8px/);
  assert.match(SOLICITUDES, /\.sol-res-aprobar>\.cmb\{grid-column:1\/-1;\}/);
  assert.match(SOLICITUDES, /\.sol-res-aprobar>\.pref-slot\{display:none;\}/);
  assert.match(SOLICITUDES, /\.sol-res-aprobar>\.pref-txt\{grid-column:1\/-1;/);
  assert.match(SOLICITUDES, /\.sol-res-aprobar>\.turno-sel\{grid-column:1;min-width:0;width:100%;\}/);
  assert.match(SOLICITUDES, /\.sol-kv-v\{color:var\(--text\);min-width:0;overflow-wrap:anywhere;white-space:normal;\}/);
  assert.doesNotMatch(SOLICITUDES, /font-size:(?:11|11\.5|12|12\.5)px/);
  assert.match(SOLICITUDES, /\.sol-row\{min-height:40px/);
  assert.match(SOLICITUDES, /\.sol-cell-centro\{white-space:normal;overflow:visible;text-overflow:clip;line-height:1\.25;padding:4px 0;\}/);
});

// ---- finding #3: real direct boot, preferences before request hydration ---

test('bootDirectScreen drives the shared pipeline in dependency order', () => {
  const fn = /async function bootDirectScreen\(\)\{[\s\S]*?\n\}/.exec(APP);
  assert.ok(fn, 'bootDirectScreen must exist');
  const body = fn[0];
  const steps = [
    'prefsReady',
    'applyRol(rol)',
    'await loadCatalogo()',
    'window.hydrateRequestPrefs()',
    'await load()',
    'syncCrearTurno()',
  ];
  const idx = steps.map((s) => body.indexOf(s));
  idx.forEach((i) => assert.ok(i !== -1, `bootDirectScreen missing step reference`));
  // Strictly ascending: each step runs AFTER the previous one (preferences
  // load before hydrateRequestPrefs, which runs before the requests load).
  for (let i = 1; i < idx.length; i++) {
    assert.ok(idx[i] > idx[i - 1], `bootDirectScreen step order wrong at ${steps[i]}`);
  }
});

test('the direct controller only boots on its owned screen', () => {
  assert.match(APP, /if\(window\.SANA_ADMIN_SCREEN === 'solicitudes'\) bootDirectScreen\(\);/);
});

// ---- finding #5: expired direct session returns through shared login --------

test('a helper redirects a server-routed screen through /admin/login with its return target', () => {
  const fn = /function redirigirSesionExpirada\(\)\{[\s\S]*?\n\}/.exec(LOGIN);
  assert.ok(fn, 'redirigirSesionExpirada must exist');
  assert.match(fn[0], /if\(!window\.SANA_ADMIN_SCREEN\) return false/);
  assert.match(fn[0], /'\/admin\/login\?next=' \+ encodeURIComponent\(target\)/);
  assert.match(fn[0], /targetPath !== '\/admin\/login'/);
});

test('api() delegates an exact boundary response to the canonical session module and keeps local failures local', () => {
  const branch = /async function api\(path, opts=\{\}\)\{[\s\S]*?\n\}/.exec(APP);
  assert.ok(branch, 'api() not found');
  const body = branch[0];
  assert.match(body, /res\.status === 401 \|\| res\.status === 403/);
  assert.match(body, /await sessionReady/);
  assert.match(body, /Session\.confirmar\(res\)/);
  assert.match(body, /if\(transitioned\) throw new Error\('401'\);/);
  assert.match(body, /if\(res\.status === 401\)\{[\s\S]*?throw new Error\('401'\);/);
  assert.doesNotMatch(body, /SanaAdminLogin|mostrarGate/);
});

test('session expiry is a direct login redirect; retired shell gates are absent', () => {
  assert.match(LOGIN, /function redirigirSesionExpirada\(\)/);
  assert.doesNotMatch(LOGIN, /mostrarGate|showGate|location\.hash|SanaAdminRouter|hashchange|#app/);
});

// ---- finding #4: no unrelated fetches on a later requests reload -----------

test('load() renders only Solicitudes and has no unrelated screen routes', () => {
  const fn = /async function load\(\)\{[\s\S]*?\n\}/.exec(APP);
  assert.ok(fn, 'load() not found');
  const body = fn[0];
  assert.match(body, /buildFilterOptions\(\);\s*render\(\);/);
  assert.doesNotMatch(body, /loadHsiSync|sanaRouteSistema|location\.hash/);
});

test('no EMR sync and no desk extension removes every HSI request-table affordance', () => {
  assert.match(APP, /let EMR_SYNC_ACTIVE = false;/);
  assert.match(APP, /let HSI_EXTENSION = null;/);
  // HSI shows up through the server push OR the desk's extension (its store
  // link in the list response), never otherwise (2026-10-27, d1122568).
  assert.match(APP, /function hsiActivo\(\)\{ return !!HsiSync && \(EMR_SYNC_ACTIVE \|\| !!HSI_EXTENSION\); \}/);
  assert.match(APP, /HSI_EXTENSION = EMR_SYNC_ACTIVE \? null : \(data\.hsi_extension \|\| null\);/);
  assert.match(APP, /if\(!hsiActivo\(\) \|\| !s\) return '';/);
  assert.match(APP, /\.concat\(hsiActivo\(\) \? \['hsi'\] : \[\]\)/);
  assert.match(APP, /hsiHtml: \(hsiActivo\(\) && !pend && s\.estado==='aprobada'\) \? hsiChip/);
  assert.match(APP, /const msg = !EMR_SYNC_ACTIVE \? T\("admin\.queue\.solicitudes\.aprobada"/);
  assert.match(APP, /: hsi==='ok' \? T\("admin\.queue\.solicitudes\.aprobada_hsi_ok"/);
});

// ---- finding #1: wrappers + dependency order + fingerprint -----------------

test('solicitudes.html restores the legacy header/content wrappers', () => {
  assertSharedShellContract(assert, TPL, [
    'platform/photo-zoom.mjs', 'platform/admin-control-fit.mjs',
    'admin-app.js',
  ]);
  assert.match(BASE, /<header class="adm-topbar">/);
  assert.match(BASE, /class="adm-h1row"/);
  assert.match(TPL, /<div class="adm-content-wrap">/);
  // #sec-solicitudes is a DIRECT child of .adm-content-wrap — the
  // `.adm-content-wrap:has(> #sec-solicitudes…)` selector requires it. The
  // hidden #adm-sub paragraph is a SIBLING of #sec-solicitudes (both direct
  // children), exactly as admin.html ships it (lines 1238-1241), so it may
  // sit between the wrapper's opening tag and the section. No intervening
  // <div> wrapper is allowed — that would break the cap.
  assert.match(
    TPL,
    /<div class="adm-content-wrap">\s*(?:<p class="adm-sub"[^>]*><\/p>\s*)?<div id="sec-solicitudes"(?: data-disclosure="loading")?>/,
  );
});

test('solicitudes.html restores the hidden #adm-sub subtitle node (Terra finding #3)', () => {
  // admin.html carries <p class="adm-sub" id="adm-sub" style="display:none;">
  // inside .adm-content-wrap right before #sec-solicitudes; the controller
  // reaches #adm-sub (routeHsi hides it). The direct template restores it for
  // DOM parity, kept hidden exactly as the legacy page ships it.
  assert.match(TPL, /<p class="adm-sub" id="adm-sub" style="display:none;">/);
});

test('the create-appointment hook sits in the legacy adm-h1row header', () => {
  assert.match(BASE, /<h1 id="adm-h1" class="adm-h1">/);
  assert.match(BASE, /\{% block title_band %\}/);
  assert.match(TPL, /\{% block title_band %\}/);
  assert.match(TPL, /id="adm-crear-turno"/);
});

test('solicitudes.html seeds CURRENT_ROLE and the screen sentinel is in base.html', () => {
  assert.match(TPL, /window\.CURRENT_ROLE = "\{\{ rol \}\}";/);
  const base = readFileSync(join(ROOT, 'templates', 'admin', 'base.html'), 'utf8');
  assert.match(base, /window\.SANA_ADMIN_SCREEN = "\{\{ active_section \}\}";/);
});

test('the dependency cohort loads in the right order with native photo zoom + admin-control-fit present', () => {
  // Mirror of admin.html's own load sequence (admin.html:1337-1415) for the
  // cohort this screen needs, INCLUDING the two restored dependencies (Terra
  // finding #3): the native photo module sits where admin.html loaded it (right after
  // i18n.js — owns the [data-photozoom] order-image viewer click handler) and
  // admin-control-fit.mjs sits where admin.html loads it (right after
  // format.mjs — self-boots and measures the .adm-sel filter widths).
  // Search on the module loader expression so documentation text cannot
  // mention doesn't collide with the real <script> tag.
  const order = [
    'type="module" src="{{ static(\'platform/photo-zoom.mjs\') }}"',
    'type="module" src="{{ static(\'platform/admin-control-fit.mjs\') }}"',
    'src="{{ static(\'admin-app.js\') }}"',
  ];
  const idx = order.map((s) => TPL.indexOf(s));
  idx.forEach((i, n) => assert.ok(i !== -1, `solicitudes.html missing script ref: ${order[n]}`));
  for (let i = 1; i < idx.length; i++) {
    assert.ok(idx[i] > idx[i - 1], `script order wrong at ${order[i]}`);
  }
});

test('the closed admin cohort loads fingerprinted, not via the ?v= alias', () => {
  // The FUNCTION form {{ static('...') }} resolves to a fingerprinted URL at
  // render time. Asserting the template uses that form (not /admin-*.js?v=N)
  // is what makes the fingerprint contract real for this screen. String
  // includes (not regex) — the `{{ static('…') }}` form has no regex metachars
  // worth balancing.
  ['admin-app.js', 'platform/photo-zoom.mjs',
   'platform/admin-control-fit.mjs'].forEach((name) => {
    assert.ok(TPL.includes(`{{ static('${name}') }}`), `${name} must load via {{ static() }}`);
    assert.ok(!TPL.includes(`/${name}?v=`), `${name} must not use the ?v= alias`);
  });
  assert.ok(!TPL.includes('admin-combo.js'));
  assert.match(APP, /sanaImportStatic\('platform\/admin-combo\.mjs'\)/);
  assert.match(APP, /sanaImportStatic\('platform\/admin-preferences\.mjs'\)/);
  assert.doesNotMatch(APP, /SanaPreferences/);
  assert.doesNotMatch(TPL, /admin-preferences\.js/);
});

test('Solicitudes has no retired drawer wiring', () => {
  assert.doesNotMatch(APP, /adm-menu-toggle|adm-menu-scrim|setMenuOpen/);
  assert.match(LOGIN, /const _gateForm=\$\('#gate-form'\);\s*if\(_gateForm\) _gateForm\.addEventListener/);
  assert.match(LOGIN, /const _gatePasskey=\$\('#gate-passkey'\);\s*if\(_gatePasskey\) _gatePasskey\.addEventListener/);
});

test('WebAuthn passkey visibility is null-safe when the direct page has no gate node', () => {
  // The WebAuthn visibility toggle MUST reuse
  // the _gatePasskey binding (null on a server-routed screen) so a
  // WebAuthn-capable browser never tries to unhide a node the page never
  // served. The canonical module promise remains behind that same node guard.
  assert.match(
    LOGIN,
    /if\(_gatePasskey\) passkeysLista\.then\(function\(\)\{\s*if\(PASSKEYS && PASSKEYS\.soportado\(\)\) _gatePasskey\.hidden = false;/,
  );
  assert.doesNotMatch(LOGIN, /window\.SanaPasskeys/);
  // And the bare unguarded form that threw on the direct page is GONE.
  assert.doesNotMatch(LOGIN, /\$\('#gate-passkey'\)\.hidden = false/);
});

test('load() has no retired gate or root-shell writes', () => {
  const fn = /async function load\(\)\{[\s\S]*?\n\}/.exec(APP);
  assert.ok(fn);
  assert.doesNotMatch(fn[0], /gate-wrap|#app/);
});

test('the controller has no hash-shell boot path', () => {
  assert.doesNotMatch(APP, /routeCurrentAdminScreen|SanaAdminRouter|hashchange|location\.hash/);
});

// ---- the template must not regress to the hash shim -----------------------

test('solicitudes.html never forces location.hash (the audit fix holds)', () => {
  assert.doesNotMatch(TPL, /location\.hash\s*=/);
});

test('the today summary reaches the direct queue route', () => {
  const solicitudes = readFileSync(join(ROOT, 'static', 'platform', 'admin-solicitudes.mjs'), 'utf8');
  assert.match(solicitudes, /class="sol-hoy-link" href="\/admin\/cola"/);
  assert.doesNotMatch(solicitudes, /class="sol-hoy-link" href="#cola"/);
});


test('today queue summary aligns its combined appointment and availability count', () => {
  const solicitudes = readFileSync(join(ROOT, 'static', 'platform', 'admin-solicitudes.mjs'), 'utf8');
  assert.match(solicitudes, /sol-hoy-tabla \.cnt-numeric\{text-align:right;\}/);
  assert.ok(solicitudes.includes('<td class=\\"cnt-numeric\\">'));
  assert.match(solicitudes, /esc\(totalTxt \+ libresTxt\)/);
});

// ---- the row is read at 1440px: no truncated service/date, no light gray ----

test('service takes the spare width and the date column fits «Dom 20 sep, 11:00» at 16px', () => {
  assert.match(SOLICITUDES, /servicio: "minmax\(160px,1\.2fr\)"/);
  assert.match(SOLICITUDES, /cuando: "140px"/);
  assert.match(SOLICITUDES, /minmax\(160px,1\.2fr\) 140px !important/);
});

test('every value in a request row and group header is 16px in the text color', () => {
  const rule = (cls) => {
    const m = SOLICITUDES.match(new RegExp('"\\.' + cls + '\\{([^}]*)\\}'));
    assert.ok(m, cls);
    return m[1];
  };
  for (const cls of ['sol-grp-n', 'sol-when', 'sol-vd-t']) {
    assert.match(rule(cls), /font-size:16px/, cls);
    assert.match(rule(cls), /color:var\(--text\)/, cls);
  }
  for (const cls of ['sol-mut', 'sol-num']) assert.match(rule(cls), /color:var\(--text\)/, cls);
  assert.match(SOLICITUDES, /font:inherit;font-size:16px;line-height:1\.4;text-align:left;color:var\(--text\);/);
  assert.doesNotMatch(SOLICITUDES, /"\.sol-(?:mut|num|when|vd-t|grp-c)\{[^}]*var\(--text-[23]\)/);
});
