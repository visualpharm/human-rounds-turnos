import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const js = fs.readFileSync(new URL("../static/platform/admin-system.mjs", import.meta.url), "utf8");
const screen = fs.readFileSync(new URL("../templates/admin/system.html", import.meta.url), "utf8");

test("Sistema: la pantalla directa carga el controlador sobre su contenedor", () => {
  assert.match(screen, /id="sec-sistema"/);
  assert.match(screen, /static\('platform\/admin-system\.mjs'\)/);
});

test("Sistema: el porcentaje deriva de ok sobre total y conserva el detalle accesible", () => {
  assert.match(js, /Math\.round\(ok \* 100 \/ total\)/);
  assert.match(js, /badge\.textContent = pct \+ "%"/);
  assert.match(js, /integraciones en verde/);
  assert.match(js, /badge\.setAttribute\("aria-label", badge\.title\)/);
});

test("Sistema: el controlador arranca solo en la pantalla directa", () => {
  assert.match(js, /export function mount\(/);
  assert.match(screen, /System\.mount\(host/);
  assert.match(screen, /pagehide/);
  // The persisted-pageshow remount is the shared loader's.
  assert.match(screen, /bootScreen\(\{/);
  assert.doesNotMatch(js, /SanaAdminRouter|location\.hash/);
});

test("Sistema: a server instant is read on the installation's clock, never the reader's", () => {
  // «Última transmisión» of HSI: relativo() prints the browser's hour from six
  // hours on; instanteServidor() reads the day and the hour from the server's text.
  assert.match(js, /Dates\.instanteServidor\(it\.cuando\) \|\| it\.valor/);
  assert.doesNotMatch(js, /Dates\.relativo\(it\.cuando\)/);
});

test("Estado: el título coincide con el menú", () => {
  assert.match(screen, /\{% block screen_title %\}\{\{ t\('admin\.system\.sistema\.title', 'Status'\) \}\}\{% endblock %\}/);
  assert.doesNotMatch(js, /h1\.textContent/);
});
