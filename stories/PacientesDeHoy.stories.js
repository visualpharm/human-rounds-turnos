// «Pacientes de hoy» (/admin/cola) as the desk and a doctor see it, mounted
// by the REAL controller: static/platform/admin-queue.mjs's mount() over the
// same #sec-cola + #queue-initial-data the server writes, with the same
// stylesheets the screen loads. No markup is written here.
//
// The row (the product owner, 2026-11-09): «Name, N años» is one link with the history
// bars beside it, the referral is an inline «Orden» icon, the doctor carries
// the service under the name; no DNI and no Atendió / No vino in the row —
// the appointment card holds them, with the AI mark right above the AI text.
// The HSI line is one link that opens its explanation in a popup.
//
// The patients are invented; nothing here identifies a person.
import { applyEditorialStyles } from "./admin-editorial-styles.mjs";
import queueCss from "../static/platform/admin-queue.css?raw";
import { mount } from "../static/platform/admin-queue.mjs";
import * as Format from "../static/platform/format.mjs";
import * as FilterControls from "../static/platform/admin-filter-controls.mjs";
import * as Explainer from "../static/platform/intake-explainer.mjs";
import * as Forecast from "../static/platform/attendance-forecast.mjs";
import * as Prefs from "../static/platform/admin-preferences.mjs";
import * as HsiSync from "../static/platform/hsi-sync.mjs";
import * as Table from "../static/platform/admin-table.mjs";
import * as CalendarGrid from "../static/platform/calendar-grid.mjs";

export default {
  title: "Admin/Pacientes de hoy",
  parameters: { layout: "fullscreen" },
};

// The controller asks for its siblings by manifest name; the catalog hands
// it the Vite-bundled modules. An unknown name is a failure, not a fallback:
// «Crear turno» (admin-create-appointment.mjs) lives in the title band,
// outside this story.
const MODULES = {
  "platform/format.mjs": Format,
  "platform/admin-filter-controls.mjs": FilterControls,
  "platform/intake-explainer.mjs": Explainer,
  "platform/attendance-forecast.mjs": Forecast,
  "platform/admin-preferences.mjs": Prefs,
  // The catalog has no session: a stale-session check never confirms.
  "platform/admin-session.mjs": { confirmar: async () => false },
  "platform/hsi-sync.mjs": HsiSync,
  "platform/admin-table.mjs": Table,
  "platform/calendar-grid.mjs": CalendarGrid,
};
function importStatic(name) {
  return name in MODULES ? Promise.resolve(MODULES[name]) : Promise.reject(new Error("no story module " + name));
}

// The month calendar reads its busy days from the API; the catalog has none.
const baseFetch = window.fetch.bind(window);
function storyFetch(input, options) {
  const url = typeof input === "string" ? input : input.url;
  if (new URL(url, window.location.href).pathname === "/api/admin/day-queue/calendar") {
    return Promise.resolve(new Response(JSON.stringify({ dia: TODAY, dias: [] }), { headers: { "Content-Type": "application/json" } }));
  }
  return baseFetch(input, options);
}

const pad = (n) => String(n).padStart(2, "0");
const now = new Date();
const TODAY = now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
const born = (years, month = 3) => (now.getFullYear() - years) + "-" + pad(month) + "-14";

const VIENE = { banda: "viene-siempre", nivel: "good", hechos: 6, motivos: ["Vino a sus últimos 6 turnos"] };
const FALTA = { banda: "falta-a-veces", nivel: "warn", hechos: 4, motivos: ["Faltó a 1 de sus últimos 4 turnos"] };
const SEGUIDO = { banda: "falta-seguido", nivel: "bad", hechos: 2, motivos: ["Faltó a sus 2 últimos turnos"] };

const turno = (id, hora, paciente, years, servicio, extra) => Object.assign({
  id, hora, paciente, nacimiento: born(years), dni: String(20000000 + id * 1371), servicio,
  centro: "Hospital de Pinamar", elegible_asistencia: true, confirmo: "si",
}, extra || {});

