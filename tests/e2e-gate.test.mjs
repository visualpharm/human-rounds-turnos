// tests/e2e-gate.test.mjs -- proves the Tier-2 E2E harness's gate-vs-
// convenience branch (Codex review of ac44fe9, item 1/6): a missing
// prerequisite (Playwright not installed, Chromium not downloaded,
// Postgres unreachable -- setupHarness() surfaces every one of these the
// same way, as `{ ok:false, reason }`) must FAIL `npm run e2e`, never
// produce a quietly-green skipped suite. Only the explicit opt-in
// convenience mode (`npm run e2e:dev`, TURNOS_E2E_ALLOW_SKIP=1) may skip.
//
// This is a fast, hermetic unit test of `requireOk()` itself (the one
// piece of new logic that decides fail-vs-skip) -- it does not re-run the
// real setupHarness()/Playwright/Postgres path, which is what
// tests/e2e/*.spec.mjs (run via `npm run e2e`) already exercises end to end.
import test from "node:test";
import assert from "node:assert/strict";
import { requireOk } from "./e2e/lib/harness.mjs";

function fakeTest() {
  const calls = [];
  return {
    calls,
    skip: (reason) => calls.push(["skip", reason]),
  };
}

test("gate mode (default): a missing prerequisite throws, it never skips", () => {
  const t = fakeTest();
  delete process.env.TURNOS_E2E_ALLOW_SKIP;
  const state = { ok: false, reason: "chromium no disponible (mocked missing binary)" };
  assert.throws(
    () => requireOk(state, t),
    /chromium no disponible/,
    "requireOk() must throw in gate mode so node:test reports a FAILED test, not a skip"
  );
  assert.deepEqual(t.calls, [], "t.skip() must never be called in gate mode");
});

test("gate mode: an ok state proceeds without throwing or skipping", () => {
  const t = fakeTest();
  delete process.env.TURNOS_E2E_ALLOW_SKIP;
  const state = { ok: true, baseUrl: "http://127.0.0.1:1" };
  assert.equal(requireOk(state, t), true);
  assert.deepEqual(t.calls, []);
});

test("convenience mode (TURNOS_E2E_ALLOW_SKIP=1): a missing prerequisite skips, it never throws", () => {
  const t = fakeTest();
  process.env.TURNOS_E2E_ALLOW_SKIP = "1";
  try {
    const state = { ok: false, reason: "postgres no accesible (mocked)" };
    assert.doesNotThrow(() => requireOk(state, t));
    assert.equal(t.calls.length, 1);
    assert.equal(t.calls[0][0], "skip");
    assert.match(t.calls[0][1], /postgres no accesible/);
  } finally {
    delete process.env.TURNOS_E2E_ALLOW_SKIP;
  }
});
