/* entrevista.js — entrevista previa a la consulta: /entrevista/<id> y
   /e/<door_token> (fase 6, QR de puerta). Rediseño 2026-08-01 (dos rondas):
   primero una superficie de chat real (burbujas + composer) con, en el QR
   de puerta, login primero y un DNI-only de respaldo; ahora, PANTALLA
   COMPLETA de verdad — sin el header/nav ni el footer del sitio, sin el
   widget flotante de turnos (static/platform/chat-widget.mjs) — sólo esta charla, y el mismo
   composer compartido (static/platform/chat-ui.mjs) que ese widget usa:
   textarea que crece, adjuntar una foto, dictado por voz — ver
   docs/architecture/entrevista-previa.md y chat-y-whatsapp.md.

   `site_shell._shell(..., chrome="chat")` (modules/intake/interview_patient.py)
   sirve el HTML sin header/footer/chat-widget — sólo entrevista.css +
   este archivo (el componente compartido llega por window.SANA_STATIC).
   El server sólo valida sesión/dueño (o resuelve
   el médico por el token de puerta, y si hay sesión de cuenta activa YA
   matchea y crea la entrevista ahí mismo) y arma el shell, nunca el
   transcript, mismo espíritu que request-detail.js: el HTML servido no
   lleva datos de la conversación. TODO lo demás sale de GET/POST
   /api/intake/t/<patient_token> — el token opaco ES la autorización de esas
   dos rutas (mismo modelo que appointment_offer.py).

   Tres entradas al mismo chat (todas terminan con `data-token` = ya hay
   patient_token, se va derecho a `cargar()`):
     - post-reserva (#iv-host[data-token]).
     - QR de puerta CON sesión de cuenta (#iv-host[data-token]): el server
       ya matcheó por DNI y creó la entrevista — el paciente nunca ve un
       paso de identidad.
     - QR de puerta SIN sesión (#iv-host[data-qr-start]): se pinta un paso
       de identidad con dos acciones — "Ingresá a tu cuenta" (primaria,
       vuelve acá con ?next=<data-account-next> después del login) y,
       debajo, un DNI solo como respaldo que arranca la entrevista sin
       cuenta. El patient_token que devuelve vive sólo en memoria (variable
       JS), nunca en localStorage: no hay cuenta de por medio en ese
       camino, así que no hay nada que "recordar" entre visitas. Ese paso
       de identidad también corre sin chrome del sitio (ver arriba), en un
       panel simple y centrado — el chat propiamente dicho recién arranca
       después.

   Elderly-first: letra grande, un solo control primario, sin flechas de
   "Atrás" — una X arriba a la derecha cierra la entrevista (vuelve a la
   solicitud o a Mis solicitudes, nunca "queda colgada" la pantalla). Al
   [fin] se muestra una tarjeta de cierre — mensaje distinto según
   data-mode ('post_booking' con link "Ver mi solicitud"; 'qr' sin link, el
   médico ya la está leyendo) — nunca queda la conversación como única
   salida. */
