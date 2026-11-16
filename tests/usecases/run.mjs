// run.mjs — drives the 46-case use-case acceptance matrix of
// the use-case matrix against the local server at
// http://127.0.0.1:7700 (fake-AI preview DB, seeded by fixtures.py).
//
// Every case: real-browser Action, Expected assertion, one desktop PNG
// (1280x900) + one phone PNG (390x844) per ID into artifacts/usecases/,
// and one results.json entry {id, group, title, verdict, assertion, observed}.
// Verdicts: pass | FAIL | known-defect (CURRENT of a CURRENT/REQUIRED row,
// observed) | blocked (feature not built, e.g. dormant R5 rows).
//
// Run: node tests/usecases/run.mjs

import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const ART = path.join(ROOT, 'artifacts', 'usecases');
const BASE = process.env.UC_BASE || 'http://127.0.0.1:7700';

// ---- install-timezone date math (America/Argentina/Buenos_Aires, UTC-3) ----
const TZ_OFFSET_MS = 3 * 3600 * 1000;
const artNow = () => new Date(Date.now() - TZ_OFFSET_MS);
const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (d, n) => new Date(d.getTime() + n * 86400000);
const nextWeekday = (d) => {
  let x = new Date(d.getTime());
  while (x.getUTCDay() === 0 || x.getUTCDay() === 6) x = addDays(x, 1);
  return x;
};
const dmy = (d) => `${String(d.getUTCDate()).padStart(2, '0')}/${String(d.getUTCMonth() + 1).padStart(2, '0')}/${d.getUTCFullYear()}`;

// ---- credentials ------------------------------------------------------------
// Suite-owned accounts (fixtures.py). The staff passwords are the ones group A
// itself types into the create form; the admin/patient pair comes from the
// generated creds file (never a literal in source).
const CREDS = JSON.parse(fs.readFileSync(path.join(ART, '.creds.json'), 'utf8'));
const PW = {
  adm: 'Mesa-Adm-2026',   // adm.mesa1 (admisionista)
  med: 'Med-Sobre-2026',  // med.sobre (medico)
  rep: 'Rep-Jefatura-2026', // rep.jefatura (reportes)
  adm2: 'Suplente-2026',  // adm.suplente (admin, B8's fresh admin login)
};

// ---- results ----------------------------------------------------------------
const results = [];
const record = (entry) => {
  results.push(entry);
  const flag = entry.verdict === 'pass' ? '✓' : entry.verdict === 'known-defect' ? '◦' : '✗';
  console.log(`${flag} ${entry.id} [${entry.verdict}] ${entry.title}`);
  if (entry.verdict === 'FAIL' || entry.verdict === 'blocked') {
    console.log(`    ${String(entry.observed).slice(0, 300)}`);
  }
};
async function runCase(id, group, title, known, fn) {
  let verdict, assertion = '', observed = '';
  try {
    const out = await fn();
    assertion = out.assertion || '';
    observed = out.observed || '';
    if (out.ok) verdict = known ? 'known-defect' : 'pass';
    else verdict = 'FAIL';
  } catch (e) {
    verdict = 'FAIL';
    assertion = fn.assertion || '';
    observed = `runner error: ${String(e && e.message || e).slice(0, 400)}`;
  }
  record({ id, group, title, verdict, assertion, observed });
}

// ---- screenshots ------------------------------------------------------------
const browser = await chromium.launch();
async function makeCtx() {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  return ctx;
}
async function shoot(page, id) {
  const desktop = path.join(ART, `${id}.png`);
  try { await page.screenshot({ path: desktop, fullPage: true }); } catch { /* page may have navigated */ }
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(ART, `${id}-phone.png`), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
  } catch { /* best effort */ }
}

// ---- shared sessions --------------------------------------------------------
const ctxAdmin = await makeCtx(); const pgAdmin = await ctxAdmin.newPage();   // uc.suite (admin)
const ctxAdm = await makeCtx(); const pgAdm = await ctxAdm.newPage();         // adm.mesa1
const ctxMed = await makeCtx(); const pgMed = await ctxMed.newPage();         // med.sobre
const ctxRep = await makeCtx(); const pgRep = await ctxRep.newPage();         // rep.jefatura
const ctxSup = await makeCtx(); const pgSup = await ctxSup.newPage();         // adm.suplente (B8)
const ctxPat = await makeCtx(); const pgPat = await ctxPat.newPage();         // patient 30444001
const ctxFresh = await makeCtx(); const pgFresh = await ctxFresh.newPage();   // no session (B9)

async function adminLogin(page, user, pass) {
  await page.goto(`${BASE}/admin/login`);
  await page.locator('#gate-user').waitFor({ timeout: 15000 });
  await page.fill('#gate-user', user);
  await page.fill('#gate-pass', pass);
  // /\/admin\/[a-z-]+/ would match /admin/login itself and resolve instantly,
  // racing the form POST — the lookahead pins the post-login destination.
  await Promise.all([
    page.waitForURL(/\/admin\/(?!login)[a-z-]+/, { timeout: 15000 }),
    page.locator('#gate-btn').click(),
  ]);
  return page.url();
}
async function patientLogin(page) {
  await page.goto(`${BASE}/account`);
  await page.locator('#sc-dni').waitFor({ timeout: 15000 });
  const swap = page.locator('#sc-swap');
  if (await swap.count() && !/crear una/i.test(await swap.innerText())) {
    await swap.click();
    await page.waitForTimeout(300);
  }
  await page.fill('#sc-dni', CREDS.patient_dni);
  await page.fill('#sc-pass', CREDS.patient_pass);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/account/login'), { timeout: 15000 }),
    page.click('#sc-submit'),
  ]);
  await page.waitForTimeout(600);
}

// ---- HTTP helper (carries the context's cookies) -----------------------------
async function httpGet(ctx, url) {
  const r = await ctx.request.get(url);
  let body = '';
  try { body = await r.text(); } catch { /* empty */ }
  return { status: r.status(), body, json: (() => { try { return JSON.parse(body); } catch { return null; } })() };
}

// ---- users-form helpers (group A) --------------------------------------------
async function openUserForm(page) {
  await page.goto(`${BASE}/admin/users`);
  await page.locator('a[data-users-state="new"]').waitFor({ timeout: 15000 });
  await page.click('a[data-users-state="new"]');
  await page.waitForURL(/new=1/, { timeout: 10000 });
  await page.locator('#usr-usuario').waitFor({ timeout: 10000 });
}
async function fillUserForm(page, { usuario, email, pass, rol, medico }) {
  if (usuario !== undefined) await page.fill('#usr-usuario', usuario);
  if (email !== undefined) await page.fill('#usr-email', email);
  if (pass !== undefined) await page.fill('#usr-pass', pass);
  if (rol) await page.check(`input[name="usr-rol"][value="${rol}"]`);
  if (medico) await page.selectOption('#usr-medico', medico);
}
async function submitUser(page) {
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith('/api/admin/users') && r.request().method() === 'POST', { timeout: 15000 }),
    page.click('button[type="submit"][form="usr-form"]'),
  ]);
  let json = null;
  try { json = await resp.json(); } catch { /* non-JSON */ }
  return { status: resp.status(), json };
}
async function rolOf(page, usuario) {
  await page.goto(`${BASE}/admin/users?user=${encodeURIComponent(usuario)}`);
  await page.locator('input[name="usr-rol"]').first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(300);
  return page.locator('input[name="usr-rol"]:checked').getAttribute('value');
}

