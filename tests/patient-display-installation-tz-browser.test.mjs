// tests/patient-display-installation-tz-browser.test.mjs — «hoy» y «abierto ahora» que lee
// una persona son los de la INSTALACIÓN (America/Argentina/Buenos_Aires), no los del
// navegador: la grilla mensual (calendar-grid.mjs, el recuadro de hoy), el estado de un
// horario (hours.mjs estado/frase) y los días de espera de un listado (listing.mjs, la
// pastilla de turno y «Abre mañana»). Cada caso corre en una página real de Chromium con
// SU huso (UTC, Madrid, Tokio, Buenos Aires) y el reloj congelado ANTES del goto
// (page.clock.setFixedTime): a las 01:30 UTC ya es martes en UTC, Madrid y Tokio pero en
// Pinamar todavía es lunes 22:30; y a las 15:00 UTC es lunes 12:00 en Pinamar pero ya
// martes 00:00 en Tokio. Sin servidor ni base. Correr: node --test tests/
import { test, ceil, hookOptions } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { chromium } from "playwright";

const root = new URL("../", import.meta.url);
const modules = new Map(readdirSync(new URL("static/platform/", root))
  .filter((name) => name.endsWith(".mjs"))
  .map((name) => ["/static/platform/" + name,
    readFileSync(new URL("static/platform/" + name, root), "utf8")]));
const manifest = Object.fromEntries([...modules.keys()].map((p) => [p.slice("/static/".length), p]));

const TZS = ["UTC", "Europe/Madrid", "Asia/Tokyo", "America/Argentina/Buenos_Aires"];
const HORARIO = "Lunes a viernes de 8 a 13 h";

// Lunes 26/10/2026 en Pinamar. `abierto`: el cartel «de 8 a 13 h» ¿atiende en ese instante?
const CLOCKS = [
  { at: "2026-10-27T01:30:00Z", hoy: "2026-10-26", abierto: false,
    estado: { dia: 2, min: 480, off: 1, proxima: "ma 8:00" },
    frase: "Atiende mañana, 08:00", espera: "lst-time--w1", walkin: "Abre mañana, 08:00" },
  { at: "2026-10-26T15:00:00Z", hoy: "2026-10-26", abierto: true,
    estado: { hasta: 780 }, frase: "Atiende ahora · hasta las 13:00",
    espera: "lst-time--w1", walkin: "Atiende ahora hasta las 13:00" },
];

const HTML = `<!doctype html><html lang="es"><head><meta charset="utf-8">
  <script>window.SANA_STATIC=${JSON.stringify(manifest)};window.SANA_I18N={lang:'es',catalog:{}};</script>
  </head><body><div id="host" data-listado></div></body></html>`;

let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

async function enPagina(tz, at, fn, arg) {
  const context = await browser.newContext({ timezoneId: tz, locale: "es-AR" });
  try {
    const page = await context.newPage();
    ceil(page);
    await page.clock.setFixedTime(at);
    const errores = [];
    page.on("pageerror", (e) => errores.push(e.message));
    await page.route("**/*", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (modules.has(path)) return route.fulfill({ contentType: "text/javascript", body: modules.get(path) });
      if (path === "/") return route.fulfill({ contentType: "text/html", body: HTML });
      return route.fulfill({ status: 404, body: "fuera del fixture" });
    });
    await page.goto("https://tz.test/");
    const out = await page.evaluate(fn, arg);
    assert.deepEqual(errores, []);
    return out;
  } finally {
    await context.close();
  }
}

for (const tz of TZS) {
  for (const c of CLOCKS) {
    test(`${tz} @ ${c.at}: grilla, horario y listado leen el reloj de la instalación`, async () => {
      const r = await enPagina(tz, c.at, async (horario) => {
        const Grid = await import("/static/platform/calendar-grid.mjs");
        const Hours = await import("/static/platform/hours.mjs");
        const L = await import("/static/platform/listing.mjs");
        const dias = [{ dia: "2026-10-25" }, { dia: "2026-10-26" }, { dia: "2026-10-27" }];
        const box = document.createElement("div");
        box.innerHTML = Grid.gridHtml(dias);
        const hoyGrid = [...box.querySelectorAll(".medcal-hoy")].map((b) => b.getAttribute("data-calendar-date"));
        box.innerHTML = Grid.gridHtml(dias, { today: "2026-10-27" });
        const hoyExplicito = [...box.querySelectorAll(".medcal-hoy")].map((b) => b.getAttribute("data-calendar-date"));
        const e = Hours.estado(horario);
        L.render(document.getElementById("host"), {
          cols: ["medico", "proximo"],
          rows: [
            { nombre: "Con turno", href: "/doctor/a", proximo: { dia: "2026-10-28", hora: "09:00" } },
            { nombre: "Walk-in", href: "/doctor/b", proximo: { horario } },
          ],
          options: { sort: false, search: false },
        });
        const trs = [...document.querySelectorAll("tbody tr")];
        const turno = trs.find((tr) => tr.textContent.includes("Con turno"));
        const walkin = trs.find((tr) => tr.textContent.includes("Walk-in"));
        return {
          hoyGrid, hoyExplicito, e, frase: Hours.frase(e),
          // la misma consulta con un instante explícito (lo que pasan los tests y los callers)
          explicito: Hours.estado(horario, new Date("2026-10-27T01:30:00Z")),
          espera: turno.querySelector(".lst-time").className,
          walkin: walkin.querySelector("td:last-child").textContent.replace(/\s+/g, " ").trim(),
        };
      }, HORARIO);

      assert.deepEqual(r.hoyGrid, [c.hoy], "el recuadro de hoy de la grilla");
      assert.deepEqual(r.hoyExplicito, ["2026-10-27"], "options.today explícito manda");
      assert.equal(r.e.abierto, c.abierto, "abierto ahora");
      for (const [k, v] of Object.entries(c.estado)) assert.equal(r.e[k], v, `estado.${k}`);
      assert.equal(r.frase, c.frase);
      assert.equal(r.explicito.abierto, false);
      assert.equal(r.explicito.off, 1);
      assert.match(r.espera, new RegExp(c.espera), "pastilla de espera del listado");
      assert.ok(r.walkin.includes(c.walkin), `walk-in «${r.walkin}» debe incluir «${c.walkin}»`);
    });
  }
}
