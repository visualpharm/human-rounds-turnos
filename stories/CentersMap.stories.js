// centers-map.mjs -> render(): the home's map of the health centers (the product owner,
// 2026-11-05). Every story mounts through the REAL render() with the REAL
// tiles.mjs (the rows under the map and the fallback list), over the demo
// pack's own drawing (packs/example-town/centros-mapa.svg, a town that does not
// exist). The points are what /api/centers answers for that pack: fractions of
// the drawing. The host is returned before it is attached; render() lays the
// names out when its ResizeObserver first sees the frame's width.
import { render } from "../static/platform/centers-map.mjs";
import * as tiles from "../static/platform/tiles.mjs";

export default {
  title: "Public site/Centers map",
  parameters: { layout: "padded" },
};

const MAPA = {
  svg: "/demo-mapa.svg",
  width: 1000,
  height: 1000,
  areas: [
    { name: "Sampleton", x: 0.50359, y: 0.61567 },
    { name: "Riverside", x: 0.70724, y: 0.66628 },
    { name: "Lakeview", x: 0.37183, y: 0.21805 },
  ],
  attribution: "© OpenStreetMap",
  attribution_url: "https://www.openstreetmap.org/copyright",
};

function route(street) {
  return "https://www.google.com/maps/dir/?api=1&destination=" +
    encodeURIComponent(street + ", Sampleton, Greenfield");
}

const CENTROS = [
  { slug: "sampleton-hospital", name: "Sampleton Hospital", place: "main rd. 123", place_main: true,
    map_x: 0.51437, map_y: 0.49566, directions_url: route("Main Rd. 123"), bookable: true },
  { slug: "riverside-clinic", name: "Riverside Clinic", place: "station st. 456",
    map_x: 0.65453, map_y: 0.54772, directions_url: route("Station St. 456"), bookable: true },
  { slug: "lakeview-clinic", name: "Lakeview Clinic", place: "harbor ave. 789",
    map_x: 0.45807, map_y: 0.30769, directions_url: route("Harbor Ave. 789"), bookable: true },
  { slug: "maternity-center", name: "Maternity Center", place: "garden ave. 500",
    map_x: 0.40057, map_y: 0.69231, directions_url: route("Garden Ave. 500"), bookable: true },
  { slug: "diagnostic-center", name: "Diagnostic Center", place: "central dr. 234",
    map_x: 0.34547, map_y: 0.41903, directions_url: route("Central Dr. 234"), bookable: true },
];

// Two services in the hospital's own building: one dot, the hospital's name
// first and the services under it.
const MISMO_EDIFICIO = [
  CENTROS[0],
  { slug: "blood-bank", name: "Sampleton Hospital, Blood bank", place: "main rd. 123",
    map_x: 0.51437, map_y: 0.49566, directions_url: route("Main Rd. 123"), bookable: false },
  { slug: "oncology", name: "Sampleton Hospital, Oncology", place: "main rd. 123",
    map_x: 0.51437, map_y: 0.49566, directions_url: route("Main Rd. 123"), bookable: false },
  ...CENTROS.slice(1),
];

// A center with no fixed address (a mobile clinic) has no point: it is a row
// under the map.
const SIN_PUNTO = [
  ...CENTROS,
  { slug: "mobile-clinic", name: "Mobile clinic", address: "Goes round the neighbourhoods",
    directions_url: "", bookable: false },
];

function story(centros, mapa) {
  return () => {
    const host = document.createElement("div");
    host.style.maxWidth = "1100px";
    render(host, centros, mapa, null, tiles);
    return host;
  };
}

export const Mapa = story(CENTROS, MAPA);
export const MismoEdificio = story(MISMO_EDIFICIO, MAPA);
export const CentroSinPunto = story(SIN_PUNTO, MAPA);

// A pack without a drawing: the compact list of tiles.mjs, as before the map.
export const SinMapa = story(CENTROS, null);
