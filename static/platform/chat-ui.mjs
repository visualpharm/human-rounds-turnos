/* static/platform/chat-ui.mjs — shared chat UI building blocks: the message
   list (bubbles + typing indicator + tappable option rows) and the composer
   (auto-growing textarea + attach + mic dictation + paste-an-image), used by
   BOTH chat surfaces of the site — static/platform/chat-widget.mjs (booking
   assistant) and entrevista.js (pre-consult interview). One component, ONE
   place that knows how to render a bubble or run MediaRecorder ->
   /api/chat/transcribe (docs/architecture/chat-y-whatsapp.md). Each page
   keeps its OWN visual chrome (panel, sidebar, top bar, sizing) — this
   module only builds the two pieces that were duplicated between the booking
   widget and the old entrevista.js:
   how a message list renders, and how the composer behaves.

   Canonical platform ES module (the chat-ui.js cutover, 2026-09-05): named
   exports only, no browser global, no classic root twin. It awaits its
   canonical I18n dependency before it can render; unlike week.mjs, an i18n
   load failure degrades to the Spanish call-site literals instead of failing
   the whole module — that is the behavior the classic file shipped (the chat
   kept working in Spanish), and the consumers' own recovery paths are for
   THIS module being unavailable, not for a missing translation.

   API (named exports):
     renderMini(texto, enlaces) -> html string. '**bold**', '* '/'- ' list
       lines, and internal links (from `enlaces`, {label,href} pairs) applied
       over already-escaped text — no library.
     crearLista(el, opts) -> {addBubble, updateBubble, setTyping, addOpciones,
       addFuentes, scrollToEnd, el}
       opts.prefix: CSS class prefix ("stzc" or "iv") — every node this
       builds gets `${prefix}-msg`, `${prefix}-msg--user|asst`,
       `${prefix}-typing`, `${prefix}-chips`/`${prefix}-chip`,
       `${prefix}-sources`. Each page's own stylesheet already owns those
       class names (or gets them, for a page adopting this for the first
       time) — crearLista never injects CSS itself.
       addBubble(role, texto, opts2) -> the bubble element. role is "user" or
         "assistant"/"asst" (either spelling accepted — the two pages used to
         disagree). opts2: {enlaces, transient (not saved/counted), markdown
         (default true for the assistant, false forces plain text), adjunto
         (true: plain label after a `${prefix}-attach-ico` span)}.
       updateBubble(el, role, texto, opts2) -> re-renders an EXISTING bubble
         returned by addBubble, same opts2 as above — for progressive reveal
         (call repeatedly on the same element as more text streams in, then
         once more with the final formatted text). Does not scroll/append a
         new node.
       setTyping(on) -> shows/hides ONE typing-dots bubble.
       addOpciones(etiquetas, onPick) -> renders a row of tappable buttons
         under the last message; tapping one removes the row and calls
         onPick(etiqueta). Returns the row element.
       addFuentes(fuentes) -> a collapsed "Ver fuentes" block (used by the
         widget's RAG citations; harmless/unused elsewhere).
     crearComposer(formEl, opts) -> {focus, getValue, setValue, setDisabled,
       ajustarTextarea, refrescarMicrofono}
       formEl: an EMPTY <form> element already in the DOM (the page owns
         where it sits and its outer styling) — crearComposer fills its
         innerHTML and wires every listener.
       opts.prefix, opts.placeholder, opts.maxLength (default 2000),
       opts.onEnviar(texto, adjunto) — called on submit (adjunto is
         {filename,mime,data_b64} or undefined). The caller decides what
         "enviar" means (POST to its own endpoint) — this module never talks
         to a chat endpoint directly, only to the two shared capability
         endpoints (voice status + transcribe).
       opts.attach (default true), opts.accept (default
         "image/*,application/pdf"), opts.soloImagen (default false — when
         true, only image/* is accepted AND a client-side MIME check runs
         before onEnviar, matching the interview's image-only contract).
       opts.mic (default true) — hidden automatically when
         GET /api/chat/voice says unavailable, same capability probe both
         pages always ran.
       opts.iconos: {attach, mic, send} — icon <img> src overrides (both
         pages default to the shared /static/icons/chat-*.png set).
     seguirViewport(el, {media, enabled, onResize}) -> {update, dispose}
       Fits a fixed chat surface to the visual viewport at scale 1. The
       caller owns its breakpoint/open state; null media means every width.
       Pinch zoom keeps the previous layout. Desktop/closed/disposed states
       restore the original inline styles. BFCache suspends and resumes.
*/
"use strict";

