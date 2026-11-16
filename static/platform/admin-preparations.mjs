/* Native, lifecycle-owned controller for /admin/preparations.  Import is inert. */
const mounts = new WeakMap();
const styles = new WeakMap();
// Shared-title ownership is per ELEMENT, never a module flag: the record
// keeps the pre-ownership snapshot plus what the current owner last wrote,
// so a takeover or an outside rewrite retires an owner without that owner's
// disposer ever restoring over the newer state.
const titleOwners = new WeakMap();
const TITLE_CLASS = "cnt-h1";

export function mount(host, options) {
  if (!host) throw new Error("Preparations host is required");
  if (mounts.has(host)) mounts.get(host)();
  options = options || {};
  var disposed = false, generation = 0, readCtl = null, mutation = null, table = null, cssRecord = null;
  var Prep, I18n, Session, Table, items = {}, order = [], routeId = "", newEditor = false, draftEpoch = 0;
  var listeners = [];
  // The entry lends the shared shell's #adm-h1 as options.titleElement: while
  // this mount is alive it is the screen's only heading, restored on disposal.
  // Without one the host renders its own standalone h1.
  var titleEl = options.titleElement && typeof options.titleElement === "object" ? options.titleElement : null;
  var titleToken = {};
  // Reserve before async imports/reads: an older host's late render cannot
  // take ownership back merely because it finishes after the new mount.
  if (titleEl) {
    var previousTitle = titleOwners.get(titleEl);
    var titleRecord = previousTitle || {
      text: titleEl.textContent, className: titleEl.className,
      hadClass: String(titleEl.className || "").split(/\s+/).includes(TITLE_CLASS)
    };
    titleRecord.owner = titleToken;
    titleOwners.set(titleEl, titleRecord);
  }
  // Labels resolve through t() at CALL time — cols() only ever runs from a
  // render, which the mount's ready promise keeps past I18n readiness, so a
  // language switch never leaves a mount-time Spanish snapshot behind.
  // No `max` on the name: a long word list beside it shrinks first, and a cap
  // here cut «MAPA / Presurometría (monitoreo ambulat…» at 1440 while the
  // list took the room.
  function cols() { return [
    { key: "nombre", label: t("admin.content.preparations.col_name", "Preparación"), min: 190, flex: 1.2 },
    { key: "resumen", label: t("admin.content.preparations.col_summary", "En una línea"), min: 220, flex: 2 },
    { key: "match", label: t("admin.content.preparations.col_match", "Se reconoce por"), min: 160, flex: 1.4 },
    { key: "ayuno", label: t("admin.content.preparations.col_ayuno", "Ayuno"), min: 62, flex: 0, numeric: true }
  ]; }

  function listView(on) { if (typeof host.setAttribute !== "function") return; if (on) host.setAttribute("data-list-view", "1"); else host.removeAttribute("data-list-view"); }
  function alive() { return !disposed && host.isConnected !== false; }
  function owns(g) { return alive() && g === generation; }
  function d() { return (typeof window !== "undefined" && window.document) || globalThis.document; }
  function w() { return (typeof window !== "undefined" && window) || globalThis; }
  function t(key, fallback) { return I18n.t(key, null, fallback); }
  // A failed call reads through i18n.mjs apiError, never the raw code (TASK-48).
  function errText(raw, fallback) { return I18n && I18n.apiError ? I18n.apiError(raw, fallback) : fallback; }
  function esc(v) { return String(v == null ? "" : v).replace(/[&<>\"']/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]; }); }
  function lines(v) { return (v || []).join("\n"); }
  function split(v) { return String(v || "").split("\n").map(function (x) { return x.trim(); }).filter(Boolean); }
  function add(target, event, fn) { target.addEventListener(event, fn); listeners.push([target, event, fn]); }
  function message(text, error) { var el = host.querySelector("#pa-msg"); if (el) { el.textContent = text || ""; el.className = "pa-msg" + (error ? " pa-msg--error" : ""); } }
  function current(g, ctl) { return owns(g) && (!ctl || readCtl === ctl); }
  function setTitle(text) {
    if (!titleEl) return;
    var rec = titleOwners.get(titleEl);
    if (!alive() || !rec || rec.owner !== titleToken) return;
    titleEl.textContent = text;
    if (!String(titleEl.className || "").split(/\s+/).includes(TITLE_CLASS)) {
      titleEl.className = titleEl.className ? titleEl.className + " " + TITLE_CLASS : TITLE_CLASS;
    }
    rec.lastText = text; rec.lastClass = titleEl.className;
  }
  // One screen header (the product owner, 2026-11-08): with the shell's title lent, «Agregar
  // una preparación» sits at the right end of the title's own line
  // (.adm-title-action, admin-direct.css), like «Nuevo paciente». A null
  // label only takes it away; without a real title row it stays in the host.
  function titleAction(label) {
    var row = titleEl && titleEl.parentNode, doc = d();
    if (!row || typeof row.insertBefore !== "function" || !doc || typeof doc.getElementById !== "function") return null;
    var old = doc.getElementById("pa-nueva");
    if (old && old.parentNode === row) row.removeChild(old);
    if (label == null || !alive()) return null;
    var button = doc.createElement("button");
    button.type = "button";
    button.id = "pa-nueva";
    button.className = "btn btn-accent adm-title-action";
    button.textContent = label;
    var bell = row.querySelector && row.querySelector(".alert-bell-wrap");
    row.insertBefore(button, bell || null);
    return button;
  }
  function releaseTitle() {
    titleAction(null);
    if (!titleEl) return;
    var rec = titleOwners.get(titleEl);
    if (!rec || rec.owner !== titleToken) return;
    titleOwners.delete(titleEl);
    // Text and class ownership are independent: preserve external additions,
    // remove only our class, and never overwrite another surface's title.
    if (titleEl.textContent === rec.lastText) titleEl.textContent = rec.text;
    var originalOwnedClass = rec.hadClass ? rec.className : (rec.className ? rec.className + " " + TITLE_CLASS : TITLE_CLASS);
    if (titleEl.className === originalOwnedClass) titleEl.className = rec.className;
    else if (!rec.hadClass) titleEl.className = String(titleEl.className || "")
      .split(/\s+/).filter(function (name) { return name && name !== TITLE_CLASS; }).join(" ");
  }
  function importStatic(name) { var url = w().SANA_STATIC && w().SANA_STATIC[name]; return url ? import(url) : Promise.reject(new Error("Missing Preparations dependency: " + name)); }
  function check(mod, name, fn) { if (!mod || !fn(mod)) throw new Error("Invalid Preparations dependency: " + name); return mod; }

  function injectCss() {
    var doc = d(), record = styles.get(doc); if (record) { record.count += 1; cssRecord = record; return; }
    var existing = doc.getElementById("prep-adm-css");
    if (existing) { cssRecord = { el: existing, count: 1, created: false }; styles.set(doc, cssRecord); return; }
    var css = doc.createElement("style"); css.id = "prep-adm-css";
    css.textContent = ".pa-edit{max-width:760px}.pa-campo{margin:0 0 22px}.pa-lab{display:block;font-size:14px;font-weight:650;margin:0 0 6px}.pa-nota{display:block;font-weight:400;color:var(--text,#1c1c1c);font-size:16px;margin:2px 0 7px}.pa-in,.pa-ta{width:100%;box-sizing:border-box;padding:10px 12px;font-size:15px;line-height:1.55;font-family:inherit;border:1px solid var(--border,#d8d8d2);border-radius:9px;background:var(--card,#fff);color:var(--text,#1c1c1c)}.pa-ta{resize:vertical;min-height:76px}.pa-in--corto{width:9ch}.pa-ayuno{border:1px solid var(--border,#e4e4e0);border-radius:11px;padding:15px 16px}.pa-ayuno-row,.pa-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.pa-check{display:flex;align-items:flex-start;gap:8px;font-size:15px;line-height:22px}.pa-actions-danger{margin-left:auto;display:flex;gap:14px}.pa-msg--error{color:var(--danger,#b3261e)}.pa-badge{font-size:13px}.pa-badge--editado{color:var(--warn)}.pa-badge--off{color:var(--danger,#b3261e)}";
    // The table engine selects dropped columns; the surface supplies the
    // matching cell selectors. Zero-width colgroups alone still leak ink.
    css.textContent += [
      "#sec-preparaciones .pa-oc-resumen :is(th,td):nth-child(2){display:none}",
      "#sec-preparaciones .pa-oc-match :is(th,td):nth-child(3){display:none}",
      "#sec-preparaciones .pa-oc-ayuno :is(th,td):nth-child(4){display:none}",
      "#sec-preparaciones .cnt-h1{font-size:24px;line-height:1.25}",
      // No inset of its own: the content wrap (and on the phone the shared
      // section list in admin-direct.css) puts the screen on --adm-gutter.
      "#sec-preparaciones .adm-h1row{padding-inline:0;flex-wrap:wrap}",
      "#sec-preparaciones :is(.pa-in,.pa-ta):focus{outline:2px solid var(--accent,#567b42);outline-offset:-1px}",
      "#sec-preparaciones :is(.pa-in,.pa-ta,.pa-check){font-size:16px}",
      "#sec-preparaciones .btn{min-height:44px;font-size:var(--adm-btn-font,14px)}",
      // The two checkbox labels are themselves the hit area: floor only the
      // checkbox-bearing .pa-check (never the hours label) at 44x44px.
      "#sec-preparaciones .pa-check:has(input[type=checkbox]){min-height:44px;min-width:44px;align-items:center}",
      "#sec-preparaciones .pa-actions-danger{flex-wrap:wrap}",
      // «Se reconoce por»: whole synonyms on one line, the rest behind the
      // engine's clip popover and the title; never «extracci…» (A9, 08.11).
      "#sec-preparaciones .pa-syn{white-space:normal;max-height:1.5em;line-height:1.5;overflow:hidden}",
      "#sec-preparaciones .pa-syn-k{white-space:nowrap}",
    ].join("");
    doc.head.appendChild(css); cssRecord = { el: css, count: 1, created: true }; styles.set(doc, cssRecord);
  }
  // Four of the five section helpers restated their own label; only avisar
  // carries instruction the label cannot (allergies, pregnancy, medication).
  // The other four keys remain in the catalogs; no localization data is deleted.
  function help(key) { return key === "avisar"
    ? t("admin.content.preparations.help_avisar", "Lo que la persona tiene que contar cuando llega: alergias, embarazo, medicación.")
    : ""; }
  function status(it) { if (it._borrado) return '<span class="pa-badge pa-badge--off">' + esc(t("admin.content.preparations.status_disabled", "Deshabilitada")) + "</span>"; if (it._origen === "db") return '<span class="pa-badge pa-badge--editado">' + esc(t("admin.content.preparations.status_new", "Nueva")) + "</span>"; if (it._origen === "json+db") return '<span class="pa-badge pa-badge--editado">' + esc(t("admin.content.preparations.status_edited", "Editada")) + "</span>"; return ""; }
  // No fasting = an empty cell: a column of «No» hid the few rows that do ask for it.
  function fasting(it) { var a = it.ayuno || {}; return !a.required ? "" : (a.horas ? a.horas + " h" : "Sí"); }
  function renderList() {
    if (!alive() || routeId || newEditor) return;
    if (!order.length) return renderEditor("");
    var heading = t("admin.content.preparations.title", "Cómo prepararse");
    // The list takes the page's whole width, like the catalog lists: at 1440
    // the 1040 px page cut long names with 185 px empty to its right.
    listView(true);
    // A lent title carries the heading in the shared band: the row keeps
    // only its action, and no second h1 enters the host.
    var addLabel = t("admin.content.preparations.new_item_title", "Nueva preparación");
    var inTitle = titleAction(addLabel);
    host.innerHTML = (inTitle ? "" : '<div class="adm-h1row">' + (titleEl ? "" : '<h1 class="cnt-h1">' + esc(heading) + '</h1>') + '<button class="btn" type="button" id="pa-nueva">' + esc(addLabel) + '</button></div>') + '<div class="cnt-table-wrap"><table class="cnt-table"><thead><tr>' + cols().map(function (c) { return '<th class="cnt-th' + (c.numeric ? " cnt-numeric" : "") + '">' + esc(c.label) + "</th>"; }).join("") + '</tr></thead><tbody>' + order.map(function (id) { var it = items[id], all = (it.match || []).map(String), ks = all.slice(0, 5).map(function (k) { return '<span class="pa-syn-k">' + esc(k) + "</span>"; }).join(" · "); if (all.length > 5) ks += " …"; return '<tr><td class="cnt-td-link"><a href="/admin/preparations?item=' + encodeURIComponent(id) + '">' + esc(it.nombre || id) + "</a> " + status(it) + "</td><td>" + esc(it.resumen || "") + "</td><td>" + (all.length ? '<div class="pa-syn" title="' + esc(all.join(" · ")) + '">' + ks + "</div>" : "") + '</td><td class="cnt-numeric">' + esc(fasting(it)) + "</td></tr>"; }).join("") + "</tbody></table></div>";
    setTitle(heading);
    var n = inTitle || host.querySelector("#pa-nueva"); if (n) add(n, "click", openNew);
    if (table && table.dispose) table.dispose();
    table = Table.crear({ clave: "preparaciones", selector: "#sec-preparaciones table", tablas: function () { return host.querySelectorAll("table"); }, cols: cols, apilado: function () { return false; }, drop: ["match", "ayuno", "resumen"], dropClase: function (k) { return "pa-oc-" + k; } });
    if (table && table.programarFit) table.programarFit();
  }
  function field(key, label, note, value) { return '<div class="pa-campo"><label class="pa-lab" for="pa-' + key + '">' + esc(label) + (note ? '<span class="pa-nota">' + esc(note) + "</span>" : "") + '</label><textarea class="pa-ta" id="pa-' + key + '">' + esc(value) + "</textarea></div>"; }
  function renderEditor(id) {
    titleAction(null);
    listView(false);
    if (table && table.dispose) table.dispose();
    table = null;
    if (!alive()) return; routeId = id || ""; if (id && !items[id]) { host.innerHTML = '<p class="adm-empty">' + esc(t("admin.content.preparations.not_found", "No se encontró esta preparación.")) + '</p>'; setTitle(t("admin.content.preparations.title", "Cómo prepararse")); return; } var it = id ? items[id] : {}, fresh = !id, ay = it.ayuno || {};
    var heading = fresh ? t("admin.content.preparations.new_item_title", "Nueva preparación") : (it.nombre || id);
    // The breadcrumb keeps only the parent link: the shared heading already
    // names the current item, so repeating it here was the small-print rule.
    host.innerHTML = '<p class="cnt-crumb"><a href="/admin/preparations">' + esc(t("admin.content.preparations.title", "Cómo prepararse")) + '</a></p>' + (titleEl ? "" : '<h1 class="cnt-h1">' + esc(heading) + '</h1>') + '<div class="pa-edit"><div class="pa-campo"><label class="pa-lab" for="pa-nombre">' + esc(t("admin.content.preparations.field_name_label", "Nombre")) + '</label><input class="pa-in" id="pa-nombre" value="' + esc(it.nombre || "") + '"></div><div class="pa-campo"><label class="pa-lab" for="pa-resumen">' + esc(t("admin.content.preparations.field_summary_label", "En una línea")) + '</label><input class="pa-in" id="pa-resumen" value="' + esc(it.resumen || "") + '"></div>' + field("match", t("admin.content.preparations.field_match_label", "Se reconoce por"), t("admin.content.preparations.field_match_hint", "Una palabra por línea. Con cualquiera de éstas en el nombre del servicio o en la orden que sube la persona, se le muestra esta preparación. Sin tildes y en minúscula: una orden escaneada las pierde."), lines(it.match)) + '<div class="pa-campo"><span class="pa-lab">' + esc(t("admin.content.preparations.ayuno_label", "Ayuno")) + '</span><div class="pa-ayuno"><div class="pa-ayuno-row"><label class="pa-check"><input type="checkbox" id="pa-ay-req"' + (ay.required ? " checked" : "") + "> " + esc(t("admin.content.preparations.ayuno_required", "Se hace en ayunas")) + "</label>" + '<label class="pa-check">' + esc(t("admin.content.preparations.ayuno_hours_label", "Horas ")) + '<input class="pa-in pa-in--corto" id="pa-ay-horas" inputmode="numeric" value="' + esc(ay.horas == null ? "" : ay.horas) + '"></label><label class="pa-check"><input type="checkbox" id="pa-ay-agua"' + (ay.agua_permitida !== false ? " checked" : "") + "> " + esc(t("admin.content.preparations.ayuno_water", "Puede tomar agua")) + "</label>" + '</div><label class="pa-lab" for="pa-ay-nota">' + esc(t("admin.content.preparations.ayuno_note_label", "Aclaración")) + '</label><textarea class="pa-ta" id="pa-ay-nota" rows="2">' + esc(ay.nota || "") + "</textarea></div></div>" + Prep.SECCIONES.map(function (s) { return field(s[0], s[1], help(s[0]), lines(it[s[0]] || (s[0] === "antes" ? it.preparacion : []))); }).join("") + '<div class="pa-actions"><button class="btn btn-primary" type="button" id="pa-guardar">' + esc(t("admin.content.preparations.save_button", "Guardar")) + '</button><span class="pa-msg" id="pa-msg" role="status" aria-live="polite"></span>' + (fresh ? "" : '<span class="pa-actions-danger">' + (it._origen === "json+db" ? '<button class="btn btn-quiet" type="button" id="pa-restaurar">' + esc(t("admin.content.preparations.restore_link", "Restaurar el original")) + '</button>' : "") + (it._borrado ? '<button class="btn btn-quiet" type="button" id="pa-habilitar">' + esc(t("admin.content.preparations.enable_link", "Habilitar")) + '</button>' : '<button class="btn btn-quiet" type="button" id="pa-deshabilitar">' + esc(t("admin.content.preparations.disable_button", "Deshabilitar")) + '</button>') + "</span>") + "</div></div>";
    setTitle(heading);
    function read() { var out = {}; Object.keys(it).forEach(function (k) { if (k[0] !== "_") out[k] = it[k]; }); out.nombre = host.querySelector("#pa-nombre").value.trim(); out.resumen = host.querySelector("#pa-resumen").value.trim(); out.match = split(host.querySelector("#pa-match").value); var h = host.querySelector("#pa-ay-horas").value.trim(); out.ayuno = Object.assign({}, it.ayuno || {}, { required: host.querySelector("#pa-ay-req").checked, horas: h === "" ? null : h, agua_permitida: host.querySelector("#pa-ay-agua").checked, nota: host.querySelector("#pa-ay-nota").value.trim() || null }); Prep.SECCIONES.forEach(function (s) { out[s[0]] = split(host.querySelector("#pa-" + s[0]).value); }); delete out.preparacion; return out; }
    var save = host.querySelector("#pa-guardar");
    if (save) add(save, "click", function () {
      var before = draftEpoch;
      mutate(fresh ? "POST" : "PUT", fresh ? "/api/admin/content/preparations" : "/api/admin/content/preparations/" + encodeURIComponent(id), read(), function (body) {
        if (!itemAck(body) || (fresh && (typeof body.item.id !== "string" || !body.item.id.trim()))) throw new Error(t("admin.content.preparations.generic_error", "No se pudo completar la acción."));
        // Persisted identity and draft paint have different ownership. Even
        // when newer typing prevents repaint, the next save must update the
        // created document instead of POSTing another one.
        if (fresh) {
          id = body.item.id; fresh = false; routeId = id; newEditor = false;
          w().history.pushState({}, "", "/admin/preparations?item=" + encodeURIComponent(id));
        }
        it = body.item;
        if (before === draftEpoch) refresh();
      });
    });
    function action(selector, url) { var el = host.querySelector(selector); if (el) add(el, "click", function () { var ask = w().confirm; if (typeof ask === "function" && !ask(t("admin.content.catalog.common.confirm_restore", "¿Restaurar el original? Se pierde la edición hecha desde el admin."))) return; var before = draftEpoch; mutate("POST", url, null, function (body) { if (!itemAck(body)) throw new Error(t("admin.content.preparations.generic_error", "No se pudo completar la acción.")); if (before === draftEpoch) refresh(); }); }); }
    action("#pa-restaurar", "/api/admin/content/preparations/" + encodeURIComponent(id) + "/restaurar"); action("#pa-habilitar", "/api/admin/content/preparations/" + encodeURIComponent(id) + "/restaurar");
    var del = host.querySelector("#pa-deshabilitar"); if (del) add(del, "click", function () { var ask = w().confirm; if (typeof ask === "function" && !ask(t("admin.content.catalog.common.confirm_disable", "¿Deshabilitar esta preparación? Deja de mostrarse."))) return; var before = draftEpoch; mutate("DELETE", "/api/admin/content/preparations/" + encodeURIComponent(id), null, function (body) { if (!body || body.ok !== true || !Number.isInteger(body.turnos) || body.turnos < 0 || typeof body.conservado !== "boolean") throw new Error(t("admin.content.preparations.generic_error", "No se pudo completar la acción.")); if (before === draftEpoch) refresh(); }); });
  }
  function itemAck(body) { return body && body.ok === true && body.item && typeof body.item === "object" && !Array.isArray(body.item); }
  function outcome(url, opts) { return fetch(url, Object.assign({ credentials: "same-origin" }, opts || {})).then(function (r) { return r.json().then(function (j) { return { r: r, j: j }; }, function () { return { r: r, j: null }; }); }); }
  function classify(x, g, ctl) { var live = function () { return current(g, ctl); }; if (!live()) return Promise.resolve(false); if (x.r.status !== 401 && x.r.status !== 403) return Promise.resolve(live()); return Session.confirmar(x.r, x.j, live).then(function (moved) { return !moved && live(); }); }
  function load() { var g = generation; if (readCtl) readCtl.abort(); var ctl = readCtl = new AbortController(); return outcome("/api/admin/content/preparations", { signal: ctl.signal }).then(function (x) { if (!current(g, ctl)) return false; return classify(x, g, ctl).then(function (go) { if (!go || !current(g, ctl)) return false; if (!x.r.ok || !x.j || !Array.isArray(x.j.items)) throw new Error(t("admin.content.preparations.load_error", "No se pudo cargar.")); items = {}; order = []; x.j.items.forEach(function (it) { if (it && it.id != null) { items[it.id] = it; order.push(it.id); } }); return true; }); }).catch(function (err) {
    if (!current(g, ctl) || (err && err.name === "AbortError")) return false;
    var text = errText(err, t("admin.content.preparations.load_error", "No se pudo cargar."));
    // A completed mutation's refresh is not a new route. Keep the editor
    // usable if that read fails; do not replace saved fields with a dead end.
    if (host.querySelector("#pa-guardar")) message(text, true);
    else {
      host.innerHTML = '<p class="adm-empty" role="alert">' + esc(text) + '</p><button class="btn" type="button" id="pa-retry">' + esc(t("admin.content.common.reload", "Recargar")) + '</button>';
      var retry = host.querySelector("#pa-retry");
      add(retry, "click", function () {
        if (owns(g) && host.querySelector("#pa-retry") === retry) refresh();
      });
    }
    return false;
  }); }
  function refresh() { var target = routeId, fresh = newEditor, draft = draftEpoch; return load().then(function (loaded) { if (loaded && alive() && draft === draftEpoch && target === routeId && fresh === newEditor) target ? renderEditor(target) : (fresh ? renderEditor("") : renderList()); }); }
  function navigate(id, push) {
    if (!alive()) return;
    generation += 1; routeId = id || ""; newEditor = false;
    if (push) w().history.pushState({}, "", "/admin/preparations" + (routeId ? "?item=" + encodeURIComponent(routeId) : ""));
    host.innerHTML = '<p role="status">' + esc(t("admin.content.preparations.loading", "Cargando…")) + '</p>';
    refresh();
  }
  function openNew() { if (!alive()) return; generation += 1; routeId = ""; newEditor = true; renderEditor(""); }
  function mutationError(body) {
    var error = body && body.error;
    var generic = t("admin.content.preparations.generic_error", "No se pudo completar la acción.");
    // La demo de solo lectura tiene su frase (TASK-48, apiError).
    if (error === "demo_solo_lectura") return errText(body, generic);
    // Preserve meaningful server validation, never expose internal wire codes.
    return typeof error === "string" && error && !/^[a-z][a-z0-9_]*$/.test(error) ? error : generic;
  }
  function mutate(method, url, body, accept) { if (mutation || !alive()) return; var token = mutation = { g: generation, ctl: new AbortController() }; var saving = t("admin.content.preparations.saving", "Guardando…"); message(saving); outcome(url, { method: method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined, signal: token.ctl.signal }).then(function (x) { return classify(x, token.g).then(function (go) { if (!go || mutation !== token) return; if (!x.r.ok) throw new Error(mutationError(x.j)); accept(x.j); }); }).catch(function (err) { if (mutation === token && owns(token.g)) message(err.message, true); }).finally(function () { if (mutation === token) { mutation = null; var el = host.querySelector("#pa-msg"); if (owns(token.g) && el && el.textContent === saving) message(""); } }); }
  function pop() { if (!alive()) return; var p = new URLSearchParams(w().location.search).get("item") || ""; navigate(p, false); }
  function onDraftInput() {
    draftEpoch += 1;
    // Abort is advisory: retiring the token also denies late JSON, rejection,
    // or shared-session confirmation authority over the newly typed draft.
    if (readCtl) readCtl.abort();
    readCtl = null;
  }
  function click(event) { var a = event.target && event.target.closest && event.target.closest('a[href^="/admin/preparations"]'); if (!a || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); var u = new URL(a.getAttribute("href"), "https://local.invalid"); navigate(u.searchParams.get("item") || "", true); }
  function dispose() { if (disposed) return; disposed = true; generation += 1; if (readCtl) readCtl.abort(); if (mutation) mutation.ctl.abort(); listeners.forEach(function (x) { x[0].removeEventListener(x[1], x[2]); }); listeners = []; releaseTitle(); listView(false); if (table && table.dispose) table.dispose(); table = null; if (cssRecord && --cssRecord.count === 0) { if (cssRecord.created && cssRecord.el.parentNode) cssRecord.el.parentNode.removeChild(cssRecord.el); styles.delete(d()); } if (mounts.get(host) === dispose) mounts.delete(host); }
  mounts.set(host, dispose);
  var dependencies = Promise.all([importStatic("platform/prep.mjs"), importStatic("platform/i18n.mjs"), importStatic("platform/admin-session.mjs"), importStatic("platform/admin-table.mjs")]);
  // The page's load deadline ends when the dependencies are in; the first
  // reads after that are the screen's own waiting and failure states.
  dispose.loaded = dependencies.then(function () { return true; }, function () { return false; });
  dispose.ready = dependencies.then(function (mods) { if (!alive()) return; Prep = check(mods[0], "platform/prep.mjs", function (m) { return Array.isArray(m.SECCIONES); }); I18n = check(mods[1], "platform/i18n.mjs", function (m) { return typeof m.t === "function"; }); Session = check(mods[2], "platform/admin-session.mjs", function (m) { return typeof m.confirmar === "function"; }); Table = check(mods[3], "platform/admin-table.mjs", function (m) { return typeof m.crear === "function"; }); injectCss(); add(host, "click", click); add(host, "input", onDraftInput); add(w(), "popstate", pop); routeId = new URLSearchParams(w().location.search).get("item") || ""; return refresh(); }).catch(function (err) { var wasCurrent = alive(); dispose(); if (wasCurrent) { if (options.onError) options.onError(err); else host.innerHTML = '<p class="adm-empty">' + esc(err.message) + "</p>"; } throw err; });
  return dispose;
}
