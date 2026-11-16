/* admin-settings.mjs — native lifecycle-owned Settings controller for the direct screen
 * Location, languages, and integration keys
 * remain live-editable through config.py; TURNOS_CONFIG_LOCK keeps the
 * environment authoritative and is surfaced by the existing banner.
 *
 * Data: GET /api/admin/settings (solo_admin) -> {lock, location, languages,
 * api_keys}. Save: POST /api/admin/settings {grupo, valores, borrar?}.
 * Probe: POST /api/admin/settings/probar {integracion}.
 *
 * Secrets never round-trip: GET returns only {configurado, last4}. Secret
 * controls start empty, and an empty value means "leave unchanged". */
const mounts = new WeakMap();
const pendingByHost = new WeakMap();

export function mount(host, options = {}) {
  if (!host) throw new Error("Settings host is required");
  if (mounts.has(host)) mounts.get(host)();
  "use strict";
  var disposed = false;
  var epoch = 0;
  var viewEpoch = 0;
  var loadRequest = null;
  var pending = new WeakSet();
  var pendingCards = pendingByHost.get(host) || new Set();
  pendingByHost.set(host, pendingCards);
  function cardKey(card) { return card.getAttribute("data-integracion-card") || card.getAttribute("data-grupo"); }
  function refreshPending() {
    if (!alive()) return;
    $$(".set-guardar, .set-borrar", host).forEach(function (button) {
      var card = button.closest(".set-card");
      if (card) button.disabled = pendingCards.has(cardKey(card));
    });
    $$('[data-connector-hsi]', host).forEach(function (input) { input.disabled = pendingCards.has("connectors"); });
  }
  function releaseCard(key) {
    pendingCards.delete(key);
    var current = mounts.get(host);
    if (current) current.refreshPending();
  }
  var drafts = host.__settingsDraft || {};
  function alive() { return !disposed && host.isConnected !== false; }
  function owns(token, view) { return alive() && token === epoch && (view == null || view === viewEpoch); }
  function captureDrafts() {
    if (!alive()) return;
    $$("input[data-key], select[data-key], textarea[data-key]", host).forEach(function (el) {
      if (el.getAttribute("data-tipo") === "secret") return;
      if (el.type === "checkbox") {
        drafts[el.getAttribute("data-key")] = !!el.checked;
        return;
      }
      drafts[el.getAttribute("data-key")] = el.multiple
        ? $$("option", el).filter(function (option) { return option.selected; }).map(function (option) { return option.value; })
        : el.value;
    });
    $$('[data-lang-check]', host).forEach(function (el) { drafts["lang:" + el.value] = !!el.checked; });
    host.__settingsDraft = drafts;
  }
  function restoreDrafts() {
    $$("input[data-key], select[data-key], textarea[data-key]", host).forEach(function (el) {
      var key = el.getAttribute("data-key");
      if (!Object.prototype.hasOwnProperty.call(drafts, key) || el.getAttribute("data-tipo") === "secret") return;
      if (el.type === "checkbox") {
        el.checked = drafts[key] === true;
        return;
      }
      if (el.multiple) {
        var selected = Array.isArray(drafts[key]) ? drafts[key] : String(drafts[key]).split(",");
        $$("option", el).forEach(function (option) { option.selected = selected.indexOf(option.value) !== -1; });
      }
      else el.value = drafts[key];
    });
    $$('[data-lang-check]', host).forEach(function (el) {
      var key = "lang:" + el.value;
      if (Object.prototype.hasOwnProperty.call(drafts, key)) el.checked = drafts[key];
    });
  }
  function dispose() {
    if (disposed) return;
    captureDrafts();
    disposed = true;
    epoch += 1;
    if (loadRequest) loadRequest.abort();
    window.removeEventListener("popstate", onPopstate);
    window.removeEventListener("resize", onResizeGrow);
    document.removeEventListener("click", onClick);
    if (mounts.get(host) === dispose) mounts.delete(host);
  }
  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing Settings dependency: " + name));
  }
  var sessionReady = importStatic("platform/admin-session.mjs");
  // «hace 5 minutos» for the last HSI transmission: one formatter for the
  // instant, platform/dates.mjs. Optional — without it (absent, or it failed
  // to load) the line prints the server's own day and hour, and the settings
  // still open.
  var Dates = null;
  var datesReady = window.SANA_STATIC && window.SANA_STATIC["platform/dates.mjs"]
    ? importStatic("platform/dates.mjs").then(function (m) { Dates = m; }, function () { Dates = null; })
    : Promise.resolve();

  var I18n = null;
    var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
    // Number agreement through i18n.mjs tn() — «1 turno», never «1 turnos».
    function Tn(key, n, vars, one, other) {
      if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
      var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
      return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
    }
    // A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
    function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function inyectarCss() {
    if (document.getElementById("set-css")) return;
    var st = document.createElement("style");
    st.id = "set-css";
    st.textContent = [
      ".set-lock{background:var(--warn-soft);border:1px solid var(--warn);color:var(--warn);border-radius:10px;",
      "padding:10px 14px;font-size:14px;margin:0 0 18px;}",
      ".set-card{background:var(--panel);border:1px solid var(--border,#e4e4e0);border-radius:12px;",
      "padding:18px 20px;margin:0 0 16px;}",
      ".set-card h2{font-size:17px;margin:0 0 4px;font-weight:650;}",
      ".set-card .set-lede{font-size:16px;line-height:1.4;color:var(--hint-ink,var(--text-2,#666));margin:0 0 14px;}",
      ".set-row{display:grid;grid-template-columns:220px minmax(0,1fr);gap:6px 14px;align-items:start;padding:9px 0;}",
      ".set-row>label{text-align:left;font-size:16px;line-height:1.25;color:var(--hint-ink,var(--text-2,#555));padding-top:13px;}",
      ".set-field{display:flex;flex-wrap:wrap;align-items:center;gap:8px;min-width:0;}",
      ".set-field input:not([type=checkbox]):not([type=radio]),.set-field select,.set-field textarea{font-size:16px;line-height:1.2;min-height:40px;padding:8px 12px;",
      ".set-field textarea.set-grow{resize:none;overflow:hidden;line-height:1.4;}",
      "border:1px solid var(--border,#ccc);font-family:inherit;",
      "border-radius:8px;background:var(--panel,#fff);color:var(--text,#1c1c1c);min-width:0;width:100%;max-width:100%;flex:1 1 220px;box-sizing:border-box;}",
      ".set-estado{font-size:16px;color:var(--hint-ink);white-space:nowrap;}",
      ".set-estado.set-ok{color:var(--done);}",
      // .set-mini is the panel's secondary button: its look lives in
      // admin-direct.css (one button system, 2026-11-08).
      ".set-actions{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:14px;}",
      ".set-actions .btn{min-height:var(--adm-btn-h,36px);padding:0 16px;font-size:var(--adm-btn-font,14px);}",
      ".set-msg{font-size:16px;color:var(--done);}",
      ".set-msg.err{color:var(--danger);}",
      ".set-checks{display:flex;flex-wrap:wrap;gap:14px 20px;}",
      // align-items:flex-start: si la etiqueta envuelve a dos líneas, el
      // control queda arriba en vez de irse al medio.
      ".set-checks label{display:flex;align-items:flex-start;min-height:44px;gap:8px;font-size:16px;line-height:22px;color:var(--text,#1c1c1c);cursor:pointer;}",
      ".set-toggle{display:flex;align-items:flex-start;min-height:44px;gap:10px;font-size:16px;line-height:22px;color:var(--text,#1c1c1c);cursor:pointer;}",
      ".set-field input[type=checkbox],.set-field input[type=radio],.set-toggle input[type=checkbox]{width:20px;height:20px;min-height:20px;margin:0;margin-top:1px;padding:0;flex:0 0 20px;font-size:20px;}",
      ".set-toggle span{line-height:1.4;}",
      ".set-hsi{font-size:16px;line-height:1.5;color:var(--text,#1c1c1c);margin:0 0 8px;}",
      ".set-hsi a{color:var(--accent,#0e8b50);font-weight:600;}",
      ".set-hol-list{display:flex;flex-direction:column;gap:8px;}",
      ".set-hol-row{display:grid;grid-template-columns:170px minmax(0,1fr) auto auto;gap:8px 12px;align-items:center;}",
      ".set-hol-row input{font:inherit;font-size:16px;min-height:44px;padding:8px 12px;border:1px solid var(--border,#ccc);border-radius:8px;background:var(--panel,#fff);color:var(--text,#1c1c1c);min-width:0;box-sizing:border-box;}",
      ".set-hol-count{font-size:16px;font-weight:600;color:var(--text,#1c1c1c);white-space:nowrap;}",
      ".set-hol-empty{font-size:16px;color:var(--text,#1c1c1c);margin:0;}",
      // On a phone a row wraps to two lines (name, then date + Quitar): a rule
      // between rows and more air around it keep each date with its own name.
      "@media(max-width:720px){.set-hol-list{gap:0;}.set-hol-row{display:flex;flex-wrap:wrap;padding:16px 0;border-top:1px solid var(--border,#ccc);}.set-hol-row:first-child{border-top:0;padding-top:0;}.set-hol-row input[type=date]{flex:1 1 150px;}.set-hol-row input[type=text]{flex:1 1 100%;order:-1;}}",
      ".set-sub{font-size:15px;font-weight:650;margin:18px 0 8px;}",
      ".set-sub:first-child{margin-top:0;}",
      "@keyframes set-flash{0%,100%{background:transparent;}30%,70%{background:var(--accent-soft,#fff3cd);}}",
      ".set-row.set-highlight{animation:set-flash 1.6s ease;border-radius:8px;}",
      "@media(max-width:720px){.set-row{grid-template-columns:1fr;gap:4px;}.set-row>label{padding-top:0;}}",
      "@media(pointer:coarse){.set-field input:not([type=checkbox]):not([type=radio]),.set-field select,.set-field textarea,.set-actions .btn{min-height:44px;}.set-chain-hint{display:none;}}",
    ].join("");
    document.head.appendChild(st);
  }

  var data = null;
  var VISTA = "general";

  function directScreen() { return true; }

  // The server sends each language's own name (`core/i18n.py`'s
  // LANGUAGE_NAMES, via the settings payload); this map only covers a payload
  // that predates it.
  var LANGUAGE_NAMES = { es: "Español", en: "English" };
  function languageName(code) {
    var names = (data && data.languages && data.languages.nombres) || {};
    return names[code] || LANGUAGE_NAMES[code] || String(code || "").toUpperCase();
  }

  // ---- persistencia (solo, en el módulo — evita pedir GET tras cada guardar) --

  function actualizarSecreto(clave, configurado, last4) {
    (data.api_keys || []).forEach(function (grp) {
      grp.campos.forEach(function (c) {
        if (c.clave === clave) { c.configurado = configurado; c.last4 = last4; }
      });
    });
  }

  // ---- render: Ubicación ----------------------------------------------------

  function renderUbicacion() {
    var loc = data.location || {};
    var chain = loc.cadena_actual || [];
    var disponibles = loc.paquetes_disponibles || [];
    // A pack reads as its place in the world — «Global › Argentina › Buenos
    // Aires» — never its level code or parent id (the product owner, 02.11: no service
    // values on screen).
    var porId = {};
    disponibles.forEach(function (p) { porId[p.id] = p; });
    function rutaPack(p) {
      var nombres = [];
      for (var cur = p, guard = 0; cur && guard < 10; cur = porId[cur.parent], guard++) nombres.unshift(cur.name || cur.id);
      return nombres.join(" › ");
    }
    var opciones = disponibles.map(function (p) {
      return '<option value="' + esc(p.id) + '"' + (chain.indexOf(p.id) !== -1 ? ' selected' : '') + '>' + esc(rutaPack(p)) + "</option>";
    }).join("");
    var idFields = [
      ["hospital", T("admin.system.settings.field.hospital", null, "Nombre del hospital/red")],
      ["city", T("admin.system.settings.field.city", null, "Localidad")],
      ["province", T("admin.system.settings.field.province", null, "Provincia")],
      ["network", T("admin.system.settings.field.network", null, "Nombre corto de la red")],
      ["network_long", T("admin.system.settings.field.network_long", null, "Nombre largo de la red")],
      ["emergency_number", T("admin.system.settings.field.emergency_number", null, "Teléfono de emergencia")],
    ];
    var identidad = loc.identidad || {};
    var filas = idFields.map(function (f) {
      var key = "SITE_ID_" + f[0].toUpperCase();
      // The long name of the network runs past a phone's width: it wraps in a
      // field that grows, never scrolls sideways out of sight (D11, 08.11).
      if (f[0] === "network_long") {
        return row(key, f[1], '<textarea class="set-grow set-one-line" rows="1" data-key="' + key + '">' +
          esc(identidad[f[0]] || "") + "</textarea>");
      }
      return row(key, f[1], '<input type="text" data-key="' + key + '" ' +
        'value="' + esc(identidad[f[0]] || "") + '">');
    }).join("");
    // The center a patient's hints name when a problem needs someone in
    // person (the product owner 23.10) — one of the installation's own centers.
    var principal = identidad.main_center || "";
    var centros = loc.centros || [];
    var opcionesCentro = (principal ? "" : '<option value="" selected></option>') +
      centros.map(function (c) {
        return '<option value="' + esc(c.slug) + '"' + (c.slug === principal ? " selected" : "") + ">" +
          esc(c.nombre) + "</option>";
      }).join("");
    filas += row("SITE_ID_MAIN_CENTER", T("admin.system.settings.field.main_center", null, "Centro principal"),
      '<select data-key="SITE_ID_MAIN_CENTER">' + opcionesCentro + "</select>");
    return '<div class="set-card" data-grupo="location"><h2>' + esc(T("admin.system.settings.location.title", null, "Ubicación")) + '</h2>' +
      '<div class="set-sub">' + esc(T("admin.system.settings.location.pack_sub", null, "Paquete de datos")) + '</div>' +
      row("PACK_CHAIN", T("admin.system.settings.location.chain_label", null, "Cadena activa"),
        '<select data-key="PACK_CHAIN" multiple size="' + Math.min(6, Math.max(3, disponibles.length)) + '">' +
        opciones + "</select>") +
      '<p class="set-lede set-chain-hint" style="margin:4px 0 0;">' +
      esc(T("admin.system.settings.location.chain_hint", null,
        "Ctrl/Cmd+click para elegir varios.")) + "</p>" +
      '<div class="set-sub">' + esc(T("admin.system.settings.location.identity_sub", null, "Identidad")) + '</div>' + filas +
      '<div class="set-actions"><button type="button" class="btn btn-accent set-guardar" data-grupo="location">' +
      esc(T("admin.system.common.save", null, "Guardar")) + '</button><span class="set-msg" data-msg="location"></span></div></div>';
  }

  function row(key, label, controlHtml) {
    return '<div class="set-row" data-campo="' + esc(key) + '"><label for="set-f-' + esc(key) + '">' +
      esc(label) + "</label>" +
      '<div class="set-field">' + controlHtml.replace("<input ", '<input id="set-f-' + esc(key) + '" ')
        .replace("<select ", '<select id="set-f-' + esc(key) + '" ')
        .replace("<textarea ", '<textarea id="set-f-' + esc(key) + '" ') + "</div></div>";
  }

  // ---- render: Idiomas --------------------------------------------------------

  function renderIdiomas() {
    var lang = data.languages || {};
    var disponibles = lang.disponibles || ["es"];
    var habilitados = lang.habilitados || ["es"];
    var checks = disponibles.map(function (code) {
      var on = habilitados.indexOf(code) !== -1;
      return '<label><input type="checkbox" data-lang-check value="' + esc(code) + '" ' +
        (on ? "checked" : "") + ">" + esc(languageName(code)) + "</label>";
    }).join("");
    var opcionesDefault = habilitados.map(function (c) {
      return '<option value="' + esc(c) + '" ' + (c === lang.default ? "selected" : "") + ">" +
        esc(languageName(c)) + "</option>";
    }).join("");
    var modos = [["off", T("admin.system.settings.languages.mode_off", null, "Apagado: nadie cambia de idioma")],
                 ["nav", T("admin.system.settings.languages.mode_nav", null, "Visible en la barra de navegación")],
                 ["secret", T("admin.system.settings.languages.mode_secret", null, "Oculto: sólo por URL (?lang=en)")]];
    var opcionesModo = modos.map(function (m) {
      return '<option value="' + m[0] + '" ' + (m[0] === lang.modo_switcher ? "selected" : "") + ">" +
        esc(m[1]) + "</option>";
    }).join("");
    return '<div class="set-card" data-grupo="languages"><h2>' + esc(T("admin.system.settings.languages.title", null, "Idiomas")) + '</h2>' +
      row("LANGUAGES", T("admin.system.settings.languages.enabled_label", null, "Habilitados"), '<div class="set-checks">' + checks + "</div>") +
      row("LANG_DEFAULT", T("admin.system.settings.languages.default_label", null, "Idioma inicial"), '<select data-key="LANG_DEFAULT">' + opcionesDefault + "</select>") +
      row("LANG_SWITCHER_MODE", T("admin.system.settings.languages.switcher_label", null, "Selector visible"), '<select data-key="LANG_SWITCHER_MODE">' + opcionesModo + "</select>") +
      '<div class="set-actions"><button type="button" class="btn btn-accent set-guardar" data-grupo="languages">' +
      esc(T("admin.system.common.save", null, "Guardar")) + '</button><span class="set-msg" data-msg="languages"></span></div></div>';
  }

  // ---- render: AI chat ---------------------------------------------------

  function renderChat() {
    var chat = data.chat || {};
    var greetings = chat.greetings || [];
    var fields = greetings.map(function (item) {
      var label = T("admin.system.settings.chat.greeting_label", { language: languageName(item.lang) },
        "Saludo en {language}");
      return row(item.key, label,
        '<textarea class="set-grow" data-key="' + esc(item.key) + '" rows="7" maxlength="' +
        esc(String(chat.max_length || 1200)) + '">' + esc(item.value || "") + "</textarea>");
    }).join("");
    return '<div class="set-card" data-grupo="chat"><h2>' +
      esc(T("admin.system.settings.chat.title", null, "Chat con IA")) + '</h2>' +
      fields +
      '<p class="set-lede set-chain-hint" style="margin:4px 0 0;">' +
      esc(T("admin.system.settings.chat.brand_hint", null,
        "Escribí {brand} donde va el nombre de la instalación.")) + " " +
      esc(T("admin.system.settings.chat.reset_hint", null,
        "Borrá el contenido de un campo y guardá para restaurar la traducción incluida.")) + '</p>' +
      '<div class="set-actions"><button type="button" class="btn btn-accent set-guardar" data-grupo="chat">' +
      esc(T("admin.system.common.save", null, "Guardar")) + '</button><span class="set-msg" data-msg="chat"></span></div></div>';
  }

  // ---- render: Turnos -----------------------------------------------------
  // Límite de reservas web pendientes o futuras de una persona para un mismo
  // servicio (booking_limits.py). Es el default del sitio: cada servicio y
  // cada profesional pueden pisarlo desde su propia ficha, en Contenido.

  function renderTurnos() {
    var t = data.turnos || {};
    return '<div class="set-card" data-grupo="turnos"><h2>' + esc(T("admin.system.settings.turnos.title", null, "Turnos")) + '</h2>' +
      row("MAX_ACTIVE_REQUESTS", T("admin.system.settings.turnos.max_label", null, "Máximo de reservas web por paciente"),
          '<input type="number" min="1" max="60" data-key="MAX_ACTIVE_REQUESTS" value="' +
          esc(String(t.max_activos || 1)) + '">') +
      row("BOOKING_HORIZON_DAYS", T("admin.settings.horizon_days", null, "Días de anticipación"),
          '<input type="number" min="1" max="365" data-key="BOOKING_HORIZON_DAYS" value="' + esc(String(t.dias_anticipacion || 31)) + '">') +
      row("BOOKING_OPENS_AT", T("admin.settings.opening_time", null, "Hora de apertura de turnos"),
          '<input type="time" data-key="BOOKING_OPENS_AT" value="' + esc(t.apertura_turnos || '') + '">') +
      '<label class="set-toggle" data-admin-binary="editable"><input type="checkbox" data-key="TURNOS_PHOTO_AUTO_ENHANCE"' +
      (t.photo_auto_enhance !== false ? ' checked' : '') + '><span>' + esc(T('admin.doctor.auto_enhance', null, 'Mejorar fotos automáticamente')) + '</span></label>' +
      '<div class="set-actions"><button type="button" class="btn btn-accent set-guardar" data-grupo="turnos">' +
      esc(T("admin.system.common.save", null, "Guardar")) + '</button><span class="set-msg" data-msg="turnos"></span></div></div>';
  }

  // ---- render: Feriados -----------------------------------------------------
  // Site holidays (modules/turnos/site_holidays.py, reference-deployment counterpart 2026-10-15): one
  // list closes those dates on every agenda at once. Appointments already
  // given on a holiday stay; each row says how many, so the desk can move
  // them. The rows are local state (`data.feriados.items`), not data-key
  // inputs: the card saves the whole list, never one field.

  function feriadosItems() {
    if (!data.feriados) data.feriados = { items: [], nacionales: false };
    if (!Array.isArray(data.feriados.items)) data.feriados.items = [];
    return data.feriados.items;
  }

  function feriadoTurnosHtml(n) {
    if (!n) return "";
    return '<span class="set-hol-count">' + esc(Tn("admin.settings.holidays.appointments", n, null,
      "{n} turno dado ese día", "{n} turnos dados ese día")) + "</span>";
  }

  function feriadoRowHtml(item, i) {
    var etqFecha = T("admin.settings.holidays.date", null, "Fecha");
    var etqNombre = T("admin.settings.holidays.name", null, "Nombre");
    var quitar = T("admin.settings.holidays.remove", null, "Quitar");
    return '<div class="set-hol-row" data-feriado="' + i + '">' +
      '<input type="date" aria-label="' + esc(etqFecha) + '" data-feriado-fecha value="' + esc(item.fecha || "") + '">' +
      '<input type="text" aria-label="' + esc(etqNombre) + '" maxlength="120" data-feriado-nombre value="' + esc(item.nombre || "") + '">' +
      feriadoTurnosHtml(item.turnos) +
      '<button type="button" class="set-mini" data-feriado-quitar="' + i + '">' + esc(quitar) + "</button></div>";
  }

  function renderFeriados() {
    var items = feriadosItems();
    var nacional = data.feriados.nacionales
      ? '<button type="button" class="set-mini" data-feriados-nacionales>' +
        esc(T("admin.settings.holidays.load_national", null, "Cargar feriados nacionales")) + "</button>"
      : "";
    return '<div class="set-card" data-grupo="feriados"><h2>' + esc(T("admin.settings.holidays.title", null, "Feriados")) + "</h2>" +
      // With no holiday the card is its «+ Agregar feriado» button alone.
      (items.length ? '<div class="set-hol-list">' + items.map(feriadoRowHtml).join("") + "</div>" : "") +
      '<div class="set-actions"><button type="button" class="set-mini" data-feriado-agregar>' +
      esc(T("admin.settings.holidays.add", null, "+ Agregar feriado")) + "</button>" + nacional + "</div>" +
      '<div class="set-actions"><button type="button" class="btn btn-accent" data-feriados-guardar>' +
      esc(T("admin.system.common.save", null, "Guardar")) + '</button><span class="set-msg" data-msg="feriados"></span></div></div>';
  }

  function syncFeriados(card) {
    var items = feriadosItems();
    $$(".set-hol-row", card).forEach(function (rowEl) {
      var item = items[+rowEl.getAttribute("data-feriado")];
      if (!item) return;
      item.fecha = $("[data-feriado-fecha]", rowEl).value;
      item.nombre = $("[data-feriado-nombre]", rowEl).value;
    });
  }

  function repaintFeriados(card) {
    var fresh = document.createElement("div");
    fresh.innerHTML = renderFeriados();
    var next = fresh.firstChild;
    card.replaceWith(next);
    wireFeriados(next);
    refreshPending();
    return next;
  }

  function guardarFeriados(card, valores, btn) {
    if (!alive() || pendingCards.has("feriados")) return;
    pendingCards.add("feriados");
    refreshPending();
    if (btn) btn.disabled = true;
    var token = epoch, view = viewEpoch;
    fetch("/api/admin/settings", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grupo: "feriados", valores: valores }),
    }).then(function (response) { return outcome(response, token, view); }).then(function (res) {
      if (!owns(token, view) || res.stale || res.transitioned) return;
      if (!res.response.ok || !validAcknowledgement(res.body, "feriados")) {
        msg(card, '[data-msg="feriados"]', errText(res.body, T("admin.system.common.save_error", null, "No se pudo guardar.")), false);
        return;
      }
      data.feriados = res.body.feriados;
      var next = repaintFeriados(card);
      msg(next, '[data-msg="feriados"]', T("admin.system.common.saved", null, "Guardado."), true);
    }).catch(function () {
      if (owns(token, view)) msg(host, '[data-msg="feriados"]', T("admin.system.common.save_error", null, "No se pudo guardar."), false);
    }).finally(function () {
      releaseCard("feriados");
      if (owns(token, view) && btn && btn.isConnected) btn.disabled = false;
    });
  }

  function wireFeriados(card) {
    if (!card) return;
    card.addEventListener("click", function (event) {
      var target = event.target;
      if (target.closest("[data-feriado-agregar]")) {
        syncFeriados(card);
        feriadosItems().push({ fecha: "", nombre: "" });
        var next = repaintFeriados(card);
        var last = $$(".set-hol-row", next).pop();
        if (last) $("[data-feriado-fecha]", last).focus();
        return;
      }
      var quitar = target.closest("[data-feriado-quitar]");
      if (quitar) {
        syncFeriados(card);
        feriadosItems().splice(+quitar.getAttribute("data-feriado-quitar"), 1);
        repaintFeriados(card);
        return;
      }
      var nacionales = target.closest("[data-feriados-nacionales]");
      var guardarBtn = target.closest("[data-feriados-guardar]");
      if (!nacionales && !guardarBtn) return;
      syncFeriados(card);
      // Loading the national list also saves what was typed, so no edit is lost.
      var valores = { items: feriadosItems().map(function (h) { return { fecha: h.fecha, nombre: h.nombre }; }) };
      if (nacionales) valores.nacionales = true;
      guardarFeriados(card, valores, nacionales || guardarBtn);
    });
  }

  // ---- render: Seguridad ---------------------------------------------------
  // Exact IPs that enter the admin login without Turnstile. Since 2026-08-08
  // only a VERIFIED loopback peer can match (ip_exempt.py — forwarded-IP
  // headers are client-suppliable with no CDN in front, so remote-IP
  // exemptions were retired; scripted logins use the signed
  // X-Turnstile-Test-Bypass header instead). Never ranges or CIDRs.

  function renderSeguridad() {
    var s = data.seguridad || {};
    var ips = (s.ips_exentas || []).join("\n");
    return '<div class="set-card" data-grupo="seguridad"><h2>' + esc(T("admin.system.settings.security.title", null, "Seguridad")) + '</h2>' +
      row("IPS_EXENTAS", T("admin.system.settings.security.ips_label", null, "IPs exentas"),
          '<textarea data-key="IPS_EXENTAS" rows="4">' + esc(ips) + "</textarea>") +
      '<div class="set-actions"><button type="button" class="btn btn-accent set-guardar" data-grupo="seguridad">' +
      esc(T("admin.system.common.save", null, "Guardar")) + '</button><span class="set-msg" data-msg="seguridad"></span></div></div>';
  }

  // ---- render: API keys ---------------------------------------------------

  function inferenceProfile() {
    var group = (data.api_keys || []).find(function (g) { return g.id === "openrouter"; }) || {};
    var field = (group.campos || []).find(function (c) { return c.clave === "AI_INFERENCE_PROFILE"; }) || {};
    return field.valor || "cloud_shared";
  }

  function campoHtml(c) {
    if (c.tipo === "toggle") {
      return '<input type="checkbox" data-key="' + esc(c.clave) + '" data-tipo="toggle"' + (c.valor === "1" ? " checked" : "") + '>';
    }
    if (c.tipo === "number") {
      return '<input type="number" min="1" data-key="' + esc(c.clave) + '" value="' + esc(c.valor) + '">';
    }
    if (c.tipo === "profile") {
      var selected = c.valor || "cloud_shared";
      var labels = {
        cloud_shared: T("admin.system.settings.inference.profile_cloud_shared", null, "Nube compartida"),
        hosted_jurisdiction: T("admin.system.settings.inference.profile_hosted_jurisdiction", null, "Alojado en tu jurisdicción"),
        self_hosted_vllm: T("admin.system.settings.inference.profile_self_hosted", null, "Autogestionado")
      };
      return '<select data-key="' + esc(c.clave) + '" data-inference-profile>' +
        ["cloud_shared", "hosted_jurisdiction", "self_hosted_vllm"].map(function (profile) {
          return '<option value="' + profile + '"' + (selected === profile ? " selected" : "") + ">" + esc(labels[profile]) + "</option>";
        }).join("") + "</select>";
    }
    if (c.tipo === "choice") {
      return '<select data-key="' + esc(c.clave) + '" data-tipo="text">' +
        (c.opciones || []).map(function (o) {
          return '<option value="' + esc(o.valor) + '"' + (o.valor === (c.valor || "") ? " selected" : "") + ">" + esc(o.etiqueta) + "</option>";
        }).join("") + "</select>";
    }
    if (c.clave === "AI_INFERENCE_BASE_URL") {
      var disabled = inferenceProfile() === "cloud_shared";
      return '<input type="url" data-key="' + esc(c.clave) + '" data-tipo="text" data-inference-base ' +
        (disabled ? "disabled " : "") + 'value="' + esc(c.valor || "") + '">';
    }
    if (c.tipo === "secret") {
      var estado = c.configurado
        ? '<span class="set-estado set-ok">' + esc(T("admin.system.settings.field.configured", { last4: c.last4 || "????" }, "Configurada, termina en {last4}")) + "</span>"
        : '<span class="set-estado">' + esc(T("admin.system.settings.field.not_configured", null, "No configurada")) + "</span>";
      var borrar = c.configurado
        ? '<button type="button" class="set-mini set-borrar" data-key="' + esc(c.clave) + '">' +
          esc(T("admin.system.common.delete", null, "Borrar")) + '</button>' : "";
      return '<input type="password" data-key="' + esc(c.clave) + '" data-tipo="secret" ' +
        'autocomplete="off" placeholder="' + esc(T("admin.system.settings.field.secret_placeholder", null, "Dejar en blanco para no cambiar")) + '">' + estado + borrar;
    }
    return '<input type="text" data-key="' + esc(c.clave) + '" data-tipo="text" ' +
      'value="' + esc(c.valor || "") + '">';
  }

  function renderIntegracion(grp) {
    var filas = grp.campos.map(function (c) {
      return row(c.clave, c.etiqueta, campoHtml(c));
    }).join("");
    var probar = grp.probar
      ? '<button type="button" class="set-mini set-probar" data-integracion="' + esc(grp.probar) +
        '">' + esc(T("admin.system.common.test", null, "Probar")) + '</button><span class="set-msg" data-probar="' + esc(grp.probar) + '"></span>' : "";
    var recovery = "";
    if (grp.id === "backup") {
      var status = grp.key_status || {};
      recovery = '<div class="set-row" data-campo="TURNOS_BACKUP_RECOVERY_KEY"><label>' +
        esc(T("backup.recovery_key", null, "Clave de recuperación")) + '</label><div class="set-field">' +
        (status.fingerprint ? '<span>' + esc(T("backup.fingerprint", null, "Huella de la clave")) + ': ' + esc(status.fingerprint) + '</span>' : '') +
        '<button type="button" class="set-mini" data-backup-generate data-rotate="' + (status.configured ? "1" : "0") + '">' +
        esc(status.configured ? T("backup.rotate", null, "Rotar clave") : T("backup.generate", null, "Generar clave de recuperación")) +
        '</button><div data-recovery-panel style="width:100%"></div></div></div>';
    }
    return '<div class="set-card" data-grupo="api_keys" data-integracion-card="' + esc(grp.id) + '">' +
      "<h2>" + esc(grp.titulo) + "</h2>" + filas + recovery +
      '<div class="set-actions"><button type="button" class="btn btn-accent set-guardar" ' +
      'data-grupo="api_keys" data-card="' + esc(grp.id) + '">' + esc(T("admin.system.common.save", null, "Guardar")) + '</button>' + probar +
      '<span class="set-msg" data-msg="api_keys-' + esc(grp.id) + '"></span></div></div>';
  }

  function renderApiKeys() {
    return (data.api_keys || []).map(renderIntegracion).join("");
  }

  function refreshIntegrationCard(sec, cardId) {
    var oldCard = sec.querySelector('[data-integracion-card="' + cardId + '"]');
    var group = (data.api_keys || []).find(function (item) { return item.id === cardId; });
    if (!oldCard || !group) return;
    var replacement = document.createElement("div");
    replacement.innerHTML = renderIntegracion(group);
    var freshCard = replacement.firstChild;
    oldCard.replaceWith(freshCard);
    cablear(freshCard);
  }

  // ---- render: Connectors -------------------------------------------------

  // The HSI state above the switch, in the same words «Sistema» and /admin/hsi
  // show: the server sends the payload already translated. In server mode the
  // journal line names the last transmission, the queue and the errors — the
  // parts that exist, never the zeros.
  function hsiStateHtml(estado) {
    if (!estado || typeof estado !== "object") return "";
    var out = '<p class="set-hsi"><b>' + esc(estado.modo_texto || "") + "</b>" +
      (estado.motivo_texto ? " " + esc(estado.motivo_texto) : "") + "</p>";
    var diario = estado.diario;
    if (diario && typeof diario === "object") {
      var partes = [];
      if (diario.ultima) {
        // The instant carries the installation's offset: «hace 5 minutos»
        // while fresh, then that day and hour. Without the formatter, the
        // server's own text — a transmission that happened is never «none».
        var cuando = (Dates && Dates.instanteServidor && Dates.instanteServidor(diario.ultima)) ||
          String(diario.ultima).slice(0, 16).replace("T", " ");
        partes.push(T("admin.system.settings.connectors.hsi_last", { when: cuando }, "Última transmisión {when}"));
      }
      if (Number(diario.en_cola) > 0) partes.push(T("admin.system.settings.connectors.hsi_queue", { n: Number(diario.en_cola) }, "{n} en cola"));
      if (Number(diario.errores) > 0) partes.push(T("admin.system.settings.connectors.hsi_errors", { n: Number(diario.errores) }, "{n} con error"));
      out += '<p class="set-hsi">' + (partes.length
        ? esc(partes.join(" · "))
        : esc(T("admin.system.settings.connectors.hsi_none_yet", null, "Todavía ninguna transmisión."))) + "</p>";
    }
    if (Number(estado.total) > 0) {
      out += '<p class="set-hsi"><a href="/admin/hsi">' + esc(estado.centros_texto || "") + "</a>" +
        (estado.centros_detalle ? " · " + esc(estado.centros_detalle) : "") + "</p>";
    }
    return out;
  }

  function renderConnectors() {
    var emr = (data.connectors || {}).emr_sync || {};
    return '<div class="set-card" data-grupo="connectors">' +
      '<h2>' + esc(T("admin.system.settings.connectors.title", null, "Sistema clínico")) + '</h2>' +
      hsiStateHtml((data.connectors || {}).hsi) +
      '<label class="set-toggle" data-admin-binary="editable"><input type="checkbox" name="HSI_ENABLED" data-connector-hsi' +
      (emr.hsi_enabled ? " checked" : "") + '><span>' +
      esc(T("admin.system.settings.connectors.hsi_label", null, "Sincronización de turnos con HSI")) +
      '</span></label><p class="set-msg" data-msg="connectors" role="status" aria-live="polite"></p></div>';
  }

  // ---- render completo ---------------------------------------------------

  function render() {
    var sec = host;
    if (!alive()) return;
    if (!data) { sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.system.common.loading", null, "Cargando…")) + '</p>'; return; }
    var lock = data.lock
      ? '<p class="set-lock">' + esc(T("admin.system.settings.lock_banner", null,
        "La configuración está bloqueada en el servidor: los cambios se guardan, pero no se aplican hasta que se quite el bloqueo.")) + "</p>" : "";
    sec.innerHTML = lock + (VISTA === "integrations"
      ? renderApiKeys()
      : VISTA === "connectors"
        ? renderConnectors()
        : renderUbicacion() + renderIdiomas() + renderChat() + renderTurnos() + renderFeriados() + renderSeguridad());
    restoreDrafts();
    cablear(sec);
    crecer(sec);
    irACampoPendiente();
  }

  // ---- deep link from System: ?field=<CAMPO> highlights and scrolls -------

  var campoPendiente = null;

  function campoEsIntegracion(clave) {
    if (clave === "TURNOS_BACKUP_RECOVERY_KEY") return true;
    return !!(data && (data.api_keys || []).some(function (grupo) {
      return (grupo.campos || []).some(function (campo) { return campo.clave === clave; });
    }));
  }

  function actualizarChrome() {
    // «Probar» exists only on Integraciones: its AI notice is shown there and nowhere else.
    var probeNotice = document.querySelector('[data-ai-notice="probe"]');
    if (probeNotice) probeNotice.hidden = VISTA !== "integrations";
    var nav = document.querySelectorAll("#adm-nav a");
    nav.forEach(function (a) {
    var target = directScreen()
        ? "settings"
        : (VISTA === "integrations" ? "settings-integrations" : "settings-general");
      a.classList.toggle("on", a.getAttribute("data-nav") === target);
    });
    $$('[data-settings-view]').forEach(function (a) {
      if (a.getAttribute("data-settings-view") === VISTA) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var h1 = document.getElementById("adm-h1");
    if (h1) {
      h1.style.display = "";
      h1.textContent = VISTA === "integrations"
        ? T("admin.system.settings.nav.integrations", null, "Integraciones")
        : VISTA === "connectors"
          ? T("admin.system.settings.nav.connectors", null, "Conectores")
          : T("admin.system.settings.nav.general", null, "Datos e idiomas");
    }
  }

  function irACampoPendiente() {
    if (!campoPendiente) return;
    var clave = campoPendiente;
    var fila = $$(".set-row[data-campo]").find(function (rowEl) {
      return rowEl.getAttribute("data-campo") === clave;
    });
    campoPendiente = null;
    if (!fila) return;
    fila.scrollIntoView({ behavior: "smooth", block: "center" });
    fila.classList.add("set-highlight");
    var input = fila.querySelector("input, select, textarea");
    if (input) input.focus({ preventScroll: true });
    setTimeout(function () { fila.classList.remove("set-highlight"); }, 1700);
  }

  // ---- acciones -------------------------------------------------------------

  function valoresDe(root) {
    var out = {};
    $$("[data-key]", root).forEach(function (el) {
      if (el.disabled) return;
      if (el.type === "checkbox") { out[el.getAttribute('data-key')] = el.checked ? '1' : '0'; return; }
      out[el.getAttribute("data-key")] = el.multiple
        ? $$("option", el).filter(function (o) { return o.selected; }).map(function (o) { return o.value; }).join(",")
        : el.value;
    });
    var checks = $$("[data-lang-check]", root);
    if (checks.length) {
      out.LANGUAGES = checks.filter(function (c) { return c.checked; }).map(function (c) { return c.value; }).join(",");
    }
    return out;
  }

  function msg(root, sel, texto, ok) {
    var el = root.querySelector(sel);
    if (!el) return;
    el.textContent = texto;
    el.className = "set-msg" + (ok ? "" : " err");
  }

  function outcome(response, token, view) {
    return response.json().catch(function () { return null; }).then(function (body) {
      if (!owns(token, view)) return { stale: true };
      var boundary = (response.status === 401 || response.status === 403)
        ? sessionReady.then(function (mod) {
          if (!owns(token, view)) return false;
          return mod.confirmar(response, body, function () { return owns(token, view); });
        })
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        if (!owns(token, view)) return { stale: true };
        return { response: response, body: body || {}, transitioned: transitioned };
      });
    });
  }

  function guardar(card, grupo, msgSel, btn) {
    var key = cardKey(card);
    if (!alive() || pending.has(card) || pendingCards.has(key)) return;
    pending.add(card);
    pendingCards.add(key);
    refreshPending();
    if (btn) btn.disabled = true;
    var token = epoch;
    var view = viewEpoch;
    var body = { grupo: grupo, valores: valoresDe(card) };
    fetch("/api/admin/settings", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }).then(function (response) { return outcome(response, token, view); })
      .then(function (res) {
        if (!owns(token, view) || res.stale || res.transitioned) return;
        var sec = host;
        if (res.response.ok) {
          if (!validAcknowledgement(res.body, grupo)) throw new Error("Invalid Settings save response");
          if (res.body[grupo]) data[grupo === "api_keys" ? "api_keys" : grupo] = res.body[grupo];
          captureDrafts();
          if (grupo === "api_keys" && JSON.stringify(valoresDe(card)) === JSON.stringify(body.valores)) refreshIntegrationCard(sec, card.getAttribute("data-integracion-card"));
          msg(sec, msgSel, T("admin.system.common.saved", null, "Guardado."), true);
        } else {
          msg(sec, msgSel, errText(res.body, T("admin.system.common.save_error", null, "No se pudo guardar.")), false);
        }
      })
      .catch(function () { if (owns(token, view)) msg(host, msgSel, T("admin.system.common.save_error", null, "No se pudo guardar."), false); })
      .finally(function () { pending.delete(card); releaseCard(key); if (owns(token, view) && btn && btn.isConnected) btn.disabled = false; });
  }

  function borrarCampo(clave, btn) {
    var card = btn.closest(".set-card");
    if (!alive() || pending.has(btn) || !card) return;
    var key = cardKey(card);
    if (pendingCards.has(key)) return;
    pending.add(btn);
    pendingCards.add(key);
    refreshPending();
    btn.disabled = true;
    var token = epoch;
    var view = viewEpoch;
    var msgSel = '[data-msg="api_keys-' + card.getAttribute("data-integracion-card") + '"]';
    fetch("/api/admin/settings", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grupo: "api_keys", valores: {}, borrar: [clave] }),
    }).then(function (response) { return outcome(response, token, view); }).then(function (result) {
        if (!owns(token, view) || result.stale || result.transitioned) return;
        if (!result.response.ok || !validAcknowledgement(result.body, "api_keys")) throw new Error(result.body.error || "Invalid Settings delete response");
        data.api_keys = result.body.api_keys;
        // Preserve other fields, including newly typed secret inputs. Only
        // update this removed key's public status and deletion control.
        var rowEl = btn.closest(".set-row");
        var status = rowEl && rowEl.querySelector(".set-estado");
        if (status) { status.textContent = T("admin.system.settings.field.not_configured", null, "No configurada"); status.className = "set-estado"; }
        btn.remove();
        msg(host, msgSel, T("admin.system.common.saved", null, "Guardado."), true);
      }).catch(function (error) {
        if (owns(token, view)) msg(host, msgSel, errText(error, T("admin.system.common.save_error", null, "No se pudo guardar.")), false);
      }).finally(function () { pending.delete(btn); releaseCard(key); if (owns(token, view) && btn.isConnected) btn.disabled = false; });
  }

  function probar(integracion, btn) {
    if (!alive() || pending.has(btn)) return;
    pending.add(btn);
    var token = epoch;
    var view = viewEpoch;
    var sec = host;
    var sel = '[data-probar="' + integracion + '"]';
    msg(sec, sel, T("admin.system.settings.probe.testing", null, "Probando…"), true);
    btn.disabled = true;
    fetch("/api/admin/settings/probar", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ integracion: integracion }),
    }).then(function (response) { return outcome(response, token, view); }).then(function (result) {
        if (!owns(token, view) || result.stale || result.transitioned) return;
        var j = result.body;
        msg(sec, sel, result.response.ok && j.ok ? (j.detalle || T("admin.system.settings.probe.works", null, "Funciona.")) +
              (j.key_activa ? T("admin.system.settings.probe.works_key", { key: j.key_activa }, " (key {key})") : "")
                          : (j.detalle || (j.error === "demo_solo_lectura" ? errText(j, "") : T("admin.system.settings.probe.fails", null, "No funciona."))), !!(result.response.ok && j.ok));
      })
      .catch(function () { if (owns(token, view)) msg(sec, sel, T("admin.system.settings.probe.error", null, "No se pudo probar."), false); })
      .finally(function () { pending.delete(btn); if (owns(token, view)) btn.disabled = false; });
  }

  function guardarConector(input) {
    if (!alive() || pending.has(input) || pendingCards.has("connectors")) return;
    pending.add(input);
    pendingCards.add("connectors");
    var token = epoch;
    var view = viewEpoch;
    var sec = host;
    var enabled = input.checked;
    input.disabled = true;
    msg(sec, '[data-msg="connectors"]', T("admin.system.settings.connectors.saving", null, "Guardando…"), true);
    fetch("/api/admin/settings", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grupo: "connectors", valores: { HSI_ENABLED: enabled } }),
    }).then(function (response) { return outcome(response, token, view); }).then(function (saved) {
      if (!owns(token, view) || saved.stale) return null;
      if (saved.transitioned) return null;
      if (!saved.response.ok || !validAcknowledgement(saved.body, "connectors")) throw new Error(saved.body.error || T("admin.system.common.save_error", null, "No se pudo guardar."));
      return fetch("/api/admin/settings", { credentials: "same-origin" });
    }).then(function (r) {
      if (!owns(token, view)) return null;
      if (!r) return null;
      return outcome(r, token, view);
    }).then(function (fresh) {
      if (!owns(token, view) || !fresh || fresh.stale) return;
      if (fresh.transitioned) return;
      if (!fresh.response.ok || !validPayload(fresh.body)) throw new Error(T("admin.system.common.load_error", null, "No se pudo cargar esta sección."));
      data = fresh.body;
      render();
      msg(host, '[data-msg="connectors"]',
        T("admin.system.common.saved", null, "Guardado."), true);
    }).catch(function (error) {
      if (!owns(token, view)) return;
      input.checked = !enabled;
      input.disabled = false;
      msg(host, '[data-msg="connectors"]',
        errText(error, T("admin.system.common.save_error", null, "No se pudo guardar.")), false);
    }).finally(function () { pending.delete(input); releaseCard("connectors"); if (owns(token, view) && input.isConnected) input.disabled = false; });
  }

  // A text field as tall as its text: the last line of a greeting was cut by
  // the field's bottom edge on a phone (D11, 08.11). A one-line value keeps
  // to one line: Enter does nothing and pasted line breaks become spaces.
  function alto(el) {
    if (!el || !el.style) return;
    el.style.height = "auto";
    if (el.scrollHeight) el.style.height = (el.scrollHeight + 2) + "px";
  }
  function crecer(sec) {
    $$("textarea.set-grow", sec).forEach(function (el) {
      alto(el);
      if (el.__setGrow) return;
      el.__setGrow = true;
      el.addEventListener("input", function () {
        if (el.classList.contains("set-one-line") && /[\r\n]/.test(el.value)) el.value = el.value.replace(/[\r\n]+/g, " ");
        alto(el);
      });
      if (el.classList.contains("set-one-line")) el.addEventListener("keydown", function (event) {
        if (event.key === "Enter") event.preventDefault();
      });
    });
  }
  function onResizeGrow() { if (alive()) crecer(host); }

  function cablear(sec) {
    $$("[data-backup-generate]", sec).forEach(function (button) {
      button.addEventListener("click", function () {
        var token = epoch, view = viewEpoch;
        var card = button.closest(".set-card");
        if (!alive() || pendingCards.has("backup")) return;
        pendingCards.add("backup");
        button.disabled = true;
        refreshPending();
        fetch("/api/admin/settings", {
          method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ grupo: "backup_key", valores: { rotate: button.dataset.rotate === "1" } })
        }).then(function (response) { return outcome(response, token, view); }).then(function (result) {
          if (!owns(token, view) || result.stale || result.transitioned) return;
          if (!result.response.ok || !result.body.backup_key || !result.body.backup_key.recovery_key) throw new Error(result.body && result.body.error === "demo_solo_lectura" ? result.body.error : "key");
          var key = result.body.backup_key;
          return importStatic("platform/backup-key.mjs").then(function (mod) {
            if (!owns(token, view)) return;
            mod.wire(card.querySelector("[data-recovery-panel]"), {
              key: key.recovery_key,
              fingerprint: T("backup.save_key", null, "Guardá esta clave fuera del servidor") + " (" + key.fingerprint + ")",
              copyLabel: T("backup.copy", null, "Copiar"), downloadLabel: T("backup.download", null, "Descargar clave")
            });
            // Keep this key visible until the operator leaves the current view.
            button.remove();
          });
        }).catch(function (err) {
          if (owns(token, view)) msg(card, '[data-msg="api_keys-backup"]',
            errText(err, T("backup.key_error", null, "No se pudo guardar la clave. Revisá el bloqueo y el archivo de configuración.")), false);
        }).finally(function () { releaseCard("backup"); if (owns(token, view) && button.isConnected) button.disabled = false; });
      });
    });
    $$('[data-inference-profile]', sec).forEach(function (select) {
      select.addEventListener("change", function () {
        var card = select.closest(".set-card");
        var base = card && card.querySelector("[data-inference-base]");
        if (base) base.disabled = select.value === "cloud_shared";
      });
    });
    $$(".set-guardar", sec).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var grupo = btn.getAttribute("data-grupo");
        var card = btn.closest(".set-card");
        var msgSel = '[data-msg="' + (btn.getAttribute("data-card")
          ? grupo + "-" + btn.getAttribute("data-card") : grupo) + '"]';
        guardar(card, grupo, msgSel, btn);
      });
    });
    $$(".set-borrar", sec).forEach(function (btn) {
      btn.addEventListener("click", function () { borrarCampo(btn.getAttribute("data-key"), btn); });
    });
    $$(".set-probar", sec).forEach(function (btn) {
      btn.addEventListener("click", function () { probar(btn.getAttribute("data-integracion"), btn); });
    });
    $$('[data-connector-hsi]', sec).forEach(function (input) {
      input.addEventListener("change", function () { guardarConector(input); });
    });
    wireFeriados($('.set-card[data-grupo="feriados"]', sec));
    refreshPending();
  }

  // ---- carga + router --------------------------------------------------------

  function parseDirectState() {
    var params = new URLSearchParams(location.search || "");
    var views = params.getAll("view");
    var fields = params.getAll("field");
    var view = views.length === 1 && (views[0] === "general" || views[0] === "connectors" || views[0] === "integrations")
      ? views[0] : "general";
    var field = fields.length === 1 && /^[A-Z][A-Z0-9_]{0,127}$/.test(fields[0])
      ? fields[0] : null;
    return { view: view, field: field };
  }

  function generalFieldExists(clave) {
    if (!data) return false;
    var probe = document.createElement("div");
    probe.innerHTML = renderUbicacion() + renderIdiomas() + renderChat() + renderTurnos() + renderSeguridad();
    return $$(".set-row[data-campo]", probe).some(function (rowEl) {
      return rowEl.getAttribute("data-campo") === clave;
    });
  }

  function fieldView(clave) {
    if (!clave || !data) return null;
    if (campoEsIntegracion(clave)) return "integrations";
    return generalFieldExists(clave) ? "general" : null;
  }

  function directUrl(view, field) {
    var url = new URL(location.href);
    url.hash = "";
    url.searchParams.delete("view");
    url.searchParams.delete("field");
    if (field) url.searchParams.set("field", field);
    else if (view === "connectors" || view === "integrations") url.searchParams.set("view", view);
    return url.pathname + url.search;
  }

  function applyDirectState(replace) {
    var requested = parseDirectState();
    var owner = fieldView(requested.field);
    campoPendiente = owner ? requested.field : null;
    VISTA = owner || requested.view;
    var canonical = directUrl(VISTA, campoPendiente);
    if (canonical !== location.pathname + location.search) {
      history[replace ? "replaceState" : "pushState"]({}, "", canonical);
    }
  }

  function navigateDirect(view) {
    captureDrafts();
    viewEpoch += 1;
    campoPendiente = null;
    VISTA = view === "connectors" ? "connectors" : view === "integrations" ? "integrations" : "general";
    var canonical = directUrl(VISTA, null);
    if (canonical !== location.pathname + location.search) history.pushState({}, "", canonical);
    actualizarChrome();
    render();
  }

  function validPayload(payload) {
    return !!payload && typeof payload === "object" && !Array.isArray(payload) &&
      !!payload.location && !!payload.languages && !!payload.chat && !!payload.turnos &&
      !!payload.seguridad && !!payload.connectors && Array.isArray(payload.api_keys);
  }

  function validAcknowledgement(payload, group) {
    if (!payload || payload.ok !== true) return false;
    var result = payload[group];
    return group === "api_keys" ? Array.isArray(result) :
      !!result && typeof result === "object" && !Array.isArray(result);
  }

  function cargar() {
    if (!alive() || loadRequest) return;
    var token = epoch;
    var sec = host;
    if (sec && !sec.innerHTML) sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.system.common.loading", null, "Cargando…")) + '</p>';
    loadRequest = new AbortController();
    return fetch("/api/admin/settings", { credentials: "same-origin", signal: loadRequest.signal })
      .then(function (r) {
        if (!owns(token)) return null;
        return outcome(r, token).then(function (result) {
          if (!owns(token) || result.stale || result.transitioned) return result;
          return { data: result.response.ok && validPayload(result.body) ? result.body : null };
        });
      })
      .then(function (result) {
        if (!owns(token) || !result || result.stale || result.transitioned) return;
        if (result.data) {
          data = result.data;
          applyDirectState(true);
          actualizarChrome();
          render();
        } else throw new Error("Invalid or unavailable Settings response");
      })
      .catch(function (error) {
        if (!owns(token)) return;
        if (options.onError) options.onError(error);
        else if (sec) sec.innerHTML = '<p class="adm-empty" role="alert">' + esc(T("admin.system.common.load_error", null, "No se pudo cargar esta sección.")) + '</p>';
      })
      .finally(function () { if (token === epoch) loadRequest = null; });
  }

  function route() {
    inyectarCss();
    var el = host;
    if (!alive()) return;
    el.style.display = "";
    var sub = document.getElementById("adm-sub");
    if (sub) sub.style.display = "none";
    if (data) {
      captureDrafts();
      viewEpoch += 1;
      applyDirectState(true);
      actualizarChrome();
      render();
      return Promise.resolve();
    } else {
      VISTA = parseDirectState().view;
      actualizarChrome();
      return cargar();
    }
  }

  function onPopstate() {
    route();
  }
  function onClick(event) {
    var link = event.target && event.target.closest && event.target.closest("a[data-settings-view]");
    if (!link || event.defaultPrevented || event.button !== 0 ||
        event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigateDirect(link.getAttribute("data-settings-view"));
  }
  dispose.refreshPending = refreshPending;
  mounts.set(host, dispose);
  window.addEventListener("popstate", onPopstate);
  window.addEventListener("resize", onResizeGrow);
  document.addEventListener("click", onClick);
  var dependencies = Promise.all([sessionReady, i18nReady, datesReady]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function () {
    if (!alive()) return;
    return route();
  }).catch(function (error) {
    if (!alive()) return;
    if (options.onError) options.onError(error);
    else host.innerHTML = '<p class="adm-empty" role="alert">' + esc(T("admin.system.common.load_error", null, "No se pudo cargar esta sección.")) + '</p>';
  });
  return dispose;
}