// ---- wizard helpers (groups C/D) ----------------------------------------------
async function openWizard(page, svcId) {
  await page.goto(`${BASE}/service/${svcId}/book?slots=1`);
  await page.waitForTimeout(2500); // slots fetch + render
  return {
    chips: page.locator('button[data-slot][data-dia]'),
    days: page.locator('div.wz-day'),
  };
}
async function chipsPerDay(page) {
  return page.evaluate(() => Array.from(document.querySelectorAll('div.wz-day')).map((d) => ({
    n: d.querySelectorAll('button[data-slot]').length,
    first: d.querySelector('button[data-slot]')?.getAttribute('data-dia') || '',
    labels: Array.from(d.querySelectorAll('button[data-slot]')).map((b) => b.textContent.trim()),
  })));
}
async function bookCurrentSlot(page, svcId) {
  const w = await openWizard(page, svcId);
  const n = await w.chips.count();
  if (!n) return { status: 0, json: null, error: 'no slot chips rendered' };
  const [respP] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/requests') && r.request().method() === 'POST', { timeout: 20000 }),
    (async () => {
      await w.chips.first().click();
      await page.locator('#wz-send').waitFor({ timeout: 15000 });
      await page.locator('#wz-send').click();
    })(),
  ]);
  let json = null;
  try { json = await respP.json(); } catch { /* non-JSON */ }
  return { status: respP.status(), json };
}
async function misTurnos(page, svcName) {
  await page.goto(`${BASE}/my-requests`);
  await page.locator('section.req').first().waitFor({ timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(800);
  return page.evaluate((name) => Array.from(document.querySelectorAll('section.req'))
    .filter((s) => s.textContent.includes(name)).length, svcName);
}

// ---- doctors editor helpers (group C) ------------------------------------------
async function openDoctorEditor(page, docId) {
  await page.goto(`${BASE}/admin/doctors?item=${encodeURIComponent(docId)}`);
  await page.locator('#cnt-form, .cnt-centro-block').first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(600); // hours editor paints
}
function franja(page, idx = 0) {
  return page.locator('.cnt-franja-row').nth(idx);
}

// ---- cola / crear-turno helpers (D8, E) ----------------------------------------
async function colaOpenDoctor(page, doctorName) {
  await page.goto(`${BASE}/admin/cola`);
  await page.locator('button.queue-doctor-link[data-cola-doctor]').first().waitFor({ timeout: 15000 });
  const rows = page.locator('button.queue-doctor-link[data-cola-doctor]');
  const n = await rows.count();
  let target = null;
  for (let i = 0; i < n; i++) {
    const v = await rows.nth(i).getAttribute('data-cola-doctor');
    if (v && v.toLowerCase().includes(doctorName.toLowerCase())) { target = rows.nth(i); break; }
  }
  if (!target) target = rows.first();
  await target.click();
  await page.locator('.queue-doctor-head').waitFor({ timeout: 15000 });
  await page.locator('#adm-crear-turno').waitFor({ timeout: 15000 });
}
async function ctOpen(page, medico) {
  // A previous case's modal may still be open (abrir() no-ops while one is) —
  // close it through the product's own control first.
  const close = page.locator('#ct-close');
  if (await close.count()) {
    await close.click();
    await page.locator('#ct-back').waitFor({ state: 'detached', timeout: 5000 }).catch(() => {});
  }
  if (medico) {
    // The cola only lists doctors with TODAY's appointments, so a doctor whose
    // given is "zero bookings" has no row there. The overlay is opened through
    // its own exported hook — the exact function the cola button invokes.
    await page.evaluate(async (m) => {
      const url = window.SANA_STATIC["platform/admin-create-appointment.mjs"];
      const mod = await import(url);
      return mod.abrir({ medico: m });
    }, medico);
  } else {
    await page.click('#adm-crear-turno');
  }
  await page.locator('#ct-modal').waitFor({ timeout: 15000 });
  await page.waitForTimeout(900); // catalog + prefill
}
async function ctPickService(page, query) {
  await page.fill('#ct-svc', query);
  await page.locator('#ct-svc-sug li[data-svc]').first().waitFor({ timeout: 10000 });
  await page.locator('#ct-svc-sug li[data-svc]').first().click();
  await page.waitForTimeout(500);
}
// The chosen SERVICE step (`.ct-elegido` is also the patient chip's class —
// scope to the paso labelled "Servicio").
function ctServicioElegido(page) {
  return page.locator('.ct-paso:has(.ct-lbl:text-is("Servicio")) .ct-elegido b');
}
async function ctPickPatient(page, query) {
  await page.fill('#ct-pac', query);
  await page.locator('#ct-pac-sug li[data-dni]').first().waitFor({ timeout: 10000 });
  await page.locator('#ct-pac-sug li[data-dni]').first().click();
  await page.waitForTimeout(500);
}
async function ctCalendarFirstDay(page) {
  await page.locator('#ct-vermas').click();
  await page.locator('.ct-cal-dia.tiene').first().waitFor({ timeout: 10000 });
  await page.locator('.ct-cal-dia.tiene').first().click();
  await page.locator('.slot-time').first().waitFor({ timeout: 10000 });
}

// =============================================================================
// BOOT: seed, then drive the matrix in ID order.
// =============================================================================
fs.mkdirSync(ART, { recursive: true });
console.log('seeding fixtures…');
execFileSync('python3', [path.join(HERE, 'fixtures.py')], { cwd: ROOT, stdio: 'inherit' });
await new Promise((r) => setTimeout(r, 3500)); // catalog overlay TTL

console.log(`\n=== A — Administrator creates staff (${BASE}) ===`);

await runCase('A1', 'A', 'Admin abre el formulario de nuevo usuario', false, async () => {
  await adminLogin(pgAdmin, CREDS.admin_user, CREDS.admin_pass);
  await openUserForm(pgAdmin);
  const roles = await pgAdmin.locator('input[name="usr-rol"]').evaluateAll(
    (els) => els.map((e) => e.getAttribute('value')).sort());
  // The form's own title (admin-users.js:432) — the page h1 stays "Usuarios".
  const titulo = await pgAdmin.locator('#adm-h1, .cnt-form, main').first().innerText();
  const ok = /nuevo usuario/i.test(titulo)
    && JSON.stringify(roles) === JSON.stringify(['admin', 'admisionista', 'medico', 'reportes']);
  await shoot(pgAdmin, 'a1-users-new-form');
  return { ok, assertion: 'Form at /admin/users?new=1 titled "Nuevo usuario"; role radios exactly {medico, admisionista, reportes, admin}', observed: `form-title ok=${/nuevo usuario/i.test(titulo)}, roles=${JSON.stringify(roles)}` };
});

await runCase('A2', 'A', 'Crear adm.mesa1 (admisionista)', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'adm.mesa1', email: 'adm.mesa1@uc-demo.example.com', pass: PW.adm, rol: 'admisionista' });
  const r = await submitUser(pgAdmin);
  await pgAdmin.locator('#usr-tbody a[data-user="adm.mesa1"]').waitFor({ timeout: 10000 }).catch(() => {});
  const rol = await rolOf(pgAdmin, 'adm.mesa1').catch(() => null);
  await shoot(pgAdmin, 'a2-admisionista-created');
  return { ok: r.status === 200 && rol === 'admisionista', assertion: 'List contains usuario=adm.mesa1, rol=admisionista, active', observed: `POST ${r.status}; detail rol=${rol}` };
});

