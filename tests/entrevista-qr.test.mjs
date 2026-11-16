// Guardas de fuente para el paso de identidad del QR de puerta (fase 6,
// modules/intake/) en entrevista.js. Mismo patrón que admin-queue.test.mjs:
// sobre el texto del archivo, sin levantar navegador — alcanza para clavar
// que el paso de identidad nunca sugiere/autocompleta nombres ajenos y que
// entrevista.js is declared as a module fingerprinted asset and every
// interview shell resolves it through the module registry.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { patientSiteSource } from './helpers/patient-site-source.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const js = readFileSync(join(ROOT, 'app', 'modules', 'intake', 'entrevista.js'), 'utf8');

test('entrevista.js reads data-qr-start off the host, separate from data-token', () => {
  assert.match(js, /host\.getAttribute\("data-qr-start"\)/);
  assert.match(js, /host\.getAttribute\("data-token"\)/);
});

test('identity step: login first, DNI-only backup, autocomplete off, no saved-profile names', () => {
  const m = /function renderIdentidad\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'renderIdentidad debe existir');
  const body = m[0];
  // primero la cuenta (el que reservó ya tiene una), el DNI como plan B —
  // y ya no se piden nombre/apellido en ningún caso
  assert.match(body, /Ingres(á|a) a tu cuenta/);
  assert.doesNotMatch(body, /name="nombre"/);
  assert.doesNotMatch(body, /name="apellido"/);
  // el <form> lleva autocomplete="off" (defensa en profundidad contra el
  // autocompletado del navegador) y el input de DNI lo repite con un
  // name= aleatorio (randomName) para que Chrome/Safari no autocompleten
  // perfiles guardados
  assert.match(body, /<form class="iv-dni-form"[^']*autocomplete="off"/);
  const inputAttrs = body.match(/autocomplete="off"/g) || [];
  assert.ok(inputAttrs.length >= 2, 'form + input de DNI deben llevar autocomplete="off"');
  assert.doesNotMatch(body, /<datalist/);
  assert.match(body, /randomName\(/);
  // sólo dígitos, teclado numérico en el teléfono
  assert.match(body, /inputmode="numeric"/);
});

test('start POST hits /api/intake/qr/<door>/start and never persists the token to localStorage', () => {
  assert.match(js, /\/api\/intake\/qr\/" \+ encodeURIComponent\(DOOR\) \+ "\/start"/);
  // el token vive sólo en la variable TOKEN (memoria) — no hay llamadas
  // reales a localStorage, sólo la prosa del comentario de arriba lo
  // menciona para explicar por qué no se usa.
  assert.doesNotMatch(js, /localStorage\.(setItem|getItem)/);
});

test('429 and generic failure on start show a friendly retry, not a raw error', () => {
  const m = /function renderIdentidad\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'renderIdentidad debe existir');
  const body = m[0];
  assert.match(body, /res\.status === 429/);
  assert.match(body, /Prob(á|a) de nuevo/);
});

test('interview_patient.py serves the declared module fingerprint in every interview shell', () => {
  const py = readFileSync(join(ROOT, 'app', 'modules', 'intake', 'interview_patient.py'), 'utf8');
  const references = [...py.matchAll(/module_url\(['"]modules\/intake\/entrevista\.js['"]\)/g)];
  assert.ok(references.length >= 2, 'entrevista.js debe estar referenciado en las dos páginas (post-reserva y QR)');
});

// ---- pantalla completa + componente compartido (chat-ui.js, 2026-08-01) ---

test('chrome="chat": interview_patient.py ya no usa chrome="focus" y pasa body_class', () => {
  const py = readFileSync(join(ROOT, 'app', 'modules', 'intake', 'interview_patient.py'), 'utf8');
  assert.doesNotMatch(py, /chrome="focus"/);
  const matches = [...py.matchAll(/chrome="chat", index=False, body_class="iv-chat-body"/g)];
  assert.ok(matches.length >= 2, 'las dos páginas (post-reserva y QR) deben usar chrome="chat" + body_class="iv-chat-body"');
});

test('el sitio del paciente (site_*.py) define chrome="chat" con el sitio alrededor, pero sin el otro chat', () => {
  // Segunda vuelta (responsable del producto, 2026-08-01): en una computadora la entrevista se
  // abre DENTRO del sitio — barra y pie de siempre, columna angosta. Que en
  // el teléfono vuelva a ser la pantalla entera lo decide el ANCHO en
  // entrevista.css, no el servidor: un solo marcado, dos anchos. Lo único
  // que nunca viaja acá es el asistente de reservas.
  const py = patientSiteSource();
  const rama = py.slice(py.indexOf('elif chrome == "chat":'),
                        py.indexOf('    else:', py.indexOf('elif chrome == "chat":')));
  // `nav_js` (Tier 4 slice 6, static_fingerprint.py): el bloque ya no
  // literal-referencia `/nav.js?v=NN`, interpola la variable fingerprinted
  // computada arriba — sigue siendo el mismo script, misma barra del sitio.
  assert.match(rama, /nav[._]js/, 'chrome="chat" trae la barra del sitio');
  assert.match(rama, /wrap--chat/);
  assert.match(rama, /_site_footer\(\)/, 'chrome="chat" trae el pie del sitio');
  assert.doesNotMatch(rama, /chat-widget\.js/,
    'dos chats en una pantalla confunden: el asistente de reservas no se carga acá');
});

test('entrevista.js consume el componente compartido (platform/chat-ui.mjs), no una copia de burbujas/composer', () => {
  assert.match(js, /importStatic\("platform\/chat-ui\.mjs"\)/);
  assert.match(js, /ChatUI\.crearLista\(document\.getElementById\("iv-msgs"\), \{ prefix: "iv" \}\)/);
  assert.match(js, /ChatUI\.crearComposer\(document\.getElementById\("iv-form"\), \{/);
  assert.match(js, /prefix: "iv"/);
  assert.match(js, /soloImagen: true/);
  assert.match(js, /accept: "image\/\*"/);
  assert.doesNotMatch(js, /window\.SanaChatUI/);
  // No hay un renderizador de burbujas/typing propio — eso vive en chat-ui.mjs.
  assert.doesNotMatch(js, /function addBubble\(/);
  assert.doesNotMatch(js, /function setTyping\(/);
});

test('la entrevista embebida cierra el panel padre sin navegar', () => {
  assert.match(js, /var EMBEDDED = document\.body\.classList\.contains\("iv-embed-body"\)/);
  assert.match(js, /postMessage\("humanrounds:close-intake", location\.origin\)/);
});

test('la entrevista pinta las opciones del modelo como botones tocables', () => {
  assert.match(js, /lista\.addOpciones\(m\.opciones, function \(etq\) \{ enviarMensaje\(etq\); \}\)/);
  assert.match(js, /res\.j\.opciones && res\.j\.opciones\.length/);
  assert.match(js, /lista\.addOpciones\(res\.j\.opciones, function \(etq\) \{ enviarMensaje\(etq\); \}\)/);
});

test('la X de cierre nunca dice "Volver" y va a la solicitud o a Mis solicitudes', () => {
  assert.match(js, /function cerrarHref\(\) \{\s*return DETALLE \|\| "\/my-requests";/);
  assert.doesNotMatch(js, /Volver/);
  // El texto pasa por T() (i18n, 2026-08-04) — el castellano queda como
  // fallback del catálogo, no ya como literal pegado a aria-label=.
  assert.match(js, /T\("intake\.common\.close", null, "Cerrar"\)/);
});

test('la identificación de IA vive en el primer mensaje, sin subtítulo duplicado', () => {
  assert.doesNotMatch(js, /iv-topbar-sub|function subtitulo\(/);
  assert.match(js, /pintarMensajes\(mensajes, activa\)/,
    'el mensaje de apertura lo aporta la conversación persistida');
});

// ---- "Actualizar entrevista" (2026-08-01): reabrir un chat 'done' ---------

test('la pantalla de cierre (showDone) ofrece "Agregar información" como único botón lleno', () => {
  const m = /function showDone\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'showDone debe existir');
  const body = m[0];
  assert.match(body, /Agregar informaci(ó|o)n/);
  assert.match(body, /class="iv-primary-btn" id="iv-update-btn"/);
  // "Ver mi solicitud" sigue siendo un link llano, nunca un segundo botón lleno.
  assert.doesNotMatch(body, /iv-primary-btn.*Ver mi solicitud/);
});

test('reabrir() pega a POST /reopen y reactiva el compositor SIN recargar toda la charla', () => {
  const m = /function reabrir\(\) \{[\s\S]*?\n  \}/.exec(js);
  assert.ok(m, 'reabrir debe existir');
  const body = m[0];
  assert.match(body, /\/api\/intake\/t\/" \+ encodeURIComponent\(TOKEN\) \+ "\/reopen"/);
  assert.match(body, /method: "POST"/);
  // en éxito: reaparece el compositor, nunca queda al paciente sin salida en el fallo
  assert.match(body, /composerEl\.hidden = false/);
  assert.match(body, /showDone\(\);/);
});

test('showDone conecta el click de "Agregar información" con reabrir', () => {
  assert.match(js, /btn\.addEventListener\("click", reabrir\)/);
});

test('?actualizar=1 en la URL reabre automáticamente en vez de mostrar la pantalla de cierre (my-requests.js manda un solo toque)', () => {
  assert.match(js, /AUTO_REABRIR = \/\(\^\|\[\?&\]\)actualizar=1\(&\|\$\)\/\.test\(location\.search\)/);
  assert.match(js, /if \(!activa && AUTO_REABRIR\) \{ reabrir\(\); return; \}/);
});
