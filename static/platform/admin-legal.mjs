/* static/platform/admin-legal.mjs — sección «Términos y privacidad» del panel.
 *
 * Los dos documentos legales del sitio, editables como texto corrido. El
 * original vive en el código (legal.py); guardar crea el override en la base
 * y «Restaurar el original» lo borra — el mismo trato que «Instrucciones de
 * la IA», y por eso los controles son los mismos: «Guardar» en el botón de
 * acento y «Restaurar el original» como enlace al costado.
 * Datos: /api/admin/legal (solo_admin).
 *
 * The direct server-routed screen (`/admin/legal`, `/admin/legal?doc=<doc>`)
 * owns its URL state through pushState/popstate and sends an expired session
 * through the shared direct login. */
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
// A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }

  var ITEMS = Object.create(null);
  var LISTA_PROMISE = null;
  var DIRECT_NAV_WIRED = false;
  var pausedDraft = null;
  var pendingMutation = null;
  function beginMutation() {
    if (pendingMutation) return false;
    var controls = Array.prototype.slice.call(document.querySelectorAll('#sec-legal button, #sec-legal textarea'));
    pendingMutation = { epoch: lifecycleEpoch, controls: controls.map(function (control) { return { control: control, disabled: control.disabled }; }) };
    controls.forEach(function (control) { control.disabled = true; });
    return true;
  }
  function finishMutation(epoch) {
    if (!isCurrent(epoch) || !pendingMutation || pendingMutation.epoch !== epoch) return;
    pendingMutation.controls.forEach(function (entry) { entry.control.disabled = entry.disabled; });
    pendingMutation = null;
  }

  function docById(id) {
    return Object.prototype.hasOwnProperty.call(ITEMS, id) ? ITEMS[id] : null;
  }

  function directStatus(msg, isError, linkHref, linkText) {
    var status = document.getElementById("legal-status");
    if (!status) return;
    status.hidden = false;
    status.className = "adm-empty" + (isError ? " lg-msg--error" : "");
    status.textContent = msg || "";
    if (linkHref) {
      status.appendChild(document.createTextNode(" "));
      var a = document.createElement("a");
      a.href = linkHref;
      a.textContent = linkText || linkHref;
      status.appendChild(a);
    }
  }

  function clearDirectStatus() {
    var status = document.getElementById("legal-status");
    if (!status) return;
    status.hidden = true;
    status.textContent = "";
    status.className = "adm-empty";
  }

  function toast(msg) {
    directStatus(msg, false);
  }

  // English path + English query key; the doc id stays the wire value
  // (terminos/privacidad), never translated text.
  function legalHref(doc) {
    return "/admin/legal" + (doc ? "?doc=" + encodeURIComponent(doc) : "");
  }

  function directState() {
    var doc = new URLSearchParams(location.search).get("doc") || "";
    var canonical = legalHref(doc);
    if (location.pathname + location.search !== canonical) {
      history.replaceState({}, "", canonical);
    }
    return doc;
  }

  function setDirectDoc(doc) {
    history.pushState({}, "", legalHref(doc));
    readyRoute();
  }

  function docAttr(doc) {
    return ' data-legal-doc="' + esc(doc || "") + '"';
  }

  // One delegated handler: plain left clicks on an in-screen document link
  // navigate in place (pushState + route); modified clicks keep the browser's
  // own open-in-new-tab behaviour because the href is a real URL.
  function wireDirectNav() {
    var sec = document.getElementById("sec-legal");
    if (!sec || DIRECT_NAV_WIRED) return;
    DIRECT_NAV_WIRED = true;
    sec.addEventListener("click", function (event) {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      var target = event.target;
      var link = target && target.closest ? target.closest("a[data-legal-doc]") : null;
      if (!link || !sec.contains(link)) return;
      event.preventDefault();
      setDirectDoc(link.getAttribute("data-legal-doc") || "");
    });
  }

  function inyectarCss() {
    if (document.getElementById("legal-css")) return;
    var st = document.createElement("style");
    st.id = "legal-css";
    st.textContent = [
      ".lg-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(320px,100%),1fr));gap:14px;margin-top:6px;}",
      ".lg-card{display:block;background:var(--card,#fff);border:1px solid var(--border,#e4e4e0);",
      "border-radius:12px;padding:16px 18px;text-decoration:none;color:inherit;}",
      ".lg-card:hover{border-color:var(--hover-bd);}",
      // The card has no frame in the panel (admin-direct.css): the title
      // alone says it opens, in the link colour of «Regenerar», not as a
      // bold heading on an empty page (ui-expert 15.11).
      ".lg-card h2{font-size:16px;margin:0 0 6px;font-weight:500;color:var(--accent);}",
      ".lg-card:hover h2{color:var(--accent-hover,var(--accent));}",
      ".lg-card p{font-size:13.5px;color:var(--hint-ink);margin:0;line-height:1.5;}",
      ".lg-estado{font-size:13px;font-weight:600;display:block;margin-top:5px;color:var(--hint-ink);}",
      ".lg-estado--editado{color:var(--warn);}",
      ".lg-edit{max-width:860px;}",
      ".lg-resumen{font-size:15px;line-height:1.55;margin:0 0 6px;}",
      // 16px minimum: anything smaller makes iOS Safari zoom the page on focus.
      ".lg-ta{width:100%;box-sizing:border-box;min-height:460px;padding:14px 16px;font-size:16px;",
      "line-height:1.62;font-family:inherit;border:1px solid var(--border,#d8d8d2);border-radius:10px;",
      "background:var(--card,#fff);color:var(--text,#1c1c1c);resize:vertical;}",
      ".lg-ta:focus{outline:2px solid var(--accent,#567b42);outline-offset:-1px;}",
      ".lg-ayuda{background:var(--bg-2,#f3f3f0);border-radius:10px;padding:13px 16px;margin:18px 0 0;",
      "font-size:13.5px;line-height:1.55;}",
      ".lg-ayuda code{background:var(--card,#fff);border:1px solid var(--border,#e4e4e0);border-radius:5px;",
      "padding:1px 6px;font-size:13px;}",
      ".lg-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px;}",
      // 44px usable touch targets for both actions, on both shells. Save keeps
      // the accent button; Restore stays a plain text link (no box, no border)
      // and only grows its hit area.
      ".lg-actions .btn{min-height:44px;min-width:44px;}",
      ".lg-actions-danger{margin-left:auto;display:flex;gap:14px;align-items:center;}",
      ".lg-actions-danger button{display:inline-flex;align-items:center;min-height:44px;min-width:44px;padding:0 4px;font:inherit;background:none;border:0;color:var(--hint-ink);cursor:pointer;}",
      ".lg-msg{font-size:14px;}",
      ".lg-msg--error{color:var(--danger,#b3261e);}"
    ].join("");
    document.head.appendChild(st);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Mismo formateador que «Instrucciones de la IA»: Format es el único
  // lugar donde se formatea una fecha en el panel.
  function fecha(iso) {
    if (!iso) return "";
    return Format.fechaCorta(iso);
  }

  function estadoTexto(it) {
    if (!it.editado) return T("admin.content.legal.estado_original", null, "Original");
    var quien = it.actualizado_por ? T("admin.content.legal.estado_por", null, " por ") + it.actualizado_por : "";
    return T("admin.content.legal.estado_editado", null, "Editado") + quien + (it.actualizado ? T("admin.content.legal.estado_el", null, " el ") + fecha(it.actualizado) : "");
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
          if (!r.ok) throw new Error(j.error || T("admin.content.legal.error_generico", null, "No se pudo completar la acción."));
          return j;
        });
      });
    }).catch(function (error) {
      // A rejected request may arrive after a fresh mount. It must settle as a
      // cancellation, never paint an error into that fresh document.
      if (!isCurrent(epoch) && !error.cancelled) throw cancelled();
      throw error;
    });
  }

  function recordar(items) {
    (items || []).forEach(function (it) { ITEMS[it.id] = it; });
    return items || [];
  }

  function datosLista(force) {
    if (!force && LISTA_PROMISE) return LISTA_PROMISE;
    var epoch = lifecycleEpoch;
    LISTA_PROMISE = api("/api/admin/legal").then(function (j) {
      if (!isCurrent(epoch)) throw cancelled();
      return recordar(j.items || []);
    }).catch(function (e) {
      if (epoch === lifecycleEpoch) LISTA_PROMISE = null;
      throw e;
    });
    return LISTA_PROMISE;
  }

  function renderLista(items) {
    var h1 = document.getElementById("adm-h1");
    if (h1) { h1.style.display = ""; h1.textContent = T("admin.content.legal.titulo", null, "Términos y privacidad"); }
    var sec = document.getElementById("sec-legal");
    sec.innerHTML = '<div class="lg-cards">' + items.map(function (it) {
      // The title alone; only an EDITED text says so, under it. «Original»
      // above and «Se publica en /terms.html» below said nothing the editor
      // does not (critical review 08.11, D12).
      return '<a class="lg-card" href="' + esc(legalHref(it.id)) + '"' + docAttr(it.id) + '>' +
        "<h2>" + esc(it.titulo) + "</h2>" +
        (it.editado ? '<span class="lg-estado lg-estado--editado">' + esc(estadoTexto(it)) + "</span>" : "") + "</a>";
    }).join("") + "</div>";
  }

  function cargarLista() {
    var sec = document.getElementById("sec-legal");
    var epoch = lifecycleEpoch;
    if (!Object.keys(ITEMS).length) sec.innerHTML = '<p class="adm-empty">' + T("admin.content.legal.cargando", null, "Cargando…") + '</p>';
    datosLista().then(function (items) { if (isCurrent(epoch)) renderLista(items); }).catch(function (e) {
      if (e.cancelled || !isCurrent(epoch)) return;
      if (String(e.message) === "401") return;
      sec.innerHTML = '<p class="adm-empty">' + T("admin.content.legal.no_se_pudieron_cargar", null, "No se pudieron cargar los documentos.") + '</p>';
    });
  }

  function ayudaHtml() {
    return '<div class="lg-ayuda"><b>' + T("admin.content.legal.ayuda_como_se_escribe", null, "Cómo se escribe") + '</b><br>' +
      "<code>## Título</code>" + T("admin.content.legal.ayuda_abre_seccion", null, ": abre una sección.") + "<br>" +
      "<code>- " + esc(T("admin.content.legal.ayuda_item", null, "ítem")) + "</code>" + T("admin.content.legal.ayuda_una_lista", null, ": una lista, un ítem por línea.") + "<br>" +
      "<code>&gt; texto</code>" + T("admin.content.legal.ayuda_recuadro_destacado", null, ": el recuadro destacado.") + "<br>" +
      "<code>**negrita**</code> " + T("admin.content.legal.ayuda_y", null, "y") + " <code>[texto](/pagina)</code>" + T("admin.content.legal.ayuda_resaltado_enlace", null, ": resaltado y enlace.") + "<br>" +
      "<code>{{sitio.brand}}</code>" + T("admin.content.legal.ayuda_nombre_instalacion", null, ": el nombre de esta instalación. ") +
      T("admin.content.legal.ayuda_tambien_andan", null, "También andan ") + "<code>{{sitio.hospital}}</code>, <code>{{sitio.city}}</code>, " +
      "<code>{{sitio.network}}</code> " + T("admin.content.legal.ayuda_y", null, "y") + " <code>{{sitio.emergency_number}}</code>. " +
      T("admin.content.legal.ayuda_escribilos_asi", null, "Escribilos así y cada instalación pone lo suyo.") +
      "</div>";
  }

  function renderEditor(it) {
    ITEMS[it.id] = it;
    var h1 = document.getElementById("adm-h1");
    // «Términos y privacidad / document» in the title band, like a doctor's
    // card — not the name above and its crumb under it (ui-expert 15.11).
    if (h1) {
      h1.style.display = "";
      h1.innerHTML = '<a class="adm-crumb" href="' + esc(legalHref("")) + '"' + docAttr("") + '>'
        + esc(T("admin.content.legal.titulo", null, "Términos y privacidad")) + '</a><span class="adm-crumb-sep">/</span>' + esc(it.titulo);
      var back = h1.querySelector && h1.querySelector("a");
      if (back) back.addEventListener("click", function (event) {
        if (event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        setDirectDoc("");
      });
    }
    var sec = document.getElementById("sec-legal");
    sec.innerHTML =
      '<div class="lg-edit"><p class="lg-resumen">' + T("admin.content.legal.resumen_pre", null, "Lo que se guarde acá es lo que lee cualquier persona en ") +
      '<a href="' + esc(it.pagina) + '" target="_blank" ' +
      'rel="noopener">' + esc(it.pagina) + "</a>" + T("admin.content.legal.resumen_post", null, ". La fecha de última actualización se pone sola al guardar.") + "</p>" +
      '<textarea class="lg-ta" id="lg-cuerpo" spellcheck="true" aria-label="' + esc(it.titulo) + '">' + esc(it.cuerpo) +
      "</textarea>" + ayudaHtml() +
      '<div class="lg-actions"><button type="button" class="btn btn-accent" id="lg-guardar">' + T("admin.content.legal.guardar_btn", null, "Guardar") + '</button>' +
      '<span id="lg-msg" class="lg-msg"></span><span class="lg-actions-danger">' +
      (it.editado ? '<button type="button" id="lg-restaurar">' + T("admin.content.legal.restaurar_original", null, "Restaurar el original") + '</button>' : "") +
      "</span></div></div>";
    wire(it);
    if (pausedDraft && pausedDraft.id === it.id) {
      var textarea = document.getElementById("lg-cuerpo");
      if (textarea) {
        textarea.value = pausedDraft.value;
        textarea.setSelectionRange(pausedDraft.start, pausedDraft.end);
      }
      pausedDraft = null;
    }
  }

  function mensaje(texto, error) {
    var el = document.getElementById("lg-msg");
    if (!el) return;
    el.textContent = texto || "";
    el.className = "lg-msg" + (error ? " lg-msg--error" : "");
  }

  function wire(it) {
    var guardar = document.getElementById("lg-guardar");
    if (guardar) {
      guardar.addEventListener("click", function () {
        var epoch = lifecycleEpoch;
        var ta = document.getElementById("lg-cuerpo");
        if (!ta || !beginMutation()) return;
        guardar.disabled = true;
        mensaje(T("admin.content.legal.guardando", null, "Guardando…"));
        api("/api/admin/legal/" + encodeURIComponent(it.id), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cuerpo: ta.value })
        }).then(function (j) {
          if (!isCurrent(epoch)) throw cancelled();
          if (!j.ok || !j.item || j.item.id !== it.id || typeof j.item.cuerpo !== "string") {
            throw new Error(T("admin.content.legal.error_generico", null, "No se pudo completar la acción."));
          }
          LISTA_PROMISE = null;
          renderEditor(j.item);
          mensaje(T("admin.content.legal.guardado_punto", null, "Guardado."));
          toast(T("admin.content.legal.documento_actualizado", null, "Documento actualizado"));
          finishMutation(epoch);
        }).catch(function (e) {
          if (e.cancelled || !isCurrent(epoch)) return;
          finishMutation(epoch);
          if (String(e.message) === "401") return;
          mensaje(errText(e, T("admin.content.legal.error_generico", null, "No se pudo completar la acción.")), true);
        });
      });
    }
    var restaurar = document.getElementById("lg-restaurar");
    if (restaurar) {
      restaurar.addEventListener("click", function (e) {
        var epoch = lifecycleEpoch;
        e.preventDefault();
        if (pendingMutation) return;
        if (!window.confirm(T("admin.content.legal.confirmar_restaurar", null, "Se pierde lo editado y vuelve el texto original. ¿Seguimos?"))) return;
        if (!beginMutation()) return;
        api("/api/admin/legal/" + encodeURIComponent(it.id), { method: "DELETE" })
          .then(function (j) {
            if (!isCurrent(epoch)) throw cancelled();
            if (!j.ok || !j.item || j.item.id !== it.id || typeof j.item.cuerpo !== "string") {
              throw new Error(T("admin.content.legal.error_generico", null, "No se pudo completar la acción."));
            }
            LISTA_PROMISE = null;
            renderEditor(j.item);
            mensaje(T("admin.content.legal.restaurado_ok", null, "Se restauró el original."));
            finishMutation(epoch);
          }).catch(function (err) {
            if (err.cancelled || !isCurrent(epoch)) return;
            finishMutation(epoch);
            if (String(err.message) === "401") return;
            mensaje(errText(err, T("admin.content.legal.error_generico", null, "No se pudo completar la acción.")), true);
          });
      });
    }
  }

  function cargarDetalle(id) {
    var epoch = lifecycleEpoch;
    var known = docById(id);
    if (known) { renderEditor(known); return; }
    var sec = document.getElementById("sec-legal");
    // The band never names a document that is not on screen.
    var h1 = document.getElementById("adm-h1");
    if (h1) h1.textContent = T("admin.content.legal.titulo", null, "Términos y privacidad");
    sec.innerHTML = '<p class="adm-empty">' + T("admin.content.legal.cargando", null, "Cargando…") + '</p>';
    datosLista().then(function () {
      if (!isCurrent(epoch)) throw cancelled();
      var it = docById(id);
      if (!it) throw new Error(T("admin.content.legal.no_existe_documento", null, "No existe ese documento."));
      renderEditor(it);
    }).catch(function (e) {
      if (e.cancelled || !isCurrent(epoch)) return;
      if (String(e.message) === "401") return;
      sec.innerHTML = '<p class="adm-empty">' + T("admin.content.legal.no_se_pudo_cargar", null, "No se pudo cargar. ") +
        '<a href="' + esc(legalHref("")) + '"' + docAttr("") + '>' + T("admin.content.legal.titulo", null, "Términos y privacidad") + '</a></p>';
    });
  }

  function route() {
    inyectarCss();
    var el = document.getElementById("sec-legal");
    if (!el) return;
    lifecycleEpoch += 1;
    pendingMutation = null;
    LISTA_PROMISE = null;
    wireDirectNav();
    clearDirectStatus();
    var doc = directState();
    if (doc) cargarDetalle(doc);
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
  var textarea = document.getElementById("lg-cuerpo");
  if (mounted && textarea) pausedDraft = {
    id: directState(), value: textarea.value,
    start: textarea.selectionStart, end: textarea.selectionEnd
  };
  mounted = false;
  lifecycleEpoch += 1;
  LISTA_PROMISE = null;
  window.removeEventListener("popstate", onPopstate);
}
export var boot = mount;
