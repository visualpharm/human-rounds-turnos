// What symptom intake is, told to the doctor: the real render module
// (static/platform/intake-explainer.mjs) with the doctor tab's button in both
// states. Spanish fallbacks stand in for the catalog, as on first paint.
import gateCss from "../static/root/admin-gate.css?raw";
import directCss from "../static/root/admin-direct.css?raw";
import * as Explainer from "../static/platform/intake-explainer.mjs";

export default {
  title: "Admin/Symptom intake explainer",
  parameters: { layout: "fullscreen" },
};

function T(key, vars, fallback) { return fallback || key; }

function mount(action) {
  const host = document.createElement("div");
  host.className = "adm5-main";
  host.style.padding = "24px";
  host.innerHTML = "<style>" + gateCss + directCss + Explainer.css + "</style>"
    + '<div class="med-intake"><h2>Captura de síntomas</h2>' + Explainer.html(T, { action }) + "</div>";
  return host;
}

export const Off = () => mount('<button type="button" class="btn btn-ok">Activar captura de síntomas</button>');

export const On = () => mount('<strong>Activa</strong><button type="button" class="btn btn-quiet">Desactivar</button>'
  + '<a href="#poster">Cartel imprimible con QR</a>');
