/* Qué es la captura de síntomas, contado al médico (responsable del producto, 2026-10-27:
 * «explain on this page what it is and try to sell to the doctors»).
 *
 * UN solo bloque para los tres lugares donde se enciende: la pestaña
 * Síntomas de la ficha del médico (admin-content.mjs), Sistema › Captura de
 * síntomas (modules/intake/admin-entrevistas.mjs) y el encabezado de
 * «Pacientes de hoy» del propio médico (admin-queue.mjs). Cada pantalla
 * pone su propio control (botón o tabla) en `opts.action`; el texto y el
 * ejemplo son estos y nada más.
 *
 * Sólo hechos del inventario (docs/architecture/entrevista-previa.md):
 * hasta unas 8 preguntas desde casa y 4 o 5 en la puerta
 * (interview_agent._contexto_*), piso determinista de señales de alarma
 * (redflags.py, la IA sube y nunca baja), filtro de datos personales antes
 * de la IA (pii.py, como lo dice la política en patient_web/legal.py).
 * Nunca una mejora medida.
 */
"use strict";

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

export const css = [
  ".ix{display:grid;grid-template-columns:minmax(0,62ch) minmax(280px,400px);grid-template-areas:'lead ex' 'act ex' 'gain ex' 'how ex';",
  "gap:16px 40px;align-items:start;justify-content:start;font-size:16px;line-height:1.5;color:var(--text);max-width:1100px}",
  ".ix-lead{grid-area:lead;margin:0}",
  ".ix-action{grid-area:act;display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}",
  ".ix-action:empty{display:none}",
  ".ix-gain{grid-area:gain}.ix-how{grid-area:how}",
  ".ix-ex{grid-area:ex;margin:0}",
  ".ix h3{margin:0 0 8px;font-size:17px;line-height:1.3;color:var(--text)}",
  ".ix ul{margin:0;padding-left:20px}.ix li+li{margin-top:6px}",
  ".ix-card{display:block;margin:0;padding:20px;border:1px solid var(--border);border-radius:8px;background:var(--panel,#fff)}",
  ".ix-card-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;margin:0 0 16px}",
  ".ix-card h4{margin:16px 0 6px;font-size:16px;color:var(--text)}.ix-card-head+h4{margin-top:0}",
  ".ix-card p{margin:0}",
  ".ix-pick{display:flex;align-items:center;gap:10px;min-height:32px}",
  ".ix-pick input{width:20px;height:20px;margin:0;flex:0 0 auto;accent-color:var(--accent)}",
  ".ix-card .btn{margin-top:16px}",
  // Plegado (Sistema y «Pacientes de hoy»): el título se lee como título.
  ".ix-details>summary{padding:10px 0;font-size:17px;font-weight:650;color:var(--text);cursor:pointer}.ix-details>.ix{margin-top:8px}",
  "@media(max-width:760px){.ix{grid-template-columns:minmax(0,1fr);grid-template-areas:'lead' 'act' 'ex' 'gain' 'how';gap:16px}}",
].join("");

export function ensureCss(doc) {
  doc = doc || (typeof document !== "undefined" ? document : null);
  if (!doc || doc.getElementById("ix-css")) return;
  var s = doc.createElement("style");
  s.id = "ix-css";
  s.textContent = css;
  doc.head.appendChild(s);
}

// Un punto de la lista: el arranque en negrita (lo que lee quien escanea)
// y el resto en tinta normal. Las claves van escritas enteras para que la
// verificación de catálogos (test/i18n-fallbacks.test.mjs) las vea.
function item(strong, text) {
  return "<li><strong>" + esc(strong) + "</strong> " + esc(text) + "</li>";
}

// Una opción del ejemplo, tildada o no, como en la consulta. El ejemplo
// entero es `inert`: se ve como los controles reales y no se toca.
function pick(label, checked) {
  return '<label class="ix-pick"><input type="checkbox"' + (checked ? " checked" : "") + "><span>" + esc(label) + "</span></label>";
}

/* Plegado bajo «Qué es la captura de síntomas» (Sistema y «Pacientes de
 * hoy», donde el control propio de la pantalla queda afuera).
 * opts.className: clase extra de la pantalla; opts.open: arranca abierto. */
export function details(T, opts) {
  opts = opts || {};
  return '<details class="ix-details' + (opts.className ? " " + esc(opts.className) : "") + '"' + (opts.open ? " open" : "") + "><summary>"
    + esc(summary(T)) + "</summary>" + html(T) + "</details>";
}

/* The block's own name, for a screen that opens it from an icon
 * («Pacientes de hoy»): the text stays in this module. */
export function summary(T) {
  return T("admin.intake.explainer.summary", null, "Qué es la captura de síntomas");
}

/* T: el T(key, vars, fallback) de la pantalla que lo pinta.
 * opts.action: HTML ya escapado del control de esa pantalla (puede faltar). */
