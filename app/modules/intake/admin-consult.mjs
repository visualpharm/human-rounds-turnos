/* Native lifecycle owner for the direct admin consultation workspace.
 *
 * Importing this file is inert.  The direct screen supplies its host to
 * mount(); that mount owns all imports, reads, submissions and listeners.
 * A route token is deliberately narrower than a page lifetime: switching an
 * interview retires only work for the former interview and leaves its draft
 * in this mount's per-interview store.
 */
"use strict";

const mounts = new WeakMap();

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function deaccent(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function matches(query, text) {
  var needle = deaccent(query).toLowerCase().trim();
  if (!needle) return true;
  var haystack = deaccent(text).toLowerCase();
  if (haystack.indexOf(needle.replace(/[\s/]+/g, " ")) >= 0) return true;
  var initials = needle.split(/[\s/]+/).filter(Boolean).map(function (word) { return word[0]; }).join("");
  var textInitials = haystack.split(/[^a-z0-9]+/).filter(Boolean).map(function (word) { return word[0]; }).join("");
  return !!initials && textInitials.indexOf(initials) >= 0;
}

// CIE-10 code of a hand-written diagnosis, normalized the way the order prints
// it ("j209" → "J20.9"), or null. Shape only — mirror of
// consult_admin._codigo_cie10; the pack has no full CIE-10 list.
function cie10(value) {
  var m = /^([A-Z])(\d{2})\.?([0-9A-Z]{1,4})?$/.exec(String(value == null ? "" : value).trim().toUpperCase());
  return m ? m[1] + m[2] + (m[3] ? "." + m[3] : "") : null;
}

function plainObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function validConsultation(value) {
  return plainObject(value) && value.id !== undefined && value.id !== null;
}

function validConfirmation(value) {
  return plainObject(value) && value.ok === true && typeof value.receta_url === "string" && value.receta_url &&
    typeof value.orden_url === "string" && value.orden_url;
}

function stateFor(data, routeId) {
  // AI output is advisory only: nothing is preselected. The clinician opts
  // in item by item, so every line of a generated draft traces back to an
  // explicit choice (catalog checkbox or manual entry), never to the model.
  var suggestedDiagnoses = new Set();
  (Array.isArray(data.differential) ? data.differential : []).forEach(function (item) {
    if (item && item.id) {
      suggestedDiagnoses.add(item.id);
    }
  });
  var suggestedExams = new Set();
  (Array.isArray(data.suggested_exams) ? data.suggested_exams : []).forEach(function (id) {
    if (id) suggestedExams.add(String(id));
  });
  return {
    id: String(routeId == null ? data.id : routeId), consultation: data,
    catalogs: plainObject(data.catalogos) ? data.catalogos : {},
    suggestedDiagnoses: suggestedDiagnoses, suggestedExams: suggestedExams,
    selectedDiagnoses: new Set(), selectedExams: new Set(),
    customDiagnoses: [], customExams: [], meds: [], notes: "",
    sending: false, confirmed: null, error: "", diagnosisQuery: "", examQuery: "",
    reviewing: false, replacing: false
  };
}

// A confirmed consultation reopens read-only (problems.md №1): the form only
// comes back through an explicit «Reemplazar», and it starts from what was
// issued, so a correction is one change instead of a blank re-entry.
function issuedOf(data) {
  return plainObject(data.confirmada) && data.confirmada.id != null ? data.confirmada : null;
}

function startReplacing(state) {
  var issued = issuedOf(state.consultation);
  var picked = issued && plainObject(issued.seleccion) ? issued.seleccion : {};
  var list = function (value) { return Array.isArray(value) ? value.filter(plainObject) : []; };
  state.replacing = true; state.reviewing = false; state.error = "";
  state.selectedDiagnoses = new Set(list(picked.diagnosticos).filter(function (item) { return !item.custom && item.id; }).map(function (item) { return item.id; }));
  state.customDiagnoses = list(picked.diagnosticos).filter(function (item) { return item.custom || !item.id; }).map(function (item) { return { label: item.label, codigo: item.codigo || undefined, sistema: item.sistema || undefined, custom: true }; });
  state.selectedExams = new Set(list(picked.estudios).filter(function (item) { return !item.custom && item.id; }).map(function (item) { return item.id; }));
  state.customExams = list(picked.estudios).filter(function (item) { return item.custom || !item.id; }).map(function (item) { return { label: item.label, custom: true }; });
  state.meds = list(picked.medicacion).map(function (item) { return { nombre: item.nombre || "", dosis: item.dosis || "", frecuencia: item.frecuencia || "", duracion: item.duracion || "" }; });
  state.notes = typeof picked.notas === "string" ? picked.notas : "";
}

function importStatic(name) {
  var host = (typeof window !== "undefined" && window) || globalThis.window;
  var map = host && host.SANA_STATIC;
  var url = map && map[name];
  return url ? import(url) : Promise.reject(new Error("Missing consultation dependency: " + name));
}

export function mount(host, options) {
  if (!host) throw new Error("Consultation host is required");
  if (mounts.has(host)) mounts.get(host)();
  options = options || {};

  var disposed = false;
  var routeGeneration = 0;
  var readController = null;
  var listeners = [];
  var drafts = new Map();
  var currentId = "";
  var displayedState = null;
  var fontSettlement = null;
  var Dates, I18n, PatientSummary, Session;

  function alive() { return !disposed && host.isConnected !== false; }
  function ownsRoute(generation, id) { return alive() && generation === routeGeneration && String(id) === currentId; }
  function current() { return alive(); }
  function doc() { return (typeof window !== "undefined" && window.document) || globalThis.document; }
  function win() { return (typeof window !== "undefined" && window) || globalThis; }
  function locationOf() { return (typeof window !== "undefined" && window.location) || globalThis.location || {}; }
  function T(key, vars, fallback) { return I18n ? I18n.t(key, vars, fallback) : (fallback || key); }
  // Number agreement through i18n.mjs tn() — «1 emisión», never «1 emisión(es)».
  function Tn(key, n, vars, one, other) {
    if (I18n && I18n.tn) return I18n.tn(key, n, vars, one, other);
    var single = Number(n) === 1 && typeof one === "string", all = Object.assign({ n: n }, vars), text = T(single ? key + "_one" : key, null, single ? one : other);
    return Object.keys(all).reduce(function (s, k) { return s.split("{" + k + "}").join(String(all[k])); }, text);
  }
  // The stored `sistema` stays "CIE-10"; only what is shown follows the page language.
  function codeSystem(item) {
    var system = item.sistema || "CIE-10";
    return system === "CIE-10" ? T("admin.reports.consulta.sistema_cie10", null, "CIE-10") : system;
  }
  // A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
  function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }

  function listen(target, type, callback) {
    if (!target || typeof target.addEventListener !== "function") return;
    target.addEventListener(type, callback);
    listeners.push({ target: target, type: type, callback: callback });
  }

  function unlisten() {
    listeners.forEach(function (item) {
      if (item.target && typeof item.target.removeEventListener === "function") item.target.removeEventListener(item.type, item.callback);
    });
    listeners = [];
  }

  function dispose() {
    if (disposed) return;
    clearStatus();
    disposed = true;
    routeGeneration += 1;
    if (readController) readController.abort();
    readController = null;
    displayedState = null;
    drafts.clear();
    unlisten();
    if (mounts.get(host) === dispose) mounts.delete(host);
  }

  function injectCss() {
    var document = doc();
    if (!document || (document.getElementById && document.getElementById("cst-css"))) return;
    var tag = document.createElement("style");
    tag.id = "cst-css";
    tag.textContent = `
      #sec-consulta{padding-bottom:60px;box-sizing:border-box;font-size:16px;color:var(--text)}
      #sec-consulta :is(p,summary,label,span,th,td,input,textarea,button){font-size:16px}
      #sec-consulta .cst-group-title{font-size:16px}
      #sec-consulta :is(input,textarea){min-width:0}
      #sec-consulta :is(button,.cst-pick-row){min-height:44px;box-sizing:border-box}
      #sec-consulta summary{min-height:44px;box-sizing:border-box;padding-block:10px;cursor:pointer}
      #sec-consulta button{max-width:100%;white-space:normal}
      #sec-consulta .cst-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:4px 0 16px}
      #sec-consulta .cst-head h2{margin:0;font-size:20px}
      #sec-consulta :is(.cst-head-meta,.cst-vacio,.cst-pick-code,.cst-group-title){color:var(--text)}
      #sec-consulta .cst-h3{font-size:18px;margin:24px 0 12px}
      #sec-consulta :is(.cst-brief,.cst-pick,.cst-done){display:block;padding:0;border:0;box-shadow:none;background:none;font-size:16px}
      #sec-consulta .cst-oneliner{line-height:1.5}
      #sec-consulta .cst-flag{padding:8px 12px;margin:0 0 8px;background:var(--danger-soft);border-radius:8px}
      #sec-consulta .cst-flag-lbl{font-weight:650}
      #sec-consulta .cst-brief-list{margin:8px 0 0;padding-left:18px;line-height:1.6}
      #sec-consulta .cst-pick-row{display:flex;align-items:center;gap:12px;padding:8px 0;line-height:1.5}
      #sec-consulta .cst-pick-row>input{margin:0;flex-shrink:0}
      #sec-consulta .cst-pick-row--hijo{padding-left:24px}
      #sec-consulta .cst-pick-main{flex:1;min-width:0;overflow-wrap:anywhere}
      #sec-consulta .cst-search,#sec-consulta .cst-in{width:100%;box-sizing:border-box;padding:10px 12px;font:inherit;font-size:16px;border:1px solid var(--border-strong);border-radius:8px;background:var(--panel);color:var(--text)}
      #sec-consulta .cst-pick-list{max-height:280px;overflow-y:auto;margin-top:12px}
      #sec-consulta .cst-add-row{display:flex;align-items:start;gap:8px;margin-top:12px}
      #sec-consulta .cst-in[aria-invalid="true"],#sec-consulta .cst-in[aria-invalid="true"]:focus{border-color:var(--danger);outline-color:var(--danger)}
      #sec-consulta .cst-add-row .cst-in{flex:1}
      #sec-consulta .cst-add-row-code{flex-wrap:wrap}
      #sec-consulta .cst-add-row-code .cst-in:first-child{flex:1 1 14em}
      #sec-consulta .cst-add-row .cst-in-code{flex:0 1 8em;min-width:7em}
      #sec-consulta .cst-add-row button{flex-shrink:0}
      #sec-consulta .cst-chip{display:inline-flex;gap:6px;align-items:center;margin:8px 6px 0 0;padding:0 8px;border:1px solid var(--border);border-radius:16px}
      #sec-consulta .cst-chip .cst-chip-x{margin:-8px -8px -8px -6px}
      #sec-consulta .cst-chip .cst-chip-x:hover{background:none}
      #sec-consulta .cst-chip .cst-chip-x::before{width:16px;height:16px}
      #sec-consulta .cst-med-table{width:100%;border-collapse:collapse}
      #sec-consulta .cst-med-table td,#sec-consulta .cst-med-table th{padding:6px;vertical-align:top}
      #sec-consulta .cst-med-table td:first-child{width:32%}
      #sec-consulta .cst-aviso{padding:8px 12px;margin:8px 0 0;background:var(--panel);border-radius:8px;color:var(--text)}
      #sec-consulta .cst-review{padding:16px;margin:0 0 10px}
      #sec-consulta .cst-review p{margin:6px 0}
      #sec-consulta .cst-actions{margin:24px 0 10px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
      #sec-consulta .cst-error{color:var(--danger);flex-basis:100%}
      @media(max-width:767px){#sec-consulta{padding-left:16px;padding-right:16px}}
      @media(max-width:1040px){
        #sec-consulta .cst-med-table thead{display:none}
        #sec-consulta .cst-med-table,#sec-consulta .cst-med-table tbody,#sec-consulta .cst-med-table tr,#sec-consulta .cst-med-table td{display:block;width:100%;box-sizing:border-box}
        #sec-consulta .cst-med-table tr{padding:8px 0}
        #sec-consulta .cst-med-table td{padding:4px 0}
        #sec-consulta .cst-med-table td:first-child{width:100%}
        #sec-consulta .cst-med-table td[data-label]::before{content:attr(data-label);display:block;color:var(--text);font-size:16px}
      }`;
    if (document.head) document.head.appendChild(tag);
  }

  function routeId() {
    if (typeof options.interviewId === "function") return String(options.interviewId() || "");
    if (options.interviewId !== undefined) return String(options.interviewId || "");
    var location = locationOf();
    var id = new URLSearchParams(location.search || "").get("interview") || "";
    var canonical = "/admin/consult" + (id ? "?interview=" + encodeURIComponent(id) : "");
    var history = win().history;
    if (history && typeof history.replaceState === "function" && location.pathname + location.search !== canonical) {
      history.replaceState({}, "", canonical);
    }
    return id;
  }

  function queueLink() {
    return '<p><a href="/admin/cola">' + esc(T("admin.queue.titulo", null, "Pacientes de hoy")) + "</a></p>";
  }

  function api(url, requestOptions, owns, ownsSession = owns) {
    var request = Object.assign({ credentials: "same-origin" }, requestOptions || {});
    return fetch(url, request).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (json) {
        if (!owns()) return { stale: true };
        var boundary = (response.status === 401 || response.status === 403) && ownsSession()
          ? Session.confirmar(response, json, ownsSession) : Promise.resolve(false);
        return boundary.then(function (transitioned) {
          if (!owns()) return { stale: true };
          if (transitioned) { if (alive()) host.innerHTML = ""; dispose(); return { transitioned: true }; }
          if (!response.ok) {
            var failure = new Error(plainObject(json) && json.error ? String(json.error) : T("admin.reports.consulta.error_generico", null, "No se pudo completar la acción."));
            failure.status = response.status; failure.body = json;
            throw failure;
          }
          return { json: json };
        });
      });
    });
  }

  function patientName(state) {
    var data = state.consultation;
    if (data.nombre) return data.nombre;
    var entered = [data.entered_name, data.entered_surname].filter(Boolean).join(" ");
    return entered || (data.dni ? "DNI " + data.dni : T("admin.reports.consulta.paciente_sin_nombre", null, "Paciente sin nombre"));
  }

  function triageHtml(value) {
    var key = String(value || "").toUpperCase();
    var labels = { ROJO: "Rojo", NARANJA: "Naranja", AMARILLO: "Amarillo", VERDE: "Verde" };
    return labels[key] ? '<span class="queue-triage queue-triage--' + key.toLowerCase() + '">' + esc(T("admin.queue.triage." + key.toLowerCase(), null, labels[key])) + "</span>" : "";
  }

  function diagnosisRows(state) {
    var catalog = Array.isArray(state.catalogs.diagnosticos) ? state.catalogs.diagnosticos : [];
    if (!catalog.length) {
      // The pack's catalog only covers some specialties (cardiology today):
      // say so instead of an endless «Sin coincidencias».
      return '<p class="cst-vacio">' + esc(T("admin.reports.consulta.sin_catalogo_dx", null, "No hay diagnósticos cargados para esta especialidad. Escribí el diagnóstico abajo y, si lo sabés, su código CIE-10.")) + "</p>";
    }
    var query = state.diagnosisQuery;
    var rows = [];
    function row(item, child) {
      var suggested = state.suggestedDiagnoses.has(item.id);
      rows.push('<label class="cst-pick-row' + (child ? " cst-pick-row--hijo" : "") + '"><input type="checkbox" data-dx="' + esc(item.id) + '"' + (state.selectedDiagnoses.has(item.id) ? " checked" : "") + '><span class="cst-pick-main">' + esc(item.label) + (suggested ? ' <span class="cst-pick-code">' + esc(T("admin.reports.consulta.sugerido", null, "Sugerido")) + "</span>" : "") + '</span>' + (item.codigo ? '<span class="cst-pick-code">' + esc(codeSystem(item) + " " + item.codigo) + '</span>' : '') + '</label>');
    }
    catalog.filter(function (item) { return item.nivel !== "specific"; }).forEach(function (parent) {
      var parentMatches = matches(query, (parent.label || "") + " " + (parent.codigo || ""));
      var children = catalog.filter(function (item) {
        return item.parent === parent.id && (parentMatches || matches(query, (item.label || "") + " " + (item.codigo || "")));
      });
      if (!parentMatches && !children.length) return;
      row(parent, false);
      children.forEach(function (child) { row(child, true); });
    });
    return rows.length ? rows.join("") : '<p class="cst-vacio">' + esc(T("admin.reports.consulta.sin_coincidencias", null, "Sin coincidencias.")) + "</p>";
  }

  function examRows(state) {
    var catalog = Array.isArray(state.catalogs.estudios) ? state.catalogs.estudios : [];
    var groups = new Map();
    catalog.forEach(function (item) {
      var category = item.categoria || T("admin.reports.consulta.categoria_otros", null, "Otros");
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(item);
    });
    var rows = [];
    groups.forEach(function (items, category) {
      var visible = items.filter(function (item) { return matches(state.examQuery, (item.label || "") + " " + category); });
      if (!visible.length) return;
      rows.push('<div class="cst-group-title">' + esc(category) + '</div>');
      visible.forEach(function (item) {
        var sugerido = state.suggestedExams.has(String(item.id))
          ? ' <span class="cst-pick-code">' + esc(T("admin.reports.consulta.sugerido", null, "Sugerido")) + "</span>" : "";
        rows.push('<label class="cst-pick-row"><input type="checkbox" data-exam="' + esc(item.id) + '"' + (state.selectedExams.has(item.id) ? " checked" : "") + '><span class="cst-pick-main">' + esc(item.label) + sugerido + "</span></label>");
      });
    });
    return rows.length ? rows.join("") : '<p class="cst-vacio">' + esc(T("admin.reports.consulta.sin_coincidencias", null, "Sin coincidencias.")) + "</p>";
  }

  function chips(items, type) {
    return items.map(function (item, index) {
      return '<span class="cst-chip">' + esc(item.label) + (item.codigo ? ' <span class="cst-pick-code">' + esc(codeSystem(item) + " " + item.codigo) + "</span>" : "") + '<button type="button" class="cst-chip-x hr-x" data-remove-' + type + '="' + index + '" aria-label="' + esc(T("admin.reports.consulta.quitar", { item: item.label }, "Quitar " + item.label)) + '"></button></span>';
    }).join("");
  }

  function medicationRows(state) {
    var labels = medicationLabels().map(esc);
    if (!state.meds.length) return '<tr><td colspan="5" class="cst-vacio">' + esc(T("admin.reports.consulta.sin_medicacion", null, "Sin medicación agregada.")) + "</td></tr>";
    return state.meds.map(function (medication, index) {
      var catalog = (Array.isArray(state.catalogs.medicamentos) ? state.catalogs.medicamentos : []).find(function (item) {
        return deaccent(item.nombre).toLowerCase() === deaccent(medication.nombre).toLowerCase();
      });
      var doses = '<datalist id="cst-dose-' + index + '">' + doseOptions(catalog) + "</datalist>";
      return '<tr><td data-label="' + labels[0] + '"><input class="cst-in" aria-label="' + labels[0] + '" list="cst-med-list" data-med-field="nombre" data-med-index="' + index + '" value="' + esc(medication.nombre) + '"></td><td data-label="' + labels[1] + '"><input class="cst-in" aria-label="' + labels[1] + '" list="cst-dose-' + index + '" data-med-field="dosis" data-med-index="' + index + '" value="' + esc(medication.dosis) + '">' + doses + '</td><td data-label="' + labels[2] + '"><input class="cst-in" aria-label="' + labels[2] + '" list="cst-freq-list" data-med-field="frecuencia" data-med-index="' + index + '" value="' + esc(medication.frecuencia) + '"></td><td data-label="' + labels[3] + '"><input class="cst-in" aria-label="' + labels[3] + '" data-med-field="duracion" data-med-index="' + index + '" value="' + esc(medication.duracion) + '"></td><td><button type="button" class="cst-chip-x hr-x" data-remove-med="' + index + '" aria-label="' + esc(T("admin.reports.consulta.quitar_fila_medicacion", null, "Quitar fila de medicación")) + '"></button></td></tr>';
    }).join("");
  }

  function medicationLabels() {
    return [["medicamento", "Medicamento"], ["dosis", "Dosis"], ["frecuencia", "Frecuencia"], ["duracion", "Duración"]]
      .map(function (field) { return T("admin.reports.consulta.col_" + field[0], null, field[1]); });
  }

  function doseOptions(catalog) {
    return catalog && Array.isArray(catalog.dosis_opciones)
      ? catalog.dosis_opciones.map(function (dose) { return '<option value="' + esc(dose) + '">'; }).join("") : "";
  }

  function inputAttributes(id) {
    var fields = {
      "cst-dx-search": ["h3_diagnosticos", "Diagnósticos", "buscar_dx_placeholder", "Buscar diagnóstico…"],
      "cst-exam-search": ["h3_estudios", "Estudios a solicitar", "buscar_estudio_placeholder", "Buscar estudio…"],
      "cst-dx-custom-input": ["otro_dx_placeholder", "Otro diagnóstico…"],
      "cst-dx-custom-code": ["codigo_cie10", "Código CIE-10 (opcional)", "codigo_cie10_placeholder", "CIE-10, ej. J20.9"],
      "cst-exam-custom-input": ["otro_estudio_placeholder", "Otro estudio…"],
      "cst-notes": ["h3_indicaciones", "Indicaciones", "notas_placeholder", "Indicaciones para la receta (opcional)…"]
    };
    var field = fields[id];
    var label = T("admin.reports.consulta." + field[0], null, field[1]);
    var hint = field[2] ? T("admin.reports.consulta." + field[2], null, field[3]) : label;
    return ' name="' + id + '" aria-label="' + esc(label) + '" placeholder="' + esc(hint) + '" autocomplete="off"';
  }

  function render(state) {
    if (!alive() || !state || currentId !== state.id) return;
    displayedState = state;
    if (state.confirmed) {
      host.innerHTML = '<div class="card cst-done"><h2>' + esc(T("admin.reports.consulta.consulta_confirmada", null, "Borradores generados")) + '</h2><p><a href="' + esc(state.confirmed.receta_url) + '" target="_blank" rel="noopener">' + esc(T("admin.reports.consulta.abrir_receta", null, "Abrir borrador de receta")) + '</a></p><p><a href="' + esc(state.confirmed.orden_url) + '" target="_blank" rel="noopener">' + esc(T("admin.reports.consulta.abrir_orden", null, "Abrir borrador de orden")) + "</a></p></div>" + queueLink();
      return;
    }
    var data = state.consultation;
    var issued = issuedOf(data);
    if (issued && !state.replacing) { host.innerHTML = issuedHtml(state, issued); return; }
    var brief =Array.isArray(data.brief) && data.brief.length ? '<ul class="cst-brief-list">' + data.brief.map(function (item) { return "<li>" + esc(item.point || item) + "</li>"; }).join("") + "</ul>" : "";
    var flags = (Array.isArray(data.red_flags) ? data.red_flags : []).map(function (flag) { return '<div class="cst-flag"><div class="cst-flag-lbl">' + esc(flag.label) + "</div>" + (flag.because ? "<div>" + esc(flag.because) + "</div>" : "") + "</div>"; }).join("");
    var diagnoses = Array.isArray(state.catalogs.diagnosticos) ? state.catalogs.diagnosticos : [];
    var differential = (Array.isArray(data.differential) ? data.differential : []).slice(0, 6).map(function (item) {
      var label = (diagnoses.find(function (candidate) { return candidate.id === item.id; }) || {}).label || item.id;
      return '<span class="cst-chip">' + esc(label) + "</span>";
    }).join("");
    // One AI warning per card (ui-expert 15.11): when the AI suggests
    // diagnoses or exams, «not a diagnosis, the decision is yours» (the
    // 2026-10-21 clinician-choice contract) joins the register notice at the
    // top of the card instead of a second warning under the differential.
    var aiSuggested = differential || (Array.isArray(data.suggested_exams) ? data.suggested_exams : []).length;
    var advisory = aiSuggested ? " " + esc(T("admin.reports.consulta.aviso_ia", null, "Las sugerencias de la IA no son un diagnóstico ni una recomendación de prescripción; la decisión clínica es tuya.")) : "";
    var profile = plainObject(data.perfil) ? Object.entries(data.perfil).filter(function (entry) { return ["medicacion", "condiciones", "alergias", "antecedentes"].includes(entry[0]) && Array.isArray(entry[1]) && entry[1].length; }).map(function (entry) {
      var labels = { medicacion: "Medicación", condiciones: "Condiciones", alergias: "Alergias", antecedentes: "Antecedentes" };
      return '<p class="cst-oneliner"><b>' + esc(T("admin.reports.consulta.perfil_" + entry[0], null, labels[entry[0]])) + ":</b> " + esc(entry[1].join("; ")) + "</p>";
    }).join("") : "";
    var proposal = plainObject(data.perfil_propuesta) ? data.perfil_propuesta : null;
    var proposalHtml = proposal && Object.values(proposal).some(function (items) { return Array.isArray(items) && items.length; })
      ? '<details open><summary>' + esc(T("ai.profile.proposal", null, "Propuesta de la IA, revisala")) + '</summary><ul>' +
        Object.keys(proposal).filter(function (key) { return ["medicacion","condiciones","alergias","antecedentes"].includes(key); }).map(function (key) {
          return '<li>' + esc(T("admin.reports.consulta.perfil_" + key, null, key)) + ': ' + esc((proposal[key] || []).join('; ')) + '</li>';
        }).join('') + '</ul><button type="button" class="btn" id="cst-profile-apply">' + esc(T("ai.profile.apply", null, "Aplicar propuesta al perfil revisado")) + '</button></details>' : '';
    var profileEditor = '<details><summary>' + esc(T("ai.profile.review", null, "Perfil revisado por el profesional")) + '</summary>' +
      ["medicacion", "condiciones", "alergias", "antecedentes"].map(function (key) {
        return '<label style="display:grid;gap:6px;margin:12px 0;">' + esc(T("admin.reports.consulta.perfil_" + key, null, {medicacion:"Medicación",condiciones:"Condiciones",alergias:"Alergias",antecedentes:"Antecedentes"}[key])) +
          '<textarea class="cst-in" data-profile-field="' + key + '" rows="2">' + esc(((state.reviewedProfile || data.perfil || {})[key] || []).join("\n")) + '</textarea></label>';
      }).join("") + '<label class="cst-pick-row"><input type="checkbox" id="cst-profile-reviewed"' + (state.profileReviewed ? ' checked' : '') + '>' + esc(T("ai.profile.save", null, "Guardar este perfil revisado al confirmar")) + '</label></details>';
    var updated = data.reopened_at ? '<p class="cst-head-meta">' + esc(T("admin.reports.consulta.actualizada", null, "Actualizada")) + (Dates && Dates.relativo(data.analyzed_at) ? " " + esc(Dates.relativo(data.analyzed_at)) : "") + "</p>" : "";
    var transcript = (Array.isArray(data.mensajes) ? data.mensajes : []).map(function (message) { return '<p>' + esc(message.role === "assistant" ? T("admin.reports.consulta.rol_asistente", null, "Asistente") : T("admin.reports.consulta.rol_paciente", null, "Paciente")) + ": " + esc(message.content) + "</p>"; }).join("") || '<p class="cst-vacio">' + esc(T("admin.reports.consulta.sin_mensajes", null, "Sin mensajes.")) + "</p>";
    var messageCount = Array.isArray(data.mensajes) ? data.mensajes.length : 0;
    var countLabel = messageCount + " " + (messageCount === 1 ? T("admin.reports.consulta.mensaje_singular", null, "mensaje") : T("admin.reports.consulta.mensaje_plural", null, "mensajes"));
    // One AI notice per screen (Decreto 742/2026): it rides with the AI text,
    // at the top of the AI brief, and the patient summary mounted below does
    // not repeat it.
    var aiNotice = '<p class="adm-ai-notice" data-ai-notice-inline="clinical">' + esc(T("ai.notice.clinical", null, "Contenido elaborado con inteligencia artificial. Revisá las fuentes antes de confirmar o usarlo en la atención.")) + advisory + '</p>';
    host.innerHTML = '<div class="cst-head"><h2>' + esc(patientName(state)) + "</h2>" + (data.status === "active" ? '<span class="queue-triage-curso">' + esc(T("admin.reports.consulta.en_curso", null, "En curso")) + "</span>" : triageHtml(data.triage)) + (data.dni && patientName(state) !== "DNI " + data.dni ? '<span class="cst-head-meta">DNI ' + esc(data.dni) + "</span>" : "") + "</div>" +
      '<div class="card cst-brief">' + aiNotice + updated + '<p class="cst-oneliner">' + esc(data.summary || (data.analyzed_at ? "—" : T("admin.reports.consulta.sin_analisis", null, "Todavía sin análisis de la IA. Podés elegir diagnósticos, estudios y medicación igual, a partir del transcript de abajo."))) + "</p>" + flags + brief + (differential ? '<p class="cst-head-meta">' + esc(T("admin.reports.consulta.diferencial_ia", null, "Diferencial IA:")) + "</p>" + differential : "") + "</div>" + (profile ? '<div class="card cst-brief"><p class="cst-head-meta">' + esc(T("admin.reports.consulta.perfil_titulo", null, "Lo que este paciente ya nos contó")) + "</p>" + profile + "</div>" : "") + proposalHtml + profileEditor +
      '<div id="cst-resumen"></div><details class="cst-transcript"><summary>' + esc(T("admin.reports.consulta.transcript_resumen", { n: countLabel }, "Transcript de la entrevista (" + countLabel + ")")) + "</summary>" + transcript + "</details>" +
      '<h3 class="cst-h3">' + esc(T("admin.reports.consulta.h3_diagnosticos", null, "Diagnósticos")) + '</h3><div class="card cst-pick"><input type="search" class="cst-search" id="cst-dx-search"' + inputAttributes("cst-dx-search") + ' value="' + esc(state.diagnosisQuery) + '"><div class="cst-pick-list">' + diagnosisRows(state) + '</div><div class="cst-add-row cst-add-row-code"><input class="cst-in" id="cst-dx-custom-input"' + inputAttributes("cst-dx-custom-input") + '><input class="cst-in cst-in-code" id="cst-dx-custom-code"' + inputAttributes("cst-dx-custom-code") + ' maxlength="8"' + (state.codeError ? ' aria-invalid="true"' : "") + '><button type="button" class="btn btn-quiet" data-add-dx>' + esc(T("admin.reports.consulta.agregar", null, "Agregar")) + "</button></div>" + '<div id="cst-dx-custom">' + customHtml(state, "dx") + "</div></div>" +
      '<h3 class="cst-h3">' + esc(T("admin.reports.consulta.h3_estudios", null, "Estudios a solicitar")) + '</h3><div class="card cst-pick"><input type="search" class="cst-search" id="cst-exam-search"' + inputAttributes("cst-exam-search") + ' value="' + esc(state.examQuery) + '"><div class="cst-pick-list">' + examRows(state) + '</div><div class="cst-add-row"><input class="cst-in" id="cst-exam-custom-input"' + inputAttributes("cst-exam-custom-input") + '><button type="button" class="btn btn-quiet" data-add-exam>' + esc(T("admin.reports.consulta.agregar", null, "Agregar")) + "</button></div>" + '<div id="cst-exam-custom">' + chips(state.customExams, "exam") + "</div></div>" +
      '<h3 class="cst-h3">' + esc(T("admin.reports.consulta.h3_medicacion", null, "Medicación")) + '</h3><div class="card cst-pick"><datalist id="cst-med-list">' + (Array.isArray(state.catalogs.medicamentos) ? state.catalogs.medicamentos : []).map(function (item) { return '<option value="' + esc(item.nombre) + '">'; }).join("") + '</datalist><datalist id="cst-freq-list">' + (Array.isArray(state.catalogs.frecuencias) ? state.catalogs.frecuencias : []).map(function (item) { return '<option value="' + esc(item) + '">'; }).join("") + '</datalist><table class="cst-med-table"><thead><tr>' + medicationLabels().map(function (label) { return "<th>" + esc(label) + "</th>"; }).join("") + '<th></th></tr></thead><tbody id="cst-med-body">' + medicationRows(state) + '</tbody></table><button type="button" class="btn btn-quiet" data-add-med>' + esc(T("admin.reports.consulta.agregar_medicamento", null, "Agregar medicamento")) + "</button></div>" +
      '<h3 class="cst-h3">' + esc(T("admin.reports.consulta.h3_indicaciones", null, "Indicaciones")) + '</h3><textarea class="cst-in" id="cst-notes"' + inputAttributes("cst-notes") + ' rows="3">' + esc(state.notes) + "</textarea>" +
      (state.reviewing ? reviewCardHtml(state) : "") +
      (issued ? '<p class="cst-aviso">' + esc(T("admin.reports.consulta.reemplazando", { when: issuedWhen(issued) }, "Estás reemplazando la receta y la orden emitidas {when}: al confirmar, las anteriores quedan ANULADAS.")) + '</p><button type="button" class="btn btn-quiet" id="cst-reemplazo-cancelar"' + (state.sending ? " disabled" : "") + ">" + esc(T("admin.reports.consulta.reemplazo_cancelar", null, "Volver a lo emitido sin cambiar nada")) + "</button>" : "") +
      '<div class="cst-actions" id="cst-actions">' + actionsHtml(state) + "</div>";
    refreshActions(state);
    if (data.dni && PatientSummary && typeof PatientSummary.mount === "function") {
      var summaryHost = host.querySelector && host.querySelector("#cst-resumen");
      if (summaryHost) PatientSummary.mount(summaryHost, { dni: data.dni, medicoId: data.doctor_id || null, aiNotice: false });
    }
  }

  function issuedWhen(issued) {
    return (Dates && Dates.relativo(issued.confirmada_at)) || "";
  }

  function issuedHtml(state, issued) {
    var data = state.consultation;
    var lines = function (items) { return Array.isArray(items) ? items.filter(Boolean).join("; ") : ""; };
    var replaced = Number(issued.reemplazos) > 0
      ? '<p class="cst-head-meta">' + esc(Tn("admin.reports.consulta.reemplazos", issued.reemplazos, null, "Reemplaza a {n} emisión anterior, que quedó anulada.", "Reemplaza a {n} emisiones anteriores, que quedaron anuladas.")) + "</p>" : "";
    return '<div class="cst-head"><h2>' + esc(patientName(state)) + "</h2>" + triageHtml(data.triage) +
      (data.dni && patientName(state) !== "DNI " + data.dni ? '<span class="cst-head-meta">DNI ' + esc(data.dni) + "</span>" : "") +
      '<span class="queue-triage-atendido">' + esc(T("admin.queue.triage.atendido", null, "Atendido")) + "</span></div>" +
      '<div class="card cst-review" id="cst-emitida"><h3 class="cst-h3">' + esc(T("admin.reports.consulta.emitida_titulo", { when: issuedWhen(issued) }, "Consulta confirmada {when}")) + "</h3>" +
      reviewLine("admin.reports.consulta.h3_diagnosticos", "Diagnósticos", lines(issued.diagnosticos)) +
      reviewLine("admin.reports.consulta.h3_estudios", "Estudios a solicitar", lines(issued.estudios)) +
      reviewLine("admin.reports.consulta.h3_medicacion", "Medicación", lines(issued.medicacion)) +
      reviewLine("admin.reports.consulta.h3_indicaciones", "Indicaciones", issued.notas) + replaced +
      '<p><a href="' + esc(issued.receta_url) + '" target="_blank" rel="noopener">' + esc(T("admin.reports.consulta.abrir_receta", null, "Abrir borrador de receta")) + "</a></p>" +
      '<p><a href="' + esc(issued.orden_url) + '" target="_blank" rel="noopener">' + esc(T("admin.reports.consulta.abrir_orden", null, "Abrir borrador de orden")) + "</a></p></div>" +
      '<div class="cst-actions" id="cst-actions">' + (state.error ? '<p class="cst-error" role="status">' + esc(state.error) + "</p>" : "") +
      '<p class="cst-head-meta">' + esc(T("admin.reports.consulta.emitida_aviso", null, "La receta y la orden ya se emitieron. Si hay que corregir algo, reemplazalas: las anteriores quedan ANULADAS y su enlace lo dice.")) + "</p>" +
      '<button type="button" class="btn btn-quiet" id="cst-reemplazar">' + esc(T("admin.reports.consulta.reemplazar", null, "Reemplazar receta y orden")) + "</button></div>" +
      queueLink();
  }

  function renderError(error) {
    if (!alive()) return;
    displayedState = null;
    var message = error && error.message === "entrevista no encontrada" ? T("admin.reports.consulta.error_no_encontrada", null, "No se encontró esa entrevista.") :
      error && error.message === "base de datos no disponible" ? T("admin.reports.consulta.error_bd", null, "La base de datos no está disponible ahora.") : T("admin.reports.consulta.error_carga", null, "No se pudo cargar la consulta.");
    host.innerHTML = '<p class="adm-empty">' + esc(message) + '</p><button class="btn" type="button" data-consult-retry>' + esc(T("admin.reports.consulta.retry", null, "Reintentar")) + "</button>" + queueLink();
  }

  function reviewLine(labelKey, fallback, value) {
    return '<p><b>' + esc(T(labelKey, null, fallback)) + ":</b> " + (value ? esc(value) : esc(T("admin.reports.consulta.revisar_vacio", null, "Ninguno"))) + "</p>";
  }

  function reviewBody(state) {
    var data = state.consultation;
    var choices = reviewedChoices(state, true);
    var medico = plainObject(data.medico) && data.medico.nombre ? data.medico.nombre : "";
    var paciente = patientName(state) + (data.dni ? " — DNI " + data.dni : "");
    return '<h3 class="cst-h3">' + esc(T("admin.reports.consulta.revisar_titulo", null, "Revisá antes de generar")) + "</h3>" +
      reviewLine("admin.reports.consulta.revisar_paciente", "Paciente", paciente) +
      (medico ? reviewLine("admin.reports.consulta.revisar_medico", "Médico", medico) : "") +
      reviewLine("admin.reports.consulta.h3_diagnosticos", "Diagnósticos", choices.diagnosticos.join("; ")) +
      reviewLine("admin.reports.consulta.h3_estudios", "Estudios a solicitar", choices.estudios.join("; ")) +
      reviewLine("admin.reports.consulta.h3_medicacion", "Medicación", choices.medicacion.join("; ")) +
      reviewLine("admin.reports.consulta.h3_indicaciones", "Indicaciones", choices.notas) +
      '<p class="cst-head-meta">' + esc(T("admin.reports.consulta.revisar_borrador", null, "Se genera un borrador para revisar e imprimir: no se firma ni se envía solo.")) + "</p>";
  }

  function reviewCardHtml(state) {
    return '<div class="card cst-review" id="cst-review-card">' + reviewBody(state) + "</div>";
  }

  function refreshReview(state) {
    // While the review card is up, the form below stays editable; the card
    // re-reads the live draft instead of showing a stale summary.
    if (!state.reviewing) return;
    var card = host.querySelector && host.querySelector("#cst-review-card");
    if (card) card.innerHTML = reviewBody(state);
  }

  function actionsHtml(state) {
    var error = state.error ? '<p class="cst-error" role="status">' + esc(state.error) + "</p>" : "";
    if (state.reviewing) {
      return error +
        '<button type="button" class="btn btn-accent" id="cst-confirmar"' + (state.sending ? " disabled" : "") + ">" +
        esc(state.sending ? T("admin.reports.consulta.generando", null, "Generando…") : T("admin.reports.consulta.generar_borrador", null, "Confirmar datos y generar borrador")) + "</button>" +
        '<button type="button" class="btn btn-quiet" data-review-cancel' + (state.sending ? " disabled" : "") + ">" +
        esc(T("admin.reports.consulta.cancelar", null, "Cancelar")) + "</button>";
    }
    return error +
      '<button type="button" class="btn btn-accent" id="cst-review"' + (state.sending ? " disabled" : "") + ">" +
      esc(T("admin.reports.consulta.revisar_generar", null, "Revisar y generar documentos")) + "</button>";
  }

  function refreshActions(state) {
    if (host.querySelectorAll) host.querySelectorAll('[data-profile-field],#cst-profile-reviewed,#cst-profile-apply').forEach(function (control) { control.disabled = state.sending; });
    var actions = host.querySelector("#cst-actions");
    if (actions) actions.innerHTML = actionsHtml(state);
  }

  function setReviewMode(state, on) {
    // Entering/leaving review never repaints the form: a clinician may have
    // typed (not yet added) custom text, and confirm()'s own sending state
    // sets the precedent — swap only the card and the actions, same as
    // refreshActions/refreshReview do for their regions.
    state.reviewing = on; state.error = "";
    var actions = host.querySelector && host.querySelector("#cst-actions");
    var parent = actions && actions.parentNode;
    if (!parent || !parent.insertBefore) { render(state); return; }
    var card = host.querySelector("#cst-review-card");
    if (on && !card && typeof document !== "undefined" && document.createElement) {
      var holder = document.createElement("div");
      holder.className = "card cst-review";
      holder.id = "cst-review-card";
      holder.innerHTML = reviewBody(state);
      parent.insertBefore(holder, actions);
    } else if (!on && card && card.parentNode) {
      card.parentNode.removeChild(card);
    }
    refreshActions(state);
  }

  function selectedChoices(state) {
    var diagnoses = Array.isArray(state.catalogs.diagnosticos) ? state.catalogs.diagnosticos : [];
    var exams = Array.isArray(state.catalogs.estudios) ? state.catalogs.estudios : [];
    return {
      diagnosticos: diagnoses.filter(function (item) { return state.selectedDiagnoses.has(item.id); }).map(function (item) { return { id: item.id, label: item.label, codigo: item.codigo, sistema: item.sistema, custom: false }; }).concat(state.customDiagnoses),
      estudios: exams.filter(function (item) { return state.selectedExams.has(item.id); }).map(function (item) { return { id: item.id, label: item.label, custom: false }; }).concat(state.customExams),
      medicacion: state.meds.filter(function (item) { return String(item.nombre || "").trim(); }).map(function (item) { return { nombre: item.nombre, dosis: item.dosis, frecuencia: item.frecuencia, duracion: item.duracion, custom: true }; }),
      notas: state.notes
    };
  }

  // `shown` = the review screen (page language); otherwise the stored wording sent to the server.
  function reviewedChoices(state, shown) {
    var choices = selectedChoices(state);
    var trim = function (value) { return String(value || "").trim(); };
    return {
      diagnosticos: choices.diagnosticos.map(function (item) {
        return trim(item.label) + (item.codigo ? " (" + (shown ? codeSystem(item) : (item.sistema || "CIE-10")) + " " + item.codigo + ")" : "");
      }),
      estudios: choices.estudios.map(function (item) { return item.label || ""; }),
      medicacion: choices.medicacion.map(function (item) {
        return [item.nombre, item.dosis, item.frecuencia, item.duracion].map(trim).filter(Boolean).join(" — ");
      }),
      notas: trim(choices.notas)
    };
  }

  function confirmationPayload(state) {
    var issued = state.replacing ? issuedOf(state.consultation) : null;
    var replace = issued ? { reemplaza_id: issued.id } : {};
    return Object.assign(selectedChoices(state), replace, {
      patient_reviewed: { nombre: patientName(state), dni: String(state.consultation.dni || "") },
      choices_reviewed: reviewedChoices(state),
      profile_reviewed: state.profileReviewed === true,
      profile: state.reviewedProfile || state.consultation.perfil || {}
    });
  }

  function load(id) {
    clearStatus();
    var generation = ++routeGeneration;
    displayedState = null;
    currentId = String(id || "");
    if (readController) readController.abort();
    readController = typeof AbortController === "function" ? new AbortController() : null;
    if (!id) {
      host.innerHTML = '<p class="adm-empty">' + esc(T("admin.reports.consulta.falta_id", null, "Falta el id de la entrevista.")) + "</p>" + queueLink();
      return Promise.resolve();
    }
    host.innerHTML = '<p class="adm-empty">' + esc(T("admin.reports.consulta.cargando", null, "Cargando…")) + "</p>";
    var owns = function () { return ownsRoute(generation, id); };
    return api("/api/admin/intake/consult/" + encodeURIComponent(id), readController ? { signal: readController.signal } : null, owns).then(function (result) {
      if (!owns() || result.stale || result.transitioned) return;
      if (!validConsultation(result.json)) throw new Error("malformed consultation response");
      // The shared theme can still be loading its face after the API completes.
      // Paint once with settled metrics; a failed font resolves to the browser's
      // fallback. The entry's owned watchdog still bounds an indefinitely hung load.
      // Only the initial font cohort belongs to this mount. A later unrelated
      // font request must not block navigation after the entry watchdog cleared.
      if (!fontSettlement) {
        var fonts = doc() && doc().fonts;
        fontSettlement = Promise.resolve(fonts && fonts.ready);
      }
      return fontSettlement.then(function () {
        if (!owns()) return;
        var saved = drafts.get(String(id));
        if (!saved) { saved = stateFor(result.json, id); drafts.set(String(id), saved); }
        else { saved.consultation = result.json; saved.catalogs = plainObject(result.json.catalogos) ? result.json.catalogos : {}; }
        render(saved);
      });
    }).catch(function (error) {
      if (!owns() || (error && error.name === "AbortError")) return;
      renderError(error);
    });
  }

  // Opened without an interview (a bookmark, a typed URL), the screen has
  // nothing to show: the doctor goes straight to Pacientes de hoy, where the
  // interviews are. Only the first load does this — a back/forward step to a
  // bare URL keeps the in-page state with its link to the queue.
  var firstRoute = true;
  function route() {
    var id = routeId();
    if (firstRoute) {
      firstRoute = false;
      var location = locationOf();
      if (!id && options.interviewId === undefined && typeof location.replace === "function") {
        location.replace("/admin/cola");
        return Promise.resolve();
      }
    }
    return load(id);
  }

  function activeState() { return displayedState; }

  function clearStatus() {
    var status = host.parentElement && host.parentElement.querySelector("#consult-status");
    if (status) { status.hidden = true; status.textContent = ""; }
  }

  // A malformed CIE-10 code is reported right under its own row, not next to
  // the confirm button far below (live demo 2026-10-25: ~660px off-screen).
  function customHtml(state, type) {
    var error = type === "dx" && state.codeError ? '<p class="cst-error" role="alert">' + esc(state.codeError) + "</p>" : "";
    return error + chips(type === "dx" ? state.customDiagnoses : state.customExams, type);
  }

  function refreshCustom(state, type) {
    var list = host.querySelector && host.querySelector(type === "dx" ? "#cst-dx-custom" : "#cst-exam-custom");
    if (list) list.innerHTML = customHtml(state, type);
    refreshReview(state);
  }

  function refreshMedication(state) {
    var body = host.querySelector && host.querySelector("#cst-med-body");
    if (body) body.innerHTML = medicationRows(state);
    refreshReview(state);
  }

  function addCustom(type) {
    var state = activeState();
    var input = host.querySelector && host.querySelector(type === "dx" ? "#cst-dx-custom-input" : "#cst-exam-custom-input");
    var label = input && String(input.value || "").trim();
    if (!state || !label) return;
    var item = { label: label, custom: true };
    var codeInput = type === "dx" && host.querySelector ? host.querySelector("#cst-dx-custom-code") : null;
    var rawCode = codeInput ? String(codeInput.value || "").trim() : "";
    if (rawCode) {
      var code = cie10(rawCode);
      if (!code) {
        // Never print free text in the code slot: keep what was typed and say why.
        state.codeError = T("admin.reports.consulta.codigo_invalido", { codigo: rawCode }, "«" + rawCode + "» no es un código CIE-10 (letra y dos números, por ejemplo J20.9). Corregilo o dejalo vacío.");
        if (codeInput.setAttribute) codeInput.setAttribute("aria-invalid", "true");
        refreshCustom(state, type);
        if (codeInput.focus) codeInput.focus();
        return;
      }
      item.codigo = code; item.sistema = "CIE-10";
      codeInput.value = "";
    }
    if (codeInput && codeInput.removeAttribute) codeInput.removeAttribute("aria-invalid");
    if (type === "dx") state.codeError = "";
    (type === "dx" ? state.customDiagnoses : state.customExams).push(item);
    input.value = "";
    refreshCustom(state, type);
    var status = host.parentElement && host.parentElement.querySelector("#consult-status");
    if (status) {
      status.hidden = false;
      status.textContent = type === "dx"
        ? T("admin.reports.consulta.dx_agregado", null, "Diagnóstico agregado")
        : T("admin.reports.consulta.estudio_agregado", null, "Estudio agregado");
    }
  }

  function onKeydown(event) {
    if (!alive() || event.key !== "Enter" || event.isComposing) return;
    var id = event.target && event.target.id;
    if (id !== "cst-dx-custom-input" && id !== "cst-dx-custom-code" && id !== "cst-exam-custom-input") return;
    event.preventDefault();
    addCustom(id === "cst-exam-custom-input" ? "exam" : "dx");
  }

  function confirm(state, ownerId, generation) {
    if (!state || !state.reviewing || state.sending || state.confirmed || !ownsRoute(generation, ownerId)) return;
    if (state.profileDirty && !state.profileReviewed) {
      state.error = T("ai.profile.unsaved", null, "Tenés cambios del perfil sin guardar. Marcá Guardar este perfil revisado antes de confirmar.");
      refreshActions(state); return;
    }
    state.sending = true; state.error = ""; refreshActions(state);
    // The mutation belongs to its draft, not to a particular read generation.
    // Navigating away must not erase a real acknowledgement or strand sending.
    // Session navigation remains narrower: only the initiating visible route.
    var owns = function () { return alive() && drafts.get(ownerId) === state; };
    var ownsSession = function () { return ownsRoute(generation, ownerId) && displayedState === state; };
    api("/api/admin/intake/consult/" + encodeURIComponent(ownerId) + "/confirm", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(confirmationPayload(state))
    }, owns, ownsSession).then(function (result) {
      if (!owns() || result.stale || result.transitioned) return;
      if (!validConfirmation(result.json)) throw new Error("invalid confirmation response");
      state.sending = false; state.reviewing = false; state.confirmed = result.json;
      if (displayedState === state) render(state);
    }).catch(function (error) {
      if (!owns() || (error && error.name === "AbortError")) return;
      state.sending = false;
      state.error = errText(error, T("admin.reports.consulta.no_se_pudo_confirmar", null, "No se pudo confirmar"));
      var issued = error && error.status === 409 && plainObject(error.body) ? issuedOf({ confirmada: error.body.confirmada }) : null;
      if (issued) {
        // Someone confirmed (or replaced) first: show what is in force now,
        // never leave a form that would issue a second set.
        state.consultation = Object.assign({}, state.consultation, { confirmada: issued });
        state.replacing = false; state.reviewing = false;
        if (displayedState === state) render(state);
        return;
      }
      if (displayedState === state) refreshActions(state);
    });
  }

  function target(event, selector) {
    return event && event.target && event.target.closest ? event.target.closest(selector) : null;
  }

  function onInput(event) {
    if (!alive()) return;
    var profileInput = event.target.closest && event.target.closest("[data-profile-field]");
    if (profileInput) {
      var profileState = activeState();
      if (!profileState || profileState.sending) return;
      profileState.profileDirty = true;
      profileState.reviewedProfile = profileState.reviewedProfile || Object.assign({}, profileState.consultation.perfil || {});
      profileState.reviewedProfile[profileInput.dataset.profileField] = profileInput.value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
      return;
    }
    var state = activeState(), element = event && event.target;
    if (!state || !element) return;
    if (element.id === "cst-notes") { state.notes = element.value; refreshReview(state); }
    else if (element.id === "cst-dx-search" || element.id === "cst-exam-search") {
      var diagnoses = element.id === "cst-dx-search";
      if (diagnoses) state.diagnosisQuery = element.value;
      else state.examQuery = element.value;
      // Only the result list changes. Replacing the host detaches the focused
      // input and independently remounts the patient-summary dependency.
      var list = element.nextElementSibling;
      if (list && list.classList.contains("cst-pick-list")) {
        list.innerHTML = diagnoses ? diagnosisRows(state) : examRows(state);
      }
    }
    else if (element.getAttribute) {
      var field = element.getAttribute("data-med-field"), index = Number(element.getAttribute("data-med-index"));
      if (field && state.meds[index]) { state.meds[index][field] = element.value; refreshReview(state); }
    }
  }

  function onChange(event) {
    if (!alive()) return;
    var state = activeState(), element = event && event.target;
    if (!state || !element || !element.getAttribute) return;
    var diagnosis = element.getAttribute("data-dx");
    if (diagnosis) {
      // Only the clinician's own click selects. A catalog diagnosis never
      // checks its `estudios_sugeridos` behind the clinician's back: every
      // study on the orden must be an explicit pick.
      if (element.checked) state.selectedDiagnoses.add(diagnosis);
      else state.selectedDiagnoses.delete(diagnosis);
      refreshReview(state);
      return;
    }
    var exam = element.getAttribute("data-exam");
    if (exam) { if (element.checked) state.selectedExams.add(exam); else state.selectedExams.delete(exam); refreshReview(state); }
    var field = element.getAttribute("data-med-field"), index = Number(element.getAttribute("data-med-index"));
    if (field === "nombre" && state.meds[index]) {
      var medication = (Array.isArray(state.catalogs.medicamentos) ? state.catalogs.medicamentos : []).find(function (item) {
        return deaccent(item.nombre).toLowerCase() === deaccent(state.meds[index].nombre).toLowerCase();
      });
      var doses = host.querySelector("#cst-dose-" + index);
      if (doses) doses.innerHTML = doseOptions(medication);
      if (medication) {
        if (!state.meds[index].dosis) state.meds[index].dosis = medication.dosis || "";
        if (!state.meds[index].frecuencia) state.meds[index].frecuencia = medication.frecuencia || "";
        if (!state.meds[index].duracion) state.meds[index].duracion = medication.duracion || "";
        ["dosis", "frecuencia", "duracion"].forEach(function (name) {
          var input = host.querySelector('[data-med-index="' + index + '"][data-med-field="' + name + '"]');
          if (input) input.value = state.meds[index][name];
        });
      }
      refreshReview(state);
    }
  }

  function onClick(event) {
    if (!alive()) return;
    var state = activeState();
    if (!state) {
      if (target(event, "[data-consult-retry]")) route();
      return;
    }
    var removeDx = target(event, "[data-remove-dx]"), removeExam = target(event, "[data-remove-exam]"), removeMed = target(event, "[data-remove-med]");
    if (target(event, "[data-add-dx]")) { addCustom("dx"); return; }
    if (target(event, "[data-add-exam]")) { addCustom("exam"); return; }
    if (target(event, "[data-add-med]")) { state.meds.push({ nombre: "", dosis: "", frecuencia: "", duracion: "" }); refreshMedication(state); return; }
    if (removeDx) { state.customDiagnoses.splice(Number(removeDx.getAttribute("data-remove-dx")), 1); refreshCustom(state, "dx"); return; }
    if (removeExam) { state.customExams.splice(Number(removeExam.getAttribute("data-remove-exam")), 1); refreshCustom(state, "exam"); return; }
    if (removeMed) { state.meds.splice(Number(removeMed.getAttribute("data-remove-med")), 1); refreshMedication(state); return; }
    if (target(event, "#cst-profile-apply")) {
      if (state.sending) return;
      if (state.profileDirty && typeof win().confirm === 'function' && !win().confirm(T("ai.profile.replace", null, "¿Reemplazar tus cambios del perfil con la propuesta de la IA?"))) return;
      state.reviewedProfile = Object.assign({}, state.consultation.perfil_propuesta || {});
      state.profileReviewed = true; state.profileDirty = true; state.error = "";
      render(state); return;
    }
    if (target(event, "#cst-profile-reviewed") && !state.sending) state.profileReviewed = target(event, "#cst-profile-reviewed").checked;
    if (target(event, "#cst-reemplazar") && !state.sending && !state.replacing && issuedOf(state.consultation)) {
      var ask = win().confirm;
      if (typeof ask !== "function" || !ask(T("admin.reports.consulta.reemplazar_confirmar", null, "¿Reemplazar la receta y la orden? Las emitidas quedan ANULADAS: su enlace va a decir que no valen."))) return;
      startReplacing(state); render(state); return;
    }
    if (target(event, "#cst-reemplazo-cancelar") && !state.sending && state.replacing) {
      state.replacing = false; state.reviewing = false; state.error = ""; render(state); return;
    }
    if (target(event, "#cst-review") && !state.sending) { setReviewMode(state, true); return; }
    if (target(event, "[data-review-cancel]") && !state.sending) { setReviewMode(state, false); return; }
    if (target(event, "#cst-confirmar") && state.reviewing) confirm(state, currentId, routeGeneration);
  }

  mounts.set(host, dispose);
  dispose.ready = Promise.all([
    importStatic("platform/dates.mjs"), importStatic("platform/i18n.mjs"),
    importStatic("platform/admin-patient-summary.mjs"), importStatic("platform/admin-session.mjs")
  ]).then(function (modules) {
    if (!modules[0] || typeof modules[0].relativo !== "function") throw new Error("Invalid consultation dependency: platform/dates.mjs");
    if (!modules[1] || typeof modules[1].t !== "function") throw new Error("Invalid consultation dependency: platform/i18n.mjs");
    if (!modules[2] || typeof modules[2].mount !== "function") throw new Error("Invalid consultation dependency: platform/admin-patient-summary.mjs");
    if (!modules[3] || typeof modules[3].confirmar !== "function") throw new Error("Invalid consultation dependency: platform/admin-session.mjs");
    Dates = modules[0]; I18n = modules[1]; PatientSummary = modules[2]; Session = modules[3];
    if (!alive()) return;
    injectCss();
    listen(host, "input", onInput); listen(host, "change", onChange); listen(host, "click", onClick); listen(win(), "popstate", route);
    listen(host, "keydown", onKeydown);
    listen(win(), "beforeunload", function (event) {
      if (Array.from(drafts.values()).some(function (state) { return state.profileDirty && !state.confirmed; })) {
        event.preventDefault(); event.returnValue = '';
      }
    });
    return route();
  });
  return dispose;
}
