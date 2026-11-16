// static/platform/week.mjs: the "store hours" strip that turns the free-text
// schedule a center types ("Martes 8 y 16 h; jueves 8 a 14 h") into the whole
// week in one band. Consecutive days with the same hours merge into one block
// ("Lunes a viernes") and each block is as wide as the days it covers.
// The story calls the canonical renderer directly; no compatibility global.
import * as Week from "../static/platform/week.mjs";

export default {
  title: "Public site/Week hours",
  parameters: { layout: "padded" },
};

function mount(horario, titulo) {
  const host = document.createElement("div");
  host.setAttribute("data-semana", "");
  host.setAttribute("data-horario", horario);
  if (titulo) host.setAttribute("data-titulo", titulo);
  Week.render(host);
  return host;
}

// Cinco días idénticos colapsan a un solo bloque ancho.
export const LunesAViernes = () =>
  mount("Lunes a viernes de 8 a 14", "Cuándo atiende");

// Dos aperturas puntuales el mismo día van con coma, nunca con guión: un
// guión diría que atiende de corrido de 8 a 16.
export const DosAperturasEnElDia = () =>
  mount("Martes 8 y 16 h; jueves 8 y 14 h", "Cuándo atiende");

// Un día sin atención sigue en tinta con "No atiende" en gris — no desaparece.
export const ConDiasCerrados = () =>
  mount("Lunes, miércoles y viernes de 8 a 13", "Cuándo atiende");

export const FinDeSemanaDistinto = () =>
  mount("Lunes a viernes de 7 a 19; sábados de 9 a 13", "Cuándo atiende");