export function html(T, opts) {
  opts = opts || {};
  return '<div class="ix" data-intake-explainer>'
    + '<p class="ix-lead"><strong>' + esc(T("admin.intake.explainer.lead_strong", null, "Antes del turno, el paciente cuenta por chat qué le pasa."))
    + "</strong> " + esc(T("admin.intake.explainer.lead", null, "Vos lo recibís con los síntomas ordenados y una propuesta lista para revisar.")) + "</p>"
    + '<div class="ix-action">' + (opts.action || "") + "</div>"
    + '<section class="ix-gain"><h3>' + esc(T("admin.intake.explainer.gain_title", null, "Qué ganás")) + "</h3><ul>"
    + item(T("admin.intake.explainer.gain_1_strong", null, "No preguntás de cero:"),
      T("admin.intake.explainer.gain_1", null, "el motivo, desde cuándo, los antecedentes y la medicación ya están escritos."))
    + item(T("admin.intake.explainer.gain_2_strong", null, "Ves los diagnósticos posibles y los estudios sugeridos:"),
      T("admin.intake.explainer.gain_2", null, "elegís con un tilde o escribís los tuyos, y sumás la medicación de una lista. La propuesta no es una indicación médica: decidís vos."))
    + item(T("admin.intake.explainer.gain_3_strong", null, "La receta y la orden salen listas para imprimir,"),
      T("admin.intake.explainer.gain_3", null, "sin escribirlas a mano."))
    + item(T("admin.intake.explainer.gain_4_strong", null, "Los casos con señales de alarma aparecen primero:"),
      T("admin.intake.explainer.gain_4", null, "en rojo, arriba en «Mis pacientes», y con aviso en la campana."))
    + "</ul></section>"
    + '<section class="ix-how"><h3>' + esc(T("admin.intake.explainer.how_title", null, "Cómo funciona")) + "</h3><ul>"
    + item(T("admin.intake.explainer.how_1_strong", null, "El paciente recibe «Contale al médico cómo te sentís»"),
      T("admin.intake.explainer.how_1", null, "al pedir el turno, en los emails del turno y el día anterior. En la puerta del consultorio, con el QR del cartel."))
    + item(T("admin.intake.explainer.how_2_strong", null, "Contesta hasta unas 8 preguntas, de a una,"),
      T("admin.intake.explainer.how_2", null, "escritas o dictadas, en su idioma. En la puerta son 4 o 5."))
    + item(T("admin.intake.explainer.how_3_strong", null, "No diagnostica ni aconseja."),
      T("admin.intake.explainer.how_3", null, "Si el paciente pide un consejo, le contesta que eso lo responde el médico. Si describe una urgencia, corta y le indica pedir atención ya."))
    + item(T("admin.intake.explainer.how_4_strong", null, "Las señales de alarma las marca un control fijo,"),
      T("admin.intake.explainer.how_4", null, "no sólo la IA: la IA puede subir la prioridad, nunca bajarla."))
    + "<li><strong>" + esc(T("admin.intake.explainer.privacy_strong", null, "Datos:")) + "</strong> "
    + esc(T("admin.intake.explainer.privacy", null, "antes de mandar el texto a la IA, el nombre, el DNI, el teléfono y el domicilio se reemplazan por etiquetas. Una foto que adjunte el paciente se envía tal cual."))
    + ' <a href="/privacy.html" target="_blank" rel="noopener">' + esc(T("admin.intake.explainer.privacy_link", null, "Política de privacidad")) + "</a></li>"
    + "</ul></section>"
    + '<figure class="ix-ex"><h3>' + esc(T("admin.intake.explainer.example_title", null, "Así te llega un paciente")) + "</h3>"
    + '<div class="ix-card" inert><div class="ix-card-head"><strong>'
    + esc(T("admin.intake.explainer.example_patient", null, "Paciente de ejemplo, 45 años")) + "</strong>"
    + '<span class="queue-triage queue-triage--amarillo">' + esc(T("admin.queue.triage.amarillo", null, "Amarillo")) + "</span></div>"
    + "<h4>" + esc(T("admin.intake.explainer.example_what", null, "Qué le pasa")) + "</h4>"
    + "<p>" + esc(T("admin.intake.explainer.example_summary", null, "Tos con flema y fiebre de 38,5 °C desde hace 4 días. Le cuesta respirar al subir escaleras. No fuma. Toma enalapril.")) + "</p>"
    + "<h4>" + esc(T("admin.intake.explainer.example_dx", null, "Diagnósticos posibles")) + "</h4>"
    + pick(T("admin.intake.explainer.example_dx_1", null, "Bronquitis aguda"), true)
    + pick(T("admin.intake.explainer.example_dx_2", null, "Neumonía de la comunidad"), false)
    + pick(T("admin.intake.explainer.example_dx_3", null, "Gripe"), false)
    + "<h4>" + esc(T("admin.intake.explainer.example_studies", null, "Estudios")) + "</h4>"
    + pick(T("admin.intake.explainer.example_study_1", null, "Radiografía de tórax"), true)
    + pick(T("admin.intake.explainer.example_study_2", null, "Hemograma"), false)
    + "<h4>" + esc(T("admin.intake.explainer.example_meds", null, "Medicación")) + "</h4>"
    + pick(T("admin.intake.explainer.example_med_1", null, "Paracetamol 500 mg cada 8 horas"), true)
    + '<button type="button" class="btn btn-quiet" tabindex="-1">' + esc(T("admin.intake.explainer.example_print", null, "Imprimir")) + "</button>"
    + "</div></figure>"
    + "</div>";
}
