// nav-my-appointments.test.mjs — the account link of the public header always
// reads «Mi cuenta» (the product owner, 2026-10-29: siempre «Mi cuenta», revierte q708829,
// que lo cambiaba a «Mis turnos» con un turno por delante). The list of
// appointments lives inside the account, in its «Mis turnos» tab.
// Renders the real nav.mjs html(), same pattern as nav-chat-button.test.mjs.
import test from "node:test";
import assert from "node:assert/strict";
import * as Nav from "../static/platform/nav.mjs";

function render(state, path = "/") {
  globalThis.window = {};
  delete globalThis.document;
  return Nav.html(path, "", state);
}

const BASE = { loggedIn: false, hasSolicitudes: false, iaDown: false, iniciales: "PB", authMode: "password" };
const accountLink = html => html.match(/<a class="stznav-cuenta[^"]*" href="([^"]+)"[^>]*>[^]*?<span class="stznav-cuenta-label">([^<]*)<\/span><\/a>/);

test("signed in: the avatar link reads «Mi cuenta» whether or not an appointment is ahead", () => {
  for (const extra of [{}, { turnoProximo: true }, { hasSolicitudes: true }]) {
    const html = render({ ...BASE, loggedIn: true, ...extra });
    const m = accountLink(html);
    assert.ok(m, "account link rendered");
    assert.equal(m[2], "Mi cuenta");
    assert.doesNotMatch(html, />Mis turnos<\/span><\/a>/);
  }
});

test("signed out: no account link and no «Mis turnos»", () => {
  const html = render({ ...BASE, loggedIn: false, turnoProximo: true });
  assert.equal(accountLink(html), null);
  assert.doesNotMatch(html, />Mis turnos</);
  assert.match(html, /data-nav-key="ingresar"/);
});

test("never a separate «Mis turnos» link in the bar", () => {
  for (const path of ["/", "/account", "/doctors"]) {
    const html = render({ ...BASE, loggedIn: true, turnoProximo: true }, path);
    assert.doesNotMatch(html, /data-nav-key="turnos"/);
    assert.doesNotMatch(html, /stznav-session-links/);
  }
});
