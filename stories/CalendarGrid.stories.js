// Renders the real canonical module used by both the admin agenda and the
// patient booking wizard. No reimplemented markup.
import { css, gridHtml } from "../static/platform/calendar-grid.mjs";

export default {
  title: "Booking/Calendar grid",
};

function mount(days, options) {
  const style = document.createElement("style");
  style.textContent = css;
  const wrap = document.createElement("div");
  wrap.innerHTML = gridHtml(days, options);
  const root = document.createDocumentFragment();
  root.appendChild(style);
  root.appendChild(wrap);
  return root;
}

function sampleDays(from, count) {
  const states = ["medcal-libre", "medcal-justo", "medcal-lleno"];
  const days = [];
  const start = new Date(from + "T00:00");
  for (let i = 0; i < count; i++) {
    const d = new Date(start);
    d.setDate(d.getDate() + i);
    const iso = d.toISOString().slice(0, 10);
    days.push({ dia: iso, className: states[i % states.length] });
  }
  return days;
}

export const MesConDisponibilidad = () =>
  mount(sampleDays("2026-08-01", 30), { from: "2026-08-01", to: "2026-08-31", today: "2026-08-04" });

export const ConDiaSeleccionado = () =>
  mount(sampleDays("2026-08-01", 30), {
    from: "2026-08-01",
    to: "2026-08-31",
    today: "2026-08-04",
    selected: "2026-08-12",
  });

export const DosMeses = () =>
  mount(sampleDays("2026-08-15", 45), { from: "2026-08-15", to: "2026-09-28", today: "2026-08-04" });
