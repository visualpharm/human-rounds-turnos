// static/platform/admin-solicitudes.mjs: la cola de solicitudes
// pendientes del panel, rediseñada el 2026-08-05 (handoff "Pinamar Admin
// Solicitudes Linear"): lista densa agrupada por lo que la IA sugiere a la
// izquierda, UNA solicitud abierta a la derecha.
//
// 2026-08-06: Aprobadas/Rechazadas/Todas pasan por el MISMO motor (ver el
// comentario de cabecera en el módulo) — las stories de acá abajo
// cubren esos tres estados con la MISMA función mount()/render() real, nunca
// con markup propio.
//
// Las stories montan el render REAL con datos de ejemplo — mismo módulo que
// sirve /admin, no una copia del markup. Los pacientes son inventados y los
// centros son los del pack de demo: nada de acá identifica a una persona.
import * as Solicitudes from "../static/platform/admin-solicitudes.mjs";
import * as HsiSync from "../static/platform/hsi-sync.mjs";
import * as Format from "../static/platform/format.mjs";

export default {
  title: "Admin/Solicitudes",
  parameters: { layout: "fullscreen" },
};

function mount(opts) {
  const wrap = document.createElement("div");
  const style = document.createElement("style");
  style.textContent = Solicitudes.css + HsiSync.css;
  wrap.appendChild(style);
  const box = document.createElement("div");
  // El split vive dentro de #list en el panel; acá le damos el alto para que
  // las dos columnas scrolleen por separado, igual que en la pantalla real.
  box.id = "list";
  box.style.cssText = "display:flex;flex-direction:column;height:640px;min-height:0;";
  box.innerHTML = Solicitudes.render(opts);
  wrap.appendChild(box);
  return wrap;
}

// 2026-11-08 (the product owner): a pending row says nothing when there is no order and
// shows a paperclip when there is one; the group names the reason, the rows
// don't repeat it; a group shows five and loads the rest inside itself.
const pend = (id, paciente, dni, servicio, centro, adjunto, motivo, cuando, extra) =>
  Object.assign({ id, paciente, dni, servicio, centro, veredicto: motivo || "", dot: "",
    adjunto, cuando, checkbox: true, abierta: false, tildada: false }, extra || {});

const APROBAR = {
  id: "aprobar",
  titulo: "Aprobar según sugerencia",
  total: 24,
  bulk: "aprobar",
  // 2026-08-06: "Aprobar" solo pasó a "Aprobar todas" — ver bulkLabelDe() en
  // admin.html y el comentario de decisión en COLA_GRUPOS.
  bulkLabel: "Aprobar todas",
  filas: [
    pend(1, "Ana Lopez", "27.888.999", "Cardiología", "Hospital de Pinamar", true, "", "Sáb 1 ago 12:26"),
    pend(2, "Lucas Díaz", "30.000.685", "Odontología", "Riverside Clinic", false, "", "Jue 30 jul 04:02"),
    pend(3, "Julieta Ruiz", "30.001.918", "Ginecología", "Centro Materno Infantil", true, "", "Vie 31 jul 05:02"),
    pend(4, "Hugo Montes", "12.443.010", "Traumatología", "Hospital de Pinamar", false, "", "Vie 31 jul 03:41"),
    pend(11, "Carla Benítez", "41.220.876", "Cardiología", "Hospital de Pinamar", false, "", "Vie 31 jul 22:10"),
  ],
  mas: 19, masLote: 19,
};

// One group per reason to look by hand, the reason in its title.
const REVISAR = {
  id: "none:orden_de_otra_persona",
  titulo: "Revisar a mano · Orden de otra persona",
  total: 2,
  bulk: null,
  motivo: "orden_de_otra_persona",
  filas: [
    pend(5, "Marina Ferreyra", "20.999.888", "Kinesiología", "CAPS Ostende", true, "", "Sáb 1 ago 14:37", { abierta: true }),
    pend(6, "Marcos Peralta", "38.111.222", "Kinesiología", "CAPS Ostende", true, "", "Sáb 1 ago 18:27"),
  ],
  mas: 0,
};

const REVISAR_DUDOSA = {
  id: "none:v:dudosa",
  titulo: "Revisar a mano · Dudosa",
  total: 2,
  bulk: null,
  motivo: "v:dudosa",
  filas: [
    pend(7, "Zoe Aguirre", "30.003.014", "Medicina general", "Riverside Clinic", true, "", "Dom 2 ago 21:02"),
    pend(8, "Mei Zhang", "95.114.207", "Oftalmología", "Hospital de Pinamar", true, "", "Dom 2 ago 19:15"),
  ],
  mas: 0,
};

