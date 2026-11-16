// DoctorHours mounts the real admin-hours-editor.mjs with fictional
// example-town centres and services. It contains no real staff identity.
//
// Each slot injects servicioHtml with the production-shaped admin-content.js
// boundary. It exercises the same .fr-svc-sel combo contract as /admin,
// rather than the native-select fallback used by consumers without the widget.
import * as HoursEditor from "../static/platform/admin-hours-editor.mjs";
import { franjaServicioHtml, NOMBRE_CENTRO } from "./admin-combo-fixture.mjs";
import "../static/root/admin-direct.css";

export default {
  title: "Admin/Horarios del médico",
  parameters: { layout: "padded" },
};

const OPTS = {
  servicioHtml: franjaServicioHtml,
  centroOptions: (selected) =>
    Object.keys(NOMBRE_CENTRO)
      .map(
        (slug) =>
          `<option value="${slug}"${slug === selected ? " selected" : ""}>${NOMBRE_CENTRO[slug]}</option>`
      )
      .join(""),
};

function mount(centros) {
  const style = document.createElement("style");
  style.textContent = HoursEditor.css;
  const host = document.createElement("div");
  HoursEditor.render(host, centros, OPTS);
  const root = document.createDocumentFragment();
  root.appendChild(style);
  root.appendChild(host);
  return root;
}

// Days use the stored schedule convention: 1=Monday through 6=Saturday, 0=Sunday.
const RINCON = {
  centro: "riverside-clinic",
  franjas: [
    { dias: [3, 4], desde: "08:00", hasta: "14:00", cada_min: 20, servicio: "medicina-general-riverside-clinic" },
    { dias: [4], desde: "08:00", hasta: "12:00", cada_min: 20 },
  ],
  excepciones: [],
};

const CANADA = {
  centro: "lakeview-clinic",
  franjas: [{ dias: [5], desde: "08:00", hasta: "14:00", cada_min: 20 }],
  excepciones: [],
};

const clon = (o) => JSON.parse(JSON.stringify(o));

// One centre, two slots, and no closures. The selected service stays closed;
// the empty slot offers only services from this centre.
export const UnCentroSinCierres = () => mount([clon(RINCON)]);

// Two independent centre sections, each with centre-filtered services.
export const DosCentros = () => mount([clon(RINCON), clon(CANADA)]);

// Web booking offers one ordinary patient per time. Extras are staff-only;
// the legacy reserved count stays in the data but has no editable control.
export const ConCuposDePersonal = () => {
  const c = clon(RINCON);
  c.franjas.push({
    dias: [5],
    desde: "10:00",
    hasta: "12:00",
    cada_min: 20,
    cupos: { web: 0, reservado: 2, sobreturnos: 0, overbooking_percent: 20 },
  });
  return mount([c]);
};

// All three closure types: date range, partial-day block, and extra slots.
export const ConCierres = () => {
  const c = clon(RINCON);
  c.excepciones = [
    { tipo: "cerrado", desde_fecha: "2027-01-12", hasta_fecha: "2027-01-31", motivo: "Vacaciones" },
    { tipo: "bloqueado", fecha: "2026-08-19", desde: "08:00", hasta: "10:00", motivo: "Capacitación" },
    { tipo: "extra", fecha: "2026-08-22", desde: "09:00", hasta: "12:00", cada_min: 15, motivo: "Jornada de niñez" },
  ];
  return mount([c, clon(CANADA)]);
};

// A new doctor has no selected centre, so the service control has no catalog.
export const MedicoNuevo = () =>
  mount([{ centro: "", franjas: [], excepciones: [] }]);
