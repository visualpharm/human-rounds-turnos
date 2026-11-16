// tests/admin-display-installation-tz.test.mjs — what the panel SHOWS as a time, «today» or
// this month is the installation's (America/Argentina/Buenos_Aires), never the browser's:
// the request list's appointment and «received» times (admin-app.js fmtTurno/fmtDt/
// fmtCreatedShort), the «Hoy: N» of «Mis turnos» (it took today from toISOString(), i.e.
// UTC, so from 21:00 to 24:00 in Pinamar it counted tomorrow), the desk calendar's today
// and the reports' months. Each case runs in a child process with its own TZ and a clock
// frozen at 22:00 in Pinamar. Run: node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DATES = pathToFileURL(join(HERE, "..", "static", "platform", "dates.mjs")).href;
const RUNTIME = pathToFileURL(join(HERE, "lineage", "runtime.mjs")).href;

const TZS = ["UTC", "Europe/Madrid", "Asia/Tokyo", "America/Argentina/Buenos_Aires"];

function runIn(tz, at) {
  const script = `
    const FIXED = Date.parse(${JSON.stringify(at)});
    const RealDate = Date;
    globalThis.Date = class extends RealDate {
      constructor(...a) { if (a.length === 0) super(FIXED); else super(...a); }
      static now() { return FIXED; }
    };
    const Dates = await import(${JSON.stringify(DATES)});
    const { runtime } = await import(${JSON.stringify(RUNTIME)});
    // The VM context has its own Date: hand it the frozen one.
    const app = runtime("static/root/admin-app.js", ["paredDe", "fmtDt", "fmtCreatedShort", "fmtTurno"], { Dates, Date });
    const mine = runtime("static/platform/admin-my-appointments.mjs", ["hoyIsoTurnos"], { Dates, Date });
    const desk = runtime("static/platform/admin-create-appointment.mjs", ["hoyISO"], { Dates, Date });
    const reports = runtime("static/platform/admin-reports.mjs", ["mesInstalacion"], { Date });
    console.log(JSON.stringify({
      wallZ: Dates.paredInstalacion("2026-10-27T00:30:00Z"),
      wallOffset: Dates.paredInstalacion("2026-10-26T21:30:00-03:00"),
      wallLocal: Dates.paredInstalacion("2026-10-26T21:30"),
      turno: app.fmtTurno("2026-10-27T00:30:00+00:00"),
      turnoLocal: app.fmtTurno("2026-10-26T21:30"),
      dt: app.fmtDt("2026-10-27T00:30:00+00:00"),
      created: app.fmtCreatedShort("2026-10-26 22:10:05.123+00"),
      odd: app.fmtTurno("pronto"),
      hoyTurnos: mine.hoyIsoTurnos(),
      hoyMesa: desk.hoyISO(),
      mes: reports.mesInstalacion(),
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
  test(`times, «Hoy» and the month are the installation's at 22:00 in Pinamar (browser in ${tz})`, () => {
    // 2026-10-27T01:00Z = 22:00 of Monday 26 October in Pinamar, already the 27th in UTC.
    assert.deepEqual(runIn(tz, "2026-10-27T01:00:00Z"), {
      wallZ: { dia: "2026-10-26", hora: "21:30" },
      wallOffset: { dia: "2026-10-26", hora: "21:30" },
      wallLocal: { dia: "2026-10-26", hora: "21:30" },
      turno: "Hoy, 21:30",
      turnoLocal: "Hoy, 21:30",
      dt: "26/10/2026 21:30",
      created: "Hoy, 19:10",
      odd: "pronto",
      hoyTurnos: "2026-10-26",
      hoyMesa: "2026-10-26",
      mes: { y: 2026, m: 9 },
    });
  });
}

test("the month turns at midnight in Pinamar, not in the browser (browser in UTC and Tokyo)", () => {
  for (const tz of ["UTC", "Asia/Tokyo"]) {
    // 23:30 of 31 October in Pinamar = 1 November in UTC and in Tokyo.
    assert.deepEqual(runIn(tz, "2026-11-01T02:30:00Z").mes, { y: 2026, m: 9 }, tz);
    assert.deepEqual(runIn(tz, "2026-11-01T03:00:00Z").mes, { y: 2026, m: 10 }, tz);
  }
});