const RECHAZAR = {
  id: "rechazar:no_es_una_orden",
  titulo: "No es una orden",
  total: 2,
  bulk: "rechazar",
  bulkLabel: "Rechazar todas",
  motivo: "no_es_una_orden",
  filas: [
    pend(9, "Rubén Ortiz", "20.456.781", "Kinesiología", "CAPS Ostende", true, "", "Sáb 1 ago 17:37"),
    pend(10, "Sofía Ledesma", "39.410.233", "Cardiología", "Hospital de Pinamar", true, "", "Vie 31 jul 21:40"),
  ],
  mas: 0,
};

// El panel derecho recibe fragmentos ya armados por admin.html (la orden, los
// campos de la IA, los controles de aprobar/rechazar). Acá se reproducen con
// el markup que esas funciones generan, para que la story muestre la pantalla
// entera y no un panel a medias.
const DETALLE = {
  id: 5,
  sug: "none",
  nombre: "Marina Ferreyra, 36",
  contactoHtml: Format.contactoIconoHtml("+54 9 2254 12-3456"),
  meta: Format.dniHtml("20999888"),
  veredicto: "Dudosa — la orden está a nombre de otra persona",
  dot: "sol-d-warn",
  pide: [
    { k: "Servicio", v: '<a href="#">Kinesiología</a>',
      aside: '<button type="button" class="svc-toggle" title="Derivar a otra especialidad">'
        + '<span class="adm-icon" aria-hidden="true">swap_horiz</span></button>' },
    { k: "Centro", v: '<a href="#">CAPS Ostende</a>' },
    { k: "Recibida", v: "Sáb 1 ago 14:37" },
  ],
  derivacion: '<div class="sol-deriv"><span class="svc-edit" hidden></span></div>',
  ordenHtml: '<div class="orden-prev" style="background:repeating-linear-gradient('
    + "135deg,var(--bg) 0 8px,var(--panel) 8px 16px)\"></div>",
  camposHtml: '<div class="campos">'
    + '<div class="campo-row"><span class="k">DNI</span><span class="v">20.456.781'
    + '<span class="ck ck-n">✗ no coincide</span></span></div>'
    + '<div class="campo-row"><span class="k">Paciente</span><span class="v">Rubén Ortiz'
    + '<span class="ck ck-n">✗ no coincide</span></span></div>'
    + '<div class="campo-row"><span class="k">Prepaga</span><span class="v">Swiss Medical</span></div>'
    + '<div class="campo-row"><span class="k">Médico</span><span class="v">Ana Amanda Robledo</span></div>'
    + '<div class="campo-row"><span class="k">Matrícula</span><span class="v">MN 100482'
    + '<span class="ck ck-y">✓ habilitado</span></span></div>'
    + '<div class="campo-row"><span class="k">Prácticas</span><span class="v">'
    + '<ul class="campo-list"><li>Kinesiología motora</li><li>Diez sesiones</li></ul></span></div>'
    + '<div class="campo-row"><span class="k">Fecha</span><span class="v">14 jul 2026'
    + '<span class="ck ck-y">✓ vigente</span></span></div></div>',
  arreglosHtml: '<div class="fixes">'
    + '<button type="button" class="fix">Pedirle la orden a su nombre</button>'
    + '<button type="button" class="fix">Pedirle la orden con su DNI</button></div>',
  aprobarHtml: '<select class="adm-sel"><option>Diego Coronel</option></select>'
    + '<select class="adm-sel turno-sel"><option>Hoy, 18:00</option></select>'
    + '<button class="btn btn-ok" data-act="aprobar">Aprobar</button>',
  sobreturnoHtml: "",
  rechazarHtml: '<select class="adm-sel motivo-sel"><option>Orden de otra persona</option></select>'
    + '<button class="btn btn-no" data-act="rechazar">Rechazar</button>',
  // No staff-patient chat exists: «Contactar» opens WhatsApp with the request named.
  contactarHtml: '<a class="btn sol-contactar" href="https://wa.me/5492254123456" target="_blank" rel="noopener">Contactar</a>',
};

// 2026-08-06: COLS creció de 6 a 10 claves para que Aprobadas/Rechazadas/
// Todas tengan sus propias columnas (estado/turno/hsi/resuelta_por) — el
// registro entero YA NO es "las columnas de Pendientes". admin.html elige el
// subconjunto según la pestaña (solColUniverso()); las stories de acá abajo
// hacen lo mismo a mano, así ninguna vista mezcla «Lectura de la IA» con
// «Estado» (son la misma fila renderizada dos veces si no se filtra).
const COLS_PENDIENTE = Solicitudes.columns().filter(c =>
  ["veredicto", "paciente", "dni", "servicio", "centro", "cuando"].includes(c.key));

const BASE = {
  cols: COLS_PENDIENTE,
  sort: { key: "cuando", dir: -1 },
  pie: { mostrando: 10, total: 72, lote: 100 },
  seleccionadas: 0,
  conSugerencia: false,
  detalle: null,
};

