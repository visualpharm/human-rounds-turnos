/* nav.mjs — barra de navegación superior COMPARTIDA por todas las páginas públicas.
   Marca (logo + nombre) que enlaza a la home, y links de texto:
   Médicos, Estudios, Centros, Talleres, Mi cuenta. Es la app de turnos del
   Hospital de Pinamar y los CAPS (sin directorio de clínicas privadas ni login
   de WebTurno). "Mis solicitudes" es condicional: sólo aparece (antes de "Mi
   cuenta") una vez que el paciente tiene al menos una solicitud enviada,
   confirmada o no (responsable del producto, 2026-07-11 — antes esa página no tenía link de entrada).

   Native consumer cutover: classic nav.js is physically retired.
   Independent browser/Account verification is still required before release.
   Sucesor nativo del clásico `nav.js`: mismo render,
   mismo DOM, mismas clases — lo que cambia es el ciclo de vida. El módulo se
   auto-monta (cada página pública lo carga con su tag fingerprinteado
   `<script type="module">`, que ya es deferred) y `mount()` devuelve un
   controlador `{dispose, ready}`: TODO listener, observer y timer que la barra
   instala queda registrado en el controlador y se retira de verdad en
   `dispose()` — incluyendo el MutationObserver del teléfono de emergencia y
   los fetch de reconcile, cuyas respuestas tardías chequean la generación y
   no repintan una barra ya desmontada. `pagehide` desmonta (una página
   congelada en bfcache no arrastra listeners ni observers vivos) y
   `pageshow.persisted` reconstruye desde el cache y re-reconcilia. Ese par
   pagehide/pageshow es el latido del PROPIO controlador y sobrevive a un
   dispose interno justamente para poder restaurar; un `dispose()` externo lo
   corta también. No hay global `window.SiteNav`: quien necesite refrescar el
   avatar (Mi cuenta) importa este módulo por el manifiesto estático.

   STICKY: the header stays reachable while scrolling, and the chat keeps its
   own floating launcher in the lower-right corner. `layout()`, further down,
   measures the real header height and publishes `--stznav-h` so another
   sticky surface (such as the home search bar) can sit below it.

   En un ancho intermedio se conservan en la barra las acciones y Mi cuenta,
   mientras los links de contenido pasan al burger (responsable del producto, 2026-08-01: a 806px
   todavía entran ambos botones y la cuenta). Sólo en el ancho pequeño donde
   esos controles ya no entran se mueve TODO al desplegable. La MEDICIÓN del
   burger no cambia de principio, sigue siendo la misma lógica:
   (1) el burger se activaba con `@media(min-width:1100px)`, un umbral de
   VIEWPORT fijo y muy conservador: el contenido vive en un `.wrap` de 1000px,
   así que a 1024px de ventana (un portátil sin maximizar) los links entraban
   de sobra — 608px de menú en 952px de barra — y aun así el menú entero
   colapsaba dentro del burger. Ahora NO hay umbral fijo: `layout()` MIDE si
   los links entran y sólo colapsa cuando de verdad no entran.

   SIN PARPADEO (the product owner, 2026-07-16: "when I click the top menu, it blinks").
   Los links que dependen de la sesión —"Mis solicitudes", y el par
   "Ingresar"/"Registrarse" vs "Mi cuenta"— salían de dos fetch asíncronos
   (`/api/account/me`, `/api/requests`) que MUTABAN la barra después del
   primer paint: la nav se dibujaba con 5 links y ~15 ms más tarde aparecía el
   6º ("Mis solicitudes"), un salto de layout visible en CADA navegación. Ahora
   el estado de sesión se cachea en localStorage (`st_nav`) y el PRIMER render
   ya sale con la forma final (caso común: usuario que vuelve con sesión
   estable → cero mutación). Los fetch sólo RECONCILIAN: se esperan los dos
   juntos y, únicamente si el estado cambió desde la última visita, se redibuja
   la barra UNA vez (regla ui-rules: swap old→final en un solo paint, nunca un
   frame intermedio).

   "MI CUENTA" = AVATAR + TEXTO, SIN ANILLO NI MARCO (responsable del producto, 2026-07-19: revoca
   el "sólo avatar" del 2026-07-18 — el responsable del producto revisó el mockup canónico de Claude
   Design [TopNav.jsx, componente único compartido por todas las páginas] y
   señaló que la implementación real había quedado desalineada con ese
   diseño aprobado). El link es una fila simple: círculo de 40px (foto real,
   o iniciales sobre el mismo degradé verde que `.rail-av` del riel de Mi
   cuenta) + la palabra "Mi cuenta", sin fondo, sin padding de píldora, sin
   `outline`/`border` alrededor del círculo — ni en reposo ni al pasar el
   mouse. Único gesto interactivo: el texto se subraya en hover, y hay un
   `outline` de foco (`:focus-visible`, no decorativo, sólo visible
   navegando con teclado) por accesibilidad — eso no es el "anillo" que se
   quitó, es el foco estándar del teclado. */

// Menu labels come from the shared catalog (platform/i18n.mjs), resolved
// through the client manifest the shell publishes as window.SANA_STATIC —
// never a bare relative import, which would bypass the fingerprint. The third
// argument is the Spanish the call site replaced, so nav renders unchanged
// with no catalog present (pages without the manifest bootstrap, a rejected
// import, or Node). A missing entry resolves null WITHOUT rejecting: the bar
// mounting is never hostage to the catalog.
var I18n = null;
var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
  ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; },
      function () { return null; })
  : Promise.resolve(null);
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}

// Standing patient-safety hook: every visible standalone “107” becomes a
// tap-to-call link, including copy injected after first paint. Authors can
// write the natural sentence; this one shared behavior prevents a future
// page, announcement or translated string from publishing an inert number.
// Exported: the emergency-number contract is a page-level behavior, not a
// nav-bar detail, so a surface that manages its own DOM can call it directly.
// The number is the installation's own: the footer's «Urgencias» link
// (sitio.json / Settings `emergency_number`, demo 112, Pinamar 107); 107 only
// when the page carries no footer.
function emergencyNumber() {
  var a = typeof document !== "undefined" && document.querySelector
    ? document.querySelector(".furg a[href^='tel:']") : null;
  var n = a ? String(a.textContent || "").trim() : "";
  return /^\d{3,4}$/.test(n) ? n : "107";
}
export function linkEmergencyNumbers(root) {
  root = root || (typeof document !== "undefined" ? document.body : null);
  if (!root || !document.createTreeWalker) return;
  var num = emergencyNumber();
  var re = new RegExp("\\b" + num + "\\b");
  var skip = /^(A|BUTTON|SCRIPT|STYLE|TEXTAREA|INPUT|OPTION|CODE|PRE)$/;
  var walker = document.createTreeWalker(root, 4); // NodeFilter.SHOW_TEXT
  var nodes = [], node;
  while ((node = walker.nextNode())) {
    if (!skip.test((node.parentElement && node.parentElement.tagName) || "") && re.test(node.nodeValue || "")) nodes.push(node);
  }
  nodes.forEach(function (textNode) {
    var parts = String(textNode.nodeValue || "").split(new RegExp("(\\b" + num + "\\b)", "g"));
    var frag = document.createDocumentFragment();
    parts.forEach(function (part) {
      if (part === num) {
        var a = document.createElement("a");
        a.className = "stz-link emergency-phone-link";
        a.href = "tel:" + num;
        a.textContent = part;
        frag.appendChild(a);
      } else if (part) frag.appendChild(document.createTextNode(part));
    });
    textNode.parentNode.replaceChild(frag, textNode);
  });
}
// Links de CONTENIDO (texto). "Directorio" (la home) se sacó del menú —
// se llega por el logo (responsable del producto, 2026-07-17).
function navItems() {
  return [
  { key: "medicos", label: T("nav.doctors", null, "Médicos"), href: "/doctors" },
  { key: "estudios", label: T("nav.studies", null, "Estudios"), href: "/d/diagnostico" },
  // Talleres ya no es un ítem del menú: es una pestaña dentro de Centros
  // (the product owner, 2026-10-03: "workshops is a good candidate to hide"), así la barra
  // entra completa en desktop. /d/grupos sigue siendo la misma página.
  { key: "centros", label: T("nav.centers", null, "Centros"), href: "/centers" },
];
}
function accountItems() {
  return [
  { key: "datos", label: T("nav.account.details", null, "Datos personales") },
  { key: "cobertura", label: T("nav.account.coverage", null, "Cobertura") },
  { key: "contacto", label: T("nav.account.contact", null, "Contacto y avisos") },
  { key: "deps", label: T("nav.account.dependents", null, "Personas a cargo") },
  { key: "mcp", label: T("nav.account.mcp", null, "Asistente de IA (MCP)") },
];
}

// --- Estado de sesión cacheado ------------------------------------------
// Lo único que hace variar la lista de links es (a) si hay sesión y (b) si el
// paciente tiene solicitudes. Se cachea para que el primer paint ya sea el
// definitivo y no haya salto cuando resuelven los fetch.
var CACHE_KEY = "st_nav";
function readCache() {
  try {
    var s = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    if (s && typeof s === "object") {
      return { loggedIn: !!s.loggedIn, hasSolicitudes: !!s.hasSolicitudes,
               iaDown: !!s.iaDown, iniciales: s.iniciales || "",
               authMode: s.authMode === "renaper" || s.authMode === "portal" ? s.authMode : "password" };
    }
  } catch (e) {}
  return { loggedIn: false, hasSolicitudes: false, iaDown: false,
           iniciales: "", authMode: "password" };
}
function writeCache(s) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({
      loggedIn: !!s.loggedIn, hasSolicitudes: !!s.hasSolicitudes,
      iaDown: !!s.iaDown, iniciales: s.iniciales || "",
      authMode: s.authMode === "renaper" || s.authMode === "portal" ? s.authMode : "password",
    }));
  } catch (e) {}
}

