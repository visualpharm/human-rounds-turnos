// Qué es la captura de síntomas, contado al médico (responsable del producto, 2026-10-27):
// un solo bloque (static/platform/intake-explainer.mjs) en los tres lugares
// donde se enciende, sólo con hechos del inventario, y el aviso de
// traducciones de la IA sólo en la pestaña del médico que tiene traducciones.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as Explainer from "../static/platform/intake-explainer.mjs";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");
const SRC = read("static/platform/intake-explainer.mjs");
const ES = JSON.parse(read("locales/es/admin.intake.json"));
const EN = JSON.parse(read("locales/en/admin.intake.json"));
const fallback = (key, vars, text) => text;

test("el bloque muestra qué hace, qué gana el médico, un ejemplo y los datos", () => {
  const html = Explainer.html(fallback, { action: '<button class="btn btn-ok" data-x>Activar</button>' });
  assert.match(html, /data-intake-explainer/);
  assert.match(html, /<div class="ix-action"><button class="btn btn-ok" data-x>Activar<\/button><\/div>/);
  assert.match(html, /Antes del turno, el paciente cuenta por chat qué le pasa\./);
  assert.match(html, /La propuesta no es una indicación médica: decidís vos\./);
  // El ejemplo: lo que contó, diagnósticos posibles, estudios y medicación
  // con tilde, e Imprimir; sin porcentajes ni envíos que el sistema no hace.
  assert.match(html, /<div class="ix-card" inert>/);
  for (const h of ["Qué le pasa", "Diagnósticos posibles", "Estudios", "Medicación"]) assert.match(html, new RegExp("<h4>" + h + "</h4>"));
  assert.match(html, /<input type="checkbox" checked><span>Bronquitis aguda<\/span>/);
  assert.match(html, />Imprimir<\/button>/);
  assert.doesNotMatch(html, /WhatsApp|matrícula|\d+ %/);
  assert.match(html, /<h3>Qué ganás<\/h3>/);
  assert.match(html, /<h3>Cómo funciona<\/h3>/);
  assert.match(html, /No diagnostica ni aconseja\./);
  assert.match(html, /hasta unas 8 preguntas/);
  assert.match(html, /En la puerta son 4 o 5\./);
  assert.match(html, /nunca bajarla/);
  // El ejemplo usa el mismo chip de prioridad que «Pacientes de hoy».
  assert.match(html, /<span class="queue-triage queue-triage--amarillo">Amarillo<\/span>/);
  assert.match(html, /<a href="\/privacy\.html"[^>]*>Política de privacidad<\/a>/);
  assert.match(Explainer.html(fallback), /<div class="ix-action"><\/div>/);
  const folded = Explainer.details(fallback, { className: "x-y", open: true });
  assert.match(folded, /^<details class="ix-details x-y" open><summary>Qué es la captura de síntomas<\/summary><div class="ix"/);
  assert.doesNotMatch(Explainer.details(fallback), / open>/);
});