// ---- 2026-08-06: Aprobadas/Rechazadas/Todas, el mismo motor -------------
//
// Una fila resuelta no se tilda (checkbox:false — el lote sólo despacha
// pendientes) y trae `turno`/`hsiHtml`/`resuelta_por` en vez de `cuando`: los
// campos que admin.html arma con turnoOMotivoTxt()/hsiChip().
// The HSI cell is the real shared control (hsi-sync.mjs), never hand markup.
const HSI_OK = HsiSync.serverHtml("ok", { id: 101, futuro: true });
const HSI_PEND = HsiSync.serverHtml("pendiente", { id: 102, futuro: true });
const filaRes = (id, paciente, dni, servicio, centro, estado, dot, turno, hsiHtml, extra) =>
  Object.assign({ id, paciente, dni, servicio, centro, veredicto: estado,
    dot: "sol-" + dot, turno, hsiHtml: hsiHtml || "", resuelta_por: "",
    cuando: "", checkbox: false, abierta: false, tildada: false }, extra || {});

const COLS_APROBADA = Solicitudes.columns().filter(c =>
  ["estado", "paciente", "dni", "servicio", "centro", "turno", "hsi"].includes(c.key));
const COLS_RECHAZADA = Solicitudes.columns().filter(c =>
  ["estado", "paciente", "dni", "servicio", "centro", "turno", "resuelta_por"].includes(c.key));
const COLS_TODAS = Solicitudes.columns().filter(c =>
  ["estado", "paciente", "dni", "servicio", "centro", "turno", "hsi", "resuelta_por"].includes(c.key));

const APROBADAS = {
  id: "aprobada", titulo: "Aprobadas", total: 4, bulk: null,
  colapsable: true, colapsado: false,
  filas: [
    filaRes(101, "Elena Suárez", "24.556.712", "Cardiología", "Hospital de Pinamar",
      "Aprobada", "d-ok", "Lun 4 ago 09:00", HSI_OK),
    filaRes(102, "Tomás Ibarra", "31.204.887", "Odontología", "Riverside Clinic",
      "Aprobada", "d-ok", "Mar 5 ago 11:30 · Sobreturno autorizado",
      HSI_PEND),
    filaRes(103, "Micaela Ríos", "29.887.410", "Ginecología", "Centro Materno Infantil",
      "Aprobada", "d-ok", "Mié 6 ago 08:20", HSI_OK),
    filaRes(104, "Bruno Castillo", "18.220.045", "Traumatología", "Hospital de Pinamar",
      "Aprobada", "d-ok", "Jue 7 ago 15:00", HSI_OK),
  ],
};

const RECHAZADAS = {
  id: "rechazada", titulo: "Rechazadas", total: 2, bulk: null,
  colapsable: true, colapsado: false,
  filas: [
    filaRes(201, "Marcos Peña", "27.554.321", "Kinesiología", "CAPS Ostende",
      "Rechazada", "d-bad", "Orden vencida", "", { resuelta_por: "María Godoy" }),
    filaRes(202, "Lucía Farías", "40.112.998", "Cardiología", "Hospital de Pinamar",
      "Rechazada", "d-bad", "Orden de otra persona", "", { resuelta_por: "reference-deployment counterpart" }),
  ],
};

const CANCELADAS = {
  id: "cancelada", titulo: "Canceladas", total: 1, bulk: null,
  colapsable: true, colapsado: false,
  filas: [
    filaRes(301, "Nadia Coria", "35.667.221", "Medicina general", "Riverside Clinic",
      "Cancelada", "d-none", "Por el paciente", ""),
  ],
};

const PENDIENTES_TODAS = {
  titulo: "Pendientes", total: 3, bulk: null, colapsable: false, colapsado: false,
  filas: REVISAR.filas.slice(0, 3),
};

// El panel de detalle de una fila YA RESUELTA — mismo shape que
// detalleResVM() en admin.html (nombre/meta/estado + una tira de datos +,
// para una aprobada, la marca de asistencia).
const DETALLE_RES = {
  id: 101,
  nombre: "Elena Suárez",
  meta: "24.556.712 · Cardiología · Hospital de Pinamar",
  estadoTexto: "Aprobada",
  estadoDot: "sol-d-ok",
  kv: [
    { k: "Turno / motivo", v: "Lun 4 ago 09:00" },
    { k: "HSI", v: HSI_OK },
  ],
  asistenciaHtml: '<button type="button" class="attendance-score">'
    + '<span class="attendance-dot attendance-dot--good"></span><span>2 visitas</span></button>',
};

// Lo que el mostrador ve al entrar: los tres grupos, nada abierto, el panel
// derecho esperando.
export const ColaAgrupada = () =>
  mount(Object.assign({}, BASE, { grupos: [APROBAR, REVISAR, REVISAR_DUDOSA, RECHAZAR], soloClip: true }));

