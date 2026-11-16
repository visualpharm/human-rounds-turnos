// The browser tier's one ceiling (tests/helpers/ceiling.mjs): a slow answer inside it passes, a
// hang fails with what was awaited, the ceiling, the time waited and the load average - for a
// promise (`within`), a polled condition (`until`), a real Chromium wait and a whole test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright";
import {
  CEILING_ENV, ceil, explained, hookOptions, testCeiling, waitCeiling, within,
} from "./helpers/ceiling.mjs";
import { until } from "./helpers/wait.mjs";

const HANG_WORDS = [/ceiling/, /waited \d+\.\d s|test running \d+\.\d s/, /load average (\d+\.\d|unknown)/, new RegExp(CEILING_ENV)];

function assertHangMessage(message, what) {
  assert.match(message, what);
  for (const words of HANG_WORDS) assert.match(message, words);
}

test("the ceiling is 120 s by default and the variable overrides it in seconds", () => {
  assert.equal(waitCeiling({}), 120000);
  assert.equal(waitCeiling({ [CEILING_ENV]: "300" }), 300000);
  assert.equal(waitCeiling({ [CEILING_ENV]: "0.5" }), 500);
  for (const raw of ["", "0", "-3", "soon"]) assert.equal(waitCeiling({ [CEILING_ENV]: raw }), 120000, raw);
  assert.equal(testCeiling({ [CEILING_ENV]: "10" }), 30000, "a test gets three waits' worth");
  assert.ok(hookOptions().timeout > testCeiling(), "a launch hook outlasts a test");
});

test("a slow promise inside the ceiling passes, a silent one fails naming what it awaited", async () => {
  assert.equal(await within(delay(150).then(() => "answer"), "the answer", { ceiling: 30000 }), "answer");
  const started = performance.now();
  await assert.rejects(within(new Promise(() => {}), "answer to GET /api/x", { ceiling: 200 }), (error) => {
    assert.ok(error instanceof assert.AssertionError);
    assertHangMessage(error.message, /no answer to GET \/api\/x within the 0\.2 s ceiling/);
    return true;
  });
  assert.ok(performance.now() - started < 2000, "the hang fails at the ceiling, not later");
});

test("until: a condition that turns true slowly passes, one that never does fails with the load", async () => {
  const since = performance.now();
  assert.equal(await until(() => performance.now() - since > 150 && "ready", "ready", { timeout: 30000 }), "ready");
  await assert.rejects(until(() => false, "the row shows up", { timeout: 100 }), (error) => {
    assertHangMessage(error.message, /no sign of: the row shows up within the 0\.1 s ceiling/);
    return true;
  });
});

test("a Chromium wait: a slow page inside the ceiling passes, a page that never answers fails with the load", hookOptions(), async (t) => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route("https://ceiling.test/slow", async (route) => {
    await delay(400);
    return route.fulfill({ contentType: "text/html", body: "<p id=answer>Turno confirmado</p>" });
  });
  ceil(page, 60000);
  await page.goto("https://ceiling.test/slow");
  assert.equal(await page.locator("#answer").textContent(), "Turno confirmado");

  // A message that never shows: the wait reaches the (tiny) ceiling.
  ceil(page, 500);
  await page.setContent('<p id="msg" hidden>Turno confirmado</p>');
  const started = performance.now();
  const error = await page.locator("#msg").waitFor({ state: "visible" }).then(() => null, (e) => e);
  assert.equal(error?.name, "TimeoutError", "Playwright gave up at the ceiling");
  const told = explained(error, started, 500);
  assert.ok(told instanceof assert.AssertionError);
  assertHangMessage(told.message, /locator\('#msg'\)/);
  assert.match(told.message, /reached the 0\.5 s ceiling/);
  assert.equal(told.cause, error, "the original error and its stack stay as the cause");
  const failedAssertion = new assert.AssertionError({ message: "expected «Turno confirmado»" });
  assert.equal(explained(failedAssertion, started), failedAssertion, "a failed assertion passes through untouched");
});

test("a whole test under the ceiling: a slow one passes, a hung one fails with the load", hookOptions(), () => {
  const env = { ...process.env, [CEILING_ENV]: "1" };
  delete env.NODE_TEST_CONTEXT;   // the child reports on its own stdout, not into this run
  const runner = fileURLToPath(new URL("./fixtures/ceiling-tier-runner.mjs", import.meta.url));
  const child = spawnSync(process.execPath, ["--test", "--test-reporter=tap", runner], { env, encoding: "utf8" });
  const out = child.stdout + child.stderr;
  assert.equal(child.status, 1, out);
  assert.match(out, /^ok \d+ - slow but inside the ceiling$/m, out);
  assert.match(out, /^not ok \d+ - never ends$/m, out);
  assertHangMessage(out, /no end of the test «never ends» within the 3 s ceiling/);
});
