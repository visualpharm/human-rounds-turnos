/* admin-account.mjs — native lifecycle-owned controller for the direct
 * /admin/account staff screen (profile email, password, logout, passkeys,
 * TOTP setup/confirm/remove, expiry notice).
 *
 * Security step (2026-10-21): while the account lacks 2FA the screen opens
 * with a choice panel — passkey first (runtime WebAuthn/secure-context
 * detection; a missing platform authenticator still allows the phone QR
 * flow), Google Authenticator second with device-matched official store
 * links (Google-only by decision). «Configurar más tarde» appears only
 * inside the grace period; an expired deadline keeps the panel without a
 * deferral. Registering a passkey never satisfies the 2FA requirement —
 * current policy counts only the TOTP secret, so the panel says so up
 * front and keeps its deadline until the app flow confirms.
 *
 * Import is inert. The template (owned by root) boots an explicit host and
 * owns page-exit disposal. Each mount owns its reads, mutations and listeners;
 * disposing or mounting over the same host aborts in-flight reads and retires
 * every callback. A late fetch, JSON decode, session confirmation or
 * mutation continuation from an old owner cannot affect a remounted UI.
 *
 * This is not the public patient platform/account.mjs module.
 *
 * Data contract (unchanged):
 *   GET  /api/admin/account
 *   POST /api/admin/account/profile          {email}
 *   POST /api/admin/account/password         {actual, nueva}
 *   POST /api/admin/account/2fa/start
 *   POST /api/admin/account/2fa/confirm      {setup_token, codigo}
 *   POST /api/admin/account/2fa/remove       {actual}
 *   GET  /api/admin/passkeys
 *   POST /api/admin/passkeys/delete          {credential_id}
 *   POST /api/admin/logout
 * HTTP 200 is not itself success: mutations need {ok:true}. Passkeys 403
 * (including expired-2FA) stays local and is not an unauthenticated 401.
 */

const mounts = new WeakMap();

var ACCOUNT_CSS = [
  /* One type scale (the product owner, 2026-10-27): 16px for labels, values and fields,
     18px block titles, 15px only for secondary dates; one left edge per block. */
  "#sec-cuenta{max-width:720px;font-size:16px;line-height:1.45;color:var(--text);}",
  "#sec-cuenta :is(input,select,button,.cnt-in){font-family:var(--font);}",
  "#sec-cuenta .cnt-in{font-size:16px;line-height:1.3;min-height:44px;padding:9px 12px;}",
  ".acct-card{margin:0 0 16px;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden;}",
  ".acct-card-head{display:flex;align-items:center;gap:12px;min-height:44px;padding:12px 20px;border-bottom:1px solid var(--border);}",
  ".acct-card-head h2{margin:0;font-size:18px;line-height:1.3;font-weight:700;letter-spacing:-.01em;}",
  ".acct-card-head .btn{margin-left:auto;}",
  ".acct-alert{margin:0;padding:14px 20px;background:var(--warn-soft);border-bottom:1px solid var(--border);}",
  ".acct-alert p{margin:0 0 8px;}",
  ".acct-alert p:last-child{margin-bottom:0;}",
  ".acct-rows{list-style:none;margin:0;padding:0;}",
  ".acct-rows + .acct-rows>li:first-child,.acct-row + .acct-row{border-top:1px solid var(--border);}",
  ".acct-row{display:grid;grid-template-columns:200px minmax(0,1fr) auto;gap:6px 16px;align-items:center;padding:12px 20px;min-height:44px;}",
  ".acct-rows>li + li{border-top:1px solid var(--border);}",
  ".acct-k{color:var(--hint-ink,var(--text-2));}",
  ".acct-v{min-width:0;font-weight:500;color:var(--text);overflow-wrap:anywhere;}",
  ".acct-v.is-empty{font-weight:400;color:var(--hint-ink);}",
  ".acct-row-act{justify-self:end;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end;}",
  ".acct-row-wide{grid-column:1/-1;min-width:0;}",
  ".acct-row-wide:empty{display:none;}",
  ".acct-dot{display:inline-block;width:8px;height:8px;margin-right:8px;border-radius:50%;background:var(--done);vertical-align:1px;}",
  ".acct-dot.is-off{background:var(--danger);}",
  ".acct-meta{display:block;color:var(--hint-ink);font-weight:400;font-size:15px;}",
  ".acct-who{display:flex;align-items:center;gap:16px;padding:16px 20px;}",
  ".acct-who .cnt-foto-av{width:72px;height:72px;flex-basis:72px;font-size:22px;}",
  ".acct-who-text{min-width:0;}",
  ".acct-who-name{margin:0;font-size:18px;font-weight:700;line-height:1.3;}",
  ".acct-who-sub{margin:2px 0 0;color:var(--hint-ink);}",
  ".acct-who-acts{margin-left:auto;display:flex;align-items:center;gap:8px;flex-wrap:wrap;justify-content:flex-end;}",
  ".acct-file{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip:rect(0 0 0 0);}",
  ".acct-who + .acct-row{border-top:1px solid var(--border);}",
  ".acct-form{display:flex;flex-direction:column;align-items:stretch;gap:12px;padding:16px 20px;}",
  ".acct-form[hidden]{display:none;}",
  ".acct-row + .acct-form,.acct-who + .acct-form{border-top:1px solid var(--border);}",
  ".acct-field{display:grid;grid-template-columns:200px minmax(0,320px);align-items:center;gap:6px 16px;width:100%;}",
  ".acct-field label{color:var(--text);}",
  ".acct-field .cnt-in,.acct-field select{width:100%;box-sizing:border-box;}",
  ".acct-field select{min-height:44px;font-size:16px;}",
  ".acct-row .acct-form{padding:4px 0 0;}",
  "#acct-profile.is-editing :is(.acct-who-text,#acct-profile-phone-row){display:none;}",
  "#acct-profile.is-editing .acct-who-acts{margin-left:0;}",
  /* 11ch, not 9ch: with .cnt-in's side padding inside border-box, 9ch left
     the 6-digit value and its placeholder clipped at the edge — same lesson
     as the gate's #gate-code. */
  ".acct-field .acct-code{width:11ch;font:20px/1.2 var(--font-mono);letter-spacing:.08em;}",
  ".acct-actions{margin-left:216px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}",
  ".acct-status{color:var(--hint-ink);}",
  ".acct-status:empty{display:none;}",
  ".acct-status.is-error{color:var(--danger);}",
  ".acct-row>.acct-status{grid-column:1/-1;}",
  "#sec-cuenta .btn{min-width:44px;min-height:44px;box-sizing:border-box;justify-content:center;}",
  ".acct-list{list-style:none;margin:0;padding:0;display:grid;gap:6px;}",
  ".acct-list li{display:flex;align-items:center;gap:12px;}",
  ".acct-item-text{flex:1 1 auto;min-width:0;display:flex;flex-wrap:wrap;align-items:baseline;gap:0 12px;}",
  ".acct-item-name{font-weight:500;}",
  ".acct-item-meta{color:var(--hint-ink);font-size:15px;font-weight:400;}",
  ".acct-list li .btn{margin-left:auto;}",
  ".acct-list li.acct-vacio{display:block;}",
  ".acct-keys-add-row{display:block;margin:10px 0 0;}",
  ".acct-list:empty + .acct-keys-add-row{margin-top:0;}",
  /* The keys' Quitar lives in the value column: the row has no action of its
     own, so the value takes the action's track too and Quitar lands on the
     same right edge as every other row's button (the empty track still kept
     the 16px column gap before it). */
  "#acct-passkeys .acct-v{font-weight:400;grid-column:2/-1;}",
  "#acct-passkeys>.acct-row-act{display:none;}",
  "#acct-passkeys.acct-row{align-items:start;}",
  "#acct-passkeys .acct-k{padding-top:10px;}",
  "#acct-passkeys .acct-vacio{padding-top:10px;}",
  ".acct-v .acct-sec-line{margin-top:6px;}",
  ".acct-vacio{color:var(--hint-ink);font-weight:400;}",
  ".acct-sec-line{display:block;margin:0;}",
  ".acct-sec-line:empty{display:none;}",
  ".acct-sec-line .btn{margin:6px 8px 0 0;}",
  ".acct-sec-links{display:block;margin:6px 0;}",
  ".acct-sec-links a{display:block;}",
  ".acct-sec-foot{padding:12px 20px;border-top:1px solid var(--border);display:grid;gap:12px;}",
  ".acct-sec-foot>p{margin:0;}",
  ".acct-sec-foot:not(:has(>:not([hidden]):not(:empty))){display:none;}",
  ".acct-security.is-deferred #acct-sec-opts,.acct-security.is-deferred .acct-sec-shared,.acct-security.is-deferred .acct-sec-skip{display:none;}",
  ".acct-security.is-setup #acct-passkeys,.acct-security.is-setup .acct-sec-shared,.acct-security.is-setup .acct-sec-skip,.acct-security.is-setup #acct-sec-restore-row{display:none;}",
  ".acct-setup{margin:4px 0 0;padding:16px;background:var(--bg-2);border-radius:10px;}",
  ".acct-setup .acct-form{padding:0;}",
  ".acct-setup p{margin:0 0 12px;}",
  ".acct-setup-cols{display:flex;align-items:flex-start;gap:20px;margin:0 0 16px;}",
  ".acct-setup-txt{flex:1 1 0;min-width:0;max-width:42ch;}",
  ".acct-setup-txt p:last-child{margin-bottom:0;}",
  ".acct-qr{flex:0 1 242px;display:block;width:min(242px,100%);padding:10px;box-sizing:border-box;background:#fff;border:1px solid var(--border);border-radius:10px;color:#111;}",
  ".acct-qr svg{display:block;width:100%;height:auto;aspect-ratio:1;}",
  ".acct-steps{margin:0;padding-left:22px;}",
  ".acct-steps li{margin:0 0 8px;}",
  ".acct-steps li:last-child{margin-bottom:0;}",
  ".acct-ayuda{margin-top:12px;}",
  ".acct-ayuda summary{cursor:pointer;color:var(--accent);width:fit-content;}",
  ".acct-ayuda p{margin:10px 0 0;}",
  ".acct-secret{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:8px 0;}",
  ".acct-secret code{font:15px/1.45 var(--font-mono);word-break:break-all;}",
  ".acct-confirm{margin:8px 0 0;padding:16px;background:var(--bg-2);border-radius:10px;}",
  ".acct-confirm p{margin:0 0 12px;}",
  ".acct-confirm .acct-form{padding:0;}",
  ".acct-loading{color:var(--hint-ink);}",
  ".adm-2fa-go{color:var(--accent);white-space:nowrap;}",
  "@media(max-width:620px){.acct-setup-cols{flex-direction:column}.acct-qr{align-self:center}}",
  "@media(max-width:720px){#sec-cuenta{padding:0 16px}.acct-card-head,.acct-row,.acct-who,.acct-form,.acct-alert,.acct-sec-foot{padding-left:16px;padding-right:16px}"
    + ".acct-row{grid-template-columns:minmax(0,1fr) auto}.acct-k{grid-column:1/-1}#acct-passkeys .acct-v{grid-column:1/-1}"
    + ".acct-field{grid-template-columns:1fr}.acct-actions{margin-left:0}"
    + ".acct-who{flex-wrap:wrap}.acct-who-acts{margin-left:88px;justify-content:flex-start}}",
].join("");

