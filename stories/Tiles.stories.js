// The single tile component used across centros, talleres and the directory
// (tiles.mjs -> render). Real render call, sample data only.
import { render } from "../static/platform/tiles.mjs";

export default {
  title: "Public site/Tiles",
};

const CENTROS = [
  {
    slug: "hospital-pinamar",
    nombre: "Hospital de Pinamar",
    direccion: "Av. Bunge 1250, Pinamar",
    jtbd: "Guardia, internación y consultorios externos",
    foto: "/logo.png",
  },
  {
    slug: "caps-montecarlo",
    nombre: "CAPS Montecarlo",
    direccion: "Constitución y Del Tuyú",
    jtbd: "Atención primaria y vacunación",
  },
  {
    slug: "caps-mayores60",
    nombre: "CAPS Ostende",
    nombre_sub: "Para mayores de 60",
    direccion: "Bunge y Cariló",
  },
];

const TALLERES = [
  {
    href: "",
    nombre: "Taller de espalda sana",
    jtbd: "Kinesiología grupal, todos los martes",
    jtbd_href: "/service/taller-espalda",
    notas: ["Cupo: 12 personas", "Traer ropa cómoda"],
  },
];

function mount(items) {
  const host = document.createElement("div");
  render(host, items);
  return host;
}

export const Centros = () => mount(CENTROS);
export const TallerInformativo = () => mount(TALLERES);

// Un solo centro: el tile no se estira al ancho completo de la grilla.
export const UnSoloCentro = () => mount(CENTROS.slice(0, 1));

// Nombres largos y direcciones largas envuelven a dos líneas — nunca elipsis:
// el contenido tiene que verse entero (ui-rules).
export const NombresLargos = () =>
  mount([
    {
      slug: "caps-endocrino",
      nombre: "CAPS de Endocrinología, diabetes y nutrición",
      direccion: "Av. Intendente Manuel Bunge 1250, entre Del Pejerrey y Del Tuyú, Pinamar",
      jtbd: "Control de diabetes, tiroides y seguimiento nutricional",
    },
    ...CENTROS.slice(1),
  ]);