await runCase('A3', 'A', 'Crear med.sobre (medico) sin médico del catálogo', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'med.sobre', email: 'med.sobre@uc-demo.example.com', pass: PW.med, rol: 'medico' });
  // UI submit with no doctor: the client guard fires first (toast, no POST).
  await pgAdmin.click('button[type="submit"][form="usr-form"]');
  await pgAdmin.waitForTimeout(700);
  const toastTxt = (await pgAdmin.locator('.toast, [class*="toast"]').first().innerText().catch(() => '')) || '';
  // The matrix's Expected is the SERVER contract (admin_users.py:191) — hit the
  // same POST directly to assert it, same session, same payload the form sends.
  const r = await pgAdmin.evaluate(async ({ u, p }) => {
    const res = await fetch('/api/admin/users', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ usuario: u, password: p, rol: 'medico', medico_id: '', email: '', centros: [] }),
    });
    return { status: res.status, body: await res.json().catch(() => ({})) };
  }, { u: 'med.sobre', p: PW.med });
  await shoot(pgAdmin, 'a3-medico-sin-doctor');
  const ok = r.status === 400 && /médico del catálogo/i.test(r.body.error || '');
  return { ok, assertion: 'HTTP 400, error "Elegí un médico del catálogo para esta cuenta." (admin_users.py:191)', observed: `client guard toast="${toastTxt.slice(0, 60)}"; direct POST ${r.status} ${JSON.stringify(r.body).slice(0, 140)}` };
});

await runCase('A4', 'A', 'Crear med.sobre (medico) con Dr. Único del catálogo', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'med.sobre', email: 'med.sobre@uc-demo.example.com', pass: PW.med, rol: 'medico', medico: 'dr-unico' });
  const r = await submitUser(pgAdmin);
  await pgAdmin.locator('#usr-tbody a[data-user="med.sobre"]').waitFor({ timeout: 10000 }).catch(() => {});
  await pgAdmin.goto(`${BASE}/admin/users?user=med.sobre`);
  await pgAdmin.locator('#usr-medico').waitFor({ timeout: 10000 });
  const linked = await pgAdmin.locator('#usr-medico').inputValue();
  await shoot(pgAdmin, 'a4-medico-created');
  return { ok: r.status === 200 && linked === 'dr-unico', assertion: 'List contains med.sobre linked to Dr. Único (medico_id set)', observed: `POST ${r.status}; #usr-medico=${linked}` };
});

await runCase('A5', 'A', 'Crear rep.jefatura (reportes)', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'rep.jefatura', email: 'rep.jefatura@uc-demo.example.com', pass: PW.rep, rol: 'reportes' });
  const r = await submitUser(pgAdmin);
  const rol = await rolOf(pgAdmin, 'rep.jefatura').catch(() => null);
  await shoot(pgAdmin, 'a5-reportes-created');
  return { ok: r.status === 200 && rol === 'reportes', assertion: 'List contains rep.jefatura, rol=reportes', observed: `POST ${r.status}; detail rol=${rol}` };
});

await runCase('A6', 'A', 'Crear adm.suplente (admin)', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'adm.suplente', email: 'adm.suplente@uc-demo.example.com', pass: PW.adm2, rol: 'admin' });
  const r = await submitUser(pgAdmin);
  const rol = await rolOf(pgAdmin, 'adm.suplente').catch(() => null);
  await shoot(pgAdmin, 'a6-admin-created');
  return { ok: r.status === 200 && rol === 'admin', assertion: 'List contains adm.suplente, rol=admin', observed: `POST ${r.status}; detail rol=${rol}` };
});

await runCase('A7', 'A', 'Usuario de 2 caracteres → rechazado', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'ab', email: 'ab@uc-demo.example.com', pass: PW.adm2, rol: 'reportes' });
  const r = await submitUser(pgAdmin);
  await shoot(pgAdmin, 'a7-username-invalid');
  const err = (r.json && r.json.error) || '';
  const ok = r.status === 400 && err.startsWith('El usuario tiene que tener entre 3 y 40 caracteres');
  return { ok, assertion: 'HTTP 400, error starts "El usuario tiene que tener entre 3 y 40 caracteres" (admin_users.py:171-175)', observed: `${r.status} ${err}` };
});

await runCase('A8', 'A', 'Contraseña de 7 caracteres → rechazada', false, async () => {
  await openUserForm(pgAdmin);
  await fillUserForm(pgAdmin, { usuario: 'corta.clave', email: 'corta@uc-demo.example.com', pass: 'abc123', rol: 'reportes' });
  const r = await submitUser(pgAdmin);
  await shoot(pgAdmin, 'a8-password-short');
  const err = (r.json && r.json.error) || '';
  const ok = r.status === 400 && err === 'La contraseña tiene que tener al menos 8 caracteres.';
  return { ok, assertion: 'HTTP 400, error "La contraseña tiene que tener al menos 8 caracteres." (admin_users.py:176-179)', observed: `${r.status} ${err}` };
});

await runCase('A9', 'A', 'No existe el rol "gestor de agendas" (R5 pendiente)', true, async () => {
  await openUserForm(pgAdmin);
  const roles = await pgAdmin.locator('input[name="usr-rol"]').evaluateAll((els) => els.map((e) => e.getAttribute('value')));
  await shoot(pgAdmin, 'a9-no-gestor-role');
  const ok = roles.length === 4 && !roles.includes('gestor');
  return { ok, assertion: 'CURRENT: exactly 4 role radios — no gestor role exists. REQUIRED: fifth role (gestor de agendas)', observed: `roles=${JSON.stringify(roles)}` };
});

console.log('\n=== B — Permission boundaries ===');

await runCase('B1', 'B', 'Login admisionista → /admin/solicitudes', false, async () => {
  const url = await adminLogin(pgAdm, 'adm.mesa1', PW.adm);
  const nav = await pgAdm.locator('nav a, aside a, [class*="nav"] a').allInnerTexts();
  const navTxt = nav.join(' | ');
  const hasUsuarios = /usuarios/i.test(navTxt);
  await shoot(pgAdm, 'b1-admisionista-landing');
  const ok = /\/admin\/solicitudes$/.test(url) && /solicitudes/i.test(navTxt) && /pacientes/i.test(navTxt) && !hasUsuarios;
  return { ok, assertion: 'Final URL /admin/solicitudes; sidebar shows Solicitudes / Pacientes de hoy / Pacientes and no "Usuarios"', observed: `url=${url}; usuarios en nav=${hasUsuarios}` };
});