// Iniciales para el avatar sin foto (mismo criterio que accounts.py:initials).
function initials(u) {
  var a = ((u && u.nombre) || "").trim(), b = ((u && u.apellido) || "").trim();
  return (((a[0] || "") + (b[0] || "")).toUpperCase()) || "";
}

// ¿Hay sesión? GET /api/account/me (200 = logueado; el body trae
// nombre/apellido para las iniciales del avatar sin foto).
function getOptions(signal) {
  var options = { credentials: "include" };
  if (signal) options.signal = signal;
  return options;
}
function fetchLoggedIn(signal) {
  return fetch("/api/account/me", getOptions(signal))
    .then(function (r) {
      if (!r.ok) return { loggedIn: false, iniciales: "", refused: r.status === 401 };
      return r.json().then(function (j) {
        return { loggedIn: true, iniciales: initials(j && j.usuario) };
      }).catch(function () { return { loggedIn: true, iniciales: "" }; });
    })
    .catch(function () { return { loggedIn: false, iniciales: "" }; });
}
// ¿La IA está caída? Si sí, sacamos "Subí tu orden" del menú: ese asistente
// sólo sirve para LEER la orden con IA (responsable del producto: "remové las opciones de IA
// cuando no anda"). Sacar turno subiendo la orden sigue andando aparte.
function fetchIaDown(signal) {
  return fetch("/api/ai-status", getOptions(signal))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) { return !!(j && j.disponible === false); })
    .catch(function () { return false; });
}

function fetchAuthMode(signal) {
  return fetch("/api/account/config", getOptions(signal))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) { return j && (j.auth_mode === "renaper" || j.auth_mode === "portal") ? j.auth_mode : "password"; })
    .catch(function () { return "password"; });
}

// Signed in on the municipal portal = signed in here, with no «Ingresar»
// (the product owner, m1008550). The portal's session cookie lasts 8 h and only the portal
// page renews it, so a person still signed in there can open a page of ours
// with no identity. A browser that was signed in here (cached `st_nav`) and now
// gets a 401 goes once through /account/portal-login: the portal renews the
// cookie and comes back to the same page. Same guard as /account's own trip in
// account-form.mjs: at most one trip per tab every two minutes, and none when
// storage is unavailable.
var PORTAL_TRIP_KEY = "hr_portal_trip";
export function portalTripDue(before, fresh, storage, now) {
  if (fresh.authMode !== "portal" || fresh.loggedIn || !fresh.refused) return false;
  if (!before.loggedIn || before.authMode !== "portal" || !storage) return false;
  try {
    var last = Number(storage.getItem(PORTAL_TRIP_KEY)) || 0;
    if (now - last < 120000) return false;
    storage.setItem(PORTAL_TRIP_KEY, String(now));
    return true;
  } catch (e) {
    return false;
  }
}
function sessionStore() {
  try { return window.sessionStorage; } catch (e) { return null; }
}

// Lista efectiva de items para un estado dado: a los links de contenido les
// agrega las acciones a la derecha. El chat has one entry point: its
// floating launcher, not a second button in this menu.
//   · logueado → "Mi cuenta" (link). Sin sesión → dos botones: Ingresar + Registrarse.
//   · responsable del producto 2026-10-29: siempre «Mi cuenta», con o sin turno por delante
//     (revierte q708829, que lo cambiaba a «Mis turnos»). La lista de turnos
//     vive dentro de la cuenta, en la pestaña «Mis turnos».
function effectiveItems(state) {
  var out = navItems().slice();
  // "Subí tu orden" vuelve al menú (responsable del producto, 2026-08-01 — revoca haberlo sacado
  // el mismo día a favor de "Chateá con IA"): son dos tareas distintas, y la
  // orden en papel es con lo que la persona llega. En desktop, los dos
  // botones en la barra. En ancho intermedio quedan junto a Mi cuenta y sólo
  // los links de contenido pasan al burger; en mobile pequeño entra todo al
  // burger. No hay copias: cada acción vive una sola vez y cambia de layout.
  // The menu and floating launcher share the same chat open action; there is
  // still only one chat instance.
  if (!state.iaDown) {
    out.push({ key: "orden", label: T("nav.upload_order", null, "Subí tu orden"), href: "/upload-order", btn: "ghost" });
    // Robot + "Chat" y nada más (responsable del producto, 2026-10-03); el nombre accesible
    // sigue siendo la frase completa.
    out.push({ key: "chat", label: T("nav.chat_short", null, "Chat"), full: T("nav.chat", null, "Chateá con IA"), accion: "abrir-chat", btn: "chat" });
  }
  if (state.loggedIn) {
    out.push({ key: "cuenta", href: "/account#turnos", iniciales: state.iniciales || "",
      label: T("nav.my_account", null, "Mi cuenta") });
  } else {
    // Sin sesión: links de texto planos, igual que el resto del menú
    // (TopNav.jsx canónico — nada de botones verdes acá).
    // On a portal installation «Ingresar» goes straight to the portal and
    // back to this page (/account/portal-login, product owner 26.10) — no page of ours
    // in between. Same href as account-form.mjs's portal button.
    out.push({ key: "ingresar", label: T("nav.sign_in", null, "Ingresar"),
      href: state.authMode === "portal" && typeof location !== "undefined"
        ? "/account/portal-login?next=" + encodeURIComponent(location.pathname + location.search)
        : "/account?mode=login" });
    // Patients from the municipal portal have no sign-up of ours: «Ingresar»
    // alone leads to the portal.
    if (state.authMode !== "portal") out.push({
      key: "registrarse",
      label: T("nav.register", null, "Registrarse"),
      href: state.authMode === "renaper"
        ? "/auth/sso?provider=renaper&next=%2Faccount"
        : "/account?mode=register"
    });
  }
  return out;
}

