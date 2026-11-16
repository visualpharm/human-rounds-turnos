import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { t, setCatalog } from "../static/platform/i18n.mjs";
import * as ChatUI from "../static/platform/chat-ui.mjs";
import { patientSiteSource } from "./helpers/patient-site-source.mjs";

const widgetSrc = readFileSync(new URL("../static/platform/chat-widget.mjs", import.meta.url), "utf8");
const bootstrapSrc = readFileSync(new URL("../templates/_chat_bootstrap.html", import.meta.url), "utf8");
const uiSrc = readFileSync(new URL("../static/platform/chat-ui.mjs", import.meta.url), "utf8");
// The composer/bubble-list implementation used to live entirely inside
// chat-widget.js; as of 2026-08-01 it's the shared ChatUI module's
// crearComposer/crearLista (the SAME component entrevista.js now consumes —
// see docs/architecture/chat-y-whatsapp.md, "Shared chat component"). Most
// assertions below don't care WHICH file a given implementation detail
// lives in, so `src` is the concatenation; a handful of tests further down
// assert specifically that the native widget DELEGATES rather than duplicates.
const src = widgetSrc + "\n" + uiSrc;
const refs = [
  "../templates/index.html", "../templates/about.html",
  "../templates/privacy.html", "../templates/terms.html",
  "../templates/account.html",
]
  .map(path => readFileSync(new URL(path, import.meta.url), "utf8"));
refs.push(patientSiteSource());

test("chat history waits for canonical dates.mjs and has no Intl shadow formatter", () => {
  assert.match(widgetSrc, /var datesReady = datesUrl/);
  assert.match(widgetSrc, /datesReady\.then\(renderConversaciones\)/);
  const formatter = widgetSrc.slice(
    widgetSrc.indexOf("function fechaConversacion"),
    widgetSrc.indexOf("function renderConversaciones"),
  );
  assert.match(formatter, /Dates\.relativo/);
  assert.doesNotMatch(formatter, /Intl\.DateTimeFormat|SanaDates/);
});

