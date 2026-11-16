import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const src = fs.readFileSync(new URL("../static/platform/admin-settings.mjs", import.meta.url), "utf8");
const template = fs.readFileSync(new URL("../templates/admin/settings.html", import.meta.url), "utf8");

test("settings separates general, connectors, and integrations", () => {
  assert.match(src, /function applyDirectState\(replace\) \{[\s\S]*?var requested = parseDirectState\(\);[\s\S]*?VISTA = owner \|\| requested\.view;/);
  assert.match(src, /VISTA === "connectors"\s*\?\s*renderConnectors\(\)/);
  assert.match(src, /admin\.system\.settings\.nav\.connectors/);
  assert.match(src, /data-connector-hsi/);
  assert.match(src, /grupo: "connectors", valores: \{ HSI_ENABLED: enabled \}/);
  assert.match(template, /static\('platform\/admin-settings\.mjs'\)/);
});

test("seguridad reuses the same card/row/guardar pattern as the other groups", () => {
  assert.match(src, /data-grupo="seguridad"/);
  assert.match(src, /class="btn btn-accent set-guardar" data-grupo="seguridad"/);
  assert.match(src, /row\("IPS_EXENTAS"/);
});

test("Conectores names the HSI state in the same words as Sistema", () => {
  // One reading for the three places (the server sends it translated); the
  // card tolerates a payload without it.
  assert.match(src, /function hsiStateHtml\(estado\)/);
  assert.match(src, /hsiStateHtml\(\(data\.connectors \|\| \{\}\)\.hsi\)/);
  assert.match(src, /estado\.modo_texto \|\| ""/);
  // Server mode: the journal line names the parts that exist, never the zeros,
  // and «hace 5 minutos» comes from the one shared formatter — the one that
  // reads an old instant on the installation's clock, never the reader's.
  assert.match(src, /Dates\.instanteServidor\(diario\.ultima\)/);
  assert.doesNotMatch(src, /Dates\.relativo\(diario/);
  // A transmission that happened is never «none»: without the formatter (or
  // when its optional import fails) the line prints the server's own text.
  assert.match(src, /String\(diario\.ultima\)\.slice\(0, 16\)\.replace\("T", " "\)/);
  assert.match(src, /then\(function \(m\) \{ Dates = m; \}, function \(\) \{ Dates = null; \}\)/);
  assert.match(src, /Number\(diario\.en_cola\) > 0/);
  assert.match(src, /Number\(diario\.errores\) > 0/);
  assert.match(src, /partes\.join\(" · "\)/);
  assert.match(src, /admin\.system\.settings\.connectors\.hsi_none_yet/);
  // Centers: a same-site link with the server-rendered summary.
  assert.match(src, /estado\.total\) > 0/);
  assert.match(src, /href="\/admin\/hsi">' \+ esc\(estado\.centros_texto \|\| ""\)/);
});

test("language choices use full names", () => {
  assert.match(src, /es: "Español"/);
  assert.match(src, /en: "English"/);
  assert.match(src, /languageName\(code\)/);
  assert.doesNotMatch(src, /esc\(code\.toUpperCase\(\)\)/);
});

test("AI chat greeting is editable per available language", () => {
  assert.match(src, /function renderChat\(\)/);
  assert.match(src, /data-grupo="chat"/);
  assert.match(src, /textarea data-key=/);
  assert.match(src, /\{brand\}/);
});

test("AI inference keeps the cloud endpoint fixed and exposes profiles clearly", () => {
  assert.match(src, /function inferenceProfile\(\)/);
  assert.match(src, /"cloud_shared", "hosted_jurisdiction", "self_hosted_vllm"/);
  assert.match(src, /data-inference-profile/);
  assert.match(src, /data-inference-base/);
  assert.match(src, /base\.disabled = select\.value === "cloud_shared"/);
  assert.match(src, /profile_cloud_shared/);
  assert.match(src, /profile_hosted_jurisdiction/);
  assert.match(src, /profile_self_hosted/);
});

test("saving one integration preserves unsaved sibling-card inputs", () => {
  assert.match(src, /function refreshIntegrationCard\(sec, cardId\)/);
  assert.match(src, /oldCard\.replaceWith\(freshCard\)/);
  assert.match(src, /refreshIntegrationCard\(sec, card\.getAttribute\("data-integracion-card"\)\)/);
  assert.doesNotMatch(src, /if \(grupo === "api_keys"\) render\(\)/);
});

test("settings form fits narrow containers and adds no row dividers", () => {
  assert.match(src, /\.set-field\{[^}]*min-width:0/);
  assert.match(src, /\.set-field input:not\(\[type=checkbox\]\):not\(\[type=radio\]\),\.set-field select,\.set-field textarea\{[^}]*font-size:16px[^}]*min-height:40px/);
  // Checkboxes/radios carry the readable form scale too: their computed
  // font-size must never fall back to the 13.33px UA default (the
  // admin-screen e2e floors every #sec-settings control at 16px).
  assert.match(src, /\.set-field input\[type=checkbox\],\.set-field input\[type=radio\],\.set-toggle input\[type=checkbox\]\{[^}]*font-size:20px/);
  assert.match(src, /\.set-actions \.btn\{min-height:var\(--adm-btn-h,36px\);[^}]*font-size:var\(--adm-btn-font,14px\)/);
  // En pantalla táctil, además del área de toque, se calla la instrucción de
  // teclado ("Ctrl/Cmd+click"), que en un teléfono no se puede seguir.
  assert.match(src, /@media\(pointer:coarse\)\{\.set-field input:not\(\[type=checkbox\]\):not\(\[type=radio\]\),\.set-field select,\.set-field textarea,\.set-actions \.btn\{min-height:44px;\}\.set-chain-hint\{display:none;\}\}/);
  assert.ok(src.includes('.replace("<select ", \'<select id="set-f-\''));
  assert.doesNotMatch(src, /\.set-row\{[^}]*border-(?:top|bottom)/);
});