async function importDep(name, nodeUrl) {
  if (typeof window === "undefined") return import(nodeUrl);
  const url = window.SANA_STATIC && window.SANA_STATIC[name];
  if (!url) throw new Error("[static] " + name + " is not in window.SANA_STATIC");
  return import(url);
}

const [I18n] = await Promise.all([
  importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url))
    // Behavior frozen from the classic file: a failed i18n load leaves the
    // chat working in Spanish (the call-site literals), never breaks it.
    .catch(function () { return null; }),
]);

function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

// Shared keyboard viewport handling. Do not focus the composer or change
// scroll position here: each chat owns whether the reader is at the end.
function seguirViewport(el, opts) {
  opts = opts || {};
  var win = el.ownerDocument.defaultView;
  var vv = win.visualViewport;
  var media = opts.media && win.matchMedia ? win.matchMedia(opts.media) : null;
  var original = ["top", "height"].map(function (name) {
    return { name: name, value: el.style.getPropertyValue(name), priority: el.style.getPropertyPriority(name) };
  });
  var owned = false, disposed = false, suspended = false, listening = false;
  var orientationTimer = null;

  function restore() {
    if (!owned) return;
    original.forEach(function (p) {
      if (p.value) el.style.setProperty(p.name, p.value, p.priority);
      else el.style.removeProperty(p.name);
    });
    owned = false;
  }
  function update() {
    if (disposed || suspended) return;
    if (!el.isConnected) { dispose(); return; }
    var enabled = !opts.enabled || opts.enabled();
    if (!enabled || (opts.media && (!media || !media.matches)) || !vv) {
      restore();
    } else {
      // Shrinking a chat to the zoomed viewport fights native pinch/pan.
      // Keep its last unzoomed dimensions until scale 1 is restored.
      if (Math.abs((vv.scale || 1) - 1) > 0.01 || !Number.isFinite(vv.height) || vv.height <= 0) return;
      el.style.top = Math.max(0, vv.offsetTop || 0) + "px";
      el.style.height = vv.height + "px";
      owned = true;
    }
    if (enabled && opts.onResize) opts.onResize();
  }
  function orientationChanged() {
    update();
    win.clearTimeout(orientationTimer);
    orientationTimer = win.setTimeout(update, 60);
  }
  function listen() {
    if (listening || disposed) return;
    listening = true;
    if (vv) {
      vv.addEventListener("resize", update);
      vv.addEventListener("scroll", update);
    }
    win.addEventListener("resize", update);
    win.addEventListener("orientationchange", orientationChanged);
    if (media && media.addEventListener) media.addEventListener("change", update);
  }
  function unlisten() {
    if (!listening) return;
    listening = false;
    if (vv) {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
    }
    win.removeEventListener("resize", update);
    win.removeEventListener("orientationchange", orientationChanged);
    if (media && media.removeEventListener) media.removeEventListener("change", update);
    win.clearTimeout(orientationTimer);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    unlisten();
    restore();
    win.removeEventListener("pagehide", pagehide);
    win.removeEventListener("pageshow", pageshow);
  }
  function pagehide(event) {
    if (!event.persisted) { dispose(); return; }
    suspended = true;
    unlisten();
    restore();
  }
  function pageshow() {
    if (disposed) return;
    suspended = false;
    listen();
    update();
  }
  win.addEventListener("pagehide", pagehide);
  win.addEventListener("pageshow", pageshow);
  listen();
  update();
  return { update: update, dispose: dispose };
}

