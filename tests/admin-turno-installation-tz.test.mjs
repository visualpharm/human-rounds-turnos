// tests/admin-turno-installation-tz.test.mjs — the appointment time the panel SENDS is
// the installation's wall clock with the installation's UTC offset
// (America/Argentina/Buenos_Aires), never the browser's. The agenda offers
// installation times ("07:00"); a browser in UTC used to send "07:00+00:00",
// i.e. 04:00 in Pinamar, and approve answered 409 turno_medico_fuera_de_agenda
// (admin-forms-audit-desk.spec.mjs in a UTC container). Each case runs in a
// child process with its own TZ (the process zone is fixed at start), like the
// "Hoy/Mañana" cases of dates-installation-tz.test.mjs. Run: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DATES = pathToFileURL(join(HERE, "..", "static", "platform", "dates.mjs")).href;
const RUNTIME = pathToFileURL(join(HERE, "lineage", "runtime.mjs")).href;

const TZS = ["UTC", "Europe/Madrid", "Asia/Tokyo", "America/Argentina/Buenos_Aires"];

function runIn(tz) {
  const script = `
    const Dates = await import(${JSON.stringify(DATES)});
    const { runtime } = await import(${JSON.stringify(RUNTIME)});
    const app = runtime("static/root/admin-app.js", ["toIsoWithOffset"], { Dates });
    // A Friday in the installation: the next weekday is Monday, whatever the browser's day.
    const friday = runtime("static/root/admin-app.js", ["nextWeekday9"], {
      Dates: { ...Dates, hoyIsoInstalacion: () => "2026-10-30" } });
    console.log(JSON.stringify({
      approve: app.toIsoWithOffset("2026-10-26T07:00"),
      seconds: app.toIsoWithOffset("2026-10-26T13:45:30"),
      empty: app.toIsoWithOffset(""),
      odd: app.toIsoWithOffset("pronto"),
      direct: Dates.conHusoInstalacion("2026-12-31T23:30"),
      today: Dates.hoyIsoInstalacion(new Date("2026-10-27T01:30:00Z")),
      todayAfter: Dates.hoyIsoInstalacion(new Date("2026-10-27T03:00:00Z")),
      nextWeekday: friday.nextWeekday9(),
    }));
  `;
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    env: { ...process.env, TZ: tz },
    encoding: "utf8",
  });
  assert.equal(r.status, 0, r.stderr);
  return JSON.parse(r.stdout);
}

for (const tz of TZS) {
  test(`the panel sends installation times with the installation offset (browser in ${tz})`, () => {
    assert.deepEqual(runIn(tz), {
      approve: "2026-10-26T07:00:00-03:00",
      seconds: "2026-10-26T13:45:30-03:00",
      empty: "",
      odd: "pronto",
      direct: "2026-12-31T23:30:00-03:00",
      // 22:30 in Pinamar on the 26th is already the 27th in UTC, Madrid and Tokyo.
      today: "2026-10-26",
      todayAfter: "2026-10-27",
      nextWeekday: "2026-11-02T09:00",
    });
  });
}
