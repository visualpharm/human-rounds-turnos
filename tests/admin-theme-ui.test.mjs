// Source guards for the marca (color y logo) preview semantics, now owned by
// the native controller static/platform/admin-appearance.mjs — the classic
// admin-theme.js was physically retired with the 2026-09-07 direct-screen
// cutover. These assertions pin source shape only: the rendered preview and
// the save/repaint behaviour around it are pinned at runtime by
// tests/admin-appearance-lifecycle.test.mjs, which executes the real module.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const appearanceTpl = readFileSync(new URL("../templates/admin/appearance.html", import.meta.url), "utf8");
const mjs = readFileSync(new URL("../static/platform/admin-appearance.mjs", import.meta.url), "utf8");

test("Apariencia y acceso muestra el emblema municipal cuando el tema Pinamar está activo", () => {
  assert.match(mjs, /return cur === "pinamar" \? "\/pinamar-emblema\.svg" : "\/logo\.png";/);
  assert.match(mjs, /var logoSrc = logoEfectivo\(\);/);
});

test("Apariencia y acceso conserva el logo subido como la marca efectiva", () => {
  assert.match(mjs, /if \(mkLogo\) return "\/logo\.png\?t=" \+ Date\.now\(\);/);
});

test("el panel carga el controlador nativo; el clásico admin-theme.js está retirado de raíz", () => {
  assert.match(appearanceTpl, /static\('platform\/admin-appearance\.mjs'\)/);
  assert.equal(existsSync(new URL("../admin-theme.js", import.meta.url)), false,
    "the retired classic controller must not come back as a file");
});
