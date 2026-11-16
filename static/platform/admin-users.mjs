/* static/platform/admin-users.mjs — alta/edición de usuarios del panel admin
   (Pinamar Turnos). Its direct URL state is /admin/users, ?user=<usuario>, or
   ?new=1. The controller owns its rendered screen and uses pushState/popstate
   for in-page list/editor navigation.

   Canonical ES module (2026-09-05 cutover, behavior-frozen from the classic
   IIFE). Named exports (boot, renderDoctorUser, refresh); no compatibility
   global, no classic twin, no fallback URL. Loaded through window.SANA_STATIC
   / import() by templates/admin/users.html (boot), admin-content.js
   (renderDoctorUser), and admin-account.js (refresh). Boot and the Doctors
   panel await readiness. Account's post-save cache refresh is optional and
   detached: it must not delay a successful email save or keep its button busy.

   Top-level await, one block per dependency, preserving the classic failure
   semantics of the old dep-promise prologue: i18n, format, preferences (+ its
   load()), session and table are awaited fail-closed (a rejected dependency
   rejects this module's import, so every consumer's load-error path runs);
   dates.mjs stays fail-open to null (fechaVisita keeps its raw-ISO fallback,
   the one deliberate soft dependency). Because the module body evaluates
   AFTER i18n resolves, ROLES/COLS_ALL labels are live T() at init — the
   classic file needed localizeColumns() to re-label columns it had built
   before its i18n promise settled; that repair pass is gone with the
   ordering, not dropped behavior.

   Stale-screen guards: route() takes a monotonic routeSeq token and every
   async paint (list, form, error) re-checks it — a late fetch for an
   abandoned route never repaints the screen the user already left.
   renderDoctorUser() checks host.isConnected before painting: its caller is
   the doctors editor's Usuario tab, which can be torn down while the users
   fetch is in flight.

   Contrato de API (admin_users.py, todo rol 'admin' — admin_auth.
   require_rol_admin, responde 401/403 si falta):
     GET    /api/admin/users              -> {items:[{usuario,rol,activo,tiene_2fa,ultimo_acceso,es_dueno,
                                              cuenta_estado,medico_id,invitado_en}]}
     POST   /api/admin/users/actions/{invite,create,disable,reset-password,reset-2fa}
                                           body={medico_ids:[id],email?} -> {resultados:[{ok,usuario,email_enviado,email?,enlace?,password?}]}
   A doctor's account (rol médico WITH medico_id) is shown with the doctor in
   Médicos, not in this list; a new médico is never created from here.
     POST   /api/admin/users               body={usuario,password,rol,forzar_2fa}
     PUT    /api/admin/users/<usuario>     body={rol?,activo?,password?,totp?,forzar_2fa?}
     DELETE /api/admin/users/<usuario>     -> {ok}   (desactiva; nunca borra en duro)
     POST   /api/admin/users/actions/link  body={medico_ids:[id],usuario}
   `forzar_2fa=true` revoca el código actual y bloquea el resto del panel en
   el próximo ingreso hasta que la persona configure uno nuevo en Cuenta.
   La bandera se limpia automáticamente al terminar esa configuración.
   `totp="quitar"` conserva la acción administrativa de quitarlo sin exigir
   reemplazo inmediato (entra al plazo normal de 7 días).
*/
"use strict";

function importDep(name, nodeUrl) {
  if (typeof window !== "undefined") {
    var u = (window.SANA_STATIC && window.SANA_STATIC[name]) || null;
    if (!u) {
      if (typeof console !== "undefined" && console.error) {
        console.error("[static] " + name + " is not in window.SANA_STATIC");
      }
      return Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
    }
    return import(u);
  }
  return import(nodeUrl);
}

var Dates = await importDep("platform/dates.mjs", new URL("./dates.mjs", import.meta.url))
  .catch(function () { return null; });
