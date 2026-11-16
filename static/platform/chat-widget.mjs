/* static/platform/chat-widget.mjs — the site's floating booking assistant
   ("Chat con IA"). The launcher opens a full-height sidebar on the patient
   pages: message list, option chips, a control to attach the medical
   referral, ONE text input, Enter to send (the <form>'s submit). It talks to
   POST /api/chat (modules/chat/chat_api.py) — signed in or anonymous, the
   server decides what the assistant may do in each case. Internal links
   arrive as safe catalog data and are applied over the names the assistant
   already wrote; they are never repeated in a separate list. `login` opens
   the embedded sign-in.

   NOT mounted on the stepped wizard's focused steps (chrome="focus" is a
   historical exception — today only chrome="chat", the pre-consult
   interview, skips it): wizard-design asks for minimal chrome there, nothing
   competing with the current step. The chat belongs to the rest of the site.

   The sidebar, its open state and the visible conversation live in
   sessionStorage: navigating through internal links neither closes it nor
   erases what the person already read. No framework, one module, CSS
   injected with the theme tokens (human-rounds.css is already loaded by the
   shell).

   The WhatsApp handoff sits next to attach and dictate in the composer
   (the product owner, 2026-08-06): it stays where the person continues the conversation,
   while "Nuevo chat" and "Historial" belong to the top bar. It stays a link
   — it opens another app, it does not submit the form. On opening the panel
   the code-less link is requested (GET /api/chat/whatsapp) and the first
   answer replaces it with the one carrying the handoff code; without a
   configured WhatsApp line there is no link and the control is not drawn.

   No dividers between regions (the product owner, 2026-07-23 + the divider hook): the
   bubbles and the input's own border already separate — space does the
   work, not a line. With little conversation the messages rest at the
   BOTTOM, on the input, instead of leaving half a screen empty.

   NATIVE ES MODULE (row 4.1 cutover, 2026-09-05): this module is the SOLE
   implementation of the public chat widget. The classic root chat-widget.js
   and its window.SanaChat global are physically retired — no compatibility
   global, no facade, no classic twin, no unversioned fallback URL. Pages
   bootstrap it through the shared inline partial (templates/
   _chat_bootstrap.html) included by the public templates and rendered by
   site_shell._shell(): that partial resolves this module's CURRENT
   fingerprinted URL from window.SANA_STATIC (fail-closed) and, when the
   import itself rejects, paints a visible localized recovery line with a
   reload action — the one failure this module cannot render itself.

   HOW TO OPEN IT FROM PAGE CODE. The shared bootstrap paints the launcher
   immediately and queues open/close (generation-checked) until this module
   evaluates — an early click never silently no-ops or navigates away, and a
   close that lands before boot cannot be resurrected by a stale callback.
   After evaluation, abrir()/cerrar() are named exports, live for the
   module's whole life:

     import(window.SANA_STATIC["platform/chat-widget.mjs"]).then(m => m.abrir())

   An abrir() that lands while platform/chat-ui.mjs is still loading queues
   (pendingOpen) instead of no-oping; a cerrar() before that boot cancels
   the queued open. There is no window.SanaChat compatibility global. The
   floating launcher (and the bootstrap's matching control) is the in-product
   entry point.

   The module's own dependency failures degrade in-panel: an i18n failure
   falls back to the Spanish call-site literals, a chat-ui failure still
   boots the panel in degraded mode (launcher + alert, no composer) — never
   a silent no-widget and never a half-boot before the component exists. */
var I18n = null;
var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
  ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
  : Promise.resolve(null);
