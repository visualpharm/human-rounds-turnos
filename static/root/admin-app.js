let ALL = [];
let EMR_SYNC_ACTIVE = false; // explicit connector registry state from the server
// No push from the server: the store page of the desk's HSI extension when
// this site is one of its own — then an approved turno is loaded from here.
let HSI_EXTENSION = null;
let HSI_INSTALAR = null;
let FILTER = 'pendiente';
// CFG, TS_WIDGET, NEED_TOTP now owned by static/platform/admin-login.mjs
let CURRENT_ROLE = null; // fijado por applyRol() — guarda que una sesión 'medico'
let Dates = null;
const datesUrl = window.SANA_STATIC && window.SANA_STATIC['platform/dates.mjs'];
const datesReady = datesUrl
  ? import(datesUrl).then(module => { Dates = module; }).catch(() => null)
  : Promise.resolve(null);
let Format = null;
const formatReady = window.sanaImportStatic('platform/format.mjs').then(module => { Format = module; });
let FilterControls = null;
const filterControlsReady = window.sanaImportStatic('platform/admin-filter-controls.mjs').then(module => { FilterControls = module; });
let Combo = null;
const comboReady = window.sanaImportStatic('platform/admin-combo.mjs').then(module => {
  Combo = module;
  Combo.register('med-sel', {freeText:true});
  Combo.register('svc-sel', {emptyHtml: q=>{
    const no = noSeRealiza(q);
    if(!no) return '';
    return `<div class="cmb-none">` + T("admin.queue.solicitudes.hospital_no_realiza", { estudio: '<b>'+esc(no)+'</b>' }, "El hospital no realiza {estudio}.") + `<br>`
      + esc(T("admin.queue.solicitudes.rechaza_no_se_realiza", null, "Rechazá con «no se realiza en este centro».")) + `</div>`;
  }});
});
let Prefs = null;
const prefsReady = window.sanaImportStatic('platform/admin-preferences.mjs').then(module => {
  Prefs = module;
  return module.load();
});
let Solicitudes = null;
const solicitudesUrl = window.SANA_STATIC && window.SANA_STATIC['platform/admin-solicitudes.mjs'];
const solicitudesReady = solicitudesUrl
  ? import(solicitudesUrl).then(module => { Solicitudes = module; return module; })
  : Promise.reject(new Error('[static] platform/admin-solicitudes.mjs is not in window.SANA_STATIC'));
const sessionReady = window.sanaImportStatic('platform/admin-session.mjs');
let Table = null;
const tableReady = window.sanaImportStatic('platform/admin-table.mjs').then(module => { Table = module; return module; });
// The one «¿está en HSI?» control (static/platform/hsi-sync.mjs). A failed
// load only takes the HSI column away, never the screen.
let HsiSync = null;
const hsiSyncReady = window.sanaImportStatic('platform/hsi-sync.mjs').then(module => { HsiSync = module; module.ensureCss(); }, () => null);
let createAppointmentReady = null;
let createAppointmentSeq = 0;
let createAppointmentIntent = {};
let createAppointmentHost = null;
function loadCreateAppointment() {
  if (!createAppointmentReady) {
    createAppointmentReady = window.sanaImportStatic('platform/admin-create-appointment.mjs').then(function (m) {
      return m;
    }, function (err) {
      createAppointmentReady = null;
      throw err;
    });
  }
  return createAppointmentReady;
}
function clearCreateAppointmentLoadError() {
  document.querySelectorAll('[data-crear-turno-error]').forEach(function (node) { node.remove(); });
}
function showCreateAppointmentLoadError() {
  const msg = T('admin.queue.create_appt.load_failed', null, 'No se pudo cargar. Reintentá.');
  if (window.toast) window.toast(msg);
  let host = createAppointmentHost && createAppointmentHost.isConnected ? createAppointmentHost : null;
  const btn = $('#adm-crear-turno');
  if (!host) host = btn && btn.parentElement;
  if (!host) return;
  clearCreateAppointmentLoadError();
  const p = document.createElement('p');
  p.className = 'adm-empty';
  p.setAttribute('role', 'alert');
  p.setAttribute('data-crear-turno-error', '1');
  p.appendChild(document.createTextNode(msg + ' '));
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'btn btn-quiet';
  retry.setAttribute('data-crear-turno-retry', '1');
  retry.textContent = T('admin.queue.create_appt.recargar', null, 'Recargar');
  p.appendChild(retry);
  host.appendChild(p);
}
function openCreateAppointment(pre, host) {
  const seq = ++createAppointmentSeq;
  createAppointmentIntent = pre || {};
  createAppointmentHost = host || null;
  return loadCreateAppointment().then(function (m) {
    if (seq !== createAppointmentSeq) return;
    if (createAppointmentHost && !createAppointmentHost.isConnected) return;
    clearCreateAppointmentLoadError();
    return m.abrir(createAppointmentIntent);
  }).catch(function () {
    if (seq !== createAppointmentSeq) return;
    showCreateAppointmentLoadError();
  });
}

const $ = (s, r=document) => r.querySelector(s);
// applyRol usa $$ para agrupar los links del menú. Sin esta definición el
// panel entero quedaba inutilizable: boot() moría en applyRol, su catch se lo
// tragaba y mostraba la pantalla de contraseña aunque la sesión fuera válida.
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
// Mismo resolver que el resto del sitio (platform/i18n.mjs, listing.js, nav.js…),
// copiado byte a byte — test/i18n-fallbacks.test.mjs no lo compara en un
// .html, pero el contrato es el mismo: sin window.SanaI18n (namespace
// "admin" excluido de toda página de paciente) cae al literal castellano de
// siempre, nunca a la clave cruda.
let I18n = null;
  var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
    ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; }).catch(function () { return null; })
    : Promise.resolve(null);
  function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
