/* Arranque del wizard sin pantallas de paso (responsable del producto, 2026-07-27: "i get to this
   screen with 2 screens refreshing automatically, visible for a fraction of a
   second"). Llegar con ?slot= y sesión abierta resuelve "Cuándo" y "Tu cuenta"
   sin preguntar nada: mientras eso pasa se muestra UNA pantalla de espera, no
   el cromo de cada paso. Y en el último paso el contador no se dibuja. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const src = fs.readFileSync(new URL('../app/modules/turnos/service.mjs', import.meta.url), 'utf8');

test('el wizard no arranca antes de que sus módulos canónicos terminen de cargar', () => {
  assert.match(src, /Promise\.all\(\[i18nReady, edadLista, calLista, datesLista, formatLista, prepLista, tsLista\]\)\.then\(function \(\) \{\s*renderForm\(formHost,/);
  assert.match(src, /var edadLista = importStatic\("platform\/age\.mjs"\)[\s\S]{0,180}\.catch\(function \(\) \{/);
  assert.match(src, /var calLista = importStatic\("platform\/calendar-grid\.mjs"\)[\s\S]{0,180}\.catch\(function \(\) \{/);
  assert.match(src, /var formatLista = importStatic\("platform\/format\.mjs"\)\.then\(function \(m\) \{\s*Format = m;\s*\}\);/);
  assert.match(src, /var prepLista = importStatic\("platform\/prep\.mjs"\)\.then\(function \(m\) \{\s*Prep = m;\s*\}\);/);
});

test('mientras el wizard avanza solo muestra una única pantalla de espera', () => {
  assert.match(src, /var avanzandoSolo = !!slotParam\(\) && \(MODE === "turno" \|\| ELEGIR_PRIMERO \|\| !!ordenPrecargada\)/);
  assert.match(src, /function pantallaEspera\(\)[\s\S]{0,320}wz-boot/);
  // los dos pasos que se resuelven solos no pintan su propio cromo
  assert.match(src, /function stepHorario\(\)[\s\S]{0,200}if \(!pantallaEspera\(\)\) \{/);
  assert.match(src, /function stepCuenta\(\)[\s\S]{0,200}if \(!pantallaEspera\(\)\) \{/);
  // cualquier pantalla que sí mira una persona apaga el modo espera
  assert.match(src, /function stepHeader\(key\)[\s\S]{0,260}avanzandoSolo = false;/);
});

test('la sesión y las personas a cargo se piden en paralelo con los turnos', () => {
  assert.match(src, /if \(avanzandoSolo\) \{ sesionPrefetch = fetchSesion\(\); depsPrefetch = fetchDependientes\(\); \}/);
  assert.match(src, /var pedido = sesionPrefetch \|\| fetchSesion\(\);\s*\n\s*sesionPrefetch = null;/);
  // quien se acaba de loguear no puede leer un prefetch pedido sin su sesión
  assert.match(src, /var pedido = \(!viaAuth && depsPrefetch\) \|\| fetchDependientes\(\);/);
});

test('el contador usa sólo los pasos estimados que la persona necesita', () => {
  assert.match(src, /Array\.isArray\(data\.pasos_estimados\)/);
  assert.match(src, /function ensureStep\(key\)/);
  assert.doesNotMatch(src, /function stepHeader\(key\) \{\s*ensureStep\(key\)/);
  assert.match(src, /function renderSlots\(d\) \{\s*ensureStep\("horario"\)/);
  assert.match(src, /function mountAuth\(\) \{\s*ensureStep\("cuenta"\)/);
  assert.match(src, /if \(STEP_KEYS\.length < 2\) \{ setSteps\(""\); return ""; \}/);
  // "Paso {n} de {total}" pasó por T() (i18n 2026-08-04); n y STEP_KEYS.length
  // viajan como variables del catálogo, no ya concatenados en el string crudo.
  assert.match(src, /T\("service\.step\.aria", \{ n: n, total: STEP_KEYS\.length \}, "Paso \{n\} de \{total\}"\)/);
  assert.doesNotMatch(src, /pintados\[key\]/);
});

/* El ingreso federado (/auth/sso?next=…) es una navegación de página COMPLETA:
   al volver, el wizard arranca de cero. Todo lo que la persona ya hizo tiene
   que estar en la URL + sessionStorage o se pierde — te volvía a pedir la orden
   después de loguearte (responsable del producto, 2026-07-27). */
