// Shared synthetic fixture for AdminCombo and DoctorHours. It adapts real
// Combo APIs to the shapes consumed by admin-content.js and admin-app.js;
// it does not duplicate combo markup, filtering, or keyboard behavior.
// Data is fictional example-town data in the same shapes as compatible-service
// responses ({id, nombre, centro}) and queue items ({v, t, s, k}).
import * as Combo from "../static/platform/admin-combo.mjs";
import { t } from "../static/platform/i18n.mjs";

// Compatible doctor services, ordered by compatibility and carrying a centre.
export const SVC_COMPAT = [
  { id: "cardiologia-sampleton-hospital", nombre: "Cardiología", centro: "sampleton-hospital" },
  { id: "guardia-pediatrica-hospital", nombre: "Consulta espontánea de guardia pediátrica", centro: "sampleton-hospital" },
  { id: "electrocardiograma-hospital", nombre: "Electrocardiograma", centro: "sampleton-hospital" },
  { id: "medicina-general-riverside-clinic", nombre: "Medicina general", centro: "riverside-clinic" },
  { id: "curaciones-riverside-clinic", nombre: "Curaciones y control de heridas", centro: "riverside-clinic" },
  { id: "enfermeria-presion-riverside-clinic", nombre: "Enfermería - control de presión arterial", centro: "riverside-clinic" },
  { id: "pediatria-riverside-clinic", nombre: "Pediatría", centro: "riverside-clinic" },
  { id: "medicina-general-lakeview-clinic", nombre: "Medicina general", centro: "lakeview-clinic" },
  { id: "adulto-mayor-lakeview-clinic", nombre: "Control de salud del adulto mayor", centro: "lakeview-clinic" },
  { id: "pediatria-lakeview-clinic", nombre: "Pediatría", centro: "lakeview-clinic" },
];

// Queue route-to items, including centre subtitles and hidden search keywords.
export const SVC_COLA = [
  { v: "cardiologia-sampleton-hospital", t: "Cardiología", s: "· Sampleton Regional Hospital", k: "corazon cardiologo" },
  { v: "pediatria-riverside-clinic", t: "Pediatría", s: "· Riverside Clinic", k: "pediatra nino sano" },
  { v: "medicina-general-riverside-clinic", t: "Medicina general", s: "· Riverside Clinic", k: "clinico medico clinica" },
  { v: "curaciones-riverside-clinic", t: "Curaciones y control de heridas", s: "· Riverside Clinic" },
];

// Queue doctors. Availability is a dropdown-only subtitle and does not widen
// the selectable input value.
export const MEDICOS = [
  { v: "María Cecilia Urtasun Viale", t: "María Cecilia Urtasun Viale", s: t("admin.queue.solicitudes.libre_desde", { cuando: "Martes, 13:00" }, "— libre Martes, 13:00") },
  { v: "Roberto Isaacs", t: "Roberto Isaacs", s: t("admin.queue.solicitudes.libre_desde", { cuando: "Miércoles, 07:30" }, "— libre Miércoles, 07:30") },
  { v: "María Gabriela Vidoni", t: "María Gabriela Vidoni", s: "" },
];

// Fictional centres for DoctorHours, using the SVC_COMPAT slugs above.
export const NOMBRE_CENTRO = {
  "sampleton-hospital": "Sampleton Regional Hospital",
  "riverside-clinic": "Riverside Clinic",
  "lakeview-clinic": "Lakeview Clinic",
};

// A small synthetic unavailable-study list for the queue empty-state policy.
export const NO_SE_REALIZA = [
  { nombre: "resonancia magnética", match: ["resonancia", "rmn"] },
  { nombre: "densitometría ósea", match: ["densitometria", "dexa"] },
];

export function noSeRealiza(q) {
  const nq = String(q || "").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
  if (nq.length < 3) return "";
  const hit = NO_SE_REALIZA.find((e) =>
    e.match.some((m) => {
      const nm = m.toLowerCase().normalize("NFD").replace(/\p{M}/gu, "");
      return nm.indexOf(nq) === 0 || nq.indexOf(nm) === 0;
    }));
  return hit ? hit.nombre : "";
}

// This synthetic adapter has the same public boundary shape as the private
// admin-content.js franjaServicioHtml closure: a catalog id is required, its
// placeholder depends on the centre, and .fr-svc-sel carries the canonical id.
export function franjaServicioHtml(fr, centro) {
  let actual = String((fr && fr.servicio) || "");
  const items = SVC_COMPAT
    .filter((s) => !!centro && String(s.centro) === String(centro))
    .map((s) => ({ v: s.id, t: s.nombre || String(s.id), s: "" }));
  const sel = items.filter((x) => x.v === actual)[0];
  if (!sel) actual = "";
  return Combo.html(
    "fr-svc-sel", items, actual, sel ? sel.t : "",
    centro
      ? t("admin.content.catalog.doctors.slot_service_unset", null, "Sin especificar")
      : t("admin.content.catalog.doctors.slot_service_no_center", null, "Elegí un centro primero"),
    t("admin.content.catalog.doctors.slot_service_title", null, "Servicio de este centro que atiende el médico en este horario"));
}

// Queue "Route to" adapter.
export function derivarA(value, display) {
  return Combo.html(
    "svc-sel", SVC_COLA, value, display,
    t("admin.queue.table.servicio", null, "Servicio"),
    t("admin.queue.solicitudes.derivar_otro_servicio_title", null, "El operador puede derivar a otro servicio"));
}

// Queue "Doctor" adapter.
export function medicoA(value, display) {
  return Combo.html(
    "med-sel", MEDICOS, value, display,
    t("admin.queue.solicitudes.medico_a_asignar", null, "Médico: a asignar"),
    t("admin.queue.solicitudes.asignar_medico_title", null, "Asignar un médico"));
}