test("el lanzador flotante abre el chat y conserva el destello de Icons8", () => {
  assert.match(widgetSrc, /stz-chat-launcher/);
  assert.match(widgetSrc, /position:fixed;right:20px;bottom:20px/);
  assert.match(widgetSrc, /if \(!reusedLauncher\) launcher\.addEventListener\("click", function \(\) \{ abrir\(true\); \}\)/);
  assert.match(bootstrapSrc, /id="stz-chat-launcher"/);
  assert.match(bootstrapSrc, /launcher\.addEventListener\("click", requestOpen\)/);
  // El título pasa por el catálogo "chat" (i18n): el fallback es el mismo
  // castellano de siempre, sólo cambia CÓMO llega al <h2>.
  assert.match(src, /T\("chat\.title", null, "Chat con IA"\) \+ '<\/h2>/);
  assert.match(src, /chat-robot-sf-regular\.png/);
  assert.match(src, /Cerrar Chat con IA/);
  assert.match(src, /border-radius:14px/);
  assert.doesNotMatch(src, /width:56px;height:56px;border-radius:50%/);
});

test("el asistente invita a escribir una necesidad concreta y oculta las fuentes por defecto", () => {
  // El saludo es T("chat.greeting", {brand}, "<castellano>") y el fallback
  // ya trae la misma estructura breve/listada que el catálogo editable.
  assert.match(src, /T\("chat\.greeting", \{ brand: chatBrand \}/);
  assert.match(src, /Hola, soy el chat con IA de \{brand\}/);
  assert.match(src, /buscar un estudio o trámite\\n\* encontrar un profesional/);
  assert.match(src, /sacar un turno\\n\* subir una orden médica/);
  assert.match(src, /placeholder: T\("chat\.placeholder", null, "Escribí tu consulta…"\)/);
  assert.doesNotMatch(src, /Buscar un servicio/);
  assert.match(src, /function addFuentes\(fuentes\)/);
  assert.match(src, /document\.createElement\("details"\)/);
  assert.match(src, /title\.textContent = T\("chat\.sources", null, "Ver fuentes"\)/);
  assert.doesNotMatch(src, /Fuentes oficiales/);
  assert.match(src, /link\.target = "_blank"/);
  assert.match(src, /link\.rel = "noopener"/);
  assert.match(src, /addFuentes\(cuerpo\.fuentes\)/);
});

test("el saludo usa la marca del despliegue, no Pinamar dentro del demo", () => {
  assert.match(src, /getAttribute\("data-theme"\) === "pinamar"/);
  assert.match(src, /\? "Turnos de salud pública en Pinamar" : "Human Rounds"/);
});

test("un chat nuevo pinta el saludo antes de esperar el historial remoto", () => {
  assert.match(
    widgetSrc,
    /pedirEnlaceWhatsapp\(\);\s*\n\s*saludarSiHaceFalta\(\);\s*\n\s*sincronizarHistorial\(\);/,
  );
  const historySync = widgetSrc.slice(
    widgetSrc.indexOf("function sincronizarHistorial()"),
    widgetSrc.indexOf("function abrir(enfocar)"),
  );
  assert.doesNotMatch(historySync, /saludarSiHaceFalta\(\)/);
  assert.match(historySync, /!actual\.hasUserMessage/);
});

test("documento equivocado pinta Subir otra orden y abre el selector", () => {
  assert.match(src, /cuerpo\.subir_otra_orden/);
  assert.match(src, /btn\.textContent = T\("chat\.retry_upload", null, "Subir otra orden"\)/);
  assert.match(src, /fileEl\.click\(\)/);
});

test("un corte transitorio reintenta el mismo mensaje antes de fallar", () => {
  assert.match(src, /function pedirStream\(intento\)/);
  assert.match(src, /if \(intento >= 2\) throw err/);
  assert.match(src, /Reconectando…/);
});

test("mientras el turno se calcula, la burbuja muestra estados en vez de Escribiendo fijo (responsable del producto, 2026-08-04)", () => {
  // La burbuja nace "Conectando…", no "Escribiendo…" — el turno recién
  // arrancó y todavía no hay ni un evento del servidor.
  assert.match(widgetSrc, /addMsg\(T\("chat\.connecting", null, "Conectando…"\), "wait", null, true\)/);
  assert.doesNotMatch(widgetSrc, /addMsg\(T\("chat\.typing"/);
  // Cada `event: estado` del servidor (chat_api.py's `_message_stream`)
  // actualiza esa misma burbuja con la etiqueta que mandó, mientras no haya
  // llegado texto real todavía.
  assert.match(src, /evento === "estado"/);
  assert.match(
    src,
    /if \(!acumulado\) lista\.updateBubble\(esperando, "wait", datos\.texto \|\| T\("chat\.typing", null, "Escribiendo…"\)\)/,
  );
  // El primer chunk real es el que efectivamente pasa a "Escribiendo…".
  assert.match(
    src,
    /if \(!acumulado\) lista\.updateBubble\(esperando, "wait", T\("chat\.typing", null, "Escribiendo…"\)\);\s*\n\s*acumulado \+= datos\.texto/,
  );
});

test("si el stream se corta a mitad de camino, reintenta una vez por /api/chat antes de mostrar el error", () => {
  // El fallback es SILENCIOSO: pega contra el endpoint clásico (no streaming)
  // con el mismo body, y sólo si ESO también falla aparece el mensaje de
  // error — nunca antes.
  assert.match(src, /function intentarFallback\(\)/);
  assert.match(src, /fetch\("\/api\/chat", \{/);
  assert.match(src, /body: JSON\.stringify\(body\)/);
  // Reusa exactamente el mismo render que `done` usaría — nada de un camino
  // aparte para opciones/login/whatsapp/turnos.
  assert.match(src, /if \(!r\.ok\) throw new Error\("fallback_no_ok"\);\s*\n\s*terminar\(cuerpo\);/);
  // Se dispara tanto si `leerEventos` termina sin `done` como si el propio
  // fetch del stream rechaza (después de sus reintentos).
  assert.match(src, /if \(terminado\) return;\s*\n\s*return intentarFallback\(\)/);
  assert.match(
    src,
    /\.catch\(function \(\) \{\s*\n\s*return intentarFallback\(\)/,
  );
  // El mensaje final de error sólo se pinta si el fallback también fracasó.
  assert.match(src, /if \(!ok\) \{\s*\n\s*esperando\.remove\(\);\s*\n\s*addMsg\(T\("chat\.connect_error"/);
});

test("los nombres del mensaje son los links internos, sin lista duplicada", () => {
  assert.match(src, /function textoConEnlaces\(texto, enlaces\)/);
  assert.ok(src.includes('href.charAt(0) !== "/"'));
  assert.match(src, /renderMini\(texto, enlaces\)/);
  assert.match(src, /respuesta \|\| T\("chat\.default_answer", null, "Estos son los primeros turnos disponibles:"\),\s*\n\s*\{ enlaces: cuerpo\.enlaces \}/);
  assert.match(src, /addTurnosEnRespuesta\(turnos, cuerpo\.mas_horarios_href\)/);
  assert.doesNotMatch(src, /function addLinks\(/);
  assert.doesNotMatch(src, /Páginas relacionadas/);
});

function cargarChatUI() {
  return ChatUI;
}

test("el enlace se pinta aunque el asistente escriba el nombre con otra capitalización o sin tilde", () => {
  // Objetivo 2026-08-06: todo lo enlazable queda enlazado. El modelo escribe
  // "Medicina General" o "pediatria"; la etiqueta del catálogo es "Medicina
  // general" / "Pediatría". El texto que se ve es el que escribió el
  // asistente, el destino el del catálogo.
  const ui = cargarChatUI();
  const enlaces = [
    { label: "Medicina general", href: "/d/clinica" },
    { label: "Pediatría", href: "/d/pediatria" },
  ];
  const html = ui.renderMini(
    "Te conviene **Medicina General**, o pediatria si es para un chico.", enlaces);
  assert.match(html, /<a href="\/d\/clinica">Medicina General<\/a>/);
  assert.match(html, /<a href="\/d\/pediatria">pediatria<\/a>/);
});

test("las opciones del modelo vuelven a pintarse como botones tocables (ronda 2026-08-01, componente compartido)", () => {
  // Revoca la ronda anterior (que las sacó porque "ya estaban en el texto"):
  // ahora se pintan vía el mismo componente que usa la entrevista
  // (chat-ui.js's crearLista().addOpciones), y tocar una la manda como el
  // próximo mensaje.
  assert.match(src, /cuerpo\.opciones && cuerpo\.opciones\.length/);
  assert.match(src, /addChipRow\(cuerpo\.opciones, function \(etiqueta\) \{ enviarMensaje\(etiqueta\); \}\)/);
  assert.match(src, /function addChipRow\(etiquetas, onPick\) \{\s*return lista\.addOpciones\(etiquetas, onPick\);/);
  assert.match(src, /function addStarterChips\(\)/);
});

test("los turnos reales aparecen agrupados servicio → día → centro, con horas enlazadas", () => {
  assert.match(widgetSrc, /function addTurnosEnRespuesta\(turnos, masHref\)/);
  // El nombre del servicio se pinta SIEMPRE, arriba del árbol de días — es
  // lo que aclara a qué especialidad pertenecen los horarios (responsable del producto, 2026-08-06).
  assert.match(widgetSrc, /nombreEl\.textContent = srv\.nombre/);
  // El día se pinta UNA sola vez por día (no una vez por centro): el centro
  // baja a un sub-ítem propio, indentado, con sus propias horas enlazadas.
  assert.match(widgetSrc, /fecha\.textContent = diaGrupo\.fecha/);
  assert.match(widgetSrc, /linea\.className = "stzc-slot-center"/);
  assert.match(widgetSrc, /T\("chat\.slot_center_times",\s*\n\s*\{ centro: centroGrupo\.centro \}, "\{centro\} a las \{horas\}"\)/);
  assert.match(widgetSrc, /partes = plantilla\.split\("\{horas\}"\)/);
  // La clave vieja ("{fecha} en {centro}") ya no hace falta: el centro nunca
  // vuelve a mezclarse con la fecha en un solo renglón.
  assert.doesNotMatch(widgetSrc, /chat\.slot_day_center/);
  assert.match(widgetSrc, /link\.href = turno\.href/);
  assert.match(widgetSrc, /link\.textContent = turno\.hora/);
  // La lista de horarios se muestra UNA sola vez: el servidor ya sacó del
  // texto la copia que escribía el modelo (chat_agent._strip_slot_restatements),
  // así que el widget no vuelve a recortar nada acá.
  assert.doesNotMatch(widgetSrc, /textoAntesDeTurnos/);
  assert.match(widgetSrc, /var respuesta = cuerpo\.respuesta \|\| "…";/);
  assert.match(widgetSrc, /if \(!turnos\.length && cuerpo\.opciones/);
  assert.match(widgetSrc, /mas\.textContent = T\("chat\.more_times", null, "Otros horarios"\)/);
});

// ---- addTurnosEnRespuesta driven for real, with a minimal DOM shim -------
// No jsdom in this repo: a hand-rolled element (createElement/createTextNode/
// appendChild/className/textContent/href/setAttribute) is enough to build and
// inspect the real tree the function produces — cheaper than mounting the
// whole widget, and it's the function whose grouping logic actually matters.
function crearNodoDom(tag) {
  return {
    tagName: tag,
    className: "",
    textContent: "",
    href: "",
    attrs: {},
    children: [],
    appendChild: function (hijo) { this.children.push(hijo); return hijo; },
    setAttribute: function (k, v) { this.attrs[k] = v; },
  };
}

function textoDe(nodo) {
  if (nodo.esTexto) return nodo.textContent;
  if (nodo.children && nodo.children.length) {
    return nodo.children.map(textoDe).join("");
  }
  return nodo.textContent || "";
}

function cargarAddTurnosEnRespuesta() {
  const inicio = widgetSrc.indexOf("function addTurnosEnRespuesta");
  const fin = widgetSrc.indexOf("\n  function addStarterChips", inicio);
  const fnSrc = widgetSrc.slice(inicio, fin);
  const window = { document: { documentElement: { getAttribute: () => "es" } } };
  const ctx = vm.createContext({
    window,
    T(key, vars, fallback) { return t(key, vars, fallback); },
  });
  ctx.document = {
    createElement: function (tag) { return crearNodoDom(tag); },
    createTextNode: function (texto) { return { esTexto: true, textContent: texto }; },
  };
  ctx.msgsEl = { appendChild: function () {} };
  ctx.scrollAlFinal = function () {};
  ctx.guardarEstado = function () {};
  return vm.runInContext("(function () {\n" + fnSrc + "\nreturn addTurnosEnRespuesta;\n})()", ctx);
}

test("un día con dos centros: el día sale una sola vez, cada centro es su propio renglón", () => {
  const addTurnosEnRespuesta = cargarAddTurnosEnRespuesta();
  const turnos = [
    { dia: "2026-08-10", hora: "08:00", href: "/service/traumatologia/book?slot=2026-08-10T08:00",
      fecha_etiqueta: "Lunes 10 de agosto", nombre: "Traumatología", centro: "Hospital de Pinamar",
      servicio_href: "/service/traumatologia" },
    { dia: "2026-08-10", hora: "12:00", href: "/service/traumatologia/book?slot=2026-08-10T12:00",
      fecha_etiqueta: "Lunes 10 de agosto", nombre: "Traumatología", centro: "Hospital de Pinamar",
      servicio_href: "/service/traumatologia" },
    { dia: "2026-08-10", hora: "15:00", href: "/service/traumatologia/book?slot=2026-08-10T15:00",
      fecha_etiqueta: "Lunes 10 de agosto", nombre: "Traumatología", centro: "CAPS Ostende",
      servicio_href: "/service/traumatologia" },
    { dia: "2026-08-10", hora: "15:50", href: "/service/traumatologia/book?slot=2026-08-10T15:50",
      fecha_etiqueta: "Lunes 10 de agosto", nombre: "Traumatología", centro: "CAPS Ostende",
      servicio_href: "/service/traumatologia" },
  ];
  const wrap = addTurnosEnRespuesta(turnos, "");
  // 1 nombre de servicio + 1 día + 2 centros = 4 renglones, ninguno repetido.
  assert.equal(wrap.children.length, 4);
  const [servicioEl, diaEl, centro1, centro2] = wrap.children;
  assert.equal(servicioEl.tagName, "a");
  assert.equal(servicioEl.href, "/service/traumatologia");
  assert.equal(servicioEl.textContent, "Traumatología");
  assert.equal(diaEl.className, "stzc-slot-day");
  assert.equal(textoDe(diaEl), "Lunes 10 de agosto");
  assert.equal(centro1.className, "stzc-slot-center");
  assert.equal(textoDe(centro1), "Hospital de Pinamar a las 08:00 y 12:00");
  assert.equal(centro2.className, "stzc-slot-center");
  assert.equal(textoDe(centro2), "CAPS Ostende a las 15:00 y 15:50");
  const horasCentro1 = centro1.children.filter((c) => c.tagName === "a");
  assert.deepEqual(horasCentro1.map((a) => a.textContent), ["08:00", "12:00"]);
  assert.equal(horasCentro1[0].href, "/service/traumatologia/book?slot=2026-08-10T08:00");
});

test("dos servicios en la misma respuesta: cada uno con su propio nombre y árbol de días", () => {
  const addTurnosEnRespuesta = cargarAddTurnosEnRespuesta();
  const turnos = [
    { dia: "2026-08-10", hora: "08:00", href: "/service/traumatologia/book?slot=2026-08-10T08:00",
      fecha_etiqueta: "Lunes 10 de agosto", nombre: "Traumatología", centro: "Hospital de Pinamar",
      servicio_href: "/service/traumatologia" },
    { dia: "2026-08-11", hora: "09:00", href: "/service/pie-diabetico/book?slot=2026-08-11T09:00",
      fecha_etiqueta: "Martes 11 de agosto", nombre: "Pie diabético", centro: "Hospital de Pinamar",
      servicio_href: "/service/pie-diabetico" },
  ];
  // masHref viene vacío porque el servidor lo apaga cuando hay más de un
  // servicio (chat_agent._slots_de_las_tools) — el link de "otros horarios"
  // no debe pintarse, cada servicio se enlaza con su PROPIO servicio_href.
  const wrap = addTurnosEnRespuesta(turnos, "");
  // Por servicio: nombre + día + centro (un solo centro cada uno) = 3 renglones,
  // dos servicios = 6.
  assert.equal(wrap.children.length, 6);
  const nombres = wrap.children.filter((n) => n.className.indexOf("stzc-slot-service") === 0);
  assert.equal(nombres.length, 2);
  assert.equal(nombres[0].textContent, "Traumatología");
  assert.equal(nombres[0].href, "/service/traumatologia");
  assert.equal(nombres[1].textContent, "Pie diabético");
  assert.equal(nombres[1].href, "/service/pie-diabetico");
  // masHref vacío (multi-servicio): "Otros horarios" no se pinta.
  assert.equal(wrap.children.some((n) => n.className === "stzc-more-times"), false);
});

test("un servicio sin destino honesto se pinta como texto plano, nunca como link muerto", () => {
  const addTurnosEnRespuesta = cargarAddTurnosEnRespuesta();
  const turnos = [
    { dia: "2026-08-10", hora: "08:00", href: "/service/traumatologia/book?slot=2026-08-10T08:00",
      fecha_etiqueta: "Lunes 10 de agosto", nombre: "Traumatología", centro: "Hospital de Pinamar" },
  ];
  const wrap = addTurnosEnRespuesta(turnos, "");
  assert.equal(wrap.children[0].tagName, "strong");
  assert.equal(wrap.children[0].textContent, "Traumatología");
});

test("el composer y la lista de mensajes son el componente compartido, no una copia", () => {
  assert.match(widgetSrc, /ChatUI\.crearLista\(msgsEl, \{ prefix: "stzc" \}\)/);
  assert.match(widgetSrc, /ChatUI\.crearComposer\(formEl, \{/);
  assert.match(widgetSrc, /prefix: "stzc"/);
  // El markup del composer ya no vive armado a mano en panel.innerHTML —
  // el <form> nace vacío y crearComposer() lo llena.
  assert.match(widgetSrc, /'<form class="stzc-form" id="stzc-form" autocomplete="off"><\/form>'/);
  assert.doesNotMatch(widgetSrc, /id="stzc-input"/);
  assert.doesNotMatch(widgetSrc, /function iniciarGrabacion\(/);
  assert.doesNotMatch(widgetSrc, /function transcribirAudio\(/);
  assert.doesNotMatch(widgetSrc, /window\.SanaChatUI/);
  assert.match(uiSrc, /function crearComposer\(formEl, opts\)/);
  assert.match(uiSrc, /function crearLista\(el, opts\)/);
  assert.match(uiSrc, /export \{ escHtml, renderMini, crearLista, crearComposer, seguirViewport \}/);
});

test("abrir()/cerrar() queue until ChatUI arrives; there is no compatibility global", () => {
  assert.match(widgetSrc, /export function abrir\(\)/);
  assert.match(widgetSrc, /export function cerrar\(\)/);
  assert.match(widgetSrc, /var pendingOpen = false;/);
  assert.match(widgetSrc, /if \(pendingOpen\) abrir\(true\);/);
  assert.doesNotMatch(widgetSrc, /window\.SanaChat\s*=|window\.ChatWidget\s*=/);
  assert.match(bootstrapSrc, /function requestOpen\(\)/);
  assert.match(bootstrapSrc, /function requestClose\(\)/);
  assert.match(bootstrapSrc, /myGen !== generation/);
  assert.match(bootstrapSrc, /staticUrl\("platform\/chat-widget\.mjs"\)/);
  const bootCode = bootstrapSrc.replace(/\{#[\s\S]*?#\}/g, "");
  assert.doesNotMatch(bootCode, /SanaChat|chat-widget\.js/);
});

test("la barra ocupa toda la altura y conserva estado y conversación entre páginas", () => {
  assert.match(src, /top:0;right:0;bottom:0/);
  assert.match(src, /body\.stzc-chat-open/);
  assert.match(src, /#stzc-page\{width:100%;min-width:0;container:stzc-page \/ inline-size/);
  assert.match(src, /width:calc\(100% - var\(--stzc-sidebar-w\)\)/);
  assert.match(src, /@container stzc-page \(max-width:900px\)/);
  assert.doesNotMatch(src, /padding-right:var\(--stzc-sidebar-w\)/);
  assert.match(src, /sessionStorage\.setItem\(STATE_KEY/);
  assert.match(src, /sessionStorage\.getItem\(STATE_KEY/);
  assert.match(src, /if \(restored && restored\.open\) abrir\(false\)/);
});

test("los layouts públicos responden al ancho restante cuando el chat está abierto", () => {
  assert.match(src, /body\.stzc-chat-open #stzc-page \.centro-head\{display:grid;grid-template-columns:minmax\(0,1fr\)/);
  assert.match(src, /grid-template-areas:'top' 'photo' 'bottom'/);
  assert.match(src, /\.centro-head \.hero-photo img\{aspect-ratio:16\/7\}/);
  assert.match(src, /\.gal-grid\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
  assert.match(src, /\.svc-major-grid\{grid-template-columns:minmax\(0,1fr\)/);
  // The home's search examples wrap, never hide (demo review 2026-11-15).
  assert.doesNotMatch(src, /\.ejemplos a:nth-of-type/);
});

test("permite crear y volver a chats anteriores", () => {
  // "Nuevo chat"/"Historial" salen del catálogo (NUEVO_CHAT_LABEL/
  // HISTORIAL_LABEL, calculados una sola vez para que el título por defecto
  // de una conversación y el texto del botón siempre coincidan).
  assert.match(src, /var NUEVO_CHAT_LABEL = T\("chat\.new_chat", null, "Nuevo chat"\)/);
  assert.match(src, /var HISTORIAL_LABEL = T\("chat\.history", null, "Historial"\)/);
  assert.match(src, /id="stzc-new">' \+ NUEVO_CHAT_LABEL/);
  assert.match(src, /id="stzc-history-btn" aria-pressed="false">' \+\s*\n\s*HISTORIAL_LABEL/);
  assert.match(src, /function crearConversacion\(\)/);
  assert.match(src, /function cargarConversacion\(item\)/);
  assert.match(src, /conversations: conversations\.slice\(0, 12\)/);
  assert.match(src, /session_id: conversationId/);
  assert.match(src, /\/\^\[A-Za-z0-9_-\]\{1,64\}\$\//);
  assert.doesNotMatch(src, /conversacion_id/);
  assert.match(src, /data-conversation-id/);
  assert.match(src, /Dates\.relativo/);
  assert.match(src, /aplicarTituloBackend\(cuerpo\.titulo, conversationId\)/);
});

test("oculta controles vacíos, separa tras una pausa y abre en el último mensaje", () => {
  assert.match(src, /var AUTO_SPLIT_MS = 4 \* 60 \* 60 \* 1000/);
  assert.match(src, /newBtn\.hidden = !puedeCrear/);
  assert.match(src, /historyBtn\.hidden = !hayHistorial/);
  assert.match(src, /conversations\.length > 1/);
  assert.match(src, /filter\(function \(item\) \{ return item\.hasUserMessage; \}\)/);
  assert.match(src, /function separarTrasPausa\(\)/);
  assert.match(src, /msgsEl\.scrollTop = msgsEl\.scrollHeight/);
  assert.match(src, /window\.requestAnimationFrame/);
});

test("el compositor usa textarea, íconos compactos y permite enviar durante el dictado", () => {
  // El markup ("stzc-input" etc.) lo arma chat-ui.js's crearComposer() con
  // el prefix "stzc" que le pasa chat-widget.js — el literal
  // '<textarea class="stzc-input"' ya no existe en ningún archivo (se arma
  // con un template + variable), así que el test clava el TEMPLATE.
  assert.match(uiSrc, /'<textarea class="' \+ p \+ '-input" rows="'/);
  assert.match(widgetSrc, /prefix: "stzc"/);
  assert.match(uiSrc, /rows="' \+ \(opts\.rows \|\| 2\) \+ '"/);
  assert.match(uiSrc, /chat-send-sf-regular\.png/);
  assert.doesNotMatch(uiSrc, /chat-send-up-sf-regular\.png/);
  assert.match(src, /chat-attach-sf-regular\.png/);
  assert.match(src, /chat-microphone-sf-regular\.png/);
  assert.match(src, /\.stzc-attach img,.stzc-mic img\{width:18px;height:18px/);
  assert.match(uiSrc, /var SEND_LABEL = T\("chat\.send_label", null, "Enviar mensaje"\)/);
  assert.match(uiSrc, /aria-label="' \+ SEND_LABEL \+ '" title="' \+ SEND_LABEL/);
  assert.doesNotMatch(src, />Enviar<\/button>/);
  assert.match(src, /navigator\.mediaDevices\.getUserMedia/);
  assert.match(src, /new MediaRecorder/);
  assert.match(src, /fetch\("\/api\/chat\/transcribe"/);
  assert.match(src, /inputEl\.value = result\.body\.texto/);
  assert.match(src, /var sendAfterTranscription = false/);
  assert.match(src, /if \(recorder && recorder\.state === "recording"\)/);
  // El callback genérico onEnviar() (chat-ui.js) reemplaza la llamada
  // directa a enviarMensaje() que había acá — chat-widget.js le pasa
  // exactamente esa función como opts.onEnviar (ver el test de arriba).
  assert.match(uiSrc, /onEnviar\(result\.body\.texto\)/);
  assert.match(src, /Transcribiendo y enviando…/);
  assert.match(src, /event\.key === "Enter" && !event\.shiftKey/);
});

test("los estados y errores del compositor se leen completos", () => {
  assert.match(src, /\.stzc-compose-row\{display:flex;flex-wrap:wrap/);
  assert.match(src, /\.stzc-compose-status\{order:-1;flex:1 0 100%/);
  assert.match(src, /white-space:normal;overflow-wrap:anywhere/);
  assert.match(src, /\.stzc-compose-status:empty\{display:none;\}/);
  assert.doesNotMatch(src, /\.stzc-compose-status\{[^}]*text-overflow:ellipsis/);
});

test("el micrófono nace oculto, aparece con el servicio y se oculta tras un rechazo real", () => {
  // Mismo motivo que arriba: el markup del botón lo arma crearComposer()
  // con el prefix — nace `hidden` (el atributo booleano) en el template.
  assert.match(uiSrc, /var MIC_LABEL = T\("chat\.mic_label", null, "Dictar mensaje"\)/);
  assert.match(uiSrc, /class="' \+ p \+ '-mic" aria-label="' \+ MIC_LABEL \+ '" '/);
  assert.match(uiSrc, /'aria-pressed="false" title="' \+ MIC_LABEL \+ '" hidden>'/);
  assert.match(src, /\.stzc-mic\[hidden\]\{display:none;\}/);
  assert.match(src, /fetch\("\/api\/chat\/voice"/);
  assert.match(src, /voiceServiceAvailable = !!\(body && body\.disponible\)/);
  assert.match(src, /navigator\.permissions\.query\(\{ name: "microphone" \}\)/);
  assert.match(src, /micBtn\.hidden = !voiceServiceAvailable \|\| microphoneBlockedObserved/);
  assert.doesNotMatch(src, /microphonePermissionState = permission\.state/);
  assert.match(src, /if \(permission\.state !== "denied"\)/);
  assert.match(src, /error\.name === "NotAllowedError"/);
  assert.match(src, /error\.name === "SecurityError"/);
  assert.match(src, /error\.name === "NotFoundError"/);
  assert.match(src, /microphoneBlockedObserved = true/);
  assert.match(src, /function ocultarMicrofono\(\)/);
});

test("el pase a WhatsApp está junto al compositor y Nuevo chat/Historial quedan en el header", () => {
  // Responsable del producto, 2026-08-06: WhatsApp continúa la conversación, por eso queda al
  // lado de Adjuntar y Dictar. La barra superior queda para cambiar de chat
  // o recorrer el historial. Sigue siendo un <a>, no un botón de envío.
  assert.match(widgetSrc, /<div class="stzc-head-end">/);
  assert.match(
    widgetSrc,
    /<div class="stzc-head-actions" aria-label="Chats">[\s\S]{0,300}id="stzc-new"[\s\S]{0,300}id="stzc-history-btn"[\s\S]{0,220}<button type="button" class="stzc-close hr-x"/,
  );
  assert.match(widgetSrc, /secondaryAction:\s*\{[\s\S]{0,420}id: "stzc-wa"/);
  assert.match(widgetSrc, /label: T\("chat\.whatsapp_switch", null, "Pasar a WhatsApp"\)/);
  assert.match(uiSrc, /var secondaryAction = opts\.secondaryAction \|\| null/);
  assert.match(uiSrc, /'<a class="' \+ escHtml\(secondaryAction\.className/);
  assert.match(uiSrc, /secondaryActionMarkup \+/);
  assert.doesNotMatch(widgetSrc, /<a class="stzc-action stzc-wa"/);
  assert.match(widgetSrc, /\.stzc-compose-action\{[^}]*font:400 13px var\(--font\)/);
  assert.match(widgetSrc, /\.stzc-compose-action\{[^}]*text-decoration:none/);
  assert.doesNotMatch(widgetSrc, /\.stzc-compose-action\{[^}]*text-decoration:underline/);
  assert.match(widgetSrc, /var chatTitleEl = panel\.querySelector\("\.stzc-title h2"\)/);
  assert.match(widgetSrc, /chatTitleEl\.hidden = hayHistorial/);
  assert.match(widgetSrc, /\.stzc-action\{[^}]*font:400 13\.5px var\(--font\)/);
  // Se pide apenas se abre el panel, no después de la primera respuesta.
  assert.match(widgetSrc, /function pedirEnlaceWhatsapp\(\)/);
  assert.match(widgetSrc, /fetch\("\/api\/chat\/whatsapp"/);
  assert.match(widgetSrc, /separarTrasPausa\(\);\s*\n\s*pedirEnlaceWhatsapp\(\);/);
  // Y la respuesta lo actualiza al enlace CON código de traspaso.
  assert.match(widgetSrc, /cuerpo\.whatsapp\.link\) ponerWhatsapp\(cuerpo\.whatsapp\.link\)/);
  // Sin línea configurada no hay enlace: el control no se dibuja (nunca un
  // control muerto), y una charla vieja guardada con el HTML de la barra
  // anterior no lo revive.
  assert.match(widgetSrc, /link\.indexOf\("https:\/\/wa\.me\/"\) !== 0/);
  // La barra tintada de la ronda anterior ya no existe.
  assert.doesNotMatch(widgetSrc, /Seguí esta conversación por <a href/);
  assert.doesNotMatch(widgetSrc, /\.stzc-wa\{background:var\(--accent-soft\)/);
});

test("con poca charla los mensajes se apoyan sobre el input, no dejan media pantalla vacía", () => {
  assert.match(widgetSrc, /\.stzc-msgs::before\{content:'';margin-top:auto/);
  // `justify-content:flex-end` recorta el principio de una charla larga.
  assert.doesNotMatch(widgetSrc, /\.stzc-msgs\{[^}]*justify-content:flex-end/);
});

test("el composer del panel descansa en una línea; el de la entrevista sigue en dos", () => {
  assert.match(widgetSrc, /rows: 1,\s*\n\s*minAlto: 26,/);
  assert.match(uiSrc, /var minAlto = Number\(opts\.minAlto\) \|\| 48;/);
  assert.match(uiSrc, /Math\.max\(minAlto, inputEl\.scrollHeight\)/);
});

test("public pages bootstrap the native widget and resolve ChatUI from the fingerprint manifest", () => {
  for (const html of refs.slice(0, 5)) {
    assert.match(html, /include "_chat_bootstrap\.html"/);
    assert.doesNotMatch(html, /static\('chat-widget\.js'\)/);
    assert.doesNotMatch(html, /static\('chat-ui\.js'\)/);
  }
  const patientSite = refs[5];
  assert.match(patientSite, /def _chat_bootstrap\(\)/);
  assert.match(patientSite, /chat_js = (site_shell\.)?_chat_bootstrap\(\)/);
  assert.doesNotMatch(patientSite, /url\("chat-widget\.js"\)/);
  assert.doesNotMatch(patientSite, /url\("chat-ui\.js"\)/);
  assert.match(widgetSrc, /importStatic\("platform\/chat-ui\.mjs"\)/);
  assert.match(widgetSrc, /Promise\.all\(\[i18nReady\.catch\(function \(\) \{ return null; \}\), chatUiReady\]\)/);
});

// ---- English cases (2026-08-04, unit 13 — the widget's own chrome) -------
// The widget's chrome reads through the canonical module's t(); running
// it against the actual locales/en/chat.json catalog proves the keys/vars
// line up end to end, not just that a T(...) call exists.
const chatEn = JSON.parse(readFileSync(new URL("../locales/en/chat.json", import.meta.url), "utf8"));
const dateEn = JSON.parse(readFileSync(new URL("../locales/en/date.json", import.meta.url), "utf8"));
const chatEs = JSON.parse(readFileSync(new URL("../locales/es/chat.json", import.meta.url), "utf8"));

function loadI18n(catalog) {
  setCatalog(catalog);
  return { t };
}

test("an English visitor sees the widget's chrome in English, not raw keys or Spanish", () => {
  const i18n = loadI18n({ ...chatEn, ...dateEn });
  assert.equal(i18n.t("chat.title", null, "Chat con IA"), "Chat with AI");
  assert.equal(i18n.t("chat.close", null, "Cerrar Chat con IA"), "Close Chat with AI");
  assert.equal(i18n.t("chat.placeholder", null, "Escribí tu consulta…"), "Type your question…");
  assert.equal(i18n.t("chat.new_chat", null, "Nuevo chat"), "New chat");
  assert.equal(i18n.t("chat.history", null, "Historial"), "History");
  assert.equal(i18n.t("chat.whatsapp_switch", null, "Pasar a WhatsApp"), "Switch to WhatsApp");
  assert.equal(i18n.t("chat.sources", null, "Ver fuentes"), "See sources");
  assert.equal(i18n.t("chat.sign_in", null, "Iniciar sesión"), "Sign in");
  assert.equal(i18n.t("date.at", null, "a las "), "at ");
});

test("las plantillas con variables (saludo, aria de horario) interpolan en inglés", () => {
  const i18n = loadI18n({ ...chatEn, ...dateEn });
  assert.equal(
    i18n.t("chat.greeting", { brand: "Human Rounds" },
      "**Hola, soy el chat con IA de {brand}.**\n\nPodés:\n* buscar un estudio o trámite\n* encontrar un profesional\n* sacar un turno\n* subir una orden médica"),
    "**Hi, I’m the AI chat for Human Rounds.**\n\nI can help you:\n* find a study or procedure\n* find a doctor\n* book an appointment\n* upload a medical referral",
  );
  assert.equal(
    i18n.t("chat.slot_aria", { fecha: "Martes 14 de julio", hora: "09:30" },
      "Sacar turno el {fecha} a las {hora}"),
    "Book the appointment on Martes 14 de julio at 09:30",
  );
});

test("sin catálogo (visita en castellano) el widget rinde exactamente lo de siempre", () => {
  // Sin window.SanaI18n (la página canónica no manda catálogo, ver i18n.md
  // "Why JS literals were untouched (fixed)"), T() devuelve el fallback tal
  // cual — el mismo contrato que ya prueba i18n-fallbacks.test.mjs, acá
  // clavado sobre un puñado representativo de las claves de este archivo.
  function T(key, vars, fallback) { return fallback || key; }
  assert.equal(T("chat.title", null, "Chat con IA"), "Chat con IA");
  assert.equal(T("chat.greeting", { brand: "x" }, "Hola, soy el chat con IA de {brand}."),
    "Hola, soy el chat con IA de {brand}.");
});

test("cada clave que usan chat-widget.js/chat-ui.js existe en los dos idiomas del catálogo chat", () => {
  const re = /\bT\(\s*"(chat\.[a-z0-9_.]+)"/g;
  const used = new Set();
  let m;
  while ((m = re.exec(src)) !== null) used.add(m[1]);
  assert.ok(used.size > 20, `se esperaban bastantes más de 20 claves "chat.*", hubo ${used.size}`);
  for (const key of used) {
    assert.ok(Object.prototype.hasOwnProperty.call(chatEs, key), `falta ${key} en locales/es/chat.json`);
    assert.ok(Object.prototype.hasOwnProperty.call(chatEn, key), `falta ${key} en locales/en/chat.json`);
  }
});

// ---- streaming formats incrementally (the product owner, 2026-08-06) -------------------
// "**" used to stay literal on screen while the answer was still streaming
// in and only turn into real bold once the whole reply had arrived. The fix
// is entirely inside chat-ui.js's shared negrita()/renderMini() (reused, not
// a second markdown renderer) plus chat-widget.js no longer forcing
// `markdown:false` on each chunk — both asserted below.

test("chat-widget.js ya no fuerza texto plano en cada chunk del stream", () => {
  assert.doesNotMatch(widgetSrc, /updateBubble\(esperando, "asst", acumulado, \{ markdown: false \}\)/);
  assert.match(widgetSrc, /lista\.updateBubble\(esperando, "asst", acumulado\);/);
});

test("negrita: una marca **cerrada** se comporta exactamente igual que antes", () => {
  const ui = cargarChatUI();
  assert.equal(ui.renderMini("Hola **mundo**.", []), "Hola <b>mundo</b>.");
  assert.equal(ui.renderMini("sin marcas", []), "sin marcas");
});

test("negrita: una marca ** sin cerrar (texto todavía en vuelo) se pinta en negrita igual, no como asteriscos literales", () => {
  const ui = cargarChatUI();
  const html = ui.renderMini("Podés atenderte en **Hospital de Pinamar", []);
  assert.doesNotMatch(html, /\*\*/);
  assert.match(html, /<b>Hospital de Pinamar<\/b>$/);
});

test("negrita: un par cerrado más una marca abierta después conserva el par y abre la segunda", () => {
  const ui = cargarChatUI();
  const html = ui.renderMini("Hola **mundo**, y **esto sigue", []);
  assert.equal(html, "Hola <b>mundo</b>, y <b>esto sigue</b>");
});

// ---- streaming, driven through the real bubble list --------------------
function crearElementoFalso() {
  const listeners = {};
  const node = {
    className: "", innerHTML: "", textContent: "", children: [],
    isConnected: true, hidden: false, attrs: {},
    appendChild: function (h) { this.children.push(h); return h; },
    remove: function () { this.isConnected = false; },
    querySelector: function (sel) {
      const cls = sel.startsWith(".") ? sel.slice(1) : "";
      return this.children.find((c) => c.className === cls || (c.className || "").split(" ").includes(cls)) || null;
    },
    addEventListener: function (type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    click: function () { (listeners.click || []).forEach((fn) => fn({})); },
    setAttribute: function (k, v) { this.attrs[k] = v; },
    closest: function () { return null; },
  };
  return node;
}

function cargarListaDeChat() {
  const prev = { document: globalThis.document, window: globalThis.window };
  globalThis.window = globalThis;
  globalThis.window.requestAnimationFrame = function (fn) { fn(); };
  globalThis.document = { createElement: function () { return crearElementoFalso(); } };
  const contenedor = crearElementoFalso();
  contenedor.scrollTop = 0;
  contenedor.scrollHeight = 0;
  const lista = ChatUI.crearLista(contenedor, { prefix: "stzc" });
  return { renderMini: ChatUI.renderMini, lista, contenedor, restore() {
    if (prev.document === undefined) delete globalThis.document;
    else globalThis.document = prev.document;
    if (prev.window === undefined) delete globalThis.window;
    else globalThis.window = prev.window;
  } };
}

function simularStream(texto, cortes) {
  // `cortes` = índices donde el texto se parte en chunks — simula la misma
  // acumulación que chat-widget.js's leerEventos hace sobre datos.texto.
  const { renderMini, lista, restore } = cargarListaDeChat();
  try {
    const burbuja = lista.addBubble("wait", "Escribiendo…", { transient: true });
    let acumulado = "";
    let previo = 0;
    const vistas = [];
    for (const corte of [...cortes, texto.length]) {
      acumulado += texto.slice(previo, corte);
      previo = corte;
      lista.updateBubble(burbuja, "asst", acumulado);
      vistas.push(burbuja.innerHTML);
    }
    return { vistas, final: burbuja.innerHTML, esperado: renderMini(texto, []) };
  } finally {
    restore();
  }
}

test("stream con el corte de chunk cayendo justo entre los dos asteriscos: nunca se ve ** literal, y el resultado final es idéntico al no-streaming", () => {
  const texto = "Podés atenderte en **Hospital de Pinamar** o en **CAPS Ostende**.";
  // Corta justo adentro de la primera marca de apertura ("**Hospital" -> "*" | "*Hospital"...)
  const idxPrimeraApertura = texto.indexOf("**Hospital") + 1;
  const cortes = [10, idxPrimeraApertura, texto.indexOf("**Hospital de Pinamar**") + 5,
                  texto.indexOf(" o en "), texto.length - 3];
  const { vistas, final, esperado } = simularStream(texto, cortes);
  for (const html of vistas) assert.doesNotMatch(html, /\*\*/);
  assert.equal(final, esperado);
});

test("stream de una sola pasada (todo el texto en un chunk) también da el mismo resultado que el no-streaming", () => {
  const texto = "**Hospital de Pinamar**: turnos disponibles.";
  const { final, esperado } = simularStream(texto, []);
  assert.equal(final, esperado);
  assert.doesNotMatch(final, /\*\*/);
});

// Geometry, viewport events, pinch zoom, open/close and lifecycle are
// exercised against the real exports/DOM in chat-viewport-browser.test.mjs.
// Keep only the consumer/CSS boundary here; no source-sliced implementation.
test("widget delegates viewport tracking at its existing responsive CSS breakpoint", () => {
  assert.match(widgetSrc, /ChatUI\.seguirViewport\(panel, \{/);
  assert.match(widgetSrc, /media: "\(max-width:700px\)"/);
  assert.match(widgetSrc, /@media \(max-width:700px\)\{html\.stzc-chat-open-root/);
  assert.doesNotMatch(widgetSrc, /function ajustarPorTeclado|function debeSeguirTeclado/);
});

test("keyboard-dismiss tap is scoped to the message area (.stzc-msgs), not the whole panel", () => {
  // Positive-scoping: the blur only fires for taps INSIDE .stzc-msgs. The old
  // panel-wide listener with a negative guard ("return unless .stzc-form")
  // also caught the embedded Account login UI, blurring its inputs the
  // instant they were tapped to focus them (Codex review 2026-08-11).
  assert.match(
    widgetSrc,
    /panel\.addEventListener\("click", function \(ev\) \{\s*\n\s*if \(!ev\.target\.closest\("\.stzc-msgs"\)\) return;/,
  );
  // The negative composer guard is gone — the composer is reachable again
  // purely because it is outside the message area, same as the auth region.
  assert.doesNotMatch(widgetSrc, /if \(ev\.target\.closest\("\.stzc-form"\)\) return;/);
  assert.match(widgetSrc, /activo\.tagName === "TEXTAREA" \|\| activo\.tagName === "INPUT"/);
  assert.match(widgetSrc, /typeof activo\.blur === "function"/);
  assert.match(widgetSrc, /activo\.blur\(\)/);
});

// ---- keyboard-dismiss handler driven behaviorally (Codex review 2026-08-11) --
// Source-regex above pins the selector; these drive the REAL extracted handler
// against a fake panel + document so the operability contract holds end to end:
// a tap in the message area drops the keyboard, while the composer and the
// embedded Account login UI keep their inputs focusable.
function cargarHandlerTeclado() {
  const startMarker = 'panel.addEventListener("click", function (ev) {';
  const start = widgetSrc.indexOf(startMarker);
  assert.ok(start !== -1, "the panel has a click handler");
  const region = widgetSrc.slice(start);
  // The handler body ends at the first "\n  });" that closes addEventListener.
  const closeIdx = region.indexOf("\n  });");
  assert.ok(closeIdx !== -1, "the panel click handler closes with });");
  const fullSrc = region.slice(0, closeIdx + "\n  });".length);
  // Disambiguate from the second panel click handler (the [data-chat-action]
  // dispatcher) — this one is the keyboard-dismiss handler.
  assert.match(fullSrc, /document\.activeElement/);
  assert.doesNotMatch(fullSrc, /data-chat-action/);
  // Strip the "panel.addEventListener(\"click\", " wrapper and the trailing
  // ");" so what remains is a bare function expression we can evaluate into a
  // callable bound to our fake panel + document.
  const fnSrc = fullSrc
    .replace(/^panel\.addEventListener\("click", /, "")
    .replace(/\);\s*$/, "");
  return function buildHandler(opts) {
    const activeEl = opts.activeEl;
    const panelContains = opts.panelContains != null ? opts.panelContains : () => true;
    const panel = { contains: panelContains };
    const document = { activeElement: activeEl };
    const ctx = vm.createContext({ panel, document });
    const fn = vm.runInContext("(" + fnSrc + ")", ctx);
    return fn;
  };
}

function fakeTapTarget(insideSelector) {
  // `closest(sel)` returns a truthy anchor iff the tapped element lives within
  // `insideSelector`, else null — enough to model the DOM walk the handler does.
  return { closest: (sel) => (sel === insideSelector ? { matched: true } : null) };
}

test("a tap in the message area blurs an active composer input (keyboard drops)", () => {
  const build = cargarHandlerTeclado();
  let blurred = 0;
  const fn = build({
    activeEl: { tagName: "TEXTAREA", blur: () => { blurred++; } },
    panelContains: () => true,
  });
  fn({ target: fakeTapTarget(".stzc-msgs") });
  assert.equal(blurred, 1);
});

test("a tap on the composer (.stzc-form) never blurs its own input — it stays operable", () => {
  const build = cargarHandlerTeclado();
  let blurred = 0;
  const fn = build({
    activeEl: { tagName: "TEXTAREA", blur: () => { blurred++; } },
    panelContains: () => true,
  });
  fn({ target: fakeTapTarget(".stzc-form") });
  assert.equal(blurred, 0);
});

test("a tap on the embedded login (.stzc-auth) never blurs its input — the control stays operable", () => {
  // The Codex finding: at click time the auth input is already
  // document.activeElement, so the old panel-wide handler blurred it on the
  // very tap that focused it. The message-area scope makes that impossible.
  const build = cargarHandlerTeclado();
  let blurred = 0;
  const fn = build({
    activeEl: { tagName: "INPUT", blur: () => { blurred++; } },
    panelContains: () => true,
  });
  fn({ target: fakeTapTarget(".stzc-auth") });
  assert.equal(blurred, 0);
});

test("a tap in the message area only ever blurs INPUT/TEXTAREA — not a focused button", () => {
  // A focused <button> (e.g. a chip) inside the message area is left alone.
  const build = cargarHandlerTeclado();
  let blurred = 0;
  const fn = build({
    activeEl: { tagName: "BUTTON", blur: () => { blurred++; } },
    panelContains: () => true,
  });
  fn({ target: fakeTapTarget(".stzc-msgs") });
  assert.equal(blurred, 0);
});

test("a tap in the message area ignores an input that lives outside the panel", () => {
  // The panel.contains(activo) guard stays: a page-level input (e.g. the main
  // search box) is not blurred by a tap inside the chat's message area.
  const build = cargarHandlerTeclado();
  let blurred = 0;
  const fn = build({
    activeEl: { tagName: "INPUT", blur: () => { blurred++; } },
    panelContains: () => false,
  });
  fn({ target: fakeTapTarget(".stzc-msgs") });
  assert.equal(blurred, 0);
});
