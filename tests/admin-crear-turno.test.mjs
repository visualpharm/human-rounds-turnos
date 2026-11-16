import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync(new URL('../static/platform/admin-create-appointment.mjs', import.meta.url), 'utf8');
const app = fs.readFileSync(new URL('../static/root/admin-app.js', import.meta.url), 'utf8');
const shell = fs.readFileSync(new URL('../templates/admin/base.html', import.meta.url), 'utf8');
const solicitudes = fs.readFileSync(new URL('../templates/admin/solicitudes.html', import.meta.url), 'utf8');
const colaTemplate = fs.readFileSync(new URL('../templates/admin/cola.html', import.meta.url), 'utf8');
const cola = fs.readFileSync(new URL('../static/platform/admin-queue.mjs', import.meta.url), 'utf8');

test('the classic root file is gone and the overlay publishes no compatibility global', () => {
  assert.equal(fs.existsSync(new URL('../admin-create-appointment.js', import.meta.url)), false);
  assert.match(src, /export \{ abrir \}/);
  assert.doesNotMatch(src, /window\.SanaCreateAppointment|module\.exports/);
});

// Pacientes de hoy keeps ONE «Crear turno», in the title row (the product owner,
// 2026-11-08): with the list narrowed to a doctor it starts with that doctor
// and the day on screen, so no per-doctor button is left in the list.
test('los puntos de entrada existen: título de Solicitudes y título de la cola con el médico elegido', () => {
  assert.match(solicitudes, /id="adm-crear-turno"/);
  assert.match(shell, /<header class="adm-topbar"><div class="adm-h1row">/);          // botón al lado del título
  assert.doesNotMatch(solicitudes, /admin-create-appointment\.js/);
  assert.match(app, /sanaImportStatic\('platform\/admin-create-appointment\.mjs'\)/);
  assert.doesNotMatch(cola, /data-crear-medico/);
  assert.match(cola, /openCreateAppointment\(actual && actual\.medico \? \{ medico: actual\.medico, dia: STATE\.dia \} : \{\}, titleCreate\)/);
});

test('el botón del título es de la mesa, no del médico', () => {
  assert.match(app, /CURRENT_ROLE === 'admin' \|\| CURRENT_ROLE === 'admisionista'/);
  assert.match(cola, /var agenda = window\.CURRENT_ROLE === "admin" \|\| window\.CURRENT_ROLE === "admisionista";/);
});

// El hueco libre SÍ (responsable del producto, 2026-08-05): es la propia agenda del médico y el
// paciente ya está enfrente. El servidor le fuerza su nombre igual.
test('un médico puede dar un hueco libre de su propio día', () => {
  const fn = cola.match(/function puedeCrearTurno\(\) \{[\s\S]*?\n  \}/)[0];
  assert.match(fn, /window\.CURRENT_ROLE === "medico"/);
});

test('el botón del título vive en Solicitudes y en Pacientes de hoy (reunión 2026-08-27: La contraparte técnica no lo encontraba parado en la cola)', () => {
  assert.match(colaTemplate, /id="adm-crear-turno"/);
  assert.doesNotMatch(colaTemplate, /admin-create-appointment\.js/);
  assert.match(colaTemplate, /static\('platform\/admin-queue\.mjs'\)/);
  assert.match(cola, /, titleCreate\);/);
  assert.match(cola, /function syncCrearTurno\(\)/);
});

test('reusa las fuentes que ya existen, no inventa endpoints paralelos', () => {
  assert.match(src, /\/api\/admin\/patients\?q=/);       // pacientes
  assert.match(src, /\/api\/admin\/content\/services/);  // catálogo
  assert.match(src, /\/api\/admin\/appointment-slots/);  // horarios (libres + sobreturnos)
  assert.match(src, /\/api\/admin\/appointments/);       // el alta
});

test('sólo ofrece servicios que de verdad dan turno', () => {
  assert.match(src, /s\.modo === "solicitud" \|\| s\.modo === "solicitud_con_orden"/);
});