// Lo que lee la persona cuando una llamada falla — nunca el código crudo del
// server (TASK-48): i18n.mjs apiError es el único que lo traduce.
function errText(raw, fallback){ return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
// api() ya avisó por su cuenta: la sesión (401) o la demo de solo lectura
// (su propio toast). Repetir «Error de red.» encima era mentir dos veces.
const yaAvisado = err => String(err && err.message) === '401' || String(err && err.message) === 'demo_solo_lectura';
const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// El formateo de personas vive en format.mjs (Format), compartido
// con las pantallas server-routed que no cargan este archivo — ver
// test/formato.test.mjs para la paridad con format.py. El alias local mantiene
// los ~4 puntos de uso de abajo tal cual.
const fmtDni = s => Format.dni(s);
const patientName = (s, fallback='') => Format.nombre(`${s.nombre||''} ${s.apellido||''}`.trim()) || fallback;

// Sin `function toast` local a propósito: este archivo no está envuelto en
// un IIFE, así que una declaración top-level PISARÍA window.toast (el
// proveedor compartido de admin-shell.js) y `if(window.toast)…` recursaría
// sobre sí mismo para siempre (RangeError, visto en el navegador real). Los
// `toast(…)` resolves the provider that admin-shell.js installs first.

function setFiltersOpen(open){
  const tools=$('#tools'), btn=$('#adm-filter-toggle');
  if(!tools || !btn) return;
  tools.classList.toggle('filters-open', !!open);
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

const _filterToggle=$('#adm-filter-toggle');
if(_filterToggle) _filterToggle.addEventListener('click', ()=>setFiltersOpen(!$('#tools').classList.contains('filters-open')));

// «Turnos de hoy» dentro de Solicitudes (reunión hospital 2026-08-27): un
// panorama del día, colapsado y con carga perezosa — no se pide hasta que
// alguien lo abre. It uses the direct queue endpoint, never a second API.
let SOL_HOY = null;   // null = todavía no se pidió; [] = se pidió, sin turnos
let SOL_HOY_ABIERTO = false;
function renderSolHoy(){
  const host = $('#sol-hoy');
  if(!host) return;
  host.hidden = !SOL_HOY_ABIERTO;
  if(!SOL_HOY_ABIERTO) return;
  host.innerHTML = SOL_HOY === null
    ? '<p class="cnt-hint">'+esc(T("admin.queue.common.loading", null, "Cargando…"))+'</p>'
    : Solicitudes.hoyResumenHtml(SOL_HOY);
}
async function cargarSolHoy(){
  if(SOL_HOY !== null){ renderSolHoy(); return; }
  renderSolHoy();
  try{
    const res = await api('/api/admin/day-queue');
    const data = res.ok ? await res.json() : null;
    SOL_HOY = (data && data.grupos) ? data.grupos : [];
  }catch(e){ SOL_HOY = []; }
  renderSolHoy();
}
const _solHoyToggle = $('#sol-hoy-toggle');
if(_solHoyToggle) _solHoyToggle.addEventListener('click', ()=>{
  SOL_HOY_ABIERTO = !SOL_HOY_ABIERTO;
  _solHoyToggle.setAttribute('aria-expanded', SOL_HOY_ABIERTO?'true':'false');
  _solHoyToggle.textContent = SOL_HOY_ABIERTO
    ? T("admin.queue.solicitudes.today_toggle_ocultar", null, "Ocultar turnos de hoy")
    : T("admin.queue.solicitudes.today_toggle", null, "Ver turnos de hoy");
  if(SOL_HOY_ABIERTO) cargarSolHoy(); else renderSolHoy();
});
document.addEventListener('keydown', e=>{
  if(e.key!=='Escape') return;
  setFiltersOpen(false);
});

// Próximo día hábil a las 09:00 contado desde el HOY DE LA INSTALACIÓN (dates.mjs), no
// el del navegador: cerca de medianoche una compu en otro huso ya estaba en otro día.
function nextWeekday9(){
  const hoy = /^(\d{4})-(\d{2})-(\d{2})$/.exec(Dates && Dates.hoyIsoInstalacion ? Dates.hoyIsoInstalacion() : '');
  const d = hoy ? new Date(+hoy[1], +hoy[2]-1, +hoy[3]) : new Date();
  d.setHours(9,0,0,0); d.setDate(d.getDate()+1);
  while(d.getDay()===0 || d.getDay()===6) d.setDate(d.getDate()+1);
  const p = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T09:00`;
}
// Día y hora de un datetime del server en el reloj de la INSTALACIÓN (dates.mjs), nunca el
// del navegador: una compu en UTC mostraba 10:00 para un turno de las 07:00. Sin dates.mjs,
// los del navegador como antes. null si no parsea.
function paredDe(iso){
  const w = Dates && Dates.paredInstalacion ? Dates.paredInstalacion(iso) : null;
  if(w) return w;
  const d=new Date(String(iso).replace(' ','T')); if(isNaN(d)) return null;
  const p=n=>String(n).padStart(2,'0');
  return {dia:`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`, hora:`${p(d.getHours())}:${p(d.getMinutes())}`};
}
function fmtDt(iso){ if(!iso) return ''; const w=paredDe(iso); if(!w) return iso; const [y,m,d]=w.dia.split('-'); return `${d}/${m}/${y} ${w.hora}`; }
// Las fechas recibidas y los turnos pasan por el mismo módulo canónico.
function fmtCreatedShort(iso){
  if(!iso) return '';
  const w=paredDe(iso); if(!w) return fmtDt(iso);
  const diaIso=w.dia;
  return Dates ? Dates.corta(diaIso, w.hora) : iso;
}
// Turnos en formato amigable CORTO ("Mañana, 08:00" / "Mié 29 jul, 10:00";
// Responsable del producto, 2026-07-22): la fecha entera obligaba a un combo de turno gigante y
// tiraba Aprobar/Rechazar a otra línea. Es el panel interno — al paciente se le
// sigue escribiendo la fecha completa. Sin el módulo se muestra el valor crudo.
function fmtTurno(iso){
  if(!iso) return '';
  const w=paredDe(iso);
  if(!w || !Dates) return iso;
  const diaIso=w.dia;
  return Dates.corta(diaIso, w.hora) || iso;
}
// datetime-local es hora de pared sin zona; le pegamos el desfase de la INSTALACIÓN
// para fijar el instante (si no, timestamptz lo interpreta en la zona del server y deriva).
// Sin dates.mjs (no cargó), el del navegador, como antes.
function toIsoWithOffset(local){
  if(!local) return local;
  // La hora es de pared de la INSTALACIÓN (la agenda): su desfase, no el del navegador.
  const instalacion = Dates && Dates.conHusoInstalacion ? Dates.conHusoInstalacion(local) : null;
  if(instalacion) return instalacion;
  const d = new Date(local); if(isNaN(d)) return local;
  const off = -d.getTimezoneOffset(); const a = Math.abs(off); const p = n => String(n).padStart(2,'0');
  const base = local.length===16 ? local+':00' : local;
  return base + (off>=0?'+':'-') + p(Math.floor(a/60)) + ':' + p(a%60);
}

// Rol de la sesión: sólo 'admin' ve "Usuarios"/"Reportes"/"Centros" en la
// nav (server-side es el gate real — cada endpoint devuelve 403 igual; esto
// es nada más para no ofrecerle un link que le va a rebotar). El operador SÍ
// ve Servicios/Médicos: editar agendas y vacaciones es su trabajo diario
// (reunión 2026-07-17), aunque crear/eliminar queda sólo para admin. Un rol
// 'medico' ve SOLO "Pacientes de hoy" (#cola, su cola del día — 2026-07-24)
// + "Mis turnos" — nada del resto (contenido, usuarios, reportes) tiene
// sentido para esa cuenta (y el server 403ea todo lo demás). "Pacientes de
// hoy" es visible para los TRES roles (admin/operador/medico, cada uno ve su
// propio recorte vía /api/admin/day-queue) así que no entra en ningún
// hide-list de acá — arranca visible por default y se queda así.
// Arranca todo oculto/visible en su default de admin/operador y se corrige
// recién cuando confirmamos el rol — nunca al revés (mostrar y después
// esconder parpadearía un link que la cuenta no puede usar).
function applyRol(rol, usuario){
  CURRENT_ROLE = rol;
  // Quién soy: admin-users.js lo usa para saber cuál de las filas de Usuarios
  // es la propia (esa lleva a Cuenta, que es donde uno se edita a sí mismo).
  if(usuario) window.CURRENT_USER = usuario;
  // Los módulos del admin viven en <script> separados y leen el rol desde
  // window. CURRENT_ROLE es un `let` global (no una propiedad de window), así
  // que mantenemos ambas referencias sincronizadas.
  window.CURRENT_ROLE = rol;
  const esAdmin = rol==='admin';
  const esMedico = rol==='medico';
  const esAdmisionista = rol==='admisionista';
  const esReportes = rol==='reportes';
  const visible = {
    'nav-solicitudes': esAdmin || esAdmisionista,
    'nav-my-schedules': esMedico,
    'nav-cola': esAdmin || esMedico || esAdmisionista,
    'nav-mis-turnos': esMedico,
    'nav-mis-pacientes': esAdmin || esMedico,
    'nav-pacientes': esAdmin || esAdmisionista,
    'nav-reportes': esAdmin || esReportes,
    'nav-servicios': esAdmin,
    'nav-centros': esAdmin,
    'nav-medicos': esAdmin,
    'nav-preparaciones': esAdmin,
    'nav-knowledge': esAdmin,
    'nav-plan': esAdmin,
    'nav-configuracion': esAdmin,
    'nav-settings': esAdmin,
    'nav-integrations': esAdmin,
    'nav-prompts': esAdmin,
    'nav-legal': esAdmin,
    'nav-sistema': esAdmin,
    'nav-hsi': esAdmin && EMR_SYNC_ACTIVE,
    'nav-usuarios': esAdmin
  };
  Object.keys(visible).forEach(id=>{
    const el=$('#'+id);
    if(el) el.style.display=visible[id]?'':'none';
  });
  // Un médico entra a ver a quién tiene hoy: "Pacientes de hoy" es su primer
  // link, no el tercero (the product owner, 2026-08-05), so it is also the first screen
  // a clinician reads on arrival.
  const nav = $('#adm-nav'), cola = $('#nav-cola');
  if(esMedico && nav && cola && nav.firstElementChild !== cola) nav.insertBefore(cola, nav.firstElementChild);
  // El aire que separa los tramos va en el primer link VISIBLE de cada uno:
  // si el rol no ve Médicos, el hueco no puede quedar colgando sobre nada.
  const TRAMOS = [['nav-medicos','nav-servicios','nav-centros','nav-preparaciones','nav-knowledge'],
                  ['nav-reportes','nav-plan','nav-configuracion','nav-settings','nav-integrations',
                   'nav-prompts','nav-legal','nav-sistema','nav-hsi','nav-usuarios']];
  TRAMOS.forEach(ids=>{
    let primero = true;
    ids.forEach(id=>{
      const el=$('#'+id);
      if(!el) return;
      const visto = el.style.display !== 'none';
      el.classList.toggle('adm-nav-sep', visto && primero);
      if(visto) primero = false;
    });
  });
  if(esAdmin && window.sanaCargarSistema) window.sanaCargarSistema();
  // The direct server shell owns the native alerts lifecycle and verified role.
}

// Secciones que un módulo aporta desde GET /api/admin/modules (manifiesto
// v2: label/label_key/order/roles — docs/roadmap/module-contract-design.md
// La sesión vive en una cookie HttpOnly (`st_admin`); el cliente no toca el token.
//
// Un 401 NO es prueba de que la sesión murió (responsable del producto, 2026-07-27: "me desconecta
// todo el tiempo"). La cookie dura 30 días y el server la valida sin base de
// datos, así que el 401 casi siempre viene de otra cosa: el Basic Auth de
// pre-lanzamiento (site_auth.py) que el navegador no reenvió, un challenge de
// Cloudflare, una respuesta cortada. Mandar a la pantalla de login por eso es
// pedirle la contraseña a alguien que YA está autenticado. Preguntamos primero:
// sólo si /api/admin/whoami dice que no hay sesión mostramos el gate.
//
async function api(path, opts={}){
  opts.credentials = 'same-origin';
  const res = await fetch(path, opts);
  if(res.status === 401 || res.status === 403){
    const Session = await sessionReady;
    const transitioned = await Session.confirmar(res);
    if(transitioned) throw new Error('401');
    if(res.status === 401){
      toast(T("admin.shell.toast.action_failed", null, "No se pudo completar la acción. Reintentá."));
      throw new Error('401');
    }
  }
  if(res.status === 403){
    // Sesión demo pública (admin_auth.py): el botón queda visible, el server
    // rechaza la mutación. clone() para no consumirle el body a quien llame
    // (otros 403 — solo_admin, rol_no_autorizado — siguen su camino normal).
    let cuerpo = null;
    try{ cuerpo = await res.clone().json(); }catch(e){}
    if(cuerpo && cuerpo.error === 'demo_solo_lectura'){
      toast(errText('demo_solo_lectura', T("admin.shell.toast.demo_read_only", null, "Demo de solo lectura: esta acción está deshabilitada. Podés seguir recorriendo las pantallas.")));
      throw new Error('demo_solo_lectura');
    }
  }
  return res;
}

// Login form helpers live in static/platform/admin-login.mjs. Direct pages never load this
// legacy controller; their session boundary lives in admin-session.mjs.

async function load(){
  try{
    const res = await api('/api/admin/requests');
    if(res.status===503){ $('#list').innerHTML='<p class="adm-empty">'+esc(T("admin.reports.reports.error_503", null, "La base de datos no está disponible."))+'</p>'; return; }
    if(!res.ok) throw new Error('bad');
    const data = await res.json();
    ALL = data.solicitudes || [];
    EMR_SYNC_ACTIVE = data.emr_sync_active === true;
    HSI_EXTENSION = EMR_SYNC_ACTIVE ? null : (data.hsi_extension || null);
    HSI_INSTALAR = EMR_SYNC_ACTIVE ? null : (data.hsi_instalar || null);
    MED_POOL = data.medicos_por_servicio || null;
    const hsiNav = $('#nav-hsi');
    if(hsiNav) hsiNav.style.display = EMR_SYNC_ACTIVE && CURRENT_ROLE === 'admin' ? '' : 'none';
    buildFilterOptions();
    render();
  }catch(e){ if(String(e.message)!=='401') $('#list').innerHTML='<p class="adm-empty">'+esc(T("admin.queue.solicitudes.no_se_pudo_cargar_cola", null, "No se pudo cargar la cola."))+'</p>'; }
}

// El botón "Crear turno" vive al lado del título compartido. Reunión
// hospital 2026-08-27: La contraparte técnica, probando el panel como admisionista, no lo
// encontraba — ahora se muestra en esta pantalla de trabajo y abre el mismo
// appointment creator that the queue uses.
function syncCrearTurno(){
  const b=$('#adm-crear-turno');
  const tools=$('#adm-sol-tools');
  if(tools) tools.style.display = '';
  if(!b) return;
  const agenda = CURRENT_ROLE === 'admin' || CURRENT_ROLE === 'admisionista';
  b.style.display = agenda ? '' : 'none';
}
window.sanaSyncCrearTurno = syncCrearTurno;
document.addEventListener('click', e=>{
  const retry = e.target.closest('[data-crear-turno-retry]');
  if (retry) {
    e.preventDefault();
    openCreateAppointment(createAppointmentIntent, createAppointmentHost);
    return;
  }
  const b = e.target.closest('#adm-crear-turno');
  if (b) {
    e.preventDefault();
    openCreateAppointment({}, b);
  }
});

// Sólo 3 estados de color (handoff 2026-07-17): amber = dudosa, verde =
// parece correcta, gris = todo lo demás (revisión manual, ilegible,
// documento equivocado, pendiente) — nada de un color de urgencia por
// veredicto, la tarjeta ya lo explica con la oración + las marcas de campo.
function verdictBadge(v){
  if(!v) return '';   // sin análisis (servicio sin orden): un badge "—" es ruido
  const map = {
    parece_correcta:['b-ok', T("admin.queue.solicitudes.verdict.parece_correcta", null, "Parece correcta")],
    dudosa:['b-warn', T("admin.queue.solicitudes.verdict.dudosa", null, "Dudosa")],
  };
  const otros = {
    revision_manual: T("admin.queue.solicitudes.verdict.revision_manual", null, "Revisión manual"),
    ilegible: T("admin.queue.solicitudes.verdict.ilegible", null, "Ilegible"),
    documento_equivocado: T("admin.queue.solicitudes.verdict.documento_equivocado", null, "Documento equivocado"),
    pendiente_ia: T("admin.queue.solicitudes.verdict.pendiente_ia", null, "Pendiente de análisis"),
  };
  const [cls,txt] = map[v] || ['b-neutral', otros[v] || v];
  return `<span class="badge ${cls}"><span class="dot"></span>${esc(txt)}</span>`;
}

// callout cuando el documento NO parece una orden médica, o cuando SÍ lo es
// pero no fue emitida en la jurisdicción de este centro
function docCallout(ai){
  const d = ai.documento;
  if(d && d.es_orden===false){
    const txt = d.tipo
      ? T("admin.queue.solicitudes.doc_no_es_orden_tipo", { tipo: '<b>'+esc(d.tipo)+'</b>' },
          "⚠ No parece una orden médica: parece {tipo}.")
      : T("admin.queue.solicitudes.doc_no_es_orden", null, "⚠ No parece una orden médica.");
    return `<div class="ai-doc">${txt}</div>`;
  }
  if(d && d.jurisdiccion_valida===false){
    const txt = d.jurisdiccion_detectada
      ? T("admin.queue.solicitudes.doc_otra_jurisdiccion_detectada", { jurisdiccion: '<b>'+esc(d.jurisdiccion_detectada)+'</b>' },
          "⚠ La orden parece emitida en otra jurisdicción: {jurisdiccion}, no en la de este centro.")
      : T("admin.queue.solicitudes.doc_otra_jurisdiccion", null,
          "⚠ La orden parece emitida en otra jurisdicción, no en la de este centro.");
    return `<div class="ai-doc">${txt}</div>`;
  }
  return '';
}

// campos que el operador puede corregir inline (handoff 2026-07-17: "make
// all this data editable, and show the edited states") — prácticas queda de
// sólo lectura (son chips derivados de la orden), prepaga/afiliado son
// informativos (nunca los escribió el paciente frente al operador, no hay
// nada que "corregir" ahí).
const CAMPOS_EDITABLES = new Set(['dni','paciente','especialidad','diagnostico','medico','matricula','fecha']);

// <input> sin chrome propio, sized-to-content (form-design: el affordance es
// el hover/foco, no un input que grita desde lejos en una tabla ya densa).
function campoInputHtml(sid, k, valor, bad){
  const v = valor!=null ? String(valor) : '';
  const editableTitle = esc(T("admin.queue.solicitudes.campo_editable_title", null, "Editable — corregí el valor leído"));
  // Diagnóstico corto = una línea; sólo el contenido largo se expande.
  if(k==='diagnostico'){
    const limpio = v.replace(/\s+/g,' ').trim();
    if(limpio.length<=44){
      const w = campoAnchoCh(limpio, 8);
      return `<input type="text" class="campo-edit${bad?' v-bad':''}" data-sid="${esc(sid)}" data-campo="${k}"
              value="${esc(limpio)}" style="width:${w}ch" title="${editableTitle}"
              autocomplete="off" spellcheck="false">`;
    }
    const rows = Math.max(1, Math.ceil(limpio.length/44));
    const cols = Math.min(48, Math.max(8, limpio.length + 2));
    return `<textarea class="campo-edit${bad?' v-bad':''}" data-sid="${esc(sid)}" data-campo="${k}"
            rows="${rows}" cols="${cols}" title="${editableTitle}"
            autocomplete="off" spellcheck="false">${esc(limpio)}</textarea>`;
  }
  // Estimado sólo para browsers sin `field-sizing` (ver <style>): con margen
  // ancho, porque en mayúsculas cada letra pasa el ancho del "0".
  // El techo sube a 48: los campos de texto libre que lee la IA pasan los 34
  // caracteres sin esfuerzo.
  const w = campoAnchoCh(v, 3);
  const refepsCls = k==='matricula' ? ' refeps-number' : '';
  const refepsAttrs = k==='matricula'
    ? ` data-refresh="${esc(sid)}" data-mat="${esc(v)}" title="${esc(T("admin.queue.solicitudes.buscar_matricula_title", null, "Buscar esta matrícula en SISA / REFEPS"))}"`
    : ` title="${editableTitle}"`;
  return `<input type="text" class="campo-edit${bad?' v-bad':''}${refepsCls}" data-sid="${esc(sid)}" data-campo="${k}"${refepsAttrs}
            value="${esc(v)}" style="width:${w}ch"
            autocomplete="off" spellcheck="false">`;
}

// Aproxima el ancho visual de la tipografía proporcional. `length * 1.45`
// dejaba un gran vacío entre el valor editable y su validación; esta medida
// conserva margen para el glifo final sin convertir el input en una columna.
function campoAnchoCh(valor, minimo){
  const n=[...String(valor||'')].reduce((w,ch)=>{
    if(/\s/.test(ch)) return w+.5;
    if(/[MWÁÉÍÓÚÑ0-9]/.test(ch)) return w+1;
    if(/[ilI.,:;!'|]/.test(ch)) return w+.45;
    return w+.78;
  },0);
  return Math.min(52,Math.max(minimo||3,Math.ceil(n+3.5)));
}

// `fr` = freshness(s), sólo se usa para plegar la vigencia en la fila Fecha
// (el banner ⚠ aparte murió — el handoff pide UN plegado por campo, no un
// banner + la fila repitiendo lo mismo).
// Fecha leída en formato amigable para MOSTRAR — el input
// editable conserva lo que el operador escriba; sólo se embellece lo parseable.
function fechaAmigable(txt){
  const dt = parseFechaTxt(txt);
  if(!dt || !Dates) return String(txt||'');
  const p=n=>String(n).padStart(2,'0');
  const iso=`${dt.getFullYear()}-${p(dt.getMonth()+1)}-${p(dt.getDate())}`;
  return Dates.nacimiento(iso) || String(txt||'');
}

// Cada dato aparece UNA vez (the product owner, 2026-07-19: "all the fields are only ones").
// El header de la tarjeta ya dice el DNI y el nombre del TITULAR; las filas
// DNI/Paciente de la orden sólo se muestran cuando NO coinciden (ahí son un
// dato distinto — lo que dice la orden — y el conflicto necesita verse) o
// cuando hay un valor sin referencia para comparar. Pares relacionados juntos:
// prepaga+afiliado, médico+matrícula (mismo renglón de la grilla de 2 col.).
// Texto que llega EN MAYÚSCULAS de la orden ("HEMOGRAMA COMPLETO") se muestra
// en oración (responsable del producto, 2026-07-20: "don't write it in all caps") — sólo si TODO
// el texto viene gritado; un valor mixto se respeta tal cual.
function sinGritos(t){
  t = String(t||'');
  if(t.length>3 && t===t.toUpperCase() && /[A-ZÁÉÍÓÚÑ]/.test(t)){
    const low = t.toLowerCase();
    return low.charAt(0).toUpperCase()+low.slice(1);
  }
  return t;
}

function fmtPrepaga(valor){
  const raw=String(valor||'').trim(), key=raw.toLowerCase();
  return {'swiss medical':'Swiss Medical','osde':'OSDE','pami':'PAMI'}[key] || sinGritos(raw);
}

function fmtAfiliado(valor, prepaga){
  const raw=String(valor||''), d=raw.replace(/\D/g,''), os=String(prepaga||'').toLowerCase();
  if(/swiss/.test(os) && d.length===18) return `${d.slice(0,3)}.${d.slice(3,6)}-${d.slice(6,12)} ${d.slice(12,14)} ${d.slice(14)}`;
  if(/swiss/.test(os) && d.length===19) return `${d.slice(0,3)}.${d.slice(3,6)}-${d.slice(6,13)} ${d.slice(13,15)} ${d.slice(15)}`;
  if(/osde/.test(os) && d.length===11) return `${d.slice(0,2)} ${d.slice(2,8)} ${d.slice(8,9)} ${d.slice(9)}`;
  return raw;
}

function camposTable(campos, sid, fr){
  if(!campos) return '';
  // Los pares relacionados comparten grupo: con CSS columns (.campos) el par
  // queda uno DEBAJO del otro y break-inside:avoid impide que el corte de
  // columna lo separe (responsable del producto, 2026-07-20).
  const order = [
    ['dni', T("admin.queue.overlay.dni", null, "DNI")],
    ['paciente', T("admin.queue.table.paciente", null, "Paciente")],
    ['prepaga', T("admin.queue.overlay.prepaga", null, "Prepaga"), 'par-os', 'no-label'],
    ['afiliado', T("admin.queue.overlay.afiliado", null, "N° afiliado"), 'par-os'],
    ['medico', T("admin.queue.overlay.medico", null, "Médico"), 'par-med', 'no-label'],
    ['matricula', T("admin.queue.overlay.matricula", null, "Matrícula"), 'par-med'],
    ['especialidad', T("admin.queue.overlay.especialidad", null, "Especialidad")],
    ['diagnostico', T("admin.queue.table.diagnostico", null, "Diagnóstico"), null, 'no-label'],
    ['practicas', T("admin.queue.table.practicas", null, "Prácticas")],
    ['fecha', T("admin.queue.overlay.fecha", null, "Fecha")]
  ];
  const parts=[];   // {par, html}
  const prepagaValor = campos.prepaga && campos.prepaga.valor;
  for(const [k,lbl,par,noLabel] of order){
    const c = campos[k]; if(!c) continue;
    const tieneValor = Array.isArray(c.valor) ? c.valor.some(Boolean) : c.valor!=null && String(c.valor).trim()!=='';
    if(!tieneValor) continue;
    // el header ya muestra DNI y titular: estas filas sólo si contradicen o
    // no se pudieron comparar (coincide=null CON valor leído)
    if((k==='dni'||k==='paciente') && (c.coincide===true || !(c.valor!=null&&c.valor!==''))) continue;
    // Prácticas: una sola queda como texto; 2+ son una lista real, una por
    // renglón. Así se escanean sin volver a convertir cada práctica en chip.
    if(k==='practicas'){
      const arr = Array.isArray(c.valor) ? c.valor.filter(Boolean) : [];
      if(!arr.length) continue;
      const valores = arr.map(sinGritos);
      const shown = valores.length===1
        ? esc(valores[0])
        : `<ul class="campo-list">${valores.map(v=>`<li>${esc(v)}</li>`).join('')}</ul>`;
      parts.push({par, lbl, noLabel, editado:false, cuerpo:shown});
      continue;
    }
    let ck='';
    let bad = c.coincide===false;
    if(c.coincide===true) ck='<span class="ck ck-y">✓ '+esc(T("admin.queue.solicitudes.coincide", null, "coincide"))+'</span>';
    else if(c.coincide===false) ck='<span class="ck ck-n">✗ '+esc(T("admin.queue.solicitudes.no_coincide", null, "no coincide"))+'</span>';
    let extra = '';
    let shown = c.valor;
    if(k==='dni') shown = fmtDni(c.valor) || c.valor;   // mismo formato que el header (con puntos)
    if(k==='prepaga') shown = fmtPrepaga(c.valor);
    if(k==='afiliado') shown = fmtAfiliado(c.valor, prepagaValor);
    if(k==='fecha' && c.valor){
      shown = fechaAmigable(c.valor);
      // vigencia PLEGADA en la fila — única marca de la fecha, sin eco "hace N"
      if(fr){
        bad = fr.vencida;
        ck = fr.vencida
          ? '<span class="ck ck-n">✗ '+esc(T("admin.queue.solicitudes.vencida_por", { duracion: duracionTxt(fr.dias-fr.limite) }, "vencida por {duracion}"))+'</span>'
          : '<span class="ck ck-y">✓ '+esc(T("admin.queue.solicitudes.vigente", null, "vigente"))+'</span>';
      }
    }
    if(k==='matricula' && c.valor){
      extra += `<span class="refeps-inline refeps-load" id="refeps-${sid}">${esc(T("admin.queue.solicitudes.verificando", null, "verificando…"))}</span>`;
    }
    const editado = !!c.editado;
    const editTag = editado ? `<span class="campo-tag-editado">${esc(T("admin.queue.solicitudes.editado_tag", null, "editado"))}</span>` : '';
    const val = CAMPOS_EDITABLES.has(k)
      ? campoInputHtml(sid, k, shown, bad) + editTag
      : esc(shown!=null&&shown!==''?shown:'—');   // prepaga/afiliado: informativo, nunca editable
    parts.push({par, lbl, noLabel, editado, cuerpo:`${val}${extra}${ck}`});
  }
  // Un par se lee como UN dato de dos renglones: «Médico → Ana Robledo» y
  // justo debajo, sin etiqueta propia, «MN 100482». Antes eran dos filas con
  // dos etiquetas metidas en un `.campo-par` que sólo evitaba que el corte de
  // columna las separara: seis renglones para tres datos, y el número de
  // afiliado leyéndose como un dato suelto en vez de como parte de la prepaga.
  // Sin etiqueta el valor debe arrancar en el MISMO borde donde arranca la
  // etiqueta de una fila con label (responsable del producto, 2026-09-15) — pero un valor editable
  // es un <input class="campo-edit"> con su propio padding-left:13px, así que
  // sin compensar queda ~11px más adentro que un valor informativo (texto
  // plano). `.campo-row-nolbl` le resta ese padding sólo en estas filas.
  const fila = (p, sub) =>
    `<div class="campo-row${p.noLabel ? ' campo-row-nolbl' : ''}">` + (p.noLabel ? '' : `<span class="k">${esc(p.lbl)}</span>`)
    + `<span class="v${p.editado?' v-editado':''}">${p.cuerpo}`
    + (sub ? `<span class="campo-sub${sub.editado?' v-editado':''}">${sub.cuerpo}</span>` : '')
    + `</span></div>`;
  let rows='';
  for(let i=0;i<parts.length;i++){
    const p = parts[i], q = parts[i+1];
    if(p.par && q && q.par===p.par){ rows += fila(p, q); i++; }
    else rows += fila(p, null);
  }
  return rows ? `<div class="campos" data-sid="${esc(sid)}">${rows}</div>` : '';
}

// Verificación en SISA / REFEPS — PUCO consulta cobertura por DNI, no matrículas.
// Se dispara sola al renderizar la cola y al clickear el número subrayado;
// (cacheada server-side; casi nunca vuelve a pegarle al sitio de terceros),
// el ↻ fuerza una consulta nueva.
function refepsInline(sid, mat, data){
  const volverAVerificar = esc(T("admin.queue.solicitudes.volver_a_verificar", null, "Volver a verificar"));
  const refresh = `<a class="refeps-refresh" data-refresh="${sid}" data-mat="${esc(mat)}" title="${volverAVerificar}">↻</a>`;
  if(data.error && !data.match){
    return `<span class="refeps-inline refeps-err" id="refeps-${sid}" title="${esc(data.error)}">${esc(T("admin.queue.solicitudes.no_se_pudo_verificar", null, "no se pudo verificar"))} ${refresh}</span>`;
  }
  const when = data.checked ? ((Dates && Dates.relativo(data.checked)) || String(data.checked)) : '';
  const verificadoTitle = esc(T("admin.queue.solicitudes.verificado_cuando", { when: when }, "Verificado {when}"));
  const m = data.match;
  if(!m){
    return `<span class="refeps-inline refeps-warn" id="refeps-${sid}" title="${verificadoTitle}">⚠ ${esc(T("admin.queue.solicitudes.no_encontrada_refeps", null, "no encontrada en SISA / REFEPS"))} ${refresh}</span>`;
  }
  // primer número del sello (los puntos son de millar: "57.093" → 57093;
  // "M.N. 104512 - M.P. 90871" → 104512) — igual que el server
  const matNum = (String(mat||'').match(/\d(?:[\d.]*\d)?/)||[''])[0].replace(/\./g,'');
  const mm = (m.matriculas||[]).find(x=>String(x.matricula||'').replace(/\D/g,'')===matNum) || (m.matriculas||[])[0] || {};
  const estado = mm.situacion || '';
  const habilitada = /habilit/i.test(estado);
  // la nota "(N persona(s) más…)" vive en el tooltip — en la fila sólo el estado
  const nOtros = data.candidatos-1;
  const otros = data.candidatos>1 ? ' · ' + Tn("admin.queue.solicitudes.persona_mas_con_numero", nOtros, null, "{n} persona más con este número", "{n} personas más con este número") : '';
  const tip = `${m.apellido||''} ${m.nombre||''}`.trim() + (mm.profesion?` — ${mm.profesion}`:'') + (mm.especialidad?` (${mm.especialidad})`:'')
    + `${otros} · ` + T("admin.queue.solicitudes.verificado_cuando", { when: when }, "Verificado {when}");
  return `<span class="refeps-inline ${habilitada?'refeps-ok':'refeps-warn'}" id="refeps-${sid}" title="${esc(tip)}">${habilitada?'✓':'⚠'} ${esc(estado||'—')} ${refresh}</span>`;
}

async function verificarMatricula(sid, mat, medico, force){
  const box = document.getElementById('refeps-'+sid);
  if(box){ box.className='refeps-inline refeps-load'; box.textContent=T("admin.queue.solicitudes.verificando", null, "verificando…"); }
  try{
    const q = new URLSearchParams({matricula: mat, medico: medico||''});
    if(force) q.set('force','1');
    const res = await api('/api/admin/refeps?'+q.toString());
    const data = await res.json();
    const cur = document.getElementById('refeps-'+sid);
    if(cur) cur.outerHTML = refepsInline(sid, mat, !res.ok ? {error: errText(data, T("admin.queue.solicitudes.no_se_pudo_verificar", null, "no se pudo verificar"))} : data);
  }catch(e){
    const cur = document.getElementById('refeps-'+sid);
    if(cur) cur.outerHTML = refepsInline(sid, mat, {error: 'red'});
  }
}

// Red de seguridad del cliente: si igual el archivo no viene (se borró entre el
// listado y el render, o falla la lectura), la tarjeta dice qué pasó en lugar
// de mostrar el JSON del error dentro del recuadro.
function ordenRota(img){
  const cont = img.closest('.orden-prev');
  if(cont) cont.outerHTML = '<div class="orden-none">'+esc(T("admin.queue.solicitudes.orden_no_cargo", null, "No se pudo cargar la orden"))+'</div>';
}

function ordenPreview(s){
  // sin orden → la tarjeta ni siquiera tiene columna lateral (pendingCard);
  // este guard queda para consumidores viejos.
  if(!s.tiene_orden) return '';
  const url = s.orden_url;  // la cookie de sesión viaja sola en el <img>/<embed> same-origin
  const mime = (s.orden_mime||'').toLowerCase();
  const alt = esc(T("admin.content.catalog.doctors.request_order_alt",
    { nombre: patientName(s, T("admin.queue.solicitudes.paciente_generico", null, "paciente")) },
    "Orden de {nombre}"));
  // PDF: no hay zoom de imagen; el embed ya es navegable a alto completo.
  // #toolbar=0: sin la botonera oscura del visor de Chrome — la columna es
  // display-only, como en el mockup (la orden la mandó el paciente).
  if(mime.includes('pdf')) return `<div class="orden-prev"><embed src="${esc(url)}#toolbar=0&navpanes=0" type="application/pdf"></div>`;
  // Imagen: the native viewer intercepts this link only after mounting. Until
  // then it is a real original-image fallback, never a dead button.
  return `<div class="orden-prev">
    <a href="${esc(url)}" target="_blank" rel="noopener" class="orden-btn" data-photozoom data-photozoom-src="${esc(url)}" data-photozoom-alt="${alt}">
      <img src="${esc(url)}" alt="${alt}" loading="lazy" onerror="ordenRota(this)">
      <span class="orden-zhint" aria-hidden="true"><svg class="orden-zhint-ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M10.5 7.5v6M7.5 10.5h6"/></svg></span>
    </a>
  </div>`;
}

// --- catálogo para etiquetas legibles (servicio_id / centro_slug -> nombre)
// y para las reasignaciones del operador (selects de servicio y médico) ---
let SVC = {}, CEN = {}, SVC_LIST = [], MED_LIST = [], MED_POOL = null;
async function loadCatalogo(){
  try{
    const [rs,rc,rm] = await Promise.all([
      fetch('/api/services',{credentials:'same-origin'}),
      fetch('/api/centers',{credentials:'same-origin'}),
      fetch('/api/doctors',{credentials:'same-origin'})]);
    if(rs.ok) ((await rs.json()).servicios||[]).forEach(x=>{ if(x.id){ SVC[x.id]=x.nombre||x.id; SVC_LIST.push(x); } });
    if(rc.ok) ((await rc.json()).centros||[]).forEach(x=>{ if(x.slug) CEN[x.slug]=x.name||x.slug; });
    if(rm.ok) MED_LIST = ((await rm.json()).medicos||[]).filter(x=>x&&x.nombre);
    SVC_LIST.sort((a,b)=>String(a.nombre||a.id).localeCompare(String(b.nombre||b.id),'es'));
    MED_LIST.sort((a,b)=>String(a.nombre).localeCompare(String(b.nombre),'es'));
  }catch(e){}
}
// --- médicos que corresponden a un servicio -------------------------------
// El catálogo nombra la ESPECIALIDAD ("Psiquiatría") y el médico su PROFESIÓN
// ("Psiquiatra"): comparamos por raíz común, no por igualdad, porque el par
// -ía/-a (psiquiatría/psiquiatra, pediatría/pediatra) y -ología/-ólogo
// (cardiología/cardiólogo) nunca coinciden literal. Umbral por prefijo:
// "psicología" vs "psiquiatra" comparte sólo "psi" y queda afuera.
function _norm(s){
  return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
}
function _mismaRaiz(a, b){
  if(!a || !b) return false;
  let i = 0; while(i < a.length && i < b.length && a[i] === b[i]) i++;
  return i >= 4 && i >= 0.6 * Math.min(a.length, b.length);
}
// "infantil"/"general" son MODIFICADORES, no especialidades: sin sacarlos,
// "Diabetología Infantil" traía también al neurólogo y al infectólogo
// infantiles sólo por compartir esa palabra.
const _ESP_STOP = new Set(['infantil','infantiles','general','generales',
                           'adulto','adultos','pediatrica','pediatrico']);
function _palabras(s){
  return _norm(s).split(/[^a-z0-9]+/).filter(w=>w.length>3 && !_ESP_STOP.has(w));
}
function coincideEsp(servNombre, espMedico){
  const A = _palabras(servNombre), B = _palabras(espMedico);
  return A.some(a=>B.some(b=>_mismaRaiz(a,b)));
}
// Médicos que se pueden asignar a un servicio: el POOL que manda el servidor
// en /api/admin/requests (`medicos_por_servicio`, schedule_policy.belongs — la
// misma regla con la que reserva el paciente y con la que la aprobación
// verifica). Antes esta lista salía de coincideEsp y, sin coincidencias,
// devolvía el catálogo ENTERO: un Ecocardiograma sin cardiólogo salía con un
// traumatólogo preelegido (mapa del hospital, 2026-10-23). Sin pool o sin
// nadie: lista vacía — el médico queda «a asignar», nunca se adivina.
function medIdDe(m){
  if(m && m.id) return String(m.id);
  const r = /^\/doctor\/([^/?#]+)/.exec(String((m && m.href) || ''));
  return r ? decodeURIComponent(r[1]) : '';
}
function medicosDe(servicioId){
  const ids = MED_POOL && MED_POOL[servicioId];
  if(!Array.isArray(ids) || !ids.length) return [];
  const set = new Set(ids.map(String));
  return MED_LIST.filter(m=>set.has(medIdDe(m)));
}
// Un solo default para la tarjeta abierta y para el lote: el médico que tiene
// libre el horario pedido, el ya asignado, el sugerido, o el primero con turno
// (en la franja preferida si se pasa `prefGen`). Sólo de la lista del servicio
// (+ el sugerido, que el servidor ya sacó del mismo pool): un asignado de otra
// especialidad no se preelige.
function medicoPorDefecto(s, base, prefGen){
  const medSug = s.medico_sugerido;
  const valido = n => !!n && (base.some(x=>x.nombre===n) || !!(medSug && medSug.nombre===n));
  if(medSug && medSug.pedido && valido(medSug.nombre)) return medSug.nombre;
  // El asignado se reconoce por id (con el nombre del catálogo, 2026-10-23);
  // el nombre guardado sólo para filas viejas sin id.
  const asigId = String(s.medico_asignado_id || '');
  const asig = asigId ? base.find(x=>medIdDe(x)===asigId) : null;
  const asigNombre = asig ? asig.nombre : s.medico_asignado;
  if(valido(asigNombre)) return asigNombre;
  if(medSug && valido(medSug.nombre)) return medSug.nombre;
  const conAgenda = base.filter(x=>x.proximo&&x.proximo.sort).slice().sort((a,b)=>
    String(a.proximo.sort).localeCompare(String(b.proximo.sort)));
  const enFranja = conAgenda.filter(x=>{
    const h = Number(String((x.proximo&&x.proximo.hora)||'').split(':')[0]);
    return prefGen==='mañanas' ? h<13 : prefGen==='tardes' ? h>=13&&h<19 : prefGen==='noches' ? (h>=19||h<6) : true;
  });
  return (enFranja[0]||conAgenda[0]||base[0]||{}).nombre||'';
}
// La pista bajo el combo vacío: por qué no hay médico y qué hacer.
function medVacioTxt(servicioId){
  return T("admin.queue.solicitudes.sin_medicos_del_servicio", { servicio: svcNombre(servicioId) },
    "No hay médicos de {servicio} cargados. Aprobá con el médico «a asignar», derivá a otro servicio o cargá el médico en Médicos.");
}

function svcNombre(id){ return SVC[id] || prettySlug(id); }
function cenNombre(slug){ return CEN[slug] || prettySlug(slug); }

// Ítems del combo "Derivar a". Dos servicios pueden llamarse igual sin ser el
// mismo: "Psiquiatría" existe en CAPS Pinamar y en Diabetpin, y en la lista se
// veían como dos filas idénticas — parecía un dato duplicado (responsable del producto, 2026-07-27:
// "two spellings of the same"). El centro se agrega SÓLO a los nombres que se
// repiten: es lo único que los distingue, y sumárselo a los 98 que no lo
// necesitan ensancharía el control para todos (hook control-content-fits).
// `k` = palabras clave del servicio: no se muestran, pero el filtro las busca.
function svcCombolItems(fallbackId){
  const base = SVC_LIST.length ? SVC_LIST
    : [{id:fallbackId, nombre:svcNombre(fallbackId)}];
  const veces = {};
  base.forEach(x=>{ const k=_norm(x.nombre||''); veces[k]=(veces[k]||0)+1; });
  return base.map(x=>({
    v: x.id,
    t: x.nombre || x.id,
    s: (veces[_norm(x.nombre||'')] > 1 && x.centro) ? '· '+cenNombre(x.centro) : '',
    k: (x.palabras||[]).join(' '),
  }));
}

// Estudios que la gente pide y el hospital NO hace: sin esto el desplegable
// devolvía una lista vacía, indistinguible de un error de tipeo. La fuente de
// verdad es webdata/estudios-preparacion.json (`_meta.no_ofrecidos_…`); acá va
// espejada para no sumarle un endpoint a una pantalla que ya carga el catálogo,
// y test/test_no_ofrecidos.py falla si las dos listas se separan.
const NO_SE_REALIZA = [
  {nombre:'resonancia magnética', match:['resonancia','rmn','resonancia nuclear magnetica']},
  {nombre:'densitometría ósea', match:['densitometria','dexa','dxa','mineralometria osea']},
  {nombre:'Holter', match:['holter','monitoreo ambulatorio de ecg']},
  {nombre:'MAPA (presurometría)', match:['mapa','presurometria','monitoreo ambulatorio de presion arterial']},
  {nombre:'espirometría', match:['espirometria','prueba de funcion pulmonar']},
  {nombre:'endoscopía digestiva alta', match:['endoscopia','videoendoscopia digestiva alta']},
  {nombre:'videocolonoscopía', match:['colonoscopia','videocolonoscopia','rectosigmoidoscopia']},
  {nombre:'electroencefalograma', match:['electroencefalograma','eeg']},
];
function noSeRealiza(q){
  const nq = _norm(q||'');
  if(nq.length < 3) return '';
  const hit = NO_SE_REALIZA.find(e=>e.match.some(m=>{
    const nm = _norm(m);
    return nm.indexOf(nq)===0 || nq.indexOf(nm)===0;
  }));
  return hit ? hit.nombre : '';
}

// Un laboratorio no asigna médicos (responsable del producto, 2026-07-19). El server ya manda
// sin_medico en cada fila; este espejo client-side cubre la REderivación
// (el operador cambia el servicio en el combo y el flag de la fila queda viejo).
function esLab(servicioId){
  const s = SVC_LIST.find(x=>x.id===servicioId);
  if(!s) return false;
  const t = _norm(`${s.nombre||''} ${(s.palabras||[]).join(' ')}`);
  return /laborator|extraccion/.test(t);
}

// --- typeable combos (svc-sel / med-sel) -----------------------------------
// admin-combo.mjs owns markup, filtering, keyboard, pointer, and focus behavior
// so admin-content.js and direct screens can use it without this controller.
// Queue-specific policies are registered when that canonical ES module is
// ready. There is no global compatibility producer or fallback copy.

// --- motivos de rechazo ESTÁNDAR ------------------------------------------
// Lista curada y cerrada: un motivo canónico por causa, sin variantes que
// digan lo mismo con otras palabras (responsable del producto, 2026-07-11). El texto le llega al
// paciente en "Mis solicitudes", por eso habla en segunda persona. "Otro
// motivo…" abre texto libre para lo que no encaje.
// 2026-07-20: partida en DOS listas — los motivos que hablan de la orden no
// tienen sentido en una solicitud sin orden ("if there is no order required
// and no attached, then don't write them"): esa tarjeta ofrece sólo los
// generales.
const MOTIVOS_ORDEN = [
  'La orden está vencida.',
  'El documento subido no es una orden médica.',
  'La orden no se lee con claridad.',
  'La orden está a nombre de otra persona.',
  'El DNI de la orden no coincide con el de la cuenta.',
  'La orden no corresponde al servicio solicitado.',
  'Falta la firma o la matrícula del médico.',
  'La orden no tiene fecha.',
  'La orden fue emitida en otra jurisdicción y no corresponde a este centro.',
];
const MOTIVOS_GENERAL = [
  'No hay turnos disponibles para este servicio por ahora.',
  'El servicio solicitado no se realiza en este centro.',
];
function motivosPara(s){
  return s && s.tiene_orden ? MOTIVOS_ORDEN.concat(MOTIVOS_GENERAL) : MOTIVOS_GENERAL;
}

// El motivo sugerido sale de lo que la IA ya encontró: si hay para qué
// rechazar, el select viene pre-elegido con el motivo estándar que matchea.
function suggestMotivo(s){
  const ai = s.ai_json || {}, c = ai.campos || {};
  if(ai.verdict==='documento_equivocado') return 'El documento subido no es una orden médica.';
  if(ai.verdict==='ilegible') return 'La orden no se lee con claridad.';
  // jurisdicción antes que cualquier otra cosa: si la orden es de otro
  // sistema de salud, nada de lo demás importa (puede incluso coincidir
  // DNI y especialidad y seguir sin corresponder a este centro)
  if(ai.documento && ai.documento.jurisdiccion_valida===false) return 'La orden fue emitida en otra jurisdicción y no corresponde a este centro.';
  // identidad antes que vigencia: si la orden es de otra persona, ESE es el
  // motivo aunque además esté vencida
  if(c.paciente && c.paciente.coincide===false) return 'La orden está a nombre de otra persona.';
  if(c.dni && c.dni.coincide===false) return 'El DNI de la orden no coincide con el de la cuenta.';
  const fr = freshness(s);
  if(fr && fr.vencida) return 'La orden está vencida.';
  if(c.especialidad && c.especialidad.coincide===false) return 'La orden no corresponde al servicio solicitado.';
  // Lo que le FALTA a la orden va último: es un motivo real, pero cualquiera
  // de los de arriba lo explica mejor si además pasa. La lista la calcula
  // ai_referral._verdict_3pass; acá no se vuelve a mirar campo por campo.
  const faltan = (ai.documento && ai.documento.campos_faltantes) || [];
  if(faltan.includes('firma_matricula')) return 'Falta la firma o la matrícula del médico.';
  if(faltan.includes('fecha')) return 'La orden no tiene fecha.';
  return '';
}

// Ambos caminos llegan listos para ejecutar. Si la IA no encontró un motivo
// específico, queda seleccionado el primer motivo válido para ese tipo de
// solicitud; la rama no sugerida sigue gris, pero nunca incompleta.
function defaultMotivo(s){
  return suggestMotivo(s) || motivosPara(s)[0] || '';
}

// Etiqueta corta para el operador; el value conserva la explicación completa
// que recibe el paciente. Así el control muestra su valor sin truncarlo.
// Las etiquetas pasan por T() con la clave castellana como fallback exacto;
// el `value` real que viaja al server y al paciente sigue siendo la oración
// completa en MOTIVOS_ORDEN/MOTIVOS_GENERAL — nunca se traduce, es lo que
// soluciones.py clasifica por regex.
function motivoLabel(m){
  return {
    'La orden está vencida.': T("admin.queue.solicitudes.lectura.orden_vencida", null, "Orden vencida"),
    'El documento subido no es una orden médica.': T("admin.queue.solicitudes.motivo_corto.no_es_orden_medica", null, "El archivo no es una orden médica"),
    'La orden no se lee con claridad.': T("admin.queue.solicitudes.motivo_corto.orden_ilegible", null, "Orden ilegible"),
    'La orden está a nombre de otra persona.': T("admin.queue.solicitudes.lectura.orden_de_otra_persona", null, "Orden de otra persona"),
    'El DNI de la orden no coincide con el de la cuenta.': T("admin.queue.solicitudes.motivo_corto.dni_distinto", null, "DNI distinto al de la cuenta"),
    'La orden no corresponde al servicio solicitado.': T("admin.queue.solicitudes.motivo_corto.servicio_distinto", null, "Servicio distinto al de la orden"),
    'Falta la firma o la matrícula del médico.': T("admin.queue.solicitudes.motivo_corto.faltan_firma_matricula", null, "Faltan firma o matrícula"),
    'La orden no tiene fecha.': T("admin.queue.solicitudes.motivo_corto.orden_sin_fecha", null, "Orden sin fecha"),
    'La orden fue emitida en otra jurisdicción y no corresponde a este centro.': T("admin.queue.solicitudes.motivo_corto.orden_otra_jurisdiccion", null, "Orden de otra jurisdicción"),
    'No hay turnos disponibles para este servicio por ahora.': T("admin.queue.solicitudes.motivo_corto.sin_turnos_por_ahora", null, "Sin turnos por ahora"),
    'El servicio solicitado no se realiza en este centro.': T("admin.queue.solicitudes.motivo_corto.servicio_no_disponible", null, "Servicio no disponible en este centro"),
  }[m] || m;
}

// --- arreglos de un click ---------------------------------------------------
// Los errores típicos de una orden tienen una salida típica. En vez de que el
// operador despliegue el select de motivos y recién ahí rechace, cada arreglo
// es UN botón — y el texto del botón dice lo que el PACIENTE va a recibir
// ("Pedirle una foto más nítida"), no el error que cometió. El contenido de
// ese aviso lo arma soluciones.py al ejecutarse el rechazo: turnos libres y
// deep links al turno, no un mail que sólo informa el problema.
function arreglosMap(){
  const ofrecerMedicinaGeneral = T("admin.queue.solicitudes.arreglo.ofrecer_medicina_general", null, "Ofrecerle turno con Medicina general");
  return {
    'El documento subido no es una orden médica.': T("admin.queue.solicitudes.arreglo.pedir_orden_medica", null, "Pedirle la orden médica"),
    'La orden no se lee con claridad.': T("admin.queue.solicitudes.arreglo.pedir_foto_nitida", null, "Pedirle una foto más nítida"),
    'La orden está a nombre de otra persona.': T("admin.queue.solicitudes.arreglo.pedir_orden_a_su_nombre", null, "Pedirle la orden a su nombre"),
    'El DNI de la orden no coincide con el de la cuenta.': T("admin.queue.solicitudes.arreglo.pedir_orden_con_su_dni", null, "Pedirle la orden con su DNI"),
    'La orden está vencida.': ofrecerMedicinaGeneral,
    'Falta la firma o la matrícula del médico.': ofrecerMedicinaGeneral,
    'La orden no tiene fecha.': ofrecerMedicinaGeneral,
    'La orden fue emitida en otra jurisdicción y no corresponde a este centro.': ofrecerMedicinaGeneral,
  };
}
function arreglosDe(s){
  if(!s.tiene_orden) return [];
  const ai = s.ai_json||{}, c = ai.campos||{};
  const out = [];
  if(ai.verdict==='documento_equivocado') out.push('El documento subido no es una orden médica.');
  if(ai.verdict==='ilegible') out.push('La orden no se lee con claridad.');
  if(ai.documento && ai.documento.jurisdiccion_valida===false) out.push('La orden fue emitida en otra jurisdicción y no corresponde a este centro.');
  if(c.paciente && c.paciente.coincide===false) out.push('La orden está a nombre de otra persona.');
  if(c.dni && c.dni.coincide===false) out.push('El DNI de la orden no coincide con el de la cuenta.');
  const fr = freshness(s);
  if(fr && fr.vencida) out.push('La orden está vencida.');
  const faltan = (ai.documento && ai.documento.campos_faltantes) || [];
  if(faltan.includes('firma_matricula')) out.push('Falta la firma o la matrícula del médico.');
  if(faltan.includes('fecha')) out.push('La orden no tiene fecha.');
  // Dos motivos distintos pueden ofrecer EL MISMO atajo (dos rutas para «no
  // coincide» terminan las dos en «Ofrecerle turno con Medicina general»), y
  // dos botones idénticos uno al lado del otro no son una elección: son un
  // error. Se deduplica por la etiqueta que se ve, no por el motivo interno.
  const vistos = new Set();
  const ARREGLOS = arreglosMap();
  const unicos = out.filter(m=>{
    const label = ARREGLOS[m] || m;
    if(vistos.has(label)) return false;
    vistos.add(label);
    return true;
  });
  // Dos como mucho: una fila de atajos más larga que eso deja de ser un atajo.
  return unicos.slice(0,2);
}
function arreglosHtml(s){
  const fixes = arreglosDe(s);
  if(!fixes.length || CURRENT_ROLE==='medico') return '';
  const ARREGLOS = arreglosMap();
  const fixTitle = esc(T("admin.queue.solicitudes.arreglo_title", null, "Rechaza la solicitud y le manda al paciente cómo resolverlo, con turnos a un click"));
  return `<div class="fixes">${fixes.map(m=>
    `<button type="button" class="fix" data-fix="${esc(m)}" title="${fixTitle}">${esc(ARREGLOS[m]||m)}</button>`
  ).join('')}</div>`;
}

// --- vigencia de la orden (desde la fecha leída; sin pegarle al server) ---
// AR: recetas de medicamentos ~30 días; órdenes de estudios/derivación ~90 días.
function parseFechaTxt(txt){
  const s=String(txt||'');
  const m=/(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/.exec(s);
  if(m){
    let d=+m[1], mo=+m[2], y=+m[3]; if(y<100) y+=2000;
    const dt=new Date(y,mo-1,d); return isNaN(dt.getTime())?null:dt;
  }
  // formato amigable con mes en letras ("28 nov 2023", "12 junio 2026") — es
  // lo que la propia fila muestra/edita, tiene que poder re-parsearse.
  const mm=/(\d{1,2})\s+de?\s*([a-záéíóú]+)\.?\s+(?:de\s+)?(\d{4})/i.exec(s) || /(\d{1,2})\s+([a-záéíóú]+)\.?\s+(\d{4})/i.exec(s);
  if(mm){
    const key=mm[2].toLowerCase().slice(0,3);
    const idx=['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'].indexOf(key);
    if(idx>=0){ const dt=new Date(+mm[3],idx,+mm[1]); return isNaN(dt.getTime())?null:dt; }
  }
  return null;
}
function freshness(s){
  const ai=s.ai_json||{};
  const dt=parseFechaTxt(ai.campos&&ai.campos.fecha&&ai.campos.fecha.valor);
  if(!dt) return null;
  const dias=Math.floor((Date.now()-dt.getTime())/86400000);
  if(dias<0) return null;
  const receta=ai.documento&&/receta|medicaci/i.test(ai.documento.tipo||'');
  const limite=receta?30:90;
  return {dt, dias, limite, vencida: dias>limite};
}

// "N días/meses/años" SIN el prefijo "hace" — se usa para la marca plegada
// en la fila Fecha ("✗ vencida por 2 años"); el banner aparte que decía esto
// mismo murió, ver camposTable().
function duracionTxt(dias){
  dias = Math.max(0, dias);
  if(dias<60) return Tn("admin.queue.solicitudes.n_dia", dias, null, "{n} día", "{n} días");
  const meses=Math.floor(dias/30.44);
  if(meses<24) return Tn("admin.queue.solicitudes.n_mes", meses, null, "{n} mes", "{n} meses");
  const anios=Math.floor(dias/365.25);
  return Tn("admin.queue.solicitudes.n_anio", anios, null, "{n} año", "{n} años");
}

// --- acción que sugiere la IA según el veredicto (y la vigencia) ---
function suggestFor(s){
  if(!s.tiene_orden) return 'aprobar';
  const ai = s.ai_json||{}, v = ai.verdict;
  if(v==='documento_equivocado'||v==='ilegible') return 'rechazar';
  if(ai.documento && ai.documento.jurisdiccion_valida===false) return 'rechazar';
  const fr = freshness(s);
  if(fr && fr.vencida) return 'rechazar';   // orden vencida → rechazo sugerido
  if(v==='parece_correcta') return 'aprobar';
  return 'none';   // dudosa / revisión manual → decide el revisor
}

// --- turno por defecto: el slot REAL del médico sugerido si lo hay (agenda
// válida, sin choques — lo calculó el server); si no, la preferencia. ---
function turnoDefault(s){
  // El horario que eligió el paciente manda: libre → ese; tomado → ninguno,
  // que el operador elija (journey 2026-09-16: se aprobaba el primer libre).
  const pedido = s.turno_pedido;
  if(pedido) return pedido.libre ? `${pedido.dia}T${pedido.hora}` : '';
  const slot = s.medico_sugerido && s.medico_sugerido.slot_iso;
  if(slot) return String(slot).replace(' ','T').slice(0,16);
  const base=nextWeekday9();               // 'YYYY-MM-DDT09:00'
  const p=(s.preferencia_horario||'').toLowerCase();
  if(/tarde/.test(p)) return base.slice(0,11)+'14:00';
  if(/noche/.test(p)) return base.slice(0,11)+'17:00';
  return base;                             // mañana / sin preferencia → 09:00
}

// preferencia de horario en TEXTO llano — si el valor guardado es un texto
// libre/datetime largo que no matchea ninguna franja general, no lo mostramos
// literal: mejor nada que un texto crudo sin sentido de "chip" (handoff
// 2026-07-17: "just say the general preference").
function prefGeneral(p){
  const s = String(p||'').toLowerCase();
  if(/noche/.test(s)) return 'noches';
  if(/tarde/.test(s)) return 'tardes';
  if(/ma[nñ]ana/.test(s)) return 'mañanas';
  // sin palabra de franja pero con una hora puntual (p.ej. una fecha/hora
  // cruda guardada de una versión vieja del wizard) → derivamos la franja
  // general de esa hora, nunca mostramos el datetime crudo tal cual.
  const m = /(\d{1,2}):(\d{2})/.exec(s);
  if(m){
    const h = +m[1];
    if(h>=19||h<6) return 'noches';
    if(h>=13) return 'tardes';
    return 'mañanas';
  }
  return '';
}

function prefIcon(pref){
  if(!pref) return '<span class="pref-slot" aria-hidden="true"></span>';
  const key = /ma[nñ]ana/.test(pref) ? 'mananas' : (/tarde/.test(pref) ? 'tardes' : 'noches');
  // `pref` es el valor de franja (wire, comparado en otros lados como
  // 'mañanas'/'tardes'/'noches') — se traduce sólo lo que se MUESTRA, nunca
  // el valor en sí.
  const franjaLbl = key==='mananas' ? T("admin.queue.solicitudes.franja_mananas", null, "mañanas")
    : key==='tardes' ? T("admin.queue.solicitudes.franja_tardes", null, "tardes")
    : T("admin.queue.solicitudes.franja_noches", null, "noches");
  const title = esc(T("admin.queue.solicitudes.prefiere_franja", { franja: franjaLbl }, "Prefiere {franja}"));
  // Plain words, never an icon-font ligature: when the font is late or blocked
  // the raw name ("wb_sunny") leaked, clipped, left of the slot (the product owner, 2026-09-15).
  // The whole phrase, not the bare word: it reads as its own line above the
  // slot (one left edge with the doctor, 2026-11-07).
  return `<span class="pref-txt pref-txt--${key}">${title}</span>`;
}

// The third way to resolve a request, next to Aprobar and Rechazar: talk to
// the person first (the hospital's counterpart, 2026-10: «nos contactábamos
// con las personas», «responder y hablar a la gente»). There is no staff ↔
// patient channel inside the product, so it opens the patient's own WhatsApp
// (or a call, or a mail) with the request already named; the request stays
// pending until someone approves or rejects it.
function contactarHtml(s){
  const c = String(s.contacto||'').trim();
  if(!c) return '';
  const vars = {nombre: Format.nombre(String(s.nombre||'').trim().split(/\s+/)[0]) || '', servicio: svcNombre(s.servicio_id)};
  const texto = T("admin.queue.solicitudes.contactar_texto", vars,
    "Hola " + vars.nombre + ", te escribimos por tu solicitud de turno de " + vars.servicio + ".");
  let href = '', externo = false;
  if(c.indexOf('@')!==-1){
    href = 'mailto:' + c + '?body=' + encodeURIComponent(texto);
  } else {
    const wa = Format.telWa(c);
    if(wa){ href = 'https://wa.me/' + wa + '?text=' + encodeURIComponent(texto); externo = true; }
    else { const tel = Format.telE164(c); if(tel) href = 'tel:' + tel; }
  }
  if(!href) return '';
  return `<a class="btn sol-contactar" href="${esc(href)}"${externo?' target="_blank" rel="noopener"':''}>`
    + esc(T("admin.queue.solicitudes.contactar", null, "Contactar")) + '</a>';
}

// La persona en la cabecera del detalle: «Sofía Herrera, 73», como la escribe
// la gente (responsable del producto, 2026-11-02, R8) — la misma función que Pacientes.
function personaDe(s){
  return Format.personaEdad(s.nombre, s.apellido, s.nacimiento)
    || patientName(s, T("admin.queue.solicitudes.sin_nombre_parentesis", null, "(sin nombre)"));
}

// El panel de detalle: los MISMOS controles que armaba pendingCard, sólo que
// repartidos en los bloques del handoff en vez de apilados en una tarjeta.
// Los hooks internos (.svc-sel, .med-sel, .turno, .turno-sel, .motivo-sel,
// [data-act], .pt-select) no cambian: executePending() y compañía los leen.
function detalleVM(s){
  const ai = s.ai_json || {};
  const sug = suggestFor(s);
  const prefGen = prefGeneral(s.preferencia_horario);
  const sugSvc = s.servicio_sugerido;
  const svcVal = sugSvc ? sugSvc.id : s.servicio_id;
  const docWrong = ai.documento && (ai.documento.es_orden===false || ai.documento.jurisdiccion_valida===false);
  const camposHtml = docWrong ? docCallout(ai)
    : (camposTable(ai.campos, s.id, freshness(s))
       || (s.tiene_orden ? `<p class="ai-resumen">${esc(ai.resumen||T("admin.queue.solicitudes.sin_analisis_automatico", null, "Sin análisis automático."))}</p>` : ''));

  // Bajo el nombre sólo el DNI, sin la palabra «DNI» (los puntos lo dicen) y
  // copiable como en Pacientes; la edad va en el nombre. El contacto son
  // iconos al lado del nombre, como en las listas — no el número escrito
  // (responsable del producto, 2026-11-03, R24: «máximo hacé iconos»; crítica del 08.11, A6/A7).
  const meta = Format.dniHtml(s.dni) || '';
  const contactoHtml = Format.contactoIconoHtml(s.contacto);

  // «Pide»: servicio y centro son links al catálogo, como en el handoff.
  // Derivar a otro servicio: el mismo combo de antes. El botón que lo abre va
  // en la tercera columna de la fila «Servicio» —pegado al dato que edita— y
  // el combo se despliega debajo del bloque, donde tiene ancho para leerse.
  const svcItems = svcCombolItems(s.servicio_id);
  const svcIt = svcItems.find(x=>x.v===svcVal);
  const svcDisp = svcIt ? (svcIt.t + (svcIt.s?' '+svcIt.s:'')) : svcNombre(svcVal);
  const svcLbl = T("admin.queue.table.servicio", null, "Servicio");
  const svcCombo = Combo.html('svc-sel', svcItems, svcVal, svcDisp, svcLbl,
    T("admin.queue.solicitudes.derivar_otro_servicio_title", null, "El operador puede derivar a otro servicio"));
  const puedeDerivar = CURRENT_ROLE!=='medico';
  const derivarAria = esc(T("admin.queue.solicitudes.derivar_otra_especialidad", null, "Derivar a otra especialidad"));
  const derivBtn = puedeDerivar
    // A worded quiet button, not a bare icon: the glyph alone read as a stray
    // mark next to the service name (the product owner, 2026-09-15).
    ? `<button type="button" class="btn btn-text svc-derive" data-toggle-service aria-expanded="${sugSvc?'true':'false'}" title="${derivarAria}">${esc(T("admin.queue.solicitudes.derivar", null, "Derivar"))}</button>`
    : '';
  const svcNota = sugSvc
    ? (sugSvc.motivo
        ? T("admin.queue.solicitudes.svc_nota_pidio_orden",
            { servicio: esc(svcNombre(s.servicio_id)), motivo: esc(sinGritos(sugSvc.motivo)) },
            "pidió {servicio}, la orden dice {motivo}")
        : T("admin.queue.solicitudes.svc_nota_pidio", { servicio: esc(svcNombre(s.servicio_id)) }, "pidió {servicio}"))
    : '';
  const derivacion = puedeDerivar
    ? `<div class="sol-deriv"><span class="svc-edit"${sugSvc?'':' hidden'}>${svcCombo}</span>`
      + (sugSvc?`<span class="svc-nota">${svcNota}</span>`:'')
      + `</div>`
    : '';

  // Business card format: line 1 is service+button, line 2 is the center (no
  // labels). La fecha de recepción NO va acá (contraparte técnica, 2026-10-24): bajo el
  // servicio se leía como la fecha del turno; vive sólo en la columna
  // «Recibida» de la lista (fmtCreatedShort).
  // La línea 2 dice lo que la paciente intentó sacar, sólo con lo que se
  // sabe (contraparte técnica, 2026-10-24: «qué día, centro y profesional había intentado
  // sacar»): el horario exacto que eligió en el wizard y si todavía está
  // libre (su hold) · el centro · el médico que pidió. Sin horario elegido
  // quedan el centro y el médico, sin fecha.
  const pedidoPartes = [];
  if(s.turno_pedido){
    const cuando = fmtTurno(`${s.turno_pedido.dia}T${s.turno_pedido.hora}`);
    pedidoPartes.push(`<span class="sol-pedido">${esc(s.turno_pedido.libre
      ? T("admin.queue.solicitudes.pidio_turno", { cuando }, "Pidió {cuando}")
      : T("admin.queue.solicitudes.pidio_turno_tomado", { cuando }, "Pidió {cuando}, ya está tomado"))}</span>`);
  }
  if(s.centro_slug) pedidoPartes.push(`<span class="sol-pide-centro">${esc(cenNombre(s.centro_slug))}</span>`);
  if(s.medico_pedido) pedidoPartes.push(esc(s.medico_pedido));
  const pide = [
    {v: `<a href="/admin/services?item=${encodeURIComponent(s.servicio_id)}">${esc(svcNombre(s.servicio_id))}</a>`, aside: derivBtn},
    {v: pedidoPartes.join(' · ')}
  ];
  // «Contanos tu caso» (contraparte técnica, 2026-10-17): sin turnos libres, el paciente
  // cuenta por qué no puede esperar y la mesa lo contacta para evaluarlo.
  // Su texto, tal cual, para que la mesa lo lea antes de llamar.
  // Con la conversación «Contanos tu caso» (responsable del producto, 2026-11-07) la mesa ve
  // primero la sintesis y abre el chat entero si hace falta (casoHtml).
  if(s.nota_paciente && !s.caso_resumen){
    pide.push({v: `<span class="sol-caso">${esc(T("admin.queue.solicitudes.caso_paciente",
      { texto: s.nota_paciente }, "Su caso: {texto}"))}</span>`});
  }

  // Médico: un laboratorio NO asigna médicos (responsable del producto, 2026-07-19) — sin control.
  let medCombo = '';
  if(!s.sin_medico && !esLab(svcVal)){
    const medSug = s.medico_sugerido;
    const base = medicosDe(svcVal);
    const medList = medSug && medSug.nombre && !base.some(x=>x.nombre===medSug.nombre)
      ? base.concat([{nombre: medSug.nombre}]) : base;
    const medItems = medList.map(x=>{
      const esSug = medSug && x.nombre===medSug.nombre;
      const libre = esSug ? fmtTurno(medSug.slot_iso) : (x.proximo && x.proximo.v) || '';
      return {v:x.nombre, t:x.nombre, s: libre ? T("admin.queue.solicitudes.libre_desde", { cuando: libre }, "— libre {cuando}") : ''};
    });
    // El médico que tiene libre el horario pedido gana sobre una asignación
    // automática vieja que no lo tiene.
    const medDefault = medicoPorDefecto(s, base, prefGen);
    const sel = medDefault ? medItems.find(x=>x.v===medDefault) : null;
    medCombo = Combo.html('med-sel', medItems, sel?sel.v:'', sel?sel.t:'',
      T("admin.queue.solicitudes.medico_a_asignar", null, "Médico: a asignar"),
      T("admin.queue.solicitudes.asignar_medico_title", null, "Asignar un médico"))
      // Sin médicos del servicio el combo queda vacío y lo dice (2026-10-23).
      + `<p class="med-vacio"${medItems.length?' hidden':''}>${esc(medVacioTxt(svcVal))}</p>`;
  }

  const turno0 = turnoDefault(s);
  const motivo0 = defaultMotivo(s);
  const motivoPlaceholder = esc(T("admin.queue.solicitudes.motivo_del_rechazo", null, "Motivo del rechazo…"));
  const motivoOpts = '<option value="" disabled>'+motivoPlaceholder+'</option>'
    + motivosPara(s).map(m=>`<option value="${esc(m)}"${m===motivo0?' selected':''}>${esc(motivoLabel(m))}</option>`).join('')
    + '<option value="__otro">'+esc(T("admin.queue.bulk_reject.otro_motivo", null, "Otro motivo…"))+'</option>';

  const okCls = 'btn btn-ok' + (sug==='aprobar'?' btn-suggest':'');
  const noCls = 'btn btn-no' + (sug==='rechazar'?' btn-suggest':'');

  // `turno0` es sólo lo que el server propone, igual para todas las tarjetas:
  // la agenda real del médico llega después (poblarTurnoSel). Hasta entonces
  // el horario está vacío y «Aprobar» apagado, así no sale un horario viejo.
  const aprobarHtml = medCombo + prefIcon(prefGen)
    + `<input type="hidden" class="turno" value="">`
    + `<select class="adm-sel turno-sel" data-agenda="cargando" aria-busy="true" disabled title="${esc(T("admin.queue.solicitudes.turno_sel_title", null, "Día y hora dentro de la agenda del servicio/médico"))}">`
    + `<option value="" selected disabled>${esc(T("admin.queue.solicitudes.agenda_cargando", null, "Cargando horarios…"))}</option></select>`
    + `<input type="datetime-local" class="turno-free" value="${turno0}" hidden>`
    + `<button class="${okCls}" data-act="aprobar" data-id="${s.id}" disabled>${esc(T("admin.queue.solicitudes.aprobar", null, "Aprobar"))}</button>`;

  const sobreturnoAutorizado = esc(T("admin.queue.solicitudes.sobreturno_autorizado", null, "Sobreturno autorizado"));
  const sobreturnoHtml = `<label class="overturn-ctl" hidden title="${esc(T("admin.queue.solicitudes.sobreturno_title", null, "No se ofrece a pacientes. Se registra como sobreturno autorizado."))}"><input type="checkbox" class="sobreturno"> ${sobreturnoAutorizado}</label>`;

  const rechazarHtml = `<select class="adm-sel motivo-sel" aria-label="${motivoPlaceholder}">${motivoOpts}</select>`
    + `<input type="text" class="motivo motivo-otro" placeholder="${esc(T("admin.queue.bulk_reject.motivo_placeholder", null, "Escribí el motivo"))}" style="display:none;">`
    + `<button class="${noCls}" data-act="rechazar" data-id="${s.id}">${esc(T("admin.queue.solicitudes.rechazar", null, "Rechazar"))}</button>`;

  return {
    contactarHtml: contactarHtml(s),
    id: s.id, sug,
    nombre: personaDe(s), contactoHtml,
    meta, veredicto: s.tiene_orden ? lecturaCorta(s) : '', dot: dotDe(s),
    pide, derivacion,
    ordenHtml: s.tiene_orden ? ordenPreview(s) : '',
    camposHtml, arreglosHtml: arreglosHtml(s), casoHtml: casoHtml(s),
    aprobarHtml, sobreturnoHtml, rechazarHtml
  };
}

// «Contanos tu caso» como conversación (responsable del producto, 2026-11-07): la síntesis
// arriba — la urgencia con su punto, qué quiere, por qué, los datos —; el
// chat entero sólo al pedirlo, porque leerlo queda en el registro de accesos.
// The dot marks ONLY what is urgent, in the warning colour: red is kept for
// errors (product owner), and a dot on every level marked nothing.
const CASO_URGENCIA = {
  emergencia: ['warn', "admin.queue.caso.urgencia_emergencia", "Emergencia: se le indicó guardia o 107"],
  alta: ['warn', "admin.queue.caso.urgencia_alta", "Urgencia alta"],
  media: ['', "admin.queue.caso.urgencia_media", "Urgencia media"],
  baja: ['', "admin.queue.caso.urgencia_baja", "Puede esperar"]
};
function casoHtml(s){
  const r = s.caso_resumen;
  if(!r) return '';
  const u = CASO_URGENCIA[r.urgencia];
  const partes = [];
  if(u) partes.push(`<p class="sol-caso-urg">${u[0] ? `<span class="sol-dot sol-d-${u[0]}"></span>` : ''}${esc(T(u[1], null, u[2]))}</p>`);
  if(r.quiere) partes.push(`<p class="sol-caso-quiere">${esc(r.quiere)}</p>`);
  if(r.por_que) partes.push(`<p>${esc(r.por_que)}</p>`);
  const hechos = Array.isArray(r.hechos) ? r.hechos.filter(Boolean) : [];
  if(hechos.length) partes.push(`<ul class="sol-caso-hechos">${hechos.map(h=>`<li>${esc(h)}</li>`).join('')}</ul>`);
  if(s.caso_chat){
    partes.push(`<button type="button" class="btn sol-caso-ver" data-caso-ver="${esc(s.id)}" aria-expanded="false">${esc(T("admin.queue.caso.ver", null, "Ver conversación"))}</button>`
      + `<div class="sol-caso-chat" hidden></div>`);
  }
  return `<div class="sol-caso-box">${partes.join('')}</div>`;
}

function casoChatHtml(data){
  const msgs = (data && Array.isArray(data.mensajes)) ? data.mensajes : [];
  return msgs.map(m=>{
    const quien = m.role === 'paciente'
      ? T("admin.queue.caso.paciente", null, "Paciente")
      : T("admin.queue.caso.asistente", null, "Asistente");
    return `<p class="sol-caso-msg sol-caso-${m.role === 'paciente' ? 'pac' : 'asi'}"><b>${esc(quien)}</b> ${esc(m.texto || '')}</p>`;
  }).join('');
}

async function casoVer(btn){
  const box = btn.nextElementSibling;
  if(!box) return;
  if(!box.hidden){
    box.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    btn.textContent = T("admin.queue.caso.ver", null, "Ver conversación");
    return;
  }
  if(!box.dataset.cargado){
    btn.disabled = true;
    try{
      const res = await api('/api/admin/requests/'+encodeURIComponent(btn.dataset.casoVer)+'/case-chat');
      if(!res.ok) throw new Error(String(res.status));
      box.innerHTML = casoChatHtml(await res.json());
      box.dataset.cargado = '1';
    }catch(e){
      box.innerHTML = `<p>${esc(T("admin.queue.caso.no_disponible", null, "No se pudo abrir la conversación. Probá de nuevo."))}</p>`;
    }finally{
      btn.disabled = false;
    }
  }
  box.hidden = false;
  btn.setAttribute('aria-expanded', 'true');
  btn.textContent = T("admin.queue.caso.ocultar", null, "Ocultar conversación");
}


// El botón sólo aparece cuando apretarlo puede funcionar: este entorno habla
// con HSI (hsi_activo del server) y el turno todavía es futuro (HSI no agenda
// turnos pasados). Un chip sin botón es información, no un callejón sin salida.
// «Enviar a HSI» cubre los turnos que nunca viajaron (omitido en cloud, filas
// previas a la integración).
//
// The control itself is the shared one (static/platform/hsi-sync.mjs,
// 2026-10-27, d1122568). Without a push it is the hook of the desk's
// extension, on a turno whose day has not passed (HSI takes no past turno).
function hsiActivo(){ return !!HsiSync && (EMR_SYNC_ACTIVE || !!HSI_EXTENSION); }
function diaDelTurno(s){
  const p = s && s.turno_dt && Dates && Dates.paredInstalacion ? Dates.paredInstalacion(s.turno_dt) : null;
  return p ? p.dia : String((s && s.turno_dt) || '').slice(0,10);
}
function hsiChip(est,s,o){
  if(!hsiActivo() || !s) return '';
  o = o || {};
  if(EMR_SYNC_ACTIVE){
    const futuro=s.turno_dt && new Date(s.turno_dt).getTime()>Date.now();
    return HsiSync.serverHtml(est, {id: s.id, futuro, linea: o.linea});
  }
  const dia = diaDelTurno(s);
  const hoy = Dates && Dates.hoyIsoInstalacion ? Dates.hoyIsoInstalacion() : '';
  if(!s.id || !dia || (hoy && dia < hoy)) return '';
  return HsiSync.extensionHtml({id: s.id, dia, auto: o.auto, linea: o.linea});
}

// «Recién aprobadas» (2026-10-27, d1122568): what was approved on THIS page,
// newest first, each with its «Cargar en HSI». Only without a server push —
// with one, the approval itself already went to HSI. Kept in memory only: a
// reload must not send anything by itself a second time.
let RECIENTES = [];
const RECIENTES_MAX = 5;
function recordarAprobada(s){
  if(!s || !s.id || EMR_SYNC_ACTIVE || !HSI_EXTENSION) return;
  RECIENTES = [String(s.id)].concat(RECIENTES.filter(id => id !== String(s.id))).slice(0, RECIENTES_MAX);
}
function renderRecientes(){
  const host = $('#hsi-recientes');
  if(!host) return;
  const filas = RECIENTES
    .map(id => ALL.find(x => String(x.id) === id))
    .filter(s => s && s.estado === 'aprobada')
    .map(s => ({s, hsi: hsiChip(s.hsi_estado, s, {auto: true, linea: true})}))
    .filter(f => f.hsi);
  host.hidden = !filas.length || !hsiActivo() || EMR_SYNC_ACTIVE;
  if(host.hidden){ host.innerHTML = ''; return; }
  host.innerHTML = '<h2 class="hsi-recientes-t">' + esc(T("admin.hsi.sync_recientes", null, "Recién aprobadas")) + '</h2>'
    + '<ul class="hsi-recientes-l">' + filas.map(f =>
      '<li class="hsi-recientes-f"><span class="hsi-recientes-q"><b>'
      + esc(patientName(f.s, T("admin.queue.solicitudes.sin_nombre_parentesis", null, "(sin nombre)"))) + '</b> · '
      + esc(svcNombre(f.s.servicio_id)) + ' · ' + esc(turnoOMotivoTxt(f.s)) + '</span>' + f.hsi + '</li>').join('')
    + '</ul>' + hsiInstalar();
}
// Where to install the extension, next to «Cargar en HSI» (the reference-deployment counterpart, 2026-10-27):
// the CSS hides it once the extension marks the page.
function hsiInstalar(){
  return HsiSync && HsiSync.instalarHtml && !EMR_SYNC_ACTIVE ? HsiSync.instalarHtml(HSI_INSTALAR) : '';
}

// Resueltas (Aprobadas/Rechazadas/Todas) = la MISMA cola densa que Pendientes
// (the product owner, 2026-08-06: "every state of the section uses the new queue view"),
// no la tabla de siempre — ver static/platform/admin-solicitudes.mjs. El estado se lee con un
// punto (.sol-dot), igual que la lectura de la IA en Pendientes, no con una
// pastilla — misma decisión, un solo componente para las dos cosas.
function estadoResueltaLabel(s){
  const map = {
    aprobada: T("admin.content.catalog.doctors.request_status_approved", null, "Aprobada"),
    rechazada: T("admin.content.catalog.doctors.request_status_rejected", null, "Rechazada"),
    cancelada: T("admin.content.catalog.doctors.request_status_cancelled", null, "Cancelada")
  };
  return map[s.estado] || s.estado || '—';
}
function estadoResueltaDot(s){
  return s.estado==='aprobada' ? 'sol-d-ok' : s.estado==='rechazada' ? 'sol-d-bad' : 'sol-d-none';
}
// Versión en texto plano de lo que turnoOMotivoCell mostraba (turno o
// motivo): la fila densa y el panel de detalle no llevan el <span> con
// estilo propio del "sobreturno autorizado" — a 13px en una fila de 34px el
// sufijo entra bien como texto sin más, y camposHtml (admin-queue.test.mjs no
// lo revisa) no perdía nada que un test estuviera guardando.
// Quién canceló lo dice el servidor (`cancelada_por`: el actor del evento «cancelada»): paciente,
// centro (la mesa) o nada si no consta — nunca se supone.
function cancelacionTxt(s){
  if(s.cancelada_por==='paciente') return T("admin.queue.solicitudes.cancelada_por_paciente", null, "Cancelada por el paciente");
  if(s.cancelada_por==='centro') return T("admin.queue.solicitudes.cancelada_por_centro", null, "Cancelada por el centro");
  return T("admin.queue.solicitudes.cancelada_sin_actor", null, "Cancelada");
}
// La celda «Turno / motivo» de la fila dice sólo quién canceló: la columna
// Estado ya dice «Cancelada», repetirlo no dice nada. Sin quién, el turno que
// se canceló. El detalle sigue diciendo la frase entera (cancelacionTxt).
function cancelacionCeldaTxt(s){
  if(s.cancelada_por==='paciente') return T("admin.queue.solicitudes.cancelada_celda_paciente", null, "Por el paciente");
  if(s.cancelada_por==='centro') return T("admin.queue.solicitudes.cancelada_celda_centro", null, "Por el centro");
  return fmtTurno(s.turno_dt) || '—';
}
function turnoOMotivoTxt(s){
  if(s.estado==='aprobada') return fmtTurno(s.turno_dt)
    + (s.sobreturno ? ' · '+T("admin.queue.solicitudes.sobreturno_autorizado", null, "Sobreturno autorizado") : '');
  if(s.estado==='cancelada') return cancelacionCeldaTxt(s);
  return s.motivo_rechazo || '—';
}
function asistenciaInfo(s){ return s.asistencia_resumen || {visitas:0,presentes:0,faltas:0,bloqueado_web:false,historial:[]}; }
function asistenciaElegible(s){
  // Regla relajada (responsable del producto, 2026-07-24): elegible si el día ART del turno no
  // es POSTERIOR a hoy — el mismo día ya alcanza (antes exigía que ya
  // hubiera pasado la hora exacta). Mismo criterio que
  // db.marcar_asistencia(exigir_no_futuro=True); el shift de -3h saca la
  // fecha de pared ART sin depender del huso del navegador.
  if(s.estado!=='aprobada' || !s.turno_dt) return false;
  const artDia=ms=>{ const d=new Date(ms-3*3600*1000); return d.getUTCFullYear()*10000+(d.getUTCMonth()+1)*100+d.getUTCDate(); };
  return artDia(new Date(s.turno_dt).getTime()) <= artDia(Date.now());
}
function asistenciaCell(s){
  if(!asistenciaElegible(s)) return '<span class="sr-only">'+esc(T("admin.queue.solicitudes.se_registra_despues_turno", null, "Se registra después del turno"))+'</span>';
  const actual=s.asistencia||'';
  const id='attendance-'+s.id;
  const paciente=esc(patientName(s, T("admin.queue.solicitudes.paciente_generico", null, "paciente")));
  return `<div class="attendance-control">
    <label class="sr-only" for="${id}">${esc(T("admin.queue.attendance.aria_group", { ref: paciente }, "Asistencia de {ref}"))}</label>
    <select id="${id}" class="adm-sel attendance-sel" data-attendance-select="${esc(s.id)}">
      <option value="" disabled${!actual?' selected':''}>${esc(T("admin.queue.solicitudes.resultado_de_la_visita", null, "Resultado de la visita"))}</option>
      <option value="presente"${actual==='presente'?' selected':''}>${esc(T("admin.queue.solicitudes.asistio", null, "Asistió"))}</option>
      <option value="ausente"${actual==='ausente'?' selected':''}>${esc(T("admin.queue.solicitudes.no_asistio", null, "No asistió"))}</option>
    </select>
    <button type="button" class="btn" data-attendance="${esc(s.id)}" aria-label="${esc(T("admin.queue.solicitudes.guardar_asistencia_de", { who: paciente }, "Guardar asistencia de {who}"))}">${esc(T("admin.queue.dependents.save", null, "Guardar"))}</button>
  </div>`;
}
// El ranking de asistencia (patient_ranking.py) manda cuando viene: la cola
// y la ficha del paciente tienen que decir lo MISMO del mismo paciente. Si el
// backend no lo mandó (fila vieja en caché, base caída), se cae al criterio
// de antes, que sólo mira faltas.
function attendanceLevel(info, s){
  const r = s && s.ranking;
  if(r && !r.sin_historial) return {cls:r.nivel||'none', label:`${r.etiqueta} (${r.puntaje})`};
  if(!(info.visitas||0)) return {cls:'none',label:T("admin.queue.solicitudes.sin_visitas_registradas", null, "Sin visitas registradas")};
  if(info.bloqueado_web) return {cls:'bad',label:T("admin.queue.solicitudes.asistencia_critica", null, "Asistencia crítica")};
  if((info.faltas||0)>0) return {cls:'warn',label:T("admin.queue.solicitudes.asistencia_irregular", null, "Asistencia irregular")};
  return {cls:'good',label:T("admin.reports.reports.buena_asistencia", null, "Buena asistencia")};
}
function visitasCell(s){
  const info=asistenciaInfo(s);
  const n=Number(info.visitas||0), level=attendanceLevel(info,s);
  const count= Tn("admin.queue.solicitudes.n_visita", n, null, "{n} visita", "{n} visitas");
  const verHistorial = esc(T("admin.queue.solicitudes.ver_historial", null, "Ver historial"));
  return `<button type="button" class="attendance-score" data-attendance-detail="${esc(s.id)}" aria-label="${esc(level.label)}, ${esc(count)}. ${verHistorial}"><span class="attendance-dot attendance-dot--${level.cls}" aria-hidden="true"></span><span>${esc(count)}</span></button>`;
}
function openAttendanceDetail(s){
  const dialog=$('#attendance-dialog'), body=$('#attendance-dialog-body'), info=asistenciaInfo(s);
  const level=attendanceLevel(info,s), history=info.historial||[], name=patientName(s, T("admin.queue.table.paciente", null, "Paciente"));
  $('#attendance-dialog-title').textContent=T("admin.queue.solicitudes.visitas_de", { name: name }, "Visitas de {name}").replace('{name}', name);
  const lines=history.length ? history.map(h=>{
    const main=h.a_asistencia==='presente'?T("admin.queue.solicitudes.asistio", null, "Asistió"):h.a_asistencia==='ausente'?T("admin.queue.solicitudes.no_asistio", null, "No asistió"):T("admin.queue.solicitudes.resultado_actualizado", null, "Resultado actualizado");
    const corrected=h.de_asistencia ? ' · '+T("admin.queue.solicitudes.antes_figuraba", { estado: h.de_asistencia==='presente'?T("admin.queue.solicitudes.como_asistio", null, "como asistió"):T("admin.queue.solicitudes.como_no_asistio", null, "como no asistió") }, "antes figuraba {estado}") : '';
    return `<li><span class="attendance-event-main">${esc(main)}</span><span class="attendance-event-meta">${esc(fmtTurno(h.created))}${esc(corrected)} · ${esc(h.operador||'—')}</span></li>`;
  }).join('') : '<li><span class="attendance-event-meta">'+esc(T("admin.queue.solicitudes.todavia_no_hay_visitas", null, "Todavía no hay visitas registradas."))+'</span></li>';
  // El porqué del puntaje, cuando el backend lo mandó: un número sin
  // explicación no sirve para decidir a quién llamar antes. Uno por línea:
  // dos motivos pegados con un punto medio son una lista disfrazada.
  const motivos=(s.ranking&&s.ranking.motivos)||[];
  const porque=motivos.length?`<ul class="attendance-dialog-porque">${motivos.map(m=>`<li>${esc(m)}</li>`).join('')}</ul>`:'';
  const nVisitas=Number(info.visitas||0), nAusencias=Number(info.faltas||0);
  const visitasTxt = Tn("admin.queue.solicitudes.n_visita", nVisitas, null, "{n} visita", "{n} visitas");
  const ausenciasTxt = Tn("admin.queue.solicitudes.n_ausencia", nAusencias, null, "{n} ausencia", "{n} ausencias");
  const gestionPresencial = info.bloqueado_web ? ' · '+T("admin.queue.solicitudes.necesita_gestion_presencial", null, "necesita gestión presencial para pedir turno") : '';
  body.innerHTML=`<p class="attendance-dialog-summary"><span class="attendance-dot attendance-dot--${level.cls}" aria-hidden="true"></span>${esc(level.label)}</p>${porque}<p class="attendance-dialog-meta">${esc(visitasTxt)} · ${esc(ausenciasTxt)}${esc(gestionPresencial)}</p><ul class="attendance-dialog-list">${lines}</ul>`;
  if(typeof dialog.showModal==='function') dialog.showModal(); else dialog.setAttribute('open','');
}
// Qué columnas son relevantes en cada contexto de la cola — Pendientes usa
// las de siempre (veredicto/cuando), Aprobadas/Rechazadas/Todas usan las
// cuatro nuevas del registro (estado/turno/hsi/resuelta_por). Se recalcula en
// cada render (barato: es un filtro sobre un array de 10 elementos) para que
// el engranaje de columnas siempre ofrezca sólo lo que aplica a lo que se
// está mirando — antes mostraba «HSI» aunque estuvieras en Pendientes.
function solColClave(){ return 'solicitudes'; }
function solColUniverso(){
  if(FILTER==='pendiente') return ['veredicto','paciente','dni','servicio','centro','cuando'];
  if(FILTER==='aprobada') return ['estado','paciente','dni','servicio','centro','turno'].concat(hsiActivo() ? ['hsi'] : []);
  if(FILTER==='rechazada') return ['estado','paciente','dni','servicio','centro','turno','resuelta_por'];
  return ['estado','paciente','dni','servicio','centro','turno'].concat(hsiActivo() ? ['hsi'] : []).concat(['resuelta_por']);   // todas
}
let COLA_GEAR_API = null;
function colaColsActivas(){
  const universo = solColUniverso();
  const full = Solicitudes.columns().filter(c=>universo.includes(c.key));
  if(!(Table && Table.columnas)) return full;
  COLA_GEAR_API = Table.columnas({clave: solColClave(), cols: full, onChange: render});
  return COLA_GEAR_API.activas();
}

// Un solo comparador para las dos familias de filas (pendiente/resuelta):
// las dos pasan por unifiedRowVM() y terminan con los mismos nombres de
// campo, así que ordenar «Paciente» funciona igual mires lo que mires.
// `estado` ordena por el mismo texto que pinta `veredicto` — es el mismo
// punto, nunca conviven las dos columnas en una fila.
function ordenarFilas(filas, sortState){
  const dir = sortState.dir;
  const key = sortState.key==='estado' ? 'veredicto' : sortState.key;
  return filas.slice().sort((a,b)=>{
    if(key==='dni'){
      const x=+String(a.dni).replace(/\D/g,''), y=+String(b.dni).replace(/\D/g,'');
      return x<y?-dir:x>y?dir:0;
    }
    if(key==='cuando'){
      const x=String(a._created||''), y=String(b._created||'');
      return x<y?-dir:x>y?dir:0;
    }
    if(key==='turno'){
      const x=String(a._turnoSort||''), y=String(b._turnoSort||'');
      return x<y?-dir:x>y?dir:0;
    }
    if(key==='hsi'){
      const x=String(a._hsiSort||''), y=String(b._hsiSort||'');
      return x<y?-dir:x>y?dir:0;
    }
    return String(a[key]||'').localeCompare(String(b[key]||''),'es') * dir;
  });
}

function counts(){
  // Sólo Pendientes lleva conteo (responsable del producto, 2026-07-20: "you probably only need
  // to count the pending") — en la opción del select y en el badge de la nav.
  const pend = ALL.filter(s=>s.estado==='pendiente').length;
  const opt = document.querySelector('#f-estado option[value="pendiente"]');
  // The option is the state's name, never a counter (the product owner, 2026-10-14).
  if(opt) opt.textContent = T("admin.queue.toolbar.estado_pendientes", null, "Pendientes");
  const badge = $('#nav-solicitudes-badge');
  if(badge){ badge.textContent = pend||0; badge.style.display = pend ? '' : 'none'; }
}

// --- pager: navegación a la izquierda + tamaño de página a la derecha —
// pendientes y resueltas
// (lista simple) tienen su propio tamaño de página, persistido aparte. ---
const PP_PEND_KEY = 'st_adm_pp_pend', PP_LIST_KEY = 'st_adm_pp_list';
const PP_OPTS = {pend:[5,10,20], list:[25,50,100]};
const PP_DEFAULT = {pend:10, list:50};
let PAGE = 1;

function ppGet(kind){
  const saved = Prefs ? Prefs.get('requests.page_size.'+kind, null) : null;
  const v = parseInt(saved==null?'':saved, 10);
  return PP_OPTS[kind].includes(v) ? v : PP_DEFAULT[kind];
}
function ppSet(kind, v){
  if(Prefs) Prefs.set('requests.page_size.'+kind, v);
}

function pagerHtml(total, perPage, page, kind){
  const pages = Math.max(1, Math.ceil(total/perPage));
  if(pages<=1) return '';
  page = Math.min(Math.max(1,page), pages);
  const selOpts = PP_OPTS[kind].map(o=>`<option value="${o}"${o===perPage?' selected':''}>${o}</option>`).join('');
  // ventana de hasta 10 números centrada en la página actual
  let start = Math.max(1, page-4), end = Math.min(pages, start+9);
  start = Math.max(1, end-9);
  let nums = '';
  for(let p=start;p<=end;p++) nums += `<a class="pgr-num${p===page?' on':''}" data-pg="${p}">${p}</a>`;
  const lastNum = end<pages ? `<a class="pgr-num${pages===page?' on':''}" data-pg="${pages}">${pages}</a>` : '';
  const nextPg = Math.min(pages, page+1);
  const atEnd = page>=pages;
  const gotoLbl = esc(T("admin.queue.solicitudes.pager_ir_a_pagina", null, "Ir a la página"));
  const siguienteLbl = esc(T("admin.queue.solicitudes.pager_siguiente", null, "Siguiente ›"));
  const ultimaLbl = esc(T("admin.queue.solicitudes.pager_ultima", null, "Última »"));
  const porPaginaLbl = esc(T("admin.queue.solicitudes.pager_por_pagina", null, "Por página"));
  return `<div class="pgr" data-kind="${kind}">
    <div class="pgr-pages">
      ${nums}<input type="number" class="pgr-goto" min="1" max="${pages}" placeholder="#" aria-label="${gotoLbl}" title="${gotoLbl}">${lastNum}
      <a class="pgr-next" data-pg="${nextPg}"${atEnd?' style="visibility:hidden"':''}>${siguienteLbl}</a>
      <a class="pgr-last" data-pg="${pages}"${atEnd?' style="visibility:hidden"':''}>${ultimaLbl}</a>
    </div>
    <div class="pgr-pp"><label>${porPaginaLbl} <select class="adm-sel pgr-pp-sel" data-kind="${kind}">${selOpts}</select></label></div>
  </div>`;
}

// --- búsqueda + filtros de la cola ---
// Getters: this file evaluates before i18nReady settles, so a value computed
// here stayed Spanish in the English panel. Each read happens at render.
const VERDICT_LBL = {
  get parece_correcta() { return T("admin.queue.solicitudes.verdict.parece_correcta", null, "Parece correcta"); },
  get dudosa() { return T("admin.queue.solicitudes.verdict.dudosa", null, "Dudosa"); },
  get revision_manual() { return T("admin.queue.solicitudes.verdict.revision_manual", null, "Revisión manual"); },
  get ilegible() { return T("admin.queue.solicitudes.verdict.ilegible", null, "Ilegible"); },
  get documento_equivocado() { return T("admin.queue.solicitudes.verdict.documento_equivocado", null, "Documento equivocado"); },
  get pendiente_ia() { return T("admin.queue.solicitudes.verdict.pendiente_ia", null, "Pendiente de análisis"); }
};
let FILTERS = {q:'',medico:'',especialidad:'',servicio:'',centro:'',verdict:'',sug:''};
window.hydrateRequestPrefs=function(){
  if(!Prefs) return;
  FILTERS=Object.assign(FILTERS, Prefs.get('requests.filters', {}));
  FILTER=Prefs.get('requests.status', FILTER);
  const link=requestDeepLink(location.search);
  if(link){ FILTERS=link.filters; FILTER=link.estado; SOLO_PROXIMOS=link.proximos; }
  const q=$('#q'); if(q) q.value=FILTERS.q||'';
  const status=$('#f-estado'); if(status) status.value=FILTER;
};
// «Ver los turnos» from a doctor that cannot be disabled yet (409, 2026-10-23):
// /admin/solicitudes?medico=<id>&estado=aprobada&proximos=1 opens exactly the
// appointments the message counted — the saved filters would otherwise hide
// some, and past ones would pad the list (live demo 24.10: 1 counted, 5
// shown). `proximos` is one-shot: never saved, dropped by any filter change.
let SOLO_PROXIMOS=false;
function requestDeepLink(search){
  const params=new URLSearchParams(search||'');
  const medico=(params.get('medico')||'').trim();
  if(!medico) return null;
  const estado=params.get('estado');
  return {
    filters:{q:'',medico:`id:${medico}`,especialidad:'',servicio:'',centro:'',verdict:'',sug:''},
    estado:['pendiente','aprobada','rechazada','todas'].includes(estado) ? estado : 'todas',
    proximos:params.get('proximos')==='1'
  };
}
function saveRequestPrefs(){
  if(!Prefs) return;
  Prefs.set('requests.filters', FILTERS);
  Prefs.set('requests.status', FILTER);
}

function prettySlug(s){ return String(s||'').split('-').map(w=>w?w[0].toUpperCase()+w.slice(1):w).join(' ').trim(); }
function campo(s,k){ const c=(s.ai_json&&s.ai_json.campos&&s.ai_json.campos[k])||null; return c&&c.valor!=null?String(c.valor):''; }
function verdictOf(s){ return (s.ai_json&&s.ai_json.verdict)||''; }
function medicoNombre(nombre){ return Format.nombre(nombre||''); }
function medicoIdDe(s){ return String(s.medico_asignado_id || s.slot_medico_id || '').trim(); }
function medicoPorId(id){ return MED_LIST.find(m=>String(m.id||'')===String(id||'')) || null; }
function medicoPorNombreUnico(nombre){
  const iguales=MED_LIST.filter(m=>_norm(m.nombre)===_norm(nombre));
  return iguales.length===1 ? iguales[0] : null;
}
function medicoDeSolicitud(s){ return medicoPorId(medicoIdDe(s)) || medicoPorNombreUnico(s.medico_asignado); }
// An ID is the selection key whenever the row has one. Legacy name-only rows
// keep their own key; duplicate names never silently become another doctor's.
function medicoClaveDe(s){
  const id=medicoIdDe(s);
  return id ? `id:${id}` : (s.medico_asignado ? `nombre:${s.medico_asignado}` : '');
}
// medico_nombre is the server's catalog name for the row's doctor id: a
// slot-only request has no free-text name, and a disabled doctor is in no list.
function medicoEtiquetaDe(s){
  const medico=medicoDeSolicitud(s);
  return medico ? medicoNombre(medico.nombre) : medicoNombre(s.medico_nombre || s.medico_asignado);
}
function especialidadUnicaDelServicio(servicioId){
  const coincidencias=MED_LIST.filter(m=>coincideEsp(svcNombre(servicioId), m.especialidad));
  const especialidades=[...new Set(coincidencias.map(m=>String(m.especialidad||'').trim()).filter(Boolean))];
  return especialidades.length===1 ? especialidades[0] : '';
}
function especialidadDe(s){
  const medico=medicoDeSolicitud(s);
  // An assigned/picked clinician is authoritative. Without one, show a
  // specialty only when the service maps to exactly one real specialty;
  // broad clusters/category are deliberately not shown as a specialty.
  return medico && medico.especialidad ? medico.especialidad : especialidadUnicaDelServicio(s.servicio_id);
}

function distinct(getter){
  const seen=new Set();
  ALL.forEach(s=>{ const v=(getter(s)||'').trim(); if(v) seen.add(v); });
  return [...seen].sort((a,b)=>a.localeCompare(b,'es'));
}

function fillSelect(sel, values, labelFn){
  const key=sel.dataset.key, cur=FILTERS[key]||sel.value;
  const actual=FilterControls.fill(sel, values, cur, labelFn);
  if(actual!==cur){ FILTERS[key]=''; }
}

function buildFilterOptions(){
  fillSelect($('#f-medico'), FilterControls.uniqueOptions(ALL, medicoClaveDe, medicoEtiquetaDe));
  fillSelect($('#f-especialidad'), distinct(especialidadDe), v=>v);
  fillSelect($('#f-servicio'), distinct(s=>s.servicio_id), svcNombre);
  fillSelect($('#f-centro'), distinct(s=>s.centro_slug), cenNombre);
  fillSelect($('#f-verdict'), distinct(verdictOf), v=>VERDICT_LBL[v]||v);
}

function matchesFilters(s){
  if(SOLO_PROXIMOS && !(s.turno_dt && new Date(s.turno_dt).getTime()>Date.now())) return false;
  if(FILTERS.medico && medicoClaveDe(s)!==FILTERS.medico) return false;
  if(FILTERS.especialidad && especialidadDe(s)!==FILTERS.especialidad) return false;
  if(FILTERS.servicio && s.servicio_id!==FILTERS.servicio) return false;
  if(FILTERS.centro && s.centro_slug!==FILTERS.centro) return false;
  if(FILTERS.verdict && verdictOf(s)!==FILTERS.verdict) return false;
  if(FILTERS.sug && suggestFor(s)!==FILTERS.sug) return false;
  if(FILTERS.q){
    const hay=[s.dni,s.nombre,s.apellido,s.medico_asignado,s.servicio_id,s.centro_slug,
      campo(s,'dni'),campo(s,'paciente'),campo(s,'medico'),campo(s,'matricula'),
      campo(s,'especialidad'),campo(s,'fecha'),(s.ai_json&&s.ai_json.resumen)||'',verdictOf(s)]
      .join(' ').toLowerCase();
    const q=FILTERS.q.toLowerCase(), qDni=q.replace(/\D/g,'');
    if(!hay.includes(q) && !(qDni.length>=7 && hay.replace(/\D/g,'').includes(qDni))) return false;
  }
  return true;
}

function filtrosActivos(){
  return SOLO_PROXIMOS||FILTERS.q||FILTERS.medico||FILTERS.especialidad||FILTERS.servicio||FILTERS.centro||FILTERS.verdict||FILTERS.sug;
}

function syncTools(){
  [['f-medico','medico'],['f-especialidad','especialidad'],['f-servicio','servicio'],['f-centro','centro'],['f-verdict','verdict'],['f-sug','sug']]
    .forEach(([id,k])=>$('#'+id).classList.toggle('on', !!FILTERS[k]));
  const filterCount=['medico','especialidad','servicio','centro','verdict','sug'].filter(k=>!!FILTERS[k]).length + (FILTER!=='pendiente'?1:0) + (SOLO_PROXIMOS?1:0);
  const count=$('#adm-filter-count'), toggle=$('#adm-filter-toggle');
  if(count){ count.textContent=filterCount; count.hidden=!filterCount; }
  if(toggle) toggle.classList.toggle('on', !!filterCount);
  $('#f-clear').style.display = filtrosActivos() ? '' : 'none';
  podarFiltros();
}

// Las cuatro pestañas del filtro de estado (Pendientes/Aprobadas/Rechazadas/
// Todas) pintan TODAS con la misma cola densa — renderCola() decide adentro
// qué agrupar según FILTER (responsable del producto, 2026-08-06: "every state of the section
// uses the new queue view"; antes sólo Pendientes pasaba por acá y el resto
// caía en la tabla vieja + su paginador, que murieron los dos con esto —
// pagerHtml()/ppGet() siguen definidas porque una guarda de test las ancla,
// pero nada de Solicitudes las llama más).
function render(){
  counts();
  syncTools();
  renderRecientes();
  renderCola();
}

// ===================== la cola: lista + detalle ==========================
//
// Rediseño 2026-08-05 (handoff "Pinamar Admin Solicitudes Linear"): la cola
// dejó de ser una pila de tarjetas paginada. El markup vive en
// static/platform/admin-solicitudes.mjs; acá queda el estado, los
// grupos y el cableado.

// Cuál está abierta en el panel derecho; cuántas se muestran (load-more, sin
// páginas); por qué columna ordena cada grupo; y si se agrupa por sugerencia.
let SEL_ID = null;
// The page opens on what asks for action, not on the whole backlog (the product owner,
// 2026-11-08: "don't show such a long page by default"):
// a flat list shows 25 at a time, a suggestion group its first 5 and then
// «Cargar N más» inside the group. A whole-group button still resolves every
// request of that group, shown or not — the confirm says how many.
let COLA_LIMITE = 25;
const COLA_LOTE = 25;
const COLA_GRUPO_BASE = 5;
const COLA_GRUPO_LOTE = 20;
let COLA_GRUPO_LIM = {};
let COLA_SORT = {key:'cuando', dir:-1};
let COLA_AGRUPA = true;
// Aprobadas/Rechazadas/Todas tienen su propio load-more y su propio orden
// (independientes del de Pendientes: son listas distintas). RES_SORT también
// gobierna el orden de Aprobadas/Rechazadas cuando se filtra a cada una por
// separado — comparten el mismo criterio que en «Todas», tiene sentido que
// el operador lo elija una vez.
let COLA_LIMITE_RES = 25;
let RES_SORT = {key:'turno', dir:-1};
// Los grupos de resueltas dentro de «Todas» arrancan plegados (responsable del producto,
// 2026-08-06): son historia, no pendientes de acción. Filtrar directo a
// Aprobadas o Rechazadas no pasa por este mapa — esa vista siempre expande.
let COLA_RES_COLAPSO = {aprobada:true, rechazada:true, cancelada:true};
// Las tildadas viven en un Set y no en el DOM: al abrir otra solicitud se
// redibuja la cola entera, y una selección que sólo existiera como `checked`
// en un <input> se perdería en cada click.
const COLA_SEL = new Set();
// Ancla del shift-click: la última casilla tildada a mano. Tildar una, y
// shift-tildar otra, aplica el estado de la segunda a todo el rango que hay
// entre ambas EN PANTALLA (el orden que se ve, con su agrupado y su
// ordenamiento) — el gesto que la mesa de entrada ya trae aprendido de
// cualquier bandeja de correo.
let COLA_ANCLA = null;

// Los tres grupos, en el orden en que la mesa de entrada los despacha: los dos
// inequívocos en lote, el del medio a mano.
// `bulk` es el botón que despacha el grupo entero; el del medio no lo tiene.
// `titulo` es getter por lo mismo que VERDICT_LBL.
const COLA_GRUPOS = [
  {sug:'aprobar',  bulk:'aprobar',
                   get titulo(){ return T("admin.queue.solicitudes.grupo_aprobar_titulo", null, "Aprobar según sugerencia"); }},
  {sug:'none',     bulk:null,
                   get titulo(){ return T("admin.queue.solicitudes.grupo_revisar_titulo", null, "Revisar a mano"); }},
  {sug:'rechazar', bulk:'rechazar',
                   get titulo(){ return T("admin.queue.solicitudes.grupo_rechazar_titulo", null, "Rechazar según sugerencia"); }}
];

// La lectura de la IA, en las pocas palabras que entran en una fila. El
// resumen largo sigue viviendo en el panel de detalle; acá hace falta la
// etiqueta que distingue una fila de la de al lado.
// The wire value behind that label: rejection groups split on it (never on
// the translated text) and the whole-group button filters by it.
function lecturaClave(s){
  if(!s.tiene_orden) return 'sin_orden_adjunta';
  const ai = s.ai_json||{}, d = ai.documento||{};
  if(d.es_orden===false) return 'no_es_una_orden';
  if(d.jurisdiccion_valida===false) return 'otra_jurisdiccion';
  const fr = freshness(s);
  if(fr && fr.vencida) return 'orden_vencida';
  const c = ai.campos||{};
  if(c.dni && c.dni.coincide===false) return 'orden_de_otra_persona';
  if(c.paciente && c.paciente.coincide===false) return 'orden_de_otra_persona';
  if(c.especialidad && c.especialidad.coincide===false) return 'otra_especialidad';
  if(ai.verdict==='parece_correcta') return 'coincide';
  return 'v:' + (VERDICT_LBL[ai.verdict] ? ai.verdict : 'revision_manual');
}
function lecturaCorta(s){
  const k = lecturaClave(s);
  if(k==='sin_orden_adjunta') return T("admin.queue.solicitudes.lectura.sin_orden_adjunta", null, "Sin orden adjunta");
  if(k==='no_es_una_orden') return T("admin.queue.solicitudes.lectura.no_es_una_orden", null, "No es una orden");
  if(k==='otra_jurisdiccion') return T("admin.queue.solicitudes.lectura.otra_jurisdiccion", null, "Otra jurisdicción");
  if(k==='orden_vencida') return T("admin.queue.solicitudes.lectura.orden_vencida", null, "Orden vencida");
  if(k==='orden_de_otra_persona') return T("admin.queue.solicitudes.lectura.orden_de_otra_persona", null, "Orden de otra persona");
  if(k==='otra_especialidad') return T("admin.queue.solicitudes.lectura.otra_especialidad", null, "Otra especialidad");
  if(k==='coincide') return T("admin.queue.solicitudes.lectura.coincide", null, "Coincide");
  const v = (s.ai_json||{}).verdict;
  return VERDICT_LBL[v] || T("admin.queue.solicitudes.verdict.revision_manual", null, "Revisión manual");
}
// What a pending ROW says (the product owner, 2026-11-08: "it keeps saying no order…
// nobody in Gmail writes 'no attachment'"): nothing about a missing order — a
// paperclip marks the ones that carry an attachment — and nothing when the
// suggestion is to approve; only the reason to look twice or to reject.
function lecturaFila(s){
  if(!s.tiene_orden || suggestFor(s)==='aprobar') return '';
  return lecturaCorta(s);
}

function dotDe(s){
  const sug = suggestFor(s);
  if(!s.tiene_orden) return 'sol-d-none';
  // A suggestion to reject is a reason to look, not an error: warning colour,
  // never red (the group heading already says «Rechazar»).
  if(sug==='aprobar') return 'sol-d-ok';
  return 'sol-d-warn';
}

// Una fila, mire lo que mire la pantalla (pendiente o resuelta): las dos
// familias terminan en el mismo shape, por eso «Todas» puede mostrarlas en
// una sola grilla con un solo encabezado. `estado`/`turno`/`hsi` quedan en
// blanco para una pendiente (todavía no hay nada que mostrar ahí) y
// `veredicto`/`cuando` en blanco para una resuelta (ya no importa cuándo
// llegó, importa qué se decidió) — static/platform/admin-solicitudes.mjs elige cuál de los
// dos pares pintar según el `cols` que le llega, nunca los dos juntos.
function unifiedRowVM(s){
  const pend = s.estado==='pendiente';
  return {
    id: s.id,
    paciente: patientName(s, T("admin.queue.solicitudes.sin_nombre_parentesis", null, "(sin nombre)")),
    dni: fmtDni(s.dni) || '—',
    servicio: svcNombre(s.servicio_id),
    centro: cenNombre(s.centro_slug),
    veredicto: pend ? lecturaFila(s) : estadoResueltaLabel(s),
    dot: pend ? '' : estadoResueltaDot(s),
    adjunto: pend ? !!s.tiene_orden : null,
    cuando: pend ? fmtCreatedShort(s.created) : '',
    turno: pend ? '' : turnoOMotivoTxt(s),
    hsiHtml: (hsiActivo() && !pend && s.estado==='aprobada') ? hsiChip(s.hsi_estado, s) : '',
    resuelta_por: pend ? '' : (Format.resueltaPor(s.resuelta_por, s.resuelta_por_nombre, true) || '—'),
    checkbox: pend,
    abierta: String(s.id)===String(SEL_ID),
    tildada: pend && COLA_SEL.has(String(s.id)),
    _created: s.created,
    _sug: pend ? suggestFor(s) : null,
    _clave: pend ? lecturaClave(s) : null,
    _lectura: pend ? lecturaCorta(s) : '',
    _turnoSort: pend ? '' : (s.estado==='aprobada' ? (s.turno_dt||'') : (s.motivo_rechazo||'')),
    _hsiSort: (EMR_SYNC_ACTIVE && !pend && s.estado==='aprobada') ? (s.hsi_estado||'') : ''
  };
}

// El panel de detalle de una fila YA RESUELTA: mismo shape que detalleVM(),
// mucho más corto — no hay nada que «Resolver», así que no hay
// aprobarHtml/rechazarHtml. `asistenciaHtml` reusa las MISMAS funciones que
// la tabla vieja usaba en su celda (asistenciaCell/visitasCell): lo único
// que cambia es dónde viven — antes en una <td>, ahora en el panel.
function detalleResVM(s){
  const nombre = personaDe(s);
  const meta = (fmtDni(s.dni)||'—') + ' · ' + svcNombre(s.servicio_id) + ' · ' + cenNombre(s.centro_slug);
  const kv = [{k: T("admin.queue.solicitudes.col_turno_motivo", null, "Turno / motivo"),
    v: esc(s.estado==='cancelada' ? cancelacionTxt(s) : turnoOMotivoTxt(s))}];
  if(hsiActivo() && s.estado==='aprobada'){
    const hsi = hsiChip(s.hsi_estado, s, {linea: true});
    if(hsi) kv.push({k:'HSI', v: hsi + hsiInstalar()});
  }
  if(s.resuelta_por) kv.push({k: T("admin.queue.solicitudes.col_resuelta_por", null, "Resuelta por"), v: esc(Format.resueltaPor(s.resuelta_por, s.resuelta_por_nombre))});
  const asistenciaHtml = s.estado==='aprobada'
    ? visitasCell(s) + (asistenciaElegible(s) ? asistenciaCell(s) : '')
    : '';
  return {
    id: s.id, nombre, meta,
    estadoTexto: estadoResueltaLabel(s), estadoDot: estadoResueltaDot(s),
    kv, asistenciaHtml, casoHtml: casoHtml(s)
  };
}

// Arma la fila del bulkLabel de un grupo de pendientes ("Aprobar
// todas"/"Rechazar todas", responsable del producto 2026-08-06 — ver el comentario en
// COLA_GRUPOS más abajo).
function bulkLabelDe(bulk){
  return bulk==='aprobar'
    ? T("admin.queue.solicitudes.aprobar_todas", null, "Aprobar todas")
    : bulk==='rechazar'
      ? T("admin.queue.solicitudes.rechazar_todas", null, "Rechazar todas")
      : '';
}

// Pending requests by what to do with them: approve, then one group per
// reason to look by hand («Revisar a mano · Dudosa») and one per rejection
// reason — the reason is the group's title, so the rows under it don't
// repeat it (the product owner, 2026-11-08: groups by meaning, no repetition) and each
// row keeps only the paperclip. Every group shows COLA_GRUPO_BASE rows,
// grows by COLA_GRUPO_LOTE from its own «Cargar N más», and always keeps the
// open request in view.
function gruposPendientes(vms){
  const out = [];
  const add = (id, titulo, bulk, motivo, filas)=>{
    if(!filas.length) return;
    const orden = ordenarFilas(filas, COLA_SORT).map(vm=>Object.assign({}, vm, {veredicto:''}));
    let vis = orden.slice(0, COLA_GRUPO_LIM[id] || COLA_GRUPO_BASE);
    const abierta = SEL_ID ? orden.find(vm=>String(vm.id)===String(SEL_ID)) : null;
    if(abierta && vis.indexOf(abierta)===-1) vis = vis.concat(abierta);
    const mas = orden.length - vis.length;
    out.push({id, titulo, total:orden.length, bulk, bulkLabel:bulkLabelDe(bulk), motivo,
      filas:vis, mas, masLote:Math.min(COLA_GRUPO_LOTE, mas), colapsable:false, colapsado:false});
  };
  add('aprobar', COLA_GRUPOS[0].titulo, 'aprobar', '', vms.filter(vm=>vm._sug==='aprobar'));
  const porMotivo = (sug, titulo, bulk)=>{
    const filas = vms.filter(vm=>vm._sug===sug);
    const cuenta = {};
    filas.forEach(vm=>{ cuenta[vm._clave] = (cuenta[vm._clave]||0) + 1; });
    Object.keys(cuenta).sort((a,b)=>cuenta[b]-cuenta[a] || (a<b?-1:a>b?1:0)).forEach(k=>{
      const de = filas.filter(vm=>vm._clave===k);
      add(sug+':'+k, titulo(de[0]._lectura), bulk, k, de);
    });
  };
  porMotivo('none', l=>COLA_GRUPOS[1].titulo + ' · ' + l, null);
  porMotivo('rechazar', l=>l, 'rechazar');
  return out;
}

// Pinta la cola en las CUATRO pestañas de estado (responsable del producto, 2026-08-06). El
// shape que le llega a Solicitudes.render() es siempre el mismo —
// `grupos`/`cols`/`detalle`—, sólo cambia CÓMO se arman según FILTER:
//   · pendiente: sin cambios — agrupado por sugerencia (o plano), load-more.
//   · aprobada/rechazada: un solo grupo plano, siempre expandido, load-more
//     propio (COLA_LIMITE_RES) — nunca comparte cupo con Pendientes.
//   · todas: Pendientes primero (con su propio agrupado, intacto) y después
//     Aprobadas/Rechazadas/Canceladas, plegables y arrancando plegadas — son
//     historia, no piden acción.
function renderCola(){
  const cols = colaColsActivas();
  let grupos, pie=null, seleccionadas=0, conSugerencia=false, abiertaRaw=null, vacioMsg;

  if(FILTER==='pendiente'){
    const pend = ALL.filter(s=>s.estado==='pendiente').filter(matchesFilters);
    // Las tildadas que ya no están en la cola (resueltas, filtradas) se
    // sueltan: el contador de la barra tiene que decir lo que el operador ve.
    const vivos = new Set(pend.map(s=>String(s.id)));
    [...COLA_SEL].forEach(id=>{ if(!vivos.has(id)) COLA_SEL.delete(id); });

    // Grouped, every group trims itself (gruposPendientes); flat, the list
    // shows COLA_LIMITE and the foot loads the next batch.
    const visibles = COLA_AGRUPA ? pend : pend.slice(0, COLA_LIMITE);
    if(SEL_ID && !visibles.some(s=>String(s.id)===String(SEL_ID))) SEL_ID = null;
    const vms = visibles.map(s=>unifiedRowVM(s));
    if(COLA_AGRUPA){
      grupos = gruposPendientes(vms);
    } else {
      const filas = ordenarFilas(vms, COLA_SORT);
      grupos = filas.length ? [{titulo:T("admin.queue.toolbar.estado_pendientes", null, "Pendientes"), total:filas.length, bulk:null, filas, colapsable:false, colapsado:false}] : [];
    }
    abiertaRaw = SEL_ID ? visibles.find(s=>String(s.id)===String(SEL_ID)) : null;
    const sugeribles = [...COLA_SEL].filter(id=>{
      const s = ALL.find(x=>String(x.id)===String(id));
      return s && suggestFor(s)!=='none';
    }).length;
    seleccionadas = COLA_SEL.size;
    conSugerencia = sugeribles>0;
    pie = COLA_AGRUPA ? null : {mostrando: visibles.length, total: pend.length, lote: COLA_LOTE};
    vacioMsg = emptyMsg('pendientes');

  } else if(FILTER==='todas'){
    const pend = ALL.filter(s=>s.estado==='pendiente').filter(matchesFilters);
    grupos = [];
    if(pend.length){
      const pvms = pend.map(s=>unifiedRowVM(s));
      if(COLA_AGRUPA){
        grupos.push(...gruposPendientes(pvms));
      } else {
        const filas = ordenarFilas(pvms, COLA_SORT);
        if(filas.length) grupos.push({titulo:T("admin.queue.toolbar.estado_pendientes", null, "Pendientes"), total:filas.length, bulk:null, filas, colapsable:false, colapsado:false});
      }
    }
    // Aprobadas → Rechazadas → Canceladas: mismo orden que el filtro directo
    // ofrece en el <select>. Cada una sólo aparece si tiene filas — un grupo
    // vacío no dice nada que el operador necesite.
    [['aprobada','grupo_aprobadas_titulo','Aprobadas'],
     ['rechazada','grupo_rechazadas_titulo','Rechazadas'],
     ['cancelada','grupo_canceladas_titulo','Canceladas']].forEach(([estado,key,fallback])=>{
      const rows = ALL.filter(s=>s.estado===estado).filter(matchesFilters);
      if(!rows.length) return;
      const vms = ordenarFilas(rows.map(s=>unifiedRowVM(s)), RES_SORT);
      const colapsado = !!COLA_RES_COLAPSO[estado];
      grupos.push({titulo:T("admin.queue.solicitudes."+key, null, fallback), total:vms.length, bulk:null,
        filas: colapsado ? [] : vms, colapsable:true, colapsado, id:estado});
    });
    abiertaRaw = SEL_ID ? ALL.find(s=>String(s.id)===String(SEL_ID)) : null;
    vacioMsg = emptyMsg('acá');

  } else {
    // aprobada | rechazada: un único grupo plano, siempre expandido — filtrar
    // directo a uno no necesita plegado, ya es lo único que se está mirando.
    const rows = ALL.filter(s=>s.estado===FILTER).filter(matchesFilters);
    const sorted = ordenarFilas(rows.map(s=>unifiedRowVM(s)), RES_SORT);
    if(SEL_ID && !sorted.some(vm=>String(vm.id)===String(SEL_ID))) SEL_ID = null;
    const visibles = sorted.slice(0, COLA_LIMITE_RES);
    const titulo = FILTER==='aprobada'
      ? T("admin.queue.solicitudes.grupo_aprobadas_titulo", null, "Aprobadas")
      : T("admin.queue.solicitudes.grupo_rechazadas_titulo", null, "Rechazadas");
    grupos = visibles.length ? [{titulo, total:visibles.length, bulk:null, filas:visibles, colapsable:false, colapsado:false}] : [];
    pie = {mostrando: visibles.length, total: sorted.length, lote: COLA_LOTE};
    vacioMsg = emptyMsg('acá');
    abiertaRaw = SEL_ID ? rows.find(s=>String(s.id)===String(SEL_ID)) : null;
  }

  const detalleTipo = abiertaRaw && abiertaRaw.estado!=='pendiente' ? 'resuelta' : 'pendiente';
  const detalle = !abiertaRaw ? null : (detalleTipo==='resuelta' ? detalleResVM(abiertaRaw) : detalleVM(abiertaRaw));

  const listadas = grupos.reduce((n,g)=>n+g.total, 0);
  const vista = colaDisclosure(listadas);

  $('#list').innerHTML = Solicitudes.render({
    grupos, cols,
    sort: FILTER==='pendiente' ? COLA_SORT : RES_SORT,
    vacio: vacioMsg, vacioHtml: vista.html, pie, seleccionadas, conSugerencia, detalle, detalleTipo,
    soloClip: FILTER==='pendiente' && COLA_AGRUPA
  });

  colaHeader(ALL.filter(s=>s.estado==='pendiente').filter(matchesFilters).length, listadas);
  if(detalleTipo==='pendiente' && abiertaRaw){
    poblarTurnoSel($('#list .card[data-id]'));
    const c = (abiertaRaw.ai_json && abiertaRaw.ai_json.campos) || {};
    const mat = c.matricula && c.matricula.valor;
    if(mat) verificarMatricula(abiertaRaw.id, mat, (c.medico && c.medico.valor) || '');
  }
}

// La banda de 44px de la pantalla. El título y «Crear turno» ya vivían ahí;
// el rediseño le suma el conteo de pendientes pegado al título y, del otro
// lado, el toggle de agrupado y el engranaje de columnas. Los tres van dentro
// de UN contenedor que absorbe el espacio libre, así el `space-between` de
// .adm-h1row —que comparten las ~20 secciones— sigue dejando «Crear turno»
// contra el borde derecho.
// El engranaje de columnas cambia de universo según la pestaña (ver
// solColUniverso) y «Agrupar por sugerencia» sólo tiene sentido en
// Pendientes — así que, a diferencia de antes, esto se reconstruye ENTERO en
// cada llamada (responsable del producto, 2026-08-06: las cuatro pestañas pasan por acá ahora).
// El click de «Agrupar» vive en el listener delegado de más abajo, no acá
// adentro — un listener puesto en el momento de crear el nodo no sobrevive a
// reconstruir el innerHTML al cambiar de pestaña.
// Nothing listed → no column picker and no grouping: they would act on an
// empty list (progressive disclosure, product owner 2026-10-26).
function colaHeader(pendientes, listadas){
  const row = $('.adm-h1row'); if(!row) return;
  let box = $('#adm-sol-tools');
  if(!box){
    box = document.createElement('div');
    box.id = 'adm-sol-tools';
    const crear = $('#adm-crear-turno');
    if(crear) crear.before(box); else row.appendChild(box);
  }
  const gearHtml = COLA_GEAR_API && listadas ? COLA_GEAR_API.gearHtml() : '';
  // Grouping by suggestion only when there are two suggestions to group by.
  const sugerencias = new Set(ALL.filter(s=>s.estado==='pendiente').filter(matchesFilters).map(suggestFor)).size;
  const agruparHtml = FILTER==='pendiente' && pendientes && sugerencias > 1
    ? '<button type="button" class="btn" id="adm-agrupar" aria-pressed="'+(COLA_AGRUPA?'true':'false')+'">'
      + esc(T("admin.queue.solicitudes.agrupar_por_sugerencia", null, "Agrupar por sugerencia")) + '</button>'
    : '';
  // No counter beside the title (the product owner, 2026-10-14: «never make
  // counters»; one screen header 2026-11-08): the rows say how many there are.
  box.innerHTML = '<span class="adm-sol-tools-r">' + gearHtml + agruparHtml + '</span>';
  const g = $('#adm-agrupar');
  if(g){ g.classList.toggle('on', COLA_AGRUPA); g.setAttribute('aria-pressed', COLA_AGRUPA ? 'true' : 'false'); }
}

function sugerenciaDeCard(card){
  const s = ALL.find(x=>String(x.id)===String(card.dataset.id));
  return s ? suggestFor(s) : 'none';
}

// Antes esto devolvía elementos del DOM porque cada solicitud tenía su
// tarjeta. Ahora sólo la abierta está dibujada, así que el lote trabaja sobre
// los DATOS y usa el DOM únicamente para la que está a la vista (si el
// operador le cambió el médico o el motivo, eso manda).
function selectedPending(){
  return [...COLA_SEL].map(id=>ALL.find(x=>String(x.id)===String(id))).filter(Boolean);
}

function cardDe(s){
  const c = document.querySelector(`#list .card[data-id="${s.id}"]`);
  return c || null;
}

function syncBulkSelection(){ renderCola(); }

// Progressive disclosure (the product owner, 2026-10-26, on an empty Solicitudes with the
// AI notice, seven filters and a table header over «No hay solicitudes
// pendientes.»): the shared rule of admin-filter-controls.mjs, with this
// screen's data.
//   · no request at all → only the title band and what will appear here;
//   · no pending, but history → the same, plus the way to the history;
//   · filters (state included) hid every row → the band stays and the empty
//     state clears them;
//   · rows → everything. The AI notice is not a list caption: it sits under
//     the AI reading of an open request (admin-solicitudes.mjs, 2026-11-09).
function colaDisclosure(listadas){
  const sec = $('#sec-solicitudes');
  const enVista = FILTER==='todas' ? ALL.length
    : ALL.filter(s=>s.estado===FILTER).length;
  let estado = FilterControls.disclosure({
    total: ALL.length, shown: listadas,
    filtered: !!filtrosActivos() || FILTER!=='pendiente'
  });
  if(estado==='data' && FILTER==='pendiente' && !enVista) estado = 'empty';
  FilterControls.applyDisclosure(sec, estado);
  if(estado==='empty'){
    const hayHistoria = ALL.length > 0;
    return {estado, html: FilterControls.emptyStateHtml({
      title: hayHistoria
        ? T("admin.queue.solicitudes.vacio", null, "No hay solicitudes pendientes.")
        : T("admin.queue.solicitudes.empty_title", null, "Todavía no hay solicitudes."),
      text: T("admin.queue.solicitudes.empty_text", null, "Cuando un paciente pide un turno desde el sitio, su solicitud aparece acá."),
      action: hayHistoria
        ? {label: T("admin.queue.solicitudes.ver_resueltas", null, "Ver aprobadas y rechazadas"), attrs: {'data-sol-ver-todas': ''}}
        : null
    })};
  }
  if(estado==='no-match'){
    return {estado, html: FilterControls.noMatchHtml({
      title: filtrosActivos()
        ? T("admin.queue.solicitudes.ninguna_coincide_busqueda", null, "Ninguna solicitud coincide con la búsqueda.")
        : T("admin.queue.solicitudes.no_hay_solicitudes_aca", null, "No hay solicitudes acá."),
      clearLabel: T("admin.shell.list.clear_filters", null, "Limpiar filtros")
    })};
  }
  return {estado, html: ''};
}

// A filter shows only when the list is worth filtering (FILTER_MIN_ROWS
// requests, the shared threshold) and the data offers two values to choose
// between (one doctor → no doctor filter); a filter already set stays, so it
// can be cleared. The state select is the way to the history, not a filter
// of a long list: it stays whenever there are two states. With no filter
// left, the phone's «Filtros» toggle goes too.
function podarFiltros(){
  if(!FilterControls) return;
  const filas = ALL.length;
  ['f-medico','f-especialidad','f-servicio','f-centro','f-verdict']
    .forEach(id=>FilterControls.showIfChoice($('#'+id), null, '', filas));
  FilterControls.showIfChoice($('#f-sug'),
    new Set(ALL.filter(s=>s.estado==='pendiente').map(suggestFor)).size, '', filas);
  FilterControls.showIfChoice($('#f-estado'), new Set(ALL.map(s=>s.estado)).size, 'pendiente');
  const quedan = [...document.querySelectorAll('#adm-filter-fields .adm-sel')].some(s=>!s.hidden);
  const toggle = $('#adm-filter-toggle');
  if(toggle) toggle.hidden = !quedan;
}

function emptyMsg(where){
  if(filtrosActivos()) return T("admin.queue.solicitudes.ninguna_coincide_busqueda", null, "Ninguna solicitud coincide con la búsqueda.");
  if(where==='pendientes') return T("admin.queue.solicitudes.vacio", null, "No hay solicitudes pendientes.");
  return T("admin.queue.solicitudes.no_hay_solicitudes_aca", null, "No hay solicitudes acá.");
}

function approveError(j){
  const mapa = {
    turno_medico_tomado: T("admin.queue.solicitudes.approve_error.turno_medico_tomado", null, "Ese médico ya tiene un turno a esa hora. Marcá sobreturno solo si fue autorizado."),
    sobreturno_requiere_turno_previo: T("admin.queue.solicitudes.approve_error.sobreturno_requiere_turno_previo", null, "Un sobreturno requiere un turno ya confirmado a esa hora."),
    sobreturno_no_configurado: T("admin.queue.solicitudes.approve_error.sobreturno_no_configurado", null, "Ese horario no tiene sobreturnos configurados."),
    sobreturno_cupo_agotado: T("admin.queue.solicitudes.approve_error.sobreturno_cupo_agotado", null, "Se agotó el cupo de sobreturnos de esta agenda."),
    sobreturno_fuera_de_agenda: T("admin.queue.solicitudes.approve_error.fuera_de_agenda", null, "La hora elegida no pertenece a la agenda de ese médico."),
    sobreturno_horario_cerrado: T("admin.queue.solicitudes.approve_error.horario_cerrado", null, "Ese día está cerrado en la agenda del médico."),
    sobreturno_horario_bloqueado: T("admin.queue.solicitudes.approve_error.horario_bloqueado", null, "Ese horario está bloqueado en la agenda del médico."),
    sobreturno_medico_sin_agenda: T("admin.queue.solicitudes.approve_error.sobreturno_medico_sin_agenda", null, "El médico elegido no tiene una agenda verificable para sobreturnos."),
    turno_medico_fuera_de_agenda: T("admin.queue.solicitudes.approve_error.fuera_de_agenda", null, "La hora elegida no pertenece a la agenda de ese médico."),
    turno_medico_horario_cerrado: T("admin.queue.solicitudes.approve_error.horario_cerrado", null, "Ese día está cerrado en la agenda del médico."),
    turno_medico_horario_bloqueado: T("admin.queue.solicitudes.approve_error.horario_bloqueado", null, "Ese horario está bloqueado en la agenda del médico."),
    turno_medico_sin_agenda: T("admin.queue.solicitudes.approve_error.turno_medico_sin_agenda", null, "El médico elegido no tiene una agenda verificable."),
    turno_tomado: T("admin.queue.solicitudes.approve_error.turno_tomado", null, "Ese horario ya está tomado. Elegí otro."),
    sobreturno_no_habilitado: T("admin.queue.solicitudes.approve_error.sobreturno_no_habilitado", null, "Esa agenda no habilita sobreturnos."),
    medico_no_atiende_servicio: T("admin.queue.solicitudes.approve_error.medico_no_atiende_servicio", null, "Ese médico no atiende este servicio. Elegí uno de la lista o dejá el médico «a asignar».")
  }[j.error];
  // Un código crudo del server ("sobreturno_no_habilitado") NO es un mensaje:
  // el server manda o un código que este mapa traduce, o ya una frase (2026-09-16).
  return mapa || errText(j, T("admin.queue.solicitudes.no_se_pudo_aprobar", null, "No se pudo aprobar"));
}

// Lo que se mandaría si nadie tocara un control: exactamente los valores que
// detalleVM() pre-elige. Con una sola solicitud dibujada a la vez, el lote no
// puede leerlos del DOM — los recalcula del dato, que es de donde salían.
function defaultsDe(s){
  const svc = s.servicio_sugerido ? s.servicio_sugerido.id : s.servicio_id;
  let medico = '';
  if(!s.sin_medico && !esLab(svc)){
    medico = medicoPorDefecto(s, medicosDe(svc), '');
  }
  return {servicio_id: svc, medico, turno: turnoDefault(s), motivo: defaultMotivo(s)};
}

// `ref` puede ser la tarjeta abierta (el operador eligió médico/horario/motivo
// a mano) o la solicitud cruda (el lote). Un único camino de ejecución para
// los dos, así aprobar de a una y aprobar en lote no pueden divergir.
// Turno de una fila de un lote que no está abierta en pantalla (auditoría de formularios,
// decidido 2026-10-25): el horario que eligió el paciente manda; si no eligió, la sugerencia
// libre que el lote todavía no tomó; si no, la PRÓXIMA hora libre del servicio. Antes todas
// caían en el mismo «próximo día hábil 09:00» y desde la segunda el server respondía 409.
async function turnoDeLote(s, usados){
  const def = defaultsDe(s);
  if(s.turno_pedido) return def.turno;
  const sugerido = s.medico_sugerido && s.medico_sugerido.slot_iso ? def.turno : '';
  if(sugerido && !usados.has(sugerido)) return sugerido;
  // SLOTS_CACHE se vacía después de cada aprobación: lo que vuelve ya descuenta lo tomado.
  const libres = (await slotsPara(def.servicio_id, def.medico))
    .filter(t=>!usados.has(`${t.dia}T${t.hora}`));
  return libres.length ? turnoPreferido(libres, prefGeneral(s.preferencia_horario)) : def.turno;
}

// La fila abierta manda con el horario que tiene en pantalla; la cerrada, el del lote.
async function turnoDelRef(ref, s, usados){
  if(ref && ref.nodeType===1){
    const el=ref.querySelector('.turno');
    return el ? el.value : '';
  }
  return turnoDeLote(s, usados);
}

// Aprueba una fila de un lote. Si el horario elegido ya está tomado (la agenda del
// servicio puede ofrecer una hora que el médico ya tiene ocupada), lo marca y prueba
// la siguiente hora libre; una fila abierta conserva la hora que eligió la mesa.
const TURNO_TOMADO = new Set(['turno_tomado', 'turno_medico_tomado']);
async function aprobarEnLote(ref, s, usados){
  let result = null;
  for(let intento=0; intento<8; intento++){
    const turno=await turnoDelRef(ref, s, usados);
    result=await executePending(ref, 'aprobar', undefined, turno);
    if(result.ok){ if(turno) usados.add(turno); return result; }
    if(!turno || !TURNO_TOMADO.has(result.code) || (ref && ref.nodeType===1) || usados.has(turno)) return result;
    usados.add(turno);
  }
  return result;
}

async function executePending(ref, action, rejectReason, turnoLote){
  const card = ref && ref.nodeType===1 ? ref : null;
  const s = card ? ALL.find(x=>String(x.id)===String(card.dataset.id)) : ref;
  const id = card ? card.dataset.id : s.id;
  const def = s ? defaultsDe(s) : {};
  // Un control DIBUJADO manda aunque esté vacío: vaciarlo es una decisión del
  // operador (derivar a otro servicio deja el médico sin elegir), no un hueco
  // que se rellene con el dato viejo de la fila (journey 2026-09-16: aprobaba
  // con el médico del servicio anterior).
  const leer = (sel, fallback)=>{
    const el = card && card.querySelector(sel);
    return el ? el.value : fallback;
  };
  if(action==='aprobar'){
    // El horario de una tarjeta cuya agenda todavía no llegó es el que propuso
    // el server, no el del médico: no sale (ni desde el botón ni desde un lote).
    const agenda = card && card.querySelector('.turno-sel[data-agenda]');
    if(agenda) return {ok:false, message: T("admin.queue.solicitudes.agenda_cargando_espera", null, "Cargando los horarios del médico. Esperá un momento.")};
    const turno=leer('.turno', turnoLote || def.turno);
    if(!turno) return {ok:false,message:T("admin.queue.solicitudes.elegi_fecha_hora_turno", null, "Elegí fecha y hora del turno.")};
    const body={turno_dt:toIsoWithOffset(turno)};
    const svcVal=leer('.svc-sel', def.servicio_id); if(svcVal) body.servicio_id=svcVal;
    const medVal=leer('.med-sel', def.medico); if(medVal) body.medico=medVal;
    const sobreturno=card&&card.querySelector('.sobreturno'); if(sobreturno&&sobreturno.checked) body.sobreturno=true;
    const res=await api(`/api/admin/requests/${id}/approve`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await res.json().catch(()=>({}));
    if(!res.ok) return {ok:false,message:approveError(j),code:j.error};
    if(s && j.solicitud) Object.assign(s, j.solicitud);
    recordarAprobada(s || j.solicitud);
    SLOTS_CACHE.clear();
    const hsi=j.solicitud&&j.solicitud.hsi_estado;
    const tipo=j.solicitud&&j.solicitud.sobreturno ? T("admin.queue.solicitudes.tipo_sobreturno_autorizado", null, " como sobreturno autorizado") : '';
    const msg = !EMR_SYNC_ACTIVE ? T("admin.queue.solicitudes.aprobada", { tipo: tipo }, "Aprobada{tipo}.")
      : hsi==='ok' ? T("admin.queue.solicitudes.aprobada_hsi_ok", { tipo: tipo }, "Aprobada{tipo} y enviada a HSI.")
      : hsi==='skip' ? T("admin.queue.solicitudes.aprobada_hsi_skip", { tipo: tipo }, "Aprobada{tipo} (HSI omitido).")
      : hsi==='error' ? T("admin.queue.solicitudes.aprobada_hsi_error", { tipo: tipo }, "Aprobada{tipo} — HSI falló, queda pendiente.")
      : T("admin.queue.solicitudes.aprobada_hsi_pendiente", { tipo: tipo }, "Aprobada{tipo} — HSI pendiente.");
    return {ok:true,message:msg};
  }
  const sel=card&&card.querySelector('.motivo-sel');
  const motivo=rejectReason || (
    sel ? ((sel.value==='__otro') ? card.querySelector('.motivo').value.trim() : (sel.value||'').trim())
        : String(def.motivo||'').trim());
  if(!motivo) return {ok:false,message:T("admin.queue.solicitudes.elegi_motivo_rechazo", null, "Elegí o escribí el motivo del rechazo.")};
  const res=await api(`/api/admin/requests/${id}/reject`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({motivo})});
  const j=await res.json().catch(()=>({}));
  if(res.ok){
    if(s && j.solicitud) Object.assign(s, j.solicitud);
    SLOTS_CACHE.clear();
  }
  return res.ok ? {ok:true,message:T("admin.queue.solicitudes.solicitud_rechazada", null, "Solicitud rechazada.")} : {ok:false,message:errText(j, T("admin.queue.solicitudes.no_se_pudo_rechazar", null, "No se pudo rechazar"))};
}

// La abierta manda sobre su propio dato: si el operador le cambió el médico o
// el motivo y además la tildó, el lote usa lo que está en pantalla.
function refPara(s){ return cardDe(s) || s; }

function nAprobadaTxt(n){
  return Tn("admin.queue.solicitudes.n_aprobada", n, null, "{n} aprobada", "{n} aprobadas");
}
function nRechazadaTxt(n){
  return Tn("admin.queue.solicitudes.n_rechazada", n, null, "{n} rechazada", "{n} rechazadas");
}

async function runBulk(action, rejectReason){
  const items=selectedPending();
  if(!items.length){ toast(T("admin.queue.bulk_reject.err_none_selected", null, "Seleccioná al menos una solicitud.")); return; }
  document.querySelectorAll('[data-bulk-action],[data-sol-grupo]').forEach(b=>b.disabled=true);
  let done=0, failed=[], demo=false;
  const usados=new Set();   // horarios que este lote ya dio
  for(const s of items){
    try{
      const ref=refPara(s);
      const result=action==='aprobar' ? await aprobarEnLote(ref, s, usados) : await executePending(ref,action,rejectReason);
      if(result.ok) done++; else failed.push(result.message);
    }catch(err){
      // La demo ya avisó una vez (api()); seguir el lote repetía su frase por fila.
      if(String(err.message)==='demo_solo_lectura'){ demo=true; break; }
      if(String(err.message)!=='401') failed.push(errText(err, T("admin.queue.solicitudes.error_de_red", null, "Error de red.")));
    }
  }
  COLA_SEL.clear();
  await load();
  if(demo && !done) return;
  const base = action==='aprobar' ? nAprobadaTxt(done) : nRechazadaTxt(done);
  toast(failed.length
    ? T("admin.queue.solicitudes.bulk_resultado_con_fallas", { base: base, n: failed.length, primero: failed[0] }, "{base} · {n} sin procesar: {primero}")
    : T("admin.queue.solicitudes.bulk_resultado_ok", { base: base }, "{base}."));
}

async function runBulkSugerencia(){
  const items=selectedPending().filter(s=>suggestFor(s)!=='none');
  if(!items.length){ toast(T("admin.queue.solicitudes.ninguna_con_sugerencia", null, "Ninguna de las seleccionadas tiene sugerencia.")); return; }
  document.querySelectorAll('[data-bulk-action],[data-sol-grupo]').forEach(b=>b.disabled=true);
  let ap=0, re=0, failed=[], demo=false;
  const usados=new Set();   // horarios que este lote ya dio
  for(const s of items){
    const action=suggestFor(s);
    try{
      // rechazo: executePending toma el motivo pre-elegido de la solicitud
      const ref=refPara(s);
      const result=action==='aprobar' ? await aprobarEnLote(ref, s, usados) : await executePending(ref,action);
      if(result.ok){ if(action==='aprobar') ap++; else re++; }
      else failed.push(result.message);
    }catch(err){
      if(String(err.message)==='demo_solo_lectura'){ demo=true; break; }
      if(String(err.message)!=='401') failed.push(errText(err, T("admin.queue.solicitudes.error_de_red", null, "Error de red.")));
    }
  }
  COLA_SEL.clear();
  await load();
  if(demo && !ap && !re) return;
  const base = T("admin.queue.solicitudes.bulk_sugerencia_resultado", { aprobadas: nAprobadaTxt(ap), rechazadas: nRechazadaTxt(re) }, "{aprobadas} · {rechazadas}");
  toast(failed.length
    ? T("admin.queue.solicitudes.bulk_resultado_con_fallas", { base: base, n: failed.length, primero: failed[0] }, "{base} · {n} sin procesar: {primero}")
    : T("admin.queue.solicitudes.bulk_resultado_ok", { base: base }, "{base}."));
}

function openBulkReject(){
  const items=selectedPending(), dialog=$('#bulk-reject-dialog'), select=$('#bulk-reject-reason'), other=$('#bulk-reject-other');
  if(!items.length){ toast(T("admin.queue.bulk_reject.err_none_selected", null, "Seleccioná al menos una solicitud.")); return; }
  // lote: puede mezclar solicitudes con y sin orden → lista completa
  select.innerHTML='<option value="">'+esc(T("admin.queue.bulk_reject.elegir_motivo", null, "Elegí un motivo…"))+'</option>'+MOTIVOS_ORDEN.concat(MOTIVOS_GENERAL).map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('')+'<option value="__otro">'+esc(T("admin.queue.bulk_reject.otro_motivo", null, "Otro motivo…"))+'</option>';
  other.value=''; other.hidden=true;
  $('#bulk-reject-count').textContent=Tn("admin.queue.bulk_reject.aplicara", items.length, null, "Se aplicará a {n} solicitud.", "Se aplicará a {n} solicitudes.");
  dialog.showModal();
}

// --- acciones ---
document.addEventListener('click', async (e)=>{
  // ---- la cola: abrir una solicitud, tildarla, ordenar, traer más ----
  const ck=e.target.closest('.sol-ck');
  if(ck){
    // Tildar no abre: son dos gestos distintos sobre la misma fila.
    e.stopPropagation();
    const id=String(ck.dataset.pendingSelect);
    // El rango se calcula ANTES de redibujar, contra las casillas tal como
    // están en pantalla: el DOM es el único lugar donde vive el orden real.
    const ids=[...document.querySelectorAll('#list .sol-ck')].map(x=>String(x.dataset.pendingSelect));
    const desde=ids.indexOf(COLA_ANCLA), hasta=ids.indexOf(id);
    if(e.shiftKey && COLA_ANCLA && COLA_ANCLA!==id && desde>=0 && hasta>=0){
      const lo=Math.min(desde,hasta), hi=Math.max(desde,hasta);
      // Todo el rango toma el estado de la casilla recién clickeada, así el
      // mismo gesto sirve para tildar de a muchas y para destildarlas.
      for(let i=lo;i<=hi;i++){ if(ck.checked) COLA_SEL.add(ids[i]); else COLA_SEL.delete(ids[i]); }
    } else if(ck.checked) COLA_SEL.add(id); else COLA_SEL.delete(id);
    COLA_ANCLA = id;
    renderCola();
    return;
  }
  // The HSI control inside a row is its own gesture: it never opens the row.
  const fila=e.target.closest('[data-hsi-retry],[data-hr-hsi]') ? null : e.target.closest('[data-sol-row]');
  if(fila){
    const id=String(fila.dataset.solRow);
    SEL_ID = String(SEL_ID)===id ? null : id;   // volver a clickear la cierra
    renderCola();
    return;
  }
  const ordenar=e.target.closest('[data-sol-sort]');
  if(ordenar){
    const k=ordenar.dataset.solSort;
    // Pendientes tiene su propio estado de orden (COLA_SORT); las otras tres
    // pestañas comparten RES_SORT — es la misma lista de columnas ahí, tiene
    // sentido que el criterio elegido en Aprobadas siga sirviendo en Todas.
    if(FILTER==='pendiente'){
      COLA_SORT = COLA_SORT.key===k ? {key:k, dir:-COLA_SORT.dir} : {key:k, dir: k==='cuando' ? -1 : 1};
    } else {
      RES_SORT = RES_SORT.key===k ? {key:k, dir:-RES_SORT.dir} : {key:k, dir: k==='turno' ? -1 : 1};
    }
    renderCola();
    return;
  }
  if(e.target.closest('[data-sol-mas]')){
    if(FILTER==='pendiente') COLA_LIMITE += COLA_LOTE; else COLA_LIMITE_RES += COLA_LOTE;
    renderCola();
    return;
  }
  const grpMas=e.target.closest('[data-sol-grp-mas]');
  if(grpMas){
    e.stopPropagation();
    const k=grpMas.dataset.solGrpMas;
    COLA_GRUPO_LIM[k] = (COLA_GRUPO_LIM[k] || COLA_GRUPO_BASE) + COLA_GRUPO_LOTE;
    renderCola();
    return;
  }
  if(e.target.closest('[data-sol-cerrar]')){ SEL_ID=null; renderCola(); return; }
  // Plegar/desplegar un grupo de resueltas dentro de «Todas» (responsable del producto,
  // 2026-08-06: chevron en el encabezado, la cuenta queda visible siempre).
  const grpToggle=e.target.closest('[data-sol-grp-toggle]');
  if(grpToggle){
    e.stopPropagation();
    const k=grpToggle.dataset.solGrpToggle;
    COLA_RES_COLAPSO[k] = !COLA_RES_COLAPSO[k];
    renderCola();
    return;
  }
  const agrupar=e.target.closest('#adm-agrupar');
  if(agrupar){ COLA_AGRUPA = !COLA_AGRUPA; render(); return; }
  // The empty state's own way out: no pending, but there is history.
  if(e.target.closest('[data-sol-ver-todas]')){
    FILTER = 'todas'; $('#f-estado').value = 'todas'; SEL_ID = null; saveRequestPrefs(); render(); return;
  }
  if(e.target.closest('#list [data-clear-filters]')){ limpiarFiltros(true); return; }

  // Despachar un grupo entero desde su encabezado. Reúne las mismas filas que
  // el grupo muestra y las resuelve por su propia sugerencia (un rechazo se
  // lleva su motivo pre-elegido), que es exactamente lo que hace «Resolver
  // según sugerencia» con lo tildado a mano.
  const grupoBulk=e.target.closest('[data-sol-grupo]');
  if(grupoBulk){
    e.stopPropagation();
    // The whole group, shown rows or not; a rejection group is one reason.
    const sug=grupoBulk.dataset.solGrupo, motivo=grupoBulk.dataset.solMotivo||'';
    const items=ALL.filter(s=>s.estado==='pendiente').filter(matchesFilters)
      .filter(s=>suggestFor(s)===sug).filter(s=>!motivo || lecturaClave(s)===motivo);
    if(!items.length) return;
    // Un click resuelve decenas de solicitudes de pacientes reales: acá sí se
    // pregunta, aunque la barra de selección no lo haga (ahí cada fila se
    // tildó a mano, que ya es la confirmación).
    // A rejection group is one reason: the question quotes the very text the
    // patients will read, so the operator approves the words, not just a count.
    const motivos = sug==='rechazar' ? [...new Set(items.map(defaultMotivo))] : [];
    const pregunta = sug==='aprobar'
      ? Tn("admin.queue.solicitudes.confirmar_grupo_aprobar", items.length, null,
          "¿Aprobar la solicitud que la IA sugiere aprobar?",
          "¿Aprobar las {n} solicitudes que la IA sugiere aprobar?")
      : motivos.length===1 && motivos[0]
      ? Tn("admin.queue.solicitudes.confirmar_grupo_rechazar_motivo", items.length, {motivo: motivos[0]},
          "¿Rechazar la solicitud? El paciente recibe por email: «{motivo}»",
          "¿Rechazar las {n} solicitudes? Cada paciente recibe por email: «{motivo}»")
      : Tn("admin.queue.solicitudes.confirmar_grupo_rechazar", items.length, null,
          "¿Rechazar la solicitud que la IA sugiere rechazar? Se le avisa al paciente por email.",
          "¿Rechazar las {n} solicitudes que la IA sugiere rechazar? Se le avisa a cada paciente por email.");
    if(!confirm(pregunta)) return;
    COLA_SEL.clear();
    items.forEach(s=>COLA_SEL.add(String(s.id)));
    await runBulkSugerencia();
    return;
  }

  const closeBulk=e.target.closest('[data-close-bulk-reject]');
  if(closeBulk){ $('#bulk-reject-dialog').close(); return; }
  const serviceToggle=e.target.closest('[data-toggle-service]');
  if(serviceToggle){
    const card=serviceToggle.closest('.card'), edit=card.querySelector('.svc-edit');
    edit.hidden=!edit.hidden;
    serviceToggle.setAttribute('aria-expanded', String(!edit.hidden));
    if(!edit.hidden) edit.querySelector('.cmb-in').focus();
    return;
  }
  const bulk=e.target.closest('[data-bulk-action]');
  if(bulk){
    if(bulk.dataset.bulkAction==='rechazar') openBulkReject();
    else if(bulk.dataset.bulkAction==='sugerencia') await runBulkSugerencia();
    else await runBulk('aprobar');
    return;
  }
  const bulkConfirm=e.target.closest('#bulk-reject-confirm');
  if(bulkConfirm){
    const select=$('#bulk-reject-reason'), reason=select.value==='__otro'?$('#bulk-reject-other').value.trim():select.value;
    if(!reason){ toast(T("admin.queue.solicitudes.elegi_motivo_rechazo", null, "Elegí o escribí el motivo del rechazo.")); return; }
    $('#bulk-reject-dialog').close(); await runBulk('rechazar',reason); return;
  }
  const closeAttendance=e.target.closest('[data-close-attendance]');
  if(closeAttendance){ $('#attendance-dialog').close(); return; }
  const attendanceDetail=e.target.closest('[data-attendance-detail]');
  if(attendanceDetail){
    const row=ALL.find(s=>String(s.id)===String(attendanceDetail.dataset.attendanceDetail));
    if(row) openAttendanceDetail(row);
    return;
  }
  const conflictRequest=e.target.closest('[data-hsi-request]');
  if(conflictRequest){
    e.preventDefault();
    const sid=conflictRequest.dataset.hsiRequest;
    const row=ALL.find(s=>String(s.id)===String(sid));
    if(!row) return;
    FILTER=row.estado==='pendiente'?'pendiente':row.estado==='rechazada'?'rechazada':'aprobada';
    PAGE=1;
    // La solicitud se ABRE en el panel, que es donde ahora se lee — pendiente
    // o resuelta, las dos tienen panel desde este rediseño.
    SEL_ID=String(sid);
    if(FILTER==='pendiente') COLA_LIMITE=Math.max(COLA_LIMITE, COLA_LOTE);
    else COLA_LIMITE_RES=Math.max(COLA_LIMITE_RES, COLA_LOTE);
    const fEstado=$('#f-estado'); if(fEstado) fEstado.value=FILTER;
    render();
    requestAnimationFrame(()=>{
      const target=document.querySelector(`#list [data-sol-row="${sid}"]`)
        || document.querySelector(`#list [data-id="${sid}"]`);
      if(target) target.scrollIntoView({behavior:'smooth',block:'center'});
    });
    return;
  }
  const attendance = e.target.closest('[data-attendance]');
  if(attendance){
    const sid=attendance.dataset.attendance;
    // `sid` comes from the numeric server id, so this direct attribute lookup
    // is safe and also works in older operator browsers without CSS.escape.
    const select=document.querySelector(`[data-attendance-select="${sid}"]`);
    const asistencia=select && select.value;
    if(!asistencia){ toast(T("admin.queue.solicitudes.elegi_asistio_o_no", null, "Elegí si asistió o no asistió.")); if(select) select.focus(); return; }
    attendance.disabled=true;
    try{
      const res=await api(`/api/admin/requests/${sid}/attendance`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({asistencia})
      });
      const j=await res.json().catch(()=>({}));
      if(!res.ok){
        const msg={asistencia_no_elegible: T("admin.queue.solicitudes.asistencia_no_elegible", null, "La asistencia sólo se marca en turnos aprobados que ya pasaron.")}[j.error]
          || errText(j, T("admin.queue.attendance.save_error_specific", null, "No se pudo guardar la asistencia."));
        toast(msg); attendance.disabled=false; return;
      }
      const base=asistencia==='ausente' ? T("admin.queue.solicitudes.inasistencia_registrada", null, "Inasistencia registrada.") : T("admin.queue.solicitudes.visita_registrada", null, "Visita registrada.");
      toast(EMR_SYNC_ACTIVE && j.hsi_sync==='sin_turno_hsi_vinculado'
        ? base+' '+T("admin.queue.solicitudes.sin_turno_hsi_vinculado", null, "No hay turno HSI vinculado para sincronizar.") : base);
      await load();
    }catch(err){ if(!yaAvisado(err)) toast(T("admin.queue.solicitudes.error_de_red", null, "Error de red.")); if(String(err.message)!=='401') attendance.disabled=false; }
    return;
  }
  const retry = e.target.closest('[data-hsi-retry]');
  if(retry){
    e.stopPropagation();
    if(!EMR_SYNC_ACTIVE) return;
    // The same control turns while HSI answers, then shows the check or
    // the reason it failed next to it (hsi-sync.mjs).
    const pintar = (estado, detalle) => HsiSync ? HsiSync.pintarServidor(retry, estado, detalle) : (retry.disabled = estado === 'enviando');
    pintar('enviando');
    try{
      const res = await api(`/api/admin/requests/${retry.dataset.hsiRetry}/retry-hsi`, {method:'POST'});
      const j = await res.json().catch(()=>({}));
      if(!res.ok){
        // detalle = la causa real que clasificó el server (HSI caído vs.
        // agenda inexistente vs. horario ocupado) — cada una se resuelve
        // distinto, el toast genérico las escondía todas.
        const msg = j.detalle
          || {hsi_desactivado: T("admin.queue.solicitudes.hsi_no_envia_turnos", null, "Este entorno no envía turnos a HSI."),
              turno_pasado: T("admin.queue.solicitudes.turno_ya_paso", null, "El turno ya pasó: HSI no agenda turnos pasados.")}[j.error]
          || errText(j, T("admin.queue.solicitudes.hsi_no_confirmo_turno", null, "HSI no confirmó el turno."));
        pintar('error', msg); toast(msg); return;
      }
      pintar('guardado');
      toast(T("admin.queue.solicitudes.turno_confirmado_hsi", null, "Turno confirmado en HSI."));
      await load();
    }catch(err){ if(!yaAvisado(err)) toast(T("admin.queue.solicitudes.error_de_red", null, "Error de red.")); if(String(err.message)!=='401') pintar('error', T("admin.queue.solicitudes.error_de_red", null, "Error de red.")); }
    return;
  }
  // The desk's extension takes a click on its hook before the panel ever
  // sees it; one that arrives here means it is not on this computer: the DNI
  // goes to the clipboard; the line under the control says where to install it.
  const sinExt = e.target.closest('[data-hr-hsi]');
  if(sinExt){
    e.stopPropagation();
    const s = ALL.find(x => String(x.id) === String(sinExt.dataset.hrHsi));
    if(HsiSync) HsiSync.sinExtension(sinExt, {dni: s && s.dni, tienda: HSI_INSTALAR ? null : HSI_EXTENSION});
    return;
  }
  // «Ver conversación» de «Su caso»: lectura registrada (casoVer).
  const casoBtn = e.target.closest('[data-caso-ver]');
  if(casoBtn){
    e.preventDefault();
    await casoVer(casoBtn);
    return;
  }
  // Arreglo de un click: elige el motivo estándar y rechaza en el mismo gesto.
  // Pasa por el botón real de Rechazar para reusar toda su lógica (toast,
  // recarga, errores) — el atajo cambia cuántos clicks hacen falta, no qué pasa.
  const fix = e.target.closest('[data-fix]');
  if(fix){
    e.preventDefault();
    const card = fix.closest('.card');
    const sel = card && card.querySelector('.motivo-sel');
    if(sel){
      sel.value = fix.dataset.fix;
      const otro = card.querySelector('.motivo-otro');
      if(otro) otro.style.display = 'none';
    }
    const rej = card && card.querySelector('[data-act="rechazar"]');
    if(rej) rej.click();
    return;
  }
  const btn = e.target.closest('[data-act]');
  if(btn){
    const card = btn.closest('.card');
    btn.disabled = true;
    try{
      const result=await executePending(card,btn.dataset.act);
      // El horario se ocupó mientras el operador miraba (otro operador, el
      // paciente): no un código, la agenda al día y qué hacer (remedies).
      const ocupado = !result.ok && btn.dataset.act==='aprobar' && TURNO_TOMADO.has(result.code||'');
      if(ocupado){
        result.message = result.code==='turno_tomado'
          ? T("admin.queue.solicitudes.turno_ocupado_agenda", null, "Ese horario se ocupó mientras tanto. Actualizamos la agenda: elegí otro horario.")
          : result.message + ' ' + T("admin.queue.solicitudes.o_elegi_otro_horario", null, "O elegí otro horario de la agenda actualizada.");
      }
      if(result.ok) toast(result.message);
      // El error vive JUNTO a la acción que lo produjo, no sólo en el toast de
      // arriba: en un panel largo el toast queda fuera de pantalla (journey
      // 2026-09-16: un 409 al aprobar no mostraba nada). La fila no cambia.
      const fila = btn.closest('.sol-res-row') || card;
      let err = fila && fila.querySelector('.sol-act-err');
      if(!result.ok && fila){
        if(!err){
          err = document.createElement('p');
          err.className = 'sol-act-err';
          err.setAttribute('role','alert');
          fila.appendChild(err);
        }
        err.textContent = result.message;
      }else if(err){ err.remove(); }
      // el control de sobreturno vive ESCONDIDO hasta que hace falta: recién
      // cuando el server rechaza por choque de turno aparece el checkbox
      // (mismo criterio wizard-design: el control del error, junto al error).
      if(!result.ok && /^(turno_medico_tomado|sobreturno_)/.test(result.code||'')){
        const ot = card.querySelector('.overturn-ctl');
        if(ot) ot.hidden = false;
      }
      if(result.ok) await load(); else btn.disabled=false;
      if(ocupado){ SLOTS_CACHE.clear(); poblarTurnoSel(card, {tras409: result.code}); }
    }catch(err){ if(!yaAvisado(err)) toast(T("admin.queue.solicitudes.error_de_red", null, "Error de red.")); if(String(err.message)!=='401') btn.disabled=false; }
    return;
  }
  const rf = e.target.closest('[data-refresh]');
  if(rf){
    e.preventDefault();
    const sid = rf.dataset.refresh, mat = rf.dataset.mat;
    const s = ALL.find(x=>String(x.id)===String(sid));
    const c = (s && s.ai_json && s.ai_json.campos) || {};
    verificarMatricula(sid, mat, (c.medico && c.medico.valor) || '', true);
    return;
  }
  const rs = e.target.closest('[data-res-sort]');
  if(rs){
    const k = rs.dataset.resSort;
    if(RES_SORT.key===k) RES_SORT.dir*=-1; else RES_SORT={key:k, dir:1};
    render();
    return;
  }
  // pager: número de página / Siguiente / Última
  const pg = e.target.closest('[data-pg]');
  if(pg){ e.preventDefault(); PAGE = parseInt(pg.dataset.pg, 10) || 1; render(); return; }
});

// Gate form wiring (#gate-form, #gate-passkey) extracted to static/platform/admin-login.mjs.
// The wiring uses node-present guards so the legacy shell still binds, while
// server-routed screens (which don't serve the gate DOM) skip cleanly.

// Atajos de la cola. A/R = aprobar/rechazar LA SOLICITUD ABIERTA (antes, la
// primera de la página: con el split ya no hay "primera tarjeta", hay una
// abierta). ↑/↓ mueven de fila y la abren; Escape cierra el panel. Se ignoran
// con el foco escribiendo o con una tecla modificadora apretada.
document.addEventListener('keydown', e=>{
  if(e.metaKey||e.ctrlKey||e.altKey||e.shiftKey) return;
  const tag = (e.target && e.target.tagName || '').toLowerCase();
  if(tag==='input'||tag==='select'||tag==='textarea'||(e.target&&e.target.isContentEditable)) return;
  if(e.key==='Escape' && SEL_ID){ e.preventDefault(); SEL_ID=null; renderCola(); return; }
  if(e.key==='ArrowDown' || e.key==='ArrowUp'){
    const filas=[...document.querySelectorAll('#list [data-sol-row]')];
    if(!filas.length) return;
    e.preventDefault();
    const i=filas.findIndex(f=>String(f.dataset.solRow)===String(SEL_ID));
    const next=e.key==='ArrowDown' ? Math.min(filas.length-1, i+1) : Math.max(0, i<0?0:i-1);
    SEL_ID=String(filas[next].dataset.solRow);
    renderCola();
    const abierta=document.querySelector('#list .sol-row-on');
    if(abierta) abierta.scrollIntoView({block:'nearest'});
    return;
  }
  const key = e.key.toLowerCase();
  if(key!=='a' && key!=='r') return;
  const card = document.querySelector('#list .card[data-id]');
  if(!card) return;
  const btn = card.querySelector(`[data-act="${key==='a'?'aprobar':'rechazar'}"]`);
  if(btn){ e.preventDefault(); btn.click(); }
});

// Derivar a otro servicio: la especialidad manda sobre la lista de médicos —
// si no, el operador puede asignar un cardiólogo a una psiquiatría. El médico
// del servicio VIEJO no sobrevive a la derivación (journey 2026-09-16: se
// aprobaba una odontología con el clínico de Medicina general), y el encabezado
// pasa a decir el servicio nuevo. Derivar a un laboratorio esconde el combo de
// médico (no se asigna).
function aplicarDerivacion(card, svc){
  if(!card) return;
  const medHid = card.querySelector('.med-sel');
  const medBox = medHid && medHid.closest('.cmb');
  const medVacio = card.querySelector('.med-vacio');
  if(medVacio){
    // La pista «no hay médicos de…» sigue al servicio que se va a aprobar.
    medVacio.hidden = esLab(svc) || medicosDe(svc).length > 0;
    medVacio.textContent = medVacioTxt(svc);
  }
  if(medBox){
    if(esLab(svc)){
      medBox.style.display = 'none';
      medHid.value = '';
    }else{
      medBox.style.display = '';
      const prev = medHid.value;
      const items = medicosDe(svc).map(x=>{
        const libre = (x.proximo && x.proximo.v) || '';
        return {v:x.nombre, t:x.nombre, s: libre ? T("admin.queue.solicitudes.libre_desde", { cuando: libre }, "— libre {cuando}") : ''};
      });
      medBox.dataset.items = JSON.stringify(items);
      // si el médico ya elegido sigue siendo válido para el nuevo servicio, se respeta
      if(!(prev && items.some(it=>it.v===prev))){
        medHid.value = '';
        const inp = medBox.querySelector('.cmb-in');
        if(inp) inp.value = '';
        medBox.dataset.disp = '';
      }
    }
  }
  const row = ALL.find(x=>String(x.id)===String(card.dataset.id));
  // El encabezado del panel dice el servicio que se va a aprobar, no el pedido.
  const titulo = card.querySelector('.sol-pide-line1 a');
  if(titulo) titulo.textContent = svcNombre(svc);
  const edit=card.querySelector('.svc-edit'), toggle=card.querySelector('[data-toggle-service]');
  // El centro es el del servicio nuevo: al aprobar, la solicitud pasa a él. La
  // línea 2 cambia sólo su centro; el horario y el médico que pidió quedan.
  const cen = ((SVC_LIST.find(x=>x.id===svc)||{}).centro) || (row&&row.centro_slug) || '';
  const centro = card.querySelector('.sol-pide-line2 .sol-pide-centro');
  if(centro && cen) centro.textContent = cenNombre(cen);
  if(edit) edit.hidden=true;
  if(toggle) toggle.setAttribute('aria-expanded','false');
}

// "Otro motivo…" en el select de rechazo → abre el campo de texto libre
document.addEventListener('change', async (e)=>{
  // Cambiar el servicio re-filtra los médicos: la especialidad manda sobre la
  // lista, si no el operador puede asignar un cardiólogo a una psiquiatría.
  // Derivar a un laboratorio esconde el combo de médico (no se asigna).
  const svcSel = e.target.closest('.svc-sel');
  if(svcSel){
    aplicarDerivacion(svcSel.closest('.card'), svcSel.value);
    return;
  }
  if(e.target.closest('#bulk-reject-reason')){
    const other=$('#bulk-reject-other'); other.hidden=e.target.value!=='__otro'; if(!other.hidden) other.focus();
    return;
  }
  const sel = e.target.closest('.motivo-sel');
  if(sel){
    // La fila es `.sol-res-row` en el panel de detalle; `.card` queda de
    // reserva. Antes buscaba `.acts` —la tarjeta vieja, que ya no se
    // renderiza— así que elegir un motivo tiraba TypeError y «Otro motivo…»
    // no destapaba nunca el campo de texto.
    const fila = sel.closest('.sol-res-row') || sel.closest('.card');
    const inp = fila && fila.querySelector('.motivo');
    if(inp){
      const otro = sel.value==='__otro';
      inp.style.display = otro ? '' : 'none';
      if(otro) inp.focus();
    }
    return;
  }
  // por página del pager (pendientes/resueltas tienen su propio tamaño)
  const ppsel = e.target.closest('.pgr-pp-sel');
  if(ppsel){
    ppSet(ppsel.dataset.kind, parseInt(ppsel.value, 10));
    PAGE = 1;
    render();
    return;
  }
  // corrección inline de un campo leído por la IA (handoff 2026-07-17:
  // "make all this data editable, and show the edited states") — al perder
  // el foco con un valor nuevo se persiste server-side y se re-renderiza la
  // cola entera para que el estado "editado" quede visible en todas partes
  // (mismo patrón que aprobar/rechazar: re-render completo tras la acción).
  const inp = e.target.closest('.campo-edit');
  if(inp){
    const sid = inp.dataset.sid, campo = inp.dataset.campo, valor = inp.value.trim();
    inp.disabled = true;
    try{
      const res = await api(`/api/admin/requests/${sid}/campos`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({campo, valor}) });
      const j = await res.json().catch(()=>({}));
      if(!res.ok){ toast(errText(j, T("admin.queue.solicitudes.no_se_pudo_guardar_correccion", null, "No se pudo guardar la corrección."))); inp.disabled=false; return; }
      const s = ALL.find(x=>String(x.id)===String(sid));
      if(s){
        s.ai_json = s.ai_json || {};
        s.ai_json.campos = j.campos || s.ai_json.campos;
      }
      render();
      // matrícula corregida → re-verificar contra REFEPS con el nuevo valor
      if(campo==='matricula' && s){
        const c = (s.ai_json && s.ai_json.campos) || {};
        const mat = c.matricula && c.matricula.valor;
        if(mat) verificarMatricula(sid, mat, (c.medico && c.medico.valor) || '');
      }
    }catch(err){ if(!yaAvisado(err)) toast(T("admin.queue.solicitudes.error_de_red", null, "Error de red.")); if(String(err.message)!=='401') inp.disabled=false; }
  }
});

