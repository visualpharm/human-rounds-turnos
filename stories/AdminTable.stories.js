// admin-table.mjs: el motor compartido de anchos
// arrastrables + columnas configurables para TODAS las tablas del admin.
//
// 2026-08-06 (the product owner: "do all the tables in the admin in the style of
// Solicitudes, which is a more compact style… it's like we use the same
// code for all tables"): la densidad de Solicitudes (fila de 34px, línea de
// pelo suave, encabezado quieto sin mayúsculas) se portó a la hoja de
// estilos que el shell directo enlaza (`admin-direct.css`). Cualquier sección que pinte
// `<table class="cnt-table">` la hereda sola — Pacientes, Mis turnos,
// Usuarios, Médicos, Reportes, HSI, la Entrevista previa, etc. — sin volver
// a declarar el estilo.
//
// El módulo de tablas no es dueño de un render(data)=>html como calendar-grid.mjs o
// admin-solicitudes.js: es un motor de COMPORTAMIENTO que se aplica sobre
// markup ya puesto en el DOM (así lo consumen admin-patients.js,
// admin-users.js, admin-my-appointments.js, admin-doctor-patients.js,
// admin-preparations.js y admin-queue.js: arman su `<table class="cnt-table">`
// por concatenación de string y después llaman `Table.crear()` +
// `Table.columnas()`). Esta story reproduce ESE MISMO contrato — la
// tabla es markup real con las clases reales, y lo que se ejercita es el
// motor real, no una reimplementación del CSS.
import * as Table from "../static/platform/admin-table.mjs";
import "../static/root/admin-direct.css";

export default {
  title: "Admin/Table engine",
  parameters: { layout: "padded" },
};

const COLS = [
  { key: "paciente", label: "Paciente", always: true },
  { key: "dni", label: "Documento", "default": true },
  { key: "servicio", label: "Servicio", "default": true },
  { key: "centro", label: "Centro", "default": true },
  { key: "turno", label: "Turno", "default": true },
  { key: "estado", label: "Estado", "default": true },
];

const FILAS = [
  ["Ana Lopez", "27.888.999", "Cardiología", "Hospital de Pinamar", "Lun 4 ago 09:00", "Aprobada"],
  ["Lucas Díaz", "30.000.685", "Odontología", "Riverside Clinic", "Mar 5 ago 11:30", "Pendiente"],
  ["Julieta Ruiz", "30.001.918", "Ginecología", "Centro Materno Infantil", "Mié 6 ago 08:20", "Aprobada"],
  ["Hugo Montes", "12.443.010", "Traumatología", "Hospital de Pinamar", "Jue 7 ago 15:00", "Rechazada"],
  ["Marina Ferreyra", "20.999.888", "Kinesiología", "CAPS Ostende", "Vie 8 ago 10:15", "Pendiente"],
  ["Mei Zhang", "95.114.207", "Oftalmología", "Hospital de Pinamar", "Sáb 9 ago 12:00", "Aprobada"],
];

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function tablaHtml(clave, cols) {
  const thead = cols.map((c) => '<th class="cnt-th">' + esc(c.label) + "</th>").join("");
  const body = FILAS.map((f) =>
    "<tr>" + f.map((v) => "<td>" + esc(v) + "</td>").join("") + "</tr>"
  ).join("");
  return '<div class="cnt-table-wrap"><table class="cnt-table" data-clave="' + clave + '">'
    + "<thead><tr>" + thead + "</tr></thead><tbody>" + body + "</tbody></table></div>";
}

// `withGear` reproduce lo que admin-patients.js/admin-users.js pintan arriba
// de su tabla: el mismo botón "Elegir columnas" (SanaTable.columnas), nunca
// uno nuevo — es la regla de "un control nuevo copia el que ya existe".
function mount(clave, cols, withGear) {
  const wrap = document.createElement("div");
  wrap.style.cssText = "max-width:900px;";
  const bar = document.createElement("div");
  const host = document.createElement("div");
  var sel = "table[data-clave='" + clave + "']";
  var anchos = Table.crear({
    clave: clave,
    selector: sel,
    tablas: function () { return wrap.querySelectorAll(sel); },
  });

  // El engranaje elige columnas de verdad: onChange repinta la tabla con la
  // lista activa (`gear.activas()`), igual que hace cada sección real
  // (admin-patients.js/admin-users.js) — si esto quedara en un no-op el
  // checkbox se vería tildar sin que la columna desaparezca.
  function pintarTabla(activas) {
    host.innerHTML = tablaHtml(clave, activas);
    anchos.programarFit();
  }

  if (withGear) {
    const gear = Table.columnas({ clave: clave, cols: cols, onChange: pintarTabla });
    bar.style.cssText = "display:flex;justify-content:flex-end;margin-bottom:10px;";
    bar.innerHTML = gear.gearHtml();
    wrap.appendChild(bar);
    pintarTabla(gear.activas());
  } else {
    pintarTabla(cols);
  }
  wrap.appendChild(host);
  return wrap;
}

// La densidad compacta: fila de 34px, encabezado quieto, línea de pelo
// suave — la misma hoja de estilos que Pacientes/Mis turnos/Usuarios/etc.
// heredan del shell directo sin declarar nada propio.
export const Compacta = () => mount("story-compacta", COLS);

// Con el engranaje de columnas (SanaTable.columnas): el mismo control en
// TODAS las tablas que lo tienen, nunca reinventado por sección.
export const ConEngranajeDeColumnas = () => mount("story-engranaje", COLS, true);
