// Guardas de fuente para la campanita de triage rojo (admin-alerts.js,
// 2026-08-01): no es una sección con hash propio (vive en .adm-h1row en
// TODAS las pantallas), así que estos tests no siguen el patrón de
// admin-section-wiring-guard — cubren el polling liviano, la visibilidad
// por rol, y que wa.me/tel se arman con el módulo Format (nunca un formateador
// nuevo).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const jsPath = join(ROOT, 'static/platform/admin-alerts.mjs');
const read = (name) => readFileSync(join(ROOT, name), 'utf8');

test('native alerts exists, classic source is retired and the shared shell imports it', () => {
  assert.ok(existsSync(jsPath));
  assert.equal(existsSync(join(ROOT, 'admin-alerts.js')), false);
  const base = read('templates/admin/base.html');
  assert.match(base, /static\('platform\/admin-alerts\.mjs'\)/);
});

test('the direct shell exclusively owns the bell with its server-verified role', () => {
  const base = read('templates/admin/base.html');
  assert.match(base, /const role = \{\{ screen_rol\(\)\|tojson \}\}/);
  assert.match(base, /mod\.mountAlerts\(\{\s*root: document, role\s*\}\)/);
  const app = read('static/root/admin-app.js');
  assert.doesNotMatch(base + app, /sanaInitAlerts/);
});

test('bell is scoped to admin + medico only, and tears itself down otherwise', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /export function mountAlerts\(options\)/);
  assert.match(js, /role !== "admin" && role !== "medico"/);
  assert.match(js, /clearInterval\(pollTimer\)/);
});

test('polls the cheap count endpoint every ~60s, not the full list', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /POLL_MS = 60000/);
  assert.match(js, /setInterval\(poll, POLL_MS\)/);
  const pollFn = /function poll\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(pollFn, 'poll debe existir');
  assert.match(pollFn[0], /\/api\/admin\/intake\/alerts\/count/);
  assert.doesNotMatch(pollFn[0], /\/api\/admin\/intake\/alerts["']\)/, 'el poll no debe pedir la lista completa');
});

test('the full list is fetched only when the panel opens', () => {
  const js = readFileSync(jsPath, 'utf8');
  const abrir = /function abrirPanel\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(abrir, 'abrirPanel debe existir');
  assert.match(abrir[0], /cargarLista\(\)/);
  const cargarLista = /function cargarLista\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(cargarLista, 'cargarLista debe existir');
  assert.match(cargarLista[0], /api\("\/api\/admin\/intake\/alerts"\)/);
});

test('row actions build WhatsApp/Llamar links from canonical Format, never a new formatter', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /Format\.telWa\(a\.contacto\)/);
  assert.match(js, /Format\.telE164\(a\.contacto\)/);
  assert.match(js, /importStatic\("platform\/format\.mjs"\)/);
  assert.doesNotMatch(js, /SanaFormat/);
  assert.match(js, /href="https:\/\/wa\.me\/' \+ esc\(wa\)/);
  assert.match(js, /href="tel:' \+ esc\(telE164\)/);
  assert.doesNotMatch(js, /function\s+telNacional/, 'no debe redefinir el formateador de teléfono');
});

test('each row links to the doctor-facing consulta view and can be marked seen', () => {
  const js = readFileSync(jsPath, 'utf8');
  // Desde una pantalla directa (/admin/<screen>) el href vuelve a /admin
  // primero — el prefijo se decide por window.SANA_ADMIN_SCREEN, no por
  // dos implementaciones. El comportamiento ejecutable (los DOS hrefs
  // reales que produce) está en tests/admin-shell-shared.test.mjs.
  assert.match(js, /"\/admin\/consult\?interview=" \+ encodeURIComponent\(a\.id\)/);
  assert.match(js, /href="' \+ consultaHref/);
  assert.match(js, /data-ack="' \+ esc\(a\.id\)/);
});

test('ack posts to the alerts ack endpoint and refreshes the badge', () => {
  const js = readFileSync(jsPath, 'utf8');
  const ackWire = /function onAckClick\(e\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(ackWire, 'el wiring de [data-ack] debe existir');
  assert.match(ackWire[0], /\/api\/admin\/intake\/alerts\/" \+ encodeURIComponent\(id\) \+ "\/ack/);
  assert.match(ackWire[0], /method: "POST"/);
  assert.match(ackWire[0], /poll\(\)/);
});

test('reuses the exact queue-triage--rojo chip class in the row, not a redefinition', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.match(js, /class="queue-triage queue-triage--rojo"/);
});

test('the alert control never exposes an icon-font token as visible fallback text', () => {
  const js = readFileSync(jsPath, 'utf8');
  assert.doesNotMatch(js, />notifications</);
  assert.match(js, /label\.className = "alert-bell-label"/);
  assert.match(js, /label\.textContent = T\("admin\.system\.alerts\.label", null, "Triage rojo"\)/);
});