test("cada texto tiene su clave con el mismo castellano, y en inglés", () => {
  const used = [...SRC.matchAll(/T\("(admin\.intake\.explainer\.[a-z0-9_]+)", null, "([^"]*)"\)/g)];
  assert.ok(used.length >= 30, "todas las cadenas pasan por T()");
  assert.deepEqual([...new Set(used.map((m) => m[1]))].sort(), Object.keys(ES).sort());
  for (const [, key, text] of used) assert.equal(ES[key], text, key);
  assert.deepEqual(Object.keys(EN).sort(), Object.keys(ES).sort());
  assert.doesNotMatch(SRC, /T\("admin\.intake\.explainer\." \+/, "claves enteras, nunca armadas");
});

test("sólo hechos: sin mejoras medidas, sin rayas, sin letra chica", () => {
  for (const text of Object.values(ES)) {
    assert.doesNotMatch(text, /%|ahorr|reduc|más rápid/i, text);
    assert.doesNotMatch(text, /—/, text);
  }
  assert.doesNotMatch(Explainer.css, /font-size:1[0-5]px/);
  assert.doesNotMatch(Explainer.css, /text-3|text-2|#9|#a|#b|#c/i);
});

test("los tres lugares que encienden la captura pintan el mismo bloque", () => {
  const consumers = ["static/platform/admin-content.mjs", "app/modules/intake/admin-entrevistas.mjs", "static/platform/admin-queue.mjs"];
  for (const file of consumers) {
    const src = read(file);
    assert.match(src, /"platform\/intake-explainer\.mjs"/, file);
    assert.match(src, /Explainer\.(html|details)\(T/, file);
    assert.doesNotMatch(src, /admin\.intake\.explainer\./, file + ": el texto vive sólo en el bloque");
  }
  assert.match(read("app/core/static_fingerprint.py"), /"platform\/intake-explainer\.mjs"/);
});

test("la pestaña Síntomas tiene un solo botón: Activar lleno, o Activa + Desactivar", () => {
  const src = read("static/platform/admin-content.mjs");
  const fn = /function renderMedicoIntake[\s\S]*?\n  \}\n/.exec(src)[0];
  assert.match(fn, /class="btn btn-ok" data-med-intake-enabled="1"/);
  assert.match(fn, /class="btn btn-quiet" data-med-intake-enabled="0"/);
  assert.doesNotMatch(fn, /type="checkbox"/);
  assert.match(src, /override: toggle\.getAttribute\("data-med-intake-enabled"\) === "1"/);
});

// 2026-11-08: «¿Qué es?» is an icon in the switch's own line; it opens the
// same block under that line, on or off, closed by default.
test("Pacientes de hoy: el ícono «¿Qué es?» de la línea del interruptor abre el bloque debajo", () => {
  const src = read("static/platform/admin-queue.mjs");
  const fn = /function entrevistaExplainerHtml\(\)[\s\S]*?\n  \}\n/.exec(src)[0];
  assert.match(fn, /window\.CURRENT_ROLE !== "medico" \|\| !cfg \|\| !EXPLAINER_OPEN \|\| !Explainer\) return "";/);
  assert.match(fn, /'<div class="queue-intake-explainer">' \+ Explainer\.html\(T\) \+ "<\/div>"/);
  assert.match(src, /var EXPLAINER_OPEN = false;/);
  const head = /function entrevistaHeaderHtml\(\)[\s\S]*?\n  \}\n/.exec(src)[0];
  assert.match(head, /var queEs = Explainer \? Explainer\.summary\(T\) : "";/);
  assert.match(head, /data-cola-explainer="1" aria-expanded="' \+ EXPLAINER_OPEN \+ '" aria-label="' \+ esc\(queEs\)/);
  assert.match(src, /entrevistaHeaderHtml\(\) \+ entrevistaExplainerHtml\(\) \+ avisoIAHtml\(\) \+ entrevistasSinTurnoHtml\(\);/);
  assert.equal(Explainer.summary((k, v, fallback) => fallback), "Qué es la captura de síntomas");
});

test("Sistema: el bloque se pinta una vez arriba de la tabla, abierto si nadie la tiene activa", () => {
  const src = read("app/modules/intake/admin-entrevistas.mjs");
  assert.match(src, /function renderExplainer\(\)/);
  assert.match(src, /host\.firstChild\) return;/);
  assert.match(src, /Explainer\.details\(T, \{ className: "ent-explainer", open: !anyOn \}\)/);
  assert.match(read("app/modules/intake/admin_entrevistas.html"), /<div id="ent-explainer"><\/div>\s*<section id="sec-entrevistas"/);
});

test("el aviso de traducciones de la IA sólo acompaña la pestaña con traducciones del médico", () => {
  // 2026-11-08 B5: no band over the screen; the caption above «Guardar»,
  // shown on «Información básica» alone (the one tab with a translated field).
  assert.doesNotMatch(read("templates/admin/content.html"), /data-ai-notice="translation"/);
  const src = read("static/platform/admin-content.mjs");
  assert.match(src, /class="adm-ai-notice cnt-ai-notice" data-ai-notice-inline="translation"/);
  assert.match(src, /var aviso = \$\(":scope > \.cnt-ai-notice", sec\);\s*if \(aviso\) aviso\.hidden = name !== "info";/);
});