export function mount(host, options) {
  if (!host) throw new Error("Account host is required");
  if (mounts.has(host)) mounts.get(host)();
  options = options || {};

  var disposed = false;
  var generation = 0;
  var routeGen = 0;
  var setupGen = 0;
  var accountCtl = null;
  var keysCtl = null;
  var flights = Object.create(null);
  var setupState = null;
  var tracked = [];
  var accountData = null;
  var securityPanel = null;
  var profileData = null;
  var profileCtl = null;

  var Session = null;
  var I18n = null;
  var Passkeys = null;

  var DIRECT_PATH = "/admin/account";
  var DIRECT_SETUP = DIRECT_PATH + "?setup=2fa";

  function alive() { return !disposed && host.isConnected !== false; }
  function ownsRoute(gen) { return alive() && gen === routeGen; }

  function doc() {
    return (typeof window !== "undefined" && window.document) || globalThis.document;
  }
  function loc() {
    return (typeof window !== "undefined" && window.location) || globalThis.location;
  }
  function hist() {
    return (typeof window !== "undefined" && window.history) || globalThis.history;
  }
  function win() {
    return (typeof window !== "undefined" && window) || globalThis;
  }

  function listen(target, name, fn) {
    if (!target || typeof target.addEventListener !== "function") return;
    target.addEventListener(name, fn);
    tracked.push({ target: target, name: name, fn: fn });
  }

  function unlisten() {
    for (var i = 0; i < tracked.length; i += 1) {
      var item = tracked[i];
      if (item.target && typeof item.target.removeEventListener === "function") {
        item.target.removeEventListener(item.name, item.fn);
      }
    }
    tracked = [];
  }

  function clearSetup() {
    setupState = null;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    generation += 1;
    routeGen += 1;
    setupGen += 1;
    clearSetup();
    if (accountCtl) accountCtl.abort();
    if (keysCtl) keysCtl.abort();
    if (profileCtl) profileCtl.abort();
    flights = Object.create(null);
    var setupHost = $("#acct-2fa-setup");
    if (setupHost) setupHost.innerHTML = "";
    unlisten();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }

  function importStatic(name) {
    var map = (typeof window !== "undefined" && window.SANA_STATIC) || globalThis.window && globalThis.window.SANA_STATIC;
    var url = map && map[name];
    return url ? import(url) : Promise.reject(new Error("Missing Account dependency: " + name));
  }

  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function $(sel, root) { return (root || host).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || host).querySelectorAll(sel)); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function mutationAck(j) { return isObject(j) && j.ok === true; }

  function inHost(el) {
    if (!el) return false;
    if (el.isConnected === false) return false;
    if (typeof host.contains === "function") return host.contains(el);
    var node = el;
    var hops = 0;
    while (node && hops < 64) {
      if (node === host) {
        var listed = host.querySelectorAll ? Array.prototype.slice.call(host.querySelectorAll("*")) : [];
        if (!listed.length) return true;
        return listed.indexOf(el) >= 0 || el === host;
      }
      node = node.parentNode;
      hops += 1;
    }
    return false;
  }

  function formCurrent(id, form) {
    return form && $(id) === form && inHost(form);
  }

  function begin(kind) {
    if (!alive() || flights[kind]) return null;
    var token = { kind: kind, gen: routeGen };
    flights[kind] = token;
    return token;
  }

  function end(token) {
    if (!token || flights[token.kind] !== token) return;
    delete flights[token.kind];
  }

  function fetchOutcome(path, opts) {
    opts = Object.assign({ credentials: "same-origin" }, opts || {});
    return fetch(path, opts).then(function (r) {
      return r.json().then(
        function (j) { return { ok: r.ok, status: r.status, json: j, response: r }; },
        function () { return { ok: r.ok, status: r.status, json: null, response: r }; }
      );
    }).catch(function (err) {
      if (err && err.name === "AbortError") return { aborted: true, ok: false, status: 0, json: null, response: null };
      return { ok: false, status: 0, json: null, response: null };
    });
  }

  function classify(r, current) {
    if (!current()) return Promise.resolve(false);
    if (r.aborted) return Promise.resolve(false);
    if (r.status !== 401 && r.status !== 403) return Promise.resolve(current());
    return Session.confirmar(r.response, r.json, current).then(function (transitioned) {
      if (!current()) return false;
      if (transitioned) {
        if (alive()) host.innerHTML = "";
        dispose();
        return false;
      }
      return current();
    });
  }

  // Lo que el server dijo, leído por i18n.mjs apiError (TASK-48): la demo da
  // su frase, una frase para personas pasa, un código cae en `fallback`.
  function errText(j, fallback) {
    if (!(isObject(j) && j.error)) return fallback;
    return I18n && I18n.apiError ? I18n.apiError(j, fallback) : fallback;
  }

  function setStatus(el, message, error) {
    if (!el) return;
    el.textContent = message || "";
    if (el.classList && el.classList.toggle) el.classList.toggle("is-error", !!error);
    else el.className = "acct-status" + (error ? " is-error" : "");
  }

  function pageLocale() {
    var d = doc();
    var lang = (d && d.documentElement && d.documentElement.getAttribute("lang") || "es").toLowerCase();
    return lang === "en" ? "en-us" : "es-ar";
  }

  function deadlineText(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return new Intl.DateTimeFormat(pageLocale(), {
      weekday: "long", day: "numeric", month: "long", year: "numeric"
    }).format(d);
  }

  function fechaCorta(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    return new Intl.DateTimeFormat(pageLocale(), { day: "numeric", month: "short", year: "numeric" }).format(d);
  }

  function rolLabel(rol) {
    var map = {
      admin: T("admin.system.account.role.admin", null, "Administrador"),
      medico: T("admin.system.account.role.medico", null, "Profesional"),
      admisionista: T("admin.system.account.role.admisionista", null, "Mesa de admisión"),
      reportes: T("admin.system.account.role.reportes", null, "Reportes"),
      marca: T("admin.system.account.role.marca", null, "Marca"),
      operador: T("admin.system.account.role.operador", null, "Operador")
    };
    return map[rol] || rol || "";
  }

  // Official Google Authenticator destinations (parent-verified, 2026-10-21).
  // Google-only by decision: no other vendor's store links on this surface.
  var GA_PLAY_URL = "https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2";
  var GA_APPSTORE_URL = "https://apps.apple.com/app/google-authenticator/id388497605";

  /* Device-class detection for the store link, not for credentials: a
     recognised phone shows only its own store; desktops and unknown
     devices show both. A desktop-class iPad UA reports "Macintosh", so
     touch support (not the UA string alone) is what marks it as iOS. */
  function tipoDispositivo() {
    var nav = (typeof window !== "undefined" && window.navigator) || globalThis.navigator || {};
    var ua = nav.userAgent || "";
    var touch = typeof nav.maxTouchPoints === "number" ? nav.maxTouchPoints : 0;
    if (/iPhone|iPod|iPad/.test(ua) || (/Macintosh/.test(ua) && touch > 1)) return "ios";
    if (/Android/.test(ua)) return "android";
    return "ambos";
  }

  function tiendaLinksHtml() {
    var play = '<a href="' + esc(GA_PLAY_URL) + '" target="_blank" rel="noopener">'
      + esc(T("admin.system.account.security.ga_play", null, "Google Authenticator en Play Store")) + "</a>";
    var appstore = '<a href="' + esc(GA_APPSTORE_URL) + '" target="_blank" rel="noopener">'
      + esc(T("admin.system.account.security.ga_appstore", null, "Google Authenticator en App Store")) + "</a>";
    var tipo = tipoDispositivo();
    if (tipo === "android") return play;
    if (tipo === "ios") return appstore;
    return play + appstore;
  }

  function setupHref() { return DIRECT_SETUP; }

  function wantsSetup() {
    var here = loc();
    var params = new URLSearchParams(here && here.search ? here.search : "");
    var setup = params.getAll("setup");
    var keys = [];
    params.forEach(function (_value, key) { if (keys.indexOf(key) < 0) keys.push(key); });
    return setup.length === 1 && setup[0] === "2fa" && keys.length === 1;
  }

  function canonicalize() {
    var here = loc();
    var history = hist();
    if (!here || !history || typeof history.replaceState !== "function") return;
    var canonical = wantsSetup() ? DIRECT_SETUP : DIRECT_PATH;
    if (String(here.pathname || "") + String(here.search || "") !== canonical) {
      history.replaceState({}, "", canonical);
    }
  }

  function applyTitle() {
    var d = doc();
    if (!d || typeof d.getElementById !== "function") return;
    var h1 = d.getElementById("adm-h1");
    if (h1) {
      h1.style = h1.style || {};
      h1.style.display = "";
      h1.textContent = T("admin.system.account.title", null, "Cuenta");
    }
    var sub = d.getElementById("adm-sub");
    if (sub) {
      sub.style = sub.style || {};
      sub.style.display = "none";
    }
  }

  function applySecurity(data) {
    data = data || {};
    var d = doc();
    var note = d && d.getElementById && d.getElementById("adm-2fa-note");
    var required = !!data["2fa_vencido"];
    if (d && d.body && d.body.classList) d.body.classList.toggle("adm-2fa-required", required);
    if (!note) return;
    if (data.tiene_2fa || data["2fa_no_aplica"]) {
      note.hidden = true;
      note.textContent = "";
      return;
    }
    var date = deadlineText(data["2fa_deadline"]);
    note.hidden = false;
    var mensaje = required
      ? T("admin.system.account.tfa_notice.required", null, "Activá 2FA para seguir usando el panel.")
      : (date
        ? T("admin.system.account.tfa_notice.deadline", { fecha: date }, "Activá 2FA antes del {fecha}.")
        : T("admin.system.account.tfa_notice.days", null, "Activá 2FA dentro de 7 días."));
    note.innerHTML = esc(mensaje)
      + ' <a href="' + esc(setupHref()) + '" class="adm-2fa-go">' + esc(T("admin.system.account.activate", null, "Activar")) + "</a>";
  }

  function injectCss() {
    var d = doc();
    if (!d || (d.getElementById && d.getElementById("acct-css"))) return;
    var st = d.createElement("style");
    st.id = "acct-css";
    st.textContent = ACCOUNT_CSS;
    if (d.head) d.head.appendChild(st);
  }

  function itemHtml(nombre, meta, attrs) {
    return "<li><span class=\"acct-item-text\"><span class=\"acct-item-name\">" + esc(nombre) + "</span>"
      + '<span class="acct-item-meta">' + esc(meta) + "</span></span>"
      + '<button class="btn btn-quiet btn-sm" type="button" ' + attrs + ">" + esc(T("admin.system.account.remove", null, "Quitar")) + "</button></li>";
  }

  function field(label, id, type, autocomplete, extra) {
    return '<div class="acct-field"><label for="' + id + '">' + esc(label) + "</label>"
      + '<input class="cnt-in" id="' + id + '" name="' + id + '" type="' + type + '" autocomplete="' + autocomplete + '"' + (extra || "") + "></div>";
  }

  /* One block of the view: a title, an optional «Editar» on the block and
     its rows. Each editable part keeps its form in the DOM, closed, and
     opens it in place with one Guardar and one Cancelar. */
  function cardHtml(id, title, headAction, inner, extraClass) {
    return '<section class="acct-card' + (extraClass ? " " + extraClass : "") + '"' + (id ? ' id="' + id + '"' : "") + ">"
      + inner.before
      + '<div class="acct-card-head"><h2>' + esc(title) + "</h2>" + (headAction || "") + "</div>"
      + inner.body + "</section>";
  }

  function rowHtml(label, value, action, opts) {
    opts = opts || {};
    return "<" + (opts.tag || "div") + ' class="acct-row"' + (opts.id ? ' id="' + opts.id + '"' : "") + ">"
      + '<span class="acct-k">' + esc(label) + "</span>"
      + '<span class="acct-v' + (opts.empty ? " is-empty" : "") + '"' + (opts.valueId ? ' id="' + opts.valueId + '"' : "") + ">" + value + "</span>"
      + '<span class="acct-row-act">' + (action || "") + "</span>"
      + (opts.wide || "")
      + "</" + (opts.tag || "div") + ">";
  }

  function editButton(kind, label, id) {
    return '<button class="btn btn-quiet btn-sm" type="button" data-edit="' + kind + '"' + (id ? ' id="' + id + '"' : "") + ">"
      + esc(label || T("admin.system.account.edit", null, "Editar")) + "</button>";
  }

  function formActions(kind, statusId, submitLabel) {
    return '<div class="acct-actions"><button class="btn btn-ok" type="submit">' + esc(submitLabel || T("admin.system.common.save", null, "Guardar")) + "</button>"
      + '<button class="btn btn-quiet" type="button" data-cancel="' + kind + '">' + esc(T("admin.system.account.cancel", null, "Cancelar")) + "</button>"
      + (statusId ? '<span class="acct-status" id="' + statusId + '"></span>' : "") + "</div>";
  }

  function photoControlsHtml() {
    return '<div class="acct-who-acts">'
      + '<input type="file" id="mifoto-foto-input" class="acct-file cnt-foto-file" accept="image/jpeg,image/png,image/webp">'
      + '<label class="btn btn-quiet btn-sm" for="mifoto-foto-input" id="mifoto-foto-pick">' + esc(T("admin.system.account.photo.change", null, "Cambiar foto")) + "</label>"
      + '<button type="button" id="mifoto-foto-quitar" class="btn btn-quiet btn-sm" style="display:none">' + esc(T("admin.content.catalog.common.remove_photo_link", null, "Quitar foto")) + "</button>"
      + "</div>";
  }

  /* The doctor's profile block. Name, specialty and phone come from the
     doctor's own record (GET /api/admin/doctor-profile) after the account
     paints; the photo has its own endpoints and uploads on choosing it. */
  function profileSectionHtml() {
    var body = '<div class="acct-who"><div class="cnt-foto-av" id="mifoto-foto-av" aria-hidden="true"></div>'
      + '<div class="acct-who-text"><p class="acct-who-name" id="acct-profile-name"></p><p class="acct-who-sub" id="acct-profile-specialty"></p></div>'
      + photoControlsHtml() + "</div>"
      + rowHtml(T("admin.doctor.phone", null, "Teléfono"), "",
        '<button class="btn btn-quiet btn-sm" type="button" data-edit="profile" id="acct-profile-phone-add" hidden>'
          + esc(T("admin.system.account.add", null, "Agregar")) + "</button>",
        { id: "acct-profile-phone-row", valueId: "acct-profile-phone" })
      + '<p class="acct-row acct-status" id="mifoto-status"></p>'
      + '<form class="acct-form" id="acct-profile-form" hidden>'
      + field(T("admin.content.catalog.common.field_firstname", null, "Nombre"), "acct-profile-first", "text", "given-name", " required")
      + field(T("admin.content.catalog.common.field_lastname", null, "Apellido"), "acct-profile-last", "text", "family-name", " required")
      + '<div class="acct-field"><label for="acct-profile-spec">' + esc(T("admin.doctor.specialty", null, "Especialidad")) + "</label>"
      + '<select class="adm-sel" id="acct-profile-spec" name="acct-profile-spec" required></select></div>'
      + field(T("admin.doctor.phone", null, "Teléfono"), "acct-profile-tel", "tel", "tel")
      + formActions("profile", "acct-profile-status")
      + "</form>";
    return cardHtml("acct-profile", T("admin.system.account.profile.heading", null, "Perfil"),
      editButton("profile", null, "acct-profile-edit"), { before: "", body: body });
  }

  function accessSectionHtml(data) {
    var portal = !!data.acceso_portal;
    var hasEmail = !!data.email;
    var rows = "";
    if (data.rol !== "medico") rows += rowHtml(T("admin.system.account.role.label", null, "Rol"), esc(rolLabel(data.rol)), "");
    rows += rowHtml(T("admin.system.account.username", null, "Usuario"), esc(data.usuario), "");
    rows += rowHtml(T("admin.system.account.email.title", null, "Email"),
      // No «Sin email» in an empty row: the «Agregar» button says it.
      hasEmail ? esc(data.email) : "",
      editButton("email", hasEmail ? null : T("admin.system.account.add", null, "Agregar"), "acct-email-edit"),
      { empty: !hasEmail, valueId: "acct-email-value", wide: '<span class="acct-status acct-row-wide" id="acct-email-status"></span>' });
    rows += '<form class="acct-form" id="acct-email-form" hidden>'
      + '<div class="acct-field"><label for="acct-email">' + esc(T("admin.system.account.email.title", null, "Email")) + "</label>"
      + '<input class="cnt-in" id="acct-email" name="email" type="email" autocomplete="email" value="'
      + esc(data.email || "") + '"></div>'
      + formActions("email")
      + "</form>";
    return cardHtml("acct-access", T("admin.system.account.access.title", null, "Datos de ingreso"), "", { before: "", body: rows });
  }

  function renderLoadError() {
    if (!alive()) return;
    host.innerHTML = '<p class="adm-empty">' + esc(T("admin.system.account.load_error", null, "No se pudo cargar la cuenta.")) + "</p>";
  }

  function keysList(keys, restricted) {
    var ul = $("#acct-keys-list");
    if (!ul) return;
    if (restricted) {
      ul.innerHTML = "";
      // Closed until the repair: say when it opens, not an error.
      setStatus(passkeyStatusEl(), restricted === "2fa_requerido"
        ? T("admin.system.account.passkeys.after_tfa", null, "Primero activá la verificación en 2 pasos con la app.")
        : T("admin.system.users.passkeys_query_error", null, "No se pudo consultar."), restricted !== "2fa_requerido");
      var add = $("#acct-keys-add");
      if (add) add.disabled = true;
      return;
    }
    if (!keys || !keys.length) {
      // With no key the row is its «Agregar una passkey» button alone; only
      // the security step explains what a passkey is.
      ul.innerHTML = securityPanel ? '<li class="acct-vacio">'
        + esc(T("admin.system.account.security.passkey_meta", null, "Entrá con la huella, la cara o el PIN del dispositivo, sin contraseña ni código. Podés registrar una por cada equipo que uses.")) + "</li>" : "";
      return;
    }
    ul.innerHTML = keys.map(function (k) {
      var fechaUso = fechaCorta(k.ultimo_uso);
      var fechaAlta = fechaCorta(k.creado);
      var cuando = k.ultimo_uso
        ? T("admin.system.account.passkeys.last_used", { fecha: fechaUso }, "Último uso: {fecha}")
        : T("admin.system.account.passkeys.registered_on", { fecha: fechaAlta }, "Registrada el {fecha}");
      return itemHtml(k.nombre || "Passkey", cuando, 'data-key-del="' + esc(k.credential_id) + '"');
    }).join("");
  }

  /* The security step paints its passkey reason next to the panel button
     when the account still lacks 2FA; once 2FA is active the Passkeys row
     owns its own status line. */
  function passkeyStatusEl() {
    return $("#acct-sec-pk-status") || $("#acct-keys-status");
  }

  function paintPasskeyCapability() {
    var add = $("#acct-keys-add");
    if (!add) return;
    if (!Passkeys || !Passkeys.soportado()) {
      add.disabled = true;
      setStatus(passkeyStatusEl(), T("admin.system.account.passkeys.unsupported", null, "Este navegador no maneja passkeys. Probá desde Chrome, Safari o Edge actualizados."), false);
      return;
    }
    if (win().isSecureContext === false) {
      add.disabled = true;
      setStatus(passkeyStatusEl(), T("admin.system.account.security.insecure", null, "Necesitás una conexión segura (https) para crear passkeys. Podés usar la app de abajo."), false);
      return;
    }
    // A missing platform authenticator is NOT "no passkeys" — and not proof
    // the device lacks a PIN either: the browser may still offer the
    // cross-device flow (the person's phone), the right path on a shared
    // hospital computer. The hint stays conditional; it cannot promise what
    // the browser will show.
    if (!securityPanel) return;
    Promise.resolve(Passkeys.disponibleEnEsteEquipo()).then(function (platform) {
      if (platform || !alive()) return;
      var cap = $("#acct-sec-pk-cap");
      if (cap) setStatus(cap, T("admin.system.account.security.no_platform", null, "Puede que este equipo no tenga huella ni PIN: al crear la passkey, elegí usar otro dispositivo o tu teléfono, si el navegador ofrece esa opción."), false);
    });
  }

  function plazoText(data, date) {
    return data["2fa_vencido"]
      ? T("admin.system.account.tfa_warning.expired", null, "El plazo terminó y el resto del panel queda bloqueado hasta que la actives.")
      : (date
        ? T("admin.system.account.tfa_warning.days_with_date", { fecha: date }, "Tenés 7 días desde el primer ingreso, hasta el {fecha}.")
        : T("admin.system.account.tfa_warning.days", null, "Tenés 7 días desde el primer ingreso."));
  }

  function dot(on, label) {
    return '<span class="acct-dot' + (on ? "" : " is-off") + '" aria-hidden="true"></span>' + esc(label);
  }

  /* Seguridad: one summary of what is set up and what is not — password,
     passkeys, 2-step verification — each with its action beside it.
     Without 2FA the block is the security step itself (#acct-security):
     deadline first, passkey first then the app, deferral only while the
     grace period runs. Registering a passkey never satisfies the 2FA
     requirement — current policy counts only the TOTP secret — so the step
     says so up front and keeps its deadline until the app flow confirms.
     A portal installation closes our password and passkeys, and shows 2FA
     only while our TOTP is switched on there. */
  function securityHtml(data) {
    var portal = !!data.acceso_portal;
    var tfaOff = !!data["2fa_no_aplica"];
    var pending = !data.tiene_2fa && !tfaOff;
    var before = "";
    if (pending) {
      var date = deadlineText(data["2fa_deadline"]);
      before = '<div class="acct-alert" role="status"><p><b>' + esc(T("admin.system.account.tfa_warning.title", null, "Activá la verificación en 2 pasos.")) + "</b> "
        + esc(plazoText(data, date)) + "</p>"
        + (portal ? "" : "<p>" + esc(T("admin.system.account.security.app_still_required", null,
          "Aunque registres una passkey, esta instalación todavía necesita Google Authenticator para completarla.")) + "</p>")
        + "</div>";
    }
    var body = "";
    if (!portal) {
      body += '<ul class="acct-rows">' + rowHtml(T("admin.system.account.password.title", null, "Contraseña"),
        // A set password is the usual case: masked, with no green «Configurada».
        '<span role="img" aria-label="' + esc(T("admin.system.account.password.set", null, "Configurada")) + '">••••••••</span>',
        editButton("password", T("admin.system.account.password.change", null, "Cambiar"), "acct-password-edit"),
        { tag: "li", wide: '<span class="acct-status acct-row-wide" id="acct-pass-status"></span>'
          + '<form class="acct-form acct-row-wide" id="acct-password-form" hidden>'
          + '<div class="acct-field"><label for="acct-username">' + esc(T("admin.system.account.username", null, "Usuario")) + "</label>"
          + '<input class="cnt-in" id="acct-username" name="username" autocomplete="username" value="' + esc(data.usuario)
          + '"' + (data.es_dueno ? " readonly" : "") + "></div>"
          + field(T("admin.system.account.password.current", null, "Contraseña actual"), "acct-pass-current", "password", "current-password")
          + field(T("admin.system.account.password.new", null, "Contraseña nueva"), "acct-pass-new", "password", "new-password")
          + field(T("admin.system.account.password.confirm", null, "Repetir contraseña"), "acct-pass-confirm", "password", "new-password")
          + formActions("password")
          + "</form>" }) + "</ul>";
    }
    var opts = "";
    if (!portal) {
      var keysStatus = pending
        ? '<span class="acct-status acct-sec-line" id="acct-sec-pk-status"></span>'
          + '<span class="acct-sec-line" id="acct-sec-pk-extra"></span>'
          + '<span class="acct-status acct-sec-line" id="acct-sec-pk-cap"></span>'
        : '<span class="acct-status acct-sec-line" id="acct-keys-status"></span>';
      // The keys are listed in the value column, each with its own Quitar;
      // adding one sits under the list, where the next key will appear.
      opts += rowHtml("Passkeys", '<ul class="acct-list" id="acct-keys-list"></ul>'
          + '<span class="acct-keys-add-row"><button class="btn btn-quiet btn-sm" type="button" id="acct-keys-add">'
          + esc(pending ? T("admin.system.account.security.passkey_button", null, "Crear passkey")
            : T("admin.system.account.passkeys.add_button", null, "Agregar una passkey")) + "</button></span>"
          + keysStatus, "",
        { tag: "li", id: "acct-passkeys" });
    }
    if (!tfaOff) {
      var tfaWide = '<div class="acct-row-wide">'
        + (pending ? '<span class="acct-sec-line" id="acct-sec-app-help" hidden>'
          + esc(T("admin.system.account.security.help_lede", null, "Instalá Google Authenticator en tu teléfono:"))
          + '<span class="acct-sec-links">' + tiendaLinksHtml() + "</span>"
          + esc(T("admin.system.account.security.help_next", null, "Después tocá «Activar con la app» y escaneá el código con la app."))
          + "</span>" : "")
        + '<div class="acct-sec-line" id="acct-2fa-confirm"></div>'
        + '<div class="acct-sec-line" id="acct-2fa-setup"></div></div>';
      opts += rowHtml(T("admin.system.account.tfa.title", null, "Verificación en 2 pasos"),
        data.tiene_2fa
          ? esc(T("admin.system.account.tfa.app_name", null, "App de autenticación"))
          : dot(false, T("admin.system.account.tfa.off", null, "No activa"))
            + '<span class="acct-meta">' + esc(T("admin.system.account.tfa.app_name", null, "App de autenticación")) + " (Google Authenticator)</span>",
        data.tiene_2fa
          ? '<button class="btn btn-quiet btn-sm" type="button" id="acct-2fa-quitar">' + esc(T("admin.system.account.remove", null, "Quitar")) + "</button>"
          : '<button class="btn btn-ok btn-sm" type="button" id="acct-2fa-start" data-2fa-start>' + esc(T("admin.system.account.security.app_button", null, "Activar con la app")) + "</button>",
        { tag: "li", id: "acct-2fa", wide: tfaWide });
    }
    if (opts) body += '<ul class="acct-rows"' + (pending ? ' id="acct-sec-opts"' : "") + ">" + opts + "</ul>";
    if (pending) {
      body += '<div class="acct-sec-foot">'
        + (portal ? "" : '<p class="acct-sec-shared">' + esc(T("admin.system.account.security.shared_hint", null, "¿Computadora compartida del hospital? Podés crear la passkey con tu teléfono: al tocar «Crear passkey», si el navegador ofrece usar otro dispositivo, elegí tu teléfono y escaneá con su cámara el código QR que aparezca.")) + "</p>")
        + (data["2fa_vencido"] ? ""
          : '<p class="acct-sec-skip"><button class="btn btn-quiet btn-sm" type="button" id="acct-sec-skip">'
            + esc(T("admin.system.account.security.skip", null, "Configurar más tarde")) + "</button></p>")
        + '<p id="acct-sec-restore-row" hidden><button class="btn btn-ok" type="button" id="acct-sec-restore">'
        + esc(T("admin.system.account.tfa_activate_button", null, "Activar la verificación en 2 pasos")) + "</button></p>"
        + '<p id="acct-sec-back-row" hidden><button class="btn btn-quiet btn-sm" type="button" id="acct-sec-back">'
        + esc(T("admin.system.account.security.back", null, "Volver a las opciones")) + "</button></p>"
        + "</div>";
    }
    if (!body) return "";
    return cardHtml(pending ? "acct-security" : "acct-seguridad", T("admin.system.account.security.title", null, "Seguridad"), "",
      { before: before, body: body }, pending ? "acct-security" : "");
  }

  function setupHtml(j, gen) {
    var qr = j.qr_svg
      ? '<div class="acct-qr" role="img" aria-label="' + esc(T("admin.system.account.setup.qr_label", null, "Código QR para configurar la verificación en 2 pasos")) + '">' + j.qr_svg + "</div>"
      : "";
    return '<div class="acct-setup">'
      + '<div class="acct-setup-cols">' + qr
      + '<div class="acct-setup-txt">'
      + '<ol class="acct-steps"><li>' + esc(T("admin.system.account.setup.how1", null, "Instalá Google Authenticator en tu teléfono:"))
      + '<span class="acct-sec-links">' + tiendaLinksHtml() + "</span></li>"
      + "<li>" + esc(T("admin.system.account.setup.how2", null, "Abrí la app y elegí Agregar una cuenta.")) + "</li>"
      + "<li>" + esc(T("admin.system.account.setup.how3", null, "Escaneá este código QR.")) + "</li>"
      + "<li>" + esc(T("admin.system.account.setup.how4", null, "Ingresá abajo el código de 6 dígitos que muestra la app.")) + "</li></ol>"
      + '<details class="acct-ayuda"><summary>' + esc(T("admin.system.account.setup.cant_scan_summary", null, "No puedo escanear")) + "</summary>"
      + "<p>" + esc(T("admin.system.account.setup.cant_scan_pre", null, "Escribí esta clave a mano en la app, en la opción ")) + "<b>" + esc(T("admin.system.account.setup.cant_scan_bold", null, "Ingresar una clave")) + "</b>:</p>"
      + '<div class="acct-secret"><code>' + esc(j.secret) + "</code>"
      + '<button class="btn btn-quiet btn-sm" type="button" data-copy-account="' + esc(j.secret) + '">' + esc(T("admin.system.account.setup.copy_key", null, "Copiar clave")) + "</button></div>"
      + "</details>"
      + "</div></div>"
      + '<form class="acct-form" id="acct-confirm-form" data-setup-gen="' + esc(gen) + '">'
      + '<div class="acct-field"><label for="acct-confirm-code">' + esc(T("admin.system.account.setup.code_label", null, "Código de 6 dígitos")) + "</label>"
      + '<input class="cnt-in acct-code" id="acct-confirm-code" name="acct-confirm-code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]*"></div>'
      + '<div class="acct-actions"><button class="btn btn-ok" type="submit">' + esc(T("admin.system.account.setup.confirm_button", null, "Confirmar")) + "</button>"
      + '<span class="acct-status" id="acct-confirm-status"></span></div></form></div>';
  }

  function render(data) {
    if (!alive()) return;
    accountData = data;
    profileData = null;
    securityPanel = data.tiene_2fa ? null : data;
    var medico = data.rol === "medico";
    var tfaOff = !!data["2fa_no_aplica"];
    var pending = !data.tiene_2fa && !tfaOff;
    var security = securityHtml(data);
    // What still has to be set up comes first: the security step leads the
    // page while 2FA is missing, the profile leads it otherwise. An expired
    // deadline closes the doctor's record AND the photo endpoints until the
    // repair, so neither is offered then.
    var html = pending ? security : "";
    if (medico && !data["2fa_vencido"]) html += profileSectionHtml();
    html += accessSectionHtml(data);
    if (!pending) html += security;
    if (data.tiene_2fa && loc().search) {
      var history = hist();
      if (history && history.replaceState) history.replaceState({}, "", DIRECT_PATH);
    }
    clearSetup();
    host.innerHTML = html;
    paintPasskeyCapability();
    if (medico && !data["2fa_vencido"]) {
      refreshFoto();
      loadProfile(routeGen);
    }
  }

  function bumpRoute() {
    routeGen += 1;
    setupGen += 1;
    clearSetup();
    if (accountCtl) accountCtl.abort();
    if (keysCtl) keysCtl.abort();
    if (profileCtl) profileCtl.abort();
    return routeGen;
  }

  function fetchAccount(gen) {
    if (accountCtl) accountCtl.abort();
    var ctl = typeof AbortController === "function" ? new AbortController() : { abort: function () {}, signal: undefined };
    accountCtl = ctl;
    var current = function () { return ownsRoute(gen) && accountCtl === ctl; };
    return fetchOutcome("/api/admin/account", { signal: ctl.signal }).then(function (r) {
      if (r.aborted || !current()) return null;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return null;
        if (!r.ok || !isObject(r.json)) {
          renderLoadError();
          return null;
        }
        applySecurity(r.json);
        return r.json;
      });
    });
  }

  function loadPasskeys(gen) {
    if (keysCtl) keysCtl.abort();
    var ctl = typeof AbortController === "function" ? new AbortController() : { abort: function () {}, signal: undefined };
    keysCtl = ctl;
    var current = function () { return ownsRoute(gen) && keysCtl === ctl; };
    return fetchOutcome("/api/admin/passkeys", { signal: ctl.signal }).then(function (r) {
      if (r.aborted || !current()) return;
      if (r.status === 403) {
        keysList([], r.json && r.json.error === "2fa_requerido" ? "2fa_requerido" : "forbidden");
        return;
      }
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !isObject(r.json)) {
          keysList([]);
          return;
        }
        keysList(Array.isArray(r.json.passkeys) ? r.json.passkeys : []);
      });
    });
  }

  function abrirSetup(boton) {
    var setupHost = $("#acct-2fa-setup");
    if (!setupHost) return Promise.resolve();
    var token = begin("2fa-start");
    if (!token) return Promise.resolve();
    setupGen += 1;
    var mySetup = setupGen;
    clearSetup();
    if (boton) boton.disabled = true;
    var here = loc();
    var history = hist();
    if (here && history && history.pushState && String(here.pathname || "") + String(here.search || "") !== DIRECT_SETUP) {
      history.pushState({}, "", DIRECT_SETUP);
    }
    $$("[data-2fa-start]").forEach(function (b) { b.style.display = "none"; });
    setSetupPanel(true);
    setupHost.innerHTML = '<p class="acct-status">' + esc(T("admin.system.account.setup.preparing", null, "Preparando…")) + "</p>";
    var current = function () { return alive() && mySetup === setupGen && ownsRoute(token.gen); };
    return fetchOutcome("/api/admin/account/2fa/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    }).then(function (r) {
      if (!current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setupHost.innerHTML = '<p class="acct-status is-error">' + esc(errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar."))) + "</p>";
          $$("[data-2fa-start]").forEach(function (b) { b.style.display = ""; });
          setSetupPanel(false);
          return;
        }
        if (typeof r.json.setup_token !== "string" || !r.json.setup_token.trim()) {
          setStatus(setupHost, T("admin.system.common.save_error", null, "No se pudo guardar."), true);
          $$("[data-2fa-start]").forEach(function (b) { b.style.display = ""; });
          setSetupPanel(false);
          return;
        }
        setupState = { gen: mySetup, token: r.json.setup_token };
        setupHost.innerHTML = setupHtml(r.json, mySetup);
        if (setupHost.scrollIntoView) setupHost.scrollIntoView({ block: "nearest" });
        var code = $("#acct-confirm-code");
        if (code && code.focus) code.focus();
      });
    }).then(function () {
      end(token);
      if (boton && current()) boton.disabled = false;
    }, function () {
      end(token);
      if (boton && current()) boton.disabled = false;
    });
  }

  function refreshUsers() {
    var map = (typeof window !== "undefined" && window.SANA_STATIC) || (globalThis.window && globalThis.window.SANA_STATIC);
    var url = map && map["platform/admin-users.mjs"];
    if (!url) return;
    import(url).then(function (mod) {
      if (!alive()) return;
      if (mod && typeof mod.refresh === "function") mod.refresh();
    }).catch(function () {});
  }

  function reloadAfter(gen) {
    return fetchAccount(gen).then(function (data) {
      if (!data || !ownsRoute(gen)) return;
      render(data);
      return Promise.all([
        loadPasskeys(gen),
        (wantsSetup() && !data.tiene_2fa && !data["2fa_no_aplica"]) ? abrirSetup($("#acct-2fa-start")) : Promise.resolve()
      ]);
    });
  }

  function loadAll() {
    if (!alive()) return Promise.resolve();
    var gen = bumpRoute();
    canonicalize();
    applyTitle();
    return fetchAccount(gen).then(function (data) {
      if (!data || !ownsRoute(gen)) return;
      render(data);
      var setupPromise = (wantsSetup() && !data.tiene_2fa && !data["2fa_no_aplica"]) ? abrirSetup($("#acct-2fa-start")) : Promise.resolve();
      return Promise.all([loadPasskeys(gen), setupPromise]);
    });
  }

  function submitButton(form) {
    return $("button[type=submit]", form) || $('button[type="submit"]', form);
  }

  function saveEmail(form) {
    if (!formCurrent("#acct-email-form", form)) return;
    var token = begin("email");
    if (!token) return;
    var button = submitButton(form);
    var status = $("#acct-email-status");
    var gen = routeGen;
    var current = function () { return ownsRoute(gen) && formCurrent("#acct-email-form", form); };
    if (button) button.disabled = true;
    var email = ($("#acct-email") && $("#acct-email").value || "").trim();
    fetchOutcome("/api/admin/account/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email })
    }).then(function (r) {
      if (!current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
          return;
        }
        if (accountData) accountData = Object.assign({}, accountData, { email: email || null });
        closeEdit("email");
        paintEmail();
        setStatus(status, T("admin.system.account.email.saved", null, "Email guardado."), false);
        refreshUsers();
      });
    }).then(function () {
      end(token);
      if (button && alive()) button.disabled = false;
    }, function () {
      end(token);
      if (button && alive()) button.disabled = false;
    });
  }

  function savePassword(form) {
    if (!formCurrent("#acct-password-form", form)) return;
    var status = $("#acct-pass-status");
    var currentVal = $("#acct-pass-current") ? $("#acct-pass-current").value : "";
    var next = $("#acct-pass-new") ? $("#acct-pass-new").value : "";
    var confirm = $("#acct-pass-confirm") ? $("#acct-pass-confirm").value : "";
    if (next !== confirm) {
      setStatus(status, T("admin.system.account.password.mismatch", null, "Las contraseñas nuevas no coinciden."), true);
      return;
    }
    var token = begin("password");
    if (!token) return;
    var button = submitButton(form);
    var gen = routeGen;
    var current = function () { return ownsRoute(gen) && formCurrent("#acct-password-form", form); };
    if (button) button.disabled = true;
    fetchOutcome("/api/admin/account/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actual: currentVal, nueva: next, usuario: $("#acct-username") ? $("#acct-username").value.trim() : accountData.usuario })
    }).then(function (r) {
      if (!current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
          return;
        }
        if (r.json.usuario) { refreshUsers(); return reloadAfter(gen); }
        closeEdit("password");
        setStatus(status, T("admin.system.account.password.updated", null, "Contraseña actualizada."), false);
      });
    }).then(function () {
      end(token);
      if (button && alive()) button.disabled = false;
    }, function () {
      end(token);
      if (button && alive()) button.disabled = false;
    });
  }

  function confirm2fa(form) {
    if (!formCurrent("#acct-confirm-form", form)) return;
    var formGen = Number(form.getAttribute("data-setup-gen"));
    if (!setupState || setupState.gen !== formGen) return;
    var token = begin("2fa-confirm");
    if (!token) return;
    var button = submitButton(form);
    var status = $("#acct-confirm-status");
    var gen = routeGen;
    var setupAt = setupState;
    var current = function () {
      return ownsRoute(gen) && formCurrent("#acct-confirm-form", form)
        && setupState === setupAt && setupState.gen === formGen;
    };
    if (button) button.disabled = true;
    fetchOutcome("/api/admin/account/2fa/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        setup_token: setupAt.token,
        codigo: $("#acct-confirm-code") ? $("#acct-confirm-code").value : ""
      })
    }).then(function (r) {
      if (!current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
          return;
        }
        clearSetup();
        return reloadAfter(gen);
      });
    }).then(function () {
      end(token);
      if (button && alive()) button.disabled = false;
    }, function () {
      end(token);
      if (button && alive()) button.disabled = false;
    });
  }

  function remove2fa(form) {
    if (!formCurrent("#acct-remove-form", form)) return;
    var token = begin("2fa-remove");
    if (!token) return;
    var button = submitButton(form);
    var status = $("#acct-remove-status");
    var gen = routeGen;
    var current = function () { return ownsRoute(gen) && formCurrent("#acct-remove-form", form); };
    if (button) button.disabled = true;
    fetchOutcome("/api/admin/account/2fa/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actual: $("#acct-remove-pass") ? $("#acct-remove-pass").value : "" })
    }).then(function (r) {
      if (!current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
          return;
        }
        return reloadAfter(gen);
      });
    }).then(function () {
      end(token);
      if (button && alive()) button.disabled = false;
    }, function () {
      end(token);
      if (button && alive()) button.disabled = false;
    });
  }

  function deleteKey(btn) {
    var token = begin("pk-delete");
    if (!token) return;
    var id = btn.getAttribute("data-key-del");
    var status = passkeyStatusEl();
    var gen = routeGen;
    btn.disabled = true;
    var current = function () { return ownsRoute(gen); };
    fetchOutcome("/api/admin/passkeys/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential_id: id })
    }).then(function (r) {
      if (!current()) return;
      if (r.status === 403) {
        setStatus(status, Passkeys ? Passkeys.mensaje(errText(r.json, "2fa_requerido")) : errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
        return;
      }
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, Passkeys ? Passkeys.mensaje(errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar."))) : errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
          return;
        }
        setStatus(status, T("admin.system.account.passkeys.removed", null, "Passkey quitada."), false);
        return loadPasskeys(gen);
      });
    }).then(function () {
      end(token);
      if (alive() && btn) btn.disabled = false;
    }, function () {
      end(token);
      if (alive() && btn) btn.disabled = false;
    });
  }

  /* Passkey attempt inside the security step: a cancelled or failed attempt
     is recoverable in place — Reintentar runs the same registration again,
     «No me funciona» reveals the Google Authenticator instructions without
     starting the QR flow. Nothing here ever presents the passkey as
     satisfying the 2FA requirement. */
  function paintPkRecovery(message, isError) {
    var status = $("#acct-sec-pk-status");
    if (!status) return;
    setStatus(status, message, !!isError);
    var extra = $("#acct-sec-pk-extra");
    if (extra) extra.innerHTML =
      '<button class="btn btn-quiet btn-sm" type="button" data-pk-retry>'
      + esc(T("admin.system.account.security.retry", null, "Reintentar")) + "</button>"
      + '<button class="btn btn-quiet btn-sm" type="button" data-pk-help>'
      + esc(T("admin.system.account.security.no_works", null, "No me funciona")) + "</button>";
  }

  function showAppHelp() {
    var help = $("#acct-sec-app-help");
    if (help) help.hidden = false;
  }

  function setSetupPanel(open) {
    var panel = $("#acct-security");
    if (panel && panel.classList) panel.classList.toggle("is-setup", !!open);
    var backRow = $("#acct-sec-back-row");
    if (backRow) backRow.hidden = !open;
  }

  function deferSecurity() {
    var panel = $("#acct-security");
    if (!panel) return;
    if (panel.classList) panel.classList.add("is-deferred");
    var row = $("#acct-sec-restore-row");
    if (row) row.hidden = false;
  }

  function restoreSecurity() {
    var panel = $("#acct-security");
    if (!panel) return;
    if (panel.classList) panel.classList.remove("is-deferred");
    var row = $("#acct-sec-restore-row");
    if (row) row.hidden = true;
  }

  function closeSetupPanel() {
    // Retire the abandoned response before exposing a fresh attempt. A late
    // QR must never repaint or re-enable controls owned by the newer setup.
    setupGen += 1;
    delete flights["2fa-start"];
    $$("[data-2fa-start]").forEach(function (button) {
      button.style.display = "";
      button.disabled = false;
    });
    setSetupPanel(false);
    var setupHost = $("#acct-2fa-setup");
    if (setupHost) setupHost.innerHTML = "";
    clearSetup();
    var here = loc();
    var history = hist();
    if (here && history && history.pushState && String(here.pathname || "") + String(here.search || "") !== DIRECT_PATH) {
      history.pushState({}, "", DIRECT_PATH);
    }
  }

  function registerKey() {
    var add = $("#acct-keys-add");
    var status = passkeyStatusEl();
    if (!add || !Passkeys || !Passkeys.soportado()) return;
    if (win().isSecureContext === false) return;
    var token = begin("pk-register");
    if (!token) return;
    var gen = routeGen;
    var panel = !!securityPanel;
    var extra = $("#acct-sec-pk-extra");
    if (extra) extra.innerHTML = "";
    add.disabled = true;
    setStatus(status, T("admin.system.account.passkeys.follow_device", null, "Seguí las instrucciones del dispositivo…"), false);
    Promise.resolve(Passkeys.registrar()).then(function (ack) {
      if (!ownsRoute(gen)) return;
      if (!mutationAck(ack)) throw new Error(errText(ack, T("admin.system.common.save_error", null, "No se pudo guardar.")));
      if (panel) {
        // Honest state: the passkey exists, the mandatory-2FA requirement
        // does not move — current policy counts only the TOTP secret.
        setStatus(status, T("admin.system.account.security.created_pending_tfa", null,
          "Passkey creada. Falta activar la verificación en 2 pasos con la app: es la que el panel exige al ingresar."), false);
      } else {
        setStatus(status, T("admin.system.account.passkeys.registered", null, "Passkey registrada."), false);
      }
      return loadPasskeys(gen);
    }).catch(function (err) {
      if (!ownsRoute(gen)) return;
      if (!panel) {
        setStatus(status, Passkeys.cancelado(err) ? "" : (err && err.message) || "", true);
        return;
      }
      if (Passkeys.cancelado(err)) {
        paintPkRecovery(T("admin.system.account.security.cancelled", null, "Cancelaste la creación de la passkey."), false);
      } else {
        paintPkRecovery((err && err.message) || T("admin.system.account.security.not_created", null, "No se creó la passkey."), true);
      }
    }).then(function () {
      end(token);
      if (add && alive() && Passkeys.soportado()) add.disabled = false;
    });
  }

  function toggleRemove() {
    var box = $("#acct-2fa-confirm");
    if (!box) return;
    if (box.innerHTML) { box.innerHTML = ""; return; }
    box.innerHTML = '<div class="acct-confirm">'
      + "<p>" + esc(T("admin.system.account.remove_tfa.lede", null, "Vas a volver a entrar sólo con tu contraseña. Confirmala para quitarla.")) + "</p>"
      + '<form class="acct-form" id="acct-remove-form">'
      + field(T("admin.system.account.password.title", null, "Contraseña"), "acct-remove-pass", "password", "current-password")
      + '<div class="acct-actions"><button class="btn btn-ok" type="submit">' + esc(T("admin.system.account.remove_tfa.confirm_button", null, "Quitar la verificación")) + "</button>"
      + '<button class="btn btn-quiet" type="button" id="acct-remove-cancel">' + esc(T("admin.system.account.cancel", null, "Cancelar")) + "</button>"
      + '<span class="acct-status" id="acct-remove-status"></span></div></form></div>';
    var input = $("#acct-remove-pass");
    if (input && input.focus) input.focus();
  }

  /* Edit in place: every editable part keeps its closed form in the DOM; its
     «Editar» opens it, Cancelar closes it and puts back what is saved. */
  function openEdit(kind) {
    var form = $("#acct-" + kind + "-form");
    if (!form) return;
    if (kind === "profile") fillProfileForm();
    if (kind === "email") {
      var input = $("#acct-email");
      if (input && accountData) input.value = accountData.email || "";
    }
    form.hidden = false;
    $$('[data-edit="' + kind + '"]').forEach(function (b) { b.hidden = true; });
    var card = $("#acct-profile");
    if (kind === "profile" && card && card.classList) card.classList.add("is-editing");
    var first = form.querySelector ? form.querySelector("input:not([readonly]),select") : null;
    if (first && first.focus) first.focus();
  }

  function closeEdit(kind) {
    var form = $("#acct-" + kind + "-form");
    if (form) {
      form.hidden = true;
      if (kind === "password" && form.reset) {
        form.reset();
        var user = $("#acct-username");
        if (user && accountData) user.value = accountData.usuario || "";
      }
    }
    $$('[data-edit="' + kind + '"]').forEach(function (b) { b.hidden = false; });
    var card = $("#acct-profile");
    if (kind === "profile" && card && card.classList) card.classList.remove("is-editing");
    if (kind === "profile") paintProfile();
  }

  function paintEmail() {
    var value = $("#acct-email-value");
    var trigger = $('[data-edit="email"]');
    var email = accountData && accountData.email;
    if (value) {
      value.textContent = email || "";
      if (value.classList && value.classList.toggle) value.classList.toggle("is-empty", !email);
    }
    if (trigger) trigger.textContent = email ? T("admin.system.account.edit", null, "Editar") : T("admin.system.account.add", null, "Agregar");
  }

  function paintProfile() {
    var name = $("#acct-profile-name");
    if (!name) return;
    var spec = $("#acct-profile-specialty");
    var phone = $("#acct-profile-phone");
    var edit = $("#acct-profile-edit");
    var item = profileData && profileData.item;
    if (!item) {
      name.textContent = "";
      if (spec) spec.textContent = profileData
        ? T("admin.system.account.profile.unlinked", null, "Tu cuenta todavía no tiene un perfil profesional. Pedíselo a la administración.")
        : "";
      if (edit) edit.hidden = true;
      var row = $("#acct-profile-phone-row");
      if (row) row.hidden = true;
      return;
    }
    name.textContent = item.nombre || [item.nombre_pila, item.apellido].filter(Boolean).join(" ");
    if (spec) spec.textContent = item.especialidad || T("admin.system.account.profile.no_specialty", null, "Sin especialidad");
    if (phone) {
      phone.textContent = item.telefono || "";
      if (phone.classList && phone.classList.toggle) phone.classList.toggle("is-empty", !item.telefono);
    }
    var phoneRow = $("#acct-profile-phone-row");
    if (phoneRow) phoneRow.hidden = false;
    var closed = !$("#acct-profile-form") || $("#acct-profile-form").hidden;
    if (edit) edit.hidden = !closed;
    var add = $("#acct-profile-phone-add");
    if (add) add.hidden = !closed || !!item.telefono;
  }

  function fillProfileForm() {
    var item = (profileData && profileData.item) || {};
    var first = $("#acct-profile-first"), last = $("#acct-profile-last"), tel = $("#acct-profile-tel"), spec = $("#acct-profile-spec");
    if (first) first.value = item.nombre_pila || "";
    if (last) last.value = item.apellido || "";
    if (tel) tel.value = item.telefono || "";
    if (spec) {
      var names = (profileData && profileData.especialidades) || [];
      if (item.especialidad && names.indexOf(item.especialidad) < 0) names = [item.especialidad].concat(names);
      spec.innerHTML = (item.especialidad ? "" : '<option value="">' + esc(T("admin.doctor.pick_specialty", null, "Elegí tu especialidad")) + "</option>")
        + names.map(function (n) {
          return '<option value="' + esc(n) + '"' + (n === item.especialidad ? " selected" : "") + ">" + esc(n) + "</option>";
        }).join("");
      spec.value = item.especialidad || "";
    }
  }

  function profileLoadFailed() {
    var name = $("#acct-profile-name");
    if (!name) return;
    name.textContent = "";
    var spec = $("#acct-profile-specialty");
    if (spec) spec.innerHTML = esc(T("admin.system.account.profile.load_error", null, "No se pudo cargar tu perfil."))
      + ' <button class="btn btn-quiet btn-sm" type="button" data-profile-retry>' + esc(T("admin.system.account.profile.retry", null, "Reintentar")) + "</button>";
    var edit = $("#acct-profile-edit");
    if (edit) edit.hidden = true;
    var row = $("#acct-profile-phone-row");
    if (row) row.hidden = true;
  }

  function loadProfile(gen) {
    if (!$("#acct-profile")) return Promise.resolve();
    if (profileCtl) profileCtl.abort();
    var ctl = typeof AbortController === "function" ? new AbortController() : { abort: function () {}, signal: undefined };
    profileCtl = ctl;
    var current = function () { return ownsRoute(gen) && profileCtl === ctl; };
    var spec = $("#acct-profile-specialty");
    if (spec) spec.innerHTML = '<span class="acct-loading">' + esc(T("admin.system.account.profile.loading", null, "Cargando tu perfil…")) + "</span>";
    return fetchOutcome("/api/admin/doctor-profile", { signal: ctl.signal }).then(function (r) {
      if (r.aborted || !current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !isObject(r.json)) { profileLoadFailed(); return; }
        profileData = { item: isObject(r.json.item) ? r.json.item : null,
          especialidades: Array.isArray(r.json.especialidades) ? r.json.especialidades : [] };
        paintProfile();
      });
    });
  }

  function saveProfile(form) {
    if (!formCurrent("#acct-profile-form", form)) return;
    var token = begin("profile");
    if (!token) return;
    var button = submitButton(form);
    var status = $("#acct-profile-status");
    var gen = routeGen;
    var current = function () { return ownsRoute(gen) && formCurrent("#acct-profile-form", form); };
    var value = function (id) { var el = $(id); return el ? String(el.value || "").trim() : ""; };
    var body = { nombre_pila: value("#acct-profile-first"), apellido: value("#acct-profile-last"),
      especialidad: value("#acct-profile-spec"), telefono: value("#acct-profile-tel") };
    if (!body.nombre_pila || !body.apellido || !body.especialidad) {
      setStatus(status, T("admin.srv.account.profile_required", null, "Completá tu nombre, tu apellido y tu especialidad."), true);
      end(token);
      return;
    }
    if (button) button.disabled = true;
    setStatus(status, "", false);
    fetchOutcome("/api/admin/doctor-profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!current()) return;
      return classify(r, current).then(function (go) {
        if (!go || !current()) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, errText(r.json, T("admin.system.common.save_error", null, "No se pudo guardar.")), true);
          return;
        }
        profileData = { item: isObject(r.json.item) ? r.json.item : null,
          especialidades: Array.isArray(r.json.especialidades) ? r.json.especialidades : (profileData ? profileData.especialidades : []) };
        closeEdit("profile");
        paintProfile();
      });
    }).then(function () {
      end(token);
      if (button && alive()) button.disabled = false;
    }, function () {
      end(token);
      if (button && alive()) button.disabled = false;
    });
  }

  function refreshFoto() {
    var av = $("#mifoto-foto-av");
    if (!av) return;
    var gen = routeGen;
    fetchOutcome("/api/mi/foto").then(function (r) {
      if (!ownsRoute(gen) || !r.ok || !isObject(r.json)) return;
      if (r.json.style) {
        av.setAttribute("style", r.json.style);
        av.className = "cnt-foto-av";
        av.textContent = "";
      } else {
        av.removeAttribute("style");
        av.className = "cnt-foto-av cnt-foto-av--" + (r.json.color || 0);
        av.textContent = r.json.ini || "";
      }
      var on = !!r.json.has_upload;
      var quitarA = $("#mifoto-foto-quitar");
      if (quitarA) quitarA.style.display = on ? "" : "none";
      // A doctor without any portrait sees what to do, not «Cambiar».
      var pick = $("#mifoto-foto-pick");
      if (pick) pick.textContent = r.json.style
        ? T("admin.system.account.photo.change", null, "Cambiar foto")
        : T("admin.system.account.photo.add", null, "Agregar foto");
    });
  }

  function onPhotoChange(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    var status = $("#mifoto-status");
    if (file.size > 5 * 1024 * 1024) {
      setStatus(status, T("admin.content.catalog.common.photo_too_large", null, "La foto supera los 5 MB."), true);
      input.value = "";
      return;
    }
    var token = begin("photo");
    if (!token) return;
    var gen = routeGen;
    var reader = new FileReader();
    reader.onload = function () {
      if (!ownsRoute(gen)) { end(token); return; }
      input.disabled = true;
      fetchOutcome("/api/mi/foto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, mime: file.type, data_b64: String(reader.result || "") })
      }).then(function (r) {
        if (!ownsRoute(gen)) return;
        if (!r.ok || !mutationAck(r.json)) {
          setStatus(status, errText(r.json, T("admin.content.catalog.common.photo_upload_failed", null, "No se pudo subir la foto.")), true);
          return;
        }
        input.value = "";
        setStatus(status, "", false);
        return refreshFoto();
      }).then(function () {
        end(token);
        if (alive()) input.disabled = false;
      }, function () {
        end(token);
        if (alive()) input.disabled = false;
      });
    };
    reader.onerror = reader.onabort = function () {
      end(token);
      if (!ownsRoute(gen)) return;
      input.disabled = false;
      input.value = "";
      setStatus(status, T("admin.content.catalog.common.photo_upload_failed", null, "No se pudo subir la foto."), true);
    };
    reader.readAsDataURL(file);
  }

  function onPhotoRemove() {
    var token = begin("photo");
    if (!token) return;
    var status = $("#mifoto-status");
    var gen = routeGen;
    fetchOutcome("/api/mi/foto", { method: "DELETE" }).then(function (r) {
      if (!ownsRoute(gen)) return;
      if (!r.ok || !mutationAck(r.json)) {
        setStatus(status, errText(r.json, T("admin.content.catalog.common.photo_remove_failed", null, "No se pudo quitar la foto.")), true);
        return;
      }
      setStatus(status, T("admin.content.catalog.common.photo_removed", null, "Foto quitada."), false);
      return refreshFoto();
    }).then(function () { end(token); }, function () { end(token); });
  }

  function logout() {
    var btn = doc() && doc().getElementById && doc().getElementById("adm-logout");
    var token = begin("logout");
    if (!token) return;
    var gen = routeGen;
    if (btn) btn.disabled = true;
    function settled() {
      end(token);
      if (!ownsRoute(gen)) return;
      var here = loc();
      // A portal installation: only the portal's own sign-out ends the session.
      var target = (btn && btn.getAttribute && btn.getAttribute("data-portal-logout")) || "/admin/login";
      if (here && typeof here.assign === "function") here.assign(target);
      else if (here) here.href = target;
    }
    fetch("/api/admin/logout", { method: "POST", credentials: "same-origin" })
      .then(settled, settled);
  }

  function onHostClick(event) {
    if (!alive() || !event || !event.target) return;
    var el = event.target;
    var start = el.closest && el.closest("[data-2fa-start]");
    if (start) { abrirSetup(start); return; }
    var del = el.closest && el.closest("[data-key-del]");
    if (del) { deleteKey(del); return; }
    if (el.closest && el.closest("#acct-keys-add")) { registerKey(); return; }
    if (el.closest && el.closest("[data-pk-retry]")) { registerKey(); return; }
    if (el.closest && el.closest("[data-pk-help]")) { showAppHelp(); return; }
    if (el.closest && el.closest("#acct-sec-skip")) { deferSecurity(); return; }
    if (el.closest && el.closest("#acct-sec-restore")) { restoreSecurity(); return; }
    if (el.closest && el.closest("#acct-sec-back")) { closeSetupPanel(); return; }
    if (el.closest && el.closest("#acct-2fa-quitar")) { toggleRemove(); return; }
    if (el.closest && el.closest("#acct-remove-cancel")) {
      var box = $("#acct-2fa-confirm");
      if (box) box.innerHTML = "";
      return;
    }
    if (el.closest && el.closest("#mifoto-foto-quitar")) { onPhotoRemove(); return; }
    var edit = el.closest && el.closest("[data-edit]");
    if (edit) { openEdit(edit.getAttribute("data-edit")); return; }
    var cancel = el.closest && el.closest("[data-cancel]");
    if (cancel) { closeEdit(cancel.getAttribute("data-cancel")); return; }
    if (el.closest && el.closest("[data-profile-retry]")) { loadProfile(routeGen); return; }
  }

  function onHostSubmit(event) {
    if (!alive() || !event || !event.target) return;
    var form = event.target.closest ? event.target.closest("form") : event.target;
    if (!form) return;
    if (event.preventDefault) event.preventDefault();
    if (form.id === "acct-email-form" || (form.closest && form.closest("#acct-email-form"))) saveEmail(form.id === "acct-email-form" ? form : form.closest("#acct-email-form"));
    else if (form.id === "acct-password-form") savePassword(form);
    else if (form.id === "acct-profile-form") saveProfile(form);
    else if (form.id === "acct-confirm-form") confirm2fa(form);
    else if (form.id === "acct-remove-form") remove2fa(form);
  }

  function onHostChange(event) {
    if (!alive() || !event || !event.target) return;
    if (event.target.id === "mifoto-foto-input") onPhotoChange(event.target);
  }

  function onDocumentClick(event) {
    if (!alive() || !event || !event.target) return;
    var copy = event.target.closest && event.target.closest("[data-copy-account]");
    var nav = (typeof window !== "undefined" && window.navigator) || globalThis.navigator;
    if (copy && nav && nav.clipboard && nav.clipboard.writeText) {
      nav.clipboard.writeText(copy.getAttribute("data-copy-account") || "").catch(function () {});
    }
    if (event.defaultPrevented || event.button) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var go = event.target.closest && event.target.closest("a.adm-2fa-go");
    if (!go) return;
    if (event.preventDefault) event.preventDefault();
    var here = loc();
    var history = hist();
    if (here && history && history.pushState && String(here.pathname || "") + String(here.search || "") !== DIRECT_SETUP) {
      history.pushState({}, "", DIRECT_SETUP);
    }
    loadAll();
  }

  function onPopstate() {
    if (!alive()) return;
    loadAll();
  }

  function bind() {
    injectCss();
    listen(host, "click", onHostClick);
    listen(host, "submit", onHostSubmit);
    listen(host, "change", onHostChange);
    listen(doc(), "click", onDocumentClick);
    listen(win(), "popstate", onPopstate);
    var logoutBtn = doc() && doc().getElementById && doc().getElementById("adm-logout");
    if (logoutBtn) {
      logoutBtn.disabled = false;
      listen(logoutBtn, "click", logout);
    }
    return loadAll();
  }

  function take(mod, name, check) {
    if (!mod || !check(mod)) throw new Error("Invalid Account dependency: " + name);
    return mod;
  }

  mounts.set(host, dispose);
  var dependencies = Promise.all([
    importStatic("platform/admin-session.mjs"),
    importStatic("platform/i18n.mjs"),
    importStatic("platform/passkeys.mjs"),
  ]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function (mods) {
    if (!alive()) return;
    Session = take(mods[0], "platform/admin-session.mjs", function (m) { return typeof m.confirmar === "function"; });
    I18n = take(mods[1], "platform/i18n.mjs", function (m) { return typeof m.t === "function"; });
    Passkeys = take(mods[2], "platform/passkeys.mjs", function (m) {
      return typeof m.soportado === "function" && typeof m.registrar === "function"
        && typeof m.cancelado === "function" && typeof m.mensaje === "function"
        && typeof m.disponibleEnEsteEquipo === "function";
    });
    return bind();
  }).catch(function (error) {
    if (!alive()) return;
    if (options.onError) options.onError(error);
    else renderLoadError();
    if (typeof console !== "undefined" && console.error && error) console.error(error);
  });
  return dispose;
}
