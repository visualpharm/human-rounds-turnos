import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { gridHtml } from '../static/platform/calendar-grid.mjs';
import { setCatalog } from '../static/platform/i18n.mjs';

const serviceSrc = fs.readFileSync(new URL('../app/modules/turnos/service.mjs', import.meta.url), 'utf8');

test('el calendario compartido marca días disponibles y deshabilita los demás', () => {
  const html = gridHtml([
    { dia: '2026-07-27', className: 'medcal-libre', title: 'Hay turnos disponibles' },
    { dia: '2026-08-03', className: 'medcal-libre', title: 'Hay turnos disponibles' },
  ], {
    selected: '2026-07-27',
    dateAttr: 'data-slot-date',
    disableMissing: true,
    today: '2026-07-25',
  });
  assert.match(html, /data-slot-date="2026-07-27"/);
  assert.match(html, /medcal-libre is-selected/);
  assert.match(html, /data-slot-date="2026-07-28" disabled/);
  assert.match(html, /julio 2026/);
  assert.match(html, /agosto 2026/);
});

// Multi-idioma (the product owner, 2026-08-04): the canonical ESM reads the same optional
// browser catalog at call time, never through a compatibility global.
const enCatalog = JSON.parse(fs.readFileSync(new URL('../locales/en/date.json', import.meta.url), 'utf8'));

function withCatalog(lang, catalog, fn) {
  setCatalog(catalog);
  try { return fn(); } finally { setCatalog({}); }
}

test('el calendario en inglés dice el mes y las iniciales de día en inglés', () => {
  const html = withCatalog('en', enCatalog, () => gridHtml([
      { dia: '2026-07-27', className: 'medcal-libre', title: 'Slots available' },
    ], { today: '2026-07-25' }));
  assert.match(html, /July 2026/);
  assert.match(html, /<span class="medcal-dow">Mo<\/span>/); // lunes/Monday
});

test('el calendario en castellano con el catálogo puesto no se mueve', () => {
  const html = gridHtml([
    { dia: '2026-07-27', className: 'medcal-libre', title: 'Hay turnos disponibles' },
  ], { today: '2026-07-25' });
  assert.match(html, /julio 2026/);
  assert.match(html, /<span class="medcal-dow">Lu<\/span>/);
});

/* Cabecera de dos letras + grilla elástica (responsable del producto, 2026-09-10): a una letra
   "M" es lunes, martes O miércoles, y las columnas fijas de 32px se pisaban
   en un teléfono de 390px. La grilla ahora escala con el contenedor y las
   claves date.day_two_letter.* existen en los dos catálogos. */
const esCatalog = JSON.parse(fs.readFileSync(new URL('../locales/es/date.json', import.meta.url), 'utf8'));

test('la cabecera del calendario usa dos letras distintas por día en ambos idiomas', () => {
  for (const key of ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']) {
    assert.ok(esCatalog['date.day_two_letter.' + key], 'es ' + key);
    assert.ok(enCatalog['date.day_two_letter.' + key], 'en ' + key);
  }
  const html = withCatalog('es', esCatalog, () => gridHtml([
    { dia: '2026-07-27' },
  ], { today: '2026-07-25' }));
  const labels = [...html.matchAll(/<span class="medcal-dow">([^<]+)<\/span>/g)].map((m) => m[1]);
  assert.equal(labels.length, 7);
  assert.equal(new Set(labels).size, 7); // ninguna inicial repetida
});

