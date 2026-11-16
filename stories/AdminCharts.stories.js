// Every example calls the canonical report renderer with fictional aggregate data.
import * as Charts from "../static/platform/admin-charts.mjs";
import "../static/root/admin-direct.css";

export default {
  title: "Admin/Chart primitives",
  parameters: { layout: "padded" },
  beforeEach: () => {
    // Match the report's measured chart width to this padded canvas.
    const viewport = typeof window === "undefined" ? 728 : window.innerWidth;
    Charts.setAncho(Math.min(760, Math.max(320, viewport - 48)));
    return () => Charts.setAncho(0);
  },
};

function show(html) {
  const host = document.createElement("div");
  host.style.maxWidth = "760px";
  host.innerHTML = html;
  return host;
}

export const Scatter = () => show(Charts.scatter([
    { x: 12, y: 9, etiqueta: "Centro Norte", grupo: "Centros" },
    { x: 24, y: 16, etiqueta: "Centro Sur", grupo: "Centros" },
    { x: 32, y: 29, etiqueta: "Hospital", grupo: "Hospital" },
  ], { ejeX: "Solicitudes", ejeY: "Turnos", agrupar: true,
    linea: { tipo: "diagonal", pendiente: 1, etiqueta: "Paridad" } }));
export const Heatmap = () => show(Charts.heatmap({
    dias: ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"],
    horas: ["08", "09", "10", "11", "12"],
    celdas: [[2, 4, 6, 3, 1], [3, 5, 7, 4, 2], [1, 3, 6, 5, 2], [2, 4, 8, 5, 3], [1, 2, 5, 4, 1]],
  }, { marcar: [{ d: "Jueves", h: "10" }] }));
export const Treemap = () => show(Charts.treemap([
    { nombre: "Medicina general", valor: 48 }, { nombre: "Pediatría", valor: 30 },
    { nombre: "Cardiología", valor: 18 }, { nombre: "Enfermería", valor: 12 },
  ]));
export const Slope = () => show(Charts.slope([
    { nombre: "Centro Norte", antes: 17, despues: 11 },
    { nombre: "Centro Sur", antes: 21, despues: 14 },
    { nombre: "Hospital", antes: 12, despues: 9 },
  ], { antes: "Antes", despues: "Después" }));
export const Lollipop = () => show(Charts.lollipop([
    { nombre: "Pediatría", valor: 6.2 }, { nombre: "Medicina general", valor: 5.1 },
    { nombre: "Cardiología", valor: 3.8 },
  ], { ref: { valor: 5, etiqueta: "Mediana" } }));
export const Linea = () => show(Charts.linea([
    { nombre: "Solicitudes", puntos: [{ x: 1, y: 24, etiqueta: "Lun" }, { x: 2, y: 31, etiqueta: "Mar" }, { x: 3, y: 27, etiqueta: "Mié" }, { x: 4, y: 36, etiqueta: "Jue" }] },
    { nombre: "Aprobadas", puntos: [{ x: 1, y: 19, etiqueta: "Lun" }, { x: 2, y: 26, etiqueta: "Mar" }, { x: 3, y: 23, etiqueta: "Mié" }, { x: 4, y: 30, etiqueta: "Jue" }] },
  ]));
export const Densidad = () => show(Charts.densidad([8, 9, 10, 11, 11, 12, 12, 13, 14, 15, 17, 18]));
export const Piramide = () => show(Charts.piramide([
    { rango: "0–17", f: 18, m: 20, cantidad: 38 },
    { rango: "18–39", f: 29, m: 25, cantidad: 54 },
    { rango: "40–64", f: 23, m: 21, cantidad: 44 },
    { rango: "65+", f: 13, m: 9, cantidad: 22 },
  ]));
export const Tile = () => show(Charts.tile({ etiqueta: "Turnos confirmados", valor: "84", pill: { tono: "ok", texto: "Esta semana" } }));
export const Hero = () => show(Charts.hero([
    { texto: "La demanda subió esta semana.", tono: "atencion" },
    { texto: "Pediatría concentra la mayor parte de las solicitudes.", tono: "ok" },
  ]));
export const SinDatos = () => show(Charts.treemap([]) + Charts.linea([]));
