/* admin-appearance.mjs — native lifecycle-owned controller for the direct
 * /admin/appearance screen ("Apariencia y acceso"): theme, patient sign-in
 * mode, anti-bot gate, chat dictation model, and the site's color + logo.
 *
 * This file is the implementation, not a facade over the retired controller. Import
 * is inert; the template boots it with an explicit host and status element
 * and owns page-exit disposal. Each mount owns its reads, saves, drafts and
 * listeners — disposing (or mounting over the same host) aborts the reads
 * and retires every callback: a late response, JSON decode, FileReader
 * completion or retained event handler from an old owner never POSTs,
 * repaints, mutates styles, reloads or touches the new owner.
 *
 * Data contract (unchanged five-endpoint surface, all admin-only):
 *   GET/POST /api/admin/theme, /api/admin/auth-mode, /api/admin/turnstile
 *   (turnos) · GET /api/admin/chat-voice (chat) · GET/POST
 *   /api/admin/branding + POST/DELETE /api/admin/branding/logo (platform).
 * Module degradation is per block: a 2xx payload draws it, a 404 (owning
 * module off) removes it silently, 401/403 goes through the canonical
 * session module's confirmar (only the live owner's response counts), and
 * anything else drops the block and shows one reload line. The retired
 * marca role has no branch here: marca cookies cannot validate, so the
 * screen is admin-only end to end (contract 2026-09-07). */

const mounts = new WeakMap();

const GET_PATHS = [
  "/api/admin/theme", "/api/admin/auth-mode", "/api/admin/turnstile",
  "/api/admin/branding", "/api/admin/chat-voice",
];

