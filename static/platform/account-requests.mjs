/* my-requests.js — estado de las solicitudes de turno del paciente.

   Session-first: sacar turno ahora requiere cuenta, así que la lista se pide con
   la cookie de sesión (GET /api/requests SIN ?dni= — el backend resuelve el
   paciente por la sesión; contrato del agente dueño de esa ruta: la sesión gana,
   ?dni= sólo se honra sin sesión). Sin sesión → invitamos a ingresar montando el
   componente de auth (native Account, cargado on-demand). */
/* Canonical Account Requests controller.  The Account shell owns mounting; this
 * module owns only work and listeners created for its current host. */
const mounts = new WeakMap();
export function mount(host, options) {
  "use strict";
  options = options || {};
  if (!host) return function () {};
  if (mounts.has(host)) mounts.get(host)();
  var disposed = false;
  var closeOverlays = new Set();
  var timers = new Set();
  function alive() { return !disposed && host && host.isConnected !== false; }
  var Dates = null;
  var datesUrl = staticUrl("platform/dates.mjs");
  var datesReady = datesUrl ? import(datesUrl).then(function (m) { Dates = m; })
    : Promise.reject(new Error("platform/dates.mjs is not present in window.SANA_STATIC"));
  var Prep = null;
  var prepUrl = staticUrl("platform/prep.mjs");
  var prepReady = prepUrl ? import(prepUrl).then(function (m) { Prep = m; })
    : Promise.reject(new Error("platform/prep.mjs is not present in window.SANA_STATIC"));

  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.reject(new Error("platform/i18n.mjs is not present in window.SANA_STATIC"));
  var ready = Promise.all([i18nReady, datesReady, prepReady]).catch(function (error) {
    return i18nReady.catch(function () {}).then(function () { throw error; });
  });
  ready.catch(function () {});
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // Ya conocemos el DNI de este navegador (se guardó en un login/registro
  // anterior, incluso si después cerró sesión) — no hacerlo tipear de nuevo.
  function loadIdent() {
    try { return JSON.parse(localStorage.getItem("st_identity") || "null") || {}; } catch (e) { return {}; }
  }

  var svcName = {};
  // El primer pintado usa `window.__ACCOUNT_REQUESTS__` (embebido por el
  // servidor, sin esperar red) para no repetir el viejo waterfall — pero ESE
  // pintado corría con svcName todavía vacío, así que la tarjeta mostraba el
  // id crudo del servicio ("medicina-general-riverside-clinic") hasta que este
  // fetch resolvía; con la red lenta (o el catch silencioso de abajo tragando
  // un fallo) se quedaba así para siempre, sin ningún error en consola (bug
  // real, demo, 2026-08-05). `svcNamesReady` deja que nombreServicio() sepa
  // si todavía está esperando el catálogo — en ese caso NUNCA cae al id.
  var svcNamesReady = false;
  function loadServicios() {
    return fetch("/api/services").then(function (r) { return r.json(); }).then(function (j) {
      (j.servicios || []).forEach(function (s) { svcName[s.id] = s.nombre; });
    }).catch(function () {}).then(function () { svcNamesReady = true; });
  }

  // "medicina-general-riverside-clinic" -> "Medicina general riverside clinic": último
  // recurso cuando el catálogo YA cargó y el id sigue sin aparecer (servicio
  // borrado, o una fila de otro pack) — mismo criterio que catalog.py:
  // _humanize() del lado admin (2026-08-01, "un slug nunca debe llegar a la
  // pantalla de nadie"). Nunca el slug crudo, ni acá ni ahí.
  function humanizeId(id) {
    var t = String(id || "").replace(/[-_]+/g, " ").trim();
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : "";
  }

  // Nombre a mostrar para una solicitud: catálogo (id->nombre, /api/services)
  // primero, `servicio_nombre` de la propia fila después (las filas
  // importadas de HSI sólo traen esto, ver booking_patient.patient_rows) —
  // y mientras el catálogo TODAVÍA no cargó, un placeholder neutro en vez del
  // id (el bug de arriba). `fallback` es lo que ya decía cada call site
  // cuando ni el id estaba.
  function nombreServicio(it, fallback) {
    if (svcName[it.servicio_id]) return svcName[it.servicio_id];
    if (it.servicio_nombre) return it.servicio_nombre;
    if (!it.servicio_id) return fallback;
    if (!svcNamesReady) return T("account.mine.requests.loading_name", null, "…");
    return humanizeId(it.servicio_id) || fallback;
  }

  // Dirección + link de Maps del centro, para la tarjeta "Tu próximo turno"
  // (2026-07-19). Mismo patrón que loadServicios: un mapa cargado una vez,
  // best-effort — sin esto la tarjeta simplemente no muestra la línea de
  // dirección, nunca rompe el resto.
  var centroInfo = {};
  function loadCentros() {
    return fetch("/api/centers").then(function (r) { return r.json(); }).then(function (j) {
      (j.centros || []).forEach(function (c) { centroInfo[c.slug] = c; });
    }).catch(function () {});
  }

  // Fechas en formato amigable (fechas.js, wizard-design regla 8) con fallback
  // al texto crudo si el helper no cargó o la fecha no parsea.
  function fechaAmigable(diaIso, hora) {
    if (Dates) { var d = Dates.amigable(diaIso, hora); if (d) return d; }
    return String(diaIso || "") + (hora ? " " + hora : "");
  }
  // «Pedida el 3 de noviembre» / «Pedida hoy»: the day a request was made, in
  // the installation's calendar (`created` may come with an offset or Z).
  function pedidaTexto(it) {
    if (!Dates || typeof Dates.paredInstalacion !== "function" || typeof Dates.pasada !== "function" ||
      typeof Dates.hoyIsoInstalacion !== "function" || !it || !it.created) return "";
    var pared = Dates.paredInstalacion(it.created);
    if (!pared || !pared.dia) return "";
    // Primero hoy: la pasada() de hoy no se usa y no se traduce de más.
    if (pared.dia === Dates.hoyIsoInstalacion()) return T("account.mine.requests.asked_today", null, "Pedida hoy");
    var dia = Dates.pasada(pared.dia);
    if (!dia) return "";
    return T("account.mine.requests.asked_on", { when: dia }, "Pedida el {when}");
  }
  // Wall-clock day of an appointment in the installation's calendar, or "".
  function diaTurno(it) {
    var raw = turnoDt(it);
    if (!raw || typeof raw !== "string") return "";
    var pared = Dates && typeof Dates.paredInstalacion === "function" && Dates.paredInstalacion(raw);
    if (pared) return pared.dia;
    var m = /^(\d{4}-\d{2}-\d{2})/.exec(raw);
    return m ? m[1] : "";
  }
  // La hora sola («09:00») de un turno cuya fecha ya la dice otra cosa — el
  // encabezado «Hoy» del grupo: repetir «Hoy, 09:00» debajo de «Hoy» es
  // información repetida (ui-rules).
  function horaDe(t) {
    if (!t) return "";
    if (typeof t === "object") return String(t.hora || "");
    if (!Dates || typeof Dates.paredInstalacion !== "function") return "";
    var pared = Dates.paredInstalacion(t);
    return pared ? pared.hora : "";
  }
  function horaTurno(it) { return horaDe(turnoDt(it)); }
  function turnoTexto(t) {
    if (!t) return "";
    if (typeof t === "string") {
      // turno_dt llega del backend como ISO datetime ("2026-07-10T08:00:00-04:00")
      // — Dates.delServidor lee fecha y hora literales de la cadena, sin pasar
      // por Date (evita corrimientos de huso) (wizard-design regla 8).
      return Dates ? (Dates.delServidor(t) || t) : t.slice(0, 16).replace("T", " ");
    }
    var bits = [];
    var f = t.fecha ? fechaAmigable(t.fecha, t.hora) : (t.hora || "");
    if (f) bits.push(f);
    if (t.centro) bits.push(t.centro);
    return bits.join(" · ");
  }

  // platform/directory.mjs and platform/account-form.mjs are fingerprinted
  // (core/static_fingerprint.py, CLIENT_LOADED). A served file cannot know its
  // current URL, so the shell emits it in window.SANA_STATIC and staticUrl()
  // resolves it there. This fails closed (null plus console.error, never a
  // fixed fallback URL), matching resolveUrl() in islands.mjs. Without an
  // entry, dynamic import rejects and the existing visible failure path runs.
  function staticUrl(name) {
    var m = (typeof window !== "undefined") && window.SANA_STATIC;
    var u = m && m[name];
    if (!u && typeof console !== "undefined" && console.error) console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
    return u || null;
  }

  function importStatic(name) {
    var u = staticUrl(name);
    return u ? import(u) : Promise.reject(new Error(name + " is not present in window.SANA_STATIC"));
  }

  var directoryModule = null;
  var accountFormReady = null;
  function loadAccountForm() {
    if (!accountFormReady) {
      accountFormReady = importStatic("platform/account-form.mjs").then(function (m) {
        return m;
      }, function (err) {
        accountFormReady = null;
        throw err;
      });
    }
    return accountFormReady;
  }

  // El directorio inline (mismas cards que la home) — no un link pelado.
  function mountDirectorio(elId) {
    if (!directoryModule) {
      directoryModule = importStatic("platform/directory.mjs");
      directoryModule.catch(function () {});
    }
    directoryModule.then(function (mod) {
      if (!alive()) return;
      var el = document.getElementById(elId);
      if (el) mod.render(el, { compact: false });
    }).catch(function () {
      if (!alive()) return;
      var el = document.getElementById(elId);
      if (el) el.textContent = T("upload.result.load_error", null,
        "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.");
    });
  }

  function renderLogin() {
    if (!alive()) return;
    // An embedded Requests view cannot authenticate a different person while
    // leaving the surrounding Account profile owned by the previous session.
    if (options.onUnauthorized) { options.onUnauthorized(); return; }
    // texto plano — nada de cajas blancas que parecen inputs (ui-rules 2026-07-08)
    host.style.minHeight = "";
    host.innerHTML =
      '<p style="margin:0 0 4px;font-size:14.5px;color:var(--text-2);">' +
      T("account.mine.requests.login_lede", null, "Ingresá para ver tus solicitudes. Sacar un turno necesita una cuenta.") + '</p>' +
      '<div id="ms-auth"></div>' +
      '<div id="ms-dir"></div>';
    loadAccountForm().then(function (Account) {
      if (!alive()) return;
      var authHost = document.getElementById("ms-auth");
      if (authHost) {
        Account.renderAuth(authHost, {
          defaultMode: "login",
          heading: false,
          prefill: loadIdent(),
          onDone: function () { load(); }
        });
      }
    }).catch(function () {
      if (!alive()) return;
      var authHost = document.getElementById("ms-auth");
      if (authHost) {
        authHost.innerHTML = '<p style="font-size:14px;"><a class="stz-link" href="/account">' +
          T("account.mine.requests.my_account", null, "Mi cuenta") + '</a></p>';
      }
    });
    mountDirectorio("ms-dir");
  }

  function renderLoadError(error) {
    if (!alive()) return;
    console.error("[my-requests] canonical module load:", error);
    host.style.minHeight = "";
    host.innerHTML = '<p role="alert" style="margin:0;font-size:16px;color:var(--text);">' +
      esc(T("account.mine.requests.load_error", null,
        "No pudimos cargar tus solicitudes. Recargá para intentar de nuevo.")) + "</p>";
  }

  // Un solo estado = una sola frase (responsable del producto, 2026-07-18: "estado y motivo son
  // dos columnas que dicen lo mismo" — se fusionan en UNA). El segundo
  // elemento ES lo que se muestra (no un label separado del motivo): para
  // rechazada, el motivo real (motivo_rechazo) se agrega ADEMÁS, más abajo,
  // en la misma celda (ver estadoCellHtml).
  function statusLabels() { return {
    pendiente: ["amber", T("account.mine.requests.status_pending", null, "El hospital la revisa")],
    aprobada: ["green", T("account.mine.requests.status_approved", null, "Aprobada")],
    aprobado: ["green", T("account.mine.requests.status_approved", null, "Aprobada")],
    rechazada: ["gray", T("account.mine.requests.status_rejected", null, "Rechazada")],
    rechazado: ["gray", T("account.mine.requests.status_rejected", null, "Rechazada")],
  }; }

  // La cancelación se lee según quién canceló (responsable del producto, 2026-10-26): `cancelada_por` viene del
  // servidor ("paciente" / "centro"); sin dato, «Cancelada» a secas — nunca una suposición.
  function cancelledLabel(it) {
    var por = it && it.cancelada_por;
    if (por === "paciente") return T("account.mine.requests.status_cancelled_by_patient", null, "Cancelada por vos");
    if (por === "centro") return T("account.mine.requests.status_cancelled_by_center", null, "Cancelada por el centro");
    return T("account.mine.requests.status_cancelled", null, "Cancelada");
  }

  // ---- "avisame si se libera un turno antes" (checkbox, toggle en el
  // momento) — vive en la tarjeta de la solicitud aprobada con turno futuro
  // (wireCards la monta en .pt-wl cuando hay fecha concreta; it.lista_espera_activo
  // trae el estado real). ----
  function waitlistUi(host, it, opts) {
    opts = opts || {};
    var activo = !!it.lista_espera_activo;
    function render() {
      host.innerHTML =
        // label de una sola línea arriba del umbral móvil (white-space:nowrap
        // en .pt-wl label, responsable del producto 2026-09-15): centrado checkbox-texto, sin el
        // margin-top:2px de la era en que la etiqueta envolvía.
        '<label style="display:flex;align-items:center;gap:8px;font-size:15px;line-height:21px;color:var(--text);cursor:pointer;">' +
        '<input type="checkbox" data-le' + (activo ? " checked" : "") +
        ' style="width:16px;height:16px;margin:0;flex:0 0 auto;accent-color:var(--accent);"> ' +
        T("account.mine.requests.waitlist_toggle", null, "Avisame si se libera un turno antes") + '</label>' +
        '<div data-le-msg style="margin-top:6px;font-size:15px;color:var(--text);"></div>';
      host.querySelector("[data-le]").addEventListener("change", function (ev) { toggle(ev.target.checked); });
    }
    function setMsg(text, isError) {
      if (!alive()) return;
      var m = host.querySelector("[data-le-msg]");
      if (!m) return;
      m.innerHTML = "";
      m.style.color = isError ? "#a3352f" : "var(--text)";
      m.appendChild(document.createTextNode(text));
    }
    function toggle(nuevoValor) {
      if (!alive()) return;
      setMsg(T("account.mine.requests.saving", null, "Guardando…"));
      fetch("/api/requests/" + encodeURIComponent(it.id) + "/waitlist", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ activo: nuevoValor })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
      }).then(function (res) {
        if (!alive()) return;
        var cb = host.querySelector("[data-le]");
        if (res.ok) {
          activo = nuevoValor;
          setMsg(activo
            ? T("account.mine.requests.waitlist_on", null, "Listo, te avisamos por email si se libera un turno antes.")
            : T("account.mine.requests.waitlist_off", null, "Listo, ya no te avisamos."));
        } else if (res.j && res.j.error === "falta_email") {
          if (cb) cb.checked = !nuevoValor;
          var m = host.querySelector("[data-le-msg]");
          if (m) {
            m.innerHTML = "";
            m.style.color = "#a3352f";
            m.appendChild(document.createTextNode(T("account.mine.requests.waitlist_needs_email", null, "Para esto necesitás cargar un email. ")));
            var a = document.createElement("a");
            a.href = "/account"; a.className = "stz-link";
            a.textContent = T("account.mine.requests.waitlist_needs_email_cta", null, "Cargalo en Mi cuenta");
            m.appendChild(a);
          }
        } else {
          if (cb) cb.checked = !nuevoValor;
          setMsg(T("account.mine.requests.save_error", null, "No pudimos guardarlo. Probá de nuevo."), true);
        }
      }).catch(function () {
        if (!alive()) return;
        var cb = host.querySelector("[data-le]");
        if (cb) cb.checked = !nuevoValor;
        setMsg(T("account.mine.requests.save_error", null, "No pudimos guardarlo. Probá de nuevo."), true);
      });
    }
    render();
  }

  // ---- solicitudes como TARJETAS (handoff "Mis Turnos" 2026-07-21, Claude
  // Design) --------------------------------------------------------------------
  // TODAS las solicitudes vigentes (aprobadas futuras y pendientes) usan la
  // MISMA tarjeta rica: foto/ícono 64, título grande, médico, centro + dirección
  // en la misma línea, cuándo a la derecha con "Al calendario". La primera
  // aprobada dice "Tu próximo turno" DENTRO de su propio título (2026-08-02,
  // ver richCardHtml) y trae su preparación abierta.
  // Rechazadas/canceladas/pasadas van bajo "Anteriores" en tarjetas apagadas.
  // Peso tipográfico deliberadamente bajo (responsable del producto, 2026-07-21: "much less bold"):
  // negrita sólo en título, cuándo y el header de la caja de preparación.
  var MT_CSS_ID = "mt-cards-style";
  function injectCardsStyle() {
    if (document.getElementById(MT_CSS_ID)) return;
    var css =
      // Fila de título dentro de la columna (el mockup no tiene encabezado de
      // página aparte): h1 "Mis turnos" (el nombre de la pestaña) + acciones a la derecha.
      ".mt-titlerow{display:flex;align-items:center;justify-content:space-between;gap:14px 20px;flex-wrap:wrap;margin:0 0 26px;}" +
      ".mt-h1{font-size:clamp(28px,3.4vw,38px);font-weight:700;color:var(--text);letter-spacing:-.02em;line-height:1.05;margin:0;}" +
      ".mt-titleacts{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}" +
      ".mt-sacar{display:inline-flex;align-items:center;gap:7px;background:var(--panel);color:var(--text);border:1.5px solid var(--border-strong);" +
        "font:400 15px var(--font);padding:10px 17px;border-radius:var(--btn-radius,9px);text-decoration:none;white-space:nowrap;transition:filter .12s;}" +
      ".mt-sacar:hover{background:var(--hover-bg-soft);border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      // Los controles secundarios son neutros. El acento lleno queda reservado
      // para el contacto clínico (Actualizar síntomas) y el chat con IA.
      ".req-btn{display:inline-flex;align-items:center;gap:7px;background:var(--btn2-bg,var(--panel));color:var(--btn2-fg,var(--text));" +
        "border:1.5px solid var(--btn2-bd,var(--border-strong));" +
        "font:400 15px var(--font);padding:9px 15px;border-radius:var(--btn-radius,9px);cursor:pointer;white-space:nowrap;" +
        "text-decoration:none;transition:background .12s;min-height:44px;}" +
      ".req-btn:hover{background:var(--btn2-bg-hover,var(--hover-bg-soft));border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      ".req-btn svg{flex-shrink:0;}" +
      ".mt-ico{display:inline-block;flex-shrink:0;background:currentColor;-webkit-mask-position:center;mask-position:center;" +
        "-webkit-mask-size:contain;mask-size:contain;-webkit-mask-repeat:no-repeat;mask-repeat:no-repeat;}" +
      ".req-btn-sm{font-size:15px;padding:8px 13px;margin-top:10px;}" +
      // "Al calendario" chico de la columna del cuándo (mockup: 12.5px).
      ".req-btn-xs{font-size:15px;padding:6px 12px;gap:6px;min-height:40px;}" +
      // Primario lleno (2026-08-02, mismo peso que .mp-cta de patient_web/site_*.py
      // — no se reusa esa clase tal cual porque esta tarjeta también vive
      // embebida en /account, que no carga ese CSS; ver injectCardsStyle
      // arriba, self-contenido a propósito).
      ".req-btn-primary{background:var(--btn-bg,var(--accent));border-color:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);font-weight:700;}" +
      // Estado vacio: texto >=18px y un boton lleno de ancho completo, >=56px.
      ".mt-empty{display:flex;flex-direction:column;gap:20px;max-width:560px;}" +
      ".mt-empty-p{margin:0;font-size:18px;line-height:1.5;color:var(--text);}" +
      ".mt-empty-cta{justify-content:center;width:100%;min-height:58px;font-size:19px;padding:14px 22px;box-sizing:border-box;}" +
      ".req-btn-primary:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}" +
      // Cancelar es una accion real, no un boton apagado: tinta oscura en
      // reposo (el gris de --text-2 en el tema del demo se leia deshabilitado).
      ".req-btn-danger{color:var(--btn2-fg,var(--text));border-color:var(--border-strong);}" +
      ".req-btn-danger:hover{background:var(--danger-soft);border-color:var(--danger);color:var(--danger);}" +
      // "Si, cancelar ..." de la confirmacion: el unico boton lleno de la
      // pregunta, en el color de peligro.
      ".req-btn-danger-fill{background:var(--danger);border-color:var(--danger);color:#fff;font-weight:700;}" +
      ".req-btn-danger-fill:hover{background:var(--danger);background-image:var(--hover-fill);border-color:var(--danger);color:#fff;}" +
      // Tarjetas
      ".req-list{display:flex;flex-direction:column;gap:16px;}" +
      ".req{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:20px 22px;" +
        "box-shadow:0 12px 40px -34px rgba(14,26,18,.5);}" +
      ".req-rich{border-radius:16px;padding:24px 26px;}" +
      ".req-muted{background:#fafaf9;border-color:#eceeec;box-shadow:none;}" +
      // Tarjeta rica: la cita es el dato principal. Servicio/profesional a la
      // izquierda; fecha y calendario forman una sola fila a la derecha.
      ".pt-top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:20px;align-items:start;}" +
      ".pt-lead{display:flex;gap:16px;align-items:flex-start;min-width:0;flex:1;}" +
      ".pt-av{width:64px;height:64px;aspect-ratio:1/1;border-radius:50%;object-fit:cover;flex-shrink:0;background:var(--border-soft);}" +
      ".pt-av-init{width:64px;height:64px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;" +
        "justify-content:center;background:linear-gradient(135deg,var(--av-hero-a),var(--av-hero-b));color:#fff;font:700 22px var(--font);}" +
      ".pt-ico{width:64px;height:64px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
      ".pt-title{display:block;font-size:clamp(22px,2.4vw,26px);font-weight:700;color:var(--text);letter-spacing:-.02em;" +
        "line-height:1.12;hyphens:auto;overflow-wrap:break-word;}" +
      ".pt-medico{font-size:16px;margin-top:7px;}" +
      ".pt-medico a{color:var(--text);font-weight:400;text-decoration:none;}" +
      ".pt-medico a:hover{color:var(--hover-link);}" +
      // centro + dirección en la MISMA línea (responsable del producto, 2026-07-21: "the direction
      // is next to the hospital").
      ".pt-lugar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:5px;}" +
      ".pt-centro{display:inline-flex;align-items:center;min-height:24px;font-size:16px;font-weight:400;color:var(--accent);text-decoration:none;}" +
      ".pt-centro:hover{color:var(--accent-hover);}" +
      ".pt-when{min-width:0;text-align:right;}" +
      // Date over «Al calendario», not beside it: a long date plus the link
      // took the whole right column and pushed the title into it.
      ".pt-when-row{display:flex;flex-direction:column;align-items:flex-end;gap:6px;}" +
      ".pt-cuando{font-size:16px;font-weight:700;color:var(--text);letter-spacing:-.01em;white-space:nowrap;}" +
      ".pt-calendar-link{display:inline-flex;align-items:center;gap:5px;color:var(--accent);font-size:15px;" +
        "font-weight:600;text-decoration:none;white-space:nowrap;min-height:24px;}" +
      ".pt-calendar-link:hover{color:var(--accent-hover);}" +
      ".mt-sec-note{margin:2px 0 0;font-size:16px;line-height:1.5;color:var(--text);max-width:60ch;}" +
      ".pt-pref-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px;}" +
      // Acciones alineadas bajo el texto (64 de foto + 16 de gap). La espera
      // vive en el mismo footer, a la derecha, sin competir con los botones.
      // flex-wrap en el footer + .pt-wl sin shrink (responsable del producto, 2026-09-15: el
      // checkbox "avisame si se libera" partía su etiqueta en dos renglones
      // cuando el footer se quedaba sin ancho): si la fila no da para todo,
      // la ESPERA salta entera a su propio renglón — la etiqueta nunca se
      // parte, y los botones quedan en una sola línea.
      ".pt-footer{display:flex;flex-direction:column;align-items:flex-start;gap:12px;margin-top:20px;margin-left:80px;}" +
      ".pt-acts{display:flex;gap:10px;flex-wrap:wrap;align-items:center;flex-shrink:0;}" +
      ".pt-expand{margin-top:16px;}" +
      ".pt-wl{display:flex;justify-content:flex-start;}" +
      ".pt-wl label{display:inline-flex;align-items:center;gap:8px;min-height:44px;white-space:nowrap;}" +
      ".pt-wl input{width:24px!important;height:24px!important;min-width:24px;min-height:24px;}" +
      // Entrevista dentro de Mi cuenta: panel lateral, sin abandonar ni
      // recargar la lista de turnos.
      ".iv-side-shade{position:fixed;inset:0;z-index:410;background:rgba(25,22,17,.32);}" +
      ".iv-side-panel{position:absolute;inset:0 0 0 auto;width:min(470px,100%);background:var(--bg);" +
        "box-shadow:-18px 0 50px rgba(0,0,0,.18);}" +
      ".iv-side-frame{display:block;width:100%;height:100%;border:0;background:var(--bg);}" +
      // Tarjeta apagada (Anteriores)
      ".req-head{display:flex;gap:16px;align-items:flex-start;}" +
      ".req-av{width:44px;height:44px;aspect-ratio:1/1;border-radius:50%;object-fit:cover;flex-shrink:0;background:var(--border-soft);}" +
      ".req-av-init{width:44px;height:44px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;" +
        "justify-content:center;background:linear-gradient(135deg,var(--av-hero-a),var(--av-hero-b));color:#fff;font:700 16px var(--font);}" +
      ".req-icon{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;}" +
      ".req-icon-pend{background:var(--warn-soft);color:#b67a1e;}" +
      ".req-icon-off{background:#eff1ef;color:var(--hint-ink);}" +
      ".req-icon-ok{background:var(--accent-soft);color:var(--accent-hover);}" +
      ".req-main{flex:1;min-width:0;}" +
      ".req-meta{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;}" +
      ".req-left{min-width:0;}" +
      ".req-title{font-size:17px;font-weight:600;color:var(--text);}" +
      // Historial en tinta oscura (2026-11-10): la escala transparente
      // --text-2..4 del tema del demo apagaba fechas y nombres hasta parecer
      // deshabilitados; aqui son datos que la persona viene a leer.
      ".req-title-off{color:var(--text);}" +
      ".req-sub{font-size:15px;color:var(--hint-ink);margin-top:2px;line-height:1.4;}" +
      ".req-sub a{color:var(--hint-ink);font-weight:400;text-decoration:none;}" +
      ".req-sub a:hover{color:var(--hover-ink);}" +
      ".req-status{font-size:15px;margin-top:6px;line-height:1.45;}" +
      ".req-status-rejected{color:var(--danger);}" +
      ".req-status-off{color:var(--hint-ink);}" +
      ".req-right{text-align:right;flex-shrink:0;display:flex;flex-direction:column;align-items:flex-end;}" +
      ".req-dot{display:inline-flex;align-items:center;gap:7px;font-size:15px;font-weight:400;color:#8a5a12;}" +
      ".req-dot:before{content:'';width:7px;height:7px;border-radius:50%;background:currentColor;flex-shrink:0;}" +
      // "Pediste: <fecha>" NO se achica (responsable del producto, 2026-08-01: "don't change the
      // font for the date when the appointment was requested, keep it the same
      // size"): es el dato que la persona vino a mirar, va al mismo cuerpo que
      // la nota de al lado.
      ".req-pref{font-size:15px;color:var(--text);margin-top:0;}" +
      ".req-pref-cal{margin-top:0;}" +
      ".req-when{font-size:15px;font-weight:400;color:var(--text);white-space:nowrap;}" +
      ".req-acts{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:15px;}" +
      ".req-maps{display:inline-flex;align-items:center;gap:6px;font-size:15px;font-weight:400;color:var(--accent);" +
        "text-decoration:none;min-height:24px;}" +
      ".req-maps:hover{color:var(--accent-hover);}" +
      ".req-maps svg{flex-shrink:0;}" +
      ".req-expand{display:inline-flex;align-items:center;gap:6px;background:none;border:0;padding:9px 6px;cursor:pointer;" +
        "color:var(--accent-hover);font:400 15px var(--font);}" +
      ".req-expand .mt-ico{transition:transform .18s;}" +
      ".req-expand.abierto .mt-ico{transform:rotate(180deg);}" +
      // Caja ámbar "Cómo prepararte".
      ".mt-prepbox{background:#fbf4e4;border:1px solid #eadfb8;border-radius:12px;padding:14px 16px;}" +
      ".mt-prepbox-h{display:flex;align-items:center;gap:8px;font-size:16px;font-weight:700;color:#6f4f0c;}" +
      ".mt-prepbox ul{margin:10px 0 0;padding:0;list-style:none;display:flex;flex-direction:column;gap:8px;}" +
      ".mt-prepbox li{display:flex;gap:9px;font-size:15px;line-height:1.45;color:#7a5410;}" +
      ".mt-prepdot{width:5px;height:5px;border-radius:50%;background:#c9a14a;flex-shrink:0;margin-top:7px;}" +
      // "Hoy" / "Próximos" / "Anteriores": encabezado de sección en tinta
      // oscura y en caja normal (2026-11-10) — la línea gris en versalitas era
      // un kicker y se leía apagada.
      ".mt-hist{font-size:var(--section-h-size,18px);font-weight:var(--section-h-weight,650);color:var(--text);" +
        "letter-spacing:-.01em;line-height:1.3;margin:12px 0 -4px;}" +
      ".mt-hist:first-child{margin-top:0;}" +
      // container query, no @media (criterio heredado de la tabla, 2026-07-18):
      // este contenido vive tanto en /my-requests como EMBEBIDO en /account
      // (rail 280px + gap), asi que el umbral de apilado mide el ancho REAL
      // del contenedor, no la ventana.
      ".mt-wrap{container-type:inline-size;container-name:mt;}" +
      // Confirmación de cancelar: crece en el lugar del botón que la abrió —
      // una pregunta que nombra qué se cancela y dos botones de verdad.
      ".mt-confirm{display:flex;flex-direction:column;align-items:flex-start;gap:12px;}" +
      ".mt-confirm-q{margin:0;font-size:16px;font-weight:600;line-height:1.4;color:var(--text);}" +
      ".mt-confirm-btns{display:flex;gap:10px;flex-wrap:wrap;}" +
      ".mt-confirm-note{margin:0;font-size:15px;color:var(--text);}" +
      ".mt-confirm-fail{margin:0;font-size:15px;color:var(--danger);}" +
      "@container mt (max-width:640px){" +
        ".pt-top{grid-template-columns:1fr;gap:14px;}" +
        ".pt-when{text-align:left;}" +
        ".pt-when-row{align-items:flex-start;}" +
        ".pt-cuando{white-space:normal;}" +
        ".pt-footer{margin-left:0;}" +
        ".req-meta{flex-direction:column;gap:10px;}" +
        ".req-right{text-align:left;align-items:flex-start;}" +
        ".req-when{white-space:normal;}" +
      "}" +
      "@container mt (max-width:420px){" +
                // Recién a este ancho la etiqueta de la espera puede volver a envolver:
        // es el umbral donde el mockup apila los botones a full width.
        ".pt-wl label{white-space:normal;}" +
              "}";
    var el = document.createElement("style"); el.id = MT_CSS_ID; el.textContent = css;
    document.head.appendChild(el);
  }

  function turnoDt(it) { return it.turno_dt || it.turno || it.fecha_turno; }
  function esAprob(it) {
    var e = String(it.estado || "").toLowerCase();
    return e === "aprobada" || e === "aprobado";
  }
  // History is a display category, not a claim that care was completed.
  // The server compares concrete appointment times in the hospital timezone.
  function esPasado(it) {
    var e = String(it.estado || "").toLowerCase();
    if (e === "rechazada" || e === "rechazado" || e === "cancelada" || e === "cancelado") return true;
    if (typeof it.turno_pasado === "boolean") return it.turno_pasado;
    // Older embedded payloads may lack the flag. Only parse an explicit
    // offset; treating a hospital wall-clock value as browser-local is wrong.
    var raw = String(turnoDt(it) || "");
    if (/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(raw)) {
      var time = Date.parse(raw);
      if (Number.isFinite(time)) return time <= Date.now();
    }
    return false;
  }
  // se puede cancelar: pendiente, o aprobado con turno futuro.
  function esActivo(it) {
    if (it.hsi_imported || esPasado(it)) return false;
    var e = String(it.estado || "").toLowerCase();
    if (e === "pendiente") return true;
    if (e === "aprobada" || e === "aprobado") return !esPasado(it);
    return false;
  }
  // ¿Esta fila tiene una fecha/hora CONCRETA para agendar? Aprobada futura
  // (turno_dt) o pendiente con un hold vigente (turno elegido en el wizard,
  // ver reservas.py — `hold` lo manda booking_requests._public_row). Una franja
  // ("A la mañana") o "sin elegir todavía" NO cuentan: no hay fecha real que
  // ponerle a un evento de calendario.
  function fechaConcreta(it) {
    var e = String(it.estado || "").toLowerCase();
    if ((e === "aprobada" || e === "aprobado") && turnoDt(it) && !esPasado(it)) return true;
    if (e === "pendiente" && !esPasado(it) && it.hold && it.hold.dia && it.hold.hora) return true;
    return false;
  }
  // Íconos de las tarjetas: archivos del pack SF Regular de Icons8 en
  // static/icons/ (revisión 2026-11-15: nada dibujado a mano), pintados con
  // currentColor por máscara — el tono lo pone el texto de al lado.
  function mtIco(file, px) {
    return '<span class="mt-ico" aria-hidden="true" style="width:' + px + 'px;height:' + px +
      'px;-webkit-mask-image:url(/static/icons/' + file + ');mask-image:url(/static/icons/' + file + ');"></span>';
  }
  var MT_CAL_ICON = mtIco("calendar-plus-sf-regular.svg", 16);
  var MTC_ICO_CLOCK = mtIco("clock-sf-regular.svg", 22);
  var MTC_ICO_CLOCK_LG = mtIco("clock-sf-regular.svg", 30);
  // Chinche de mapa al lado de la dirección (responsable del producto,
  // 2026-08-01: "make the Google Maps icon"): el marker de Icons8.
  var MTC_ICO_DIR = mtIco("marker-sf-regular.svg", 16);
  var MTC_ICO_CLOCK_SM = mtIco("clock-sf-regular.svg", 17);
  var MTC_ICO_WARN = mtIco("error-sf-regular.svg", 22);
  var MTC_ICO_X = mtIco("cancel-sf-regular.svg", 20);
  var MTC_ICO_XS = mtIco("cancel-sf-regular.svg", 16);
  var MTC_ICO_REPRO = mtIco("refresh-sf-regular.svg", 17);
  var MTC_ICO_CHEV = mtIco("expand-arrow-sf-regular.svg", 15);
  var MTC_ICO_CAL = mtIco("calendar-sf-regular.svg", 22);
  var MTC_ICO_CALTODOS = mtIco("calendar-plus-sf-regular.svg", 18);
  var MTC_ICO_PLUS = mtIco("plus-sf-regular.svg", 17);

  // Caja ambar "Como prepararte": aplana las entradas de it.preparacion
  // (ai_json.preparacion, matcheadas en servidor) en UNA lista de pasos —
  // el ayuno primero (la nota redactada si existe, si no la frase de
  // prep.mjs). Es un RESUMEN: sólo lo que hay que hacer antes de venir. Las
  // otras secciones (avisá, no hace falta, después) viven en la tarjeta
  // desplegable de abajo, no en una caja de tres líneas.
  function prepBoxHtml(it) {
    var P = Prep;
    var entries = it.preparacion || [];
    var pasos = [];
    entries.forEach(function (e) {
      var ay = P.ayuno(e);
      if (ay.hay) pasos.push(ay.nota || ay.texto);
      P.antes(e).forEach(function (paso) { pasos.push(paso); });
    });
    if (!pasos.length) return "";
    return '<div class="mt-prepbox"><div class="mt-prepbox-h">' + MTC_ICO_CLOCK_SM +
      T("account.mine.requests.prep_heading", null, "C\u00f3mo prepararte") + "</div><ul>" +
      pasos.map(function (paso) { return '<li><span class="mt-prepdot"></span><span>' + esc(paso) + "</span></li>"; }).join("") +
      "</ul></div>";
  }

  // Avatar de la tarjeta rica (64): aprobada con medico → foto (fallback
  // iniciales); aprobada sin medico → calendario verde (nunca un circulo
  // vacio); pendiente → reloj ambar.
  function richAvatarHtml(it) {
    var e = String(it.estado || "").toLowerCase();
    if (e === "aprobada" || e === "aprobado") {
      var ini = initialsOf(it.medico_asignado);
      if (it.medico_asignado_id) {
        var url = "/doctor-photo/" + encodeURIComponent(it.medico_asignado_id);
        return '<img class="pt-av" src="' + esc(url) + '" alt="" width="64" height="64" ' +
          'onerror="this.outerHTML=\'<span class=&quot;pt-av-init&quot;>' + esc(ini) + '</span>\'">';
      }
      if (ini) return '<span class="pt-av-init">' + esc(ini) + "</span>";
      return '<span class="pt-ico req-icon-ok">' + MTC_ICO_CAL + "</span>";
    }
    return '<span class="pt-ico req-icon-pend">' + MTC_ICO_CLOCK_LG + "</span>";
  }

  // Avatar de la tarjeta apagada (44): rechazada → triangulo gris;
  // cancelada → cruz gris; aprobada pasada → foto/iniciales del medico.
  function mutedAvatarHtml(it) {
    var e = String(it.estado || "").toLowerCase();
    if (e === "aprobada" || e === "aprobado") {
      var ini = initialsOf(it.medico_asignado);
      if (it.medico_asignado_id) {
        var url = "/doctor-photo/" + encodeURIComponent(it.medico_asignado_id);
        return '<img class="req-av" src="' + esc(url) + '" alt="" width="44" height="44" ' +
          'onerror="this.outerHTML=\'<span class=&quot;req-av-init&quot;>' + esc(ini) + '</span>\'">';
      }
      if (ini) return '<span class="req-av-init">' + esc(ini) + "</span>";
      return '<span class="req-icon req-icon-ok">' + MTC_ICO_CAL + "</span>";
    }
    if (e === "pendiente") return '<span class="req-icon req-icon-pend">' + MTC_ICO_CLOCK + "</span>";
    if (e === "rechazada" || e === "rechazado") return '<span class="req-icon req-icon-off">' + MTC_ICO_WARN + "</span>";
    return '<span class="req-icon req-icon-off">' + MTC_ICO_X + "</span>";
  }

  // Tarjeta RICA: todas las solicitudes vigentes (aprobada futura o pendiente)
  // comparten esta anatomia (handoff 2026-07-21: "all the cards are similar") --
  // la primera aprobada trae su preparación abierta por defecto. El contexto
  // de la página ya deja claro que es el próximo turno, así que el título
  // prioriza el nombre concreto del servicio. Todo lo que hay para hacer con la
  // solicitud (cancelar, avisame si se libera antes) vive EN la tarjeta —
  // ya no hay una página de detalle aparte (responsable del producto, 2026-08-01: "everything
  // should be in the acc list"), así que el título es texto plano.
  function richCardHtml(it, idx, opts) {
    opts = opts || {};
    var e = String(it.estado || "").toLowerCase();
    var aprob = e === "aprobada" || e === "aprobado";
    var nombre = nombreServicio(it, T("account.mine.requests.default_name", null, "Solicitud"));
    var centro = centroInfo[it.centro_slug] || {};

    var txt = '<div style="min-width:0;">';
    txt += '<span class="pt-title">' + esc(nombre) + "</span>";
    // El profesional se muestra apenas se sabe, aunque la solicitud todavía
    // espere confirmación (revisión 2026-11-15: la nota decía «te avisamos
    // cuando asigne profesional» con el profesional ya elegido).
    if (it.medico_asignado) {
      var med = it.medico_asignado_id
        ? '<a href="/doctor/' + encodeURIComponent(it.medico_asignado_id) + '">' + esc(it.medico_asignado) + "</a>"
        : esc(it.medico_asignado);
      txt += '<div class="pt-medico">' + med + "</div>";
    }
    if (centro.name) {
      // centro + direccion en la MISMA linea (responsable del producto, 2026-07-21).
      var lugar = '<a class="pt-centro" href="/center/' + esc(it.centro_slug) + '">' + esc(centro.name) + "</a>";
      if (centro.address && centro.maps_url) {
        lugar += '<a class="req-maps" href="' + esc(centro.maps_url) + '" target="_blank" rel="noopener">' +
          MTC_ICO_DIR + esc(centro.address) + "</a>";
      }
      txt += '<div class="pt-lugar">' + lugar + "</div>";
    }
    if (!aprob) {
      // El estado y el aviso viven UNA vez bajo el encabezado de la sección
      // (renderList), no repetidos en cada tarjeta.
      // Lo que la persona ya eligio (slot del wizard / franja) no se pierde.
      var pref = String(it.preferencia_horario || "").trim();
      if (pref || (fechaConcreta(it) && it.id != null)) {
        txt += '<div class="pt-pref-row">';
        if (pref) txt += '<div class="req-pref">' + T("account.mine.requests.you_asked_for", null, "Pediste: ") + esc(pref) + "</div>";
      // Al calendario TAMBIÉN sin confirmar (responsable del producto, 2026-08-01: "even if it's
      // not confirmed yet, people may want to block it in the calendar"). Sólo
      // si eligió un horario concreto en el wizard — una franja no es una
      // fecha. El .ics ya sabía servir la pendiente (calendar_feed.py marca el
      // evento como TENTATIVE); lo que faltaba era el botón. Mismo botón que
      // el de la tarjeta aprobada: misma clase, mismo ícono, mismo texto.
      if (fechaConcreta(it) && it.id != null) {
        txt += '<a class="req-btn req-btn-xs req-pref-cal" href="/api/appointment/' +
          encodeURIComponent(it.id) + '.ics">' + MT_CAL_ICON + T("account.mine.requests.to_calendar", null, "Al calendario") + "</a>";
      }
        txt += "</div>";
      }
    }
    txt += "</div>";
    var showsWaitlist = aprob && esActivo(it) && fechaConcreta(it);

    var when = "";
    if (aprob) {
      var cuandoTxt = opts.soloHora ? (horaTurno(it) || turnoTexto(turnoDt(it))) : turnoTexto(turnoDt(it));
      when = '<div class="pt-when"><div class="pt-when-row"><div class="pt-cuando">' +
        esc(cuandoTxt || T("account.mine.requests.to_confirm", null, "A confirmar")) + "</div>";
      if (fechaConcreta(it) && it.id != null) {
        when += '<a class="pt-calendar-link" href="/api/appointment/' + encodeURIComponent(it.id) + '.ics">' +
          MT_CAL_ICON + T("account.mine.requests.to_calendar", null, "Al calendario") + "</a>";
      }
      when += "</div></div>";
    }

    var actionsRow = "";
    var prepRow = "";
    var waitlistRow = "";
    if (aprob && esActivo(it)) {
      var prep = prepBoxHtml(it);
      // Entrevista previa (modules/intake/): _public_row trae `entrevista`
      // en las filas aprobadas; si falta (intake apagado, fila vieja) el
      // bloque simplemente no entra.
      // Un único primario lleno: la conversación clínica. La reprogramación
      // se retira de esta tarjeta porque duplicaba/confundía el cambio de
      // síntomas; cancelar sigue secundario y sólo se vuelve rojo al hover.
      var acts = "";
      if (it.entrevista && it.entrevista.ofrecer && it.entrevista.url) {
        // Sin contestar → la invitación. YA contestada → poder agregar algo
        // más (the product owner, 2026-08-01: "if the patient did the chat and responded to
        // the questions, there should be an option to add more commentary").
        // El backend ya sabía reabrirla (POST /reopen) y el detalle ya ofrecía
        // el link; lo que faltaba era la salida acá, que es donde la persona
        // mira sus turnos.
        acts += '<button type="button" class="req-btn req-btn-primary" data-entrevista="' +
          esc(it.entrevista.url) + '" data-actualizar="' + (it.entrevista.completada ? "1" : "0") + '">' +
          (it.entrevista.completada
            ? T("account.mine.requests.update_symptoms", null, "Actualizar síntomas")
            : T("account.mine.requests.tell_symptoms", null, "Contar síntomas")) + "</button>";
      }
      if (prep) {
        acts += '<button type="button" class="req-expand' + (opts.expanded ? " abierto" : "") + '" data-exp>' +
          (opts.expanded
            ? T("account.mine.requests.hide_prep", null, "Ocultar preparación ")
            : T("account.mine.requests.show_prep", null, "Cómo preparar ")) + MTC_ICO_CHEV + "</button>";
      }
      acts += '<button type="button" class="req-btn req-btn-danger req-btn-cancel" data-cancel>' +
        MTC_ICO_XS + T("account.mine.requests.cancel_appointment", null, "Cancelar turno") + "</button>";
      actionsRow = '<div class="pt-acts">' + acts + "</div>";
      if (prep) prepRow = '<div class="pt-expand"' + (opts.expanded ? "" : " hidden") + ">" + prep + "</div>";
      // "Avisame si se libera un turno antes" — vivía sólo en /request/<id>;
      // esa página ya no existe, así que la caja se muda acá (responsable del producto,
      // 2026-08-01: "everything should be in the acc list").
      if (showsWaitlist) waitlistRow = '<div class="pt-wl" data-wl></div>';
    } else if (!aprob && it.id != null) {
      // Pendiente: sin turno todavía, así que la única acción es cancelar la
      // solicitud — antes vivía en /request/<id>, ahora en la misma tarjeta
      // (mismo botón chico/rojo/con borde que "Cancelar turno" arriba).
      actionsRow = '<div class="pt-acts"><button type="button" class="req-btn req-btn-danger req-btn-cancel" data-cancel>' +
        MTC_ICO_XS + T("account.mine.requests.cancel_request", null, "Cancelar solicitud") + "</button></div>";
    }

    var rows = (actionsRow || waitlistRow)
      ? '<div class="pt-footer">' + actionsRow + waitlistRow + "</div>" + prepRow
      : prepRow;

    return '<section class="req req-rich" data-idx="' + idx + '">' +
      '<div class="pt-top"><div class="pt-lead">' + richAvatarHtml(it) + txt + "</div>" + when + "</div>" +
      rows +
      "</section>";
  }

  // Tarjeta APAGADA (Anteriores): rechazada con motivo o cancelada, las dos con
  // "Volver a solicitar" (2026-11-10); aprobada ya pasada con su cuando a la derecha.
  function mutedCardHtml(it, idx) {
    var e = String(it.estado || "").toLowerCase();
    var nombre = nombreServicio(it, T("account.mine.requests.default_name", null, "Solicitud"));
    var centro = centroInfo[it.centro_slug] || {};
    var aprob = e === "aprobada" || e === "aprobado";
    var rej = e === "rechazada" || e === "rechazado";
    var canc = e === "cancelada" || e === "cancelado";

    var left = '<span class="req-title req-title-off">' + esc(nombre) + "</span>";
    if (aprob) {
      // Dos hechos distintos (con quién, dónde) — cada uno su propia línea,
      // nunca unidos con " · " (ui-rules), mismo criterio que pt-medico/
      // pt-lugar en la tarjeta activa de arriba.
      if (it.medico_asignado) left += '<div class="req-sub">' + T("account.mine.requests.with_doctor", null, "con ") + esc(it.medico_asignado) + "</div>";
      if (centro.name) left += '<div class="req-sub"><a href="/center/' + esc(it.centro_slug) + '">' + esc(centro.name) + "</a></div>";
    }
    if (rej) {
      left += '<div class="req-status req-status-rejected"><strong>' +
        T("account.mine.requests.rejected_label", null, "Rechazada:") + '</strong> ' + esc(it.motivo_rechazo || "") + "</div>";
    }
    if (canc) left += '<div class="req-status req-status-off">' + cancelledLabel(it) + "</div>";
    var pend = e === "pendiente";
    if (pend) left += '<div class="req-status req-status-off">' + T("account.mine.requests.past_unconfirmed", null, "El hospital no llegó a confirmarla") + "</div>";
    // El día en que se pidió es un dato de la solicitud, no del turno: va a la
    // izquierda, con el estado. La columna derecha dice sólo cuándo era el
    // turno (revisión 2026-11-15: mezclaba las dos fechas).
    if (!aprob) {
      var pedida = pedidaTexto(it);
      if (pedida) left += '<div class="req-status req-status-off">' + esc(pedida) + "</div>";
    }

    // A rejected or cancelled request has lost its hold: `turno_pedido` is the
    // appointment it asked for, kept on the request itself (the product owner 26.10: show
    // the day the patient tried to book).
    var historicalTime = turnoDt(it) || (it.hold && {fecha: it.hold.dia, hora: it.hold.hora}) || it.turno_pedido;
    // A request that never named a slot (a time band, a referral) still gets a
    // date in the history: the day it was asked for (2026-11-10).
    // Bajo el encabezado «Hoy» la tarjeta dice sólo la hora; el resto de la
    // historia, fecha y hora completas.
    var deHoy = aprob && !!Dates && typeof Dates.hoyIsoInstalacion === "function" &&
      diaTurno(it) === Dates.hoyIsoInstalacion();
    var whenTxt = historicalTime
      ? ((deHoy ? horaDe(historicalTime) : "") || turnoTexto(historicalTime))
      : "";
    var estado = whenTxt ? '<div class="req-when">' + esc(whenTxt) + "</div>" : "";

    var rows = "";
    // Una pendiente que se quedó en el pasado tampoco es un callejón sin
    // salida: la misma acción que una rechazada o cancelada.
    if ((rej || canc || pend) && it.servicio_id) {
      rows = '<div class="req-acts"><a class="req-btn" href="/service/' + encodeURIComponent(it.servicio_id) + '/book">' +
        MTC_ICO_REPRO + T("account.mine.requests.request_again", null, "Volver a solicitar") + "</a></div>";
    }

    return '<div class="req req-muted" data-idx="' + idx + '">' +
      '<div class="req-head">' + mutedAvatarHtml(it) +
        '<div class="req-main">' +
          '<div class="req-meta"><div class="req-left">' + left + '</div><div class="req-right">' + estado + "</div></div>" +
          rows +
        "</div>" +
      "</div></div>";
  }

  // La pregunta nombra lo que se cancela (2026-11-10): una aprobada es un
  // turno, una pendiente todavía es una solicitud. Dos botones de verdad,
  // el lleno dice la acción entera — mismo patrón que la cola del panel
  // (admin-queue cancelarConfirmHtml: «Sí, cancelar el turno» / «No»).
  function cancelConfirmHtml(it) {
    var e = String(it.estado || "").toLowerCase();
    var turno = e === "aprobada" || e === "aprobado";
    var q = turno
      ? T("account.mine.requests.confirm_cancel", null, "¿Cancelar el turno?")
      : T("account.mine.requests.confirm_cancel_request", null, "¿Cancelar la solicitud?");
    var si = turno
      ? T("account.mine.requests.confirm_yes_appointment", null, "Sí, cancelar el turno")
      : T("account.mine.requests.confirm_yes_request", null, "Sí, cancelar la solicitud");
    return '<div class="mt-confirm" role="group" aria-label="' + esc(q) + '">' +
      '<p class="mt-confirm-q">' + esc(q) + "</p>" +
      '<div class="mt-confirm-btns">' +
        '<button type="button" class="req-btn req-btn-danger-fill" data-si>' + esc(si) + "</button>" +
        '<button type="button" class="req-btn" data-no>' + T("account.mine.requests.no", null, "No") + "</button>" +
      "</div></div>";
  }

  function openInterviewSide(url, actualizar) {
    if (!alive()) return;
    var shade = document.createElement("div");
    shade.className = "iv-side-shade";
    var joiner = url.indexOf("?") === -1 ? "?" : "&";
    var src = url + joiner + "embed=1" + (actualizar ? "&actualizar=1" : "");
    var ivTitle = T("account.mine.requests.interview_title", null, "Contale al médico cómo te sentís");
    shade.innerHTML = '<div class="iv-side-panel" role="dialog" aria-modal="true" aria-label="' + esc(ivTitle) + '">' +
      '<iframe class="iv-side-frame" title="' + esc(ivTitle) + '" src="' + esc(src) + '"></iframe></div>';
    document.body.appendChild(shade);
    var previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function close() {
      if (!closeOverlays.delete(close)) return;
      window.removeEventListener("message", onMessage);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      shade.remove();
    }
    function onMessage(e) {
      if (e.origin === location.origin && e.data === "humanrounds:close-intake") close();
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    shade.addEventListener("click", function (e) { if (e.target === shade) close(); });
    closeOverlays.add(close);
    window.addEventListener("message", onMessage);
    document.addEventListener("keydown", onKey);
  }

  // Acciones de cada tarjeta: entrevista lateral, Cancelar (confirmación en
  // la MISMA fila de acciones, ui-rules) y el desplegable
  // de preparacion (label que alterna, mockup: "Como preparar" /
  // "Ocultar preparacion").
  function wireCards(root, itemsInOrder) {
    root.querySelectorAll(".req").forEach(function (card) {
      var it = itemsInOrder[Number(card.getAttribute("data-idx"))];
      if (!it) return;
      var ivBtn = card.querySelector("[data-entrevista]");
      if (ivBtn) ivBtn.addEventListener("click", function () {
        openInterviewSide(ivBtn.getAttribute("data-entrevista"), ivBtn.getAttribute("data-actualizar") === "1");
      });
      var exp = card.querySelector("[data-exp]");
      if (exp) exp.addEventListener("click", function () {
        if (!alive()) return;
        var box = card.querySelector(".pt-expand");
        if (!box) return;
        box.hidden = !box.hidden;
        exp.classList.toggle("abierto", !box.hidden);
        exp.innerHTML = (box.hidden
          ? T("account.mine.requests.show_prep", null, "Cómo preparar ")
          : T("account.mine.requests.hide_prep", null, "Ocultar preparación ")) + MTC_ICO_CHEV;
      });
      var wlHost = card.querySelector("[data-wl]");
      if (wlHost) waitlistUi(wlHost, it);
      var cBtn = card.querySelector("[data-cancel]");
      if (!cBtn) return;
      cBtn.addEventListener("click", function () {
        if (!alive()) return;
        var cell = cBtn.parentElement;
        cell.innerHTML = cancelConfirmHtml(it);
        var noBtn = cell.querySelector("[data-no]");
        noBtn.addEventListener("click", function () { renderList(lastItems); });
        // The button that opened the question is gone: keep the keyboard on
        // the safe answer instead of dropping focus to the page.
        if (typeof noBtn.focus === "function") noBtn.focus();
        // The server's answer decides what the patient reads (TASK-55, 26.10):
        // a request no longer active (cancelled in another tab, resolved) is
        // read again and said so above the list; any other failure leaves a
        // «Reintentar» that sends the cancel again — before, the button turned
        // into «No se pudo. Reintentá.» with nothing to press.
        function cancelar() {
          if (!alive()) return;
          // La región viva entra al DOM VACÍA y después recibe su texto: una
          // región insertada ya hablando no se anuncia (spec aria-live). El
          // foco cae en la región, no en el <body>, porque el botón que la
          // abrió ya no está.
          cell.innerHTML = '<div class="mt-confirm" aria-live="polite" tabindex="-1"><p class="mt-confirm-note"></p></div>';
          var viva = cell.querySelector(".mt-confirm");
          var nota = cell.querySelector(".mt-confirm-note");
          if (nota) nota.textContent = T("account.mine.requests.cancelling", null, "Cancelando…");
          if (viva && typeof viva.focus === "function") viva.focus();
          fetch("/api/requests/" + encodeURIComponent(it.id) + "/cancel", { method: "POST", credentials: "same-origin" })
            .then(function (r) {
              if (r.ok) return { ok: true };
              return r.json().catch(function () { return {}; }).then(function (j) { return { ok: false, code: j && j.error }; });
            })
            .then(function (res) {
              if (!alive()) return;
              if (res.ok) { load(); return; }
              if (res.code === "no_cancelable" || res.code === "turno_pasado") {
                avisoLista = res.code === "turno_pasado"
                  ? T("account.mine.requests.already_past", null, "Ese turno ya pasó: no hace falta cancelarlo.")
                  : T("account.mine.requests.already_cancelled", null, "Esta solicitud ya estaba cancelada o resuelta.");
                load();
                return;
              }
              fallo();
            })
            .catch(function () { if (alive()) fallo(); });
        }
        function fallo() {
          // Misma región viva que al cancelar: vacía al entrar, texto después.
          cell.innerHTML = '<div class="mt-confirm" aria-live="polite"><p class="mt-confirm-fail"></p>' +
            '<div class="mt-confirm-btns"><button type="button" class="req-btn" data-reintentar>' +
            T("account.mine.requests.retry", null, "Reintentar") + '</button></div></div>';
          var nota = cell.querySelector(".mt-confirm-fail");
          if (nota) nota.textContent = T("account.mine.requests.cancel_failed_short", null, "No se pudo cancelar.");
          var otra = cell.querySelector("[data-reintentar]");
          if (otra) {
            otra.addEventListener("click", cancelar);
            if (typeof otra.focus === "function") otra.focus();
          }
        }
        cell.querySelector("[data-si]").addEventListener("click", cancelar);
      });
    });
  }

  function initialsOf(nombre) {
    // El tratamiento no es un nombre: "Dra. Lucía Martín" → LM, no DL.
    // p && …: "".split(/\s+/) devuelve [""] (no []), y ""[0] es undefined —
    // sin este guard una aprobada sin médico mostraba "UNDEFINED" en el círculo.
    var partes = String(nombre || "").trim().split(/\s+/).filter(function (p) {
      return p && !/^(dr|dra|lic|od|enf|klgo|klga)\.?$/i.test(p);
    });
    if (!partes.length) return "";
    return (partes[0][0] + (partes.length > 1 ? partes[partes.length - 1][0] : "")).toUpperCase();
  }

  // Suscripción al feed de calendario (todos los turnos aprobados futuros, se
  // actualiza solo cuando el hospital confirma uno nuevo) — link plano con
  // ícono, visible sólo si el feed existe. El link YA NO navega al .ics
  // (responsable del producto, 2026-07-17: descargar el archivo era el comportamiento incorrecto —
  // un feed se SUSCRIBE): abre un popup con la dirección del feed e
  // instrucciones por app de calendario — Google Calendar por defecto,
  // Apple Calendar, Outlook y "otra app" en un <select> (ui-rules: elección
  // única = dropdown, nunca pills). Cada app con botón de un toque donde hay
  // deep link (addbyurl / webcal: / addfromweb) + la dirección copiable como
  // plan B universal.
  var MS_FEED_CSS_ID = "ms-feed-style";
  function injectFeedStyle() {
    if (document.getElementById(MS_FEED_CSS_ID)) return;
    var css =
      // Botón secundario (responsable del producto, 2026-07-18): vive junto a "Sacar turno" en la
      // misma fila, así que necesita chrome propio de botón para no leerse
      // como un link perdido — pero bordeado/panel, nunca accent, para no
      // competir con la acción primaria. ≥44px de alto para el toque.
      ".ms-feed-link{display:inline-flex;align-items:center;gap:8px;padding:10px 16px;min-height:24px;" +
        "border:1.5px solid var(--border-strong);border-radius:var(--btn-radius,9px);background:var(--panel);min-height:44px;" +
        "font-size:15px;font-weight:400;color:var(--text);text-decoration:none;white-space:nowrap;" +
        "transition:border-color .12s,color .12s;}" +
      ".ms-feed-link:hover{border-color:var(--hover-bd-strong);color:var(--hover-ink);}" +
      ".ms-feed-link svg{flex-shrink:0;}" +
      ".ms-cal-overlay{position:fixed;inset:0;background:rgba(25,22,17,.45);z-index:220;display:flex;align-items:center;justify-content:center;padding:16px;}" +
      ".ms-cal-pop{position:relative;background:var(--bg);border-radius:14px;max-width:540px;width:100%;max-height:90vh;overflow-y:auto;padding:24px 26px 22px;box-shadow:0 18px 50px rgba(0,0,0,.28);}" +
      ".ms-cal-x{position:absolute;top:8px;right:8px;}" +
      ".ms-cal-h{margin:0 0 6px;font-size:19px;font-weight:700;color:var(--text);}" +
      ".ms-cal-lede{margin:0 0 16px;font-size:14.5px;line-height:1.5;color:var(--text-2);}" +
      ".ms-cal-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 16px;}" +
      ".ms-cal-lbl{font-size:14.5px;color:var(--text-2);}" +
      ".ms-cal-sel{padding:9px 12px;font:inherit;font-size:15px;color:var(--text);background:var(--panel);border:1px solid var(--border-strong);border-radius:10px;cursor:pointer;}" +
      ".ms-cal-btn{display:inline-block;padding:11px 18px;background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);border-radius:var(--btn-radius,10px);font-size:15px;font-weight:600;text-decoration:none;}" +
      ".ms-cal-btn:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);}" +
      ".ms-cal-steps{margin:14px 0 0;padding-left:22px;font-size:14.5px;line-height:1.5;color:var(--text-2);}" +
      ".ms-cal-steps li{margin:0 0 6px;}" +
      ".ms-cal-nota{margin:12px 0 0;font-size:13.5px;line-height:1.5;color:var(--text-3);}" +
      ".ms-cal-urlrow{display:flex;gap:8px;margin-top:16px;}" +
      ".ms-cal-url{flex:1;min-width:0;padding:10px 12px;font:inherit;font-size:13px;color:var(--text-2);background:var(--panel);border:1px solid var(--border);border-radius:10px;}" +
      ".ms-cal-copy{padding:10px 14px;font:inherit;font-size:14px;font-weight:600;color:var(--text);background:var(--panel);border:1px solid var(--border-strong);border-radius:10px;cursor:pointer;white-space:nowrap;}" +
      ".ms-cal-copy:hover{border-color:var(--hover-bd-strong);}" +
      ".ms-cal-once{margin:14px 0 0;font-size:14.5px;line-height:1.5;color:var(--text-2);}" +
      "button.ms-cal-btn{border:0;font:inherit;font-size:15px;font-weight:600;cursor:pointer;}" +
      "button.ms-cal-btn[disabled]{opacity:.6;cursor:default;}";
    var el = document.createElement("style");
    el.id = MS_FEED_CSS_ID;
    el.textContent = css;
    document.head.appendChild(el);
  }

  // Instrucciones por app. `href(url)` arma el deep link que deja el feed ya
  // cargado; "otro" no tiene deep link universal — sólo copiar y pegar.
  // `copyFirst:true` = la app NO tiene deep link que precargue la dirección,
  // así que el botón la copia al portapapeles antes de abrir la app (queda
  // lista para pegar de una).
  var MS_CAL_APPS = {
    google: {
      // Google DESACTIVÓ el deep link que precargaba el feed (responsable del producto, 2026-07-18:
      // "no pre-fill"): `render?cid=` dejó de funcionar (Google Issue Tracker
      // 442619500, 2025-09) y `addbyurl?cid=<url ics>` ni precarga — tira
      // "No se pudo agregar el calendario. Revisá la URL". No hay forma de
      // precargar la dirección por URL. Entonces: copiamos el feed al
      // portapapeles y abrimos la página "Agregar desde una URL" limpia; la
      // persona sólo pega y confirma.
      nombre: "Google Calendar", // nombre de marca, no se traduce
      btn: T("account.mine.requests.cal_google_btn", null, "Copiar la dirección y abrir Google Calendar"),
      href: function () { return "https://calendar.google.com/calendar/u/0/r/settings/addbyurl"; },
      copyFirst: true,
      pasos: [
        T("account.mine.requests.cal_google_step1", null, "Tocá el botón: copiamos la dirección y se abre Google Calendar en “Agregar desde una URL”."),
        T("account.mine.requests.cal_google_step2", null, "Pegá la dirección (Ctrl+V o ⌘V) en el campo."),
        T("account.mine.requests.cal_google_step3", null, "Confirmá con “Agregar calendario”.")
      ],
      nota: T("account.mine.requests.cal_google_note", null,
        "¿No se pegó sola? Copiala con el botón de acá abajo y pegala en Configuración → Agregar calendario → Desde una URL.")
    },
    apple: {
      nombre: T("account.mine.requests.cal_apple_name", null, "Apple Calendar (iPhone, iPad o Mac)"),
      btn: T("account.mine.requests.cal_apple_btn", null, "Agregar a Apple Calendar"),
      href: function (url) { return url.replace(/^https?:/, "webcal:"); },
      pasos: [
        T("account.mine.requests.cal_apple_step1", null, "Tocá el botón: se abre la app Calendario."),
        T("account.mine.requests.cal_apple_step2", null, "Confirmá con “Suscribirse”.")
      ]
    },
    outlook: {
      nombre: "Outlook", // nombre de marca, no se traduce
      btn: T("account.mine.requests.cal_outlook_btn", null, "Agregar a Outlook"),
      href: function (url) {
        return "https://outlook.live.com/calendar/0/addfromweb?url=" + encodeURIComponent(url) + "&name=" +
          encodeURIComponent(T("account.mine.requests.calendar_name", null, "Mis turnos"));
      },
      pasos: [
        T("account.mine.requests.cal_outlook_step1", null, "Tocá el botón: se abre Outlook con la dirección ya cargada."),
        T("account.mine.requests.cal_outlook_step2", null, "Confirmá con “Importar”.")
      ],
      nota: T("account.mine.requests.cal_outlook_note", null,
        "Si usás Outlook del trabajo, la opción está en Agregar calendario → Suscribirse desde la web: pegá la dirección de acá abajo.")
    },
    otro: {
      nombre: T("account.mine.requests.cal_other_name", null, "Otra app de calendario"),
      pasos: [
        T("account.mine.requests.cal_other_step1", null, "Copiá la dirección de acá abajo."),
        T("account.mine.requests.cal_other_step2", null, "En tu app buscá “Suscribirse a un calendario” o “Agregar calendario por URL”."),
        T("account.mine.requests.cal_other_step3", null, "Pegá la dirección.")
      ]
    }
  };

  // Copia `text` al portapapeles (best-effort, cae a execCommand); devuelve
  // una promesa que resuelve siempre (nunca rechaza) para no romper el flujo.
  function copyToClipboard(text, inputEl) {
    return new Promise(function (resolve) {
      function fallback() {
        try { if (inputEl) { inputEl.select(); document.execCommand("copy"); } } catch (e) {}
        resolve();
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(resolve, fallback);
      } else {
        fallback();
      }
    });
  }

  // The address is a credential (whoever has it reads the patient's
  // appointments), so the server shows it in full only when it is created or
  // replaced, and the last 4 characters afterwards (alembic 0040). It is asked
  // for when the popup opens, never on render, and kept for this page only.
  var feedAddr = null;
  function loadFeed(regen) {
    var req = regen
      ? fetch("/api/calendar/feed/regenerate", { method: "POST", credentials: "same-origin" })
      : fetch("/api/calendar/feed", { credentials: "same-origin" });
    return req.then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j || !(j.url || j.hint)) return null;
        feedAddr = { url: j.url || null, hint: j.hint || "" };
        return feedAddr;
      })
      .catch(function () { return null; });
  }

  function openCalPopup() {
    if (!alive()) return;
    var overlay = document.createElement("div");
    overlay.className = "ms-cal-overlay";
    overlay.innerHTML =
      '<div class="ms-cal-pop" role="dialog" aria-modal="true" aria-labelledby="ms-cal-h">' +
        '<button type="button" class="ms-cal-x hr-x" aria-label="' + T("account.mine.requests.close", null, "Cerrar") + '"></button>' +
        '<h3 class="ms-cal-h" id="ms-cal-h">' + T("account.mine.requests.cal_popup_title", null, "Tus turnos en tu calendario") + '</h3>' +
        '<div id="ms-cal-main"><p class="ms-cal-lede">' +
          esc(T("account.mine.requests.cal_loading", null, "Preparando tu dirección…")) + "</p></div>" +
      "</div>";
    document.body.appendChild(overlay);
    var main = overlay.querySelector("#ms-cal-main");
    function shown() { return alive() && closeOverlays.has(close); }

    function paintAddress(feedUrl) {
      main.innerHTML =
        '<p class="ms-cal-lede">' + T("account.mine.requests.cal_popup_lede", null,
          "Suscribite una sola vez: cada turno que el hospital confirme aparece solo en tu calendario, y si cambia se actualiza.") + '</p>' +
        '<div class="ms-cal-row"><label class="ms-cal-lbl" for="ms-cal-sel">' +
          T("account.mine.requests.cal_which", null, "¿Qué calendario usás?") + '</label>' +
        '<select class="ms-cal-sel" id="ms-cal-sel">' +
          '<option value="google">Google Calendar</option>' +
          '<option value="apple">Apple Calendar</option>' +
          '<option value="outlook">Outlook</option>' +
          '<option value="otro">' + T("account.mine.requests.cal_other_option", null, "Otra app") + '</option>' +
        "</select></div>" +
        '<div id="ms-cal-body"></div>' +
        '<div class="ms-cal-urlrow"><input class="ms-cal-url" id="ms-cal-url" type="text" readonly value="' + esc(feedUrl) + '">' +
        '<button type="button" class="ms-cal-copy" id="ms-cal-copy">' + T("account.mine.requests.copy", null, "Copiar") + '</button></div>' +
        '<p class="ms-cal-once">' + esc(T("account.mine.requests.cal_once", null,
          "Agregala a tu calendario ahora: por seguridad, esta dirección no se vuelve a mostrar.")) + "</p>";

      function paint(key) {
        var app = MS_CAL_APPS[key] || MS_CAL_APPS.otro;
        var html = "";
        if (app.href) {
          html += '<a class="ms-cal-btn" href="' + esc(app.href(feedUrl)) + '" target="_blank" rel="noopener">' + esc(app.btn) + "</a>";
        }
        html += '<ol class="ms-cal-steps">' + app.pasos.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ol>";
        if (app.nota) html += '<p class="ms-cal-nota">' + esc(app.nota) + "</p>";
        var body = main.querySelector("#ms-cal-body");
        body.innerHTML = html;
        // Apps sin deep link que precargue (Google): el botón copia el feed al
        // portapapeles en el mismo gesto del click y deja que el link abra la app.
        if (app.copyFirst) {
          var btnEl = body.querySelector(".ms-cal-btn");
          if (btnEl) btnEl.addEventListener("click", function () { copyToClipboard(feedUrl, main.querySelector("#ms-cal-url")); });
        }
      }
      paint("google");
      main.querySelector("#ms-cal-sel").addEventListener("change", function () { paint(this.value); });

      var urlInput = main.querySelector("#ms-cal-url");
      urlInput.addEventListener("focus", function () { this.select(); });
      main.querySelector("#ms-cal-copy").addEventListener("click", function () {
        var btn = this;
        copyToClipboard(feedUrl, urlInput).then(function () {
          if (!alive()) return;
          btn.textContent = T("account.mine.requests.copied", null, "Copiada");
          var timer = setTimeout(function () { timers.delete(timer); if (alive()) btn.textContent = T("account.mine.requests.copy", null, "Copiar"); }, 2000);
          timers.add(timer);
        });
      });
    }

    // The address exists and only its last 4 characters are known: nothing to
    // do if the calendar is already subscribed; a new one replaces it.
    function paintHint(hint, failed) {
      main.innerHTML =
        '<p class="ms-cal-lede">' + esc(T("account.mine.requests.cal_exists", { hint: hint },
          "Ya tenés una dirección de calendario, la que termina en {hint}. Si tus turnos aparecen en tu calendario, no hace falta nada más.")
          .split("{hint}").join(hint)) + "</p>" +
        '<p class="ms-cal-lede">' + esc(T("account.mine.requests.cal_hidden", null,
          "Por seguridad no se vuelve a mostrar. Para agregarlos ahora o en otro calendario, creá una dirección nueva: la anterior deja de actualizarse.")) + "</p>" +
        (failed ? '<p class="ms-cal-lede" role="alert">' + esc(T("account.mine.requests.cal_regen_retry", null,
          "No pudimos crearla ahora. Probá de nuevo en un momento.")) + "</p>" : "") +
        '<button type="button" class="ms-cal-btn" id="ms-cal-regen">' +
          esc(T("account.mine.requests.cal_regenerate", null, "Crear una dirección nueva")) + "</button>";
      var btn = main.querySelector("#ms-cal-regen");
      btn.addEventListener("click", function () {
        btn.disabled = true;
        loadFeed(true).then(function (a) {
          if (!shown()) return;
          if (a && a.url) paintAddress(a.url);
          else paintHint(hint, true);
        });
      });
    }

    function paintState(a) {
      if (!shown()) return;
      if (a && a.url) paintAddress(a.url);
      else if (a && a.hint) paintHint(a.hint, false);
      else main.innerHTML = '<p class="ms-cal-lede" role="alert">' + esc(T("account.mine.requests.cal_load_retry", null,
        "No pudimos traer tu dirección ahora. Cerrá y probá de nuevo en un momento.")) + "</p>";
    }

    function close() {
      if (!closeOverlays.delete(close)) return;
      document.removeEventListener("keydown", onKey);
      overlay.remove();
    }
    function onKey(e) { if (e.key === "Escape") close(); }
    overlay.querySelector(".ms-cal-x").addEventListener("click", close);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
    closeOverlays.add(close);
    document.addEventListener("keydown", onKey);

    if (feedAddr && feedAddr.url) paintState(feedAddr);
    else loadFeed(false).then(paintState);
  }

  function renderFeed(elId) {
    var el = document.getElementById(elId);
    if (!el) return;
    injectFeedStyle();
    // Boton outline junto a "Sacar turno" (mockup): suscribe TODOS los
    // turnos de una vez, de ahi el nombre. La dirección se pide recién al
    // abrir el popup: el servidor la muestra entera una sola vez.
    el.innerHTML = '<button type="button" class="req-btn ms-feed-link">' + MTC_ICO_CALTODOS +
      T("account.mine.requests.add_all_to_calendar", null, "Agregar todos al calendario") + "</button>";
    el.querySelector(".ms-feed-link").addEventListener("click", function () { openCalPopup(); });
  }

  var lastItems = null;
  // One line said once above the list after a reload it caused (TASK-55).
  var avisoLista = "";
  function avisoHtml() {
    var t = avisoLista;
    avisoLista = "";
    return t ? '<p class="mt-aviso" role="status" style="margin:0 0 12px;font-size:16px;color:var(--text);">' + esc(t) + "</p>" : "";
  }
  // Fila de titulo del mockup: h1 "Mis turnos" (el de la pestaña) + acciones ("Sacar turno"
  // primero, despues el feed — orden del handoff 2026-07-21). Vive DENTRO de
  // la columna de contenido: la seccion se titula sola, a la derecha del menu.
  function titleRowHtml(sinSacar) {
    // Lista vacía: el único botón lleno es el del cuerpo (instrucción + CTA);
    // el secundario «Sacar turno» de la fila no se repite.
    return '<div class="mt-titlerow"><h1 class="mt-h1">' + T("account.dash.nav_turnos", null, "Mis turnos") + '</h1>' +
      '<div class="mt-titleacts">' + (sinSacar ? "" : '<a class="mt-sacar" href="/">' + MTC_ICO_PLUS +
      T("account.mine.requests.book_appointment", null, "Sacar turno") + "</a>") +
      '<span id="ms-feed"></span></div></div>';
  }
  function renderList(items) {
    if (!alive()) return;
    lastItems = items;
    injectCardsStyle();
    // The account shell paints before its catalogs resolve. Keep the footer
    // below the initial viewport, then swap the reserve and final cards in a
    // single task so patients never see the page jump while it loads.
    host.style.minHeight = "";
    if (!items || !items.length) {
      // Estado vacio = instruccion con su boton (patient-flow-design): que
      // hacer ahora, en las palabras de la persona, y UN boton lleno grande.
      // El directorio completo solo en la pagina standalone (hoy redirigida
      // a /account, se conserva por si vuelve).
      var vacio = '<div class="mt-empty">' +
        '<p class="mt-empty-p">' +
        T("account.mine.requests.empty_text", null, "Todav\u00eda no ten\u00e9s turnos. Para sacar el primero, eleg\u00ed una especialidad y un horario.") + '</p>' +
        '<a class="req-btn req-btn-primary mt-empty-cta" href="/">' +
        T("account.mine.requests.empty_book", null, "Sacar un turno") + '</a></div>';
      host.innerHTML = '<div class="mt-wrap">' + titleRowHtml(true) + avisoHtml() + vacio +
        (host.getAttribute("data-embedded") ? "" : '<div id="ms-dir"></div>') + "</div>";
      if (!host.getAttribute("data-embedded")) mountDirectorio("ms-dir");
      return;
    }
    // Actuales: aprobadas futuras por fecha ascendente, despues las
    // pendientes (orden de llegada) — todas con la MISMA tarjeta rica; la
    // primera aprobada por venir (la mas cercana en el tiempo) dice "Tu
    // proximo turno" en su propio titulo y trae su preparacion abierta.
    // Anteriores: pasadas/resueltas, en tarjetas apagadas.
    // «Hoy» on top (2026-11-10): today's approved visits — the one still to
    // come and the one that already happened this morning — are one group of
    // their own, never mixed into «Anteriores» a few rows down; under the
    // heading the card says just the time, the heading already says the day.
    // Una sola pasada por la lista: el día de cada turno se calcula una vez.
    var hoyIso = Dates && typeof Dates.hoyIsoInstalacion === "function" ? Dates.hoyIsoInstalacion() : "";
    var dias = items.map(function (it) { return hoyIso ? diaTurno(it) : ""; });
    var hoy = [], futuros = [], pendientes = [], pasados = [];
    items.forEach(function (it, i) {
      if (hoyIso && dias[i] === hoyIso && esAprob(it)) { hoy.push(it); return; }
      if (esPasado(it)) { pasados.push(it); return; }
      if (esAprob(it)) futuros.push(it); else pendientes.push(it);
    });
    var porTurno = function (a, b) { return String(turnoDt(a) || "").localeCompare(String(turnoDt(b) || "")); };
    hoy.sort(porTurno);
    futuros.sort(porTurno);
    // Sin confirmar no se mezcla con «Próximos» (revisión 2026-11-15): las
    // pendientes tienen su propio encabezado y UNA sola nota debajo, en vez
    // de repetir el aviso gris en cada tarjeta.
    var orden = hoy.concat(futuros, pendientes, pasados);
    // «Tu próximo turno» es el primer aprobado que TODAVÍA no pasó — puede
    // ser el de hoy a la tarde, aunque el de hoy a la mañana ya esté apagado.
    var proximo = null;
    orden.forEach(function (it) { if (!proximo && esAprob(it) && !esPasado(it)) proximo = it; });
    var html = titleRowHtml() + avisoHtml() + '<div class="req-list">';
    function seccion(key, fallback) { return '<h2 class="mt-hist">' + T(key, null, fallback) + "</h2>"; }
    orden.forEach(function (it, i) {
      if (hoy.length && it === hoy[0]) html += seccion("account.mine.requests.today", "Hoy");
      if ((hoy.length || pendientes.length) && futuros.length && it === futuros[0]) html += seccion("account.mine.requests.upcoming", "Próximos");
      if (pendientes.length && it === pendientes[0]) {
        html += seccion("account.mine.requests.awaiting", "Esperan confirmación del hospital") +
          '<p class="mt-sec-note">' + T("account.mine.requests.pending_note", null,
            "Te avisamos por acá apenas el hospital confirme el día y la hora.") + "</p>";
      }
      if (pasados.length && it === pasados[0]) html += seccion("account.mine.requests.previous", "Anteriores");
      html += esPasado(it)
        ? mutedCardHtml(it, i)
        : richCardHtml(it, i, { eyebrow: it === proximo, expanded: it === proximo, soloHora: hoy.indexOf(it) !== -1 });
    });
    html += "</div>";
    host.innerHTML = '<div class="mt-wrap">' + html + "</div>";
    wireCards(host, orden);
    // «Agregar todos al calendario» only when there is an appointment to add
    // (the product owner 26.10): a list of past or rejected requests offers nothing.
    if (orden.some(fechaConcreta)) renderFeed("ms-feed");
  }

  function load() {
    if (!alive()) return;
    fetch("/api/requests", { credentials: "same-origin" })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) { renderLogin(); return null; }
        if (!r.ok) throw new Error("Requests lookup failed");
        return r.json();
      })
      .then(function (j) {
        if (!alive()) return;
        if (j == null) return; // ya mostramos el login
        var items = Array.isArray(j) ? j : (j.solicitudes || j.items || []);
        renderList(items);
      })
      .catch(renderLoadError);
  }

  if (host) {
    ready.then(function () {
    if (!alive()) return;
    var preloaded = options.requests;
    // Un solo pintado con datos completos (regla "async refresh en UN solo
    // paint", ui-rules) — antes se pintaba dos veces: apenas montaba (con
    // svcName/centroInfo todavía vacíos) y de nuevo cuando loadServicios()/
    // loadCentros() resolvían. Ese segundo host.innerHTML de reemplazo total
    // pisaba cualquier interacción abierta en la tarjeta (el "¿Cancelar el
    // turno? Sí/No" desaparecía a mitad de click, bug real, 2026-08-05 —
    // ver test/e2e/cancel-waitlist.e2e.mjs). El catálogo SÍ aporta datos que
    // el primer pintado no tenía (nombre del servicio, dirección del centro),
    // así que no alcanza con pintar antes: se espera a que ambos catálogos
    // respondan y se pinta una única vez, con un "Cargando…" mientras tanto
    // (mismo patrón que lab-results.js/admin-*.js) — nunca dos pintados
    // completos en sucesión.
    if (host.getAttribute("data-embedded")) {
      host.style.minHeight = "min(760px, calc(100vh - 180px))";
    }
    host.innerHTML = '<p style="margin:0;font-size:16px;color:var(--text);">' +
      T("account.mine.requests.loading", null, "Cargando…") + "</p>";
    if (Array.isArray(preloaded)) {
      Promise.all([loadServicios(), loadCentros(), datesReady, prepReady]).then(function () { renderList(preloaded); })
        .catch(renderLoadError);
    } else {
      Promise.all([loadServicios(), loadCentros(), datesReady, prepReady]).then(load)
        .catch(renderLoadError);
    }
    }).catch(renderLoadError);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    closeOverlays.forEach(function (close) { close(); });
    timers.forEach(clearTimeout); timers.clear();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }
  mounts.set(host, dispose);
  return dispose;
}