// ---- markdown-lite: **bold**, '* '/'- ' lines as <ul>, internal links ---
function escHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}
function negrita(escapado) {
  // Balanced markers (the normal, finished case) render exactly as
  // before — byte-identical to what a one-shot, non-streaming reply has
  // always produced. Streaming (the product owner, 2026-08-06: "when you see two
  // stars, switch to bold" — don't wait for the closing pair) can call
  // this on TEXT STILL BEING TYPED, where the last `**` opened a bold run
  // that hasn't closed yet; an odd count means exactly that. Everything
  // up to that last marker still pairs up normally, and the tail after it
  // renders bold immediately instead of sitting on screen as literal
  // asterisks until the rest of the sentence arrives.
  var ocurrencias = (escapado.match(/\*\*/g) || []).length;
  if (ocurrencias % 2 === 0) {
    return escapado.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  }
  var idx = escapado.lastIndexOf("**");
  var antes = escapado.slice(0, idx).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  var despues = escapado.slice(idx + 2);
  return antes + "<b>" + despues + "</b>";
}
// Un plegado 1 a 1 (misma longitud que el original) para poder comparar sin
// acentos ni mayúsculas SIN perder los índices: el enlace se pinta con el
// texto tal como lo escribió el asistente, no con la etiqueta del catálogo.
var ACENTOS = { "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u",
                "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u", "ñ": "n",
                "ç": "c" };
function plegar(s) {
  return String(s || "").toLowerCase().replace(/[^\u0000-\u007F]/g, function (ch) {
    return Object.prototype.hasOwnProperty.call(ACENTOS, ch) ? ACENTOS[ch] : ch;
  });
}
function textoConEnlaces(texto, enlaces) {
  var candidatos = (Array.isArray(enlaces) ? enlaces : [])
    .map(function (enlace) {
      var href = String((enlace && enlace.href) || "");
      var label = String((enlace && enlace.label) || "").trim();
      if (!label || href.charAt(0) !== "/" || href.charAt(1) === "/") return null;
      return { label: label, plano: plegar(label), href: href };
    })
    .filter(Boolean)
    .sort(function (a, b) { return b.label.length - a.label.length; });
  var resto = String(texto || "");
  var out = [];
  while (resto) {
    var plano = plegar(resto);
    var elegido = null;
    var indice = -1;
    candidatos.forEach(function (enlace) {
      var pos = plano.indexOf(enlace.plano);
      if (pos < 0) return;
      if (indice < 0 || pos < indice ||
          (pos === indice && enlace.label.length > elegido.label.length)) {
        elegido = enlace;
        indice = pos;
      }
    });
    if (!elegido) {
      out.push(escHtml(resto));
      break;
    }
    out.push(escHtml(resto.slice(0, indice)));
    out.push('<a href="' + escHtml(elegido.href) + '">' +
             escHtml(resto.substr(indice, elegido.label.length)) + "</a>");
    resto = resto.slice(indice + elegido.label.length);
  }
  return out.join("");
}
function renderMini(texto, enlaces) {
  var lineas = String(texto || "").split("\n");
  var out = [];
  var listaBuf = [];
  function flushLista() {
    if (listaBuf.length) {
      out.push("<ul>" + listaBuf.map(function (li) { return "<li>" + li + "</li>"; }).join("") + "</ul>");
      listaBuf = [];
    }
  }
  lineas.forEach(function (linea) {
    var m = linea.match(/^(?:\*|-)\s+(.*)$/);
    if (m) {
      listaBuf.push(negrita(textoConEnlaces(m[1], enlaces)));
    } else {
      flushLista();
      out.push(negrita(textoConEnlaces(linea, enlaces)));
    }
  });
  flushLista();
  // Un <ul> ya abre y cierra línea por sí mismo: el <br> que lo separaba
  // del título de arriba y del texto de abajo agregaba una línea en blanco
  // de más en cada bloque de turnos (2026-08-02).
  return out.reduce(function (html, trozo, i) {
    if (!i) return trozo;
    var pegado = out[i - 1].slice(-5) === "</ul>" || trozo.slice(0, 4) === "<ul>";
    return html + (pegado ? "" : "<br>") + trozo;
  }, "");
}