await runCase('B2', 'B', 'Admisionista GET /admin/users → 403 solo_admin', false, async () => {
  const r = await httpGet(ctxAdm, `${BASE}/admin/users`);
  await pgAdm.goto(`${BASE}/admin/users`).catch(() => {});
  await shoot(pgAdm, 'b2-admisionista-users-403');
  const ok = r.status === 403 && r.json && r.json.error === 'solo_admin';
  return { ok, assertion: 'HTTP 403, body error solo_admin (admin_users_router.py:46, admin_auth.py:472)', observed: `${r.status} ${r.body.slice(0, 120)}` };
});

await runCase('B3', 'B', 'Admisionista GET /admin/doctors → 403 solo_admin', false, async () => {
  const r = await httpGet(ctxAdm, `${BASE}/admin/doctors`);
  await pgAdm.goto(`${BASE}/admin/doctors`).catch(() => {});
  await shoot(pgAdm, 'b3-admisionista-doctors-403');
  const ok = r.status === 403 && r.json && r.json.error === 'solo_admin';
  return { ok, assertion: 'HTTP 403, body error solo_admin (the screen gate is require_admin(solo_admin=True), admin_content_screen.py:64 — the matrix\'s old rol_no_autorizado cited the retired hash shell)', observed: `${r.status} ${r.body.slice(0, 120)}` };
});

await runCase('B4', 'B', 'Login medico → /admin/cola', false, async () => {
  const url = await adminLogin(pgMed, 'med.sobre', PW.med);
  await shoot(pgMed, 'b4-medico-landing');
  return { ok: /\/admin\/cola$/.test(url), assertion: 'Final URL /admin/cola (medico default, admin_navigation.py:31)', observed: `url=${url}` };
});

await runCase('B5', 'B', 'Medico GET /admin/pacientes → 403 rol_no_autorizado', false, async () => {
  const r = await httpGet(ctxMed, `${BASE}/admin/pacientes`);
  await pgMed.goto(`${BASE}/admin/pacientes`).catch(() => {});
  await shoot(pgMed, 'b5-medico-pacientes-403');
  const ok = r.status === 403 && r.json && r.json.error === 'rol_no_autorizado';
  return { ok, assertion: 'HTTP 403, body error rol_no_autorizado (Pacientes is admin/admisionista only)', observed: `${r.status} ${r.body.slice(0, 120)}` };
});

await runCase('B6', 'B', 'Login reportes → /admin/reportes', false, async () => {
  const url = await adminLogin(pgRep, 'rep.jefatura', PW.rep);
  await shoot(pgRep, 'b6-reportes-landing');
  return { ok: /\/admin\/reportes/.test(url), assertion: 'Final URL /admin/reportes', observed: `url=${url}` };
});

await runCase('B7', 'B', 'Reportes GET /admin/cola → 403 rol_no_autorizado', false, async () => {
  const r = await httpGet(ctxRep, `${BASE}/admin/cola`);
  await pgRep.goto(`${BASE}/admin/cola`).catch(() => {});
  await shoot(pgRep, 'b7-reportes-cola-403');
  const ok = r.status === 403 && r.json && r.json.error === 'rol_no_autorizado';
  return { ok, assertion: 'HTTP 403, body error rol_no_autorizado', observed: `${r.status} ${r.body.slice(0, 120)}` };
});

await runCase('B8', 'B', 'Login admin → /admin/solicitudes; /admin/system 200', false, async () => {
  const url = await adminLogin(pgSup, 'adm.suplente', PW.adm2);
  const sys = await httpGet(ctxSup, `${BASE}/admin/system`);
  await pgSup.goto(`${BASE}/admin/system`);
  await pgSup.waitForTimeout(400);
  await shoot(pgSup, 'b8-admin-landing');
  const ok = /\/admin\/solicitudes$/.test(url) && sys.status === 200;
  return { ok, assertion: 'Final URL /admin/solicitudes; /admin/system answers 200', observed: `url=${url}; /admin/system=${sys.status}` };
});

await runCase('B9', 'B', 'Sin sesión GET /admin/users → redirect a /admin/login?next=', false, async () => {
  const resp = await pgFresh.goto(`${BASE}/admin/users`, { waitUntil: 'domcontentloaded' });
  const status = resp.status();
  const finalUrl = pgFresh.url();
  await shoot(pgFresh, 'b9-no-session-redirect');
  const ok = status >= 300 && status < 400 && /\/admin\/login/.test(finalUrl) && /next=/.test(finalUrl);
  return { ok, assertion: 'Redirect to /admin/login carrying next=/admin/users', observed: `HTTP ${status}, final URL ${finalUrl} (no redirect happened; body: ${(await pgFresh.locator('body').innerText().catch(() => '')).slice(0, 60)})` };
});

record({ id: 'B10', group: 'B', title: 'Gestor de agendas accede a Médicos (R5)', verdict: 'blocked', assertion: 'When R5 activates: HTTP 200 with the Médicos editor rendered', observed: 'Dormant until R5: no gestor-de-agendas role exists (A9 CURRENT); the account B10 needs cannot be created.' });
record({ id: 'B11', group: 'B', title: 'Gestor de agendas GET /admin/settings → 403 (R5)', verdict: 'blocked', assertion: 'When R5 activates: HTTP 403 rol_no_autorizado', observed: 'Dormant until R5: no gestor-de-agendas role exists (A9 CURRENT).' });

console.log('\n=== C — Doctor agendas ===');

await runCase('C1', 'C', 'Editor de Dra. Con Agenda: una franja exacta', false, async () => {
  await openDoctorEditor(pgAdmin, 'dra-con-agenda');
  const rows = await pgAdmin.locator('.cnt-franja-row').count();
  const f = franja(pgAdmin, 0);
  const desde = await f.locator('[data-franja-desde]').inputValue();
  const hasta = await f.locator('[data-franja-hasta]').inputValue();
  const cada = await f.locator('[data-franja-cada]').inputValue();
  const reservado = await f.locator('[data-cupo-reservado]').inputValue();
  const dias = await f.locator('[data-franja-dia]:checked').evaluateAll((els) => els.map((e) => e.value).sort());
  // Límites disclosure — web and overbooking are now doctor-level, aggregated from all franjas
  const limites = pgAdmin.locator('.hor-limits');
  const webChecked = await limites.locator('[data-limits-web]').isChecked();
  const sobreChecked = await limites.locator('[data-limits-overbooking]').isChecked();
  await shoot(pgAdmin, 'c1-con-agenda-editor');
  const ok = rows === 1 && desde === '09:00' && hasta === '12:00' && cada === '20'
    && webChecked === true && sobreChecked === false
    && JSON.stringify(dias) === JSON.stringify(['1', '2', '3', '4', '5']);
  return { ok, assertion: 'Exactly one franja row: Desde 09:00, Hasta 12:00, Cada 20; Límites: Reserva online checked, Extras unchecked', observed: `rows=${rows} desde=${desde} hasta=${hasta} cada=${cada} web=${webChecked} res=${reservado} sobre=${sobreChecked} dias=${dias.join(',')}` };
});

