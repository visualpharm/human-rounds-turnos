/* static/platform/admin-login.mjs — the admin panel's login-gate controller.
 *
 * Canonical ES module (platform-owned, row 1.1). Named exports; no browser
 * function API namespace, no classic twin, no CommonJS wrapper. Lives under
 * static/platform/ and is loaded through the page's emitted fingerprint
 * manifest (window.SANA_STATIC["platform/admin-login.mjs"]) — the login
 * screen is standalone and cannot assume the admin shell's sanaImportStatic.
 *
 * Extracted behavior-frozen out of admin-app.js (Tier 5, 2026-08-31): the gate
 * functions, the Turnstile widget, the TOTP reveal, the passkey handler, and the
 * Humor de guardia / Consejos de uso panel. The direct /admin/login screen
 * navigates itself: a validated data-next first, else the role-aware
 * landing from the identity the login API returned (2026-08-31 slice), and a
 * session that dies on a direct screen re-enters through /admin/login with
 * the current screen as ?next= — never through the legacy /admin shell.
 *
 * Own helpers (T, vars, esc, $, $$) per the extraction doctrine — does NOT
 * borrow from admin-app.js. Login waits for i18n when it is still loading so
 * feedback follows the active catalog; a failed import settles to the
 * established fallback copy instead of blocking authentication. */
"use strict";