function esAsistente(role) {
  return role === "asst" || role === "assistant";
}

// ---- message list: bubbles + typing indicator + option rows -----------
function crearLista(el, opts) {
  opts = opts || {};
  var p = opts.prefix || "chat";

  function scrollToEnd() {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        el.scrollTop = el.scrollHeight;
      });
    });
  }

  // An attachment bubble: the page's `${prefix}-attach-ico` (an Icons8
  // paperclip mask in its own stylesheet) before the label — never a
  // Unicode pictograph in the text.
  function pintarAdjunto(d, texto) {
    d.textContent = "";
    var ico = document.createElement("span");
    ico.className = p + "-attach-ico";
    ico.setAttribute("aria-hidden", "true");
    d.appendChild(ico);
    d.appendChild(document.createTextNode(String(texto == null ? "" : texto)));
  }

  function addBubble(role, texto, opts2) {
    opts2 = opts2 || {};
    var asst = esAsistente(role);
    // Keep the caller's own role string in the class name (the widget uses
    // a third role, "wait", for its transient "Escribiendo…" bubble, with
    // its own italic/muted CSS) — only "assistant" normalizes to "asst".
    var clase = asst ? "asst" : role;
    var d = document.createElement("div");
    d.className = p + "-msg " + p + "-msg--" + clase;
    var markdown = opts2.markdown !== false;
    if (opts2.adjunto) pintarAdjunto(d, texto);
    else if (asst && markdown) d.innerHTML = renderMini(texto, opts2.enlaces);
    else d.textContent = String(texto == null ? "" : texto);
    el.appendChild(d);
    scrollToEnd();
    return d;
  }

  function updateBubble(el, role, texto, opts2) {
    // Re-renders an EXISTING bubble in place (same node, no re-append) —
    // for progressive reveal (the product owner, 2026-08-02: stream the answer instead
    // of showing it all at once): the caller keeps calling this on the
    // SAME element as more text arrives, then once with the final,
    // fully-formatted text (markdown/enlaces applied) to finish it off.
    opts2 = opts2 || {};
    var asst = esAsistente(role);
    var clase = asst ? "asst" : role;
    el.className = p + "-msg " + p + "-msg--" + clase;
    var markdown = opts2.markdown !== false;
    if (asst && markdown) el.innerHTML = renderMini(texto, opts2.enlaces);
    else el.textContent = String(texto == null ? "" : texto);
    scrollToEnd();
    return el;
  }

  function setTyping(on) {
    var existente = el.querySelector("." + p + "-typing");
    if (on) {
      if (existente) return;
      var d = document.createElement("div");
      d.className = p + "-typing";
      d.setAttribute("aria-label", T("chat.typing", null, "Escribiendo…"));
      d.innerHTML = "<span></span><span></span><span></span>";
      el.appendChild(d);
      scrollToEnd();
    } else if (existente) {
      existente.remove();
    }
  }

  function addOpciones(etiquetas, onPick) {
    var row = document.createElement("div");
    row.className = p + "-chips";
    (etiquetas || []).forEach(function (etq) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = p + "-chip";
      b.textContent = etq;
      b.addEventListener("click", function () {
        if (!el.isConnected) return;
        row.remove();
        onPick(etq);
      });
      row.appendChild(b);
    });
    el.appendChild(row);
    scrollToEnd();
    return row;
  }

  function addFuentes(fuentes) {
    var oficiales = (Array.isArray(fuentes) ? fuentes : []).filter(function (f) {
      return f && String(f.titulo || "").trim() && /^https:\/\//.test(String(f.url || ""));
    });
    if (!oficiales.length) return null;
    var wrap = document.createElement("details");
    wrap.className = p + "-sources";
    var title = document.createElement("summary");
    title.textContent = T("chat.sources", null, "Ver fuentes");
    wrap.appendChild(title);
    oficiales.forEach(function (f) {
      var link = document.createElement("a");
      link.href = String(f.url);
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = String(f.titulo).trim();
      wrap.appendChild(link);
    });
    el.appendChild(wrap);
    scrollToEnd();
    return wrap;
  }

  return {
    el: el, addBubble: addBubble, updateBubble: updateBubble, setTyping: setTyping,
    addOpciones: addOpciones, addFuentes: addFuentes, scrollToEnd: scrollToEnd,
  };
}

