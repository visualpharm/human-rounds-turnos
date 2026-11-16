/* static/platform/account-form.mjs — reusable register / login / recover /
   account-edit / password-reset component. Canonical ES module. Named
   exports; no browser global, no classic twin, no CommonJS wrapper.
   Lives under static/platform/ and is loaded through window.SANA_STATIC.
   Top-level await on platform/i18n.mjs so a consumer that awaits this
   module also awaits its translation dependency — T() is live from the
   first renderAuth / renderEditar / renderReset / dobField call.

   Named API:
     renderAuth(host, {
       prefill:   {dni, nombre, apellido, nacimiento, sexo, contacto, email},
       defaultMode: "registro" | "login" | "recuperar",
       heading:   true | false,
       onDone:    function (usuario) { ... }
     })
     renderEditar(host, { usuario, onSave(usuarioActualizado) })
     renderReset(host, { token, onDone(usuario) })
     parseFlexibleDate, isoToArg, ageFromIso, dobField, fileToJpeg,
     photoFileFromEvent, eventCarriesFiles, isHeicFile, fileAsDataUrl
     (the photo surfaces: paste/drop that never takes a text field's paste,
     HEIC detection by bytes, raw-file passthrough for the server to judge)

   "recuperar" mode (direct or login's "¿Olvidaste tu contraseña?") posts to
   POST /api/account/recover. Register/login post to /api/account/{register,login};
   a 409 switches to login keeping the DNI. TURNOS_AUTH_MODE=renaper shows an
   identity-provider handoff. Stub mode opens the synthetic person picker.
   Identity is mirrored to localStorage (st_identity) for service.js continuity.

   Injects its own <style> (like nav.js) — does not touch shared CSS.
   Form-design: content-width fields (DNI mono, phone ~16ch, date ~12ch),
   day-first flexible DOB (never a calendar as the primary input) with
   understood-date echo + age, DNI/phone normalisation while typing,
   left labels ≥480px, inputs ≥16px. */
  // dni-barcode.mjs (the tier-1 DNI photo reader) is fingerprinted
  // (core/static_fingerprint.py, CLIENT_LOADED): a served file can't know its
  // current URL, so the shell publishes it as window.SANA_STATIC and
  // staticUrl() resolves through that map — fail-closed (null +
  // console.error, never a hardcoded fallback URL), the same shape as
  // islands.mjs's resolveUrl(). importStatic() rejects on a missing entry
  // exactly like an import() that failed to load, so the existing .catch at
  // the call site falls back to the tier-2 OCR path unchanged.
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

  // Interface strings go through the catalog. The third argument is the
  // Spanish this call site replaced, so with no catalog loaded — a
  // single-language installation, a Storybook story, a node test — every
  // one of these renders exactly what it rendered before.
  var I18n = await (
    (typeof window !== "undefined")
      ? ((window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
        ? import(window.SANA_STATIC["platform/i18n.mjs"])
        : Promise.reject(new Error("[static] platform/i18n.mjs is not in window.SANA_STATIC")))
      : import(new URL("./i18n.mjs", import.meta.url))
  );
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  // Visitor's language, resolved ONCE per request by the server and exposed
  // as configuration data (window.SANA_I18N.active — the same wire value
  // <html lang> renders). NEVER keyed on rendered/translated text.
  function activeLang() {
    return I18n && typeof I18n.activeLang === "function"
      ? I18n.activeLang()
      : "es";
  }
  // Demo rule (the product owner, 2026-09-11): a visitor whose active language is not
  // Spanish is NEVER asked for a DNI, a DNI photo/barcode scan, an obra
  // social / prepaga / nº de afiliado, or a PUCO lookup — the gate is the
  // visitor's LANGUAGE, not the installation country. The server mints a
  // synthetic identity key and the account is keyed by email instead.
  // Spanish visitors keep today's flow byte-for-byte.
  function sinDniVisitante() {
    return activeLang().split("-")[0] !== "es";
  }
  function emailValida(v) {
    return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(v || "").trim());
  }

  // ---------- parser de fecha flexible para campos de entrada ----------
  var MIN_YEAR = 1900;
  var MONTHS = {
    ene: 1, enero: 1, jan: 1, january: 1, feb: 2, febrero: 2, february: 2,
    mar: 3, marzo: 3, march: 3, abr: 4, abril: 4, apr: 4, april: 4, may: 5,
    mayo: 5, jun: 6, junio: 6, june: 6, jul: 7, julio: 7, july: 7, ago: 8,
    agosto: 8, aug: 8, august: 8, sep: 9, sept: 9, setiembre: 9, septiembre: 9,
    september: 9, oct: 10, octubre: 10, october: 10, nov: 11, noviembre: 11,
    november: 11, dic: 12, diciembre: 12, dec: 12, december: 12
  };
  function curY() { return new Date().getFullYear(); }
  function dim(y, m) { return new Date(y, m, 0).getDate(); }
  function pad(n, l) { return String(n).padStart(l, "0"); }
  function isoToday() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1, 2) + "-" + pad(d.getDate(), 2);
  }
  function build(y, m, d) {
    if (!(y >= MIN_YEAR && y <= curY())) return null;
    if (!(m >= 1 && m <= 12)) return null;
    if (!(d >= 1 && d <= dim(y, m))) return null;
    var iso = pad(y, 4) + "-" + pad(m, 2) + "-" + pad(d, 2);
    if (iso > isoToday()) return null;
    return iso;
  }
  function expandYear(yy, attempt) {
    var c = 2000 + yy;
    if (c <= curY()) { var iso = attempt(c); if (iso) return iso; }
    return attempt(1900 + yy);
  }
  function orderDM(a, b, y) { return build(y, b, a) || build(y, a, b) || null; }
  function parseFlexibleDate(input) {
    if (input == null) return null;
    var s = String(input).trim().toLowerCase();
    if (!s) return null;
    if (/[a-záéíóúñ]{3,}/i.test(s)) {
      var mm = s.match(/(ene|enero|feb|febrero|mar|marzo|abr|abril|may|mayo|jun|junio|jul|julio|ago|agosto|sep|sept|setiembre|septiembre|oct|octubre|nov|noviembre|dic|diciembre|jan|january|february|march|april|june|july|august|september|october|november|december)/);
      if (mm) {
        var mon = MONTHS[mm[1]], nums = (s.match(/\d+/g) || []).map(Number), day = null, yr = null;
        for (var i = 0; i < nums.length; i++) {
          if (nums[i] > 31 || String(nums[i]).length === 4) yr = nums[i];
          else if (day === null) day = nums[i];
        }
        if (yr === null) for (var j = 0; j < nums.length; j++) if (nums[j] !== day) { yr = nums[j]; break; }
        if (mon && day != null && yr != null) {
          var isoN = yr < 100 ? expandYear(yr, function (y) { return build(y, mon, day); }) : build(yr, mon, day);
          if (isoN) return isoN;
        }
      }
    }
    var bare = s.replace(/\s/g, ""), digits = s.replace(/[^\d]/g, "");
    if (/^\d+$/.test(bare) && digits.length === bare.length) {
      if (digits.length === 8) {
        var d8 = orderDM(+digits.slice(0, 2), +digits.slice(2, 4), +digits.slice(4));
        return d8 || build(+digits.slice(0, 4), +digits.slice(4, 6), +digits.slice(6));
      }
      if (digits.length === 6) return expandYear(+digits.slice(4), function (y) { return orderDM(+digits.slice(0, 2), +digits.slice(2, 4), y); });
      return null;
    }
    var parts = s.split(/[^\d]+/).filter(Boolean);
    if (parts.length === 3) {
      var n0 = +parts[0], n1 = +parts[1], n2 = +parts[2];
      if (parts[0].length === 4) return build(n0, n1, n2) || build(n0, n2, n1);
      if (parts[2].length === 4) return orderDM(n0, n1, n2);
      return expandYear(n2, function (y) { return orderDM(n0, n1, y); });
    }
    return null;
  }
  function isoToArg(iso) { var m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/); return m ? (m[3] + "/" + m[2] + "/" + m[1]) : ""; }
  // El eco de la fecha tipeada va EN PALABRAS (responsable del producto, 2026-07-12): "28 de octubre
  // de 1978" no se puede leer al revés; "28/10/1978" sí. El nombre del mes sale
  // del catálogo compartido date.month_full.* (el mismo que usa listing.js),
  // no de una cuarta tabla de pantalla. Este parser/eco pertenece al control
  // de entrada flexible; las fechas de lectura usan el módulo dates.mjs.
  var _MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  function isoEnPalabras(iso) {
    var m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return "";
    var dia = +m[3], mesN = +m[2], mes = T("date.month_full." + mesN, null, _MESES_ES[mesN - 1]);
    // Plantilla completa (no día + " de " + mes a mano): un idioma que ordena
    // las piezas distinto, o no lleva preposición, mueve la plantilla entera —
    // mismo patrón que site_schedule._friendly_date con date.long. La
    // sustitución de {day}/{month}/{year} la hacemos acá (no vía T(..., vars,
    // ...)) porque el resolver T() de este archivo, sin catálogo
    // (node --test, una story sin i18n), devuelve el fallback TAL
    // CUAL — sin reemplazar nada — y la fecha quedaría literalmente
    // "{day} de {month} de {year}".
    var tmpl = T("account.form.dob.echo_date", null, "{day} de {month} de {year}");
    return tmpl.split("{day}").join(String(dia)).split("{month}").join(mes).split("{year}").join(m[1]);
  }
  function ageFromIso(iso) {
    var m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/); if (!m) return null;
    var n = new Date(), a = n.getFullYear() - (+m[1]);
    var md = ((n.getMonth() + 1) - (+m[2])) || (n.getDate() - (+m[3]));
    if (md < 0) a -= 1;
    return a >= 0 && a < 130 ? a : null;
  }
  // Campo de nacimiento: una vez que hay una fecha válida, se muestra DIRECTO
  // en palabras ("28 de octubre de 1978") — sin una segunda línea
  // reinterpretándola cada vez que se recarga la pantalla (responsable del producto, 2026-07-17:
  // "once I enter it, there's no need to reinterpret it again"). El eco de
  // abajo queda sólo para el error de "no entendimos la fecha" mientras se
  // tipea. Foco = vuelve a modo edición (dígitos, fácil de tipear); blur con
  // fecha válida = formatea. commit() formatea sin esperar el blur — para un
  // valor puesto a mano (calendario, OCR del DNI).
  function wireDob(nacEl, echoEl) {
    if (!nacEl) return { commit: function () {} };
    function setError(msg) {
      if (!echoEl) return;
      echoEl.className = msg ? "sc-echo bad" : "sc-echo";
      echoEl.textContent = msg || "";
    }
    function onInput() {
      var v = nacEl.value.trim();
      if (!v) { delete nacEl.dataset.iso; setError(""); return; }
      var iso = parseFlexibleDate(v);
      if (iso) { nacEl.dataset.iso = iso; setError(""); }
      else { delete nacEl.dataset.iso; setError(T("account.form.dob.parse_error", null, "No entendimos la fecha. Probá DD/MM/AAAA.")); }
    }
    function commit() {
      var iso = nacEl.dataset.iso || parseFlexibleDate(nacEl.value);
      if (!iso) return;
      nacEl.dataset.iso = iso;
      nacEl.value = isoEnPalabras(iso);
      setError("");
    }
    function onFocus() {
      var iso = nacEl.dataset.iso;
      if (iso) nacEl.value = isoToArg(iso);
    }
    nacEl.addEventListener("input", onInput);
    nacEl.addEventListener("focus", onFocus);
    nacEl.addEventListener("blur", commit);
    onInput();
    commit(); // valor inicial (prefill ya guardado) directo en palabras, sin esperar el blur
    return { commit: commit };
  }

  // ---------- helpers ----------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function normDni(v) { return String(v == null ? "" : v).replace(/\D/g, ""); }
  // DNI con puntos de millar (20.456.781), como en el riel de identidad.
  function fmtDni(v) { var d = normDni(v); return d.replace(/\B(?=(\d{3})+(?!\d))/g, "."); }
  function normTel(v) {
    var s = String(v == null ? "" : v).trim();
    var plus = s.charAt(0) === "+";
    var d = s.replace(/\D/g, "");
    return d ? (plus ? "+" + d : d) : "";
  }

  // ---- Cloudflare Turnstile en el login (mismo sitekey que /admin) ----------
  // GET /api/account/config trae el sitekey (o null si no está configurado —
  // dev local, sin gate). Se pide una sola vez por carga de página.
  var _cfgPromise = null;
  function fetchConfig() {
    if (!_cfgPromise) {
      _cfgPromise = fetch("/api/account/config", { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; });
    }
    return _cfgPromise;
  }

  // «Ingresar» where patients come in through the municipal portal: our entry
  // (/account/portal-login) sends the person to the portal with THIS page as
  // the way back — the bare portal URL left them on the portal's home page
  // (the product owner, 26.10). The same href lives in nav.mjs.
  function portalEntryHref() {
    return "/account/portal-login?next=" + encodeURIComponent(location.pathname + location.search);
  }
  // /account goes to the portal by itself, once: when the person is already
  // signed in there the portal sends them straight back signed in. A second
  // arrival within two minutes without a portal identity means the way back
  // does not carry one — the page then stays, with its button, instead of
  // bouncing between the two sites.
  var PORTAL_TRIP_KEY = "hr_portal_trip";
  function portalTripAllowed() {
    try {
      var last = Number(sessionStorage.getItem(PORTAL_TRIP_KEY)) || 0;
      if (Date.now() - last < 120000) return false;
      sessionStorage.setItem(PORTAL_TRIP_KEY, String(Date.now()));
      return true;
    } catch (e) {
      return false;
    }
  }
  function injectStyles() {
    if (document.getElementById("sana-cuenta-styles")) return;
    var css =
      // .sc-authform = SÓLO las pantallas pre-cuenta (registro/login/recuperar/
      // reset): el cap de 480px y el h2 grande son de ESE contexto. .sc-auth
      // (sin cap) queda como wrapper neutro de renderEditar — antes ambos
      // compartían .sc-auth y el cap de 480px se filtraba a las tarjetas del
      // dashboard logueado (que tienen que llenar la columna de 786px), y el
      // descendant selector ".sc-auth h2" (specificity 0,1,1) le ganaba a
      // ".sc-card-h" (0,1,0) y le pisaba tipografía a los títulos de tarjeta.
      ".sc-authform{max-width:480px;margin-top:16px;}" +
      ".sc-authform h2{font-size:20px;font-weight:680;letter-spacing:-.01em;margin:0 0 4px;color:var(--text,#1f1f1f);}" +
      ".sc-authform .sc-sub{font-size:14px;color:var(--text-2,#5a625b);margin:0 0 18px;}" +
      ".sc-provider-intro{font-size:15px;line-height:1.5;color:var(--text-2,#5a625b);margin:0 0 15px;}" +
      ".sc-portal-p{max-width:420px;margin:0 0 16px;font-size:16px;line-height:1.5;color:var(--text,#1f1f1f);}" +
      ".sc-portal-p .sc-nowrap{white-space:nowrap;}" +
      ".sc-provider-list{display:flex;flex-direction:column;gap:10px;max-width:360px;}" +
      ".sc-provider{display:flex;align-items:center;justify-content:center;width:100%;min-height:48px;border:1.5px solid var(--border-strong,#d6dad6);border-radius:10px;background:var(--panel,#fff);color:var(--text,#1f1f1f);font:650 15px var(--font);padding:11px 16px;cursor:pointer;}" +
      ".sc-provider:hover{border-color:var(--hover-bd-strong);background:var(--hover-bg-soft);}" +
      ".sc-provider--primary{background:var(--accent,#0e8b50);border-color:var(--accent,#0e8b50);color:#fff;}" +
      ".sc-provider--primary:hover{background:var(--accent-hover,#0b6e33);border-color:var(--accent-hover,#0b6e33);color:#fff;}" +
      ".sc-provider-note{max-width:360px;margin:14px 0 0;padding:11px 13px;border:1px solid var(--accent-bd,#cde7d5);border-radius:9px;background:var(--accent-soft,#eaf4ec);color:var(--text-2,#5a625b);font-size:13px;line-height:1.45;}" +
      ".sc-provider-overlay{position:fixed;inset:0;z-index:10000;display:flex;flex-direction:column;background:var(--bg,#f6f7f5);}" +
      ".sc-provider-overlay-bar{height:48px;flex:0 0 48px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid var(--border-strong,#d6dad6);background:var(--panel,#fff);color:var(--text-2,#5a625b);font-size:13px;}" +
      ".sc-provider-close{border:0;background:none;color:var(--accent,#0e8b50);font:650 13px var(--font);padding:8px 0;cursor:pointer;}" +
      ".sc-provider-close:hover{color:var(--accent-hover);}" +
      ".sc-provider-frame{width:100%;min-height:0;flex:1 1 auto;border:0;background:var(--bg,#f6f7f5);}" +
      ".sc-row{display:grid;grid-template-columns:130px minmax(0,1fr);gap:6px 14px;align-items:baseline;margin-bottom:15px;}" +
      ".sc-row>label,.sc-row>.sc-label{text-align:right;font-size:13px;font-weight:500;color:var(--text-2,#5a625b);padding-top:9px;}" +
      ".sc-row .sc-field{display:flex;flex-direction:column;gap:5px;min-width:0;}" +
      /* Tarjetas del dashboard logueado (Datos/Cobertura/Contacto): grilla de
         2 columnas con el label ARRIBA del input (mockup Claude Design
         2026-07-16), no la fila label-izquierda de 130px de las pantallas de
         registro/login (esas siguen con .sc-row — ancho por contenido, ver
         skill form-design). */
      ".sc-card-body{display:flex;flex-direction:column;gap:18px;}" +
      ".sc-grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px 20px;}" +
      ".sc-field2{display:flex;flex-direction:column;gap:7px;min-width:0;}" +
      ".sc-field2>label,.sc-field2>.sc-label{font-size:13px;font-weight:500;color:var(--text-2,#5a625b);}" +
      ".sc-field2 .sc-in{width:100%;}" +
      ".sc-field2 .sc-dob-wrap .sc-in,.sc-combo-row .sc-in{width:auto;flex:1 1 auto;min-width:0;}" +
      "@media(max-width:480px){.sc-grid2{grid-template-columns:1fr;gap:14px;}}" +
      /* Combobox de Obra social/Prepaga (responsable del producto 2026-07-16): chevron a la derecha
         del input abre una lista de las coberturas más comunes; el texto libre
         sigue valiendo siempre (la lista sugiere, no restringe). */
      ".sc-combo{position:relative;max-width:420px;}" +
      ".sc-combo-row{display:flex;gap:8px;align-items:stretch;}" +
      ".sc-combo-btn{display:inline-flex;align-items:center;justify-content:center;width:46px;flex-shrink:0;border:1.5px solid var(--border-strong,#d6dad6);border-radius:10px;background:var(--panel,#fff);color:var(--text-2,#5a625b);cursor:pointer;}" +
      ".sc-combo-btn:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      ".sc-combo-list{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:30;background:var(--panel,#fff);border:1px solid var(--border-strong,#d6dad6);border-radius:12px;box-shadow:0 18px 44px -22px rgba(14,26,18,.4);max-height:264px;overflow-y:auto;padding:6px;}" +
      ".sc-combo-list[hidden]{display:none;}" +
      ".sc-combo-opt{display:block;width:100%;text-align:left;border:0;background:none;font:400 15px var(--font);color:var(--text,#1f1f1f);padding:11px 12px;border-radius:8px;cursor:pointer;min-height:44px;}" +
      ".sc-combo-opt:hover{background:var(--hover-bg);color:var(--hover-ink);}" +
      /* affordance real de input: fondo blanco + borde visible (responsable del producto 2026-07-08:
         "this doesn't look like an input") */
      ".sc-in{font-size:16px;padding:10px 12px;border:1.5px solid var(--border-strong,#d6dad6);border-radius:9px;background:var(--panel,#fff);color:var(--text,#1f1f1f);font-family:inherit;}" +
      ".sc-in:focus{outline:none;border-color:var(--focus-border,#0e8b50);box-shadow:0 0 0 3px var(--focus-ring,rgba(201,99,66,.14));}" +
      /* Placeholder = hint, no valor (responsable del producto 2026-08-05: "30.123.456" parecía un
         DNI ya cargado): el token más claro que sigue AA de cada tema
         (--text-4, 4,6:1 sobre las superficies) — el fallback hardcodeado va
         con ESE valor, nunca más claro que el token. */
      ".sc-in::placeholder{color:var(--text-4,#656e67);}" +
      /* 13ch, no 11: el valor formateado "30.123.456" son 10 caracteres y el ancho
         debe cubrir contenido + padding (24px) — con 11ch el último dígito quedaba
         cortado (responsable del producto 2026-07-19, screenshot recuperar contraseña). */
      ".sc-dni{width:13ch;font:700 23px/1.15 var(--font-mono);letter-spacing:.03em;}" +
      /* En las tarjetas de la cuenta (field2 estira .sc-in a 100%): el DNI vuelve
         a ancho por contenido — 8 dígitos no piden un campo full-width (responsable del producto
         2026-07-18, form-design r1). Editable, mismo mono que el registro. */
      ".sc-field2 .sc-dni{width:13ch;}" +
      ".sc-tel{width:18ch;}" +
      /* 24ch: cubre el valor en palabras que el campo muestra tras el blur
         ("30 de septiembre de 2026", el más largo posible) — 14ch alcanzaba
         para el eco en dígitos de antes de la r2b, no para el texto real que
         queda puesto en el input (responsable del producto, screenshot fix8 2026-09-15: "14 de
         mayo de" cortado). box-sizing:border-box para que el ancho declarado
         incluya el padding, y max-width:100% + min-width:0 para que en el
         teléfono ceda antes que desbordar la fila junto al botón calendario. */
      ".sc-dob{width:24ch;max-width:100%;min-width:0;box-sizing:border-box;font-variant-numeric:tabular-nums;}" +
      ".sc-dob-wrap{flex-wrap:wrap;}" +
      ".sc-dob-wrap .sc-in{flex:1 1 auto;min-width:0;}" +
      ".sc-wide{width:100%;max-width:300px;}" +
      /* sexo: control segmentado (radios grandes), no un select — las 3 opciones
         reparten el ancho por igual (responsable del producto 2026-07-11: la última quedaba angosta
         y "Otro" se comía todo el resto del ancho). */
      ".sc-seg{display:flex;width:100%;border:1.5px solid var(--border-strong,#d6dad6);border-radius:10px;overflow:hidden;background:var(--panel,#fff);}" +
      ".sc-seg label{position:relative;cursor:pointer;flex:1 1 0;min-width:0;}" +
      ".sc-seg input{position:absolute;opacity:0;pointer-events:none;}" +
      ".sc-seg span{display:flex;align-items:center;justify-content:center;padding:11px 8px;font-size:15px;color:var(--text-2,#5a625b);border-left:1.5px solid var(--border-strong,#d6dad6);text-align:center;}" +
      ".sc-seg label:first-child span{border-left:0;}" +
      ".sc-seg input:checked+span{background:var(--accent-soft,#eaf4ec);color:var(--accent-hover,#0b6e33);font-weight:500;}" +
      ".sc-seg input:focus-visible+span{box-shadow:inset 0 0 0 2px var(--focus-border,#0e8b50);}" +
      /* fecha de nacimiento: texto + calendario año-primero */
      ".sc-dob-wrap{display:flex;gap:7px;align-items:stretch;}" +
      ".sc-calbtn{display:inline-flex;align-items:center;justify-content:center;width:44px;border:1.5px solid var(--border-strong,#d6dad6);border-radius:9px;background:var(--panel,#fff);color:var(--text-2,#5a625b);cursor:pointer;}" +
      ".sc-calbtn:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      /* contraseña: ojo para mostrar/ocultar + generador */
      ".sc-pass-wrap{display:flex;gap:7px;align-items:stretch;max-width:300px;}" +
      ".sc-pass-wrap .sc-in{flex:1 1 auto;min-width:0;}" +
      ".sc-eyebtn{display:inline-flex;align-items:center;justify-content:center;width:44px;flex-shrink:0;border:1.5px solid var(--border-strong,#d6dad6);border-radius:9px;background:var(--panel,#fff);color:var(--text-2,#5a625b);cursor:pointer;}" +
      ".sc-eyebtn:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      ".sc-genbtn{align-self:flex-start;min-height:44px;margin-top:8px;border:1.5px solid var(--border-strong,#d6dad6);background:var(--panel,#fff);color:var(--text-2,#5a625b);font:600 13px var(--font);padding:8px 13px;border-radius:8px;cursor:pointer;}" +
      ".sc-genbtn:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      ".sc-cal{margin-top:8px;border:1.5px solid var(--border-strong,#d6dad6);border-radius:12px;background:var(--panel,#fff);padding:12px;max-width:320px;box-shadow:0 14px 34px -18px rgba(40,30,20,.3);}" +
      ".sc-cal-head{display:flex;gap:6px;align-items:center;font-size:13.5px;color:var(--text-2,#5a625b);margin:0 2px 10px;}" +
      ".sc-cal-head a{color:var(--accent,#0e8b50);cursor:pointer;font-weight:600;text-decoration:none;}" +
      ".sc-cal-head a:hover{color:var(--accent-hover);}" +
      ".sc-cal-grid{display:grid;gap:5px;}" +
      ".sc-cal-grid.y{grid-template-columns:repeat(4,1fr);max-height:264px;overflow-y:auto;padding-right:2px;}" +
      ".sc-cal-grid.m{grid-template-columns:repeat(3,1fr);}" +
      ".sc-cal-grid.d{grid-template-columns:repeat(7,1fr);}" +
      ".sc-cal-grid button{border:1px solid var(--border,#eaeaea);background:var(--bg,#f6f7f5);border-radius:8px;" +
        "font:500 15px var(--font);color:var(--text,#1f1f1f);padding:10px 0;min-height:44px;cursor:pointer;}" +
      ".sc-cal-grid.d button{padding:0;min-height:40px;font-size:14px;}" +
      ".sc-cal-grid button:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);background:var(--hover-bg);}" +
      ".sc-echo{font-size:13px;color:var(--text-3,#7c847d);min-height:1em;}" +
      /* obra social encontrada en el padrón (PUCO): valor como texto, no input */
      ".sc-os-box{display:flex;flex-direction:column;gap:5px;min-width:0;}" +
      ".sc-os-val{display:block;padding:9px 0 0;font-size:16px;font-weight:650;color:var(--text,#1f1f1f);}" +
      /* credencial: nº de afiliado/socio — los de Swiss son largos
         ("800006 3055008 01 0001", 22 caracteres con espacios) y alguno puede
         venir peor. Ancho por contenido con aire de sobra (28ch), no full-width.
         El responsable del producto 2026-07-18: el número tiene que entrar entero y sobrar un poco. */
      ".sc-af{width:28ch;max-width:100%;}" +
      ".sc-plan{width:12ch;}" +
      /* combobox obra social / prepaga: lista sugerida, texto libre permitido */
      ".sc-combo{position:relative;max-width:352px;}" +
      ".sc-combo-wrap{display:flex;gap:7px;align-items:stretch;}" +
      ".sc-combo-wrap .sc-in{flex:1 1 auto;min-width:0;}" +
      ".sc-combo-list{position:absolute;top:calc(100% + 6px);left:0;right:0;z-index:30;" +
        "background:var(--panel,#fff);border:1.5px solid var(--border-strong,#d6dad6);border-radius:12px;" +
        "box-shadow:0 14px 34px -18px rgba(40,30,20,.3);max-height:264px;overflow-y:auto;padding:6px;}" +
      ".sc-combo-list button{display:block;width:100%;text-align:left;border:0;background:none;" +
        "font:500 15px var(--font);color:var(--text,#1f1f1f);padding:11px 12px;" +
        "border-radius:8px;cursor:pointer;min-height:44px;}" +
      ".sc-combo-list button:hover{background:var(--hover-bg);color:var(--hover-ink);}" +
      ".sc-combo-list button.on{background:var(--accent-soft,#eaf4ec);color:var(--accent-hover,#0b6e33);}" +
      /* Secondary action aligned with the shared coverage picker. */
      ".sc-os-add{font:inherit;font-size:16px;min-height:44px;padding:0;border:0;background:none;text-align:left;color:var(--accent,#0e8b50);cursor:pointer;}" +
      ".sc-os-add:hover{color:var(--accent-hover);}" +
      ".sc-os-item .sc-combo-list button{font-size:16px;overflow-wrap:anywhere;}" +
      ".sc-os-item .sc-combo-list button:disabled{color:var(--text-3,#7c847d);cursor:default;}" +
      ".sc-os-del{display:inline-flex;align-items:center;justify-content:center;width:44px;min-height:44px;flex-shrink:0;border:0;border-radius:9px;background:none;color:var(--text-2,#5a625b);cursor:pointer;}" +
      ".sc-os-del[hidden],.sc-combo-btn[hidden]{display:none;}" +
      ".sc-os-del:hover{color:var(--hover-ink);}" +
      ".sc-echo.ok{color:var(--done,#0e8b50);}" +
      ".sc-echo.bad{color:var(--danger,#a3352f);}" +
      ".sc-hint{display:block;font-size:13px;color:var(--text-3,#7c847d);margin:-6px 0 14px;}" +
      /* One content-width code field accepts typing and paste; it is safer
         and easier to use than six tiny focus-trapping inputs. */
      ".sc-otp-code{width:10ch;max-width:100%;font:700 24px/1.15 var(--font-mono);letter-spacing:.18em;text-align:center;font-variant-numeric:tabular-nums;}" +
      ".sc-otp-copy{font-size:15px;line-height:1.5;color:var(--text-2,#5a625b);margin:0 0 18px;}" +
      ".sc-otp-resend{display:inline-flex;align-items:center;min-height:44px;margin-top:10px;padding:8px 0;border:0;background:none;color:var(--accent,#0e8b50);font:600 13.5px var(--font);cursor:pointer;}" +
      ".sc-otp-resend:hover{color:var(--accent-hover,#0b6e33);}.sc-otp-resend:disabled{color:var(--text-3,#7c847d);cursor:default;}" +
      /* Escaneo del DNI por foto (frente + dorso) — acelerador opcional del
         registro; lee nombre/DNI/nacimiento del frente y domicilio del dorso. */
      ".sc-scan{margin:0 0 22px;padding:16px 17px 12px;border:1.5px solid var(--border,#eaeaea);border-radius:13px;background:var(--panel-2,#faf9f6);}" +
      ".sc-scan-title{font:600 15.5px var(--font);color:var(--text,#1f1f1f);margin:0 0 4px;}" +
      ".sc-scan-sub{font-size:13px;color:var(--text-2,#5a625b);margin:0 0 13px;line-height:1.45;}" +
      ".sc-scan-btns{display:flex;flex-wrap:wrap;gap:9px;}" +
      ".sc-scan-btn{display:inline-flex;align-items:center;gap:7px;border:1.5px solid var(--border-strong,#d6dad6);background:var(--panel,#fff);color:var(--text,#1f1f1f);font:600 14px var(--font);padding:11px 15px;border-radius:10px;cursor:pointer;}" +
      ".sc-scan-btn:hover{border-color:var(--hover-bd-strong);background:var(--hover-bg-soft);}" +
      ".sc-scan-btn.done{border-color:var(--done,#0e8b50);color:var(--done,#0e8b50);background:var(--accent-soft,#eaf4ec);}" +
      // Icons8 sf-regular camera as a mask: it takes the button's ink, so a
      // read side turns green with its label (no Unicode pictograph).
      ".sc-scan-ico{width:20px;height:20px;flex:none;background:currentColor;" +
      "-webkit-mask:url(/static/icons/camera-sf-regular.png) center/contain no-repeat;" +
      "mask:url(/static/icons/camera-sf-regular.png) center/contain no-repeat;}" +
      ".sc-scan-status{font-size:13px;margin-top:11px;color:var(--text-2,#5a625b);line-height:1.45;}" +
      ".sc-scan-status:empty{display:none;}" +
      ".sc-scan-status.err,.sc-scan-status .fail{color:var(--danger,#a3352f);}.sc-scan-status.ok,.sc-scan-status .ok{color:var(--done,#0e8b50);}" +
      ".sc-foto-prev{display:flex;align-items:center;gap:10px;margin-top:11px;font-size:13px;color:var(--text-2,#5a625b);}" +
      ".sc-foto-prev[hidden]{display:none;}" +
      ".sc-foto-prev img{width:44px;height:44px;border-radius:50%;object-fit:cover;border:1px solid var(--border-strong,#d6dad6);}" +
      ".sc-foto-prev a{color:var(--accent,#0e8b50);cursor:pointer;}.sc-foto-prev a:hover{color:var(--accent-hover);}" +
      ".sc-cp{width:8ch;}" +
      /* Tarjetas de la cuenta (Datos/Cobertura/Contacto) — mismo look que las
         .dcard del panel, self-contained acá para que renderEditar traiga sus
         propias tarjetas sin acoplarse a la página. */
      ".sc-card{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:26px 28px;box-shadow:0 8px 30px -22px rgba(20,24,20,.5);margin-bottom:20px;}" +
      ".sc-card:last-child{margin-bottom:0;}" +
      /* Datos personales / Cobertura / Contacto viven cada una en su propio
         ítem del riel de Mi cuenta (responsable del producto, 2026-07-17) — un solo <form> las
         sigue envolviendo (autosave e IDs compartidos), pero sólo la tarjeta
         de la sub-pestaña activa se muestra; accounts.py alterna la clase "on"
         por data-panel al cambiar de ítem del riel. */
      ".sc-card[data-panel]{display:none;margin-bottom:0;}" +
      ".sc-card[data-panel].on{display:block;}" +
      /* Default = sin subtítulo abajo (mockup: 20px de aire, p.ej. "Datos
         personales"); .sc-card-h--sub achica a 4px cuando SÍ hay un <p
         class="sc-card-p"> justo debajo (p.ej. "Cobertura"). */
      ".sc-card-h{font-size:19px;font-weight:700;color:var(--text);letter-spacing:-.01em;margin:0 0 20px;}" +
      ".sc-card-h--sub{margin-bottom:4px;}" +
      ".sc-card-p{font-size:15px;color:var(--text-2);margin:0 0 18px;}" +
      ".sc-postcoverage{margin-top:24px;min-width:0;}" +
      // One field and one button (the product owner 26.10, 28.10): rows stack at every width, the
      // arrow that opens the list is gone (typing already suggests), and the first
      // label is read by screen readers only — the card title and the hint say it.
      ".sc-postcoverage .sc-row{grid-template-columns:minmax(0,1fr);gap:6px;}" +
      ".sc-postcoverage .sc-row>label{font-size:16px;text-align:left;padding-top:0;}" +
      ".sc-postcoverage .sc-row:not(.sc-cob-det)>label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;}" +
      ".sc-postcoverage .sc-combo-btn{display:none;}" +
      ".sc-postcoverage .sc-combo{width:100%;max-width:none;}" +
      ".sc-postcoverage .sc-combo-wrap .sc-in{flex:1;min-width:0;max-width:none;}" +
      ".sc-postcoverage p{font-size:16px;line-height:1.5;overflow-wrap:anywhere;}" +
      ".sc-postcoverage [data-coverage-status]:empty{display:none;}" +
      ".sc-postcoverage-actions{margin-top:16px;}" +
      ".sc-postcoverage-actions .sc-cta{font-size:16px;font-weight:600;min-height:48px;}" +
      "@media(max-width:479px){.sc-postcoverage{padding:20px 16px;}}" +
      ".sc-actions{display:flex;align-items:center;gap:16px;margin-top:6px;}" +
      ".sc-actions .sc-cta{width:auto;margin-top:0;}.sc-actions .sc-save{margin-top:0;}" +
      ".sc-static{display:inline-block;padding:9px 0;font:700 20px/1.15 var(--font-mono);letter-spacing:.02em;color:var(--text,#1f1f1f);}" +
      ".sc-save{font-size:13px;min-height:1em;margin-top:2px;}" +
      ".sc-save.ok{color:var(--done,#0e8b50);}.sc-save.err{color:var(--danger,#a3352f);}" +
      ".sc-cta{width:100%;display:inline-flex;align-items:center;justify-content:center;border:0;cursor:pointer;" +
        "background:var(--accent,#0e8b50);color:#fff;font:500 15px var(--font);letter-spacing:-.01em;padding:13px 20px;border-radius:11px;transition:filter .12s,transform .1s;margin-top:4px;}" +
      ".sc-cta:hover{background-image:var(--hover-fill);}.sc-cta:active{transform:translateY(1px);}" +
      ".sc-msg{margin-top:12px;font-size:16px;line-height:1.5;display:none;}" +
      ".sc-msg.show{display:block;}.sc-msg.err{color:var(--danger,#a3352f);}.sc-msg.ok{color:var(--text-2,#5a625b);}" +
      /* El cambio de modo vive arriba del formulario. El bloque de foto ya
         tiene borde propio: no se agrega un divisor inmediatamente encima. */
      ".sc-toggle{margin-bottom:16px;font-size:14px;color:var(--text-2,#5a625b);}" +
      ".sc-toggle a{color:var(--accent,#0e8b50);text-decoration:none;font-weight:600;cursor:pointer;}" +
      ".sc-toggle a:hover{color:var(--accent-hover);}" +
      ".sc-toggle+.sc-toggle{margin-top:-8px;}" +
      /* alinear con el inicio del campo (no con el label): mismo offset que la
         columna de label del sc-row (130px + 14px de gap) — el responsable del producto 2026-07-11. */
      ".sc-indent{margin-left:144px;}" +
      ".sc-forgot{font-size:13px;color:var(--accent,#0e8b50);text-decoration:none;font-weight:600;cursor:pointer;}" +
      ".sc-forgot:hover{color:var(--accent-hover);}" +
      /* Cobertura: credencial anidada bajo el campo (Nº de afiliado / socio+plan),
         pozo mint con borde-izquierdo — igual que el mockup. Se muestra sólo
         cuando el campo de arriba tiene un valor. */
      ".sc-cred{max-width:440px;margin:10px 0 0 16px;border-left:2px solid var(--accent-bd);background:var(--accent-soft);border-radius:0 12px 12px 0;padding:13px 16px;}" +
      ".sc-cred[hidden]{display:none;}" +
      ".sc-cred-h{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--text-3);font-weight:500;margin-bottom:12px;}" +
      ".sc-cred-lbl{display:block;font-size:13px;font-weight:500;color:var(--text-2);margin-bottom:6px;}" +
      ".sc-cred-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:14px;}" +
      ".sc-cred input{font-family:inherit;font-size:16px;padding:9px 12px;border:1.5px solid var(--border-strong);border-radius:9px;background:var(--panel);color:var(--text);width:100%;font-variant-numeric:tabular-nums;}" +
      ".sc-cred input:focus{outline:none;border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}" +
      /* Cómo te avisamos: filas icono + nombre + toggle */
      ".sc-avisos{margin-top:22px;padding-top:20px;border-top:1px solid var(--border-soft);}" +
      ".sc-av-h{font-size:14px;font-weight:700;color:var(--text);margin-bottom:3px;}" +
      ".sc-av-p{font-size:14px;color:var(--text-2);margin:0 0 10px;}" +
      ".sc-av-list{max-width:520px;}" +
      ".sc-av{display:flex;align-items:center;gap:14px;padding:12px 2px;}" +
      ".sc-av+.sc-av{border-top:1px solid var(--border-soft);}" +
      ".sc-av-ic{width:40px;height:40px;border-radius:11px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
      ".sc-av-tx{flex:1;min-width:0;}" +
      ".sc-av-name{font-size:15px;font-weight:500;color:var(--text);}" +
      ".sc-av-sub{font-size:13px;color:var(--text-3);}" +
      ".sc-av-tag{font-size:13px;font-weight:600;color:var(--accent-hover);background:var(--accent-soft);border-radius:5px;padding:1px 7px;margin-left:6px;letter-spacing:.02em;}" +
      ".sc-sw{position:relative;width:44px;height:26px;border:0;border-radius:999px;background:var(--border-strong);cursor:pointer;flex-shrink:0;padding:0;transition:background .15s;}" +
      ".sc-sw.on{background:var(--accent);}" +
      ".sc-sw i{position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:left .15s;}" +
      ".sc-sw.on i{left:21px;}" +
      ".sc-ico{display:inline-block;flex:none;vertical-align:middle;background-color:currentColor;-webkit-mask-position:center;mask-position:center;-webkit-mask-size:contain;mask-size:contain;-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;}" +
      /* Account cards (review 2026-11-15, item 16): every label above its field,
         at a readable size; DNI and birth date in the body font without spacing;
         the postal code as wide as its four digits. */
      ".sc-card .sc-row{grid-template-columns:minmax(0,1fr);gap:7px;}" +
      ".sc-card .sc-row>label,.sc-card .sc-row>.sc-label,.sc-card .sc-field2>label,.sc-card .sc-field2>.sc-label{text-align:left;padding-top:0;font-size:15px;font-weight:500;color:var(--text);}" +
      ".sc-card .sc-row>.sc-label:empty{display:none;}" +
      ".sc-card .sc-dni,.sc-card .sc-static{font:600 18px/1.3 var(--font);letter-spacing:0;font-variant-numeric:normal;}" +
      ".sc-card .sc-dob{font-variant-numeric:normal;letter-spacing:0;}" +
      ".sc-field2 .sc-cp{width:8ch;}" +
      ".sc-card .sc-save,.sc-card .sc-echo,.sc-card .sc-card-p,.sc-card .sc-av-sub,.sc-card .sc-cred-lbl{font-size:15px;}" +
      ".sc-card .sc-av-h{font-size:16px;}.sc-card .sc-av-p{font-size:15px;}" +
      ".sc-card .sc-cred-h{font-size:15px;letter-spacing:0;text-transform:none;color:var(--text-2);font-weight:600;}" +
      "@media(max-width:480px){.sc-row{grid-template-columns:1fr;gap:6px;}.sc-row>label,.sc-row>.sc-label{text-align:left;padding-top:0;}.sc-indent{margin-left:0;}.sc-cred{margin-left:0;}.sc-cred-grid{grid-template-columns:1fr;}.sc-otp-code{font-size:22px;}}";
    var st = document.createElement("style");
    st.id = "sana-cuenta-styles";
    st.textContent = css;
    document.head.appendChild(st);
  }

  // Icons8 SF Regular files in static/icons, painted by mask with the
  // text colour (review 2026-11-15: nothing drawn by hand in the account).
  function scIco(file, px, color) {
    return '<span class="sc-ico" aria-hidden="true" style="width:' + px + 'px;height:' + px + 'px;' +
      (color ? 'background-color:' + color + ';' : '') +
      '-webkit-mask-image:url(/static/icons/' + file + ');mask-image:url(/static/icons/' + file + ');"></span>';
  }
  var CAL_ICON = scIco("calendar-sf-regular.svg", 18);
  var EYE_ICON = scIco("visible-sf-regular.svg", 18);
  var EYE_OFF_ICON = scIco("invisible-sf-regular.svg", 18);

  // ---- contraseña: generador aleatorio ----------------------------------
  // sin caracteres ambiguos (0/O, 1/l/I) — se lee y se copia a mano.
  var PASS_CHARS = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
  function generatePassword(len) {
    len = len || 12;
    var out = "";
    var bytes = new Uint32Array(len);
    if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (var j = 0; j < len; j++) bytes[j] = Math.floor(Math.random() * 4294967296);
    for (var i = 0; i < len; i++) out += PASS_CHARS[bytes[i] % PASS_CHARS.length];
    return out;
  }

  // ---- foto del DNI: helpers de imagen y texto --------------------------
  // La foto que trae un paste (Cmd/Ctrl+V) o un drop sobre un área de foto.
  // Mismas reglas que el control de foto del médico (photoFileFromEvent en
  // doctor-workspace.mjs): un evento ya consumido o dirigido a un campo de
  // texto nunca se intercepta (el paste del formulario sigue su curso), y
  // vale la primera imagen por tipo o por extensión. null = no hay foto.
  var TEXT_TARGET = 'input,textarea,select,[contenteditable="true"],[contenteditable=""]';
  function pasteTargetsText(ev) {
    var t = ev && ev.target;
    if (!t) return false;
    if (typeof t.closest === "function") return !!t.closest(TEXT_TARGET);
    return /^(INPUT|TEXTAREA|SELECT)$/i.test(t.tagName || "") || t.isContentEditable === true;
  }
  function photoFileFromEvent(ev) {
    if (!ev || ev.defaultPrevented || pasteTargetsText(ev)) return null;
    var transfer = ev.clipboardData || ev.dataTransfer;
    if (!transfer) return null;
    var files = Array.prototype.slice.call(transfer.files || []);
    Array.prototype.forEach.call(transfer.items || [], function (item) {
      if (item && item.kind === "file" && item.getAsFile) {
        var f = item.getAsFile();
        if (f && files.indexOf(f) < 0) files.push(f);
      }
    });
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      if (/^image\//.test(f.type || "") || /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name || "")) return f;
    }
    return null;
  }
  // Un drop con archivos: hay que cancelarlo aunque no sea una foto, para
  // que el navegador no abra el archivo y se pierda lo cargado.
  function eventCarriesFiles(ev) {
    var t = ev && ev.dataTransfer;
    if (!t) return false;
    if (t.files && t.files.length) return true;
    if (Array.prototype.some.call(t.items || [], function (item) { return item && item.kind === "file"; })) return true;
    return Array.prototype.indexOf.call(t.types || [], "Files") >= 0;
  }
  // HEIC (el formato de cámara del iPhone) por magic bytes, no por la
  // extensión: los bytes 4..8 dicen "ftyp" y la marca que sigue es de la
  // familia HEIC/HEIF (las mismas que core/photo_guard._HEIC_BRANDS).
  // Chrome/Firefox NO decodifican HEIC en canvas: sin este chequeo la
  // persona ve un fallo sin remedio.
  var HEIC_BRANDS = ["heic", "heix", "hevc", "hevx", "heim", "heis", "hevm", "hevs", "heif", "mif1", "msf1"];
  function isHeicFile(file) {
    return new Promise(function (resolve) {
      try {
        var blob = file && file.slice ? file.slice(0, 12) : null;
        if (!blob) { resolve(false); return; }
        var fr = new FileReader();
        fr.onload = function () {
          var b = new Uint8Array(fr.result || new ArrayBuffer(0));
          var tag = String.fromCharCode.apply(null, Array.prototype.slice.call(b.slice(4, 8)));
          var brand = String.fromCharCode.apply(null, Array.prototype.slice.call(b.slice(8, 12))).toLowerCase();
          resolve(tag === "ftyp" && HEIC_BRANDS.indexOf(brand) >= 0);
        };
        fr.onerror = function () { resolve(false); };
        fr.readAsArrayBuffer(blob);
      } catch (e) { resolve(false); }
    });
  }
  // Los bytes crudos del archivo como data URL (sin canvas): el camino de
  // escape cuando el navegador no puede decodificar el formato — el servidor
  // decide (core/photo_guard) y, si no puede, contesta con el remedio.
  function fileAsDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var fr = new FileReader();
      fr.onload = function () { resolve(String(fr.result || "")); };
      fr.onerror = function () { reject(new Error("no se pudo leer el archivo")); };
      fr.readAsDataURL(file);
    });
  }
  // Bajar una foto de celular gigante antes de mandarla al OCR: acota el
  // payload/costo manteniendo el texto legible (lado largo <= 2000px).
  var OCR_MAX_DIM = 2000;
  function fileToDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      var url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        var scale = Math.min(1, OCR_MAX_DIM / Math.max(img.width, img.height));
        if (scale >= 1) {
          var fr = new FileReader();
          fr.onload = function () { resolve(fr.result); };
          fr.onerror = reject;
          fr.readAsDataURL(file);
          return;
        }
        var c = document.createElement("canvas");
        c.width = Math.round(img.width * scale);
        c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", 0.9));
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("imagen inválida")); };
      img.src = url;
    });
  }
  // Re-encoda cualquier imagen a JPEG achicado (foto de perfil): siempre pasa
  // por canvas, así un HEIC/PNG gigante del celular sale como JPEG liviano.
  function fileToJpeg(file, maxDim) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      var url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        var scale = Math.min(1, maxDim / Math.max(img.width, img.height));
        var c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(img.width * scale));
        c.height = Math.max(1, Math.round(img.height * scale));
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", 0.88));
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("imagen inválida")); };
      img.src = url;
    });
  }

  // Recorta el retrato del DNI para la foto de perfil. El bbox (fracciones
  // 0..1 de la imagen) lo devuelve /api/ocr-dni; el recorte es 100% local
  // (canvas) — la foto del documento nunca se re-sube para esto.
  function cropFaceFromDataUrl(dataUrl, bbox, maxDim) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        var padX = bbox.w * 0.22, padY = bbox.h * 0.22;
        var x0 = Math.max(0, bbox.x - padX), y0 = Math.max(0, bbox.y - padY);
        var x1 = Math.min(1, bbox.x + bbox.w + padX), y1 = Math.min(1, bbox.y + bbox.h + padY);
        var x = x0 * img.width, y = y0 * img.height;
        var w = (x1 - x0) * img.width, h = (y1 - y0) * img.height;
        if (w < 40 || h < 40) { reject(new Error("recorte muy chico")); return; }
        var scale = Math.min(1, (maxDim || 480) / Math.max(w, h));
        var c = document.createElement("canvas");
        c.width = Math.round(w * scale);
        c.height = Math.round(h * scale);
        c.getContext("2d").drawImage(img, x, y, w, h, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", 0.88));
      };
      img.onerror = function () { reject(new Error("imagen inválida")); };
      img.src = dataUrl;
    });
  }

  // Nombres del DNI vienen en MAYÚSCULAS (barcode y OCR): a Capital Inicial
  // para que se lean como el resto del form.
  function titleCase(s) {
    return String(s || "").toLowerCase().replace(/(^|[\s'-])([a-záéíóúüñ])/g,
      function (m, sep, c) { return sep + c.toUpperCase(); });
  }
  // Domicilio: calle + número + piso en una línea (lo que la gente escribe).
  function domLine(d) {
    d = d || {};
    return [d.calle, d.numero, d.piso].filter(Boolean).join(" ");
  }

  // Ojo (mostrar/ocultar) + generador — compartido entre renderAuth y
  // renderReset, ambos usan el mismo markup #sc-pass/#sc-eyebtn/#sc-genbtn.
  function wirePassword(host) {
    var passEl = host.querySelector("#sc-pass");
    var eyeBtn = host.querySelector("#sc-eyebtn");
    var genBtn = host.querySelector("#sc-genbtn");
    if (eyeBtn && passEl) eyeBtn.addEventListener("click", function () {
      var shown = passEl.type === "text";
      passEl.type = shown ? "password" : "text";
      eyeBtn.innerHTML = shown ? EYE_ICON : EYE_OFF_ICON;
      eyeBtn.setAttribute("aria-label", shown
        ? T("account.form.password.show", null, "Mostrar contraseña")
        : T("account.form.password.hide", null, "Ocultar contraseña"));
    });
    if (genBtn && passEl) genBtn.addEventListener("click", function () {
      passEl.value = generatePassword();
      passEl.type = "text";
      if (eyeBtn) { eyeBtn.innerHTML = EYE_OFF_ICON; eyeBtn.setAttribute("aria-label", T("account.form.password.hide", null, "Ocultar contraseña")); }
      passEl.focus();
    });
    return passEl;
  }

  function field(label, inner, opts) {
    opts = opts || {};
    var labelHtml = opts.forId
      ? '<label for="' + esc(opts.forId) + '">' + label + '</label>'
      : opts.labelId
        ? '<span class="sc-label" id="' + esc(opts.labelId) + '">' + label + '</span>'
        : '<span class="sc-label">' + label + '</span>';
    return '<div class="sc-row">' + labelHtml + '<div class="sc-field">' + inner + "</div></div>";
  }
  // ---- tarjetas del dashboard logueado: label ARRIBA del input, 2 por fila
  // (mockup Claude Design 2026-07-16) — ver .sc-field2/.sc-grid2 en injectStyles.
  function field2(label, inner, opts) {
    opts = opts || {};
    var labelHtml = opts.forId
      ? '<label for="' + esc(opts.forId) + '">' + label + '</label>'
      : opts.labelId
        ? '<span class="sc-label" id="' + esc(opts.labelId) + '">' + label + '</span>'
        : '<span class="sc-label">' + label + '</span>';
    return '<div class="sc-field2">' + labelHtml + inner + "</div>";
  }
  // título de tarjeta + subtítulo opcional: sin subtítulo el título deja 20px
  // de aire abajo; con subtítulo, 4px (el propio <p> ya trae el aire).
  function cardHead(title, sub) {
    return '<h2 class="sc-card-h' + (sub ? ' sc-card-h--sub' : '') + '">' + title + "</h2>" +
      (sub ? '<p class="sc-card-p">' + sub + "</p>" : "");
  }

  // ---- cobertura: listas cortas de las más aceptadas en la zona ----
  // Fuente: frecuencia en la cartilla privada de Pinamar (salud-pinamar/
  // cartilla-2026-05-14.json, 442 filas — OSDE 129, IOMA 90, Federada 63,
  // Medifé/Galeno 56...). PAMI va arriba a mano: la cartilla privada lo
  // subrepresenta (16) pero es EL público del hospital municipal, junto con
  // IOMA. One combined suggestion list; unlisted values are added explicitly.
  var OBRAS_SOCIALES = ["IOMA", "PAMI", "OSECAC (Empleados de Comercio)",
    "Banco Provincia", "OSUTHGRA (Gastronómicos)", "OSPE (Petroleros)",
    "OSPRERA (Trabajadores Rurales)", "COMEI", "OSPEDYC",
    "Luz y Fuerza (OSFATLyF)", "AMFFA", "Poder Judicial",
    "Colegio de Abogados (CASA)", "Unión Personal"];
  var PREPAGAS = ["OSDE", "Federada Salud", "Medifé", "Galeno", "Sancor Salud",
    "Swiss Medical", "Medicus", "Prevención Salud", "Jerárquicos Salud",
    "OMINT", "Avalian", "Luis Pasteur"];
  function osPucoNota(n) {
    return n > 1
      ? T("account.form.coverage.registry_note_other", null, "Figuran a tu nombre en el padrón nacional.")
      : T("account.form.coverage.registry_note_one", null, "Figura a tu nombre en el padrón nacional.");
  }

  // Localidades del Partido de Pinamar (+ vecinas que atiende el hospital) con
  // su código postal. La combobox sugiere estas; elegir una autocompleta el CP.
  // Texto libre igual vale (form-design: la lista sugiere, no restringe).
  var LOCALIDADES = [
    { n: "Pinamar", cp: "7167" }, { n: "Cariló", cp: "7167" },
    { n: "Ostende", cp: "7167" }, { n: "Valeria del Mar", cp: "7167" },
    { n: "Montecarlo", cp: "7167" }, { n: "General Madariaga", cp: "7163" },
    { n: "Villa Gesell", cp: "7165" }, { n: "Mar de las Pampas", cp: "7165" }
  ];
  var LOCALIDAD_NOMBRES = LOCALIDADES.map(function (l) { return l.n; });
  function _sinAcento(s) { return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").trim(); }
  // CP de una localidad tipeada/elegida: match exacto o "contiene" (el dorso del
  // DNI a veces trae "CARILÓ - PINAMAR" en una línea).
  function cpDeLocalidad(v) {
    var q = _sinAcento(v);
    if (!q) return "";
    for (var i = 0; i < LOCALIDADES.length; i++) if (_sinAcento(LOCALIDADES[i].n) === q) return LOCALIDADES[i].cp;
    for (var j = 0; j < LOCALIDADES.length; j++) if (q.indexOf(_sinAcento(LOCALIDADES[j].n)) !== -1) return LOCALIDADES[j].cp;
    return "";
  }
  // combobox de localidad + autocompletar el CP. Elegir de la lista fuerza el
  // CP; tipear una localidad conocida lo completa sólo si está vacío. onSave
  // (edición con autosave) guarda al aplicar el CP.
  function wireLocalidad(host, onSave) {
    var loc = host.querySelector("#sc-dom-loc");
    if (!loc) return;
    var setCp = function (force) {
      var cp = cpDeLocalidad(loc.value);
      var cpEl = host.querySelector("#sc-dom-cp");
      if (!cp || !cpEl) return;
      if (force || !cpEl.value.trim()) { cpEl.value = cp; if (onSave) onSave(); }
    };
    wireCombo(host, "sc-dom-loc", LOCALIDAD_NOMBRES, function () { setCp(true); });
    loc.addEventListener("blur", function () { setCp(false); });
  }

  // ---- combobox: input + flecha que abre la lista, filtra al tipear --------
  // (form-design: acepta cualquier texto — la lista sugiere, no restringe)
  var CHEV_ICON = scIco("expand-arrow-sf-regular.svg", 16);

  function comboHtml(id, placeholder, valor, ariaLabel, trailingControl) {
    return '<div class="sc-combo" id="' + id + '-combo">' +
      '<div class="sc-combo-wrap">' +
      '<input class="sc-in sc-wide" id="' + id + '" placeholder="' + placeholder + '" value="' + esc(valor || "") + '" autocomplete="off"' + (ariaLabel ? ' aria-label="' + esc(ariaLabel) + '"' : "") + '>' +
      // The x right after its own field, before the list arrow (review 2026-11-15).
      (trailingControl || "") +
      '<button type="button" class="sc-calbtn sc-combo-btn" id="' + id + '-btn" tabindex="-1" aria-label="' + T("account.form.combo.choose_from_list", null, "Elegir de la lista") + '">' + CHEV_ICON + "</button>" +
      "</div>" +
      '<div class="sc-combo-list" id="' + id + '-list" hidden></div>' +
      "</div>";
  }

  function wireCombo(host, id, opciones, onPick, config) {
    config = config || {};
    var input = host.querySelector("#" + id);
    var btn = host.querySelector("#" + id + "-btn");
    var list = host.querySelector("#" + id + "-list");
    if (!input || !list || input._combo) return;
    input._combo = true;
    var idx = -1;
    var visibles = [];
    var blurTimer = null;
    function on(el, type, fn) { el.addEventListener(type, fn, config.signal ? { signal: config.signal } : undefined); }
    if (config.signal) config.signal.addEventListener("abort", function () { clearTimeout(blurTimer); }, { once: true });
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-controls", id + "-list");
    input.setAttribute("aria-expanded", "false");
    list.setAttribute("role", "listbox");

    function norm(s) { return String(s).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, ""); }
    function label(o) { return typeof o === "string" ? o : (o.label || o.nombre); }
    function filtradas(todas) {
      if (typeof opciones === "function") return opciones(input.value, todas);
      var q = norm(input.value.trim());
      if (todas || !q) return opciones;
      return opciones.filter(function (o) { return norm(o).indexOf(q) !== -1; });
    }
    function cerrar() {
      idx = -1; list.hidden = true; list.innerHTML = "";
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }
    function marcar() {
      list.querySelectorAll("button:not(:disabled)").forEach(function (b, i) {
        b.className = i === idx ? "on" : "";
        b.setAttribute("aria-selected", i === idx ? "true" : "false");
        if (i === idx) input.setAttribute("aria-activedescendant", b.id);
      });
    }
    var eligiendo = false; // el dispatch de "input" de elegir() no debe reabrir la lista
    function elegir(v) {
      input.value = typeof v === "string" ? v : v.nombre;
      cerrar();
      eligiendo = true;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      eligiendo = false;
      if (onPick) onPick(v);
    }
    function abrir(todas) {
      clearTimeout(blurTimer);
      var ops = filtradas(todas);
      if (!ops.length) { cerrar(); return; }
      visibles = ops;
      idx = -1;
      input.removeAttribute("aria-activedescendant");
      list.innerHTML = ops.map(function (o, i) {
        return '<button type="button" tabindex="-1" role="option" aria-selected="false" id="' + id + '-opt-' + i + '" data-option="' + i + '"' + (o.disabled ? ' disabled aria-disabled="true"' : "") + '>' + esc(label(o)) + "</button>";
      }).join("");
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      list.querySelectorAll("button").forEach(function (b) {
        // mousedown (no click): gana antes que el blur del input
        on(b, "mousedown", function (e) {
          e.preventDefault();
          if (!b.disabled) elegir(visibles[Number(b.getAttribute("data-option"))]);
        });
        on(b, "click", function (e) {
          if (e.detail === 0 && !b.disabled) elegir(visibles[Number(b.getAttribute("data-option"))]);
        });
      });
    }

    on(input, "focus", function () { abrir(true); });
    on(input, "input", function () {
      if (!eligiendo) { if (config.onQuery) config.onQuery(); abrir(false); }
    });
    on(input, "blur", function () {
      if (config.onBlur) config.onBlur();
      blurTimer = setTimeout(function () { if (document.activeElement !== input) cerrar(); }, 120);
    });
    on(input, "keydown", function (e) {
      var ops = list.querySelectorAll("button:not(:disabled)");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (list.hidden) { abrir(false); ops = list.querySelectorAll("button:not(:disabled)"); }
        idx = e.key === "ArrowDown" ? Math.min(idx + 1, ops.length - 1) : Math.max(idx - 1, 0);
        marcar();
        if (ops[idx]) ops[idx].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter") {
        if (!list.hidden && idx >= 0 && ops[idx]) { e.preventDefault(); elegir(visibles[Number(ops[idx].getAttribute("data-option"))]); }
        else if (!list.hidden) { if (config.onQuery) e.preventDefault(); cerrar(); }
      } else if (e.key === "Escape") {
        if (config.onBlur) config.onBlur();
        cerrar();
      }
    });
    if (btn) on(btn, "mousedown", function (e) {
      e.preventDefault();
      if (list.hidden) { input.focus(); abrir(true); } else cerrar();
    });
  }

  // One picker, existing wire contract: up to six obras_sociales and one
  // prepaga. Names are the catalog keys; RNOS only comes from saved/PUCO data.
  // Never infer a saved row's type from its name (Swiss can also be in PUCO).
  function normPrepaga(pre) {
    if (typeof pre === "string") return pre ? { nombre: pre } : {};
    return pre || {};
  }
  // «No tengo cobertura» is a choice of the same picker (on-site tester 21/10: the
  // list had no "Ninguna"). It is the wire's declarada_sin_cobertura, never
  // a payer name; withNone=false keeps callers with their own button as-is.
  function noCoverageLabel() { return T("account.form.coverage.none", null, "No tengo cobertura"); }
  function coverageItems(cob, withNone) {
    cob = cob || {};
    var obras = cob.obras_sociales;
    if (!Array.isArray(obras)) obras = cob.obra_social ? [cob.obra_social] : [];
    var items = obras.map(function (o) {
      if (typeof o === "string") o = { nombre: o };
      return { nombre: o.nombre || "", tipo: "obra_social", afiliado: o.afiliado || "", rnos: o.rnos || null, puco: o.origen === "puco" };
    });
    var pre = normPrepaga(cob.prepaga);
    if (pre.nombre) items.push({ nombre: pre.nombre, tipo: "prepaga", afiliado: pre.afiliado || "", plan: pre.plan || "" });
    if (!items.length && withNone && cob.declarada_sin_cobertura === true) return [{ nombre: noCoverageLabel(), tipo: "none" }];
    return items.length ? items : [{ nombre: "" }];
  }
  function coverageKey(s) { return _sinAcento(s).replace(/[^a-z0-9]/g, ""); }
  function coverageOptions(items, current, query, all) {
    var q = coverageKey(query);
    var options = OBRAS_SOCIALES.map(function (nombre) { return { nombre: nombre, tipo: "obra_social" }; })
      .concat(PREPAGAS.map(function (nombre) { return { nombre: nombre, tipo: "prepaga" }; }));
    // Keep saved legacy names and registry aliases selectable without
    // rewriting their identity, type, or registry code to a catalog guess.
    if (current.nombre && !options.some(function (o) { return o.nombre === current.nombre && o.tipo === current.tipo; })) {
      options.unshift({ nombre: current.nombre, tipo: current.tipo || "obra_social" });
    }
    var otherPrivate = items.some(function (o) { return o !== current && o.nombre && o.tipo === "prepaga"; });
    var otherInsurers = items.filter(function (o) { return o !== current && o.nombre && o.tipo !== "prepaga"; }).length;
    var exact = options.some(function (o) { return coverageKey(o.nombre) === q; });
    options = options.filter(function (o) { return all || !q || coverageKey(o.nombre).indexOf(q) !== -1; });
    var noneKey = coverageKey(noCoverageLabel());
    if (items.withNone && items.length === 1 && current.tipo !== "none" && (all || !q || noneKey.indexOf(q) !== -1)) {
      options.unshift({ nombre: noCoverageLabel(), tipo: "none" });
      if (noneKey === q) exact = true;
    }
    if (query.trim() && !exact && !all) {
      options.push({ nombre: query.trim(), tipo: "obra_social", label: T("account.form.coverage.manual_insurer", { name: query.trim() }, 'Agregar «{name}» como obra social') });
      options.push({ nombre: query.trim(), tipo: "prepaga", label: T("account.form.coverage.manual_private", { name: query.trim() }, 'Agregar «{name}» como prepaga') });
    }
    return options.map(function (o) {
      var duplicate = items.some(function (x) { return x !== current && x.tipo === o.tipo && coverageKey(x.nombre) === coverageKey(o.nombre); });
      var full = o.tipo === "prepaga" ? otherPrivate : otherInsurers >= 6;
      if (duplicate || full) {
        o.disabled = true;
        o.label = (o.label || o.nombre) + " — " + (duplicate
          ? T("account.form.coverage.already_added", null, "ya agregada")
          : o.tipo === "prepaga" ? T("account.form.coverage.one_private", null, "ya tenés una prepaga")
            : T("account.form.coverage.insurer_limit", null, "hasta 6 obras sociales"));
      }
      return o;
    });
  }
  function osSectionHtml(items, eco) {
    var pucoN = items.filter(function (o) { return o.puco; }).length;
    var notaPuesta = false;
    var rows = items.map(function (o, i) {
      var title = T("account.form.coverage.combined_label", null, "Obra social o prepaga");
      var label = i === 0 ? title : "";
      var selected = !!o.nombre;
      var removeLabel = T("account.form.coverage.remove_named", { name: o.nombre || "" }, "Quitar {name}");
      var quitar = '<button type="button" class="sc-os-del hr-x"' + (selected ? "" : " hidden") + ' aria-label="' + esc(removeLabel) + '"></button>';
      var inner = comboHtml("sc-os-" + i, "IOMA, PAMI, Swiss Medical…", o.nombre, label ? "" : title, quitar);
      if (o.puco && !notaPuesta) {
        inner += '<span class="sc-echo ok">' + osPucoNota(pucoN) + "</span>";
        notaPuesta = true;
      } else if (!o.puco && eco && i === 0) inner += '<span class="sc-echo">' + eco + "</span>";
      var memberLabel = o.tipo === "prepaga"
        ? T("account.form.coverage.private_member_number", null, "Nº de socio")
        : T("account.form.coverage.insurer_member_number", null, "Nº de afiliado");
      var details = '<div class="sc-row sc-cob-det" id="sc-os-af-row-' + i + '"' + (selected && o.tipo !== "none" ? "" : ' style="display:none"') + '><label for="sc-os-af-' + i + '">' + memberLabel + '</label>' +
        '<div class="sc-field"><input class="sc-in sc-af" id="sc-os-af-' + i + '" placeholder="' + T("account.form.coverage.member_number_hint", null, "el de tu credencial") + '" value="' + esc(o.afiliado || "") + '"></div></div>' +
        '<div class="sc-row sc-cob-det" id="sc-os-plan-row-' + i + '"' + (selected && o.tipo === "prepaga" ? "" : ' style="display:none"') + '><label for="sc-os-plan-' + i + '">' + T("account.form.coverage.plan_label", null, "Plan") + '</label>' +
        '<div class="sc-field"><input class="sc-in sc-plan" id="sc-os-plan-' + i + '" placeholder="210, SMG20…" value="' + esc(o.plan || "") + '"></div></div>';
      return '<div class="sc-os-item" data-i="' + i + '"' + (o.puco ? ' data-puco="1"' : "") + '>' +
        field(label, inner, label ? { forId: "sc-os-" + i } : {}) + details + "</div>";
    }).join("");
    return rows + '<div class="sc-row" id="sc-os-add-row" style="display:none"><span class="sc-label"></span>' +
      '<div class="sc-field"><button type="button" id="sc-os-add" class="sc-os-add">' + T("account.form.coverage.add_coverage", null, "Agregar otra cobertura") + '</button></div></div>';
  }
  // Search text is never a selected insurer. Capture only credential fields;
  // choosing a result or explicitly adding a manual value commits its name.
  function captureObras(host, items) {
    items.forEach(function (o, i) {
      var af = host.querySelector("#sc-os-af-" + i);
      if (af) o.afiliado = af.value;
      var plan = host.querySelector("#sc-os-plan-" + i);
      if (plan) o.plan = plan.value;
    });
  }
  function leerCobertura(host, items) {
    captureObras(host, items);
    var cob = { obras_sociales: [] };
    items.forEach(function (o) {
      if (o.tipo === "none") { cob.declarada_sin_cobertura = true; return; }
      if (!o.nombre) return;
      var out = { nombre: o.nombre };
      if ((o.afiliado || "").trim()) out.afiliado = o.afiliado.trim();
      if (o.tipo === "prepaga") {
        if ((o.plan || "").trim()) out.plan = o.plan.trim();
        cob.prepaga = out;
      } else {
        if (o.rnos) out.rnos = o.rnos;
        cob.obras_sociales.push(out);
      }
    });
    return cob;
  }
  function wireOsSection(host, items, opts) {
    opts = opts || {};
    function on(el, type, fn) { el.addEventListener(type, fn, opts.signal ? { signal: opts.signal } : undefined); }
    items.forEach(function (o, i) {
      var input = host.querySelector("#sc-os-" + i);
      wireCombo(host, "sc-os-" + i, function (query, all) { return coverageOptions(items, o, query, all); }, function (choice) {
        captureObras(host, items);
        if (o.nombre !== choice.nombre || (o.tipo || "obra_social") !== choice.tipo) {
          o.nombre = choice.nombre; o.tipo = choice.tipo;
          o.afiliado = ""; o.plan = ""; o.puco = false; o.rnos = null;
          host.querySelector("#sc-os-af-" + i).value = "";
          host.querySelector("#sc-os-plan-" + i).value = "";
          var row = input.closest(".sc-os-item");
          row.removeAttribute("data-puco");
          var echo = row.querySelector(".sc-echo"); if (echo) echo.remove();
        }
        o.querying = false;
        syncCoberturaRows(host);
        if (opts.onSave) opts.onSave();
      }, {
        signal: opts.signal,
        onQuery: function () { o.querying = true; syncCoberturaRows(host); },
        onBlur: function () { input.value = o.nombre || ""; o.querying = false; syncCoberturaRows(host); }
      });
      if (opts.onSave) {
        ["#sc-os-af-" + i, "#sc-os-plan-" + i].forEach(function (sel) {
          var el = host.querySelector(sel);
          if (el) on(el, "blur", function () { opts.onSave(); });
        });
      }
      var del = host.querySelector('.sc-os-item[data-i="' + i + '"] .sc-os-del');
      if (del && opts.rerender) on(del, "click", function () {
        captureObras(host, items);
        items.splice(i, 1);
        if (!items.length) items.push({ nombre: "" });
        opts.rerender();
        // This is an explicit removal, so put keyboard focus on the nearest
        // remaining picker. No delayed/global focus that steals another task.
        var next = host.querySelector("#sc-os-" + Math.min(i, items.length - 1));
        if (next) next.focus();
        if (opts.onSave) opts.onSave();
      });
    });
    var add = host.querySelector("#sc-os-add");
    if (add && opts.rerender) on(add, "click", function () {
      captureObras(host, items);
      items.push({ nombre: "" });
      opts.rerender();
      var inp = host.querySelector("#sc-os-" + (items.length - 1));
      if (inp) inp.focus();
    });
    syncCoberturaRows(host);
  }
  function syncCoberturaRows(host) {
    var items = host._osItems || [];
    host.querySelectorAll(".sc-os-item").forEach(function (item) {
      var i = Number(item.getAttribute("data-i"));
      var o = items[i] || {};
      var selected = !!o.nombre && !o.querying;
      var afRow = item.querySelector("#sc-os-af-row-" + i);
      if (afRow) {
        afRow.style.display = selected && o.tipo !== "none" ? "" : "none";
        afRow.querySelector("label").textContent = o.tipo === "prepaga"
          ? T("account.form.coverage.private_member_number", null, "Nº de socio")
          : T("account.form.coverage.insurer_member_number", null, "Nº de afiliado");
      }
      var planRow = item.querySelector("#sc-os-plan-row-" + i);
      if (planRow) planRow.style.display = selected && o.tipo === "prepaga" ? "" : "none";
      var del = item.querySelector(".sc-os-del");
      var arrow = item.querySelector(".sc-combo-btn");
      if (arrow) arrow.hidden = selected;
      if (del) {
        del.hidden = !selected;
        del.setAttribute("aria-label", T("account.form.coverage.remove_named", { name: o.nombre || "" }, "Quitar {name}"));
      }
    });
    var addRow = host.querySelector("#sc-os-add-row");
    if (addRow) addRow.style.display = items.length < 7 && items.every(function (o) { return o.nombre && !o.querying && o.tipo !== "none"; }) ? "" : "none";
  }

  // Public adapter around the SAME picker used by registration and account.
  // Its mount owns timers/listeners; it never persists a typed search query.
  function renderCoveragePicker(host, opts) {
    opts = opts || {};
    injectStyles();
    var items = coverageItems(opts.coverage), bindings;
    host._osItems = items;
    function draw() {
      if (bindings) bindings.abort();
      bindings = new AbortController();
      host.innerHTML = osSectionHtml(items, "");
      wireOsSection(host, items, { rerender: draw, onSave: opts.onChange, signal: bindings.signal });
    }
    draw();
    return {
      read: function () { return leerCobertura(host, items); },
      dispose: function () { bindings.abort(); delete host._osItems; }
    };
  }

  // Optional enhancement of an already-authorized booking receipt. No booking
  // POST, provider lookup or local cache; the server owns patient authorization.
  function renderPostbookingCoverage(host, opts) {
    opts = opts || {};
    var requestId = Number(opts.requestId);
    if (!host || !Number.isSafeInteger(requestId) || requestId <= 0) return function () {};
    injectStyles();
    var stopped = false, suspended = false, operation = 0, timer, deadline, controller, picker;
    var bindings = new AbortController(), revision, identity = opts.userKey || null;
    var route = location.pathname + location.search + location.hash;
    var endpoint = "/api/requests/" + requestId + "/coverage";
    function identityHint() { try { return localStorage.getItem("st_identity"); } catch (_) { return null; } }
    var initialHint = identityHint();
    host.classList.add("sc-card", "sc-postcoverage");
    function cancel() {
      operation++; clearTimeout(timer); clearTimeout(deadline);
      if (controller) controller.abort();
    }
    function dispose() {
      if (stopped) return;
      stopped = true; cancel(); bindings.abort(); observer.disconnect();
      if (picker) picker.dispose();
    }
    function sessionEnded() {
      dispose();
      host.innerHTML = '<p>' + T("account.form.coverage.session", null, "Ingresá a Mi cuenta para completar la cobertura. Tu solicitud no cambia.") +
        ' <a href="/account">' + T("account.form.coverage.account_link", null, "Mi cuenta") + '</a></p>';
    }
    function live() {
      if (stopped || suspended) return false;
      if (!host.isConnected || route !== location.pathname + location.search + location.hash) { dispose(); return false; }
      if (initialHint !== identityHint()) { sessionEnded(); return false; }
      return true;
    }
    var observer = new MutationObserver(function () { if (!host.isConnected) dispose(); });
    observer.observe(document.body, { childList: true, subtree: true });
    function listen(target, type, fn) { target.addEventListener(type, fn, { signal: bindings.signal }); }
    listen(window, "popstate", function (event) {
      var state = event && event.state;
      // The restored receipt's own traversal is not a route exit. Keep the
      // optional editor usable if popstate follows its persisted pageshow.
      if (state && state.wzStep === "success" && Number(state.wzRequestId) === requestId && route === location.pathname + location.search + location.hash) return;
      dispose();
    });
    listen(window, "hashchange", function () { dispose(); });
    listen(window, "storage", function (e) { if (!e.key || e.key === "st_identity") live(); });
    listen(window, "stz:session-change", sessionEnded);
    listen(window, "focus", function () { live(); });
    listen(window, "pagehide", function (e) {
      if (!e.persisted) { dispose(); return; }
      suspended = true; cancel(); if (picker) picker.dispose(); host.replaceChildren();
    });
    listen(window, "pageshow", function (e) { if (e.persisted && !stopped) { suspended = false; if (live()) load(); } });
    function say(text) { var el = host.querySelector("[data-coverage-status]"); if (el) el.textContent = text; }
    // One action on this card (the product owner 26.10: four buttons on a scrap of screen):
    // the person who does not want to fill it in just leaves it —
    // «Prefiero más tarde» and «No tengo cobertura» are gone from here.
    function frame(content) {
      if (picker) { picker.dispose(); picker = null; }
      host.innerHTML = '<h2 class="sc-card-h">' + T("account.form.coverage.after_title", null, "Tu cobertura (opcional)") + '</h2>' + content +
        '<p data-coverage-status role="status" aria-live="polite"></p>';
    }
    function done(data) {
      revision = data.revision;
      var names = coverageItems(data.cobertura).filter(function (o) { return o.nombre; }).map(function (o) { return o.nombre; });
      var edit = '<button type="button" class="sc-os-add" data-coverage-edit>' + T("account.form.coverage.edit", null, "Editar cobertura") + '</button>';
      if (names.length > 1 && !data.sin_cobertura) {
        frame('<p>' + T("account.form.coverage.saved", null, "Cobertura guardada.") + '</p><ul>' +
          names.map(function (name) { return '<li>' + esc(name) + '</li>'; }).join("") + '</ul>' + edit);
      } else {
        frame('<p>' + esc(data.sin_cobertura
          ? T("account.form.coverage.none_saved", null, "Guardado: no tenés cobertura.")
          : T("account.form.coverage.saved_named", { names: names[0] || "" }, "Cobertura guardada: {names}.")) + ' ' + edit + '</p>');
      }
      host.querySelector("[data-coverage-edit]").onclick = function () { form(data); };
    }
    function form(data) {
      revision = data.revision;
      frame('<form data-coverage-form><div data-coverage-picker></div><div class="sc-postcoverage-actions">' +
        '<button type="submit" class="sc-cta">' + T("account.form.coverage.save", null, "Guardar cobertura") + '</button></div></form>');
      picker = renderCoveragePicker(host.querySelector("[data-coverage-picker]"), { coverage: data.cobertura });
      host.querySelector("[data-coverage-form]").onsubmit = function (e) {
        e.preventDefault();
        var coverage = picker.read();
        if (!coverage.obras_sociales.length && !coverage.prepaga) {
          say(T("account.form.coverage.choose", null, "Elegí tu cobertura de la lista.")); return;
        }
        save({ choice: "coverage", cobertura: coverage, revision: revision });
      };
    }
    function busy(value) { host.querySelectorAll("form input, form button").forEach(function (el) { el.disabled = value; }); }
    function save(payload) {
      if (!live()) return;
      busy(true); say(T("account.form.coverage.saving", null, "Guardando cobertura…"));
      send(payload, done);
    }
    function load() {
      frame(""); say(T("account.form.coverage.loading", null, "Cargando cobertura…"));
      send(null, function (data) {
        if (!data.supported) {
          dispose();
          host.innerHTML = data.reason === "dependent" ? '<p>' + T("account.form.coverage.dependent", null,
            "La cobertura de una persona a cargo se completa en admisión. Tu solicitud ya quedó enviada.") + '</p>' : "";
          if (!host.textContent) host.hidden = true;
          return;
        }
        if (data.sin_cobertura || coverageItems(data.cobertura).some(function (o) { return o.nombre; })) done(data);
        else form(data);
      });
    }
    function send(payload, success) {
      cancel(); var mine = operation, delay = 1000, previous = 0;
      function attempt() {
        if (!live() || mine !== operation) return;
        controller = new AbortController();
        var timeout = setTimeout(function () { controller.abort(); }, 10000);
        deadline = timeout;
        // Recheck the actual session before each read/write/retry. No password
        // or identity is sent in the body; a new tab/session cannot inherit a draft.
        fetch("/api/account/me", { credentials: "same-origin", signal: controller.signal })
          .then(function (r) { if (r.status === 401) return null; if (!r.ok) throw new Error("unavailable"); return r.json(); })
          .then(function (session) {
            if (!live() || mine !== operation) return null;
            var user = session && session.usuario;
            if (!user || !user.dni || (identity && String(identity) !== String(user.dni))) { sessionEnded(); return null; }
            identity = user.dni;
            return fetch(endpoint, { method: payload ? "POST" : "GET", credentials: "same-origin", signal: controller.signal,
              headers: payload ? { "Content-Type": "application/json" } : {}, body: payload ? JSON.stringify(payload) : undefined });
          }).then(function (r) {
            if (!r) return null;
            return Promise.all([r.json(), fetch("/api/account/me", { credentials: "same-origin", signal: controller.signal })
              .then(function (check) { if (check.status === 401) return null; if (!check.ok) throw new Error("unavailable"); return check.json(); })])
              .then(function (values) {
                if (!live() || mine !== operation) return null;
                if (!values[1] || !values[1].usuario || String(values[1].usuario.dni) !== String(identity)) { sessionEnded(); return null; }
                return { response: r, data: values[0] };
              });
          })
          .then(function (result) {
            clearTimeout(timeout);
            if (!result || !live() || mine !== operation) return;
            if (result.response.ok) { success(result.data); return; }
            var status = result.response.status;
            if (status >= 500 || status === 429) { retry(); return; }
            if (status === 401 || status === 403 || status === 404) { sessionEnded(); return; }
            if (status === 409) {
              busy(true);
              say(T("account.form.coverage.changed", null, "La cobertura cambió en otra pantalla. Revisá lo guardado antes de reemplazarla."));
              var review = document.createElement("button"); review.type = "button"; review.className = "sc-os-add";
              review.textContent = T("account.form.coverage.review", null, "Revisar cobertura guardada"); review.onclick = load;
              host.querySelector("[data-coverage-status]").append(document.createElement("br"), review);
            } else { busy(false); say(T("account.form.coverage.invalid", null, "Revisá la cobertura elegida. Tu solicitud ya quedó enviada.")); }
          }).catch(function () { clearTimeout(timeout); if (live() && mine === operation) retry(); });
      }
      function retry() {
        say(T("account.form.coverage.reconnecting", null, "Esperando conexión. Podés continuar con tu solicitud."));
        timer = setTimeout(attempt, delay); var next = Math.min(60000, previous + delay); previous = delay; delay = next;
      }
      attempt();
    }
    load();
    return dispose;
  }

  function segOpt(val, label, current) {
    return '<label><input type="radio" name="sexo" value="' + val + '"' +
      (current === val ? " checked" : "") + '><span>' + label + "</span></label>";
  }

  // Abreviaturas del catálogo compartido date.month.* (mismo que listing.js) —
  // no una tercera tabla local; MES_CORTO_ES sólo alimenta el fallback de T().
  var MES_CORTO_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  function mesCorto(n) { return T("date.month." + n, null, MES_CORTO_ES[n - 1]); }

  /* Calendario año-primero para fecha de nacimiento (pedido del responsable del producto 2026-07-08):
     abre por el AÑO (grilla, sin años futuros, con los años típicos de un adulto
     a la vista), después mes, después día. Celdas grandes para el dedo. */
  function attachCalendar(hostEl, inputEl, calBtn, onPick) {
    var st = { open: false, step: "y", y: null, m: null };

    function outsideClick(e) {
      if (hostEl.contains(e.target) || calBtn.contains(e.target)) return;
      st.open = false;
      document.removeEventListener("click", outsideClick, true);
      render();
    }

    function head() {
      var bits = [];
      bits.push(st.y ? '<a data-go="y">' + st.y + "</a>" : "<b>" + T("account.form.calendar.year", null, "Año") + "</b>");
      if (st.step !== "y") bits.push(st.m ? '<a data-go="m">' + mesCorto(st.m) + "</a>" : "<b>" + T("account.form.calendar.month", null, "Mes") + "</b>");
      if (st.step === "d") bits.push("<b>" + T("account.form.calendar.day", null, "Día") + "</b>");
      return '<div class="sc-cal-head">' + bits.join('<span style="color:var(--text-4)">›</span>') + "</div>";
    }

    function grid() {
      var out = "", i;
      if (st.step === "y") {
        out = '<div class="sc-cal-grid y" id="sc-cal-scroll">';
        for (i = curY(); i >= MIN_YEAR; i--) out += '<button type="button" data-y="' + i + '">' + i + "</button>";
        out += "</div>";
      } else if (st.step === "m") {
        out = '<div class="sc-cal-grid m">';
        for (i = 1; i <= 12; i++) out += '<button type="button" data-m="' + i + '">' + mesCorto(i) + "</button>";
        out += "</div>";
      } else {
        out = '<div class="sc-cal-grid d">';
        var max = dim(st.y, st.m), hoy = isoToday();
        for (i = 1; i <= max; i++) {
          var iso = pad(st.y, 4) + "-" + pad(st.m, 2) + "-" + pad(i, 2);
          if (iso > hoy) break; // sin fechas futuras
          out += '<button type="button" data-d="' + i + '">' + i + "</button>";
        }
        out += "</div>";
      }
      return out;
    }

    function render() {
      if (!st.open) { hostEl.innerHTML = ""; return; }
      hostEl.innerHTML = '<div class="sc-cal">' + head() + grid() + "</div>";
      var sc = hostEl.querySelector("#sc-cal-scroll");
      if (sc) {
        // dejar a la vista los años de nacimiento típicos de un adulto (~1960-2000)
        var target = sc.querySelector('[data-y="' + (st.y || 1985) + '"]');
        if (target) target.scrollIntoView({ block: "center" });
      }
      hostEl.querySelectorAll("[data-go]").forEach(function (a) {
        a.addEventListener("click", function () { st.step = a.getAttribute("data-go"); render(); });
      });
      hostEl.querySelectorAll("button[data-y]").forEach(function (b) {
        b.addEventListener("click", function () { st.y = +b.getAttribute("data-y"); st.step = "m"; render(); });
      });
      hostEl.querySelectorAll("button[data-m]").forEach(function (b) {
        b.addEventListener("click", function () { st.m = +b.getAttribute("data-m"); st.step = "d"; render(); });
      });
      hostEl.querySelectorAll("button[data-d]").forEach(function (b) {
        b.addEventListener("click", function () {
          var d = +b.getAttribute("data-d");
          var iso = build(st.y, st.m, d);
          if (iso) { inputEl.value = isoToArg(iso); inputEl.dataset.iso = iso; }
          st.open = false;
          document.removeEventListener("click", outsideClick, true);
          render();
          if (onPick) onPick(iso);
        });
      });
    }

    return {
      toggle: function () {
        st.open = !st.open;
        if (st.open) {
          // si ya hay una fecha tipeada, arrancar el calendario desde ahí
          var iso = inputEl.dataset.iso || parseFlexibleDate(inputEl.value);
          if (iso) { st.y = +iso.slice(0, 4); st.m = +iso.slice(5, 7); }
          st.step = "y";
          document.addEventListener("click", outsideClick, true);
        } else {
          document.removeEventListener("click", outsideClick, true);
        }
        render();
      },
      close: function () {
        st.open = false;
        document.removeEventListener("click", outsideClick, true);
        render();
      }
    };
  }

  // ---------- render ----------
  function renderAuth(host, opts) {
    if (!host) return;
    opts = opts || {};
    injectStyles();
    var state = {
      mode: ["login", "recuperar"].includes(opts.defaultMode) ? opts.defaultMode : "registro",
      authMode: "password",
      identityProvider: "real",
      prefill: Object.assign({}, opts.prefill || {}),
      heading: opts.heading !== false,
      fotoPerfil: null,  // dataURL del retrato recortado del DNI (se sube al crear la cuenta)
      onDone: typeof opts.onDone === "function" ? opts.onDone : function () {},
      // The page that hosts the form with heading:false owns the title: it
      // hears every mode change («Ingresar» from the registration form) so the
      // title never names the other form (anon-16, 10.11).
      onMode: typeof opts.onMode === "function" ? opts.onMode : null,
      tsSitekey: null,   // sitekey de Turnstile (null = sin gate, dev local)
      // The server publishes whether phone verification is required. This UI
      // does not change the rollout flag or the email-only registration path.
      otpRequired: false,
      otp: { payload: null, cooldownUntil: 0, timer: null, registering: false },
      // obras sociales según el padrón (PUCO): la consulta se dispara apenas
      // el DNI está completo y corre mientras la persona llena el resto.
      // status: idle | busca | hit | miss | off (off = padrón no disponible)
      puco: { dni: null, status: "idle", obras: [] },
      // Selected registry/manual insurers and the optional private plan.
      osItems: null
    };

    var TS = null;
    var tsReady = null;
    function renderTurnstile() {
      var box = host.querySelector("#sc-turnstile");
      if (!box || state.mode !== "login" || !state.tsSitekey) { if (box) box.style.display = "none"; return; }
      if (!tsReady) tsReady = importStatic("platform/turnstile.mjs").then(function(m) { TS = m; });
      return tsReady.then(function() {
        if (box.isConnected) TS.mount(box, {turnstile_sitekey:state.tsSitekey});
      }).catch(function() {});
    }
    function resetTurnstile() { if (TS) TS.reset(); }

    // Ingreso federado: la pantalla de login ES el SSO (/auth/sso, estilo
    // sso-renaper). Navegación de página completa, como el SSO real — al
    // volver, la sesión ya existe y la página que embebía el form la levanta.
    function ssoUrl(provider) {
      return "/auth/sso?provider=" + encodeURIComponent(provider || "renaper") +
        "&next=" + encodeURIComponent(returnPath());
    }

    function openIdentityProvider(provider) {
      window.location.href = ssoUrl(provider);
    }

    // heading opcional: cuando la página que embebe el form ya tiene su
    // propio título+bajada (responsable del producto 2026-07-11: "no dupliques título y subtítulo"),
    // opts.heading:false lo apaga acá.
    function heading(title, sub) {
      if (!state.heading) return "";
      return "<h2>" + title + "</h2>" + (sub ? '<p class="sc-sub">' + sub + "</p>" : "");
    }

    function passField(opts2) {
      opts2 = opts2 || {};
      return field(T("account.form.password.label", null, "Contraseña"),
        '<div class="sc-pass-wrap">' +
        '<input class="sc-in" id="sc-pass" name="password" type="password" autocomplete="' + (opts2.autocomplete || "new-password") + '">' +
        '<button type="button" class="sc-eyebtn" id="sc-eyebtn" aria-label="' + T("account.form.password.show", null, "Mostrar contraseña") + '">' + EYE_ICON + "</button>" +
        "</div>" +
        (opts2.generate ? '<button type="button" class="sc-genbtn" id="sc-genbtn">' + T("account.form.password.generate", null, "O generá una automáticamente") + '</button>' : ""),
        { forId: "sc-pass" }
      );
    }

    // Registry rows retain their names/codes; manually selected coverages
    // (including the private plan) survive a background lookup.
    function osModelo() {
      // conserva lo ya tipeado (nombres manuales + afiliados) al recalcular
      var previos = state.osItems || [];
      var manuales = previos.filter(function (o) { return !o.puco; });
      if (!previos.length) {
        manuales = coverageItems(state.prefill.cobertura || {
          obra_social: state.prefill.obra_social ? { nombre: state.prefill.obra_social, afiliado: state.prefill.os_afiliado || "" } : null,
          prepaga: state.prefill.prepaga
        }, true);
      }
      var items;
      if (state.puco.status !== "hit") items = manuales.length ? manuales : [{ nombre: "", afiliado: "" }];
      else {
        items = state.puco.obras.map(function (o) {
          var prev = previos.filter(function (x) { return x.puco && x.nombre === o.nombre; })[0];
          return { nombre: o.nombre, tipo: "obra_social", rnos: o.rnos || null, puco: true, afiliado: prev ? prev.afiliado : "" };
        });
        // the registry found an insurer: «No tengo cobertura» no longer holds
        manuales.forEach(function (o) {
          if (o.tipo !== "none" && ((o.nombre || "").trim() || o.querying)) items.push(o);
        });
      }
      items.withNone = true;
      return items;
    }

    function osEco() {
      return state.puco.status === "busca"
        ? T("account.form.coverage.searching", null, "Buscando tu obra social en el padrón…")
        : state.puco.status === "miss"
          ? T("account.form.coverage.not_found", null, "No encontramos una obra social a tu nombre en el padrón.")
          : "";
    }

    // repintar conserva los ítems tal cual (lo usa "Agregar otra": la fila
    // recién agregada está vacía y NO debe filtrarse); renderOsSec rearma el
    // modelo (sólo en cambios de estado del lookup PUCO).
    function paintOsSec() {
      var sec = host.querySelector("#sc-os-sec");
      if (!sec) return;
      host._osItems = state.osItems;
      sec.innerHTML = osSectionHtml(state.osItems, osEco());
      wireOsSection(host, state.osItems, { rerender: paintOsSec });
    }
    function renderOsSec(rebuild) {
      var active = document.activeElement;
      var match = active && /^sc-os-(?:(af|plan)-)?(\d+)$/.exec(active.id);
      var focused = match && state.osItems && state.osItems[Number(match[2])];
      var query = focused && focused.querying ? active.value : null;
      if (state.osItems) captureObras(host, state.osItems);
      if (!state.osItems || rebuild) state.osItems = osModelo();
      paintOsSec();
      // A lookup may prepend registry rows while the person is typing. Keep
      // their own field/query active; unrelated fields never receive focus.
      if (focused) {
        var i = state.osItems.indexOf(focused);
        if (i >= 0) {
          var next = host.querySelector("#sc-os-" + (match[1] ? match[1] + "-" : "") + i);
          if (next) {
            next.focus();
            if (query !== null) { next.value = query; next.dispatchEvent(new Event("input", { bubbles: true })); }
          }
        }
      }
    }

    var pucoTimer = null;
    function pucoLookup(dni) {
      if (state.puco.dni === dni && state.puco.status !== "idle") return; // ya consultado o en vuelo
      state.puco = { dni: dni, status: "busca", obras: [] };
      renderOsSec(true);
      fetch("/api/puco", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dni: dni })
      }).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (j) {
        if (state.puco.dni !== dni) return; // el DNI cambió mientras tanto
        var obras = (j && j.obras_sociales) ||
          (j && j.obra_social && j.obra_social.nombre ? [j.obra_social] : []);
        if (!j || j.disponible !== true) state.puco = { dni: dni, status: "off", obras: [] };
        else if (obras.length) state.puco = { dni: dni, status: "hit", obras: obras };
        else state.puco = { dni: dni, status: "miss", obras: [] };
        renderOsSec(true);
      }).catch(function () {
        if (state.puco.dni !== dni) return;
        state.puco = { dni: dni, status: "off", obras: [] };
        renderOsSec(true);
      });
    }

    function armPuco(dniEl, alTiro) {
      var d = normDni(dniEl.value);
      if (d.length < 7 || d.length > 9) return;
      if (pucoTimer) clearTimeout(pucoTimer);
      if (alTiro) { pucoLookup(d); return; }
      pucoTimer = setTimeout(function () { pucoLookup(d); }, 600);
    }

    // ¿La IA está operativa? (OpenRouter). Si no, escondemos la tarjeta de foto
    // del DNI: el frente por código de barras andaría igual, pero el dorso
    // (domicilio) necesita OCR — media tarjeta rota confunde, así que se saca
    // entera (responsable del producto: "remové las opciones de IA cuando no anda"). Cache 1 fetch.
    var _iaCache = null;
    function iaDisponible() {
      if (_iaCache) return _iaCache;
      _iaCache = fetch("/api/ai-status", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (j) { return !!(j && j.disponible); })
        .catch(function () { return true; });  // fail-open: el endpoint OCR igual gatea
      return _iaCache;
    }
    function hideScanIfDown() {
      iaDisponible().then(function (disp) {
        if (!disp) { var c = host.querySelector(".sc-scan"); if (c) c.style.display = "none"; }
      });
    }

    function scanHtml() {
      return (
        '<div class="sc-scan">' +
        '<p class="sc-scan-title">' + T("account.form.scan.title", null, "Opcional: completá tus datos con una foto del DNI") + '</p>' +
        '<p style="font-size:16px;color:var(--text);">' + T("ai.notice.dni", null, "La inteligencia artificial lee la foto completa del DNI en un proveedor externo. Revisá los datos antes de registrarte.") + '</p>' +
        '<div class="sc-scan-btns">' +
        '<button type="button" class="sc-scan-btn" id="sc-scan-f"><span class="sc-scan-ico" aria-hidden="true"></span>' + T("account.form.scan.front_button", null, "Foto del frente") + '</button>' +
        '<button type="button" class="sc-scan-btn" id="sc-scan-d"><span class="sc-scan-ico" aria-hidden="true"></span>' + T("account.form.scan.back_button", null, "Foto del dorso") + '</button>' +
        '</div>' +
        '<input type="file" accept="image/*" id="sc-scan-f-file" hidden>' +
        '<input type="file" accept="image/*" id="sc-scan-d-file" hidden>' +
        '<div class="sc-scan-status" id="sc-scan-status"></div>' +
        '<div class="sc-foto-prev" id="sc-foto-prev" hidden>' +
        '<img id="sc-foto-img" alt="' + T("account.form.scan.photo_alt", null, "Tu foto, recortada del DNI") + '">' +
        '<span>' + T("account.form.scan.photo_caption", null, "La usamos como tu foto de perfil.") + '</span>' +
        '<a id="sc-foto-no">' + T("account.form.scan.dont_use", null, "No usar") + '</a>' +
        '</div>' +
        '</div>'
      );
    }

    function domFields(p) {
      var d = p.domicilio || {};
      return (
        field(T("account.form.address.label", null, "Domicilio"), '<input class="sc-in sc-wide" id="sc-dom-calle" name="domicilio" autocomplete="street-address" placeholder="' + T("account.form.address.placeholder", null, "Calle y número") + '" value="' + esc(domLine(d)) + '">', { forId: "sc-dom-calle" }) +
        field(T("account.form.address.city_label", null, "Localidad"), comboHtml("sc-dom-loc", "Pinamar, Cariló…", d.localidad || ""), { forId: "sc-dom-loc" }) +
        field(T("account.form.address.postal_label", null, "Código postal"), '<input class="sc-in sc-cp" id="sc-dom-cp" inputmode="numeric" autocomplete="postal-code" placeholder="7167" value="' + esc(d.cp || "") + '">', { forId: "sc-dom-cp" })
      );
    }

    // El campo DNI de las pantallas pre-cuenta, en UN solo lugar (registro,
    // login y recuperar comparten el mismo control): placeholder con forma de
    // patrón, no de valor real (responsable del producto 2026-09-11: "30.123.456" parecía un DNI
    // ya cargado — 12.345.678 se lee como la máscara que es).
    function dniFieldHtml(p) {
      return field(T("account.form.field.dni", null, "DNI"),
        '<input class="sc-in sc-dni" id="sc-dni" name="dni" inputmode="numeric" autocomplete="username" placeholder="12.345.678" value="' + esc(fmtDni(p.dni)) + '">', { forId: "sc-dni" });
    }
    // El gemelo email del mismo control (login/recuperar/registro sin DNI de
    // un visitante no hispanohablante): autocomplete username — para el
    // gestor de contraseñas el identificador de la cuenta.
    function emailFieldHtml(p, forId) {
      forId = forId || "sc-email";
      return field(T("account.form.field.email", null, "Email"),
        '<input class="sc-in sc-wide" id="' + forId + '" name="email" type="email" autocomplete="username" placeholder="' + T("account.form.field.email_placeholder", null, "vos@ejemplo.com") + '" value="' + esc(p.email || "") + '">', { forId: forId });
    }
    // Keep explicit empty input and email/DNI spelling across auth screens.
    // Only the registration DNI field uses document-number formatting.
    function authIdentifier(p) {
      return String(p.identifier != null ? p.identifier : p.email || p.dni || "").trim();
    }
    function identifierFieldHtml(p) {
      return field(T("account.form.field.identifier", null, "DNI o email"),
        '<input class="sc-in sc-wide" id="sc-dni" name="username" inputmode="text" autocomplete="username" autocapitalize="off" spellcheck="false" value="' + esc(authIdentifier(p)) + '">', { forId: "sc-dni" });
    }

    function regHtml(p) {
      // Visitante no hispanohablante: sin DNI, sin foto/escaneo de DNI, sin
      // obra social/prepaga ni consulta al padrón (PUCO) — el email es la
      // identidad de la cuenta. Todo lo demás queda igual.
      var sinDni = sinDniVisitante();
      return (
        '<div class="sc-authform">' +
        heading(T("account.form.register.heading", null, "Crear cuenta"), T("account.form.register.lede", null, "Necesitás una cuenta para sacar turno. Ver los centros y servicios es libre.")) +
        '<div class="sc-toggle">' + T("account.form.register.have_account", null, "¿Ya tenés cuenta?") + ' <a href="#" id="sc-swap">' + T("account.form.action.sign_in", null, "Ingresar") + '</a></div>' +
        (sinDni ? "" : scanHtml()) +
        '<form id="sc-form" autocomplete="on" novalidate>' +
        (sinDni ? "" : dniFieldHtml(p)) +
        field(T("account.form.field.first_name", null, "Nombre"), '<input class="sc-in sc-wide" id="sc-nombre" name="nombre" autocomplete="given-name" value="' + esc(p.nombre || "") + '">', { forId: "sc-nombre" }) +
        field(T("account.form.field.last_name", null, "Apellido"), '<input class="sc-in sc-wide" id="sc-apellido" name="apellido" autocomplete="family-name" value="' + esc(p.apellido || "") + '">', { forId: "sc-apellido" }) +
        field(T("account.form.field.dob", null, "Nacimiento"),
          '<div class="sc-dob-wrap">' +
          '<input class="sc-in sc-dob" id="sc-nac" name="nacimiento" inputmode="numeric" autocomplete="bday" placeholder="' + T("account.form.field.dob_placeholder", null, "DD/MM/AAAA") + '" value="' + esc(p.nacimiento ? isoToArg(p.nacimiento) || p.nacimiento : "") + '">' +
          '<button type="button" class="sc-calbtn" id="sc-calbtn" aria-label="' + T("account.form.field.dob_choose_calendar", null, "Elegir en el calendario") + '">' + CAL_ICON + "</button>" +
          "</div>" +
          '<div id="sc-cal-host"></div>' +
          '<span class="sc-echo" id="sc-nac-echo"></span>', { forId: "sc-nac" }) +
        field(sinDni ? T("account.form.field.sex_plain", null, "Sexo") : T("account.form.field.sex", null, "Sexo <span style=\"font-weight:400\">(según tu DNI)</span>"),
          '<div class="sc-seg" id="sc-sexo-seg" role="radiogroup" aria-labelledby="sc-sexo-label">' +
          segOpt("F", T("account.form.field.sex_female", null, "Femenino"), p.sexo) + segOpt("M", T("account.form.field.sex_male", null, "Masculino"), p.sexo) + segOpt("X", T("account.form.field.sex_other", null, "Otro"), p.sexo) +
          "</div>", { labelId: "sc-sexo-label" }) +
        domFields(p) +
        (sinDni ? "" : '<div id="sc-os-sec"></div>') +
        field(T("account.form.field.phone", null, "Teléfono"), '<input class="sc-in sc-tel" id="sc-contacto" name="contacto" inputmode="tel" autocomplete="tel" placeholder="2254 550000" value="' + esc(p.contacto || "") + '">', { forId: "sc-contacto" }) +
        (sinDni
          ? field(T("account.form.field.email", null, "Email"), '<input class="sc-in sc-wide" id="sc-email" name="email" type="email" autocomplete="email" placeholder="' + T("account.form.field.email_placeholder", null, "vos@ejemplo.com") + '" value="' + esc(p.email || "") + '" required>', { forId: "sc-email" })
          : field(T("account.form.field.email", null, "Email"), '<input class="sc-in sc-wide" id="sc-email" name="email" type="email" autocomplete="email" placeholder="' + T("account.form.field.email_placeholder", null, "vos@ejemplo.com") + '" value="' + esc(p.email || "") + '">', { forId: "sc-email" })) +
        '<span class="sc-hint">' + (sinDni
          ? T("account.form.hint.email_required", null, "Dejá tu email: con él ingresás a tu cuenta y te avisamos de tus turnos.")
          : T("account.form.hint.contact_required", null, "Dejá al menos un teléfono o un email: los necesitamos para avisarte de tus turnos.")) + '</span>' +
        passField({ generate: true }) +
        '<button class="sc-cta" id="sc-submit" type="submit">' + T("account.form.register.heading", null, "Crear cuenta") + '</button>' +
        '<div class="sc-msg" id="sc-msg"></div>' +
        "</form>" +
        "</div>"
      );
    }

    // This is only reachable after the ordinary registration form has been
    // validated and /api/account/otp/send accepted the phone number. The
    // code itself is never retained after registration succeeds: the server
    // owns the short-lived, one-use verification ticket.
    function otpHtml() {
      return (
        '<div class="sc-authform">' +
        heading(T("account.form.otp.heading", null, "Confirmá tu teléfono"), T("account.form.otp.lede", null, "Ingresá el código de verificación de 6 dígitos antes de crear tu cuenta.")) +
        '<p class="sc-otp-copy">' + T("account.form.otp.sent_to", null, "Te mandamos un código de 6 dígitos por WhatsApp.") + '</p>' +
        '<form id="sc-otp-form" autocomplete="one-time-code" novalidate>' +
        field(T("account.form.otp.code_label", null, "Código de verificación"),
          '<input class="sc-in sc-otp-code" id="sc-otp-code" name="otp" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]*" maxlength="6" aria-describedby="sc-otp-msg">', { forId: "sc-otp-code" }) +
        '<button class="sc-cta" id="sc-otp-submit" type="submit">' + T("account.form.otp.verify", null, "Confirmar código") + '</button>' +
        '<button class="sc-otp-resend" id="sc-otp-resend" type="button"></button>' +
        '<div class="sc-msg" id="sc-otp-msg" role="status" aria-live="polite"></div>' +
        "</form>" +
        "</div>"
      );
    }

    function loginHtml(p) {
      var sinDni = sinDniVisitante();
      return (
        '<div class="sc-authform">' +
        heading(T("account.form.action.sign_in", null, "Ingresar"), sinDni
          ? T("account.form.login.lede_email", null, "Entrá con tu email y contraseña para ver y sacar turnos.")
          : T("account.form.login.lede", null, "Entrá con tu DNI y contraseña para ver y sacar turnos.")) +
        '<div class="sc-toggle">' + T("account.form.login.have_no_account", null, "¿No tenés cuenta?") + ' <a href="#" id="sc-swap">' + T("account.form.action.create_one", null, "Crear una") + '</a></div>' +
        '<form id="sc-form" autocomplete="on" novalidate>' +
        (sinDni ? emailFieldHtml(p) : identifierFieldHtml(p)) +
        passField({ autocomplete: "current-password" }) +
        '<div class="sc-indent" style="margin-top:-8px;margin-bottom:20px;"><a href="#" id="sc-forgot" class="sc-forgot">' + T("account.form.login.forgot_password", null, "¿Olvidaste tu contraseña?") + '</a></div>' +
        '<div id="sc-turnstile" class="sc-indent" style="margin-bottom:12px;display:none;"></div>' +
        '<button class="sc-cta" id="sc-submit" type="submit">' + T("account.form.action.sign_in", null, "Ingresar") + '</button>' +
        '<div class="sc-msg" id="sc-msg"></div>' +
        "</form>" +
        "</div>"
      );
    }

    function renaperHtml() {
      var note = state.identityProvider === "stub"
        ? '<p class="sc-provider-note">' + T("account.form.renaper.test_note", null,
            "<strong>Entorno de prueba.</strong> El ingreso abre un simulador local del SSO con identidades sintéticas.") + '</p>'
        : "";
      return (
        '<div class="sc-authform">' +
        heading(T("account.form.action.sign_in", null, "Ingresar"), T("account.form.renaper.lede", null, "Confirmá quién sos con tu DNI, validado por RENAPER. Sin contraseñas.")) +
        '<div class="sc-provider-list">' +
        '<button class="sc-provider sc-provider--primary" data-provider="renaper" type="button">' + T("account.form.renaper.button", null, "Ingresar con RENAPER") + '</button>' +
        "</div>" + note +
        '<div class="sc-msg" id="sc-msg"></div>' +
        "</div>"
      );
    }

    /* Installation whose patients come in only through the municipal portal
       (auth_mode "portal"): no password, no sign-up, no recovery — the one
       way in, or, when the portal already vouched for this person and still
       no account opened, the remedy (help line, else the front desk). */
    // The installation's main center (the product owner 23.10): «Hospital de Pinamar,
    // Av. Shaw 255 · 2254 60-2222», its name linking to its page.
    function centerPlaceHtml(c, withContact) {
      var parts = ['<a href="' + esc(c.url) + '">' + esc(c.nombre) + "</a>"];
      if (c.direccion) {
        parts.push(c.maps_url
          ? '<a href="' + esc(c.maps_url) + '" target="_blank" rel="noopener noreferrer">' + esc(c.direccion) + "</a>"
          : esc(c.direccion));
      }
      var place = parts.join(", ");
      if (withContact && c.telefono && c.telefono_href) {
        place += ' <span class="sc-nowrap">·&nbsp;<a href="' + esc(c.telefono_href) + '">' + esc(c.telefono) + "</a></span>";
      }
      return place;
    }

    function portalHtml() {
      var p = state.portal || {};
      if (p.identidad) {
        var c = p.centro && p.centro.nombre && p.centro.url ? p.centro : null;
        var wa = p.ayudaWhatsapp
          ? T("account.form.portal.refused_whatsapp", null,
              "Tu usuario del portal no sirve para sacar turnos acá. Escribinos por WhatsApp y lo resolvemos:") +
            ' <a href="https://wa.me/' + esc(p.ayudaWhatsapp) + '">' + esc(p.ayudaTexto || p.ayudaWhatsapp) + "</a>" +
            (c ? '</p><p class="sc-portal-p">' + T("account.form.portal.refused_center_also", null,
              "También podés acercarte a la admisión:") + " " + centerPlaceHtml(c, false) : "")
          : c
            ? T("account.form.portal.refused_desk_center", null,
                "Tu usuario del portal no sirve para sacar turnos acá. Acercate a la admisión y lo resolvemos:") +
              " " + centerPlaceHtml(c, true)
            : T("account.form.portal.refused_desk", null,
                "Tu usuario del portal no sirve para sacar turnos acá. Acercate a la admisión de tu centro de salud y lo resolvemos.");
        return '<div class="sc-authform">' +
          heading(T("account.form.portal.refused_heading", null, "No pudimos abrir tu cuenta")) +
          '<p class="sc-portal-p">' + wa + "</p></div>";
      }
      return '<div class="sc-authform">' +
        heading(T("account.form.action.sign_in", null, "Ingresar")) +
        '<p class="sc-portal-p">' + T("account.form.portal.lede", null, "Entrá con tu cuenta del portal para ver y sacar turnos.") + "</p>" +
        (p.loginUrl
          ? '<div class="sc-provider-list"><a class="sc-provider sc-provider--primary" href="' + esc(portalEntryHref()) + '">' +
            T("account.form.portal.button", null, "Ingresar con el portal") + "</a></div>"
          : "") +
        "</div>";
    }

    function recuperarHtml(p) {
      return (
        '<div class="sc-authform">' +
        heading(T("account.form.recover.heading", null, "Olvidé mi contraseña"), T("account.form.recover.lede", null, "Te mandamos un link a tu email para elegir una contraseña nueva.")) +
        '<div class="sc-toggle">' + T("account.form.recover.remembered", null, "¿Te acordaste?") + ' <a href="#" id="sc-swap">' + T("account.form.action.sign_in", null, "Ingresar") + '</a></div>' +
        '<form id="sc-form" autocomplete="on" novalidate>' +
        identifierFieldHtml(p) +
        '<button class="sc-cta" id="sc-submit" type="submit">' + T("account.form.recover.submit", null, "Enviar instrucciones") + '</button>' +
        '<div class="sc-msg" id="sc-msg"></div>' +
        "</form>" +
        sinEmailHtml() +
        "</div>"
      );
    }

    // The link only goes to an email (TASK-50): no code is sent by WhatsApp
    // or SMS. An account without an email gets one at the admission desk,
    // then this same screen works. Said to everyone — /recover answers the
    // same whether a DNI exists, so the screen never reveals which do.
    function sinEmailHtml() {
      var a = state.ayuda || {};
      var c = a.centro && a.centro.nombre && a.centro.url ? a.centro : null;
      var text = a.whatsapp
        ? T("account.form.recover.no_email_whatsapp", null, "¿Tu cuenta no tiene email? Escribinos por WhatsApp y te lo agregamos:") +
          ' <a href="https://wa.me/' + esc(a.whatsapp) + '">' + esc(a.whatsappTexto || a.whatsapp) + "</a>"
        : c
          ? T("account.form.recover.no_email_center", null, "¿Tu cuenta no tiene email? Acercate a la admisión y te lo agregan:") +
            " " + centerPlaceHtml(c, true)
          : T("account.form.recover.no_email_desk", null, "¿Tu cuenta no tiene email? Acercate a la admisión de tu centro de salud y te lo agregan.");
      return '<p class="sc-portal-p" id="sc-sin-email">' + text + "</p>";
    }

    // ---- foto del DNI: prefill de los campos ------------------------------
    function prefillFront(patient) {
      if (!patient) return;
      var dniEl = host.querySelector("#sc-dni");
      if (dniEl && patient.dni) {
        dniEl.value = fmtDni(patient.dni);
        dniEl.dispatchEvent(new Event("input", { bubbles: true })); // normaliza + dispara PUCO
      }
      var nEl = host.querySelector("#sc-nombre");
      if (nEl && patient.nombres) nEl.value = titleCase(patient.nombres);
      var aEl = host.querySelector("#sc-apellido");
      if (aEl && patient.apellido) aEl.value = titleCase(patient.apellido);
      if (patient.sexo) {
        var r = host.querySelector('#sc-sexo-seg input[value="' + patient.sexo + '"]');
        if (r) r.checked = true;
      }
      var nacEl = host.querySelector("#sc-nac");
      if (nacEl && patient.nacimiento) {
        nacEl.value = isoToArg(patient.nacimiento) || patient.nacimiento;
        nacEl.dispatchEvent(new Event("input", { bubbles: true })); // valida
        if (host._dob) host._dob.commit(); // y lo pinta directo en palabras
      }
    }

    function prefillBack(dom) {
      if (!dom) return;
      var setv = function (id, v) { var e = host.querySelector(id); if (e && v) e.value = v; };
      setv("#sc-dom-calle", domLine(dom));
      setv("#sc-dom-loc", dom.localidad);
      setv("#sc-dom-cp", dom.cp);
      state.prefill.domicilio = Object.assign({}, state.prefill.domicilio, dom); // conserva provincia
    }

    function otpResponse(response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        return { ok: response.ok, status: response.status, body: body || {} };
      });
    }

    
    function mapErrorCode(code) {
      const mapping = {
        'database_unavailable': 'account.form.error.database_unavailable',
        'name_required': 'account.form.error.name_required',
        'dni_already_account_holder': 'account.form.error.dni_already_account_holder',
        'check_dob': 'account.form.error.check_dob',
        'choose_relationship': 'account.form.error.choose_relationship',
        'confirm_data': 'account.form.error.confirm_data',
        'invalid_email': 'account.form.error.invalid_email',
        'invalid_contact': 'account.form.error.invalid_contact',
        'invalid_purpose': 'account.form.error.invalid_purpose',
        'invalid_gender': 'account.form.error.invalid_gender',
        'dob_invalid': 'account.form.error.dob_invalid',
      };
      return mapping[code];
    }
    function otpMessage(key, fallback) {
      return T(key, null, fallback);
    }

    // The DNI already has an account (TASK-50, 26.10). The sign-up stays on
    // screen exactly as typed — it used to jump to the login screen and erase
    // the whole form, and a 74-year-old gave up there — and the refusal
    // itself offers both ways in. Only the rejected password is dropped.
    function showRegistrationConflict(identifier) {
      if (state.otp.timer) clearTimeout(state.otp.timer);
      var sent = state.otp.payload;
      state.otp = { payload: null, cooldownUntil: 0, timer: null, registering: false };
      if (state.mode === "otp" && sent) {
        // Phone-verified sign-up: the form is behind the code screen; what
        // was sent is what was typed.
        ["nombre", "apellido", "nacimiento", "sexo", "contacto", "email"].forEach(function (k) {
          if (sent[k] != null && sent[k] !== "") state.prefill[k] = sent[k];
        });
      } else {
        capture();
      }
      state.prefill.identifier = String(identifier || "").trim();
      if (sinDniVisitante()) state.prefill.email = state.prefill.identifier;
      else state.prefill.dni = state.prefill.identifier;
      state.mode = "registro";
      render(); // A fresh password input; never carry the rejected password.
      var message = host.querySelector("#sc-msg");
      if (!message) return;
      message.className = "sc-msg show err";
      message.innerHTML = esc(T("account.form.error.registration_exists", null,
        "Ya tenés una cuenta: no creamos otra y la contraseña que escribiste no se guardó.")) +
        ' <a href="#" id="sc-conflict-login">' + esc(T("account.form.action.sign_in", null, "Ingresar")) + "</a>" +
        ' · <a href="#" id="sc-conflict-forgot">' + esc(T("account.form.login.forgot_password", null, "¿Olvidaste tu contraseña?")) + "</a>";
      [["#sc-conflict-login", "login"], ["#sc-conflict-forgot", "recuperar"]].forEach(function (pair) {
        var link = message.querySelector(pair[0]);
        if (link) link.addEventListener("click", function (e) {
          e.preventDefault();
          capture();
          state.mode = pair[1];
          render();
          var next = host.querySelector(pair[1] === "login" ? "#sc-pass" : "#sc-dni");
          if (next) next.focus();
        });
      });
      var first = message.querySelector("#sc-conflict-login");
      if (first) first.focus();
    }

    function completeVerifiedRegistration(codeEl, submit, say) {
      var payload = state.otp.payload;
      if (!payload || state.otp.registering) return;
      state.otp.registering = true;
      if (submit) submit.disabled = true;
      if (codeEl) codeEl.disabled = true;
      say(otpMessage("account.form.otp.creating", "Creando tu cuenta…"), "ok");
      fetch("/api/account/register", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(otpResponse).then(function (res) {
        if (res.ok && res.body && res.body.usuario) {
          if (state.otp.timer) clearTimeout(state.otp.timer);
          state.otp = { payload: null, cooldownUntil: 0, timer: null, registering: false };
          mirror(res.body.usuario);
          if (state.fotoPerfil) {
            try {
              fetch("/api/account/photo", {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ data_b64: state.fotoPerfil })
              }).catch(function () {});
            } catch (e) {}
          }
          state.onDone(res.body.usuario);
          return;
        }
        state.otp.registering = false;
        if (submit) submit.disabled = false;
        if (codeEl) codeEl.disabled = false;
        if (res.status === 409) {
          showRegistrationConflict(sinDniVisitante() ? payload.email : fmtDni(payload.dni));
          return;
        }
        if (res.body.error === "telefono_no_verificado") {
          say(otpMessage("account.form.otp.verify_again", "Confirmá el código de nuevo antes de crear tu cuenta."), "err");
          if (codeEl) codeEl.focus();
          return;
        }
        say(res.body.error && mapErrorCode(res.body.error) ? otpMessage(mapErrorCode(res.body.error), res.body.error) : otpMessage("account.form.error.generic", "No pudimos completar la operación. Probá de nuevo."), "err");
        if (codeEl) codeEl.focus();
      }).catch(function () {
        state.otp.registering = false;
        if (submit) submit.disabled = false;
        if (codeEl) { codeEl.disabled = false; codeEl.focus(); }
        say(otpMessage("account.form.error.network", "Error de red. Probá de nuevo."), "err");
      });
    }

    function wireOtp() {
      var form = host.querySelector("#sc-otp-form");
      var code = host.querySelector("#sc-otp-code");
      var submit = host.querySelector("#sc-otp-submit");
      var resend = host.querySelector("#sc-otp-resend");
      var message = host.querySelector("#sc-otp-msg");
      function say(text, kind) {
        if (!message) return;
        message.className = "sc-msg show " + (kind === "err" ? "err" : "ok");
        message.textContent = text;
      }
      function refreshResend() {
        var seconds = Math.max(0, Math.ceil((state.otp.cooldownUntil - Date.now()) / 1000));
        if (resend) {
          resend.disabled = seconds > 0;
          resend.textContent = seconds > 0
            ? otpMessage("account.form.otp.resend_in", "Reenviar código en ") + seconds + "s"
            : otpMessage("account.form.otp.resend", "Reenviar código");
        }
        if (seconds > 0) state.otp.timer = setTimeout(refreshResend, 500);
        else state.otp.timer = null;
      }
      refreshResend();
      if (code) {
        code.addEventListener("input", function () {
          code.value = String(code.value || "").replace(/\D/g, "").slice(0, 6);
          if (message && message.className.indexOf("err") !== -1) { message.className = "sc-msg"; message.textContent = ""; }
        });
        code.addEventListener("blur", function () {
          var value = String(code.value || "").replace(/\D/g, "");
          if (value && value.length !== 6) say(otpMessage("account.form.otp.code_invalid", "Ingresá el código de 6 dígitos."), "err");
        });
        setTimeout(function () { if (!state.otp.registering) code.focus(); }, 0);
      }
      if (form) form.addEventListener("submit", function (event) {
        event.preventDefault();
        var value = String((code || {}).value || "").replace(/\D/g, "");
        if (value.length !== 6) {
          say(otpMessage("account.form.otp.code_invalid", "Ingresá el código de 6 dígitos."), "err");
          if (code) code.focus();
          return;
        }
        if (submit) submit.disabled = true;
        say(otpMessage("account.form.otp.verifying", "Confirmando código…"), "ok");
        fetch("/api/account/otp/verify", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ contacto: state.otp.payload.contacto, proposito: "registro", code: value })
        }).then(otpResponse).then(function (res) {
          if (res.ok && res.body.ok) { completeVerifiedRegistration(code, submit, say); return; }
          if (submit) submit.disabled = false;
          say(otpMessage("account.form.otp.code_wrong", "Ese código no es válido. Revisalo e intentá de nuevo."), "err");
          if (code) code.focus();
        }).catch(function () {
          if (submit) submit.disabled = false;
          say(otpMessage("account.form.error.network", "Error de red. Probá de nuevo."), "err");
          if (code) code.focus();
        });
      });
      if (resend) resend.addEventListener("click", function () {
        if (resend.disabled || !state.otp.payload) return;
        resend.disabled = true;
        fetch("/api/account/otp/send", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ contacto: state.otp.payload.contacto, proposito: "registro" })
        }).then(otpResponse).then(function (res) {
          if (res.ok && res.body.ok) {
            state.otp.cooldownUntil = Date.now() + 45000;
            refreshResend();
            say(otpMessage("account.form.otp.resent", "Te mandamos un código nuevo."), "ok");
            if (code) code.focus();
            return;
          }
          if (res.status === 429) {
            state.otp.cooldownUntil = Date.now() + 45000;
            refreshResend();
            say(otpMessage("account.form.otp.wait", "Esperá antes de pedir otro código."), "err");
            return;
          }
          resend.disabled = false;
          say(otpMessage("account.form.otp.send_failed", "No pudimos mandar un código. Probá de nuevo."), "err");
        }).catch(function () {
          resend.disabled = false;
          say(otpMessage("account.form.error.network", "Error de red. Probá de nuevo."), "err");
        });
      });
    }

    function startRegistrationOtp(payload, say, button) {
      if (button) button.disabled = true;
      say(otpMessage("account.form.otp.sending", "Mandando un código de verificación…"), "ok");
      fetch("/api/account/otp/send", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ contacto: payload.contacto, proposito: "registro" })
      }).then(otpResponse).then(function (res) {
        if (res.ok && res.body.ok) {
          state.otp = { payload: payload, cooldownUntil: Date.now() + 45000, timer: null, registering: false };
          state.mode = "otp";
          render();
          return;
        }
        if (button) button.disabled = false;
        say(res.status === 429
          ? otpMessage("account.form.otp.wait", "Esperá antes de pedir otro código.")
          : otpMessage("account.form.otp.send_failed", "No pudimos mandar un código. Probá de nuevo."), "err");
      }).catch(function () {
        if (button) button.disabled = false;
        say(otpMessage("account.form.error.network", "Error de red. Probá de nuevo."), "err");
      });
    }

    function readDom() {
      var g = function (id) { var e = host.querySelector(id); return e && e.value ? e.value.trim() : ""; };
      var calle = g("#sc-dom-calle"), loc = g("#sc-dom-loc"), cp = g("#sc-dom-cp");
      if (!calle && !loc && !cp) return null;
      var prov = (state.prefill.domicilio && state.prefill.domicilio.provincia) || "";
      var d = {};
      if (calle) d.calle = calle;
      if (loc) d.localidad = loc;
      if (cp) d.cp = cp;
      if (prov) d.provincia = prov;
      d.texto = [calle, loc, cp ? "(" + cp + ")" : ""].filter(Boolean).join(", ");
      return d;
    }

    // ---- foto de perfil recortada del retrato del DNI ----------------------
    function showFotoPrev(dataUrl) {
      var box = host.querySelector("#sc-foto-prev");
      var img = host.querySelector("#sc-foto-img");
      if (!box || !img) return;
      img.src = dataUrl;
      box.hidden = false;
      var no = host.querySelector("#sc-foto-no");
      if (no && !no._wired) {
        no._wired = true;
        no.addEventListener("click", function () { state.fotoPerfil = null; box.hidden = true; });
      }
    }

    // dataUrlP = la foto del frente; bboxP = el recuadro del retrato. Cuando
    // los dos llegan, recorta local y muestra la vista previa. Best-effort:
    // cualquier falla se traga en silencio — la foto de perfil es un extra,
    // jamás traba el registro.
    function fotoPerfilDesde(dataUrlP, bboxP) {
      Promise.all([dataUrlP, bboxP]).then(function (r) {
        if (!r[0] || !r[1]) return null;
        return cropFaceFromDataUrl(r[0], r[1], 480).then(function (crop) {
          state.fotoPerfil = crop;
          showFotoPrev(crop);
        });
      }).catch(function () {});
    }

    // Tier 1 (frente): código PDF417 en el navegador (gratis, offline). Si no
    // sale, o para el dorso (no tiene código), cae al OCR con IA (/api/ocr-dni).
    // Each side keeps its own outcome: a back photo that can't be read must
    // never cover the front that was (on-site tester 21/10 saw "No pude leer el DNI"
    // above fields the front had just filled).
    var scanSides = {};
    function scan(file, lado, btn, statusEl) {
      var setS = function (cls, txt) { statusEl.className = "sc-scan-status" + (cls ? " " + cls : ""); statusEl.textContent = txt; };
      var SIDE_TEXT = {
        frente: {
          ok: function () { return T("account.form.scan.front_ok", null, "Frente leído ✓ — revisá los datos abajo."); },
          fail: function () { return T("account.form.scan.front_fail", null, "No pude leer el frente. Probá con más luz y sin reflejos, o completá tus datos a mano abajo."); }
        },
        dorso: {
          ok: function () { return T("account.form.scan.back_ok", null, "Domicilio leído ✓ — revisalo abajo."); },
          fail: function () { return T("account.form.scan.back_fail", null, "No pude leer el domicilio del dorso. Probá con más luz o completalo a mano abajo."); }
        }
      };
      var paint = function () {
        var lines = ["frente", "dorso"].filter(function (s) { return scanSides[s]; }).map(function (s) {
          return { cls: scanSides[s], text: SIDE_TEXT[s][scanSides[s]]() };
        });
        statusEl.className = "sc-scan-status";
        statusEl.innerHTML = lines.map(function (l) { return '<span class="' + l.cls + '">' + esc(l.text) + '</span>'; }).join("<br>");
      };
      var ok = function () {
        scanSides[lado] = "ok"; paint();
        if (btn) btn.classList.add("done");
      };
      var fail = function () {
        // a re-read that fails keeps what an earlier photo already filled
        if (scanSides[lado] !== "ok") scanSides[lado] = "fail";
        paint();
      };
      // IA caída (no una foto ilegible): mensaje honesto + escondemos la tarjeta.
      var iaDown = function () { setS("err", T("account.form.scan.ai_down", null, "El lector automático no está disponible en este momento. Cargá los datos a mano abajo.")); var c = host.querySelector(".sc-scan"); if (c) c.style.display = "none"; };
      setS("", T("account.form.scan.reading", null, "Leyendo la foto…"));
      if (btn) btn.disabled = true;
      var done = function () { if (btn) btn.disabled = false; };

      var tier1 = lado === "frente"
        ? importStatic("platform/dni-barcode.mjs").then(function (m) { return m.readDni(file); }).catch(function () { return null; })
        : Promise.resolve(null);

      tier1.then(function (patient) {
        if (patient) {
          prefillFront(patient); ok(); done();
          // el barcode no sabe DÓNDE está el retrato: una llamada liviana al
          // OCR (lado "foto") devuelve sólo el recuadro y recortamos local.
          var duP = fileToDataUrl(file);
          fotoPerfilDesde(duP, duP.then(function (du) {
            return fetch("/api/ocr-dni", {
              method: "POST", credentials: "same-origin",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ lado: "foto", imageDataUrl: du })
            }).then(function (r) { return r.json(); })
              .then(function (j) { return j && j.ok ? j.bbox : null; });
          }));
          return;
        }
        // Tier 2: OCR
        var lastDataUrl = null;
        fileToDataUrl(file).then(function (dataUrl) {
          lastDataUrl = dataUrl;
          return fetch("/api/ocr-dni", {
            method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lado: lado, imageDataUrl: dataUrl })
          });
        }).then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (j) {
            if (lado === "frente" && j && j.ok && j.patient) {
              prefillFront(j.patient); ok();
              // el OCR del frente ya trae el recuadro del retrato
              if (j.bbox && lastDataUrl) fotoPerfilDesde(Promise.resolve(lastDataUrl), Promise.resolve(j.bbox));
            }
            else if (lado === "dorso" && j && j.ok && j.domicilio) { prefillBack(j.domicilio); ok(); }
            else if (j && j.motivo === "ia_no_disponible") iaDown();
            else fail();
            done();
          }).catch(function () {
            // Chrome/Firefox no decodifican HEIC en canvas: una foto de iPhone
            // recibe el remedio exacto, no el «no pude leer» de una foto mala.
            isHeicFile(file).then(function (heic) {
              if (heic) setS("err", T("account.form.scan.heic", null, "El navegador no puede leer fotos HEIC de iPhone. Sacá la foto con la cámara desde el botón, o guardala como JPG y volvé a elegirla."));
              else fail();
              done();
            });
          });
      });
    }

    function wireScan() {
      var statusEl = host.querySelector("#sc-scan-status");
      [["#sc-scan-f", "#sc-scan-f-file", "frente"], ["#sc-scan-d", "#sc-scan-d-file", "dorso"]].forEach(function (t) {
        var btn = host.querySelector(t[0]), inp = host.querySelector(t[1]);
        if (!btn || !inp) return;
        btn.addEventListener("click", function () { inp.click(); });
        inp.addEventListener("change", function () {
          var f = inp.files && inp.files[0];
          if (f) scan(f, t[2], btn, statusEl);
          inp.value = "";
        });
      });
      // Pegar (Cmd/Ctrl+V) o arrastrar una foto sobre la tarjeta del DNI la
      // lee como FRENTE (el dorso se pide con su botón). Un paste dirigido a
      // un campo de texto del formulario nunca llega acá.
      var card = host.querySelector(".sc-scan");
      if (!card || !statusEl) return;
      card.setAttribute("tabindex", "0");
      card.addEventListener("paste", function (ev) {
        var f = photoFileFromEvent(ev);
        if (!f) return;
        ev.preventDefault();
        scan(f, "frente", host.querySelector("#sc-scan-f"), statusEl);
      });
      card.addEventListener("dragover", function (ev) {
        if (eventCarriesFiles(ev)) ev.preventDefault();
      });
      card.addEventListener("drop", function (ev) {
        if (!ev || ev.defaultPrevented) return;
        var f = photoFileFromEvent(ev);
        if (eventCarriesFiles(ev)) ev.preventDefault();
        if (f) scan(f, "frente", host.querySelector("#sc-scan-f"), statusEl);
      });
    }

    function capture() {
      // guarda lo tipeado antes de re-render (para no perder el DNI al alternar)
      var dni = host.querySelector("#sc-dni");
      if (dni) {
        var raw = String(dni.value || "").trim();
        state.prefill.identifier = raw;
        if (raw.includes("@") || sinDniVisitante()) state.prefill.email = raw;
        else state.prefill.dni = raw;
      }
      var n = host.querySelector("#sc-nombre"); if (n) state.prefill.nombre = n.value;
      var a = host.querySelector("#sc-apellido"); if (a) state.prefill.apellido = a.value;
      var em = host.querySelector("#sc-email");
      if (em) {
        state.prefill.email = em.value.trim();
        if (state.mode === "login") state.prefill.identifier = state.prefill.email;
      }
      // The rest of the sign-up survives a swap too (TASK-50): phone and
      // birth date were lost on every round trip through login.
      var tel = host.querySelector("#sc-contacto"); if (tel) state.prefill.contacto = tel.value;
      var nac = host.querySelector("#sc-nac"); if (nac && nac.value) state.prefill.nacimiento = nac.value;
      var s = host.querySelector('#sc-sexo-seg input:checked'); if (s) state.prefill.sexo = s.value;
      var dm = readDom(); if (dm) state.prefill.domicilio = Object.assign({}, state.prefill.domicilio, dm);
      if (state.osItems) captureObras(host, state.osItems); // sobreviven al swap login/registro
    }

    function render() {
      host.innerHTML =
        state.mode === "otp" ? otpHtml() :
        state.mode === "login" ? loginHtml(state.prefill) :
        state.mode === "renaper" ? renaperHtml(state.prefill) :
        state.mode === "portal" ? portalHtml() :
        state.mode === "recuperar" ? recuperarHtml(state.prefill) :
        regHtml(state.prefill);
      if (state.onMode) state.onMode(state.mode);
      wire();
      renderTurnstile();
    }

    function wire() {
      var form = host.querySelector("#sc-form");
      var dniEl = host.querySelector("#sc-dni");
      var telEl = host.querySelector("#sc-contacto");
      var nacEl = host.querySelector("#sc-nac");
      var echo = host.querySelector("#sc-nac-echo");
      var swap = host.querySelector("#sc-swap");
      var forgot = host.querySelector("#sc-forgot");
      var msg = host.querySelector("#sc-msg");
      var btn = host.querySelector("#sc-submit");
      if (state.mode === "otp") { wireOtp(); return; }
      var passEl = wirePassword(host);

      host.querySelectorAll(".sc-provider[data-provider]").forEach(function (providerButton) {
        providerButton.addEventListener("click", function () {
          var provider = providerButton.getAttribute("data-provider") || "mi_argentina";
          openIdentityProvider(provider);
        });
      });

      if (dniEl) dniEl.addEventListener("input", function () {
        if (state.mode === "registro") armPuco(dniEl); // acepta puntos/divisores; el padrón normaliza en background
      });
      if (dniEl && state.mode === "registro") {
        dniEl.addEventListener("blur", function () { dniEl.value=fmtDni(dniEl.value); armPuco(dniEl, true); });
        armPuco(dniEl, true); // DNI ya prefillado (wizard / localStorage): consultar ya
      }
      if (telEl) telEl.addEventListener("blur", function () { if (telEl.value) telEl.value = normTel(telEl.value); });

      // delegado en host (una sola vez): sobrevive a los re-render de render()
      // y de renderOsSec() sin re-wirear cada input de cobertura.
      if (!host._cobSync) {
        host._cobSync = true;
        host.addEventListener("input", function () { syncCoberturaRows(host); });
      }
      if (state.mode === "registro" && !sinDniVisitante()) renderOsSec();
      if (state.mode === "registro") {
        // La foto/escaneo de DNI y el padrón (PUCO) no existen para el
        // visitante sin DNI; el domicilio (y su combo de localidad) queda.
        if (!sinDniVisitante()) { wireScan(); hideScanIfDown(); }
        wireLocalidad(host);
      }
      // un swap login↔registro re-arma el DOM: la vista previa vuelve del state
      if (state.mode === "registro" && state.fotoPerfil) showFotoPrev(state.fotoPerfil);

      var dob = wireDob(nacEl, echo);
      host._dob = dob; // prefillFront() lo usa para pintar en palabras tras el OCR

      var calBtn = host.querySelector("#sc-calbtn");
      var calHost = host.querySelector("#sc-cal-host");
      if (calBtn && calHost && nacEl) {
        var cal = attachCalendar(calHost, nacEl, calBtn, function () { dob.commit(); });
        calBtn.addEventListener("click", function () { cal.toggle(); });
      }

      if (swap) swap.addEventListener("click", function (e) {
        e.preventDefault();
        capture();
        state.mode = state.mode === "login" ? "registro" : "login";
        render();
      });
      if (forgot) forgot.addEventListener("click", function (e) {
        e.preventDefault();
        capture();
        state.mode = "recuperar";
        render();
      });

      function say(t, kind) {
        if (!msg) return;
        msg.className = "sc-msg show " + (kind === "err" ? "err" : "ok");
        msg.textContent = t;
      }

      if (form) form.addEventListener("submit", async function (e) {
        e.preventDefault();
        if (btn && btn.disabled) return;
        // Recovery accepts either identifier in every locale; no credentials
        // are carried from the previous form or included in this request.
        if (state.mode === "recuperar") {
          var valor = String(dniEl ? dniEl.value : "").trim();
          if (!valor) {
            say(T("account.form.recover.identifier_required", null, "Ingresá tu DNI o el email de tu cuenta."), "err");
            return;
          }
          var porEmail = valor.indexOf("@") !== -1;
          var dniRec = normDni(valor);
          if (porEmail && !emailValida(valor)) {
            say(T("account.form.error.invalid_email", null, "Email inválido."), "err");
            return;
          }
          if (!porEmail && (dniRec.length < 7 || dniRec.length > 9)) {
            say(T("account.form.error.dni_length", null, "El DNI tiene que tener 7 a 9 dígitos."), "err");
            return;
          }
          if (btn) btn.disabled = true;
          say(T("account.form.recover.sending", null, "Enviando…"), "ok");
          fetch("/api/account/recover", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            // El link del correo vuelve adonde se pidió (la reserva a medio
            // hacer), aunque se abra en otro navegador (responsable del producto, 2026-10-25).
            body: JSON.stringify(Object.assign(porEmail ? { email: valor } : { dni: dniRec },
              returnPath() !== "/account" ? { next: returnPath() } : {}))
          }).then(function () {
            say(porEmail
              ? T("account.form.recover.sent_email", null, "Si ese email corresponde a una cuenta, te llegó un link para elegir una contraseña nueva.")
              : T("account.form.recover.sent", null, "Si ese DNI tiene un email registrado, te llegó un link para elegir una contraseña nueva."), "ok");
          }).catch(function () {
            say(T("account.form.error.network", null, "Error de red. Probá de nuevo."), "err");
          }).finally(function () {
            if (btn) btn.disabled = false;
          });
          return;
        }
        // Identidad por idioma (responsable del producto, 2026-09-11): el visitante sin DNI se
        // identifica con el email; el español sigue el flujo de siempre.
        var loginEmail = state.mode === "login" && dniEl && String(dniEl.value).includes("@");
        var sinDni = sinDniVisitante() || loginEmail;
        var dni = loginEmail ? "" : normDni(dniEl ? dniEl.value : "");
        var email = "";
        if (sinDni) {
          email = String(loginEmail ? dniEl.value : (host.querySelector("#sc-email") || {}).value || "").trim();
          if (!emailValida(email)) {
            say(T("account.form.error.email_required", null, "Dejá tu email para crear la cuenta."), "err");
            return;
          }
        }
        else if (dni.length < 7 || dni.length > 9) { say(T("account.form.error.dni_length", null, "El DNI tiene que tener 7 a 9 dígitos."), "err"); return; }

        var pass = (passEl || {}).value || "";
        if (state.mode === "registro" && pass.length < 8) { say(T("account.form.password.min_length_error", null, "La contraseña tiene que tener al menos 8 caracteres."), "err"); return; }

        var needsTurnstile = state.mode === "login" || state.mode === "renaper";
        var tsToken = "";
        if (needsTurnstile && state.tsSitekey) {
          if (btn && btn.disabled) return;
          if (btn) btn.disabled = true;
          try {
            await renderTurnstile();
            if (!TS) throw new Error("turnstile");
            tsToken = await TS.requireToken({onWait:function() {
              say(T("account.form.verifying", null, "Verificando…"), "ok");
            }});
          } catch(e) {
            if (btn) btn.disabled = false;
            say(T("account.form.error.turnstile_failed", null, "No se pudo verificar que no sos un bot. Reintentá."), "err");
            resetTurnstile();
            return;
          }
        }

        var payload = { turnstile_token: tsToken };
        if (sinDni) payload.email = email;
        else payload.dni = dni;
        if (state.mode !== "renaper") payload.password = pass;
        if (state.mode === "registro") {
          payload.nombre = (host.querySelector("#sc-nombre") || {}).value ? host.querySelector("#sc-nombre").value.trim() : "";
          payload.apellido = (host.querySelector("#sc-apellido") || {}).value ? host.querySelector("#sc-apellido").value.trim() : "";
          var sexoEl = host.querySelector('#sc-sexo-seg input:checked');
          payload.sexo = sexoEl ? sexoEl.value : "";
          payload.contacto = normTel((host.querySelector("#sc-contacto") || {}).value || "");
          payload.email = (host.querySelector("#sc-email") || {}).value ? host.querySelector("#sc-email").value.trim() : "";
          if (!payload.contacto && !payload.email) {
            say(T("account.form.hint.contact_required", null, "Dejá al menos un teléfono o un email: los necesitamos para avisarte de tus turnos."), "err");
            return;
          }
          if (nacEl && nacEl.value.trim()) {
            var iso = nacEl.dataset.iso || parseFlexibleDate(nacEl.value);
            if (!iso) { say(T("account.form.error.dob_invalid", null, "Revisá la fecha de nacimiento (DD/MM/AAAA)."), "err"); return; }
            payload.nacimiento = iso;
          }
          // cobertura: las del padrón mandan (el server las re-confirma contra
          // su propio cache PUCO); lo tipeado a mano va como declarado. El nº
          // de afiliado siempre sale del input (el padrón no lo trae).
          var cob = leerCobertura(host, state.osItems || []);
          if (cob.obras_sociales.length || cob.prepaga || cob.declarada_sin_cobertura) payload.cobertura = cob;
          var dom = readDom();
          if (dom) payload.domicilio = dom;
        }

        // Preserve the legacy registration POST byte-for-byte unless the
        // server explicitly requires a verified phone. A registration with
        // email only still follows the ordinary path even when the flag is on.
        if (state.mode === "registro" && state.otpRequired && payload.contacto) {
          startRegistrationOtp(payload, say, btn);
          return;
        }

        if (btn) btn.disabled = true;
        say(state.mode === "registro" ? T("account.form.status.creating_account", null, "Creando tu cuenta…")
          : state.mode === "renaper" ? T("account.form.status.confirming_identity", null, "Confirmando tu identidad…")
          : T("account.form.status.signing_in", null, "Ingresando…"), "ok");
        var submittedIdentifier = sinDni ? email : String(dniEl.value).trim();
        var url = state.mode === "registro" ? "/api/account/register" : "/api/account/login";
        fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, ok: r.ok, j: j }; });
        }).then(function (res) {
          if (res.ok && res.j && res.j.ok && (res.j.redirect === "/admin" || res.j.redirect === "/admin/login?staff=1")) {
            window.location.assign(res.j.redirect);
            return;
          }
          if (res.ok && res.j && res.j.usuario) {
            mirror(res.j.usuario);
            // la sesión recién abierta ya viaja en la cookie: subimos la foto
            // recortada del DNI en background (best-effort, no frena el alta)
            if (state.mode === "registro" && state.fotoPerfil) {
              try {
                fetch("/api/account/photo", {
                  method: "POST", credentials: "same-origin",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ data_b64: state.fotoPerfil })
                }).catch(function () {});
              } catch (e) {}
            }
            state.onDone(res.j.usuario);
            return;
          }
          if (btn) btn.disabled = false;
          resetTurnstile(); // el token es de un solo uso: cualquier intento fallido lo quema
          if (res.status === 409 && url === "/api/account/register") {
            showRegistrationConflict(submittedIdentifier);
            return;
          }
          if (res.status === 401) { say(T("account.form.error.bad_credentials", null, "DNI o contraseña incorrectos."), "err"); return; }
          if (res.j && res.j.error === "contacto_o_email_requerido") {
            say(T("account.form.hint.contact_required", null, "Dejá al menos un teléfono o un email: los necesitamos para avisarte de tus turnos."), "err");
            return;
          }
          if (res.j && res.j.error === "turnstile") {
            say(T("account.form.error.turnstile_failed", null, "No se pudo verificar que no sos un bot. Reintentá."), "err");
            return;
          }
          if (res.j && res.j.error === "renaper_no_configurado") {
            say(T("account.form.error.renaper_unavailable", null, "La verificación de identidad no está disponible. Probá más tarde."), "err");
            return;
          }
          say((res.j && res.j.error) || T("account.form.error.generic", null, "No pudimos completar la operación. Probá de nuevo."), "err");
        }).catch(function () {
          if (btn) btn.disabled = false;
          say(T("account.form.error.network", null, "Error de red. Probá de nuevo."), "err");
        });
      });
    }

    function mirror(u) {
      try {
        // Replace the identity projection, including server-cleared fields.
        var cur = {};
        ["dni", "identity_kind", "nombre", "apellido", "nacimiento", "sexo", "contacto", "email"].forEach(function (key) {
          if (Object.prototype.hasOwnProperty.call(u, key)) cur[key] = u[key];
        });
        localStorage.setItem("st_identity", JSON.stringify(cur));
      } catch (e) {}
    }

    fetchConfig().then(function (cfg) {
      state.tsSitekey = (cfg && cfg.turnstile_sitekey) || null;
      state.otpRequired = !!(cfg && cfg.otp_registro_requerido);
      state.authMode = cfg && cfg.auth_mode === "renaper" ? "renaper" : "password";
      state.identityProvider = (cfg && cfg.renaper_provider) || "real";
      state.ayuda = {
        whatsapp: (cfg && cfg.ayuda_whatsapp) || "",
        whatsappTexto: (cfg && cfg.ayuda_whatsapp_texto) || "",
        centro: (cfg && cfg.centro_principal) || null
      };
      if (cfg && cfg.auth_mode === "portal") {
        state.authMode = "portal";
        state.mode = "portal";
        state.portal = {
          loginUrl: cfg.portal_login_url || "",
          identidad: !!cfg.portal_identidad,
          ayudaWhatsapp: cfg.ayuda_whatsapp || "",
          ayudaTexto: cfg.ayuda_whatsapp_texto || "",
          centro: cfg.centro_principal || null
        };
        if (!state.portal.identidad && state.portal.loginUrl &&
            window.location.pathname === "/account" && portalTripAllowed()) {
          window.location.replace(portalEntryHref());
          return;
        }
      }
      if (state.authMode === "renaper") {
        state.mode = "renaper";
        // En /account el login ES el SSO: ir derecho, sin pantalla intermedia
        // (el flujo del SSO real también redirige). En superficies embebidas
        // (wizard) se muestra el botón para no perder el contexto de la página.
        if (window.location.pathname === "/account") {
          window.location.replace(ssoUrl("renaper"));
          return;
        }
      }
      render(); // una sola pintura cuando ya conocemos el modo configurado
      renderTurnstile();
    });
  }

  // ---------- edición de la cuenta ya logueada (autosave, sin botón Guardar) ----------
  // renderEditar(host, { usuario, onSave(usuarioActualizado) }).
  // El DNI se muestra fijo (no editable — es la clave de login/HSI). Cada campo
  // se guarda solo al perder foco (form-design: sin Cancelar, auto-save).
  function renderEditar(host, opts) {
    if (!host) return;
    opts = opts || {};
    injectStyles();
    var usuario = opts.usuario || {};
    var onSave = typeof opts.onSave === "function" ? opts.onSave : function () {};
    // La pantalla no muestra nunca la clave sintética (responsable del producto, 2026-09-11: un
    // número inventado no se le enseña a una persona) y el visitante no
    // hispanohablante no edita un DNI que nunca cargó — el campo desaparece
    // en ambos casos, junto con la tarjeta de cobertura (obra social /
    // prepaga / nº de afiliado) que este visitante no vio al registrarse.
    var sinDni = sinDniVisitante() || usuario.identity_kind === "synthetic";
    // afterGuardar: qué hacer tras el submit explícito de "Guardar cambios"
    // (no el autosave-on-blur). Por defecto navega a /my-requests — pero
    // renderEditar hoy sólo se usa EMBEBIDO en el riel de /account, así que
    // el dueño (accounts.py) pasa un callback que cambia de panel en vez de
    // sacar a la persona del dashboard (perdía el riel entero, 2026-07-17).
    var afterGuardar = typeof opts.afterGuardar === "function"
      ? opts.afterGuardar : function () { window.location.href = "/my-requests"; };
    var cobertura = usuario.cobertura || {};
    var osItems = coverageItems(cobertura, true);
    osItems.withNone = true;
    // Avisos: default whatsapp+email on. SMS se sacó de la lista (responsable del producto
    // 2026-07-17: no había forma real de mandar SMS, era un canal fantasma).
    var av = usuario.avisos || {};
    function avOn(k, d) { return (k in av) ? !!av[k] : d; }
    function swBtn(id, on, label) {
      return '<button type="button" role="switch" class="sc-sw' + (on ? " on" : "") +
        '" id="' + id + '" aria-checked="' + (on ? "true" : "false") +
        '" aria-label="' + esc(label) + '"><i></i></button>';
    }
    function avRow(icBg, iconSvg, name, sub, swId, on) {
      var plano = name.replace(/<[^>]+>/g, "").trim();
      return '<div class="sc-av">' +
        '<span class="sc-av-ic" style="background:' + icBg + ';">' + iconSvg + '</span>' +
        '<div class="sc-av-tx"><div class="sc-av-name">' + name + '</div>' +
          '<div class="sc-av-sub">' + sub + '</div></div>' +
        swBtn(swId, on, T("account.form.editor.notify_aria", { name: plano }, "Avisos por " + plano)) + '</div>';
    }
    // WhatsApp: el logo de marca (glyph relleno de Icons8, id 16733) en verde
    // WhatsApp, para que se reconozca de una — no un globo de diálogo genérico
    // (responsable del producto 2026-07-18: "usá el ícono de WhatsApp, no el de call-out").
    var IC_WA = scIco("whatsapp-sf-regular.svg", 24, "#1f9d55");
    var IC_MAIL = scIco("email-sf-regular.svg", 20, "#5c6b82");

    // Cada tarjeta cierra con su propio "Guardar cambios" + estado (responsable del producto
    // 2026-07-18: la gente espera el botón; sin él se sorprende y teme perder
    // lo cargado si cierra la ventana). Los campos igual se autoguardan al
    // salir de cada uno; el botón es un flush explícito + confirmación, sin
    // sacar a nadie de la pantalla.
    function actionsRow() {
      return '<div class="sc-actions"><button class="sc-cta sc-guardar" type="button">' +
        T("account.form.editor.save_changes", null, "Guardar cambios") + '</button><span class="sc-save"></span></div>';
    }

    // Tres tarjetas separadas como en el mockup (Datos personales / Cobertura /
    // Contacto), no un formulario plano — un solo <form> las envuelve para que
    // el submit y el autosave sigan funcionando con los mismos IDs. Foto,
    // domicilio y la sección multi-obra (padrón PUCO) vienen del form clásico:
    // misma funcionalidad, presentada dentro de las tarjetas.
    host.innerHTML =
      '<div class="sc-auth">' +
      '<form id="sc-form" autocomplete="on" novalidate>' +
      '<section class="sc-card" data-panel="datos">' +
        cardHead(T("account.form.editor.card.personal_title", null, "Datos personales")) +
        '<div class="sc-card-body">' +
        // DNI editable: es la clave de login/HSI, pero si alguien lo guardó
        // con un error corregirlo a mano evita un dolor de cabeza mayor (responsable del producto
        // 2026-07-18). El server cambia la clave y arrastra sesión, turnos y
        // personas a cargo al DNI nuevo. Oculto para la clave sintética y
        // para el visitante sin DNI (ver sinDni arriba).
        (sinDni ? "" : field2(T("account.form.field.dni", null, "DNI"), '<input class="sc-in sc-dni" id="sc-dni" name="dni" inputmode="numeric" autocomplete="off" value="' + esc(fmtDni(usuario.dni)) + '">', { forId: "sc-dni" })) +
        '<div class="sc-grid2">' +
          field2(T("account.form.field.first_name", null, "Nombre"), '<input class="sc-in" id="sc-nombre" name="nombre" autocomplete="given-name" value="' + esc(usuario.nombre || "") + '">', { forId: "sc-nombre" }) +
          field2(T("account.form.field.last_name", null, "Apellido"), '<input class="sc-in" id="sc-apellido" name="apellido" autocomplete="family-name" value="' + esc(usuario.apellido || "") + '">', { forId: "sc-apellido" }) +
        "</div>" +
        '<div class="sc-grid2">' +
          field2(T("account.form.field.dob", null, "Nacimiento"),
            '<div class="sc-dob-wrap">' +
            '<input class="sc-in sc-dob" id="sc-nac" name="nacimiento" inputmode="numeric" autocomplete="bday" placeholder="' + T("account.form.field.dob_placeholder", null, "DD/MM/AAAA") + '" value="' + esc(usuario.nacimiento ? isoToArg(usuario.nacimiento) || usuario.nacimiento : "") + '">' +
            '<button type="button" class="sc-calbtn" id="sc-calbtn" aria-label="' + T("account.form.field.dob_choose_calendar", null, "Elegir en el calendario") + '">' + CAL_ICON + "</button>" +
            "</div>" +
            '<div id="sc-cal-host"></div>' +
            '<span class="sc-echo" id="sc-nac-echo"></span>', { forId: "sc-nac" }) +
          field2(T("account.form.field.sex", null, "Sexo <span style=\"font-weight:400\">(según tu DNI)</span>"),
            '<div class="sc-seg" id="sc-sexo-seg" role="radiogroup" aria-labelledby="sc-sexo-label">' +
            segOpt("F", T("account.form.field.sex_female", null, "Femenino"), usuario.sexo) +
            segOpt("M", T("account.form.field.sex_male", null, "Masculino"), usuario.sexo) +
            segOpt("X", T("account.form.field.sex_other", null, "Otro"), usuario.sexo) +
            "</div>", { labelId: "sc-sexo-label" }) +
        "</div>" +
        field2(T("account.form.address.label", null, "Domicilio"), '<input class="sc-in sc-wide" id="sc-dom-calle" name="domicilio" autocomplete="street-address" placeholder="' + T("account.form.address.placeholder", null, "Calle y número") + '" value="' + esc(domLine(usuario.domicilio)) + '">', { forId: "sc-dom-calle" }) +
        '<div class="sc-grid2">' +
          field2(T("account.form.address.city_label", null, "Localidad"), comboHtml("sc-dom-loc", "Pinamar, Cariló…", (usuario.domicilio || {}).localidad || ""), { forId: "sc-dom-loc" }) +
          field2(T("account.form.address.postal_label", null, "Código postal"), '<input class="sc-in sc-cp" id="sc-dom-cp" inputmode="numeric" autocomplete="postal-code" placeholder="7167" value="' + esc((usuario.domicilio || {}).cp || "") + '">', { forId: "sc-dom-cp" }) +
        "</div>" +
        actionsRow() +
        "</div>" +
      "</section>" +
      (sinDni ? "" : '<section class="sc-card" data-panel="cobertura">' +
        cardHead(T("account.form.editor.card.coverage_title", null, "Cobertura"), T("account.form.editor.card.coverage_sub", null, "Si tenés obra social o prepaga, la usamos para agilizar la facturación de tus turnos.")) +
        '<div class="sc-card-body">' +
        '<div id="sc-os-sec"></div>' +
        actionsRow() +
        "</div>" +
      "</section>") +
      '<section class="sc-card" data-panel="contacto">' +
        cardHead(T("account.form.editor.card.contact_title", null, "Contacto y avisos"), T("account.form.editor.card.contact_sub", null, "Dejá al menos un WhatsApp o un email: los necesitamos para avisarte de tus turnos. Se guarda solo, al salir de cada campo.")) +
        '<div class="sc-card-body">' +
        '<div class="sc-grid2">' +
          field2(T("account.form.field.whatsapp", null, "WhatsApp"), '<input class="sc-in sc-tel" id="sc-contacto" name="contacto" inputmode="tel" autocomplete="tel" placeholder="2254 550000" value="' + esc(usuario.contacto || "") + '">', { forId: "sc-contacto" }) +
          field2(T("account.form.field.email", null, "Email"), '<input class="sc-in sc-wide" id="sc-email" name="email" type="email" autocomplete="email" placeholder="' + T("account.form.field.email_placeholder", null, "vos@ejemplo.com") + '" value="' + esc(usuario.email || "") + '">', { forId: "sc-email" }) +
        "</div>" +
        '<div class="sc-avisos">' +
          '<div class="sc-av-h">' + T("account.form.editor.notify_heading", null, "Cómo querés que te avisemos") + '</div>' +
          '<p class="sc-av-p">' + T("account.form.editor.notify_sub", null, "Solo te escribimos por tus turnos y resultados. Elegí por dónde.") + '</p>' +
          '<div class="sc-av-list">' +
            avRow("#e3f7ea", IC_WA,
              T("account.form.editor.notify_whatsapp_label", null, 'WhatsApp <span class="sc-av-tag">RECOMENDADO</span>'),
              T("account.form.editor.notify_whatsapp_sub", null, "Al número que dejaste arriba"), "sc-av-wa", avOn("whatsapp", true)) +
            avRow("#eef1f5", IC_MAIL, T("account.form.field.email", null, "Email"),
              T("account.form.editor.notify_email_sub", null, "Al mail que dejaste arriba"), "sc-av-email", avOn("email", true)) +
          '</div>' +
        '</div>' +
        actionsRow() +
        "</div>" +
      "</section>" +
      "</form>" +
      "</div>";

    var form = host.querySelector("#sc-form");
    var nacEl = host.querySelector("#sc-nac");
    var echo = host.querySelector("#sc-nac-echo");
    var telEl = host.querySelector("#sc-contacto");
    var dniEl = host.querySelector("#sc-dni");
    var saveEls = host.querySelectorAll(".sc-save");
    var lastSaved = null;

    var dob = wireDob(nacEl, echo);

    var calBtn = host.querySelector("#sc-calbtn");
    var calHost = host.querySelector("#sc-cal-host");
    if (calBtn && calHost && nacEl) {
      var cal = attachCalendar(calHost, nacEl, calBtn, function () { dob.commit(); guardar(); });
      calBtn.addEventListener("click", function () { cal.toggle(); });
    }

    if (telEl) telEl.addEventListener("blur", function () { if (telEl.value) telEl.value = normTel(telEl.value); guardar(); });

    // El estado se refleja en las tres tarjetas a la vez (sólo se ve la activa),
    // así el botón de cada panel confirma "Guardado" donde el usuario mira.
    function sayGuardado(t, kind) {
      for (var i = 0; i < saveEls.length; i++) {
        saveEls[i].className = "sc-save " + (kind === "err" ? "err" : "ok");
        saveEls[i].textContent = t;
      }
    }
    // Nothing is said on load: "Todo guardado" beside "Guardar cambios" read as a
    // contradiction (review 2026-11-15, item 16). The status appears after a save.

    function payload() {
      var p = {
        nombre: (host.querySelector("#sc-nombre") || {}).value || "",
        apellido: (host.querySelector("#sc-apellido") || {}).value || "",
        contacto: normTel((host.querySelector("#sc-contacto") || {}).value || ""),
        email: (host.querySelector("#sc-email") || {}).value || ""
      };
      // DNI: sólo se manda si viene con formato válido (7-9 dígitos). Si está a
      // medio corregir, se omite del payload y el server conserva el actual —
      // nunca se guarda un DNI trunco.
      if (dniEl) { var nd = normDni(dniEl.value); if (nd.length >= 7 && nd.length <= 9) p.dni = nd; }
      var sexoEl = host.querySelector('#sc-sexo-seg input:checked');
      p.sexo = sexoEl ? sexoEl.value : "";
      p.nacimiento = "";
      if (nacEl && nacEl.value.trim()) {
        var iso = nacEl.dataset.iso || parseFlexibleDate(nacEl.value);
        if (!iso) return null; // fecha tipeada pero todavía inválida: no guardar
        p.nacimiento = iso;
      }
      // cobertura: siempre presente aunque quede vacía (borrar la prepaga o la
      // última obra social también es un cambio; el server sólo conserva lo
      // guardado cuando la key falta). Las obras del padrón se re-mandan tal
      // cual el nombre — el server las matchea con las guardadas y conserva
      // origen 'puco' + rnos. El visitante sin DNI no ve la tarjeta: la key
      // NO viaja, para que un autosave suyo no borre una cobertura ya guardada
      // ("obras_sociales: []" con la key presente ES un borrado deliberado).
      if (!sinDni) {
        p.cobertura = leerCobertura(host, osItems);
        if (usuario.coverage_revision) p.coverage_revision = usuario.coverage_revision;
      }
      // domicilio: siempre presente (borrarlo también es un cambio; el server
      // sólo conserva lo guardado cuando la key falta). Provincia conservada
      // de lo guardado si el form no la muestra.
      var g = function (id) { var e = host.querySelector(id); return e && e.value ? e.value.trim() : ""; };
      var calle = g("#sc-dom-calle"), loc = g("#sc-dom-loc"), cp = g("#sc-dom-cp");
      var dom = {};
      if (calle) dom.calle = calle;
      if (loc) dom.localidad = loc;
      if (cp) dom.cp = cp;
      var provG = (usuario.domicilio || {}).provincia;
      if (provG) dom.provincia = provG;
      if (calle || loc || cp) dom.texto = [calle, loc, cp ? "(" + cp + ")" : ""].filter(Boolean).join(", ");
      p.domicilio = dom;
      // avisos: preferencia de canales (whatsapp/email)
      function swOn(id) { var e = host.querySelector(id); return !!(e && e.classList.contains("on")); }
      p.avisos = { whatsapp: swOn("#sc-av-wa"), email: swOn("#sc-av-email") };
      return p;
    }

    // redirect:true sólo en el click/submit explícito de "Guardar" — el
    // autosave-on-blur de cada campo NO navega, sería sacar a alguien de la
    // pantalla porque tocó otro campo (responsable del producto 2026-07-11).
    // forceMsg:true (click en "Guardar cambios") confirma "Todo guardado" aunque
    // no haya nada nuevo — el botón siempre da feedback, no un silencio raro.
    function guardar(redirect, forceMsg) {
      var p = payload();
      if (!p) {
        // payload() returned null (likely invalid date) — clear the success message
        // to avoid showing "Todo guardado" when there's a validation error
        sayGuardado("", "ok");
        return;
      }
      var key = JSON.stringify(p);
      if (key === lastSaved) { if (redirect) afterGuardar(); else if (forceMsg) sayGuardado(T("account.form.editor.saved_all", null, "Todo guardado"), "ok"); return; }
      sayGuardado(T("account.form.editor.saving", null, "Guardando…"), "ok");
      fetch("/api/account/update", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(p)
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
      }).then(function (res) {
        if (res.ok && res.j && res.j.usuario) {
          usuario.coverage_revision = res.j.usuario.coverage_revision;
          lastSaved = key;
          sayGuardado(T("account.form.editor.saved", null, "Guardado"), "ok");
          onSave(res.j.usuario);
          if (redirect) afterGuardar();
          return;
        }
        if (res.j && res.j.error === "contacto_o_email_requerido") {
          sayGuardado(T("account.form.editor.contact_required", null, "Dejá al menos un teléfono o un email para poder avisarte de tus turnos."), "err");
          return;
        }
        if (res.j && res.j.error === "dni_en_uso") {
          sayGuardado(T("account.form.editor.dni_in_use", null, "Ese DNI ya está registrado en otra cuenta. Revisalo."), "err");
          return;
        }
        if (res.j && (res.j.error === "dni_invalido" || res.j.error === "DNI inválido (7 a 9 dígitos)")) {
          sayGuardado(T("account.form.error.dni_length", null, "El DNI tiene que tener 7 a 9 dígitos."), "err");
          return;
        }
        sayGuardado((res.j && res.j.error) || T("account.form.editor.save_failed", null, "No pudimos guardar. Probá de nuevo."), "err");
      }).catch(function () {
        sayGuardado(T("account.form.error.network", null, "Error de red. Probá de nuevo."), "err");
      });
    }

    // sección de obras sociales: se re-arma al agregar una fila; los inputs
    // nuevos se re-wirean ahí (blur → autosave)
    function renderOsSec() {
      var sec = host.querySelector("#sc-os-sec");
      if (!sec) return;
      host._osItems = osItems;
      sec.innerHTML = osSectionHtml(osItems, "");
      wireOsSection(host, osItems, { rerender: renderOsSec, onSave: function () { guardar(false); } });
    }
    renderOsSec();

    host.querySelectorAll("#sc-nombre,#sc-apellido,#sc-email,#sc-dom-calle,#sc-dom-loc,#sc-dom-cp").forEach(function (el) {
      el.addEventListener("blur", function () { guardar(false); });
    });
    host.addEventListener("input", function () { syncCoberturaRows(host); });
    wireLocalidad(host, function () { guardar(false); });
    host.querySelectorAll("#sc-sexo-seg input").forEach(function (el) {
      el.addEventListener("change", function () { guardar(false); });
    });
    if (nacEl) nacEl.addEventListener("blur", function () { guardar(false); });

    // DNI: acepta puntos, espacios o guiones; se normaliza al guardar y se
    // vuelve a mostrar con puntos al salir del campo.
    if (dniEl) {
      dniEl.addEventListener("blur", function () { dniEl.value=fmtDni(dniEl.value); guardar(false); });
    }

    // "Guardar cambios" de cada tarjeta: flush explícito + confirmación, sin
    // navegar (todos los campos ya se autoguardan al salir de cada uno).
    host.querySelectorAll(".sc-guardar").forEach(function (b) {
      b.addEventListener("click", function () { guardar(false, true); });
    });

    // Toggles de avisos: flip inmediato + autosave.
    ["#sc-av-wa", "#sc-av-email"].forEach(function (sel) {
      var b = host.querySelector(sel);
      if (!b) return;
      b.addEventListener("click", function () {
        var on = b.classList.toggle("on");
        b.setAttribute("aria-checked", on ? "true" : "false");
        guardar(false);
      });
    });

    // Enter en cualquier campo: guarda y confirma, sin navegar (los botones son
    // type=button; el submit sólo lo dispara Enter).
    if (form) form.addEventListener("submit", async function (e) { e.preventDefault(); guardar(false, true); });
  }

  // ---------- punto de vuelta (`next`) ----------
  // Adónde vuelve la persona después de entrar, de crear la cuenta, del SSO o
  // del link de recuperación: la MISMA página que la mandó (p.ej. la reserva a
  // medio hacer, con su ?slot=). Sólo un camino de este sitio: "//host" y
  // "/\host" (el navegador lee la barra invertida como "/") son otro host.
  function returnPath() { return window.location.pathname + window.location.search; }
  function safeNext(v) {
    v = String(v || "");
    if (v.charAt(0) !== "/" || v.charAt(1) === "/" || v.indexOf("\\") !== -1 || /[\u0000-\u001f\s]/.test(v)) return "";
    return v;
  }

  // ---------- reset de contraseña (link del email de recuperación) ----------
  function renderReset(host, opts) {
    if (!host) return;
    opts = opts || {};
    injectStyles();
    var token = opts.token;
    var next = safeNext(opts.next);
    var pedirNuevo = "/account" + (next ? "?mode=login&next=" + encodeURIComponent(next) : "");
    var onDone = typeof opts.onDone === "function" ? opts.onDone : function () {};

    host.innerHTML =
      '<div class="sc-authform">' +
      '<form id="sc-form" autocomplete="on" novalidate>' +
      '<div class="sc-row"><label for="sc-pass">' + T("account.form.password.new_label", null, "Contraseña nueva") + '</label><div class="sc-field">' +
      '<div class="sc-pass-wrap">' +
      '<input class="sc-in" id="sc-pass" name="password" type="password" autocomplete="new-password">' +
      '<button type="button" class="sc-eyebtn" id="sc-eyebtn" aria-label="' + T("account.form.password.show", null, "Mostrar contraseña") + '">' + EYE_ICON + "</button>" +
      "</div>" +
      '<button type="button" class="sc-genbtn" id="sc-genbtn">' + T("account.form.password.generate", null, "O generá una automáticamente") + '</button>' +
      "</div></div>" +
      '<button class="sc-cta" id="sc-submit" type="submit">' + T("account.form.reset.submit", null, "Actualizar contraseña") + '</button>' +
      '<div class="sc-msg" id="sc-msg"></div>' +
      "</form>" +
      "</div>";

    var form = host.querySelector("#sc-form");
    var msg = host.querySelector("#sc-msg");
    var btn = host.querySelector("#sc-submit");
    var passEl = wirePassword(host);

    function say(t, kind) {
      if (!msg) return;
      msg.className = "sc-msg show " + (kind === "err" ? "err" : "ok");
      msg.textContent = t;
    }

    if (form) form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var pass = (passEl || {}).value || "";
      if (pass.length < 8) { say(T("account.form.password.min_length_error", null, "La contraseña tiene que tener al menos 8 caracteres."), "err"); return; }
      if (btn) btn.disabled = true;
      say(T("account.form.reset.updating", null, "Actualizando…"), "ok");
      fetch("/api/account/reset", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token, password: pass })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, ok: r.ok, j: j }; });
      }).then(function (res) {
        if (res.ok && res.j && res.j.usuario) { onDone(res.j.usuario); return; }
        if (btn) btn.disabled = false;
        if (res.j && res.j.error === "token_invalido") {
          host.innerHTML = '<p style="font-size:16px;color:var(--danger,#a3352f);">' + T("account.form.reset.link_expired", null, "Este link venció o ya se usó. ") +
            '<a href="' + esc(pedirNuevo) + '" style="color:var(--accent,#0e8b50);font-weight:600;">' + T("account.form.reset.request_new", null, "Pedí uno nuevo") + '</a>.</p>';
          return;
        }
        say((res.j && res.j.error) || T("account.form.error.generic", null, "No pudimos completar la operación. Probá de nuevo."), "err");
      }).catch(function () {
        if (btn) btn.disabled = false;
        say(T("account.form.error.network", null, "Error de red. Probá de nuevo."), "err");
      });
    });
  }

  // DOB reutilizable: el MISMO control que Datos personales (texto flexible +
  // calendario año-primero + eco en palabras). Lo consume platform/dependents.mjs para que
  // "Personas a cargo" tenga la misma forma y funcione igual (responsable del producto 2026-07-18).
  // opts = {input, echo?, calBtn?, calHost?, onCommit?}. Idempotente en estilos.
  function dobField(opts) {
    opts = opts || {};
    injectStyles();
    var input = opts.input, echo = opts.echo, calBtn = opts.calBtn, calHost = opts.calHost;
    if (!input) return { commit: function () {} };
    var dob = wireDob(input, echo);
    if (calBtn && calHost) {
      var cal = attachCalendar(calHost, input, calBtn, function () { dob.commit(); if (opts.onCommit) opts.onCommit(); });
      calBtn.addEventListener("click", function () { cal.toggle(); });
    }
    return dob;
  }

export {
  renderAuth,
  renderEditar,
  renderReset,
  safeNext,
  renderCoveragePicker,
  renderPostbookingCoverage,
  parseFlexibleDate,
  isoToArg,
  ageFromIso,
  dobField,
  fileToJpeg,
  photoFileFromEvent,
  eventCarriesFiles,
  isHeicFile,
  fileAsDataUrl
};