// --- selector de turno agendado (responsable del producto, 2026-07-20, ronda 2) -----------------
// Un <select> común: sus opciones son los slots reales del servicio (o del
// médico elegido) — la misma fuente que ve el paciente (/api/service-slots,
// que ya descuenta tomados y holds) — en fecha amigable; la última opción
// "Otro horario…" destapa el datetime-local libre. El valor canónico vive en
// el <input type="hidden" class="turno"> que executePending ya lee. El
// default respeta la PREFERENCIA del paciente: primer slot de su franja
// ("when you choose a doctor, you automatically assign the first spot that
// matches her preference"), también al cambiar de médico/servicio.
const SLOTS_CACHE = new Map();   // 'svc|medico' → [{dia,hora}]
async function slotsPara(svc, medico, estricto){
  const key = `${svc}|${medico||''}`;
  if(SLOTS_CACHE.has(key)) return SLOTS_CACHE.get(key);
  let turnos = [];
  try{
    // La agenda de la MESA, no la ventana del paciente: un médico con una
    // semana de anticipación y un cierre hasta el viernes no ofrecía nada
    // (contraparte técnica, 19/10: Montes atiende los jueves y «no me aparece»).
    // Médico sin agenda propia → la agenda del servicio (`respaldo=servicio`), y
    // el server saca los minutos que ese médico ya tiene en cualquier servicio:
    // la misma fuente con la que «Aprobar» responde 409 (slot-409).
    // `derivacion=1`: el servicio puede ser de otro centro que el de la
    // solicitud (la mesa del Hospital deriva a un CAPS) y la mesa acotada igual
    // ve sus horarios.
    const url = `/api/admin/appointment-slots?servicio_id=${encodeURIComponent(svc)}&derivacion=1`
      + (medico ? `&medico=${encodeURIComponent(medico)}&respaldo=servicio` : '');
    const res = await fetch(url);
    const j = await res.json();
    // `estricto` (la tarjeta abierta): una respuesta que no es la agenda no es
    // «sin horarios» — falla y no entra al cache, así «Reintentar» pide de nuevo.
    if(estricto && (!res.ok || !Array.isArray(j.libres))) throw new Error('agenda');
    turnos = (j.libres||[]).map(t=>({dia:t.dia, hora:t.hora}));
  }catch(e){
    if(estricto) throw e;
    /* red caída → sin opciones de agenda, queda "Otro horario…" */
  }
  SLOTS_CACHE.set(key, turnos);
  return turnos;
}
// primer slot que cae en la franja preferida ('mañanas'/'tardes'/'noches');
// sin match (o sin preferencia) → el primero a secas.
function turnoPreferido(turnos, pref){
  const franjaDe = h => { const n = parseInt(h, 10); return n>=19?'noches':(n>=13?'tardes':'mañanas'); };
  const t = (pref && turnos.find(x=>franjaDe(x.hora)===pref)) || turnos[0];
  return `${t.dia}T${t.hora}`;
}
// Estados de la agenda de UNA tarjeta. Mientras no llegó (o falló) el horario
// del select está vacío y «Aprobar» apagado: lo que había era sólo la propuesta
// del server, igual para todas las tarjetas, y aprobar con eso chocaba con un
// turno ocupado (409). Se pone sincrónico al dibujar y se saca al llegar la
// agenda, así que con buena conexión no se llega a ver.
function agendaMarcar(card, estado){
  const sel = card.querySelector('.turno-sel');
  const btn = card.querySelector('[data-act="aprobar"]');
  if(!sel) return;
  if(!estado){
    sel.disabled = false;
    sel.removeAttribute('aria-busy');
    delete sel.dataset.agenda;
    if(btn) btn.disabled = false;
    return;
  }
  sel.disabled = true;
  sel.dataset.agenda = estado;
  if(estado==='cargando') sel.setAttribute('aria-busy','true'); else sel.removeAttribute('aria-busy');
  if(btn) btn.disabled = true;
}
// Una agenda que no llegó no es un error que la mesa tenga que leer (R19,
// Responsable del producto 20.10: «los mejores mensajes de error son los que no existen»): el
// horario sigue «Cargando horarios…», «Aprobar» apagado, y la tarjeta la pide
// de nuevo sola, cada vez más espaciado — como Mis turnos y Mis agendas.
function agendaReintentar(card, tok, opts){
  const pasos = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 60];
  const n = card._agendaReintento || 0;
  card._agendaReintento = n + 1;
  const espera = pasos[Math.min(n, pasos.length - 1)] * 1000;
  setTimeout(()=>{
    if(card._agendaTok===tok && card.isConnected) poblarTurnoSel(card, opts);
  }, espera);
}
// La tarjeta sabe su servicio aunque no dibuje el combo: un médico no deriva
// (`puedeDerivar`), así que su agenda sale del servicio de la solicitud — el
// mismo que manda executePending al aprobar (crítica del 08.11, F1: sin esto
// el médico pedía `servicio_id=` vacío y nunca podía aprobar).
function svcDeTarjeta(card){
  const el = card.querySelector('.svc-sel');
  if(el) return el.value || '';
  const s = ALL.find(x=>String(x.id)===String(card.dataset.id));
  return s ? (defaultsDe(s).servicio_id || '') : '';
}
let AGENDA_TOKEN = 0;
// `opts.tras409`: el server dijo que el horario elegido ya estaba tomado; la
// agenda se pide de nuevo y no se elige nada por el operador (con «el médico ya
// tiene turno» la hora queda en «Otro horario…», por si es un sobreturno).
async function poblarTurnoSel(card, opts){
  const sel = card.querySelector('.turno-sel');
  if(!sel) return;
  const svc = svcDeTarjeta(card);
  const med = (card.querySelector('.med-sel') && card.querySelector('.med-sel').value) || '';
  const hid = card.querySelector('.turno'), free = card.querySelector('.turno-free');
  const tras409 = (opts && opts.tras409) || '';
  const previo = tras409 ? hid.value : '';
  const tok = ++AGENDA_TOKEN;
  card._agendaTok = tok;
  sel.innerHTML = `<option value="" selected disabled>${esc(T("admin.queue.solicitudes.agenda_cargando", null, "Cargando horarios…"))}</option>`;
  hid.value = ''; free.hidden = true;
  agendaMarcar(card, 'cargando');
  let turnos;
  try{ turnos = await slotsPara(svc, med, true); }
  catch(e){ if(card._agendaTok===tok) agendaReintentar(card, tok, opts); return; }
  // mientras esperaba el fetch pudieron cambiar el combo otra vez → el último gana
  const medNow = (card.querySelector('.med-sel') && card.querySelector('.med-sel').value) || '';
  const svcNow = svcDeTarjeta(card);
  if(card._agendaTok!==tok || svcNow!==svc || medNow!==med) return;
  card._agendaReintento = 0;
  agendaMarcar(card, '');
  if(!turnos.length){
    // sin agenda: sólo queda cargar la hora a mano
    sel.innerHTML = `<option value="__otro" selected>${esc(T("admin.queue.solicitudes.otro_horario", null, "Otro horario…"))}</option>`;
    free.hidden = false; free.value = previo || free.value; hid.value = free.value;
    return;
  }
  const s = ALL.find(x=>String(x.id)===String(card.dataset.id));
  // Sólo vale para el servicio y el médico que lo tienen libre: derivado a
  // otro servicio u otro médico elegido, vuelve la franja preferida.
  const sug = s && s.medico_sugerido;
  const pedido = s && s.turno_pedido && svc===s.servicio_id
    && (!s.turno_pedido.libre || !med || (sug && sug.pedido && sug.nombre===med)) ? s.turno_pedido : null;
  const pedidoV = pedido ? `${pedido.dia}T${pedido.hora}` : '';
  // El hold del propio paciente saca su horario de /api/service-slots: libre
  // según el server → se agrega y se pre-elige; tomado → no se pre-elige nada.
  if(pedido && pedido.libre && !turnos.some(t=>`${t.dia}T${t.hora}`===pedidoV)){
    turnos = [{dia: pedido.dia, hora: pedido.hora}].concat(turnos);
  }
  const def = tras409 ? '' : pedido ? (pedido.libre ? pedidoV : '') : turnoPreferido(turnos, prefGeneral(s && s.preferencia_horario));
  const otro = tras409==='turno_medico_tomado' ? previo : '';
  hid.value = otro || def;
  const elegi = (def || otro) ? '' : `<option value="" selected disabled>${esc(T("admin.queue.solicitudes.elegi_horario", null, "Elegí un horario"))}</option>`;
  sel.innerHTML = elegi + turnos.map(t=>{
    const v = `${t.dia}T${t.hora}`;
    return `<option value="${esc(v)}"${v===def?' selected':''}>${esc(fmtTurno(v))}</option>`;
  }).join('') + `<option value="__otro"${otro?' selected':''}>${esc(T("admin.queue.solicitudes.otro_horario", null, "Otro horario…"))}</option>`;
  free.hidden = !otro;
  if(otro) free.value = otro;
}
function poblarTurnoSels(){
  document.querySelectorAll('#list .card[data-id]').forEach(c=>{ poblarTurnoSel(c); });
}
document.addEventListener('change', e=>{
  const ts = e.target.closest('.turno-sel');
  if(!ts) return;
  const acts = ts.closest('.sol-res-row') || ts.closest('.card');
  if(!acts) return;
  const hid = acts.querySelector('.turno'), free = acts.querySelector('.turno-free');
  if(!hid || !free) return;
  if(ts.value==='__otro'){
    free.value = hid.value || free.value;
    free.hidden = false; free.focus();
  }else{
    hid.value = ts.value;
    free.hidden = true;
  }
});
document.addEventListener('input', e=>{
  const f = e.target.closest('.turno-free');
  if(!f || !f.value) return;
  const fila = f.closest('.sol-res-row') || f.closest('.card');
  const hid = fila && fila.querySelector('.turno');
  if(hid) hid.value = f.value;
});
// derivar a otro servicio / cambiar médico invalida el cache local de slots y
// re-puebla el select de ESA tarjeta (default = franja preferida de nuevo).
document.addEventListener('change', e=>{
  const medSel=e.target.closest('.med-sel');
  if(e.target.closest('.svc-sel') || medSel){
    SLOTS_CACHE.clear();
    const card = e.target.closest('.card');
    if(card) poblarTurnoSel(card);
    if(medSel && card && medSel.value){
      api(`/api/admin/requests/${card.dataset.id}/assign`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({medico:medSel.value})
      }).then(async res=>{
        const data=await res.json().catch(()=>({}));
        if(!res.ok) throw new Error(errText(data, T("admin.queue.solicitudes.no_se_pudo_reasignar", null, "No se pudo reasignar.")));
        SLOTS_CACHE.clear();
        if(CURRENT_ROLE==='medico'){
          toast(T("admin.queue.solicitudes.reasignada_sigue_pendiente", null, "Reasignada. Sigue pendiente para el otro médico."));
          await load();
        }else{
          const row=ALL.find(x=>String(x.id)===String(card.dataset.id));
          if(row && data.solicitud){
            Object.assign(row, data.solicitud);
          }
          toast(T("admin.queue.solicitudes.medico_asignado", null, "Médico asignado."));
        }
      }).catch(err=>{ if(!yaAvisado(err)) toast(errText(err, T("admin.queue.solicitudes.no_se_pudo_reasignar", null, "No se pudo reasignar."))); });
    }
  }
});

