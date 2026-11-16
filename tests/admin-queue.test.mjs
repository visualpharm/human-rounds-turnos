// Guardas anti-basura de la cola del admin (responsable del producto, 2026-07-19: "add tests to
// avoid adding garbage, to avoid duplicating stuff"). El controlador de
// Solicitudes es un script sin módulos, así que estas guardas son sobre el
// FUENTE: cada una ancla una decisión que ya se degradó una vez (label junto
// al checkbox, hint del sobreturno, chips de prácticas, resumen duplicando
// las marcas).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as Dates from '../static/platform/dates.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'static', 'root', 'admin-app.js'), 'utf8');
// Desde el rediseño 2026-08-05 el markup y el CSS de la cola viven en su
// propio módulo; el estado y los datos siguen en el controlador. Las guardas
// que miran forma leen `cola`, las que miran lógica leen `src`.  Direct-screen
// layout rules live in admin-direct.css, not the retired base-shell inline CSS.
const cola = readFileSync(join(ROOT, 'static/platform/admin-solicitudes.mjs'), 'utf8');
const directCss = readFileSync(join(ROOT, 'static', 'root', 'admin-direct.css'), 'utf8');
// The typeable combo's markup, filtering, and keyboard behavior live in
// admin-combo.mjs; widget-specific assertions read `combo`.
const combo = readFileSync(join(ROOT, 'static/platform/admin-combo.mjs'), 'utf8');

test('el checkbox de lote no lleva texto al lado (un checkbox no se autodescribe)', () => {
  // el string sólo puede vivir en un aria-label, nunca como texto visible
  const visible = src.replace(/aria-label="[^"]*"/g, '');
  assert.ok(!visible.includes('Seleccionar esta solicitud'));
  assert.ok(!src.includes('bulk-card-select'));
});

test('sobreturno: sin renglón explicativo suelto — la explicación vive en title', () => {
  assert.ok(!src.includes('overturn-hint'));
  // 2026-08-05 el texto pasó por T() (i18n): ahora vive en un
  // title="${esc(T(...))}" en vez de un title="..." literal — se pelan los
  // dos casos antes de buscar el texto fuera de un atributo.
  const suelto = src.replace(/title="\$\{[\s\S]*?\}"|title="[^"]*"/g, '');
  assert.ok(!suelto.includes('No se ofrece a pacientes'));
  assert.match(src, /admin\.queue\.solicitudes\.sobreturno_title/);
  // y el control arranca escondido: aparece recién con el error de choque
  assert.match(src, /class="overturn-ctl" hidden/);
});

test('prácticas = texto o lista real, no chips, y sin GRITOS (mayúsculas sostenidas)', () => {
  assert.ok(!src.includes('practica-chip'));
  assert.match(src, /const valores = arr\.map\(sinGritos\)/);
  assert.match(src, /<ul class="campo-list">/);
  // Las prácticas nunca se aplastan en una línea con puntos: van como lista.
  // (El separador ' · ' sí se usa en otras líneas del panel — p. ej. la meta
  // del paciente —, así que la prohibición mira los valores de prácticas.)
  assert.ok(!/(valores|practicas|prácticas)[^\n]*\.join\(' · '\)/.test(src));
});

test('sinGritos: sólo baja el texto TODO en mayúsculas, respeta el mixto', () => {
  const code = extractFn('sinGritos') + '\nreturn {sinGritos};';
  const { sinGritos } = new Function(code)();
  assert.equal(sinGritos('HEMOGRAMA COMPLETO'), 'Hemograma completo');
  assert.equal(sinGritos('Glucemia en ayunas'), 'Glucemia en ayunas');
  assert.equal(sinGritos('TSH'), 'TSH');                    // sigla corta se respeta
  assert.equal(sinGritos('T4 LIBRE'), 'T4 libre');
});

