/* static/platform/i18n.mjs — the canonical client-side string lookup, the
   JS half of core/i18n.py's {{t.*}} pipeline (phase 3a, 2026-07-30; native
   ES module cutover 2026-09-05). Platform-owned code lives in the
   static/platform/ nested bucket (row 1.1's "under its owning package"),
   registered in core/static_fingerprint.py's MIGRATED + CLIENT_LOADED and
   served at /static/platform/i18n.<hash8>.mjs — the same immutable-URL
   contract as every other canonical module. The root-level i18n.js classic
   shim and its window.SanaI18n global are physically retired with this
   cutover: there is no bridge and no classic twin.

   HOW A CONSUMER CALLS IT. Every consumer resolves the module's CURRENT
   fingerprinted URL through window.SANA_STATIC (the client manifest every
   shell inlines in <head>) — window.sanaImportStatic(name) in the admin
   shell, the identical fail-closed staticUrl/importStatic pair elsewhere,
   or a relative disk import in Node/Storybook — stores the namespace in a
   module-local var and awaits that promise at its boot/render boundary:

     var I18n = null;
     var i18nReady = <import mechanism>("platform/i18n.mjs")
       .then(function (m) { I18n = m; });
     function T(key, vars, fallback) {
       return I18n ? I18n.t(key, vars, fallback) : (fallback || key);
     }

   T() looks the namespace up on every call, so a module whose i18nReady has
   not settled yet renders the caller's fallback (the Spanish literal the
   call site carries), never a raw dotted key — but every boot/render path
   that paints translated text awaits i18nReady first, which is what keeps
   an English page's FIRST paint English and an early click correct.

   BOOTSTRAP. Auto-bootstraps SYNCHRONOUSLY from window.SANA_I18N.catalog at
   module evaluation — the server keeps inlining that payload (i18n.py's
   bootstrap_script()) in <head> as CONFIGURATION DATA (nav.js's language
   switcher reads mode/enabled from it too). Inline classic scripts in
   <head> run at parse; this module can only evaluate afterwards, so the
   catalog is always there before the first t() call. That is what makes
   t() safe at first paint and on a click that lands before any network
   settles — the async-fetch-only version could render a raw dotted key.

   Dropped with the classic shim: the window.SANA_I18N_CATALOG inline
   injection hook. It had no producer in production (only test harnesses
   set it); those harnesses now adopt their catalog through the real
   setCatalog() export instead. The fetch below survives for a page that
   carries no bootstrap at all. */

function applyVars(text, vars) {
  if (!vars) return text;
  var out = text;
  for (var k in vars) {
    if (Object.prototype.hasOwnProperty.call(vars, k)) {
      out = out.split("{" + k + "}").join(String(vars[k]));
    }
  }
  return out;
}

function makeT(catalog) {
  return function (key, vars, fallback) {
    // `catalog[key]` a secas trataba una traducción DELIBERADAMENTE vacía
    // ("" — un idioma que no lleva el prefijo que el castellano sí lleva,
    // p.ej. date.article en inglés) como si faltara, y caía al fallback
    // castellano ("el") en vez de respetar el "" real — mismo criterio que
    // i18n.py:raw() ya documenta ("una clave PRESENTE, no faltante") pero
    // que este lado nunca tuvo (bug real, demo, 2026-08-05: withWeekdayArticle
    // veía date.article="el" en inglés y capitalizaba mal el día).
    var has = catalog && Object.prototype.hasOwnProperty.call(catalog, key);
    var text = has ? catalog[key]
      : (typeof fallback === "string" ? fallback : key);
    return applyVars(text, vars);
  };
}

function activeLang() {
  // The bootstrap (i18n.py's bootstrap_script()) carries the request-resolved
  // language as CONFIGURATION DATA; <html lang> is the same server-resolved
  // value and serves pages without the bootstrap. Logic never keys on
  // rendered text — this is the wire value both were built from.
  try {
    var boot = (typeof window !== "undefined") && window.SANA_I18N;
    var lang = boot && boot.active;
    if (!lang) {
      var doc = (typeof window !== "undefined") && window.document;
      lang = doc && doc.documentElement && doc.documentElement.getAttribute("lang");
    }
    return (lang || "es").toLowerCase();
  } catch (e) {
    return "es";
  }
}

function bootCatalog() {
  var boot = (typeof window !== "undefined") && window.SANA_I18N;
  if (boot && boot.catalog && typeof boot.catalog === "object") return boot.catalog;
  return null;
}

var booted = bootCatalog();
var state = { lang: activeLang(), catalog: booted || {} };
state.t = makeT(state.catalog);

