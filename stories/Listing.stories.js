// listing.mjs -> named render(): the ONE table engine of the patient site
// (repo invariant). Every story calls the real render() with a {cols, rows}
// payload shaped exactly like the one patient_web/site_*.py emits, so the column
// registry, the canonical order, the proximity colours and the uniform-column
// collapse are all exercised as they ship.
import { render } from "../static/platform/listing.mjs";

export default {
  title: "Public site/Listing",
  parameters: { layout: "padded" },
};

function mount(payload) {
  const host = document.createElement("div");
  render(host, payload);
  return host;
}

// A bookable cell as _next_from() builds it: friendly text plus the raw
// dia/hora the client needs to colour by proximity (<=7d green, 8-30 amber,
// >30 grey).
const turno = (v, dia, hora) => ({ v, kind: "turno", dia, hora, sort: "0" + dia + hora });
const walkin = () => ({ v: "Vení en el día", kind: "walkin", sort: "1a", nota: "por orden de llegada" });
const porOrden = () => ({ v: "Por orden de llegada", kind: "walkin", sort: "1b" });
const confirma = () => ({ v: "Te confirma el centro", kind: "solicitud", sort: "2" });

// El avatar es OBLIGATORIO en una fila de médico: site_doctors._doctor_avatar_fields
// siempre manda foto (`avatar`, un style inline) o iniciales (`avatar_ini` +
// `avatar_c`, 0-5). Sin ninguno de los dos la fila no dibuja el avatar y en
// mobile se rompe: el CSS corre las celdas 56px a la derecha y sube el turno
// -10px contando con que hay 44px de foto, así que el botón se monta sobre el
// centro. Las caras son las sintéticas del pack de demo — una story nunca
// muestra la foto de un médico real.
const foto = (id) =>
  `background-image:url('/demo-fotos/${id}.jpg');background-size:cover;background-position:center`;

const MEDICOS = [
  {
    avatar: foto("haddad-maria"),
    nombre: "María Laura Sangiorgi", href: "/doctor/maria-laura-sangiorgi",
    especialidad: "Cardiología", esp_href: "/d/cardiologia",
    centro: "Hospital de Pinamar", centro_href: "/center/hospital-pinamar",
    orden: true,
    proximo: turno("Mañana, 09:40", "2026-08-05", "09:40"),
    turno_href: "/service/cardiologia-hospital/book?slot=2026-08-05T09:40",
    info_href: "/service/cardiologia-hospital",
  },
  {
    avatar: foto("kowalski-miguel"),
    nombre: "Hugo Montes", href: "/doctor/hugo-montes",
    especialidad: "Clínica médica", esp_href: "/d/clinica-medica",
    centro: "CAPS Ostende", centro_href: "/center/caps-ostende",
    orden: false,
    proximo: turno("Martes 18 de agosto, 08:20", "2026-08-18", "08:20"),
    turno_href: "/service/clinica-ostende/book?slot=2026-08-18T08:20",
  },
  {
    avatar: foto("martinez-patricia"),
    nombre: "Martin Lucena", href: "/doctor/martin-lucena",
    especialidad: "Psicomotricidad", esp_href: "/d/psicomotricidad",
    centro: "CAPS DiabetPin", centro_href: "/center/caps-diabetpin",
    orden: true,
    proximo: turno("Jueves 24 de septiembre, 10:00", "2026-09-24", "10:00"),
    turno_href: "/service/psicomotricidad/book?slot=2026-09-24T10:00",
  },
  {
    avatar_ini: "NC", avatar_c: 3,
    nombre: "Nancy Pellegrini", href: "/doctor/nancy-pellegrini",
    especialidad: "Pediatría", esp_href: "/d/pediatria",
    centro: "Hospital de Pinamar", centro_href: "/center/hospital-pinamar",
    orden: false,
    proximo: walkin(),
  },
  {
    avatar_ini: "AS", avatar_c: 1,
    nombre: "Alejo Varela", href: "/doctor/alejo-varela",
    especialidad: "Traumatología", esp_href: "/d/traumatologia",
    centro: "Hospital de Pinamar", centro_href: "/center/hospital-pinamar",
    orden: true,
    proximo: confirma(),
  },
];

