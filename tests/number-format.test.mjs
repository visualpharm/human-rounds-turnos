// tests/number-format.test.mjs — the panel's one number formatter
// (i18n.mjs formatNumber/formatPercent, format.mjs numero/porcentaje) writes
// every case of tests/fixtures/number-format.json; the Python twin reads the
// same file in tests/test_number_format.py.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.SANA_COUNTRY_PHONE = JSON.parse(
  readFileSync(new URL("../packs/ar/country.json", import.meta.url), "utf8")
).phone;

const I18n = await import("../static/platform/i18n.mjs");
const Format = await import("../static/platform/format.mjs");
const cases = JSON.parse(readFileSync(new URL("./fixtures/number-format.json", import.meta.url), "utf8"));

test("formatNumber writes every fixture case", () => {
  for (const c of cases.number) {
    assert.equal(I18n.formatNumber(c.n, c.d, c.lang), c.out, JSON.stringify(c));
  }
});

test("formatPercent writes every fixture case", () => {
  for (const c of cases.percent) {
    assert.equal(I18n.formatPercent(c.n, c.lang), c.out, JSON.stringify(c));
  }
});

test("format.mjs numero/porcentaje are the same formatter; empty stays empty", () => {
  assert.equal(Format.numero(4188, 0), I18n.formatNumber(4188, 0));
  assert.equal(Format.porcentaje(52), I18n.formatPercent(52));
  assert.equal(I18n.formatNumber(null), "");
  assert.equal(I18n.formatPercent(undefined), "");
});

test("tn() writes its {n} grouped", () => {
  I18n.setCatalog({ "x.turnos": "{n} turnos" });
  assert.equal(I18n.tn("x.turnos", 4188, null, "{n} turno", "{n} turnos"), "4.188 turnos");
});