test('el buscador de servicios entiende cómo habla la gente (platform/search.mjs)', () => {
  assert.match(src, /importDep\("platform\/search\.mjs"/);
  assert.match(src, /Search\.buscar\(filas, q,/);
  assert.doesNotMatch(src, /window\.SanaSearch|search\.js/);
});

test('el delegado del modal se ata UNA vez (si no, cada click corre N veces)', () => {
  assert.match(src, /if \(m\._ctWired\) return;/);
  assert.match(src, /m\._ctWired = true;/);
});

test('propone el primer horario libre y deja abrir el calendario entero', () => {
  assert.match(src, /return S\.sel \|\| \(S\.libres \|\| \[\]\)\[0\] \|\| null;/);
  assert.match(src, /Ver todos los horarios/);
  assert.match(src, /function calendarioHtml\(\)/);
  // los días del mes se miden por lugar disponible, no por existir
  assert.match(src, /var n = \(porDia\[iso\] \|\| \[\]\)\.length;/);
});

test('el sobreturno se elige a propósito: nunca es la propuesta automática', () => {
  // `elegido()` cae a S.libres[0] — jamás a S.sobres[0]
  const fn = /function elegido\(\) \{[\s\S]*?\n  \}/.exec(src)[0];
  assert.doesNotMatch(fn, /sobres/);
  // y viaja marcado al server, que es quien valida el cupo de la agenda
  assert.match(src, /sobreturno: !!el\.sobreturno/);
  assert.match(src, /Sin lugar libre\. Se puede agregar un sobreturno/);
});

test('los motivos del server se leen en castellano, no como código', () => {
  ['sobreturno_requiere_turno_previo', 'sobreturno_no_configurado',
   'sobreturno_cupo_agotado', 'turno_medico_tomado'].forEach((code) => {
    assert.match(src, new RegExp(code + ':'), `falta el texto de ${code}`);
  });
  assert.match(src, /function motivoTexto\(code\)/);
});

test('desde el calendario del médico se crea con médico y día puestos', () => {
  const content = fs.readFileSync(new URL('../static/platform/admin-content.mjs', import.meta.url), 'utf8');
  assert.match(content, /data-med-crear=/);
  assert.match(content, /openCreateAppointment\(\{ medico: item\.nombre \|\| "", dia: crear\.getAttribute\("data-med-crear"\)/);
  // y el turno recién creado aparece sin recargar la página
  assert.match(content, /onCreated: function/);
  assert.match(content, /currentCalendar === calendar\) return calendar\.refresh\(\)/);
  assert.doesNotMatch(content, /sanaRecargarCalendarioMedico/);
  assert.doesNotMatch(src, /sanaRecargarCalendarioMedico/);
  // el día que abrió el modal manda sobre el primer libre del mes
  assert.match(src, /if \(pre\) S\.sel = porDia\(pre\)\.libres\[0\] \|\| null;/);
});

test('el contexto del médico limita los servicios y se puede quitar', () => {
  const servicio = /function pasoServicio\(\)[\s\S]*?\n  \}/.exec(src)[0];
  const sugerir = /function sugerirServicios\(q\)[\s\S]*?\n  \}/.exec(src)[0];
  const abrir = /function abrir\(pre, options\)[\s\S]*?\n  \}/.exec(src)[0];
  assert.match(servicio, /data-cambiar="medico"/);
  assert.match(sugerir, /var catalogo = S\.medicoPre \? serviciosDe\(S\.medicoPre\) : SERVICIOS;/);
  assert.match(src, /if \(q === "medico"\) \{\s*S\.medicoPre = "";\s*if \(S\.servicio\) \{ cargarSlots\(\); return; \}/);
  assert.match(abrir, /var permitidos = pre\.medico \? serviciosDe\(pre\.medico\) : SERVICIOS;/);
});

test('quitar el médico invalida la selección y un request viejo no pisa la disponibilidad nueva', () => {
  const slots = /function cargarSlots\(\)[\s\S]*?\n  \}/.exec(src)[0];
  assert.match(src, /function limpiarSlots\(\) \{\s*SLOTS_REQUEST \+= 1;/);
  assert.match(slots, /var estado = S, request = \+\+SLOTS_REQUEST;/);
  assert.match(slots, /if \(S !== estado \|\| request !== SLOTS_REQUEST\) return;/);
  assert.match(slots, /S\.sel = null/);
  assert.match(src, /if \(limpiar !== false\) \{ SLOTS_REQUEST \+= 1; S = null; \}/);
});

test('Enter elige la primera sugerencia aunque el debounce no haya corrido', () => {
  // El operador tipea el DNI de corrido y aprieta Enter: la lista todavía está
  // vacía, así que Enter tiene que disparar la búsqueda y elegir cuando llega.
  assert.match(src, /function elegirPrimera\(inp, buscar\)/);
  assert.match(src, /clearTimeout\(TIMER\);/);
  assert.match(src, /Promise\.resolve\(buscar\(q\)\)\.then\(function \(\) \{/);
  assert.match(src, /if \(primera\) primera\.click\(\); else inp\.focus\(\);/);
});

test('los listeners de elementos van ARRIBA del guard de una-sola-vez', () => {
  // Puesto abajo, el segundo render (el del catálogo) salía por el return y el
  // input nuevo se quedaba sin keydown — así se perdió el Enter.
  const iKeydown = src.indexOf('addEventListener("keydown"');
  const iGuard = src.indexOf('_ctWired');
  assert.ok(iKeydown > 0 && iKeydown < iGuard,
    'el keydown tiene que atarse antes del guard _ctWired');
});

test('el render asíncrono conserva la búsqueda de paciente, sugerencias y caret', () => {
  const capturar = /function capturaBusquedaPaciente\(\) \{[\s\S]*?\n  \}/.exec(src)?.[0];
  const restaurar = /function restauraBusquedaPaciente\(busqueda\) \{[\s\S]*?\n  \}/.exec(src)?.[0];
  assert.ok(capturar && restaurar, 'faltan los helpers que preservan la búsqueda');

  let input = { value: 'Carla', selectionStart: 2, selectionEnd: 5 };
  let lista = { innerHTML: '<li>Carla Gómez</li>' };
  const document = { activeElement: input };
  const context = {
    document,
    hayPaciente: () => false,
    $: (selector) => selector === '#ct-pac' ? input : (selector === '#ct-pac-sug' ? lista : null)
  };
  vm.createContext(context);
  vm.runInContext(`${capturar}; ${restaurar}; globalThis.capturar = capturaBusquedaPaciente; globalThis.restaurar = restauraBusquedaPaciente;`, context);
  const busqueda = context.capturar();

  input = {
    value: '', selectionStart: 0, selectionEnd: 0,
    focus() { document.activeElement = this; },
    setSelectionRange(inicio, fin) { this.selectionStart = inicio; this.selectionEnd = fin; }
  };
  lista = { innerHTML: '' };
  context.restaurar(busqueda);

  assert.equal(input.value, 'Carla');
  assert.equal(lista.innerHTML, '<li>Carla Gómez</li>');
  assert.equal(document.activeElement, input);
  assert.deepEqual([input.selectionStart, input.selectionEnd], [2, 5]);
  assert.match(src, /cargarCatalogo\(\)\.then\(function \(\) \{[\s\S]*?render\(\);/);
});

test('una respuesta de pacientes tardía escribe en las sugerencias que sobrevivieron al render del catálogo', async () => {
  const sugerir = /function sugerirPacientes\(q\) \{[\s\S]*?\n  \}/.exec(src)?.[0];
  assert.ok(sugerir, 'falta la búsqueda de pacientes');
  let responder;
  const enVuelo = new Promise((resolve) => { responder = resolve; });
  const estado = {};
  let input = { value: 'Carla' };
  let lista = { innerHTML: '' };
  const context = {
    S: estado, PACIENTES_REQUEST: 0, PAC_CACHE: {}, NOTA_PENDIENTE: '',
    api: () => enVuelo,
    esc: (value) => String(value),
    dniVisible: (value) => String(value),
    T: (_key, _vars, fallback) => fallback,
    applyVars: (text) => text,
    $: (selector) => selector === '#ct-pac' ? input : (selector === '#ct-pac-sug' ? lista : null)
  };
  vm.createContext(context);
  vm.runInContext(`${sugerir}; globalThis.sugerir = sugerirPacientes;`, context);
  const pendiente = context.sugerir('Carla');

  // Simula el render que ejecuta cargarCatalogo(): los nodos viejos ya no
  // existen, pero la búsqueda se restauró en los controles nuevos.
  input = { value: 'Carla' };
  lista = { innerHTML: '' };
  responder({ ok: true, j: { items: [{ dni: '123', nombre: 'Carla', apellido: 'Gómez', activo: true }] } });
  await pendiente;

  assert.match(lista.innerHTML, /Gómez, Carla/);
  assert.equal(context.PAC_CACHE['123'].nombre, 'Carla');
  assert.match(sugerir, /S !== estado \|\| request !== PACIENTES_REQUEST/);
  assert.match(sugerir, /entrada\.value\.trim\(\) !== q/);
});

test('usa el verde de la marca, no un verde propio', () => {
  assert.doesNotMatch(src, /#567b42|#6f9959/);
  assert.match(src, /\.ct-cal-dia\.on\{background:var\(--accent\)/);
  assert.match(src, /\.slot-time\.on\{background:var\(--accent\)/);
});

test('un choque de horario se explica y refresca la grilla, no se traga', () => {
  assert.match(src, /r\.j\.error === "turno_tomado"/);
  assert.match(src, /Ese horario se ocupó recién/);
});

test('create closes before POST but announces only saving until acknowledgement', () => {
  const fn = /function crear\(\) \{[\s\S]*?\n  \}/.exec(src)[0];
  assert.ok(fn.indexOf('quitarModal(false)') < fn.indexOf('api("/api/admin/appointments"'));
  // Behavioral proof: held POST and malformed acknowledgement cases in
  // admin-create-appointment-native.test.mjs. This is only a wiring guard.
  assert.match(fn, /admin\.queue\.create_appt\.saving", null, "Guardando…"/);
  assert.doesNotMatch(fn, /turno_creado_guardando/);
  assert.match(src, /function montarModal\(\)/);
  assert.match(fn, /S = estado;\s*montarModal\(\)/);
  assert.doesNotMatch(solicitudes, /admin-create-appointment\.js/);
  assert.match(src, /export \{ abrir \}/);
  assert.doesNotMatch(src, /SanaCreateAppointment/);
});

// ---- turno anotado a mano, sin ficha (the product owner, 2026-07-27) -----------------
// "If I type something and it's not found in the list of patients — or even
// if found — there should be an option to just create the turno with this
// text as a note." Es lo que hoy hacen en HCI mientras atienden gente.

test('la opción de anotar aparece siempre, haya o no fichas que coincidan', () => {
  const fn = /function sugerirPacientes\(q\)[\s\S]*?\n  \}/.exec(src)[0];
  assert.match(fn, /var notaLi = /);
  // se concatena SIEMPRE, no dentro de la rama "sin resultados"
  assert.match(fn, /\) \+ notaLi;/);
  assert.match(fn, /data-nota="1"/);
});

test('con la nota alcanza para avanzar y para crear', () => {
  assert.match(src, /function hayPaciente\(\) \{ return !!\(S\.paciente \|\| S\.nota\); \}/);
  assert.match(src, /if \(!hayPaciente\(\) \|\| !S\.servicio \|\| !el \|\| GUARDANDO\) return;/);
  // uno de los dos viaja en el body, nunca los dos
  assert.match(src, /if \(S\.paciente\) payload\.dni = S\.paciente\.dni; else payload\.nota = S\.nota;/);
});

test('elegir una ficha limpia la nota y viceversa', () => {
  assert.match(src, /S\.paciente = PAC_CACHE\[li\.getAttribute\("data-dni"\)\]; S\.nota = "";/);
  assert.match(src, /S\.nota = \(\(inp && inp\.value\.trim\(\)\) \|\| NOTA_PENDIENTE \|\| ""\)\.slice\(0, 80\);/);
  assert.match(src, /if \(q === "paciente"\) \{ S\.paciente = null; S\.nota = ""; S\.alta = null; \}/);
});

// ---- escáner de DNI en la mesa (responsable del producto, 2026-07-27) -----------------------
// "Llegó el tipo y le escaneás el DNI, le ponés nuevo y te levanta el
// apellido" (reunión 2026-07-24). Dos caminos: el lector de teclado y la foto.

test('el lector de teclado se engancha al campo de paciente', () => {
  // dni-scan.mjs is fingerprinted (static_fingerprint.CLIENT_LOADED): its
  // current URL comes from window.SANA_STATIC via importDep(), never from a
  // literal /dni-scan.mjs?v=N.
  assert.match(src, /importDep\("platform\/dni-scan\.mjs"/);
  assert.doesNotMatch(src, /dni-scan\.mjs\?v=/);
  assert.match(src, /mod\.escucharLector\(input, usarDniLeido\)/);
  assert.match(src, /function wireLector\(input\)/);
});

test('la foto del documento usa el mismo módulo que el registro del paciente', () => {
  assert.match(src, /importDep\("platform\/dni-barcode\.mjs"/);
  assert.doesNotMatch(src, /dni-barcode\.mjs\?v=/);
  assert.match(src, /mod\.readDni\(f\)/);
  assert.match(src, /id="ct-scan-file"/);
});

test('dni-scan/dni-barcode resolution fails closed through window.SANA_STATIC', () => {
  // Same pattern as resolveUrl() in islands.mjs: rejected import plus console.error when
  // the shell did not emit the manifest, never a fixed fallback URL; the
  // import() rejection takes the existing catch path.
  assert.match(src, /function importDep\(name, nodeUrl\) \{/);
  assert.match(src, /window\.SANA_STATIC && window\.SANA_STATIC\[name\]/);
  assert.match(src, /return Promise\.reject\(new Error\("\[static\] " \+ name \+ " is not in window\.SANA_STATIC"\)\)/);
  assert.doesNotMatch(src, /"\/dni-scan\.js"|"\/dni-barcode\.js"/);
  assert.doesNotMatch(src, /function importStatic|function staticUrl/);
});

test('lo leído busca la ficha, y si no existe abre el alta con todo lo que dice el documento', () => {
  const fn = /function usarDniLeido\(patient\)[\s\S]*?\n  \}/.exec(src)[0];
  assert.match(fn, /sugerirPacientes\(patient\.dni\)/);
  assert.match(fn, /if \(ficha\) \{ S\.paciente = ficha; S\.nota = ""; render\(\); return; \}/);
  assert.match(fn, /S\.alta = \{ dni: patient\.dni, apellido: legible\(patient\.apellido\), nombre: legible\(patient\.nombres\),/);
  assert.match(fn, /nacimiento: arg \|\| patient\.nacimiento \|\| "", sexo: patient\.sexo \|\| "" \}/);
});

// El alta pide lo que HSI necesita para empadronar; anotar a mano sigue ahí.
test('sin ficha se ofrece el alta con los datos que pide HSI, y la nota a mano queda', () => {
  assert.match(src, /var altaLi = items\.length \? "" : '<li data-alta="1" class="on">/);
  assert.match(src, /'<\/li>' \+ altaLi\) \+ notaLi;/);
  const crear = /function crearPaciente\(\)[\s\S]*?\n  \}/.exec(src)[0];
  assert.match(crear, /if \(!a\.nacimiento \|\| !a\.sexo\)/);
  assert.match(crear, /mod\.parseFlexibleDate\(a\.nacimiento\)/);
  assert.match(crear, /S\.paciente = r\.j\.item; S\.alta = null; S\.nota = "";/);
});

test('el parseo del payload vive una sola vez (dni-scan.mjs), no duplicado', () => {
  const scan = fs.readFileSync(new URL('../static/platform/dni-scan.mjs', import.meta.url), 'utf8');
  const barcode = fs.readFileSync(new URL('../static/platform/dni-barcode.mjs', import.meta.url), 'utf8');
  assert.match(scan, /export function parsePayload\(raw\)/);
  assert.match(scan, /export function escucharLector\(input, onDni, opts\)/);
  // dni-scan.mjs is fingerprinted: dni-barcode.mjs dynamically imports the URL
  // published by window.SANA_STATIC (fail-closed, without fixed fallback) and
  // takes parsePayload from the resolved module, never a static ?v= import.
  assert.match(barcode, /const SCAN_URL = staticUrl\('platform\/dni-scan\.mjs'\);/);
  assert.match(barcode, /const SCAN = SCAN_URL \? import\(SCAN_URL\) : Promise\.reject\(/);
  assert.match(barcode, /const \{ parsePayload \} = await SCAN;/);
  assert.doesNotMatch(barcode, /from '\/dni-scan\.mjs/);
  assert.doesNotMatch(barcode, /dni-scan\.mjs\?v=/);
  assert.doesNotMatch(barcode, /function parsePayload/);
});

// The hospital desk (2026-10-23): a disabled test doctor still
// read under Cardiología. The content APIs list disabled rows too (the editors
// show them with «Deshabilitado»); the desk modal offers only live ones.
test('a disabled doctor or service never reaches Crear turno', () => {
  assert.match(src, /MEDICOS = \(\(rs\[2\]\.j \|\| \{\}\)\.items \|\| \[\]\)\.filter\(function \(m\) \{ return !m\._borrado && !m\.rol; \}\);/);
  assert.match(src, /if \(s\._borrado\) return false;/);
});

// 2026-10-23: Ecocardiograma and Ergometría (Hospital) read «Técnico/a,
// Administrativo.» as their doctors. Those are the spreadsheet's role rows
// (`rol: true` in medicos.json): their windows feed the service hours, but
// they are not a person — same rule as site_catalog._professionals.
test('a role row is never named as a doctor of a service in Crear turno', () => {
  const load = src.match(/MEDICOS = \(\(rs\[2\][^\n]*/)[0];
  const ctx = { rs: [null, null, { j: { items: [
    { id: 'tecnico-a', nombre: 'Técnico/a', rol: true, cluster: 'imagenes', centro: 'hospital' },
    { id: 'administrativo', nombre: 'Administrativo.', rol: true, cluster: 'imagenes', centro: 'hospital' },
    { id: 'eco-pratti', nombre: 'Mauro Ezequiel Pratti', cluster: 'imagenes', centro: 'hospital' },
    { id: 'baja', nombre: 'Dada de Baja', _borrado: true, cluster: 'imagenes', centro: 'hospital' },
  ] } }], MEDICOS: [] };
  vm.createContext(ctx);
  vm.runInContext(load, ctx);
  assert.deepEqual(ctx.MEDICOS.map((m) => m.id), ['eco-pratti']);
});

test('un walk-in que abre el mismo día sí entra en la lista de servicios', () => {
  assert.match(src, /s\.apertura_mismo_dia && \(s\.modo === "walkin"/);
});

// La contraparte técnica (2026-10-19): «les ahorraría tiempo si le aparecieran el nombre del
// profesional además del caps y los horarios».
test('the service list and the day view say which doctor', () => {
  const fn = src.match(/function enPool\(m, s\) \{[\s\S]*?\n  \}/)[0]
    + src.match(/function medicosDe\(s\) \{[\s\S]*?\n  \}/)[0];
  // The doctor↔service link is the server's (`_servicios_pool`, schedule_policy
  // .belongs): the overlay never re-derives it from cluster + center.
  const ctx = {
    MEDICOS: [
      { nombre: 'LORENA ZAPATA', cluster: 'oftalmo', centro: 'caps-valeria', _servicios_pool: ['oftalmo-caps'] },
      { nombre: 'Ana Ruiz', cluster: 'oftalmo', centros: [{ centro: 'hospital' }], _servicios_pool: ['oftalmo-hospital'] },
      { nombre: 'Otro', cluster: 'clinica', centro: 'caps-valeria', _servicios_pool: ['clinica-caps'] },
      // same cluster + center as the service, but the server says no (catch-all
      // cluster, other specialty) and a disabled one: neither is named
      { nombre: 'Distinta', cluster: 'oftalmo', centro: 'caps-valeria', _servicios_pool: [] },
      { nombre: 'Baja', _borrado: true, _servicios_pool: ['oftalmo-caps'] },
      { nombre: 'Sin dato', cluster: 'oftalmo', centro: 'caps-valeria' },
    ],
    nombreMedico: (n) => n.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()),
  };
  vm.createContext(ctx);
  vm.runInContext(fn, ctx);
  assert.deepEqual([...ctx.medicosDe({ id: 'oftalmo-caps', cluster: 'oftalmo', centro: 'caps-valeria' })], ['Lorena Zapata']);
  assert.match(src, /resto: \[s\.categoria, centroNombre\(s\.centro\), medicos\.join\(" "\)/);
  assert.match(src, /'<span class="ct-svc-med">' \+ esc\(f\.medicos\.join\(", "\)\)/);
  assert.match(src, /html \+= horasPorMedico\(delDia, "l", libres\);/);
  assert.match(src, /\+ horasPorMedico\(sobreDia, "s", sobres\);/);
});
