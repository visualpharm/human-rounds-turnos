// stories/MedCalIsland.stories.js — Tier 5 slice 3 pilot island (docs/roadmap/
// tier5-frontend-design.md, "Interactive screens as Preact+HTM islands").
// Calls the REAL island export, static/islands/MedCal.mjs's mount() — same
// function admin-content.js's wireMedicoCalendario mounts into the doctor
// Calendario tab's #med-cal-grid — which itself renders through the SAME
// canonical gridHtml() the plain CalendarGrid story next to this one proves.
// No reimplemented markup: click a day here and you're exercising the exact
// interactivity Preact now owns in production, not a story-only stand-in.
import { mount } from "../static/islands/MedCal.mjs";

export default {
  title: "Admin/Doctor calendar (island)",
  parameters: { layout: "padded" },
};

function sampleDias(from, count) {
  const estados = ["medcal-libre", "medcal-justo", "medcal-lleno"];
  const dias = [];
  const start = new Date(from + "T00:00");
  for (let i = 0; i < count; i++) {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    const iso = d.toISOString().slice(0, 10);
    dias.push({ dia: iso, className: estados[i % estados.length], title: iso });
  }
  return dias;
}

function render(dias, selectedInitial) {
  const root = document.createElement("div");
  const status = document.createElement("p");
  status.style.cssText = "font:13px system-ui, sans-serif;color:var(--text-3);margin:0 0 10px;";
  function setStatus(day) {
    status.textContent = day
      ? "Día seleccionado (click real, mismo callback que admin-content.js): " + day
      : "Ningún día seleccionado todavía — hacé click en uno.";
  }
  setStatus(selectedInitial);
  root.appendChild(status);
  const gridHost = document.createElement("div");
  root.appendChild(gridHost);
  mount(gridHost, {
    dias,
    selected: selectedInitial || "",
    onSelect: setStatus,
  });
  return root;
}

export const MesConDisponibilidad = () => render(sampleDias("2026-08-01", 30));

export const ConDiaSeleccionado = () => render(sampleDias("2026-08-01", 30), "2026-08-12");

export const SinTurnosAsignados = () => render([]);