test('la orden aceptada queda en el handoff y en la URL', () => {
  assert.match(src, /function guardarOrdenHandoff\(\)[\s\S]{0,400}sessionStorage\.setItem\("st_order_pending"/);
  assert.match(src, /ponerOrdenEnUrl\(wiz\.token\)/);
  // se guarda al ACEPTARLA, no apenas se analiza
  assert.match(src, /else \{ guardarOrdenHandoff\(\); afterOrden\(\); \}/);
  assert.match(src, /\$\("#wz-seguro", host\)\.addEventListener\("click", function \(\) \{\s*\n\s*guardarOrdenHandoff\(\);/);
});

test('un documento que frenó en la pantalla de duda no se persiste', () => {
  // la rama del veredicto dudoso llama a stepDocDudoso y NADA más
  assert.match(src, /if \(ai\.documento && ai\.documento\.es_orden === false\) stepDocDudoso\(\);/);
  assert.doesNotMatch(src, /guardarOrdenHandoff\(\);\s*\n?\s*stepDocDudoso\(\)/);
});

test('cambiar o enviar la orden limpia el handoff', () => {
  const olvidos = src.match(/olvidarOrdenHandoff\(\)/g) || [];
  assert.ok(olvidos.length >= 4, `esperaba al menos 4 limpiezas, hay ${olvidos.length}`);
  assert.match(src, /olvidarOrdenHandoff\(\);\s*\/\/ la orden ya es de la solicitud/);
});

test('el horario elegido a mano viaja en la URL, y se borra al soltarlo', () => {
  assert.match(src, /recordarSlotEnUrl\(dia, hora\);/);
  const limpiezas = src.match(/recordarSlotEnUrl\(null, null\)/g) || [];
  // Both auth-summary and confirmation can now return to the grid. Each exit
  // must clear the selected slot rather than reusing it behind the user's
  // back; the service-choice route is the fourth existing release path.
  // Fix 4 adds a fifth one: when navigating back via popstate, replayStep
  // clears the slot parameter to prevent stale URLs (issue 4).
  assert.equal(limpiezas.length, 5, 'cinco salidas que sueltan el turno (cuatro + popstate back)');
  assert.match(src, /wz-auth-change-time[\s\S]{0,700}recordarSlotEnUrl\(null, null\)/,
    'Cambiar horario también libera el turno antes de volver a la grilla');
});

/* Un ?slot= que ya no está en la agenda mandaba a la grilla sin decir nada: el
   aviso «lo acaba de tomar otra persona» sólo salía cuando fallaba el hold, no
   cuando el turno directamente desapareció. */
test('un turno que ya no está en la agenda se explica', () => {
  // el aviso pasó por T() (i18n 2026-08-04); el castellano vive en el
  // fallback de service.slots.notice_gone, ya no pegado a un "=" literal.
  // Since 15.11 (hr-patient review #7) the notice names the lost time and the
  // grid opens on that day: perdido() keeps the day and builds the sentence.
  assert.match(src, /if \(!pre && !slotSaltado && slotParam\(\)\) \{[\s\S]{0,200}perdido\(String\(slotParam\(\)\)\.split\("T"\)\[0\][^\n]*"gone"\)/);
  assert.match(src, /function perdido\(dia, hora, motivo\) \{[\s\S]{0,900}T\("service\.slots\.notice_gone_when", \{ when: when \}, "\{when\}: ese turno ya no está libre/);
  assert.match(src, /var ancla = diaPerdido \|\| wiz\.slotDia/);
  // el aviso se consume una sola vez y no vuelve al cambiar de horario
  assert.match(src, /if \(!pre && !slotSaltado && slotParam\(\)\) \{\s*\n\s*slotSaltado = true;/);
});

/* La columna de 300px del grid es PARA la foto: un PDF no tiene preview, y
   reservarla igual dejaba el contenido en 284px pegado al borde izquierdo. */
test('la pantalla de duda sólo pide dos columnas cuando hay foto', () => {
  assert.match(src, /"@media\(min-width:640px\)\{\.wz-err--foto\{grid-template-columns/);
  assert.match(src, /'<div class="sec"><div class="wz-err' \+ \(wiz\.ordenPreview \? " wz-err--foto" : ""\)/);
  // la regla vieja, sin variante, no vuelve
  assert.doesNotMatch(src, /\{\.wz-err\{grid-template-columns/);
});
