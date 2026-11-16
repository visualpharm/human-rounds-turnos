import * as Labs from "../static/platform/account-labs.mjs";
import {
  configureAccountFixture, installAccountFixture, mountAccountStoryWhenAttached,
  setAccountStoryHash,
} from "./account-fixture.mjs";

export default {
  title: "Public site/Account labs",
  parameters: { layout: "padded" },
  beforeEach: installAccountFixture,
};

const labResults = {
  informes: [
    {
      fecha: "2026-08-12", tipo_estudio: "Análisis de sangre", servicio: "Laboratorio",
      resumen: "Un valor para revisar con tu médico.", tono: "revisar",
      items: [
        { nombre: "Glucosa", valor: 118, unidad: "mg/dL", rango: [70, 100], fuera: true },
        { nombre: "Hemoglobina", valor: 13.8, unidad: "g/dL", rango: [12, 16], fuera: false },
      ],
    },
    // This is the complete server-redacted shape: no concealed report fields.
    { fecha: "2026-07-09", sensible: true },
  ],
  series: [{
    id: "glucosa", label: "Glucosa", unidad: "mg/dL", rango_ok: [70, 100],
    puntos: [["2026-03-12", 91], ["2026-05-18", 103], ["2026-08-12", 118]],
  }],
  analisis_pendiente: 0,
  clinico_href: "/d/clinica-medica",
};

function shell() {
  // Account places both IDs inside the same .dcard. Labs' phone-width table
  // margins rely on that card padding; the module itself owns all content.
  const card = document.createElement("section");
  card.className = "dcard";
  card.style.maxWidth = "800px";
  const host = document.createElement("div");
  host.id = "lr-host";
  const note = document.createElement("div");
  note.id = "lr-note";
  card.append(host, note);
  return { card, host };
}

function mountLabs(card, host) {
  return () => Labs.mount(host, {
    // The embedded controller delegates expiry to its owner, not a second
    // login form. This story makes that owner notification visible.
    onUnauthorized: () => {
      host.textContent = "Sign in required";
      host.setAttribute("role", "status");
      card.dataset.unauthorized = "true";
    },
  });
}

function show(labs, status) {
  configureAccountFixture({
    labs, labsStatus: status,
    uploadedLab: { fecha: "2026-09-01", tipo_estudio: "Resultado sintético adjuntado", items: [] },
  });
  setAccountStoryHash("#labs");
  const { card, host } = shell();
  mountAccountStoryWhenAttached(card, mountLabs(card, host));
  return card;
}

export const Results = () => show(labResults);
export const Empty = () => show({ informes: [], series: [], analisis_pendiente: 0 });
export const Unauthorized = () => show(null, 401);