var Format = await importDep("platform/format.mjs", new URL("./format.mjs", import.meta.url));
var Prefs = await importDep("platform/admin-preferences.mjs", new URL("./admin-preferences.mjs", import.meta.url));
await Prefs.load();
var Session = await importDep("platform/admin-session.mjs", new URL("./admin-session.mjs", import.meta.url));
var Table = await importDep("platform/admin-table.mjs", new URL("./admin-table.mjs", import.meta.url));
var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url));
// The shared progressive-disclosure rule (empty / no-match / data).
var Filters = await importDep("platform/admin-filter-controls.mjs", new URL("./admin-filter-controls.mjs", import.meta.url));

  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Number agreement through i18n.mjs tn() — «1 libre», never «1 libres».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
  // A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
  function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  function toast(msg) {
    if (window.toast) { window.toast(msg); return; }
    var status = document.getElementById("users-status");
    if (!status) return;
    status.textContent = msg;
    status.hidden = false;
  }
  function nombre(value) {
    return Format.nombre(value);
  }

  /* Espejo de core/usernames.py (ahí vive la regla canónica): al menos 2
     caracteres y ningún espacio ni carácter de control. Sin tope de largo ni
     alfabeto ASCII — un apellido con tilde o con ñ es un nombre de usuario
     perfectamente válido. */
  var PASSWORD_MIN = 8;
  var USERNAME_MIN = 2;
  var USERNAME_MAX = 120;
  var USERNAME_MAL = /[\s\u0000-\u001f\u007f]/;
  function validUsername(value) {
    value = value || "";
    return value.length >= USERNAME_MIN && value.length <= USERNAME_MAX
      && !USERNAME_MAL.test(value);
  }

  function directState() {
    var params = new URLSearchParams(location.search);
    var users = params.getAll("user");
    var fresh = params.getAll("new");
    var state = { sec: "usuarios", id: null };
    if (users.length === 1 && validUsername(users[0]) && fresh.length === 0) {
      state.id = users[0];
    } else if (fresh.length === 1 && fresh[0] === "1" && users.length === 0) {
      state.id = "nuevo";
    }
    var canonical = state.id === "nuevo"
      ? "/admin/users?new=1"
      : (state.id ? "/admin/users?user=" + encodeURIComponent(state.id) : "/admin/users");
    if (location.pathname + location.search !== canonical) {
      history.replaceState({}, "", canonical);
    }
    return state;
  }

  function userHref(usuario) {
    return "/admin/users?user=" + encodeURIComponent(usuario);
  }


  function listHref() {
    return "/admin/users";
  }

  // An open user reads «Usuarios / name» in the screen's own title band, the
  // way a doctor's card does (admin-content): no empty band above a crumb and
  // a second heading in the page (ui-expert 15.11).
  function titleCrumb(name) {
    var h1 = $("#adm-h1");
    if (!h1) return;
    h1.style.display = "";
    h1.innerHTML = '<a class="adm-crumb" href="' + listHref() + '" data-users-state="list">'
      + esc(T("admin.system.users.title", null, "Usuarios")) + '</a><span class="adm-crumb-sep">/</span>' + esc(name);
  }

  function accountHref() {
    return "/admin/account";
  }

  function goTo(href) {
    history.pushState({}, "", href);
    route();
  }

  // Las opciones de "qué puede hacer" — dichas como las diría alguien del
  // hospital, no como el nombre del campo en la base (ui-microcopy: "rol:
  // admin" es jerga; "administra todo" es lo que un compañero le diría a otro).
  var ROLES = [
    ["medico", T("admin.system.users.role.medico.label", null, "Profesional"),
      T("admin.system.users.role.medico.desc", null,
        "Ve las solicitudes asignadas, sus pacientes de hoy y sus turnos. Puede reasignar una solicitud a otro médico.")],
    ["admisionista", T("admin.system.users.role.admisionista.label", null, "Mesa de admisión"),
      T("admin.system.users.role.admisionista.desc", null, "Ve solicitudes y pacientes de hoy. Puede crear pacientes y turnos.")],
    ["reportes", T("admin.system.users.role.reportes.label", null, "Ve reportes y estadísticas"),
      T("admin.system.users.role.reportes.desc", null, "Sólo entra a Reportes: ocupación, tiempos de atención y estadística mensual.")],
    ["admin", T("admin.system.users.role.admin.label", null, "Administrador"),
      T("admin.system.users.role.admin.desc", null, "Puede hacer todo: atención, catálogo, usuarios, configuración y reportes.")]
  ];

  function roleChoices(data, selected) {
    var roles = Array.isArray(data.roles) && data.roles.length
      ? data.roles.filter(function (r) { return r && r.slug && r.enabled !== false; }).map(function (r) {
          var fallback = ROLES.filter(function (entry) { return entry[0] === r.slug; })[0];
          return [r.slug, r.name || r.nombre || (fallback ? fallback[1] : r.slug), r.description || (fallback ? fallback[2] : "")];
        }) : ROLES.slice();
    if (selected && !roles.some(function (r) { return r[0] === selected; })) {
      // Preserve an existing disabled role; never silently save it as medico.
      var existing = (data.items || []).filter(function (item) { return item.rol === selected; })[0];
      roles.push([selected, (existing && existing.rol_nombre) || selected, ""]);
    }
    return roles;
  }

  function rolLabel(rol) {
    var f = roleChoices(CACHE || {}).filter(function (r) { return r[0] === rol; })[0];
    var existing = ((CACHE && CACHE.items) || []).filter(function (item) { return item.rol === rol; })[0];
    return f ? f[1] : ((existing && existing.rol_nombre) || rol);
  }

  var CACHE = null;
  var MEDICOS_CACHE = null;
  var FILTERS = { q: "", rol: "", estado: "" };
  var SORT = { key: "usuario", dir: 1 };

  function prefsGet(key, fallback) {
    return Prefs ? Prefs.get(key, fallback) : fallback;
  }
  function prefsSet(key, value) {
    if (Prefs) Prefs.set(key, value);
  }
  var PREFS_HYDRATED = false;
  function hydratePrefs() {
    if (PREFS_HYDRATED) return;
    FILTERS = Object.assign(FILTERS, prefsGet("users.filters", {}));
    SORT = Object.assign(SORT, prefsGet("users.sort", {}));
    PREFS_HYDRATED = true;
  }

  // Catálogo de médicos para el <select> de la cuenta 'medico' — mismo
  // endpoint que usa admin-contenido.js (solo_admin, así que sólo lo pega
  // quien ya está creando/editando usuarios).
  function fetchMedicos(force, requestOptions, isCurrent) {
    if (!force && MEDICOS_CACHE) return Promise.resolve(MEDICOS_CACHE);
    return fetch("/api/admin/content/doctors", Object.assign({ credentials: "same-origin" }, requestOptions || {})).then(function (res) {
      if (!res.ok) throw new Error("bad-" + res.status);
      return res.json().then(function (j) {
        var items = (j.items || []).filter(function (m) { return m && m.id && m.nombre && !m._borrado; })
          .sort(function (a, b) { return String(a.nombre).localeCompare(String(b.nombre), "es"); });
        if (!isCurrent || isCurrent()) MEDICOS_CACHE = items;
        return items;
      });
    });
  }

  var CENTROS_CACHE = null;
  function fetchCentros(force, isCurrent) {
    if (!force && CENTROS_CACHE) return Promise.resolve(CENTROS_CACHE);
    return fetch("/api/admin/content/centers", { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) throw new Error("bad-" + res.status);
      return res.json().then(function (j) {
        var items = (j.items || []).filter(function (c) { return c && c.slug && !c._borrado; })
          .sort(function (a, b) { return String(a.name).localeCompare(String(b.name), "es"); });
        if (!isCurrent || isCurrent()) CENTROS_CACHE = items;
        return items;
      });
    }).catch(function () { return []; });
  }

  function fetchLista(force, isCurrent, requestOptions) {
    if (!force && CACHE) return Promise.resolve(CACHE);
    var current = typeof isCurrent === "function" ? isCurrent : function () { return true; };
    return fetch("/api/admin/users", Object.assign({ credentials: "same-origin" }, requestOptions || {})).then(function (res) {
      if (!current()) return null;
      var boundary = (res.status === 401 || res.status === 403)
        ? Session.confirmar(res, undefined, current)
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        if (!current()) return null;
        // Sólo un 401 que whoami confirma se lleva la pantalla a otra página
        // ("redirect"); uno sin confirmar (Basic Auth del sitio, un proxy)
        // llega a «No se pudo cargar», no a una pantalla muda.
        if (transitioned) throw new Error("redirect");
        if (res.status === 401) throw new Error("401");
        if (res.status === 403) throw new Error("403");
        if (!res.ok) throw new Error("bad-" + res.status);
        return res.json().then(function (j) {
          if (!current()) return null;
          CACHE = j;
          return CACHE;
        });
      });
    });
  }

  function fechaVisita(iso) {
    if (!iso) return "—";
    if (Dates) return Dates.relativo(iso) || iso;
    return iso;
  }

  /* Cada fila con campo lleva su propia línea de mensaje, vacía y oculta hasta
     que haga falta: el error se escribe DEBAJO del campo que lo causó, no en un
     cartel flotante en otra punta de la pantalla (responsable del producto, 2026-09-05: "escribilo
     al lado del campo que tiene el error"). La misma línea sirve para la
     confirmación en positivo mientras la persona escribe. */
  function hiddenIf(cond, html) {
    return cond ? "<div hidden>" + html + "</div>" : html;
  }

  function row(label, inner, htmlFor) {
    var msg = htmlFor ? '<p class="cnt-msg" id="' + esc(htmlFor) + '-msg" hidden></p>' : "";
    return '<div class="cnt-row"><label' + (htmlFor ? ' for="' + esc(htmlFor) + '"' : '') + '>' + esc(label) + '</label><div class="cnt-field">' + inner + msg + "</div></div>";
  }

  /* Escribe la línea de un campo. `tono` es "ok" (confirmación en positivo),
     "mal" (lo que falta) o "" (dato neutro). Sin texto, la línea desaparece. */
  function fieldMsg(sec, id, texto, tono) {
    var p = $("#" + id + "-msg", sec);
    var campo = $("#" + id, sec);
    if (!p) return;
    p.className = "cnt-msg" + (tono ? " cnt-msg--" + tono : "");
    p.textContent = texto || "";
    p.hidden = !texto;
    if (campo) {
      if (tono === "mal") campo.setAttribute("aria-invalid", "true");
      else campo.removeAttribute("aria-invalid");
    }
  }

  /* El mismo mensaje, repetido al lado de Guardar: ahí es donde está mirando
     quien acaba de apretar el botón. */
  function saveMsg(sec, texto) {
    var p = $("#usr-save-msg", sec);
    if (!p) return;
    p.textContent = texto || "";
    p.hidden = !texto;
  }

  function limpiarMensajes(sec, ids) {
    saveMsg(sec, "");
    ids.forEach(function (id) { fieldMsg(sec, id, ""); });
  }

  /* Un error de la API llega con el campo que lo causó ("field", lo pone
     admin_users.py). Lo escribimos al lado de ESE campo y al lado de Guardar, y
     movemos el foco ahí. Sólo si el error no tiene campo cae al aviso general. */
  var CAMPO_DE = {
    usuario: "usr-usuario", email: "usr-email", password: "usr-pass",
    medico_id: "usr-medico", rol: "usr-usuario", centros: "usr-usuario"
  };
  function mostrarErrorApi(sec, mensaje, field) {
    var id = CAMPO_DE[field];
    saveMsg(sec, mensaje);
    if (!id || !$("#" + id, sec)) { if (!id) toast(mensaje); return; }
    fieldMsg(sec, id, mensaje, "mal");
    var campo = $("#" + id, sec);
    if (campo && campo.focus) campo.focus();
  }

  /* Passkeys de la cuenta que se está editando. El botón sólo aparece si hay
     algo que quitar; el borrado es inmediato (no espera al Guardar del form)
     porque es la acción de emergencia: alguien perdió el teléfono y hay que
     cortar esa llave ya. */
  function wirePasskeys(sec, usuario, current) {
    var est = $("#usr-keys-estado", sec);
    var btn = $("#usr-keys-quitar", sec);
    if (!est || !btn) return;

    function pintar(n) {
      if (!current()) return;
      est.innerHTML = n
        ? "<b>" + esc(T("admin.system.users.passkeys_registered_label", null, "Registradas:")) + "</b> " + n + "."
        : esc(T("admin.system.users.passkeys_none", null, "Ninguna registrada. Cada persona da de alta la suya desde su cuenta."));
      btn.hidden = !n;
      btn.disabled = false;
    }

    fetch("/api/admin/passkeys?usuario=" + encodeURIComponent(usuario), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error('passkeys-query-failed'); return r.json(); })
      .then(function (j) { if (!Array.isArray(j?.passkeys)) throw new Error('passkeys-query-malformed'); pintar(j.passkeys.length); })
      .catch(function () { if (current()) est.textContent = T("admin.system.users.passkeys_query_error", null, "No se pudo consultar."); });

    btn.addEventListener("click", function () {
      if (!current()) return;
      btn.disabled = true;
      fetch("/api/admin/passkeys/reset", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usuario: usuario })
      }).then(function (r) { return r.json().catch(function () { return null; }).then(function (body) {
        if (!r.ok || body?.ok !== true) throw new Error(body?.error === "demo_solo_lectura" ? body.error : T("admin.system.users.passkeys_remove_error", null, "No se pudo quitar."));
        return body;
      }); })
        .then(function (j) {
          if (!current()) return;
          toast(T("admin.system.users.passkeys_removed", null, "Passkeys quitadas."));
          pintar(0);
        }).catch(function (err) { if (current()) { toast(errText(err, T("admin.system.users.passkeys_remove_error", null, "No se pudo quitar."))); btn.disabled = false; } });
    });
  }

  // Editarse a uno mismo es otra pantalla (Cuenta): acá un admin actúa SOBRE
  // otra cuenta y cambia la contraseña sin pedir la actual. Para el dueño ni
  // siquiera había forma de entrar — su fila era texto plano (responsable del producto,
  // 2026-07-27: "no puedo editar mi perfil").
  function soyYo(usuario) {
    return !!window.CURRENT_USER && usuario === window.CURRENT_USER;
  }

  function estado2fa(item) {
    if (item.tiene_2fa) return T("admin.system.users.tfa.activa", null, "Activa");
    if (item.requiere_configurar_2fa) return T("admin.system.users.tfa.pendiente", null, "Pendiente");
    return T("admin.system.users.tfa.no_configurada", null, "No configurada");
  }

  function clase2fa(item) {
    if (item.tiene_2fa) return "chip-activo";
    if (item.requiere_configurar_2fa) return "chip-pendiente";
    return "chip-inactivo";
  }

  // =====================================================================
  // LISTA
  // =====================================================================

  // Columnas configurables, mismo motor que el resto de las tablas
  // (SanaTable.columnas). `always` = la que identifica la fila.
  var COLS_ALL = [
    { key: "usuario", label: T("admin.system.users.col.usuario", null, "Usuario"), always: true, min: 130, flex: 1 },
    { key: "rol", label: T("admin.system.users.col.rol", null, "Rol"), "default": true, min: 130, flex: 1 },
    { key: "email", label: T("admin.system.users.col.email", null, "Email"), "default": true, min: 150, flex: 1 },
    { key: "centros", label: T("admin.system.users.col.centros", null, "Centros"), min: 130, max: 260, flex: 1 },
    { key: "tiene_2fa", label: T("admin.system.users.col.tfa", null, "2FA"), "default": true, min: 110 },
    { key: "activo", label: T("admin.system.users.col.estado", null, "Estado"), "default": true, min: 92 },
    { key: "ultimo_acceso", label: T("admin.system.users.col.ultima_visita", null, "Última visita"), "default": true, min: 120 }
  ];
  var COLPICK = null;
  // Repinta la tabla con las columnas nuevas SIN volver a pedir la lista ni
  // rehacer la toolbar: si se rehiciera, el popover del engranaje se cerraría
  // en cada checkbox y habría que reabrirlo para prender la siguiente.
  var REPINTAR = null;
  // Portal-login installation without our TOTP: there is no 2FA of ours to
  // report, so the column never shows (set from the list response).
  var SIN_2FA = false;
  function cols() {
    var activas = COLPICK ? COLPICK.activas()
      : COLS_ALL.filter(function (c) { return c.always || c["default"]; });
    return SIN_2FA ? activas.filter(function (c) { return c.key !== "tiene_2fa"; }) : activas;
  }

  function theadHtml(activas, SORT) {
    return activas.map(function (c) {
      return '<th scope="col" class="cnt-th cnt-th-sortable" data-sort="' + esc(c.key)
        + '" tabindex="0" aria-sort="' + (SORT.key === c.key ? (SORT.dir === 1 ? "ascending" : "descending") : "none")
        + '">' + esc(c.label) + "</th>";
    }).join("");
  }

  function celdaUsuario(it) {
    if (!it.es_dueno) {
      return '<a href="' + userHref(it.usuario) + '" data-users-state="user" data-user="'
        + esc(it.usuario) + '">' + esc(it.usuario) + "</a>";
    }
    var badge = ' <span class="cnt-badge" title="' + esc(T("admin.system.users.owner_title", null, "Entra siempre, incluso con la base de datos caída"))
      + '">' + esc(T("admin.system.users.owner_badge", null, "Dueño")) + "</span>";
    // Every row opens (pinamar-turnos 2026-11-03): one's own owner account
    // goes to Cuenta, someone else's to a read-only page.
    return '<a href="' + (soyYo(it.usuario) ? accountHref() : userHref(it.usuario)) + '">' + esc(it.usuario) + "</a>" + badge;
  }

  // The owner account is managed only from its own Cuenta screen; another
  // admin sees what it is, without a form to change it.
  function ownerReadOnlyHtml(item) {
    // Same read-only rows as a doctor's account (mountDoctorUser).
    return '<div class="doctor-user-summary" data-owner-readonly>'
      + (item.email ? row(T("admin.system.users.col.email", null, "Email"), esc(item.email)) : "")
      + row(T("admin.system.users.col.rol", null, "Rol"), esc(rolLabel(item.rol)))
      + row(T("admin.system.users.col.tfa", null, "2FA"), '<span class="chip ' + clase2fa(item) + '">' + esc(estado2fa(item)) + "</span>")
      + (item.ultimo_acceso ? row(T("admin.system.users.col.ultima_visita", null, "Última visita"), esc(fechaVisita(item.ultimo_acceso))) : "")
      + '</div><p class="usr-owner-note">' + esc(T("admin.system.users.owner_managed_elsewhere", null,
        "Solo esa persona cambia sus datos, desde su pantalla Cuenta.")) + "</p>";
  }

  function centrosTexto(it) {
    var c = it.centros;
    if (!Array.isArray(c) || !c.length) return T("admin.system.users.centros_todos", null, "Todos");
    return c.join(", ");
  }

  function celda(key, it) {
    var activo = it.activo !== false;
    if (key === "usuario") return celdaUsuario(it);
    if (key === "rol") return esc(rolLabel(it.rol));
    // What is missing or repeated on every row is not written: no «—» for a
    // missing email or a visit that never happened, no «Activo» on every
    // active account — only «Inactivo» stands out (Opus UI review
    // 2026-11-03, the same rule as Pacientes).
    if (key === "email") return esc(it.email || "");
    if (key === "centros") return esc(centrosTexto(it));
    if (key === "tiene_2fa") return '<span class="chip ' + clase2fa(it) + '">' + esc(estado2fa(it)) + "</span>";
    if (key === "activo") return activo ? "" : '<span class="chip chip-inactivo">'
      + esc(T("admin.system.users.inactivo", null, "Inactivo")) + "</span>";
    if (key === "ultimo_acceso") return it.ultimo_acceso ? esc(fechaVisita(it.ultimo_acceso)) : "";
    return "";
  }

  function isDoctorAccount(it) {
    return !!it && it.rol === "medico" && !!it.medico_id;
  }

  function renderLista(seq) {
    hydratePrefs();
    var sec = $("#sec-usuarios");
    function current() { return seq === routeSeq && sec.isConnected; }
    if (!COLPICK && Table) {
      COLPICK = Table.columnas({
        clave: "users", cols: COLS_ALL,
        onChange: function () { if (REPINTAR && $("#usr-tbody")) REPINTAR(); }
      });
    }
    return fetchLista(false, current).then(function (data) {
      if (!current()) return;
      SIN_2FA = !!data.acceso_portal && !data.acceso_portal_totp;
      // A doctor's account lives with the doctor, in Médicos (the product owner,
      // 2026-10-19: "users and doctors on the same tab"). A médico account
      // tied to no doctor stays here so it can still be repaired.
      var items = (data.items || []).filter(function (it) { return !isDoctorAccount(it); });
      sec.setAttribute("data-list-view", "1");
      if (!items.length) {
        // Nadie a quien buscar, filtrar u ordenar (the product owner, 2026-10-26): sin
        // buscador, filtros, engranaje ni encabezado — el título, «Nuevo
        // usuario» y qué va a aparecer acá. No es «ningún usuario coincide».
        REPINTAR = null;
        sec.innerHTML = Filters.emptyStateHtml({
            title: T("admin.system.users.empty_none", null, "Todavía no hay usuarios del panel."),
            text: T("admin.system.users.empty_none_text", null, "Acá aparece cada persona que entra al panel, salvo los médicos: sus cuentas están en Médicos.")
          });
        Filters.applyDisclosure(sec, "empty");
        return;
      }
      // El filtro de rol ofrece sólo los roles que tiene alguien de la lista
      // (más el ya elegido, para poder sacarlo), en el orden del catálogo.
      var orden = roleChoices(data).map(function (r) { return r[0]; });
      var presentes = [];
      items.forEach(function (it) { if (it.rol && presentes.indexOf(it.rol) === -1) presentes.push(it.rol); });
      if (FILTERS.rol && presentes.indexOf(FILTERS.rol) === -1) presentes.push(FILTERS.rol);
      function lugar(slug) { var i = orden.indexOf(slug); return i === -1 ? orden.length : i; }
      presentes.sort(function (a, b) { return lugar(a) - lugar(b); });
      var estados = {};
      items.forEach(function (it) { estados[it.activo === false ? "inactivo" : "activo"] = 1; });
      // The column chooser and «Descargar» go with the filters: a list short
      // enough to read at a glance (Filters.FILTER_MIN_ROWS) has no columns
      // to pick or file to take away (2026-11-08 review D14, product owner 26.10: «if
      // there's no content, why to show all the ui?»). The search stays.
      var herramientas = Filters.filtersWorth(items.length);
      var html = '<div class="cnt-toolbar"><div class="cnt-toolbar-filters">'
        + '<input class="adm-search cnt-search" id="usr-q" type="search" value="' + esc(FILTERS.q) + '" placeholder="' + esc(T("admin.system.users.search_placeholder", null, "Nombre o mail")) + '">'
        + '<select class="adm-sel" id="usr-f-rol"><option value="">' + esc(T("admin.system.users.col.rol", null, "Rol")) + '</option>'
        + presentes.map(function (slug) { return '<option value="' + esc(slug) + '"' + (FILTERS.rol === slug ? " selected" : "") + ">" + esc(rolLabel(slug)) + "</option>"; }).join("")
        + '</select><select class="adm-sel" id="usr-f-estado"><option value="">' + esc(T("admin.system.users.col.estado", null, "Estado")) + '</option>'
        + '<option value="activo"' + (FILTERS.estado === "activo" ? " selected" : "") + '>' + esc(T("admin.system.users.filter.active", null, "Activos")) + '</option>'
        + '<option value="inactivo"' + (FILTERS.estado === "inactivo" ? " selected" : "") + '>' + esc(T("admin.system.users.filter.inactive", null, "Inactivos")) + '</option>'
        + '</select></div><div class="cnt-toolbar-right">'
        + (herramientas && COLPICK ? COLPICK.gearHtml() : "")
        + (herramientas && Table && Table.exportHtml ? Table.exportHtml({ scope: "#usr-results" }) : "")
        + '</div></div>';
      // La tabla o «ningún usuario coincide»: redraw decide cuál, sin un
      // encabezado de tabla vacío arriba del aviso.
      html += '<div class="cnt-table-wrap" id="usr-results"></div>';
      sec.innerHTML = html;
      // Un filtro con un solo valor en la lista no tiene nada que elegir, y
      // una lista corta (Filters.FILTER_MIN_ROWS) no tiene nada que filtrar.
      Filters.showIfChoice($("#usr-f-rol", sec), null, "", items.length);
      Filters.showIfChoice($("#usr-f-estado", sec), Object.keys(estados).length, "", items.length);
      var results = $("#usr-results", sec);

      function redraw() {
        if (!current()) return;
        var activas = cols();
        var q = String(FILTERS.q || "").toLocaleLowerCase("es");
        var visible = items.filter(function (it) {
          if (FILTERS.rol && it.rol !== FILTERS.rol) return false;
          if (FILTERS.estado === "activo" && it.activo === false) return false;
          if (FILTERS.estado === "inactivo" && it.activo !== false) return false;
          return !q || [it.usuario, it.email, rolLabel(it.rol)].join(" ").toLocaleLowerCase("es").indexOf(q) !== -1;
        });
        visible.sort(function (a, b) {
          var av = a[SORT.key], bv = b[SORT.key];
          if (SORT.key === "rol") { av = rolLabel(av); bv = rolLabel(bv); }
          if (SORT.key === "activo") { av = a.activo === false ? 0 : 1; bv = b.activo === false ? 0 : 1; }
          return String(av == null ? "" : av).localeCompare(String(bv == null ? "" : bv), "es", { numeric: true }) * SORT.dir;
        });
        if (!visible.length) {
          results.innerHTML = Filters.noMatchHtml({
            title: T("admin.system.users.empty", null, "Ningún usuario coincide con los filtros."),
            clearLabel: T("admin.shell.list.clear_filters", null, "Limpiar filtros")
          });
          Filters.applyDisclosure(sec, "no-match");
          return;
        }
        // The table is built once and then only its body is redrawn, so a
        // header sorted from the keyboard keeps its focus.
        if (!$("#usr-tbody", results)) {
          results.innerHTML = '<table class="cnt-table"><thead><tr id="usr-thead">' + theadHtml(activas, SORT)
            + '</tr></thead><tbody id="usr-tbody"></tbody></table>';
        }
        var body = "";
        visible.forEach(function (it) {
          body += '<tr class="cnt-tr' + (it.activo !== false ? "" : " cnt-tr-borrado") + '">'
            + activas.map(function (c) {
                return '<td data-col="' + esc(c.key) + '">' + celda(c.key, it) + "</td>";
              }).join("")
            + "</tr>";
        });
        $("#usr-tbody", results).innerHTML = body;
        Filters.applyDisclosure(sec, "data");
      }
      REPINTAR = function () {
        if (!current()) return;
        var thead = $("#usr-thead", sec);
        if (thead) thead.innerHTML = theadHtml(cols(), SORT);
        redraw();
      };
      redraw();
      $("#usr-q", sec).addEventListener("input", function (e) {
        if (!current()) return;
        FILTERS.q = e.target.value; prefsSet("users.filters", FILTERS); redraw();
      });
      [["usr-f-rol", "rol"], ["usr-f-estado", "estado"]].forEach(function (pair) {
        $("#" + pair[0], sec).addEventListener("change", function (e) {
          if (!current()) return;
          FILTERS[pair[1]] = e.target.value; prefsSet("users.filters", FILTERS); redraw();
        });
      });
      // Ordenar con mouse Y con teclado: en el panel hay una persona que
      // trabaja con lector de pantalla y un th sólo-click la deja afuera.
      // La tabla pertenece a esta vista; la sección se reutiliza al navegar.
      // Delegar en ella conserva los listeners al redibujar sólo el encabezado.
      function ordenar(th) {
        if (!current()) return;
        var key = th.getAttribute("data-sort");
        if (SORT.key === key) SORT.dir *= -1; else { SORT.key = key; SORT.dir = 1; }
        prefsSet("users.sort", SORT);
        $$("[data-sort]", sec).forEach(function (o) {
          o.setAttribute("aria-sort", o.getAttribute("data-sort") === SORT.key
            ? (SORT.dir === 1 ? "ascending" : "descending") : "none");
        });
        redraw();
      }
      results.addEventListener("click", function (e) {
        if (!current()) return;
        if (e.target.closest && e.target.closest("[data-clear-filters]")) {
          // The way back from «ningún usuario coincide»: every filter off,
          // and the toolbar redrawn so a filter shown only because it was
          // set goes away with it.
          FILTERS.q = ""; FILTERS.rol = ""; FILTERS.estado = "";
          prefsSet("users.filters", FILTERS);
          renderLista(seq);
          return;
        }
        var th = e.target.closest && e.target.closest("[data-sort]");
        if (th && !e.target.closest("[data-tbl-res]")) ordenar(th);
      });
      results.addEventListener("keydown", function (e) {
        if (!current()) return;
        if (e.key !== "Enter" && e.key !== " ") return;
        var th = e.target.closest && e.target.closest("[data-sort]");
        if (!th) return;
        e.preventDefault();
        ordenar(th);
      });
    });
  }

  // =====================================================================
  // FORMULARIO (alta / edición)
  // =====================================================================

  function renderForm(usuario, seq) {
    hydratePrefs();
    var sec = $("#sec-usuarios");
    function current() { return seq === routeSeq && sec.isConnected; }
    sec.removeAttribute("data-list-view");
    sec.removeAttribute("data-disclosure");
    var isNew = usuario == null;
    return Promise.all([fetchLista(false, current), fetchMedicos(false, undefined, current), fetchCentros(false, current)]).then(function (results) {
      if (seq !== routeSeq) return;
      var data = results[0], medicos = results[1], centros = results[2];
      var item = isNew ? null : (data.items || []).filter(function (x) { return x.usuario === usuario; })[0];
      if (!isNew && !item) {
        sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.system.users.not_found", null, "No se encontró ese usuario.")) + '</p>';
        return;
      }
      if (!isNew && item.es_dueno) {
        if (soyYo(item.usuario)) {
          location.assign(accountHref());
          return;
        }
        titleCrumb(item.usuario);
        sec.innerHTML = ownerReadOnlyHtml(item);
        return;
      }
      var titulo = isNew ? T("admin.system.users.new_title", null, "Nuevo usuario") : item.usuario;
      titleCrumb(titulo);
      var rolActual = isNew ? prefsGet("users.last_role", "admisionista") : item.rol;
      var availableRoles = roleChoices(data, isNew ? null : rolActual);
      // A doctor is never created here: creating the doctor in Médicos creates
      // its account in the same step (the product owner, 2026-10-19: "don't make
      // administrators create medics twice"). Existing médico accounts keep
      // the role so an unlinked one can still be repaired.
      if (isNew) availableRoles = availableRoles.filter(function (r) { return r[0] !== "medico"; });
      if (isNew && !availableRoles.some(function (r) { return r[0] === rolActual; })) {
        rolActual = availableRoles.some(function (r) { return r[0] === "admisionista"; }) ? "admisionista" : availableRoles[0]?.[0];
      }
      var medicoActual = isNew ? "" : (item.medico_id || "");

      var rolHtml = availableRoles.map(function (r) {
        var checked = r[0] === rolActual;
        return '<label class="cnt-modo-op' + (checked ? " on" : "") + '"><input type="radio" name="usr-rol" value="' + esc(r[0]) + '"'
          + (checked ? " checked" : "") + '><span class="cnt-modo-txt"><b>' + esc(r[1]) + "</b><br><span class=\"cnt-hint\">"
          + esc(r[2]) + "</span></span></label>";
      }).join("");

      // Sólo tiene sentido para rol 'medico' — arranca oculto salvo que ya
      // sea el rol actual; el listener de abajo lo muestra/oculta al vuelo.
      var medOptsHtml = '<option value="">' + esc(T("admin.system.users.pick_doctor", null, "Elegí un médico…")) + '</option>' + medicos.map(function (m) {
        return '<option value="' + esc(m.id) + '"' + (m.id === medicoActual ? " selected" : "") + '>'
          + esc(nombre(m.nombre)) + (m.especialidad ? " — " + esc(m.especialidad) : "") + "</option>";
      }).join("");
      var medicoRowHtml = '<div id="usr-medico-wrap" style="display:' + (rolActual === "medico" ? "" : "none") + ';">'
        + row(T("admin.system.users.field.medico", null, "Médico"), '<select class="cnt-in" id="usr-medico">' + medOptsHtml + "</select>", "usr-medico") + "</div>";

      // Alcance por centro (reunión hospital 2026-07-24, Gabriel): la mesa de
      // un CAPS no tiene por qué ver la jornada de los otros. Ninguno tildado
      // = todos, que es como venía funcionando; un admin nunca se acota.
      var centrosActual = isNew ? [] : (item.centros || []);
      var centrosHtml = centros.map(function (c) {
        var on = centrosActual.indexOf(c.slug) !== -1;
        return '<label class="cnt-check"><input type="checkbox" class="usr-centro" value="' + esc(c.slug) + '"'
          + (on ? " checked" : "") + "><span>" + esc(c.name) + "</span></label>";
      }).join("");
      var centrosRowHtml = centros.length
        ? '<div id="usr-centros-wrap" style="display:' + (rolActual === "admin" ? "none" : "") + ';">'
          + row(T("admin.system.users.field.centros_salud", null, "Centros de salud"), '<div class="usr-centros">' + centrosHtml + "</div>"
            + '<p class="cnt-hint">' + esc(T("admin.system.users.centros_hint", null, "Si no tildás ninguno, ve todos los centros.")) + '</p>') + "</div>"
        : "";

      var html = '<form id="usr-form">';
      if (isNew) {
        html += row(T("admin.system.users.col.usuario", null, "Usuario"),
          '<input class="cnt-in cnt-field-name" id="usr-usuario" autocomplete="username" autocapitalize="off" spellcheck="false" placeholder="' + esc(T("admin.system.users.username_placeholder", null, "p.ej. jperez")) + '">', "usr-usuario");
      }
      html += row(T("admin.system.users.col.email", null, "Email"),
        '<input class="cnt-in cnt-field-email" id="usr-email" type="email" autocomplete="email" value="' + esc(isNew ? "" : (item.email || "")) + '">', "usr-email");
      // Portal-login installation: staff enter with the portal account the
      // email matches, so there is no password, access mode or passkey to
      // set here (the controls stay in the DOM, hidden, for the handlers).
      var portal = !!data.acceso_portal;
      var portalSin2fa = portal && !data.acceso_portal_totp;
      if (isNew) html += hiddenIf(portal, row(T("admin.system.users.access_mode", null, "Acceso"),
        '<label class="cnt-check"><input id="usr-invite" name="invite" type="checkbox"><span>'
        + esc(T("admin.system.users.choose_password_email", null, "Elegir contraseña por email")) + '</span></label>'));
      html += hiddenIf(portal, row(isNew ? T("admin.system.users.field.password", null, "Contraseña") : T("admin.system.users.field.new_password", null, "Nueva contraseña"),
        '<input class="cnt-in cnt-in--password" id="usr-pass" type="password" autocomplete="new-password" placeholder="'
        + esc(isNew ? T("admin.system.users.field.password_hint_new", null, "mínimo 8 caracteres")
                    : T("admin.system.users.field.password_hint_edit", null, "dejalo vacío para no cambiarla")) + '">', "usr-pass"));
      html += row(T("admin.system.users.field.puede", null, "Puede"), '<div class="cnt-modo-list">' + rolHtml + "</div>"
        + (isNew ? '<p class="usr-doctors-elsewhere"><a href="/admin/doctors">'
          + esc(T("admin.system.users.doctors_elsewhere", null, "Los médicos se crean e invitan desde Médicos")) + "</a></p>" : ""));
      if (!isNew) html += medicoRowHtml;
      html += centrosRowHtml;
      if (!isNew) {
        html += row(T("admin.system.users.activo", null, "Activo"), '<label class="cnt-check"><input type="checkbox" id="usr-activo"'
          + (item.activo !== false ? " checked" : "") + '><span>' + esc(T("admin.system.users.can_enter_panel", null, "Puede entrar al panel")) + "</span></label>", "usr-activo");
      }
      var forceChecked = !isNew && item.requiere_configurar_2fa;
      var forceText = !isNew && item.tiene_2fa
        ? T("admin.system.users.force_2fa_replace", null, "Quitar el código actual y exigir uno nuevo al ingresar")
        : T("admin.system.users.force_2fa_new", null, "Exigir que configure 2FA al ingresar");
      var estadoTxt = isNew ? T("admin.system.users.tfa.no_configurada", null, "No configurada") : estado2fa(item);
      html += hiddenIf(portalSin2fa, row(T("admin.system.users.field.tfa", null, "Verificación en dos pasos"),
        '<p class="cnt-hint" id="usr-totp-estado"><b>' + esc(T("admin.system.users.estado_label", null, "Estado:")) + '</b> ' + esc(estadoTxt) + ".</p>"
        + '<label class="cnt-check"><input type="checkbox" id="usr-force-2fa"'
        + (forceChecked ? " checked" : "") + '><span>' + esc(forceText) + "</span></label>"
        + (!isNew && item.tiene_2fa
          ? '<button type="button" class="btn btn-quiet btn-sm usr-2fa-remove" id="usr-totp-quitar">' + esc(T("admin.system.users.remove_tfa", null, "Quitar 2FA")) + '</button>'
          : "")));
      /* Passkeys de esa cuenta. Es la salida para el teléfono perdido: se
         borran todas y la persona vuelve a entrar con contraseña y registra
         una nueva desde su cuenta. No se pueden "ver" — la llave vive en el
         dispositivo, acá sólo consta cuántas hay. */
      if (!isNew) {
        html += hiddenIf(portal, row("Passkeys",
          '<p class="cnt-hint" id="usr-keys-estado">' + esc(T("admin.system.users.counting", null, "Contando…")) + '</p>'
          + '<button type="button" class="btn btn-quiet btn-sm" id="usr-keys-quitar" hidden>' + esc(T("admin.system.users.remove_passkeys", null, "Quitar las passkeys")) + '</button>'));
      }
      html += "</form>";
      html += '<div class="cnt-actions"><button class="btn btn-ok" type="submit" form="usr-form">' + esc(T("admin.system.common.save", null, "Guardar")) + '</button>'
        + '<p class="cnt-save-msg" id="usr-save-msg" role="alert" hidden></p>';
      if (!isNew) html += '<span class="cnt-actions-danger"><button class="btn btn-quiet" type="button" id="usr-desactivar">' + esc(T("admin.system.users.deactivate", null, "Desactivar")) + '</button></span>';
      html += "</div>";
      sec.innerHTML = html;

      /* Confirmación en positivo mientras se escribe (responsable del producto, 2026-09-05:
         "reemplazá la confirmación negativa por confirmaciones positivas").
         Nadie se entera de que la contraseña era corta recién al apretar
         Guardar: la línea de abajo dice "Lista" apenas alcanza el largo. Antes
         de llegar no decimos que está MAL — todavía la están escribiendo — sino
         cuánto falta, en neutro. */
      var campoUsuario = $("#usr-usuario", sec);
      if (campoUsuario) {
        campoUsuario.addEventListener("input", function () {
          var v = campoUsuario.value.trim();
          if (!v) return fieldMsg(sec, "usr-usuario", "");
          if (validUsername(v)) {
            return fieldMsg(sec, "usr-usuario",
              T("admin.system.users.username_ok", null, "Nombre de usuario listo."), "ok");
          }
          fieldMsg(sec, "usr-usuario", /\s/.test(v)
            ? T("admin.system.users.username_no_spaces", null, "Sin espacios.")
            : T("admin.system.users.username_short", null, "Al menos 2 caracteres."), "");
        });
      }
      var campoPass = $("#usr-pass", sec);
      var inviteChoice = $("#usr-invite", sec);
      if (inviteChoice) inviteChoice.addEventListener("change", function () {
        campoPass.closest('.cnt-row').style.display = inviteChoice.checked ? 'none' : '';
        if (inviteChoice.checked) campoPass.value = '';
      });
      if (campoPass) {
        campoPass.addEventListener("input", function () {
          var v = campoPass.value;
          if (!v) return fieldMsg(sec, "usr-pass", "");
          if (v.length >= PASSWORD_MIN) {
            return fieldMsg(sec, "usr-pass",
              T("admin.system.users.password_ok", null, "Contraseña lista."), "ok");
          }
          fieldMsg(sec, "usr-pass", Tn("admin.system.users.password_left", PASSWORD_MIN - v.length, { faltan: PASSWORD_MIN - v.length },
            "Falta {faltan} carácter.", "Faltan {faltan} caracteres."), "");
        });
      }

      $$('input[name="usr-rol"]', sec).forEach(function (r) {
        r.addEventListener("change", function () {
          $$(".cnt-modo-op", sec).forEach(function (l) {
            l.classList.toggle("on", l.contains(r) ? r.checked : l.querySelector("input").checked);
          });
          var sel = $('input[name="usr-rol"]:checked', sec);
          var wrap = $("#usr-medico-wrap", sec);
          if (wrap) wrap.style.display = sel && sel.value === "medico" ? "" : "none";
          var wc = $("#usr-centros-wrap", sec);
          if (wc) wc.style.display = sel && sel.value === "admin" ? "none" : "";
        });
      });

      var pendingTotpAction = null; // 'quitar' | null
      var force2fa = $("#usr-force-2fa", sec);
      if (force2fa) force2fa.addEventListener("change", function () {
        var est = $("#usr-totp-estado", sec);
        if (!est) return;
        if (force2fa.checked) {
          est.innerHTML = "<b>" + esc(T("admin.system.users.estado_label", null, "Estado:")) + "</b> "
            + esc(T("admin.system.users.force_2fa_pending", null, "Se exigirá una configuración nueva al próximo ingreso."));
        } else {
          est.innerHTML = "<b>" + esc(T("admin.system.users.estado_label", null, "Estado:")) + "</b> " + esc(estadoTxt) + ".";
        }
      });
      if (!isNew) wirePasskeys(sec, item.usuario, current);

      var quitBtn = $("#usr-totp-quitar", sec);
      if (quitBtn) quitBtn.addEventListener("click", function () {
        pendingTotpAction = "quitar";
        if (force2fa) force2fa.checked = false;
        var est = $("#usr-totp-estado", sec);
        if (est) est.innerHTML = "<b>" + esc(T("admin.system.users.estado_label", null, "Estado:")) + "</b> "
          + esc(T("admin.system.users.tfa_will_remove", null, "Se quitará al guardar."));
      });

      $("#usr-form", sec).addEventListener("submit", function (e) {
        e.preventDefault();
        if (!current()) return;
        limpiarMensajes(sec, ["usr-usuario", "usr-email", "usr-pass", "usr-medico"]);
        var body = {};
        var rolEl = $('input[name="usr-rol"]:checked', sec);
        body.rol = rolEl ? rolEl.value : "medico";
        body.email = $("#usr-email", sec).value.trim();
        prefsSet("users.last_role", body.rol);
        if (body.rol === "medico") {
          var medSel = $("#usr-medico", sec);
          var medicoId = medSel ? medSel.value : "";
          if (!medicoId) {
            mostrarErrorApi(sec, T("admin.system.users.pick_doctor_error", null,
              "Elegí un médico para esta cuenta."), "medico_id");
            return;
          }
          body.medico_id = medicoId;
        } else {
          body.medico_id = "";  // saliendo del rol 'medico' (o nunca lo fue): no dejar un id colgado
        }
        body.centros = body.rol === "admin" ? []
          : $$(".usr-centro", sec).filter(function (c) { return c.checked; })
              .map(function (c) { return c.value; });
        var pass = $("#usr-pass", sec).value;
        if (isNew) {
          body.usuario = $("#usr-usuario", sec).value.trim();
          if (inviteChoice && inviteChoice.checked) body.access_mode = "invite";
          else body.password = pass;
          body.forzar_2fa = force2fa.checked;
        } else {
          if (body.rol === item.rol) delete body.rol;
          if (pass) body.password = pass;
          var act = $("#usr-activo", sec);
          if (act) body.activo = act.checked;
          if (pendingTotpAction) body.totp = pendingTotpAction;
          if (force2fa.checked !== !!item.requiere_configurar_2fa) {
            body.forzar_2fa = force2fa.checked;
          }
        }
        var btn = $('button[type="submit"]', sec);
        btn.disabled = true;
        var url = isNew ? "/api/admin/users" : "/api/admin/users/" + encodeURIComponent(item.usuario);
        fetch(url, {
          method: isNew ? "POST" : "PUT", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
        }).then(function (res) {
          CACHE = null;
          if (!current()) return null;
          var boundary = (res.status === 401 || res.status === 403)
            ? Session.confirmar(res, undefined, current)
            : Promise.resolve(false);
          return boundary.then(function (transitioned) {
            if (!current()) return null;
            if (transitioned) throw new Error("redirect");
            return res.json().catch(function () { return {}; }).then(function (j) {
              if (!current()) return null;
              if (!res.ok || j?.ok !== true) {
                var fallo = new Error(j?.error || T("admin.system.common.save_error", null, "No se pudo guardar."));
                fallo.field = j?.field || "";   // lo pone admin_users.py
                throw fallo;
              }
              return j;
            });
          });
        }).then(function (j) {
          if (!current() || !j) return null;
          return fetchLista(true, current).then(function () { return j; });
        }).then(function (j) {
          if (!current() || !j) return;
          if (j.email_enviado === false) {
            // The account exists: leave creation before allowing another save.
            // If the invite email never left, the random password exists only
            // here — reveal it in place instead of a toast that disappears.
            if (j.password) {
              var pw = $("#usr-pass", sec);
              if (pw) {
                pw.value = j.password;
                pw.type = "text";
                pw.readOnly = true;
                pw.autocomplete = "off";
              }
              fieldMsg(sec, "usr-pass", T("admin.system.users.email_not_sent_reveal", null,
                "No se pudo enviar el email. Quedó creado el usuario: esta es la contraseña generada, compartila personalmente."), "ok");
              // Sin goTo: la contraseña recién revelada viviría menos que el
              // formulario. El botón queda deshabilitado — la cuenta ya existe.
              return;
            }
            goTo(userHref(j.item.usuario));
            toast(T("admin.system.users.email_not_sent", null,
              "No se pudo enviar el email. Quedó creado el usuario."));
            return;
          }
          toast(T("admin.system.common.saved", null, "Guardado."));
          goTo(listHref());
        }).catch(function (e2) {
          if (!current()) return;
          btn.disabled = false;
          if (String(e2.message) === "redirect") return;
          mostrarErrorApi(sec, errText(e2, T("admin.system.common.save_error", null, "No se pudo guardar.")), e2.field);
        });
      });

      var desA = $("#usr-desactivar", sec);
      if (desA) desA.addEventListener("click", function (e) {
        e.preventDefault();
        if (!current()) return;
        if (!confirm(T("admin.system.users.confirm_deactivate", { usuario: item.usuario },
          "¿Desactivar a {usuario}? No va a poder entrar al panel; sus solicitudes ya resueltas van a seguir mostrando su nombre."))) return;
        fetch("/api/admin/users/" + encodeURIComponent(item.usuario), { method: "DELETE", credentials: "same-origin" })
          .then(function (res) {
            CACHE = null;
            if (!current()) return null;
            var boundary = (res.status === 401 || res.status === 403)
              ? Session.confirmar(res, undefined, current)
              : Promise.resolve(false);
            return boundary.then(function (transitioned) {
              if (!current()) return null;
              if (transitioned) throw new Error("redirect");
              return res.json().catch(function () { return {}; }).then(function (j) {
                if (!current()) return null;
                if (!res.ok || j?.ok !== true) throw new Error(j?.error || T("admin.system.users.deactivate_error", null, "No se pudo desactivar."));
              });
            });
          }).then(function () {
            if (!current()) return null;
            toast(T("admin.system.users.deactivated", null, "Desactivado."));
            return fetchLista(true, current);
          }).then(function () { if (current()) goTo(listHref()); }).catch(function (e2) {
            if (!current()) return;
            if (String(e2.message) !== "redirect") toast(errText(e2, T("admin.system.users.deactivate_error", null, "No se pudo desactivar.")));
          });
      });
    });
  }

  function doctorAction(medicoId, action, extra, isCurrent) {
    var current = typeof isCurrent === "function" ? isCurrent : function () { return true; };
    var payload = Object.assign({ medico_ids: [medicoId] }, extra || {});
    return fetch("/api/admin/users/actions/" + action, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (res) {
      var boundary = (res.status === 401 || res.status === 403)
        ? Session.confirmar(res, undefined, current)
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        if (transitioned) throw new Error("401");
        return res.json().catch(function () { return {}; });
      }).then(function (data) {
        if (!res.ok || data?.ok !== true) throw new Error(data?.error || T("admin.system.users.action_error", null, "No se pudo completar la acción."));
        var result = (data.resultados || [])[0] || {};
        if (result.ok !== true) throw new Error(result.error || T("admin.system.users.action_error", null, "No se pudo completar la acción."));
        CACHE = null;
        return result;
      });
    });
  }

  // The invitation of one doctor's account (email optional). Its result is
  // what showInviteOutcome paints.
  export function inviteDoctor(medicoId, email) {
    return doctorAction(medicoId, "invite", email ? { email: email } : null);
  }

  function doctorUserDefaults(doctor, items) {
    function words(value) {
      return String(value || "").normalize("NFD").replace(/[̀-ͯ]/g, "")
        .toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    }
    var parts = words(doctor.nombre).filter(function (part) {
      return ["dr", "dra", "doctor", "doctora", "lic"].indexOf(part) < 0;
    });
    var first = parts[0] || "", last = parts[parts.length - 1] || "";
    var base = (parts.length > 1 ? first + "." + last : first || "medico").slice(0, 32);
    if (base.length < 3) base = "med." + base;
    var used = items.map(function (item) { return item.usuario.toLowerCase(); });
    var username = base, suffix = 2;
    while (used.indexOf(username) >= 0) username = base + suffix++;
    var email = String(doctor.email || "").trim().toLowerCase();
    var ranked = items.filter(function (item) { return !item.es_dueno && !item.medico_id; }).map(function (item) {
      var userWords = words(item.usuario);
      var fullName = userWords.join("") === parts.join("");
      var firstLast = parts.length > 1 && first !== last &&
        (userWords.join("") === first + last ||
         (userWords.indexOf(first) >= 0 && userWords.indexOf(last) >= 0 &&
          userWords.every(function (word) { return parts.indexOf(word) >= 0; })));
      var score = email && String(item.email || "").trim().toLowerCase() === email ? 2
        : (parts.length > 1 && (fullName || firstLast) ? 1 : 0);
      return { item: item, score: score };
    }).sort(function (a, b) { return b.score - a.score || a.item.usuario.localeCompare(b.item.usuario); });
    return {
      username: username,
      candidates: ranked.map(function (entry) { return entry.item; }),
      match: ranked.length && ranked[0].score > 0 && (!ranked[1] || ranked[1].score < ranked[0].score)
        ? ranked[0].item.usuario : ""
    };
  }

  function credentialText(result) {
    return T("admin.system.users.col.usuario", null, "Usuario") + ": " + result.usuario + "\n"
      + T("admin.system.users.field.password", null, "Contraseña") + ": " + result.password;
  }

  function copyText(textPromise, isCurrent) {
    // Start the clipboard operation inside the original user gesture, including
    // on Safari. The response resolves the ClipboardItem later.
    var current = typeof isCurrent === "function" ? isCurrent : function () { return true; };
    function owned(text) {
      if (!current()) throw new Error("doctor-user-disposed");
      return text;
    }
    try {
      if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
        var blob = textPromise.then(function (text) {
          return new Blob([owned(text)], { type: "text/plain" });
        });
        // Permission can be denied before ClipboardItem consumes its promise.
        blob.catch(function () {});
        return navigator.clipboard.write([new ClipboardItem({
          "text/plain": blob
        })]).then(function () { return true; }, function () { return false; });
      }
      return textPromise.then(function (text) {
        return navigator.clipboard.writeText(owned(text));
      }).then(function () { return true; }, function () { return false; });
    } catch (_) { return Promise.resolve(false); }
  }

  function copyDoctorCredentials(resultPromise, isCurrent) {
    return copyText(resultPromise.then(credentialText), isCurrent);
  }

  function showDoctorCredentials(host, result, copied, isCurrent, own) {
    if (!isCurrent()) return;
    host.innerHTML = '<div class="doctor-user-credentials">'
      + row(T("admin.system.users.col.usuario", null, "Usuario"), esc(result.usuario))
      + row(T("admin.system.users.field.password", null, "Contraseña"),
        '<input class="cnt-in doctor-user-select" id="doctor-user-password" name="password" type="text" readonly autocomplete="off" value="' + esc(result.password) + '">', "doctor-user-password")
      + '<div class="doctor-user-link-actions"><button type="button" class="btn btn-quiet" id="doctor-user-copy">'
        + esc(T("admin.system.users.copy_access", null, "Copiar acceso")) + '</button>'
      + '<span id="doctor-user-copy-status" role="status">' + esc(copied
        ? T("admin.system.users.access_copied", null, "Usuario y contraseña copiados.")
        : T("admin.system.users.access_not_copied", null, "Cuenta creada. Copiá los datos de acceso.")) + '</span></div></div>';
    var button = $("#doctor-user-copy", host);
    var copy = function () {
      if (!isCurrent()) return;
      button.disabled = true;
      copyDoctorCredentials(Promise.resolve(result), isCurrent).then(function (ok) {
        if (!isCurrent()) return;
        var status = $("#doctor-user-copy-status", host);
        if (status) status.textContent = ok
          ? T("admin.system.users.access_copied", null, "Usuario y contraseña copiados.")
          : T("admin.system.users.clipboard_blocked", null, "Portapapeles bloqueado. Seleccioná y copiá los datos.");
        button.disabled = false;
      });
    };
    button.addEventListener("click", copy);
    own(function () { button.removeEventListener("click", copy); });
  }

  /* Estado de la cuenta de un médico, dicho como lo diría la mesa (responsable del producto,
     2026-10-19: médico = usuario, la cuenta existe siempre). Una sola
     traducción del dato para la lista de Médicos y la pestaña Usuario. */
  var ACCOUNT_ORDER = ["sin_invitar", "invitado", "ingreso", "perfil_completo"];
  export function accountStateLabel(item) {
    if (!item) return "";
    var estado = item.cuenta_estado || "";
    if (item.activo === false && estado !== "sin_invitar") {
      return T("admin.system.users.account_state.inactiva", null, "Inactiva");
    }
    if (estado === "sin_invitar") return T("admin.system.users.account_state.sin_invitar", null, "Sin invitar");
    if (estado === "invitado") return T("admin.system.users.account_state.invitado", null, "Invitado");
    if (estado === "ingreso") return T("admin.system.users.account_state.ingreso", null, "Ingresó");
    if (estado === "perfil_completo") return T("admin.system.users.account_state.perfil_completo", null, "Perfil completo");
    return item.activo === false ? T("admin.system.users.account_state.inactiva", null, "Inactiva") : "";
  }
  export function accountStateRank(item) {
    if (!item) return null;
    if (item.activo === false && item.cuenta_estado !== "sin_invitar") return ACCOUNT_ORDER.length;
    var rank = ACCOUNT_ORDER.indexOf(item.cuenta_estado || "");
    return rank < 0 ? null : rank;
  }

  /* Resultado de una invitación (crear e invitar, Invitar, Reenviar): con
     email, a quién salió; sin email — o si no salió — el enlace de un solo
     uso para copiar y mandar por WhatsApp o en mano. El Copiar es el mismo
     del acceso con contraseña (copyText). Devuelve su propio disposer. */
  export function showInviteOutcome(host, result, options) {
    options = options || {};
    var isCurrent = typeof options.isCurrent === "function" ? options.isCurrent : function () { return host.isConnected; };
    var cleanups = [];
    function dispose() { cleanups.splice(0).forEach(function (cleanup) { cleanup(); }); }
    if (!host || !result || !isCurrent()) return dispose;
    if (result.email_enviado) {
      host.innerHTML = '<p class="doctor-user-outcome" role="status">' + esc(result.email
        ? T("admin.system.users.invite_sent_to", { email: result.email }, "Invitación enviada a {email}.")
        : T("admin.system.users.invite_sent", null, "Invitación enviada.")) + "</p>";
      return dispose;
    }
    var lead = result.email
      ? T("admin.system.users.invite_link_email_failed", { email: result.email },
        "No se pudo enviar el email a {email}. Copiá el enlace y mandáselo.")
      : T("admin.system.users.invite_link_copy", null,
        "Copiá el enlace y mandáselo al médico. Sirve una sola vez, durante 7 días.");
    if (!result.enlace) {
      host.innerHTML = '<p class="doctor-user-outcome" role="status">' + esc(T("admin.system.users.email_not_sent", null,
        "No se pudo enviar el email. Quedó creado el usuario.")) + "</p>";
      return dispose;
    }
    host.innerHTML = '<div class="doctor-user-credentials doctor-user-invite-link">'
      + '<p class="doctor-user-outcome" role="status">' + esc(lead) + "</p>"
      // Without an email the doctor logs in with the username, so the admin
      // passes it on together with the link.
      + (result.usuario ? row(T("admin.system.users.col.usuario", null, "Usuario"),
        '<strong class="doctor-user-invite-username">' + esc(result.usuario) + "</strong>") : "")
      + row(T("admin.system.users.invite_link", null, "Enlace de invitación"),
        '<input class="cnt-in doctor-user-select doctor-user-link-input" type="text" readonly autocomplete="off" value="' + esc(result.enlace) + '">')
      + '<div class="doctor-user-link-actions"><button type="button" class="btn btn-quiet doctor-user-link-copy">'
        + esc(T("admin.system.users.copy", null, "Copiar")) + "</button>"
      + '<span class="doctor-user-copy-status" role="status"></span></div></div>';
    var button = $(".doctor-user-link-copy", host);
    var field = $(".doctor-user-link-input", host);
    var copy = function () {
      if (!isCurrent()) return;
      button.disabled = true;
      copyText(Promise.resolve(result.enlace), isCurrent).then(function (ok) {
        if (!isCurrent()) return;
        var status = $(".doctor-user-copy-status", host);
        if (status) status.textContent = ok
          ? T("admin.system.users.link_copied", null, "Enlace copiado.")
          : T("admin.system.users.link_clipboard_blocked", null, "Portapapeles bloqueado. Seleccioná y copiá el enlace.");
        if (!ok && field) field.select();
        button.disabled = false;
      });
    };
    button.addEventListener("click", copy);
    cleanups.push(function () { button.removeEventListener("click", copy); });
    return dispose;
  }

  function bindDoctorSubmit(group, handler, own) {
    if (!group) return;
    // The doctor editor already owns a form. Nested forms are parsed away by
    // browsers, so these independent account actions use explicit click/Enter.
    var button = $('button[type="button"]', group);
    var keyboard = function (event) {
      if (event.key === "Enter" && /^(INPUT|SELECT)$/.test(event.target.tagName)) handler(event);
    };
    button.addEventListener("click", handler);
    group.addEventListener("keydown", keyboard);
    own(function () {
      button.removeEventListener("click", handler);
      group.removeEventListener("keydown", keyboard);
    });
  }

  var DOCTOR_USER_OWNERS = new WeakMap();

  export function mountDoctorUser(host, medicoId, doctor, options) {
    options = options || {};
    if (!host) return { ready: Promise.resolve(), dispose: function () {} };
    var previous = DOCTOR_USER_OWNERS.get(host);
    if (previous) previous.dispose();
    var disposed = false;
    var cleanups = [];
    var lifetimeCleanups = [];
    var renderVersion = 0;
    var controller = typeof AbortController === "undefined" ? null : new AbortController();
    var owner = {
      ready: null,
      dispose: function () {
        if (disposed) return;
        disposed = true;
        if (controller) controller.abort();
        cleanups.splice(0).forEach(function (cleanup) { cleanup(); });
        lifetimeCleanups.splice(0).forEach(function (cleanup) { cleanup(); });
        if (DOCTOR_USER_OWNERS.get(host) === owner) {
          DOCTOR_USER_OWNERS.delete(host);
          // Credentials are short-lived and must not survive an abandoned tab.
          host.textContent = "";
        }
      }
    };
    function ownerCurrent() {
      return !disposed && DOCTOR_USER_OWNERS.get(host) === owner && host.isConnected;
    }
    function own(cleanup) { cleanups.push(cleanup); }
    DOCTOR_USER_OWNERS.set(host, owner);
    if (options.signal) {
      var disposeOnAbort = function () { owner.dispose(); };
      if (options.signal.aborted) disposeOnAbort();
      else {
        options.signal.addEventListener("abort", disposeOnAbort, { once: true });
        lifetimeCleanups.push(function () { options.signal.removeEventListener("abort", disposeOnAbort); });
      }
    }
    if (disposed) {
      owner.ready = Promise.resolve();
      return owner;
    }
    host.classList.add("doctor-user-host");
    // One email per doctor (2026-11-03): the account logs in, is invited and
    // recovers its password with the email of «Información básica», shown
    // here as it is — never a second field that drifts from it.
    function doctorEmailRow(id, accountEmail, invitable) {
      // An account that predates the merge may still hold its own: the
      // invitation goes there (the server reads the account first).
      var email = String(accountEmail || doctor.email || "").trim();
      // An account that already came in needs no invitation, so the hint
      // about where the invitation's email goes has nothing to say: no row.
      if (!email && invitable === false) return "";
      return row(T("admin.system.users.col.email", null, "Email"), email
        ? '<span class="doctor-user-email" id="' + esc(id) + '">' + esc(email) + "</span>"
        : '<span class="doctor-user-email" id="' + esc(id) + '">' + esc(T("admin.system.users.doctor_email_where", null,
            "Escribí el email en Información básica para que la invitación le llegue.")) + "</span>");
    }
    // «Cambiar la dirección del calendario», built like the doctor's own in
    // Mis turnos (admin-my-appointments.mjs): the button, or in its place the
    // consequence and the two answers. Same wording for the answers.
    function calRotateHtml(confirming, usuario) {
      if (!confirming) {
        return '<button type="button" class="btn btn-quiet" data-doctor-user-cal-rotate>'
          + esc(T("admin.system.users.reset_calendar", null, "Cambiar la dirección del calendario")) + "</button>";
      }
      // A doctor still without a name (incompleto) is named by its account.
      var who = nombre(doctor && doctor.nombre) || usuario || "";
      return "<p>" + esc(T("admin.system.users.reset_calendar_confirm", { name: who },
          "La dirección actual deja de funcionar y {name} va a tener que agregar la nueva desde Mis turnos.")) + "</p>"
        + '<button type="button" class="btn btn-no" data-doctor-user-cal-rotate-yes>' + esc(T("admin.queue.my_appointments.cal_rotate_yes", null, "Cambiar la dirección")) + "</button> "
        + '<button type="button" class="btn btn-quiet" data-doctor-user-cal-rotate-no>' + esc(T("admin.queue.my_appointments.cal_rotate_no", null, "No cambiarla")) + "</button>";
    }
    // `outcome` is what the action that triggered this reread produced (an
    // invitation link, a sent email): painted under the refreshed account so
    // the new state and the link to copy are both on screen.
    function render(outcome) {
      if (!ownerCurrent()) return Promise.resolve();
      var version = ++renderVersion;
      function current() { return ownerCurrent() && version === renderVersion; }
      if (controller) controller.abort();
      controller = typeof AbortController === "undefined" ? null : new AbortController();
      cleanups.splice(0).forEach(function (cleanup) { cleanup(); });
      var requestOptions = controller ? { signal: controller.signal } : null;
      var doctorReady = doctor ? Promise.resolve(doctor) : fetchMedicos(false, requestOptions, current).then(function (items) {
        return items.filter(function (item) { return String(item.id) === String(medicoId); })[0] || {};
      });
      return Promise.all([fetchLista(true, current, requestOptions), doctorReady]).then(function (results) {
      if (!current()) return;
      var data = results[0];
      doctor = results[1];
      var portal = !!data.acceso_portal;
      var portalSin2fa = portal && !data.acceso_portal_totp;
      var item = (data.items || []).filter(function (x) {
        // A doctor promoted to administrator keeps the link (2026-11-02): the
        // account is found by the doctor, whatever its role.
        return (x.rol === "medico" || x.rol === "admin") && String(x.medico_id || "") === String(medicoId);
      })[0];
      if (!item) {
        var defaults = doctorUserDefaults(doctor, data.items || []);
        var candidates = defaults.candidates;
        var optionMarkup = candidates.map(function (x) {
          var details = [rolLabel(x.rol)];
          if (x.activo === false) details.push(T("admin.system.users.inactivo", null, "Inactivo"));
          return '<option value="' + esc(x.usuario) + '"' + (x.usuario === defaults.match ? ' selected' : '') + '>' + esc(x.usuario + " (" + details.join(", ") + ")") + "</option>";
        }).join("");
        // Linking an existing user is a legacy repair, never the normal path:
        // every doctor gets its account when it is created (the product owner, 2026-10-19:
        // "don't make this a separate step to link users to doctors"). It
        // stays folded behind «Reparar vínculo», and only here, where the
        // doctor has no account at all.
        var linkControl = candidates.length
          ? '<div class="doctor-user-link-actions doctor-user-repair"><button type="button" class="btn btn-quiet" id="doctor-user-repair" aria-expanded="false" aria-controls="doctor-user-link-form">'
            + esc(T("admin.system.users.repair_link", null, "Reparar vínculo")) + "</button></div>"
            + '<div id="doctor-user-link-form" class="doctor-user-link-form" hidden>'
            + row(T("admin.system.users.existing_user", null, "Usuario existente"),
              '<select class="cnt-in doctor-user-select" id="doctor-user-existing" name="usuario" aria-describedby="doctor-user-link-error">'
                + '<option value="">' + esc(T("admin.system.users.pick_existing_user", null, "Elegí un usuario…")) + "</option>"
                + optionMarkup + "</select>"
              + '<p class="doctor-user-link-error" id="doctor-user-link-error" role="alert" hidden></p>',
              "doctor-user-existing")
            + '<div class="doctor-user-link-actions">'
              + '<button type="button" class="btn ' + (defaults.match ? "btn-ok" : "btn-quiet") + '">' + esc(T("admin.system.users.link_as_doctor", null, "Vincular como médico")) + "</button>"
            + "</div></div>"
          : "";
        host.innerHTML = '<p class="doctor-user-empty">' + esc(T("admin.system.users.doctor_without_account", null,
            "Este médico todavía no tiene cuenta.")) + "</p>"
          + '<div id="doctor-user-create-form" class="doctor-user-link-form">'
          + row(T("admin.system.users.new_username", null, "Usuario nuevo"),
            '<input class="cnt-in doctor-user-select" id="doctor-user-new" name="usuario" type="text" inputmode="text" autocomplete="off" autocapitalize="none" spellcheck="false" value="' + esc(defaults.username) + '" aria-describedby="doctor-user-create-error">'
            + '<p class="doctor-user-link-error" id="doctor-user-create-error" role="alert" hidden></p>', "doctor-user-new")
          + doctorEmailRow("doctor-user-email")
          // Portal-login installation: the account is always an invitation
          // by email — no «usuario y contraseña» to choose.
          + hiddenIf(portal, row(T("admin.system.users.access_mode", null, "Acceso"),
            '<select class="cnt-in doctor-user-select" id="doctor-user-mode" name="access_mode">'
            + (portal ? "" : '<option value="create">' + esc(T("admin.system.users.login_password", null, "Usuario y contraseña")) + '</option>')
            + '<option value="invite">' + esc(T("admin.system.users.invite", null, "Invitar")) + '</option></select>', 'doctor-user-mode'))
          + '<div class="doctor-user-link-actions"><button type="button" class="btn ' + (defaults.match ? "btn-quiet" : "btn-ok") + '">'
          + esc(T("admin.system.users.create_copy_access", null, "Crear y copiar acceso")) + '</button></div></div>'
          + linkControl
          + '<div id="doctor-user-outcome"></div>';
      } else {
        // The account every doctor has, inline: who, in which state, when it
        // last came in — and what can be done with it from here.
        var activo = item.activo !== false;
        var esAdmin = item.rol === "admin";
        var sinInvitar = item.cuenta_estado === "sin_invitar";
        var entro = item.cuenta_estado === "ingreso" || item.cuenta_estado === "perfil_completo";
        var inviteLabel = sinInvitar || !activo
          ? T("admin.system.users.invite", null, "Invitar")
          : T("admin.system.users.resend_invite", null, "Reenviar invitación");
        // An account that already came in needs no invitation: it has its
        // access. A deactivated one keeps «Invitar» — inviting reactivates it.
        var invitable = !(entro && activo);
        host.innerHTML = '<div class="doctor-user-summary">'
          + row(T("admin.system.users.col.usuario", null, "Usuario"), esc(item.usuario))
          + row(T("admin.system.users.col.cuenta", null, "Cuenta"), esc(accountStateLabel(item)))
          // The role is a yes/no setting of the account: one checkbox that
          // saves by itself (same PUT as Usuarios), never a button that
          // flips its own label.
          + row(T("admin.system.users.col.rol", null, "Rol"), '<label class="cnt-check"><input type="checkbox" id="doctor-user-admin"'
            + (esAdmin ? " checked" : "") + '><span>' + esc(rolLabel("admin")) + "</span></label>", "doctor-user-admin")
          + (sinInvitar || portalSin2fa ? "" : row(T("admin.system.users.col.tfa", null, "2FA"), '<span class="chip ' + clase2fa(item) + '">' + esc(estado2fa(item)) + "</span>"))
          // Never came in: the state already says so, no «Última visita —».
          + (item.ultimo_acceso ? row(T("admin.system.users.col.ultima_visita", null, "Última visita"), esc(fechaVisita(item.ultimo_acceso))) : "")
          // The email is a datum of the account like the rows above, not a
          // field of the invitation: same row, same label.
          + doctorEmailRow("doctor-user-invite-email", item.email, invitable)
          + "</div>"
          + (invitable ? '<div id="doctor-user-invite-form" class="doctor-user-link-form">'
            + '<p class="doctor-user-link-error" id="doctor-user-invite-error" role="alert" hidden></p>'
            + '<div class="doctor-user-link-actions"><button type="button" class="btn btn-ok">' + esc(inviteLabel) + "</button></div></div>" : "")
          // Only what can be done with the account in the state it is in
          // (Opus UI review 2026-11-03): an account that never came in has no
          // password to reset (the invitation above is how it gets one), no
          // second factor to reset unless one is set, no calendar address
          // handed out yet. «Desactivar» stands apart, after the rest.
          + '<div class="cnt-actions doctor-user-actions">'
          + (sinInvitar && !portal ? '<button type="button" class="btn btn-quiet" id="doctor-user-password-access">' + esc(T("admin.system.users.login_password", null, "Usuario y contraseña")) + "</button>" : "")
          + (entro && !portal ? '<button type="button" class="btn btn-quiet" data-doctor-user-action="reset-password">' + esc(T("admin.system.users.reset_password", null, "Restablecer contraseña")) + "</button>" : "")
          + ((item.tiene_2fa || entro) && !portalSin2fa ?'<button type="button" class="btn btn-quiet" data-doctor-user-action="reset-2fa">' + esc(T("admin.system.users.reset_2fa", null, "Restablecer 2FA")) + "</button>" : "")
          // The doctor's own «Cambiar la dirección» (Mis turnos), copied: the
          // same confirm in place, the same answers.
          + (entro ? '<div class="doctor-user-cal-rotate">' + calRotateHtml(false) + "</div>" : "")
          + "</div>"
          + (activo ? '<div class="doctor-user-danger"><button type="button" class="btn btn-quiet" data-doctor-user-action="disable">' + esc(T("admin.system.users.deactivate", null, "Desactivar")) + "</button></div>" : "")
          + '<div id="doctor-user-outcome"></div>';
      }
      if (outcome && outcome.invite) {
        own(showInviteOutcome($("#doctor-user-outcome", host), outcome.invite, { isCurrent: current }));
      }
      var busy = false;
      function setBusy(value) {
        busy = value;
        $$('.doctor-user-link-form button', host).forEach(function (button) { button.disabled = value; });
      }
      var createForm = $("#doctor-user-create-form", host);
      bindDoctorSubmit(createForm, function (event) {
        event.preventDefault();
        if (!current() || busy) return;
        var input = $("#doctor-user-new", createForm);
        var error = $("#doctor-user-create-error", createForm);
        var username = input.value.trim();
        if (!validUsername(username)) {
          input.setAttribute("aria-invalid", "true");
          error.textContent = T("admin.system.users.username_format", null, "Usá al menos 2 caracteres, sin espacios.");
          error.hidden = false;
          return;
        }
        input.removeAttribute("aria-invalid");
        error.hidden = true;
        setBusy(true);
        var mode = $("#doctor-user-mode", createForm).value;
        var email = String(doctor.email || "").trim();
        var creation = doctorAction(medicoId, mode, { usuario: username, email: email }, current);
        var copying = mode === 'create' ? copyDoctorCredentials(creation, current) : Promise.resolve(false);
        creation.then(function (result) {
          if (!current()) return;
          if (mode === 'invite') {
            // The invitation gave the doctor its account: reread it and keep
            // the outcome (email sent, or the one-use link to copy) under it.
            // The generated password is never revealed (the product owner, 2026-10-19).
            return owner.render({ invite: result });
          }
          // Render access before waiting for an OS clipboard permission prompt.
          showDoctorCredentials(host, result, false, current, own);
          copying.then(function (copied) {
            if (current() && copied && $("#doctor-user-copy-status", host)) {
              $("#doctor-user-copy-status", host).textContent = T("admin.system.users.access_copied", null, "Usuario y contraseña copiados.");
            }
          });
        }).catch(function (err) {
          if (!current()) return;
          error.textContent = errText(err, T("admin.system.users.action_error", null, "No se pudo completar la acción."));
          error.hidden = false;
          setBusy(false);
        });
      }, own);
      var modeControl = createForm && $("#doctor-user-mode", createForm);
      if (modeControl) {
        var modeChanged = function () {
          $('button[type="button"]', createForm).textContent = modeControl.value === 'invite'
            ? T("admin.system.users.send_invite", null, "Enviar invitación")
            : T("admin.system.users.create_copy_access", null, "Crear y copiar acceso");
        };
        modeControl.addEventListener('change', modeChanged);
        own(function () { modeControl.removeEventListener('change', modeChanged); });
      }
      var linkForm = $("#doctor-user-link-form", host);
      bindDoctorSubmit(linkForm, function (event) {
        event.preventDefault();
        if (!current() || busy) return;
        var select = $("#doctor-user-existing", linkForm);
        var error = $("#doctor-user-link-error", linkForm);
        var username = select ? select.value : "";
        if (!username) {
          if (select) select.setAttribute("aria-invalid", "true");
          if (error) {
            error.textContent = T("admin.system.users.pick_existing_user_error", null, "Elegí un usuario existente.");
            error.hidden = false;
          }
          return;
        }
        if (select) select.removeAttribute("aria-invalid");
        if (error) error.hidden = true;
        setBusy(true);
        doctorAction(medicoId, "link", { usuario: username }, current)
          .then(function () { if (current()) return owner.render(); })
          .catch(function (err) {
            if (!current()) return;
            if (error) {
              error.textContent = errText(err, T("admin.system.users.action_error", null, "No se pudo completar la acción."));
              error.hidden = false;
            }
            setBusy(false);
          });
      }, own);
      var repair = $("#doctor-user-repair", host);
      if (repair && linkForm) {
        var toggleRepair = function () {
          if (!current()) return;
          linkForm.hidden = !linkForm.hidden;
          repair.setAttribute("aria-expanded", linkForm.hidden ? "false" : "true");
          if (!linkForm.hidden) { var pick = $("#doctor-user-existing", linkForm); if (pick) pick.focus(); }
        };
        repair.addEventListener("click", toggleRepair);
        own(function () { repair.removeEventListener("click", toggleRepair); });
      }
      // «Invitar» / «Reenviar invitación»: every field optional. With an email
      // it goes out by itself; without one the link comes back to copy.
      var inviteForm = $("#doctor-user-invite-form", host);
      bindDoctorSubmit(inviteForm, function (event) {
        event.preventDefault();
        if (!current() || busy) return;
        var error = $("#doctor-user-invite-error", inviteForm);
        error.hidden = true;
        setBusy(true);
        // The doctor's one email (Información básica): the server reads it.
        doctorAction(medicoId, "invite", null, current).then(function (result) {
          if (!current()) return;
          return owner.render({ invite: result });
        }).catch(function (err) {
          if (!current()) return;
          error.textContent = errText(err, T("admin.system.users.action_error", null, "No se pudo completar la acción."));
          error.hidden = false;
          setBusy(false);
        });
      }, own);
      // «Usuario y contraseña»: only for the account nobody has used yet. The
      // one response that reveals a generated password, copied in the gesture.
      var passwordAccess = $("#doctor-user-password-access", host);
      if (passwordAccess) {
        var grantPassword = function () {
          if (!current() || busy) return;
          passwordAccess.disabled = true;
          var creation = doctorAction(medicoId, "create", null, current);
          var copying = copyDoctorCredentials(creation, current);
          creation.then(function (result) {
            if (!current()) return;
            showDoctorCredentials(host, result, false, current, own);
            copying.then(function (copied) {
              if (current() && copied && $("#doctor-user-copy-status", host)) {
                $("#doctor-user-copy-status", host).textContent = T("admin.system.users.access_copied", null, "Usuario y contraseña copiados.");
              }
            });
          }).catch(function (err) {
            if (!current()) return;
            toast(errText(err, T("admin.system.users.action_error", null, "No se pudo completar la acción.")));
            passwordAccess.disabled = false;
          });
        };
        passwordAccess.addEventListener("click", grantPassword);
        own(function () { passwordAccess.removeEventListener("click", grantPassword); });
      }
      // «Administrador»: same account, same doctor, only the role changes
      // (PUT as in Usuarios). The ticked box is the confirmation — no toast;
      // a refusal unticks it and says why under the row.
      var adminBox = $("#doctor-user-admin", host);
      if (adminBox && item) {
        var changeRole = function () {
          if (!current()) return;
          var wanted = adminBox.checked;
          adminBox.disabled = true;
          fieldMsg(host, "doctor-user-admin", "", "");
          fetch("/api/admin/users/" + encodeURIComponent(item.usuario), {
            method: "PUT", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rol: wanted ? "admin" : "medico" })
          }).then(function (res) {
            var boundary = (res.status === 401 || res.status === 403)
              ? Session.confirmar(res, undefined, current)
              : Promise.resolve(false);
            return boundary.then(function (transitioned) {
              if (transitioned) throw new Error("401");
              return res.json().catch(function () { return {}; });
            }).then(function (data) {
              if (!res.ok || data?.ok !== true) throw new Error(data?.error || T("admin.system.users.action_error", null, "No se pudo completar la acción."));
              CACHE = null;
              item.rol = wanted ? "admin" : "medico";
              if (!current()) return;
              adminBox.disabled = false;
            });
          }).catch(function (err) {
            if (!current()) return;
            adminBox.checked = !wanted;
            adminBox.disabled = false;
            if (String(err.message) !== "401") {
              fieldMsg(host, "doctor-user-admin", errText(err, T("admin.system.users.action_error", null, "No se pudo completar la acción.")), "mal");
            }
          });
        };
        adminBox.addEventListener("change", changeRole);
        own(function () { adminBox.removeEventListener("change", changeRole); });
      }
      // «Cambiar la dirección del calendario»: the confirm takes the button's
      // place; «No cambiarla» puts the button back.
      var calRotate = $(".doctor-user-cal-rotate", host);
      if (calRotate && item) {
        var actionsRow = $(".doctor-user-actions", host);
        var paintCal = function (confirming) {
          calRotate.innerHTML = calRotateHtml(confirming, item.usuario);
          calRotate.classList.toggle("is-confirming", confirming);
          if (actionsRow) actionsRow.classList.toggle("is-confirming", confirming);
        };
        var calRotating = false;
        var onCalClick = function (event) {
          var target = event.target;
          if (!current() || !target || !target.closest || calRotating) return;
          if (target.closest("[data-doctor-user-cal-rotate]")) {
            paintCal(true);
            var yes = $("[data-doctor-user-cal-rotate-yes]", calRotate);
            if (yes) yes.focus();
          } else if (target.closest("[data-doctor-user-cal-rotate-no]")) {
            paintCal(false);
            var again = $("[data-doctor-user-cal-rotate]", calRotate);
            if (again) again.focus();
          } else if (target.closest("[data-doctor-user-cal-rotate-yes]")) {
            calRotating = true;
            $$("button", calRotate).forEach(function (button) { button.disabled = true; });
            doctorAction(medicoId, "reset-calendar", null, current).then(function () {
              if (!current()) return;
              toast(T("admin.queue.my_appointments.cal_rotated", null, "Listo: la dirección anterior ya no funciona."));
              return owner.render();
            }).catch(function (err) {
              if (!current()) return;
              calRotating = false;
              paintCal(false);
              toast(errText(err, T("admin.queue.my_appointments.cal_rotate_retry", null, "No pudimos cambiarla ahora. Probá de nuevo en un momento.")));
            });
          }
        };
        calRotate.addEventListener("click", onCalClick);
        own(function () { calRotate.removeEventListener("click", onCalClick); });
      }
      $$("[data-doctor-user-action]", host).forEach(function (button) {
        var action = function () {
          if (!current()) return;
          button.disabled = true;
          var name = button.getAttribute("data-doctor-user-action");
          doctorAction(medicoId, name, null, current)
            .then(function (result) {
              if (!current()) return;
              // `invite`/`reset-password` son las dos acciones que dependen
              // del email, y el envío es best-effort: la fila queda creada
              // aunque Mailgun no responda. Un "Listo." ahí es la trampa de
              // invitación parcial (el médico nunca recibe nada y el admin
              // cree que sí) — el resultado trae `email_enviado` para esto.
              var emailCayo = (name === "invite" || name === "reset-password")
                && result && result.email_enviado === false;
              var render = owner.render();
              if (!emailCayo) {
                toast(T("admin.system.users.done", null, "Listo."));
                return render;
              }
              toast(T("admin.system.users.email_not_sent", null,
                "No se pudo enviar el email. Quedó creado el usuario."));
              // OJO con el guard: `current()` acá adentro es la versión que
              // caducó cuando owner.render() incrementó renderVersion — un
              // check de staleness pensado para PINTAR, no para reaccionar a
              // la pintura nueva. La vida útil real del host la dice
              // isConnected; el aviso se appendea AL host ya repintado y la
              // próxima render() lo barre sola (feedback fresco por acción).
              return render.then(function () {
                if (!host.isConnected) return;
                if (!$(".doctor-user-actions", host)) return;
                if (document.getElementById("doctor-user-email-warning")) return;
                var warn = document.createElement("p");
                warn.id = "doctor-user-email-warning";
                warn.className = "doctor-user-link-error";
                warn.setAttribute("role", "alert");
                warn.textContent = T("admin.system.users.email_not_sent_detail", null,
                  "El email no salió (el servidor de correo no respondió). Reintentá en un rato o compartí el acceso personalmente.");
                host.appendChild(warn);
              });
            })
            .catch(function (err) {
              if (!current()) return;
              toast(errText(err, T("admin.system.users.action_error", null, "No se pudo completar la acción.")));
              button.disabled = false;
            });
        };
        button.addEventListener("click", action);
        own(function () { button.removeEventListener("click", action); });
      });
      }).catch(function (err) {
        // An aborted/disposed owner has no remaining UI to recover. Its
        // readiness must settle rather than leaking an unhandled rejection.
        if (!current()) return;
        throw err;
      });
    }
    owner.render = function (outcome) {
      owner.ready = render(outcome);
      return owner.ready;
    };
    owner.render();
    return owner;
  }

  // Compatibility for the existing Doctors consumer until it adopts the
  // explicit owner. Repeated legacy renders still replace old ownership.
  function renderDoctorUser(host, medicoId, doctor, options) {
    return mountDoctorUser(host, medicoId, doctor, options).ready;
  }

  // =====================================================================
  // routing propio (ver comentario del encabezado)
  // =====================================================================

  // Token monotónico de ruta: cada route() lo incrementa y toda pintura
  // async (lista, formulario, error) re-verifica que el suyo siga siendo el
  // último — un fetch tardío de una ruta abandonada nunca repinta la pantalla
  // que la persona ya dejó (goTo/popstate durante la carga).
  var routeSeq = 0;

  function showOnly() {
    ["sec-solicitudes", "sec-cola", "sec-servicios", "sec-centros", "sec-medicos", "sec-usuarios",
     "sec-pacientes", "sec-mis-pacientes"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.style.display = id === "sec-usuarios" ? "" : "none";
    });
  }

  function route() {
    var seq = ++routeSeq;
    var parsed = directState();
    showOnly();
    $$("#adm-nav a").forEach(function (a) { a.classList.toggle("on", a.getAttribute("data-nav") === "usuarios"); });
    var h1 = $("#adm-h1");
    if (h1) {
      // An open user keeps the band: renderForm() writes «Usuarios / name».
      h1.style.display = "";
      h1.textContent = T("admin.system.users.title", null, "Usuarios");
    }
    // «Nuevo usuario» belongs to the list's band, not to an open user's.
    var nuevo = document.querySelector('.adm-h1row > a[data-users-state="new"]');
    if (nuevo) nuevo.style.display = parsed.id ? "none" : "";
    var sub = $("#adm-sub");
    if (sub) sub.style.display = "none";

    var p = parsed.id === "nuevo" ? renderForm(null, seq) : (parsed.id ? renderForm(parsed.id, seq) : renderLista(seq));
    p.catch(function (e) {
      if (String(e.message) === "redirect") return;
      if (seq !== routeSeq) return;
      var sec = document.getElementById("sec-usuarios");
      if (!sec) return;
      sec.innerHTML = String(e.message) === "403"
        ? '<p class="adm-empty">' + esc(T("admin.system.users.no_permission", null, "No tenés permiso para ver esta sección.")) + '</p>'
        : '<p class="adm-empty">' + esc(T("admin.system.common.load_error_retry", null, "No se pudo cargar. Reintentá.")) + '</p>';
    });
  }

  var booted = false;
  // El template de /admin/users es el único llamador: importa el módulo y
  // llama boot(). La pantalla de Médicos importa este mismo módulo por
  // renderDoctorUser() — nunca bootea el controlador de Usuarios ni esconde
  // la pantalla de Médicos.
  export function boot() {
    if (booted) return;
    booted = true;
    window.addEventListener("pagehide", function () { routeSeq++; });
    window.addEventListener("pageshow", function (event) { if (event.persisted) route(); });
    window.addEventListener("popstate", route);
    document.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.button !== 0
          || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      var link = event.target.closest && event.target.closest("a[data-users-state]");
      if (!link) return;
      event.preventDefault();
      goTo(link.getAttribute("href"));
    });
    // Generic copy controls belong to the standalone Users boot lifecycle,
    // never to importing this module for the doctor editor.
    document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-copy]");
      if (!button) return;
      var text = button.getAttribute("data-copy") || "";
      var seq = routeSeq;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          if (seq !== routeSeq) return;
          toast(T("admin.system.common.copied", null, "Copiado."));
        }).catch(function () {});
      }
    });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", route);
    else route();
  }

  export function refresh() { CACHE = null; }

  export { renderDoctorUser };
