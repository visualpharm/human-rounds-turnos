/* static/platform/week.mjs — canonical weekly schedule renderer.
 *
 * Named ES-module exports only: no browser global, CommonJS wrapper, or
 * root-level twin. The module awaits its canonical Hours and I18n dependencies
 * before it can render, so a cold page never paints a Spanish/fallback grid
 * and later changes it after hydration.
 */
"use strict";

async function importDep(name, nodeUrl) {
  if (typeof window === "undefined") return import(nodeUrl);
  const url = window.SANA_STATIC && window.SANA_STATIC[name];
  if (!url) throw new Error("[static] " + name + " is not in window.SANA_STATIC");
  return import(url);
}

const [I18n, Hours] = await Promise.all([
  importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url)),
  importDep("platform/hours.mjs", new URL("./hours.mjs", import.meta.url)),
]);

function T(key, vars, fallback) { return I18n.t(key, vars, fallback); }

function activeLang() {
  const page = typeof document !== "undefined" ? document
    : (typeof window !== "undefined" ? window.document : null);
  const html = page && page.documentElement;
  return String((html && html.getAttribute("lang")) || I18n.lang || "es").toLowerCase();
}

const DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const GETDAY = [1, 2, 3, 4, 5, 6, 0];

function dayName(pos) { return T("date.day." + DAY_KEYS[pos], null, DIAS[pos]); }
function fmt(min) { return Math.floor(min / 60) + ":" + (min % 60 < 10 ? "0" : "") + (min % 60); }

function horas(list) {
  const sorted = (list || []).slice().sort((a, b) => a.start - b.start);
  if (sorted.length === 1 && sorted[0].end == null) {
    return [T("date.at_cap", null, "A las") + " " + fmt(sorted[0].start)];
  }
  return sorted.map((op) => op.end != null ? fmt(op.start) + " – " + fmt(op.end) : fmt(op.start));
}

function bloques(week) {
  const out = [];
  for (let i = 0; i < 7; i++) {
    const hs = horas((week || {})[GETDAY[i]]);
    const k = hs.join("|");
    if (out.length && out[out.length - 1].k === k) out[out.length - 1].hasta = i;
    else out.push({ k, desde: i, hasta: i, horas: hs });
  }
  return out.map((b) => {
    const esGram = activeLang() === "es";
    let endName = dayName(b.hasta);
    if (esGram) endName = endName.charAt(0).toLowerCase() + endName.slice(1);
    return {
      label: b.desde === b.hasta ? dayName(b.desde) : dayName(b.desde) + " " + T("date.to", null, "a ") + endName,
      dias: b.hasta - b.desde + 1,
      horas: b.horas,
    };
  });
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

const CSS =
  ".sem-t{margin:30px 0 10px;font-size:17px;font-weight:650;letter-spacing:-.01em;color:var(--text);}" +
  ".sem-grid{display:grid;border:1px solid var(--border);border-radius:13px;background:var(--panel);overflow:hidden;}" +
  ".sem-b{padding:14px 16px;border-left:1px solid var(--border);min-width:0;}" +
  ".sem-b:first-child{border-left:0;}" +
  ".sem-d{font-size:15.5px;font-weight:650;letter-spacing:-.01em;color:var(--text);}" +
  ".sem-hs{margin-top:5px;display:flex;flex-direction:column;gap:2px;}" +
  ".sem-h{font-size:15px;color:var(--text-2);font-variant-numeric:tabular-nums;}" +
  ".sem-h--off{color:var(--text-3);}" +
  "@container stzc-page (max-width:900px){" +
  ".sem-grid{display:flex;flex-direction:column;}" +
  ".sem-b{display:grid;grid-template-columns:minmax(120px,1fr) minmax(0,1.4fr);align-items:baseline;gap:18px;border-left:0;border-top:1px solid var(--border);padding:11px 15px;}" +
  ".sem-b:first-child{border-top:0;}.sem-hs{margin:0;text-align:left;}}" +
  "@media(max-width:640px){" +
  ".sem-grid{display:flex;flex-direction:column;}" +
  ".sem-b{display:flex;align-items:baseline;justify-content:space-between;gap:14px;border-left:0;border-top:1px solid var(--border);padding:11px 15px;}" +
  ".sem-b:first-child{border-top:0;}.sem-hs{margin:0;text-align:right;}}";

function ensureCss(doc) {
  if (doc.getElementById("sem-css")) return;
  const style = doc.createElement("style");
  style.id = "sem-css";
  style.textContent = CSS;
  doc.head.appendChild(style);
}

function render(el, parseSchedule = Hours.parse) {
  if (!el || typeof parseSchedule !== "function") return false;
  const week = parseSchedule(el.getAttribute("data-horario"));
  if (!week) return false;
  ensureCss(el.ownerDocument);
  const bs = bloques(week);
  const cols = bs.map((b) => b.dias + "fr").join(" ");
  const titulo = el.getAttribute("data-titulo");
  el.innerHTML = (titulo ? '<h2 class="sem-t">' + esc(titulo) + "</h2>" : "") +
    '<div class="sem-grid" style="grid-template-columns:' + cols + '">' +
    bs.map((b) => '<div class="sem-b"><div class="sem-d">' + esc(b.label) +
      '</div><div class="sem-hs">' + (b.horas.length
        ? '<div class="sem-h">' + esc(b.horas.join(", ")) + "</div>"
        : '<div class="sem-h sem-h--off">' + esc(T("date.closed", null, "No atiende")) + "</div>") +
      "</div></div>").join("") + "</div>";
  return true;
}

function init(root) {
  root = root || (typeof document !== "undefined" ? document : null);
  if (!root || !root.querySelectorAll) return 0;
  let rendered = 0;
  for (const el of root.querySelectorAll("[data-semana]")) if (render(el)) rendered++;
  return rendered;
}

function boot() {
  if (typeof document === "undefined") return;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => init(), { once: true });
  else init();
}

boot();

export { CSS, activeLang, bloques, init, render };
