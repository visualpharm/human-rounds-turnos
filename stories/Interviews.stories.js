// The story mounts the production controller with synthetic doctors. It is
// the review surface for the direct-screen redesign, not a second copy of the
// page markup. HTML Storybook expects the node synchronously; preview()
// paints into that node after the canonical module is ready.
import gateCss from "../static/root/admin-gate.css?raw";
import directCss from "../static/root/admin-direct.css?raw";
import country from "../packs/ar/country.json";

window.SANA_STATIC = Object.assign({}, window.SANA_STATIC, {
  "platform/format.mjs": new URL("../static/platform/format.mjs", import.meta.url).href,
  "platform/admin-preferences.mjs": new URL("../static/platform/admin-preferences.mjs", import.meta.url).href,
  "platform/admin-session.mjs": new URL("../static/platform/admin-session.mjs", import.meta.url).href,
  "platform/admin-table.mjs": new URL("../static/platform/admin-table.mjs", import.meta.url).href,
  "platform/i18n.mjs": new URL("../static/platform/i18n.mjs", import.meta.url).href,
  "platform/admin-filter-controls.mjs": new URL("../static/platform/admin-filter-controls.mjs", import.meta.url).href,
  "platform/intake-explainer.mjs": new URL("../static/platform/intake-explainer.mjs", import.meta.url).href,
});
window.sanaImportStatic = (name) => import(window.SANA_STATIC[name]);
globalThis.SANA_COUNTRY_PHONE = country.phone;

const { preview } = await import("../app/modules/intake/admin-entrevistas.mjs");
await import("../static/platform/admin-table.mjs");
await import("../static/platform/admin-control-fit.mjs");

export default {
  title: "Admin/Symptom capture",
  parameters: { layout: "fullscreen" },
};

// Synthetic display fixtures; never copy a deployment's staff directory.
const PEOPLE = [
  ["Administrativo", "Diagnóstico por imágenes", false, false],
  ["ADRIANA RIVERA", "Psicología", null, true],
  ["Agustina Navarro", "Obstetricia", true, true],
  ["Alejandro Torres", "Cardiología", null, true],
  ["Alejo Robledo", "Diabetología", false, false],
  ["Alexia Duarte", "Medicina general", null, true],
  ["Ana Benítez", "Pediatría", true, true],
  ["Analía Figueroa", "Psicopedagogía", null, true],
  ["Analía Herrera", "Pediatría", false, false],
  ["Camila Ocampo", "Dermatología", null, true],
  ["Carolina Fontana", "Clínica médica", true, true],
  ["Cecilia Robles", "Neurología", null, true],
  ["Damián Ferraro", "Traumatología", false, false],
  ["Elena Córdoba", "Oftalmología", null, true],
  ["Esteban Reinoso", "Urología", true, true],
  ["Florencia Rey", "Ginecología", null, true],
  ["Gabriel Molina", "Otorrinolaringología", null, true],
  ["Inés Acosta", "Nutrición", false, false],
  ["Julián Ferreyra", "Kinesiología", true, true],
  ["María del Carmen Vera", "Medicina familiar", null, true],
];

const DATA = {
  enabled_system: true,
  medicos: PEOPLE.map((person, index) => ({
    id: "demo-doctor-" + (index + 1),
    nombre: person[0],
    especialidad: person[1],
    override: person[2],
    efectivo: person[2] === null ? true : person[2],
    cartel_url: "/intake/demo-doctor-" + (index + 1) + "/poster",
    pdf_url: person[2] !== false ? "/intake/demo-doctor-" + (index + 1) + "/poster?format=pdf" : null,
    tiene_token: person[3],
  })),
};

export const Doctors = () => {
  if (!document.getElementById("ent-story-shared-css")) {
    const style = document.createElement("style");
    style.id = "ent-story-shared-css";
    style.textContent = gateCss + directCss;
    document.head.appendChild(style);
  }
  const main = document.createElement("main");
  main.className = "adm5-main";
  main.innerHTML = '<header class="adm-topbar"><div class="adm-h1row">'
    + '<h1 class="adm-h1" id="adm-h1">Captura de síntomas</h1></div></header>'
    + '<div class="adm-content-wrap"><section id="sec-entrevistas" aria-labelledby="adm-h1"></section></div>';
  preview(main.querySelector("#sec-entrevistas"), DATA);
  return main;
};
