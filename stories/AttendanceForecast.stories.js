// The attendance forecast («Historial») as the doctor's tables show it: the three-bar cell of
// each band, the dash with no history, and the explanation the Information button opens. Real
// render functions of static/platform/attendance-forecast.mjs; the catalog's Spanish fallbacks.
import * as Forecast from "../static/platform/attendance-forecast.mjs";
import "../static/root/admin-direct.css";

export default {
  title: "Admin/Attendance forecast",
  parameters: { layout: "padded" },
};

const T = (key, vars, fallback) => fallback;

const ROWS = [
  { banda: "viene-siempre", nivel: "good", hechos: 7, motivos: ["Vino a sus últimos 7 turnos"] },
  { banda: "falta-a-veces", nivel: "warn", hechos: 4, motivos: ["Faltó a 1 de sus últimos 4 turnos"] },
  { banda: "falta-seguido", nivel: "bad", hechos: 1, motivos: ["Faltó a su único turno"] },
  { banda: "sin-historial", sin_historial: true, hechos: 0 },
];

export const Cells = () => {
  const table = document.createElement("table");
  table.className = "cnt-table";
  table.innerHTML = "<thead><tr><th>Banda</th><th>Historial " + Forecast.infoButtonHtml(T) + "</th></tr></thead><tbody>"
    + ROWS.map((r) => "<tr><td>" + Forecast.bandLabel(r, T) + "</td><td>" + Forecast.cellHtml(r, T) + "</td></tr>").join("")
    + "</tbody>";
  return table;
};

export const Explanation = () => {
  const pop = document.createElement("div");
  pop.className = "forecast-pop";
  pop.innerHTML = Forecast.popoverHtml(T, { patient: Forecast.patientLine(ROWS[1], T), why: ROWS[1].motivos.join("\n") });
  return pop;
};
