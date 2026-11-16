/* admin-session.mjs — Direct admin-page session boundary.
 *
 * A 401 alone is not enough to send someone to login: site Basic Auth and
 * transient upstream responses use the same status.  Only an admin API's
 * explicit {error:"no_autenticado"}, confirmed by /api/admin/whoami, proves
 * that the application session has ended. A live whoami response and a
 * non-JSON 401 stay local. The one exceptional 403 is the server's exact
 * {error:"2fa_requerido"} repair contract: it goes to Account's 2FA setup;
 * all other 403s stay local.
 *
 * Canonical ES module. Consumers import it through window.SANA_STATIC /
 * sanaImportStatic and await that promise before classifying a response.
 * No browser global, no classic twin, no copied implementation.
 */
"use strict";

var checking = null;
var nativeFetch = fetch;

function pageLocation() {
  if (typeof window !== "undefined" && window.location) return window.location;
  return location;
}

function noAutenticado(response, body, bodyKnown) {
  if (!response || response.status !== 401) return Promise.resolve(false);
  if (bodyKnown) return Promise.resolve(!!body && body.error === "no_autenticado");
  if (!response.clone) return Promise.resolve(false);
  return response.clone().json().then(function (parsed) {
    return !!parsed && parsed.error === "no_autenticado";
  }).catch(function () { return false; });
}

function needs2fa(response, body, bodyKnown) {
  if (!response || response.status !== 403) return Promise.resolve(false);
  if (bodyKnown) return Promise.resolve(!!body && body.error === "2fa_requerido");
  if (!response.clone) return Promise.resolve(false);
  return response.clone().json().then(function (parsed) {
    return !!parsed && parsed.error === "2fa_requerido";
  }).catch(function () { return false; });
}

export function nextValido(value) {
  if (typeof value !== "string" || !value) return false;
  var decoded = value;
  for (var i = 0; i < 3; i += 1) {
    try { decoded = decodeURIComponent(decoded); } catch (err) { return false; }
  }
  if (value.indexOf("//") >= 0 || value.indexOf("\\") >= 0 || /[\x00-\x1f]/.test(decoded)) return false;
  if (decoded.indexOf("//") >= 0 || decoded.indexOf("\\") >= 0 || decoded.indexOf("@") >= 0 || decoded.indexOf("..") >= 0) return false;
  if (value === "/admin") return true;
  return /^\/admin\/[a-zA-Z0-9\-_]+(?:\?[a-zA-Z0-9_.~%=&,+:\-]{1,256})?$/.test(value);
}

export function redirigir() {
  var screen = typeof window !== "undefined" ? window.SANA_ADMIN_SCREEN : undefined;
  if (!screen || screen === "login") return false;
  var loc = pageLocation();
  var target = String(loc.pathname || "") + String(loc.search || "");
  loc.href = nextValido(target) ? "/admin/login?next=" + encodeURIComponent(target) : "/admin/login";
  return true;
}

export function confirmar(response, parsedBody, isCurrent) {
  // A lifecycle owner may expire while body parsing or shared whoami is
  // pending. Share only the check, never the authority to navigate.
  var current = typeof isCurrent === "function" ? isCurrent : function () { return true; };
  var bodyKnown = parsedBody !== undefined;
  if (!response || !current()) return Promise.resolve(false);
  if (response.status === 403) {
    return needs2fa(response, parsedBody, bodyKnown).then(function (required) {
      if (!current()) return false;
      // Already on Account, the screen that repairs it: another request on
      // this page (a doctor's workspace load) answering 2fa_requerido must
      // not reload the page — it did, in a loop, and the screen flickered
      // white for a tester whose 2FA was required at once (2026-10-19).
      // Reported as no transition, so Account keeps its form on screen.
      if (required && String(pageLocation().pathname || "") === "/admin/account") return false;
      if (required) pageLocation().href = "/admin/account?setup=2fa";
      return required;
    });
  }
  if (response.status !== 401) return Promise.resolve(false);
  return noAutenticado(response, parsedBody, bodyKnown).then(function (declaredExpired) {
    if (!declaredExpired || !current()) return false;
    if (!checking) {
      checking = nativeFetch("/api/admin/whoami", { credentials: "same-origin" })
        .then(noAutenticado)
        .catch(function () { return false; })
        .finally(function () { checking = null; });
    }
    return checking.then(function (confirmed) {
      if (!current()) return false;
      if (confirmed) redirigir();
      return confirmed;
    });
  });
}

// Controllers call `confirmar(response)` explicitly at their own response
// boundary.  Do not monkey-patch fetch: an observer races the controller's
// local error handling and makes a role/demo 403 look like a session event.