// pager: Enter en "Ir a…" salta a esa página
document.addEventListener('keydown', e=>{
  const goto = e.target.closest('.pgr-goto');
  if(!goto || e.key!=='Enter') return;
  e.preventDefault();
  const v = parseInt(goto.value, 10);
  if(v>=1){ PAGE = v; render(); }
});

// búsqueda + filtros — cualquier cambio vuelve a la página 1 (pendiente y
// resueltas comparten esto: el cupo de "cargar más" de la pestaña que no se
// está mirando también se resetea, para que no arranque ya expandido si el
// operador vuelve a ella).
$('#q').addEventListener('input', e=>{ FILTERS.q = e.target.value; PAGE = 1; COLA_LIMITE_RES = COLA_LOTE; saveRequestPrefs(); render(); });
$('#f-estado').addEventListener('change', e=>{ FILTER = e.target.value; SOLO_PROXIMOS = false; PAGE = 1; SEL_ID = null; saveRequestPrefs(); render(); });
[['f-medico','medico'],['f-especialidad','especialidad'],['f-servicio','servicio'],['f-centro','centro'],['f-verdict','verdict'],['f-sug','sug']].forEach(([id,k])=>{
  $('#'+id).addEventListener('change', e=>{ FILTERS[k] = e.target.value; SOLO_PROXIMOS = false; PAGE = 1; COLA_LIMITE_RES = COLA_LOTE; saveRequestPrefs(); render(); });
});
// `estadoTambien`: the empty state's «Limpiar filtros» also returns to
// Pendientes when the state filter is what hid every row; the band's
// «Limpiar» keeps the state the operator picked, as before.
function limpiarFiltros(estadoTambien){
  FILTERS = {q:'',medico:'',especialidad:'',servicio:'',centro:'',verdict:'',sug:''};
  SOLO_PROXIMOS = false;
  if(estadoTambien){ FILTER = 'pendiente'; $('#f-estado').value = 'pendiente'; SEL_ID = null; }
  saveRequestPrefs();
  $('#q').value=''; ['f-medico','f-especialidad','f-servicio','f-centro','f-verdict','f-sug'].forEach(id=>$('#'+id).value='');
  PAGE = 1;
  COLA_LIMITE_RES = COLA_LOTE;
  render();
}
$('#f-clear').addEventListener('click', ()=>limpiarFiltros(false));