export function mount(host, options) {
  if (!host) throw new Error("Appearance host is required");
  if (mounts.has(host)) mounts.get(host)();
  options = options || {};
  var statusHost = options.status || null;

  var disposed = false;
  var generation = 0;
  var request = null;
  var inflight = {};

  function alive() { return !disposed && host.isConnected !== false; }
  function owns(gen) { return alive() && gen === generation; }

  var drafts = host.__appearanceDrafts || {};
  function dispose() {
    if (disposed) return;
    captureDrafts();
    disposed = true;
    generation += 1;
    if (request) request.abort();
    host.removeEventListener("click", onClick);
    host.removeEventListener("input", onInput);
    host.removeEventListener("change", onChange);
    if (mounts.get(host) === dispose) mounts.delete(host);
  }

  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing Appearance dependency: " + name));
  }
  var Session = null;
  var sessionReady = importStatic("platform/admin-session.mjs").then(function (m) { Session = m; return m; });
  var I18n = null;
  var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function $(s) { return host.querySelector(s); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  var cur = null, THEMES = [];
  var authCur = null, AUTH_MODES = [], authProvider = "real";
  var tsCur = true, TS_OPTS = [], tsConfigurado = true, tsHostDev = false, tsForzadoOff = false;
  var voiceConfigured = false;
  var renaperConectado = false;
  var mkColor = null, mkEfectivo = "#0d7d48", mkLogo = false;
  // Which blocks answered on the last load (render order: tema, auth, ts,
  // voice, marca) and whether any GET failed for a reason other than "module
  // off" (404) — that one failure draws the single reload line.
  var BLOCKS = { tema: false, auth: false, ts: false, voice: false, marca: false };
  var loadError = false;

  function inyectarCss() {
    if (document.getElementById("cfg-style")) return;
    var css =
      // El shell directo cancela el padding horizontal del wrap en teléfono
      // (tablas full-bleed); el formulario recupera su margen con el mismo
      // patrón que usa la pantalla Cuenta (#sec-cuenta), sin tocar el shell.
      "@media(max-width:720px){#sec-configuracion{padding:0 16px;}}" +
      ".cfg-themes{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;max-width:680px;}" +
      ".cfg-theme{display:flex;align-items:flex-start;gap:14px;text-align:left;background:var(--panel);cursor:pointer;" +
        "border:1.5px solid var(--border);border-radius:14px;padding:16px 18px;font-family:inherit;transition:border-color .12s,box-shadow .12s;}" +
      ".cfg-theme:hover{border-color:var(--hover-bd);}" +
      ".cfg-theme.on{border-color:var(--accent);box-shadow:0 0 0 3px var(--focus-ring);}" +
      ".cfg-sw{flex-shrink:0;width:42px;height:42px;border-radius:10px;border:1px solid rgba(0,0,0,.08);}" +
      // swatches con los DOS colores de cada tema (fondo + accent), independientes
      // del tema activo del panel (por eso hexes literales, no tokens).
      ".cfg-sw--pinamar{background:linear-gradient(135deg,#0d7d48 0 55%,#eaf4ec 55% 100%);}" +
      ".cfg-sw--humanrounds{background:linear-gradient(135deg,#874f2e 0 55%,#f8e8de 55% 100%);}" +
      ".cfg-meta{display:flex;flex-direction:column;gap:3px;min-width:0;}" +
      ".cfg-name{font-size:16px;font-weight:700;color:var(--text);letter-spacing:-.01em;}" +
      ".cfg-desc{font-size:16px;color:var(--text);line-height:1.4;}" +
      ".cfg-msg{font-size:16px;color:var(--text);margin:18px 2px 0;min-height:1.2em;}" +
      // An empty status line keeps its live region but no height: it left a
      // band of empty space under every block (ui review 08.11).
      ".cfg-msg:empty{min-height:0;margin-top:0;}" +
      ".cfg-msg.err{color:var(--danger);}" +
      // Without an explicit status element the one load-failure line sits above
      // the first block and takes over that block's zero top margin.
      ".cfg-load-msg{margin:0 0 18px;}" +
      ".cfg-status--error{color:var(--danger);}" +
      ".cfg-h2{font-size:17px;font-weight:700;color:var(--text);letter-spacing:-.01em;margin:34px 0 12px;}" +
      ".cfg-h2:first-child,.cfg-load-msg+.cfg-h2{margin-top:0;}" +
      ".cfg-note{max-width:680px;font-size:16px;color:var(--text);line-height:1.45;margin:12px 2px 0;}" +
      // A switched-off block is its name and «Sin configurar · Integraciones»;
      // when both do not fit the state wraps whole under the name instead of
      // the name breaking at its hyphen («anti-/bot» on a phone, ui review 08.11).
      ".cfg-off{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:12px;row-gap:4px;}" +
      ".cfg-off-state{font-weight:400;letter-spacing:0;white-space:nowrap;}" +
      "#sec-configuracion .cnt-check{font-size:16px;align-items:flex-start;min-height:44px;}" +
      // Marca: el color y el logo. Los controles se dimensionan por su
      // contenido (un hex son 7 caracteres, no media pantalla).
      ".cfg-mk{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;margin:0 0 4px;}" +
      ".cfg-mk-lab{display:block;font-size:16px;color:var(--text);margin:0 0 5px;}" +
      // 44px tall controls and a 16px field font: a phone thumb reaches every
      // target and iOS Safari never zooms a focused field.
      ".cfg-mk-color{width:52px;height:44px;font-size:16px;padding:2px;border:1px solid var(--border);border-radius:9px;background:var(--panel);cursor:pointer;}" +
      ".cfg-mk-hex{width:10ch;font-family:var(--mono,monospace);font-size:16px;height:44px;padding:0 10px;" +
        "border:1px solid var(--border);border-radius:9px;background:var(--panel);color:var(--text);}" +
      ".cfg-mk-logo-block{margin:20px 0 0;}" +
      ".cfg-mk-logo{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}" +
      ".cfg-mk-prev{width:56px;height:56px;border:1px solid var(--border);border-radius:12px;background:var(--panel) center/contain no-repeat;flex-shrink:0;}" +
      ".cfg-mk-acts{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}" +
      // The two "go back to the default" actions stay text links (never a
      // .btn, no box of their own) but still give a 44px target.
      "#cfg-mk-reset,#cfg-mk-quitar{display:inline-flex;align-items:center;min-height:44px;min-width:44px;}";
    var el = document.createElement("style");
    el.id = "cfg-style";
    el.textContent = css;
    document.head.appendChild(el);
  }

  // The explicit status input is the screen's one load-error surface; without
  // it the line is painted in-band so zero blocks is never a blank page.
  function setStatus(msg, isError) {
    if (!statusHost || statusHost.isConnected === false) return;
    statusHost.hidden = !msg;
    statusHost.className = "adm-empty" + (isError && msg ? " cfg-status--error" : "");
    statusHost.textContent = msg || "";
  }

  function msgP(id) {
    return '<p class="cfg-msg" id="' + id + '" role="status" aria-live="polite"></p>';
  }

  function setMsg(sel, texto, err) {
    if (!alive()) return;
    var m = $(sel);
    if (m) { m.className = "cfg-msg" + (err ? " err" : ""); m.textContent = texto; }
  }

  // Saving one block must not wipe another block's unsaved edits: the draft
  // values live on the host, survive re-renders and even a full remount.
  function captureDrafts() {
    var hex = $("#cfg-mk-hex");
    if (hex) drafts.hex = hex.value;
    var color = $("#cfg-mk-color");
    if (color) drafts.color = color.value;
    host.__appearanceDrafts = drafts;
  }
  function restoreDrafts() {
    var hex = $("#cfg-mk-hex");
    if (hex && drafts.hex !== undefined) hex.value = drafts.hex;
    var color = $("#cfg-mk-color");
    if (color && drafts.color !== undefined) color.value = drafts.color;
  }

  // ---- render -------------------------------------------------------------

  // Opciones CONOCIDAS: el texto visible pasa por el catálogo activo (una
  // página en inglés las traduce) con el literal castellano como fallback —
  // la página canónica no viaja con catálogo y renderiza ese literal, no el
  // texto crudo del servidor. Un id que no está acá es una opción futura y
  // conserva tal cual la etiqueta y la descripción que manda el servidor.
  var OPTION_TEXT = {
    tema: {
      pinamar: {
        key: "admin.system.theme.tema.opcion.pinamar",
        label: "Pinamar",
        desc: "",
      },
      humanrounds: {
        key: "admin.system.theme.tema.opcion.humanrounds",
        label: "Human Rounds",
        desc: "",
      },
    },
    auth: {
      password: {
        key: "admin.system.theme.auth.opcion.password",
        label: "DNI y contraseña",
        desc: "El paciente crea una cuenta con su DNI y una contraseña propia.",
      },
      renaper: {
        key: "admin.system.theme.auth.opcion.renaper",
        label: "Identidad digital (RENAPER)",
        desc: "El paciente confirma su identidad con Mi Argentina o RENAPER, sin contraseña propia.",
      },
    },
    ts: {
      on: {
        key: "admin.system.theme.ts.opcion.on",
        label: "Activada",
        desc: "Cloudflare revisa que sea una persona antes de dejar entrar o subir una orden.",
      },
      off: {
        key: "admin.system.theme.ts.opcion.off",
        label: "Desactivada",
        desc: "Nadie ve la verificación; los límites por IP siguen funcionando.",
      },
    },
  };

  function optionText(kind, option, field) {
    var known = OPTION_TEXT[kind] && OPTION_TEXT[kind][option.id];
    if (!known || typeof known[field] !== "string") return option[field];
    return T(known.key + "." + field, null, known[field]);
  }

  // One option card (theme / sign-in mode). aria-pressed tells a screen reader
  // which one is active; the accent border + ring say it visually.
  function optionCard(attr, id, label, desc, on, swatchHtml) {
    return '<button type="button" class="cfg-theme' + (on ? " on" : "") + '" ' + attr + '="' + esc(id) + '" aria-pressed="' + (on ? "true" : "false") + '">' +
      (swatchHtml || "") +
      '<span class="cfg-meta">' +
      '<span class="cfg-name">' + esc(label) + "</span>" +
      (desc ? '<span class="cfg-desc">' + esc(desc) + "</span>" : "") +
      "</span></button>";
  }

  // A feature with no key or no agreement is ONE line: its name, «Sin
  // configurar» and the place that connects it (Ajustes › Integraciones) —
  // never its controls plus a note explaining why they do nothing (the product owner
  // 26.10: «if there's no content, why to show all the ui?»; 2026-11-08
  // review D8).
  function offHtml(block, titleKey, titleFallback) {
    return '<h2 class="cfg-h2 cfg-off" data-off="' + esc(block) + '">' + esc(T(titleKey, null, titleFallback)) +
      '<span class="cfg-off-state">' + esc(T("admin.system.status.unconfigured", null, "Sin configurar")) +
      ' · <a href="/admin/settings?view=integrations">' +
      esc(T("admin.system.settings.nav.integrations", null, "Integraciones")) + "</a></span></h2>";
  }

  function temaHtml() {
    var html =
      '<h2 class="cfg-h2">' + esc(T("admin.system.theme.tema.title", null, "Tema visual")) + '</h2>' +
      '<div class="cfg-themes">';
    THEMES.forEach(function (t) {
      html += optionCard("data-tema", t.id, optionText("tema", t, "label"), optionText("tema", t, "desc"), t.id === cur, '<span class="cfg-sw cfg-sw--' + esc(t.id) + '"></span>');
    });
    return html + '</div>' + msgP("cfg-msg");
  }

  function authHtml() {
    // RENAPER without the simulator or the IDP credentials is not a choice:
    // one line instead of a card that only switches to «pendiente» (D8). An
    // installation already set to it keeps both cards to switch back.
    var renaperOn = authProvider === "stub" || renaperConectado;
    if (!renaperOn && authCur !== "renaper") {
      return offHtml("auth", "admin.system.theme.auth.opcion.renaper.label", "Identidad digital (RENAPER)");
    }
    var html =
      '<h2 class="cfg-h2">' + esc(T("admin.system.theme.auth.title", null, "Ingreso de los pacientes")) + '</h2>' +
      '<div class="cfg-themes">';
    AUTH_MODES.forEach(function (m) {
      html += optionCard("data-auth", m.id, optionText("auth", m, "label"), optionText("auth", m, "desc"), m.id === authCur);
    });
    html += "</div>";
    if (!renaperOn) {
      html += '<p class="cfg-note">' + esc(T("admin.system.theme.auth.note", null,
        "RENAPER está pendiente del convenio. El sitio público mantiene el ingreso con contraseña; el entorno local usa el simulador.")) + '</p>';
    }
    return html + msgP("cfg-auth-msg");
  }

  function tsHtml() {
    if (!tsConfigurado && !tsHostDev && !tsForzadoOff) {
      return offHtml("ts", "admin.system.theme.ts.title", "Verificación anti-bot");
    }
    var html =
      '<h2 class="cfg-h2">' + esc(T("admin.system.theme.ts.title", null, "Verificación anti-bot")) + '</h2>';
    // La opción binaria (on/off) es UNA casilla real, etiquetada y operable
    // por teclado — el mismo control (cnt-check) que el panel usa para cada
    // sí/no. Cualquier otro conjunto de opciones (una elección futura que no
    // sea sí/no) conserva las tarjetas de selección y su etiqueta del servidor.
    var tiene = function (id) { return TS_OPTS.some(function (o) { return o.id === id; }); };
    if (TS_OPTS.length === 2 && tiene("on") && tiene("off")) {
      html += '<label class="cnt-check"><input type="checkbox" id="cfg-ts-check"' + (tsCur ? " checked" : "") + '><span>' +
        esc(T("admin.system.theme.ts.check_label", null,
          "Pedir la verificación de Cloudflare al ingresar y al subir una orden")) + "</span></label>";
    } else {
      html += '<div class="cfg-themes">';
      TS_OPTS.forEach(function (o) {
        html += optionCard("data-ts", o.id, optionText("ts", o, "label"), optionText("ts", o, "desc"), (o.id === "on") === !!tsCur);
      });
      html += "</div>";
    }
    // Por qué puede no estar corriendo aunque diga "Activada": el orden acá es
    // el mismo que aplica el servidor en turnstile.activo().
    if (tsHostDev) {
      html += '<p class="cfg-note">' + esc(T("admin.system.theme.ts.note_host_dev_pre", null, "Servidor local: la verificación ")) +
        "<b>" + esc(T("admin.system.theme.ts.note_host_dev_bold", null, "no corre acá")) + "</b>" +
        esc(T("admin.system.theme.ts.note_host_dev_post", null, " aunque la actives; la clave de Cloudflare sólo funciona en el dominio configurado.")) + '</p>';
    } else if (tsForzadoOff) {
      // No variable names on screen (the product owner, 02.11): the sentence says what
      // happens, the server setting stays in the deploy docs.
      html += '<p class="cfg-note">' + esc(T("admin.system.theme.ts.note_forzado_off", null,
        "Además está apagada en la configuración del servidor, que gana sobre esta opción.")) + '</p>';
    }
    return html + msgP("cfg-ts-msg");
  }

  // El modelo de dictado lo fija la política del servidor (core/ai_policy.py:
  // cualquier valor guardado distinto del default se ignora al leer), así que
  // este bloque nunca fue un formulario editable de verdad — un input con
  // botón que solo podía devolver «Usá fal-ai/wizper». Ni el nombre del
  // servicio ni la clave del modelo van en pantalla (2026-11-08, review
  // F-Apariencia: «Modelo de Fal fal-ai/wizper» era una clave técnica): el
  // bloque es una línea, como el apagado — el título y «Activo».
  function voiceHtml() {
    if (!voiceConfigured) return offHtml("voice", "admin.system.theme.voice.title", "Dictado del chat");
    return '<h2 class="cfg-h2 cfg-off" data-on="voice">' + esc(T("admin.system.theme.voice.title", null, "Dictado del chat")) +
      '<span class="cfg-off-state" id="cfg-voice-model">' + esc(T("admin.system.theme.shared.activo", null, "Activo")) + "</span></h2>";
  }

  function logoEfectivo() {
    if (mkLogo) return "/logo.png?t=" + Date.now();
    return cur === "pinamar" ? "/pinamar-emblema.svg" : "/logo.png";
  }

  function marcaHtml() {
    var val = mkColor || mkEfectivo;
    var logoSrc = logoEfectivo();
    return '<h2 class="cfg-h2">' + esc(T("admin.system.theme.marca.title", null, "Color y logo")) + '</h2>' +
      '<div class="cfg-mk">' +
      '<div><label class="cfg-mk-lab" for="cfg-mk-color">' + esc(T("admin.system.theme.marca.color_label", null, "Color")) + '</label>' +
      '<input type="color" id="cfg-mk-color" class="cfg-mk-color" value="' + esc(val) + '"></div>' +
      '<div><label class="cfg-mk-lab" for="cfg-mk-hex">' + esc(T("admin.system.theme.marca.hex_label", null, "Código")) + '</label>' +
      '<input type="text" id="cfg-mk-hex" class="cfg-mk-hex" inputmode="latin" spellcheck="false" ' +
      'maxlength="7" value="' + esc(val) + '"></div>' +
      '<div class="cfg-mk-acts"><button type="button" class="btn btn-ok" id="cfg-mk-save">' + esc(T("admin.system.theme.marca.save_color", null, "Guardar color")) + '</button>' +
      (mkColor ? '<button type="button" id="cfg-mk-reset">' + esc(T("admin.system.theme.marca.reset_color", null, "Volver al del tema")) + '</button>' : "") +
      "</div></div>" +
      msgP("cfg-mk-msg") +
      // «Logo» is a label over its row, like «Color» and «Código» over theirs:
      // the preview, then the picker centred on it (ui review 2026-11-08).
      '<div class="cfg-mk-logo-block"><span class="cfg-mk-lab">' + esc(T("admin.system.theme.marca.logo_label", null, "Logo")) + '</span>' +
      '<div class="cfg-mk-logo">' +
      '<span class="cfg-mk-prev" id="cfg-mk-prev" style="background-image:url(' + esc(logoSrc) + ')"></span>' +
      // The panel's file picker (2026-11-08): the system «Choose File / No
      // file chosen» stays hidden behind a secondary button, like the photo
      // of a doctor or a center (.cnt-foto-file + label.btn).
      '<input type="file" id="cfg-mk-file" class="cnt-foto-file" accept="image/png,image/jpeg,image/webp,image/svg+xml" aria-label="' +
      esc(T("admin.system.theme.marca.logo_label", null, "Logo")) + '">' +
      '<label class="btn btn-quiet" for="cfg-mk-file" id="cfg-mk-pick">' +
      esc(T("admin.content.catalog.doctors.import_pick", null, "Elegir archivo")) + "</label>" +
      (mkLogo ? '<button type="button" id="cfg-mk-quitar">' + esc(T("admin.system.theme.marca.reset_logo", null, "Volver al logo original")) + '</button>' : "") +
      "</div></div>" +
      '<p class="cfg-note">' + esc(T("admin.system.theme.marca.logo_hint", null,
        "PNG, JPG, WEBP o SVG; máximo 2 MB.")) + '</p>' +
      msgP("cfg-mk-logo-msg");
  }

  function render(acknowledgedColor) {
    if (!alive()) return;
    captureDrafts();
    if (acknowledgedColor !== undefined) {
      drafts.hex = acknowledgedColor;
      drafts.color = acknowledgedColor;
    }
    inyectarCss();
    // Each block draws only when its own GET answered (BLOCKS); the order of
    // the survivors never changes.
    var html = "";
    if (BLOCKS.tema) html += temaHtml();
    if (BLOCKS.auth) html += authHtml();
    if (BLOCKS.ts) html += tsHtml();
    if (BLOCKS.voice) html += voiceHtml();
    if (BLOCKS.marca) html += marcaHtml();
    var anyBlock = BLOCKS.tema || BLOCKS.auth || BLOCKS.ts || BLOCKS.voice || BLOCKS.marca;
    var loadMsg = (loadError || !anyBlock)
      ? T("admin.system.theme.load_error", null, "Algunas opciones no se pudieron cargar. Recargá la página.")
      : "";
    if (statusHost) setStatus(loadMsg, true);
    else if (loadMsg) html = '<p class="cfg-msg err cfg-load-msg">' + esc(loadMsg) + "</p>" + html;
    if (loadMsg) html += '<button type="button" class="btn" id="cfg-retry">' +
      esc(T("admin.content.common.reload", null, "Recargar")) + '</button>';
    host.innerHTML = html;
    restoreDrafts();
  }

  // ---- persistence ----------------------------------------------------------

  // One in-flight save per operation: a second click while a save is pending
  // is ignored (no duplicate POST), and the guard is released when the save
  // settles — a failure leaves deliberate retry open.
  function begin(op) {
    if (!alive() || inflight[op]) return null;
    inflight[op] = true;
    return generation;
  }
  function end(op, gen) {
    if (owns(gen)) delete inflight[op];
  }

  function requestJson(method, path, body) {
    return fetch(path, {
      method: method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (j) {
          return { ok: r.ok, status: r.status, json: j, response: r };
        });
      })
      .catch(function () { return { ok: false, status: 0, json: null, response: null }; });
  }

  function post(path, body) { return requestJson("POST", path, body); }

  // Resolves false when this owner may no longer act (disposed, replaced, or
  // the session module transitioned the page away). A 401/403 that confirmar
  // does NOT confirm stays a local failure of that request.
  function settle(res, gen) {
    if (!owns(gen)) return Promise.resolve(false);
    if (res.status === 401 || res.status === 403) {
      return Session.confirmar(res.response, res.json, function () { return owns(gen); }).then(function (transitioned) {
        if (transitioned) { dispose(); return false; }
        return owns(gen);
      });
    }
    return Promise.resolve(true);
  }

  function errText(res, baseKey, baseFallback, retryKey, retryFallback) {
    // TASK-48: el código del server (demo_solo_lectura…) pasa por apiError.
    if (res.json && res.json.error === "demo_solo_lectura" && I18n && I18n.apiError) {
      return I18n.apiError(res.json, T(baseKey, null, baseFallback));
    }
    if (res.json && res.json.error) return res.json.error;
    if (res.status === 0) return T(retryKey, null, retryFallback);
    return T(baseKey, null, baseFallback);
  }

  // Pinta el color nuevo en ESTE panel al instante (el resto del sitio lo toma
  // en su próximo render server-side), sin recargar.
  function aplicarEnVivo(d) {
    var r = document.documentElement.style;
    if (!d) {
      ["--accent", "--accent-hover", "--accent-soft", "--accent-bd"].forEach(function (k) { r.removeProperty(k); });
      return;
    }
    r.setProperty("--accent", d.accent);
    r.setProperty("--accent-hover", d.accent_hover);
    r.setProperty("--accent-soft", d.accent_soft);
    r.setProperty("--accent-bd", d.accent_bd);
  }

  function setTema(id) {
    if (id === cur) return;
    var gen = begin("tema");
    if (gen === null) return;
    setMsg("#cfg-msg", T("admin.system.theme.shared.guardando", null, "Guardando…"), false);
    post("/api/admin/theme", { tema: id }).then(function (res) {
      return settle(res, gen).then(function (go) {
        if (!go) return;
        if (!res.ok || !res.json || res.json.ok !== true) {
          setMsg("#cfg-msg", errText(res, "admin.system.theme.tema.error", "No se pudo cambiar el tema.",
            "admin.system.theme.tema.error_retry", "No se pudo cambiar el tema. Reintentá."), true);
          return;
        }
        cur = id;
        // The brand text, fonts and favicon are server-injected (base.html):
        // a reload is the one path that repaints everything without a third
        // hand-copied brand map. Ownership-gated, single call site — an old
        // owner's late success never reloads the new owner's page.
        location.reload();
      });
    }).catch(function () {
      if (owns(gen)) setMsg("#cfg-msg", T("admin.system.theme.tema.error_retry", null, "No se pudo cambiar el tema. Reintentá."), true);
    }).finally(function () { end("tema", gen); });
  }

  function setAuthMode(id) {
    if (id === authCur) return;
    var gen = begin("auth");
    if (gen === null) return;
    setMsg("#cfg-auth-msg", T("admin.system.theme.shared.guardando", null, "Guardando…"), false);
    post("/api/admin/auth-mode", { auth_mode: id }).then(function (res) {
      return settle(res, gen).then(function (go) {
        if (!go) return;
        if (!res.ok || !res.json || res.json.ok !== true) {
          setMsg("#cfg-auth-msg", errText(res, "admin.system.theme.auth.error", "No se pudo cambiar el ingreso.",
            "admin.system.theme.auth.error_retry", "No se pudo cambiar el ingreso. Reintentá."), true);
          return;
        }
        authCur = id;
        render();
        var mode = AUTH_MODES.filter(function (m) { return m.id === id; })[0];
        var nombre = mode ? optionText("auth", mode, "label") : id;
        setMsg("#cfg-auth-msg", T("admin.system.theme.auth.done", { nombre: nombre }, "Listo — el ingreso de los pacientes ahora es “{nombre}”."), false);
      });
    }).catch(function () {
      if (owns(gen)) setMsg("#cfg-auth-msg", T("admin.system.theme.auth.error_retry", null, "No se pudo cambiar el ingreso. Reintentá."), true);
    }).finally(function () { end("auth", gen); });
  }

  // La casilla binaria refleja SIEMPRE el estado guardado: una operación ya
  // en curso, un fallo o una respuesta sin confirmación la devuelven al valor
  // que el servidor tiene — nunca queda pintada una elección no guardada.
  function revertirTsCasilla() {
    var box = $("#cfg-ts-check");
    if (box) box.checked = !!tsCur;
  }

  function setTurnstile(on) {
    if (on === !!tsCur) return;
    var gen = begin("ts");
    if (gen === null) { revertirTsCasilla(); return; }
    setMsg("#cfg-ts-msg", T("admin.system.theme.shared.guardando", null, "Guardando…"), false);
    post("/api/admin/turnstile", { on: on }).then(function (res) {
      return settle(res, gen).then(function (go) {
        if (!go) return;
        if (!res.ok || !res.json || res.json.ok !== true) {
          revertirTsCasilla();
          setMsg("#cfg-ts-msg", errText(res, "admin.system.theme.ts.error", "No se pudo cambiar la verificación.",
            "admin.system.theme.ts.error_retry", "No se pudo cambiar la verificación. Reintentá."), true);
          return;
        }
        tsCur = on;
        render();
        setMsg("#cfg-ts-msg", on
          ? T("admin.system.theme.ts.on_done", null, "Listo — la verificación anti-bot está activada.")
          : T("admin.system.theme.ts.off_done", null, "Listo — la verificación anti-bot está desactivada. Los límites por IP siguen activos."), false);
      });
    }).catch(function () {
      if (owns(gen)) {
        revertirTsCasilla();
        setMsg("#cfg-ts-msg", T("admin.system.theme.ts.error_retry", null, "No se pudo cambiar la verificación. Reintentá."), true);
      }
    }).finally(function () { end("ts", gen); });
  }

  function guardarColor(valor) {
    var gen = begin("color");
    if (gen === null) return;
    var submittedHex = $("#cfg-mk-hex").value;
    var submittedColor = $("#cfg-mk-color").value;
    var saved = false;
    setMsg("#cfg-mk-msg", T("admin.system.theme.shared.guardando", null, "Guardando…"), false);
    post("/api/admin/branding", { color: valor || "" }).then(function (res) {
      return settle(res, gen).then(function (go) {
        if (!go) return;
        if (!res.ok || !res.json || res.json.ok !== true ||
            !(res.json.color === null || /^#[0-9a-f]{6}$/i.test(res.json.color))) {
          setMsg("#cfg-mk-msg", errText(res, "admin.system.theme.marca.color_error", "No se pudo guardar el color.",
            "admin.system.theme.marca.color_error_retry", "No se pudo guardar el color. Reintentá."), true);
          return;
        }
        saved = true;
        // The initial effective color may itself be a custom override. After
        // reset, ask the owner for the actual theme color; never guess a palette.
        var state = res.json.color === null
          ? fetchOutcome("/api/admin/branding").then(function (fresh) {
              return settle(fresh, gen).then(function (current) {
                if (!current) return null;
                if (!fresh.ok || !fresh.json || fresh.json.color !== null ||
                    !/^#[0-9a-f]{6}$/i.test(fresh.json.color_efectivo)) {
                  throw new Error("Branding reset refresh failed");
                }
                return fresh.json;
              });
            })
          : Promise.resolve(res.json);
        return state.then(function (branding) {
          if (!branding || !owns(gen)) return;
          var derived = branding.derivados;
          if (branding.color !== null && (!derived ||
              !["accent", "accent_hover", "accent_soft", "accent_bd"].every(function (key) {
                return typeof derived[key] === "string" && derived[key].length > 0;
              }))) throw new Error("Invalid branding color tokens");
          mkColor = branding.color;
          mkEfectivo = mkColor || branding.color_efectivo;
          aplicarEnVivo(mkColor ? derived : null);
          // Acknowledgement replaces only the submitted draft. Edits made
          // during this request remain unsaved and must survive its repaint.
          var unchanged = $("#cfg-mk-hex").value === submittedHex &&
            $("#cfg-mk-color").value === submittedColor;
          render(unchanged ? mkEfectivo : undefined);
          setMsg("#cfg-mk-msg", mkColor
            ? T("admin.system.theme.marca.color_saved", null, "Listo — el sitio entero ya usa este color.")
            : T("admin.system.theme.marca.color_reset_done", null, "Listo — volvió el color del tema."), false);
        });
      });
    }).catch(function () {
      if (owns(gen)) setMsg("#cfg-mk-msg", saved
        ? T("admin.system.theme.load_error", null, "Algunas opciones no se pudieron cargar. Recargá la página.")
        : T("admin.system.theme.marca.color_error_retry", null, "No se pudo guardar el color. Reintentá."), true);
    }).finally(function () { end("color", gen); });
  }

  function subirLogo(f) {
    if (!f) return;
    var gen = begin("logo");
    if (gen === null) return;
    setMsg("#cfg-mk-logo-msg", T("admin.system.theme.marca.uploading", null, "Subiendo…"), false);
    var fr = new FileReader();
    fr.onload = function () {
      // A retained reader completing after disposal or replacement is
      // inert: no POST, no repaint, no message on the new owner.
      if (!owns(gen)) return;
      post("/api/admin/branding/logo", { data_b64: String(fr.result).split(",")[1] || "" }).then(function (res) {
        return settle(res, gen).then(function (go) {
          if (!go) return;
          if (!res.ok || !res.json || res.json.ok !== true) {
            setMsg("#cfg-mk-logo-msg", errText(res, "admin.system.theme.marca.logo_upload_error", "No se pudo subir el logo.",
              "admin.system.theme.marca.logo_upload_error_retry", "No se pudo subir el logo. Reintentá."), true);
            return;
          }
          mkLogo = true;
          render();
          setMsg("#cfg-mk-logo-msg", T("admin.system.theme.marca.logo_uploaded", null, "Listo — el logo nuevo ya está en todo el sitio."), false);
        });
      }).catch(function () {
        if (owns(gen)) setMsg("#cfg-mk-logo-msg", T("admin.system.theme.marca.logo_upload_error_retry", null, "No se pudo subir el logo. Reintentá."), true);
      }).finally(function () { end("logo", gen); });
    };
    fr.onerror = function () {
      if (owns(gen)) setMsg("#cfg-mk-logo-msg", T("admin.system.theme.marca.file_read_error", null, "No se pudo leer el archivo."), true);
      end("logo", gen);
    };
    fr.readAsDataURL(f);
  }

  function quitarLogo() {
    if (!alive() || inflight.logo) return;
    if (!confirm(T("admin.system.theme.marca.confirm_reset_logo", null, "¿Volver al logo original del sitio?"))) return;
    var gen = begin("logo");
    if (gen === null) return;
    setMsg("#cfg-mk-logo-msg", T("admin.system.theme.shared.guardando", null, "Guardando…"), false);
    requestJson("DELETE", "/api/admin/branding/logo").then(function (res) {
      return settle(res, gen).then(function (go) {
        if (!go) return;
        if (!res.ok || !res.json || res.json.ok !== true) {
          setMsg("#cfg-mk-logo-msg", errText(res, "admin.system.theme.marca.logo_remove_error", "No se pudo quitar el logo.",
            "admin.system.theme.marca.logo_remove_error_retry", "No se pudo quitar el logo. Reintentá."), true);
          return;
        }
        mkLogo = false;
        render();
        setMsg("#cfg-mk-logo-msg", T("admin.system.theme.marca.logo_removed", null, "Listo — volvió el logo original."), false);
      });
    }).catch(function () {
      if (owns(gen)) setMsg("#cfg-mk-logo-msg", T("admin.system.theme.marca.logo_remove_error_retry", null, "No se pudo quitar el logo. Reintentá."), true);
    }).finally(function () { end("logo", gen); });
  }

  // ---- events (one delegated set per mount, removed on dispose) -----------

  function onClick(event) {
    if (!alive() || !event || !event.target || !event.target.closest) return;
    var el = event.target;
    var card;
    if (el.closest("#cfg-retry")) { location.reload(); return; }
    if ((card = el.closest("[data-tema]"))) { setTema(card.getAttribute("data-tema")); return; }
    if ((card = el.closest("[data-auth]"))) { setAuthMode(card.getAttribute("data-auth")); return; }
    if ((card = el.closest("[data-ts]"))) { setTurnstile(card.getAttribute("data-ts") === "on"); return; }
    if (el.closest("#cfg-mk-save")) {
      var hex = $("#cfg-mk-hex"), color = $("#cfg-mk-color");
      guardarColor((hex && hex.value) || (color && color.value) || "");
      return;
    }
    if (el.closest("#cfg-mk-reset")) { event.preventDefault(); guardarColor(""); return; }
    if (el.closest("#cfg-mk-quitar")) { event.preventDefault(); quitarLogo(); return; }
  }

  function onInput(event) {
    if (!alive() || !event || !event.target) return;
    var el = event.target;
    // Los dos controles son el MISMO dato: el que se toca manda sobre el otro.
    if (el.id === "cfg-mk-color") {
      var hex = $("#cfg-mk-hex");
      if (hex) hex.value = el.value;
    } else if (el.id === "cfg-mk-hex") {
      var v = String(el.value || "").trim();
      if (v && v.charAt(0) !== "#") v = "#" + v;
      if (/^#[0-9a-fA-F]{6}$/.test(v)) {
        var color = $("#cfg-mk-color");
        if (color) color.value = v.toLowerCase();
      }
    }
  }

  function onChange(event) {
    if (!alive() || !event || !event.target) return;
    var el = event.target;
    if (el.id === "cfg-mk-file") subirLogo(el.files && el.files[0]);
    else if (el.id === "cfg-ts-check") setTurnstile(el.checked);
  }

  // ---- load -----------------------------------------------------------------

  // Per-request outcome: a rejected fetch or an unparsable body is a failure
  // of THAT block, never a rejected Promise.all that blanks the whole screen.
  function fetchOutcome(path) {
    return fetch(path, { credentials: "same-origin", signal: request.signal })
      .then(function (r) {
        return r.json().then(
          function (j) { return { ok: r.ok, status: r.status, json: j, response: r }; },
          function () { return { ok: r.ok, status: r.status, json: null, response: r }; }
        );
      })
      .catch(function () { return { ok: false, status: 0, json: null, response: null }; });
  }

  function load() {
    if (!alive()) return Promise.resolve();
    generation += 1;
    if (request) request.abort();
    request = new AbortController();
    var gen = generation;
    return Promise.all(GET_PATHS.map(fetchOutcome))
      .then(function (rs) {
        if (!owns(gen)) return;
        var boundary = null;
        for (var i = 0; i < rs.length; i += 1) {
          if (rs[i].status === 401 || rs[i].status === 403) { boundary = rs[i]; break; }
        }
        if (boundary) {
          // Only the current owner's response may drive the transition; a
          // live 401 that whoami does not confirm and an ordinary 403 stay
          // local failures, not a reason to leave the screen blank-loading.
          return Session.confirmar(boundary.response, boundary.json, function () { return owns(gen); }).then(function (transitioned) {
            if (!owns(gen)) return;
            if (transitioned) { dispose(); return; }
            BLOCKS = { tema: false, auth: false, ts: false, voice: false, marca: false };
            loadError = true;
            render();
          });
        }
        var failed = false;
        // 2xx with a payload → the block renders; 404 → the feature is not
        // installed (owning module off), absent silently; anything else →
        // absent and remembered as a failure.
        function take(r, collectionKey) {
          var value = r.json;
          var record = value && typeof value === "object" && !Array.isArray(value);
          var collection = !collectionKey || (record && Array.isArray(value[collectionKey]) &&
            value[collectionKey].every(function (item) {
              return item && typeof item === "object" && !Array.isArray(item) &&
                typeof item.id === "string" && typeof item.label === "string" && typeof item.desc === "string";
            }));
          if (r.ok && record && collection) return value;
          if (r.status !== 404) failed = true;
          return null;
        }
        var j = take(rs[0], "themes"), a = take(rs[1], "modes"), t = take(rs[2], "options"), m = take(rs[3]), v = take(rs[4]);
        if (m) {
          mkColor = m.color || null;
          mkEfectivo = m.color_efectivo || mkEfectivo;
          mkLogo = !!m.logo;
        }
        if (v) {
          voiceConfigured = !!v.fal_configurado;
        }
        if (j) { cur = j.tema; THEMES = j.themes || []; }
        if (a) { authCur = a.auth_mode; AUTH_MODES = a.modes || []; authProvider = a.renaper_provider || "real"; renaperConectado = !!a.renaper_conectado; }
        if (t) {
          tsCur = !!t.on; TS_OPTS = t.options || [];
          tsConfigurado = !!t.configurado; tsHostDev = !!t.host_dev; tsForzadoOff = !!t.forzado_off_env;
        }
        BLOCKS = { tema: !!j, auth: !!a, ts: !!t, voice: !!v, marca: !!m };
        loadError = failed;
        render();
      });
  }

  // ---- boot -----------------------------------------------------------------

  host.addEventListener("click", onClick);
  host.addEventListener("input", onInput);
  host.addEventListener("change", onChange);
  mounts.set(host, dispose);

  // The direct screen's URL is its bare English path; a stray query (a login
  // ?next round trip) is canonicalized away, exactly as the classic route()
  // did — once, at mount, never rewritten afterwards.
  var canonicalPath = options.canonicalPath !== undefined ? options.canonicalPath : "/admin/appearance";
  if (canonicalPath && typeof location !== "undefined" && location.search) {
    try { history.replaceState({}, "", canonicalPath); } catch (err) { /* best effort */ }
  }

  // Both dependencies must settle before the first read; a missing manifest
  // entry fails closed through onError (or the status line), never reads.
  var dependencies = Promise.all([sessionReady, i18nReady]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function () {
    if (!alive()) return;
    return load();
  }).catch(function (error) {
    if (!alive()) return;
    if (options.onError) options.onError(error);
    else setStatus(T("admin.system.theme.load_error", null, "Algunas opciones no se pudieron cargar. Recargá la página."), true);
  });

  return dispose;
}