/* The frozen public snapshot of the page's language, taken at module
   evaluation (<html lang>, the server-resolved value). load() below may
   move the INTERNAL default, exactly like the classic shim did — this
   export deliberately does not follow it. Consumers that need the live
   document language read <html lang> themselves (week.js's activeLang,
   admin-prompts.js's idiomaActivo), which agrees with it by construction:
   the server renders both from the same request-scoped resolution. */
var lang = state.lang;

function t(key, vars, fallback) { return state.t(key, vars, fallback); }

/* Number agreement (05.11: the panel said «1 solicitudes», «1 libres»).
   ONE rule for every «N + noun» string, mirrored by core/i18n.py's
   plural_category()/tn_or(): the CLDR category of `n` in the page language
   (Intl.PluralRules — 1 in Spanish and English, 0 and 1 in French, 21 in
   Russian), "other" for anything that is not a number.

   tn(key, n, vars, one, other) reads `key_one` when the category is "one"
   and `key` otherwise; `{n}` is always filled (a caller's formatted
   vars.n wins). A `_one` text with no placeholder at all — a whole
   singular phrase («¿Aprobar la solicitud…?») — is used only for exactly
   1, so a language whose "one" also covers 0 or 21 never prints the
   singular phrase for them; a bare unit word («médico») serves every «one»
   count (singularFits). `one`/`other` are the Spanish fallbacks, like
   t()'s third argument. A «few» or «many» count reads `key_few`/`key_many`
   when the catalog has it — only Russian and Ukrainian do (2, 3, 4 and
   5–20 take different words); the French/Spanish «many» of a million finds none and
   stays on `key`. */
var PLACEHOLDER = /\{[A-Za-z_]+\}/;
var SENTENCE = /[.,;:?!¿¡]/;

/* May the `_one` text stand for `n`, whose category is «one»? A text with a
   placeholder, or a bare unit word (at most two words, no sentence
   punctuation: «médico», «turno» printed after the caller's number), for any
   such n — the 21 of Russian included; a whole singular phrase with no number
   in it only for exactly 1. Twin of core/i18n.py _singular_fits(). */
function singularFits(text, n) {
  if (pluralNumber(n) === 1 || PLACEHOLDER.test(text)) return true;
  return text.trim().split(/\s+/).length <= 2 && !SENTENCE.test(text);
}

function pluralNumber(n) {
  if (n === null || n === undefined || n === "" || typeof n === "boolean") return NaN;
  return typeof n === "number" ? n : Number(String(n).trim().replace(",", "."));
}

function pluralCategory(n, language) {
  var num = pluralNumber(n);
  if (!isFinite(num)) return "other";
  try {
    return new Intl.PluralRules(language || state.lang).select(num);
  } catch (e) {
    return Math.abs(num) === 1 ? "one" : "other";
  }
}

/* How a number is written in each shipped language: [thousands, decimal],
   grouping always on so «4.188» reads like «10.238» (Spanish CLDR leaves a
   four-digit number ungrouped and the panel printed «4188 de 10238» next to
   «4.185» — ui-expert 2026-11-15). The ONE implementation behind
   format.mjs numero()/porcentaje(); twin of core/i18n.py format_number(),
   parity-held by tests/fixtures/number-format.json. It lives here because
   tn() writes its {n} with it and format.mjs already imports this module. */
var NUM_SEPS = {
  en: [",", "."], ja: [",", "."], zh: [",", "."],
  fr: [" ", ","], ru: [" ", ","], uk: [" ", ","]
};

function formatNumber(n, decimals, language) {
  if (n === null || n === undefined || n === "" || typeof n === "boolean") return "";
  if (typeof n !== "number" && String(n).trim() === "") return "";
  var v = typeof n === "number" ? n : Number(String(n).trim());
  if (!isFinite(v)) return "";
  var d = decimals == null ? 1 : decimals;
  // Intl rounds the shortest decimal form half away from zero (1.005 →
  // 1.01), like the Decimal(repr()) ROUND_HALF_UP of the Python twin.
  var raw = v.toLocaleString("en-US", { useGrouping: false, minimumFractionDigits: 0, maximumFractionDigits: d });
  var neg = raw.charAt(0) === "-";
  if (neg) raw = raw.slice(1);
  var parts = raw.split(".");
  var whole = parts[0], frac = (parts[1] || "").replace(/0+$/, "");
  if (neg && !/[1-9]/.test(whole + frac)) neg = false;
  var root = String(language || state.lang || "es").toLowerCase().split("-")[0];
  var seps = NUM_SEPS[root] || [".", ","];
  return (neg ? "-" : "") + whole.replace(/\B(?=(\d{3})+(?!\d))/g, seps[0]) + (frac ? seps[1] + frac : "");
}

