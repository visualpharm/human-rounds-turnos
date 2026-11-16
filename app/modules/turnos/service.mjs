/* modules/turnos/service.mjs — página de servicio de Pinamar Turnos.
   Canonical ES module owned by modules/turnos. One named idempotent boot().
   No window.WzUpload, no classic twin. Direct native upload imports.
   Para servicios `solicitud_con_orden` (y `solicitud`, sin orden — arranca en
   "Elegí un turno") arma un WIZARD por pasos (wizard-design,
   2026-07-10: cada paso REEMPLAZA al anterior y muestra sólo el contexto del
   proceso + lo que ese paso necesita; nada de filas vacías ni referencia).
   Antes del primer paint, el servidor elimina del contador los pasos ya
   resueltos por la URL o la sesión. El orden completo posible es:
     1) Tu orden   — subís la orden (foto, archivo o pegada con Ctrl/⌘V); la
                     analizamos (Gemini Flash vía POST /api/analyze-order).
                     ¿Parece una orden válida — o el análisis no está disponible
                     (cuota/red)? → pasamos DERECHO al paso siguiente, sin
                     pantalla intermedia (una persona la revisa igual). ¿NO
                     parece una orden? → pantalla de duda: mensaje + preview +
                     3 acciones (subir otra con el MISMO control / "estoy
                     seguro" / "no tengo la orden").
     2) Cuándo     — turnos online reales si hay agenda, si no franja preferida.
     3) Tu cuenta  — GET /api/account/me: logueado salta; si no, native Account.
     4) Confirmá   — resumen + gate por edad → POST /api/requests.
   Fechas en formato amigable (dates.mjs): "Hoy, 14:40" / "Mañana, miércoles 11,
   14:40" / "Martes 14 de julio, 14:40". Para el resto de los modos (walkin /
   walkin_con_orden) sólo muestra, si hace falta, el aviso por edad. Estático:
   sin datos por página más allá del <script id="svc-data">. */
import { css as uploadCss, zoneHtml, wire as wireUploadControl, adoptPaintedInput, fileToOrden, analyzeHeaders } from "./wz-upload.mjs";

