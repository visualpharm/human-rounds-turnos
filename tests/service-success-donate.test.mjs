/* service-success-donate.test.mjs — visibilidad contextual de la donación de
   sangre en la pantalla de éxito del wizard (reunión hospital 2026-08-27,
   La contraparte técnica pidió visibilidad — el responsable del producto descartó un banner fijo): un renglón discreto
   DESPUÉS de la acción principal, nunca antes ni como pastilla.

   Contracto de fuente sobre service.js (mismo estilo que
   service-wizard-arranque.test.mjs): el wizard no es un módulo cargable.

   Correr:  node --test tests/service-success-donate.test.mjs   (desde la raíz) */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../app/modules/turnos/service.mjs", import.meta.url), "utf8");

// La construcción entera de donateHtml, whitespace-flexible: al fijarla completa
// queda probado también lo que NO es — no hay clase wz-cta ni pastilla alguna,
// sólo el renglón wz-donate-note con el {link} adentro.
test("donateHtml es UN renglón discreto (wz-donate-note) con el CTA hacia /blood-donation", () => {
  const m = src.match(
    /var donateHtml = '<p class="wz-donate-note">' \+ T\("service\.success\.donate_note",\s*\{ link: '<a href="\/blood-donation">' \+\s*T\("service\.success\.donate_cta", null, "([^"]+)"\) \+ '<\/a>' \},\s*"([^"]+)"\) \+ "<\/p>";/);
  assert.ok(m, "donateHtml existe y es exactamente ese renglón");
  // el CTA tiene su propia clave con fallback castellano
  assert.equal(m[1], "sumate como donante");
  // la frase completa es la del fallback, con {link} como variable (i18n:
  // nunca concatenar una palabra traducida con formato — la frase es una)
  assert.equal(m[2], "Ya que estás: el hospital necesita donantes de sangre — {link}.");
});

test("el renglón de donación va DESPUÉS de la acción principal, dentro de wz-success", () => {
  // orden literal del return: resumen → acciones → donateHtml → cierre
  assert.match(src, /successSummaryHtml\(\) \+\s*acciones \+\s*donateHtml \+\s*"<\/div>";/);
  // y no forma parte de la pila de acciones (que compite con el trámite)
  const def = src.indexOf("var donateHtml");
  const uso = src.indexOf("donateHtml +", def);
  assert.ok(def > -1 && uso > def, "donateHtml se define y se usa una vez, al final");
});