// Una solicitud abierta: la fila queda marcada y el panel muestra la orden,
// lo que la IA leyó, los arreglos de un click y las dos ramas de Resolver.
export const ConDetalleAbierto = () =>
  mount(Object.assign({}, BASE, { grupos: [APROBAR, REVISAR, REVISAR_DUDOSA, RECHAZAR], detalle: DETALLE, soloClip: true }));

// Con filas tildadas aparece la barra de selección — y sólo entonces.
export const ConSeleccion = () => {
  const g = JSON.parse(JSON.stringify(APROBAR));
  g.filas[0].tildada = true;
  g.filas[1].tildada = true;
  return mount(Object.assign({}, BASE, {
    grupos: [g, REVISAR], seleccionadas: 2, conSugerencia: true, soloClip: true,
  }));
};

// Sin agrupar: un solo bloque plano, para ordenar la cola entera por una
// columna sin que el agrupado la parta en tres.
export const SinAgrupar = () =>
  mount(Object.assign({}, BASE, {
    grupos: [{
      titulo: "Pendientes", total: 10, bulk: null,
      filas: APROBAR.filas.concat(REVISAR.filas, RECHAZAR.filas),
    }],
  }));

// La cola vacía: cuando un filtro no deja nada, el mensaje lo dice.
export const Vacia = () =>
  mount(Object.assign({}, BASE, {
    grupos: [], pie: null, vacio: "Ninguna solicitud coincide con la búsqueda.",
  }));

// ---- 2026-08-06: Aprobadas/Rechazadas/Todas ------------------------------

// Filtrando directo a Aprobadas: un solo grupo, siempre expandido (no hay
// nada más que mirar), con la columna Turno y el chip de HSI — lo que la
// tabla vieja mostraba y el feedback del responsable del producto pidió no perder.
export const Aprobadas = () =>
  mount(Object.assign({}, BASE, {
    cols: COLS_APROBADA, sort: { key: "turno", dir: -1 },
    grupos: [Object.assign({}, APROBADAS, { colapsable: false })],
    pie: { mostrando: 4, total: 67, lote: 100 },
  }));

// Filtrando directo a Rechazadas: la columna Turno pasa a mostrar el motivo,
// y aparece «Resuelta por» en vez de HSI.
export const Rechazadas = () =>
  mount(Object.assign({}, BASE, {
    cols: COLS_RECHAZADA, sort: { key: "turno", dir: -1 },
    grupos: [Object.assign({}, RECHAZADAS, { colapsable: false })],
    pie: { mostrando: 2, total: 32, lote: 100 },
  }));

// «Todas», como arranca: Pendientes primero (con su propio agrupado, sin
// tocar) y Aprobadas/Rechazadas/Canceladas PLEGADAS — son historia, no piden
// acción. El chevron y la cuenta quedan visibles igual.
export const TodasColapsada = () =>
  mount(Object.assign({}, BASE, {
    cols: COLS_TODAS, sort: { key: "turno", dir: -1 }, pie: null,
    grupos: [
      PENDIENTES_TODAS,
      Object.assign({}, APROBADAS, { colapsado: true, filas: [] }),
      Object.assign({}, RECHAZADAS, { colapsado: true, filas: [] }),
      Object.assign({}, CANCELADAS, { colapsado: true, filas: [] }),
    ],
  }));

// «Todas» con Aprobadas desplegada a mano: el punto verde en cada fila y las
// columnas Turno/HSI conviven con la lectura de la IA de las pendientes de
// arriba, bajo el mismo encabezado.
export const TodasConAprobadasAbierta = () =>
  mount(Object.assign({}, BASE, {
    cols: COLS_TODAS, sort: { key: "turno", dir: -1 }, pie: null,
    grupos: [
      PENDIENTES_TODAS,
      APROBADAS,
      Object.assign({}, RECHAZADAS, { colapsado: true, filas: [] }),
      Object.assign({}, CANCELADAS, { colapsado: true, filas: [] }),
    ],
  }));

// El panel de detalle de una solicitud YA RESUELTA: sin sección «Resolver»,
// con el estado en punto verde y, para una aprobada, la marca de asistencia.
export const ConDetalleResuelto = () =>
  mount(Object.assign({}, BASE, {
    cols: COLS_APROBADA, sort: { key: "turno", dir: -1 },
    grupos: [Object.assign({}, APROBADAS, {
      colapsable: false,
      filas: APROBADAS.filas.map(f => Object.assign({}, f, { abierta: f.id === 101 })),
    })],
    pie: { mostrando: 4, total: 67, lote: 100 },
    detalle: DETALLE_RES, detalleTipo: "resuelta",
  }));
