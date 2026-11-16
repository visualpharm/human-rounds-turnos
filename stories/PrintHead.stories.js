// The head of every printed sheet: the real module (static/platform/print-head.mjs)
// reads a sample panel screen (its title, a filter left on a value) and writes the
// line the paper carries in its top margin. Shown on screen under the sample, as
// the sheet would print it.
import * as PrintHead from "../static/platform/print-head.mjs";

export default {
  title: "Admin/Print head",
  parameters: { layout: "fullscreen" },
};

const NOW = new Date("2026-11-07T18:50:00Z");

function mount(filterHtml) {
  const host = document.createElement("div");
  host.className = "adm5-main";
  host.style.padding = "24px";
  host.innerHTML = '<h1 id="adm-h1">Solicitudes</h1>' + filterHtml
    + '<p style="margin-top:24px;font:14px/1.4 system-ui;color:#222"></p>';
  // Measured once the sample is in the page: screenName/filters read what is shown.
  requestAnimationFrame(() => {
    host.querySelector("p").textContent = PrintHead.headText(host.ownerDocument, NOW);
  });
  return host;
}

export const NoFilter = () => mount("");

export const WithFilter = () => mount('<span data-print-filter>Pendientes</span>');
