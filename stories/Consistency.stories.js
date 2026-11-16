// Consistency (the product owner, 2026-09-15): the same listing engine painting the two
// tables a specialty page stacks — exams (no avatar, rows with AND without a
// booking button) and doctors (avatar rows) — one under the other, exactly as
// /d/<specialty> does. Every row of both tables has to measure the same
// height, share one font size per column type, and one link treatment.
// Chromatic snapshots this at the breakout width, so a row that grows or a
// name that loses its underline shows up as a visual diff. It calls the real
// render(): a hand-rolled table would stop catching the regression.
import { render } from "../static/platform/listing.mjs";

export default {
  title: "Public site/Consistency",
  parameters: { layout: "fullscreen" },
};

const turno = (v, dia, hora) => ({ v, kind: "turno", dia, hora, sort: "0" + dia + hora });
const walkin = () => ({ v: "Vení en el día", kind: "walkin", sort: "1a", nota: "por orden de llegada" });
const porOrden = () => ({ v: "Por orden de llegada", kind: "walkin", sort: "1b" });

const ESTUDIOS = [
  {
    nombre: "Ecografía general", href: "/service/ecografia-general",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    orden: true,
    proximo: turno("Hoy, 08:00", "2026-09-15", "08:00"),
    turno_href: "/service/ecografia-general/book?slot=2026-09-15T08:00",
  },
  {
    nombre: "Entrega de resultados de laboratorio", href: "/service/entrega-resultados",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    orden: false,
    proximo: walkin(),
  },
  {
    nombre: "Laboratorio", href: "/service/laboratorio",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    orden: true,
    proximo: porOrden(),
  },
  {
    nombre: "Mamografía", href: "/service/mamografia",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    orden: true,
    proximo: turno("Hoy, 07:00", "2026-09-15", "07:00"),
    turno_href: "/service/mamografia/book?slot=2026-09-15T07:00",
  },
];

const MEDICOS = [
  {
    avatar_ini: "SM", avatar_c: 0,
    nombre: "Sabrina Moretti", href: "/doctor/sabrina-moretti",
    especialidad: "Laboratorio", esp_href: "/d/laboratorio",
    centro: "Hospital de Pinamar", centro_href: "/center/hospital-pinamar",
    orden: true,
    proximo: porOrden(),
  },
  {
    avatar_ini: "MG", avatar_c: 1,
    nombre: "Milagros Greco", href: "/doctor/milagros-greco",
    especialidad: "Diagnóstico por imágenes", esp_href: "/d/diagnostico",
    centro: "Hospital de Pinamar", centro_href: "/center/hospital-pinamar",
    orden: true,
    proximo: turno("Hoy, 10:00", "2026-09-15", "10:00"),
    turno_href: "/service/diagnostico/book?slot=2026-09-15T10:00",
  },
];

// The specialty page shell: a readable column (.wrap) whose listing blocks
// break out to the viewport at >=1121px. Same class names as patient_web/site_*.py
// emits, so the breakout rule inside listing.mjs applies here too.
function page() {
  const wrap = document.createElement("main");
  wrap.className = "wrap";
  wrap.style.cssText = "max-width:1120px;margin:0 auto;padding:26px 24px 40px;";
  for (const [id, cols, rows] of [
    ["estudio-centros", ["estudio", "lugar", "orden", "proximo"], ESTUDIOS],
    ["medicos-tabla", ["medico", "especialidad", "centro", "orden", "proximo"], MEDICOS],
  ]) {
    const sec = document.createElement("div");
    sec.className = "sec";
    sec.setAttribute("data-listado", "");
    sec.id = id;
    const host = document.createElement("div");
    sec.appendChild(host);
    wrap.appendChild(sec);
    render(host, { cols, rows });
  }
  return wrap;
}

// Rows with a booking button, walk-in rows and avatar rows, all the same
// height; exam and doctor names share size, weight and underline.
export const DosTablasMismaAltura = () => page();
