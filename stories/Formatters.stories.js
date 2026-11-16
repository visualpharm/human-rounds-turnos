// format.mjs is the ONE browser formatter for phones, names, dates
// and ages, on both the patient site and the panel (repo invariant — there is
// no second copy, and format.py is parity-tested against this file). These
// stories are the visual proof: left the raw value as it arrives from HSI,
// RENAPER or a front-desk keyboard, right what a person actually reads.
//
// Tier 5 slice 0 pilot (docs/roadmap/tier5-frontend-design.md): this story is
// the one that proves the canonical ESM module end-to-end — real named imports
// with no compatibility global and no build step.
import country from "../packs/ar/country.json";
globalThis.SANA_COUNTRY_PHONE = country.phone;
const { nombre, telTexto, telHtml, fechaCorta, edad, edadTexto } = await import("../static/platform/format.mjs");

export default {
  title: "Foundations/Formatters",
  parameters: { layout: "padded" },
};

function sheet(title, note, pairs) {
  const root = document.createElement("div");
  root.style.font = "14px/1.5 system-ui, sans-serif";
  root.style.color = "var(--text)";
  root.innerHTML =
    `<h3 style="margin:0 0 4px;font-size:16px;">${title}</h3>` +
    `<p style="margin:0 0 14px;color:var(--text-3);font-size:13px;max-width:62ch;">${note}</p>` +
    `<table style="border-collapse:collapse;width:100%;max-width:720px;">
       <thead><tr>
         <th style="text-align:left;padding:6px 12px 6px 0;font-size:12px;color:var(--text-3);font-weight:600;border-bottom:1px solid var(--border);">Como llega</th>
         <th style="text-align:left;padding:6px 0;font-size:12px;color:var(--text-3);font-weight:600;border-bottom:1px solid var(--border);">Como se muestra</th>
       </tr></thead>
       <tbody>${pairs.map(([raw, out]) => `
         <tr>
           <td style="padding:7px 12px 7px 0;border-bottom:1px solid var(--border);font-family:ui-monospace,monospace;font-size:12.5px;color:var(--text-3);">${raw}</td>
           <td style="padding:7px 0;border-bottom:1px solid var(--border);">${out}</td>
         </tr>`).join("")}
       </tbody>
     </table>`;
  return root;
}

// HSI y el teclado del mostrador entregan nombres en mayúsculas; un nombre ya
// escrito en capitalización normal se deja exactamente como está.
export const NombresDePersonas = () =>
  sheet(
    "Nombres",
    "Un nombre nunca se grita. Sólo se reescribe si viene TODO en mayúsculas o todo en minúsculas; uno ya mixto (Hugo Montes) se respeta tal cual fue tipeado.",
    [
      ["ALEJO VARELA", nombre("ALEJO VARELA")],
      ["maría laura sangiorgi", nombre("maría laura sangiorgi")],
      ["Hugo Montes", nombre("Hugo Montes")],
      ["ANA-MARÍA O'BRIEN", nombre("ANA-MARÍA O'BRIEN")],
      ["DR. JUAN GONZÁLEZ", nombre("DR. JUAN GONZÁLEZ")],
      ["JOSÉ DE LA TORRE", nombre("JOSÉ DE LA TORRE")],
    ]
  );

// Un teléfono argentino puede llegar con o sin 0, con o sin 15, con +54 9 o
// pelado: todos los caminos terminan en el mismo texto.
export const Telefonos = () =>
  sheet(
    "Teléfonos",
    "Todas estas formas de escribir el mismo número terminan en un único texto legible. El 9 de móvil y el 15 son la misma cosa dicha de dos maneras.",
    [
      ["+5492254123456", telTexto("+5492254123456")],
      ["02254 15 123456", telTexto("02254 15 123456")],
      ["2254123456", telTexto("2254123456")],
      ["011 4321-8765", telTexto("011 4321-8765")],
      ["0800-333-1234", telTexto("0800-333-1234")],
      ["107", telTexto("107")],
    ]
  );

export const TelefonoComoLinkYWhatsApp = () =>
  sheet(
    "Teléfono clickeable",
    "El mismo número, ya como control: llamar, y WhatsApp cuando el número es un móvil.",
    [
      ["+5492254123456", telHtml("+5492254123456", "")],
      ["02254 15 123456", telHtml("02254 15 123456", "")],
      ["011 4321-8765", telHtml("011 4321-8765", "")],
    ]
  );

export const FechasYEdades = () =>
  sheet(
    "Fechas y edades",
    "Una fecha que lee una persona nunca sale en ISO. Un bebé se cuenta en meses hasta que la cifra en años deja de ser útil.",
    [
      ["2026-08-04", fechaCorta("2026-08-04")],
      ["1985-03-17", fechaCorta("1985-03-17")],
      ["1985-03-17 → edad", String(edad("1985-03-17", "2026-08-04"))],
      ["2026-05-20 → edad", edadTexto("2026-05-20", "2026-08-04")],
      ["2025-11-02 → edad", edadTexto("2025-11-02", "2026-08-04")],
    ]
  );
