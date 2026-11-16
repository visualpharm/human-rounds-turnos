/* One native-select builder for staff filters, and the one progressive-
   disclosure rule every staff list follows (empty → no data tools).

   Queue and request filters share a small ESM boundary rather than a browser
   global. It keeps their escaping, selected-value behavior, and native-select
   affordance consistent while each screen keeps its own data semantics. */
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}

export function select(spec) {
  var attrs = spec.attrs || {};
  var attrText = Object.keys(attrs).map(function (key) {
    return " " + key + '="' + esc(attrs[key]) + '"';
  }).join("");
  var options = (spec.options || []).map(function (option) {
    return '<option value="' + esc(option.value) + '"'
      + (String(option.value) === String(spec.value == null ? "" : spec.value) ? " selected" : "") + ">"
      + esc(option.label) + "</option>";
  }).join("");
  return '<select class="' + esc(spec.className || "adm-sel") + '"'
    + (spec.label ? ' aria-label="' + esc(spec.label) + '"' : "") + attrText + ">" + options + "</select>";
}

export function fill(selectEl, options, value, labelFor) {
  if (!selectEl) return "";
  var current = value == null ? selectEl.value : String(value);
  var first = selectEl.querySelector("option");
  var items = first ? [{ value: first.value, label: first.textContent }] : [];
  (options || []).forEach(function (option) {
    if (option && typeof option === "object") {
      items.push({ value: String(option.value || ""), label: option.label == null ? String(option.value || "") : String(option.label) });
    } else {
      items.push({ value: option, label: labelFor ? labelFor(option) : option });
    }
  });
  selectEl.innerHTML = items.map(function (option) {
    return '<option value="' + esc(option.value) + '">' + esc(option.label) + "</option>";
  }).join("");
  selectEl.value = current;
  return selectEl.value;
}

/* Progressive disclosure for every staff list (the product owner, 2026-10-26: «if there's
   no content, why to show all the ui? all over the admin panel»).

   Three states, decided once per render from the data, never from the DOM:
     · "empty"    — the screen has no rows at all: no search, no filters, no
                    column picker, no table header, no counters, no AI notice.
                    The title, ONE empty state that says what will appear here
                    and from where, and the screen's primary action remain.
     · "no-match" — there ARE rows, the filters hid them all: filters stay,
                    the empty state says so and offers «Quitar filtros».
     · "data"     — rows on screen.
   A screen marks its root with `data-disclosure` (applyDisclosure); the shell
   CSS (admin-direct.css) hides the toolbar band and the AI notice from that
   attribute, and each renderer simply skips its header row when nothing is
   listed. */
export function disclosure(o) {
  o = o || {};
  if (!(o.total > 0)) return "empty";
  if (!(o.shown > 0) && o.filtered) return "no-match";
  return "data";
}

export function applyDisclosure(root, state) {
  if (root && root.setAttribute) root.setAttribute("data-disclosure", state);
  return state;
}

/** Real choices a select offers: options with a non-empty value. */
export function choiceCount(selectEl) {
  if (!selectEl) return 0;
  return Array.prototype.filter.call(selectEl.querySelectorAll("option"), function (option) {
    return String(option.value || "") !== "";
  }).length;
}

/* Filters only for a list worth filtering (the product owner, 05.09 C1, again 21.10 and
   26.10: «fewer than ~10 records — do not draw filters at all… one shared
   helper»). A list shorter than FILTER_MIN_ROWS is read at a glance: no
   filter selects and no phone «Filtros» toggle. The search box is not a
   filter and is not governed by this. This is the ONE threshold — a screen
   passes its row count, never its own number. */
export var FILTER_MIN_ROWS = 10;

export function filtersWorth(rows) {
  return Number(rows) >= FILTER_MIN_ROWS;
}

/* The one decision for every staff filter: offered when the list is worth
   filtering (`rows`, see FILTER_MIN_ROWS) AND the data gives it at least two
   values to choose between (one doctor → no doctor filter). A filter that is
   already set is always offered, so it can be cleared. `rows` omitted = a
   choice that is not over a list (the state select that leads to the
   history): only the two-values rule applies. */
export function offerFilter(count, isSet, rows) {
  if (isSet) return true;
  return count >= 2 && (rows == null || filtersWorth(rows));
}

/* offerFilter() applied to a rendered select (sets `hidden`). `count`
   defaults to the select's own non-empty options; pass it when the options
   are a fixed list and the data decides how many of them can match. */
export function showIfChoice(selectEl, count, defaultValue, rows) {
  if (!selectEl) return false;
  var n = count == null ? choiceCount(selectEl) : count;
  var set = String(selectEl.value || "") !== String(defaultValue == null ? "" : defaultValue);
  var show = offerFilter(n, set, rows);
  selectEl.hidden = !show;
  return show;
}

/** The meaningful empty state: what will appear here, and from where. */
export function emptyStateHtml(o) {
  o = o || {};
  var action = o.action
    ? '<button type="button" class="btn' + (o.action.accent ? " btn-accent" : "") + '"'
      + Object.keys(o.action.attrs || {}).map(function (key) {
        return " " + key + '="' + esc(o.action.attrs[key]) + '"';
      }).join("") + ">" + esc(o.action.label) + "</button>"
    : "";
  return '<div class="adm-empty-state" data-empty-state="' + esc(o.kind || "empty") + '">'
    + '<p class="adm-empty-title">' + esc(o.title) + "</p>"
    + (o.text ? '<p class="adm-empty-text">' + esc(o.text) + "</p>" : "")
    + action + "</div>";
}

/** Filters hid every row: say so and offer the way back. */
export function noMatchHtml(o) {
  o = o || {};
  return emptyStateHtml({
    kind: "no-match",
    title: o.title,
    action: { label: o.clearLabel, attrs: Object.assign({ "data-clear-filters": "" }, o.clearAttrs || {}) }
  });
}

/* The AI notice (Decreto 742/2026, core/ai_register.py) goes with the content
   the AI produced, right before it — never as a plaque over a screen that has
   none. The screen decides `hasContent`; this owns the markup. */
export function aiNoticeHtml(o) {
  o = o || {};
  if (!o.hasContent) return "";
  return '<p class="adm-ai-notice" data-ai-notice="' + esc(o.kind || "clinical") + '">' + esc(o.text) + "</p>";
}

/** Return one stable, human-labelled option for every non-empty raw value. */
export function uniqueOptions(rows, valueFor, labelFor) {
  var values = new Map();
  (rows || []).forEach(function (row) {
    var value = String(valueFor(row) || "").trim();
    if (value && !values.has(value)) values.set(value, String(labelFor(row, value) || value));
  });
  return Array.from(values, function (entry) { return { value: entry[0], label: entry[1] }; })
    .sort(function (a, b) { return a.label.localeCompare(b.label, "es"); });
}
