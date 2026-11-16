/* static/platform/admin-prompts.mjs — sección «Instrucciones de la IA» del panel.
 *
 * El inglés y su traducción al español son dos editores del mismo texto de
 * trabajo. El contrato técnico (JSON, marcadores y tools) se muestra aparte y
 * no se edita. Abrir un prompt nunca espera a la traducción: la portada ya trae
 * título, descripción, inglés y contrato; si falta español, sólo ese textarea
 * queda traduciendo. Datos: /api/admin/prompts (solo_admin). */
"use strict";

function importDep(name, nodeUrl) {
  var url = typeof window !== "undefined" && window.SANA_STATIC && window.SANA_STATIC[name];
  return url ? import(url) : import(nodeUrl);
}

var Format = await importDep("platform/format.mjs", new URL("./format.mjs", import.meta.url));
var sessionReady = importDep("platform/admin-session.mjs", new URL("./admin-session.mjs", import.meta.url));
sessionReady.catch(function () {});
var I18n = await importDep("platform/i18n.mjs", new URL("./i18n.mjs", import.meta.url)).catch(function () { return null; });
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

  var ACTUAL = null;
  var pendingMutation = null;
  function beginMutation() {
    if (pendingMutation) return false;
    var controls = Array.prototype.slice.call(document.querySelectorAll('#sec-prompts button, #sec-prompts textarea'));
    pendingMutation = { epoch: lifecycleEpoch, controls: controls.map(function (control) { return { control: control, disabled: control.disabled }; }) };
    controls.forEach(function (control) { control.disabled = true; });
    return true;
  }
  function finishMutation(epoch) {
    if (!isCurrent(epoch) || !pendingMutation || pendingMutation.epoch !== epoch) return;
    pendingMutation.controls.forEach(function (entry) { entry.control.disabled = entry.disabled; });
    pendingMutation = null;
  }
  // A direct URL is untrusted input. Keep the prompt index prototype-free and
  // resolve only catalog keys, never Object.prototype names such as toString.
  var ITEMS = Object.create(null);
  var LISTA_PROMISE = null;
  var TRADUCIENDO = Object.create(null);
  var pausedDraft = null;

  function inyectarCss() {
    if (document.getElementById("prompts-css")) return;
    var st = document.createElement("style");
    st.id = "prompts-css";
    st.textContent = [
      ".pr-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin-top:6px;}",
      ".pr-card{display:block;background:var(--card,#fff);border:1px solid var(--border,#e4e4e0);",
      "border-radius:12px;padding:16px 18px;text-decoration:none;color:inherit;}",
      ".pr-card:hover{border-color:var(--hover-bd);}",
      // Same frameless card as Términos y privacidad: the title in the link
      // colour says it opens.
      ".pr-card h2{font-size:16px;margin:0 0 6px;font-weight:500;color:var(--accent);}",
      ".pr-card:hover h2{color:var(--accent-hover,var(--accent));}",
      ".pr-card p{font-size:15px;color:var(--text,#1c1c1c);margin:0;line-height:1.5;}",
      ".pr-estado{font-size:13px;font-weight:600;display:block;margin-top:5px;color:var(--hint-ink,var(--text-2,#666));}",
      ".pr-estado--editado{color:var(--warn);}",
      ".pr-edit{max-width:860px;}",
      ".pr-resumen{font-size:15px;line-height:1.55;margin:0 0 6px;color:var(--text,#1c1c1c);}",
      ".pr-editor{margin-top:22px;}",
      // Mismas pestañas que «Resumen del paciente» (admin-patient-summary.mjs):
      // un solo control, no dos editores apilados.
      ".pr-tabs{display:flex;gap:18px;border-bottom:1px solid var(--border,#e4e4e0);margin:0 0 14px;}",
      ".pr-tab{font:inherit;font-size:13px;border:0;background:none;cursor:pointer;padding:6px 2px 8px;",
      "color:var(--hint-ink,var(--text-2,#666));border-bottom:2px solid transparent;margin-bottom:-1px;}",
      ".pr-tab.on{color:var(--text,#1c1c1c);font-weight:650;border-bottom-color:var(--accent,#567b42);}",
      ".pr-lab{display:block;font-size:14px;font-weight:650;margin:0 0 7px;}",
      ".pr-lab-nota{display:block;font-weight:400;color:var(--hint-ink);font-size:13px;margin-top:3px;}",
      ".pr-ta{width:100%;box-sizing:border-box;min-height:300px;padding:14px 16px;font-size:14.5px;",
      "line-height:1.62;font-family:inherit;border:1px solid var(--border,#d8d8d2);border-radius:10px;",
      "background:var(--card,#fff);color:var(--text,#1c1c1c);resize:vertical;}",
      ".pr-ta:focus{outline:2px solid var(--accent,#567b42);outline-offset:-1px;}",
      ".pr-ta[readonly]{background:var(--bg-2,#f3f3f0);color:var(--hint-ink);}",
      ".pr-vars,.pr-tech{background:var(--bg-2,#f3f3f0);border-radius:10px;padding:13px 16px;margin:18px 0 0;font-size:13.5px;line-height:1.55;}",
      // The variable name reads as part of the sentence: no chip, whose padding
      // and border showed «{hoy} :» with a gap before the colon (ui-expert 15.11).
      ".pr-vars code{font:inherit;font-weight:650;background:none;border:0;padding:0;}",
      ".pr-tech h2{font-size:14px;margin:0 0 3px;}",
      ".pr-tech p{color:var(--hint-ink);margin:0 0 10px;}",
      ".pr-tech pre{margin:0;padding:12px;background:var(--card,#fff);border:1px solid var(--border,#e4e4e0);",
      "border-radius:8px;font:13px/1.55 var(--font-mono,monospace);white-space:pre-wrap;word-break:break-word;}",
      ".pr-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px;}",
      ".pr-actions-danger{margin-left:auto;display:flex;gap:14px;align-items:center;}",
      ".pr-msg{font-size:14px;}",
      ".pr-msg--error{color:var(--danger,#b3261e);}",
      ".pr-cambios{margin:22px 0 0;}",
      ".pr-cambios h3{font-size:15px;font-weight:650;margin:0 0 10px;}",
      ".pr-cambio{border:1px solid var(--border,#e4e4e0);border-radius:10px;padding:12px 14px;margin-bottom:10px;background:var(--card,#fff);}",
      ".pr-cambio-t{font-size:13px;font-weight:650;color:var(--hint-ink);margin:0 0 7px;}",
      ".pr-cambio pre{margin:0;font-size:13px;line-height:1.55;white-space:pre-wrap;word-break:break-word;font-family:inherit;}",
      ".pr-cambio .pr-antes{color:var(--hint-ink);text-decoration:line-through;margin-bottom:6px;}",
      ".pr-det{margin-top:26px;}",
      ".pr-det>summary{cursor:pointer;font-size:14px;font-weight:600;}",
      ".pr-hist{list-style:none;padding:0;margin:12px 0 0;font-size:14px;}",
      ".pr-hist li{padding:9px 0;border-top:1px solid var(--border,#eee);display:flex;gap:14px;align-items:baseline;flex-wrap:wrap;}",
      ".pr-hist li:first-child{border-top:0;}",
      ".pr-hist .pr-hist-fecha{color:var(--hint-ink);}",
      ".pr-hist button[data-hist]{font:inherit;background:none;border:0;padding:0;color:var(--accent);cursor:pointer;}",
      // The phone gutter comes from the shared section list in admin-direct.css (--adm-gutter).
      "@media(max-width:640px){.pr-cards{grid-template-columns:1fr;}}",
    ].join("");
    document.head.appendChild(st);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function directStatus(msg, isError) {
    var status = document.getElementById("prompts-status");
    if (!status) return;
    status.hidden = false;
    status.className = "adm-empty" + (isError ? " pr-msg--error" : "");
    status.textContent = msg;
  }

  function toast(msg) {
    directStatus(msg, false);
  }

  function promptHref(clave) {
    return "/admin/prompts" + (clave ? "?prompt=" + encodeURIComponent(clave) : "");
  }

  function directState() {
    var key = new URLSearchParams(location.search).get("prompt") || "";
    var canonical = promptHref(key);
    if (location.pathname + location.search !== canonical) {
      history.replaceState({}, "", canonical);
    }
    return key;
  }

  function setDirectPrompt(key) {
    history.pushState({}, "", promptHref(key));
    readyRoute();
  }

  function fecha(iso) {
    if (!iso) return "";
    return Format.fechaCorta(iso);
  }

  function estadoTexto(it) {
    if (!it.editado) return T("admin.content.prompts.status_original", null, "Original");
    var quien = it.actualizado_por
      ? T("admin.content.prompts.status_by", { who: it.actualizado_por }, " por {who}") : "";
    var fechaEditado = it.actualizado ? fecha(it.actualizado) : "";
    var cuando = it.actualizado
      ? T("admin.content.prompts.status_on", { when: fechaEditado }, " el {when}") : "";
    return T("admin.content.prompts.status_edited", null, "Editado") + quien + cuando;
  }

  function api(url, opts) {
    var epoch = lifecycleEpoch;
    opts = opts || {};
    opts.credentials = "same-origin";
    return fetch(url, opts).then(function (r) {
      if (!isCurrent(epoch)) throw cancelled();
      var boundary = (r.status === 401 || r.status === 403)
        ? sessionReady.then(function (mod) { return mod.confirmar(r, undefined, function () { return isCurrent(epoch); }); })
        : Promise.resolve(false);
      return boundary.then(function (transitioned) {
        if (!isCurrent(epoch)) throw cancelled();
        if (transitioned) throw new Error("401");
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!isCurrent(epoch)) throw cancelled();
          if (!r.ok) throw new Error(j.error || T("admin.content.prompts.generic_error", null,
            "No se pudo completar la acción."));
          return j;
        });
      });
    }).catch(function (error) {
      // fetch can reject after destroy too. Convert that late transport error
      // into the same settled cancellation used for late successful replies.
      if (!isCurrent(epoch) && !error.cancelled) throw cancelled();
      throw error;
    });
  }

  function recordar(items) {
    (items || []).forEach(function (it) { ITEMS[it.clave] = it; });
    return items || [];
  }

  function promptByKey(clave) {
    return Object.prototype.hasOwnProperty.call(ITEMS, clave) ? ITEMS[clave] : null;
  }

  function datosLista(force) {
    if (!force && LISTA_PROMISE) return LISTA_PROMISE;
    var epoch = lifecycleEpoch;
    LISTA_PROMISE = api("/api/admin/prompts").then(function (j) {
      if (!isCurrent(epoch)) throw cancelled();
      return recordar(j.items || []);
    }).catch(function (e) {
      if (epoch === lifecycleEpoch) LISTA_PROMISE = null;
      throw e;
    });
    return LISTA_PROMISE;
  }

  // Qué hace y dónde corre, una sola frase oscura: la nota de «dónde» iba
  // aparte, más chica y más clara que el texto que acompaña (C14, 08.11).
  function queYDonde(it) {
    return [it.que_hace, it.donde].filter(function (x) { return x && String(x).trim(); })
      .map(function (x) { return String(x).trim(); }).join(" ");
  }
  // One line for the list: where the instruction runs, else its first sentence.
  function resumenCorto(it) {
    var donde = String(it.donde || "").trim();
    if (donde) return donde;
    var que = String(it.que_hace || "").trim();
    var corte = que.search(/[.:]\s/);
    return corte > 0 ? que.slice(0, corte) + "." : que;
  }
  function renderLista(items) {
    var h1 = document.getElementById("adm-h1");
    if (h1) { h1.style.display = ""; h1.textContent = T("admin.content.prompts.title", null, "Instrucciones de la IA"); }
    var sec = document.getElementById("sec-prompts");
    sec.innerHTML = '<div class="pr-cards">' + items.map(function (it) {
      // The list is the title and one short line (where it runs); the full
      // description waits in the editor. Only an EDITED instruction carries a
      // status, under its title — «Original» twelve times said nothing.
      return '<a class="pr-card" href="' + esc(promptHref(it.clave)) + '" data-prompt-key="' + esc(it.clave) + '">' +
        "<h2>" + esc(it.titulo) + "</h2>" +
        (resumenCorto(it) ? "<p>" + esc(resumenCorto(it)) + "</p>" : "") +
        (it.editado ? '<span class="pr-estado pr-estado--editado">' + esc(estadoTexto(it)) + "</span>" : "") + "</a>";
    }).join("") + "</div>";
    wireDirectLinks(sec);
  }

  function cargarLista() {
    var sec = document.getElementById("sec-prompts");
    var epoch = lifecycleEpoch;
    if (!Object.keys(ITEMS).length) sec.innerHTML = '<p class="adm-empty">' +
      esc(T("admin.content.prompts.loading", null, "Cargando…")) + "</p>";
    datosLista().then(function (items) { if (isCurrent(epoch)) renderLista(items); }).catch(function (e) {
      if (e.cancelled || !isCurrent(epoch)) return;
      if (String(e.message) === "401") return;
      sec.innerHTML = '<p class="adm-empty">' + esc(T("admin.content.prompts.list_load_error", null,
        "No se pudieron cargar las instrucciones.")) + "</p>";
    });
  }

  function varsHtml(it) {
    if (!it.variables || !it.variables.length) return "";
    return '<div class="pr-vars"><b>' +
      esc(T("admin.content.prompts.vars_heading", null, "Datos que el sistema agrega al usarlas")) +
      "</b><br>" +
      it.variables.map(function (v) {
        return "<code>{" + esc(v.nombre) + "}</code>: " + esc(v.que_es);
      }).join("<br>") + "</div>";
  }

  function tecnicoHtml(it) {
    if (!it.tecnico_en) return "";
    return '<section class="pr-tech"><h2>' +
      esc(T("admin.content.prompts.tech_heading", null, "Datos técnicos")) + "</h2>" +
      "<p>" + esc(T("admin.content.prompts.tech_hint", null,
        "El sistema necesita esta estructura exacta. Se muestra para referencia y no se edita.")) + "</p>" +
      "<pre>" + esc(it.tecnico_en) + "</pre></section>";
  }

  // The document language is the server-resolved value; I18n.lang agrees
  // with it after the canonical module evaluates. In English there are no
  // translation tabs: only the original is shown.
  var IDIOMAS = { es: "Español", en: "Inglés", pt: "Portugués" };

  function idiomaActivo() {
    var l = (document.documentElement && document.documentElement.lang)
      || (I18n && I18n.lang) || "es";
    return String(l).toLowerCase().slice(0, 2);
  }

  function editorTraducidoHtml(it) {
    if (it.texto_es && it.alineado) {
      return '<textarea class="pr-ta" id="pr-es" spellcheck="true">' +
        esc(it.texto_es) + "</textarea>";
    }
    var mensaje = it.error_traduccion
      ? T("admin.content.prompts.translation_error", null,
        "No se pudo traducir. Podés editar y guardar el inglés mientras tanto.")
      : T("admin.content.prompts.translating", null, "Traduciendo…");
    return '<textarea class="pr-ta" id="pr-es" readonly aria-busy="' +
      (it.error_traduccion ? "false" : "true") + '">' + esc(mensaje) + "</textarea>" +
      (it.error_traduccion
        ? '<div class="pr-actions"><button type="button" class="btn btn-quiet" id="pr-reintentar">' +
          esc(T("admin.content.prompts.retry_translation", null, "Reintentar traducción")) + "</button></div>"
        : "");
  }

  // UN control con dos pestañas: el idioma en que se usa el panel y el
  // original en inglés. Un solo botón de guardar — guarda la pestaña que se
  // esté viendo, y el backend ya acepta las dos formas del payload.
  function editorHtml(it) {
    var lang = idiomaActivo();
    var soloIngles = lang === "en";
    var nombre = IDIOMAS[lang] || lang.toUpperCase();
    var tabs = soloIngles ? "" :
      '<div class="pr-tabs" role="tablist">' +
      '<button type="button" class="pr-tab on" role="tab" aria-selected="true" data-tab="trad">' +
      esc(nombre) + "</button>" +
      '<button type="button" class="pr-tab" role="tab" aria-selected="false" data-tab="en">' +
      esc(T("admin.content.prompts.tab_english", null, "Inglés (original)")) + "</button>" +
      "</div>";
    // The legal AI notice (ai.notice.prompt, decision 14.10) rides with the
    // translated edit, not under the screen title (the product owner, 08.11): it is shown
    // where the AI translates, before the text it translates.
    var aviso = '<p class="adm-ai-notice" data-ai-notice="prompt">' + esc(T("ai.notice.prompt", null,
      "La inteligencia artificial traduce las instrucciones editadas. Revisá los cambios antes de usarlos.")) + "</p>";
    var panelTrad = soloIngles ? "" :
      '<div data-panel="trad">' + aviso + '<p class="pr-lab-nota">' + esc(T("admin.content.prompts.save_hint_translated", null,
        "Al guardar, sólo se traducen al inglés los párrafos que cambiaste.")) +
      '</p><div id="pr-es-host">' +
      editorTraducidoHtml(it) + "</div></div>";
    var panelEn = '<div data-panel="en"' + (soloIngles ? "" : " hidden") + ">" + (soloIngles ? aviso : "") +
      '<p class="pr-lab-nota">' + esc(T("admin.content.prompts.save_hint_english", null,
        "Es el texto que recibe la IA. Si lo guardás, se almacena tal cual.")) +
      '</p><textarea class="pr-ta" id="pr-en" spellcheck="true">' +
      esc(it.texto_en) + "</textarea></div>";
    return '<section class="pr-editor">' + tabs + panelTrad + panelEn +
      '<div class="pr-actions"><button type="button" class="btn btn-accent" id="pr-guardar">' +
      esc(T("admin.content.prompts.save_button", null, "Guardar")) + "</button>" +
      '<span id="pr-msg" class="pr-msg"></span><span class="pr-actions-danger">' +
      (it.editado ? '<button type="button" id="pr-restaurar">' +
        esc(T("admin.content.prompts.restore_link", null, "Restaurar el original")) + "</button>" : "") +
      "</span></div></section>";
  }

  function pestanaActiva() {
    var on = document.querySelector("#sec-prompts .pr-tab.on");
    return on ? on.getAttribute("data-tab") : "en";
  }

  function renderEditor(it, extra) {
    ACTUAL = it;
    ITEMS[it.clave] = it;
    var h1 = document.getElementById("adm-h1");
    // «Instrucciones de la IA / name» in the title band, like a doctor's card
    // (ui-expert 15.11): the crumb no longer sits under the name.
    if (h1) {
      h1.style.display = "";
      h1.innerHTML = '<a class="adm-crumb" href="' + esc(promptHref("")) + '" data-prompt-key="">' +
        esc(T("admin.content.prompts.title", null, "Instrucciones de la IA")) + '</a><span class="adm-crumb-sep">/</span>' + esc(it.titulo);
      wireDirectLinks(h1);
    }
    var sec = document.getElementById("sec-prompts");
    sec.innerHTML =
      '<div class="pr-edit"><p class="pr-resumen">' + esc(queYDonde(it)) + "</p>" + varsHtml(it) +
      editorHtml(it) + "<div id=\"pr-cambios\"></div>" + tecnicoHtml(it) +
      '<details class="pr-det" id="pr-det-hist"><summary>' +
      esc(T("admin.content.prompts.history_summary", null, "Versiones anteriores")) + "</summary>" +
      '<div id="pr-hist"><p class="adm-empty">' +
      esc(T("admin.content.prompts.loading", null, "Cargando…")) + "</p></div></details></div>";
    if (extra) pintarCambios(extra);
    wire(it);
    wireDirectLinks(sec);
    if (pausedDraft && pausedDraft.clave === it.clave) {
      pausedDraft.fields.forEach(function (field) {
        var textarea = document.getElementById(field.id);
        if (textarea) {
          textarea.value = field.value;
          textarea.setSelectionRange(field.start, field.end);
        }
      });
      var tab = sec.querySelector('[data-tab="' + pausedDraft.tab + '"]');
      if (tab) tab.click();
      pausedDraft = null;
    }
    if (!it.texto_es || !it.alineado) cargarTraduccion(it.clave);
  }

  function actualizarEspanol(it) {
    if (!ACTUAL || ACTUAL.clave !== it.clave) return;
    ACTUAL = it;
    ITEMS[it.clave] = it;
    var host = document.getElementById("pr-es-host");
    if (!host) return;
    host.innerHTML = editorTraducidoHtml(it);
    wireReintento();
  }

  function cargarTraduccion(clave, force) {
    if (TRADUCIENDO[clave] && !force) return TRADUCIENDO[clave];
    var epoch = lifecycleEpoch;
    TRADUCIENDO[clave] = api("/api/admin/prompts/" + encodeURIComponent(clave) + "/traduccion")
      .then(function (it) {
        if (!isCurrent(epoch)) throw cancelled();
        delete TRADUCIENDO[clave];
        actualizarEspanol(it);
        return it;
      }).catch(function (e) {
        if (e.cancelled || !isCurrent(epoch)) return;
        delete TRADUCIENDO[clave];
        if (String(e.message) === "401") return;
        var it = Object.assign({}, ITEMS[clave] || ACTUAL || {}, {
          clave: clave,
          texto_es: null,
          alineado: false,
          error_traduccion: e.message
        });
        actualizarEspanol(it);
      });
    return TRADUCIENDO[clave];
  }

  function pintarCambios(cambios) {
    var host = document.getElementById("pr-cambios");
    if (!host) return;
    if (!cambios || !cambios.length) { host.innerHTML = ""; return; }
    var TIT = {
      editado: T("admin.content.prompts.change_type_edited", null, "Párrafo cambiado"),
      nuevo: T("admin.content.prompts.change_type_new", null, "Párrafo nuevo"),
      borrado: T("admin.content.prompts.change_type_deleted", null, "Párrafo borrado")
    };
    host.innerHTML = '<div class="pr-cambios"><h3>' +
      esc(Tn("admin.content.prompts.changes_count", cambios.length, null, "Cambió {n} párrafo", "Cambiaron {n} párrafos")) +
      "</h3>" + cambios.map(function (c) {
        return '<div class="pr-cambio"><p class="pr-cambio-t">' + esc(TIT[c.tipo] || c.tipo) + "</p>" +
          (c.en_antes ? '<pre class="pr-antes">' + esc(c.en_antes) + "</pre>" : "") +
          (c.en_ahora ? "<pre>" + esc(c.en_ahora) + "</pre>" : "") + "</div>";
      }).join("") + "</div>";
  }

  function guardar(payload, btn) {
    if (btn.disabled || !beginMutation()) return;
    var epoch = lifecycleEpoch;
    var clave = ACTUAL && ACTUAL.clave;
    var msg = document.getElementById("pr-msg");
    btn.disabled = true;
    if (msg) { msg.className = "pr-msg"; msg.textContent = T("admin.content.prompts.saving", null, "Guardando…"); }
    api("/api/admin/prompts/" + encodeURIComponent(ACTUAL.clave) + "/guardar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (j) {
      if (!isCurrent(epoch) || !ACTUAL || ACTUAL.clave !== clave) throw cancelled();
      if (!j.ok || !j.item || j.item.clave !== clave || typeof j.item.texto_en !== "string") {
        throw new Error(T("admin.content.prompts.generic_error", null, "No se pudo completar la acción."));
      }
      LISTA_PROMISE = null;
      toast(T("admin.content.prompts.saved_toast", null, "Instrucciones guardadas."));
      renderEditor(j.item, j.cambios || []);
      var m = document.getElementById("pr-msg");
      if (m) m.textContent = T("admin.content.prompts.saved_msg", null, "Guardado.");
      finishMutation(epoch);
    }).catch(function (e) {
      if (e.cancelled || !isCurrent(epoch) || !ACTUAL || ACTUAL.clave !== clave) return;
      finishMutation(epoch);
      if (String(e.message) === "401") return;
      var m = document.getElementById("pr-msg");
      if (m) { m.className = "pr-msg pr-msg--error"; m.textContent = errText(e, T("admin.content.prompts.generic_error", null, "No se pudo guardar.")); }
    });
  }

  function wireReintento() {
    var rt = document.getElementById("pr-reintentar");
    if (rt) rt.addEventListener("click", function () {
      var it = Object.assign({}, ACTUAL, { error_traduccion: null });
      actualizarEspanol(it);
      cargarTraduccion(it.clave, true);
    });
  }

  function wirePestanas() {
    var sec = document.querySelector("#sec-prompts .pr-editor");
    if (!sec) return;
    sec.querySelectorAll(".pr-tab").forEach(function (b) {
      b.addEventListener("click", function () {
        var cual = b.getAttribute("data-tab");
        sec.querySelectorAll(".pr-tab").forEach(function (x) {
          var on = x === b;
          x.classList.toggle("on", on);
          x.setAttribute("aria-selected", on ? "true" : "false");
        });
        // Los dos paneles siguen en el DOM: cambiar de pestaña nunca tira lo
        // que se venía escribiendo en la otra.
        sec.querySelectorAll("[data-panel]").forEach(function (p) {
          p.hidden = p.getAttribute("data-panel") !== cual;
        });
      });
    });
  }

  function wire(it) {
    wireReintento();
    wirePestanas();
    var g = document.getElementById("pr-guardar");
    if (g) g.addEventListener("click", function () {
      if (pestanaActiva() === "en") {
        var ta = document.getElementById("pr-en");
        guardar({ texto_en: ta ? ta.value : "" }, g);
        return;
      }
      var es = document.getElementById("pr-es");
      if (!es || es.readOnly) {
        // Todavía está traduciendo (o falló): guardar mandaría el cartel de
        // "Traduciendo…" como si fuera el prompt.
        var m = document.getElementById("pr-msg");
        if (m) {
          m.className = "pr-msg pr-msg--error";
          m.textContent = T("admin.content.prompts.wait_translation", null, "Esperá a que termine la traducción, o editá el inglés.");
        }
        return;
      }
      guardar({ texto_es: es.value }, g);
    });
    var r = document.getElementById("pr-restaurar");
    if (r) r.addEventListener("click", function (e) {
      e.preventDefault();
      if (pendingMutation) return;
      if (!window.confirm(T("admin.content.prompts.confirm_restore", null, "Se vuelve al texto original y se pierde lo editado. ¿Seguir?"))) return;
      var epoch = lifecycleEpoch;
      if (!beginMutation()) return;
      api("/api/admin/prompts/" + encodeURIComponent(it.clave), { method: "DELETE" })
        .then(function (j) {
          if (!isCurrent(epoch) || !ACTUAL || ACTUAL.clave !== it.clave) throw cancelled();
          if (!j.ok || !j.item || j.item.clave !== it.clave || typeof j.item.texto_en !== "string") {
            throw new Error(T("admin.content.prompts.generic_error", null, "No se pudo completar la acción."));
          }
          LISTA_PROMISE = null;
          toast(T("admin.content.prompts.restored_toast", null, "Se restauró el original."));
          renderEditor(j.item, []);
          finishMutation(epoch);
        }).catch(function (err) {
          finishMutation(epoch);
          if (!err.cancelled && isCurrent(epoch) && String(err.message) !== "401") toast(errText(err, T("admin.content.prompts.generic_error", null, "No se pudo guardar.")));
        });
    });
    var det = document.getElementById("pr-det-hist");
    if (det) det.addEventListener("toggle", function () {
      if (!det.open || det.dataset.cargado) return;
      det.dataset.cargado = "1";
      var epoch = lifecycleEpoch;
      api("/api/admin/prompts/" + encodeURIComponent(it.clave) + "/historial")
        .then(function (j) { if (!isCurrent(epoch) || !ACTUAL || ACTUAL.clave !== it.clave) throw cancelled(); pintarHistorial(j.items || []); })
        .catch(function (err) {
          if (err.cancelled || !isCurrent(epoch) || !ACTUAL || ACTUAL.clave !== it.clave) return;
          var h = document.getElementById("pr-hist");
          if (h) h.innerHTML = '<p class="adm-empty">' + T("admin.content.prompts.history_load_error", null, "No se pudo cargar el historial.") + '</p>';
        });
    });
  }

  function pintarHistorial(items) {
    var host = document.getElementById("pr-hist");
    if (!host) return;
    if (!items.length) {
      host.innerHTML = '<p class="adm-empty">' + T("admin.content.prompts.no_previous_versions", null, "Todavía no hay versiones anteriores.") + '</p>';
      return;
    }
    host.innerHTML = '<ul class="pr-hist">' + items.map(function (h, i) {
      return "<li><span class='pr-hist-fecha'>" + esc(fecha(h.actualizado)) +
        (h.actualizado_por ? " · " + esc(h.actualizado_por) : "") + "</span>" +
        "<button type='button' data-hist='" + i + "'>" + T("admin.content.prompts.revert_to_version", null, "Esta versión") + "</button></li>";
    }).join("") + "</ul>";
    host.querySelectorAll("button[data-hist]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        var h = items[Number(a.getAttribute("data-hist"))];
        if (h) guardar({ texto_en: h.texto_en }, a);
      });
    });
  }

  function cargarDetalle(clave) {
    var epoch = lifecycleEpoch;
    var item = promptByKey(clave);
    if (item) {
      renderEditor(item, null);
      return;
    }
    var sec = document.getElementById("sec-prompts");
    // The band never names an instruction that is not on screen.
    var h1 = document.getElementById("adm-h1");
    if (h1) h1.textContent = T("admin.content.prompts.title", null, "Instrucciones de la IA");
    sec.innerHTML = '<p class="adm-empty">' + T("admin.content.prompts.loading", null, "Cargando…") + '</p>';
    datosLista().then(function () {
      if (!isCurrent(epoch)) throw cancelled();
      var loaded = promptByKey(clave);
      if (!loaded) throw new Error(T("admin.content.prompts.not_found", null, "No existe esa instrucción."));
      renderEditor(loaded, null);
    }).catch(function (e) {
      if (e.cancelled || !isCurrent(epoch)) return;
      if (String(e.message) === "401") return;
      sec.innerHTML = '<p class="adm-empty">' + T("admin.content.prompts.load_error", null, "No se pudo cargar. ") +
        '<a href="' + esc(promptHref("")) + '" data-prompt-key="">' +
        T("admin.content.prompts.title", null, "Instrucciones de la IA") + '</a></p>';
      wireDirectLinks(sec);
    });
  }

  function wireDirectLinks(root) {
    if (!root) return;
    root.querySelectorAll("a[data-prompt-key]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        // A modified or middle click keeps the browser's own «open in a new tab».
        if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        setDirectPrompt(link.getAttribute("data-prompt-key") || "");
      });
    });
  }

  function route() {
    inyectarCss();
    var el = document.getElementById("sec-prompts");
    if (!el) return;
    lifecycleEpoch += 1;
    pendingMutation = null;
    LISTA_PROMISE = null;
    TRADUCIENDO = Object.create(null);
    var clave = directState();
    el.style.display = "";
    if (clave) cargarDetalle(clave);
    else cargarLista();
  }

var mounted = false;
var lifecycleEpoch = 0;
function isCurrent(epoch) { return mounted && epoch === lifecycleEpoch; }
function cancelled() { var error = new Error("editorial controller destroyed"); error.cancelled = true; return error; }
function readyRoute() { return Promise.resolve().then(function () { if (mounted) route(); }); }
function onPopstate() { readyRoute(); }

export function mount() {
  if (mounted) return;
  mounted = true;
  lifecycleEpoch += 1;
  window.addEventListener("popstate", onPopstate);
  return sessionReady.then(readyRoute);
}
export function destroy() {
  if (mounted && ACTUAL) {
    pausedDraft = {
      clave: ACTUAL.clave,
      tab: pestanaActiva(),
      fields: ["pr-en", "pr-es"].map(function (id) {
        var textarea = document.getElementById(id);
        return textarea && !textarea.readOnly ? {
          id: id, value: textarea.value,
          start: textarea.selectionStart, end: textarea.selectionEnd
        } : null;
      }).filter(Boolean)
    };
  }
  mounted = false;
  lifecycleEpoch += 1;
  LISTA_PROMISE = null;
  TRADUCIENDO = Object.create(null);
  window.removeEventListener("popstate", onPopstate);
}
export var boot = mount;