test('la grilla escala con el contenedor y no pisa columnas en 390px', async () => {
  const { css } = await import('../static/platform/calendar-grid.mjs');
  assert.match(css, /grid-template-columns:repeat\(7,minmax\(32px,1fr\)\)/);
  assert.doesNotMatch(css, /repeat\(7,32px\)/);
  // los meses pueden encoger (antes min-width:238px forzaba overflow en móvil)
  assert.match(css, /\.medcal-mes\{min-width:0;flex:0 1 auto;\}/);
  assert.match(css, /@media\(max-width:767px\)\{\.medcal-mes\{flex:1 1 100%;\}\.medcal-dia\{height:44px;line-height:44px;\}\}/);
  // cabecera de dos letras: font-size inherit de .medcal-grid, color key-info dark gray/black
  assert.match(css, /\.medcal-dow\{font-size:inherit;color:var\(--text\)/);
  assert.doesNotMatch(css, /!important/);
});

test('el wizard conserva el médico y "Otras fechas" abre el calendario', () => {
  assert.match(serviceSrc, /prefer_medico=/);
  assert.match(serviceSrc, /medico_id === PREFERRED_MEDICO/);
  assert.match(serviceSrc, /data-slot-date/);
  // el botón con ícono murió: ahora es un link al lado de la fecha
  // ("Otras fechas" pasó por T() en la ronda de i18n del 2026-08-04; el
  // castellano vive en el fallback, no ya pegado al cierre del <button>)
  assert.match(serviceSrc, /class="wz-otras"[\s\S]{0,300}T\("service\.slots\.other_dates", null, "Otras fechas"\)[\s\S]{0,20}<\/button>/);
  assert.doesNotMatch(serviceSrc, /wz-calendar-trigger/);
  assert.doesNotMatch(serviceSrc, /CAL_ICON/);
  assert.doesNotMatch(serviceSrc, /Prefiero otro horario/);
  assert.doesNotMatch(serviceSrc, /id="wz-otro"/);
});

/* Un calendario POR médico (responsable del producto, 2026-07-28: "add it to all the doctors").
   Con uno solo y global, elegir un día que otra persona no atiende le borraba
   el bloque entero de la pantalla. */
test('cada médico tiene su calendario y su filtro de día', () => {
  assert.match(serviceSrc, /var key = m\.medico_id \|\| medico \|\| "bloque-" \+ idx;/);
  assert.match(serviceSrc, /var calId = "wz-calendar-" \+ idx;/);
  assert.match(serviceSrc, /slotFilterDia: \{\}, calendarOpen: \{\}/);
  assert.match(serviceSrc, /var filtro = wiz\.slotFilterDia\[key\] \|\| "";/);
  assert.match(serviceSrc, /wiz\.slotFilterDia\[dkey\] = day\.getAttribute\("data-slot-date"\);/);
  // el calendario sólo ofrece los días de ESE médico
  assert.match(serviceSrc, /gridHtml\(allDays\.map\(/);
});

// hr-patient review 15.11 (#20): six open days buried «Otras fechas» under
// every time of the week. The first screen keeps two days (several, never
// one), anchored on the day the person was on; the calendar has the rest.
// Without a calendar nothing is cut.
test('la vista inicial muestra dos días desde el día elegido y elegir uno cierra el calendario', () => {
  assert.match(serviceSrc, /var allDays = Object\.keys\(byDay\)\.sort\(\)/);
  assert.match(serviceSrc, /var DIAS_VISIBLES = 2;/);
  assert.match(serviceSrc, /var visibleDays = filtro[\s\S]{0,180}: \(Cal \? allDays\.slice\(desde, desde \+ DIAS_VISIBLES\) : allDays\)/);
  assert.doesNotMatch(serviceSrc, /allDays\.slice\(0,\s*1\)/);
  assert.match(serviceSrc, /wiz\.calendarOpen\[dkey\] = false;/);
  assert.match(serviceSrc, /trigger\.setAttribute\("aria-expanded", abrir \? "true" : "false"\)/);
  // el panel se abre al final, después de que la persona vio todas las opciones
  assert.doesNotMatch(serviceSrc, /\(di === 0 \? calendarPanel : ""\)/);
  assert.match(serviceSrc, /days \+ otras \+ calendarPanel/);
});

test('los nombres de médico son links y los grupos no tienen divisor', () => {
  assert.match(serviceSrc, /<a class="wz-medico-n" href=/);
  assert.doesNotMatch(serviceSrc, /\.wz-medico\{[^}]*border-bottom/);
  // the grid reaches the page's own right edge, like every other page (#20)
  assert.match(serviceSrc, /\.wz-card--slots\{max-width:none/);
  assert.match(serviceSrc, /\.wz-day \.wz-seg\{display:grid;grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(serviceSrc, /\.wz-medico-n\{[^}]*font-size:20px/);
});

test('el calendario usa dos meses en desktop y el link queda al final de las fechas', () => {
  assert.match(serviceSrc, /\.wz-calendar \.medcal-grids\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(serviceSrc, /@media\(max-width:700px\)\{\.wz-calendar \.medcal-grids\{grid-template-columns:minmax\(0,1fr\)/);
  assert.match(serviceSrc, /class="wz-otras-row"/);
  assert.doesNotMatch(serviceSrc, /esc\(friendlyDia\(dia\)\) \+ "<\/div>" \+ otras/);
});

/* Quién atiende no se elige sólo por el nombre: la especialidad es lo que
   dice si esa persona es quien corresponde (responsable del producto, 2026-07-28). */
test('nombre, especialidad y centro comparten una fila y el contexto baja entero', () => {
  assert.match(serviceSrc, /function quienDonde\(m\)/);
  assert.match(serviceSrc, /m\.esp_href[\s\S]{0,120}esc\(m\.especialidad\)/);
  assert.match(serviceSrc, /m\.centro_href[\s\S]{0,120}esc\(m\.centro_nombre\)/);
  assert.match(serviceSrc, /partes\.join\(" · "\)/);
  // El link del lugar es un link de texto: color de link del tema (tinta +
  // subrayado en humanrounds, acento en pinamar) — 2026-09-15.
  assert.match(serviceSrc, /\.wz-medico-lugar a\{color:var\(--link,var\(--accent\)\)/);
  assert.match(serviceSrc, /nombre \+\s*quienDonde\(m\) \+ "<\/div>"/);
  assert.match(serviceSrc, /\.wz-medico-head\{[^}]*flex-wrap:wrap/);
  assert.match(serviceSrc, /\.wz-medico-lugar\{[^}]*white-space:nowrap/);
});

test('al entrar desde un médico no aparece un segundo bloque anónimo de horarios', () => {
  assert.match(serviceSrc, /preferidoExiste[\s\S]{0,260}medico_id === PREFERRED_MEDICO/);
  assert.doesNotMatch(serviceSrc, /wz-medico-n wz-medico-n--plain">Horarios/);
  assert.match(serviceSrc, /align-items:baseline;column-gap:12px/);
});

test('el análisis de la orden tiene una transición visible y accesible', () => {
  assert.match(serviceSrc, /class="wz-processing" role="status" aria-live="polite"/);
  assert.match(serviceSrc, /Estamos leyendo tu orden/);
  assert.match(serviceSrc, /@keyframes wzscan/);
  assert.match(serviceSrc, /prefers-reduced-motion:reduce/);
});

test('el documento equivocado destaca sólo el tipo y pide subir la orden', () => {
  assert.match(serviceSrc, /tipoConArticulo/);
  // el tipo va en <strong> ARMADO por separado (tipoConArticuloHtml) y viaja
  // como variable {tipo} del catálogo (i18n 2026-08-04) — ya no queda pegado
  // al literal en el código fuente.
  assert.match(serviceSrc, /var tipoConArticuloHtml = "<strong>" \+ esc\(tipoConArticulo\) \+ "<\/strong>";/);
  assert.match(serviceSrc, /"service\.doc\.warning", \{ tipo: tipoConArticuloHtml \}/);
  assert.match(serviceSrc, /Este documento parece ser \{tipo\}, no una orden médica\. Por favor, subí la orden médica\./);
  assert.doesNotMatch(serviceSrc, /¿Seguro que es la orden\?/);
});

/* Entrevista previa en "Solicitud enviada" (responsable del producto, 2026-08-01): con el médico
   habilitado, contarle los síntomas ES la acción primaria — botón grande, y
   "Ver mis solicitudes" baja a secundario. El médico del bloque elegido viaja
   al POST como slot_medico_id, sólo para decidir esa oferta (el override por
   médico puede estar ON con el default del sistema OFF). */
test('el éxito ofrece la entrevista como CTA primaria con el médico del turno', () => {
  // el bloque elegido viaja al POST y se limpia con el resto del slot
  assert.match(serviceSrc, /payload\.slot_medico_id = wiz\.slotMedicoId/);
  assert.match(serviceSrc, /data-medico-id/);
  assert.match(serviceSrc, /wiz\.slotMedicoId = null/);
  // botón grande hacia la entrevista
  assert.ok(serviceSrc.includes('esc(entrevista.url) + \'">\' + T("service.success.entrevista_cta", null, "Contanos qué te pasa") + \'</a>\''));
  assert.match(serviceSrc, /wz-entrevista-lead/);
  // «Ver mis solicitudes» dejó el éxito (responsable del producto 26.10): ni primaria ni
  // secundaria — el único botón que queda es «Guardar cobertura».
  assert.doesNotMatch(serviceSrc, /verRequests/);
  assert.doesNotMatch(serviceSrc, /class="wz-cta-(lg|sec)" href="\/my-requests"/);
  // el link de texto de la fase 2 murió
  assert.doesNotMatch(serviceSrc, /wz-success-link/);
});

/* Confirmá mostraba el nombre de archivo pelado ("IMG_...jpg" + "Cambiar")
   cuando la orden llegó SÓLO con token (handoff de "Subí tu orden" o vuelta
   de SSO: service.js:512-528, los bytes nunca viajan) — incluso siendo una
   foto. La miniatura ahora se prueba contra el servidor
   (booking_requests._orden_preview, token = capacidad) como una <img>
   normal; si falla (PDF) degrada a un chip de documento, nunca a texto
   pelado (the product owner, 2026-08-01). */
test('Confirmá prueba la miniatura del servidor cuando la orden sólo tiene token', () => {
  // arma la URL del servidor con el token, nunca con datos adivinados
  assert.match(serviceSrc, /function ordenPreviewUrl\(\)/);
  assert.match(serviceSrc, /"\/api\/orden-preview\/" \+ encodeURIComponent\(wiz\.token\)/);
  // sólo token (sin bytes en memoria) y sin haber fallado antes -> se intenta como foto
  assert.match(serviceSrc, /var ordenTalVezFoto = !ordenFotoSrc && !!wiz\.token && !wiz\.ordenNoEsFoto;/);
  assert.match(serviceSrc, /var mostrarComoFoto = !!\(ordenFotoSrc \|\| ordenTalVezFoto\);/);
  assert.match(serviceSrc, /widenHost\(mostrarComoFoto \? 900 : 680\)/);
  // el <img> de la miniatura reintenta el paso completo como documento si el
  // servidor no la sirve (404 = no es una imagen)
  assert.match(serviceSrc, /id="wz-orden-thumb-img"/);
  assert.match(serviceSrc, /thumbImg\.addEventListener\("error", function \(\) {\s*wiz\.ordenNoEsFoto = true;\s*stepConfirm\(\);/);
});

test('sin miniatura, la orden se muestra como chip de documento, nunca texto pelado', () => {
  // ordenSummary(): la fila vieja de "wz-preference" para la orden murió —
  // ahora es un chip con ícono, y sólo aparece si el servidor YA confirmó
  // que no es una foto (o nunca hubo token para probar)
  assert.match(serviceSrc, /if \(wiz\.token && !wiz\.ordenNoEsFoto\) return "";/);
  assert.match(serviceSrc, /class="wz-orden-doc"/);
  assert.match(serviceSrc, /class="wz-orden-doc-name"/);
  assert.doesNotMatch(serviceSrc, /'<div class="wz-preference"><span>' \+ nombre/);
});
