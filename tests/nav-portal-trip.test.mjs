// nav-portal-trip.test.mjs — signed in on the municipal portal = signed in on
// salud, with no «Ingresar» (the product owner, m1008550). The portal's `sso` cookie on
// .pinamar.gob.ar lasts 8 h and only the portal page renews it, so a person
// still signed in on the portal can open salud with no identity. A browser
// that was signed in here before goes once through the portal's sign-in,
// which renews the cookie and comes back to the same page.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as Nav from "../static/platform/nav.mjs";

const NAV = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "static/platform/nav.mjs"), "utf8");

function memory() {
  const data = new Map();
  return {
    getItem: k => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
  };
}

const WAS_IN = { loggedIn: true, authMode: "portal" };
const NOW_OUT = { loggedIn: false, refused: true, authMode: "portal" };
const T0 = 1_800_000_000_000;

test("a browser signed in before and now without an identity goes through the portal once", () => {
  const storage = memory();
  assert.equal(Nav.portalTripDue(WAS_IN, NOW_OUT, storage, T0), true);
  // Back within two minutes still without one: stay, with «Ingresar».
  assert.equal(Nav.portalTripDue(WAS_IN, NOW_OUT, storage, T0 + 60_000), false);
  // Later on, in the same tab, it may try again.
  assert.equal(Nav.portalTripDue(WAS_IN, NOW_OUT, storage, T0 + 121_000), true);
});

test("never for a visitor who was not signed in, a signed-in one, or another installation", () => {
  const storage = memory();
  assert.equal(Nav.portalTripDue({ loggedIn: false, authMode: "portal" }, NOW_OUT, storage, T0), false);
  assert.equal(Nav.portalTripDue(WAS_IN, { ...NOW_OUT, loggedIn: true, refused: false }, storage, T0), false);
  assert.equal(Nav.portalTripDue({ loggedIn: true, authMode: "password" }, { ...NOW_OUT, authMode: "password" }, storage, T0), false);
  assert.equal(Nav.portalTripDue({ loggedIn: true, authMode: "password" }, NOW_OUT, storage, T0), false);
  assert.equal(storage.getItem("hr_portal_trip"), null, "no trip recorded");
});

test("a network failure is not a signed-out answer", () => {
  assert.equal(Nav.portalTripDue(WAS_IN, { loggedIn: false, refused: false, authMode: "portal" }, memory(), T0), false);
});

test("storage unavailable: stay on the page with its button", () => {
  const broken = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } };
  assert.equal(Nav.portalTripDue(WAS_IN, NOW_OUT, broken, T0), false);
  assert.equal(Nav.portalTripDue(WAS_IN, NOW_OUT, null, T0), false);
});

test("the trip shares the guard of /account's own trip and clears the marker first", () => {
  const form = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "static/platform/account-form.mjs"), "utf8");
  assert.match(form, /var PORTAL_TRIP_KEY = "hr_portal_trip";/);
  assert.match(NAV, /var PORTAL_TRIP_KEY = "hr_portal_trip";/);
  // The fresh (signed-out) state is cached before leaving, so a trip that
  // comes back without an identity never starts another one.
  assert.match(NAV, /var before = readCache\(\);\s*writeCache\(fresh\);\s*if \(portalTripDue\(before, fresh, sessionStore\(\), Date\.now\(\)\)\) \{\s*window\.location\.replace\("\/account\/portal-login\?next=" \+ encodeURIComponent\(location\.pathname \+ location\.search\)\);/);
  // /api/account/me answering 401 is the only «signed out».
  assert.match(NAV, /refused: r\.status === 401/);
});
