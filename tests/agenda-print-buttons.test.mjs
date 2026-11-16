// «Imprimir» (2026-11-07): every panel view of one doctor's day links to the
// same A4 sheet HSI prints («Turnos programados», GET /admin/cola/print).
// Pacientes de hoy is pinned in admin-cola.test.mjs and by the browser spec
// tests/e2e/admin-agenda-print.spec.mjs; this file pins the doctor's calendar
// (Médicos → Calendario) and the doctor's own «Mis turnos».
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const CONTENT = read("../static/platform/admin-content.mjs");
const MINE = read("../static/platform/admin-my-appointments.mjs");
const DIRECT_CSS = read("../static/root/admin-direct.css");

test("Médicos → Calendario: a day with turnos prints that doctor's day, beside «Crear turno»", () => {
  const fn = CONTENT.slice(CONTENT.indexOf("var create = '<button type=\"button\" class=\"btn btn-quiet btn-sm medcal-create\""));
  const block = fn.slice(0, fn.indexOf("if (hsiUrl)"));
  assert.match(block, /var medId = MEDCAL_DATA && MEDCAL_DATA\.medico && MEDCAL_DATA\.medico\.id;/);
  assert.match(block, /if \(medId && \(g\.turnos \|\| \[\]\)\.length\)/);
  assert.match(block, /href="\/admin\/cola\/print\?dia='\s*\+ esc\(encodeURIComponent\(g\.diaIso\)\) \+ '&amp;medico=' \+ esc\(encodeURIComponent\(medId\)\) \+ '&amp;auto=1" target="_blank" rel="noopener">'/);
  assert.match(block, /T\("admin\.queue\.print\.print", null, "Imprimir"\)/);
  assert.match(block, /'<\/a>' \+ create;/);
});

test("Mis turnos: each day heading prints the doctor's own day (the server ignores any other doctor)", () => {
  assert.match(MINE, /'<div class="mt-day"><div class="mt-day-head"><h2 class="mt-day-h">'/);
  assert.match(MINE, /href="\/admin\/cola\/print\?dia='\s*\+ esc\(encodeURIComponent\(g\.diaIso\)\) \+ '&amp;auto=1" target="_blank" rel="noopener">'/);
  assert.doesNotMatch(MINE, /mt-print[^\n]*medico=/);
  // Printing Mis turnos itself (the browser's own print) never prints the button.
  assert.match(DIRECT_CSS, /@media print\{\s*\.adm5-side, \.mt-cal, #mt-cal-open, \.adm-band, \.mt-print\{ display:none !important; \}/);
  assert.match(DIRECT_CSS, /#sec-mis-turnos \.mt-day-head\{display:flex;align-items:center;justify-content:space-between;/);
});
