/* admin-control-fit.mjs — ancho real de los selects del panel.

   `appearance:none` quita también el cálculo de ancho que el navegador hacía
   para su flecha nativa. Cada `.adm-sel` recibe el ancho de su opción más
   larga, sumando padding, bordes y el espacio exclusivo del chevron. Los
   layouts deciden después si usan ese ancho o pasan el grupo a una grilla.

   Desde 2026-09-15 también arma el colapso de filtros en teléfono: cada fila
   (.cnt-toolbar-filters, .queue-controls) con selects recibe UN botón de
   icono (.adm-filter-toggle) con el conteo de filtros activos, y
   admin-direct.css oculta los selects hasta que la fila tiene .filters-open
   (≤640px). En escritorio el botón no se muestra y los filtros siguen
   inline. Solicitudes ya trae su botón propio: sólo se le cambia el rótulo
   de texto por el icono (el cableado sigue en admin-app.js).
*/
  var canvas = document.createElement("canvas");
  var ctx = canvas.getContext("2d");

  function px(v) {
    var n = parseFloat(v);
    return Number.isFinite(n) ? n : 0;
  }

  function fontOf(cs) {
    return [cs.fontStyle, cs.fontVariant, cs.fontWeight, cs.fontSize, cs.fontFamily]
      .filter(Boolean).join(" ");
  }

  export function fit(select) {
    if (!select || !select.matches || !select.matches("select.adm-sel")) return;
    var cs = getComputedStyle(select);
    ctx.font = fontOf(cs);
    // Product owner, 2026-09-15: a filter is as wide as the option it currently shows
    // (label + chevron right after it), not as wide as its longest option.
    var chosen = select.options && select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
    var text = ctx.measureText((chosen && chosen.text) || "").width;
    var chrome = px(cs.paddingLeft) + px(cs.paddingRight)
      + px(cs.borderLeftWidth) + px(cs.borderRightWidth);
    select.style.setProperty("--control-fit-width", Math.ceil(text + chrome + 2) + "px");
  }

  export function fitAll(root) {
    if (!root) return;
    if (root.matches && root.matches("select.adm-sel")) fit(root);
    if (root.querySelectorAll) {
      Array.prototype.forEach.call(root.querySelectorAll("select.adm-sel"), fit);
    }
  }

  // ---- Colapso de filtros en teléfono (2026-09-15) ----------------------
  //
  // La fila entera se re-renderiza con innerHTML en cada cambio de estado de
  // los módulos, así que el botón se (re)planta de forma idempotente desde
  // boot() y desde el MutationObserver. El estado abierto se recuerda POR
  // SECCIÓN (el ancestro [id^=sec-]) para sobrevivir a esos re-renders.
  var FILTER_ROW = ".cnt-toolbar-filters, .queue-controls";
  var FOLDED_CHECKBOX = ".queue-free-chk";
  // A select marked .adm-picker chooses WHAT the screen shows («Mis
  // pacientes»: the doctor), it is not a filter: never folded, never counted.
  function plegables(row) {
    return Array.prototype.filter.call(row.querySelectorAll("select.adm-sel"), function (sel) {
      return (" " + (sel.className || "") + " ").indexOf(" adm-picker ") < 0;
    });
  }
  var OPEN_ROWS = new Map();
  var I18n = null;
  if (typeof window !== "undefined" && typeof window.sanaImportStatic === "function") {
    window.sanaImportStatic("platform/i18n.mjs").then(function (m) { I18n = m; }).catch(function () {});
  }
  function labelFiltros() {
    return I18n ? I18n.t("admin.queue.toolbar.filtros_aria", null, "Filtros") : "Filtros";
  }
  // Embudo (funnel): el icono universal de "filtros". Es el archivo Icons8
  // (sf-regular `filter`, static/icons/filter-sf-regular.svg) que
  // admin-direct.css pinta como máscara con currentColor, así hereda el color
  // del botón, incluido el estado .on — nunca un svg dibujado en línea (regla 04.10).
  function iconoFiltro() {
    var span = document.createElement("span");
    span.className = "adm-filter-icon";
    span.setAttribute("aria-hidden", "true");
    return span;
  }
  function filaKey(row, index) {
    var owner = row.closest && (row.closest('[id^="sec-"]') || row.closest("section[id]"));
    return owner && owner.id ? owner.id : "pos:" + index;
  }
  function contarActivos(row) {
    var n = 0;
    plegables(row).forEach(function (sel) {
      if (sel.selectedIndex > 0) n++;
    });
    return n;
  }
  function actualizarBadge(row, btn) {
    var badge = btn.querySelector(".adm-filter-count");
    if (!badge) return;
    var n = contarActivos(row);
    badge.textContent = String(n);
    badge.hidden = !n;
  }

  export function enhanceFilterBars(root) {
    if (!root || !root.querySelectorAll) return;
    // Solicitudes: el botón ya existe ("Filtros" + badge, admin-app.js lo
    // cablea y cuenta); en teléfono el rótulo de texto se va y queda el
    // icono solo. El aria-label del botón conserva el término i18n.
    Array.prototype.forEach.call(root.querySelectorAll(".adm-filter-toggle .adm-filter-label"), function (label) {
      if (label.parentNode) label.parentNode.replaceChild(iconoFiltro(), label);
    });
    Array.prototype.forEach.call(root.querySelectorAll(FILTER_ROW), function (row, index) {
      if (!row.querySelectorAll) return;
      // «Mostrar horarios libres» waits behind the funnel too: on a doctor's
      // one-service day it is the only thing there, and without the funnel
      // it could not be reached at all.
      if (!row.querySelector(FOLDED_CHECKBOX) && !plegables(row).length) return;
      if (row.querySelector("[data-filter-toggle]")) return; // idempotente
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "adm-filter-toggle";
      btn.setAttribute("data-filter-toggle", "");
      btn.setAttribute("aria-expanded", "false");
      btn.setAttribute("aria-label", labelFiltros());
      btn.appendChild(iconoFiltro());
      var badge = document.createElement("span");
      badge.className = "adm-filter-count";
      badge.hidden = true;
      btn.appendChild(badge);
      // El botón va pegado al buscador: en teléfono esa pareja ES el renglón
      // visible de la fila colapsada.
      var search = row.querySelector('input[type="search"], .cnt-search, .adm-search');
      if (search && search.parentNode) search.parentNode.insertBefore(btn, search.nextSibling);
      else row.insertBefore(btn, row.firstChild);
      var key = filaKey(row, index);
      if (OPEN_ROWS.get(key)) {
        row.classList.add("filters-open");
        btn.setAttribute("aria-expanded", "true");
      }
      btn.addEventListener("click", function () {
        var open = !row.classList.contains("filters-open");
        row.classList.toggle("filters-open", open);
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        OPEN_ROWS.set(key, open);
      });
      actualizarBadge(row, btn);
    });
  }

  // Cuando algo reescribe el TEXTO de una <option> ya montada (p. ej.
  // counts() en admin.html hace `opt.textContent = "Pendientes (7)"` sobre
  // el <option> de Solicitudes DESPUÉS del primer render), la mutación que
  // llega acá tiene como target ESE <option> — nunca el <select> que lo
  // contiene. `fitAll` sólo mira hacia abajo (root y sus descendientes), así
  // que ese registro no encontraba ningún "select.adm-sel" y el ancho medido
  // se quedaba con el de la etiqueta vieja, más corta ("Pendientes" en vez
  // de "Pendientes (7)"): el texto entraba en el <select> pero salía
  // recortado bajo la flecha. Subir hasta el <select> dueño arregla la causa
  // (el <select> se vuelve a medir con la opción real) en vez de agrandar el
  // control a mano.
  function ownerSelect(node) {
    var el = node && node.nodeType === 1 ? node : (node && node.parentElement);
    return el && el.closest ? el.closest("select.adm-sel") : null;
  }

  export function boot() {
    fitAll(document);
    enhanceFilterBars(document);
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        fitAll(record.target);
        enhanceFilterBars(record.target);
        Array.prototype.forEach.call(record.addedNodes || [], function (node) {
          fitAll(node);
          enhanceFilterBars(node);
        });
        var owner = ownerSelect(record.target);
        if (owner) fit(owner);
      });
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
    document.addEventListener("change", function (event) {
      fit(event.target);
      // Un select que cambió puede activar/desactivar un filtro: el badge
      // del botón de la fila sigue contando selectedIndex > 0.
      var row = event.target && event.target.closest ? event.target.closest(FILTER_ROW) : null;
      if (!row) return;
      var btn = row.querySelector("[data-filter-toggle]");
      if (btn) actualizarBadge(row, btn);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
