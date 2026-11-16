// admin-api-errors.test.mjs — a failed write in the panel never shows the
// server's machine code (TASK-48, 2026-10-26). The AI-persona run on the demo
// saw «demo_solo_lectura» as a toast in the day queue, «Crear turno» and the
// consultation, and «Error de red.» right after the correct toast when
// approving in Solicitudes. One translation (i18n.mjs apiError) owns what a
// staff member reads for a failed call; every panel screen goes through it.
import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { setCatalog, apiError } from "../static/platform/i18n.mjs";

const DEMO = "Demo de solo lectura: esta acción está deshabilitada. Podés seguir recorriendo las pantallas.";

describe("apiError: what a staff member reads for a failed call", { concurrency: false }, () => {
  test("the demo's read-only refusal is one human sentence, from any shape", () => {
    setCatalog({});
    assert.equal(apiError("demo_solo_lectura", "No se pudo guardar."), DEMO);
    assert.equal(apiError({ error: "demo_solo_lectura" }, "No se pudo guardar."), DEMO);
    assert.equal(apiError(new Error("demo_solo_lectura"), "No se pudo guardar."), DEMO);
  });

  test("the translated catalog wins over the Spanish literal", () => {
    setCatalog({ "admin.shell.toast.demo_read_only": "Read-only demo." });
    assert.equal(apiError("demo_solo_lectura", "x"), "Read-only demo.");
    setCatalog({});
  });

  test("any other machine code falls back to the screen's own sentence", () => {
    setCatalog({});
    for (const code of ["rol_no_autorizado", "no_cancelable", "401", "stale", "error", "", null, undefined]) {
      assert.equal(apiError(code, "No se pudo guardar."), "No se pudo guardar.", String(code));
    }
  });

  test("a sentence the server wrote for people passes through", () => {
    assert.equal(apiError("Ya existe un paciente con ese DNI.", "x"), "Ya existe un paciente con ese DNI.");
    assert.equal(apiError({ error: "Ese horario ya no está libre." }, "x"), "Ese horario ya no está libre.");
  });

  test("a browser exception is not shown as if the server said it", () => {
    assert.equal(apiError(new TypeError("Failed to fetch"), "No se pudo guardar."), "No se pudo guardar.");
    assert.equal(apiError(new SyntaxError("Unexpected token < in JSON"), "No se pudo guardar."), "No se pudo guardar.");
  });
});

// Every place a panel screen turns a failed response into text. A raw
// `body.error || fallback` shows whatever code the server sent — the defect
// above. Each of these files reads it through apiError instead.
const PANEL = [
  "static/root/admin-app.js",
  ...readdirSync(new URL("../static/platform/", import.meta.url))
    .filter((f) => /^admin-.*\.mjs$/.test(f) || f === "doctor-workspace.mjs")
    .map((f) => "static/platform/" + f),
  ...readdirSync(new URL("../app/modules/intake/", import.meta.url))
    .filter((f) => /^admin-.*\.mjs$/.test(f))
    .map((f) => "app/modules/intake/" + f),
];
// `new Error(body.error || …)` keeps the code on purpose — logic downstream
// matches on it — so what is checked is where that message reaches the screen.
const RAW = /\.error\s*(\|\||\?\?)|\.error\)\s*(\|\||\?\?)|\b(err|e|e2|error)\.message\s*\|\||error\.message\s*\?\s*error\.message|(toast|notify|mensaje|textContent\s*=)\s*\(?\s*(err|e|e2|error)\.message\s*\)?\s*[;)]/;

describe("no panel screen prints a raw error code", () => {
  for (const file of PANEL) {
    test(file, () => {
      const lines = readFileSync(new URL("../" + file, import.meta.url), "utf8").split("\n");
      const raw = lines
        .map((line, i) => [i + 1, line])
        .filter(([, line]) => RAW.test(line) && !/apiError|errText|new Error\(/.test(line));
      assert.deepEqual(raw.map(([n, l]) => n + ": " + l.trim()), []);
    });
  }

  // Demo 2026-10-26: the paths TASK-48 missed printed a fixed sentence (or the
  // raw code) without reading what the server said. Each now reads it.
  test("helpers that turn a failed body into text go through apiError", () => {
    const src = (f) => readFileSync(new URL("../" + f, import.meta.url), "utf8");
    const between = (text, from, to) => text.slice(text.indexOf(from), text.indexOf(to, text.indexOf(from)));
    for (const [file, from, to] of [
      ["static/platform/admin-account.mjs", "function errText(j, fallback)", "\n  }"],
      ["static/platform/admin-appearance.mjs", "function errText(res, baseKey", "\n  }"],
      ["static/platform/admin-preparations.mjs", "function mutationError(body)", "\n  }"],
      ["static/platform/doctor-workspace.mjs", "const message = body?.detalle", "return body;"],
      ["static/platform/admin-usability.mjs", "function saveError(err)", "\n  }"],
    ]) {
      assert.match(between(src(file), from, to), /apiError|errText\(/, file);
    }
    for (const [file, needle] of [
      ["static/platform/admin-alerts.mjs", 'err.message === "demo_solo_lectura"'],
      ["static/platform/admin-patient-summary.mjs", 'res.j.error === "demo_solo_lectura"'],
      ["static/platform/admin-knowledge.mjs", 'err.message === "demo_solo_lectura"'],
      ["static/platform/admin-create-appointment.mjs", 'r.j.error === "demo_solo_lectura"'],
      ["static/platform/admin-settings.mjs", "errText(err, T(\"backup.key_error\""],
      ["static/platform/admin-users.mjs", 'throw new Error(body?.error === "demo_solo_lectura"'],    ]) {
      assert.ok(src(file).includes(needle), file + ": " + needle);
    }
  });

  test("a passkey refusal on the demo reads as the demo, never as its code", async () => {
    setCatalog({});
    const { mensaje } = await import("../static/platform/passkeys.mjs");
    assert.equal(mensaje("demo_solo_lectura"), DEMO);
    assert.equal(mensaje("algo_desconocido"), "No se pudo usar la passkey.");
    assert.equal(mensaje("La llave venció."), "La llave venció.");
  });

  test("a bulk action on the demo stops at the first refusal and does not repeat it", () => {
    const src = readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
    for (const fn of ["async function runBulk(", "async function runBulkSugerencia("]) {
      const body = src.slice(src.indexOf(fn), src.indexOf("\n}\n", src.indexOf(fn)));
      assert.match(body, /=== ?'demo_solo_lectura'\)\{ demo=true; break; \}/, fn);
      assert.match(body, /if\(demo && /, fn);
    }
  });

  test("admin-app.js: a demo refusal is not followed by «Error de red.»", () => {
    const src = readFileSync(new URL("../static/root/admin-app.js", import.meta.url), "utf8");
    const red = src.split("\n").filter((l) => l.includes("error_de_red"));
    assert.ok(red.length > 0);
    for (const line of red) assert.match(line, /yaAvisado\(|apiError|errText/, line.trim());
  });
});