await runCase('C2', 'C', 'Paciente: grilla de svc-libre — 9 turnos por día hábil', false, async () => {
  await patientLogin(pgPat);
  const w = await openWizard(pgPat, 'svc-libre');
  const nChips = await w.chips.count();
  const perDay = await chipsPerDay(pgPat);
  const maxDay = perDay.length ? Math.max(...perDay.map((d) => d.n)) : 0;
  const extraLabels = perDay.flatMap((d) => d.labels).filter((l) => /sobre|extra/i.test(l));
  await shoot(pgPat, 'c2-con-agenda-picker');
  const ok = nChips > 0 && maxDay === 9 && perDay.every((d) => d.n <= 9) && extraLabels.length === 0;
  return { ok, assertion: 'Exactly 9 bookable slots on a weekday (09:00–12:00 every 20 min), no extra labelled slots', observed: `chips=${nChips}; per-day=${JSON.stringify(perDay.map((d) => d.n))}; extra=${extraLabels.length}` };
});

await runCase('C3', 'C', 'Editor de Dr. Sin Agenda: cero franjas, guarda igual', false, async () => {
  await openDoctorEditor(pgAdmin, 'dr-sin-agenda');
  const rows = await pgAdmin.locator('.cnt-franja-row').count();
  // The editor paints one EMPTY row for an agenda with no franjas — "zero franja
  // rows" means zero WITH content: a row whose day checkboxes are all unchecked.
  const filled = await pgAdmin.locator('.cnt-franja-row [data-franja-dia]:checked').count();
  const [resp] = await Promise.all([
    pgAdmin.waitForResponse((r) => /\/api\/admin\/content\/doctors\/dr-sin-agenda$/.test(r.url()) && r.request().method() === 'PUT', { timeout: 15000 }),
    pgAdmin.click('button[type="submit"][form="cnt-form"]'),
  ]);
  await shoot(pgAdmin, 'c3-sin-agenda-editor');
  const ok = filled === 0 && resp.status() === 200;
  return { ok, assertion: 'Zero franja rows (empty state) and the doctor saves unchanged', observed: `rows=${rows}, checked-days=${filled}, save=${resp.status()}` };
});

await runCase('C4', 'C', 'Paciente: Dr. Sin Agenda — 0 turnos ofrecidos', false, async () => {
  const w = await openWizard(pgPat, 'svc-sin-agenda');
  const chips = await w.chips.count();
  const days = await w.days.count();
  const franjaMode = await pgPat.locator('#wz-casochat, #wz-caso, .wz-seg').count();
  await shoot(pgPat, 'c4-sin-agenda-picker');
  const ok = chips === 0 && days === 0 && franjaMode > 0;
  return { ok, assertion: '0 bookable slots offered (no slot grid rendered)', observed: `chips=${chips} days=${days} no-slots-mode=${franjaMode > 0}` };
});

await runCase('C5', 'C', 'Editor de Dra. Sobre: Extras si se llena = 3 antes y después de guardar', false, async () => {
  await openDoctorEditor(pgAdmin, 'dra-sobre');
  // Límites disclosure — overbooking is now doctor-level
  const limites = pgAdmin.locator('.hor-limits');
  const before = await limites.locator('[data-limits-overbooking-percent]').inputValue();
  const [resp] = await Promise.all([
    pgAdmin.waitForResponse((r) => /\/api\/admin\/content\/doctors\/dra-sobre$/.test(r.url()) && r.request().method() === 'PUT', { timeout: 15000 }),
    pgAdmin.click('button[type="submit"][form="cnt-form"]'),
  ]);
  await openDoctorEditor(pgAdmin, 'dra-sobre');
  const after = await pgAdmin.locator('.hor-limits').locator('[data-limits-overbooking-percent]').inputValue();
  await shoot(pgAdmin, 'c5-sobre-editor');
  const ok = before === '3' && after === '3' && resp.status() === 200;
  return { ok, assertion: 'Límites shows "Extras si se llena" 3% before and after the save/reload', observed: `before=${before} after=${after} save=${resp.status()}` };
});

await runCase('C6', 'C', 'Paciente: Dra. Sobre — exactamente 9 públicos, nunca 10 (R1)', false, async () => {
  const w = await openWizard(pgPat, 'svc-sobre');
  const perDay = await chipsPerDay(pgPat);
  const maxDay = perDay.length ? Math.max(...perDay.map((d) => d.n)) : 0;
  await shoot(pgPat, 'c6-sobre-picker-9');
  const ok = maxDay === 9 && perDay.every((d) => d.n <= 9);
  return { ok, assertion: 'Exactly 9 public slots — never a 10th (public generator consumes cupos.web only) — R1 patient half', observed: `per-day=${JSON.stringify(perDay.map((d) => d.n))}` };
});

await runCase('C7', 'C', 'Admin carga Cierre total hoy → hoy+6 en Dr. Cierre', false, async () => {
  const hoy = artNow();
  const desde = iso(hoy);
  const hasta = iso(addDays(hoy, 6));
  await openDoctorEditor(pgAdmin, 'dr-cierre');
  await pgAdmin.locator('[data-add-excepcion]').first().click();
  const row = pgAdmin.locator('.cnt-exc-row').first();
  await row.locator('[data-exc-tipo]').selectOption('cerrado');
  await row.locator('[data-exc-desde-fecha]').fill(desde);
  await row.locator('[data-exc-hasta-fecha]').fill(hasta);
  await row.locator('[data-exc-motivo]').fill('Suite cierre');
  const [resp] = await Promise.all([
    pgAdmin.waitForResponse((r) => /\/api\/admin\/content\/doctors\/dr-cierre$/.test(r.url()) && r.request().method() === 'PUT', { timeout: 15000 }),
    pgAdmin.click('button[type="submit"][form="cnt-form"]'),
  ]);
  await pgAdmin.waitForTimeout(800);
  await openDoctorEditor(pgAdmin, 'dr-cierre');
  const savedRow = pgAdmin.locator('.cnt-exc-row').first();
  const tipo = await savedRow.locator('[data-exc-tipo]').inputValue();
  const d1 = await savedRow.locator('[data-exc-desde-fecha]').inputValue();
  const d2 = await savedRow.locator('[data-exc-hasta-fecha]').inputValue();
  await shoot(pgAdmin, 'c7-cierre-saved');
  const ok = resp.status() === 200 && tipo === 'cerrado' && d1 === desde && d2 === hasta;
  return { ok, assertion: `Cierres list shows one row tipo "Cierre total" with desde ${desde} / hasta ${hasta} (platform/admin-hours-editor.mjs)`, observed: `save=${resp.status()}; row tipo=${tipo} desde=${d1} hasta=${d2}` };
});

