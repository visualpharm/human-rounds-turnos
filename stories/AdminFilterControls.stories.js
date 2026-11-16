import { select, fill, uniqueOptions } from "../static/platform/admin-filter-controls.mjs";
import "../static/root/admin-direct.css";

export default { title: "Admin/Filter controls", parameters: { layout: "padded" } };

const rows = [
  { centro: "norte", nombre: "Centro Norte" },
  { centro: "sur", nombre: "Centro Sur" },
  { centro: "norte", nombre: "Centro Norte" },
];

export const Seleccion = () => {
  const host = document.createElement("div");
  host.innerHTML = select({
    label: "Centro", className: "adm-sel", value: "sur",
    options: [{ value: "", label: "Todos los centros" }, ...uniqueOptions(rows, (row) => row.centro, (row) => row.nombre)],
  });
  return host;
};

export const ActualizacionDeOpciones = () => {
  const host = document.createElement("div");
  host.innerHTML = select({ label: "Centro", value: "norte", options: [{ value: "", label: "Todos los centros" }] });
  fill(host.querySelector("select"), uniqueOptions(rows, (row) => row.centro, (row) => row.nombre), "norte");
  return host;
};
