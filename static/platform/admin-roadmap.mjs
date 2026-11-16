/* admin-roadmap.mjs — native lifecycle-owned controller for the direct
 * /admin/plan screen: el plan de puesta en marcha en Pinamar, contado dos
 * veces — «En pocas palabras» para la administración del hospital y «Detalle
 * técnico» para quien opera el sistema.
 *
 * Static content (no API): edit it here. Sub-tab state is ?view=technical.
 * The four content blocks are built at render time, AFTER the i18n module
 * resolves — the legacy controller froze them at evaluation time, before
 * asynchronous i18n finished, so translations never reached the screen. A
 * missing manifest entry or a failed i18n import fails closed through
 * onError / the visible alert line; it never renders untranslated content
 * as if the dependency had loaded. */
const mounts = new WeakMap();

export function mount(host, options = {}) {
  if (!host) throw new Error("Roadmap host is required");
  if (mounts.has(host)) mounts.get(host)();
  var disposed = false;
  var ready = false;
  var EMR_SYNC_ACTIVE = options.emrSync === true;
  function alive() { return !disposed && host.isConnected !== false; }
  function dispose() {
    if (disposed) return;
    disposed = true;
    window.removeEventListener("popstate", onPopstate);
    document.removeEventListener("click", onClick);
    if (mounts.get(host) === dispose) mounts.delete(host);
  }
  function importStatic(name) {
    var url = window.SANA_STATIC && window.SANA_STATIC[name];
    return url ? import(url) : Promise.reject(new Error("Missing Roadmap dependency: " + name));
  }

  var I18n = null;
  var i18nReady = importStatic("platform/i18n.mjs").then(function (m) { I18n = m; return m; });
  function T(key, vars, fallback) {
    return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function inyectarCss() {
    if (document.getElementById("roadmap-css")) return;
    var st = document.createElement("style");
    st.id = "roadmap-css";
    st.textContent = [
      ".rm-flow{display:flex;align-items:stretch;margin:4px 0 10px;overflow-x:auto;padding-bottom:4px;}",
      ".rm-node{flex:1 1 0;min-width:148px;border:1.5px solid var(--border,#e4e4e0);border-radius:10px;padding:11px 13px;background:var(--panel);}",
      ".rm-node h3{font-size:14px;margin:0 0 4px;font-weight:650;}",
      ".rm-node p{font-size:13px;color:var(--hint-ink);margin:0;line-height:1.45;}",
      ".rm-node--ok{border-color:var(--done);background:var(--state-done-bg);}",
      ".rm-node--now{border-color:var(--info);background:var(--info-soft);}",
      ".rm-node--wait{border-color:var(--warn);background:var(--warn-soft);}",
      ".rm-node--future{background:var(--bg-2,#f3f3f0);}",
      ".rm-arrow{flex:0 0 24px;display:flex;align-items:center;justify-content:center;color:var(--hint-ink);font-size:17px;}",
      ".rm-leyenda{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:var(--hint-ink);margin:0 0 26px;}",
      ".rm-progress{display:grid;grid-template-columns:auto minmax(180px,420px);align-items:center;gap:10px 18px;margin:4px 0 22px;max-width:680px;}",
      ".rm-progress strong{font-size:30px;line-height:1;}",
      ".rm-progress-copy{font-size:14px;color:var(--hint-ink);}",
      ".rm-progress-track{grid-column:1/-1;height:8px;border-radius:4px;background:var(--bg-2,#f3f3f0);overflow:hidden;}",
      ".rm-progress-fill{display:block;width:var(--rm-progress,53%);height:100%;background:var(--done);}",
      ".rm-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;}",
      ".rm-dot--ok{background:var(--done);}",
      ".rm-dot--now{background:var(--info);}",
      ".rm-dot--wait{background:var(--warn);}",
      ".rm-dot--future{background:var(--text-4);}",
      ".rm-h2{font-size:17px;font-weight:650;margin:30px 0 4px;}",
      ".rm-h2sub{color:var(--hint-ink);font-size:14px;margin:0 0 12px;}",
      ".rm-lista{columns:2;column-gap:34px;font-size:14px;margin:10px 0 0;padding-left:20px;}",
      ".rm-lista li{margin-bottom:5px;break-inside:avoid;}",
      ".rm-lista--done{list-style:none;padding-left:0;}",
      ".rm-lista--done li{position:relative;padding-left:22px;}",
      ".rm-lista--done li::before{content:'✓';position:absolute;left:0;top:0;color:var(--done);font-weight:800;}",
      ".rm-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-top:12px;}",
      ".rm-card{background:var(--panel);border:1px solid var(--border,#e4e4e0);border-radius:10px;padding:12px 14px;}",
      ".rm-card h4{font-size:14.5px;margin:0 0 4px;font-weight:650;}",
      ".rm-card p{font-size:13px;color:var(--hint-ink);margin:0;line-height:1.5;}",
      ".rm-st{font-size:13px;font-weight:600;display:block;margin-bottom:4px;}",
      ".rm-st .rm-dot{position:relative;top:-1px;}",
      ".rm-st--ok{color:var(--done);}",
      ".rm-st--now{color:var(--info);}",
      ".rm-st--wait{color:var(--warn);}",
      ".rm-st--future{color:var(--hint-ink);}",
      ".rm-fin{font-size:14.5px;margin:12px 0 0;max-width:720px;line-height:1.55;}",
      ".rm-pasos{margin:10px 0 0;padding-left:22px;font-size:14.5px;}",
      ".rm-pasos li{margin-bottom:9px;}",
      ".rm-quien{color:var(--hint-ink);font-size:13px;}",
      ".rm-tabs{display:flex;align-items:center;gap:4px;width:max-content;max-width:100%;",
      "padding:4px;margin:0 0 22px;border-radius:11px;background:var(--bg-2,#f3f3f0);}",
      ".rm-tabs a{display:inline-flex;align-items:center;justify-content:center;min-height:38px;padding:0 14px;",
      "border-radius:8px;color:var(--hint-ink,var(--text-2,#666));font-size:13.5px;font-weight:600;text-decoration:none;}",
      ".rm-tabs a:hover{color:var(--hover-ink);background:var(--hover-bg);}",
      ".rm-tabs a.on{color:var(--accent-hover);background:var(--panel,#fff);box-shadow:0 1px 3px rgba(20,30,24,.12);}",
      ".rm-tabs a:focus-visible{outline:3px solid var(--focus-ring);outline-offset:1px;}",
      "@media(max-width:1100px){.rm-flow{flex-direction:column;overflow-x:visible;}",
      ".rm-arrow{transform:rotate(90deg);height:20px;flex-basis:auto;}",
      ".rm-node{min-width:0;}.rm-lista{columns:1;}.rm-progress{grid-template-columns:auto 1fr;}}",
    ].join("");
    document.head.appendChild(st);
  }

  /* El diagrama es el mismo en los dos tabs: en lenguaje simple ya le sirve
   * a cualquiera de los dos lectores. Termina en el lanzamiento. */
  function flowHtml() {
    return '<div class="rm-flow">' +
    '<div class="rm-node rm-node--ok"><h3>' + T("admin.system.roadmap.flow.ready_title", null, "El sistema ya está listo") + "</h3>" +
    "<p>" + T("admin.system.roadmap.flow.ready_desc", null, "La página de turnos funciona completa, en el servidor del proyecto.") + "</p></div>" +
    '<div class="rm-arrow">→</div>' +
    '<div class="rm-node rm-node--wait"><h3>' + T("admin.system.roadmap.flow.step1_title", null, "Paso 1 · La Municipalidad") + "</h3>" +
    "<p>" + (EMR_SYNC_ACTIVE
      ? T("admin.system.roadmap.flow.step1_desc", null, "Conexión con el HSI, la dirección oficial y la fecha de lanzamiento.")
      : T("admin.system.roadmap.flow.step1_desc_no_emr", null, "La dirección oficial y la fecha de lanzamiento.")) + "</p></div>" +
    '<div class="rm-arrow">→</div>' +
    '<div class="rm-node rm-node--now"><h3>' + T("admin.system.roadmap.flow.step2_title", null, "Paso 2 · WhatsApp y accesos") + "</h3>" +
    "<p>" + T("admin.system.roadmap.flow.step2_desc", null, "Número oficial de WhatsApp del hospital y apertura de la página.") + "</p></div>" +
    '<div class="rm-arrow">→</div>' +
    '<div class="rm-node rm-node--now"><h3>' + T("admin.system.roadmap.flow.step3_title", null, "Paso 3 · El equipo del hospital") + "</h3>" +
    "<p>" + T("admin.system.roadmap.flow.step3_desc", null, "Usuarios y una capacitación corta para el personal.") + "</p></div>" +
    '<div class="rm-arrow">→</div>' +
    '<div class="rm-node rm-node--future"><h3>' + T("admin.system.roadmap.flow.launch_title", null, "Lanzamiento en Pinamar") + "</h3>" +
    "<p>" + T("admin.system.roadmap.flow.launch_desc", null, "Se abre la página y se les avisa a los vecinos.") + "</p></div>" +
    "</div>" +
    '<div class="rm-leyenda">' +
    '<span><span class="rm-dot rm-dot--ok"></span>' + T("admin.system.roadmap.flow.leg_ok", null, "listo") + "</span>" +
    '<span><span class="rm-dot rm-dot--now"></span>' + T("admin.system.roadmap.flow.leg_now", null, "lo hacemos nosotros ahora") + "</span>" +
    '<span><span class="rm-dot rm-dot--wait"></span>' + T("admin.system.roadmap.flow.leg_wait", null, "necesita a la Municipalidad") + "</span>" +
    '<span><span class="rm-dot rm-dot--future"></span>' + T("admin.system.roadmap.flow.leg_future", null, "después de los pasos") + "</span>" +
    "</div>";
  }

  function progressHtml() {
    return '<div class="rm-progress" style="--rm-progress:' + (EMR_SYNC_ACTIVE ? '53%' : '50%') + '" aria-label="' + (EMR_SYNC_ACTIVE
      ? T("admin.system.roadmap.progress.aria", null, "Progreso hacia el lanzamiento: 53 por ciento")
      : T("admin.system.roadmap.progress.aria_no_emr", null, "Progreso hacia el lanzamiento: 50 por ciento")) + '">' +
    '<strong>' + (EMR_SYNC_ACTIVE ? '53%' : '50%') + '</strong><span class="rm-progress-copy">' + (EMR_SYNC_ACTIVE
      ? T("admin.system.roadmap.progress.copy", null, "8 de 15 condiciones de lanzamiento ya están completas")
      : T("admin.system.roadmap.progress.copy_no_emr", null, "7 de 14 condiciones de lanzamiento ya están completas")) + '</span>' +
    '<span class="rm-progress-track" aria-hidden="true"><span class="rm-progress-fill"></span></span></div>';
  }

  function st(clase, texto) {
    return '<span class="rm-st rm-st--' + clase + '"><span class="rm-dot rm-dot--' +
      clase + '"></span>' + texto + "</span>";
  }
  function card(estado, titulo, cuerpo) {
    return '<div class="rm-card">' + estado + "<h4>" + titulo + "</h4><p>" + cuerpo + "</p></div>";
  }

  function simpleHtml() {
    return '<h2 class="rm-h2">' + T("admin.system.roadmap.simple.done_title", null, "Qué ya funciona") + '</h2>' +
    '<ul class="rm-lista rm-lista--done">' +
    "<li>" + T("admin.system.roadmap.simple.done1", null, "Los vecinos ven los 134 servicios de los 10 centros de salud, con los horarios reales") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done2", null, "El turno se saca desde el celular; la orden médica se sube con una foto y el sistema la lee solo") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done3", null, "Cuenta con el DNI; los padres sacan turnos para sus hijos") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done4", null, "El personal aprueba o rechaza cada solicitud desde este panel, con motivos estándar") + "</li>" +
    (EMR_SYNC_ACTIVE ? "<li>" + T("admin.system.roadmap.simple.done5", null, "La conexión con la historia clínica (HSI) está preparada y probada localmente") + "</li>" : "") +
    "<li>" + T("admin.system.roadmap.simple.done6", null, "El paciente recibe avisos por correo: pedido recibido, turno confirmado y un recordatorio el día anterior") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done7", null, "Si se libera un turno más cercano, se le ofrece a quien está esperando") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done8", null, "El chat de ayuda web y la integración con WhatsApp están construidos y probados") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done9", null, "Reportes para la dirección: demanda, horas de atención y rechazos evitables") + "</li>" +
    "<li>" + T("admin.system.roadmap.simple.done10", null, "Todo esto ya funciona en el servidor del proyecto") + "</li>" +
    "</ul>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.simple.step1_title", null, "Paso 1 — junto con la Municipalidad") + '</h2>' +
    '<p class="rm-h2sub">' + T("admin.system.roadmap.simple.step1_sub", null, "Esta parte no la podemos hacer solos: cada pieza necesita la cooperación de la Municipalidad.") + '</p>' +
    '<div class="rm-cards">' +
    (EMR_SYNC_ACTIVE ? card(st("wait", T("admin.system.roadmap.simple.step1c1_st", null, "Pendiente — lo más importante")), T("admin.system.roadmap.simple.step1c1_title", null, "Conexión con el HSI del hospital"),
      T("admin.system.roadmap.simple.step1c1_body", null, "Los turnos aprobados tienen que quedar en la historia clínica real. Falta el usuario y el acceso al HSI del hospital; en las pruebas el sistema ya lo hace.")) : "") +
    card(st("wait", T("admin.system.roadmap.simple.step1c2_st", null, "Pendiente")), T("admin.system.roadmap.simple.step1c2_title", null, "La dirección oficial"),
      T("admin.system.roadmap.simple.step1c2_body", null, "turnos.pinamar.gob.ar la habilita la Municipalidad. Hoy la página vive en una dirección provisoria.")) +
    card(st("wait", T("admin.system.roadmap.simple.step1c3_st", null, "Pedido el 16 de julio")), T("admin.system.roadmap.simple.step1c3_title", null, "Consulta de obras sociales (SISA)"),
      T("admin.system.roadmap.simple.step1c3_body", null, "El usuario oficial para consultar el padrón nacional de obras sociales. Mientras tanto funciona con la consulta pública.")) +
    card(st("wait", T("admin.system.roadmap.simple.step1c4_st", null, "Para definir más adelante")), T("admin.system.roadmap.simple.step1c4_title", null, "Fecha de lanzamiento"),
      T("admin.system.roadmap.simple.step1c4_body", null, "La presentación ya se hizo. Cuándo se lanza y cómo se les cuenta a los vecinos se define más adelante, sin apuro.")) +
    "</div>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.simple.step2_title", null, "Paso 2 — lo que hacemos nosotros") + '</h2>' +
    '<div class="rm-cards">' +
    card(st("now", T("admin.system.roadmap.simple.step2c1_st", null, "En construcción")), T("admin.system.roadmap.simple.step2c1_title", null, "Verificación por WhatsApp"),
      T("admin.system.roadmap.simple.step2c1_body", null, "El sistema verifica la identidad por WhatsApp: un código al crear la cuenta y para recuperar la contraseña. Hoy corre con un número de prueba.")) +
    card(st("now", T("admin.system.roadmap.simple.step2c2_st", null, "Lleva minutos")), T("admin.system.roadmap.simple.step2c2_title", null, "El número de WhatsApp del hospital"),
      T("admin.system.roadmap.simple.step2c2_body", null, "Con el número oficial, el chat y la verificación quedan andando para los vecinos.")) +
    card(st("now", T("admin.system.roadmap.simple.step2c3_st", null, "El día del lanzamiento")), T("admin.system.roadmap.simple.step2c3_title", null, "Abrir la página"),
      T("admin.system.roadmap.simple.step2c3_body", null, "Hoy está protegida con una clave provisoria para que nadie entre antes de tiempo. Sacarla lleva minutos.")) +
    "</div>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.simple.step3_title", null, "Paso 3 — el equipo del hospital") + '</h2>' +
    '<div class="rm-cards">' +
    card(st("now", T("admin.system.roadmap.simple.step3c1_st", null, "Listo para arrancar")), T("admin.system.roadmap.simple.step3c1_title", null, "Usuarios para el personal administrativo"),
      T("admin.system.roadmap.simple.step3c1_body", null, "Rosa Vázquez, Rosa Rivero y Natalia Cobas ya tienen su rol definido. Falta crearles el usuario y una capacitación corta.")) +
    card(st("now", T("admin.system.roadmap.simple.step3c2_st", null, "De a poco")), T("admin.system.roadmap.simple.step3c2_title", null, "Acceso para cada médico"),
      T("admin.system.roadmap.simple.step3c2_body", null, "Cada profesional puede ver su propia agenda y sus turnos. Se entrega gradualmente; no frena el lanzamiento.")) +
    card(st("now", T("admin.system.roadmap.simple.step3c3_st", null, "Ya en manos del hospital")), T("admin.system.roadmap.simple.step3c3_title", null, "El catálogo lo mantiene el hospital"),
      T("admin.system.roadmap.simple.step3c3_body", null, "Servicios, horarios y vacaciones se editan desde este panel; ya no dependen de una planilla.")) +
    "</div>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.simple.launch_title", null, "Lanzamiento en Pinamar") + '</h2>' +
    '<p class="rm-fin">' + T("admin.system.roadmap.simple.launch_body", null, "Con los tres pasos cumplidos, se saca la clave provisoria, se les avisa a los vecinos y seguimos el uso día a día desde los reportes de este panel.") + '</p>' +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.simple.later_title", null, "Más adelante") + '</h2>' +
    '<p class="rm-h2sub">' + T("admin.system.roadmap.simple.later_sub", null, "Ideas para después del lanzamiento, todavía no se están construyendo.") + '</p>' +
    '<div class="rm-cards">' +
    card(st("future", T("admin.system.roadmap.simple.later_badge", null, "Más adelante")), T("admin.system.roadmap.simple.later1_title", null, "Teleconsulta (turno por videollamada)"),
      T("admin.system.roadmap.simple.later1_body", null, "El paciente pide el turno y la consulta se hace por video. Sirve para traer especialistas que no viven en Pinamar: en la región ya hubo guardias de especialistas suspendidas por falta de profesionales, y la Provincia está impulsando la telemedicina.")) +
    card(st("future", T("admin.system.roadmap.simple.later_badge", null, "Más adelante")), T("admin.system.roadmap.simple.later2_title", null, "Resumen de la historia clínica"),
      T("admin.system.roadmap.simple.later2_body", null, "Antes de atender, el médico ve un resumen automático de los turnos y antecedentes del paciente, para no arrancar de cero cada vez.")) +
    "</div>";
  }

  function tecnicoHtml() {
    return '<h2 class="rm-h2">' + T("admin.system.roadmap.tec.built_title", null, "Ya construido") + '</h2>' +
    '<ul class="rm-lista rm-lista--done">' +
    "<li>" + T("admin.system.roadmap.tec.built1", null, "4 modos de atención, 134 servicios, 10 centros, 99 profesionales (planilla oficial de la Secretaría importada)") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built2", null, "Wizard con lectura de la orden por IA (Gemini Flash), soft-lock de turnos, lista de espera y cancelaciones") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built3", null, "Cuentas por DNI, cobertura vía PUCO/SISA, alta con foto del DNI (código de barras + OCR)") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built4", null, "Cola de revisión con sugerencias, resolución en lote y motivos estándar") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built5", null, "Roles admin / operador / médico, agenda por médico y feeds ICS") + "</li>" +
    (EMR_SYNC_ACTIVE ? "<li>" + T("admin.system.roadmap.tec.built6", null, "Push a HSI + resolutor de conflictos + resincronización automática") + "</li>" : "") +
    "<li>" + T("admin.system.roadmap.tec.built7", null, "4 correos al paciente (Mailgun) + recordatorio el día anterior") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built8", null, "Chat: un motor, tres puertas — widget web, WhatsApp (Meta + WAHA) y MCP — con traspaso web→WhatsApp") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built9", null, "Reportes narrativos, costos de IA y página «Sistema» con el estado de cada integración") + "</li>" +
    "<li>" + T("admin.system.roadmap.tec.built10", null, "Deploy automático con cada push, corriendo en el servidor del proyecto; sitio detrás de una clave previa al lanzamiento") + "</li>" +
    "</ul>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.tec.step1_title", null, "Paso 1 — junto con la Municipalidad") + '</h2>' +
    '<p class="rm-h2sub">' + T("admin.system.roadmap.tec.step1_sub", null, "El camino crítico: estas piezas son de la Municipalidad (the reference-deployment counterpart / la Secretaría) y se arman junto con ellos; los caminos de código ya existen y degradan con gracia.") + '</p>' +
    '<div class="rm-cards">' +
    (EMR_SYNC_ACTIVE ? card(st("wait", T("admin.system.roadmap.tec.step1c1_st", null, "Pendiente — el requisito duro")), T("admin.system.roadmap.tec.step1c1_title", null, "Acceso al HSI productivo"),
      T("admin.system.roadmap.tec.step1c1_body", null, "Producción corre con TURNOS_SKIP_HSI=1: falta el endpoint institucional y las credenciales para que las aprobaciones lleguen a la historia clínica real. Cliente, resolutor y resync ya están probados contra el HSI local.")) : "") +
    card(st("wait", T("admin.system.roadmap.tec.step1c2_st", null, "Pendiente")), T("admin.system.roadmap.tec.step1c2_title", null, "Dominio oficial"),
      T("admin.system.roadmap.tec.step1c2_body", null, "turnos.pinamar.gob.ar necesita la delegación DNS municipal. La página «Sistema» ya marca el host actual como provisorio. Turnstile se re-ata al dominio final.")) +
    card(st("wait", T("admin.system.roadmap.tec.step1c3_st", null, "Pedidas el 16 de julio")), T("admin.system.roadmap.tec.step1c3_title", null, "Credenciales SISA WS131"),
      T("admin.system.roadmap.tec.step1c3_body", null, "La consulta pública sin credenciales ya funciona en producción, así que esto suma robustez, no destraba.")) +
    card(st("wait", T("admin.system.roadmap.tec.step1c4_st", null, "Pendiente")), T("admin.system.roadmap.tec.step1c4_title", null, "Luz verde formal de la Secretaría"),
      T("admin.system.roadmap.tec.step1c4_body", null, "Presentación hecha (semana del 13/7) y el personal de agendas ya nombrado (reunión del 17/7). Falta la fecha de lanzamiento y quién lo anuncia.")) +
    "</div>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.tec.step2_title", null, "Paso 2 — identidad y mensajes, de nuestro lado") + '</h2>' +
    '<div class="rm-cards">' +
    card(st("now", T("admin.system.roadmap.tec.step2c1_st", null, "Próxima construcción")), T("admin.system.roadmap.tec.step2c1_title", null, "Verificación por WhatsApp"),
      T("admin.system.roadmap.tec.step2c1_body", null, "Código al registrarse y para recuperar la contraseña — el sistema está diseñado sobre WhatsApp. Faltan la plantilla de autenticación aprobada por Meta, la verificación del negocio y el número real; el resto se construye ya contra el número de prueba.")) +
    card(st("now", T("admin.system.roadmap.tec.step2c2_st", null, "Equipo del proyecto, minutos")), T("admin.system.roadmap.tec.step2c2_title", null, "Token permanente de Meta + número real"),
      T("admin.system.roadmap.tec.step2c2_body", null, "El token actual del Cloud API venció y el número es el de prueba de Meta. Con token permanente y el número del hospital, la puerta de WhatsApp queda viva.")) +
    card(st("now", T("admin.system.roadmap.tec.step2c3_st", null, "Tarea de momento tranquilo")), T("admin.system.roadmap.tec.step2c3_title", null, "Migración de variables de entorno"),
      T("admin.system.roadmap.tec.step2c3_body", null, "Los nombres viejos en español siguen configurados en el servidor; el código lee los dos. Migrar una vez y después borrar los fallbacks.")) +
    card(st("now", T("admin.system.roadmap.tec.step2c4_st", null, "El día del lanzamiento")), T("admin.system.roadmap.tec.step2c4_title", null, "Sacar el Basic Auth"),
      T("admin.system.roadmap.tec.step2c4_body", null, "Un cambio de configuración. Webhooks, MCP y export ya están exentos, así que nada más se mueve.")) +
    "</div>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.tec.step3_title", null, "Paso 3 — personas, no código") + '</h2>' +
    '<div class="rm-cards">' +
    card(st("now", T("admin.system.roadmap.tec.step3c1_st", null, "Listo para ejecutar")), T("admin.system.roadmap.tec.step3c1_title", null, "Cuentas de operador + capacitación"),
      T("admin.system.roadmap.tec.step3c1_body", null, "Rosa Vázquez, Rosa Rivero y Natalia Cobas reciben su login de operador (el rol está desplegado desde el 17/7). Una guía de 1 página + una sesión práctica: cola, agendas y vacaciones.")) +
    card(st("now", T("admin.system.roadmap.tec.step3c2_st", null, "Gradual")), T("admin.system.roadmap.tec.step3c2_title", null, "Logins de médico"),
      T("admin.system.roadmap.tec.step3c2_body", null, "Cada profesional puede tener su cuenta con agenda propia + feed ICS. Se entrega de a poco; no frena el lanzamiento.")) +
    card(st("now", T("admin.system.roadmap.tec.step3c3_st", null, "Proceso del hospital")), T("admin.system.roadmap.tec.step3c3_title", null, "Mantenimiento del catálogo"),
      T("admin.system.roadmap.tec.step3c3_body", null, "La planilla importada fue el arranque; desde el lanzamiento el panel es la fuente de verdad y la Secretaría es dueña de altas y bajas de profesionales.")) +
    "</div>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.tec.week_title", null, "Cómo avanzar esta semana") + '</h2>' +
    '<ol class="rm-pasos">' +
    "<li><b>" + T("admin.system.roadmap.tec.week1_bold", null, "Un solo pedido agrupado a la contraparte técnica") + "</b>: " +
      (EMR_SYNC_ACTIVE
        ? T("admin.system.roadmap.tec.week1_rest", null, "endpoint + credenciales del HSI productivo, estado de SISA WS131 y el pedido de DNS de turnos.pinamar.gob.ar. Las tres cosas son suyas, un solo mensaje.")
        : T("admin.system.roadmap.tec.week1_rest_no_emr", null, "estado de SISA WS131 y el pedido de DNS de turnos.pinamar.gob.ar. Las dos cosas son suyas, un solo mensaje.")) +
      " <span class='rm-quien'>— " + T("admin.system.roadmap.tec.week1_who", null, "contraparte") + "</span></li>" +
    "<li><b>" + T("admin.system.roadmap.tec.week2_bold", null, "Construir la verificación por WhatsApp") + "</b>: " +
      T("admin.system.roadmap.tec.week2_rest", null, "el envío y la validación del código se programan ya contra el número de prueba; sólo la aprobación de la plantilla espera a Meta.") +
      " <span class='rm-quien'>— " + T("admin.system.roadmap.tec.week2_who", null, "nosotros, ahora") + "</span></li>" +
    "<li><b>" + T("admin.system.roadmap.tec.week3_bold", null, "El equipo del proyecto pega un token permanente de Meta") + "</b> " +
      T("admin.system.roadmap.tec.week3_rest", null, "y define número real vs. WAHA para el volumen del lanzamiento.") +
      " <span class='rm-quien'>— " + T("admin.system.roadmap.tec.week3_who", null, "Equipo del proyecto, minutos") + "</span></li>" +
    "<li><b>" + T("admin.system.roadmap.tec.week4_bold", null, "Redactar la guía de operadores") + "</b> " +
      T("admin.system.roadmap.tec.week4_rest", null, "para capacitar el mismo día que se entreguen los usuarios.") +
      " <span class='rm-quien'>— " + T("admin.system.roadmap.tec.week4_who", null, "nosotros, ahora") + "</span></li>" +
    "</ol>" +

    '<h2 class="rm-h2">' + T("admin.system.roadmap.tec.later_title", null, "Más adelante — evaluado, sin construir") + '</h2>' +
    '<p class="rm-h2sub">' + T("admin.system.roadmap.tec.later_sub", null, "No forma parte del lanzamiento. Bocetos de diseño en docs/roadmap/.") + '</p>' +
    '<div class="rm-cards">' +
    card(st("future", T("admin.system.roadmap.simple.later_badge", null, "Más adelante")), T("admin.system.roadmap.tec.later1_title", null, "Teleconsulta"),
      T("admin.system.roadmap.tec.later1_body", null, "Flag de modalidad en el servicio/agenda (presencial vs. video); al aprobar la solicitud se genera el link de la videollamada y se manda por correo/WhatsApp, visible también en la agenda del médico. Marco legal: ley 27.678 de telesalud. Diseño en docs/roadmap/teleconsulta.md.")) +
    card(st("future", T("admin.system.roadmap.simple.later_badge", null, "Más adelante")), T("admin.system.roadmap.tec.later2_title", null, "Resumen de historia clínica"),
      (EMR_SYNC_ACTIVE
        ? T("admin.system.roadmap.tec.later2_body", null, "Párrafo automático para el médico antes de la consulta, armado con nuestros propios datos (turnos, órdenes leídas) — la v1 no toca datos clínicos del HSI. La OCR de historias en papel ya tiene investigación propia: docs/medical-history-ocr-findings.md. Diseño de la v1 en docs/roadmap/resumen-historia-clinica.md.")
        : T("admin.system.roadmap.tec.later2_body_no_emr", null, "Párrafo automático para el médico antes de la consulta, armado sólo con nuestros propios datos (turnos y órdenes leídas). La OCR de historias en papel ya tiene investigación propia: docs/medical-history-ocr-findings.md. Diseño de la v1 en docs/roadmap/resumen-historia-clinica.md."))) +
    "</div>";
  }

  function render(sub) {
    var tecnico = sub === "tecnico";
    var simpleHref = "/admin/plan";
    var tecnicoHref = "/admin/plan?view=technical";
    host.innerHTML =
      progressHtml() + flowHtml() +
      '<nav class="rm-tabs" aria-label="' + T("admin.system.roadmap.tabs_aria", null, "Vista de la hoja de ruta") + '" role="tablist">' +
      '<a href="' + simpleHref + '" role="tab" aria-selected="' + (tecnico ? "false" : "true") + '"' + (tecnico ? "" : ' class="on"') + ">" + T("admin.system.roadmap.tab_simple", null, "En pocas palabras") + "</a>" +
      '<a href="' + tecnicoHref + '" role="tab" aria-selected="' + (tecnico ? "true" : "false") + '"' + (tecnico ? ' class="on"' : "") + ">" + T("admin.system.roadmap.tab_tecnico", null, "Detalle técnico") + "</a>" +
      "</nav>" +
      (tecnico ? tecnicoHtml() : simpleHtml());
  }

  function directSub() {
    return new URLSearchParams(location.search).get("view") === "technical" ? "tecnico" : "";
  }

  function canonicalDirectUrl() {
    return directSub() ? "/admin/plan?view=technical" : "/admin/plan";
  }

  function route() {
    if (!alive() || !ready) return;
    inyectarCss();
    if (location.search !== (directSub() ? "?view=technical" : "")) {
      history.replaceState({}, "", canonicalDirectUrl());
    }
    host.style.display = "";
    render(directSub());
  }

  function onPopstate() {
    route();
  }
  function onClick(event) {
    if (!alive() || !ready) return;
    if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target && event.target.closest && event.target.closest(".rm-tabs a");
    if (!link || !host.contains(link)) return;
    event.preventDefault();
    history.pushState({}, "", link.getAttribute("href"));
    route();
  }

  mounts.set(host, dispose);
  window.addEventListener("popstate", onPopstate);
  document.addEventListener("click", onClick);
  dispose.ready = i18nReady.then(function () {
    if (!alive()) return;
    ready = true;
    route();
  }).catch(function (error) {
    ready = false;
    if (!alive()) return;
    if (options.onError) options.onError(error);
    else host.innerHTML = '<p class="adm-empty" role="alert">' + esc(T("admin.system.common.load_error", null, "No se pudo cargar esta sección.")) + '</p>';
  });
  return dispose;
}