await runCase('C8', 'C', 'Paciente: primer turno de Dr. Cierre = hoy+7', false, async () => {
  const hoy = artNow();
  const expected = iso(nextWeekday(addDays(hoy, 7)));
  await new Promise((r) => setTimeout(r, 3500)); // catalog overlay TTL is 3 s
  const w = await openWizard(pgPat, 'svc-cierre');
  const first = await w.chips.first().getAttribute('data-dia').catch(() => null);
  await shoot(pgPat, 'c8-cierre-first-slot');
  const ok = first === expected;
  return { ok, assertion: `The first offered date is exactly ${expected} (today+7d, behaviour verified live with the reference-deployment counterpart)`, observed: `first offered=${first}` };
});

await runCase('C9', 'C', 'Límites: Reserva online checkbox — puede marcarse y desmarcarse', false, async () => {
  // R2 validation test — with new checkbox design, web booking has no numeric limits.
  // Instead, test that the checkbox state can be toggled and persists.
  await openDoctorEditor(pgAdmin, 'dra-con-agenda');
  const limites = pgAdmin.locator('.hor-limits');
  const checkbox = limites.locator('[data-limits-web]');
  const before = await checkbox.isChecked();
  // Toggle the checkbox
  await checkbox.click();
  const toggled = await checkbox.isChecked();
  const [resp] = await Promise.all([
    pgAdmin.waitForResponse((r) => /\/api\/admin\/content\/doctors\/dra-con-agenda$/.test(r.url()) && r.request().method() === 'PUT', { timeout: 15000 }),
    pgAdmin.click('button[type="submit"][form="cnt-form"]'),
  ]);
  await openDoctorEditor(pgAdmin, 'dra-con-agenda');
  const after = await pgAdmin.locator('.hor-limits').locator('[data-limits-web]').isChecked();
  await shoot(pgAdmin, 'c9-reserva-online-toggle');
  const ok = before !== toggled && toggled === after && resp.status() === 200;
  return { ok, assertion: 'Límites web-booking checkbox can be toggled and persists across save/reload', observed: `before=${before} toggled=${toggled} after=${after} save=${resp.status()}` };
});

await runCase('C10', 'C', 'Límites: Extras = 3% → aceptado y persiste (R2)', false, async () => {
  // R2 validation test — overbooking percent is accepted and persists
  await openDoctorEditor(pgAdmin, 'dra-con-agenda');
  const limites = pgAdmin.locator('.hor-limits');
  const checkbox = limites.locator('[data-limits-overbooking]');
  const percentInput = limites.locator('[data-limits-overbooking-percent]');
  // Enable overbooking by checking the checkbox
  await checkbox.click();
  // Fill the percent value
  await percentInput.fill('3');
  const [resp] = await Promise.all([
    pgAdmin.waitForResponse((r) => /\/api\/admin\/content\/doctors\/dra-con-agenda$/.test(r.url()) && r.request().method() === 'PUT', { timeout: 15000 }),
    pgAdmin.click('button[type="submit"][form="cnt-form"]'),
  ]);
  await openDoctorEditor(pgAdmin, 'dra-con-agenda');
  const after = await pgAdmin.locator('.hor-limits').locator('[data-limits-overbooking-percent]').inputValue();
  await shoot(pgAdmin, 'c10-extras-3pct');
  const ok = resp.status() === 200 && after === '3';
  return { ok, assertion: 'Límites: Overbooking percent 3 is accepted and persists after save/reload', observed: `save=${resp.status()} after=${after}` };
});

console.log('\n=== D — Booking by service type + per-person limit ===');

await runCase('D1', 'D', 'Wizard de svc-libre sin paso de orden', false, async () => {
  await pgPat.goto(`${BASE}/service/svc-libre`);
  await pgPat.waitForTimeout(1200);
  // The service page's primary CTA (mp-cta) carries ?slot=… — the wizard opens
  // with that appointment PRESELECTED (skips the When step), by design.
  const cta = pgPat.locator('a.mp-cta[href^="/service/svc-libre/book"]').first();
  const ctaN = await cta.count();
  if (ctaN) { await cta.click(); await pgPat.waitForTimeout(2200); }
  const landed = /\/service\/svc-libre\/book/.test(pgPat.url());
  const chips = await pgPat.locator('button[data-slot][data-dia]').count();
  const send = await pgPat.locator('#wz-send').count();
  const file = await pgPat.locator('#wz-file').count();
  await shoot(pgPat, 'd1-solicitud-no-orden');
  const ok = ctaN > 0 && landed && (chips > 0 || send > 0) && file === 0;
  return { ok, assertion: 'The wizard starts (preselected slot or grid) with no orden upload step anywhere (page flag orden false)', observed: `cta-on-page=${ctaN > 0} landed=${landed} chips=${chips} confirm-visible=${send > 0} file-inputs=${file}` };
});

await runCase('D2', 'D', 'Completar una solicitud en svc-libre', false, async () => {
  const r = await bookCurrentSlot(pgPat, 'svc-libre');
  await pgPat.waitForTimeout(1000);
  const mine = await misTurnos(pgPat, 'UC Turno libre');
  await shoot(pgPat, 'd2-solicitud-booked');
  const ok = r.status === 200 && mine === 1;
  return { ok, assertion: 'POST /api/requests 200; Mis turnos lists exactly 1 pendiente appointment for that service', observed: `POST=${r.status} body=${JSON.stringify(r.json).slice(0, 120)}; mis-turnos cards=${mine}` };
});

await runCase('D3', 'D', 'Wizard de svc-orden exige la orden', false, async () => {
  await pgPat.goto(`${BASE}/service/svc-orden/book`);
  await pgPat.waitForTimeout(2200);
  const h1 = (await pgPat.locator('h1').first().innerText().catch(() => '')).trim();
  const file = await pgPat.locator('#wz-file').count();
  const send = await pgPat.locator('#wz-send').count();
  await shoot(pgPat, 'd3-orden-upload-step');
  const ok = /subí tu orden/i.test(h1) && file === 1 && send === 0;
  return { ok, assertion: 'The wizard presents the orden de derivación upload (file input) and cannot be confirmed without it', observed: `h1="${h1}" #wz-file=${file} #wz-send=${send}` };
});

