// static/platform/admin-combo.mjs owns the admin typeable combo's markup,
// filtering, keyboard, and pointer behavior. This story calls its real
// html()/register()/open()/close() API, never copied markup or a replacement
// global. Its synthetic adapters exercise the same policy shapes as production:
// free-text doctors, the queue's no-service guidance, and strict catalog-only
// doctor-hour services.
//
// The direct admin shell CSS is imported unchanged. Synthetic data and API
// adapters live in admin-combo-fixture.mjs and are shared with DoctorHours.
import * as Combo from "../static/platform/admin-combo.mjs";
import { t } from "../static/platform/i18n.mjs";
import {
  franjaServicioHtml,
  derivarA,
  medicoA,
  noSeRealiza,
} from "./admin-combo-fixture.mjs";
import "../static/root/admin-direct.css";

export default {
  title: "Admin/Combo tipeable",
  parameters: { layout: "padded" },
};

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

Combo.register("med-sel", { freeText: true });
Combo.register("svc-sel", {
  emptyHtml: (q) => {
    const no = noSeRealiza(q);
    if (!no) return "";
    return '<div class="cmb-none">'
      + t("admin.queue.solicitudes.hospital_no_realiza", { estudio: "<b>" + esc(no) + "</b>" }, "El hospital no realiza {estudio}.")
      + "<br>"
      + esc(t("admin.queue.solicitudes.rechaza_no_se_realiza", null, "Rechazá con «no se realiza en este centro»."))
      + "</div>";
  },
});

// This uses the direct admin form row: label on the left, control on the right.
function campo(labelText, controlHtml) {
  const row = document.createElement("div");
  row.className = "cnt-row";
  row.innerHTML = "<label>" + esc(labelText) + "</label>" + '<div class="cnt-field">' + controlHtml + "</div>";
  return row;
}

const LBL_SERVICIO = () => t("admin.content.hours.col.service", null, "Servicio");
const LBL_DERIVAR = () => t("admin.queue.solicitudes.derivar_otra_especialidad", null, "Derivar a otra especialidad");
const LBL_MEDICO = () => t("admin.queue.solicitudes.medico_a_asignar", null, "Médico: a asignar");

const comboDe = (row) => row.querySelector(".cmb");
const abierto = (row, q) => {
  Combo.open(comboDe(row), q);
  return row;
};

// An already-selected service plus a focused open catalog.
export const SeleccionOrdinaria = () => {
  const h = document.createElement("div");
  h.appendChild(campo(LBL_SERVICIO(), franjaServicioHtml({ servicio: "cardiologia-sampleton-hospital" }, "sampleton-hospital")));
  h.appendChild(abierto(campo(LBL_SERVICIO(), franjaServicioHtml({ servicio: "" }, "sampleton-hospital")), ""));
  return h;
};

// Accent-insensitive search, shared no-result state, and queue-specific
// guidance for studies unavailable at the hospital.
export const BusquedaSinResultados = () => {
  const h = document.createElement("div");
  h.appendChild(abierto(campo(LBL_DERIVAR(), derivarA("", "")), "pedia"));
  h.appendChild(abierto(campo(LBL_DERIVAR(), derivarA("", "")), "resonancia"));
  h.appendChild(abierto(campo(LBL_SERVICIO(), franjaServicioHtml({ servicio: "" }, "sampleton-hospital")), "zzzz"));
  return h;
};

// Free-text doctors retain their typed value; strict slot services revert to
// the last valid catalog value.
export const TextoLibre = () => {
  const h = document.createElement("div");
  const medRow = campo(LBL_MEDICO(), medicoA("", ""));
  const medBox = comboDe(medRow);
  medBox.querySelector(".cmb-in").value = "Dra. Nueva";
  Combo.close(medBox);
  h.appendChild(medRow);
  const frRow = campo(LBL_SERVICIO(), franjaServicioHtml({ servicio: "cardiologia-sampleton-hospital" }, "sampleton-hospital"));
  const frBox = comboDe(frRow);
  frBox.querySelector(".cmb-in").value = "un servicio que no existe";
  Combo.close(frBox);
  h.appendChild(frRow);
  return h;
};

// Long catalog labels fit in the input and remain readable in the open list.
export const EtiquetasLargas = () => {
  const h = document.createElement("div");
  h.appendChild(campo(LBL_SERVICIO(), franjaServicioHtml({ servicio: "enfermeria-presion-riverside-clinic" }, "riverside-clinic")));
  h.appendChild(campo(LBL_MEDICO(), medicoA("María Cecilia Urtasun Viale", "María Cecilia Urtasun Viale")));
  h.appendChild(abierto(campo(LBL_DERIVAR(), derivarA("", "")), "curaciones"));
  return h;
};