var I18n = null;
var i18nUrl = (typeof window !== "undefined")
  ? (window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
  : new URL("./i18n.mjs", import.meta.url);
var i18nReady = i18nUrl
  ? import(i18nUrl).then(function (m) { I18n = m; return m; }).catch(function () { return null; })
  : Promise.resolve(null);
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
// A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
function vars(text, map) {
  var out = String(text == null ? "" : text);
  if (!map) return out;
  for (var k in map) {
    if (Object.prototype.hasOwnProperty.call(map, k)) out = out.split("{" + k + "}").join(String(map[k]));
  }
  return out;
}
function $(s, r) { return (r || document).querySelector(s); }
function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

// passkeys.mjs (el cliente WebAuthn) es un módulo ES canónico servido con
// huella (core/static_fingerprint.py, CLIENT_LOADED): un archivo servido no
// puede conocer su URL vigente, así que la página la publica como
// window.SANA_STATIC y staticUrl() resuelve a través de ese mapa —
// fail-closed (null + console.error, nunca una URL fallback hardcodeada),
// la misma forma que service.js/islands.mjs. El gate es una pantalla
// standalone (sin base.html ni admin-shell.js), de modo que este controlador
// lleva su propio resolver en vez de sanaImportStatic.
function staticUrl(name) {
  var m = (typeof window !== "undefined") && window.SANA_STATIC;
  var u = m && m[name];
  if (!u && typeof console !== "undefined" && console.error) console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
  return u || null;
}

// UNA sola promesa de módulo, arrancada con la evaluación de ESTE módulo —
// antes el UMD con global llegaba con <script> antes de admin-login.js, así
// que su API existía antes que cualquier handler; esa garantía la reproduce
// passkeysLista esperándola antes de usar PASSKEYS. Si el import falla —
// red, hash viejo, manifiesto ausente — PASSKEYS queda null y el botón
// simplemente no se ofrece: el camino fail-open que ya tomaba la pantalla
// cuando WebAuthn no está soportado.
var PASSKEYS = null;
var passkeysUrl = staticUrl("platform/passkeys.mjs");
var passkeysLista = passkeysUrl ? import(passkeysUrl).then(function (m) { PASSKEYS = m; })
  .catch(function () { /* fail-open: sin módulo no hay passkeys */ })
  : Promise.resolve(null);

var CFG = { turnstile_sitekey:null, totp:false, usuario:'admin' };
var TS = null;
var tsUrl = staticUrl("platform/turnstile.mjs");
var tsReady = tsUrl ? import(tsUrl).then(m => { TS = m; }).catch(() => {}) : Promise.resolve();
var NEED_TOTP = false;
var bound = false;
var configReady = Promise.resolve();

// Configuration data only — not a function API namespace.
if (typeof window !== "undefined") {
  window.SANA_ADMIN_LOGIN_CFG = CFG;
}

// ---- validated admin redirect targets --------------------------------------
// The SAME shape /admin/login's server-side _is_valid_admin_next enforces
// (admin_panel/admin_login_screen.py): /admin or a same-origin direct-screen
// path with an optional bounded query string. Queries carry direct state
// such as ?interview=, ?doc= or ?setup=2fa across a login round-trip; hashes
// remain excluded because they never reach the server. No scheme,
// protocol-relative target, userinfo, traversal or control bytes survives.
// The DOM is not trusted, so every client-side navigation re-validates too.
function nextValido(p){
  if(typeof p !== 'string' || !p) return false;
  if(p.indexOf('//') >= 0 || p.indexOf('\\') >= 0 || /[\x00-\x1f]/.test(p)) return false;
  if(p.indexOf('@') >= 0 || p.indexOf('..') >= 0) return false;
  if(p === '/admin') return true;
  return /^\/admin\/[a-zA-Z0-9\-_]+(?:\?[a-zA-Z0-9_.~%=&,+:\-]{1,256})?$/.test(p);
}

// ---- redirect helper for server-routed screens ----------------------------
// A dead session on a direct screen re-enters through the DIRECT login
// screen, carrying its exact /admin/<screen> path plus bounded query state
// as ?next= so a fresh login lands the person back where they were. The
// login screen itself and any pathname that does not survive validation log in plain — never
// through the legacy /admin shell (admin-shell retirement slice,
// 2026-08-31).
function redirigirSesionExpirada(){
  if(!window.SANA_ADMIN_SCREEN) return false;
  var target = String(location.pathname || '') + String(location.search || '');
  var targetPath = String(location.pathname || '');
  location.href = (nextValido(target) && targetPath !== '/admin/login')
    ? '/admin/login?next=' + encodeURIComponent(target)
    : '/admin/login';
  return true;
}

// ---- direct screen target helper ---------------------------------------
// Login never chooses a screen from client role data.  It hands off to the
// authenticated server resolver at /admin; that resolver applies the
// current role navigation, explicit continuation, saved bare path and
// default/account fallback in that order.
function getDirectTarget(){
  if(window.SANA_ADMIN_SCREEN !== 'login') return null;
  const form = $('#gate-form');
  const next = form ? String(form.getAttribute('data-next') || '') : '';
  var nextPath = next.split('?')[0];
  if(nextValido(next) && nextPath !== '/admin' && nextPath !== '/admin/login'){
    return '/admin?next=' + encodeURIComponent(next);
  }
  return '/admin';
}

// ---- Turnstile widget -------------------------------------------------------
function tsReset(){ if(TS) TS.reset(); }
async function renderTurnstile(){
  await tsReady;
  const box = $('#gate-turnstile');
  if(TS && box) TS.mount(box, CFG);
}

// ---- TOTP reveal ------------------------------------------------------------
function show2fa(on){
  NEED_TOTP = on;
  $('#gate-2fa').style.display = on ? '' : 'none';
  $('#gate-creds').style.display = on ? 'none' : '';
  $('#gate-intro').textContent = on
    ? T("admin.shell.gate.intro_totp", null, "Ingresá el código de 6 dígitos de tu app de autenticación.")
    : T("admin.shell.gate.intro", null, "Ingresá tu usuario y contraseña para ver la cola de solicitudes.");
}

// ---- panel del gate: Humor de guardia / Consejos de uso --------------------
const GP = {data:null, tab:null, variante:null, idx:{humor:0, consejos:0}, sid:null, t0:0, hash:0};

function gpFp(){
  return [navigator.userAgent, navigator.language,
          screen.width + 'x' + screen.height, String(window.devicePixelRatio||1),
          (Intl.DateTimeFormat().resolvedOptions().timeZone||''),
          String(navigator.hardwareConcurrency||''), String(navigator.maxTouchPoints||'')].join('|');
}

function gpHash(str){
  let h = 0x811c9dc5;
  for(let i=0;i<str.length;i++){ h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}

function gpHoy(){
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

function gpBeacon(evento, extra){
  if(!GP.sid || !GP.variante) return;
  const body = Object.assign({evento, variante:GP.variante, sid:GP.sid}, extra||{});
  try{
    fetch('/api/login-panel/event', {method:'POST', keepalive:true,
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)}).catch(()=>{});
  }catch(e){}
}

function gpItem(tab){
  const list = tab==='humor' ? GP.data.humor : GP.data.consejos;
  return list.length ? list[GP.idx[tab]] : null;
}

function gpRender(){
  const it = gpItem(GP.tab);
  const card = $('#gp-card');
  card.innerHTML = '';
  if(GP.tab==='humor'){
    const img = document.createElement('img');
    img.className = 'gp-img'; img.src = it.src; img.alt = it.alt || '';
    if(it.w && it.h){ img.width = it.w; img.height = it.h; }
    card.append(img);
  }else{
    if(it.src){
      const img = document.createElement('img');
      img.className = 'gp-img gp-tip-img'; img.src = it.src; img.alt = it.alt || '';
      if(it.w && it.h){ img.width = it.w; img.height = it.h; }
      card.append(img);
    }
    const t = document.createElement('h3');
    t.className = 'gp-tip-t'; t.textContent = it.titulo || '';
    const p = document.createElement('p');
    p.className = 'gp-tip'; p.innerHTML = it.texto;
    card.append(t, p);
    if(it.donde){
      const d = document.createElement('p');
      d.className = 'gp-tip-d'; d.textContent = it.donde;
      card.append(d);
    }
  }
  document.querySelectorAll('#gp-tabs a').forEach(a=>a.classList.toggle('on', a.dataset.tab===GP.tab));
}

async function gpInit(){
  if(GP.sid) return;
  GP.sid = Array.from(crypto.getRandomValues(new Uint8Array(8)), b=>b.toString(16).padStart(2,'0')).join('');
  GP.t0 = Date.now();
  const side = $('#gate-side');
  if(side) side.style.display = '';
  try{
    const res = await fetch('/api/login-panel');
    if(!res.ok){ if(side) side.style.display='none'; return; }
    GP.data = await res.json();
  }catch(e){ if(side) side.style.display='none'; return; }
  GP.data.humor = GP.data.humor || []; GP.data.consejos = GP.data.consejos || [];
  if(!GP.data.humor.length && !GP.data.consejos.length){ if(side) side.style.display='none'; return; }
  const fp = gpFp();
  GP.hash = gpHash(fp + '|' + gpHoy());
  GP.variante = 'consejos';
  GP.tab = GP.variante;
  let pref = null;
  try{ pref = localStorage.getItem('st_gp_tab'); }catch(e){}
  if(pref==='humor' || pref==='consejos') GP.tab = pref;
  if(GP.tab==='humor' && !GP.data.humor.length) GP.tab = 'consejos';
  if(GP.tab==='consejos' && !GP.data.consejos.length) GP.tab = 'humor';
  if(GP.data.humor.length) GP.idx.humor = GP.hash % GP.data.humor.length;
  if(GP.data.consejos.length) GP.idx.consejos = GP.hash % GP.data.consejos.length;
  const wrap = $('#gate-wrap');
  if(wrap && wrap.style.display==='none') return;
  const humorTab = document.querySelector('#gp-tabs a[data-tab="humor"]');
  const tipsTab = document.querySelector('#gp-tabs a[data-tab="consejos"]');
  if(!GP.data.humor.length && humorTab) humorTab.style.display='none';
  if(!GP.data.consejos.length && tipsTab) tipsTab.style.display='none';
  gpRender();
  const it0 = gpItem(GP.tab);
  gpBeacon('exposicion', {tab: GP.tab, item: it0 && it0.id});
  document.querySelectorAll('#gp-tabs a').forEach(a=>{
    a.addEventListener('click', ()=>{
      if(GP.tab === a.dataset.tab) return;
      GP.tab = a.dataset.tab;
      try{ localStorage.setItem('st_gp_tab', GP.tab); }catch(e){}
      const it = gpItem(GP.tab);
      gpBeacon('tab', {tab: GP.tab, item: it && it.id});
      gpRender();
    });
  });
}

// ---- login handlers --------------------------------------------------------
async function entrarConPasskey(){
  const btn = $('#gate-passkey');
  if(!btn) return;
  await passkeysLista;
  if(!PASSKEYS) return;
  btn.disabled = true;
  $('#gate-err').textContent = '';
  try{
    const j = await PASSKEYS.ingresar();
    const target = getDirectTarget();
    if(target){
      location.href = target;
    }else if(window.SANA_ADMIN_SCREEN === 'login'){
      $('#gate-err').textContent = T("admin.shell.gate.err_no_access", null, "Tu cuenta no tiene acceso al panel.");
    }else location.href = '/admin/login';
  }catch(e){
    if(!PASSKEYS.cancelado(e)) $('#gate-err').textContent = errText(e, T("admin.shell.gate.err_passkey", null, "No se pudo entrar con la passkey."));
  }finally{ btn.disabled = false; }
}

async function tryLogin(){
  const btn = $('#gate-btn');
  const err = $('#gate-err');
  if(btn && btn.disabled) return;
  if(btn) btn.disabled = true;
  // Both promises always settle: i18n falls back on a rejected module and
  // config catches its own failed request. Waiting here keeps an English gate
  // English and prevents an ordinary post-bind submit from bypassing a late
  // Turnstile configuration response.
  await Promise.all([i18nReady, configReady]);
  if(err) err.textContent = '';
  const user = $('#gate-user');
  const pass = $('#gate-pass');
  const code = $('#gate-code');
  const payload = {
    usuario: user ? user.value.trim() : '',
    password: pass ? pass.value : '',
    codigo: code ? code.value.trim() : '',
    turnstile_token: ''
  };
  if ($('#gate-form') && $('#gate-form').getAttribute('data-staff-pending') === '1') payload.staff_pending = true;
  if((NEED_TOTP || payload.codigo) && !/^\d{6}$/.test(payload.codigo)){
    if(err) err.textContent=T("admin.shell.gate.err_code_len", null, "El código son 6 dígitos.");
    if(btn) btn.disabled = false;
    return;
  }
  if(CFG.turnstile_sitekey){
    try {
      if (!TS) throw new Error('turnstile');
      payload.turnstile_token = await TS.requireToken({onWait: () => {
        if(err) err.textContent=T("account.form.verifying", null, "Verificando…");
      }});
      if(err) err.textContent = '';
    } catch(e) { payload.turnstile_token = ''; }
    if(!payload.turnstile_token){
      if(err) err.textContent=T("admin.shell.gate.err_turnstile_load", null, "No se pudo cargar la verificación anti-bot. Recargá la página.");
      if(btn) btn.disabled = false;
      return;
    }
  }
  try{
    const res = await fetch('/api/admin/login', {
      method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    const j = await res.json().catch(()=>({}));
    if(res.ok && j.ok){
      gpBeacon('login_ok', {tab: GP.tab, ms: Date.now() - GP.t0});
      const target = getDirectTarget();
      if(target){
        location.href = target;
      }else if(window.SANA_ADMIN_SCREEN === 'login'){
        if(err) err.textContent = T("admin.shell.gate.err_no_access", null, "Tu cuenta no tiene acceso al panel.");
      }else location.href = '/admin/login';
      return;
    }
    tsReset();
    // The password was right and the account asks for its code: a step of
    // the login, not an error (the demo counted 9 of 13 such sessions as failed).
    const pasoCodigo = j.need_totp && !j.error;
    if(!pasoCodigo) gpBeacon('login_error', {tab: GP.tab});
    if(j.need_totp){
      show2fa(true);
      if(err) err.textContent = j.error==='codigo' ? T("admin.shell.gate.err_code_wrong", null, "Código incorrecto. Probá de nuevo.") : '';
      setTimeout(()=>{ const c = $('#gate-code'); if(c && c.focus) c.focus(); }, 30);
      return;
    }
    const msg = {
      turnstile: T("admin.shell.gate.err_turnstile", null, "No se pudo verificar que no sos un bot. Reintentá."),
      credenciales: T("admin.shell.gate.err_credentials", null, "Usuario o contraseña incorrectos."),
      base_datos: T("admin.shell.gate.err_db", null, "El sistema está momentáneamente sin conexión. Esperá unos minutos y reintentá."),
      demasiados_intentos: T("admin.shell.gate.err_rate_limit", null, "Demasiados intentos. Esperá unos minutos."),
    }[j.error] || T("admin.shell.gate.err_generic", null, "No se pudo iniciar sesión.");
    if(err) err.textContent = msg;
    if(j.error==='credenciales' && pass){ pass.value=''; if(pass.focus) pass.focus(); }
  }catch(e){ if(err) err.textContent=T("admin.shell.gate.err_network", null, "Error de red. Reintentá."); tsReset(); }
  finally{ if(btn) btn.disabled=false; }
}

// ---- boot-time config fetch -----------------------------------------------
async function bootConfig(){
  try{
    const c = await fetch('/api/admin/config', {credentials:'same-origin'});
    if(c.ok) CFG = Object.assign(CFG, await c.json());
  }catch(e){}
  if (typeof window !== "undefined") window.SANA_ADMIN_LOGIN_CFG = CFG;
  await renderTurnstile();
}

async function requestReset(){
  const btn = $('#gate-forgot');
  const msg = $('#gate-reset-msg');
  const user = $('#gate-user');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  await i18nReady;
  try {
    const identifier = user ? user.value.trim() : '';
    if (!identifier) {
      msg.textContent = T('admin.shell.gate.reset_identifier', null, 'Ingresá tu usuario o email.');
      if (user) user.focus();
      return;
    }
    msg.textContent = '';
    const res = await fetch('/api/admin/reset-password', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'request', usuario: identifier})
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error('request failed');
    msg.textContent = data.message || T('admin.reset.request_received', null,
      'Si la cuenta tiene un email registrado, recibirás un enlace para elegir una nueva contraseña.');
  } catch (e) {
    msg.textContent = T('admin.shell.gate.err_network', null, 'Error de red. Reintentá.');
  } finally { btn.disabled = false; }
}

function bindGate(){
  if(bound) return;
  bound = true;
  const _gateForm=$('#gate-form');
  if(_gateForm) _gateForm.addEventListener('submit', e=>{ e.preventDefault(); tryLogin(); });
  const _gatePasskey=$('#gate-passkey');
  if(_gatePasskey) _gatePasskey.addEventListener('click', entrarConPasskey);
  const forgot = $('#gate-forgot');
  if (forgot) forgot.addEventListener('click', requestReset);
  // La visibilidad del botón de passkey la decide el módulo canónico (y el
  // navegador): se revela sólo cuando passkeys.mjs cargó Y soportado() dice
  // que hay WebAuthn. Sin módulo o sin WebAuthn queda oculto — mismo estado
  // fail-open de siempre, nunca un error a la cara.
  if(_gatePasskey) passkeysLista.then(function(){
    if(PASSKEYS && PASSKEYS.soportado()) _gatePasskey.hidden = false;
  });
}

async function boot(opts){
  opts = opts || {};
  bindGate();
  configReady = bootConfig();
  if ($('#gate-form') && $('#gate-form').getAttribute('data-staff-pending') === '1') {
    await i18nReady;
    show2fa(true);
  }
  var panelP = (opts.panel === false) ? Promise.resolve() : gpInit();
  if(opts.pendingSubmit){
    // Config (Turnstile sitekey / owner TOTP) must land before a queued
    // submit runs: the hold exists because this module arrived late, so the
    // classic "user typed while config fetched" race is no longer true.
    await tryLogin();
  }
  await Promise.all([configReady, panelP]);
}

export {
  boot,
  bootConfig,
  gpInit,
  tryLogin,
  entrarConPasskey,
  redirigirSesionExpirada,
  nextValido,
};