await runCase('D4', 'D', 'svc-espontánea: estado walk-in, sin turnos', false, async () => {
  await pgPat.goto(`${BASE}/service/svc-espontanea`);
  await pgPat.waitForTimeout(1500);
  const txt = (await pgPat.locator('body').innerText()).toLowerCase();
  // The shipped walk-in state is the Requisitos block (patient_web/site_*.py:4457);
  // the matrix's original "_STATUS_BY_MODE" citation is a dead constant.
  const sinTurno = txt.includes('no necesitás turno') && txt.includes('no necesitás orden médica');
  // No wizard entry: /book redirects back to the info page.
  const resp = await pgPat.goto(`${BASE}/service/svc-espontanea/book`, { waitUntil: 'domcontentloaded' });
  await pgPat.waitForTimeout(600);
  const redirected = /\/service\/svc-espontanea\/?$/.test(pgPat.url()) && resp.status() !== 404;
  const grid = await pgPat.locator('div.wz-day, button[data-slot]').count();
  await shoot(pgPat, 'd4-walkin-no-turno');
  const ok = sinTurno && redirected && grid === 0;
  return { ok, assertion: 'Page shows the walk-in state ("No necesitás turno. / No necesitás orden médica.", patient_web/site_*.py:4457) and offers zero bookable slots — no wizard entry', observed: `walkin-state=${sinTurno} book-redirect=${redirected} (url=${pgPat.url()}) grid=${grid}` };
});

await runCase('D5', 'D', 'svc-espontanea-orden: cero turnos ofrecidos', false, async () => {
  await pgPat.goto(`${BASE}/service/svc-espontanea-orden`);
  await pgPat.waitForTimeout(1500);
  const txt = (await pgPat.locator('body').innerText()).toLowerCase();
  const conOrden = txt.includes('no necesitás turno') && txt.includes('traé una orden médica');
  const resp = await pgPat.goto(`${BASE}/service/svc-espontanea-orden/book`, { waitUntil: 'domcontentloaded' });
  await pgPat.waitForTimeout(600);
  const redirected = /\/service\/svc-espontanea-orden\/?$/.test(pgPat.url());
  const grid = await pgPat.locator('div.wz-day, button[data-slot]').count();
  await shoot(pgPat, 'd5-walkin-orden');
  const ok = conOrden && redirected && grid === 0;
  return { ok, assertion: 'Zero bookable slots offered (walk-in con orden never offers appointments; apertura_mismo_dia unset) and the orden requirement is stated ("Traé una orden médica")', observed: `orden-required=${conOrden} book-redirect=${redirected} grid=${grid}` };
});

await runCase('D6', 'D', '2ª solicitud en svc-libre → 409 limite_turnos_activos (1)', false, async () => {
  const r = await bookCurrentSlot(pgPat, 'svc-libre');
  await shoot(pgPat, 'd6-limit-1-rechazado');
  const err = r.json && r.json.error;
  const lim = r.json && r.json.limite;
  const ok = r.status === 409 && err === 'limite_turnos_activos' && String(lim) === '1';
  return { ok, assertion: 'Rejected: HTTP 409, body error limite_turnos_activos with limite 1', observed: `POST=${r.status} ${JSON.stringify(r.json).slice(0, 160)}` };
});

await runCase('D7', 'D', 'Servicio DISTINTO (svc-serie) → aceptado', false, async () => {
  const r = await bookCurrentSlot(pgPat, 'svc-serie');
  await pgPat.waitForTimeout(800);
  const mine = await misTurnos(pgPat, 'UC Turno en serie');
  await shoot(pgPat, 'd7-limit-por-servicio');
  const ok = r.status === 200 && mine === 3; // 2 seeded approved + this one
  return { ok, assertion: 'Accepted — the limit counts same service + same clinical DNI only (booking_limits.py:24-29)', observed: `POST=${r.status}; mis-turnos svc-serie cards=${mine} (2 seeded + 1 new)` };
});

await runCase('D8', 'D', 'Mesa crea 2º turno del mismo servicio sin límite', false, async () => {
  await colaOpenDoctor(pgAdm, 'Agendi');           // dra-con-agenda → svc-libre
  await ctOpen(pgAdm);
  await ctPickPatient(pgAdm, 'Caso');              // Patricia Caso (holds 1 pendiente from D2)
  // dra-con-agenda matches TWO clinica services (svc-libre + svc-sin-agenda —
  // serviciosDe is cluster+centro, static/platform/admin-create-appointment.mjs), so the
  // overlay asks which one: pick it from the typeahead, as the desk would.
  await ctPickService(pgAdm, 'UC Turno libre');
  await pgAdm.waitForTimeout(1200);                // slots load
  await ctCalendarFirstDay(pgAdm);
  const [resp] = await Promise.all([
    pgAdm.waitForResponse((r) => r.url().includes('/api/admin/appointments') && r.request().method() === 'POST', { timeout: 20000 }),
    pgAdm.locator('#ct-ok').click(),
  ]);
  const json = await resp.json().catch(() => ({}));
  await shoot(pgAdm, 'd8-front-desk-sin-limite');
  const ok = resp.status() === 200 && json.ok !== false;
  return { ok, assertion: 'Created successfully — the panel POST is never subject to the limit (booking_limits.py:32-36)', observed: `POST=${resp.status()} ${JSON.stringify(json).slice(0, 140)}` };
});

await runCase('D9', 'D', '4º turno en svc-serie → 409 limite_turnos_activos (3)', false, async () => {
  const r = await bookCurrentSlot(pgPat, 'svc-serie');
  await shoot(pgPat, 'd9-service-override-3');
  const err = r.json && r.json.error;
  const lim = r.json && r.json.limite;
  const ok = r.status === 409 && err === 'limite_turnos_activos' && String(lim) === '3';
  return { ok, assertion: 'HTTP 409 limite_turnos_activos with limite 3 (service override beats site default)', observed: `POST=${r.status} ${JSON.stringify(r.json).slice(0, 160)}` };
});

await runCase('D10', 'D', '3er turno en svc-limite2 → 409 limite_turnos_activos (2)', false, async () => {
  const r = await bookCurrentSlot(pgPat, 'svc-limite2');
  await shoot(pgPat, 'd10-doctor-override-2');
  const err = r.json && r.json.error;
  const lim = r.json && r.json.limite;
  const ok = r.status === 409 && err === 'limite_turnos_activos' && String(lim) === '2';
  return { ok, assertion: 'HTTP 409 limite_turnos_activos with limite 2 (doctor override beats service and site)', observed: `POST=${r.status} ${JSON.stringify(r.json).slice(0, 120)} — ROOT CAUSE: the wizard's agenda slots are anonymous by design (patient_web/site_*.py:3562: "Schedule appointments go WITHOUT a doctor's name"), so the POST carries no slot_medico_id and booking_limits.check (booking_requests.py:1224) resolves the SITE default 1, never the doctor's 2. The doctor override is unreachable from the patient path.` };
});

console.log('\n=== E — Live regressions from the 2026-09-04 meeting ===');

await runCase('E1', 'E', 'Sobreturnos invisibles al crear turno (R1)', true, async () => {
  // dra-sobre has zero bookings, so she has no row in today's cola — the
  // overlay opens via its own hook (what the cola button calls).
  await ctOpen(pgAdm, 'Sonia Sobrera');
  await ctPickPatient(pgAdm, 'Caso');
  await pgAdm.waitForTimeout(1500);                // svc-sobre prefilled (her only service), slots load
  await ctCalendarFirstDay(pgAdm);
  const libres = await pgAdm.locator('.slot-time:not(.slot-time--over)').count();
  const sobres = await pgAdm.locator('.slot-time--over').count();
  const sobreLbl = await pgAdm.locator('.ct-sobre-lbl').count();
  await shoot(pgAdm, 'e1-sobreturnos-invisibles');
  const ok = libres === 9 && sobres === 0 && sobreLbl === 0;
  return { ok, assertion: 'CURRENT: overlay offers only the 9 libres — the sobreturnos list is empty. REQUIRED: 9 libres plus 3 clearly labelled sobreturno slots', observed: `libres=${libres} sobreturnos=${sobres} sobre-labels=${sobreLbl} (overlay via abrir() — no cola row exists for a zero-booking doctor)` };
});