(function () {
  "use strict";
  var host = document.getElementById("iv-host");
  if (!host) return;
  var EMBEDDED = document.body.classList.contains("iv-embed-body");

  // Pantalla completa de verdad: nada detrás scrollea (el chat mismo es la
  // única superficie con scroll, ver #iv-msgs en entrevista.css).
  document.documentElement.style.overflow = "hidden";
  document.body.style.overflow = "hidden";

  var TOKEN = host.getAttribute("data-token") || "";
  var DOOR = host.getAttribute("data-qr-start") || "";
  var ACCOUNT_NEXT = host.getAttribute("data-account-next") || "";
  var DETALLE = host.getAttribute("data-detail") || "";
  var MODO = host.getAttribute("data-mode") || "post_booking";

  var lista = null;
  var composer = null;
  var viewport = null;
  var scrollWithViewport = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function randomName(prefix) {
    return prefix + "_" + Math.random().toString(36).slice(2, 10);
  }

  // ---- dependencias de plataforma: i18n + el componente de chat ----------
  // platform/i18n.mjs y platform/chat-ui.mjs son fingerprinted: el shell
  // publica sus URLs en window.SANA_STATIC y importStatic() las resuelve,
  // fail-closed (null + console.error, nunca una URL fallback fija). El
  // arranque espera a ambas: una i18n que falla degrada a los literales en
  // castellano; un componente de chat que falla muestra la pantalla de error
  // existente — nunca un chat a medias sin composer.
  function staticUrl(name) {
    var m = (typeof window !== "undefined") && window.SANA_STATIC;
    var u = m && m[name];
    if (!u && typeof console !== "undefined" && console.error) console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
    return u || null;
  }
  function importStatic(name) {
    var u = staticUrl(name);
    return u ? import(u) : Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
  }
  var I18n = null;
  var i18nReady = importStatic("platform/i18n.mjs")
    .then(function (m) { I18n = m; return m; })
    .catch(function () { return null; });
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  var ChatUI = null;

  // The AI disclosure belongs to the first assistant message, once.

  function cerrarHref() {
    return DETALLE || "/my-requests";
  }

  function cerrarControl() {
    var icon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2.3" stroke-linecap="round"><line x1="5" y1="5" x2="19" y2="19"/>' +
      '<line x1="19" y1="5" x2="5" y2="19"/></svg>';
    var label = esc(T("intake.common.close", null, "Cerrar"));
    return EMBEDDED
      ? '<button type="button" class="iv-close" data-iv-close aria-label="' + label + '">' + icon + '</button>'
      : '<a class="iv-close" href="' + esc(cerrarHref()) + '" aria-label="' + label + '">' + icon + '</a>';
  }

  // ---- superficie de chat: pantalla completa ------------------------------

  function renderShell() {
    scrollWithViewport = null;
    host.innerHTML =
      '<header class="iv-topbar">' +
        '<div class="iv-topbar-text">' +
          '<h1 class="iv-topbar-title">' + esc(T("intake.entrevista.title", null, "Contale al médico cómo te sentís")) + '</h1>' +
        '</div>' +
        cerrarControl() +
      '</header>' +
      '<div class="iv-msgs" id="iv-msgs" role="log" aria-live="polite" aria-label="' +
        esc(T("intake.entrevista.conversation_label", null, "Conversación")) + '"></div>' +
      '<form class="iv-composer" id="iv-form"></form>' +
      '<div class="iv-done" id="iv-done" hidden></div>';

    var closeBtn = host.querySelector("[data-iv-close]");
    if (closeBtn) closeBtn.addEventListener("click", function () {
      window.parent.postMessage("humanrounds:close-intake", location.origin);
    });

    lista = ChatUI.crearLista(document.getElementById("iv-msgs"), { prefix: "iv" });
    composer = ChatUI.crearComposer(document.getElementById("iv-form"), {
      prefix: "iv",
      placeholder: T("intake.entrevista.placeholder", null, "Escribí tu respuesta…"),
      maxLength: 2000,
      attach: true,
      mic: true,
      soloImagen: true,
      accept: "image/*",
      attachLabel: T("intake.entrevista.attach_label", null, "Adjuntar una foto"),
      attachTitle: T("intake.entrevista.attach_title", null,
        "Adjuntar una foto (por ejemplo, de una zona de la piel o un estudio)"),
      onEnviar: enviarMensaje,
    });

    // `#iv-msgs` ocupa el espacio disponible entre la cabecera y el composer.
    // Si la ventana cambia de tamaño (o cruza la media query de 768px)
    // DESPUÉS de que ChatUI ya hizo scroll al fondo, el `scrollTop` absoluto
    // queda desactualizado: el contenedor se achica pero el scroll no se
    // vuelve a fijar, así que la última burbuja (justo la que anuncia el
    // cierre) queda cortada, con sólo un filo visible arriba de la tarjeta
    // "Listo" (QA 2026-09-15, 1440px). Volvemos a fijar el scroll al fondo
    // en cada resize, pero sólo si ya estaba ahí — así no le pisamos la
    // relectura a quien se desplazó hacia arriba a propósito. El estado se
    // mide en el listener de scroll (con las dimensiones DE ANTES del
    // resize), nunca dentro del propio handler de resize, donde el
    // contenedor ya cambió de tamaño y el cálculo "¿está al fondo?" mentiría
    // justo en el caso que queremos arreglar.
    var msgsEl = document.getElementById("iv-msgs");
    if (msgsEl) {
      var pegadoAlFondo = true;
      msgsEl.addEventListener("scroll", function () {
        pegadoAlFondo = (msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight) < 120;
      });
      scrollWithViewport = function () {
        if (!pegadoAlFondo) return;
        window.requestAnimationFrame(function () {
          window.requestAnimationFrame(function () {
            if (msgsEl.isConnected) msgsEl.scrollTop = msgsEl.scrollHeight;
          });
        });
      };
    }
    if (viewport) viewport.update();
  }

  // "Actualizar entrevista" (2026-08-01): una entrevista 'done' para un
  // turno todavía futuro se puede reabrir para agregar información nueva
  // (síntomas nuevos, algo que cambió) — el médico ya leyó un resumen, pero
  // el paciente puede seguir agregando antes de la consulta. Dos caminos al
  // mismo POST /reopen: el botón "Agregar información" en la pantalla de
  // cierre, y ?actualizar=1 en la URL (lo arma request-detail.js para el
  // botón primario "Contanos si algo cambió" — un solo toque, sin pasar por
  // la pantalla de cierre primero).
  var AUTO_REABRIR = /(^|[?&])actualizar=1(&|$)/.test(location.search);

  function showDone() {
    // La conversación queda visible (el paciente puede releer lo que
    // contó); lo único que desaparece es el compositor.
    var composerEl = document.getElementById("iv-form");
    var done = document.getElementById("iv-done");
    if (composerEl) composerEl.hidden = true;
    // El compositor queda ESCONDIDO pero utilizable: así reabrir() sólo tiene
    // que mostrarlo, y nunca queda un campo visible que no acepta texto.
    if (composer) composer.setDisabled(false);
    if (!done) return;
    var actualizarBtn = '<button type="button" class="iv-primary-btn" id="iv-update-btn">' +
      esc(T("intake.entrevista.add_info_btn", null, "Agregar información")) + '</button>';
    if (MODO === "qr") {
      done.innerHTML = "<p>" + esc(T("intake.entrevista.done_qr", null,
        "Listo — el médico va a leer tus respuestas durante la consulta.")) + "</p>" + actualizarBtn;
    } else {
      done.innerHTML =
        "<p>" + esc(T("intake.entrevista.done_default", null,
          "Listo — gracias por contarnos. El médico va a leer tus respuestas durante la consulta.")) + "</p>" +
        actualizarBtn +
        (DETALLE ? '<a href="' + esc(DETALLE) + '">' +
          esc(T("intake.entrevista.view_request", null, "Ver mi solicitud")) + '</a>' : "");
    }
    done.hidden = false;
    var btn = document.getElementById("iv-update-btn");
    if (btn) btn.addEventListener("click", reabrir);
  }

  // Cuántas burbujas ya están pintadas en pantalla — reabrir() lo usa para
  // agregar SÓLO los mensajes nuevos que trajo el reopen (nunca repintar
  // uno que ya estaba, como la última respuesta de cierre).
  var PINTADOS = 0;

  // Cada burbuja que TAMBIÉN quedó guardada en el servidor suma acá. Las
  // transitorias (un 429, un error de red) no: no están en el transcript, así
  // que contarlas haría que reabrir() se saltee un mensaje real. Sin esto,
  // PINTADOS se quedaba en el número de la carga inicial y "Agregar
  // información" repintaba toda la charla en vivo por segunda vez (QA
  // 2026-08-01).
  function burbujaGuardada(role, texto, opts) {
    lista.addBubble(role, texto, opts);
    PINTADOS += 1;
  }

  function pintarMensajes(mensajes, activa) {
    (mensajes || []).forEach(function (m, i) {
      lista.addBubble(m.role, m.texto);
      var esUltimo = i === mensajes.length - 1;
      if (esUltimo && activa && m.role === "assistant" &&
          m.opciones && m.opciones.length) {
        lista.addOpciones(m.opciones, function (etq) { enviarMensaje(etq); });
      }
    });
    PINTADOS = (mensajes || []).length;
  }

  // Reabre una entrevista 'done' (POST /reopen) y reactiva el compositor EN
  // EL LUGAR — sin recargar la página ni volver a pintar toda la charla
  // (ya está en pantalla, el paciente la puede releer). Sólo agrega los
  // mensajes que el reopen sumó por encima de PINTADOS — nunca repinta una
  // burbuja que ya estaba (p.ej. la respuesta de cierre que se ve arriba).
  // Nunca deja al paciente sin salida: si falla, vuelve a mostrar la
  // pantalla de cierre.
  function reabrir() {
    var done = document.getElementById("iv-done");
    if (done) done.innerHTML = "<p>" + esc(T("intake.entrevista.preparing", null, "Preparando…")) + "</p>";
    fetch("/api/intake/t/" + encodeURIComponent(TOKEN) + "/reopen", {
      method: "POST", credentials: "same-origin"
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (res) {
      if (!res.ok || !res.j) { showDone(); return; }
      if (done) { done.hidden = true; done.innerHTML = ""; }
      var composerEl = document.getElementById("iv-form");
      if (composerEl) composerEl.hidden = false;
      // Hay que HABILITARLO, no sólo mostrarlo: el último envío lo dejó
      // deshabilitado (enviarMensaje lo apaga mientras espera y el cierre
      // llegó antes de volver a encenderlo), así que "Agregar información"
      // devolvía un campo visible pero muerto — no se podía escribir.
      composer.setDisabled(false);
      var mensajes = res.j.mensajes || [];
      var nuevos = mensajes.slice(PINTADOS);
      nuevos.forEach(function (m, i) {
        lista.addBubble(m.role, m.texto);
        var esUltimo = i === nuevos.length - 1;
        if (esUltimo && m.role === "assistant" && m.opciones && m.opciones.length) {
          lista.addOpciones(m.opciones, function (etq) { enviarMensaje(etq); });
        }
      });
      PINTADOS = mensajes.length;
      composer.focus();
    }).catch(function () { showDone(); });
  }

  function cargar() {
    if (!TOKEN) { renderError(); return; }
    fetch("/api/intake/t/" + encodeURIComponent(TOKEN), { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("no_ok");
        return r.json();
      })
      .then(function (j) {
        renderShell();
        var mensajes = j.mensajes || [];
        var activa = (j.status || "active") === "active";
        pintarMensajes(mensajes, activa);
        if (!activa && AUTO_REABRIR) { reabrir(); return; }
        if (!activa) showDone();
        else composer.focus();
      })
      .catch(renderError);
  }

  function renderError() {
    scrollWithViewport = null;
    host.innerHTML =
      '<div class="iv-id-screen"><div class="iv-wrap">' +
        '<h1 class="h1">' + esc(T("intake.entrevista.load_error_title", null, "No pudimos cargar la entrevista")) + '</h1>' +
        '<p class="lede">' + esc(T("intake.entrevista.load_error_lede", null,
          "Probá de nuevo en un momento — tu turno no se pierde por esto.")) + '</p>' +
        (DETALLE ? '<p class="sd-ref"><a href="' + esc(DETALLE) + '">' +
          esc(T("intake.entrevista.view_request", null, "Ver mi solicitud")) + '</a></p>' : "") +
      '</div></div>';
  }

  function enviarMensaje(texto, adjunto) {
    texto = (texto || "").trim();
    if (!texto && !adjunto) return;
    if (adjunto) burbujaGuardada("user", T("intake.entrevista.photo_attached", null, "Foto adjunta"), { adjunto: true });
    else burbujaGuardada("user", texto);
    composer.setDisabled(true);
    lista.setTyping(true);
    var body = { texto: texto };
    if (adjunto) body.adjunto = adjunto;
    fetch("/api/intake/t/" + encodeURIComponent(TOKEN) + "/message", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
    }).then(function (res) {
      lista.setTyping(false);
      if (res.status === 429) {
        lista.addBubble("assistant", (res.j && res.j.mensaje) || T("intake.entrevista.rate_limited", null,
          "Hiciste varios mensajes seguidos. Esperá un rato y volvé a escribir."));
        composer.setDisabled(false);
        return;
      }
      if (res.status === 409) {
        lista.addBubble("assistant", T("intake.entrevista.already_closed", null, "Esta entrevista ya terminó."));
        showDone();
        return;
      }
      if (!res.ok || !res.j || !res.j.texto) {
        lista.addBubble("assistant", (res.j && res.j.error) === "adjunto_invalido"
          ? T("intake.entrevista.attachment_invalid", null, "Esa foto no se pudo usar — probá con otra imagen, más liviana.")
          : T("intake.entrevista.answer_error", null, "Tuvimos un problema para responderte. Probá de nuevo."));
        composer.setDisabled(false);
        return;
      }
      burbujaGuardada("assistant", res.j.texto);
      if (res.j.opciones && res.j.opciones.length) {
        lista.addOpciones(res.j.opciones, function (etq) { enviarMensaje(etq); });
      }
      if (res.j.fin) {
        showDone();
      } else {
        composer.setDisabled(false);
        composer.focus();
      }
    }).catch(function () {
      lista.setTyping(false);
      lista.addBubble("assistant", T("intake.entrevista.network_error", null, "Error de red. Probá de nuevo."));
      composer.setDisabled(false);
    });
  }

  // ---- paso de identidad del QR de puerta: login primero, DNI de respaldo -

  function accountLoginUrl() {
    var next = ACCOUNT_NEXT ? "?next=" + encodeURIComponent(ACCOUNT_NEXT) + "&mode=login" : "?mode=login";
    return "/account" + next;
  }

  function renderIdentidad() {
    host.innerHTML =
      '<div class="iv-id-screen"><div class="iv-wrap">' +
        '<h1 class="h1">' + esc(T("intake.entrevista.id_title", null, "Antes de empezar")) + '</h1>' +
        '<p class="lede">' + esc(T("intake.entrevista.id_lede", null,
          "Si ya tenés cuenta, entrá para no escribir nada más.")) + '</p>' +
        '<a class="iv-primary-btn" href="' + esc(accountLoginUrl()) + '">' +
          esc(T("intake.entrevista.login_cta", null, "Ingresá a tu cuenta")) + '</a>' +
        '<div class="iv-alt">' +
          '<p class="iv-alt-label">' + esc(T("intake.entrevista.id_alt_label", null, "o empezá con tu DNI")) + '</p>' +
          '<form class="iv-dni-form" id="iv-id-form" autocomplete="off">' +
            '<div class="iv-dni-field">' +
              '<label class="iv-id-label" for="iv-id-dni">' + esc(T("intake.entrevista.dni_label", null, "DNI")) + '</label>' +
              '<input class="iv-dni-input" id="iv-id-dni" type="text" inputmode="numeric" ' +
                'name="' + randomName("dni") + '" autocomplete="off" maxlength="10" required>' +
            '</div>' +
            '<button class="iv-dni-btn" id="iv-id-btn" type="submit">' +
              esc(T("intake.entrevista.continue_btn", null, "Continuar")) + '</button>' +
          '</form>' +
          '<p class="iv-id-error" id="iv-id-error" hidden></p>' +
        '</div>' +
      '</div></div>';

    var dniInput = document.getElementById("iv-id-dni");
    dniInput.addEventListener("input", function () {
      dniInput.value = dniInput.value.replace(/[^0-9]/g, "");
    });

    var form = document.getElementById("iv-id-form");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var dni = (dniInput.value || "").trim();
      if (dni.length < 6) {
        var e = document.getElementById("iv-id-error");
        e.textContent = T("intake.entrevista.dni_required", null, "Escribí tu número de documento.");
        e.hidden = false;
        return;
      }

      var errorEl = document.getElementById("iv-id-error");
      var btn = document.getElementById("iv-id-btn");
      errorEl.hidden = true;
      btn.disabled = true;

      fetch("/api/intake/qr/" + encodeURIComponent(DOOR) + "/start", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dni: dni })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          return { ok: r.ok, status: r.status, j: j };
        });
      }).then(function (res) {
        if (res.status === 429) {
          errorEl.textContent = T("intake.entrevista.id_rate_limited", null, "Probá de nuevo en un rato");
          errorEl.hidden = false;
          btn.disabled = false;
          return;
        }
        if (res.status === 400 && res.j && res.j.error === "dni_invalido") {
          errorEl.textContent = T("intake.entrevista.dni_invalid", null, "Revisá tu número de documento.");
          errorEl.hidden = false;
          btn.disabled = false;
          return;
        }
        if (!res.ok || !res.j || !res.j.patient_token) {
          errorEl.textContent = T("intake.entrevista.id_start_error", null, "No pudimos empezar. Probá de nuevo.");
          errorEl.hidden = false;
          btn.disabled = false;
          return;
        }
        TOKEN = res.j.patient_token;
        cargar();
      }).catch(function () {
        errorEl.textContent = T("intake.entrevista.network_error", null, "Error de red. Probá de nuevo.");
        errorEl.hidden = false;
        btn.disabled = false;
      });
    });
  }

  Promise.all([
    i18nReady,
    importStatic("platform/chat-ui.mjs").catch(function () { return null; }),
  ]).then(function (listo) {
    ChatUI = listo[1];
    if (!ChatUI) {
      renderError();
      return;
    }
    viewport = ChatUI.seguirViewport(host, {
      media: EMBEDDED ? null : "(max-width:767px)",
      onResize: function () { if (scrollWithViewport) scrollWithViewport(); },
    });
    if (DOOR && !TOKEN) {
      renderIdentidad();
    } else {
      cargar();
    }
  });
})();