test('sin orden: el panel no dibuja el bloque «Orden médica»', () => {
  // Antes esto era "sin columna lateral en la tarjeta". Con el split la
  // tarjeta murió, pero la decisión es la misma: sin orden no se dibuja un
  // recuadro vacío que parezca una orden rota — el bloque directamente no va.
  assert.match(src, /ordenHtml: s\.tiene_orden \? ordenPreview\(s\) : ''/);
  assert.match(cola, /if \(d\.ordenHtml\) \{/);
  // The missing order is not written in the row either (the product owner, 2026-11-08:
  // "nobody in Gmail writes 'no attachment'"): the row of a request with no
  // order says nothing there, the one with an order carries a paperclip.
  assert.match(src, /function lecturaFila\(s\)\{\n  if\(!s\.tiene_orden \|\| suggestFor\(s\)==='aprobar'\) return '';/);
  assert.match(src, /veredicto: pend \? lecturaFila\(s\) : estadoResueltaLabel\(s\)/);
  assert.match(src, /adjunto: pend \? !!s\.tiene_orden : null/);
});

test('el turno es un <select> de slots de agenda con "Otro horario…" adentro', () => {
  assert.ok(!src.includes('turno-echo'));
  assert.ok(!src.includes('turno-disp'), 'murió el botón+popup');
  assert.ok(!src.includes('turno-free-ck'), 'murió el checkbox Otro horario');
  // valor canónico oculto + select poblado desde la misma fuente que ve el
  // paciente + opción "__otro" que destapa el datetime-local libre
  assert.match(src, /<input type="hidden" class="turno"/);
  assert.match(src, /<select class="adm-sel turno-sel"/);
  assert.match(src, /api\/admin\/appointment-slots/);
  assert.match(src, /<option value="__otro"\$\{otro\?' selected':''\}>\$\{esc\(T\("admin\.queue\.solicitudes\.otro_horario", null, "Otro horario…"\)\)\}<\/option>/);
  assert.match(src, /function poblarTurnoSel\(/);
});

test('Aprobar nace apagada y el horario en carga hasta que llega la agenda de la tarjeta', () => {
  // el HTML de la tarjeta sale con el select en carga y el botón apagado
  assert.match(src, /<select class="adm-sel turno-sel" data-agenda="cargando" aria-busy="true" disabled/);
  assert.match(src, /data-act="aprobar"[^>]*disabled/);
  // executePending se niega a aprobar mientras la agenda no llegó o falló
  assert.match(src, /querySelector\('\.turno-sel\[data-agenda\]'\)/);
  // si la agenda falla: sigue «Cargando horarios…» y la tarjeta la pide de nuevo
  // sola, cada vez más espaciado (como Mis turnos); ni línea roja ni «Reintentar»
  assert.match(src, /function agendaReintentar\(/);
  assert.match(src, /catch\(e\)\{ if\(card\._agendaTok===tok\) agendaReintentar\(card, tok, opts\); return; \}/);
  assert.doesNotMatch(src, /sol-agenda-retry|sol-agenda-err|function agendaFalla\(/);
  // un médico no ve el combo de servicio: la agenda sale del servicio de la solicitud (F1, 08.11)
  assert.match(src, /function svcDeTarjeta\(/);
  assert.match(src, /const svc = svcDeTarjeta\(card\);/);
  // 409 por turno tomado: se vuelve a pedir la agenda y se ofrece otro horario
  assert.match(src, /poblarTurnoSel\(card, \{tras409: result\.code\}\)/);
});

test('el default del turno respeta la franja preferida, también al cambiar médico', () => {
  assert.match(src, /function turnoPreferido\(/);
  assert.match(src, /turnoPreferido\(turnos, prefGeneral\(/);
  // cambiar servicio/médico re-puebla el select de esa tarjeta
  assert.match(src, /SLOTS_CACHE\.clear\(\);\s*const card = e\.target\.closest\('\.card'\);\s*if\(card\) poblarTurnoSel\(card\);/);
});

test('la preferencia aparece una sola vez, junto a médico y horario', () => {
  assert.match(src, /function prefIcon\(/);
  // en la fila de aprobar: médico → preferencia → horario, en ese orden
  assert.match(src, /const aprobarHtml = medCombo \+ prefIcon\(prefGen\)[\s\S]*turno-sel/);
  assert.ok(!src.includes('${pref}'), 'la preferencia textual no se duplica en el pedido');
});

test('motivos partidos: los de orden no se ofrecen en solicitudes sin orden', () => {
  assert.match(src, /const MOTIVOS_ORDEN = \[/);
  assert.match(src, /const MOTIVOS_GENERAL = \[/);
  assert.match(src, /s\.tiene_orden \? MOTIVOS_ORDEN\.concat\(MOTIVOS_GENERAL\) : MOTIVOS_GENERAL/);
  assert.match(src, /motivosPara\(s\)\.map/);
  assert.ok(!src.includes('MOTIVOS_STD'), 'la lista única murió');
});

test('«Resolver»: primero aprobar, debajo motivo + rechazar', () => {
  const m = cola.match(/<div class="sol-res">[\s\S]*?sol-res-sep[\s\S]*?rechazarHtml/);
  assert.ok(m, 'bloque Resolver no encontrado');
  assert.match(m[0], /sol-res-aprobar[\s\S]*aprobarHtml[\s\S]*sol-res-sep[\s\S]*rechazarHtml/);
  // los controles se reparten el ancho REAL del panel: los 160/170px fijos de
  // la tarjeta vieja empujaban «Aprobar» fuera de la pantalla
  assert.match(cola, /\.sol-res-row > \.cmb[^{]*\{flex:1 1 96px;width:auto;/);
});

test('cada rama viene completa y la alternativa se apaga SIN pintar bloques', () => {
  assert.match(src, /function defaultMotivo\(/);
  assert.match(src, /function motivoLabel\(/);
  assert.match(src, /const motivo0 = defaultMotivo\(s\)/);
  assert.match(src, /value="\$\{esc\(m\)\}"[^>]*>\$\{esc\(motivoLabel\(m\)\)\}/);
  assert.match(cola, /card card--sug-/);
  // la única señal es la opacidad de la rama NO sugerida + el botón sólido
  assert.match(cola, /\.card--sug-aprobar \.sol-res-sep,\.card--sug-rechazar \.sol-res-aprobar\{opacity:\.58;\}/);
  // Product owner, 2026-07-22: "don't invent the background blocks beneath accept and reject"
  assert.ok(!/\.sol-res-(aprobar|sep)\{background:/.test(cola),
    'ninguna rama de Resolver lleva fondo propio');
});

test('Aprobar queda compacto y Rechazar separado en la misma fila', () => {
  // el combo se dimensiona por lo que muestra el INPUT, no por el
  // "— libre <cuándo>" que sólo vive en la lista desplegada (ese subtítulo
  // arranca con "—" y queda excluido de la medición). El del servicio (el
  // centro, 2026-07-27) sí entra al input al elegir, así que sí cuenta.
  // Width measurement lives with the widget in admin-combo.js.
  assert.match(combo, /const lens = items\.map\(x=>\(\(x\.t\|\|''\) \+ \(\/\^—\/\.test\(x\.s\|\|''\) \? '' : ' '\+\(x\.s\|\|''\)\)\)\.length\)/);
  // La fila de decisiones es `.sol-res-row` en el panel de detalle. Las
  // guardas apuntaban a `.acts`/`.act-approve-controls`, de la tarjeta vieja
  // que ya no se renderiza — y por eso nadie notó que los controles de esa
  // fila se habían quedado sin estilo. Los cuatro comparten UNA receta.
  assert.match(cola, /\.sol-res-row\{display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap;\}/);
  assert.match(cola, /\.sol-res-row > \.motivo-otro,\.sol-res-row > \.turno-free\{flex:1 1 100%;width:100%;/);
  assert.match(cola, /\.sol-res-row \.cmb-in\{font-size:16px;min-height:40px;/);
  assert.match(cola, /\.sol-res-row input\[type=datetime-local\],\.sol-res-row \.motivo-otro\{[^}]*min-height:40px;/);
  // fecha corta ("Mié 29 jul, 10:00") en el panel, no la larga del paciente
  assert.match(src, /Dates\.corta\(diaIso/);
});

test('sin orden propone aprobar y la selección por lote empieza vacía', () => {
  assert.match(src, /function suggestFor\(s\)\{\s*if\(!s\.tiene_orden\) return 'aprobar';/);
  const pendingCheckbox = src.match(/<input type="checkbox" class="pt-select"[^>]*>/)?.[0] || '';
  const pageCheckbox = src.match(/<input type="checkbox" data-select-pending-page[^>]*>/)?.[0] || '';
  assert.ok(!pendingCheckbox.includes(' checked'));
  assert.ok(!pageCheckbox.includes(' checked'));
});

test('Rechazar no sugerido es gris; el rojo queda para el sugerido', () => {
  // Gris oscuro del panel (--hint-ink), no la escala translucida (responsable del producto 04.11).
  // The rule has to outrank the panel's `.adm5-main .btn.btn-no` (red ink and
  // outline), and covers the group's and the selection bar's «Rechazar» too.
  const gris = cola.match(/\.adm5-main \.sol-res \.btn\.btn-no,[^{]*\{[^}]*\}/)?.[0] || '';
  assert.match(gris, /color:var\(--hint-ink\)/);
  assert.match(gris, /border:1px solid var\(--border-strong\)/);
  assert.match(gris, /\.adm5-main \.btn\.sol-grp-btn\.btn-no/);
  assert.match(gris, /\.adm5-main \.sol-selbar \.btn\.btn-no/);
  assert.doesNotMatch(cola, /\.sol-res \.btn-no\{/, 'the bare rule alone loses to the panel');
  assert.match(cola, /\.adm5-main \.sol-res \.btn\.btn-no\.btn-suggest\{[^}]*var\(--danger\)/);
  // Phone: only the grouped queue drops its header band; the flat and
  // resolved lists are sorted from it.
  assert.match(cola, /\.sol-q-clip \.sol-cols\{display:none;\}/);
  assert.doesNotMatch(cola, /"\.sol-cols\{display:none;\}"/);
  // Y su espejo: «Aprobar» es outline salvo cuando la IA lo sugiere. Sin esta
  // guarda volvía al `.btn-ok` genérico —negro sólido, el de Guardar— que es
  // exactamente lo que pasó al morir el scope `.acts` (handoff 2026-08-06).
  assert.match(cola, /\.sol-res \.btn-ok\{[^}]*background:var\(--panel\)/);
  assert.match(cola, /\.sol-res \.btn-ok\.btn-suggest\{[^}]*background:var\(--btn-bg,var\(--accent\)/);
  assert.ok(!/\.acts\s*\{/.test(src) && !/\.acts\s+\.[\w-]+[^{]*\{/.test(src),
    'la tarjeta vieja `.acts` ya no tiene reglas CSS que puedan quedar huérfanas');
});

test('valores sin negrita; diagnóstico corto queda en una sola línea', () => {
  const v = directCss.match(/\.campos \.v\{[^}]*\}/);
  assert.ok(v && !/font-weight:6/.test(v[0]), '.campos .v no debe ir en negrita');
  const ce = directCss.match(/\n\s*\.campo-edit\{[^}]*\}/);
  assert.ok(ce && !/font-weight:6/.test(ce[0]), '.campo-edit no debe ir en negrita');
  assert.match(src, /if\(k==='diagnostico'\)\{[\s\S]*?limpio\.length<=44[\s\S]*?<input type="text" class="campo-edit/);
  assert.match(src, /limpio\.length<=44[\s\S]*?<textarea class="campo-edit/);
});

test('timestamp compacto y decisiones a lo ancho, debajo de la orden', () => {
  // La columna usa el módulo de fechas canónico; no conserva otra tabla de
  // días/meses que pueda ocultar una importación rota.
  assert.match(extractFn('fmtCreatedShort'), /Dates\.corta\(diaIso/);
  assert.match(directCss, /\.card:not\(\.card--solo\)\{display:grid;grid-template-columns:minmax\(0,1fr\) 300px;\}/);
  // fila de decisiones a ancho completo: encerrada en la columna izquierda,
  // Rechazar caía a un segundo renglón en una laptop de 1280 (responsable del producto, 2026-07-22)
  assert.match(directCss, /\.card:not\(\.card--solo\) \.card-actions\{grid-column:1\/-1;grid-row:2;\}/);
  // Los controles de Resolver se reparten el ancho real del panel (428px),
  // no los 160/170px fijos que les daba la tarjeta vieja en una fila de 1240.
  assert.match(cola, /\.sol-res-row > \.cmb,\.sol-res-row > \.adm-sel,/);
  assert.match(cola, /\.motivo-sel\{flex:1 1 96px;width:auto;min-width:0;max-width:none;\}/);
});

test('los botones de lote no muestran contadores entre paréntesis', () => {
  // 2026-08-05 los labels pasaron por T() (i18n) — la guarda ahora mira las
  // claves de catálogo y el fallback castellano, no el texto crudo.
  const bulk = cola.match(/function selbarHtml\([\s\S]*?\n  \}/)?.[0] || '';
  assert.ok(bulk.includes('admin.queue.solicitudes.resolver_boton'));
  assert.ok(bulk.includes('admin.queue.solicitudes.aprobar'));
  assert.ok(bulk.includes('admin.queue.solicitudes.rechazar'));
  // el conteo va UNA vez, en la barra ("N seleccionada(s)"), no en cada botón
  assert.ok(!/\(0\)/.test(bulk));
  assert.match(bulk, /sol-selbar-n">' \+ esc\(contado\)/);
  assert.match(bulk, /Tn\("admin\.queue\.solicitudes\.seleccionada", n, null, "\{n\} seleccionada", "\{n\} seleccionadas"\)/);
});

test('la barra de selección aparece SÓLO con filas tildadas', () => {
  // una barra permanente con tres botones apagados era chrome que ocupaba
  // lugar sin decir nada (rediseño 2026-08-05)
  assert.match(cola, /function selbarHtml\(n, conSugerencia\) \{\s*if \(!n\) return "";/);
});

test('validación queda junto al valor editable y la prepaga respeta su nombre', () => {
  assert.match(directCss, /\.campos \.v\{display:inline-flex;[^}]*gap:4px/);
  assert.match(src, /function campoAnchoCh\(/);
  assert.ok(!src.includes("ck=' <span"));
  assert.match(src, /function fmtPrepaga\([\s\S]*?'swiss medical':'Swiss Medical'/);
});

test('la orden muestra zoom-in en la esquina inferior izquierda y los links no se subrayan', () => {
  assert.match(directCss, /\.orden-zhint\{position:absolute;left:9px;bottom:9px;/);
  // Drawn magnifier, never an icon-font ligature that leaks as text ("zoom_in").
  assert.match(src, /<span class="orden-zhint" aria-hidden="true"><svg class="orden-zhint-ic"/);
  assert.doesNotMatch(src, />zoom_in</);
  assert.match(directCss, /\.refeps-number\{[^}]*text-decoration:none/);
  assert.match(directCss, /\.pt-meta \.lnk:hover\{[^}]*text-decoration:none/);
});

test('pager: navegación a la izquierda, tamaño de página a la derecha, sin nota ni divisor', () => {
  const pager = src.match(/return `<div class="pgr"[\s\S]*?<\/div>`;/)?.[0] || '';
  assert.ok(pager.indexOf('pgr-pages') < pager.indexOf('pgr-pp'));
  assert.match(pager, /placeholder="#"/);
  assert.ok(!src.includes('tarjetas extendidas'));
  const css = directCss.match(/\.pgr\{[^}]*\}/)?.[0] || '';
  assert.ok(!/border-(top|bottom)/.test(css));
});

test('pares en columna: prepaga/afiliado y médico/matrícula son UNA fila de dos renglones', () => {
  // 2026-08-05 los labels pasaron por T() (i18n) — se mira la clave de
  // catálogo, no el literal castellano crudo.
  assert.match(src, /\['prepaga', T\("admin\.queue\.overlay\.prepaga", null, "Prepaga"\), 'par-os', 'no-label'\]/);
  assert.match(src, /\['afiliado', T\("admin\.queue\.overlay\.afiliado", null, "N° afiliado"\), 'par-os'\]/);
  assert.match(src, /\['medico', T\("admin\.queue\.overlay\.medico", null, "Médico"\), 'par-med', 'no-label'\]/);
  assert.match(src, /\['matricula', T\("admin\.queue\.overlay\.matricula", null, "Matrícula"\), 'par-med'\]/);
  // El segundo del par cuelga del primero, sin etiqueta propia (diseño
  // 2026-08-06). Antes eran dos `.campo-row` con dos etiquetas dentro de un
  // `.campo-par`; seis renglones para tres datos.
  assert.match(src, /<span class="campo-sub\$\{sub\.editado\?' v-editado':''\}">\$\{sub\.cuerpo\}<\/span>/);
  assert.ok(!/class="campo-par"/.test(src) && !/class="campo-par"/.test(directCss), 'el wrapper .campo-par ya no se usa');
  assert.match(directCss, /\.campo-sub\{flex:0 0 100%;/);
  assert.match(directCss, /\.campos\{columns:2/);
});

test('cada dato una vez: DNI/Paciente de la orden sólo cuando contradicen el header', () => {
  assert.match(src, /\(k==='dni'\|\|k==='paciente'\) && \(c\.coincide===true/);
});

test('el resumen de la IA no duplica las marcas de campo (sólo es fallback sin campos)', () => {
  // ai-resumen aparece únicamente como fallback cuando camposTable no dio nada
  const usos = src.match(/class="ai-resumen"/g) || [];
  assert.equal(usos.length, 1);
  assert.match(src, /camposTable\(ai\.campos, s\.id, freshness\(s\)\)\s*\|\|\s*\(s\.tiene_orden \?/);
});

test('documento equivocado: un solo aviso, sin filas de campos vacías', () => {
  assert.match(src, /const camposHtml = docWrong \? docCallout\(ai\)/);
});

test('formatos consistentes: DNI con puntos y fecha amigable en la tabla de campos', () => {
  assert.match(src, /shown = fmtDni\(c\.valor\)/);
  assert.match(src, /shown = fechaAmigable\(c\.valor\)/);
});

test('números de afiliado respetan los grupos visuales de Swiss Medical y OSDE', () => {
  assert.match(src, /d\.length===18[\s\S]*d\.slice\(6,12\)[\s\S]*d\.slice\(14\)/);
  assert.match(src, /d\.length===19[\s\S]*d\.slice\(6,13\)[\s\S]*d\.slice\(15\)/);
  assert.match(src, /\/osde\/\.test\(os\) && d\.length===11/);
});

test('pares relacionados juntos: prepaga+afiliado y médico+matrícula contiguos', () => {
  const m = src.match(/const order = \[\[([^\]]+\]\s*,?\s*)+\];/s) || src.match(/const order = \[[\s\S]*?\];/);
  assert.ok(m, 'orden de campos no encontrado');
  const o = m[0];
  const idx = (k) => o.indexOf(`'${k}'`);
  assert.ok(idx('prepaga') >= 0 && idx('afiliado') > idx('prepaga'));
  assert.ok(o.slice(idx('prepaga'), idx('afiliado')).split('[').length <= 3,
    'prepaga y afiliado no son contiguos');
  assert.ok(idx('medico') >= 0 && idx('matricula') > idx('medico'));
  assert.ok(o.slice(idx('medico'), idx('matricula')).split('[').length <= 3,
    'medico y matricula no son contiguos');
});

test('la barra de lote se renderiza UNA vez (no duplicada arriba y abajo)', () => {
  const calls = cola.match(/selbarHtml\(/g) || [];
  // 1 definición + 1 llamada en render()
  assert.equal(calls.length, 2);
});

test('la cola no tiene páginas: se cargan más (load-more) y se dice cuántas se ven', () => {
  // 2026-08-05 los textos pasaron por T() (i18n) — la guarda mira las claves
  // de catálogo y el fallback castellano, no el texto crudo.
  assert.match(cola, /admin\.queue\.solicitudes\.mostrando"[\s\S]*?"Mostrando " \+ o\.pie\.mostrando \+ " de " \+ o\.pie\.total\)\)/);
  assert.match(cola, /admin\.queue\.solicitudes\.cargar_mas"[\s\S]*?data-sol-mas>' \+ masTxt/);
  assert.match(src, /COLA_LIMITE \+= COLA_LOTE/);
  // 2026-08-06: Aprobadas/Rechazadas/Todas dejaron el paginador (pagerHtml)
  // por el MISMO load-more que Pendientes — con su propio cupo
  // (COLA_LIMITE_RES) para no compartirlo con la cola de pendientes.
  assert.match(src, /COLA_LIMITE_RES \+= COLA_LOTE/);
  assert.ok(!/pagerHtml\([^)]*'pend'\)/.test(src));
  assert.ok(!/pagerHtml\([^)]*'list'\)/.test(src), 'el paginador de resueltas ya no se llama');
});

test('la cola se agrupa por lo que la IA sugiere, y el agrupado se puede apagar', () => {
  assert.match(src, /const COLA_GRUPOS = \[/);
  assert.match(src, /Aprobar según sugerencia/);
  assert.match(src, /Revisar a mano/);
  assert.match(src, /Rechazar según sugerencia/);
  assert.match(src, /COLA_AGRUPA = !COLA_AGRUPA/);
});

test('abrir una solicitud es un gesto distinto de tildarla', () => {
  // el click en la casilla no puede abrir el panel: son dos acciones
  assert.match(src, /const ck=e\.target\.closest\('\.sol-ck'\);[\s\S]*e\.stopPropagation\(\)/);
  // el control de HSI (reintentar / estado) tampoco abre la solicitud
  assert.match(src, /const fila=e\.target\.closest\('\[data-hsi-retry\],\[data-hr-hsi\]'\) \? null : e\.target\.closest\('\[data-sol-row\]'\)/);
});

test('el lote trabaja sobre los datos, y la abierta manda sobre su propio dato', () => {
  // con una sola solicitud dibujada, leer el DOM de las demás no es posible:
  // executePending recalcula los defaults del dato (defaultsDe)
  assert.match(src, /function defaultsDe\(s\)/);
  assert.match(src, /function refPara\(s\)\{ return cardDe\(s\) \|\| s; \}/);
  assert.match(src, /const card = ref && ref\.nodeType===1 \? ref : null;/);
});

test('estado y sugerencia son selects del filtro; murieron las pastillas con conteos', () => {
  const screen = readFileSync(join(ROOT, 'templates/admin/solicitudes.html'), 'utf8');
  assert.match(screen, /<select id="f-estado"/);
  assert.match(screen, /<select id="f-sug"/);
  assert.ok(!src.includes('class="adm-filter on"') && !screen.includes('class="adm-filter on"'));
  // sólo Pendientes lleva conteo — nada de "Pendientes de revisión (N)" aparte
  assert.ok(!src.includes('Pendientes de revisión'));
  // Never a counter (the product owner, 2026-10-14): neither in the option nor beside the title.
  assert.doesNotMatch(src, /estado_pendientes_n/);
  assert.doesNotMatch(src, /adm-h1-count/);
});

test('Solicitudes and Pacientes de hoy expose the same doctor, specialty, and center filters', () => {
  const queue = readFileSync(join(ROOT, 'static/platform/admin-queue.mjs'), 'utf8');
  const app = readFileSync(join(ROOT, 'static', 'root', 'admin-app.js'), 'utf8');
  const screen = readFileSync(join(ROOT, 'templates/admin/solicitudes.html'), 'utf8');
  assert.match(screen, /id="f-medico"[\s\S]*data-key="medico"/);
  assert.match(screen, /id="f-especialidad"[\s\S]*data-key="especialidad"/);
  assert.match(screen, /id="f-servicio"[\s\S]*data-key="servicio"/);
  assert.match(app, /FilterControls\.uniqueOptions\(ALL, medicoClaveDe, medicoEtiquetaDe\)/);
  assert.match(app, /medico_asignado_id \|\| s\.slot_medico_id/);
  assert.match(app, /FILTERS\.medico && medicoClaveDe\(s\)!==FILTERS\.medico/);
  assert.match(app, /function especialidadUnicaDelServicio/);
  assert.match(queue, /function especialidadFiltroHtml\(\)/);
  assert.match(queue, /function centroFiltroHtml\(\)/);
  assert.match(queue, /STATE\.filtroEspecialidad/);
  assert.match(queue, /STATE\.filtroCentro/);
  assert.match(queue, /FilterControls\.select/);
});

test('el lote arranca con "Resolver según sugerencia" y sabe ejecutarla', () => {
  assert.match(cola, /data-bulk-action="sugerencia"/);
  assert.match(src, /function runBulkSugerencia/);
  assert.match(src, /sugerenciaDeCard/);
});

test('combos tipeables para Derivar a y Médico; un laboratorio no ofrece médico', () => {
  // The queue calls the shared admin-combo.js boundary and keeps no copy.
  assert.match(src, /Combo\.html\('svc-sel'/);
  assert.match(src, /Combo\.html\('med-sel'/);
  assert.doesNotMatch(src, /function comboHtml\(/);
  assert.match(src, /s\.sin_medico/);
  assert.match(src, /function esLab\(/);
});

test('Derivar a otro centro: pide los horarios como derivación y muestra el centro nuevo', () => {
  // La mesa acotada al Hospital deriva a un CAPS: el servidor sólo abre la agenda
  // de otro centro con `derivacion=1`, y la tarjeta ya no sigue diciendo «Hospital».
  assert.match(src, /appointment-slots\?servicio_id=\$\{encodeURIComponent\(svc\)\}&derivacion=1/);
  assert.match(src, /\(SVC_LIST\.find\(x=>x\.id===svc\)\|\|\{\}\)\.centro/);
});

test('si falta la sugerencia del servidor, se preselecciona el próximo médico con agenda', () => {
  // Un solo default para la tarjeta y el lote (medicoPorDefecto, 2026-10-23);
  // el comportamiento lo prueba admin-medico-otra-especialidad.test.mjs.
  assert.match(src, /const conAgenda = base\.filter\(x=>x\.proximo&&x\.proximo\.sort\)/);
  assert.match(src, /const medDefault = medicoPorDefecto\(s, base, prefGen\);/);
  assert.match(src, /enFranja\[0\]\|\|conAgenda\[0\]/);
});

// --- lógica pura extraída del fuente (parse/format de fechas) ---------------
function extractFn(name) {
  const re = new RegExp(`function ${name}\\([^)]*\\)\\{[\\s\\S]*?\\n\\}`);
  const m = src.match(re);
  assert.ok(m, `función ${name} no encontrada`);
  return m[0];
}

test('fechaAmigable ida y vuelta: lo que se muestra se puede re-parsear', () => {
  const code = extractFn('parseFechaTxt') + '\n'
    + `const _MESES_CORTOS = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];\n`
    + extractFn('fechaAmigable')
    + '\nreturn {parseFechaTxt, fechaAmigable};';
  const { parseFechaTxt, fechaAmigable } = new Function('Dates', code)(Dates);
  assert.equal(fechaAmigable('11/05/2026'), '11 de mayo de 2026');
  assert.equal(fechaAmigable('28-11-2023'), '28 de noviembre de 2023');
  // round-trip: la forma amigable vuelve a parsear al mismo día
  const dt = parseFechaTxt(fechaAmigable('11/05/2026'));
  assert.equal(dt.getFullYear(), 2026);
  assert.equal(dt.getMonth(), 4);
  assert.equal(dt.getDate(), 11);
  // y las formas largas que escribe la IA también entran
  const dt2 = parseFechaTxt('12 junio 2026');
  assert.equal(dt2.getMonth(), 5);
  const dt3 = parseFechaTxt('12 de junio de 2026');
  assert.equal(dt3.getDate(), 12);
  assert.match(extractFn('fechaAmigable'), /Dates\.nacimiento\(iso\)/);
  assert.doesNotMatch(src, /const _MESES_CORTOS\s*=/);
});

test('fmtCreatedShort (columna "Recibida") delega en dates.mjs sin tablas locales', () => {
  const fn = extractFn('fmtCreatedShort');
  assert.match(fn, /Dates\.corta\(diaIso/);
  assert.doesNotMatch(fn, /date\.day_short|date\.month|\['Dom'|\['ene'/);
  assert.doesNotMatch(src, /_DIAS_CORTOS_KEY/);
});

// --- derivación sugerida y arreglos de un click (2026-07-27) ---------------

test('orden de otra especialidad: el combo llega abierto y prefilleado', () => {
  // Product owner: "when the orden is for a wrong specialist, show and prefill the
  // reassignment dropdown". Sin esto el operador tenía que abrir el combo con
  // el botón y buscar el servicio a mano entre 120.
  assert.match(src, /const sugSvc = s\.servicio_sugerido;/);
  assert.match(src, /const svcVal = sugSvc \? sugSvc\.id : s\.servicio_id;/);
  // el valor prefilleado es el que viaja al aprobar (mismo .svc-sel de siempre)
  assert.match(src, /Combo\.html\('svc-sel', svcItems, svcVal, svcDisp/);
  // abierto: .svc-edit sin hidden. El nombre plano ya no se esconde — vive en
  // la fila «Servicio» del panel, y el combo se despliega debajo del bloque.
  assert.match(src, /<span class="svc-edit"\$\{sugSvc\?'':' hidden'\}>/);
  assert.match(src, /aria-expanded="\$\{sugSvc\?'true':'false'\}"/);
  // los médicos salen del servicio DESTINO, no del original
  assert.match(src, /const base = medicosDe\(svcVal\);/);
});

test('mismo nombre en dos centros se distingue por el centro', () => {
  // "two spellings of the same": Psiquiatría existe en dos centros y se veían
  // como dos filas idénticas. El centro se agrega SÓLO a los repetidos.
  const fn = src.match(/function svcCombolItems\([\s\S]*?\n\}/)[0];
  assert.match(fn, /veces\[_norm\(x\.nombre\|\|''\)\] > 1 && x\.centro/);
  assert.match(fn, /k: \(x\.palabras\|\|\[\]\)\.join\(' '\)/);
});

test('el combo busca con stemming, no con substring crudo', () => {
  // "psiquiatra" tiene que encontrar Psiquiatría y "cardiólogo" Cardiología:
  // the order names a professional while the catalog names a specialty. The
  // filter lives in admin-combo.js, and the queue keeps no private copy.
  assert.match(combo, /function filter\(items, q\)\{/);
  assert.match(combo, /Search\.buscarConPuntaje\(items, q,/);
  assert.match(combo, /principal:\[it\.t\], resto:\[it\.s\|\|'', it\.k\|\|''\]/);
  // y sigue habiendo red: substring cuando el ranking descarta todo
  assert.match(combo, /return items\.filter\(it=>_norm\(`\$\{it\.t\} \$\{it\.s\|\|''\} \$\{it\.k\|\|''\}`\)\.includes\(nq\)\)/);
  assert.doesNotMatch(src, /function cmbFiltrar\(/);
});

test('un estudio que el hospital no hace lo dice, no devuelve lista vacía', () => {
  assert.match(src, /const NO_SE_REALIZA = \[/);
  assert.match(src, /function noSeRealiza\(q\)\{/);
  // 2026-08-05 el texto pasó por T() (i18n) — se mira la clave de catálogo.
  assert.match(src, /admin\.queue\.solicitudes\.hospital_no_realiza", \{ estudio: '<b>'\+esc\(no\)\+'<\/b>' \}/);
  // The queue registers the message only for service combos; the doctor combo
  // instead registers its free-text policy on the shared widget.
  assert.match(src, /Combo\.register\('svc-sel', \{emptyHtml:/);
  assert.match(src, /Combo\.register\('med-sel', \{freeText:true\}\)/);
});

test('los arreglos de un click dicen la salida del paciente, no su error', () => {
  const map = src.match(/function arreglosMap\(\)\{[\s\S]*?\n\}/)[0];
  // el texto del botón es lo que el paciente recibe — 2026-08-05 pasó por
  // T() (i18n), se mira la clave de catálogo en vez del literal castellano.
  assert.match(map, /'La orden está vencida\.': ofrecerMedicinaGeneral/);
  assert.match(map, /admin\.queue\.solicitudes\.arreglo\.ofrecer_medicina_general/);
  assert.match(map, /'La orden no se lee con claridad\.': T\("admin\.queue\.solicitudes\.arreglo\.pedir_foto_nitida"/);
  // ningún botón nombra el error crudo
  assert.ok(!/'Orden vencida'|'Orden ilegible'/.test(map));
  // pasa por el botón real de Rechazar: el atajo cambia los clicks, no la lógica
  assert.match(src, /const rej = card && card\.querySelector\('\[data-act="rechazar"\]'\);/);
  // un médico no resuelve solicitudes
  assert.match(src, /if\(!fixes\.length \|\| CURRENT_ROLE==='medico'\) return '';/);
});