// E2's Given: exactly ONE approved appointment on dra-sobre, on a franja minute.
execFileSync('python3', [path.join(HERE, 'fixtures.py'), 'e2'], { cwd: ROOT, stdio: 'inherit' });
await new Promise((r) => setTimeout(r, 3000));

await runCase('E2', 'E', 'Sobreturno con turno dado: capacidad restante > 0 (control)', false, async () => {
  await ctOpen(pgAdm, 'Sonia Sobrera');            // her one approved appointment is on the first offered day
  await ctPickPatient(pgAdm, 'Caso');
  await pgAdm.waitForTimeout(1500);
  await ctCalendarFirstDay(pgAdm);
  // The taken minute is offered as a dashed sobreturno button with capacity in its title.
  const sobreBtn = pgAdm.locator('.slot-time--over').first();
  const n = await pgAdm.locator('.slot-time--over').count();
  const title = n ? await sobreBtn.getAttribute('title') : '';
  const m = title.match(/(\d+)/);
  await shoot(pgAdm, 'e2-sobreturno-con-turno');
  const ok = n > 0 && !!m && +m[1] > 0;
  return { ok, assertion: 'That minute is offered with sobreturno capacity restantes > 0 (CURRENT = REQUIRED)', observed: `sobre-buttons=${n} title="${title}"` };
});

await runCase('E3', 'E', 'Crear turno de Dr. Multi sin contexto (R3)', true, async () => {
  await colaOpenDoctor(pgAdm, 'Multipro');         // dr-multi IS in the cola (today's appointment)
  await ctOpen(pgAdm);
  await ctPickPatient(pgAdm, 'Caso');
  await pgAdm.waitForTimeout(1500);
  const svcInput = pgAdm.locator('#ct-svc');
  const hasInput = await svcInput.count();
  const value = hasInput ? await svcInput.inputValue() : '(ya elegido)';
  const placeholder = hasInput ? await svcInput.getAttribute('placeholder') : '';
  const elegido = await ctServicioElegido(pgAdm).count();
  await shoot(pgAdm, 'e3-crear-turno-sin-contexto');
  const ok = hasInput === 1 && value === '' && elegido === 0 && /cardiología, ecografía, control/i.test(placeholder || '');
  return { ok, assertion: 'CURRENT: overlay opens with an EMPTY service typeahead (placeholder "Cardiología, ecografía, control…"). REQUIRED: doctor pinned and pre-selected, typeahead scoped to that doctor\'s services', observed: `typeahead present=${hasInput === 1} value="${value}" servicio-elegido=${elegido} placeholder="${placeholder}"` };
});

await runCase('E4', 'E', 'Crear turno de Dr. Único prellenado (control)', false, async () => {
  await ctOpen(pgAdm, 'Ulises Unico');             // dr-unico, exactly 1 service (no cola row: no today-appointments)
  await ctPickPatient(pgAdm, 'Caso');
  await pgAdm.waitForTimeout(1500);
  const elegido = ctServicioElegido(pgAdm);
  const n = await elegido.count();
  const txt = n ? (await elegido.first().innerText()).replace(/\n/g, ' ') : '';
  await shoot(pgAdm, 'e4-crear-turno-prellenado');
  const ok = n === 1 && /UC Turno con orden/.test(txt);
  return { ok, assertion: 'His one service is already chosen (the suyos.length === 1 branch; CURRENT = REQUIRED)', observed: `servicio-elegido=${n} text="${txt.slice(0, 80)}"` };
});

await runCase('E5', 'E', 'Cola sin filtro por centro (R4)', true, async () => {
  await pgAdm.goto(`${BASE}/admin/cola`);
  await pgAdm.locator('.queue-controls, .queue-doctor-link').first().waitFor({ timeout: 15000 });
  const selects = await pgAdm.locator('.queue-controls select, #sec-cola select, main select').evaluateAll(
    (els) => els.map((e) => ({ id: e.id, cls: e.className, first: (e.options[0] || {}).text || '' })));
  const centroSel = selects.filter((s) => /centro/i.test(s.first) || /centro/i.test(s.id) || /centro/i.test(s.cls));
  await shoot(pgAdm, 'e5-cola-sin-filtro-centro');
  const ok = selects.length === 2 && centroSel.length === 0
    && /todos los médicos/i.test(selects[0]?.first || '') && /todos los servicios/i.test(selects[1]?.first || '');
  return { ok, assertion: 'CURRENT: exactly two filter selects — médico ("Todos los médicos") and servicio ("Todos los servicios") — and no centro control. REQUIRED: a centro filter present (plus especialidad)', observed: `selects=${JSON.stringify(selects)}` };
});

await runCase('E6', 'E', 'Solicitudes sin filtro por médico (R4)', true, async () => {
  await pgAdm.goto(`${BASE}/admin/solicitudes`);
  await pgAdm.locator('#adm-filter-fields').waitFor({ timeout: 15000 });
  const selects = await pgAdm.locator('#adm-filter-fields select').evaluateAll(
    (els) => els.map((e) => ({ id: e.id, label: e.getAttribute('aria-label') || '' })));
  const medico = selects.filter((s) => /m[eé]dico/i.test(s.id) || /m[eé]dico/i.test(s.label));
  const needed = ['f-estado', 'f-esp', 'f-centro'].every((id) => selects.some((s) => s.id === id));
  await shoot(pgAdm, 'e6-solicitudes-sin-filtro-medico');
  const ok = needed && medico.length === 0;
  return { ok, assertion: 'CURRENT: estado + especialidad + centro selects and NO médico select. REQUIRED: médico filter added', observed: `selects=${JSON.stringify(selects)}` };
});

// ---- write results ------------------------------------------------------------
const order = { A: 1, B: 2, C: 3, D: 4, E: 5 };
results.sort((a, b) => (order[a.group] - order[b.group]) || a.id.localeCompare(b.id, 'es', { numeric: true }));
fs.writeFileSync(path.join(ART, 'results.json'), JSON.stringify(results, null, 2));

const tally = {};
for (const r of results) {
  tally[r.group] = tally[r.group] || { pass: 0, FAIL: 0, 'known-defect': 0, blocked: 0 };
  tally[r.group][r.verdict]++;
}
console.log('\n=== tally ===');
for (const g of ['A', 'B', 'C', 'D', 'E']) {
  console.log(g, JSON.stringify(tally[g] || {}));
}
await browser.close();
process.exit(0);