const GRUPOS = [
  { medico_id: "m1", medico: "Laura Giménez", especialidad: "Cardiología", libres: [], turnos: [
    turno(1, "08:30", "Ana López", 64, "Cardiología", { ranking: VIENE, asistencia: "presente" }),
    turno(2, "09:00", "Hugo Montes", 71, "Cardiología", { ranking: FALTA, tiene_orden: true }),
    turno(3, "10:15", "Carla Benítez", 38, "Ecocardiograma", { ranking: VIENE, entrevista: {
      estado: "done", triage: "AMARILLO",
      resumen: "Palpitaciones desde hace dos semanas, sobre todo de noche. Sin dolor de pecho. Toma enalapril 10 mg." } }),
  ] },
  { medico_id: "m2", medico: "Martín Sosa", especialidad: "Kinesiología", libres: [], turnos: [
    turno(4, "08:45", "Rubén Ortiz", 52, "Kinesiología", { ranking: SEGUIDO, tiene_orden: true }),
    turno(5, "09:30", "Sofía Ledesma", 29, "Kinesiología", { asistencia: "ausente" }),
    turno(6, "11:00", "Marcos Peralta", 45, "Kinesiología", { ranking: VIENE, tiene_orden: true }),
  ] },
  { medico_id: "m3", medico: "Julieta Ruiz", especialidad: "Medicina general", libres: [], turnos: [
    turno(7, "09:15", "Mei Zhang", 33, "Medicina general", { ranking: VIENE }),
    turno(8, "10:45", "Zoe Aguirre", 8, "Pediatría", { ranking: FALTA }),
  ] },
];

const HSI = {
  tienda: "https://chromewebstore.google.com/detail/hsi-humanizador",
  guia: "/static/hsi-humanizador/guide.pdf",
};

let current = null;
function render(role, { grupos = GRUPOS, open = null } = {}) {
  if (current) current();
  window.CURRENT_ROLE = role;
  window.sanaImportStatic = importStatic;
  window.EventSource = undefined;
  window.fetch = storyFetch;
  // The stylesheets admin/base.html and admin/cola.html load, owned by the
  // story's root so they leave with it.
  const root = document.createElement("div");
  applyEditorialStyles(root);
  const queueStyle = document.createElement("style");
  queueStyle.textContent = queueCss;
  root.appendChild(queueStyle);
  const main = document.createElement("main");
  main.className = "adm5-main";
  root.appendChild(main);
  main.innerHTML ='<header class="adm-topbar"><div class="adm-h1row"><h1 id="adm-h1" class="adm-h1"></h1></div></header>'
    + '<div class="adm-content-wrap"><div id="queue-load-status" role="status" aria-live="polite"></div><div id="sec-cola"></div></div>';
  const data = document.createElement("script");
  data.id = "queue-initial-data";
  data.type = "application/json";
  data.textContent = JSON.stringify({ dia: TODAY, hoy: true, grupos, hsi_instalar: HSI });
  main.appendChild(data);
  // The controller looks its hosts up in the document: start once attached.
  const start = () => {
    if (!main.isConnected) { requestAnimationFrame(start); return; }
    const dispose = mount(main);
    current = dispose;
    dispose.ready.then(() => {
      if (open == null || current !== dispose) return;
      const link = main.querySelector('[data-cola-row="' + open + '"] .queue-pat-link');
      if (link) link.click();
    });
  };
  requestAnimationFrame(start);
  return root;
}

// The desk: one list for the day by hour, the doctor as a column.
export const Lista = () => render("admin");

// A doctor's own day: no doctor column, the service under the patient.
export const ListaDelMedico = () => render("medico", { grupos: [GRUPOS[0]] });

// The appointment card: when, with whom, attendance, DNI, history and the
// interview with the AI mark above the AI's summary.
export const Ficha = () => render("admin", { open: 3 });