var booted = false;
var pendingUploadFile = null;
export function boot(opts) {
  if (booted) return;
  booted = true;
  opts = opts || {};
  pendingUploadFile = (opts && opts.pendingFile) || null;
  function takePendingUploadFile() {
    var queued = typeof opts.takePendingFile === "function"
      ? opts.takePendingFile()
      : pendingUploadFile;
    pendingUploadFile = null;
    return queued;
  }
  function uploadUnavailable() {
    pendingUploadFile = null;
    if (typeof opts.uploadUnavailable === "function") opts.uploadUnavailable();
  }
  var I18n = null;
    var i18nReady = (typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC["platform/i18n.mjs"])
      ? import(window.SANA_STATIC["platform/i18n.mjs"]).then(function (m) { I18n = m; return m; })
      : Promise.resolve(null);
    function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }
  var $ = function (s, r) { return (r || document).querySelector(s); };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  var raw = $("#svc-data");
  if (!raw) { uploadUnavailable(); return; }
  var data = {};
  try { data = JSON.parse(raw.textContent); } catch (e) { uploadUnavailable(); return; }
  var servicio = data.servicio || {};
  // Anonymous step counter (platform/analytics.mjs, only when the installation turns the stream on): the
  // step name and the public service slug, nothing the patient typed. Never throws.
  function evStep(name) {
    try { (window.__hrq = window.__hrq || []).push({ t: "step", step: name, service: servicio.id || "" }); } catch (e) { /* statistics never break the wizard */ }
  }
  var centro = data.centro || {};
  var centroNombre = centro.name || "";
  // "password" | "renaper" | "portal" (patient_web/site_*.py lo escribe
  // server-side): una sesión del portal no se cambia con nuestro logout.
  var AUTH_MODE = data.auth_mode || "password";
  // age.mjs (el ruteo por edad) es un módulo ES canónico servido con huella
  // (core/static_fingerprint.py, CLIENT_LOADED): un archivo servido no puede
  // conocer su URL vigente, así que el shell la publica como window.SANA_STATIC
  // y staticUrl() resuelve a través de ese mapa — fail-closed (null +
  // console.error, nunca una URL fallback hardcodeada), la misma forma que
  // islands.mjs's resolveUrl(). importStatic() rechaza ante una entrada ausente
  // exactamente igual que un import() que falló en cargar.
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
  // UNA sola promesa de módulo de edad, arrancada con la evaluación de ESTE
  // script. Antes el UMD se cargaba con `defer` ANTES de service.js, así que
  // su API existía antes de cualquier arranque que dependiera de ella;
  // esa garantía la reproduce edadLista antes de arrancar cualquiera de los
  // dos boots (el wizard o el gate de los modos sin formulario). Si el
  // import falla — red, hash viejo, manifiesto ausente — Edad queda null y
  // todo el ruteo por edad toma el camino fail-open de siempre: sin edad
  // conocida no se bloquea nada (gateHtml/renderGateOnly ya lo trataban así).
  var Edad = null;
  var edadLista = importStatic("platform/age.mjs").then(function (m) { Edad = m; })
    .catch(function () { /* fail-open: sin módulo no hay gate por edad */ });
  // La grilla mensual compartida (calendar-grid.mjs) es el segundo módulo ES
  // canónico que este wizard resuelve por manifiesto — el UMD global que antes
  // llegaba con `defer` se retiró. Misma forma que edadLista: la
  // promesa arranca con la evaluación de este script y el boot del formulario
  // espera ambas, así injectStyle/renderSlots ven el módulo tan seguro como lo
  // veían cuando el script defer corría antes. Si el import falla, Cal queda
  // null y "Otras fechas" simplemente no se ofrece — el wizard sigue (fail-open,
  // el mismo trato que ya le daba el guard del módulo anterior).
  var Cal = null;
  var calLista = importStatic("platform/calendar-grid.mjs").then(function (m) { Cal = m; })
    .catch(function () { /* fail-open: sin grilla no hay "Otras fechas" */ });
  var Dates = null;
  var datesLista = importStatic("platform/dates.mjs").then(function (m) { Dates = m; })
    .catch(function () { /* fail-open: el texto conserva su fallback */ });
  // Teléfonos, mails y nombres: el módulo canónico se resuelve por el mismo
  // manifiesto inmutable y antes de cualquier primer render.
  var Format = null;
  var formatLista = importStatic("platform/format.mjs").then(function (m) {
    Format = m;
  });
  var Prep = null;
  var prepLista = importStatic("platform/prep.mjs").then(function (m) {
    Prep = m;
  });
  // Account form is loaded lazily at mountAuth (after the chosen slot is
  // already painted), never at wizard boot — delaying the module must not
  // block slot picking. Cached so returning to auth does not re-import.
  var Account = null;
  var accountReady = null;
  function loadAccountForm() {
    if (!accountReady) {
      accountReady = importStatic("platform/account-form.mjs").then(function (m) {
        Account = m;
        return m;
      }, function (err) {
        accountReady = null;
        throw err;
      });
    }
    return accountReady;
  }
  // platform/dependents.mjs (personas a cargo) es el módulo canónico.
  // Se carga RECIÉN cuando el wizard tiene que decidir para quién es la
  // atención (afterCuenta / startOrdenConEdad / elegir-agregar): sin esa
  // decisión el turno sale directo y el módulo nunca viaja. Cacheado igual
  // que Account; si el import falla, cada paso muestra su recuperación
  // localizada (dependentsNoDisponibles) en vez de avanzar en silencio.
  var Dependents = null;
  var dependentsReady = null;
  function loadDependents() {
    if (!dependentsReady) {
      dependentsReady = importStatic("platform/dependents.mjs").then(function (m) {
        Dependents = m;
        return m;
      }, function (err) {
        dependentsReady = null;
        throw err;
      });
    }
    return dependentsReady;
  }
  // El widget de Turnstile (turnstile.mjs, canónico desde el cutover físico):
  // misma forma que los anteriores — una sola promesa por evaluación de ESTE
  // script y el boot del wizard la espera antes del primer wireUploadZone. Si
  // el import falla, TS queda null y el POST viaja sin token: el camino
  // fail-open que ya tomaba el script defer cuando no cargaba.
  var TS = null;
  var tsLista = importStatic("platform/turnstile.mjs").then(function (m) { TS = m; })
    .catch(function () { /* fail-open: sin widget no hay token */ });

  var IDENT_KEY = "st_identity";
  function loadIdent() {
    try { return JSON.parse(localStorage.getItem(IDENT_KEY) || "null") || {}; } catch (e) { return {}; }
  }
  function saveIdent(o) {
    try {
      var cur = loadIdent();
      localStorage.setItem(IDENT_KEY, JSON.stringify(Object.assign(cur, o)));
    } catch (e) {}
  }

  var todosServicios = null; // para alternativas por edad
  var todosCentros = null;
  function fetchServicios() {
    if (todosServicios) return Promise.resolve(todosServicios);
    return fetch("/api/services").then(function (r) { return r.json(); })
      .then(function (j) { todosServicios = (j && j.servicios) || []; return todosServicios; })
      .catch(function () { todosServicios = []; return todosServicios; });
  }
  function fetchCentros() {
    if (todosCentros) return Promise.resolve(todosCentros);
    return fetch("/api/centers").then(function (r) { return r.json(); })
      .then(function (j) { todosCentros = (j && j.centros) || []; return todosCentros; })
      .catch(function () { todosCentros = []; return todosCentros; });
  }

  /* --- ruteo por edad: devuelve el HTML del aviso (o "") --- */
  function gateHtml(edadNum) {
    if (!Edad || edadNum == null) return "";
    var ap = Edad.aptoPorEdad(servicio, edadNum);
    if (ap.ok) return "";
    var alt = Edad.alternativaPorEdad(todosServicios || [], servicio, edadNum);
    var linkHtml = "";
    if (alt && alt.servicio) {
      var altLink = '<a class="stz-link" href="/service/' + esc(alt.servicio.id) + '">' + esc(alt.servicio.nombre) + "</a>";
      var altMotivo = esc(alt.motivo);
      linkHtml = ' ' + T("service.gate.suggestion", { link: altLink, motivo: altMotivo }, "Te puede servir {link} ({motivo}).");
    }
    return '<div class="gate"><b>' + T("service.gate.title", null, "No podés pedir este turno para esta persona.") + '</b> ' +
      '<span><b>' + esc(servicio.nombre) + "</b> " + esc(ap.motivo) + ".</span>" +
      linkHtml + '<span> ' + T("service.gate.note", null, "Si el turno es para una persona a tu cargo, agregala y elegila.") + '</span></div>';
  }

  /* ------------------------------------------------------------------ *
   *  ESTILOS DEL WIZARD (inyectados desde JS)                          *
   * ------------------------------------------------------------------ */
  var WZ_CSS_ID = "wz-style";
  function injectStyle() {
    if (document.getElementById(WZ_CSS_ID)) return;
    var css =
      (Cal ? Cal.css : "") +
      uploadCss +
      /* contador de pasos numerado (1·2·3·4): el nombre del paso vive en el H1
         de la página (#svc-title); acá sólo el progreso — círculos + conectores
         (responsable del producto 2026-07-12: "un mejor contador uno-dos-tres", sin el rótulo). */
      ".wz-steps{margin:0 0 16px;}" +
      ".wz-stepper{display:flex;align-items:center;margin:0;padding:0;list-style:none;}" +
      ".wz-st{position:relative;display:flex;align-items:center;}" +
      ".wz-st:not(:first-child){margin-left:34px;}" +
      ".wz-st:not(:first-child):before{content:'';position:absolute;right:100%;top:50%;transform:translateY(-50%);width:34px;height:2px;background:var(--border);}" +
      ".wz-st:not(:first-child).on:before,.wz-st:not(:first-child).done:before{background:color-mix(in oklch,var(--accent) 55%,var(--border));}" +
      ".wz-st-n{box-sizing:border-box;flex:none;width:31px;height:31px;border-radius:50%;display:flex;align-items:center;justify-content:center;" +
        "background:var(--panel);border:2px solid var(--border);color:var(--text-3);font:700 14.5px var(--font);" +
        "transition:background .15s,border-color .15s,color .15s;}" +
      ".wz-st-n svg{width:15px;height:15px;}" +
      ".wz-st.done .wz-st-n{background:color-mix(in oklch,var(--accent) 55%,var(--border));border-color:transparent;color:#fff;}" +
      ".wz-st.on .wz-st-n{background:var(--accent);border-color:var(--accent);color:#fff;}" +
      ".wz-lead{font-size:19px;line-height:1.5;color:var(--text);margin:0 0 20px;}" +
      /* la CSS de la zona de subida vive en wz-upload.js (control compartido) */
      /* pantalla de duda: mensaje + preview (al costado en desktop, grande — hay
         que poder LEER el documento para decidir) + acciones */
      ".wz-err{display:grid;gap:16px;margin-top:4px;}" +
      ".wz-err-img-btn{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in;justify-self:start;}" +
      ".wz-err-img{display:block;width:100%;max-width:100%;max-height:400px;object-fit:contain;" +
        "border-radius:12px;border:1px solid var(--border);background:var(--bg-2);}" +
      /* La columna de 300px es PARA la foto: sin foto (un PDF no la tiene) el
         grid igual se la reservaba y el contenido quedaba en 284px, apretado
         contra el borde izquierdo (responsable del producto, 2026-07-27). Mismo idioma que
         .wz-card--photo en Confirmá: la variante de dos columnas se pide. */
      "@media(min-width:640px){.wz-err--foto{grid-template-columns:minmax(0,1fr) 300px;align-items:start;}" +
        ".wz-err--foto .wz-err-img-btn{grid-column:2;grid-row:1/span 2;}.wz-err--foto .wz-err-img{max-height:520px;}" +
        ".wz-err--foto .wz-warn,.wz-err--foto .wz-err-act{grid-column:1;}}" +
      /* --- texto-link secundario "no tengo la orden" --- */
      ".wz-altcard{display:inline-block;margin-top:18px;cursor:pointer;}" +
      ".wz-altcard-txt{display:inline;}" +
      ".wz-altcard-t{font-size:16px;color:inherit;}" +
      ".wz-altcard-s{display:none;}" +
      ".wz-altcard svg{display:none;}" +
      /* --- explicador \"cómo conseguir la orden\" --- */
      ".wz-h2{margin:6px 0 0;font-size:27px;line-height:1.15;font-weight:700;letter-spacing:-.02em;color:var(--text);}" +
      ".wz-steplist{display:flex;flex-direction:column;gap:16px;margin:22px 0 4px;}" +
      ".wz-step{display:flex;gap:15px;align-items:flex-start;}" +
      ".wz-step-n{flex-shrink:0;width:38px;height:38px;border-radius:50%;background:var(--accent);color:#fff;" +
        "font-size:19px;font-weight:700;display:flex;align-items:center;justify-content:center;}" +
      ".wz-step-t{padding-top:5px;font-size:18px;line-height:1.45;color:var(--text);}" +
      /* --- botones grandes (un primario por pantalla) --- */
      ".wz-btnstack{display:flex;flex-direction:column;gap:12px;margin-top:24px;}" +
      ".wz-cta-lg{display:flex;align-items:center;justify-content:center;width:100%;min-height:62px;" +
        "padding:16px 20px;border:0;border-radius:var(--btn-radius,15px);background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);" +
        "font:700 20px var(--font);letter-spacing:-.01em;cursor:pointer;text-decoration:none;" +
        "transition:filter .12s,transform .1s,background .12s;}" +
      ".wz-cta-lg:hover{background-image:var(--hover-fill);color:var(--btn-fg,#fff);} .wz-cta-lg:active{transform:translateY(1px);}" +
      ".wz-cta-sec{display:flex;align-items:center;justify-content:center;width:100%;min-height:56px;" +
        "padding:14px 20px;border:1px solid var(--btn2-bd,var(--border-strong));border-radius:var(--btn-radius,15px);background:var(--btn2-bg,var(--panel));" +
        "color:var(--btn2-fg,var(--text));font:700 18px var(--font);cursor:pointer;text-decoration:none;" +
        "transition:background .12s;}" +
      ".wz-cta-sec:hover{background:var(--btn2-bg-hover,var(--hover-bg));}" +
      /* --- entrevista previa (modules/intake/) en el éxito: con el médico
         habilitado es LA acción primaria del flujo — botón grande (carve-out
         de patient-flow-design regla 5; responsable del producto, 2026-08-01, reemplaza el link
         de texto de la fase 2). --- */
      ".wz-entrevista-lead{margin:18px 2px 0;font-size:17px;line-height:1.5;color:var(--text);}" +
      /* --- tarjeta \"cómo prepararte\" (derivada de la orden) --- */
      /* Los paneles hermanos nunca se tocan: el contenedor reutilizable deja
         una separación constante, incluso si la orden trae varios estudios. */
      ".wz-panel-stack{display:flex;flex-direction:column;gap:10px;}" +
      ".wz-panel-stack>.wz-prep{margin:0;}" +
      ".wz-prep{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px 18px;margin:2px 0 16px;}" +
      ".wz-prep-h{font-size:19px;font-weight:700;color:var(--text);margin:0 0 12px;letter-spacing:-.01em;}" +
      /* The body is the page's own `prep-*` markup (Prep.cuerpoHtml), styled
         once in the page CSS; in a card it gets its padding, not the page's
         column gap. */
      ".wz-prep .prep-resumen{font-size:17px;margin:0 0 14px;}" +
      ".wz-prep .prep-cols{column-gap:28px;}" +
      ".wz-prep-line{font-size:16px;line-height:1.5;color:var(--text);margin:0 0 7px;padding-left:17px;position:relative;}" +
      ".wz-prep-line:last-child{margin-bottom:0;}" +
      ".wz-prep-line:before{content:'';position:absolute;left:3px;top:9px;width:6px;height:6px;border-radius:50%;background:var(--accent);}" +
      ".wz-upbox{border:1.5px dashed var(--border-strong);border-radius:14px;padding:26px 20px;background:var(--bg);text-align:center;}" +
      ".wz-upbox .f-hint{margin-top:11px;}" +
      ".wz-card{box-sizing:border-box;width:100%;min-width:0;max-width:560px;overflow-wrap:anywhere;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:18px 18px 20px;margin-top:10px;}" +
      ".wz-card--slots{max-width:none;padding:22px 28px 24px;}" +
      ".wz-warn{background:#f7f0df;border:1px solid #ecdfbd;border-radius:12px;padding:14px 16px;margin:0 0 14px;font-size:16px;line-height:1.5;color:#7a5a12;}" +
      ".wz-warn strong{color:#5f4708;font-weight:750;}" +
      ".wz-soft{background:var(--bg);border:1px solid var(--border);border-radius:11px;padding:12px 14px;margin:0 0 14px;font-size:16px;line-height:1.5;color:var(--text-2);}" +
      ".wz-k{color:var(--text-2);white-space:normal;}" +
      ".wz-v{color:var(--text);word-break:break-word;}" +
      ".wz-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px;}" +
      ".wz-actions .stz-cta{padding:11px 18px;font-size:14px;}" +
      ".stz-cta.ghost{background:transparent;color:var(--text-2);border:1px solid var(--border-strong);}" +
      ".stz-cta.ghost:hover{background:var(--hover-bg);filter:none;}" +
      /* --- paso Confirmá (mockup "Pinamar Turnos Confirmar Turno", 2026-07-28) ---
         El TURNO va arriba de todo, en su propia tarjeta verde: es lo que la
         persona viene a confirmar. Debajo, para quién es; después cómo la
         avisamos. La frase corrida de antes (servicio + centro + nombre + DNI
         en un párrafo) queda sólo dentro de esa tarjeta, repartida en las
         líneas que le corresponden. */
      ".wz-confirm-main{min-width:0;}" +
      /* Página blanca, un solo nivel de recuadros (responsable del producto, 2026-10-27: «tiene
         muchos marcos; una página blanca, una sola capa»). El paso no va en
         una tarjeta: el único recuadro es el del turno. Sin el margen de
         `.sec`, que con la tarjeta de fondo leía como borde y en blanco deja
         un hueco de 70px bajo los pasos. */
      "body.wz-pagina-blanca{background:var(--panel);}" +
      /* En el teléfono Confirmá no repite las migas: centro y servicio están
         en la tarjeta del turno justo debajo, y las dos líneas bajaban
         «Pedir el turno» del primer pantallazo de 390 x 844 (2026-11-10). */
      "@media(max-width:600px){body.wz-pagina-blanca .svc-crumb{display:none;}}" +
      // 20px under the title: at 0 the H1 sat on the card (review 2026-11-15).
      ".sec:has(>.wz-card--confirm){margin-top:20px;}" +
      ".wz-card.wz-card--confirm{max-width:none;background:none;border:0;border-radius:0;padding:0;margin-top:0;}" +
      // OJO: la mezcla va `in srgb`. En oklch, mezclar un color con el blanco
      // (acromático) devuelve `hue:none` y el navegador lo pinta rosa — el
      // verde de la tarjeta salía salmón.
      ".wz-turno{background:color-mix(in srgb,var(--accent-soft) 55%,var(--panel));" +
        "border:1px solid var(--accent-bd);border-radius:14px;padding:15px 18px 16px;}" +
      // "Cambiar fecha y horario" va pegado a la fecha, en la MISMA línea
      // (responsable del producto, 2026-07-28). Sin el reloj compitiendo por el renglón entra sin
      // pelear; `wz-line` sigue con wrap por si la fecha larga no deja lugar.
      ".wz-line{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;min-width:0;}" +
      ".wz-cuando{font-size:26px;font-weight:700;line-height:1.2;letter-spacing:-.01em;color:var(--text);}" +
      // Una franja no es una fecha: mismo bloque, menos voz.
      ".wz-cuando--pref{font-size:21px;}" +
      ".wz-hora{font-variant-numeric:tabular-nums;font-weight:700;}" +
      ".wz-quien{font-size:17px;color:var(--text);margin-top:7px;}" +
      ".wz-donde{font-size:16px;color:var(--text-2);}" +
      ".wz-turno .wz-change-link,.wz-pac .wz-change-link,.wz-avisos .wz-change-link{font-size:15px;flex-shrink:0;}" +
      ".wz-pref-note{margin:6px 0 0;font-size:14px;line-height:1.4;color:var(--text-2);}" +
      ".wz-pac{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-top:24px;font-size:17px;color:var(--text);}" +
      ".wz-pac b{font-weight:700;}" +
      ".wz-pac-dni{color:var(--text-2);}" +
      /* "¿Cómo te avisamos?" y sus datos: una sección sin recuadro, título y
         datos juntos (responsable del producto, 2026-10-27 — antes era un recuadro dentro de la
         tarjeta). */
      ".wz-avisos{margin-top:24px;}" +
      ".wz-avisos-h{display:flex;align-items:center;gap:12px;flex-wrap:wrap;}" +
      ".wz-sec-t{font-size:17px;font-weight:600;color:var(--text);margin:0;}" +
      ".wz-sec-p{font-size:15px;line-height:1.5;color:var(--text-2);margin:6px 0 0;}" +
      // WhatsApp y email en UNA línea, cada uno con su ícono en vez de su
      // rótulo (responsable del producto, 2026-10-27); en el teléfono el email baja entero a la
      // línea siguiente, nunca partido. Un dato en edición es un campo con
      // su rótulo y ocupa el ancho que necesita.
      ".wz-cuenta{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 28px;margin-top:12px;}" +
      ".wz-cuenta>div{min-width:0;}" +
      ".wz-cuenta>.wz-dato-col--edit{flex:1 1 240px;}" +
      // First and last name are peers: two equal columns, not tel + email.
      ".wz-cuenta--par{display:grid;grid-template-columns:1fr 1fr;align-items:start;}" +
      // El dato y su "Cambiar" viajan juntos: el link nunca se va al margen
      // derecho (hook de proximidad, responsable del producto 2026-07-28).
      ".wz-dato{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;min-width:0;}" +
      ".wz-ico{width:20px;height:20px;flex-shrink:0;align-self:center;margin-right:-2px;}" +
      ".wz-dato-col{min-width:0;}" +
      ".wz-lab{display:block;font-size:13px;font-weight:500;color:var(--text-2);margin-bottom:4px;}" +
      ".wz-val{font-size:16px;color:var(--text);overflow-wrap:anywhere;}" +
      ".wz-val a{color:var(--link,var(--accent));}" +
      ".wz-val a:hover{color:var(--accent-hover);}" +
      ".wz-mono{font-variant-numeric:tabular-nums;}" +
      /* El contacto de escape entra a la tarjeta, abajo del botón (responsable del producto,
         2026-07-28): el `<p class="wizard-help">` del shell se esconde y su
         contenido se repinta acá — una sola frase, nunca dos. */
      ".wz-ayuda{margin:16px 0 0;font-size:15px;line-height:1.45;color:var(--text-2);}" +
      ".wz-ayuda a{color:var(--link,var(--accent));}" +
      ".wz-ayuda a:hover{color:var(--accent-hover);}" +
      // El botón puede llevar una segunda línea (el aviso del último minuto):
      // apilado y centrado, sin que la etiqueta principal se mueva de lugar.
      ".wz-cta-send{flex-direction:column;gap:2px;line-height:1.2;margin-top:20px;}" +
      ".wz-send-aviso{font-size:14px;font-weight:400;opacity:.92;}" +
      ".wz-send-aviso:empty{display:none;}" +
      ".wz-confirm-summary{margin:0;font-size:19px;line-height:1.48;color:var(--text);}" +
      ".wz-confirm-summary strong{font-weight:700;}" +
      ".wz-confirm-dni{font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap;}" +
      // Peso normal: es un link, no un rótulo que compita con el dato que
      // cambia (the product owner, 2026-07-28: "Cambiar, don't make them bold").
      ".wz-change-link{appearance:none;padding:0;border:0;background:transparent;color:var(--accent);font:400 14px/1.35 var(--font);text-decoration:none;white-space:nowrap;cursor:pointer;}" +
      ".wz-change-link:hover{color:var(--accent-hover);}" +
      ".wz-editin{box-sizing:border-box;display:block;width:100%;max-width:none;font-size:16px;padding:9px 11px;" +
        "border:1.5px solid var(--border-strong);border-radius:9px;background:var(--panel);color:var(--text);font-family:inherit;}" +
      ".wz-editin:focus{outline:none;border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}" +
      ".wz-editin::placeholder{color:var(--text-4);}" +
      ".wz-editin--tel{width:22ch;max-width:100%;}" +
      ".wz-contact-error{margin:8px 0 0;font-size:13px;line-height:1.35;color:var(--accent);}" +
      ".wz-contact-error:empty{margin:0;}" +
      // «Guardar también en mi cuenta»: la tilde se opera con el pulgar (caja
      // de 24px, fila de 44px) y se lee a 16px.
      ".wz-guardar-cuenta:not(:empty){margin-top:14px;}" +
      ".wz-guardar-op{display:flex;align-items:center;gap:12px;min-height:44px;font-size:16px;line-height:1.35;color:var(--text);cursor:pointer;}" +
      ".wz-guardar-op input{width:24px;height:24px;margin:0;flex:0 0 auto;accent-color:var(--accent);}" +
      ".wz-guardar-nota{font-size:16px;margin-top:2px;}" +
      ".wz-preference{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-top:14px;font-size:16px;line-height:1.4;color:var(--text);}" +
      ".wz-preference>span{min-width:0;}" +
      ".wz-slot-detail{display:block;margin-top:2px;}" +
      ".wz-auth-layout{display:grid;grid-template-columns:minmax(0,480px) 260px;grid-template-areas:'main summary';gap:36px;align-items:start;}" +
      ".wz-auth-main{grid-area:main;min-width:0;}" +
      ".wz-booking-summary{grid-area:summary;background:var(--panel);border:1px solid var(--border);" +
        "border-radius:14px;padding:18px 20px;box-shadow:var(--shadow-card);}" +
      ".wz-booking-summary-k{font-size:14px;font-weight:700;color:var(--text-3);}" +
      ".wz-booking-summary-service{display:block;margin-top:10px;font-size:18px;line-height:1.3;color:var(--text);}" +
      ".wz-booking-summary .wz-slot-detail{font-size:15px;line-height:1.45;}" +
      ".wz-booking-summary .wz-change-link{margin-top:12px;}" +
      "@media(max-width:720px){.wz-auth-layout{grid-template-columns:1fr;grid-template-areas:'summary' 'main';gap:18px;}" +
        ".wz-booking-summary{padding:15px 17px;}}" +
      ".wz-confirm-note{margin:14px 0 0;font-size:14px;line-height:1.45;color:var(--text-2);}" +
      ".wz-center-picker{margin:14px 0 0;padding:14px;border:1px solid var(--border);border-radius:12px;}" +
      ".wz-center-picker legend{padding:0 4px;font-size:14px;color:var(--text-2);}" +
      ".wz-center-choice{display:block;width:100%;margin:8px 0 0;padding:11px 12px;border:1.5px solid var(--border-strong);border-radius:9px;background:var(--panel);color:var(--text);font:600 16px/1.35 var(--font);text-align:left;cursor:pointer;}" +
      ".wz-center-choice:hover{border-color:var(--hover-bd-strong);}" +
      ".wz-center-cancel{margin-top:12px;}" +
      ".wz-center-otra{margin-right:18px;}" +
      ".wz-success{width:100%;max-width:680px;}" +
      ".wz-success-state{background:color-mix(in srgb,var(--accent-soft) 55%,var(--panel));" +
        "border:1px solid var(--accent-bd);border-radius:14px;padding:18px 20px;}" +
      ".wz-success-state p{margin:0;font-size:17px;line-height:1.5;color:var(--text);}" +
      ".wz-success-state strong{font-weight:720;}" +
      ".wz-success-summary{margin-top:14px;background:var(--panel);border:1px solid var(--border);" +
        "border-radius:14px;padding:18px 20px;display:grid;gap:13px;}" +
      ".wz-success-row{display:grid;grid-template-columns:118px minmax(0,1fr);gap:14px;align-items:baseline;}" +
      ".wz-success-lab{font-size:13px;font-weight:600;color:var(--text-2);}" +
      ".wz-success-val{min-width:0;font-size:16px;line-height:1.4;color:var(--text);overflow-wrap:anywhere;}" +
      ".wz-success-cal{display:flex;align-items:center;gap:8px;margin-top:6px;min-height:44px;}" +
      ".wz-success>.wz-cta-lg{margin-top:16px;}" +
      ".wz-success .wz-btnstack{margin-top:14px;}" +
      /* Visibilidad de donación de sangre, discreta (reunión hospital
         2026-08-27): un renglón de texto después de la acción principal, no
         una pastilla ni un cartel — mismo lenguaje que .wz-confirm-note. */
      ".wz-donate-note{margin:16px 2px 0;font-size:14.5px;line-height:1.5;color:var(--text-2);}" +
      ".wz-donate-note a{color:var(--link,var(--accent));}" +
      ".wz-donate-note a:hover{color:var(--accent-hover);}" +
      ".sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}" +
      /* foto de la orden grande junto al resumen (al costado en desktop) */
      ".wz-card--photo{max-width:none;min-width:0;}" +
      ".wz-confirm-main,.wz-card--photo>*{min-width:0;max-width:100%;}" +
      ".wz-confirm-photo{display:block;width:100%;margin-top:18px;padding:0;border:0;background:none;cursor:zoom-in;}" +
      ".wz-confirm-photo img{display:block;width:100%;max-height:340px;object-fit:contain;" +
        "border-radius:12px;border:1px solid var(--border);background:var(--bg-2);}" +
      "@media(min-width:640px){.wz-card--photo{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:24px;align-items:start;}" +
        ".wz-card--photo .wz-confirm-photo{grid-column:2;margin-top:0;}" +
        ".wz-card--photo .wz-confirm-photo img{max-height:460px;}}" +
      /* chip de documento: reemplaza el nombre de archivo pelado cuando la
         orden es un PDF, o cuando la miniatura del servidor no cargó
         (ver ordenSummary/stepConfirm) */
      ".wz-orden-doc{display:flex;align-items:center;gap:12px;margin-top:14px;padding:13px 15px;" +
        "border:1px solid var(--border);border-radius:12px;background:var(--bg-2);}" +
      ".wz-orden-doc-icon{flex:0 0 auto;width:26px;height:26px;color:var(--text-3);}" +
      ".wz-orden-doc-name{flex:1 1 auto;min-width:0;font-size:15px;line-height:1.35;color:var(--text);overflow-wrap:anywhere;}" +
      ".wz-orden-doc .wz-change-link{flex:0 0 auto;}" +
      ".wz-spin{display:flex;align-items:center;gap:11px;color:var(--text-2);font-size:14px;padding:22px 4px;}" +
      ".wz-spin i{width:18px;height:18px;border:2.5px solid var(--border-strong);border-top-color:var(--accent);border-radius:50%;display:inline-block;animation:wzspin .8s linear infinite;}" +
      "@keyframes wzspin{to{transform:rotate(360deg)}}" +
      ".wz-processing{min-height:230px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:17px;padding:24px 12px;}" +
      ".wz-processing-sheet{position:relative;width:70px;height:88px;box-sizing:border-box;border:2px solid var(--border-strong);border-radius:10px;background:var(--panel);overflow:hidden;box-shadow:0 10px 30px color-mix(in oklch,var(--text) 9%,transparent);animation:wzfloat 1.8s ease-in-out infinite;}" +
      ".wz-processing-sheet:before,.wz-processing-sheet:after{content:'';position:absolute;left:14px;right:14px;height:3px;border-radius:3px;background:var(--border);}" +
      ".wz-processing-sheet:before{top:25px;box-shadow:0 13px 0 var(--border),0 26px 0 var(--border);}" +
      ".wz-processing-sheet:after{top:-8px;left:0;right:0;height:12px;background:linear-gradient(180deg,transparent,color-mix(in oklch,var(--accent) 35%,transparent),transparent);animation:wzscan 1.6s ease-in-out infinite;}" +
      ".wz-processing-h{margin:0;font-size:26px;line-height:1.2;font-weight:720;letter-spacing:-.02em;color:var(--text);}" +
      ".wz-processing-p{margin:0;max-width:420px;font-size:17px;line-height:1.5;color:var(--text-2);}" +
      "@keyframes wzscan{0%{transform:translateY(0)}50%,100%{transform:translateY(92px)}}" +
      "@keyframes wzfloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-5px)}}" +
      "@media(prefers-reduced-motion:reduce){.wz-processing-sheet,.wz-processing-sheet:after{animation:none}.wz-spin i{animation-duration:1.8s}}" +
      ".wz-opt{font-weight:400;color:var(--text-3);}" +
      ".wz-seg{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;}" +
      ".wz-seg-opt{flex:1 1 auto;min-width:96px;padding:13px 16px;font:inherit;font-size:16px;color:var(--text);" +
        "background:var(--bg);border:1.5px solid var(--border-strong);border-radius:11px;cursor:pointer;transition:border-color .12s,background .12s;}" +
      ".wz-seg-opt:hover{border-color:var(--hover-bd-strong);}" +
      ".wz-seg-opt.on{border-color:var(--accent);background:color-mix(in oklch,var(--accent) 12%,var(--bg));color:var(--text);font-weight:600;}" +
      /* «Ahora no hay turnos» (contraparte técnica, 2026-10-17): tres opciones apiladas,
         cada una con su título; el texto libre copia el borde/foco de los
         campos del wizard. */
      ".wz-noslots-opt+.wz-noslots-opt{margin-top:18px;padding-top:16px;border-top:1px solid var(--border);}" +
      ".wz-noslots-h{margin:0 0 4px;font-size:17px;font-weight:700;line-height:1.35;color:var(--text);}" +
      ".wz-noslots-h label{font:inherit;color:inherit;}" +
      ".wz-noslots-val{margin:0;font-size:19px;font-weight:600;line-height:1.4;color:var(--text);}" +
      ".wz-noslots-txt{margin:0;font-size:16px;line-height:1.5;color:var(--text);text-wrap:pretty;}" +
      ".wz-noslots-urgent{margin-top:6px;font-weight:600;}" +
      ".wz-caso{box-sizing:border-box;display:block;width:100%;margin-top:10px;min-height:7.5em;resize:vertical;font-size:16px;line-height:1.5;padding:10px 12px;" +
        "border:1.5px solid var(--border-strong);border-radius:9px;background:var(--panel);color:var(--text);font-family:inherit;}" +
      ".wz-caso[hidden]{display:none;}" +
      ".wz-caso:focus{outline:none;border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}" +
      ".wz-caso-echo{white-space:pre-line;overflow-wrap:anywhere;color:var(--text);}" +
      /* «Contanos tu caso» como conversación (responsable del producto, 2026-11-07): los mismos
         pedazos de chat-ui.mjs que la entrevista previa (prefijo wzc), dentro
         de la tarjeta del paso — burbujas, opciones tocables y el campo. */
      // min-height: el lugar de la primera pregunta con sus opciones y el
      // campo, reservado antes de que llegue (sin salto del botón Continuar).
      // Medido 07.11: la primera pregunta con sus tres opciones y el campo
      // ocupa ~540px a 390 de ancho y ~425px desde 600 (tarjeta de 560).
      ".wzc{margin-top:12px;display:flex;flex-direction:column;min-height:460px;}" +
      ".wzc[hidden]{display:none;}" +
      "@media(min-width:600px){.wzc{min-height:390px;}}" +
      ".wzc-msgs{display:flex;flex-direction:column;gap:10px;margin-bottom:12px;}" +
      ".wzc-msg{max-width:88%;padding:11px 15px;font-size:17px;line-height:1.45;white-space:pre-wrap;overflow-wrap:break-word;}" +
      ".wzc-msg--asst{align-self:flex-start;background:var(--bg-2);color:var(--text);border-radius:15px 15px 15px 4px;}" +
      ".wzc-msg--user{align-self:flex-end;background:var(--accent-soft);color:var(--text);border-radius:15px 15px 4px 15px;}" +
      ".wzc-typing{align-self:flex-start;padding:14px 17px;background:var(--bg-2);border-radius:15px 15px 15px 4px;display:flex;gap:5px;align-items:center;}" +
      ".wzc-typing span{width:7px;height:7px;border-radius:50%;background:var(--text-3);animation:wzc-bounce 1.1s infinite ease-in-out;}" +
      ".wzc-typing span:nth-child(2){animation-delay:.15s;}" +
      ".wzc-typing span:nth-child(3){animation-delay:.3s;}" +
      "@keyframes wzc-bounce{0%,80%,100%{transform:translateY(0);opacity:.5;}40%{transform:translateY(-4px);opacity:1;}}" +
      "@media(prefers-reduced-motion:reduce){.wzc-typing span{animation:none;opacity:.75;}}" +
      ".wzc-chips{display:flex;flex-wrap:wrap;gap:9px;align-self:flex-start;max-width:100%;}" +
      ".wzc-chip{min-height:46px;padding:0 16px;border-radius:12px;border:1.5px solid var(--border-strong);background:var(--panel);" +
        "color:var(--accent-hover);font:600 16px var(--font);cursor:pointer;text-align:left;}" +
      ".wzc-chip:hover{border-color:var(--hover-bd-strong);background:var(--hover-bg-soft);}" +
      ".wzc-chip:disabled{opacity:.55;cursor:default;}" +
      ".wzc-composer{margin-top:auto;padding:9px 10px;border:1.5px solid var(--border-strong);border-radius:14px;background:var(--panel);display:flex;flex-direction:column;gap:6px;}" +
      ".wzc-composer[hidden]{display:none;}" +
      ".wzc-composer:focus-within{border-color:var(--focus-border);box-shadow:0 0 0 3px var(--focus-ring);}" +
      ".wzc-input{flex:0 0 auto;display:block;width:100%;min-width:0;min-height:28px;max-height:140px;box-sizing:border-box;padding:1px 2px;border:0;outline:0;" +
        "background:transparent;color:var(--text);font:400 17px/1.4 var(--font);resize:none;overflow-y:auto;}" +
      ".wzc-input:disabled{opacity:.6;}" +
      ".wzc-compose-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;min-width:0;}" +
      ".wzc-compose-status{order:-1;flex:1 0 100%;min-width:0;font-size:14px;line-height:1.35;color:var(--text-2);}" +
      ".wzc-compose-status:empty{display:none;}" +
      ".wzc-attach,.wzc-mic{flex-shrink:0;width:44px;height:44px;border-radius:10px;border:0;background:transparent;cursor:pointer;display:flex;align-items:center;justify-content:center;}" +
      ".wzc-attach[hidden],.wzc-mic[hidden]{display:none;}" +
      ".wzc-attach img,.wzc-mic img{width:21px;height:21px;opacity:.78;}" +
      ".wzc-mic[aria-pressed=true]{background:var(--accent-soft);}" +
      ".wzc-send{flex-shrink:0;margin-left:auto;width:44px;height:44px;border:0;border-radius:50%;background:var(--btn-bg,var(--accent));color:var(--btn-fg,#fff);" +
        "cursor:pointer;display:flex;align-items:center;justify-content:center;}" +
      ".wzc-send:disabled,.wzc-mic:disabled{opacity:.5;cursor:default;}" +
      ".wzc-send img{width:22px;height:22px;filter:brightness(0) invert(1);}" +
      ".wz-date-echo{min-height:18px;margin-top:6px;font-size:16px;color:var(--text-2);}" +
      ".wz-date-echo.err{color:var(--accent);}" +
      ".wz-medico{padding:2px 0 14px;margin-bottom:16px;}" +
      ".wz-medico:last-child{margin-bottom:4px;}" +
      ".wz-medico-head{display:flex;align-items:baseline;flex-wrap:wrap;column-gap:10px;row-gap:2px;margin:0 0 8px;}" +
      // Nombre + especialidad/centro comparten una línea cuando entran. Si no,
      // el bloque secundario baja entero y conserva juntos sus dos datos.
      ".wz-medico-lugar{font-size:15.5px;color:var(--text-2);margin:0;line-height:1.35;white-space:nowrap;max-width:100%;}" +
      ".wz-medico-lugar a{color:var(--link,var(--accent));}" +
      ".wz-medico-lugar a:hover{color:var(--accent-hover);}" +
      ".wz-medico-n{display:block;width:fit-content;font-weight:700;font-size:20px;line-height:1.3;color:var(--accent);margin:0;}" +
      ".wz-medico-n--plain{color:var(--text);}" +
      "a.wz-medico-n:hover{color:var(--accent-hover);}" +
      ".wz-day{margin-top:10px;}" +
      ".wz-day-head{display:flex;align-items:baseline;column-gap:12px;margin:0 0 6px;}" +
      ".wz-day-h{font-size:16px;font-weight:600;line-height:1.35;color:var(--text-2);margin:0;}" +
      // "Otras fechas" comes after every visible date and time. It is the
      // fallback only once the offered slots have been considered.
      ".wz-otras-row{margin-top:16px;}" +
      ".wz-otras{font:inherit;font-size:15px;line-height:1.35;padding:0;border:0;background:none;color:var(--accent);cursor:pointer;text-decoration:none;}" +
      ".wz-otras:hover{color:var(--accent-hover);}" +
      // One grid of equal columns per day: every time button has the same
      // width and every row the same count (4 on a phone, 6 from 600px), so
      // a lone «11:40» never stretches across a row of its own.
      ".wz-day .wz-seg{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));}" +
      "@media(min-width:600px){.wz-day .wz-seg{grid-template-columns:repeat(6,minmax(0,112px));}}" +
      ".wz-day .wz-seg-opt{min-width:0;padding:13px 4px;text-align:center;font-variant-numeric:tabular-nums;}" +
      ".wz-calendar{margin:0 0 22px;padding:16px 16px 2px;border:1px solid var(--border);border-radius:12px;background:var(--bg);}" +
      // abierto entre la fecha y sus horarios: pegado al link que lo abrió
      ".wz-day .wz-calendar{margin:2px 0 12px;}" +
      ".wz-calendar[hidden]{display:none;}" +
      ".wz-calendar .medcal-grids{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 28px;margin-bottom:10px;}" +
      ".wz-calendar .medcal-mes{min-width:0;width:auto;max-width:none;}" +
      ".wz-calendar .medcal-mes-h{font-size:16px;margin-bottom:10px;}" +
      ".wz-calendar .medcal-grid{grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;}" +
      ".wz-calendar .medcal-dow{font-size:13px;line-height:1.8;}" +
      ".wz-calendar .medcal-dia{height:42px;line-height:42px;font-size:15px;}" +
      ".wz-calendar-key{margin:0 0 12px;font-size:14px;color:var(--text-2);}" +
      ".wz-calendar-key i{display:inline-block;width:11px;height:11px;margin-right:7px;border-radius:3px;background:var(--state-done-bg);border:1px solid color-mix(in oklch,var(--done) 35%,transparent);vertical-align:-1px;}" +
      "@media(max-width:700px){.wz-calendar .medcal-grids{grid-template-columns:minmax(0,1fr);gap:18px;}.wz-calendar .medcal-mes{max-width:420px;}}" +
      "@media(max-width:640px){.wz-cuando{font-size:23px;}.wz-cuenta{grid-template-columns:1fr;gap:10px 24px;}" +
        ".wz-editin--tel{width:100%;}.wz-success-row{grid-template-columns:1fr;gap:3px;}}" +
      "@media(max-width:600px){.wz-card{border-radius:12px;padding:16px 14px 18px;}.wz-card--slots{padding:16px 12px 18px;}.wz-medico-n{font-size:19px;}.wz-confirm-summary{font-size:18px;}.wz-calendar{padding:13px 8px 2px;}}";
    var el = document.createElement("style");
    el.id = WZ_CSS_ID;
    el.textContent = css;
    document.head.appendChild(el);
  }

  var SVG_CHEV = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"></path></svg>';
  var CHECK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7"></path></svg>';
  /* ---- preparación del estudio: se deriva de lo que dice la ORDEN (o del
     servicio como respaldo). Dataset estático en /webdata; matcheo por palabra
     clave más específica. Nunca bloquea el envío; desconocido → línea segura. ---- */
  var prepData = null, prepPromise = null;
  function loadPrep() {
    if (prepPromise) return prepPromise;
    // /api/preparation-studies, no el JSON estático: el endpoint trae las ediciones
    // del panel encima del archivo base (preparation.py).
    prepPromise = fetch("/api/preparation-studies")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { prepData = (j && j.estudios) || []; return prepData; })
      .catch(function () { prepData = []; return prepData; });
    return prepPromise;
  }
  function normTxt(s) {
    return String(s == null ? "" : s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  function matchPrep(text) {
    var t = normTxt(text);
    if (!t || !prepData) return null;
    var best = null, bestLen = 0;
    prepData.forEach(function (e) {
      (e.match || []).forEach(function (kw) {
        var k = normTxt(kw);
        if (k && t.indexOf(k) >= 0 && k.length > bestLen) { best = e; bestLen = k.length; }
      });
    });
    return best;
  }
  function prepCardHtml(entry, study) {
    if (!entry) {
      return '<div class="wz-prep"><div class="wz-prep-h">' + T("service.prep_heading", null, "Cómo prepararte") + '</div>' +
        '<div class="wz-prep-line">' + T("service.prep.center_confirms", null, "El centro te indica la preparación cuando confirme el turno.") + '</div></div>';
    }
    // Qué secciones tiene una preparación y cómo se llaman lo decide prep.mjs,
    // el mismo modelo que la página del servicio, "Subí tu
    // orden" y los emails. `sin_preparacion` significa "no hay ayuno ni dieta",
    // NO "no hay nada que decirte": las indicaciones (ropa cómoda, sin cremas)
    // son lo único que distingue un estudio de otro, y taparlas dejaba dos
    // tarjetas distintas idénticas (responsable del producto, 2026-07-12).
    // The markup is the service page's own (Prep.cuerpoHtml, twin of
    // prep_model.cuerpo_html): bold section headings, a list per section
    // (the product owner 26.10: the subheadings did not even look bold).
    var P = Prep;
    var lines = P.cuerpoHtml(entry);
    if (!lines) lines = '<div class="wz-prep-line">' + esc(P.SIN_NADA) + "</div>";
    var entryNombre = esc(entry.nombre);
    return '<div class="wz-prep"><div class="wz-prep-h">' +
      T("service.prep.title_for", { nombre: entryNombre }, "Cómo prepararte · {nombre}") + "</div>" + lines + "</div>";
  }
  /* Elige el estudio a preparar: lo que dice la orden (rule 8: gana el documento)
     y si no, el servicio de la página. */
  function prepFor(wiz, servicio) {
    var det = wiz && wiz.ai && wiz.ai.campos && wiz.ai.campos.especialidad && wiz.ai.campos.especialidad.valor;
    return matchPrep(det) || matchPrep(servicio && servicio.nombre);
  }

  /* ------------------------------------------------------------------ *
   *  WIZARD                                                            *
   * ------------------------------------------------------------------ */
  function Wizard(host, opts) {
    var wiz = { ai: null, token: null, ordenRaw: null, ordenPreview: null, ordenFilename: null, ordenNoEsFoto: null, usuario: null, paciente: null, dependientes: null, preferencia: null, slotMedico: null, slotMedicoId: null, slotCuando: null, holdToken: null, slotDia: null, slotHora: null,
      // por bloque de horarios (clave = médico): qué día quedó filtrado y si
      // su calendario está abierto — ver renderSlots
      slotFilterDia: {}, calendarOpen: {} };
    // Soft-lock del turno elegido (2026-07-16, ver reservas.py): el token del
    // hold en curso se guarda para que un F5 a mitad del wizard no se
    // autoconflictúe pidiendo un token nuevo por el MISMO slot. Vive en
    // localStorage, no en sessionStorage: abrir el propio link del paso 2 en
    // OTRA pestaña dejaba al paciente sin su token y su propio hold le
    // aparecía como "ya no está disponible" (journey 2026-09-16). El token
    // sigue siendo secreto del cliente — nunca viaja en la URL de una página.
    var HOLD_KEY = "st_hold_token";
    function holdStore() {
      try { if (window.localStorage) return window.localStorage; } catch (e) {}
      try { return window.sessionStorage; } catch (e) { return null; }
    }
    try { var hs0 = holdStore(); wiz.holdToken = (hs0 && hs0.getItem(HOLD_KEY)) || null; } catch (e) {}
    function saveHoldToken(t) {
      wiz.holdToken = t || null;
      try {
        var hs = holdStore();
        if (!hs) return;
        if (t) hs.setItem(HOLD_KEY, t); else hs.removeItem(HOLD_KEY);
        // Restos del esquema viejo por pestaña: que no ganen sobre el nuevo.
        try { if (window.sessionStorage && hs !== window.sessionStorage) window.sessionStorage.removeItem(HOLD_KEY); } catch (e2) {}
      } catch (e) {}
    }
    // Toma (o renueva) el hold de (dia,hora) para el token en curso.
    // true = lo consiguió (y wiz.holdToken/sessionStorage quedan al día);
    // false = SÓLO cuando el servidor confirmó 409 (otra persona lo tiene) —
    // cualquier hiccup de red/servidor (timeout, 5xx) resuelve a true: nunca
    // dejar a alguien varado por un problema nuestro, no suyo.
    //
    // holdGen cuenta las veces que se soltó el hold: un pedido (sobre todo la
    // renovación fire-and-forget) que sale antes de un holdLiberar() y contesta
    // después ya es viejo — no guarda el token ni la hora y suelta lo que
    // volvió a tomar, en vez de retener un horario que el paciente dejó.
    var holdGen = 0;
    function holdTomar(dia, hora) {
      var g = holdGen;
      return fetch("/api/slot-hold", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ servicio_id: servicio.id, dia: dia, hora: hora, token: wiz.holdToken || undefined })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, j: j }; });
      }).then(function (res) {
        if (g !== holdGen) {
          // Hubo un holdLiberar() después de disparar este pedido: si el
          // servidor lo tomó igual, soltarlo (409 = no se tomó nada).
          if (res.status === 200 && res.j && res.j.token) {
            fetch("/api/slot-hold/" + encodeURIComponent(res.j.token), { method: "DELETE", credentials: "include" }).catch(function () {});
          }
          return true;
        }
        if (res.status === 409) return false;
        if (res.j && res.j.token) saveHoldToken(res.j.token);
        // Hasta cuándo lo tenemos guardado: Confirmá lo muestra como cuenta
        // regresiva ("Te reservamos el horario 12:04"). El TTL lo dice el
        // servidor — nunca hardcodear 15 acá y prometer un minuto que no es.
        if (res.j && res.j.ttl_min) wiz.holdHasta = Date.now() + res.j.ttl_min * 60000;
        return true;
      }).catch(function () { return true; });
    }
    function holdLiberar() {
      holdGen++;
      if (!wiz.holdToken) return;
      var t = wiz.holdToken;
      wiz.holdHasta = null;
      saveHoldToken(null);
      fetch("/api/slot-hold/" + encodeURIComponent(t), { method: "DELETE", credentials: "include" }).catch(function () {});
    }
    // 'orden' = upload an order first; 'turno' = book a live online slot, no order.
    var MODE = (opts && opts.mode) || "orden";
    var TURNOS = (opts && opts.turnos) || null;
    var PREFERRED_MEDICO = "";
    try { PREFERRED_MEDICO = new URLSearchParams(window.location.search).get("medico") || ""; } catch (e) {}
    // "Más horarios" (?slots=1): el paciente viene a ELEGIR el turno, así
    // que "Cuándo" pasa al frente y la orden se sube después. Sin el flag ese
    // link caía en "Subí tu orden" — lo contrario de lo que prometía
    // (the product owner, 2026-07-15).
    var ELEGIR_PRIMERO = MODE === "orden" && /[?&]slots=1(?:&|$)/.test(window.location.search);
    // La orden puede llegar YA analizada desde "Subí tu orden" (upload-order.js:
    // subís la orden ahí, elegimos a qué turno te lleva, y el link al wizard
    // lleva ?order=<token> + el handoff en sessionStorage st_order_pending —
    // el mismo token es la prueba de que es ESTE navegador el que la subió,
    // nunca un token ajeno adivinado por la URL). El wizard no vuelve a
    // pedirla: arranca en "Cuándo" como si fuera MODE "turno" (responsable del producto,
    // 2026-07-16). Token que no coincide (otra pestaña, sessionStorage
    // limpiado, expiró) → se ignora, sigue el flujo normal de subida.
    /* El MISMO handoff lo escribe el wizard apenas analiza una orden, porque
       el ingreso federado (/auth/sso?next=…) es una navegación de página
       COMPLETA: al volver, el wizard arranca de cero y la orden, que sólo
       vivía en `wiz`, se había perdido — te la volvía a pedir después de
       loguearte (responsable del producto, 2026-07-27). Con el token en sessionStorage y en la
       URL, la vuelta del SSO se reconstruye sola: `next` incluye el search,
       así que el ?order= vuelve con vos. La foto NO se guarda (es base64 de
       megas): Confirmá muestra el nombre del archivo, que es lo que ya hacía
       la llegada desde "Subí tu orden". */
    function guardarOrdenHandoff() {
      if (!wiz.token) return;
      try {
        sessionStorage.setItem("st_order_pending", JSON.stringify({
          token: wiz.token, ai: wiz.ai, filename: wiz.ordenFilename || ""
        }));
      } catch (e) {}
      ponerOrdenEnUrl(wiz.token);
    }
    function olvidarOrdenHandoff() {
      try { sessionStorage.removeItem("st_order_pending"); } catch (e) {}
      ponerOrdenEnUrl(null);
    }
    function ponerOrdenEnUrl(token) { ponerEnUrl("order", token); }
    /* El horario elegido viaja por el MISMO camino, y por la misma razón: al
       volver del SSO la URL mandaba, así que el ?slot= viejo (el que ofrecía
       el listado) pisaba el que la persona había elegido a mano — y si ese ya
       no estaba libre, aterrizaba de nuevo en la grilla. El hold es de este
       navegador (st_hold_token), así que la grilla se lo sigue mostrando y lo
       vuelve a tomar sin pelearse consigo mismo. */
    function recordarSlotEnUrl(dia, hora) {
      ponerEnUrl("slot", dia && hora ? dia + "T" + hora : null);
    }
    function ponerEnUrl(clave, valor) {
      try {
        var u = new URL(window.location.href);
        if (valor) u.searchParams.set(clave, valor);
        else u.searchParams.delete(clave);
        history.replaceState(history.state, "", u.pathname + u.search + u.hash);
      } catch (e) {}
    }

    var ordenPrecargada = null;
    if (MODE === "orden") {
      var mOrden = /[?&]order=([^&]+)/.exec(window.location.search);
      if (mOrden) {
        try {
          var pendRaw = sessionStorage.getItem("st_order_pending");
          var pend = pendRaw ? JSON.parse(pendRaw) : null;
          if (pend && pend.token && pend.token === decodeURIComponent(mOrden[1])) ordenPrecargada = pend;
        } catch (e) {}
      }
    }
    if (ordenPrecargada) {
      wiz.token = ordenPrecargada.token || null;
      wiz.ai = ordenPrecargada.ai || null;
      wiz.ordenFilename = ordenPrecargada.filename || null;
      // This route already has its accepted order and starts at the schedule.
      // The bootstrap bridge is only for the interval before an upload control
      // takes ownership; retaining it here would swallow unrelated later
      // image pastes even though this journey never mounts that control.
      uploadUnavailable();
    }
    // "Paciente" ya NO es un paso fijo del contador: la mayoría de los
    // servicios no son pediátricos y ese paso no aplicaba a nadie (responsable del producto
    // 2026-07-12). Ahora lo decide dependientes.js:plan() en afterCuenta() —
    // si hace falta preguntar, la pantalla reusa el hueco de "Tu cuenta" en
    // el contador (stepHeader("cuenta")) en vez de sumar un paso propio.
    var KEY_TITLE = {
      orden: T("service.step.your_order", null, "Tu orden"),
      horario: T("service.step.when", null, "Cuándo"),
      cuenta: T("service.step.your_account", null, "Tu cuenta"),
      confirm: T("service.step.confirm_short", null, "Confirmá")
    };
    var STEP_ORDER = (MODE === "turno" || ordenPrecargada)
      ? ["horario", "cuenta", "confirm"]
      : ELEGIR_PRIMERO
        ? ["horario", "orden", "cuenta", "confirm"]
        : ["orden", "horario", "cuenta", "confirm"];
    var STEP_KEYS = Array.isArray(data.pasos_estimados)
      ? data.pasos_estimados.filter(function (key) { return STEP_ORDER.indexOf(key) >= 0; })
      : STEP_ORDER.slice();
    if (STEP_KEYS.indexOf("confirm") < 0) STEP_KEYS.push("confirm");
    /* The server estimates the happy path before first paint. If that estimate
       becomes stale (session expired, slot disappeared), restore the newly
       necessary step in its natural position instead of lying in the counter. */
    function ensureStep(key) {
      if (STEP_KEYS.indexOf(key) >= 0) return;
      var rank = STEP_ORDER.indexOf(key);
      var at = STEP_KEYS.length;
      for (var i = 0; i < STEP_KEYS.length; i++) {
        if (STEP_ORDER.indexOf(STEP_KEYS[i]) > rank) { at = i; break; }
      }
      STEP_KEYS.splice(at, 0, key);
    }
    // El nombre del paso ahora ES el H1 de la página (#svc-title): breadcrumb
    // arriba, título = la acción del paso actual. El contador numerado ya no lo
    // repite (the product owner 2026-07-12).
    var STEP_TITLE = {
      orden: T("page.wizard.upload_order", null, "Subí tu orden"),
      horario: MODE === "turno"
        ? T("page.wizard.pick_slot", null, "Elegí un turno")
        : T("service.step.pick_time", null, "Elegí un horario"),
      cuenta: T("page.wizard.authorize", null, "Autorizá"),
      confirm: T("page.wizard.confirm", null, "Confirmá tu solicitud")
    };
    var TITLE_EL = document.getElementById("svc-title");
    function setTitle(t) { if (TITLE_EL && t) TITLE_EL.textContent = t; }
    // El contador de pasos sigue inmediatamente al título (#svc-steps); el
    // subtítulo queda debajo, en la misma columna.
    var STEPS_EL = document.getElementById("svc-steps");
    var SUB_EL = document.getElementById("svc-sub");
    function setSteps(html) { if (STEPS_EL) STEPS_EL.innerHTML = html || ""; }
    function setSub(html, alto) {
      if (!SUB_EL) return;
      SUB_EL.style.minHeight = "";
      SUB_EL.innerHTML = html || "";
      // `alto`: the height the line had while the schedule loaded. A shorter
      // line keeps it, so the form below does not jump up (booking-first-paint).
      if (alto && alto > SUB_EL.offsetHeight) SUB_EL.style.minHeight = alto + "px";
    }

    function slotIntro() {
      return T("service.slots.pick_lede", null, "Elegí el turno que mejor te queda. ") +
        (MODE === "turno"
          ? T("service.slots.pick_note_account", null, "Lo pedís con tu cuenta y el hospital te lo confirma.")
          : T("service.slots.pick_note_order", null, "El hospital lo confirma con tu orden."));
    }
    function finishScheduleBoot() {
      host.classList.remove("svc-form-host--schedule-boot", "svc-form-host--schedule-fallback", "svc-form-host--schedule-recovery");
    }
    // An empty agenda is a real next step, not an empty loading region.  Keep
    // the measured first-screen geometry while its actual preference form is
    // mounted, so the support/footer do not jump up into the viewport.
    function settleScheduleFallback() {
      if (host.classList.contains("svc-form-host--schedule-boot")) {
        host.classList.remove("svc-form-host--schedule-boot");
        host.classList.add("svc-form-host--schedule-fallback");
      } else {
        finishScheduleBoot();
      }
    }
    // Los pasos con una foto de la orden al costado (pantalla de duda, confirmá)
    // necesitan más ancho que el resto del wizard para que la foto se lea de
    // verdad en desktop; el resto de los pasos vuelve al ancho angosto normal.
    // "full": the page's own content width, the same right edge as every
    // other page (the slot grid; hr-patient review 15.11, #20).
    function widenHost(px) { host.style.maxWidth = px === "full" ? "none" : px ? px + "px" : ""; }
    /* Confirmá es una página blanca con un solo nivel de recuadros: la
       tarjeta del turno (responsable del producto, 2026-10-27). El fondo es el del <body>, así
       que el paso lo pide con una clase y cualquier otro paso la saca. */
    function paginaBlanca(on) {
      var b = typeof document !== "undefined" && document.body;
      if (b && b.classList) b.classList.toggle("wz-pagina-blanca", !!on);
    }

    /* ---------- arranque sin parpadeo (responsable del producto, 2026-07-27) ----------
       Llegar con el turno ya elegido (?slot=) y la sesión abierta significa que
       los pasos "Cuándo" y "Tu cuenta" se resuelven SOLOS: la persona no toca
       nada, sólo espera dos fetches. Pintar el cromo completo de cada uno de
       esos pasos (título + contador + spinner propio) hacía aparecer dos
       pantallas enteras durante una fracción de segundo antes de Confirmá.
       Mientras el wizard avanza sin pedirle nada a nadie se muestra UNA sola
       pantalla de espera y el título de la página no cambia; el primer paso
       que de verdad necesita a la persona la reemplaza. Todo paso que pinta
       pasa por stepHeader(), así que ahí se apaga el modo espera. */
    var avanzandoSolo = !!slotParam() && (MODE === "turno" || ELEGIR_PRIMERO || !!ordenPrecargada);
    function pantallaEspera() {
      if (!avanzandoSolo) return false;
      if (!host.querySelector(".wz-boot")) {
        host.innerHTML = '<div class="sec"><div class="wz-card wz-boot">' +
          '<div class="wz-spin"><i></i> ' + T("service.wait.preparing", null, "Preparando tu solicitud…") + '</div></div></div>';
      }
      return true;
    }

    /* Lo que el arranque automático va a necesitar sí o sí, pedido EN PARALELO
       con los turnos en vez de en cadena: sin esto la espera era la suma de
       tres viajes al servidor (turnos → sesión → personas a cargo). */
    var sesionPrefetch = null, depsPrefetch = null;
    function fetchSesion() {
      return fetch("/api/account/me", { credentials: "include" })
        .then(function (r) { return r.ok ? r.json().catch(function () { return null; }) : null; })
        .catch(function () { return null; });
    }

    /* ---------- historial: cada pantalla del wizard es un back-stop real
       (the product owner, 2026-07-15: "check that the back button works for all the
       wizard steps"). La MISMA url (nunca cambia dentro del wizard, salvo el
       cambio de centro): la primera pantalla reemplaza la entrada de la
       navegación real (replaceState), cada pantalla NUEVA suma una entrada
       (pushState), un refresco de la MISMA pantalla (ej. re-render tras
       editar un campo) no suma nada. popstate vuelve a invocar la función del
       paso — todo lo que necesita ya vive en `wiz`, sin volver a pedirle nada
       al servidor salvo turnos (cacheados en wiz.turnosData). El envío final
       usa replaceState (no pushState): "Atrás" desde "Solicitud enviada"
       nunca puede reaparecer en Confirmá con el botón Enviar habilitado. */
    var currentStep = null, historyInited = false, replaying = false;
    var coverageDispose = null, receiptHistory = null;
    function disposeCoverage() { if (coverageDispose) coverageDispose(); coverageDispose = null; }
    function setStep(name) {
      if (name !== "success") disposeCoverage();
      if (replaying) { currentStep = name; return; }
      if (name === currentStep) return;
      currentStep = name;
      var url = location.pathname + location.search;
      try {
        if (!historyInited) { historyInited = true; history.replaceState({ wzStep: name }, "", url); }
        else history.pushState({ wzStep: name }, "", url);
      } catch (e) {}
    }
    function markSuccess() {
      currentStep = "success";
      if (replaying) return;
      try { history.replaceState({ wzStep: "success", wzRequestId: Number((wiz.successResp || {}).id) || null }, "", location.pathname + location.search); } catch (e) {}
    }
    function replayStep(name) {
      replaying = true;
      // When going back to a step before slots, clear the ?slot= parameter (issue 4)
      if (name !== "slots" && slotParam()) {
        recordarSlotEnUrl(null, null);
      }
      try {
        switch (name) {
          case "orden": stepOrden(); break;
          case "no-orden": stepNoOrden(); break;
          case "doc-dudoso": stepDocDudoso(); break;
          case "slots": if (wiz.turnosData) renderSlots(wiz.turnosData); else stepHorario(); break;
          case "franja": stepFranja(); break;
          case "auth": mountAuth(); break;
          case "paciente-agregar":
            stepAgregarPaciente(function (dep) { if (dep) wiz.paciente = dep; stepConfirm(); });
            break;
          case "paciente-elegir":
            stepElegirPaciente(wiz.dependientes || [], function (dep) { wiz.paciente = dep; stepConfirm(); });
            break;
          case "ofrecer-hijo": stepOfrecerHijo(); break;
          case "confirm": stepConfirm(); break;
          case "success": renderSuccess(); break;
          default: break;
        }
      } finally { replaying = false; }
    }
    window.addEventListener("popstate", function (e) {
      if (receiptHistory) { receiptHistory(e); return; }
      var st = e && e.state;
      if (st && st.wzStep) replayStep(st.wzStep);
    });

    function stepHeader(key) {
      var title = KEY_TITLE[key];
      var n = STEP_KEYS.indexOf(key) + 1;
      avanzandoSolo = false;  // esta pantalla la mira una persona: se acabó la espera
      // El reloj del hold vive sólo en Confirmá: cualquier pintura lo apaga y
      // Confirmá lo vuelve a prender (si no, tickea contra un nodo que ya no está).
      holdTickerStop();
      setTitle(STEP_TITLE[key] || title);
      setSub("");  // el paso que tenga subtítulo lo re-pone después
      // El contacto de escape vive adentro de la tarjeta sólo en Confirmá.
      ayudaEnShell(key !== "confirm");
      paginaBlanca(key === "confirm");
      /* "1 de 1" no informa nada. Two or more estimated interaction steps
         remain visible throughout; skipped transport/fetch stages never get
         their own circles. */
      if (STEP_KEYS.length < 2) { setSteps(""); return ""; }
      var items = "";
      for (var i = 1; i <= STEP_KEYS.length; i++) {
        var cls = i === n ? "on" : (i < n ? "done" : "");
        var mark = i < n ? CHECK_ICON : String(i);
        items += '<li class="wz-st ' + cls + '"><span class="wz-st-n">' + mark + "</span></li>";
      }
      setSteps('<ol class="wz-stepper" aria-label="' +
        esc(T("service.step.aria", { n: n, total: STEP_KEYS.length }, "Paso {n} de {total}")) +
        '">' + items + "</ol>");
      return "";  // el contador ya no vive dentro del cuerpo (#svc-steps al lado del título)
    }

    /* ---------- utilidades de nombre / teléfono / dni ---------- */
    function splitNombre(full) {
      full = String(full || "").trim();
      if (!full) return { nombre: "", apellido: "" };
      if (full.indexOf(",") >= 0) { // "APELLIDO, Nombre" (forma más común en órdenes AR)
        var p = full.split(",");
        return { apellido: p[0].trim(), nombre: p.slice(1).join(",").trim() };
      }
      var t = full.split(/\s+/);
      if (t.length === 1) return { nombre: t[0], apellido: "" };
      return { nombre: t[0], apellido: t.slice(1).join(" ") }; // "Nombre Apellido"
    }
    function normPhone(s) {
      s = String(s || "").trim();
      if (!s) return "";
      var plus = s.charAt(0) === "+" ? "+" : "";
      return plus + s.replace(/[^\d]/g, "");
    }
    function onlyDigits(s) { return Format.digits(s); }
    function formatDni(s) {
      return Format.dni(s);
    }
    /* Cómo se escribe un teléfono lo decide format.mjs para TODO el sitio
       (responsable del producto, 2026-07-28) — el wizard tenía su propia versión, que era la
       quinta copia y además cortaba el característico en 4 dígitos siempre.
       El primer render espera formatLista, así que no existe una copia local. */
    function formatPhone(s) {
      return Format.telTexto(s);
    }

    function ageSignature(s) {
      var e = (s && s.edad) || {};
      return String(e.min == null ? "" : e.min) + ":" + String(e.max == null ? "" : e.max);
    }
    function equivalentCentros() {
      return Promise.all([fetchServicios(), fetchCentros()]).then(function (all) {
        var servicios = all[0], centros = all[1], bySlug = {}, seen = {};
        centros.forEach(function (c) { bySlug[c.slug] = c; });
        return servicios.filter(function (s) {
          if (!s || s.id === servicio.id || s.modo !== servicio.modo || ageSignature(s) !== ageSignature(servicio)) return false;
          if (normTxt(s.nombre) !== normTxt(servicio.nombre)) return false;
          var same = !servicio.cluster || !s.cluster || s.cluster === servicio.cluster;
          if (!same || seen[s.centro]) return false;
          seen[s.centro] = true;
          return true;
        }).map(function (s) { return { servicio: s, centro: bySlug[s.centro] || { slug: s.centro, name: s.centro } }; });
      });
    }
    function setServicioCentro(nextServicio, nextCentro) {
      servicio = nextServicio;
      centro = nextCentro || {};
      centroNombre = centro.name || "";
      var crumb = document.querySelector(".svc-crumb"), serviceHref = "/service/" + encodeURIComponent(servicio.id), centroHref = "/center/" + encodeURIComponent(centro.slug || servicio.centro || "");
      var cluster = data.cluster || {}, clusterHref = "/d/" + encodeURIComponent(servicio.cluster || cluster.id || "");
      if (crumb) {
        crumb.innerHTML = (centroNombre ? '<a href="' + esc(centroHref) + '">' + esc(centroNombre) + "</a>" : "") +
          (centroNombre ? ' <span class="svc-crumb-sep">›</span> ' : "") +
          (cluster.nombre ? '<a href="' + esc(clusterHref) + '">' + esc(cluster.nombre) + "</a>" : "") +
          (cluster.nombre ? ' <span class="svc-crumb-sep">›</span> ' : "") +
          '<a href="' + esc(serviceHref) + '">' + esc(servicio.nombre) + "</a>";
      }
      // replaceState con state:null pisaría el wzStep de la entrada actual
      // (quedaría un back-stop mudo, sin pantalla que reproducir al volver) —
      // se preserva el paso en curso, sólo cambia la URL al nuevo centro.
      try { window.history.replaceState({ wzStep: currentStep }, "", serviceHref + "/turno"); } catch (e) {}
    }

    function buildPrefill() {
      // La orden puede pertenecer a un hijo. Nunca usar su identidad para crear
      // accidentalmente la cuenta del adulto; el titular se prellena sólo con
      // lo que este navegador ya conoce.
      var p = {};
      var ident = loadIdent();
      ["dni", "nombre", "apellido", "nacimiento", "sexo", "contacto"].forEach(function (k) {
        if (ident[k]) p[k] = ident[k];
      });
      return p;
    }

    /* =================== PASO 1 · Tu orden =================== */
    /* Control de subida ÚNICO (wizard-design regla 6): el mismo markup en el
       paso 1 y en la pantalla de duda — vive en wz-upload.js (compartido con
       preparacion.js), acá sólo se invoca. */
    function uploadZoneHtml() {
      return zoneHtml();
    }
    function wireUploadZone() {
      // /api/analyze-order está detrás de abuso.py: sin widget montado, el
      // análisis se come un 403 y la IA queda muerta en producción. Montarlo
      // antes de entregar el archivo retenido también conserva su nodo: analyze
      // reemplaza host.innerHTML de forma síncrona.
      var ts = $("#wz-ts", host);
      // Every step replaces host.innerHTML. Keep the exact rendered upload
      // step that started this mount, so a delayed provider result cannot
      // paint recovery or submit a file after the user left it.
      var uploadStep = host.firstElementChild;
      var mountFailed = false;
      var mountPending = false;
      var pendingAtMount = null;
      function uploadStepIsCurrent() {
        return !!(uploadStep && host.contains(uploadStep) && ts &&
          ts.isConnected && uploadStep.contains(ts));
      }
      function mountTurnstile() {
        if (!ts || !TS) return Promise.resolve(false);
        try { return Promise.resolve(TS.mount(ts)); }
        catch (e) { return Promise.resolve(false); }
      }
      var turnstileReady = mountTurnstile();
      function showTurnstileMountError() {
        var result = $("#wz-result", host);
        if (!result) {
          result = document.createElement("div");
          result.id = "wz-result";
          host.appendChild(result);
        }
        result.innerHTML = '<div class="wz-soft" style="color:var(--accent)">' +
          esc(T("service.analyze.turnstile_fail", null, "No pudimos verificar que sos una persona. Volvé a subir la orden.")) + "</div>";
      }
      function analyzeWhenReady(file) {
        if (!file || !uploadStepIsCurrent()) return;
        // A native chooser can deliver several change/paste events before
        // Turnstile finishes mounting. One analysis is enough: the newest
        // file is the user's current choice.
        pendingAtMount = file;
        if (mountPending) return;
        mountPending = true;
        if (mountFailed) {
          mountFailed = false;
          turnstileReady = mountTurnstile();
        }
        turnstileReady.then(async function (mounted) {
          mountPending = false;
          var currentFile = pendingAtMount;
          pendingAtMount = null;
          if (!currentFile || !uploadStepIsCurrent()) return;
          if (TS && TS.activo() && !mounted) {
            mountFailed = true;
            showTurnstileMountError();
            return;
          }
          mountPending = true;
          try {
            var tsToken = TS ? await TS.requireToken({onWait:function() {
              var result = $("#wz-result", host);
              if (result && uploadStepIsCurrent()) result.textContent = T("account.form.verifying", null, "Verificando…");
            }}) : "";
            if (uploadStepIsCurrent()) analyze(pendingAtMount || currentFile, tsToken);
          } catch (e) {
            if (uploadStepIsCurrent()) showTurnstileMountError();
            if (TS) TS.reset();
          } finally { mountPending = false; pendingAtMount = null; }
        });
      }
      wireUploadControl(host, analyzeWhenReady);
      var queued = takePendingUploadFile();
      if (queued) analyzeWhenReady(queued);
    }
    var TS_BOX = '<div id="wz-ts" style="margin-top:16px;display:none;"></div>';

    function stepOrden() {
      ensureStep("orden");
      setStep("orden");
      widenHost(0);
      // A chooser the patient already opened on the server-painted input
      // answers to that node: carry it into the zone rendered below.
      var paintedInput = $("#wz-file[data-first-paint]", host);
      host.innerHTML =
        '<div class="sec">' + stepHeader("orden") +
        uploadZoneHtml() +
        TS_BOX +
        '<a class="wz-altcard" id="wz-noorden" href="#" role="button">' +
          '<span class="wz-altcard-txt">' +
            '<span class="wz-altcard-t">' + T("service.order.dont_have", null, "No tengo la orden todavía") + '</span>' +
            '<span class="wz-altcard-s">' + T("service.order.explain", null, "Te explico cómo conseguirla") + '</span>' +
          "</span>" + SVG_CHEV +
        "</a>" +
        '<div id="wz-result"></div></div>';
      setSub(T("service.order.upload_sub", null, "El hospital la revisa y te confirma el turno."));
      adoptPaintedInput(host, paintedInput);
      wireUploadZone();
      $("#wz-noorden", host).addEventListener("click", function (e) { e.preventDefault(); stepNoOrden(); });
    }

    /* ---- Explicador: "No tengo la orden todavía" → cómo pedir la derivación ---- */
    function stepNoOrden() {
      setStep("no-orden");
      widenHost(0);
      var svc = servicio.nombre || T("service.order.generic_study", null, "el estudio");
      var svcLow = svc.charAt(0).toLowerCase() + svc.slice(1);
      var clinicoHref = data.clinico_href || (centro.slug ? "/center/" + centro.slug : "/");
      function step(n, inner) {
        return '<div class="wz-step"><div class="wz-step-n">' + n + '</div><div class="wz-step-t">' + inner + "</div></div>";
      }
      setTitle(T("service.order.need_title", null, "Necesitás una orden médica"));
      setSteps("");
      var svcEsc = esc(svcLow);
      setSub(T("service.order.need_sub", { svc: svcEsc },
        "Para hacerte la {svc}, primero un médico de Medicina General tiene que darte la orden de derivación. Es rápido:"));
      host.innerHTML =
        '<div class="sec">' +
        '<div class="wz-steplist">' +
          step(1, T("service.order.step1", null, "Sacá un turno con <b>Medicina General</b>.")) +
          step(2, T("service.order.step2", { svc: svcEsc }, "Pedile la <b>orden para {svc}</b>.")) +
          step(3, T("service.order.step3", null, "Volvé acá y <b>subí la orden</b>.")) +
        "</div>" +
        '<div class="wz-btnstack">' +
          '<a class="wz-cta-lg" href="' + esc(clinicoHref) + '">' + T("service.order.book_general", null, "Sacar turno con Medicina General") + '</a>' +
          '<button type="button" class="wz-cta-sec" id="wz-hasorden">' + T("service.order.have_it", null, "Ya tengo la orden") + '</button>' +
        "</div></div>";
      $("#wz-hasorden", host).addEventListener("click", stepOrden);
    }

    function analyze(file, tsToken) {
      // El análisis REEMPLAZA el paso (wizard-design regla 3): nada se apila
      // debajo del control que lo produjo.
      host.innerHTML =
        '<div class="sec"><div class="wz-processing" role="status" aria-live="polite">' +
          '<div class="wz-processing-sheet" aria-hidden="true"></div>' +
          '<p class="wz-processing-h">' + T("service.analyze.reading", null, "Estamos leyendo tu orden") + '</p>' +
          '<p class="wz-processing-p">' + T("service.analyze.checking", null, "Revisamos los datos y el estudio pedido. Puede tardar unos segundos.") + '</p>' +
        "</div></div>";
      fileToOrden(file).then(function (orden) {
        if (!orden) { showUploadError(T("service.analyze.cant_read", null, "No pudimos leer el archivo. Probá con otra foto.")); return null; }
        wiz.ordenRaw = orden;
        wiz.ordenFilename = orden.filename;
        wiz.ordenPreview = /^image\//.test(orden.mime) ? ("data:" + orden.mime + ";base64," + orden.data_b64) : null;
        return Promise.resolve(tsToken).then(function (tsToken) {
          return fetch("/api/analyze-order", {
            method: "POST", headers: analyzeHeaders(),
            body: JSON.stringify({
              servicio_id: servicio.id, especialidad: servicio.nombre,
              orden: orden, turnstile_token: tsToken
            })
          });
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
        });
      }).then(function (res) {
        if (!res) return;
        // Errores del GATE (no de la IA): se los mostramos y reintenta en el
        // mismo control. Tragárselos dejaba el análisis muerto sin que nadie
        // lo viera (docs/solutions/ai/turnstile-mata-el-analisis-de-orden.md).
        if (res.status === 403 && res.j && res.j.error === "turnstile") {
          if (TS) TS.reset();
          showUploadError(T("service.analyze.turnstile_fail", null, "No pudimos verificar que sos una persona. Volvé a subir la orden."));
          return;
        }
        if (res.status === 429) {
          showUploadError((res.j && res.j.mensaje) || T("service.analyze.rate_limited", null, "Hiciste varias consultas seguidas. Esperá un rato y volvé a intentar."));
          return;
        }
        if (TS) TS.reset(); // el token es de un solo uso, aunque haya andado
        if (res.ok && res.j && res.j.ok) {
          wiz.token = res.j.token || null;
          wiz.ai = res.j.ai || null;
        } else {
          // el análisis falló (cuota/red): no bloqueamos ni se lo contamos al
          // paciente — la orden se guarda igual y una persona la revisa.
          wiz.token = null; wiz.ai = null;
        }
        // Orden válida (o análisis no disponible) → DERECHO al paso siguiente
        // (wizard-design regla 4: sin pantalla intermedia de "esto leímos").
        // Sólo frenamos cuando el modelo SÍ leyó y no parece una orden médica.
        // El handoff se guarda cuando la orden queda ACEPTADA, no apenas se
        // analiza: un documento que frenó en la pantalla de duda todavía no es
        // la orden de nadie, y persistirlo haría que un refresh se la atribuya.
        var ai = wiz.ai || {};
        if (ai.documento && ai.documento.es_orden === false) stepDocDudoso();
        else { guardarOrdenHandoff(); afterOrden(); }
      }).catch(function () { wiz.token = null; wiz.ai = null; afterOrden(); });
    }

    function showUploadError(msg) {
      stepOrden(); // mismo control para reintentar
      $("#wz-result", host).innerHTML = '<div class="wz-soft" style="color:var(--accent)">' + esc(msg) + "</div>";
    }

    /* ---- Pantalla de duda: el documento no parece una orden médica ----
       Sólo el mensaje + la preview (al costado en desktop) + 3 acciones:
       subir otra (MISMO control), "estoy seguro", "no tengo la orden".
       Sin filas de campos vacías, sin preparación, sin contactos. */
    function stepDocDudoso() {
      setStep("doc-dudoso");
      widenHost(wiz.ordenPreview ? 900 : 0);
      setTitle(T("service.doc.title", null, "¿Es la orden correcta?"));
      setSteps(""); setSub("");
      var tipo = wiz.ai && wiz.ai.documento && wiz.ai.documento.tipo;
      var tipoTexto = String(tipo || T("service.doc.unknown_type", null, "otro tipo de documento")).trim();
      var tipoConArticulo = /^(?:un|una|otro|otra)\b/i.test(tipoTexto)
        ? tipoTexto : "una " + tipoTexto;
      var tipoConArticuloHtml = "<strong>" + esc(tipoConArticulo) + "</strong>";
      host.innerHTML =
        '<div class="sec"><div class="wz-err' + (wiz.ordenPreview ? " wz-err--foto" : "") + '">' +
        '<div class="wz-warn">' + T("service.doc.warning", { tipo: tipoConArticuloHtml },
          "Este documento parece ser {tipo}, no una orden médica. Por favor, subí la orden médica.") + "</div>" +
        (wiz.ordenPreview ?
          '<a class="wz-err-img-btn" href="' + esc(wiz.ordenPreview) + '" download="order-image" aria-label="' + esc(T("service.doc.zoom_aria", null, "Ampliar lo que subiste")) + '" ' +
            'data-photozoom data-photozoom-src="' + esc(wiz.ordenPreview) + '" data-photozoom-alt="' + esc(T("service.doc.zoom_alt", null, "Lo que subiste")) + '">' +
            '<img class="wz-err-img" src="' + esc(wiz.ordenPreview) + '" alt="' + esc(T("service.doc.zoom_alt", null, "Lo que subiste")) + '"></a>' : "") +
        '<div class="wz-err-act">' +
          uploadZoneHtml() + TS_BOX +
          '<div class="wz-btnstack">' +
            '<button type="button" class="wz-cta-sec" id="wz-seguro">' + T("service.doc.confirm_yes", null, "Estoy seguro: es la orden correcta") + '</button>' +
            '<button type="button" class="wz-cta-sec" id="wz-sinorden">' + T("service.doc.dont_have", null, "Todavía no tengo la orden") + '</button>' +
          "</div>" +
        "</div></div></div>";
      wireUploadZone();
      // "Estoy seguro": recién ahí ese documento pasa a ser SU orden.
      $("#wz-seguro", host).addEventListener("click", function () {
        guardarOrdenHandoff();
        afterOrden();
      });
      $("#wz-sinorden", host).addEventListener("click", function () {
        wiz.ai = null; wiz.token = null; wiz.ordenRaw = null; wiz.ordenPreview = null; wiz.ordenFilename = null; wiz.ordenNoEsFoto = null; olvidarOrdenHandoff();
        stepNoOrden();
      });
    }

    /* Fechas amigables (fechas.js / wizard-design regla 8): "Hoy" / "Mañana,
       miércoles 11" / "Martes 14 de julio" para MOSTRAR (mañana con su día:
       sobre las horas de la mañana «Mañana» solo se leía «a la mañana»);
       `larga` (siempre absoluta) para el texto que se GUARDA como
       preferencia — lo relativo se pudre en el admin. */
    var F = Dates;
    function friendlyDia(iso) {
      if (F) { var d = F.diaFechado(iso); if (d) return d; }
      var p = String(iso || "").split("-");
      return p.length === 3 ? p[2] + "/" + p[1] : (iso || "");
    }
    function slotGuardado(iso, hora) {
      if (F) { var d = F.larga(iso, hora); if (d) return d; }
      return String(iso || "") + (hora ? " " + hora : "");
    }

    /* La agenda real (Mi Salud Digital) decide si mostramos turnos concretos o
       la preferencia por franja. Sin agenda online → franja (solicitud c/orden). */
    // Notificación de un solo uso que renderSlots muestra arriba de la grilla
    // (p.ej. "otra persona tomó ese horario") y se limpia sola al mostrarse.
    var horarioNotice = null;
    // A slot the person had chosen and lost (taken by someone else, or gone
    // from the agenda): the notice names it, and the grid opens on that day
    // so the neighbouring times are the first thing offered — never a reset
    // to the first day of the agenda.
    var diaPerdido = "";
    var DIAS_VISIBLES = 2;
    function perdido(dia, hora, motivo) {
      diaPerdido = dia || "";
      var when = dia ? slotGuardado(dia, hora) : "";
      if (motivo === "taken") {
        horarioNotice = when
          ? T("service.slots.notice_taken_when", { when: when }, "{when}: otra persona acaba de tomar ese turno. Elegí otro horario de ese día o de los siguientes.")
          : T("service.slots.notice_taken", null, "Ese horario lo acaba de tomar otra persona. Elegí otro.");
      } else {
        horarioNotice = when
          ? T("service.slots.notice_gone_when", { when: when }, "{when}: ese turno ya no está libre. Elegí otro horario de ese día o de los siguientes.")
          : T("service.slots.notice_gone", null, "El horario que habías elegido ya no está disponible. Elegí otro.");
      }
    }
    function stepHorario() {
      // Slot selection ends at 1000px on desktop. Keep that geometry from
      // the server-owned shell through the API-backed grid.
      widenHost("full");
      if (!pantallaEspera()) {
        host.innerHTML = '<div class="sec">' + stepHeader("horario") +
          '<div class="wz-card"><div class="wz-spin"><i></i> ' + T("service.slots.loading", null, "Buscando turnos disponibles…") + '</div></div></div>';
        setSub(slotIntro());
      }
      // ?hold=<token>: si este cliente ya tiene un slot tomado, que la grilla
      // se lo siga mostrando a ÉL (soft-lock, 2026-07-16).
      var query = [];
      if (wiz.holdToken) query.push("hold=" + encodeURIComponent(wiz.holdToken));
      if (PREFERRED_MEDICO) query.push("prefer_medico=" + encodeURIComponent(PREFERRED_MEDICO));
      var url = "/api/service-slots/" + encodeURIComponent(servicio.id) +
        (query.length ? "?" + query.join("&") : "");
      fetch(url, { credentials: "include" })
        .then(function (r) { return r.ok ? r.json().catch(function () { return {}; }) : {}; })
        .then(function (d) {
          if (d && d.medicos && d.medicos.length) {
            // Turno ya elegido en el listado (?slot=) y todavía libre → lo
            // tomamos y vamos derecho al paso siguiente (responsable del producto, 2026-07-14). Si
            // justo lo tomó otra persona, se muestra la grilla igual — nunca
            // avanzar sin el hold en mano.
            var pre = !slotSaltado && slotEnAgenda(d, slotParam());
            // El turno que traía la URL ya no está en la agenda (lo tomaron, o
            // el médico cambió su agenda). Sin decirlo, la grilla aparece de la
            // nada y parece que se hubiera ignorado lo que la persona eligió.
            if (!pre && !slotSaltado && slotParam()) {
              slotSaltado = true;
              perdido(String(slotParam()).split("T")[0], String(slotParam()).split("T")[1] || "", "gone");
            }
            if (pre) {
              slotSaltado = true;
              holdTomar(pre.dia, pre.hora).then(function (ok) {
                if (!ok) {
                  perdido(pre.dia, pre.hora, "taken");
                  renderSlots(d);
                  return;
                }
                wiz.preferencia = pre.preferencia; wiz.sinTurno = false;
                wiz.slotMedico = pre.medico;
                wiz.slotMedicoId = pre.medico_id || null;
                wiz.slotCuando = pre.cuando;
                wiz.slotDia = pre.dia;
                wiz.slotHora = pre.hora;
                afterHorario();
              });
              return;
            }
            renderSlots(d);
          } else stepFranja(d);
        })
        .catch(stepFranja);
    }

    /* Los listados y la página del servicio ofrecen un turno concreto como botón
       (?slot=YYYY-MM-DDTHH:MM). Llega acá YA elegido: el paso de horarios NO se
       vuelve a mostrar — derecho al paso siguiente (the product owner, 2026-07-14: "don't
       show the times again, show the thing I already chose"; revoca el
       comportamiento 2026-07-12 de mostrar la grilla igual). La grilla queda
       para "más horarios" / "Cambiar". Si el slot ya no existe (la agenda
       cambió), se muestra la grilla normal sin preselección. */
    function slotParam() {
      try {
        var m = /[?&]slot=([^&]+)/.exec(window.location.search);
        return m ? decodeURIComponent(m[1]) : "";
      } catch (e) { return ""; }
    }

    // ¿el ?slot= sigue libre en la agenda? Devuelve los datos del turno o null.
    function slotEnAgenda(d, want) {
      if (!want || !d || !d.medicos) return null;
      for (var i = 0; i < d.medicos.length; i++) {
        var m = d.medicos[i], ts = m.turnos || [];
        for (var j = 0; j < ts.length; j++) {
          if (ts[j].dia + "T" + ts[j].hora === want) {
            var medico = m.medico || "";
            var cuando = slotGuardado(ts[j].dia, ts[j].hora);
            return { preferencia: medico ? medico + " · " + cuando : cuando,
                     medico: medico, medico_id: m.medico_id || "",
                     cuando: cuando, dia: ts[j].dia, hora: ts[j].hora };
          }
        }
      }
      return null;
    }
    var slotSaltado = false; // el ?slot= salta la grilla UNA sola vez ("Cambiar" sí la muestra)

    /* Con ?slots=1 los pasos se invierten, así que "qué sigue" depende de
       qué FALTA, no de un orden fijo: tras elegir el turno falta la orden si
       todavía no se subió; tras la orden falta el turno si aún no se eligió. */
    function afterHorario() {
      // The first-paint reservation belongs only to the schedule, including
      // its empty-agenda fallback. Later steps own their natural geometry.
      finishScheduleBoot();
      if (MODE === "orden" && !wiz.token && !wiz.ordenRaw) stepOrden();
      else stepCuenta();
    }
    function afterOrden() {
      if (wiz.preferencia) stepCuenta();
      else stepHorario();
    }

    /* Qué es y dónde atiende, bajo el nombre: "Médico general · CAPS Pinamar"
       (responsable del producto, 2026-07-28). La especialidad es lo que dice si esa persona es
       quien corresponde — el nombre solo no alcanza para elegir. Las dos van
       linkeadas a su página, como en los listados: revierte la decisión del
       2026-07-27 de dejar el lugar como texto suelto. La sede queda texto:
       no tiene página propia. */
    function quienDonde(m) {
      var partes = [];
      if (m.especialidad) {
        partes.push(m.esp_href
          ? '<a href="' + esc(m.esp_href) + '">' + esc(m.especialidad) + "</a>"
          : esc(m.especialidad));
      }
      if (m.sede) partes.push(esc(m.sede));
      if (m.centro_nombre) {
        partes.push(m.centro_href
          ? '<a href="' + esc(m.centro_href) + '">' + esc(m.centro_nombre) + "</a>"
          : esc(m.centro_nombre));
      } else if (!partes.length && m.lugar) {
        partes.push(esc(m.lugar));
      }
      return partes.length
        ? '<div class="wz-medico-lugar">' + partes.join(" · ") + "</div>"
        : "";
    }

    function renderSlots(d) {
      ensureStep("horario");
      evStep("slots_shown");
      setStep("slots");
      widenHost("full");
      finishScheduleBoot();
      wiz.turnosData = d; // cache: el back button reproduce este paso sin volver a pedirlo al servidor
      var want = slotParam(), picked = false;  // dos médicos pueden tener el MISMO horario: preselecciona uno solo
      // Volver acá con "Atrás" después de haber elegido un turno (wiz.slotCuando
      // ya cargado) tiene que resaltar ESE turno, no sólo el de ?slot= inicial.
      var wantMedico = wiz.slotMedico, wantCuando = wiz.slotCuando;
      var medicos = (d.medicos || []).slice();
      if (PREFERRED_MEDICO) {
        var preferidoExiste = medicos.some(function (m) { return m.medico_id === PREFERRED_MEDICO; });
        // Entrar desde la página de una persona significa ver SUS horarios.
        // La agenda genérica del servicio no tiene profesional asignado y no
        // debe aparecer como un segundo bloque anónimo llamado "Horarios".
        if (preferidoExiste) {
          medicos = medicos.filter(function (m) { return m.medico_id === PREFERRED_MEDICO; });
        }
        medicos.sort(function (a, b) {
          return (b.medico_id === PREFERRED_MEDICO ? 1 : 0) -
            (a.medico_id === PREFERRED_MEDICO ? 1 : 0);
        });
      }
      var blocks = medicos.map(function (m, idx) {
        // Los turnos derivados de la agenda vienen SIN médico (el hospital lo
        // asigna al aprobar): sin nombre no va el encabezado ni el " · " suelto.
        var medico = m.medico || "";
        // Cada bloque tiene SU calendario: el filtro es de esa agenda, no del
        // paso (the product owner, 2026-07-28: "add it to all the doctors"). Con uno solo y
        // global, elegir un día que otra persona no atiende le borraba el
        // bloque entero de la pantalla.
        var key = m.medico_id || medico || "bloque-" + idx;
        var byDay = {};
        (m.turnos || []).forEach(function (t) { (byDay[t.dia] = byDay[t.dia] || []).push(t); });
        // The first screen offers the first DIAS_VISIBLES agenda days, starting
        // from the day the person was already on (a slot just lost, a slot
        // chosen before «Atrás», the listing's ?slot=); «Otras fechas» opens
        // the calendar for the rest. Picking a date there shows that one day.
        // Without a calendar every day stays listed — nothing may become
        // unreachable.
        var allDays = Object.keys(byDay).sort();
        var filtro = wiz.slotFilterDia[key] || "";
        var ancla = diaPerdido || wiz.slotDia || String(want || "").split("T")[0] || "";
        var desde = 0;
        if (ancla) {
          for (var ai = 0; ai < allDays.length; ai++) { if (allDays[ai] >= ancla) { desde = ai; break; } }
        }
        var visibleDays = filtro
          ? allDays.filter(function (dia) { return dia === filtro; })
          : (Cal ? allDays.slice(desde, desde + DIAS_VISIBLES) : allDays);
        if (!visibleDays.length) visibleDays = allDays.slice(0, DIAS_VISIBLES);
        var abierto = !!wiz.calendarOpen[key];
        var calId = "wz-calendar-" + idx;
        var calendar = Cal && allDays.length
          ? Cal.gridHtml(allDays.map(function (dia) {
              return { dia: dia, className: "medcal-libre", title: T("service.slots.available_title", null, "Hay turnos disponibles") };
            }), {
              selected: filtro || wiz.slotDia || "",
              dateAttr: "data-slot-date",
              disableMissing: true
            })
          : "";
        // The calendar follows its trigger at the end of the offered slots.
        var calendarPanel = calendar
          ? '<div class="wz-calendar" id="' + calId + '"' + (abierto ? "" : " hidden") + ">" +
              calendar + '<p class="wz-calendar-key"><i></i>' + T("service.slots.calendar_key", null, "Verde: hay turnos disponibles.") + '</p></div>'
          : "";
        var days = visibleDays.map(function (dia) {
          var chips = byDay[dia].map(function (t) {
            var cuando = slotGuardado(dia, t.hora);
            var slot = medico ? medico + " · " + cuando : cuando;
            var preQuery = !picked && want && want === dia + "T" + t.hora;
            var prePrev = !picked && !want && wantCuando && cuando === wantCuando && medico === (wantMedico || "");
            var pre = preQuery || prePrev;
            if (pre) {
              picked = true;
              wiz.preferencia = slot; wiz.sinTurno = false;
              wiz.slotMedico = medico;
              wiz.slotMedicoId = m.medico_id || null;
              wiz.slotCuando = cuando;
            }
            return '<button type="button" class="wz-seg-opt' + (pre ? " on" : "") + '" data-slot="' + esc(slot) +
              '" data-medico="' + esc(medico) + '" data-medico-id="' + esc(m.medico_id || "") +
              '" data-cuando="' + esc(cuando) +
              '" data-dia="' + esc(dia) + '" data-hora="' + esc(t.hora) + '">' + esc(t.hora) + "</button>";
          }).join("");
          return '<div class="wz-day"><div class="wz-day-head"><div class="wz-day-h">' +
            esc(friendlyDia(dia)) + "</div></div>" + '<div class="wz-seg">' +
            chips + "</div></div>";
        }).join("");
        if (!days) return "";
        var otras = calendarPanel
          ? '<div class="wz-otras-row"><button type="button" class="wz-otras" data-cal="' + esc(calId) + '" aria-controls="' +
            esc(calId) + '" aria-expanded="' + (abierto ? "true" : "false") + '">' + T("service.slots.other_dates", null, "Otras fechas") + '</button></div>'
          : "";
        var nombre = medico
          ? (m.medico_href
            ? '<a class="wz-medico-n" href="' + esc(m.medico_href) + '">' + esc(medico) + "</a>"
            : '<div class="wz-medico-n wz-medico-n--plain">' + esc(medico) + "</div>")
          : "";
        return '<div class="wz-medico" data-key="' + esc(key) + '"><div class="wz-medico-head">' + nombre +
          quienDonde(m) + "</div>" + days + otras + calendarPanel + "</div>";
      }).filter(Boolean).join("");

      var notice = horarioNotice
        ? '<div class="wz-soft" style="color:var(--accent)">' + esc(horarioNotice) + "</div>" : "";
      horarioNotice = null;  // se muestra una sola vez
      host.innerHTML = '<div class="sec">' + stepHeader("horario") + notice +
        '<div class="wz-card wz-card--slots">' +
          '<div class="wz-slot-blocks">' + blocks + "</div>" +
        "</div></div>";
      setSub(slotIntro());

      // Elegir un horario ES la acción del paso: al tocarlo se avanza solo,
      // sin un "Continuar" de más (wizard-design regla 4, responsable del producto 2026-07-15).
      // El click primero toma el hold del slot (soft-lock, 2026-07-16): si
      // otra persona lo tomó justo antes, NO se avanza — se avisa y se
      // refresca la grilla con los horarios que sigan libres.
      var card = $(".wz-card", host);
      card.addEventListener("click", function (e) {
        // "Otras fechas" abre (o cierra) el calendario de ESE médico.
        var trigger = e.target.closest(".wz-otras");
        if (trigger) {
          var panel = document.getElementById(trigger.getAttribute("data-cal"));
          if (!panel) return;
          var bloque = trigger.closest(".wz-medico");
          var key = bloque ? bloque.getAttribute("data-key") : "";
          var abrir = panel.hidden;
          panel.hidden = !abrir;
          if (key) wiz.calendarOpen[key] = abrir;
          trigger.setAttribute("aria-expanded", abrir ? "true" : "false");
          if (abrir) {
            var foco = panel.querySelector(".is-selected") || panel.querySelector(".medcal-libre");
            if (foco) foco.focus();
          }
          return;
        }
        var day = e.target.closest("[data-slot-date]");
        if (day) {
          if (day.disabled) return;
          var caja = day.closest(".wz-medico");
          var dkey = caja ? caja.getAttribute("data-key") : "";
          if (dkey) {
            wiz.slotFilterDia[dkey] = day.getAttribute("data-slot-date");
            wiz.calendarOpen[dkey] = false;
          }
          renderSlots(d);
          return;
        }
        var b = e.target.closest("[data-slot]"); if (!b || b.disabled) return;
        var dia = b.getAttribute("data-dia"), hora = b.getAttribute("data-hora");
        var slot = b.getAttribute("data-slot"), medico = b.getAttribute("data-medico"),
            medicoId = b.getAttribute("data-medico-id"),
            cuando = b.getAttribute("data-cuando");
        b.disabled = true;
        holdTomar(dia, hora).then(function (ok) {
          if (!ok) {
            perdido(dia, hora, "taken");
            stepHorario();  // grilla fresca (?hold= sigue mostrando lo que este cliente ya tenga)
            return;
          }
          wiz.preferencia = slot; wiz.sinTurno = false;
          wiz.slotMedico = medico;
          wiz.slotMedicoId = medicoId || null;
          wiz.slotCuando = cuando;
          wiz.slotDia = dia;
          wiz.slotHora = hora; diaPerdido = "";
          recordarSlotEnUrl(dia, hora);
          card.querySelectorAll(".wz-seg-opt").forEach(function (el) { el.classList.toggle("on", el === b); });
          b.disabled = false;
          afterHorario();
        });
      });
    }

    /* Sin turnos libres (contraparte técnica, hospital, 2026-10-17: «Ahora no hay turnos y
       tres opciones»). Reemplaza la franja + «A partir de», que se leían como
       una promesa: (1) cuándo vuelve a haber turnos, calculado en el server
       con la anticipación real y las agendas; (2) «Contanos tu caso» — texto
       libre que viaja con la
       misma solicitud sin horario de siempre y la mesa lo lee en la cola.
       Cada opción que no tiene un dato real se omite, nunca se inventa. */
    /* «Contanos tu caso» como conversación (responsable del producto, 2026-11-07; contraparte técnica 19.10:
       «evaluamos tu caso»): el motor de la entrevista previa hace 1-3
       preguntas (qué necesita, desde cuándo, cuánto puede esperar) y la mesa
       recibe la síntesis más el chat. El token vive en `wiz`, así que volver
       a este paso retoma la misma conversación. Sin IA, sin base o sin red
       el server dice `disponible:false` y queda el campo de siempre
       (`sinChat`): la solicitud se manda igual. */
    function casoChatStart(host, casoMsg, sinChat) {
      var box = $("#wz-casochat", host), msgs = $("#wz-casochat-msgs", host), form = $("#wz-casochat-form", host);
      var lista = null, comp = null, activo = false, ocupado = false;
      function apagar() { activo = false; box.hidden = true; box.classList.remove("wzc--cargando"); sinChat(); }
      function opciones(arr) {
        if (!lista || !arr || !arr.length || wiz.casoChatFin) return;
        lista.addOpciones(arr, function (etq) { enviar(etq); });
      }
      function respuesta(j) {
        lista.addBubble("assistant", j.texto || "");
        wiz.casoChatLog.push({ role: "assistant", texto: j.texto || "", opciones: j.opciones || null });
        if (j.fin) {
          wiz.casoChatFin = true;
          form.hidden = true;
        } else {
          opciones(j.opciones);
        }
      }
      function enviar(texto) {
        texto = String(texto || "").trim();
        if (!texto || ocupado || wiz.casoChatFin || !wiz.casoChatToken) return;
        ocupado = true;
        casoMsg.textContent = "";
        msgs.querySelectorAll(".wzc-chips").forEach(function (r) { r.remove(); });
        lista.addBubble("user", texto);
        wiz.casoChatLog.push({ role: "user", texto: texto });
        if (comp) { comp.setValue(""); comp.setDisabled(true); }
        lista.setTyping(true);
        fetch("/api/intake/case/" + encodeURIComponent(wiz.casoChatToken) + "/message", {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ texto: texto })
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
        }).then(function (res) {
          lista.setTyping(false);
          if (res.status === 409) { wiz.casoChatFin = true; form.hidden = true; return; }
          if (!res.ok) throw new Error(String(res.status));
          respuesta(res.j);
        }).catch(function () {
          lista.setTyping(false);
          casoMsg.textContent = T("service.noslots.case_chat_error", null, "No se pudo enviar. Probá de nuevo o tocá Continuar.");
        }).then(function () {
          ocupado = false;
          if (comp) { comp.setDisabled(false); if (!wiz.casoChatFin) comp.focus(); }
        });
      }
      function pintar() {
        activo = true;
        box.hidden = false;
        return chatUi().then(function (Ui) {
          if (!Ui) { apagar(); return; }
          box.classList.remove("wzc--cargando");
          msgs.textContent = "";
          lista = Ui.crearLista(msgs, { prefix: "wzc" });
          wiz.casoChatLog.forEach(function (m) { lista.addBubble(m.role, m.texto); });
          var ultimo = wiz.casoChatLog[wiz.casoChatLog.length - 1];
          if (ultimo && ultimo.role === "assistant") opciones(ultimo.opciones);
          comp = Ui.crearComposer(form, {
            prefix: "wzc", attach: false, maxLength: 1000, rows: 2,
            placeholder: T("service.noslots.case_chat_placeholder", null, "Escribí tu respuesta…"),
            onEnviar: function (texto) { enviar(texto); }
          });
          form.hidden = !!wiz.casoChatFin;
        });
      }
      if (wiz.casoChatToken && wiz.casoChatLog && wiz.casoChatLog.length) {
        pintar();
      } else if (wiz.caso && !wiz.casoChatToken) {
        // Ya escribió su caso en el campo (volvió atrás): sigue el campo.
        sinChat();
      } else {
        activo = true;
        // El lugar del chat se reserva ya (min-height en .wzc) con los
        // puntos de «escribiendo»: la primera pregunta llega sin correr
        // el botón Continuar (CLS de booking-first-paint).
        box.hidden = false;
        box.classList.add("wzc--cargando");
        chatUi().then(function (Ui) {
          if (Ui && !lista && box.classList.contains("wzc--cargando")) Ui.crearLista(msgs, { prefix: "wzc" }).setTyping(true);
        });
        fetch("/api/intake/case/start", {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" }, body: "{}"
        }).then(function (r) { return r.ok ? r.json() : { disponible: false }; })
          .catch(function () { return { disponible: false }; })
          .then(function (j) {
            if (!host.contains(box)) return;
            if (!j || !j.disponible || !j.token) { apagar(); return; }
            wiz.casoChatToken = j.token;
            wiz.casoChatFin = !!j.fin;
            wiz.casoChatLog = [{ role: "assistant", texto: j.texto || "", opciones: j.opciones || null }];
            pintar();
          });
      }
      return {
        activo: function () { return activo; },
        focus: function () { if (comp) comp.focus(); },
        palabras: function () {
          return (wiz.casoChatLog || []).filter(function (m) { return m.role === "user"; })
            .map(function (m) { return m.texto; }).join(" · ").slice(0, 1000);
        },
        // La síntesis se calcula una vez al cerrar; Confirmá la espera un
        // rato (casoChatListo) antes de mandar la solicitud.
        terminar: function () {
          var t = wiz.casoChatToken;
          wiz.casoChatFinish = fetch("/api/intake/case/" + encodeURIComponent(t) + "/finish", {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" }, body: "{}"
          }).catch(function () {});
        }
      };
    }
    var chatUiPromise = null;
    function chatUi() {
      if (!chatUiPromise) chatUiPromise = importStatic("platform/chat-ui.mjs").catch(function () { return null; });
      return chatUiPromise;
    }
    function casoChatListo() {
      if (!wiz.casoChat || !wiz.casoChatFinish) return Promise.resolve();
      return Promise.race([wiz.casoChatFinish, new Promise(function (ok) { setTimeout(ok, 15000); })]);
    }

    function stepFranja(d) {
      ensureStep("horario");
      setStep("franja");
      settleScheduleFallback();
      if (d && typeof d === "object" && !(d instanceof Error)) wiz.sinTurnos = d.sin_turnos || null;
      var info = wiz.sinTurnos || {};
      var opciones = [];
      var ap = info.apertura || null;
      if (ap && ap.estado === "fecha" && ap.dia) {
        var cuando = (Dates && Dates.amigable(ap.dia, ap.hora)) || ap.dia;
        // Contraparte técnica, 19/10: «Cuándo vuelve» se leía como la fecha del turno;
        // es la fecha en que se HABILITA la carga — cero ambigüedad.
        // The lead above the card (setSub below) already says there are no
        // turnos; the option holds only the date (TASK-15: said once).
        opciones.push('<div class="wz-noslots-opt" id="wz-noslots-when">' +
          '<p class="wz-noslots-txt">' + T("service.noslots.opens_from", null, "Se habilitan a partir de:") + "</p>" +
          '<p class="wz-noslots-val">' + esc(cuando) + "</p></div>");
      }
      // Con la orden ya subida, la solicitud se sostiene sola: el caso suma
      // contexto pero no es obligatorio. Sin orden, el caso ES la solicitud.
      var casoOpcional = MODE === "orden";
      opciones.push('<div class="wz-noslots-opt" id="wz-noslots-case">' +
        '<h3 class="wz-noslots-h"><label for="wz-caso">' + T("service.noslots.case_label", null, "Contanos tu caso") +
          (casoOpcional ? ' <span class="wz-opt">' + T("service.common.optional", null, "(opcional)") + "</span>" : "") +
        "</label></h3>" +
        '<p class="wz-noslots-txt">' + T("service.noslots.case_hint", null, "Si necesitás un sobreturno, contanos qué te pasa y por qué no podés esperar al próximo turno disponible. El hospital lo lee y te contacta para evaluarlo.") + "</p>" +
        '<p class="wz-noslots-txt wz-noslots-urgent">' + T("service.noslots.urgency", null, "Si es una urgencia, llamá al 107 o acercate a la guardia.") + "</p>" +
        // La conversación (responsable del producto, 2026-11-07): el hospital pregunta en vez de
        // dejar un papel en blanco. Sin IA el campo de siempre (casoChatStart).
        '<div class="wzc" id="wz-casochat" hidden><div class="wzc-msgs" id="wz-casochat-msgs" aria-live="polite"></div>' +
          '<form class="wzc-composer" id="wz-casochat-form"></form></div>' +
        '<textarea id="wz-caso" class="wz-caso" rows="4" maxlength="1000" hidden></textarea>' +
        '<div class="wz-date-echo err" id="wz-caso-msg" aria-live="polite"></div>' +
        '<div class="wz-btnstack">' +
          '<button type="button" class="wz-cta-lg" id="wz-horario-next">' + T("service.common.continue", null, "Continuar →") + "</button>" +
        "</div></div>");
      var subAlto = SUB_EL ? SUB_EL.offsetHeight : 0;
      host.innerHTML = '<div class="sec">' + stepHeader("horario") +
        '<div class="wz-card wz-card--noslots">' + opciones.join("") + "</div></div>";
      setSub(T("service.noslots.lead", null, "Ahora no hay turnos."), subAlto);

      var caso = $("#wz-caso", host), casoMsg = $("#wz-caso-msg", host);
      caso.value = wiz.caso || "";
      caso.addEventListener("input", function () { casoMsg.textContent = ""; });
      var chat = casoChatStart(host, casoMsg, function () { caso.hidden = false; });
      var nextBtn = $("#wz-horario-next", host);
      nextBtn.addEventListener("click", function () {
        var enChat = chat.activo();
        var texto = enChat ? chat.palabras() : String(caso.value || "").trim();
        if (!texto && !casoOpcional) {
          casoMsg.textContent = enChat
            ? T("service.noslots.case_chat_required", null, "Contestá en la conversación para que el hospital evalúe tu caso.")
            : T("service.noslots.case_required", null, "Escribí tu caso para que el hospital lo evalúe.");
          if (enChat) chat.focus(); else caso.focus();
          return;
        }
        wiz.caso = texto || null;
        wiz.casoChat = enChat && texto ? wiz.casoChatToken : null;
        if (wiz.casoChat) chat.terminar();
        wiz.sinTurno = true;
        // El contrato de la solicitud sin horario no cambia: la mesa sigue
        // recibiendo «Cualquier horario» y el caso viaja aparte, entero.
        wiz.preferencia = T("service.time.any_time", null, "Cualquier horario");
        afterHorario();
      });
    }

    // Keep-alive del hold: re-postea el MISMO slot+token para estirar el TTL
    // (15 min) cada vez que se entra a un paso que puede tardar en llenarse —
    // gente mayor completando cuenta/confirmación no debería perder el
    // horario por tardar en escribir. Fire-and-forget: si falla (alguien más
    // lo tomó justo en el medio, rarísimo) el 409 de submit() lo atrapa igual.
    function holdRenovar() {
      if (wiz.slotDia && wiz.slotHora) holdTomar(wiz.slotDia, wiz.slotHora);
    }

    /* =================== PASO 3 · Tu cuenta =================== */
    function stepCuenta() {
      holdRenovar();
      widenHost(0);
      if (!pantallaEspera()) {
        host.innerHTML = '<div class="sec">' + stepHeader("cuenta") + '<div class="wz-spin"><i></i> ' +
          T("service.auth.checking_session", null, "Verificando tu sesión…") + '</div></div>';
      }
      var pedido = sesionPrefetch || fetchSesion();
      sesionPrefetch = null;  // una sesión recién creada no puede salir del cache
      pedido
        .then(function (j) {
          var u = j && (j.usuario || j.user || (j.dni ? j : null));
          if (u && u.dni) { wiz.usuario = u; if (wiz.paciente) stepConfirm(); else afterCuenta(false); return; }
          mountAuth();
        })
        .catch(function () { mountAuth(); });
    }

    function mountAuth() {
      ensureStep("cuenta");
      setStep("auth");
      widenHost(840);
      // El turno elegido queda como resumen lateral en desktop (patrón de
      // checkout) y arriba del formulario en mobile. No duplica datos dentro
      // del formulario de autorización.
      var authAria = esc(T("service.auth.chosen_aria", null, "Turno elegido"));
      var authK = T("service.auth.your_appointment", null, "Tu turno");
      var turnoElegido = wiz.slotCuando
        ? '<aside class="wz-booking-summary" aria-label="' + authAria + '">' +
          '<div class="wz-booking-summary-k">' + authK + "</div>" +
          '<strong class="wz-booking-summary-service">' + esc(servicio.nombre) + "</strong>" +
          '<span class="wz-slot-detail">' + esc(wiz.slotCuando) + "</span>" +
          (wiz.slotMedico ? '<span class="wz-slot-detail">' + esc(wiz.slotMedico) + "</span>" : "") +
          (centroNombre ? '<span class="wz-slot-detail">' + esc(centroNombre) + "</span>" : "") +
          '<button type="button" class="wz-change-link" id="wz-auth-change-time">' + T("service.auth.change_time", null, "Cambiar horario") + '</button></aside>'
        : '<aside class="wz-booking-summary" aria-label="' + authAria + '"><div class="wz-booking-summary-k">' + authK + "</div>" +
          '<strong class="wz-booking-summary-service">' + esc(servicio.nombre) + "</strong></aside>";
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") +
        '<div class="wz-auth-layout"><div class="wz-auth-main" id="wz-auth"></div>' + turnoElegido + "</div></div>";
      setSub(T("service.auth.sub", null, "Ingresá para autorizar el turno con tu identidad."));
      var cambio = $("#wz-auth-change-time", host);
      if (cambio) cambio.addEventListener("click", function () {
        // "Cambiar horario" means choose again, not immediately reuse the
        // held slot and reopen authentication. Clearing this state also makes
        // any still-pending Account import stale by construction.
        holdLiberar();
        wiz.preferencia = null; wiz.slotMedico = null; wiz.slotMedicoId = null; wiz.slotCuando = null;
        wiz.slotDia = null; wiz.slotHora = null; recordarSlotEnUrl(null, null);
        stepHorario();
      });
      var authHost = $("#wz-auth", host);
      loadAccountForm().then(function (mod) {
        // The user can choose another time while the module is still loading.
        // Never resurrect an auth screen into that now-detached host, and never
        // advance the wizard from callbacks owned by that stale step.
        if (currentStep !== "auth" || !host.contains(authHost) || $("#wz-auth", host) !== authHost) return;
        mod.renderAuth(authHost, {
          prefill: buildPrefill(),
          defaultMode: "registro",
          heading: false,
          // el booleano marca "vino de crear cuenta / loguearse ACÁ mismo" vs "ya
          // tenía sesión abierta" — sólo se ofrece el nudge de stepOfrecerHijo()
          // cuando pasó por esta pantalla DENTRO del wizard, nunca con sesión ya
          // abierta (afterCuenta(false) desde stepCuenta).
          onDone: function (usuario) {
            if (currentStep !== "auth" || !host.contains(authHost) || $("#wz-auth", host) !== authHost) return;
            wiz.usuario = usuario || null; if (usuario) saveIdent(usuario); if (wiz.paciente) stepConfirm(); else afterCuenta(true);
          }
        });
      }).catch(function () {
        if (currentStep !== "auth" || !host.contains(authHost) || $("#wz-auth", host) !== authHost) return;
        var authLinkText = T("service.auth.link_text", null, "creá tu cuenta o ingresá");
        authHost.innerHTML = '<div class="wz-soft">' + T("service.auth.no_account",
          { link: '<a class="stz-link" href="/account">' + authLinkText + '</a>' },
          "Para continuar, {link} y volvé a esta página.") + '</div>';
      });
    }

    /* =================== ¿Para quién es la atención? ===================
       Ya no es un paso fijo del contador: dependientes.js:plan() decide si
       hace falta preguntar. No pediátrico -> siempre el titular, directo a
       Confirmá. Pediátrico sin hijos aptos -> alta obligatoria. Pediátrico
       con 1 hijo apto -> auto-selección, sin pantalla. Pediátrico con 2+ ->
       elegir entre ellos. */
    function fetchDependientes() {
      return fetch("/api/account/dependents", { credentials: "include" })
        .then(function (r) { return r.ok ? r.json().catch(function () { return {}; }) : {}; })
        .then(function (j) { return (j && j.dependientes) || []; })
        .catch(function () { return []; });
    }

    /* El módulo de personas a cargo no cargó (red, hash viejo, manifiesto
       ausente). En cualquier paso que necesitaba DECIDIR para quién es la
       atención eso es bloqueante: sin el módulo no hay forma segura de
       elegir al paciente, y mandar al titular derecho a Confirmá manda un
       adulto a un 400 del server en pediatría. Recuperación localizada con
       recarga — el skip silencioso del guard global de antes no vuelve. */
    function dependentsNoDisponibles() {
      widenHost(0);
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") +
        '<div class="wz-soft" role="alert">' +
        T("service.dependent.load_error", null, "No pudimos cargar la selección de paciente.") +
        ' <a class="stz-link" href="' + esc(location.href) + '">' +
        T("account.mine.dependents.reload", null, "Recargar la página") + '</a></div></div>';
      setTitle(T("service.who.title", null, "¿Para quién es la atención?"));
      setSub("");
    }

    function afterCuenta(viaAuth) {
      // Quien acaba de crear la cuenta o loguearse ACÁ no puede leer el prefetch
      // (se pidió sin sesión, o con la de otra persona): sólo vale cuando la
      // sesión ya estaba abierta al entrar.
      var stillHere = host.firstElementChild;
      var pedido = (!viaAuth && depsPrefetch) || fetchDependientes();
      depsPrefetch = null;
      pedido.then(function (deps) {
        if (stillHere && !host.contains(stillHere)) return;
        wiz.dependientes = deps;
        loadDependents().then(function (SD) {
          if (stillHere && !host.contains(stillHere)) return;
          var p = SD.plan(deps, servicio, wiz.usuario);
          var pediatrico = SD.esPediatrico(servicio);
          if (p.paso === "agregar") {
            if (pediatrico) {
              // Paediatric service: go directly to form with reason
              stepAgregarPaciente(function (dep) { wiz.paciente = dep || wiz.usuario; stepConfirm(); }, T("service.who.pediatric_lede", null, "Pediatría atiende a menores: agregalo para pedir el turno."));
            } else {
              // Non-paediatric service: ask first, then go to form
              stepOfrecerHijoParaFormulario(function (dep) { wiz.paciente = dep || wiz.usuario; stepConfirm(); });
            }
            return;
          }
          if (p.paso === "elegir") {
            stepElegirPaciente(deps, function (dep) { wiz.paciente = dep; stepConfirm(); });
            return;
          }
          wiz.paciente = p.paciente || wiz.usuario;
          // servicio NO pediátrico + recién creó/logueó su cuenta acá + todavía
          // no tiene hijos cargados -> un único ofrecimiento, skippeable, nunca
          // insistente (responsable del producto 2026-07-12: "no bloquear a nadie con esto").
          if (viaAuth && !pediatrico && !deps.length) { stepOfrecerHijo(); return; }
          stepConfirm();
        }).catch(function () {
          if (stillHere && !host.contains(stillHere)) return;
          dependentsNoDisponibles();
        });
      });
    }

    function stepEdadAntesDeOrden(usuario, deps) {
      // No upload control exists while the patient eligibility gate is open.
      // Release the bootstrap bridge now; when an eligible person is chosen,
      // stepOrden installs the native control that owns subsequent pastes.
      uploadUnavailable();
      ensureStep("cuenta");
      setStep("paciente-elegir");
      widenHost(680);
      wiz.usuario = usuario;
      wiz.dependientes = deps || [];
      wiz.paciente = usuario;
      var edadNum = Edad ? Edad.edadDesde(usuario && usuario.nacimiento) : null;
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") +
        '<div class="wz-card"><div class="wz-age-before-order">' + gateHtml(edadNum) + '</div>' +
        '<button type="button" class="wz-cta-lg" id="wz-age-add" style="margin-top:18px;">' +
        T("service.age.add_dependent", null, "Agregar persona a cargo") + '</button></div></div>';
      setTitle(T("service.who.title", null, "¿Para quién es la atención?"));
      setSub(T("service.who.before_order_sub", null, "Elegí a la persona antes de subir una orden."));
      $("#wz-age-add", host).addEventListener("click", function () {
        stepAgregarPaciente(function (dep) {
          if (!dep) { stepEdadAntesDeOrden(usuario, wiz.dependientes || []); return; }
          wiz.paciente = dep;
          var edadDep = Edad ? Edad.edadDesde(dep.nacimiento) : null;
          if (Edad && !Edad.aptoPorEdad(servicio, edadDep).ok) {
            stepEdadAntesDeOrden(usuario, wiz.dependientes || []);
            return;
          }
          stepOrden();
        });
      });
    }

    function startOrdenConEdad() {
      var limites = (servicio && servicio.edad) || {};
      if (!Edad || (limites.min == null && limites.max == null)) { stepOrden(); return; }
      var stillHere = host.firstElementChild;
      fetchSesion().then(function (usuario) {
        if (stillHere && !host.contains(stillHere)) return;
        if (!usuario) { stepOrden(); return; }
        return fetchDependientes().then(function (deps) {
          if (stillHere && !host.contains(stillHere)) return;
          wiz.usuario = usuario;
          saveIdent(usuario);
          wiz.dependientes = deps;
          var edadTitular = Edad.edadDesde(usuario.nacimiento);
          if (Edad.aptoPorEdad(servicio, edadTitular).ok) {
            wiz.paciente = usuario;
            stepOrden();
            return;
          }
          loadDependents().then(function (SD) {
            if (stillHere && !host.contains(stillHere)) return;
            var aptos = SD.aptosPorEdad(deps, servicio);
            if (aptos.length === 1) {
              wiz.paciente = aptos[0];
              stepOrden();
            } else if (aptos.length > 1) {
              stepElegirPaciente(deps, function (dep) { wiz.paciente = dep; stepOrden(); });
            } else {
              stepEdadAntesDeOrden(usuario, deps);
            }
          }).catch(function () {
            if (stillHere && !host.contains(stillHere)) return;
            dependentsNoDisponibles();
          });
        });
      }).catch(stepOrden);
    }

    function stepAgregarPaciente(onDone, customLede) {
      ensureStep("cuenta");
      widenHost(0);
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") + '<div id="wz-paciente"></div></div>';
      setTitle(T("service.who.title", null, "¿Para quién es la atención?"));
      setStep("paciente-agregar"); // recién acá hay una pantalla real que reproducir con "Atrás"
      var pacHost = $("#wz-paciente", host);
      loadDependents().then(function (SD) {
        // El usuario puede elegir otro horario o volver con "Atrás" mientras
        // el módulo sigue en vuelo: nunca pintar el alta sobre un host ya
        // reemplazado ni avanzar el wizard desde un paso abandonado (mismo
        // guard de staleness que mountAuth).
        if (currentStep !== "paciente-agregar" || !host.contains(pacHost) || $("#wz-paciente", host) !== pacHost) return;
        SD.renderAgregar(pacHost, {
          lede: customLede || T("service.who.add_lede", null, "Agregalo una vez para pedir turnos a su nombre."),
          onAdded: function (dep) { wiz.dependientes = (wiz.dependientes || []).concat([dep]); onDone(dep); },
          onCancel: function () { onDone(null); }
        });
      }).catch(function () {
        if (currentStep !== "paciente-agregar" || !host.contains(pacHost) || $("#wz-paciente", host) !== pacHost) return;
        dependentsNoDisponibles();
      });
    }

    /* `deps` son las personas a cargo SIN filtrar: el filtro por edad lo hace
       el propio módulo (aptosPorEdad) cuando ya cargó — es su decisión, no la
       de este paso. */
    function stepElegirPaciente(deps, onDone) {
      ensureStep("cuenta");
      widenHost(0);
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") + '<div id="wz-paciente"></div></div>';
      setTitle(T("service.who.title", null, "¿Para quién es la atención?"));
      setStep("paciente-elegir");
      var pacHost = $("#wz-paciente", host);
      loadDependents().then(function (SD) {
        if (currentStep !== "paciente-elegir" || !host.contains(pacHost) || $("#wz-paciente", host) !== pacHost) return;
        SD.renderElegir(pacHost, {
          aptos: SD.aptosPorEdad(deps || [], servicio),
          lede: T("service.who.choose_lede", null, "Elegí quién va a recibir la atención."),
          onSelect: function (dep) { onDone(dep); }
        });
      }).catch(function () {
        if (currentStep !== "paciente-elegir" || !host.contains(pacHost) || $("#wz-paciente", host) !== pacHost) return;
        dependentsNoDisponibles();
      });
    }

    function stepOfrecerHijoParaFormulario(onDone) {
      ensureStep("cuenta");
      setStep("ofrecer-hijo");
      widenHost(0);
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") +
        '<div class="wz-btnstack">' +
          '<button type="button" class="wz-cta-lg" id="wz-hijo-si">' + T("service.dependent.add_child", null, "Agregar un hijo o persona a cargo") + '</button>' +
          '<button type="button" class="wz-cta-sec" id="wz-hijo-no">' + T("service.common.not_now", null, "Ahora no") + '</button>' +
        "</div></div>";
      setTitle(T("service.dependent.ask_title", null, "¿Sacás turnos para tus hijos?"));
      setSub(T("service.dependent.ask_sub", null, "Agregalos una vez y la próxima vez el turno sale directo, sin volver a cargar sus datos."));
      $("#wz-hijo-no", host).addEventListener("click", function () {
        onDone(null);
      });
      $("#wz-hijo-si", host).addEventListener("click", function () {
        stepAgregarPaciente(onDone);
      });
    }

    function stepOfrecerHijo() {
      ensureStep("cuenta");
      setStep("ofrecer-hijo");
      widenHost(0);
      host.innerHTML = '<div class="sec">' + stepHeader("cuenta") +
        '<div class="wz-btnstack">' +
          '<button type="button" class="wz-cta-lg" id="wz-hijo-si">' + T("service.dependent.add_child", null, "Agregar un hijo o persona a cargo") + '</button>' +
          '<button type="button" class="wz-cta-sec" id="wz-hijo-no">' + T("service.common.not_now", null, "Ahora no") + '</button>' +
        "</div></div>";
      setTitle(T("service.dependent.ask_title", null, "¿Sacás turnos para tus hijos?"));
      setSub(T("service.dependent.ask_sub", null, "Agregalos una vez y la próxima vez el turno sale directo, sin volver a cargar sus datos."));
      $("#wz-hijo-no", host).addEventListener("click", stepConfirm);
      $("#wz-hijo-si", host).addEventListener("click", function () {
        // el titular sigue siendo el paciente de ESTE turno; alta pura, sin
        // reemplazar wiz.paciente.
        stepAgregarPaciente(function () { stepConfirm(); });
      });
    }

    /* =================== PASO 4 · Confirmá =================== */
    /* Vacío si la persona no tiene nombre (una cuenta del portal todavía no lo
       tiene): quien lo dibuja decide qué decir en su lugar, nunca un guion. */
    function personName(u) {
      return [u.nombre, u.apellido].filter(Boolean).join(" ").trim();
    }
    /* El paciente del turno. Cuando es el titular, siempre es la cuenta de
       AHORA: la sesión se vuelve a leer (o se guarda el nombre que acaba de
       escribir) y wiz.usuario pasa a ser otro objeto, mientras wiz.paciente
       seguía siendo la copia vieja, todavía sin nombre. */
    function pacienteDelTurno() {
      var p = wiz.paciente, u = wiz.usuario;
      if (p && u && p !== u && p.dni && String(p.dni) === String(u.dni)) wiz.paciente = p = u;
      return p || u || {};
    }
    /* El contacto de escape ("¿Te trabás en algún paso? Escribile a…") entra a
       la tarjeta, abajo del botón (responsable del producto, 2026-07-28: "make it below Enviar
       solicitud, but on the white rectangle"). Lo escribe el server una sola
       vez, en el `<p class="wizard-help">` del shell: acá se lee de ahí y se
       reimprime adentro, y el original se esconde — así sigue habiendo UNA
       sola frase y los otros pasos del wizard la conservan donde estaba. */
    var HELP_EL = document.querySelector(".wizard-help");
    var HELP_HTML = HELP_EL ? HELP_EL.innerHTML : "";
    function ayudaHtml() {
      return HELP_HTML ? '<p class="wz-ayuda">' + HELP_HTML + "</p>" : "";
    }
    function ayudaEnShell(mostrar) {
      if (HELP_EL) HELP_EL.hidden = !mostrar;
    }

    /* La tarjeta del turno: arriba de todo, porque ES lo que se viene a
       confirmar. Fecha y hora grandes (la hora en mono, se lee de un vistazo)
       con su "Cambiar fecha y horario" pegado al lado, y debajo qué es y dónde
       — cada dato con su "Cambiar" junto al dato que cambia. */

    /* NO hay reloj a la vista (responsable del producto, 2026-07-28: "don't show this ticker
       because it will only distract"). Confirmar lleva segundos y el 99% de la
       gente termina muy antes de que el hold importe; un contador corriendo
       arriba de la pantalla sólo apura a quien no necesita apurarse. El aviso
       aparece SÓLO en el último minuto, y aparece en el botón — el único lugar
       donde ya se está mirando cuando queda algo por hacer. */
    var AVISO_SEG = 60;
    var UNIDADES = ["cero", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
      "nueve", "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
      "dieciocho", "diecinueve", "veinte", "veintiún", "veintidós", "veintitrés", "veinticuatro",
      "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"];
    var DECENAS = { 30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta" };
    // En letras, no en dígitos: es una frase que se lee, no un cronómetro.
    function enLetras(n) {
      if (n < 30) return UNIDADES[n] || String(n);
      var d = Math.floor(n / 10) * 10, u = n % 10;
      var base = DECENAS[d] || String(d);
      return u ? base + " y " + UNIDADES[u] : base;
    }
    function segundosTexto(n) {
      return enLetras(n) + (n === 1 ? " segundo" : " segundos");
    }
    function holdRestante() {
      if (!wiz.holdHasta || !wiz.slotDia || !wiz.slotHora) return null;
      return Math.max(0, Math.round((wiz.holdHasta - Date.now()) / 1000));
    }
    // Segunda línea del botón Enviar. Vacía casi siempre; en el último minuto
    // dice cuánto le queda guardado el horario, y al vencerse lo dice también
    // en vez de callarse — el envío se intenta igual, porque la palabra final
    // es el 409 de /api/requests, que sabe volver a la grilla sin perder nada.
    function holdAvisoTexto() {
      var left = holdRestante();
      if (left == null || left > AVISO_SEG) return "";
      if (left <= 0) return T("service.hold.expired", null, "Se venció la reserva del horario — probá igual");
      // segundosTexto() deletrea el número en castellano (UNIDADES arriba): un
      // número en letras en inglés es un módulo aparte que no entra en esta
      // pasada de traducción — la frase se traduce, la cantidad queda en
      // castellano hasta que exista ese módulo (lang-coupled algorithm).
      var seg = segundosTexto(left);
      return T("service.hold.remaining", { seg: seg }, "Te guardamos el horario {seg} más");
    }
    var holdTicker = null;
    function holdTickerStop() { if (holdTicker) { clearInterval(holdTicker); holdTicker = null; } }
    function holdTickerStart() {
      holdTickerStop();
      if (holdRestante() == null) return;
      holdTicker = setInterval(function () {
        var el = $("#wz-send-aviso", host);
        if (!el) { holdTickerStop(); return; }
        var txt = holdAvisoTexto();
        if (el.textContent !== txt) el.textContent = txt;
        if (holdRestante() <= 0) holdTickerStop();
      }, 1000);
    }
    /* «Agregar al calendario» vive en la pantalla de éxito (el archivo de la
       solicitud ya guardada, /api/appointment/<id>.ics) y en «Mis turnos»;
       antes de enviar no hay nada que agendar (plan 2026-11-10). */
    var CAL_ICO = "/static/icons/calendar-plus-sf-regular.svg";
    function turnoCardHtml() {
      if (!wiz.preferencia) return "";
      var quien = esc(servicio.nombre) +
        (wiz.slotMedico ? " con " + esc(wiz.slotMedico) : "");
      var direccion = String(centro.address || "").trim();
      var donde = centroNombre
        ? '<div class="wz-line" style="margin-top:2px;">' +
            '<div class="wz-donde">' + esc(centroNombre) +
              (direccion ? " — " + esc(direccion) : "") + "</div>" +
            (data.puede_cambiar_centro
              ? '<button type="button" class="wz-change-link wz-change-centro" id="wz-change-centro">' + T("service.common.change_center", null, "Cambiar centro") + '</button>'
              : "") +
          "</div>"
        : "";
      var cabecera;
      if (wiz.slotDia && wiz.slotHora) {
        cabecera = '<div class="wz-cuando">' + esc(friendlyDia(wiz.slotDia)) +
          ', <span class="wz-hora">' + esc(wiz.slotHora) + "</span></div>" +
          '<button type="button" class="wz-change-link" id="wz-change-time">' + T("service.common.change_datetime", null, "Cambiar fecha y horario") + '</button>';
      } else {
        // Sin turno real: el rótulo no puede sonar a horario confirmado (responsable del producto
        // 2026-07-11: "no overpromise") — dice lo que pasa después.
        cabecera = '<div class="wz-cuando wz-cuando--pref">' +
            T("service.noslots.card_head", null, "El hospital te contacta") + "</div>" +
          '<button type="button" class="wz-change-link" id="wz-change-time">' + T("service.common.change", null, "Cambiar") + '</button>';
      }
      var nota = (wiz.slotDia && wiz.slotHora) ? ""
        : (wiz.caso
          ? '<p class="wz-pref-note wz-caso-echo">' + esc(T("service.noslots.card_case", { texto: wiz.caso }, "Tu caso: {texto}")) + "</p>"
          : "") +
          '<p class="wz-pref-note">' + (wiz.caso
            ? T("service.noslots.card_note_case", null, "El hospital lee tu caso y te contacta para darte la fecha.")
            : T("service.noslots.card_note", null, "El hospital te contacta para darte la fecha.")) + "</p>";
      return '<div class="wz-turno">' +
        '<div class="wz-line">' + cabecera + "</div>" +
        '<div class="wz-quien">' + quien + "</div>" + donde + nota + "</div>";
    }
    function ordenSummary() {
      // Con foto al costado no hace falta esta fila — la preview alcanza
      // (responsable del producto 2026-07-12). Eso incluye la orden que llegó YA analizada desde
      // "Subí tu orden" o de vuelta del SSO (sólo el token, sin los bytes en
      // memoria: service.js:512-528): stepConfirm prueba la miniatura que
      // sirve el servidor ahí mismo, al costado, así que esta fila se queda
      // afuera mientras esa prueba está en curso (wiz.ordenNoEsFoto null).
      // Si el servidor confirmó que NO es una imagen (PDF, o la miniatura
      // falló) degradamos con gracia: chip de documento + "Cambiar" que
      // vuelve a subirla (replaya stepOrden) — nunca un nombre de archivo
      // pelado.
      if (wiz.ordenPreview) return "";
      if (wiz.token && !wiz.ordenNoEsFoto) return "";
      if (!wiz.ordenFilename && !wiz.token) return "";
      var nombre = wiz.ordenFilename ? esc(wiz.ordenFilename) : T("service.order.uploaded_fallback", null, "Orden subida");
      return '<div class="wz-orden-doc">' +
        '<svg class="wz-orden-doc-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
          '<path d="M6 2h8l6 6v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
          '<path d="M14 2v6h6" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
        "</svg>" +
        '<span class="wz-orden-doc-name">' + nombre + "</span>" +
        '<button type="button" class="wz-change-link" id="wz-change-orden">' + T("service.common.change", null, "Cambiar") + '</button></div>';
    }
    // URL de la miniatura que sirve el servidor para una orden que sólo
    // llegó por token (booking_requests._orden_preview): posesión del token
    // ES la autorización, igual que orden_token en /api/requests — nunca
    // pedimos sesión para esto. Devuelve 404 si no es imagen (PDF).
    function ordenPreviewUrl() {
      return wiz.token ? "/api/orden-preview/" + encodeURIComponent(wiz.token) : null;
    }
    function dniMismatchNote(u) {
      // Sin DNI real detrás de la cuenta la comparación no tiene sentido: la
      // orden trae el documento de la persona, la clave de la cuenta no es un
      // documento — la nota "otro DNI" sería siempre un falso positivo.
      if (!u || u.identity_kind === "synthetic") return "";
      var ordenDni = wiz.ai && wiz.ai.campos && wiz.ai.campos.dni && wiz.ai.campos.dni.valor;
      ordenDni = ordenDni ? onlyDigits(ordenDni) : "";
      var sesDni = u.dni ? onlyDigits(u.dni) : "";
      if (ordenDni && sesDni && ordenDni !== sesDni) {
        var dniEsc = esc(formatDni(ordenDni));
        return '<div class="wz-soft">' + T("service.order.dni_mismatch", { dni: dniEsc }, "La orden trae otro DNI ({dni}) — la revisa el hospital.") + "</div>";
      }
      return "";
    }

    /* "¿Cómo te avisamos?" — los datos SON los de la cuenta, así que se leen,
       no se completan: quien ya los tiene cargados no vuelve a tipearlos
       (mockup 2026-07-28). Se pasan a editables cuando faltan (si no, no habría
       forma de avisarle a nadie) o cuando la persona toca "Cambiar" — el flujo
       nunca la manda a Mi cuenta a mitad del trámite. */
    var CONTACTO_LAB = { tel: "WhatsApp", email: "Email" };
    // Qué campo está en modo edición: "", "tel", "email" o "todo". Por campo y
    // no global, porque "Cambiar" vive al lado del dato que cambia — abrir los
    // dos al tocar uno haría del link una mentira (hook de proximidad).
    var contactoEditando = "";
    function editando(kind) {
      return contactoEditando === "todo" || contactoEditando === kind;
    }
    /* El teléfono del TURNO es el que se escribe en este paso (wiz.contactoTurno).
       El de la cuenta sólo cambia si la persona tilda «Guardar también en mi
       cuenta»; una cuenta SIN teléfono lo completa, que no pisa nada. Regla de
       El responsable del producto, hub, 2026-10-23: el teléfono de la cuenta no se pisa sin el
       consentimiento del paciente. La cuenta es siempre la de quien saca el
       turno (`u`), también cuando el paciente es un hijo. */
    function telefonoDelTurno(u) {
      return wiz.contactoTurno == null ? String((u && u.contacto) || "") : wiz.contactoTurno;
    }
    function mismoTelefono(a, b) {
      var na = Format.telNacional(a), nb = Format.telNacional(b);
      return na && nb ? na === nb : normPhone(a) === normPhone(b);
    }
    function ofreceGuardarTelefono(u) {
      var turno = normPhone(telefonoDelTurno(u)), cuenta = normPhone((u && u.contacto) || "");
      return !!turno && !!cuenta && !mismoTelefono(turno, cuenta);
    }
    function telefonoParaCuenta(u, guardar) {
      var turno = normPhone(telefonoDelTurno(u)), cuenta = normPhone((u && u.contacto) || "");
      return (!cuenta || (guardar && ofreceGuardarTelefono(u))) ? turno : cuenta;
    }
    /* Lo que Confirmá le manda a la cuenta, o null si el paso no cambia nada
       de ella (el número del turno solo no es un cambio de la cuenta). */
    function cuentaAActualizar(u, nombre, apellido, email, guardarTel) {
      var contacto = telefonoParaCuenta(u, guardarTel);
      var igual = contacto === normPhone((u && u.contacto) || "") &&
        email === String((u && u.email) || "").trim() &&
        nombre === String((u && u.nombre) || "").trim() &&
        apellido === String((u && u.apellido) || "").trim();
      if (igual) return null;
      return { nombre: nombre, apellido: apellido, nacimiento: u.nacimiento || "", sexo: u.sexo || "",
        contacto: contacto, email: email };
    }
    function guardarTelefonoHtml(u) {
      if (!ofreceGuardarTelefono(u)) return "";
      return '<label class="wz-guardar-op" for="wz-guardar-tel"><input type="checkbox" id="wz-guardar-tel"' +
        (wiz.guardarTelCuenta ? " checked" : "") + "><span>" +
        T("service.contact.save_to_account", null, "Guardar también en mi cuenta") + "</span></label>" +
        '<p class="wz-sec-p wz-guardar-nota">' +
        T("service.contact.this_turn_only", { tel: esc(Format.telTexto(u.contacto)) },
          "Este número es para este turno. Tu cuenta sigue con el {tel}.") + "</p>";
    }
    function contactField(kind, value) {
      var isTel = kind === "tel";
      var display = isTel ? Format.telTexto(value) : String(value || "").trim();
      return '<div class="wz-dato-col wz-dato-col--edit"><label class="wz-lab" for="wz-contact-' + kind + '">' + CONTACTO_LAB[kind] + "</label>" +
        '<input class="wz-editin' + (isTel ? " wz-editin--tel" : "") + '" id="wz-contact-' + kind + '" type="' +
        (isTel ? "tel" : "email") + '" inputmode="' + (isTel ? "tel" : "email") + '" autocomplete="' +
        (isTel ? "tel" : "email") + '" value="' + esc(display) + '" placeholder="' +
        (isTel ? "2254 55-0713" : esc(T("service.contact.email_placeholder", null, "vos@ejemplo.com"))) + '"></div>';
    }
    /* El dato, linkeado, y su "Cambiar" a 10px — nunca contra el margen
       derecho (responsable del producto, 2026-07-28). El teléfono va a WhatsApp y el mail a
       mailto: es lo que la persona quiere hacer si lo ve mal, y en el panel el
       hospital ya lo tiene así (format.mjs, el MISMO módulo). Sin rótulo: el
       ícono dice qué es (responsable del producto, 2026-10-27), y su alt lo dice en voz alta. */
    var CONTACTO_ICO = { tel: "/static/icons/whatsapp-sf-regular.svg", email: "/static/icons/email-sf-regular.svg" };
    function contactValue(kind, value) {
      var isTel = kind === "tel";
      var html = isTel
        ? Format.telHtml(value)
        : Format.emailHtml(value);
      return '<div class="wz-dato-col"><div class="wz-dato">' +
        '<img class="wz-ico" src="' + CONTACTO_ICO[kind] + '" alt="' + CONTACTO_LAB[kind] + '" width="20" height="20">' +
        '<span class="wz-val">' + html + "</span>" +
        '<button type="button" class="wz-change-link" data-contact-edit="' + kind + '">' + T("service.common.change", null, "Cambiar") + '</button></div></div>';
    }
    /* Título y datos en una sección sin recuadro (responsable del producto, 2026-10-27). La cuenta
       verificada ya no lleva sello: ocupaba una línea entera y no le pide
       nada a la persona. */
    function contactoSection(u) {
      var tel = telefonoDelTurno(u);
      var email = u.email || "";
      // Un dato que falta se pide en el acto; el que está, se muestra.
      var editTel = editando("tel") || !tel;
      var editEmail = editando("email") || !email;
      return '<div class="wz-avisos">' +
        '<div class="wz-avisos-h"><div class="wz-sec-t">' + T("service.contact.how_title", null, "¿Cómo te avisamos?") + "</div></div>" +
        '<p class="wz-sec-p">' + T("service.contact.notice_when", null,
            "Te escribimos cuando el hospital confirma el turno, y si algo cambia.") + "</p>" +
        '<div class="wz-cuenta" id="wz-cuenta">' +
          (editTel ? contactField("tel", tel) : contactValue("tel", tel)) +
          (editEmail ? contactField("email", email) : contactValue("email", email)) +
        "</div>" +
        '<div class="wz-guardar-cuenta" id="wz-guardar-cuenta" aria-live="polite">' + guardarTelefonoHtml(u) + "</div>" +
        '<p class="wz-contact-error" id="wz-contact-error" aria-live="polite"></p></div>';
    }

    /* An account that came from the municipal portal has no name yet (the
       portal sends none): the first booking asks for it, right here, and it
       is saved with the contact — never a detour to Mi cuenta. */
    function faltaNombre(u) {
      return !String(u.nombre || "").trim() || !String(u.apellido || "").trim();
    }
    function nombreSection(u) {
      function campo(key, label, auto) {
        return '<div class="wz-dato-col wz-dato-col--edit"><label class="wz-lab" for="wz-name-' + key + '">' + label + "</label>" +
          '<input class="wz-editin" id="wz-name-' + key + '" type="text" autocomplete="' + auto + '" value="' +
          esc(u[key] || "") + '"></div>';
      }
      return '<div class="wz-avisos">' +
        '<div class="wz-avisos-h"><div class="wz-sec-t">' + T("service.confirm.name_title", null, "¿Cómo te llamás?") + "</div></div>" +
        '<div class="wz-cuenta wz-cuenta--par">' +
          campo("nombre", T("service.confirm.first_name", null, "Nombre"), "given-name") +
          campo("apellido", T("service.confirm.last_name", null, "Apellido"), "family-name") +
        "</div>" +
        '<p class="wz-contact-error" id="wz-name-error" aria-live="polite"></p></div>';
    }

    function pacienteOptions(u, deps) {
      return [u].concat(deps || []);
    }

    /* "Para" pasa a poder cambiarse desde Confirmá (Yo / cada hijo / alta
       nueva) — mismo patrón "Cambiar" + picker inline que ya usan el horario
       y el centro (wz-change-link + fieldset). IMPORTANTE: este bloque queda
       SIEMPRE visible, incluso cuando el gate de edad bloquea al paciente
       actual — si no, alguien que cambia a un paciente que no entra por edad
       queda sin forma de volver a elegir a otra persona. */
    function confirmSentence(paciente) {
      // Servicio y centro ya los dice la tarjeta del turno: acá queda sólo
      // PARA QUIÉN es, que es el otro dato que se puede haber elegido mal.
      // Sin nombre (cuenta del portal): «Para vos» si es el titular; de otra
      // persona sin nombre no hay nada que decir, queda sólo su DNI.
      var pacienteNombreHtml = "<b>" + esc(personName(paciente)) + "</b>";
      var paraHtml = personName(paciente)
        ? T("service.patient.for_label", { nombre: pacienteNombreHtml }, "Para {nombre}")
        : (paciente === wiz.usuario ? T("service.patient.for_you", null, "Para vos") : "");
      // La clave sintética (alta sin DNI del visitante no hispanohablante)
      // jamás se muestra: no es el documento de nadie.
      var mostrarDni = !!paciente.dni && paciente.identity_kind !== "synthetic";
      return '<div class="wz-pac">' + (paraHtml ? "<span>" + paraHtml + "</span>" : "") +
        (mostrarDni ? '<span class="wz-pac-dni">DNI <span class="wz-mono">' +
          esc(formatDni(paciente.dni)) + "</span></span>" : "") +
        '<button type="button" class="wz-change-link wz-change-paciente" id="wz-change-paciente">' + T("service.patient.change", null, "Cambiar paciente") + '</button></div>';
    }
    function wirePacienteChange(u, deps) {
      var change = $("#wz-change-paciente", host);
      if (!change) return;
      change.addEventListener("click", function () {
        var picker = $("#wz-paciente-picker", host);
        var opciones = pacienteOptions(u, deps);
        picker.innerHTML = '<fieldset class="wz-center-picker"><legend>' + T("service.who.title", null, "¿Para quién es la atención?") + '</legend>' +
          opciones.map(function (p, i) {
            // The account holder is shown by name, not as "Me": on a shared
            // computer (family, a phone-both PC) "Me" does not say WHOSE
            // session is open, and the name gives away "this is not my account".
            var nom = personName(p);
            if (!nom) nom = i === 0 ? T("service.patient.myself", null, "Yo") : "DNI " + formatDni(p.dni);
            return '<button type="button" class="wz-center-choice" data-i="' + i + '">' + esc(nom) + "</button>";
          }).join("") +
          '<button type="button" class="wz-center-choice" id="wz-paciente-agregar">' + T("service.dependent.add_child_ellipsis", null, "Agregar un hijo o persona a cargo…") + '</button>' +
          // A portal session is the portal's: our logout could not switch it.
          (AUTH_MODE === "portal" ? "" :
            '<button type="button" class="wz-change-link wz-center-cancel wz-center-otra" id="wz-paciente-otra-cuenta">' + T("service.patient.switch_account", null, "Ingresá con otra cuenta") + '</button>') +
          '<button type="button" class="wz-change-link wz-center-cancel" id="wz-paciente-cancel">' + T("service.common.cancel", null, "Cancelar") + '</button></fieldset>';
        picker.querySelectorAll("[data-i]").forEach(function (b) {
          b.addEventListener("click", function () { wiz.paciente = opciones[+b.dataset.i]; stepConfirm(); });
        });
        $("#wz-paciente-agregar", picker).addEventListener("click", function () {
          stepAgregarPaciente(function (dep) { if (dep) wiz.paciente = dep; stepConfirm(); });
        });
        // Neither the holder nor a dependent → end this session and sign in
        // with the person's own account, coming back to THIS turn (slot and
        // all): volver carries pathname+search, and the ?slot= that
        // recordarSlotEnUrl() left in the URL survives the account switch
        // (the hold is per-browser; _logout never touches localStorage —
        // see cuentas/accounts.py).
        var otra = $("#wz-paciente-otra-cuenta", picker);
        if (otra) otra.addEventListener("click", function () {
          otra.disabled = true;
          var volver = location.pathname + location.search;
          fetch("/api/account/logout", { method: "POST", credentials: "same-origin" })
            .then(function (r) {
              // fetch only rejects on a network failure, so a 4xx/5xx lands
              // here too. A non-success means the session did NOT end: do not
              // redirect as if the switch succeeded. Restore the control and
              // leave the current session usable so the person can retry.
              if (!r.ok) { otra.disabled = false; return; }
              location.href = "/account?mode=login&next=" + encodeURIComponent(volver);
            })
            .catch(function () { otra.disabled = false; });
        });
        $("#wz-paciente-cancel", picker).addEventListener("click", function () { picker.innerHTML = ""; });
      });
    }

    function stepConfirm() {
      holdRenovar();
      setStep("confirm");
      // Certeza (bytes en memoria) vs. "probemos" (sólo token: no sabemos si
      // es una foto hasta que el <img> del servidor cargue o falle — ver
      // ordenPreviewUrl/wiz.ordenNoEsFoto). Mientras la prueba está en
      // curso la tratamos como foto: es el caso más común (handoff de
      // "Subí tu orden") y evita el parpadeo de layout si carga bien.
      var ordenFotoSrc = wiz.ordenPreview;
      var ordenTalVezFoto = !ordenFotoSrc && !!wiz.token && !wiz.ordenNoEsFoto;
      var mostrarComoFoto = !!(ordenFotoSrc || ordenTalVezFoto);
      widenHost(mostrarComoFoto ? 900 : 680);
      var u = wiz.usuario || {};
      var deps = wiz.dependientes || [];
      var paciente = pacienteDelTurno();

      // gate por edad del paciente ACTUALMENTE seleccionado
      var edadNum = Edad ? Edad.edadDesde(paciente.nacimiento) : null;
      var g = gateHtml(edadNum);

      var restHtml = g ? "" : (
        (faltaNombre(u) ? nombreSection(u) : "") +
        contactoSection(u) +
        ordenSummary() +
        dniMismatchNote(paciente) +
        '<button type="button" class="wz-cta-lg wz-cta-send" id="wz-send">' +
          "<span>" + T("service.confirm.send", null, "Pedir el turno") + "</span>" +
          '<span class="wz-send-aviso" id="wz-send-aviso" aria-live="polite">' + esc(holdAvisoTexto()) + "</span>" +
        "</button>" +
        '<div class="f-msg" id="wz-msg"></div>' +
        ayudaHtml()
      );

      var mainHtml =
        '<div class="wz-confirm-main">' +
          (g ? "" : turnoCardHtml()) +
          confirmSentence(paciente) +
          (!g ? '<div id="wz-centro-picker"></div>' : "") +
          '<div id="wz-paciente-picker"></div>' +
          (g ? '<div class="wz-card" style="margin-top:14px;padding:0;border:0;">' + g + "</div>" : restHtml) +
        "</div>";
      var zoomAria = esc(T("service.confirm.zoom_order_aria", null, "Ampliar la orden que subiste"));
      var ordenAlt = esc(T("service.confirm.order_alt", null, "Orden médica subida"));
      var photoHtml = ordenFotoSrc ?
        '<a class="wz-confirm-photo" href="' + esc(ordenFotoSrc) + '" download="order-image" aria-label="' + zoomAria + '" ' +
          'data-photozoom data-photozoom-src="' + esc(ordenFotoSrc) + '" data-photozoom-alt="' + ordenAlt + '">' +
          '<img src="' + esc(ordenFotoSrc) + '" alt="' + ordenAlt + '"></a>' :
        ordenTalVezFoto ?
          '<a class="wz-confirm-photo" id="wz-orden-thumb-btn" href="' + esc(ordenPreviewUrl()) + '" download="order-image" aria-label="' + zoomAria + '" ' +
            'data-photozoom data-photozoom-src="' + esc(ordenPreviewUrl()) + '" data-photozoom-alt="' + ordenAlt + '">' +
            '<img id="wz-orden-thumb-img" src="' + esc(ordenPreviewUrl()) + '" alt="' + ordenAlt + '"></a>' : "";
      host.innerHTML = '<div class="sec">' + stepHeader("confirm") +
        '<div class="wz-card wz-card--confirm' + (mostrarComoFoto ? " wz-card--photo" : "") + '" id="wz-confirm">' +
        mainHtml + photoHtml + "</div></div>";
      window.scrollTo(0, 0);
      holdTickerStart();

      // La miniatura del servidor puede resultar un PDF (404): reintentamos
      // el paso completo tratándolo como documento, no como foto rota.
      if (ordenTalVezFoto) {
        var thumbImg = $("#wz-orden-thumb-img", host);
        if (thumbImg) thumbImg.addEventListener("error", function () {
          wiz.ordenNoEsFoto = true;
          stepConfirm();
        }, { once: true });
      }

      wirePacienteChange(u, deps);
      // Con el gate por edad no se pinta la tarjeta completa (ni el botón, ni
      // el contacto de escape): la frase del shell tiene que seguir visible.
      if (g) { ayudaEnShell(true); return; }

      // «Guardar también en mi cuenta»: aparece sólo mientras el número del
      // turno difiere del de la cuenta, y se repinta al escribir o al guardar.
      var guardarPintado = guardarTelefonoHtml(u);
      function pintarGuardarTel() {
        var caja = $("#wz-guardar-cuenta", host);
        if (!caja) return;
        caja.innerHTML = guardarPintado = guardarTelefonoHtml(u);
        var tilde = $("#wz-guardar-tel", caja);
        if (tilde) tilde.addEventListener("change", function () { wiz.guardarTelCuenta = tilde.checked; });
      }
      function actualizarGuardarTel() {
        // Sin diferencia no hay nada que tildar: una tilde vieja no vale.
        if (!ofreceGuardarTelefono(u)) wiz.guardarTelCuenta = false;
        if (guardarTelefonoHtml(u) !== guardarPintado) pintarGuardarTel();
      }
      pintarGuardarTel();
      var telEscrito = $("#wz-contact-tel", host);
      if (telEscrito) telEscrito.addEventListener("input", function () {
        wiz.contactoTurno = telEscrito.value;
        actualizarGuardarTel();
      });

      var contactSavePromise = null;
      function guardarContactos(blur) {
        // «Enviar» no se conforma con un guardado a medias de otro campo: espera
        // y vuelve a mirar (la tilde y el número pueden haber cambiado en el
        // medio); salir de un campo no reintenta.
        if (contactSavePromise) {
          return blur ? contactSavePromise : contactSavePromise.then(function () { return guardarContactos(false); });
        }
        var telInput = $("#wz-contact-tel", host), emailInput = $("#wz-contact-email", host);
        var error = $("#wz-contact-error", host);
        // El número escrito es del turno; se recuerda aunque la cuenta no cambie.
        if (telInput) wiz.contactoTurno = telInput.value;
        var guardarInput = $("#wz-guardar-tel", host);
        if (guardarInput) wiz.guardarTelCuenta = guardarInput.checked;
        var telVal = normPhone(telefonoDelTurno(u));
        var emailVal = String(emailInput ? emailInput.value : u.email || "").trim();
        var nombreInput = $("#wz-name-nombre", host), apellidoInput = $("#wz-name-apellido", host);
        var nombreVal = String(nombreInput ? nombreInput.value : u.nombre || "").trim();
        var apellidoVal = String(apellidoInput ? apellidoInput.value : u.apellido || "").trim();
        var nameError = $("#wz-name-error", host);
        if (nombreInput && (!nombreVal || !apellidoVal)) {
          // Leaving the phone field must not scold about a name not typed yet.
          if (blur) return Promise.resolve(false);
          if (nameError) nameError.textContent = T("service.confirm.name_needed", null, "Escribí tu nombre y apellido.");
          return Promise.resolve(false);
        }
        if (nameError) nameError.textContent = "";
        if (!telVal && !emailVal) {
          if (error) error.textContent = T("service.contact.need_one", null, "Dejá al menos un teléfono o un email para poder avisarte de tus turnos.");
          return Promise.resolve(false);
        }
        if (error) error.textContent = "";
        // Sólo se toca la cuenta si este paso cambia algo de ELLA (nombre que
        // faltaba, email, teléfono que no tenía o el que la persona manda
        // guardar); el número del turno solo no es un cambio de la cuenta.
        // La tilde se cumple recién en «Enviar», cuando ya es definitiva:
        // salir de un campo no escribe un teléfono que después se destilda.
        var cuerpo = cuentaAActualizar(u, nombreVal, apellidoVal, emailVal, !blur && !!wiz.guardarTelCuenta);
        if (!cuerpo) return Promise.resolve(true);
        contactSavePromise = fetch("/api/account/update", {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cuerpo)
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
        }).then(function (res) {
          if (res.ok && res.j && res.j.usuario) {
            // The holder chosen as patient is the same account: the name just
            // saved here is the receipt's «Paciente» too, never the copy
            // taken before it had one (the product owner 26.10: «Paciente —»).
            var titular = paciente === u || (paciente && paciente.dni && paciente.dni === u.dni);
            u = res.j.usuario; wiz.usuario = u;
            if (titular) { paciente = u; wiz.paciente = u; }
            saveIdent({ nombre: u.nombre, apellido: u.apellido, contacto: u.contacto, email: u.email });
            if (telInput) telInput.value = formatPhone(telefonoDelTurno(u) || u.contacto);
            actualizarGuardarTel();
            return true;
          }
          if (error) error.textContent = (res.j && res.j.error) || T("service.contact.save_error", null, "No pudimos guardar. Probá de nuevo.");
          return false;
        }).catch(function () {
          if (error) error.textContent = T("service.contact.network_error_short", null, "Error de red. Probá de nuevo.");
          return false;
        }).then(function (ok) {
          contactSavePromise = null;
          return ok;
        });
        return contactSavePromise;
      }
      host.querySelectorAll("#wz-contact-tel,#wz-contact-email").forEach(function (input) {
        input.addEventListener("blur", function () { guardarContactos(true); });
      });
      // Cada "Cambiar" abre SU campo: el link está al lado de ese dato y no de
      // otro (hook de proximidad, responsable del producto 2026-07-28).
      host.querySelectorAll("[data-contact-edit]").forEach(function (b) {
        b.addEventListener("click", function () {
          var kind = b.getAttribute("data-contact-edit");
          contactoEditando = kind;
          stepConfirm();
          var input = $("#wz-contact-" + kind, host);
          if (input) input.focus();
        });
      });

      var timeChange = $("#wz-change-time", host);
      if (timeChange) timeChange.addEventListener("click", function () {
        holdLiberar();
        wiz.preferencia = null; wiz.slotMedico = null; wiz.slotMedicoId = null; wiz.slotCuando = null;
        wiz.slotDia = null; wiz.slotHora = null; recordarSlotEnUrl(null, null);
        stepHorario();
      });
      var ordenChange = $("#wz-change-orden", host);
      if (ordenChange) ordenChange.addEventListener("click", function () {
        wiz.ai = null; wiz.token = null; wiz.ordenRaw = null; wiz.ordenPreview = null; wiz.ordenFilename = null; wiz.ordenNoEsFoto = null; olvidarOrdenHandoff();
        stepOrden();
      });
      var centerChange = $("#wz-change-centro", host);
      if (centerChange) centerChange.addEventListener("click", function () {
        var picker = $("#wz-centro-picker", host);
        var servicioNombreEsc = esc(servicio.nombre);
        var chooseCenterTitle = esc(T("service.center.choose_title", { servicio: servicioNombreEsc }, "Elegí otro centro para {servicio}"));
        picker.innerHTML = '<fieldset class="wz-center-picker"><legend>' + chooseCenterTitle + '</legend><div class="wz-spin"><i></i> ' + T("service.center.searching", null, "Buscando centros…") + '</div></fieldset>';
        equivalentCentros().then(function (options) {
          if (!options.length) {
            var centroNombreEsc = esc(centroNombre);
            picker.innerHTML = '<fieldset class="wz-center-picker"><legend>' + chooseCenterTitle + "</legend>" +
              '<p class="wz-confirm-note">' + T("service.center.only_at", { centro: centroNombreEsc }, "Este estudio sólo se hace en {centro}.") + "</p>" +
              '<button type="button" class="wz-change-link wz-center-cancel" id="wz-center-cancel">' + T("service.common.close", null, "Cerrar") + '</button></fieldset>';
            $("#wz-center-cancel", picker).addEventListener("click", function () { picker.innerHTML = ""; });
            return;
          }
          picker.innerHTML = '<fieldset class="wz-center-picker"><legend>' + chooseCenterTitle + "</legend>" +
            options.map(function (o) { return '<button type="button" class="wz-center-choice" data-service="' + esc(o.servicio.id) + '">' + esc(o.centro.name) + "</button>"; }).join("") +
            '<button type="button" class="wz-change-link wz-center-cancel" id="wz-center-cancel">' + T("service.common.cancel", null, "Cancelar") + '</button></fieldset>';
          picker.querySelectorAll("[data-service]").forEach(function (choice) {
            choice.addEventListener("click", function () {
              var match = options.filter(function (o) { return o.servicio.id === choice.getAttribute("data-service"); })[0];
              if (!match) return;
              holdLiberar();
              setServicioCentro(match.servicio, match.centro);
              wiz.preferencia = null; wiz.slotMedico = null; wiz.slotMedicoId = null; wiz.slotCuando = null;
              wiz.slotDia = null; wiz.slotHora = null; recordarSlotEnUrl(null, null);
              stepHorario();
            });
          });
          $("#wz-center-cancel", picker).addEventListener("click", function () { picker.innerHTML = ""; });
        });
      });

      $("#wz-send", host).addEventListener("click", function () {
        guardarContactos().then(function (ok) { if (ok) submit(u, paciente); });
      });
    }

    function successContactHtml(lowercase) {
      var u = wiz.usuario || {};
      var email = String(u.email || "").trim();
      var tel = telefonoDelTurno(u).trim();
      var lead = lowercase
        ? T("service.success.notify_lower", null, "te avisamos")
        : T("service.success.notify_cap", null, "Te avisamos");
      if (email) {
        var emailHtml = Format.emailHtml(email);
        return T("service.success.notify_email", { lead: lead, email: emailHtml }, "{lead} por email a {email}.");
      }
      if (tel) {
        var telHtml = Format.telHtml(tel);
        return T("service.success.notify_whatsapp", { lead: lead, tel: telHtml }, "{lead} por WhatsApp al {tel}.");
      }
      var action = lowercase
        ? T("service.success.can_follow_lower", null, "podés seguir")
        : T("service.success.can_follow_cap", null, "Podés seguir");
      return T("service.time.follow_status", { action: action }, "{action} el estado desde Mis turnos.");
    }

    function successSummaryHtml() {
      var F2 = Dates;
      var paciente = pacienteDelTurno();
      var cuando = "";
      if (wiz.slotDia) {
        cuando = (F2 && F2.larga(wiz.slotDia, wiz.slotHora)) ||
          (wiz.slotDia + (wiz.slotHora ? ", " + wiz.slotHora : ""));
      } else if (wiz.sinTurno) {
        cuando = T("service.noslots.success_when", null, "El hospital te contacta para darte la fecha");
      } else {
        cuando = wiz.preferencia || T("service.success.center_confirms_date", null, "El centro confirma la fecha");
      }
      var rows = [
        [T("service.success.row_datetime", null, "Fecha y hora"), cuando],
        [T("service.success.row_service", null, "Atención"), servicio.nombre || "—"],
        [T("service.success.row_center", null, "Centro"), centroNombre || centro.name || "—"],
      ];
      if (personName(paciente)) rows.push([T("service.success.row_patient", null, "Paciente"), personName(paciente)]);
      if (!wiz.slotDia && wiz.sinTurno && wiz.caso) {
        rows.push([T("service.noslots.success_row_case", null, "Tu caso"), wiz.caso]);
      }
      // El turno ya es una solicitud guardada: «Agregar al calendario» baja el
      // archivo de ESA solicitud (/api/appointment/<id>.ics, con sesión), pegado
      // a la fecha que cuenta. Sin fecha real (sin turnos libres) no hay evento.
      var sid = Number((wiz.successResp || {}).id);
      var calLink = (wiz.slotDia && Number.isSafeInteger(sid) && sid > 0)
        ? '<a class="wz-success-cal" href="/api/appointment/' + sid + '.ics">' +
          '<img class="wz-ico" src="' + CAL_ICO + '" alt="" width="20" height="20">' +
          esc(T("service.calendar.add", null, "Agregar al calendario")) + "</a>"
        : "";
      return '<div class="wz-success-summary">' + rows.map(function (row, i) {
        return '<div class="wz-success-row"><div class="wz-success-lab">' + esc(row[0]) +
          '</div><div class="wz-success-val">' + esc(row[1]) + (i === 0 ? calLink : "") + "</div></div>";
      }).join("") + "</div>";
    }

    function successHtml(confirmado) {
      // Sin turnos libres: lo que viene es un llamado del hospital, no un turno
      // confirmado (contraparte técnica, 2026-10-17) — se dice así, con por dónde avisa.
      var porCaso = !confirmado && !wiz.slotDia && wiz.sinTurno && (MODE === "turno" || !!wiz.caso);
      var intro = confirmado
        ? "<strong>" + T("service.success.confirmed_title", null, "Listo, el turno ya está confirmado.") + "</strong> " + successContactHtml(false)
        : porCaso
        ? "<strong>" + T("service.noslots.success_title", null, "El hospital te va a contactar para evaluar tu caso.") + "</strong> " + successContactHtml(false)
        : (MODE === "turno"
          ? "<strong>" + T("service.success.pending_title_turno", null, "Ahora el hospital confirma el turno.") + "</strong> " + successContactHtml(false)
          : "<strong>" + T("service.success.pending_title_orden", null, "Ahora el hospital revisa tu orden.") + "</strong> " +
            T("service.success.when_approved_prefix", null, "Cuando la apruebe, ") +
            successContactHtml(true));
      // wiz.successResp: la misma respuesta del server que renderSuccess ya
      // guardó ahí (nunca un parámetro nuevo acá — un test de contrato fija
      // la firma exacta de successHtml).
      // Con la entrevista previa habilitada para este médico, contarle los
      // síntomas ES la acción primaria del éxito (responsable del producto, 2026-08-01 —
      // reemplaza el link de texto de la fase 2): botón grande, y "Ver mis
      // solicitudes" baja a secundario (una sola CTA llena por pantalla).
      // «Ver mis solicitudes» left this page (the product owner 26.10: not needed): the
      // receipt is the summary, «Mi cuenta» is in the top bar, and the one
      // button left to press is the coverage's «Guardar cobertura».
      var entrevista = (wiz.successResp || {}).entrevista;
      var acciones = (entrevista && entrevista.ofrecer && entrevista.url)
        ? '<p class="wz-entrevista-lead">' + T("service.success.entrevista_lead", null,
            "Antes de la consulta, contanos qué te pasa y dejale al médico las preguntas que tengas, por ejemplo sobre un remedio. Lo lee en la consulta y tu turno rinde más.") + "</p>" +
          '<div class="wz-btnstack">' +
            '<a class="wz-cta-lg" href="' + esc(entrevista.url) + '">' + T("service.success.entrevista_cta", null, "Contanos qué te pasa") + '</a>' +
          "</div>"
        : "";
      // Visibilidad de donación de sangre, contextual (reunión hospital
      // 2026-08-27, la contraparte técnica pidió visibilidad — el responsable del producto descartó un banner fijo):
      // un renglón discreto DESPUÉS de la acción principal, nunca antes ni
      // como pastilla — el hospital necesita donantes, no compite con el
      // trámite que la persona vino a hacer.
      var donateHtml = '<p class="wz-donate-note">' + T("service.success.donate_note",
        { link: '<a href="/blood-donation">' +
            T("service.success.donate_cta", null, "sumate como donante") + '</a>' },
        "Ya que estás: el hospital necesita donantes de sangre — {link}.") + "</p>";
      return '<div class="wz-success">' +
        '<div class="wz-success-state"><p>' + intro + "</p></div>" +
        successSummaryHtml() +
        acciones +
        donateHtml +
        "</div>";
    }

    /* La "entrada" de este paso es replaceState, no pushState (PRG: el envío
       ya se hizo, "Atrás" desde acá NUNCA puede reaparecer en Confirmá con el
       botón Enviar habilitado — reaparecería lista para un doble envío). */
    function mountSuccessCoverage(resp) {
      if (!resp || !Number.isSafeInteger(Number(resp.id)) || Number(resp.id) <= 0) return;
      var mount = document.createElement("section");
      host.querySelector(".wz-success-summary").insertAdjacentElement("afterend", mount);
      loadAccountForm().then(function (mod) {
        if (currentStep !== "success" || !mount.isConnected || !host.contains(mount)) return;
        coverageDispose = mod.renderPostbookingCoverage(mount, { requestId: Number(resp.id), userKey: wiz.usuario && wiz.usuario.dni });
      }).catch(function () { mount.remove(); /* Receipt actions remain available. */ });
    }

    function renderSuccess(resp) {
      disposeCoverage();
      if (resp) wiz.successResp = resp;
      resp = resp || wiz.successResp || {};
      markSuccess();
      widenHost(0);
      holdTickerStop();
      paginaBlanca(false);
      ayudaEnShell(true);  // el trámite terminó: la salida vuelve a su lugar
      // Confirmación automática (reunión hospital 2026-07-24): el server ya
      // aprobó el turno — no hay revisión, el mensaje dice CONFIRMADO y repite
      // el horario elegido. `resp.estado` sólo viene "aprobada" en servicios
      // con auto_confirmar.
      if (resp && resp.estado === "aprobada") {
        setTitle(T("service.success.title_confirmed", null, "Turno confirmado"));
        setSteps(""); setSub("");
        host.innerHTML = '<div class="sec">' + successHtml(true) + "</div>";
        mountSuccessCoverage(resp);
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      // R4: preferí el match del server (preparacion.match_estudios contra lo
      // que el modelo leyó de LA ORDEN, puede traer más de un estudio); si el
      // ai_json no lo trae (cache viejo, ruta legacy), el match local de un
      // solo texto sigue de respaldo.
      var prepEntries = (wiz.ai && Array.isArray(wiz.ai.preparacion) && wiz.ai.preparacion.length)
        ? wiz.ai.preparacion
        : (function () { var pe = prepFor(wiz, servicio); return pe ? [pe] : []; })();
      var prepHtml = prepEntries.map(function (e) { return prepCardHtml(e); }).join("");
      setTitle(T("service.success.title_sent", null, "Solicitud enviada"));
      setSteps(""); setSub("");
      host.innerHTML =
        '<div class="sec wz-panel-stack">' + successHtml(false) + prepHtml + "</div>";
      mountSuccessCoverage(resp);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    function submit(u, paciente) {
      var btn = $("#wz-send", host), msg = $("#wz-msg", host);
      function show(t, kind) {
        msg.style.display = "block";
        msg.style.color = kind === "err" ? "var(--accent)" : "var(--text-2)";
        msg.textContent = t;
      }
      // El turno lleva el número escrito en Confirmá; la memoria de identidad
      // del navegador sigue siendo la de la cuenta.
      var contacto = normPhone(telefonoDelTurno(u));
      saveIdent({ contacto: normPhone((u && u.contacto) || "") });
      var payload = { servicio_id: servicio.id, centro_slug: servicio.centro, contacto: contacto };
      if (paciente && paciente.dni && onlyDigits(paciente.dni) !== onlyDigits(u && u.dni)) payload.paciente_dni = onlyDigits(paciente.dni);
      if (wiz.preferencia) payload.preferencia = wiz.preferencia;
      if (!wiz.slotDia && wiz.sinTurno && wiz.caso) payload.caso = wiz.caso;
      if (!wiz.slotDia && wiz.sinTurno && wiz.casoChat) payload.caso_chat = wiz.casoChat;
      if (wiz.token) payload.orden_token = wiz.token;
      else if (wiz.ordenRaw) payload.orden = wiz.ordenRaw;
      // Slot concreto elegido (soft-lock, 2026-07-16): el server vuelve a
      // chequear/tomar el hold al crear la solicitud, así que este POST es la
      // verdad final aunque el hold haya vencido en el medio.
      if (wiz.slotDia && wiz.slotHora) {
        payload.slot_dia = wiz.slotDia;
        payload.slot_hora = wiz.slotHora;
        if (wiz.holdToken) {
          payload.hold_token = wiz.holdToken;
          // Idempotencia de Confirmá (2026-08-05, data-integrity guards): el
          // mismo hold_token viaja también como idem_key — un doble tap del
          // botón o un reintento tras un timeout de red vuelve a mandar el
          // MISMO token, y el server devuelve la solicitud ya creada en vez
          // de intentar crear (o contar contra el límite) una segunda.
          payload.idem_key = wiz.holdToken;
        }
        // El médico del bloque elegido: sólo decide si el éxito ofrece la
        // entrevista previa (su override puede estar ON con el default del
        // sistema OFF) — la asignación real la sigue haciendo el hospital.
        if (wiz.slotMedicoId) payload.slot_medico_id = wiz.slotMedicoId;
      }

      btn.disabled = true;
      show(T("service.submit.sending", null, "Enviando…"), "ok");
      evStep("submit");
      casoChatListo().then(function () {
        return fetch("/api/requests", {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
      }).then(function (res) {
        if (res.ok && res.j && res.j.ok !== false) {
          saveHoldToken(null);  // ya quedó vinculado a la solicitud del lado del server
          olvidarOrdenHandoff();  // la orden ya es de la solicitud: no puede reaparecer en la próxima
          evStep("done");
          renderSuccess(res.j);
          return;
        }
        evStep("error");
        btn.disabled = false;
        if (res.status === 401) { show(T("service.submit.session_expired", null, "Se cerró tu sesión. Ingresá de nuevo."), "err"); stepCuenta(); return; }
        var medicoFuera = res.status === 400 && res.j &&
          (res.j.codigo === "medico_inexistente" || res.j.codigo === "medico_no_atiende_servicio");
        if ((res.status === 409 && res.j && res.j.error === "turno_tomado") || medicoFuera) {
          // Justo se lo llevaron mientras completaba el resto (o el médico de
          // ese horario dejó el catálogo): se elige de nuevo el horario, todo
          // lo demás (orden/cuenta/paciente) queda cargado — nunca hacer
          // perder el formulario por esto.
          var perdidoDia = wiz.slotDia, perdidoHora = wiz.slotHora;
          wiz.preferencia = null; wiz.slotMedico = null; wiz.slotMedicoId = null; wiz.slotCuando = null;
          wiz.slotDia = null; wiz.slotHora = null; recordarSlotEnUrl(null, null); saveHoldToken(null);
          if (medicoFuera) { horarioNotice = res.j.error; diaPerdido = perdidoDia || ""; }
          else if (perdidoDia) perdido(perdidoDia, perdidoHora, "taken");
          else horarioNotice = T("service.submit.slot_taken", null, "Justo alguien tomó ese horario. Elegí otro — el resto ya queda cargado.");
          stepHorario();
          return;
        }
        if (res.status === 403 && res.j && res.j.error === "bloqueado_por_ausencias") {
          // The server's text names the installation's main center (the product owner 23.10).
          show(res.j.detalle || T("service.submit.blocked_absences", null, "Por tus ausencias recientes, este turno no se puede pedir por la web. Acercate al hospital o al centro de salud para que te ayudemos."), "err");
          return;
        }
        if (res.j && res.j.detalle && res.j.accion &&
            ((res.status === 409 && res.j.error === "limite_turnos_activos") || res.j.outcome === "unknown")) {
          // The way out lives where the patient can take it (TASK-49): the
          // server's sentence and the link to «Mis turnos» — where the one they
          // already hold is cancelled, or where a request whose save the
          // database left unknown shows up if it landed. A page link, not a button.
          // Unknown outcome: the request may already exist, so «Enviar» stays
          // off — the check is «Mis turnos», not a second send.
          if (res.j.outcome === "unknown") btn.disabled = true;
          show(res.j.detalle, "err");
          var salida = res.j.accion;
          if (salida.url && salida.texto && /^\/(?!\/)/.test(salida.url)) {
            msg.innerHTML = esc(res.j.detalle) + ' <a class="stz-link" href="' + esc(salida.url) + '">' + esc(salida.texto) + "</a>";
          }
          return;
        }
        show((res.j && (res.j.detalle || res.j.error)) || T("service.submit.generic_error", null, "No pudimos enviar la solicitud. Probá de nuevo o llevá la orden a la mesa de admisión."), "err");
      }).catch(function () {
        btn.disabled = false;
        show(T("service.submit.network_error", null, "Error de red. Probá de nuevo o llevá la orden a la mesa de admisión."), "err");
      });
    }

    // History contains only the receipt ID. Reload reads the authorized detail
    // and current session; it NEVER returns to Confirm or repeats booking POST.
    var completedId = history.state && history.state.wzStep === "success" && Number(history.state.wzRequestId);
    if (Number.isSafeInteger(completedId) && completedId > 0) {
      currentStep = "success";
      setTitle(T("service.success.restore_title", null, "Tu solicitud")); setSteps(""); setSub("");
      host.innerHTML = '<p role="status">' + T("service.success.restoring", null, "Consultando tu solicitud…") + '</p><a href="/my-requests">' + T("service.success.view_requests", null, "Ver mis turnos") + '</a>';
      var restoreHost = host.firstChild, restoreRoute = location.href;
      var restoreController = null, restoreGeneration = 0, restoreStopped = false, restoreSuspended = false;
      // A reloaded receipt has no wizard state to replay. BFCache traversal
      // may emit popstate after pageshow: the same entry keeps its reads/UI;
      // another entry only disposes them, never paints an empty success.
      receiptHistory = function (event) {
        var state = event && event.state;
        if (state && state.wzStep === "success" && Number(state.wzRequestId) === completedId && location.href === restoreRoute) return;
        stopRestore();
      };
      function cancelRestoreRead() {
        restoreGeneration++;
        if (restoreController) restoreController.abort();
      }
      function stopRestore() {
        if (restoreStopped) return;
        restoreStopped = true; cancelRestoreRead(); restoreObserver.disconnect();
        window.removeEventListener("pagehide", hideRestore);
        window.removeEventListener("pageshow", showRestore);
        window.removeEventListener("hashchange", stopRestore);
        window.removeEventListener("storage", sessionRestore);
        window.removeEventListener("stz:session-change", sessionRestore);
      }
      function hideRestore(event) {
        if (!event.persisted) { stopRestore(); return; }
        // Keep the return listener while aborting this generation. An aborted
        // fetch must not dispose the mount that BFCache will bring back.
        restoreSuspended = true; cancelRestoreRead();
      }
      function showRestore(event) {
        if (!event.persisted || !restoreSuspended || restoreStopped) return;
        restoreSuspended = false; startRestore();
      }
      function sessionRestore(event) {
        if (event.type === "storage" && event.key && event.key !== "st_identity") return;
        if (!restoreSuspended && !restoreStopped) startRestore();
      }
      var restoreObserver = new MutationObserver(function () { if (!restoreHost.isConnected) stopRestore(); });
      restoreObserver.observe(document.body, { childList: true, subtree: true });
      window.addEventListener("pagehide", hideRestore);
      window.addEventListener("pageshow", showRestore);
      window.addEventListener("hashchange", stopRestore);
      window.addEventListener("storage", sessionRestore);
      window.addEventListener("stz:session-change", sessionRestore);
      function startRestore() {
        if (restoreStopped || restoreSuspended) return;
        if (!restoreHost.isConnected || location.href !== restoreRoute) { stopRestore(); return; }
        cancelRestoreRead();
        var generation = restoreGeneration, controller = new AbortController(), restoreHint;
        restoreController = controller;
        try { restoreHint = localStorage.getItem("st_identity"); } catch (_) {}
        function live() {
          return !restoreStopped && !restoreSuspended && generation === restoreGeneration &&
            !controller.signal.aborted && restoreHost.isConnected && location.href === restoreRoute;
        }
        Promise.all(["/api/requests/" + completedId, "/api/account/me"].map(function (url) {
          return fetch(url, { credentials: "same-origin", signal: controller.signal }).then(function (r) {
            if (!r.ok) throw new Error("unavailable"); return r.json();
          });
        })).then(function (data) {
          if (!live()) return null;
          return fetch("/api/account/me", { credentials: "same-origin", signal: controller.signal })
            .then(function (r) { if (!r.ok) throw new Error("unavailable"); return r.json(); })
            .then(function (session) {
              if (!session.usuario || !data[1].usuario || String(session.usuario.dni) !== String(data[1].usuario.dni)) throw new Error("unavailable");
              return data;
            });
        }).then(function (data) {
          if (!data || !live()) return;
          var currentHint;
          try { currentHint = localStorage.getItem("st_identity"); } catch (_) {}
          if (restoreHint !== currentHint) throw new Error("unavailable");
          var request = data[0].solicitud, user = data[1].usuario;
          if (!request || !user || request.turno_pasado || Number(request.id) !== completedId || String(request.servicio_id) !== String(servicio.id) || request.centro_slug !== servicio.centro || ["pendiente", "aprobada"].indexOf(request.estado) < 0) throw new Error("unavailable");
          wiz.usuario = user;
          wiz.paciente = { dni: request.paciente_dni, nombre: request.paciente_nombre || "" };
          wiz.preferencia = request.preferencia_horario || null;
          wiz.ai = { preparacion: request.preparacion || [] };
          var when = request.turno_dt;
          wiz.slotDia = when ? when.slice(0, 10) : (request.hold && request.hold.dia) || null;
          wiz.slotHora = when ? when.slice(11, 16) : (request.hold && request.hold.hora) || null;
          stopRestore();
          renderSuccess(request);
        }).catch(function () {
          if (!live()) return;
          restoreHost.textContent = T("service.success.restore_unavailable", null, "Consultá el estado actual en Mis turnos. No volvimos a enviar el pedido.");
          stopRestore();
        });
      }
      startRestore();
      return;
    }

    // Con el turno ya elegido en la URL sabemos de entrada que van a hacer
    // falta la sesión y las personas a cargo: se piden junto con los turnos,
    // no uno después del otro.
    if (avanzandoSolo) { sesionPrefetch = fetchSesion(); depsPrefetch = fetchDependientes(); }
    if (MODE === "turno") { if (TURNOS) renderSlots(TURNOS); else stepHorario(); }
    else if (ELEGIR_PRIMERO || ordenPrecargada) stepHorario();
    else startOrdenConEdad();
  }

  /* --- entrada del formulario (modos solicitud_con_orden / solicitud) --- */
  function renderForm(host, mode) {
    injectStyle();
    loadPrep();
    Wizard(host, { mode: mode });
  }

  /* --- aviso por edad para modos sin formulario --- */
  function renderGateOnly(host) {
    if (!Edad) return;
    var edadNum = Edad.edadDesde((loadIdent() || {}).nacimiento);
    var g = gateHtml(edadNum);
    if (g) host.innerHTML = '<div class="sec">' + g + "</div>";
  }

  var formHost = document.getElementById("svc-form-host");
  if (formHost && (servicio.modo === "solicitud_con_orden" || servicio.modo === "solicitud")) {
    // El paso de subida no depende de /api/services (sólo se usa para las
    // alternativas por edad, recién en Confirmá) — renderizamos ya y lo traemos
    // en segundo plano, sin esperar la red antes del primer paint.
    // modo `solicitud` = turno programado sin orden: el wizard arranca en
    // "Elegí un turno" (mode:"turno") y salta la subida de la orden.
    fetchServicios();
    // Los UMD anteriores terminaban de cargar antes de service.js. Esperar
    // las dos promesas una sola vez acá conserva esa garantía para TODO el
    // wizard (edad + grilla mensual), incluso si una persona toca rápido un
    // camino que consulta la edad. Los catch de edadLista/calLista resuelven
    // igual, así que una falla sigue fail-open. tsLista entra acá porque el
    // primer paso del wizard (subir la orden) monta el widget de Turnstile.
    Promise.all([i18nReady, edadLista, calLista, datesLista, formatLista, prepLista, tsLista]).then(function () {
      renderForm(formHost, servicio.modo === "solicitud" ? "turno" : "orden");
    }).catch(function (error) {
      console.error("[service] canonical module load:", error);
      uploadUnavailable();
      // The server already rendered the honest first task. Preserve that
      // geometry while replacing it with the usable recovery message.
      if (formHost.classList.contains("svc-form-host--schedule-boot")) {
        formHost.classList.remove("svc-form-host--schedule-boot");
        formHost.classList.add("svc-form-host--schedule-recovery");
      } else {
        formHost.classList.remove("svc-form-host--schedule-fallback", "svc-form-host--schedule-recovery");
      }
      formHost.innerHTML = '<div class="sec"><p class="svc-sub">' + esc(T("service.submit.network_error", null, "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.")) + "</p></div>";
    });
  } else {
    uploadUnavailable();
    // Los modos sin formulario sólo muestran, si hace falta, el aviso por
    // edad: ese gate tampoco decide antes de que el módulo de edad esté
    // (edadLista arriba) — el import fallado deja el aviso vacío, igual que
    // el camino fail-open anterior. /api/services (las alternativas por
    // edad) arranca en paralelo con el import; el render espera a ambos.
    var serviciosListos = fetchServicios();
    Promise.all([i18nReady, edadLista, datesLista, formatLista, prepLista, serviciosListos]).then(function () {
      var gateHost = document.getElementById("svc-gate-host");
      if (!gateHost) return;
      // Walk-in / demanda espontánea: se atiende por orden de llegada — NUNCA
      // proponer turnos acá aunque Mi Salud Digital tenga agenda (contradice el
      // cartel "No necesitás turno"). El modo turno del Wizard queda reservado
      // para un futuro modo "turno programado sin orden" (web/147).
      renderGateOnly(gateHost);
    }).catch(function (error) {
      console.error("[service] canonical module load:", error);
      var gateHost = document.getElementById("svc-gate-host");
      if (gateHost) gateHost.innerHTML = '<div class="sec"><p class="svc-sub">' + esc(T("service.submit.network_error", null, "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.")) + "</p></div>";
    });
  }
}
