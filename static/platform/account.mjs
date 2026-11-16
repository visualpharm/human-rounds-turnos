const mounts = new WeakMap();
export function mount(host, options) {
  "use strict";
  options = options || {};
  if (!host) return function () {};
  if (mounts.has(host)) mounts.get(host)();
  var disposed = false;
  var generation = 0;
  var hashHandler = null;
  var timers = new Set();
  var requestsDispose = null;
  var labsDispose = null;
  function alive() { return !disposed && host.isConnected !== false; }
  function current(view) { return alive() && generation === view; }
  function resetView() {
    generation++;
    if (requestsDispose) { requestsDispose(); requestsDispose = null; }
    if (labsDispose) { labsDispose(); labsDispose = null; }
    if (hashHandler) { window.removeEventListener("hashchange", hashHandler); hashHandler = null; }
    timers.forEach(clearTimeout); timers.clear();
    return generation;
  }
  function later(view, callback, delay) {
    var timer = setTimeout(function () { timers.delete(timer); if (current(view)) callback(); }, delay);
    timers.add(timer);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    resetView();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }
  mounts.set(host, dispose);
  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("[static] " + name + " is not in window.SANA_STATIC"));
  }
  var lede = document.getElementById("cuenta-lede");
  var title = document.getElementById("cuenta-title");
  // resolver del catálogo i18n.js — el mismo de nav.js/listing.js: la clave
  // del catálogo si existe, si no el literal castellano que reemplazó (así
  // esta página nunca renderiza peor que antes de traducirse).
  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.reject(new Error("[static] platform/i18n.mjs is not in window.SANA_STATIC"));
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  function showAccountLoadError() {
    if (!alive()) return;
    if (options.onError) { options.onError(); return; }
    if (host) host.innerHTML = '<p class="account-load-error" role="alert">' +
      T("account.error.formulario", null, "No se pudo cargar el formulario. Recargá la página.") + "</p>";
  }
  var accountUrl = window.SANA_STATIC && window.SANA_STATIC["platform/account-form.mjs"];
  var accountReady = accountUrl
    ? import(accountUrl)
    : Promise.reject(new Error("[static] platform/account-form.mjs is not in window.SANA_STATIC"));
  // Personas a cargo: módulo canónico platform/dependents.mjs. Arranca en
  // paralelo con Account y se resuelve a null si no carga — el panel decide
  // qué mostrar cuando su host ya existe, nunca antes.
  var dependentsUrl = window.SANA_STATIC && window.SANA_STATIC["platform/dependents.mjs"];
  var dependentsReady = dependentsUrl
    ? import(dependentsUrl).catch(function () { return null; })
    : Promise.resolve(null);
  dispose.ready = Promise.all([i18nReady, accountReady]).then(function (results) {
  if (!alive()) return;
  var Account = results[1];
  function loadIdent(){
    try{ return JSON.parse(localStorage.getItem("st_identity")||"null")||{}; }catch(e){ return {}; }
  }
  function mirror(u){
    try{
      // Replace the identity projection, including server-cleared fields.
      var cur = {};
      ["dni", "identity_kind", "nombre", "apellido", "nacimiento", "sexo", "contacto", "email"].forEach(function (key) {
        if (Object.prototype.hasOwnProperty.call(u, key)) cur[key] = u[key];
      });
      localStorage.setItem("st_identity",JSON.stringify(cur));
    }catch(e){}
  }
  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}
  function initials(u){var a=(u.nombre||"").trim(),b=(u.apellido||"").trim();return (((a[0]||"")+(b[0]||"")).toUpperCase())||"?";}
  // Mismo gate que account-form.mjs (responsable del producto, 2026-09-11): al visitante cuyo
  // idioma activo no es el castellano nunca se le pide obra social/prepaga, y
  // la clave sintética (alta sin DNI) no trae tarjeta de cobertura — el ítem
  // del riel desaparece junto con la tarjeta que muestra.
  function sinDniVisitante(){
    var lang = I18n && typeof I18n.activeLang === "function" ? I18n.activeLang() : "es";
    return lang.split("-")[0] !== "es";
  }
  // Íconos del riel: glyphs rellenos del pack iOS Glyph (ios11) de Icons8,
  // una sola familia visual para toda la navegación. Source re-pullable desde
  // static/icons/account-rail/.manifest; fill="currentColor" deja el tono al
  // CSS (.rail-btn / .rail-btn.on), igual que los outline que reemplazaron.
  var IC={
    datos:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M 15 3 C 11.686 3 9 5.686 9 9 L 9 10 C 9 13.314 11.686 16 15 16 C 18.314 16 21 13.314 21 10 L 21 9 C 21 5.686 18.314 3 15 3 z M 14.998047 19 C 10.992047 19 5.8520469 21.166844 4.3730469 23.089844 C 3.4590469 24.278844 4.329125 26 5.828125 26 L 24.169922 26 C 25.668922 26 26.539 24.278844 25.625 23.089844 C 24.146 21.167844 19.004047 19 14.998047 19 z"/></svg>',
    turnos:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M24,4H6C4.895,4,4,4.895,4,6v18c0,1.105,0.895,2,2,2h18c1.105,0,2-0.895,2-2V6C26,4.895,25.105,4,24,4z M7,23 c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1C8,22.552,7.552,23,7,23z M7,19c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1 s1,0.448,1,1C8,18.552,7.552,19,7,19z M11,23c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1C12,22.552,11.552,23,11,23z M11,19c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1C12,18.552,11.552,19,11,19z M11,15c-0.552,0-1-0.448-1-1 c0-0.552,0.448-1,1-1s1,0.448,1,1C12,14.552,11.552,15,11,15z M15,23c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1 C16,22.552,15.552,23,15,23z M15,19c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1C16,18.552,15.552,19,15,19z M15,15 c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1C16,14.552,15.552,15,15,15z M19,23c-0.552,0-1-0.448-1-1 c0-0.552,0.448-1,1-1s1,0.448,1,1C20,22.552,19.552,23,19,23z M19,19c-0.552,0-1-0.448-1-1s0.448-1,1-1s1,0.448,1,1S19.552,19,19,19 z M18,14c0-0.552,0.448-1,1-1s1,0.448,1,1c0,0.552-0.448,1-1,1S18,14.552,18,14z M23,19c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1 s1,0.448,1,1C24,18.552,23.552,19,23,19z M23,15c-0.552,0-1-0.448-1-1c0-0.552,0.448-1,1-1s1,0.448,1,1C24,14.552,23.552,15,23,15z M24,10H6V6h18V10z"/></svg>',
    deps:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M 9.5 3 A 3.5 3.5 0 0 0 6 6.5 A 3.5 3.5 0 0 0 9.5 10 A 3.5 3.5 0 0 0 13 6.5 A 3.5 3.5 0 0 0 9.5 3 z M 20.5 3 A 3.5 3.5 0 0 0 17 6.5 A 3.5 3.5 0 0 0 20.5 10 A 3.5 3.5 0 0 0 24 6.5 A 3.5 3.5 0 0 0 20.5 3 z M 24 9 C 24 9 23.946781 10.846172 25.050781 11.951172 C 26.155781 13.055172 28 13 28 13 C 28 13 28.053219 11.153828 26.949219 10.048828 C 25.844219 8.9448281 24 9 24 9 z M 9 12 C 6.239 12 4 14.239 4 17 L 4 24 C 4 25.105 4.895 26 6 26 L 10 26 C 10 23.791 11.791 22 14 22 L 16 22 C 18.209 22 20 23.791 20 26 L 24 26 C 25.105 26 26 25.105 26 24 L 26 17 C 26 14.239 23.761 12 21 12 L 9 12 z M 15 14 C 16.657 14 18 15.343 18 17 C 18 18.657 16.657 20 15 20 C 13.343 20 12 18.657 12 17 C 12 15.343 13.343 14 15 14 z"/></svg>',
    cobertura:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M 15 3 C 10.398 3 5.6113281 4.0371094 5.6113281 4.0371094 L 5.6074219 4.0410156 A 2 2 0 0 0 4 6 L 4 12 C 4 22.398 14.021484 26.744141 14.021484 26.744141 A 2 2 0 0 0 15 27 A 2 2 0 0 0 15.974609 26.744141 L 15.978516 26.744141 C 15.979516 26.744141 26 22.398 26 12 L 26 6 A 2 2 0 0 0 24.388672 4.0371094 C 24.388672 4.0371094 19.602 3 15 3 z M 21 9 C 21.25575 9 21.511531 9.0974688 21.707031 9.2929688 C 22.098031 9.6839688 22.098031 10.316031 21.707031 10.707031 L 14.146484 18.267578 C 13.958484 18.455578 13.704453 18.560547 13.439453 18.560547 C 13.174453 18.560547 12.919422 18.455578 12.732422 18.267578 L 9.2792969 14.814453 C 8.8882969 14.423453 8.8882969 13.791391 9.2792969 13.400391 C 9.6702969 13.009391 10.302359 13.009391 10.693359 13.400391 L 13.439453 16.146484 L 20.292969 9.2929688 C 20.488469 9.0974688 20.74425 9 21 9 z"/></svg>',
    contacto:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M 15 3 C 13.895 3 13 3.895 13 5 L 13 5.2617188 C 9.5517488 6.1512979 7 9.2740449 7 13 L 7 15.394531 C 7 16.984531 6.6734531 18.547 6.0644531 20 L 23.935547 20 C 23.326547 18.548 23 16.984531 23 15.394531 L 23 13 C 23 9.2740449 20.448251 6.1512979 17 5.2617188 L 17 5 C 17 3.895 16.105 3 15 3 z M 5 22 A 1.0001 1.0001 0 1 0 5 24 L 12.175781 24 A 3 3 0 0 0 12 25 A 3 3 0 0 0 15 28 A 3 3 0 0 0 18 25 A 3 3 0 0 0 17.826172 24 L 25 24 A 1.0001 1.0001 0 1 0 25 22 L 15 22 L 5 22 z"/></svg>',
    labs:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M 15 1 A 1 1 0 0 0 14.195312 1.4082031 A 1 1 0 0 0 14.166016 1.4472656 C 11.804895 4.4855713 6 14.023464 6 19 C 6 23.971 10.029 28 15 28 C 19.971 28 24 23.971 24 19 C 24 14.029064 18.208002 4.5104371 15.841797 1.4609375 C 15.841528 1.4605907 15.842066 1.459331 15.841797 1.4589844 A 1 1 0 0 0 15.804688 1.4082031 A 1 1 0 0 0 15 1 z M 18.5 21 C 19.328 21 20 21.672 20 22.5 C 20 23.328 19.328 24 18.5 24 C 17.672 24 17 23.328 17 22.5 C 17 21.672 17.672 21 18.5 21 z"/></svg>',
    mcp:'<svg width="18" height="18" viewBox="0 0 30 30" preserveAspectRatio="xMidYMid meet" fill="currentColor" aria-hidden="true"><path d="M 9 1 A 1 1 0 0 0 8 2 A 1 1 0 0 0 9 3 A 1 1 0 0 0 10 2 A 1 1 0 0 0 9 1 z M 13 1 A 1 1 0 0 0 12 2 A 1 1 0 0 0 13 3 A 1 1 0 0 0 14 2 A 1 1 0 0 0 13 1 z M 17 1 A 1 1 0 0 0 16 2 A 1 1 0 0 0 17 3 A 1 1 0 0 0 18 2 A 1 1 0 0 0 17 1 z M 21 1 A 1 1 0 0 0 20 2 A 1 1 0 0 0 21 3 A 1 1 0 0 0 22 2 A 1 1 0 0 0 21 1 z M 7 5 C 5.895 5 5 5.895 5 7 L 5 23 C 5 24.105 5.895 25 7 25 L 23 25 C 24.105 25 25 24.105 25 23 L 25 7 C 25 5.895 24.105 5 23 5 L 7 5 z M 2 8 A 1 1 0 0 0 1 9 A 1 1 0 0 0 2 10 A 1 1 0 0 0 3 9 A 1 1 0 0 0 2 8 z M 9.5 8 C 10.328 8 11 8.672 11 9.5 C 11 10.328 10.328 11 9.5 11 C 8.672 11 8 10.328 8 9.5 C 8 8.672 8.672 8 9.5 8 z M 28 8 A 1 1 0 0 0 27 9 A 1 1 0 0 0 28 10 A 1 1 0 0 0 29 9 A 1 1 0 0 0 28 8 z M 2 12 A 1 1 0 0 0 1 13 A 1 1 0 0 0 2 14 A 1 1 0 0 0 3 13 A 1 1 0 0 0 2 12 z M 28 12 A 1 1 0 0 0 27 13 A 1 1 0 0 0 28 14 A 1 1 0 0 0 29 13 A 1 1 0 0 0 28 12 z M 2 16 A 1 1 0 0 0 1 17 A 1 1 0 0 0 2 18 A 1 1 0 0 0 3 17 A 1 1 0 0 0 2 16 z M 28 16 A 1 1 0 0 0 27 17 A 1 1 0 0 0 28 18 A 1 1 0 0 0 29 17 A 1 1 0 0 0 28 16 z M 20.5 19 C 21.328 19 22 19.672 22 20.5 C 22 21.328 21.328 22 20.5 22 C 19.672 22 19 21.328 19 20.5 C 19 19.672 19.672 19 20.5 19 z M 2 20 A 1 1 0 0 0 1 21 A 1 1 0 0 0 2 22 A 1 1 0 0 0 3 21 A 1 1 0 0 0 2 20 z M 28 20 A 1 1 0 0 0 27 21 A 1 1 0 0 0 28 22 A 1 1 0 0 0 29 21 A 1 1 0 0 0 28 20 z M 9 27 A 1 1 0 0 0 8 28 A 1 1 0 0 0 9 29 A 1 1 0 0 0 10 28 A 1 1 0 0 0 9 27 z M 13 27 A 1 1 0 0 0 12 28 A 1 1 0 0 0 13 29 A 1 1 0 0 0 14 28 A 1 1 0 0 0 13 27 z M 17 27 A 1 1 0 0 0 16 28 A 1 1 0 0 0 17 29 A 1 1 0 0 0 18 28 A 1 1 0 0 0 17 27 z M 21 27 A 1 1 0 0 0 20 28 A 1 1 0 0 0 21 29 A 1 1 0 0 0 22 28 A 1 1 0 0 0 21 27 z"/></svg>'
  };
  function portalSession(){
    var b=window.__ACCOUNT_BOOTSTRAP__;
    return !!(b&&b.acceso_portal);
  }
  // The municipal gateway closes /mcp: the server sets `mcp_cerrado` and issues no token there.
  function mcpClosed(){
    var b=window.__ACCOUNT_BOOTSTRAP__;
    return !!(b&&b.mcp_cerrado);
  }
  function navBtn(tab,label,extra){
    return '<button type="button" class="rail-btn" data-tab="'+tab+'">'+IC[tab]+"<span>"+label+"</span>"+(extra||"")+"</button>";
  }
  function renderCard(u, turnos){
    if(!alive()) return;
    var view = resetView();
    mirror(u);
    // "Mis turnos" es el PRIMER ítem y el panel por defecto, haya o no turnos
    // (sin turnos explica cómo sacar el primero; antes se ocultaba, 2026-10-28).
    // Pedimos el conteo ANTES de armar el riel para que
    // el badge salga bien de una, sin parpadeo — EXCEPTO cuando
    // el deep-link ya pide otra pestaña (#labs/#datos/…): ahí el shell pinta
    // YA y el conteo de turnos llega en paralelo (2026-08-10: #labs se sentía
    // lento porque esperaba /api/requests sin necesitarlo).
    if(Array.isArray(turnos)){ buildDash(u, turnos, view); return; }
    var hashTab=(location.hash||"").replace("#","").split("&")[0];
    if(["labs","datos","cobertura","contacto","deps","mcp"].indexOf(hashTab)>=0){
      buildDash(u, null, view);
      return;
    }
    fetch("/api/requests",{credentials:"same-origin"})
      .then(function(r){ if(!r.ok) throw new Error("Requests lookup failed"); return r.json(); })
      .then(function(j){ if(current(view)) buildDash(u, Array.isArray(j)?j:(j.solicitudes||j.items||[]), view); })
      .catch(function(){ if(current(view)) buildDash(u, undefined, view); });
  }
  // El panel "Personas a cargo" se pinta cuando el módulo canónico llega: un
  // buildDash posterior (re-login desde esta misma página) monta en el host
  // VIGENTE — un import tardío nunca pinta sobre un panel ya reemplazado —
  // y un módulo que no cargó deja una línea de recuperación localizada en
  // el propio panel, no un panel vacío.
  function mountDependents(el){
    if(!el) return;
    var view = generation;
    dependentsReady.then(function(Dep){
      if(!current(view) || el.isConnected === false) return;
      if(!Dep){
        el.innerHTML='<p class="account-load-error" role="alert">'+
          T("account.mine.dependents.load_error",null,"No se pudo cargar esta sección. Recargá la página.")+"</p>";
        return;
      }
      Dep.renderCuenta(el);
    });
  }
  // The «Mis turnos» badge counts what is still ahead: today's and future
  // appointments and open requests. Cancelled, rejected and past ones stay in
  // «Anteriores» but never inflate the number (review 2026-11-15, item 10).
  function liveCount(list){
    if(!Array.isArray(list)) return 0;
    return list.filter(function(it){
      var e=String((it&&it.estado)||"").toLowerCase();
      if(e==="cancelada"||e==="cancelado"||e==="rechazada"||e==="rechazado") return false;
      return !(it&&it.turno_pasado===true);
    }).length;
  }
  function buildDash(u, turnos, view){
    if(!current(view)) return;
    // turnos===null → conteo todavía en vuelo (deep-link); el riel se completa
    // cuando llega /api/requests. Array (aunque vacío) = dato definitivo.
    var turnosAsync = (turnos === null);
    var requestsUnavailable = (turnos === undefined);
    var turnosLive = Array.isArray(turnos) ? turnos : undefined;
    var hasTurnos = liveCount(turnosLive) > 0;
    // Deep link por hash (#turnos/#datos/#cobertura/#contacto/#labs/#deps/#mcp):
    // cada pestaña es su propia URL compartible; /my-requests redirige acá
    // con #turnos (design-loop 2026-07-19).
    var sinCobertura = sinDniVisitante() || (u && u.identity_kind === "synthetic");
    var tabs=["turnos","datos","cobertura","contacto","labs","deps","mcp"].filter(function(t){return !(sinCobertura && t==="cobertura") && !(mcpClosed() && t==="mcp");});
    var hashTab = (location.hash || "").replace("#", "").split("&")[0];
    // «Mis turnos» es siempre la primera pestaña y la de entrada: una persona
    // sin solicitudes ve ahí cómo sacar su primer turno, no «Datos personales»
    // (repaso de escenarios, 2026-10-28).
    var defTab = tabs.indexOf(hashTab) >= 0 ? hashTab : "turnos";
    var nom=((u.nombre||"")+" "+(u.apellido||"")).trim()||T("account.dash.nombre_generico",null,"Mi cuenta");
    var turnosBtn = navBtn("turnos",T("account.dash.nav_turnos",null,"Mis turnos"),hasTurnos ? '<span class="rail-badge">'+liveCount(turnosLive)+'</span>' : '');
    // El dashboard NO lleva el encabezado "Mi cuenta" de la página (responsable del producto,
    // 2026-07-21: "the whole section doesn't have its own bulky title — only
    // the section names, to the right from the menu"): cada panel se titula
    // solo en la columna de contenido. El encabezado vuelve en la vista de
    // registro/login (mountAuth).
    var head=document.getElementById("cuenta-head");
    if(head) head.style.display="none";
    host.innerHTML =
      '<div class="dash">'+
        '<aside class="rail">'+
          '<div class="rail-id">'+
            '<div class="rail-av" id="rail-av" tabindex="0">'+
              '<span class="rail-av-init" id="rail-av-init">'+esc(initials(u))+'</span>'+
              '<img class="rail-av-img" id="rail-av-img" alt="" width="70" height="70" hidden>'+
              '<div class="rail-av-ov">'+
                '<a id="rail-av-cambiar">'+T("account.dash.foto_subir",null,"Subir foto")+'</a>'+
                '<a id="rail-av-quitar" hidden>'+T("account.dash.foto_quitar",null,"Quitar")+'</a>'+
              '</div>'+
              '<input type="file" accept="image/*" id="rail-av-file" hidden>'+
            '</div>'+
            '<div class="rail-name">'+esc(nom)+'</div>'+
          '</div>'+
          '<nav class="rail-nav" aria-label="'+esc(T("account.dash.nav_aria",null,"Secciones de la cuenta"))+'">'+
            turnosBtn+
            navBtn("datos",T("account.dash.nav_datos",null,"Datos personales"))+
            (sinCobertura?"":navBtn("cobertura",T("account.dash.nav_cobertura",null,"Cobertura")))+
            navBtn("contacto",T("account.dash.nav_contacto",null,"Contacto y avisos"))+
            navBtn("labs",T("account.dash.nav_labs",null,"Resultados de laboratorio"))+
            navBtn("deps",T("account.dash.nav_deps",null,"Personas a cargo"))+
            (mcpClosed()?"":navBtn("mcp",T("account.dash.nav_mcp",null,"Tu asistente de IA")))+
          '</nav>'+
          // A portal session is the portal's: no «Cerrar sesión» that cannot end it.
          (portalSession()?"":'<div class="rail-foot"><button class="rail-out" id="acc-out" type="button">'+T("account.dash.cerrar_sesion",null,"Cerrar sesión")+'</button></div>')+
        '</aside>'+
        '<div class="dash-col">'+
          // Mis turnos SIN tarjeta envolvente (design-loop mockup 2026-07-19):
          // my-requests.js dibuja su propia fila de título ("Mis turnos"
          // + acciones) y las tarjetas directamente sobre el fondo gris.
          '<div class="dpanel" id="panel-turnos"><div id="ms-host" data-embedded="1"></div></div>'+
          '<div class="dpanel" id="panel-datos"><div id="acc-editar"></div></div>'+
          '<div class="dpanel" id="panel-labs"><section class="dcard">'+
            '<h2 class="dcard-h">'+T("account.dash.nav_labs",null,"Resultados de laboratorio")+'</h2>'+
            '<div id="lr-host"></div><div id="lr-note"></div></section></div>'+
          '<div class="dpanel" id="panel-deps"><section class="dcard" id="acc-dependientes"></section></div>'+
          (mcpClosed()?'':'<div class="dpanel" id="panel-mcp"><section class="dcard">'+
            '<h2 class="dcard-h">'+T("account.dash.nav_mcp",null,"Tu asistente de IA")+'</h2>'+
            '<p class="dcard-p acc-mcp-lede">'+T("account.dash.mcp_lede",null,"Si usás un asistente de IA compatible con MCP (Claude, ChatGPT u otro), podés conectarlo para que busque servicios y pida turnos por vos.")+'</p>'+
            '<div id="acc-mcp"></div></section></div>')+
        '</div>'+
      '</div>';
    Account.renderEditar(document.getElementById("acc-editar"),{
      usuario:u,
      onSave:function(nu){ if(current(view)) mirror(nu); },
      // "Guardar cambios" está embebido en el riel de /account: al confirmar
      // pasa a la pestaña Mis turnos EN EL MISMO dashboard — antes navegaba a
      // /my-requests standalone y la persona perdía el riel entero (reportado
      // por el responsable del producto 2026-07-17, ss: pantalla sin menú tras guardar).
      afterGuardar:function(){ show("turnos"); }
    });
    mountDependents(document.getElementById("acc-dependientes"));
    if(!mcpClosed()) renderMcp(document.getElementById("acc-mcp"));
    wireRailAvatar();

    // menú del riel: cambia el panel visible; "Mis turnos" carga su módulo la
    // primera vez que se abre (my-requests.js se monta solo en #ms-host).
    // Cobertura/Contacto y avisos son ítems propios del riel, pero viven en
    // el MISMO <form> que Datos personales (autosave compartido) — muestran
    // el panel "datos" y adentro alternan cuál de las 3 sc-card está "on"
    // (data-panel, ver account-form.mjs).
    var loadedTurnos=false, loadedLabs=false;
    // replaceState no dispara hashchange: el menú del teléfono (nav.mjs) se
    // entera por este evento de qué pestaña quedó activa y si hay «Mis turnos»
    // (responsable del producto, 2026-10-17: en /account el menú marcaba «Datos personales» y no
    // listaba «Mis turnos» aunque el riel los mostraba).
    function publishNavTab(tab){
      try{ dispatchEvent(new CustomEvent("stz:account-tab",{detail:{tab:tab,turnos:true}})); }catch(e){}
    }
    function show(tab, preserveDetailHash){
      if(!current(view)) return;
      // Re-query: el botón "Mis turnos" puede inyectarse después (deep-link
      // async) y tiene que entrar al toggle .on igual que los demás.
      var btns=host.querySelectorAll(".rail-btn");
      for(var i=0;i<btns.length;i++){
        var isOn=btns[i].getAttribute("data-tab")===tab;
        btns[i].classList.toggle("on",isOn);
        // On a phone the rail is a horizontal strip: keep the open section in view.
        if(isOn && typeof btns[i].scrollIntoView==="function") try{ btns[i].scrollIntoView({block:"nearest",inline:"nearest"}); }catch(e){}
      }
      var grupo=(tab==="cobertura"||tab==="contacto")?"datos":tab;
      ["datos","turnos","labs","deps","mcp"].forEach(function(t){var p=document.getElementById("panel-"+t);if(p)p.classList.toggle("on",t===grupo);});
      var cards=host.querySelectorAll("#acc-editar .sc-card[data-panel]");
      for(var i=0;i<cards.length;i++) cards[i].classList.toggle("on",cards[i].getAttribute("data-panel")===tab);
      try{ if(!preserveDetailHash || !(tab==="labs" && /^#labs&r=\d+$/.test(location.hash||""))) history.replaceState(null,"","#"+tab); }catch(e){}
      publishNavTab(tab);
      if(tab==="turnos"&&!loadedTurnos){
        loadedTurnos=true;
        var requestsHost = document.getElementById("ms-host");
        importStatic("platform/account-requests.mjs").then(function(Requests){
          if(!current(view) || requestsHost.isConnected === false) return;
          requestsDispose=Requests.mount(requestsHost,{
            requests:turnosLive,
            onUnauthorized:function(){ if(current(view)) mountAuth("login"); }
          });
        }).catch(function(){ if(current(view)){ loadedTurnos=false; if(requestsHost) requestsHost.innerHTML='<p class="account-load-error" role="alert">'+T("account.mine.requests.load_error",null,"No pudimos cargar tus solicitudes. Recargá la página.")+"</p>"; } });
      }
      if(tab==="labs"&&!loadedLabs){
        loadedLabs=true;
        var labsHost = document.getElementById("lr-host");
        importStatic("platform/account-labs.mjs").then(function(Labs){
          if(!current(view) || labsHost.isConnected === false) return;
          labsDispose=Labs.mount(labsHost,{
            onUnauthorized:function(){ if(current(view)) mountAuth("login"); }
          });
        }).catch(function(){ if(current(view)){ loadedLabs=false; if(labsHost) labsHost.innerHTML='<p class="account-load-error" role="alert">'+T("account.mine.lab.load_error",null,"No pudimos mostrar tus resultados ahora. Volvé a intentar en un rato.")+"</p>"; } });
      }
    }
    var btnsInit=host.querySelectorAll(".rail-btn");
    for(var i=0;i<btnsInit.length;i++)(function(b){b.addEventListener("click",function(){show(b.getAttribute("data-tab"));});})(btnsInit[i]);
    if(turnosAsync){
      function publishRequestsNav(list){
          if(!current(view)) return;
          turnosLive=list;
          // El botón ya está; sólo le falta el contador cuando llegan solicitudes.
          var btn=host.querySelector('.rail-nav [data-tab="turnos"]');
          if(!btn || !liveCount(list) || btn.querySelector(".rail-badge")) return;
          var badge=document.createElement("span");
          badge.className="rail-badge";
          badge.textContent=String(liveCount(list));
          btn.appendChild(badge);
      }
      fetch("/api/requests",{credentials:"same-origin"})
        .then(function(r){ if(!r.ok) throw new Error("Requests lookup failed"); return r.json(); })
        .then(function(j){ publishRequestsNav(Array.isArray(j)?j:(j.solicitudes||j.items||[])); })
        .catch(function(){ publishRequestsNav(undefined); });
    }
    show(defTab, true);   // panel por defecto: preserve a direct report hash until Labs mounts
    hashHandler=function(){
      if(!current(view)) return;
      // lab-results.js extiende el hash de #labs con un parámetro de informe
      // (#labs&r=<idx>, "ver todos los resultados") — acá se toma sólo la
      // parte antes del primero "&" para resolver la pestaña del riel; el
      // parámetro r lo lee y reacciona el propio lab-results.js.
      var tab=(location.hash||"#datos").replace("#","").split("&")[0];
      if(tabs.indexOf(tab)>=0) show(tab,tab==="labs" && /^#labs&r=\d+$/.test(location.hash||""));
    };
    window.addEventListener("hashchange",hashHandler);

    var out=document.getElementById("acc-out");
    if(out) out.addEventListener("click",function(){
      if(!current(view)) return;
      out.disabled=true;
      // el logout cierra la sesión, no borra lo que ya sabíamos del paciente:
      // el DNI (y el resto) se quedan en localStorage para no hacerlo tipear
      // de nuevo en este navegador (responsable del producto 2026-07-11).
      fetch("/api/account/logout",{method:"POST",credentials:"same-origin"})
        .then(function(r){ if(current(view)){ if(r.ok) mountAuth("login"); else out.disabled=false; } })
        .catch(function(){ if(current(view)) out.disabled=false; });
    });
  }
  // Foto de perfil: vive en el avatar del riel (responsable del producto, 2026-07-17) — hover
  // muestra "Subir foto"/"Cambiar" + "Quitar" sobre el círculo, se sube y se
  // ve reflejada ahí mismo al toque (antes era un campo aparte dentro de
  // "Datos personales"; un solo lugar para esto, no dos).
  function wireRailAvatar(){
    var view = generation;
    var av=document.getElementById("rail-av");
    var img=document.getElementById("rail-av-img"), init=document.getElementById("rail-av-init");
    var cambiar=document.getElementById("rail-av-cambiar"), quitar=document.getElementById("rail-av-quitar");
    var file=document.getElementById("rail-av-file");
    if(!av||!cambiar||!file||!Account) return;
    var labelPorDefecto=T("account.dash.foto_subir",null,"Subir foto");
    // Fuente del avatar: "upload" (subida propia) | "gravatar" (del email) |
    // "none". "Quitar" sólo aplica a una foto SUBIDA — el gravatar sale del
    // email, no se borra desde acá. "Cambiar" se ofrece igual sobre un gravatar
    // (subir una propia lo pisa).
    function pintar(source){
      if(!current(view)) return;
      var tiene=source==="upload"||source==="gravatar";
      if(img) img.hidden=!tiene;
      if(init) init.hidden=tiene;
      if(quitar) quitar.hidden=(source!=="upload");
      av.classList.toggle("has-photo",tiene);
      cambiar.textContent=tiene?T("account.dash.foto_cambiar",null,"Cambiar"):labelPorDefecto;
    }
    // Refresca el avatar del botón "Mi cuenta" del menú (otra superficie que
    // muestra la misma foto): sube la versión cacheada y repunta su <img>.
    function refrescarNav(){
      if (!current(view)) return;
      importStatic("platform/nav.mjs").then(function (nav) {
        if (current(view)) nav.refreshAvatar();
      }).catch(function () { /* The saved profile remains valid if optional navigation is unavailable. */ });
    }
    // Consulta la fuente y, si hay foto, la carga. bust=true fuerza saltar el
    // cache del navegador (tras subir/quitar/cambiar el email, la URL sería la
    // misma y serviría la vieja). Sin bust en la carga normal: la foto no
    // cambia entre visitas, la sirve de su cache (responsable del producto 2026-07-17: "que cachee").
    function sync(bust){
      if(!current(view)) return;
      fetch("/api/account/photo?probe=1",{credentials:"same-origin"})
      .then(function(r){ return r.ok?r.json():null; })
        .then(function(j){
          if(!current(view)) return;
          var source=(j&&j.source)||"none";
          if(source==="none"){ pintar("none"); return; }
          if(!img){ pintar(source); return; }
          img.onload=function(){ pintar(source); };
          img.onerror=function(){ pintar("none"); };
          img.src="/api/account/photo"+(bust?("?t="+Date.now()):"");
        })
        .catch(function(){ pintar("none"); });
    }
    function fallo(msg){
      if(!current(view)) return;
      cambiar.textContent=msg;
      later(view,function(){ sync(false); },2600);
    }
    pintar("none");
    sync(false);
    // Un solo camino de subida para elegir archivo, pegar (Cmd/Ctrl+V) y
    // arrastrar. fileToJpeg re-encoda por canvas: un HEIC/PNG gigante del
    // celular sale JPEG liviano. Si el navegador no puede decodificar el
    // formato (HEIC en Chrome/Firefox), la promesa RECHAZA — antes eso se
    // tragaba en silencio y parecía "no se guarda" (responsable del producto 2026-07-18). Una foto
    // HEIC sube entonces CRUDA y decide el servidor (core/photo_guard): con
    // lector la normaliza, sin lector contesta el remedio, que se muestra.
    function subir(f){
      if(!current(view) || !f) return;
      cambiar.textContent=T("account.dash.foto_subiendo",null,"Subiendo…");
      Account.fileToJpeg(f,600).catch(function(err){
        return Account.isHeicFile(f).then(function(heic){
          if(heic) return Account.fileAsDataUrl(f);
          throw err;
        });
      }).then(function(du){
        if(!current(view) || !du) return null;
        return fetch("/api/account/photo",{
          method:"POST",credentials:"same-origin",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({data_b64:du})
        });
      }).then(function(r){
        if(!current(view) || !r) return;
        if(r.ok){ sync(true); refrescarNav(); return; }
        var porDefecto=T("account.dash.foto_error_subir",null,"No se pudo subir");
        return Promise.resolve(r.json()).catch(function(){ return null; }).then(function(j){
          fallo((j&&typeof j.error==="string"&&j.error)||porDefecto);
        });
      }).catch(function(){ fallo(T("account.dash.foto_error_formato",null,"Formato no soportado")); });
    }
    cambiar.addEventListener("click",function(){ if(current(view)) file.click(); });
    file.addEventListener("change",function(){
      if(!current(view)) return;
      var f=file.files&&file.files[0];
      file.value="";
      subir(f);
    });
    // Pegar o arrastrar sobre el avatar (enfocable, tabindex=0): mismas reglas
    // que el control de foto del médico — un paste dirigido a un campo de
    // texto sigue su curso, y un drop de archivo nunca abre el archivo.
    av.addEventListener("paste",function(ev){
      if(!current(view)) return;
      var f=Account.photoFileFromEvent(ev);
      if(!f) return;
      ev.preventDefault();
      subir(f);
    });
    av.addEventListener("dragover",function(ev){
      if(Account.eventCarriesFiles(ev)) ev.preventDefault();
    });
    av.addEventListener("drop",function(ev){
      if(!current(view) || !ev || ev.defaultPrevented) return;
      var f=Account.photoFileFromEvent(ev);
      if(Account.eventCarriesFiles(ev)) ev.preventDefault();
      if(f) subir(f);
      else if(Account.eventCarriesFiles(ev)) fallo(T("account.dash.foto_error_formato",null,"Formato no soportado"));
    });
    // Tocar el avatar (fuera de sus links) abre el selector y deja el foco
    // ahí: un Cmd+V inmediato reemplaza la foto sin buscar el link chico.
    av.addEventListener("click",function(ev){
      if(!current(view)) return;
      var t=ev&&ev.target;
      if(t===cambiar||t===quitar||t===file) return;
      try{ av.focus(); }catch(e){}
      file.click();
    });
    if(quitar) quitar.addEventListener("click",function(){
      if(!current(view)) return;
      fetch("/api/account/photo",{method:"DELETE",credentials:"same-origin"})
        .then(function(){ sync(true); refrescarNav(); }).catch(function(){});
    });
    // Gravatar por email: en cuanto la persona escribe/corrige su email (se
    // autoguarda al salir del campo), el server ya puede resolver su gravatar.
    // Tras dar tiempo al autosave, re-consultamos el avatar — así "aparece solo"
    // sin que suba nada (responsable del producto 2026-07-18). El delay cubre el POST del autosave.
    var emailEl=document.getElementById("sc-email");
    if(emailEl) emailEl.addEventListener("blur",function(){
      later(view,function(){ sync(true); refrescarNav(); },900);
    });
  }
  // Conector MCP: el asistente de IA del paciente (Claude, ChatGPT u otro)
  // puede buscar servicios y pedir turnos en su nombre. Los datos (URL +
  // token) se piden recién al click — no ensuciamos la cuenta de quien no usa esto.
  // `desconectado`: right after «Desconectar el asistente» the screen says so and offers an explicit
  // «Conectar de nuevo»; a new token is issued only when the person presses it (never on render).
  function renderMcp(el,desconectado){
    if(!el) return;
    var etiqueta=desconectado
      ? T("account.dash.mcp_reconectar",null,"Conectar un asistente de nuevo")
      : T("account.dash.mcp_ver",null,"Datos de conexión");
    el.innerHTML=
      (desconectado?'<p id="acc-mcp-desconectado" role="status" style="font-size:16px;color:var(--text);">'+T("account.dash.mcp_desconectado",null,"El asistente quedó desconectado.")+'</p>':'')+
      '<p style="font-size:16px;color:var(--text);">'+T("ai.notice.mcp",null,"El asistente conectado usa inteligencia artificial y recibe tu identidad y los datos de las herramientas que autorizás. Revisá sus propuestas antes de actuar.")+'</p>'+
      '<a class="stz-link" id="acc-mcp-ver" style="font-size:14px;cursor:pointer;">'+etiqueta+'</a>'+
      '<div id="acc-mcp-datos" hidden style="margin-top:10px;"></div>';
    var ver=el.querySelector("#acc-mcp-ver");
    ver.addEventListener("click",function(){ cargarMcp(el,false); });
  }
  function cargarMcp(el,regen){
    var view = generation;
    if(!current(view) || el.isConnected === false) return;
    var url=regen?"/api/account/mcp/regenerate":"/api/account/mcp";
    fetch(url,{method:regen?"POST":"GET",credentials:"same-origin"})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if(!current(view) || el.isConnected === false) return;
        if(!j||!j.ok) return;
        var d=el.querySelector("#acc-mcp-datos");
        var ver=el.querySelector("#acc-mcp-ver");
        if(ver) ver.style.display="none";
        d.hidden=false;
        var copiarLbl=T("account.dash.mcp_copiar",null,"Copiar");
        // The token in the clear arrives only right after it is created (first
        // visit or «Generar uno nuevo»); the database keeps its hash, so later
        // visits get the last 4 characters and nothing to copy (2026-11-06).
        var visible=!!j.token;
        var aviso=visible
          ? T("account.dash.mcp_aviso_una_vez",null,"Copialo ahora y no lo compartas: no se vuelve a mostrar.")
          : T("account.dash.mcp_aviso_oculto",null,"Por seguridad no se vuelve a mostrar.");
        d.innerHTML=
          '<div class="acc-mcp-row">'+T("account.dash.mcp_servidor",null,"Servidor:")+' <code id="acc-mcp-url"></code> <a class="stz-link" style="cursor:pointer;" data-copy="acc-mcp-url">'+copiarLbl+'</a></div>'+
          '<p class="acc-sec-s acc-mcp-note" id="acc-mcp-oauth" style="margin:8px 0 12px;">'+T("account.dash.mcp_oauth",null,"En ChatGPT pegá el servidor y elegí «OAuth».")+'</p>'+
          '<div class="acc-mcp-row">'+T("account.dash.mcp_token",null,"Token:")+' <code id="acc-mcp-token"></code>'+(visible?' <a class="stz-link" style="cursor:pointer;" data-copy="acc-mcp-token">'+copiarLbl+'</a>':'')+'</div>'+
          '<div class="acc-mcp-row" id="acc-mcp-uso"></div>'+
          '<div class="acc-mcp-row" id="acc-mcp-vence"></div>'+
          '<p class="acc-sec-s acc-mcp-note" style="margin:6px 0 0;">'+aviso+' <a class="stz-link" id="acc-mcp-regen" style="cursor:pointer;">'+T("account.dash.mcp_regenerar",null,"Generar uno nuevo")+'</a> '+T("account.dash.mcp_aviso_post",null,"(el anterior deja de servir).")+'</p>'+
          '<p class="acc-sec-s acc-mcp-note" style="margin:6px 0 0;"><a class="stz-link" id="acc-mcp-revocar" style="cursor:pointer;">'+T("account.dash.mcp_revocar",null,"Desconectar el asistente")+'</a> <span id="acc-mcp-revocar-error" role="alert"></span></p>';
        // Last use (2026-11-13): the token expires by itself after 30 days
        // without use, so the screen says when it was last used.
        // The instant is written in the installation's timezone by the server;
        // dates.mjs reads its day and hour from that text, never from the
        // browser's clock (a patient abroad would otherwise see another hour).
        var usoEl=d.querySelector("#acc-mcp-uso");
        usoEl.textContent=j.last_used ? "" : T("account.dash.mcp_sin_uso",null,"Todavía no se usó.");
        if(j.last_used){
          importStatic("platform/dates.mjs").then(function(Dates){
            var cuando=Dates.instanteServidor(j.last_used);
            if(!cuando || !usoEl.isConnected) return;
            usoEl.textContent=T("account.dash.mcp_ultimo_uso",{when:cuando},"Último uso: {when}");
          }).catch(function(){});
        }
        d.querySelector("#acc-mcp-vence").textContent=T("account.dash.mcp_vence",null,"Si pasan 30 días sin usarlo, se desconecta solo.");
        d.querySelector("#acc-mcp-revocar").addEventListener("click",function(){
          // A failed revoke must say so: the patient would believe the assistant is cut off.
          var fallo=function(){
            if(!current(view) || el.isConnected === false) return;
            var m=d.querySelector("#acc-mcp-revocar-error");
            if(m) m.textContent=T("account.dash.mcp_revocar_error",null,"No se pudo desconectar. Probá de nuevo.");
          };
          fetch("/api/account/mcp/revoke",{method:"POST",credentials:"same-origin"})
            .then(function(r){ return r.json(); })
            .then(function(rj){
              if(!rj || !rj.ok){ fallo(); return; }
              if(!current(view) || el.isConnected === false) return;
              renderMcp(el,true);
            }).catch(fallo);
        });
        d.querySelector("#acc-mcp-url").textContent=j.url;
        d.querySelector("#acc-mcp-token").textContent=visible?j.token:"••••"+(j.hint||"");
        d.querySelectorAll("[data-copy]").forEach(function(a){
          a.addEventListener("click",function(){
            if(!current(view)) return;
            var t=d.querySelector("#"+a.getAttribute("data-copy")).textContent;
            navigator.clipboard&&navigator.clipboard.writeText(t).then(function(){
              if(!current(view)) return;
              a.textContent=T("account.dash.mcp_copiado",null,"Copiado ✓");
              later(view,function(){ a.textContent=T("account.dash.mcp_copiar",null,"Copiar"); },1600);
            });
          });
        });
        d.querySelector("#acc-mcp-regen").addEventListener("click",function(){ cargarMcp(el,true); });
      }).catch(function(){});
  }
  function mountAuth(mode){
    if(!alive()) return;
    var view = resetView();
    var head=document.getElementById("cuenta-head");
    if(head) head.style.display="";
    // Portal installation: the server already wrote the heading, and the
    // portal entry (or the remedy) carries the only text.
    var portal=!!window.__ACCOUNT_PORTAL__;
    if(portal&&lede) lede.hidden=true;
    // The title names the form on screen, also after «Ingresar» / «Crear
    // una» / «¿Olvidaste tu contraseña?» inside it (anon-16, 10.11: the login
    // form sat under «Crear cuenta»). Other modes (phone code, portal) keep
    // the title of the form that led there.
    function authHead(m){
      if(portal||!current(view)) return;
      var head=m==="login"
        ?[T("account.login.title",null,"Ingresar"),T("account.login.lede",null,"Entrá con tu DNI y contraseña para ver y sacar turnos.")]
        :m==="recuperar"
        ?[T("account.form.recover.heading",null,"Olvidé mi contraseña"),T("account.form.recover.lede",null,"Te mandamos un link a tu email para elegir una contraseña nueva.")]
        :m==="registro"
        ?[T("account.register.title",null,"Crear cuenta"),T("account.register.lede",null,"Creá una cuenta para sacar turnos. Podés ver los centros y servicios sin registrarte.")]
        :null;
      if(!head) return;
      if(title) title.textContent=head[0];
      if(lede) lede.textContent=head[1];
    }
    authHead(mode==="login"?"login":"registro");
    if(!Account){ showAccountLoadError(); return; }
    Account.renderAuth(host,{defaultMode:mode||"registro",heading:false,prefill:loadIdent(),onMode:authHead,
      onDone:function(u){ if(!current(view)) return; if(siguiente){ window.location.href=siguiente; return; } renderCard(u);window.scrollTo({top:0,behavior:"smooth"}); }});
  }
  var qsParams=new URLSearchParams(location.search);
  var qModo=qsParams.get("mode")||qsParams.get("modo");
  if(qModo==="register") qModo="registro";
  // ?next=: quien nos mandó acá (p.ej. /e/<door_token> sin sesión, "Ingresá
  // a tu cuenta") quiere volver ahí después de loguearse — nunca un
  // open-redirect: sólo un path relativo que empiece con "/" y no con "//"
  // ni "/\" (protocol-relative, se resuelven a otro host) — la misma regla que
  // Account.safeNext y el link de recuperación.
  var siguiente=qsParams.get("next")||null;
  if(siguiente&&(siguiente.charAt(0)!=="/"||siguiente.charAt(1)==="/"||siguiente.indexOf("\\")!==-1||/[\u0000-\u001f\s]/.test(siguiente))) siguiente=null;
  // sin modo explícito en la URL: si ya conocemos un DNI de este navegador,
  // lo más probable es que ya tenga cuenta — arrancamos en login, no registro.
  var modo=qModo==="login"||qModo==="registro"?qModo:(loadIdent().dni?"login":"registro");
  var bootstrap=options.freshSession ? null : (window.__ACCOUNT_BOOTSTRAP__||null);
  if(bootstrap&&bootstrap.usuario){
    if(siguiente){ window.location.href=siguiente; return; }
    if(window.__ACCOUNT_CANONICAL_PATH__){
      try{ history.replaceState(null,"",window.__ACCOUNT_CANONICAL_PATH__+(location.hash||"#turnos")); }catch(e){}
    }
    renderCard(bootstrap.usuario,bootstrap.solicitudes||[]);
  }else{
    return fetch("/api/account/me",{credentials:"same-origin"})
      .then(function(r){
        if(r.status===401 || r.status===403) return null;
        if(!r.ok) throw new Error("Account session lookup failed");
        return r.json();
      })
      .then(function(j){
        if(!alive()) return;
        if(j&&j.usuario){ if(siguiente){ window.location.href=siguiente; return; } renderCard(j.usuario); }
        else mountAuth(modo);
      })
      .catch(function(){ if(alive()) showAccountLoadError(); });
  }
}).catch(function () {
  // An Account import can reject before the parallel i18n import settles.
  // Wait for the page's real catalog so an English recovery never falls back
  // to the Spanish source literal merely because this failure won the race.
  return i18nReady.catch(function () { return null; }).then(showAccountLoadError);
});
  dispose.ready.catch(function () {});
  return dispose;
}
