// tests/dates-installation-tz.test.mjs — "Hoy"/"Mañana" se cuentan en el huso de la
// INSTALACIÓN (America/Argentina/Buenos_Aires), no con el reloj del navegador.
// Cada caso corre en un proceso hijo con su propio TZ (el huso del proceso se
// fija al arrancar): el mismo instante tiene que dar el mismo texto en una compu
// de UTC, de Tokio o de Pinamar. Correr: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const DATES = pathToFileURL(
  join(dirname(fileURLToPath(import.meta.url)), "..", "static", "platform", "dates.mjs"),
).href;

// [instante, qué función, argumentos, esperado] — el instante manda; ART = UTC-3.
const CASES = [
  // 22:30 ART del 10/07 = ya 11/07 en UTC y en Tokio.
  ["2026-07-11T01:30:00Z", "amigable", ["2026-07-10", "08:00"], "Hoy, 08:00"],
  ["2026-07-11T01:30:00Z", "amigable", ["2026-07-11", "08:00"], "Mañana, sábado 11, 08:00"],
  ["2026-07-11T01:30:00Z", "dia", ["2026-07-12"], "Domingo 12 de julio"],
  ["2026-07-11T01:30:00Z", "corta", ["2026-07-10", "09:15"], "Hoy, 09:15"],
  ["2026-07-11T01:30:00Z", "corta", ["2026-07-11", "09:15"], "Mañana, 09:15"],
  // 13:00 ART del 10/07 = 01:00 del 11/07 en Tokio.
  ["2026-07-10T16:00:00Z", "dia", ["2026-07-10"], "Hoy"],
  ["2026-07-10T16:00:00Z", "dia", ["2026-07-11"], "Mañana"],
  // 23:30 ART del 10/07 = 11:30 del 11/07 en Tokio y 02:30 en UTC.
  ["2026-07-11T02:30:00Z", "amigable", ["2026-07-10", "07:00"], "Hoy, 07:00"],
  ["2026-07-11T02:30:00Z", "amigable", ["2026-07-09", "07:00"], "Jueves 9 de julio, 07:00"],
  // El cambio de día es a las 00:00 ART (03:00 UTC), ni un minuto antes.
  ["2026-07-10T02:59:00Z", "dia", ["2026-07-09"], "Hoy"],
  ["2026-07-10T03:00:00Z", "dia", ["2026-07-10"], "Hoy"],
  ["2026-07-10T03:00:00Z", "dia", ["2026-07-09"], "Jueves 9 de julio"],
  // Año nuevo: 22:00 ART del 31/12 todavía es 2026 aunque en UTC ya sea 2027.
  ["2027-01-01T01:00:00Z", "corta", ["2027-01-01", "10:00"], "Mañana, 10:00"],
  ["2027-01-01T01:00:00Z", "corta", ["2026-12-30"], "Mié 30 dic"],
  ["2027-01-01T01:00:00Z", "larga", ["2026-12-30"], "Miércoles 30 de diciembre"],
  ["2027-01-01T01:00:00Z", "larga", ["2027-01-02"], "Sábado 2 de enero de 2027"],
];

const TZS = ["UTC", "Asia/Tokyo", "Pacific/Kiritimati", "America/Argentina/Buenos_Aires"];

function runIn(tz) {
  const script = `
    const F = await import(${JSON.stringify(DATES)});
    const cases = ${JSON.stringify(CASES)};
    console.log(JSON.stringify(cases.map(([at, fn, args]) => {
      const now = new Date(at);
      return fn === "amigable" || fn === "corta" || fn === "larga"
        ? F[fn](args[0], args[1], now)
        : F[fn](args[0], now);
    })));
  `;
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    env: { ...process.env, TZ: tz },
    encoding: "utf8",
  });
  assert.equal(r.status, 0, r.stderr);
  return JSON.parse(r.stdout);
}

for (const tz of TZS) {
  test(`Hoy/Mañana siguen el huso de la instalación con el navegador en ${tz}`, () => {
    const got = runIn(tz);
    CASES.forEach(([at, fn, args, want], i) => {
      assert.equal(got[i], want, `${fn}(${args.join(", ")}) @ ${at} (TZ=${tz})`);
    });
  });
}