/* A percentage with its sign glued on («52%», the form the catalog already
   uses): one decimal only where it changes the reading — under 10 («4,3%»)
   and near 100, where rounding would claim «100%» for less than all
   («99,6%»). Twin of core/i18n.py format_percent(). */
function formatPercent(n, language) {
  if (n === null || n === undefined || n === "" || typeof n === "boolean") return "";
  var v = Number(n);
  if (!isFinite(v)) return "";
  var a = Math.abs(v);
  var d = a < 10 ? 1 : 0;
  // Short of 100 never reads «100%»: as many decimals as it takes (99,95%).
  while (a < 100 && d < 6 && Number(a.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: d })) >= 100) d++;
  return formatNumber(v, d, language) + "%";
}

function tn(key, n, vars, one, other) {
  // A numeric count is written in the page language's digits («1.214»); a
  // caller's own formatted vars.n still wins below.
  var all = { n: typeof n === "number" ? formatNumber(n, 1) : n };
  if (vars) {
    for (var k in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, k)) all[k] = vars[k];
    }
  }
  var category = pluralCategory(n);
  if (category === "few" || category === "many") {
    var formKey = key + "_" + category;
    if (Object.prototype.hasOwnProperty.call(state.catalog, formKey)) return applyVars(state.catalog[formKey], all);
  }
  if (category === "one") {
    var oneKey = key + "_one";
    var has = Object.prototype.hasOwnProperty.call(state.catalog, oneKey);
    var text = has ? state.catalog[oneKey] : (typeof one === "string" ? one : null);
    if (text !== null && singularFits(text, n)) return applyVars(text, all);
  }
  return state.t(key, all, other);
}

/* What a staff member reads when a panel call fails (TASK-48, 2026-10-26).
   The server answers with a machine code (`demo_solo_lectura`,
   `rol_no_autorizado`) or, in a few places, a sentence written for people.
   Screens used to print `body.error || fallback`, so the demo showed its
   internal code as a toast. Every panel screen reads the failure through
   here: a known code gets its translated sentence, any other code the
   screen's own fallback, a human sentence passes through. `raw` is the code,
   the response body ({error}) or a thrown Error. */
var API_ERRORS = {
  demo_solo_lectura: ["admin.shell.toast.demo_read_only",
    "Demo de solo lectura: esta acción está deshabilitada. Podés seguir recorriendo las pantallas."],
};

function apiError(raw, fallback) {
  var text = raw;
  if (raw && typeof raw === "object") {
    // A browser exception (no network, an HTML body parsed as JSON) is not
    // something the server said: the screen's sentence covers it.
    if (raw.name === "TypeError" || raw.name === "SyntaxError") return fallback;
    text = raw instanceof Error ? raw.message : raw.error;
  }
  text = typeof text === "string" ? text.trim() : "";
  var known = Object.prototype.hasOwnProperty.call(API_ERRORS, text) ? API_ERRORS[text] : null;
  if (known) return t(known[0], null, known[1]);
  if (!text || /^[A-Za-z0-9_.:\-]+$/.test(text)) return fallback;
  return text;
}

// `language` (optional) is the catalog's language, which tn()'s plural
// category follows — test harnesses adopt a Russian catalog this way.
function setCatalog(dict, language) {
  if (typeof language === "string" && language) state.lang = language.toLowerCase();
  state.catalog = (dict && typeof dict === "object") ? dict : {};
  state.t = makeT(state.catalog);
  return i18n;
}

function load(requested) {
  var target = requested || state.lang;
  var fetch_ = (typeof window !== "undefined") && window.fetch;
  if (!fetch_) return Promise.resolve(state.catalog);
  // Baseline preserved from the classic shim (2026-09-05 review): a failed
  // fetch — network error or non-ok response — adopts an EMPTY catalog and
  // still resolves. Pages render their fallback literals; nothing throws.
  return fetch_("/api/i18n/" + encodeURIComponent(target) + ".json")
    .then(function (r) { return r && r.ok ? r.json() : {}; })
    .catch(function () { return {}; })
    .then(function (data) {
      state.lang = target;
      setCatalog(data);
      return data;
    });
}

var i18n = {
  lang: lang,
  t: t,
  tn: tn,
  pluralCategory: pluralCategory,
  formatNumber: formatNumber,
  formatPercent: formatPercent,
  apiError: apiError,
  setCatalog: setCatalog,
  load: load,
};

// Every server-rendered page inlines its catalog (bootstrap_script()), so
// this fetch is the fallback for a page that doesn't — a fragment loaded on
// its own, a test harness. When it does run it is lazy and never blocks
// first paint.
if (!booted && (typeof window !== "undefined") && window.fetch) {
  load(state.lang);
}

export { i18n, t, tn, pluralCategory, lang, setCatalog, load, activeLang, apiError, formatNumber, formatPercent };