// ---- composer: textarea autogrow + attach + mic dictation --------------
var DEFAULT_ICONS = {
  attach: "/static/icons/chat-attach-sf-regular.png",
  mic: "/static/icons/chat-microphone-sf-regular.png",
  send: "/static/icons/chat-send-sf-regular.png",
};

function crearComposer(formEl, opts) {
  opts = opts || {};
  var p = opts.prefix || "chat";
  var maxLength = opts.maxLength || 2000;
  var accept = opts.soloImagen ? "image/*" : (opts.accept || "image/*,application/pdf");
  var iconos = Object.assign({}, DEFAULT_ICONS, opts.iconos || {});
  var attachOn = opts.attach !== false;
  var micOn = opts.mic !== false;
  // A caller may add one destination link beside the attachment and
  // dictation controls. It remains an <a>, rather than being restyled as a
  // submit button, because it takes the person to another app.
  var secondaryAction = opts.secondaryAction || null;
  var onEnviar = typeof opts.onEnviar === "function" ? opts.onEnviar : function () {};
  function composerActivo() {
    // A closed widget hides its panel (and this form with it). A finished
    // interview hides the form itself. Either way a late transcription,
    // attach, or submit must not send or overwrite whatever the person
    // typed after the surface went away.
    return !!(formEl && formEl.isConnected && !formEl.closest("[hidden]"));
  }
  // Calculadas una sola vez: se reusan al armar el markup Y al resetear el
  // botón de dictado después de grabar (limpiarGrabacion), así que las dos
  // rutas siempre dicen lo mismo en el idioma activo.
  var MIC_LABEL = T("chat.mic_label", null, "Dictar mensaje");
  var MIC_STOP_LABEL = T("chat.mic_stop_label", null, "Detener dictado");
  var SEND_LABEL = T("chat.send_label", null, "Enviar mensaje");
  var secondaryActionMarkup = secondaryAction ?
    '<a class="' + escHtml(secondaryAction.className || (p + '-secondary-action')) + '" ' +
    'id="' + escHtml(secondaryAction.id || '') + '" target="_blank" rel="noopener" hidden ' +
    'aria-label="' + escHtml(secondaryAction.label || '') + '" ' +
    'title="' + escHtml(secondaryAction.title || secondaryAction.label || '') + '">' +
    (secondaryAction.iconClass ? '<span class="' + escHtml(secondaryAction.iconClass) + '" aria-hidden="true"></span>' : '') +
    escHtml(secondaryAction.label || '') + '</a>' : '';

  formEl.innerHTML =
    '<p class="' + p + '-voice-disclosure" style="font-size:16px;color:var(--text);" hidden>' + escHtml(T("ai.notice.voice", null, "El dictado usa inteligencia artificial. Tu audio completo se envía al proveedor; puede incluir lo que te identifica.")) + '</p>' +
    '<textarea class="' + p + '-input" rows="' + (opts.rows || 2) + '" ' +
    'placeholder="' + escHtml(opts.placeholder || T("chat.composer_placeholder_default", null, "Escribí tu mensaje…")) + '" ' +
    'aria-label="' + T("chat.message_aria", null, "Mensaje") + '" autocomplete="off" maxlength="' + maxLength + '"></textarea>' +
    '<div class="' + p + '-compose-row">' +
    (attachOn ?
      '<button type="button" class="' + p + '-attach" ' +
      'aria-label="' + escHtml(opts.attachLabel || T("chat.attach_label_default", null, "Adjuntar una foto")) + '" ' +
      'title="' + escHtml(opts.attachTitle || T("chat.attach_title_default", null, "Adjuntar una foto (o pegala con Ctrl/⌘V)")) + '">' +
      '<img src="' + iconos.attach + '" alt="" aria-hidden="true"></button>' +
      '<input type="file" class="' + p + '-file" accept="' + accept + '" hidden>'
      : "") +
    (micOn ?
      '<button type="button" class="' + p + '-mic" aria-label="' + MIC_LABEL + '" ' +
      'aria-pressed="false" title="' + MIC_LABEL + '" hidden>' +
      '<img src="' + iconos.mic + '" alt="" aria-hidden="true"></button>'
      : "") +
    secondaryActionMarkup +
    '<span class="' + p + '-compose-status" role="status"></span>' +
    '<button class="' + p + '-send" type="submit" aria-label="' + SEND_LABEL + '" title="' + SEND_LABEL + '">' +
    '<img src="' + iconos.send + '" alt="" aria-hidden="true"></button></div>';

  var inputEl = formEl.querySelector("." + p + "-input");
  var attachBtn = formEl.querySelector("." + p + "-attach");
  var fileEl = formEl.querySelector("." + p + "-file");
  var micBtn = formEl.querySelector("." + p + "-mic");
  var sendBtn = formEl.querySelector("." + p + "-send");
  var statusEl = formEl.querySelector("." + p + "-compose-status");
  var voiceNotice = formEl.querySelector("." + p + "-voice-disclosure");
  var secondaryActionEl = secondaryAction && secondaryAction.id ?
    formEl.querySelector("#" + secondaryAction.id) : null;

  var recorder = null;
  var recorderStream = null;
  var recorderChunks = [];
  var recorderTimer = null;
  var sendAfterTranscription = false;
  var voiceServiceAvailable = false;
  var microphoneBlockedObserved = false;

  // `minAlto`: how tall the textarea rests when it is empty. 48 (two lines
  // of room) is what the full-page interview uses; the side panel passes a
  // single line so its composer does not sit there as a half-empty box.
  var minAlto = Number(opts.minAlto) || 48;

  function ajustarTextarea() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(120, Math.max(minAlto, inputEl.scrollHeight)) + "px";
  }

  function estadoCompositor(texto, error) {
    if (!statusEl) return;
    statusEl.textContent = texto || "";
    statusEl.style.color = error ? "var(--danger)" : "";
  }

  function setDisabled(disabled) {
    inputEl.disabled = disabled;
    sendBtn.disabled = disabled;
    if (attachBtn) attachBtn.disabled = disabled;
    if (micBtn) micBtn.disabled = disabled;
  }

  // ---- attach: button, file input, paste (Ctrl/⌘V) ----------------------
  function handleFile(file) {
    if (!file) return;
    if (opts.soloImagen && file.type && file.type.indexOf("image/") !== 0) {
      estadoCompositor(T("chat.attach_error_image_only", null, "Sólo se puede adjuntar una foto."), true);
      return;
    }
    var reader = new FileReader();
    reader.onload = function () {
      if (!composerActivo()) return;
      var texto = inputEl.value.trim();
      inputEl.value = "";
      ajustarTextarea();
      onEnviar(texto, { filename: file.name || "", mime: file.type || "", data_b64: reader.result });
    };
    reader.readAsDataURL(file);
  }

  if (attachBtn) {
    attachBtn.addEventListener("click", function () { fileEl.click(); });
    fileEl.addEventListener("change", function () {
      var f = fileEl.files && fileEl.files[0];
      fileEl.value = "";
      handleFile(f);
    });
  }
  inputEl.addEventListener("paste", function (ev) {
    var items = (ev.clipboardData && ev.clipboardData.items) || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type && items[i].type.indexOf("image/") === 0) {
        var file = items[i].getAsFile();
        if (file) {
          ev.preventDefault();
          handleFile(file);
          return;
        }
      }
    }
  });

  // ---- dictado: MediaRecorder -> /api/chat/transcribe --------------------
  function mimeGrabacion() {
    var candidatos = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];
    for (var i = 0; i < candidatos.length; i++) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(candidatos[i])) {
        return candidatos[i];
      }
    }
    return "";
  }

  function limpiarGrabacion() {
    if (recorderTimer) window.clearTimeout(recorderTimer);
    recorderTimer = null;
    if (recorderStream) recorderStream.getTracks().forEach(function (t) { t.stop(); });
    recorderStream = null;
    recorder = null;
    if (micBtn) {
      micBtn.setAttribute("aria-pressed", "false");
      micBtn.setAttribute("aria-label", MIC_LABEL);
      micBtn.title = MIC_LABEL;
    }
  }

  function ocultarMicrofono() {
    limpiarGrabacion();
    if (micBtn) micBtn.hidden = true;
    estadoCompositor("");
  }

  function sincronizarMicrofono() {
    if (!micBtn) return;
    micBtn.hidden = !voiceServiceAvailable || microphoneBlockedObserved;
    if (voiceNotice && micBtn.hidden) voiceNotice.hidden = true;
    if (micBtn.hidden && recorder) ocultarMicrofono();
  }

  // The dictation notice (ai.notice.voice, a prior disclosure: decisions
  // 2026-10-14) is shown by the FIRST press of the microphone, which does not
  // record; the next press records. Rendered with the composer it took three
  // lines of the answer field on a phone for everyone, dictating or not, and
  // pushed the question and its choices out of view (the product owner's hub, 2026-11-07).
  var voiceNoticeSeen = false;
  function avisoDictadoPendiente() {
    if (!voiceNotice || voiceNoticeSeen) return false;
    voiceNoticeSeen = true;
    voiceNotice.hidden = false;
    return true;
  }
  function ocultarAvisoDictado() {
    if (voiceNotice && voiceNoticeSeen) voiceNotice.hidden = true;
  }

  function revisarPermisoMicrofono() {
    if (!micOn || !micBtn) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      ocultarMicrofono();
      return;
    }
    fetch("/api/chat/voice", { cache: "no-store" }).then(function (r) {
      return r.ok ? r.json() : {};
    }).then(function (body) {
      voiceServiceAvailable = !!(body && body.disponible);
      sincronizarMicrofono();
      if (!voiceServiceAvailable || !navigator.permissions || !navigator.permissions.query) return;
      navigator.permissions.query({ name: "microphone" }).then(function (permission) {
        permission.addEventListener("change", function () {
          if (permission.state !== "denied") {
            microphoneBlockedObserved = false;
            sincronizarMicrofono();
          }
        });
      }).catch(function () {});
    }).catch(function () {
      voiceServiceAvailable = false;
      sincronizarMicrofono();
    });
  }

  function transcribirAudio(blob) {
    var enviarCuandoTermine = sendAfterTranscription;
    var envioIniciado = false;
    var valorAlEmpezar = inputEl.value;
    sendAfterTranscription = false;
    micBtn.disabled = true;
    sendBtn.disabled = true;
    estadoCompositor(T("chat.mic_transcribing", null, "Transcribiendo…"));
    var reader = new FileReader();
    reader.onload = function () {
      fetch("/api/chat/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio: { mime: blob.type || "audio/webm", data_b64: reader.result } }),
      }).then(function (response) {
        return response.json().catch(function () { return {}; }).then(function (body) {
          return { ok: response.ok, body: body };
        });
      }).then(function (result) {
        if (!composerActivo()) return;
        if (!result.ok || !result.body.texto) {
          if (result.body.error === "voz_no_configurada") {
            voiceServiceAvailable = false;
            ocultarMicrofono();
            return;
          }
          estadoCompositor(result.body.mensaje || T("chat.mic_transcribe_error", null, "No pudimos transcribir. Podés escribir el mensaje."), true);
          return;
        }
        if (enviarCuandoTermine) {
          estadoCompositor("");
          envioIniciado = true;
          ocultarAvisoDictado();
          onEnviar(result.body.texto);
          return;
        }
        if (inputEl.value !== valorAlEmpezar) {
          estadoCompositor("");
          return;
        }
        inputEl.value = result.body.texto;
        ajustarTextarea();
        estadoCompositor("");
        inputEl.focus();
      }).catch(function () {
        if (!composerActivo()) return;
        estadoCompositor(T("chat.mic_transcribe_error", null, "No pudimos transcribir. Podés escribir el mensaje."), true);
      }).finally(function () {
        if (!envioIniciado) {
          micBtn.disabled = false;
          sendBtn.disabled = false;
        }
      });
    };
    reader.onerror = function () {
      sendAfterTranscription = false;
      micBtn.disabled = false;
      sendBtn.disabled = false;
      estadoCompositor(T("chat.mic_read_error", null, "No pudimos leer la grabación."), true);
    };
    reader.readAsDataURL(blob);
  }

  function detenerGrabacion() {
    if (recorder && recorder.state === "recording") recorder.stop();
  }

  function iniciarGrabacion() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      estadoCompositor(T("chat.mic_unsupported", null, "Este navegador no permite dictar mensajes."), true);
      return;
    }
    sendAfterTranscription = false;
    estadoCompositor(T("chat.mic_opening", null, "Abriendo el micrófono…"));
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      recorderStream = stream;
      recorderChunks = [];
      var mime = mimeGrabacion();
      recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      recorder.addEventListener("dataavailable", function (event) {
        if (event.data && event.data.size) recorderChunks.push(event.data);
      });
      recorder.addEventListener("stop", function () {
        var blob = new Blob(recorderChunks, { type: (recorder && recorder.mimeType) || mime || "audio/webm" });
        limpiarGrabacion();
        if (blob.size) transcribirAudio(blob);
        else {
          sendAfterTranscription = false;
          sendBtn.disabled = false;
          estadoCompositor(T("chat.mic_no_speech", null, "No escuchamos una frase. Probá de nuevo."), true);
        }
      });
      recorder.start();
      micBtn.setAttribute("aria-pressed", "true");
      micBtn.setAttribute("aria-label", MIC_STOP_LABEL);
      micBtn.title = MIC_STOP_LABEL;
      estadoCompositor(T("chat.mic_listening", null, "Escuchando…"));
      recorderTimer = window.setTimeout(detenerGrabacion, 120000);
    }).catch(function (error) {
      limpiarGrabacion();
      sendAfterTranscription = false;
      sendBtn.disabled = false;
      if (error && (error.name === "NotAllowedError" || error.name === "SecurityError" ||
                    error.name === "NotFoundError")) {
        microphoneBlockedObserved = true;
        ocultarMicrofono();
        return;
      }
      estadoCompositor(T("chat.mic_open_error", null, "No pudimos abrir el micrófono."), true);
    });
  }

  if (micBtn) {
    micBtn.addEventListener("click", function () {
      if (recorder && recorder.state === "recording") detenerGrabacion();
      else if (!avisoDictadoPendiente()) iniciarGrabacion();
    });
  }

  inputEl.addEventListener("input", function () {
    ajustarTextarea();
    if (statusEl && statusEl.style.color) estadoCompositor("");
  });
  inputEl.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (inputEl.value.trim()) formEl.requestSubmit();
    }
  });

  formEl.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (!composerActivo()) return;
    if (recorder && recorder.state === "recording") {
      sendAfterTranscription = true;
      sendBtn.disabled = true;
      estadoCompositor(T("chat.mic_transcribing_send", null, "Transcribiendo y enviando…"));
      detenerGrabacion();
      return;
    }
    var texto = inputEl.value.trim();
    if (!texto) return;
    inputEl.value = "";
    ajustarTextarea();
    ocultarAvisoDictado();
    onEnviar(texto);
  });

  revisarPermisoMicrofono();
  ajustarTextarea();

  return {
    focus: function () { inputEl.focus(); },
    getValue: function () { return inputEl.value; },
    setValue: function (v) { inputEl.value = v || ""; ajustarTextarea(); },
    setDisabled: setDisabled,
    ajustarTextarea: ajustarTextarea,
    refrescarMicrofono: revisarPermisoMicrofono,
    els: { input: inputEl, attachBtn: attachBtn, fileEl: fileEl, micBtn: micBtn, sendBtn: sendBtn, statusEl: statusEl, secondaryActionEl: secondaryActionEl },
  };
}

export { escHtml, renderMini, crearLista, crearComposer, seguirViewport };