function T(key, vars, fallback) {
  return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
}
// platform/account-form.mjs and platform/chat-ui.mjs are fingerprinted
// (core/static_fingerprint.py, CLIENT_LOADED): a served file cannot know
// their current URLs, so the shell emits them in window.SANA_STATIC and
// staticUrl()/importStatic() resolve them, fail-closed (null plus
// console.error, never a fixed fallback URL), matching resolveUrl() in
// islands.mjs.
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
// Public API, live from module evaluation (the exports above are hoisted
// declarations): before bootWidget() finishes, abrir() queues and cerrar()
// cancels the queued open — the pendingOpen contract.
var pendingOpen = false;
var widgetListo = null;
export function abrir() {
  if (widgetListo) widgetListo.abrir(true);
  else pendingOpen = true;
}
export function cerrar() {
  if (widgetListo) widgetListo.cerrar();
  else pendingOpen = false;
}
// No double-mount: a module body evaluates once per document (the module
// map dedupes repeated import() of the same URL), but a #stz-chat-panel
// already in the DOM must never get a second launcher/panel pair or a
// second copy of the state. Skip entirely when there is no document (Node
// source tests import this file as text; a live import must not boot).
if (typeof document !== "undefined" && !document.getElementById("stz-chat-panel")) {
  var chatUiReady = importStatic("platform/chat-ui.mjs").catch(function () { return null; });
  // The widget boots when BOTH platform deps settled: an i18n failure
  // degrades to the Spanish call-site literals (the classic behavior), a
  // chat-ui failure still boots the panel in degraded mode — never a silent
  // no-widget, and never a half-boot before the component exists. The
  // classic loader tag was `defer`: bootWidget() wraps the WHOLE <body>
  // into #stzc-page, so it must never run mid-parse (an import() from an
  // inline script at the top of <body> can resolve while the parser is
  // still working and would capture only what was parsed so far).
  Promise.all([i18nReady.catch(function () { return null; }), chatUiReady])
    .then(function (listo) {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded",
          function () { bootWidget(listo[1]); }, { once: true });
      } else {
        bootWidget(listo[1]);
      }
    });
}
function bootWidget(ChatUI) {
  var Dates = null;
  var datesUrl = staticUrl("platform/dates.mjs");
  var datesReady = datesUrl
    ? import(datesUrl).then(function (m) { Dates = m; return m; }).catch(function () { return null; })
    : Promise.resolve(null);
  var NUEVO_CHAT_LABEL = T("chat.new_chat", null, "Nuevo chat");
  var HISTORIAL_LABEL = T("chat.history", null, "Historial");

  var STATE_KEY = "stz_chat_sidebar_v2";
  var ICON_SRC = "/static/icons/chat-robot-sf-regular.png";
  // Attach/mic/send icons: chat-ui.mjs's crearComposer() defaults already
  // point at these same files (shared with entrevista.js) — no override
  // needed here.
  var AUTO_SPLIT_MS = 4 * 60 * 60 * 1000;

  var css =
    ":root{--stzc-sidebar-w:clamp(340px,31vw,440px);}" +
    "#stzc-page{width:100%;min-width:0;container:stzc-page / inline-size;transition:width .16s ease;}" +
    "@media (min-width:701px){body.stzc-chat-open #stzc-page{width:calc(100% - var(--stzc-sidebar-w));}" +
    "body.stzc-chat-open #stzc-page .wrap>[data-listado]{" +
    "width:min(1480px,calc(100cqw - 48px));}}" +
    "#stz-chat-launcher{position:fixed;right:20px;bottom:20px;z-index:8999;height:52px;padding:0 17px;border:0;border-radius:var(--btn-radius,14px);cursor:pointer;background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);box-shadow:var(--shadow-pop);display:inline-flex;align-items:center;justify-content:center;gap:9px;font:650 14px var(--font);white-space:nowrap;max-width:calc(100vw - 32px);transition:transform .16s ease,opacity .16s ease;}" +
    // Un solo "Chateá con IA" visible (responsable del producto, 2026-09-19: "probably we don't
    // need both"): mientras la página tiene el site nav SIN colapsar — el
    // <header id="stznav"> existe desde el HTML del server, así que la
    // regla manda desde el primer paint, sin flash — el launcher flotante
    // NO se dibuja; el botón de chat de la barra hace ese trabajo. Cuando
    // layout() colapsa el nav al burger (o en páginas sin nav), vuelve.
    // «Burger» incluye el layout compacto: ahí el botón de chat queda DENTRO
    // del desplegable cerrado, así que sin el launcher no había ninguna
    // entrada de chat visible en escritorio (2026-10-02).
    // La variante inline del flujo de reserva queda exempta.
    "body:has(#stznav:not(.stznav--collapsed):not(.stznav--compact)) #stz-chat-launcher:not(.stzc-booking-inline){display:none;}" +
    "#stz-chat-launcher:hover{background-image:var(--hover-fill);}" +
    "#stz-chat-launcher img{width:22px;height:22px;aspect-ratio:1/1;flex-shrink:0;filter:brightness(0) invert(1);}" +
    "body.stzc-chat-open #stz-chat-launcher{opacity:0;pointer-events:none;transform:translateY(12px);}" +
    // A control row passing beside the floating button (launcherAside below).
    "#stz-chat-launcher.stzc-launcher--aside:not(.stzc-booking-inline){opacity:0;pointer-events:none;}" +
    // The open site menu already offers «Chat»: never both at once.
    "body:has(#stznav.open) #stz-chat-launcher:not(.stzc-booking-inline){display:none;}" +
    "#stz-chat-launcher.stzc-booking-inline{position:static;right:auto;bottom:auto;width:auto;height:52px;min-height:52px;margin:var(--space-3,24px) 0 0;padding:0 17px;max-width:100%;}" +
    "#stz-chat-launcher[hidden]{display:none;}" +
    // Phone forms (the product owner, 2026-09-15: registration looked cramped): the floating
    // launcher steps aside while a field is being filled, and a form page keeps
    // room below its last field so the button never sits on top of it.
    "@media (max-width:640px){body:has(:is(input:not([type=checkbox]):not([type=radio]):not([type=hidden]),select,textarea):focus):not(.stzc-chat-open) #stz-chat-launcher:not(.stzc-booking-inline){opacity:0;pointer-events:none;}" +
    "body:has(form :is(input[type=text],input[type=email],input[type=password],input[type=tel],select)) #stzc-page{padding-bottom:72px;}}" +
    // The shared footer keeps its own 76px for the launcher from 601px up
    // (up to 600px the launcher steps aside from any footer line under it
    // instead: the 76px read as an empty band under the footer, demo review
    // 2026-11-15), on every page where the
    // launcher is drawn (the site nav is collapsed/compact, or the page has
    // none; the inline booking variant is exempt). The form room above would
    // stack under it as an empty 72px band, so a form page with that footer
    // drops it. A form page without that footer keeps the room.
    "@media (min-width:601px){body:has(#stz-chat-launcher:not(.stzc-booking-inline)):not(:has(#stznav:not(.stznav--collapsed):not(.stznav--compact))) :is(footer.site,footer.about-footer,.wrap>footer){padding-bottom:76px;}}" +
    "@media (max-width:640px){body:has(form :is(input[type=text],input[type=email],input[type=password],input[type=tel],select)) #stzc-page:has(footer.site,footer.about-footer,>.wrap>footer){padding-bottom:0;}}" +
    "#stz-chat-panel{position:fixed;z-index:9000;top:0;right:0;bottom:0;" +
    "width:var(--stzc-sidebar-w);max-width:100vw;height:100vh;height:100dvh;" +
    "background:var(--panel);border:0;border-left:1px solid var(--border-strong);" +
    "box-shadow:-18px 0 46px -42px rgba(20,24,20,.75);" +
    "display:flex;flex-direction:column;overflow:hidden;}" +
    "#stz-chat-panel[hidden]{display:none;}" +
    ".stzc-head{padding:max(14px,env(safe-area-inset-top)) 20px 10px;display:flex;" +
    "align-items:center;justify-content:space-between;flex-shrink:0;gap:10px;}" +
    ".stzc-title{display:flex;align-items:center;gap:10px;min-width:0;}" +
    ".stzc-title img{width:24px;height:24px;flex-shrink:0;opacity:.88;}" +
    ".stzc-head h2{margin:0;font-size:18px;line-height:1.25;color:var(--text);}" +
    // .stzc-close is the shared close button .hr-x (human-rounds.css).
    ".stzc-close{margin-right:-10px;}" +
    ".stzc-action{border:0;background:none;padding:4px 0;color:var(--accent);font:400 13.5px var(--font);" +
    "cursor:pointer;text-align:left;text-decoration:none;}" +
    ".stzc-action:hover{color:var(--accent-hover);}" +
    ".stzc-action[aria-pressed=true]{color:var(--text);}" +
    ".stzc-action:disabled{opacity:.5;cursor:default;text-decoration:none;}" +
    ".stzc-action[hidden]{display:none;}" +
    ".stzc-head-end{display:flex;align-items:center;gap:10px;flex-shrink:0;min-width:0;}" +
    ".stzc-head-actions{display:flex;align-items:center;gap:12px;min-width:0;}" +
    ".stzc-compose-action{display:inline-flex;align-items:center;gap:6px;min-height:40px;" +
    "padding:0 4px;color:var(--accent);font:400 13px var(--font);cursor:pointer;" +
    "text-decoration:none;white-space:nowrap;flex-shrink:0;}" +
    // `hidden` is the availability contract for optional composer actions.
    // This explicit rule is needed because the base action is inline-flex.
    ".stzc-compose-action[hidden]{display:none;}" +
    ".stzc-compose-action:hover{color:var(--accent-hover);}" +
    ".stzc-wa-glyph{width:17px;height:17px;flex-shrink:0;background:currentColor;" +
    "-webkit-mask:url(/static/icons/chat-whatsapp-sf-regular.svg) center/contain no-repeat;" +
    "mask:url(/static/icons/chat-whatsapp-sf-regular.svg) center/contain no-repeat;}" +
    ".stzc-history{flex:1;min-height:0;overflow-y:auto;padding:4px 20px 18px;}" +
    ".stzc-thread-list{display:flex;flex-direction:column;gap:3px;}" +
    ".stzc-thread{width:100%;border:0;background:none;border-radius:9px;padding:10px 9px;text-align:left;" +
    "cursor:pointer;color:var(--hint-ink,var(--text-2));font:inherit;}" +
    ".stzc-thread:hover,.stzc-thread[aria-current=true]{background:var(--hover-bg);color:var(--hover-ink);}" +
    ".stzc-thread-title{display:block;font-size:14.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}" +
    ".stzc-thread-date{display:block;margin-top:2px;font-size:13px;color:var(--text-3);}" +
    ".stzc-msgs{flex:1;min-height:0;overflow-y:auto;padding:8px 20px 16px;display:flex;" +
    "flex-direction:column;gap:14px;font-size:16px;line-height:1.5;}" +
    /* A short conversation sits ON the composer instead of leaving a screen of
       void between the greeting and the input (the product owner, 2026-08-02). The auto
       margin on a zero-height first flex item eats the free space and
       collapses to 0 as soon as the thread overflows — `justify-content:
       flex-end` would clip the top of a long thread instead. */
    ".stzc-msgs::before{content:'';margin-top:auto;flex-shrink:0;}" +
    ".stzc-msg{max-width:88%;padding:9px 12px;border-radius:12px;}" +
    ".stzc-msg p,.stzc-msg ul{margin:0;padding:0;}" +
    ".stzc-msg ul{padding-left:19px;margin:4px 0;}" +
    ".stzc-msg--user{align-self:flex-end;background:var(--accent-soft);color:var(--text);" +
    "white-space:pre-wrap;}" +
    ".stzc-attach-ico{display:inline-block;width:17px;height:17px;margin-right:6px;vertical-align:-3px;" +
    "background:currentColor;" +
    "-webkit-mask:url(/static/icons/chat-attach-sf-regular.svg) center/contain no-repeat;" +
    "mask:url(/static/icons/chat-attach-sf-regular.svg) center/contain no-repeat;}" +
    /* Sólo el paciente habla dentro de una burbuja. Las respuestas del
       asistente traen títulos, listas de turnos y enlaces: encerrarlas en un
       recuadro gris de media pantalla las hacía leer como un documento
       adentro de una ficha (responsable del producto, 2026-08-02). Van sobre el fondo del panel,
       a todo el ancho, y quien habla se distingue igual — la burbuja verde y
       alineada a la derecha es la de la persona. */
    ".stzc-msg--asst{align-self:stretch;max-width:100%;background:none;color:var(--text);" +
    "padding:0;border-radius:0;}" +
    ".stzc-msg--asst a{color:var(--accent);font-weight:700;" +
    "}" +
    ".stzc-sources{align-self:flex-start;max-width:88%;font-size:13px;line-height:1.45;color:var(--text-3);}" +
    ".stzc-sources summary{cursor:pointer;color:var(--text-3);list-style-position:inside;}" +
    ".stzc-sources[open] summary{margin-bottom:4px;}" +
    ".stzc-sources a{display:block;color:var(--text-2);}" +
    ".stzc-msg--wait{align-self:flex-start;color:var(--text-3);font-style:italic;}" +
    ".stzc-chips{display:flex;flex-wrap:wrap;gap:8px;align-self:flex-start;max-width:100%;}" +
    ".stzc-chip{height:34px;padding:0 13px;border-radius:10px;border:1.5px solid var(--border-strong);" +
    "background:var(--panel);color:var(--accent-hover);font:600 14px var(--font);" +
    "cursor:pointer;white-space:nowrap;}" +
    ".stzc-chip:hover{border-color:var(--hover-bd-strong);background:var(--hover-bg-soft);}" +
    ".stzc-chip:disabled{opacity:.55;cursor:default;}" +
    /* Real appointment destinations live in the answer flow as links, not as
       a detached row of quick-reply buttons.  One day label owns all of its
       times, so the date is never repeated four times down the rail. */
    ".stzc-slot-days{display:flex;flex-direction:column;align-self:stretch;gap:9px;" +
    "margin-top:-4px;font-size:16px;line-height:1.45;}" +
    // El nombre del servicio abre el grupo — negro/oscuro (nunca gris claro,
    // hay pacientes con baja visión) y algo separado del grupo anterior.
    ".stzc-slot-service{margin:6px 0 0;color:var(--text);font-weight:700;}" +
    ".stzc-slot-days>.stzc-slot-service:first-child{margin-top:0;}" +
    ".stzc-slot-day{margin:0;color:var(--text);}" +
    ".stzc-slot-day strong{font-weight:600;}" +
    // El centro es un sub-ítem del día: indentado, una línea por centro (no
    // punteado-junto — ui-rules: una lista es de a un ítem por línea).
    ".stzc-slot-center{margin:0 0 0 16px;color:var(--text);}" +
    ".stzc-slot-time,.stzc-more-times{color:var(--accent);font-weight:700;" +
    "}" +
    ".stzc-more-times{align-self:flex-start;margin-top:1px;font-size:14px;}" +
    ".stzc-login{align-self:flex-start;}" +
    ".stzc-login-btn{height:38px;padding:0 16px;border-radius:var(--btn-radius,10px);border:none;" +
    "background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);font:600 14.5px var(--font);cursor:pointer;}" +
    ".stzc-login-btn:hover{background-image:var(--hover-fill);}" +
    ".stzc-auth{padding:6px 20px 16px;overflow-y:auto;flex:1;min-height:0;}" +
    ".stzc-auth .sc-authform{margin-top:6px;}" +
    ".stzc-form{margin:8px 20px max(16px,env(safe-area-inset-bottom));padding:9px 10px;" +
    "border:1px solid var(--border-strong);border-radius:14px;background:var(--panel);" +
    "display:flex;flex-direction:column;gap:6px;flex-shrink:0;}" +
    ".stzc-form:focus-within{border-color:var(--accent-bd);}" +
    ".stzc-input{display:block;width:100%;min-width:0;min-height:26px;max-height:120px;" +
    "box-sizing:border-box;border:0;outline:0;resize:none;overflow-y:auto;padding:1px 2px;" +
    "font:16px/1.4 var(--font);color:var(--text);background:transparent;}" +
    ".stzc-input::placeholder{color:var(--text-3);opacity:1;}" +
    ".stzc-compose-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;min-width:0;}" +
    ".stzc-compose-status{order:-1;flex:1 0 100%;min-width:0;white-space:normal;overflow-wrap:anywhere;" +
    "font-size:13px;line-height:1.35;color:var(--text-3);}" +
    ".stzc-compose-status:empty{display:none;}" +
    ".stzc-attach,.stzc-mic,.stzc-send{flex-shrink:0;width:38px;height:38px;border-radius:10px;" +
    "border:0;background:transparent;cursor:pointer;display:flex;align-items:center;justify-content:center;}" +
    ".stzc-attach:hover,.stzc-mic:hover{background:var(--hover-bg);}" +
    ".stzc-attach:disabled,.stzc-mic:disabled,.stzc-send:disabled{opacity:.5;cursor:default;}" +
    ".stzc-mic[hidden]{display:none;}" +
    ".stzc-attach img,.stzc-mic img{width:18px;height:18px;opacity:.78;}" +
    ".stzc-mic[aria-pressed=true]{background:var(--accent-soft);box-shadow:inset 0 0 0 1px var(--accent-bd);}" +
    ".stzc-mic[aria-pressed=true] img{opacity:1;filter:var(--icon-accent-filter,none);}" +
    ".stzc-send{margin-left:auto;border-radius:50%;background:var(--btn-bg,var(--accent));}" +
    ".stzc-send:hover{background-image:var(--hover-fill);}" +
    ".stzc-send img{width:19px;height:19px;filter:brightness(0) invert(1);}" +
    /* Public pages normally use viewport breakpoints. With the persistent chat
       open, the remaining page column is the real viewport: mirror the shared
       compact layouts against that container so medium split widths do not
       squeeze desktop grids into narrow tracks. */
    "@container stzc-page (max-width:900px){" +
    "body.stzc-chat-open #stzc-page .centro-head{display:grid;grid-template-columns:minmax(0,1fr);" +
    "grid-template-areas:'top' 'photo' 'bottom';row-gap:18px}" +
    "body.stzc-chat-open #stzc-page .centro-head .ch-photo{margin:0}" +
    "body.stzc-chat-open #stzc-page .centro-head .hero-photo img{aspect-ratio:16/7}" +
    "body.stzc-chat-open #stzc-page .gal-grid{grid-template-columns:repeat(2,minmax(0,1fr))}" +
    "body.stzc-chat-open #stzc-page .svc-major-grid{grid-template-columns:minmax(0,1fr);max-width:720px}" +
    "body.stzc-chat-open #stzc-page .svc-major--prep{grid-column:auto}}" +
    // The home's search examples wrap instead of hiding (demo review
    // 2026-11-15), so a narrow page beside the chat keeps all of them.
    /* listing.js responds to the browser viewport. Once the page is one column
       beside the chat, its own width is the relevant breakpoint, so mirror the
       canonical tablet/mobile layouts as container queries. The mirror
       carries the same appointment-cell wrap as listing.mjs's 901–1120 band
       (00e14ad5): without it a 1440 window put the page at 1000px with the
       doctors grid 50–127px wider than the listing (TASK-28). */
    "@container stzc-page (min-width:901px) and (max-width:1120px){" +
    ".lst-scroll{overflow-x:auto}.lst-table th{padding:8px 7px;font-size:13px}.lst-sort{font-size:13px}" +
    ".lst-table td{padding:11px 7px;font-size:15px}.lst-name{font-size:17px}" +
    ".lst-name--sub,.lst-txt--sub{font-size:15px}.lst-timecell{gap:4px 7px;flex-wrap:wrap;max-width:100%}" +
    ".lst-time{font-size:15px;padding:6px 9px}a.lst-time{padding:6px 9px}" +
    ".lst-more,.lst-orden{font-size:15px}.lst-av{width:34px;height:34px;margin:-2px 0;font-size:13px}" +
    ".lst-person-copy{flex-direction:row;justify-content:flex-start;align-items:baseline;flex-wrap:wrap;column-gap:8px;row-gap:1px}" +
    ".lst-person-specialty{display:inline;font-size:15px}" +
    ".lst-table:has(.lst-row-av) .lst-col-especialidad{display:none}" +
    ".lst-table:has(.lst-row-av),.lst-table:has(.lst-row-av) thead,.lst-table:has(.lst-row-av) tbody{display:block}" +
    ".lst-table:has(.lst-row-av) tr{display:grid;grid-template-columns:var(--lst-av-grid,minmax(210px,1.25fr) minmax(120px,.78fr) minmax(88px,.55fr) minmax(238px,1.45fr));align-items:center;border-bottom:1px solid var(--border-soft)}" +
    ".lst-table:has(.lst-row-av) tbody tr{min-height:56px}" +
    ".lst-table:has(.lst-row-av) tbody td{height:auto;min-width:0}" +
    ".lst-table:has(.lst-row-av) tbody tr:last-child{border-bottom:0}" +
    ".lst-table:has(.lst-row-av) td{border-bottom:0}" +
    /* Same tracks as listing.mjs (2026-11-13): the template is the table's
       own --lst-av-grid (one track per shown column), the cells after the
       name in order. #stzc-page is always a container, so this band applies
       at 901–1120 with the chat closed too. */
    ".lst-table:has(.lst-row-av) .lst-col-nombre{grid-column:1;grid-row:1;align-self:center}" +
    ".lst-time-full{display:none}.lst-time-mobile{display:inline}.lst-timecell{gap:8px}}" +
    "@container stzc-page (max-width:900px){" +
    "body.stzc-chat-open #stzc-page .wrap>[data-listado]{width:100%;margin-left:0;transform:none}" +
    ".lst-tools--filters{flex-wrap:wrap;gap:10px}.lst-tools--filters .lst-searchgroup{flex:1 1 100%}" +
    ".lst-tools--filters .lst-examples{display:flex}" +
    ".lst-selects{display:flex;flex-wrap:nowrap;flex:1 1 100%;gap:6px;min-width:0}" +
    ".lst-selects>.lst-select{flex:1 1 auto}" +
    ".lst-selects.lst-selects--stacked{display:grid;grid-template-columns:minmax(0,1fr)}" +
    ".lst-selects.lst-selects--stacked .lst-select{width:100%!important}" +
    ".lst-tools--filters .lst-select{font-size:16px;padding:10px 26px 10px 8px;background-position:right 7px center;min-width:0}" +
    ".lst-scroll{overflow-x:visible;margin:0}.lst-table,.lst-table tbody,.lst-table tr,.lst-table td{display:block}" +
    ".lst-table:has(.lst-row-av),.lst-table:has(.lst-row-av) tbody{display:block}" +
    ".lst-table:has(.lst-row-av) thead{display:none}" +
    ".lst-table:has(.lst-row-av) tr{display:block;grid-template-columns:none}" +
    ".lst-table:has(.lst-row-av) .lst-col-nombre,.lst-table:has(.lst-row-av) .lst-col-centro," +
    ".lst-table:has(.lst-row-av) .lst-col-orden,.lst-table:has(.lst-row-av) .lst-col-proximo{" +
    "grid-column:auto;grid-row:auto;align-self:auto}" +
    ".lst-table thead{display:none}.lst-table tr{padding:14px 0;border-bottom:1px solid var(--border-soft)}" +
    /* A stacked cell is a block: the desktop viewport's height:56px floor
       (listing.mjs, min-width:901px) would become a cap and spill a closed
       row's «No hay turnos» + its way on onto the next row. */
    ".lst-table tr:last-child{border-bottom:0}.lst-table td{padding:0;border:0;white-space:normal}" +
    ".lst-table tbody td{height:auto}" +
    ".lst-table td.lst-hide-sm,.lst-hide-sm{display:none}" +
    ".lst-table tr:not(.lst-row-av) td.lst-td-link,.lst-table tr:not(.lst-row-av) .lst-linkcell{display:inline}" +
    ".lst-table tr:not(.lst-row-av) td.lst-td-sub{margin-top:0;margin-left:10px;white-space:normal}" +
    ".lst-table tr.lst-row-av{padding-left:0}.lst-table tr.lst-row-av td.lst-col-nombre{padding-left:0}" +
    ".lst-table tr.lst-row-av td:not(.lst-col-nombre){margin-left:56px}" +
    ".lst-person-copy{flex-direction:row;justify-content:flex-start;align-items:baseline;flex-wrap:wrap;column-gap:8px;row-gap:1px}" +
    ".lst-person-specialty{display:inline;font-size:15px;line-height:1.2}" +
    ".lst-table tr.lst-row-av .lst-name{font-size:17px;line-height:1.2}.lst-td-sub{margin-top:3px}.lst-td-proximo{margin-top:2px}" +
    /* Same cell as listing.mjs's phone layout (review D17, ui review
       2026-11-15): «Más horarios» is the outline button on its own line under
       the booking button, never an inline link beside it. */
    ".lst-timecell{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:8px 10px;max-width:100%;white-space:nowrap}" +
    ".lst-timecell>.lst-time{justify-self:start}.lst-timecell>.lst-more{grid-column:1/-1;justify-self:start;margin:0}" +
    ".lst-time{font-size:15px;white-space:nowrap}.lst-time-full{display:none}.lst-time-mobile{display:inline}" +
    ".lst-time-note{white-space:normal}.lst-more{padding:0 12px}.lst-searchbox{gap:11px;padding:15px 16px}" +
    ".lst-examples a:nth-of-type(n+4){display:none}.lst-av{width:44px;height:44px;margin:0;font-size:15px}}" +
    "@container stzc-page (max-width:360px){.lst-person-copy{flex-direction:column;align-items:flex-start;gap:2px}" +
    ".lst-person-specialty{display:block}}" +
    "@media (max-width:700px){html.stzc-chat-open-root,body.stzc-chat-open{overflow:hidden}" +
    "#stz-chat-panel{width:100vw;border-left:0;}" +
    ".stzc-head{padding-left:16px;padding-right:12px}.stzc-history,.stzc-msgs{padding-left:16px;padding-right:16px}" +
    ".stzc-form{margin-left:12px;margin-right:12px}}" +
    "@media (max-width:480px){#stz-chat-launcher{right:12px;bottom:12px;height:44px;padding:0 14px;gap:7px;font-size:14px;}" +
    "#stz-chat-panel{width:100vw;border-left:0;}" +
    ".stzc-head{gap:8px}.stzc-title{gap:7px}.stzc-head h2{font-size:16px}.stzc-head-end{gap:7px}" +
    ".stzc-head-actions{gap:10px}.stzc-action{font-size:13px}.stzc-compose-status{font-size:13px}}";
  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
  function elevarEstilosResponsive() {
    document.head.appendChild(styleEl);
  }
  // The canonical listing stylesheet is injected by the deferred listing.js
  // that runs after this global widget. Reorder once the deferred queue has
  // finished, and again at load as a defensive fallback.
  window.setTimeout(elevarEstilosResponsive, 0);
  window.addEventListener("load", elevarEstilosResponsive, { once: true });

  var reusedLauncher = document.getElementById("stz-chat-launcher");
  var launcher = reusedLauncher || document.createElement("button");
  launcher.id = "stz-chat-launcher";
  launcher.type = "button";
  launcher.setAttribute("aria-label", T("chat.open", null, "Abrir Chat con IA"));
  launcher.setAttribute("aria-controls", "stz-chat-panel");
  launcher.setAttribute("aria-expanded", "false");
  launcher.innerHTML = '<img src="' + ICON_SRC + '" alt="" aria-hidden="true" width="22" height="22">' +
    '<span class="stzc-launcher-label">' + T("nav.chat", null, "Chateá con IA") + "</span>";

  function marcarBoton(abierto) {
    var btns = document.querySelectorAll('#stz-chat-launcher,[data-nav-accion="abrir-chat"]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute("aria-expanded", abierto ? "true" : "false");
      btns[i].setAttribute("aria-controls", "stz-chat-panel");
    }
  }

  // The floating button never sits on top of content (demo review 2026-11-15:
  // at 390px it covered «+4» on the home, «Más horarios», a list row, a
  // heading). Earlier (2026-11-05) it only stepped aside for a fixed list of
  // one-line rows; now any visible content under it counts: a text line (its
  // real line boxes, not the paragraph's full-width box), an image or a
  // control. It fades out at once and comes back only after the scroll has
  // been still for a moment and the spot under it is clear, so it doesn't
  // blink while text streams past. At the very end of a page the footer keeps
  // its own room under its last line, so the button is back there.
  var ASIDE_MEDIA = /^(IMG|SVG|VIDEO|CANVAS|IFRAME|INPUT|SELECT|TEXTAREA|BUTTON|SUMMARY|PICTURE)$/;
  var asideFrame = 0, asideTimer = 0;
  function overlaps(a, b) {
    return a.width > 0 && a.height > 0 && a.left < b.right - 1 && a.right > b.left + 1 &&
      a.top < b.bottom - 1 && a.bottom > b.top + 1;
  }
  function contentUnder(r) {
    var seen = [];
    var range = document.createRange();
    for (var yi = 0; yi < 3; yi++) {
      for (var xi = 0; xi < 4; xi++) {
        var x = r.left + 2 + (r.width - 4) * xi / 3, y = r.top + 2 + (r.height - 4) * yi / 2;
        var els = document.elementsFromPoint ? document.elementsFromPoint(x, y) : [];
        for (var i = 0; i < els.length; i++) {
          var el = els[i];
          if (el === document.documentElement || el === document.body || launcher.contains(el) ||
              seen.indexOf(el) !== -1) continue;
          seen.push(el);
          if (ASIDE_MEDIA.test(el.tagName.toUpperCase())) {
            if (overlaps(el.getBoundingClientRect(), r)) return true;
            continue;
          }
          for (var n = el.firstChild; n; n = n.nextSibling) {
            if (n.nodeType !== 3 || !/\S/.test(n.nodeValue || "")) continue;
            range.selectNodeContents(n);
            var rects = range.getClientRects();
            for (var k = 0; k < rects.length; k++) if (overlaps(rects[k], r)) return true;
          }
        }
      }
    }
    return false;
  }
  function launcherAside() {
    asideFrame = 0;
    var r = launcher.getBoundingClientRect();
    var hit = r.height > 0 && !launcher.classList.contains("stzc-booking-inline") &&
      window.getComputedStyle(launcher).position === "fixed" && contentUnder(r);
    if (hit) {
      window.clearTimeout(asideTimer);
      asideTimer = 0;
      launcher.classList.add("stzc-launcher--aside");
    } else if (launcher.classList.contains("stzc-launcher--aside") && !asideTimer) {
      // Measured while hidden (opacity 0 keeps the box): re-check once the
      // scroll has settled, then show.
      asideTimer = window.setTimeout(function () {
        asideTimer = 0;
        var rr = launcher.getBoundingClientRect();
        if (!contentUnder(rr)) launcher.classList.remove("stzc-launcher--aside");
      }, 350);
    }
  }
  function queueLauncherAside() {
    if (!asideFrame) asideFrame = window.requestAnimationFrame(launcherAside);
  }
  window.addEventListener("scroll", queueLauncherAside, { passive: true, capture: true });
  window.addEventListener("resize", queueLauncherAside);
  window.setTimeout(queueLauncherAside, 0);
  document.addEventListener("toggle", queueLauncherAside, true);

  var panel = document.createElement("div");
  panel.id = "stz-chat-panel";
  panel.hidden = true;
  panel.innerHTML =
    '<div class="stzc-head"><div class="stzc-title">' +
    '<img src="' + ICON_SRC + '" alt="" aria-hidden="true"><h2>' +
    T("chat.title", null, "Chat con IA") + '</h2></div>' +
    '<div class="stzc-head-end">' +
    '<div class="stzc-head-actions" aria-label="Chats">' +
    '<button type="button" class="stzc-action" id="stzc-new">' + NUEVO_CHAT_LABEL + '</button>' +
    '<button type="button" class="stzc-action" id="stzc-history-btn" aria-pressed="false">' +
    HISTORIAL_LABEL + '</button></div>' +
    '<button type="button" class="stzc-close hr-x" aria-label="' +
    T("chat.close", null, "Cerrar Chat con IA") + '"></button></div></div>' +
    '<p style="font-size:16px;color:var(--text);margin:8px 20px;">' + T("ai.notice.chat", null, "Este chat usa inteligencia artificial. Puede equivocarse. Los turnos los confirma el equipo de salud.") + '</p>' +
    '<section class="stzc-history" id="stzc-history" aria-label="' +
    T("chat.history_aria", null, "Historial de chats") + '" hidden>' +
    '<div class="stzc-thread-list" id="stzc-thread-list"></div></section>' +
    '<div class="stzc-msgs" id="stzc-msgs"></div>' +
    // The form starts EMPTY: chat-ui.mjs's crearComposer() (below, once the
    // panel is in the DOM) fills it — same shared composer entrevista.js
    // uses (textarea autogrow + attach + mic), so this markup lives in ONE
    // place instead of being duplicated per chat surface.
    '<form class="stzc-form" id="stzc-form" autocomplete="off"></form>';

  // The page needs its own layout box so reducing its width causes normal
  // responsive reflow. Body padding only moved the viewport-sized page and
  // clipped wide content; this wrapper is the actual remaining page column.
  var page = document.createElement("div");
  page.id = "stzc-page";
  var existing = Array.prototype.slice.call(document.body.childNodes);
  var focused = document.activeElement;
  var selection = focused && typeof focused.selectionStart === "number"
    ? [focused.selectionStart, focused.selectionEnd, focused.selectionDirection] : null;
  document.body.insertBefore(page, document.body.firstChild);
  existing.forEach(function (node) {
    if (node.nodeType === 1) {
      if (/^(SCRIPT|STYLE|LINK)$/.test(node.tagName)) return;
      if (node.id === "stz-chat-launcher" || node.id === "stz-chat-panel" ||
          node.id === "chat-boot-error") return;
    }
    // A delayed mount can arrive while the person is typing. Atomic moves
    // preserve focus; appendChild removes/reinserts the focused subtree.
    // Keep comments on the broadly supported path; they carry no focus state.
    if (typeof page.moveBefore === "function" && (node.nodeType === 1 || node.nodeType === 3)) {
      page.moveBefore(node, null);
    } else {
      page.appendChild(node);
    }
  });
  // Older browsers lack atomic moves. Restore only the page control that
  // this wrap blurred, without scrolling or changing the person's value.
  if (focused && page.contains(focused) && document.activeElement !== focused) {
    focused.focus({ preventScroll: true });
    if (selection) focused.setSelectionRange(selection[0], selection[1], selection[2]);
  }

  // The shared bootstrap may already have placed the reused launcher in the
  // booking page's normal-flow anchor. Do not reinsert it into <body>: that
  // would restore the fixed overlap after a delayed native module resolves.
  if (!reusedLauncher) document.body.appendChild(launcher);
  document.body.appendChild(panel);
  queueLauncherAside();

  var msgsEl = panel.querySelector("#stzc-msgs");
  var formEl = panel.querySelector("#stzc-form");
  var closeBtn = panel.querySelector(".stzc-close");
  var newBtn = panel.querySelector("#stzc-new");
  var historyBtn = panel.querySelector("#stzc-history-btn");
  var headerActionsEl = panel.querySelector(".stzc-head-actions");
  var chatTitleEl = panel.querySelector(".stzc-title h2");
  var historyEl = panel.querySelector("#stzc-history");
  var threadListEl = panel.querySelector("#stzc-thread-list");

  // platform/chat-ui.mjs failed to load: degraded boot. The launcher and
  // panel still mount (the person still finds the chat entry point and reads
  // what happened), but there is no composer to type into — the panel
  // carries one actionable alert line instead. Nothing below this branch
  // runs, so no callback of a half-booted widget can fire later.
  if (!ChatUI) {
    formEl.hidden = true;
    var avisoCarga = document.createElement("p");
    avisoCarga.setAttribute("role", "alert");
    avisoCarga.style.cssText = "font-size:16px;color:var(--text-2);margin:14px 20px 8px;";
    avisoCarga.textContent = T("chat.connect_error", null, "No pudimos conectar. Probá de nuevo en un rato.");
    msgsEl.appendChild(avisoCarga);
    var retryCarga = document.createElement("button");
    retryCarga.type = "button";
    retryCarga.className = "stzc-login-btn";
    retryCarga.textContent = T("chat.open", null, "Abrir Chat con IA");
    retryCarga.style.margin = "0 20px 14px";
    retryCarga.addEventListener("click", function () { window.location.reload(); });
    msgsEl.appendChild(retryCarga);
    // A failed shared module must not leave an apparently-open panel with the
    // launcher still saying collapsed (or a phone page scrolling behind it).
    // Keep the same open/close state contract as the working widget, even
    // though its composer cannot be mounted.
    function abrirDegradado() {
      panel.hidden = false;
      opened = true;
      document.body.classList.add("stzc-chat-open");
      document.documentElement.classList.add("stzc-chat-open-root");
      marcarBoton(true);
    }
    function cerrarDegradado() {
      panel.hidden = true;
      opened = false;
      document.body.classList.remove("stzc-chat-open");
      document.documentElement.classList.remove("stzc-chat-open-root");
      marcarBoton(false);
    }
    closeBtn.addEventListener("click", cerrarDegradado);
    if (!reusedLauncher) launcher.addEventListener("click", abrirDegradado);
    widgetListo = {
      abrir: abrirDegradado,
      cerrar: cerrarDegradado,
    };
    if (pendingOpen) abrirDegradado();
    return;
  }

  // Shared component (static/platform/chat-ui.mjs) — the SAME bubble list +
  // composer entrevista.js uses. `onEnviar` mirrors the old inline submit/attach
  // handlers exactly: text goes through enviarMensaje's own bubble, an
  // attachment gets its paperclip + <filename> bubble here first.
  var lista = ChatUI.crearLista(msgsEl, { prefix: "stzc" });
  var composer = ChatUI.crearComposer(formEl, {
    prefix: "stzc",
    placeholder: T("chat.placeholder", null, "Escribí tu consulta…"),
    maxLength: 2000,
    rows: 1,
    minAlto: 26,
    accept: "image/*,application/pdf",
    attachLabel: T("chat.attach_label", null, "Adjuntar la foto o el PDF de una orden médica"),
    attachTitle: T("chat.attach_title", null, "Subí una foto o PDF de tu orden (o pegala con Ctrl/⌘V)"),
    secondaryAction: {
      id: "stzc-wa",
      className: "stzc-compose-action stzc-wa",
      iconClass: "stzc-wa-glyph",
      label: T("chat.whatsapp_switch", null, "Pasar a WhatsApp"),
      title: T("chat.whatsapp_tooltip", null, "Pasar esta conversación a WhatsApp"),
    },
    onEnviar: function (texto, adjunto) {
      if (adjunto) addMsg(adjunto.filename || T("chat.attachment_fallback", null, "archivo adjunto"), "user", null, false, true);
      enviarMensaje(texto, adjunto);
    },
  });
  var inputEl = composer.els.input;
  var sendBtn = composer.els.sendBtn;
  var attachBtn = composer.els.attachBtn;
  var micBtn = composer.els.micBtn;
  var composeStatusEl = composer.els.statusEl;
  var fileEl = composer.els.fileEl;
  var waEl = composer.els.secondaryActionEl;

  var opened = false;
  var welcomed = false;
  var conversations = [];
  var activeId = "";
  var viewingHistory = false;

  function scrollAlFinal() {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        msgsEl.scrollTop = msgsEl.scrollHeight;
      });
    });
  }

  function actualizarLayout() {
    // nav.js measures the space that is really available instead of using a
    // hard breakpoint. Notify it once immediately and once after the width
    // transition so the menu collapses/expands against the new page column.
    window.dispatchEvent(new Event("resize"));
    window.setTimeout(function () {
      window.dispatchEvent(new Event("resize"));
    }, 180);
  }

  function guardarEstado() {
    try {
      var active = conversacionesActiva();
      if (active) {
        active.welcomed = welcomed;
        active.messages = msgsEl.innerHTML;
        active.whatsapp = waEl.getAttribute("href") || "";
      }
      sessionStorage.setItem(STATE_KEY, JSON.stringify({
        open: opened,
        activeId: activeId,
        conversations: conversations.slice(0, 12),
      }));
    } catch (_) {}
  }

  function leerEstado() {
    try {
      var raw = sessionStorage.getItem(STATE_KEY);
      if (!raw || raw.length > 100000) return null;
      var state = JSON.parse(raw);
      return state && typeof state === "object" ? state : null;
    } catch (_) {
      return null;
    }
  }

  function idConversacion() {
    return "chat-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);
  }

  function conversacionesActiva() {
    return conversations.find(function (item) { return item.id === activeId; }) || null;
  }

  function crearConversacion() {
    var item = {
      id: idConversacion(),
      title: NUEVO_CHAT_LABEL,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      welcomed: false,
      hasUserMessage: false,
      messages: "",
      whatsapp: "",
      hidratado: false,
    };
    conversations.unshift(item);
    activeId = item.id;
    return item;
  }

  function cargarConversacion(item) {
    if (!item) return;
    activeId = item.id;
    welcomed = !!item.welcomed;
    msgsEl.innerHTML = item.messages || "";
    ponerWhatsapp(item.whatsapp, true);
    mostrarMensajes();
    actualizarAcciones();
    scrollAlFinal();
    guardarEstado();
  }

  function fechaConversacion(ts) {
    try {
      if (Dates && Dates.relativo) {
        var relativa = Dates.relativo(new Date(ts).toISOString());
        if (relativa) return relativa.charAt(0).toUpperCase() + relativa.slice(1);
      }
      return String(ts || "");
    } catch (_) {
      return "";
    }
  }

  function renderConversaciones() {
    threadListEl.innerHTML = "";
    conversations
      .slice()
      .filter(function (item) { return item.hasUserMessage; })
      .sort(function (a, b) { return (b.updatedAt || 0) - (a.updatedAt || 0); })
      .forEach(function (item) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "stzc-thread";
        btn.setAttribute("data-conversation-id", item.id);
        if (item.id === activeId) btn.setAttribute("aria-current", "true");
        var title = document.createElement("span");
        title.className = "stzc-thread-title";
        title.textContent = item.title || NUEVO_CHAT_LABEL;
        var date = document.createElement("span");
        date.className = "stzc-thread-date";
        date.textContent = fechaConversacion(item.updatedAt || item.createdAt);
        btn.appendChild(title);
        btn.appendChild(date);
        threadListEl.appendChild(btn);
      });
  }

  function mostrarHistorial() {
    if (historyBtn.hidden) return;
    guardarEstado();
    viewingHistory = true;
    datesReady.then(renderConversaciones);
    historyEl.hidden = false;
    msgsEl.hidden = true;
    formEl.hidden = true;
    historyBtn.setAttribute("aria-pressed", "true");
  }

  function mostrarMensajes() {
    viewingHistory = false;
    historyEl.hidden = true;
    msgsEl.hidden = false;
    formEl.hidden = false;
    historyBtn.setAttribute("aria-pressed", "false");
  }

  // ---- pasar la charla a WhatsApp ---------------------------------------------
  // El link está junto a Adjuntar y Dictar: la salida del chat queda al lado
  // de las demás acciones de composición, mientras la barra superior queda
  // exclusivamente para cambiar o recorrer conversaciones. Sin línea
  // configurada no hay link: nunca se pinta un control muerto.
  function ponerWhatsapp(link, esRestaurado) {
    link = String(link || "");
    // Una conversación guardada de antes de esta ronda tiene HTML acá, no un
    // enlace: se descarta y el panel vuelve a pedir el suyo al abrirse.
    if (link.indexOf("https://wa.me/") !== 0) {
      if (!esRestaurado) return;
      link = "";
    }
    if (link) {
      waEl.href = link;
      waEl.hidden = false;
    } else {
      waEl.removeAttribute("href");
      waEl.hidden = true;
    }
    actualizarAcciones();
  }

  function pedirEnlaceWhatsapp() {
    if (waEl.getAttribute("href")) return; // ya hay uno (con código o sin él)
    window
      .fetch("/api/chat/whatsapp", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        // El enlace con código pudo llegar mientras esto viajaba: no lo pisa.
        if (data && data.link && !waEl.getAttribute("href")) ponerWhatsapp(data.link);
      })
      .catch(function () {});
  }

  function actualizarAcciones() {
    var active = conversacionesActiva();
    var puedeCrear = !!(active && active.hasUserMessage);
    var hayHistorial = conversations.length > 1 &&
      conversations.some(function (item) { return item.hasUserMessage; });
    newBtn.hidden = !puedeCrear;
    historyBtn.hidden = !hayHistorial;
    headerActionsEl.hidden = newBtn.hidden && historyBtn.hidden;
    // Con dos conversaciones, el título ya no aporta orientación: queda el
    // destello y la barra se dedica a las dos acciones de conversación.
    chatTitleEl.hidden = hayHistorial;
    if (!hayHistorial && viewingHistory) mostrarMensajes();
  }

  // «Nuevo chat» (responsable del producto, 2026-09-11): el botón cierra la sesión en el servidor
  // también — POST /api/chat/new (fire-and-forget: la identidad la saca el
  // servidor de la cookie, y al anónimo le contesta {"ok":false} sin error) —
  // porque la hidratación fusiona el hilo reciente de TODA la cuenta y sin
  // ese corte la conversación recién creada recibía la charla vieja de
  // vuelta. La conversación que abre queda `hidratado: true`: nunca pide
  // /api/chat/history, arranca vacía de verdad (el saludo lo pone abrir()).
  function avisarSesionNueva() {
    window
      .fetch("/api/chat/new", { method: "POST", credentials: "same-origin" })
      .catch(function () {});
  }

  function nuevaConversacion() {
    guardarEstado();
    var item = conversations.find(function (candidate) {
      return !candidate.hasUserMessage;
    }) || crearConversacion();
    item.hidratado = true;
    avisarSesionNueva();
    cargarConversacion(item);
    abrir(false);
    inputEl.focus();
  }

  function separarTrasPausa() {
    var active = conversacionesActiva();
    if (!active || !active.hasUserMessage) return;
    if (Date.now() - Number(active.updatedAt || 0) < AUTO_SPLIT_MS) return;
    cargarConversacion(crearConversacion());
  }

  // ---- markdown liviano ('**negrita**', links internos): chat-ui.mjs -------
  // (extraído 2026-08-01 a chat-ui.js, hoy platform/chat-ui.mjs — ver
  // docs/architecture/chat-y-whatsapp.md "ONE shared chat component": el
  // mismo renderMini/textoConEnlaces que usa entrevista.js. `lista` (creada
  // más abajo junto al resto de refs del panel) es el mismo componente el
  // que dibuja burbujas/typing/opciones acá.

  function addMsg(texto, quien, enlaces, transient, adjunto) {
    var d = lista.addBubble(quien, texto, { enlaces: enlaces, adjunto: !!adjunto });
    if (!transient) {
      var active = conversacionesActiva();
      if (active) active.updatedAt = Date.now();
      if (quien === "user") {
        if (active) active.hasUserMessage = true;
        actualizarAcciones();
      }
      if (quien === "user" && !adjunto) {
        if (active && (active.title === NUEVO_CHAT_LABEL || active.title === "Nuevo chat" || active.title === "Nueva conversación")) {
          active.title = texto.replace(/\s+/g, " ").trim().slice(0, 54) || active.title;
        }
      }
      guardarEstado();
    }
    return d;
  }

  function aplicarTituloBackend(titulo, conversationId) {
    var limpio = String(titulo || "").replace(/\s+/g, " ").trim().slice(0, 54);
    if (!limpio) return;
    var item = conversations.find(function (candidate) {
      return candidate.id === conversationId;
    });
    if (!item) return;
    item.title = limpio;
    item.updatedAt = Date.now();
    guardarEstado();
    if (viewingHistory) datesReady.then(renderConversaciones);
  }

  // addFuentes/addChipRow now delegate to the shared component (chat-ui.mjs's
  // `lista`, same instance the interview chat uses) — identical DOM/CSS
  // classes as before (prefix "stzc"), just built in one place.
  function addFuentes(fuentes) {
    var wrap = lista.addFuentes(fuentes);
    if (wrap) guardarEstado();
  }

  function addChipRow(etiquetas, onPick) {
    return lista.addOpciones(etiquetas, onPick);
  }

  function addTurnosEnRespuesta(turnos, masHref) {
    // Agrupa servicio -> día -> centro: el día se dice UNA sola vez (no una
    // vez por centro), y el servicio queda arriba de todo — sin eso, un
    // paciente que preguntó por traumatología no puede saber si los horarios
    // que le mostramos son de traumatología general o del pie diabético
    // (responsable del producto, 2026-08-06). El orden de los días es el que mandó el servidor
    // (ya viene ordenado cronológicamente) — acá sólo se agrupa, no se
    // reordena.
    var servicios = [];
    (turnos || []).forEach(function (turno) {
      var nombre = String(turno.nombre || "");
      var srv = servicios.find(function (item) { return item.nombre === nombre; });
      if (!srv) {
        srv = { nombre: nombre, href: String(turno.servicio_href || ""), dias: [] };
        servicios.push(srv);
      }
      var dia = String(turno.dia || "");
      var diaGrupo = srv.dias.find(function (item) { return item.dia === dia; });
      if (!diaGrupo) {
        diaGrupo = { dia: dia, fecha: turno.fecha_etiqueta || dia, centros: [] };
        srv.dias.push(diaGrupo);
      }
      var centro = String(turno.centro || "");
      var centroGrupo = diaGrupo.centros.find(function (item) { return item.centro === centro; });
      if (!centroGrupo) {
        centroGrupo = { centro: centro, turnos: [] };
        diaGrupo.centros.push(centroGrupo);
      }
      centroGrupo.turnos.push(turno);
    });
    if (!servicios.length) return null;
    var wrap = document.createElement("div");
    wrap.className = "stzc-slot-days";
    servicios.forEach(function (srv) {
      // El nombre del servicio se muestra SIEMPRE, aunque haya uno solo —
      // es lo que aclara a qué especialidad pertenecen los horarios de abajo.
      var esLink = String(srv.href || "").charAt(0) === "/";
      var nombreEl = document.createElement(esLink ? "a" : "strong");
      nombreEl.className = esLink ? "stzc-slot-service stzc-slot-time" : "stzc-slot-service";
      if (esLink) nombreEl.href = srv.href;
      nombreEl.textContent = srv.nombre;
      wrap.appendChild(nombreEl);

      srv.dias.forEach(function (diaGrupo) {
        var diaP = document.createElement("p");
        diaP.className = "stzc-slot-day";
        var fecha = document.createElement("strong");
        fecha.textContent = diaGrupo.fecha;
        diaP.appendChild(fecha);
        wrap.appendChild(diaP);

        diaGrupo.centros.forEach(function (centroGrupo) {
          var linea = document.createElement("p");
          linea.className = "stzc-slot-center";
          var plantilla = T("chat.slot_center_times",
            { centro: centroGrupo.centro }, "{centro} a las {horas}");
          var partes = plantilla.split("{horas}");
          linea.appendChild(document.createTextNode(partes[0]));
          centroGrupo.turnos.forEach(function (turno, index) {
            if (index) linea.appendChild(document.createTextNode(
              index === centroGrupo.turnos.length - 1
                ? T("chat.slot_join_and", null, " y ") : ", "));
            var link = document.createElement("a");
            link.className = "stzc-slot-time";
            link.href = turno.href;
            link.textContent = turno.hora;
            link.setAttribute("aria-label", T("chat.slot_aria",
              { fecha: diaGrupo.fecha, hora: turno.hora }, "Sacar turno el {fecha} a las {hora}"));
            linea.appendChild(link);
          });
          if (partes[1]) linea.appendChild(document.createTextNode(partes[1]));
          wrap.appendChild(linea);
        });
      });
    });
    if (String(masHref || "").charAt(0) === "/") {
      var mas = document.createElement("a");
      mas.className = "stzc-more-times";
      mas.href = masHref;
      mas.textContent = T("chat.more_times", null, "Otros horarios");
      wrap.appendChild(mas);
    }
    msgsEl.appendChild(wrap);
    scrollAlFinal();
    guardarEstado();
    return wrap;
  }

  function addStarterChips() {
    var row = document.createElement("div");
    row.className = "stzc-chips";
    [
      [T("chat.chip_hours", null, "Ver próximos horarios"), "hours"],
      [T("chat.chip_upload", null, "Subir una orden"), "upload"],
    ].forEach(function (par) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "stzc-chip";
      b.textContent = par[0];
      b.setAttribute("data-chat-action", par[1]);
      row.appendChild(b);
    });
    msgsEl.appendChild(row);
    scrollAlFinal();
    guardarEstado();
  }

  // ---- inicio de sesión embebido (login:true en la respuesta) ---------------
  // platform/account-form.mjs loads lazily — when login opens, not when the
  // widget loads — through the outer importStatic(). Without a manifest entry
  // it rejects and the existing account-link notice appears.
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

  function addLoginPrompt() {
    var wrap = document.createElement("div");
    wrap.className = "stzc-login";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "stzc-login-btn";
    btn.textContent = T("chat.sign_in", null, "Iniciar sesión");
    btn.setAttribute("data-chat-action", "login");
    wrap.appendChild(btn);
    msgsEl.appendChild(wrap);
    scrollAlFinal();
    guardarEstado();
  }

  function addUploadRetryPrompt() {
    var wrap = document.createElement("div");
    wrap.className = "stzc-login";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "stzc-login-btn";
    btn.textContent = T("chat.retry_upload", null, "Subir otra orden");
    btn.setAttribute("data-chat-action", "retry-upload");
    wrap.appendChild(btn);
    msgsEl.appendChild(wrap);
    scrollAlFinal();
    guardarEstado();
  }

  function abrirAuth() {
    panel.classList.add("stzc-panel--auth");
    formEl.hidden = true;
    var authWrap = document.createElement("div");
    authWrap.className = "stzc-auth";
    authWrap.id = "stzc-auth";
    panel.insertBefore(authWrap, formEl);

    loadAccountForm().then(function (Account) {
      Account.renderAuth(authWrap, {
        defaultMode: "login",
        heading: false,
        onDone: function () {
          authWrap.remove();
          panel.classList.remove("stzc-panel--auth");
          formEl.hidden = false;
          addMsg(T("chat.auth_done", null, "Listo, ya estás con tu cuenta. Sigamos."), "asst");
          inputEl.focus();
        },
      });
    }).catch(function () {
      authWrap.innerHTML = '<p style="font-size:14px;color:var(--text-2);">' +
        T("chat.auth_unavailable_pre", null, "No pudimos cargar el inicio de sesión acá. Podés entrar desde ") +
        '<a class="stz-link" href="/account">' +
        T("chat.auth_unavailable_link", null, "Mi cuenta") + '</a>.</p>';
    });
  }

  // X-Turnos-Client: the random id of THIS browser, the same `stz_client_id`
  // modules/turnos/wz-upload.mjs sends with the order reader. Behind the
  // municipal gateway every patient shares one IP, so the chat's hourly limit
  // counts per browser (chat_api._limit_key), not per hospital. No storage:
  // one id per page load.
  var memoryClientId = null;
  function clientId() {
    var id = null;
    try { id = localStorage.getItem("stz_client_id"); } catch (e) { id = null; }
    if (id && /^[A-Za-z0-9_-]{16,64}$/.test(id)) return id;
    var bytes = new Uint8Array(18);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    id = btoa(String.fromCharCode.apply(null, bytes)).replace(/\+/g, "-").replace(/\//g, "_");
    try { localStorage.setItem("stz_client_id", id); } catch (e) { memoryClientId = memoryClientId || id; id = memoryClientId; }
    return id;
  }

  // ---- envío -----------------------------------------------------------------
  // Streams the answer instead of waiting for it whole (the product owner, 2026-08-02):
  // POST /api/chat/stream reveals the ALREADY-FINISHED text in small pieces
  // (`event: chunk`) — the model's raw output still goes through every
  // safety pass server-side before any of it is sent, see chat_api.py's
  // `_message_stream` — then closes with `event: done` carrying the exact
  // body /api/chat used to send in one shot (opciones/whatsapp/turnos/etc).
  // Product owner, 2026-08-04: while the turn is still being computed server-side
  // (can take 60-120s — an agenda search, the model), the server now sends
  // periodic `event: estado` pieces with a staged status label; the
  // placeholder bubble shows those until the first real `chunk` arrives. If
  // the stream still dies mid-way (a proxy/Cloudflare cut it, or the fetch
  // itself failed after its own retries), ONE silent retry against the
  // plain `POST /api/chat` (same body, full JSON reply) runs before ever
  // showing the "no pudimos conectar" message — see `intentarFallback`.
  function enviarMensaje(texto, adjunto) {
    texto = (texto || "").trim();
    if (!texto && !adjunto) return;
    if (texto && !adjunto) addMsg(texto, "user");
    inputEl.disabled = true;
    sendBtn.disabled = true;
    attachBtn.disabled = true;
    micBtn.disabled = true;
    newBtn.disabled = true;
    historyBtn.disabled = true;
    var esperando = addMsg(T("chat.connecting", null, "Conectando…"), "wait", null, true);

    var conversationId = activeId;
    var chatHeaders = { "Content-Type": "application/json", "X-Turnos-Client": clientId() };
    var body = { mensaje: texto, session_id: conversationId };
    if (adjunto) body.adjunto = adjunto;

    function terminar(cuerpo) {
      var turnos = Array.isArray(cuerpo.turnos) ? cuerpo.turnos : [];
      // El texto llega SIN la lista de horarios: el servidor la saca una sola
      // vez (chat_agent._strip_slot_restatements) para todos los canales, y
      // acá se pinta como enlaces reales.
      var respuesta = cuerpo.respuesta || "…";
      // Última pasada con el texto YA COMPLETO: markdown + enlaces reales,
      // reemplazando el texto plano que se fue revelando de a partes.
      lista.updateBubble(
        esperando, "asst",
        respuesta || T("chat.default_answer", null, "Estos son los primeros turnos disponibles:"),
        { enlaces: cuerpo.enlaces }
      );
      var active = conversacionesActiva();
      if (active) active.updatedAt = Date.now();
      if (turnos.length) addTurnosEnRespuesta(turnos, cuerpo.mas_horarios_href);
      aplicarTituloBackend(cuerpo.titulo, conversationId);
      addFuentes(cuerpo.fuentes);
      // Tappable options under the assistant's message (shared component,
      // same [opciones: ...] marker the interview chat and WhatsApp use) —
      // tapping one sends it as the next message, same as typing it.
      if (!turnos.length && cuerpo.opciones && cuerpo.opciones.length) {
        addChipRow(cuerpo.opciones, function (etiqueta) { enviarMensaje(etiqueta); });
      }
      if (cuerpo.subir_otra_orden) addUploadRetryPrompt();
      if (cuerpo.login) addLoginPrompt();
      // Ya hay historial: el enlace pasa a ser el que lleva el código de
      // traspaso, que es lo que se lo lleva a WhatsApp. Reemplaza al enlace
      // sin código que el control mostraba desde que se abrió el panel.
      if (cuerpo.whatsapp && cuerpo.whatsapp.link) ponerWhatsapp(cuerpo.whatsapp.link);
      guardarEstado();
    }

    // Parses `text/event-stream` framing off the raw byte stream as it
    // arrives (fetch, not EventSource — EventSource can't POST a body) and
    // dispatches each block as it completes. Returns whether a `done` event
    // was actually seen (a dropped connection mid-stream resolves false).
    function leerEventos(reader) {
      var decoder = new TextDecoder();
      var buffer = "";
      var acumulado = "";
      var terminado = false;

      function procesarBloque(bloque) {
        var evento = "", crudo = "";
        bloque.split("\n").forEach(function (linea) {
          if (linea.indexOf("event: ") === 0) evento = linea.slice(7);
          else if (linea.indexOf("data: ") === 0) crudo = linea.slice(6);
        });
        if (!crudo) return;
        var datos;
        try { datos = JSON.parse(crudo); } catch (e) { return; }
        if (evento === "estado") {
          // Sólo mientras no llegó texto real: una vez que el primer chunk
          // aparece, el contenido que se va revelando ES el indicador — un
          // estado tardío (no debería pasar) no debe pisar la respuesta.
          if (!acumulado) lista.updateBubble(esperando, "wait", datos.texto || T("chat.typing", null, "Escribiendo…"));
        } else if (evento === "chunk") {
          if (!acumulado) lista.updateBubble(esperando, "wait", T("chat.typing", null, "Escribiendo…"));
          acumulado += datos.texto || "";
          // Format as it types (the product owner, 2026-08-06): `**` was staying literal
          // on screen until the WHOLE answer finished, then jumping to
          // bold all at once. chat-ui.mjs's renderMini/negrita already
          // treats a trailing unpaired `**` as an open bold run, so
          // re-rendering the growing text on every chunk paints it live —
          // no `enlaces` yet (those need the finished text), the final
          // pass below adds them and is otherwise byte-identical to this.
          lista.updateBubble(esperando, "asst", acumulado);
        } else if (evento === "done") {
          terminado = true;
          terminar(datos);
        }
      }

      function paso() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buffer += decoder.decode(r.value, { stream: true });
          var bloques = buffer.split("\n\n");
          buffer = bloques.pop();
          bloques.forEach(function (b) { if (b) procesarBloque(b); });
          if (terminado) return;
          return paso();
        });
      }
      return paso().then(function () { return terminado; });
    }

    function pedirStream(intento) {
      return fetch("/api/chat/stream", {
        method: "POST",
        headers: chatHeaders,
        body: JSON.stringify(body),
      }).catch(function (err) {
        if (intento >= 2) throw err;
        esperando.textContent = T("chat.reconnecting", null, "Reconectando…");
        return new Promise(function (resolve) {
          setTimeout(resolve, 800 * (intento + 1));
        }).then(function () { return pedirStream(intento + 1); });
      });
    }

    // Silent fallback (the product owner, 2026-08-04): the stream can still die mid-way
    // (pedirStream's own retries exhausted, or leerEventos ending without a
    // `done`) — try the plain, one-shot POST /api/chat ONCE before giving
    // up. Same body; the JSON it returns carries the same shape `done`
    // does, so `terminar` renders it exactly as if it had streamed
    // (opciones/login/whatsapp/turnos all still work). Only if THIS also
    // fails does the person see "no pudimos conectar".
    function intentarFallback() {
      esperando.textContent = T("chat.reconnecting", null, "Reconectando…");
      return fetch("/api/chat", {
        method: "POST",
        headers: chatHeaders,
        body: JSON.stringify(body),
      })
        .then(function (r) {
          return r.json().then(function (cuerpo) {
            if (!r.ok) throw new Error("fallback_no_ok");
            terminar(cuerpo);
            return true;
          });
        })
        .catch(function () { return false; });
    }

    pedirStream(0)
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (j) {
            esperando.remove();
            addMsg(
              (j && j.mensaje) || T("chat.process_error", null, "No pudimos procesar el mensaje. Probá de nuevo."),
              "asst"
            );
          });
        }
        return leerEventos(r.body.getReader()).then(function (terminado) {
          if (terminado) return;
          return intentarFallback().then(function (ok) {
            if (!ok) {
              esperando.remove();
              addMsg(T("chat.connect_error", null, "No pudimos conectar. Probá de nuevo en un rato."), "asst");
            }
          });
        });
      })
      .catch(function () {
        return intentarFallback().then(function (ok) {
          if (!ok) {
            esperando.remove();
            addMsg(T("chat.connect_error", null, "No pudimos conectar. Probá de nuevo en un rato."), "asst");
          }
        });
      })
      .finally(function () {
        inputEl.disabled = false;
        sendBtn.disabled = false;
        attachBtn.disabled = false;
        micBtn.disabled = false;
        newBtn.disabled = false;
        historyBtn.disabled = false;
        inputEl.focus();
      });
  }

  // ---- adjuntar orden + dictado por voz: chat-ui.mjs's crearComposer() -------
  // (extraído 2026-08-01 — textarea autogrow, adjuntar/pegar imagen, y el
  // MediaRecorder -> /api/chat/transcribe de más arriba vivían acá
  // duplicados con entrevista.js; ahora es el MISMO composer, ver `composer`
  // más arriba junto al resto de refs del panel.)

  // ---- barra lateral persistente ----------------------------------------------

  function saludarSiHaceFalta() {
    if (welcomed) return;
    welcomed = true;
    var chatBrand = document.documentElement.getAttribute("data-theme") === "pinamar"
      ? "Turnos de salud pública en Pinamar" : "Human Rounds";
    addMsg(
      T("chat.greeting", { brand: chatBrand },
        "**Hola, soy el chat con IA de {brand}.**\n\nPodés:\n* buscar un estudio o trámite\n* encontrar un profesional\n* sacar un turno\n* subir una orden médica"),
      "asst"
    );
    addStarterChips();
  }

  // El saludo de una conversación nueva se pinta SIN esperar este fetch.
  // Después traemos al panel la charla que la persona ya tuvo por WhatsApp
  // (the product owner,
  // 2026-08-03: "una sola conversación por paciente entre la web y
  // WhatsApp"): un panel RECIÉN abierto (sin mensaje propio todavía) pide
  // una vez el historial fusionado — si ya vinculó su número, esos turnos
  // aparecen acá antes de que escriba nada. Sin sesión iniciada el servidor
  // devuelve una lista vacía (GET /api/chat/history saca la identidad de la
  // sesión, nunca de lo que manda el cliente). `hidratado` hace esto
  // idempotente: una conversación real nunca se vuelve a pedir ni se duplica
  // en una apertura posterior. Si la persona escribe mientras el historial
  // viaja, la respuesta tardía no modifica la conversación que ya empezó.
  function sincronizarHistorial() {
    var active = conversacionesActiva();
    if (!active || active.hidratado) return;
    active.hidratado = true;
    window
      .fetch("/api/chat/history?session_id=" + encodeURIComponent(active.id),
             { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        var mensajes = data && Array.isArray(data.mensajes) ? data.mensajes : [];
        var actual = conversacionesActiva();
        if (mensajes.length && actual && actual.id === active.id && !actual.hasUserMessage) {
          welcomed = true;
          mensajes.forEach(function (m) {
            addMsg(String(m.texto || ""), m.rol === "user" ? "user" : "asst");
          });
          scrollAlFinal();
          guardarEstado();
          return;
        }
        guardarEstado();
      })
      .catch(function () {
        guardarEstado();
      });
  }

  var viewport = ChatUI.seguirViewport(panel, {
    media: "(max-width:700px)",
    enabled: function () { return opened; },
  });

  function abrir(enfocar) {
    // listing.js injects its own stylesheet after this global widget. Move the
    // responsive container rules to the end when the rail opens so the
    // remaining-width layout wins the cascade without touching listing.js.
    elevarEstilosResponsive();
    panel.hidden = false;
    opened = true;
    viewport.update();
    document.body.classList.add("stzc-chat-open");
    document.documentElement.classList.add("stzc-chat-open-root");
    marcarBoton(true);
    separarTrasPausa();
    pedirEnlaceWhatsapp();
    saludarSiHaceFalta();
    sincronizarHistorial();
    guardarEstado();
    actualizarLayout();
    scrollAlFinal();
    if (enfocar !== false) inputEl.focus();
  }

  function cerrar() {
    panel.hidden = true;
    opened = false;
    viewport.update();
    document.body.classList.remove("stzc-chat-open");
    document.documentElement.classList.remove("stzc-chat-open-root");
    marcarBoton(false);
    guardarEstado();
    actualizarLayout();
  }

  closeBtn.addEventListener("click", cerrar);
  if (!reusedLauncher) launcher.addEventListener("click", function () { abrir(true); });
  newBtn.addEventListener("click", nuevaConversacion);
  historyBtn.addEventListener("click", function () {
    if (viewingHistory) mostrarMensajes();
    else mostrarHistorial();
  });
  threadListEl.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-conversation-id]");
    if (!btn) return;
    var id = btn.getAttribute("data-conversation-id");
    cargarConversacion(conversations.find(function (item) { return item.id === id; }));
    inputEl.focus();
  });

  // Tapping the message area drops the on-screen keyboard by blurring an
  // active input — but ONLY the message area (.stzc-msgs). A panel-wide
  // listener with a negative guard (e.g. "return unless inside .stzc-form")
  // also blurs the embedded Account login UI the instant one of its
  // INPUT/TEXTAREA controls is tapped to focus it: at click time that control
  // is already document.activeElement, so it gets blurred straight away and
  // can never be operated (Codex review 2026-08-11). Positive-scoping to
  // .stzc-msgs leaves the composer (.stzc-form), the embedded auth region
  // (.stzc-auth), the header and the history list all operable.
  panel.addEventListener("click", function (ev) {
    if (!ev.target.closest(".stzc-msgs")) return;
    var activo = document.activeElement;
    if (activo && panel.contains(activo) &&
        (activo.tagName === "TEXTAREA" || activo.tagName === "INPUT") &&
        typeof activo.blur === "function") {
      activo.blur();
    }
  });

  panel.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-chat-action]");
    if (!btn) return;
    var action = btn.getAttribute("data-chat-action");
    var row = btn.closest(".stzc-chips,.stzc-login");
    if (row) row.remove();
    guardarEstado();
    if (action === "hours") enviarMensaje(T("chat.quick_hours_message", null, "Quiero ver los próximos horarios disponibles"));
    else if (action === "upload" || action === "retry-upload") fileEl.click();
    else if (action === "login") abrirAuth();
  });

  // The form's submit (Enter, or tapping send) is wired by crearComposer()
  // above — including its own "still recording? finish + send" handling.

  var restored = leerEstado();
  if (restored && Array.isArray(restored.conversations)) {
    conversations = restored.conversations.filter(function (item) {
      return item && typeof item.id === "string" &&
        /^[A-Za-z0-9_-]{1,64}$/.test(item.id) &&
        typeof item.messages === "string";
    }).map(function (item) {
      item.hasUserMessage = !!item.hasUserMessage ||
        item.messages.indexOf("stzc-msg--user") >= 0;
      if (item.title === "Nueva conversación") item.title = NUEVO_CHAT_LABEL;
      item.createdAt = Number(item.createdAt || Date.now());
      item.updatedAt = Number(item.updatedAt || item.createdAt);
      return item;
    }).slice(0, 12);
    activeId = String(restored.activeId || "");
  } else if (restored && typeof restored.messages === "string") {
    // Migrate the previous single-conversation shape without losing the chat
    // that was visible before the history control was introduced.
    var migrated = crearConversacion();
    migrated.messages = restored.messages;
    migrated.whatsapp = restored.whatsapp || "";
    migrated.welcomed = !!restored.welcomed;
  }
  if (!conversations.length) crearConversacion();
  var active = conversacionesActiva() || conversations[0];
  cargarConversacion(active);
  if (restored && restored.open) abrir(false);

  // Public API: the module's exported abrir()/cerrar() (declared at the top
  // of the module, live since evaluation) resolve through widgetListo from
  // here on — an early abrir() queued before boot opens now, unless a
  // cerrar() cancelled it first.
  widgetListo = { abrir: abrir, cerrar: cerrar };
  if (pendingOpen) abrir(true);
}
