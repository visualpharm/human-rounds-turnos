/* static_recover.js — what a page does when a lazy module cannot be fetched
   because the municipal portal gateway ended the session.

   salud.pinamar.gob.ar sits behind the portal gateway: any request without a
   live portal session (the /static files included) is answered by the gateway
   itself with 401 {"error":"unauthenticated","login":"<portal sign-in>"} and
   never reaches the app. A page that stays open past the session then fails
   its next dynamic import() with a bare TypeError. Here: probe one static file;
   on that exact 401 shape go once to the portal sign-in, coming back to this
   same page. Anything else (a 404, offline, the shared Basic Auth of a test
   site, no gateway at all) is left to the page's own error handling.

   Inlined by static_fingerprint.manifest_script() right after
   window.SANA_STATIC — it cannot be a static file: the file is behind the same
   401. Loop guard: one trip per five minutes per tab (sessionStorage). */
(function (w) {
  "use strict";
  var KEY = "sana:static-relogin";
  var QUIET_MS = 300000;
  var COALESCE_MS = 10000;
  var FAILED = /dynamically imported module|importing a module script failed|failed to load module script/i;
  var probe = null, probeAt = 0;

  function importFailure(x) {
    return FAILED.test(String((x && x.message) || x || ""));
  }

  function probeUrl() {
    var map = w.SANA_STATIC || {};
    return map["platform/i18n.mjs"] || map[Object.keys(map)[0]] || "";
  }

  function lastTrip() {
    try { return Number(w.sessionStorage.getItem(KEY)) || 0; } catch (e) { return 0; }
  }

  function signInUrl(body) {
    var login = body && body.login;
    if (typeof login !== "string") return "";
    try {
      var url = new URL(login);
      if (url.protocol !== "https:") return "";
      url.searchParams.set("return", w.location.href);
      return url.toString();
    } catch (e) { return ""; }
  }

  // Resolves true when the page is on its way to the portal sign-in.
  w.sanaRecoverStatic = function () {
    var now = Date.now();
    if (probe && now - probeAt < COALESCE_MS) return probe;
    var url = probeUrl();
    probeAt = now;
    if (!url || !w.fetch || now - lastTrip() < QUIET_MS) {
      probe = Promise.resolve(false);
      return probe;
    }
    probe = w.fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.status === 401 ? res.json() : null;
      })
      .then(function (body) {
        var target = signInUrl(body);
        if (!target) return false;
        try { w.sessionStorage.setItem(KEY, String(Date.now())); } catch (e) { /* no storage: the 5 min guard is off, one trip still */ }
        w.location.assign(target);
        return true;
      })
      .catch(function () { return false; });
    return probe;
  };

  w.addEventListener("unhandledrejection", function (ev) {
    if (importFailure(ev && ev.reason)) w.sanaRecoverStatic();
  });
  w.addEventListener("error", function (ev) {
    var t = ev && ev.target;
    if (t && t !== w && t.tagName === "SCRIPT" && t.type === "module") w.sanaRecoverStatic();
    else if (ev && importFailure(ev.message)) w.sanaRecoverStatic();
  }, true);
})(window);