function injectStyles() {
  if (document.getElementById("stznav-styles")) return;
  // Estado por DEFECTO = menú desplegado en la barra, sin burger (responsable del producto
  // 2026-07-11: "on desktop it should be a normal menu like before"). El
  // burger sólo aparece si `layout()` mide que los links NO entran y marca
  // el host con .stznav--collapsed. Sin media queries: el umbral real no es
  // el ancho de la ventana sino si el menú entra al lado de la marca, y eso
  // depende de cuántos links haya (la sesión agrega "Mis solicitudes" /
  // "Ingresar"), así que hay que medirlo, no adivinarlo.
  var css =
    // La barra ES una banda blanca a todo el ancho con hairline inferior (el
    // header del mockup Claude Design — "the menu is white"). Cuando el header
    // vive como hijo directo de <body> (index/cuenta/listados) la banda llega
    // de borde a borde sin trucos de 100vw (cero overflow horizontal); el
    // contenido se re-centra con .stznav-inner al ancho de la página.
    // Tema con barra de color (pinamar, 2026-10-16: el hospital pidió la barra
    // #007342): tokens --nav-*; sin ellos (humanrounds) la barra sigue blanca.
    // Los desplegables redefinen los tokens para volver a tinta sobre papel.
    ".stznav{background:var(--nav-bg,var(--panel));border-bottom:1px solid var(--nav-bd,var(--border));}" +
    ".stznav--compact .stznav-content,.stznav--collapsed .stznav-menu{--nav-ink:var(--text);--nav-hover:var(--hover-bg);--nav-hover-ink:var(--hover-ink);--nav-on-ink:var(--text);--nav-on-bar:var(--accent);--nav-on-text:var(--text);--nav-icon-filter:none;--nav-btn-bg:var(--panel);--nav-btn-ink:var(--text);--nav-btn-bd:var(--border-strong);--nav-btn-hover:var(--hover-bg-soft);--nav-btn-hover-bd:var(--hover-bd-strong);--nav-btn-hover-ink:var(--hover-ink);--nav-btn-on:var(--accent);--nav-weight:initial;--nav-size:initial;}" +
    ".stznav-inner{position:relative;display:flex;align-items:center;justify-content:space-between;gap:24px;max-width:1120px;margin:0 auto;padding:14px 24px;}" +
    ".stznav-brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:inherit;flex-shrink:0;margin-left:-13px;}" +
    // Logotipo un paso más grande (responsable del producto, 2026-09-15: "make the logotype a
    // bit larger") — 52px de marca y 24px de nombre; layout() sigue
    // midiendo el ancho real, así el menú colapsa solo si deja de entrar.
    // Aún más grande (responsable del producto, 2026-09-16: "enlarge it even more") — 64px de
    // marca y 28px de nombre.
    ".stznav-mark{width:64px;height:64px;background:url(/logo.png) center/contain no-repeat;flex-shrink:0;filter:var(--nav-mark-filter,none);}" +
    ".stznav-name{font-size:28px;font-weight:700;letter-spacing:-.03em;color:var(--nav-name,var(--text));}" +
    // Nombre largo del sitio (tema Pinamar: «Turnos de salud pública en
    // Pinamar», 2026-10-27; antes el lema de prensa). Siempre en dos renglones
    // parejos al lado del escudo: un renglón solo ocupaba ~380px en desktop y
    // no entraba en el teléfono junto a «Subí tu orden», «Mis turnos» y el menú.
    // En el teléfono (≤420px) el nombre largo mide como mucho lo que medía el
    // logotipo anterior (~128px), así la barra colapsa en los mismos casos.
    ".stznav-name-long{display:block;max-width:10.5em;font-size:18px;line-height:1.15;font-weight:700;letter-spacing:-.01em;color:var(--nav-name,var(--text));text-wrap:balance;}" +
    // lado derecho: [link primario] [menú] [burger] — el primario y el burger
    // son hermanos del menú, así el primario sobrevive al colapso.
    ".stznav-right{display:flex;align-items:center;gap:2px;min-width:0;}" +
    ".stznav-menu,.stznav-content,.stznav-priority,.stznav-session-links{display:flex;flex-direction:row;align-items:center;gap:2px;}" +
    // Links del menú = píldoras livianas (TopNav.jsx canónico, 2026-07-21);
    // radio 999; el activo con fondo verde suave. MISMA caja de 44px y misma
    // letra de 15px que los botones y Mi cuenta (responsable del producto, 2026-09-19: "the
    // header should have buttons of the same height and the same font for
    // all menu items and buttons") — el peso se queda en 400: a 500 la fila
    // se ensancha, layout() la colapsa a burger en 1440 y pierde el menú.
    // Padding lateral 10px (no 12): con la marca de 52px, «Chat» y los links
    // de sesión, la barra en castellano quedaba 5px más ancha que 1280 — la
    // primera medición (fuente de respaldo) entraba y, al asentarse Manrope,
    // layout() la pasaba a compacto: un salto visible en cada carga.
    ".stznav-link{display:inline-flex;align-items:center;min-height:44px;font-size:var(--nav-size,15px);font-weight:var(--nav-weight,400);color:var(--nav-ink,var(--text));text-decoration:none;" +
      "padding:0 10px;border-radius:999px;white-space:nowrap;transition:background .12s,color .12s;}" +
    ".stznav-link:hover{background-color:var(--nav-hover,var(--hover-bg));color:var(--nav-hover-ink,var(--hover-ink));}" +
    // El peso NO cambia al activarse: un ítem que engorda mueve a sus vecinos.
    // Activo = una raya de 2px bajo la palabra, no una píldora (revisión del demo,
    // 2026-11-15: «the active item is a white pill»). Es un background-image, así
    // que ni el ancho ni la altura cambian y el hover (background-color) no la borra.
    ".stznav-link.on,.stznav-cuenta.on .stznav-cuenta-label{color:var(--nav-on-ink,var(--text));background-image:linear-gradient(var(--nav-on-bar,var(--accent)),var(--nav-on-bar,var(--accent)));background-repeat:no-repeat;background-size:calc(100% - 20px) 2px;background-position:50% calc(100% - 7px);}" +
    ".stznav-cuenta.on .stznav-cuenta-label{background-size:100% 2px;background-position:0 calc(100% + 4px);}" +
    ".stznav--compact .stznav-content .stznav-link.on,.stznav--collapsed .stznav-menu .stznav-link.on{background-size:3px 24px;background-position:0 50%;}" +
    // Dos bloques: texto (links, sesión, idioma) y acciones (Chat, Subí tu
    // orden), con aire entre el logo y el primer link. En la barra desplegada
    // el menú se aplana (display:contents) para ordenar todo en una sola fila;
    // compacto/colapsado siguen usando los contenedores como desplegable.
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) :is(.stznav-menu,.stznav-content,.stznav-priority){display:contents;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-public-links{order:1;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-right{gap:8px;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-session-links{order:5;gap:0;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-session-links .stznav-link{padding:0 8px;}" +
    // La píldora de «Mis turnos» activa (::before, más abajo) desborda 12px a
    // la derecha del link. Sin reservarlos quedaba a 2px de «Chat» (o encima
    // del idioma) mientras «Chat» y «Subí tu orden» tienen 8px (responsable del producto,
    // 2026-10-25, m908541). 6px de margen siempre, activa o no, así la barra
    // no salta entre páginas: píldora → vecino = 6 + 2 de gap + su margen − 12.
    // También en compacto, donde el vecino es el idioma o el burger; en
    // colapsado la cuenta vive en el desplegable.
    ".stznav:not(.stznav--collapsed) .stznav-cuenta{margin-left:0;}" +
    // Compacto: la fila separa con 2px; 6px más dan los 8px de Chat → orden, y
    // −5px a la derecha igualan al burger (sus barras están 11px adentro) y al idioma.
    ".stznav--compact:not(.stznav--collapsed) .stznav-cuenta{margin-left:6px;margin-right:-5px;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-cuenta{order:5;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-langs{order:4;margin-left:0;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-btn--chat{order:2;}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-btn--ghost{order:3;}" +
    // 1024–1199px: con la letra más grande de Pinamar la fila completa quedaba
    // 10px más ancha que la barra y layout() escondía el menú en el burger;
    // 2px menos de aire por link la mantiene entera (medido, 2026-10-17).
    "@media(max-width:1199px){.stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-public-links .stznav-link{padding:0 8px;}}" +
    ".stznav:not(.stznav--compact):not(.stznav--collapsed) .stznav-burger{order:6;}" +
    // "Subí tu orden" = botón blanco, borde NEUTRO (no verdoso) + ícono de
    // orden médica (TopNav.jsx canónico, 2026-07-21). Misma caja de 44px y
    // misma letra que los links del menú — sólo cambia el fondo/borde.
    ".stznav-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;white-space:nowrap;" +
      "min-height:44px;font-size:var(--nav-size,15px);font-weight:var(--nav-weight,400);text-decoration:none;padding:0 13px;border-radius:10px;" +
      "margin-left:0;transition:filter .12s,background .12s,border-color .12s,transform .1s;}" +
    ".stznav-btn:active{transform:translateY(1px);}" +
    ".stznav-ico{display:inline-block;width:20px;height:20px;flex-shrink:0;background:currentColor;-webkit-mask:var(--ico) center/contain no-repeat;mask:var(--ico) center/contain no-repeat;}" +
    ".stznav-btn--ghost{background:var(--nav-btn-bg,var(--panel));color:var(--nav-btn-ink,var(--text));border:1.5px solid var(--nav-btn-bd,var(--border-strong));}" +
    ".stznav-btn--ghost:hover{background:var(--nav-btn-hover,var(--hover-bg-soft));border-color:var(--nav-btn-hover-bd,var(--hover-bd-strong));color:var(--nav-btn-hover-ink,var(--hover-ink));}" +
    ".stznav-btn--ghost.on{border-color:var(--nav-btn-on,var(--accent));color:var(--nav-btn-on,var(--accent));}" +
    // Chat = ícono de robot + «Chat» en un botón con borde, el MISMO control que
    // «Subí tu orden» (responsable del producto, 2026-10-17: «que Chat también parezca un botón»;
    // revoca el «sin caja» del 2026-10-03). Misma caja, letra y tokens --nav-btn-*.
    ".stznav-btn--chat{font-family:inherit;cursor:pointer;background:var(--nav-btn-bg,var(--panel));color:var(--nav-btn-ink,var(--text));" +
      "border:1.5px solid var(--nav-btn-bd,var(--border-strong));}" +
    ".stznav-btn--chat:hover{background:var(--nav-btn-hover,var(--hover-bg-soft));border-color:var(--nav-btn-hover-bd,var(--hover-bd-strong));color:var(--nav-btn-hover-ink,var(--hover-ink));}" +
    ".stznav-btn--chat img{filter:var(--nav-icon-filter,none);}" +
    ".stznav-btn--chat img{width:20px;height:20px;}" +
    ".stznav-btn--chat[hidden]{display:none;}" +
    ".stznav-account-item{display:none;}" +
    ".stznav-account-section{display:none;}" +
    // Intermedio: los controles prioritarios siguen visibles y el burger
    // abre sólo los links de contenido. El DOM no se duplica.
    ".stznav--compact .stznav-burger{display:block;}" +
    ".stznav--compact .stznav-content{position:absolute;top:calc(100% + 8px);right:var(--stznav-pad,24px);z-index:30;" +
      "flex-direction:column;align-items:stretch;gap:12px;padding:12px;background:var(--panel);" +
      "border:1px solid var(--border-strong);border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.14);" +
      "opacity:0;visibility:hidden;transform:translateY(-4px);transition:opacity .12s,transform .12s;}" +
    ".stznav--compact.open .stznav-content{opacity:1;visibility:visible;transform:translateY(0);}" +
    // Ancho por contenido con piso de 240px (responsable del producto, 2026-10-17: «el menú es
    // demasiado ancho»; antes 272px fijos pegados al borde de la pantalla). El
    // panel cuelga alineado al borde derecho del burger (--stznav-pad).
    ".stznav--compact .stznav-content,.stznav--collapsed .stznav-menu{width:max-content;min-width:240px;max-width:calc(100vw - 32px);max-height:calc(100dvh - var(--stznav-h,92px) - 24px);overflow-y:auto;padding:8px;gap:2px;}" +
    ".stznav--compact .stznav-public-links{flex-direction:column;align-items:stretch;width:100%;}" +
    ".stznav--compact .stznav-session-links,.stznav--collapsed .stznav-session-links{flex-direction:column;align-items:stretch;gap:2px;order:3;}" +
    // En el desplegable Chat y «Subí tu orden» son filas como las demás: sin
    // caja, ícono + texto, misma letra y el mismo borde izquierdo (revisión del
    // demo 2026-11-15: «full-width buttons» entre links sin caja).
    ".stznav--compact .stznav-content .stznav-btn,.stznav--collapsed .stznav-menu .stznav-btn{justify-content:flex-start;gap:10px;padding:0 10px;border:0;border-radius:8px;background:none;font-size:16px;color:var(--text);}" +
    ".stznav--compact .stznav-content .stznav-btn:hover,.stznav--collapsed .stznav-menu .stznav-btn:hover{background:var(--hover-bg);}" +
    ".stznav--compact .stznav-link,.stznav--collapsed .stznav-link{min-height:44px;font-size:16px;border-radius:8px;}" +
    // Los ítems del desplegable son filas flex (no block): .stznav-link
    // centra el texto con align-items:center sobre su caja de 44px, y un
    // display:block lo dejaba pegado arriba — el resaltado de hover/activo
    // salía con la letra 10px por encima del centro.
    ".stznav--compact .stznav-content .stznav-link{display:flex;}" +
    // El botón de la barra trae margin-left:6px; dentro del desplegable
    // dejaba «Chateá con IA» 6px corrido a la derecha del resaltado de
    // «Ingresar» (mismo borde de contenido del panel que los links).
    ".stznav--compact .stznav-content .stznav-btn{margin-left:0;}" +
    ".stznav--compact.stznav--account .stznav-account-item{display:flex;width:100%;box-sizing:border-box;}" +
    // Sin margen extra arriba: entre «Chat» y «Subí tu orden» queda el gap del
    // panel (12px), igual que el aire bajo el último botón (responsable del producto, 2026-10-17).
    ".stznav--collapsed .stznav-btn{margin-left:0;margin-top:0;width:100%;}" +
    ".stznav--collapsed.stznav--account .stznav-menu .stznav-cuenta{display:none;}" +
    ".stznav--collapsed .stznav-content,.stznav--collapsed .stznav-priority{display:contents;}" +
    ".stznav-public-links{display:flex;align-items:center;gap:2px;}" +
    ".stznav--collapsed .stznav-public-links{order:1;display:flex;flex-direction:column;align-items:stretch;gap:2px;}" +
    ".stznav--collapsed .stznav-btn{order:2;}" +
    ".stznav--collapsed.stznav--account .stznav-account-section{display:block;order:3;margin-top:8px;padding-top:8px;}" +
    ".stznav-account-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 5px 5px 12px;color:var(--text);font-size:13px;font-weight:700;}" +
    ".stznav-account-section .stznav-account-item{display:flex;width:100%;box-sizing:border-box;}" +
    ".stznav .stznav-account-section .stznav-account-item[hidden]{display:none;}" +
    ".stznav-account-logout{display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;padding:0;border:0;border-radius:8px;background:none;color:var(--text);cursor:pointer;}" +
    ".stznav-account-logout:hover{background:var(--hover-bg);color:var(--hover-ink);}" +
    ".stznav-account-logout .stznav-ico{width:20px;height:20px;}" +
    // "Mi cuenta" = avatar + texto, sin fondo/padding de píldora y SIN
    // anillo/borde alrededor del círculo (responsable del producto, 2026-07-19 — revoca el
    // "sólo avatar con anillo" del 2026-07-18, ver comentario de cabecera).
    // Fila flex simple igual que el mockup canónico de TopNav.jsx: círculo
    // fijo de 40px + gap 9px + texto negro, igual que Ingresar/Registrarse.
    // Mismo sistema métrico de la barra (2026-09-19): 15px/500 en una fila
    // de 44px — la única diferencia con los botones es que no lleva caja.
    ".stznav-cuenta{display:inline-flex;align-items:center;gap:9px;flex-shrink:0;margin-left:14px;min-height:44px;" +
      "color:var(--nav-ink,var(--text));text-decoration:none;font-size:var(--nav-size,15px);font-weight:var(--nav-weight,400);white-space:nowrap;" +
      "border-radius:6px;}" +
    ".stznav-cuenta:hover{color:var(--nav-hover-ink,var(--hover-ink));}" +
    // Activa = la misma píldora que Médicos/Centros (responsable del producto, 2026-10-17:
    // «highlight my account when I am there»). La píldora es un ::before que
    // desborda el link, así ni el ancho ni el peso cambian y la barra no salta.
    ".stznav-cuenta.on{color:var(--nav-on-ink,var(--text));}" +
        ".stznav-cuenta:focus-visible{outline:2px solid var(--accent);outline-offset:3px;}" +
    // El degradé era un literal fijo (#17a46a/#0e8b50 = el verde de
    // pinamar) que no pasaba por el tema: en "humanrounds" este círculo
    // salía verde igual, ignorando el rediseño (2026-08-05, sweep del
    // design-loop). Ahora lee --av-hero-a/-b, que sí cambian por tema.
    ".stznav-cuenta-av{position:relative;width:40px;height:40px;border-radius:50%;flex-shrink:0;overflow:hidden;" +
      "background:linear-gradient(135deg,var(--av-hero-a),var(--av-hero-b));display:flex;align-items:center;justify-content:center;" +
      "color:#fff;font:700 15px var(--font);}" +
    ".stznav-cuenta-av img{position:absolute;inset:0;width:100%;height:100%;aspect-ratio:1/1;object-fit:cover;}" +
    ".stznav-cuenta-av.noimg img{display:none;}" +
    // Iniciales (mismo degradé verde que .rail-av de Mi cuenta): sólo se
    // muestran cuando la foto falla (.noimg) — hasta entonces el círculo
    // queda liso, mismo comportamiento que antes.
    ".stznav-cuenta-init{display:none;}" +
    ".stznav-cuenta-av.noimg .stznav-cuenta-init{display:block;}" +
    ".stznav--collapsed .stznav-cuenta{margin-left:0;margin-top:4px;}" +
    // burger: mismo hit-target que el resto de los controles de la app (44px),
    // tres barras — <span> literales (no pseudo-elementos: anidarlos dentro de un
    // span ya posicionado duplica el offset y sólo se ve la barra del medio).
    // Oculto salvo que midamos que hace falta.
    ".stznav-burger{display:none;position:relative;width:44px;height:44px;flex-shrink:0;padding:0;border:0;" +
      "background:none;border-radius:10px;cursor:pointer;-webkit-tap-highlight-color:transparent;}" +
    ".stznav-burger:hover{background:var(--nav-hover-bar,var(--hover-bg));}" +
    ".stznav-burger .stznav-ico{width:24px;height:24px;margin:10px;color:var(--nav-name,var(--text));}" +
    // Colapsado: el menú pasa a ser el desplegable del burger. La acción
    // "Subí tu orden" queda visible en la barra y su copia del menú se oculta.
    ".stznav--collapsed .stznav-burger{display:block;}" +
    ".stznav--collapsed .stznav-menu{position:absolute;top:calc(100% + 8px);right:var(--stznav-pad,24px);z-index:30;" +
      "flex-direction:column;align-items:stretch;gap:12px;padding:12px;background:var(--panel);" +
      "border:1px solid var(--border-strong);border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.14);" +
      "opacity:0;visibility:hidden;transform:translateY(-4px);transition:opacity .12s,transform .12s;}" +
    ".stznav--collapsed.open .stznav-menu{opacity:1;visibility:visible;transform:translateY(0);}" +
    "@media(max-width:600px){.stznav-inner{--stznav-pad:16px;padding-left:16px;padding-right:16px;}.stznav--compact .stznav-content,.stznav--collapsed .stznav-menu{left:16px;right:16px;width:auto;min-width:0;max-width:none;}}" +
    "@media(max-width:560px){.stznav-name{font-size:22px;}.stznav-name-long{font-size:16px;}}" +
    "@media(max-width:420px){.stznav-inner{gap:7px;}.stznav-brand{gap:6px;}" +
      ".stznav-mark{width:40px;height:40px;}.stznav-name{font-size:17px;}.stznav-name-long{font-size:15px;max-width:10em;}" +
      // Nombre largo a 15px en el teléfono (2026-11-15, revisión pública: 13,5px era
      // letra chica en la barra).
      // El vacío interno del logo escala con la marca: 13px a 64, 8px a 40.
      ".stznav-brand{margin-left:-8px;}}" +
    // Selector de idioma: ADENTRO del menú, última posición a la derecha,
    // en la misma línea que el resto de los ítems (responsable del producto, 2026-08-01 —
    // revoca la sub-fila propia debajo de la barra que traía la fase 3a).
    // Cada idioma es un .stznav-link como cualquier otro ítem del menú (un
    // control nuevo copia al que ya hace ese trabajo): mismo tamaño, mismo
    // hover, mismo pill verde para el activo. Al colapsar entra al burger
    // con el resto del menú. Renderiza sólo cuando hay un
    // window.SANA_I18N con mode="nav" y 2+ idiomas (ver langSwitchHtml()).
    // El switcher de idioma es un control de NAV, no de formulario: mismo
    // tamaño y tinta que los links (13,5px, --text-2, borde hairline), ancho
    // por contenido (la opción más larga define el mínimo) para que la barra
    // completa siga entrando en 1440 — con el select viejo de 145px+fondo
    // panel + 16px el menú no entraba y layout() lo mandaba a compacto
    // aunque sobrara lugar. En el panel del burger vuelve a fila completa
    // de 44px (piso táctil, ítem más del menú).
    // Sin caja (responsable del producto, 2026-10-03, skill ui-design-polish: ajustes de la barra =
    // controles compactos, sin cuerpo de botón): nombre del idioma + chevrón,
    // 44px de alto táctil. El chevrón es un ::after del contenedor (el select
    // nativo sigue siendo el control) y no atrapa el clic.
    ".stznav-langs{position:relative;display:flex;align-items:center;flex:none;margin-left:4px;}" +
    ".stznav-language{appearance:none;-webkit-appearance:none;font:var(--nav-weight,400) var(--nav-size,15px) var(--font);min-height:44px;width:auto;max-width:100%;padding:0 22px 0 8px;border:0;border-radius:8px;background:transparent;color:var(--nav-ink,var(--text));cursor:pointer;transition:color .12s,background .12s;}" +
    ".stznav-language:hover{color:var(--nav-hover-ink,var(--hover-ink));background:var(--nav-hover,var(--hover-bg));}" +
    ".stznav-language:focus-visible{outline:2px solid var(--nav-focus,var(--accent));outline-offset:1px;}" +
    ".stznav-langs::after{content:'';position:absolute;right:6px;top:50%;width:12px;height:12px;margin-top:-6px;background:currentColor;-webkit-mask:url(/static/icons/expand-arrow-sf-regular.svg) center/contain no-repeat;mask:url(/static/icons/expand-arrow-sf-regular.svg) center/contain no-repeat;color:var(--nav-ink,var(--text));pointer-events:none;}" +
    // En compacto/colapsado el switcher sigue EN LA BARRA (hermano del
    // burger): mismo tamaño quieto — es el único control que nunca se
    // esconde con el menú.
    ".stznav--compact .stznav-langs,.stznav--collapsed .stznav-langs{margin-left:4px;}";
  var st = document.createElement("style");
  st.id = "stznav-styles";
  st.textContent = css;
  document.head.appendChild(st);
}

function norm(p) { return (p || "/").replace(/\/+$/, "") || "/"; }

function sectionFor(path) {
  path = norm(path);
  if (path === "/") return "directorio";
  if (path === "/doctors") return "medicos";
  // /study/<cluster>/<slug> (un estudio, dónde te lo hacés) también es
  // Estudios — antes marcaba "Centros de salud", que era justamente el ítem
  // que el responsable del producto quería demotar (responsable del producto, 2026-07-12).
  if (path === "/d/diagnostico" || path.indexOf("/study/") === 0) return "estudios";
  if (path === "/upload-order") return "orden";
  if (path === "/centers" || path.indexOf("/center/") === 0 || path === "/d/grupos") return "centros";
  if (path === "/my-requests") return "mis";
  if (path === "/account") return "cuenta";
  return "";
}

// Versión de la foto de perfil para cache-bustear el avatar de "Mi cuenta".
// El navegador cachea /api/account/photo (Cache-Control private) — sin esto,
// al subir/cambiar/quitar la foto el botón del menú seguiría mostrando la
// vieja (responsable del producto 2026-07-18: "no actualiza el avatar del botón Mi cuenta").
// refreshAvatar() (la llama Mi cuenta al subir/quitar) sube este número y
// repunta el <img> vivo en la barra, así el cambio se ve al instante y en
// cada página siguiente.
var PHOTO_VER_KEY = "st_photo_v";
function photoVer() {
  try { return localStorage.getItem(PHOTO_VER_KEY) || "1"; } catch (e) { return "1"; }
}
export function refreshAvatar() {
  var v = String(Date.now());
  try { localStorage.setItem(PHOTO_VER_KEY, v); } catch (e) {}
  // Only the live controller for the current shell can refresh an avatar.
  // Looking up every matching node could revive an old, detached render.
  var host = typeof document !== "undefined" && document.getElementById
    ? document.getElementById("stznav") : null;
  var refresh = host && navAvatarRefreshers.get(host);
  if (refresh) refresh(v);
}

// A missing profile photo is an ordinary account state, not a failed image
// request. Probe the source first and request the binary only when one exists;
// this keeps the initials fallback while avoiding a normal 404 in the console.
// `alive` (opcional) es la pertenencia real del callback: la respuesta tardía
// de una barra ya desmontada o re-renderizada no repunta el <img> huérfano.
function loadAvatar(av, version, alive, signal) {
  if (!av) return;
  var img = av.querySelector("img");
  if (!img) return;
  var active = true;
  function clearImage() {
    active = false;
    img.onload = null;
    img.onerror = null;
  }
  av.classList.add("noimg");
  clearImage();
  active = true;
  img.removeAttribute("src");
  var options = { credentials: "same-origin" };
  if (signal) options.signal = signal;
  fetch("/api/account/photo?probe=1", options)
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (j) {
      if (!active || (alive && !alive())) return;
      var source = (j && j.source) || "none";
      if (source === "none") return;
      img.onload = function () { if (active && (!alive || alive())) av.classList.remove("noimg"); };
      img.onerror = function () { if (active && (!alive || alive())) av.classList.add("noimg"); };
      img.src = "/api/account/photo?v=" + (version || photoVer());
    })
    .catch(function () {
      if (active && (!alive || alive())) av.classList.add("noimg");
    });
  return clearImage;
}

// Íconos de la barra: archivos de Icons8 (pack sf-regular, el mismo del
// robot del chat) pintados con currentColor por máscara, así siguen la
// tinta de cada tema. Nunca un glifo dibujado a mano.
function ico(name) {
  return '<span class="stznav-ico" aria-hidden="true" style="--ico:url(/static/icons/' + name + '.svg)"></span>';
}
var ICO_ORDEN = ico("document-sf-regular");
var ICO_LOGOUT = ico("exit-sf-regular");
var ICO_BTN = { orden: ICO_ORDEN };

function link(it, active) {
  if (it.accion === "abrir-chat") {
    return '<button type="button" class="stznav-btn stznav-btn--chat" data-nav-key="chat" data-nav-accion="abrir-chat" aria-controls="stz-chat-panel" aria-label="' + (it.full || it.label) + '">' +
      '<img src="/static/icons/chat-robot-sf-regular.png" alt="" width="20" height="20">' + it.label + '</button>';
  }
  // Navigation remains links; opening chat is a real button.
  // El activo se marca en ambos.
  if (it.btn) {
    return '<a class="stznav-btn stznav-btn--' + it.btn + (it.key === active ? " on" : "") +
      '" data-nav-key="' + it.key +
      (it.accion ? '" data-nav-accion="' + it.accion : "") +
      '" href="' + it.href + '">' + (ICO_BTN[it.key] || "") + it.label + "</a>";
  }
  // "Mi cuenta" = avatar (foto real si tiene — mismo target que
  // /api/account/photo del resto del sitio — o iniciales sobre el degradé
  // verde si falla/no tiene) + el texto "Mi cuenta" (responsable del producto, 2026-07-19: el
  // mockup canónico siempre llevó label, la implementación real se había
  // desalineado quitándolo).
  if (it.key === "cuenta") {
    var ini = String(it.iniciales || "").replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
    return '<a class="stznav-cuenta' + (it.key === active ? " on" : "") +
      '" href="' + it.href + '" title="' + it.label + '">' +
      '<span class="stznav-cuenta-av noimg"><img alt="" width="40" height="40">' +
      '<span class="stznav-cuenta-init">' + ini + "</span></span>" +
      '<span class="stznav-cuenta-label">' + it.label + '</span></a>';
  }
  return '<a class="stznav-link' + (it.key === active ? " on" : "") +
    '" data-nav-key="' + it.key + '" href="' + it.href + '">' + it.label + "</a>";
}

// window.SANA_I18N is injected inline by i18n.bootstrap_script() ONLY on
// pages that carry the {{i18n_bootstrap}} template token (the four
// site_identity pages) — absent everywhere else, so this returns "" and
// every other page is byte-for-byte unaffected.
function langSwitchHtml() {
  var i18n = (typeof window !== "undefined") && window.SANA_I18N;
  if (!i18n || i18n.mode !== "nav" || !i18n.enabled || i18n.enabled.length < 2) return "";
  function escape(value) {
    return String(value).replace(/[&<>"']/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; });
  }
  var options = i18n.enabled.map(function (code) {
    var name = code;
    try { name = new Intl.DisplayNames([code], { type: "language" }).of(code); } catch (_) {}
    return '<option value="' + escape(code) + '" lang="' + escape(code) + '"' +
      (code === i18n.active ? ' selected' : '') + '>' + escape(name) + '</option>';
  }).join("");
  return '<div class="stznav-langs"><select class="stznav-language" aria-label="' + escape(T("demo.banner.language_label", null, "Idioma")) + '">' + options + '</select></div>';
}

export function html(pathname, section, state) {
  var active = section || sectionFor(pathname || (typeof location !== "undefined" ? location.pathname : "/"));
  var items = effectiveItems(state || readCache());
  var publicItems = items.filter(function (it) {
    return !it.btn && it.key !== "cuenta" && it.key !== "ingresar" && it.key !== "registrarse";
  });
  var content = '<div class="stznav-public-links">' + publicItems.map(function (it) {
    return link(it, active);
  }).join("") + "</div>";
  var priority = items.filter(function (it) {
    return (!!it.btn && it.key !== "chat") || it.key === "cuenta";
  }).map(function (it) { return link(it, active); }).join("");
  var sessionLinks = items.filter(function (it) { return it.key === "ingresar" || it.key === "registrarse"; });
  if (sessionLinks.length) content += '<div class="stznav-session-links">' + sessionLinks.map(function (it) { return link(it, active); }).join("") + '</div>';
  content += items.filter(function (it) { return it.key === "chat"; }).map(function (it) { return link(it, active); }).join("");
  if (active === "cuenta" && state && state.loggedIn) {
    var accountNav = accountItems().slice();
    // «Mis turnos» va siempre; la página de cuenta lo muestra u oculta por el
    // evento stz:account-tab (nadie trae ya /api/requests para la barra, así
    // que el hasSolicitudes del cache casi nunca está en true).
    accountNav.unshift({ key: "turnos", label: T("nav.my_appointments", null, "Mis turnos"), hidden: !state.hasSolicitudes });
    priority += '<section class="stznav-account-section" aria-label="' + T("nav.my_account", null, "Mi cuenta") + '">' +
      '<div class="stznav-account-heading"><span>' + T("nav.my_account", null, "Mi cuenta") + '</span>' +
      // A portal session is the portal's: no «Cerrar sesión» that cannot end it.
      (state.authMode === "portal" ? "</div>" :
        '<button type="button" class="stznav-account-logout" aria-label="' + T("nav.sign_out", null, "Cerrar sesión") + '" title="' + T("nav.sign_out", null, "Cerrar sesión") + '">' + ICO_LOGOUT + '</button></div>') +
      accountNav.map(function (it) {
        return '<a class="stznav-link stznav-account-item" data-account-tab="' + it.key +
          '" href="/account#' + it.key + '"' + (it.hidden ? " hidden" : "") + '>' + it.label + "</a>";
      }).join("") + '</section>';
  }
  // Brand text follows the active theme. The table intentionally mirrors
  // theme.brand_name() because browser JavaScript cannot import Python.
  var NOMBRES_MARCA = { pinamar: "Turnos de salud pública en Pinamar", humanrounds: "Human Rounds" };
  var tema = (typeof document !== "undefined" && document.documentElement.getAttribute("data-theme")) || "humanrounds";
  var marca = NOMBRES_MARCA[tema] || NOMBRES_MARCA.humanrounds;
  // La barra dice el nombre del sitio, el mismo de la pestaña, el chat y los
  // correos (Pinamar: «Turnos de salud pública en Pinamar», responsable del
  // producto 2026-10-27; reemplaza el lema «Portal de turnos de salud en
  // Pinamar» de la oficina de prensa del 2026-10-22). Un nombre largo (más de
  // 20 letras) va en dos renglones parejos (.stznav-name-long); uno corto, en
  // un renglón. Es un nombre propio y no se traduce: su idioma va por tema
  // (IDIOMA_MARCA, no por el largo) para que un lector de pantalla lo lea en
  // ese idioma en cualquier idioma del sitio.
  var IDIOMA_MARCA = { pinamar: "es" };
  var idiomaMarca = IDIOMA_MARCA[tema] ? ' lang="' + IDIOMA_MARCA[tema] + '"' : "";
  var nombre = '<span class="' + (marca.length > 20 ? "stznav-name-long" : "stznav-name") + '"' +
    idiomaMarca + ">" + marca + "</span>";
  // El switcher de idioma va como HERMANO del menú, NUNCA adentro: dentro del
  // menú se esconde con el burger cada vez que layout() colapsa la barra
  // (con 10 idiomas + labels largos — "Carica la tua impegnativa" — eso pasa
  // incluso a 1440), y el visitante pierde el idioma de un vistazo. Afuera
  // queda en la barra a TODO ancho, al lado del burger (responsable del producto, 2026-09-16).
  return (
    '<div class="stznav-inner">' +
      '<a class="stznav-brand" href="/"><span class="stznav-mark"></span>' + nombre + "</a>" +
      '<div class="stznav-right">' +
        '<nav class="stznav-menu" aria-label="' + T("nav.main", null, "Navegación principal") + '"><div class="stznav-content">' + content +
          '</div><div class="stznav-priority">' + priority + "</div></nav>" +
        langSwitchHtml() +
        '<button type="button" class="stznav-burger" aria-label="' + T("nav.menu", null, "Menú") + '" aria-expanded="false">' +
          ico("menu-sf-regular") + "</button>" +
      "</div>" +
    "</div>"
  );
}

/* ¿Entran los links al lado de la marca? Se mide, no se adivina: se fuerza el
   estado desplegado, se lee el ancho natural del lado derecho y se colapsa
   sólo si se pasa del ancho de la barra. Todo dentro del mismo frame (sin
   pintar en el medio), así no hay parpadeo. Se re-corre al cambiar el tamaño
   y, si la sesión cambió desde la última visita, tras el reconcile. */
/* El select de idioma mide lo que muestra (el idioma elegido), no su opción
   más larga: así el chevrón queda pegado al nombre, sin hueco. */
var fitCtx = null;
function fitLanguage(host) {
  var sel = host.querySelector(".stznav-language");
  if (!sel || !sel.options || !sel.options[sel.selectedIndex]) return;
  try {
    fitCtx = fitCtx || document.createElement("canvas").getContext("2d");
    var cs = getComputedStyle(sel);
    fitCtx.font = cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
    var pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    sel.style.width = Math.ceil(fitCtx.measureText(sel.options[sel.selectedIndex].text).width + pad + 2) + "px";
  } catch (e) {}
}

function layout(host) {
  fitLanguage(host);
  var inner = host.querySelector(".stznav-inner");
  var right = host.querySelector(".stznav-right");
  var brand = host.querySelector(".stznav-brand");
  if (!inner || !right || !brand) return;
  host.classList.remove("stznav--compact", "stznav--collapsed");
  // Se mide contra el ancho de contenido de .stznav-inner (clientWidth menos su
  // padding), no contra el host: el host ahora es la banda a todo el ancho.
  var cs = getComputedStyle(inner);
  var pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
  var gap = parseFloat(cs.columnGap) || 16; // gap de .stznav-inner
  var need = brand.offsetWidth + right.scrollWidth + gap;
  var available = inner.clientWidth - pad;
  if (need > available) {
    host.classList.add("stznav--compact");
    var priority = host.querySelector(".stznav-priority");
    var burger = host.querySelector(".stznav-burger");
    var langs = host.querySelector(".stznav-langs");
    // El switcher de idioma vive EN LA BARRA también en compacto/colapsado
    // (hermano del burger): su ancho entra en TODOS los presupuestos, o la
    // barra desborda en el único estado donde nada más se puede esconder.
    var langsW = langs ? langs.offsetWidth + 4 : 0;
    var compactNeed = brand.offsetWidth + (priority ? priority.offsetWidth : 0) +
      (burger ? burger.offsetWidth : 44) + langsW + gap + 6;
    if (compactNeed > available) {
      host.classList.remove("stznav--compact");
      host.classList.add("stznav--collapsed");
    }
  } else {
    host.classList.remove("open");
    var b = host.querySelector(".stznav-burger");
    if (b) b.setAttribute("aria-expanded", "false");
  }
  // Publica el alto real de la barra como variable CSS: la barra es sticky
  // (human-rounds.css#stznav) y elementos como .searchbar de la portada
  // necesitan pararse DEBAJO de ella, nunca tapados. Se mide acá — el mismo
  // sitio que ya mide el resto del layout — en vez de hardcodear un número
  // que se desincroniza en cuanto cambia el contenido de la barra (responsable del producto,
  // 2026-08-05, pedido de reference-deployment counterpart de que el chat quede alcanzable
  // scrolleando). El fallback en :root cubre el frame antes del primer
  // layout().
  document.documentElement.style.setProperty("--stznav-h", host.offsetHeight + "px");
}

function closeMenu(host) {
  host.classList.remove("open");
  var b = host.querySelector(".stznav-burger");
  if (b) b.setAttribute("aria-expanded", "false");
}

// ---- Cmd-K / Ctrl-K → the page's public search (the product owner, 2026-10-21) ---------
// The patient site's search boxes are its visible input[type=search] elements
// (the home's #q and the listings' .lst-search — the same convention
// listing.mjs's pinSearchTop follows), so the shortcut focuses the first
// visible one; a page without any opens the home, which IS the search.
// The shortcut yields to whatever else owns the keyboard at the moment: an
// open dialog/aria-modal surface, the chat panel, an editor (textarea,
// contenteditable, any non-search input) or an IME composition.
function publicSearchBox() {
  if (typeof document === "undefined" || typeof document.querySelectorAll !== "function") {
    return null;
  }
  var boxes = document.querySelectorAll('input[type="search"]');
  for (var i = 0; i < boxes.length; i++) {
    var el = boxes[i];
    if (!el || el.disabled || el.readOnly) continue;
    if (!el.getClientRects || !el.getClientRects().length) continue; // hidden
    if (el.closest && el.closest('dialog, [aria-modal="true"], [hidden], [inert]')) continue;
    if (typeof getComputedStyle === "function" && getComputedStyle(el).visibility === "hidden") continue;
    return el;
  }
  return null;
}
function modalOwnsKeyboard(el) {
  if (el && el.closest && (el.closest("dialog[open]") || el.closest('[aria-modal="true"]'))) return true;
  // A modal can be open before its focus trap has moved focus. Do not take
  // the keyboard away from that surface during the handoff either.
  return typeof document.querySelectorAll === "function" &&
    Array.from(document.querySelectorAll('dialog[open], [aria-modal="true"]')).some(function (modal) {
      return modal.getClientRects && modal.getClientRects().length &&
        !(modal.closest && modal.closest('[hidden], [inert], [aria-hidden="true"]'));
    });
}
function searchModifier(e) {
  var platform = typeof navigator !== "undefined"
    ? ((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "") : "";
  return /Mac|iPhone|iPad|iPod/i.test(platform) ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
}
function chatPanelOpen() {
  var panel = typeof document !== "undefined" && document.getElementById
    ? document.getElementById("stz-chat-panel") : null;
  return !!(panel && typeof panel.hasAttribute === "function" &&
            !panel.hasAttribute("hidden"));
}
function isEditor(el) {
  if (!el || !el.tagName) return false;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.tagName === "OPTION") return true;
  if (el.isContentEditable) return true;
  return el.tagName === "INPUT" && String(el.type || "text").toLowerCase() !== "search";
}

// Dibuja la barra para un estado y (re)cablea el burger + mide el layout.
// Idempotente: se llama en el primer paint y, si hizo falta, en el reconcile.
// `alive` es la generación del controlador: los callbacks asíncronos que
// render cablea (el probe del avatar, el POST del logout) no tocan una barra
// que ya fue desmontada o re-renderizada mientras volvían.
function render(host, state, alive, loadOwnedAvatar) {
  host.classList.toggle("stznav--account", sectionFor(location.pathname) === "cuenta");
  host.innerHTML = html(location.pathname, host.getAttribute("data-section") || "", state);
  var burger = host.querySelector(".stznav-burger");
  if (burger) {
    burger.addEventListener("click", function (e) {
      if (!alive()) return;
      e.stopPropagation();
      var open = host.classList.toggle("open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  var language = host.querySelector(".stznav-language");
  if (language) language.addEventListener("change", function () {
    if (!alive()) return;
    var url = new URL(location.href);
    url.searchParams.set("lang", language.value);
    location.assign(url.href);
  });
  var chat = host.querySelector('[data-nav-accion="abrir-chat"]');
  if (chat) {
    chat.hidden = !document.getElementById("stz-chat-launcher");
    chat.addEventListener("click", function () {
      if (!alive()) return;
      var launcher = document.getElementById("stz-chat-launcher");
      if (!launcher) return;
      closeMenu(host);
      launcher.click();
    });
  }
  // Sin foto → iniciales, sin emitir una solicitud binaria que respondería 404.
  var av = host.querySelector(".stznav-cuenta-av");
  if (av) loadOwnedAvatar(av);
  var logout = host.querySelector(".stznav-account-logout");
  if (logout) {
    logout.addEventListener("click", function () {
      if (!alive() || logout.disabled) return;
      logout.disabled = true;
      fetch("/api/account/logout", { method: "POST", credentials: "same-origin" })
        .then(function () { if (!alive || alive()) location.href = "/account?mode=login"; })
        .catch(function () { if (!alive || alive()) logout.disabled = false; });
    });
  }
  syncAccountActive(host);
  layout(host);
}

// La pestaña activa la decide la página de cuenta (account.mjs abre «Mis
// turnos» por defecto cuando hay solicitudes) y la publica con
// stz:account-tab; sin ese aviso se lee del hash.
var accountTab = null;
function syncAccountActive(host, detail) {
  if (detail && detail.tab) accountTab = detail;
  var hash = (location.hash || "").slice(1).split("&")[0];
  var tab = hash || (accountTab && accountTab.tab) || "datos";
  var links = host.querySelectorAll("[data-account-tab]");
  for (var i = 0; i < links.length; i++) {
    var key = links[i].getAttribute("data-account-tab");
    if (key === "turnos" && accountTab) links[i].hidden = !accountTab.turnos;
    links[i].classList.toggle("on", key === tab);
  }
}

function sameState(a, b) {
  return a.loggedIn === b.loggedIn && a.iaDown === b.iaDown &&
    a.authMode === b.authMode && (a.iniciales || "") === (b.iniciales || "");
}

/* mount(host?) → {dispose, ready}. El controlador posee TODO lo que la barra
   instala fuera de su propio DOM:
   - listeners de document/window (click-afuera, Escape, resize con su timer
     de debounce, hashchange), registrados y retirados en dispose();
   - el MutationObserver del teléfono de emergencia (107), desconectado en
     dispose() y reinstalado al restaurar;
   - las respuestas tardías de los fetch (reconcile, probe del avatar,
     POST del logout), anuladas por generación.
   Producción (no sólo tests): `pagehide` desmonta y `pageshow.persisted`
   reconstruye — una página restaurada de bfcache vuelve a medir, a linkear
   el 107 y a reconciliar la sesión, sin duplicar nada de lo anterior. */
const navOwners = new WeakMap();
const navAvatarRefreshers = new WeakMap();
const emergencyLeases = new Set();
var emergencyOwner = null;
export function mount(host) {
  host = host || (typeof document !== "undefined" && document.getElementById
    ? document.getElementById("stznav") : null);
  var gen = 0;              // generación del controlador: bump en cada begin/dispose
  var observer = null;      // MutationObserver del 107, propiedad del controlador
  var readsController = null;
  var avatarController = null;
  var avatarCleanup = null;
  var avatarGeneration = 0;
  var rt = 0;               // timer de debounce del resize
  var listeners = [];       // pares [target, tipo, fn] instalados por este controlador
  var searchFocus = null;   // {el, invoker} mientras ESTE atajo posee el foco de la búsqueda
  var rendered = null;      // estado con el que se dibujó (para el sameState del reconcile)
  var disposed = false;
  var running = false;
  var hidden = false;
  var renderGeneration = 0;
  const ownedClasses = ["stznav", "stznav--account", "stznav--compact", "stznav--collapsed", "open"];
  if (host) navOwners.get(host)?.dispose();
  var previousEmergency = emergencyOwner;
  emergencyOwner = null; // Reserve transfer; predecessor disposal must not reacquire mid-mount.
  if (previousEmergency) {
    // A page-only owner has no independent header to preserve. A header
    // owner relinquishes just the singleton observer, not its own controls.
    if (!previousEmergency.host) previousEmergency.controller.dispose();
    else previousEmergency.release();
  }
  const initialClasses = new Set(ownedClasses.filter(name => host && host.classList.contains(name)));
  const controller = { dispose, ready: null };
  if (host) {
    navOwners.set(host, controller);
    navAvatarRefreshers.set(host, refreshOwnedAvatar);
  }
  const emergencyLease = {
    controller, host, release: stopEmergency,
    available: () => !disposed && ownsHost() && (!host || host.isConnected !== false),
    resume: () => { if (alive(gen)) { stopEmergency(); watchEmergency(gen); } },
  };
  emergencyLeases.add(emergencyLease);
  emergencyOwner = emergencyLease;

  function ownsHost() { return !host || navOwners.get(host) === controller; }
  function alive(token) {
    return !disposed && !hidden && running && token === gen && ownsHost() &&
      (!host || host.isConnected !== false);
  }
  function paint(state, token) {
    if (!alive(token)) return;
    const epoch = ++renderGeneration;
    rendered = state;
    render(host, state, () => alive(token) && epoch === renderGeneration,
      function (av) { loadOwnedAvatar(av, undefined, token, epoch); });
  }

  function abortReads() {
    if (readsController) readsController.abort();
    readsController = null;
  }
  function clearAvatar() {
    avatarGeneration += 1;
    if (avatarCleanup) avatarCleanup();
    avatarCleanup = null;
    if (avatarController) avatarController.abort();
    avatarController = null;
  }
  function loadOwnedAvatar(av, version, token, epoch) {
    clearAvatar();
    avatarController = typeof AbortController !== "undefined" ? new AbortController() : null;
    var avatarToken = ++avatarGeneration;
    avatarCleanup = loadAvatar(av, version, function () {
      return alive(token) && epoch === renderGeneration && avatarToken === avatarGeneration;
    }, avatarController && avatarController.signal);
  }
  function refreshOwnedAvatar(version) {
    if (!host || !alive(gen)) return;
    var av = host.querySelector(".stznav-cuenta-av");
    if (av) loadOwnedAvatar(av, version, gen, renderGeneration);
  }

  function on(target, type, fn) {
    target.addEventListener(type, fn);
    listeners.push([target, type, fn]);
  }
  function off() {
    for (var i = 0; i < listeners.length; i++) {
      listeners[i][0].removeEventListener(listeners[i][1], listeners[i][2]);
    }
    listeners.length = 0;
  }

  function stopEmergency() {
    if (observer) { observer.disconnect(); observer = null; }
  }
  function watchEmergency(token) {
    if (emergencyOwner?.controller !== controller) return;
    linkEmergencyNumbers(typeof document !== "undefined" ? document.body : null);
    if (typeof MutationObserver === "undefined" || !document.body) return;
    observer = new MutationObserver(function (records) {
      if (!alive(token) || emergencyOwner?.controller !== controller) return;
      records.forEach(function (record) {
        for (var i = 0; i < record.addedNodes.length; i++) {
          var added = record.addedNodes[i];
          if (added.nodeType === 3) linkEmergencyNumbers(added.parentNode);
          else if (added.nodeType === 1) linkEmergencyNumbers(added);
        }
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function begin() {
    if (disposed || hidden || running || !ownsHost() || (host && host.isConnected === false)) return;
    running = true;
    gen += 1;
    var token = gen;
    readsController = typeof AbortController !== "undefined" ? new AbortController() : null;
    rendered = readCache();
    if (!emergencyOwner) emergencyOwner = emergencyLease;
    watchEmergency(token);
    if (!host) return; // página sin <header id="stznav">: sólo el hook del 107
    injectStyles();
    host.classList.add("stznav");
    paint(rendered, token);
    // Listeners de documento una sola vez por vida del controlador (viven
    // aunque re-renderice el menú; se van completos en dispose()).
    on(document, "click", function (e) {
      if (!alive(token)) return;
      if (!host.contains(e.target)) closeMenu(host);
    });
    on(document, "keydown", function (e) {
      if (!alive(token)) return;
      if (e.key === "Escape") {
        closeMenu(host);
        // Escape hands focus back to whoever had it before Cmd-K moved it
        // into the public search — only while this shortcut still owns that
        // move (any later focus change cleared `searchFocus` via focusin).
        if (searchFocus && document.activeElement === searchFocus.el &&
            !e.defaultPrevented && !e.isComposing && e.keyCode !== 229 &&
            !modalOwnsKeyboard(document.activeElement) && !chatPanelOpen()) {
          var invoker = searchFocus.invoker;
          searchFocus = null;
          if (e.preventDefault) e.preventDefault();
          if (invoker && invoker.isConnected) invoker.focus();
        }
        return;
      }
      // Cmd-K (Mac) / Ctrl-K (elsewhere) opens the page's public search. The
      // PHYSICAL key decides (e.code === "KeyK"), so a non-Latin keyboard
      // layout (Cyrillic, where Cmd-K reports the Cyrillic letter U+043A as key) still triggers it.
      // preventDefault keeps Cmd-K away from the browser's own search
      // shortcut (the Chrome omnibox).
      if (searchModifier(e) && !e.altKey && !e.shiftKey &&
          (e.code === "KeyK" || e.key === "k" || e.key === "K") &&
          !e.defaultPrevented && !e.isComposing && e.keyCode !== 229) {
        var a = document.activeElement;
        if (modalOwnsKeyboard(a) || chatPanelOpen() || isEditor(a)) return;
        if (e.preventDefault) e.preventDefault();
        var box = publicSearchBox();
        if (!box) { location.assign("/#q"); return; }
        if (!searchFocus || searchFocus.el !== box || a !== box) searchFocus = (a && a.focus && a.tagName !== "BODY" && a !== box &&
                       a.isConnected !== false)
          ? { el: box, invoker: a } : { el: box, invoker: null };
        box.focus();
        if (box.select) { try { box.select(); } catch (err) {} }
      }
    });
    on(document, "focusin", function (e) {
      if (!alive(token)) return;
      if (searchFocus && e.target !== searchFocus.el) searchFocus = null;
    });
    // A page without a search uses the existing home surface. The fragment
    // carries only this focus intent, never a query or private form values.
    if (location.pathname === "/" && location.hash === "#q") {
      var initialSearch = publicSearchBox();
      if (initialSearch && !modalOwnsKeyboard(document.activeElement) && !isEditor(document.activeElement)) initialSearch.focus();
    }
    on(window, "resize", function () {
      if (!alive(token)) return;
      clearTimeout(rt);
      rt = setTimeout(function () { if (alive(token)) layout(host); }, 100);
    });
    // La PRIMERA medición de layout() suele correr antes de que Manrope haya
    // cargado: con la fuente de respaldo (más ancha) el menú "no entra" y la
    // barra queda clavada en compacto PARA SIEMPRE en anchos donde después
    // sí entraría — así se vio la demo a 1440 con el switcher de idioma
    // tirado abajo (2026-09-16). Volver a medir cuando la fuente se asienta.
    if (document.fonts && document.fonts.ready && document.fonts.status !== "loaded") {
      document.fonts.ready.then(function () {
        if (alive(token)) layout(host);
      });
    }
    on(window, "hashchange", function () {
      if (!alive(token)) return;
      syncAccountActive(host);
      closeMenu(host);
    });
    on(window, "stz:account-tab", function (e) {
      if (alive(token)) syncAccountActive(host, e.detail);
    });
    // Reconcile: esperar los TRES fetch juntos y, sólo si el estado cambió,
    // redibujar UNA vez (nunca dos swaps ni un frame intermedio). Una
    // respuesta que vuelve después de un dispose/restore pertenece a una
    // generación muerta: ni escribe el cache ni repinta.
    var signal = readsController && readsController.signal;
    Promise.all([fetchLoggedIn(signal), fetchIaDown(signal), fetchAuthMode(signal)]).then(function (res) {
      if (!alive(token)) return;
      var fresh = { loggedIn: res[0].loggedIn, iniciales: res[0].iniciales,
                    iaDown: res[1], authMode: res[2], refused: !!res[0].refused };
      // Cached signed-out BEFORE leaving: a trip that comes back without an
      // identity never starts another one.
      var before = readCache();
      writeCache(fresh);
      if (portalTripDue(before, fresh, sessionStore(), Date.now())) {
        window.location.replace("/account/portal-login?next=" + encodeURIComponent(location.pathname + location.search));
        return;
      }
      if (!sameState(fresh, rendered)) paint(fresh, token);
    });
  }

  function suspend() {
    const clearHost = running && ownsHost();
    running = false;
    gen += 1; // invalida cada token capturado por los callbacks en vuelo
    renderGeneration += 1;
    abortReads();
    clearAvatar();
    off();
    searchFocus = null;
    clearTimeout(rt);
    rt = 0;
    if (observer) { observer.disconnect(); observer = null; }
    if (host && clearHost) {
      host.replaceChildren();
      for (const name of ownedClasses) host.classList.toggle(name, initialClasses.has(name));
      if (typeof document !== "undefined" && document.documentElement &&
          document.documentElement.style && document.documentElement.style.removeProperty) {
        document.documentElement.style.removeProperty("--stznav-h");
      }
    }
  }
  function dispose() {
    if (disposed) return;
    suspend();
    disposed = true;
    if (host && ownsHost()) {
      navOwners.delete(host);
      navAvatarRefreshers.delete(host);
    }
    emergencyLeases.delete(emergencyLease);
    if (emergencyOwner === emergencyLease) {
      // A temporary page-only owner must not permanently disable a still-live
      // header's emergency hook. Retain eligible leases across nested transfers;
      // suspended headers reserve ownership and resume only on their pageshow.
      emergencyOwner = [...emergencyLeases].reverse().find(lease => lease.available()) || null;
      emergencyOwner?.resume();
    }
    if (typeof window !== "undefined" && window.removeEventListener) {
      window.removeEventListener("pagehide", onHidden);
      window.removeEventListener("pageshow", onShown);
    }
  }

  function onHidden() { hidden = true; suspend(); }
  function onShown(event) {
    if (event && event.persisted && hidden && !disposed) {
      hidden = false;
      i18nReady.then(begin);
    }
  }

  // El latido bfcache vive FUERA de `listeners` a propósito: dispose() vacía
  // el registro, y si pagehide se llevara puesto el pageshow, la página
  // restaurada nunca se reconstruiría. Un dispose() externo (embedder) sí lo
  // corta — controlador muerto es controlador muerto.
  if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("pagehide", onHidden);
    window.addEventListener("pageshow", onShown);
  }

  // El primer render espera al catálogo (i18nReady nunca rechaza): mismo
  // contrato que el clásico `i18nReady.then(mount, mount)`.
  controller.ready = i18nReady.then(begin);
  return controller;
}

// Auto-monte: cada página pública carga este módulo con su tag
// `<script type="module" src=...fingerprinteado>` (deferred por spec, así que
// el DOM ya está parseado); la guarda de readyState cubre un import dinámico
// que aterrice más temprano. En Node (tests) document no existe y no pasa nada.
if (typeof document !== "undefined" && document.addEventListener) {
  boot();
}
function boot() {
  if (document.readyState === "loading") {
    let hidden = false;
    let pending = true;
    function finish() {
      if (!pending || hidden || document.readyState === "loading") return;
      pending = false;
      document.removeEventListener("DOMContentLoaded", finish);
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", show);
      mount();
    }
    function hide() { hidden = true; }
    function show(event) {
      if (event.persisted) { hidden = false; finish(); }
    }
    document.addEventListener("DOMContentLoaded", finish);
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    return;
  }
  mount();
}

// The head of a printed sheet (platform/print-head.mjs: screen, filters, print
// time) rides on the bar every public page already loads. Fetched once the
// page is idle — never on the first paint's path — and on a print that comes
// before that.
if (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/print-head.mjs"]) {
  const loadPrintHead = () => { import(window.SANA_STATIC["platform/print-head.mjs"]).catch(() => {}); };
  if (window.requestIdleCallback) window.requestIdleCallback(loadPrintHead, { timeout: 4000 });
  else setTimeout(loadPrintHead, 2000);
  window.addEventListener("beforeprint", loadPrintHead, { once: true });
}

// The anonymous analytics beacon (platform/analytics.mjs) — present only where the server turned the
// stream on (window.SANA_EV). Idle-loaded like the print head; events raised before it arrives wait in
// window.__hrq.
if (typeof window !== "undefined" && window.SANA_EV && window.SANA_STATIC && window.SANA_STATIC["platform/analytics.mjs"]) {
  const loadAnalytics = () => { import(window.SANA_STATIC["platform/analytics.mjs"]).catch(() => {}); };
  if (window.requestIdleCallback) window.requestIdleCallback(loadAnalytics, { timeout: 3000 });
  else setTimeout(loadAnalytics, 1500);
}