// Arranque: pedimos la config del gate (usuario sugerido, si hay 2FA/Turnstile),
// después probamos si ya hay sesión válida por cookie.
// ¿Hay sesión? Es la ÚNICA pregunta que puede mandar a la pantalla de login.
// Devuelve la identidad, 'sin-sesion' (401 nuestro: la cookie no vale) o
// 'error' (no se pudo saber: red, 5xx, Basic Auth, Cloudflare). Reintenta,
// porque un solo hipo de red no es motivo para pedirle la contraseña a nadie.
async function whoamiBoot(intentos=3){
  for(let i=0; i<intentos; i++){
    try{
      const w = await fetch('/api/admin/whoami', {credentials:'same-origin', cache:'no-store'});
      if(w.ok) return await w.json();
      if(w.status === 401){
        const j = await w.json().catch(()=>null);
        if(j && j.error === 'no_autenticado') return 'sin-sesion';
      }
    }catch(e){ /* red: reintentamos */ }
    if(i < intentos-1) await new Promise(r=>setTimeout(r, 400*(i+1)));
  }
  return 'error';
}

// Boot path for the server-routed /admin/solicitudes screen (Tier 5). The
// template seeds window.CURRENT_ROLE (the route already authenticated); here
// we only sync the module's `let CURRENT_ROLE` via applyRol and drive the
// SAME shared Solicitudes controller the legacy boot drives — no gate, no
// hash. The route gated this screen to admin/medico/admisionista, all three
// of which may read /api/admin/requests (the legacy boot only asked for
// 'admin' because medico/admisionista land on a different hash). Each step is
// isolated so none can down the screen.
async function bootDirectScreen(){
  try {
    await Promise.all([
      i18nReady,
      datesReady,
      formatReady,
      filterControlsReady,
      comboReady,
      prefsReady,
      tableReady,
      solicitudesReady,
      hsiSyncReady
    ]);
  } catch (error) {
    // A renderer failure can arrive before a delayed locale module. Wait for
    // its bounded settlement so English recovery copy is not frozen to the
    // Spanish fallback; a rejected locale still resolves to the fallback.
    await i18nReady;
    console.error('[admin] solicitudes:', error);
    const list = $('#list');
    if (list) list.innerHTML = '<p class="cnt-hint" role="alert">'
      + esc(T("admin.queue.solicitudes.load_failed", null, "No pudimos cargar las solicitudes."))
      + ' <a href="">' + esc(T("admin.queue.solicitudes.reload", null, "Reintentar")) + '</a></p>';
    return;
  }
  const rol = window.CURRENT_ROLE || null;
  try{ applyRol(rol); }catch(e){ console.error('[admin] applyRol:', e); }
  try{ await loadCatalogo(); }catch(e){ console.error('[admin] catálogo:', e); }
  // prefsReady loads the server-backed module before request hydration so the
  // first paint restores the saved filter/status state.
  try{ if(window.hydrateRequestPrefs) window.hydrateRequestPrefs(); }catch(e){ console.error('[admin] prefs:', e); }
  try{ await load(); }catch(e){ console.error('[admin] cola:', e); }
  try{ syncCrearTurno(); }catch(e){ console.error('[admin] crear-turno:', e); }
}

if(window.SANA_ADMIN_SCREEN === 'solicitudes') bootDirectScreen();

// Public direct-screen refresh hook used after a request mutation.
window.sanaShowSolicitudes = function(){
  if(CURRENT_ROLE && !['admin','medico','admisionista'].includes(CURRENT_ROLE)) return;
  if(ALL.length) render(); else load();
};
