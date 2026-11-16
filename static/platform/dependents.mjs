/* static/platform/dependents.mjs — personas menores a cargo, reutilizable en
   /cuenta y en el wizard de turnos. Identidad clínica = DNI + nombre +
   nacimiento + sexo; el vínculo se declara una vez desde la cuenta del adulto.
   Módulo ES canónico (2026-09-05): antes era el IIFE raíz dependents.js con
   global window.SanaDependents; ese archivo se borró y éste es el dueño único.
   Named exports; sin global de navegador, sin gemelo clásico, sin wrapper
   CommonJS. Vive en static/platform/ y se carga por window.SANA_STATIC.

   Named API:
     renderCuenta(host)                       — panel de /account (#deps)
     renderSelector(host, {usuario, onSelect})— selector titular + a cargo
     renderAgregar(host, {heading, lede, onAdded, onCancel})
     renderElegir(host, {aptos, lede, onSelect})
     plan / aptosPorEdad / esPediatrico       — ruteo por edad del wizard

   Top-level await SOLO de platform/i18n.mjs (misma forma que
   account-form.mjs): un consumidor que espera este módulo también espera su
   traducción. La dependencia de account-form.mjs (el control de fecha de
   nacimiento) queda en cambio como promesa NO bloqueante a nivel de módulo:
   el formulario tiene que pintarse y ser cancelable MIENTRAS ese módulo
   sigue en vuelo (test/e2e/account-delayed.e2e.mjs), y su rechazo se
   recupera en pantalla — ver wireForm(). */
  // platform/i18n.mjs and platform/account-form.mjs travel with a content
  // hash (core/static_fingerprint.py, CLIENT_LOADED). A served file cannot
  // know its live URL, so the shell publishes window.SANA_STATIC and
  // importDep() resolves through that map — fail-closed in the browser
  // (reject + console.error, never a hardcoded fallback URL), the same
  // shape as listing.mjs. Node tests import the sibling files by URL.
  // Account form stays a NON-blocking module-level promise: the form must
  // paint and stay cancellable while that module is still in flight.
  function importDep(name, nodeUrl) {
    if (typeof window !== "undefined") {
      var u = (window.SANA_STATIC && window.SANA_STATIC[name]) || null;
      if (!u) {
        if (typeof console !== "undefined" && console.error) {
          console.error("[static] " + name + " is not in window.SANA_STATIC (the shell must emit static_fingerprint.manifest_script())");
        }
        return Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
      }
      return import(u);
    }
    return import(nodeUrl);
  }

  // Interface strings go through the catalog. The third argument is the
  // Spanish this call site replaced, so with no catalog loaded — a
  // single-language installation, a Storybook story, a node test — every
  // one of these renders exactly what it rendered before.
  var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url));
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  // Mismo gate que account-form.mjs (responsable del producto, 2026-09-11): el idioma ACTIVO del
  // visitante — wire value del bootstrap del server, nunca texto renderizado
  // — decide si se le pide DNI a la persona a cargo. En otro idioma el campo
  // no se muestra y el server acuña la clave sintética.
  function sinDniVisitante() {
    var lang = I18n && typeof I18n.activeLang === "function" ? I18n.activeLang() : "es";
    return lang.split("-")[0] !== "es";
  }
  // La clave sintética no se muestra (un número inventado no se le enseña a
  // una persona): meta de DNI vacía para esas filas.
  // `copiable`: el número se copia con un toque (Format.dniHtml); dentro de un
  // botón no, ahí el toque es del botón.
  var Format = await importDep("platform/format.mjs", new URL("./format.mjs", import.meta.url)).catch(function () { return null; });
  function dniMeta(p, copiable) {
    if (!p || !p.dni || p.identity_kind === "synthetic") return "";
    if (copiable && Format && Format.dniHtml) return Format.dniHtml(p.dni, "DNI ") || "";
    return "DNI " + esc(fmtDni(p.dni));
  }
  var ROOT = (typeof window !== "undefined") ? window : globalThis;
  var Account = null;
  var accountLoadError = null;
  var accountReady = importDep("platform/account-form.mjs", new URL("./account-form.mjs", import.meta.url)).then(function (m) {
    if (!m || typeof m.parseFlexibleDate !== "function" || typeof m.dobField !== "function") {
      throw new Error("[dependents] Account form does not expose the required date controls");
    }
    Account = m;
    return m;
  }).catch(function (err) {
    // A dependent can never silently degrade to a second date parser. Keep the
    // form recoverable, but make the unavailable native control explicit.
    accountLoadError = err;
    return null;
  });
  var $ = function (s, r) { return (r || document).querySelector(s); };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
  function dni(s) { return String(s || "").replace(/\D/g, ""); }
  function fmtDni(s) { var d=dni(s); return d ? d.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : ""; }
  function age(iso, now) {
    var m = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/); if (!m) return null;
    var n = (now instanceof Date) ? now : new Date(), a = n.getFullYear() - (+m[1]);
    if ((n.getMonth() + 1 < +m[2]) || (n.getMonth() + 1 === +m[2] && n.getDate() < +m[3])) a--;
    return a;
  }
  // Icons8 SF Regular files in static/icons, painted by mask with the
  // text colour (review 2026-11-15: nothing drawn by hand in the account).
  function depIco(file, px, color) {
    return '<span aria-hidden="true" style="display:inline-block;flex:none;vertical-align:middle;width:' + px + 'px;height:' + px + 'px;' +
      'background-color:' + (color || 'currentColor') + ';-webkit-mask:url(/static/icons/' + file + ') center/contain no-repeat;' +
      'mask:url(/static/icons/' + file + ') center/contain no-repeat;"></span>';
  }
  var CAL_ICON = depIco("calendar-sf-regular.svg", 18);
  function inject() {
    if (document.getElementById("dep-style")) return;
    var css =
      ".dep{max-width:560px}.dep h2{font-size:19px;font-weight:700;color:var(--text);letter-spacing:-.01em;margin:0 0 4px}.dep-lede{margin:0 0 14px;color:var(--text-2);font-size:15px;line-height:1.5}"+
      // Encabezado del panel "Personas a cargo": glyph mint 44×44 + título/
      // subtítulo/contenido a la derecha (mockup Claude Design 2026-07-16).
      ".dep-head{display:flex;align-items:flex-start;gap:14px}.dep-icon{width:44px;height:44px;border-radius:12px;background:var(--accent-soft);display:flex;align-items:center;justify-content:center;flex-shrink:0}.dep-body{flex:1;min-width:0}"+
      ".dep-list{display:flex;flex-direction:column;gap:9px}.dep-choice{display:flex;width:100%;align-items:center;justify-content:space-between;gap:12px;text-align:left;padding:14px 16px;border:1.5px solid var(--border-strong);border-radius:12px;background:var(--panel);color:var(--text);font:inherit;cursor:pointer}"+
      ".dep-choice:hover{border-color:var(--hover-bd-strong)}.dep-name{display:block;font-size:17px;font-weight:680}.dep-meta{display:block;margin-top:2px;color:var(--text-2);font-size:14px}.dep-chevron{color:var(--text-3);font-size:22px}"+
      ".dep-row{display:flex;align-items:baseline;justify-content:space-between;gap:14px;padding:12px 0;border-top:1px solid var(--border)}.dep-row:first-child{border-top:0}.dep-remove{color:var(--accent);font-size:13px;text-decoration:none;white-space:nowrap}"+
      ".dep-add{display:inline-flex;align-items:center;gap:9px;margin-top:4px;padding:11px 18px;border:1.5px solid var(--accent);border-radius:10px;background:transparent;color:var(--accent);font:500 15px var(--font);cursor:pointer}.dep-add:hover{background:var(--hover-bg)}.dep-list+.dep-add,.dep-rows+.dep-add{margin-top:14px}.dep-remove:hover{color:var(--accent-hover);}"+
      ".dep-form{margin-top:14px;padding-top:16px;border-top:1px solid var(--border)}.dep-form h3{font-size:18px;margin:0 0 14px}.dep-field{display:grid;grid-template-columns:118px minmax(0,1fr);gap:8px 13px;align-items:baseline;margin-bottom:13px}.dep-field>label{text-align:right;color:var(--text-2);font-size:13px;font-weight:500}.dep-in{width:100%;max-width:300px;font:inherit;font-size:16px;padding:10px 12px;border:1.5px solid var(--border-strong);border-radius:9px;background:var(--panel);color:var(--text)}.dep-dni{max-width:12ch;font-family:ui-monospace,monospace}.dep-date{max-width:13ch}.dep-seg{display:flex;max-width:300px;border:1.5px solid var(--border-strong);border-radius:9px;overflow:hidden}.dep-seg label{flex:1;position:relative}.dep-seg input{position:absolute;opacity:0}.dep-seg span{display:block;text-align:center;padding:10px 6px;border-left:1px solid var(--border-strong)}.dep-seg label:first-child span{border-left:0}.dep-seg input:checked+span{background:var(--accent-soft);color:var(--accent-hover);font-weight:500}"+
      // flex:0 0 auto: en la fila flex el checkbox no debe achicarse si el
      // texto de declaración jurada es largo.
      ".dep-declare{display:flex;align-items:flex-start;gap:9px;margin:13px 0 15px;font-size:14px;line-height:1.45;color:var(--text-2)}.dep-declare input{margin-top:4px;flex:0 0 auto}.dep-actions{display:flex;gap:14px;align-items:center}.dep-save{border:0;border-radius:var(--btn-radius,10px);background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);font:500 15px var(--font);padding:12px 18px;cursor:pointer}.dep-cancel{color:var(--accent);text-decoration:none}.dep-msg{margin-top:10px;font-size:14px;color:var(--text-2)}.dep-msg.err{color:var(--danger,#a3352f)}.dep-msg a{color:var(--accent);font-weight:600}"+
      // Nacimiento = input flexible + botón de calendario (mismo control que
      // Datos personales); el botón (.sc-calbtn) y el eco (.sc-echo) los estila
      // account-form.mjs.
      ".dep-dob-wrap{display:flex;gap:7px;align-items:stretch;max-width:300px}.dep-dob-wrap .dep-in{flex:1 1 auto;width:auto;min-width:0;max-width:none}"+
      "@media(max-width:520px){.dep-field{grid-template-columns:1fr;gap:5px}.dep-field>label{text-align:left}.dep-choice{padding:14px}.dep-row{align-items:flex-start}}";
    var st = document.createElement("style"); st.id = "dep-style"; st.textContent = css; document.head.appendChild(st);
  }
  function api(url, opts) {
    opts = opts || {}; opts.credentials = "same-origin";
    if (opts.body) opts.headers = {"Content-Type":"application/json"};
    return fetch(url, opts).then(function (r) { return r.json().catch(function(){return {};}).then(function(j){return {ok:r.ok,status:r.status,j:j};}); });
  }
  function load() { return api("/api/account/dependents").then(function (r) { return r.ok ? (r.j.dependientes || []) : []; }); }
  // Singular/plural en dos claves separadas (una frase entera cada una, con
  // {n}) — pegarle un sufijo al número no sobrevive la traducción al inglés
  // ("1 years" vs "1 year").
  function edadTexto(a) {
    return a === 1
      ? T("account.mine.dependents.age_one", { n: a }, "{n} año")
      : T("account.mine.dependents.age_other", { n: a }, "{n} años");
  }
  function label(p) { var a = age(p.nacimiento); return [p.nombre, p.apellido].filter(Boolean).join(" ") + (a == null ? "" : " · " + edadTexto(a)); }
  // Etiquetas del vínculo: mismas dos primeras en el <select> del alta y en
  // el meta del selector; "otro" difiere de contexto (opción del formulario
  // vs. resumen de una fila ya cargada), así que tiene una clave cada uno.
  function vinculoLabel(v) {
    if (v === "hijo") return T("account.mine.dependents.rel_child", null, "Hijo/a");
    if (v === "tutoriado") return T("account.mine.dependents.rel_ward", null, "Tutoriado/a");
    return T("account.mine.dependents.rel_other_meta", null, "Persona a cargo");
  }
  function formHtml() {
    function f(l, x) { return '<div class="dep-field"><label>'+l+'</label><div>'+x+'</div></div>'; }
    var sinDni = sinDniVisitante();
    return '<form class="dep-form" id="dep-form" novalidate><h3>' +
      T("account.mine.dependents.form_title", null, "Agregar persona a cargo") + '</h3>'+
      (sinDni ? "" : f("DNI",'<input class="dep-in dep-dni" id="dep-dni" inputmode="numeric" placeholder="50123456">'))+
      f(T("account.mine.dependents.field_name", null, "Nombre"),'<input class="dep-in" id="dep-nombre" autocomplete="off">')+
      f(T("account.mine.dependents.field_surname", null, "Apellido"),'<input class="dep-in" id="dep-apellido" autocomplete="off">')+
      // Nacimiento con el MISMO control que Datos personales: texto flexible +
      // calendario año-primero + eco en palabras (Account.dobField). Antes
      // era un input pelado sin feedback ni calendario y "no entraba" (responsable del producto
      // 2026-07-18). Las clases sc-* las estila account-form.mjs (injectStyles).
      f(T("account.mine.dependents.field_birth", null, "Nacimiento"),
        '<div class="dep-dob-wrap">'+
          '<input class="dep-in dep-date" id="dep-nac" inputmode="numeric" autocomplete="bday" placeholder="' +
          T("account.mine.dependents.dob_mask", null, "DD/MM/AAAA") + '">'+
          '<button type="button" class="sc-calbtn" id="dep-calbtn" aria-label="' +
          T("account.mine.dependents.pick_in_calendar", null, "Elegir en el calendario") + '">'+CAL_ICON+'</button>'+
        '</div>'+
        '<div id="dep-cal-host"></div>'+
        '<span class="sc-echo" id="dep-nac-echo"></span>')+
      f(sinDni ? T("account.mine.dependents.field_sex_plain", null, "Sexo") : T("account.mine.dependents.field_sex", null, "Sexo según DNI"),
        '<div class="dep-seg"><label><input type="radio" name="dep-sexo" value="F"><span>' +
        T("account.mine.dependents.sex_f", null, "Femenino") + '</span></label><label><input type="radio" name="dep-sexo" value="M"><span>' +
        T("account.mine.dependents.sex_m", null, "Masculino") + '</span></label><label><input type="radio" name="dep-sexo" value="X"><span>' +
        T("account.mine.dependents.sex_x", null, "Otro") + '</span></label></div>')+
      f(T("account.mine.dependents.field_rel", null, "Vínculo"),
        '<select class="dep-in" id="dep-vinculo"><option value="hijo">' + vinculoLabel("hijo") +
        '</option><option value="tutoriado">' + vinculoLabel("tutoriado") +
        '</option><option value="otro">' + T("account.mine.dependents.rel_other_option", null, "Otra persona a cargo") + '</option></select>')+
      '<label class="dep-declare"><input type="checkbox" id="dep-ddjj"><span>' +
      T("account.mine.dependents.declaration", null,
        "Declaro que soy responsable o estoy autorizado para pedir atención para esta persona, y que los datos son correctos.") +
      '</span></label>'+
      '<div class="dep-actions"><button class="dep-save" type="submit">' + T("account.mine.dependents.submit", null, "Agregar") +
      '</button><a class="dep-cancel" href="#">' + T("account.mine.dependents.cancel", null, "Cancelar") +
      '</a></div><div class="dep-msg" id="dep-msg"></div></form>';
  }
  function wireForm(host, onAdded, onCancel) {
    var form = $("#dep-form", host), cancel = $(".dep-cancel", host), msg = $("#dep-msg", host);
    var save = $(".dep-save", host), calBtn = $("#dep-calbtn", host);
    var active = true, ready = false, accountModule = null, saveController = null;
    // The cancellation route is deliberately available while the native
    // control is loading. A late import must not paint into a replaced form.
    if (save) save.disabled = true;
    if (calBtn) calBtn.disabled = true;
    function ownsForm() { return active && !!form && host.contains(form); }
    function setMessage(text, isError) {
      if (!ownsForm() || !msg) return;
      msg.className = "dep-msg" + (isError ? " err" : "");
      msg.textContent = text;
    }
    function unavailable() {
      if (!ownsForm() || !msg) return;
      if (save) save.disabled = true;
      if (calBtn) calBtn.disabled = true;
      msg.className = "dep-msg err";
      msg.textContent = T("account.mine.dependents.birth_control_unavailable", null,
        "No pudimos cargar el control de fecha. Recargá la página antes de continuar.") + " ";
      var reload = document.createElement("a");
      reload.href = window.location.href;
      reload.textContent = T("account.mine.dependents.reload", null, "Recargar la página");
      msg.appendChild(reload);
    }
    // Mismo control de fecha que Datos personales: eco en palabras + calendario.
    setMessage(T("account.mine.dependents.loading_birth_control", null,
      "Preparando el control de fecha de nacimiento…"), false);
    accountReady.then(function (mod) {
      if (!ownsForm()) return;
      if (!mod) { unavailable(); return; }
      try {
        mod.dobField({ input: $("#dep-nac", host), echo: $("#dep-nac-echo", host),
          calBtn: calBtn, calHost: $("#dep-cal-host", host) });
        accountModule = mod;
        ready = true;
        if (save) save.disabled = false;
        if (calBtn) calBtn.disabled = false;
        setMessage("", false);
      } catch (err) {
        accountLoadError = err;
        unavailable();
      }
    });
    cancel.addEventListener("click", function(e){
      active=false;
      if (saveController) saveController.abort();
      e.preventDefault();
      onCancel();
    });
    form.addEventListener("submit", function(e){
      e.preventDefault();
      if (!ready || !accountModule) {
        // Do not replace the unavailable state with plain text: it carries the
        // only recoverable action (a real reload link) after a failed import.
        if (accountLoadError) unavailable();
        else setMessage(T("account.mine.dependents.loading_birth_control", null,
          "Preparando el control de fecha de nacimiento…"), true);
        return;
      }
      var nacEl = $("#dep-nac",host);
      // dobField leaves the canonical ISO in dataset.iso. The same native
      // parser owns handwritten input too; no local regex may diverge.
      var iso = (nacEl && nacEl.dataset.iso) || accountModule.parseFlexibleDate(nacEl.value); var sex = $('input[name="dep-sexo"]:checked',host);
      var dniEl = $("#dep-dni",host);
      var body = {nombre:$("#dep-nombre",host).value.trim(),apellido:$("#dep-apellido",host).value.trim(),nacimiento:iso||"",sexo:sex?sex.value:"",vinculo:$("#dep-vinculo",host).value,declaracion_jurada:$("#dep-ddjj",host).checked};
      // Sin campo de DNI (visitante no hispanohablante) la key no viaja: el
      // server acuña la sintética. Con campo, el DNI se manda como siempre.
      if (dniEl) body.dni = dni(dniEl.value);
      if (!iso) {
        msg.textContent = T("account.mine.dependents.err_birth", null, "Revisá la fecha de nacimiento (DD/MM/AAAA).");
        return;
      }
      var btn=$(".dep-save",host); btn.disabled=true; setMessage(T("account.mine.dependents.saving", null, "Guardando…"), false);
      saveController = typeof AbortController === "function" ? new AbortController() : null;
      api("/api/account/dependents",{method:"POST",body:JSON.stringify(body),signal:saveController && saveController.signal}).then(function(r){
        // The network can win the cancel race (or an older browser cannot
        // abort it). A detached/cancelled form must never advance its owning
        // wizard after that response arrives.
        if (!ownsForm()) return;
        if(r.ok&&r.j.dependiente){onAdded(r.j.dependiente);return;}
        btn.disabled=false;
        setMessage(r.j.error||T("account.mine.dependents.err_save", null, "No pudimos agregarla. Probá de nuevo."), true);
      }).catch(function(){
        if (!ownsForm()) return;
        btn.disabled=false;
        setMessage(T("account.mine.dependents.err_network", null, "Error de red. Probá de nuevo."), true);
      });
    });
  }
  /* --- planificación del paciente del wizard (service.js la consume) ---
     El titular sigue siendo el camino directo cuando corresponde por edad.
     Si no corresponde (pediatría, geriatría u otro límite), buscamos personas
     a cargo aptas: 0 -> agregar, 1 -> selección automática, 2+ -> elegir. */
  function esPediatrico(servicio) {
    var e = (servicio && servicio.edad) || {};
    var max = e.max == null ? null : Number(e.max);
    return max != null && !isNaN(max) && max < 18;
  }
  function _apto(dep, servicio, now) {
    var e = (servicio && servicio.edad) || {};
    var a = age(dep && dep.nacimiento, now);
    if (a == null) return true;
    if (e.min != null && a < Number(e.min)) return false;
    if (e.max != null && a > Number(e.max)) return false;
    return true;
  }
  function aptosPorEdad(deps, servicio, now) {
    return (Array.isArray(deps) ? deps : []).filter(function (d) { return _apto(d, servicio, now); });
  }
  function plan(deps, servicio, usuario, now) {
    if (_apto(usuario, servicio, now)) return { paciente: usuario || null, paso: null };
    var aptos = aptosPorEdad(deps, servicio, now);
    if (!aptos.length) return { paciente: null, paso: "agregar" };
    if (aptos.length === 1) return { paciente: aptos[0], paso: null };
    return { paciente: null, paso: "elegir" };
  }

  /* --- pantalla completa "agregar" (alta obligatoria, sin volver atrás salvo
     que opts.onCancel lo permita) --- */
  function renderAgregar(host, opts) {
    inject(); opts = opts || {};
    host.innerHTML = '<section class="dep">' +
      (opts.heading ? "<h2>" + esc(opts.heading) + "</h2>" : "") +
      (opts.lede ? '<p class="dep-lede">' + esc(opts.lede) + "</p>" : "") +
      formHtml() + "</section>";
    wireForm(host,
      function (p) { if (opts.onAdded) opts.onAdded(p); },
      function () { if (opts.onCancel) opts.onCancel(); });
    if (!opts.onCancel) { var c = host.querySelector(".dep-cancel"); if (c) c.style.display = "none"; }
  }

  /* --- pantalla completa "elegir" entre 2+ personas aptas por edad --- */
  function renderElegir(host, opts) {
    inject(); opts = opts || {};
    var aptos = opts.aptos || [];
    function paint(showForm) {
      var choices = aptos.map(function (p, i) {
        return '<button type="button" class="dep-choice" data-i="' + i + '"><span><span class="dep-name">' +
          esc(label(p)) + "</span></span><span class=\"dep-chevron\">›</span></button>";
      }).join("");
      host.innerHTML = '<section class="dep">' +
        (opts.lede ? '<p class="dep-lede">' + esc(opts.lede) + "</p>" : "") +
        '<div class="dep-list">' + choices + "</div>" +
        '<button type="button" class="dep-add">' +
        T("account.mine.dependents.add_button", null, "Agregar persona a cargo") + '</button>' +
        (showForm ? formHtml() : "") + "</section>";
      host.querySelectorAll(".dep-choice").forEach(function (b) {
        b.addEventListener("click", function () { opts.onSelect(aptos[+b.dataset.i]); });
      });
      var addBtn = host.querySelector(".dep-add");
      if (addBtn) addBtn.addEventListener("click", function () { paint(true); });
      if (showForm) wireForm(host, function (p) { opts.onSelect(p); }, function () { paint(false); });
    }
    paint(false);
  }

  // glyph mint del panel (mismo trazo que el ítem "Personas a cargo" del riel)
  // + el "+" del botón "Agregar…" (mockup Claude Design 2026-07-16).
  var DEP_ICON = depIco("user-group-sf-regular.svg", 24, "var(--accent)");
  var PLUS_ICON = depIco("plus-sf-regular.svg", 17);

  function renderCuenta(host) {
    inject(); load().then(function(items){
      function paint(showForm){
        // Vacío = una línea + el botón, nada más (sin fila "todavía no agregaste
        // a nadie": es un dead-end que sólo agrega ruido). El responsable del producto 2026-07-12.
        var rows=items.map(function(p){return '<div class="dep-row"><span><b>'+esc([p.nombre,p.apellido].join(" "))+'</b><span class="dep-meta">'+[dniMeta(p, true), esc(edadTexto(age(p.nacimiento)))].filter(Boolean).join(" · ")+'</span></span><a class="dep-remove" href="#" data-dni="'+esc(p.dni)+'">' +
          T("account.mine.dependents.remove", null, "Quitar") + '</a></div>';}).join("");
        host.innerHTML='<section class="dep"><div class="dep-head"><span class="dep-icon">'+DEP_ICON+'</span><div class="dep-body">'+
          '<h2>' + T("account.mine.dependents.title", null, "Personas a cargo") + '</h2>'+
          '<p class="dep-lede">' + T("account.mine.dependents.lede", null,
            "Agregalos una vez y después pedís turnos para ellos desde tu cuenta, sin volver a cargar los datos.") + '</p>'+
          (rows?'<div class="dep-rows">'+rows+'</div>':"") +
          (showForm?formHtml():'<button type="button" class="dep-add">'+PLUS_ICON+
            T("account.mine.dependents.add_button", null, "Agregar persona a cargo") + '</button>')+
        '</div></div></section>';
        if(!showForm)$(".dep-add",host).addEventListener("click",function(e){e.preventDefault();paint(true);});
        host.querySelectorAll(".dep-remove").forEach(function(a){a.addEventListener("click",function(e){e.preventDefault();if(!ROOT.confirm(T("account.mine.dependents.confirm_remove", null, "¿Quitar esta persona de tu cuenta?")))return;api("/api/account/dependents/delete",{method:"POST",body:JSON.stringify({dni:a.dataset.dni})}).then(function(){items=items.filter(function(x){return x.dni!==a.dataset.dni;});paint(false);});});});
        if(showForm)wireForm(host,function(p){items.push(p);paint(false);},function(){paint(false);});
      } paint(false);
    });
  }
  function renderSelector(host, opts) {
    inject(); opts=opts||{}; var u=opts.usuario||{};
    load().then(function(items){
      function paint(showForm){
        // The account holder goes by name, not "Me" — on a shared computer
        // "Me" does not say whose session is open (same rule as the wizard's
        // patient picker, service.js).
        var choices=[u].concat(items).map(function(p,i){var nom=i===0?([p.nombre,p.apellido].filter(Boolean).join(" ")||T("account.mine.dependents.myself", null, "Yo")):label(p);var meta=i===0?dniMeta(p):[dniMeta(p), esc(vinculoLabel(p.vinculo))].filter(Boolean).join(" · ");return '<button type="button" class="dep-choice" data-i="'+i+'"><span><span class="dep-name">'+esc(nom)+(meta?'<span class="dep-meta">'+meta+'</span>':"")+'</span><span class="dep-chevron">›</span></button>';}).join("");
        host.innerHTML='<section class="dep"><p class="dep-lede">' + T("account.mine.dependents.selector_lede", null, "Elegí quién va a recibir la atención.") +
          '</p><div class="dep-list">'+choices+'</div>'+(showForm?formHtml():'<button type="button" class="dep-add">' +
          T("account.mine.dependents.add_button", null, "Agregar persona a cargo") + '</button>')+'</section>';
        host.querySelectorAll(".dep-choice").forEach(function(b){b.addEventListener("click",function(){opts.onSelect([u].concat(items)[+b.dataset.i]);});});
        if(!showForm)$(".dep-add",host).addEventListener("click",function(e){e.preventDefault();paint(true);});
        if(showForm)wireForm(host,function(p){items.push(p);opts.onSelect(p);},function(){paint(false);});
      } paint(false);
    });
  }
  export { renderCuenta, renderSelector, renderAgregar, renderElegir,
    plan, aptosPorEdad, esPediatrico };