const ESTUDIOS = [
  {
    nombre: "Ecografía abdominal", href: "/service/ecografia-abdominal",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    lugar_mas: [{ nombre: "CAPS Ostende", href: "/center/caps-ostende" }],
    orden: true,
    proximo: turno("Hoy, 14:40", "2026-08-04", "14:40"),
    turno_href: "/service/ecografia-abdominal/book?slot=2026-08-04T14:40",
  },
  {
    nombre: "Electrocardiograma", href: "/service/electrocardiograma",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    orden: false,
    proximo: turno("Viernes 14 de agosto, 11:00", "2026-08-14", "11:00"),
    turno_href: "/service/electrocardiograma/book?slot=2026-08-14T11:00",
  },
  {
    nombre: "Laboratorio de rutina", href: "/service/laboratorio",
    lugar: "Hospital de Pinamar", lugar_href: "/center/hospital-pinamar",
    lugar_mas: [
      { nombre: "CAPS Ostende", href: "/center/caps-ostende" },
      { nombre: "CAPS Montecarlo", href: "/center/caps-montecarlo" },
    ],
    orden: true,
    proximo: porOrden(),
  },
];

const MEDICO_COLS = ["medico", "especialidad", "centro", "orden", "proximo"];
const ESTUDIO_COLS = ["estudio", "lugar", "orden", "proximo"];

// Los tres buckets de cercanía en una sola pantalla: verde (<=7 días), ámbar
// (8-30), gris (>30) — más las dos formas de walk-in.
export const MedicosPorEspecialidad = () =>
  mount({ cols: MEDICO_COLS, rows: MEDICOS });

export const EstudiosConVariosCentros = () =>
  mount({ cols: ESTUDIO_COLS, rows: ESTUDIOS });

// Un solo médico: la tabla no gana buscador ni encabezados ordenables.
export const UnaSolaFila = () =>
  mount({ cols: MEDICO_COLS, rows: MEDICOS.slice(0, 1) });

// Toda la lista es walk-in genérico -> la columna "Próxima disponibilidad"
// colapsa a UNA nota al pie en vez de repetir la misma frase por fila.
export const ColumnaUniformeColapsa = () =>
  mount({
    cols: MEDICO_COLS,
    rows: MEDICOS.map((r) => ({ ...r, proximo: confirma() })),
  });

// Más de 15 filas: aparecen el buscador y los encabezados ordenables.
export const ConBuscador = () =>
  mount({
    cols: MEDICO_COLS,
    rows: Array.from({ length: 18 }, (_, i) => {
      const base = MEDICOS[i % MEDICOS.length];
      return { ...base, nombre: base.nombre + " " + (i + 1), href: base.href + "-" + (i + 1) };
    }),
  });

// El payload llega desordenado a propósito: expandCols lo reordena al orden
// canónico entidad -> contexto -> requisitos -> cuándo.
export const OrdenCanonicoDeColumnas = () =>
  mount({ cols: ["proximo", "orden", "centro", "especialidad", "medico"], rows: MEDICOS });

export const SinResultados = () =>
  mount({ cols: MEDICO_COLS, rows: [] });

// La lista de UN centro no repite el centro en cada fila, así que el turno
// queda pegado al nombre y en mobile sube -10px para ocupar el aire del avatar
// (`lst-row-av--pegado`). Mirala a 390px junto a "Medicos Por Especialidad",
// que SÍ muestra centro: ahí el turno no sube y no tapa el nombre del centro.
export const SinColumnaCentro = () =>
  mount({
    cols: ["medico", "especialidad", "orden", "proximo"],
    rows: MEDICOS.map(({ centro, centro_href, ...r }) => r),
  });
