// wait.mjs - wait for a condition, not for a number of milliseconds (the JS twin of tests/_wait.py).
//
//   import { until, stays } from "./helpers/wait.mjs";
//   await until(() => calls.length === 1, "the request goes out");          // polls every 5 ms, up to the ceiling
//   const row = await until(() => rows.find(Boolean), "a row shows up");   // returns the truthy value
//   await stays(() => calls.length === 0, "nothing is sent", 80);           // a negative window
//
// `until` rejects with an AssertionError naming what it waited for, the ceiling, the time waited and
// the load average, so the failure reads like an assertion; only a real hang reaches the ceiling
// (`waitCeiling()` in ceiling.mjs, 120 s, TURNOS_TEST_BROWSER_CEILING). `stays` is for "nothing
// happens": it checks the condition for the whole window and fails the moment it is false. A window
// is time, so keep it as short as the test's meaning allows. The delay goes through
// `timers/promises`, which the ratchet (scripts/hygiene_ratchet.py) does not count: the polling
// itself is not a pause in the test.
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { hang, waitCeiling } from "./ceiling.mjs";

export async function until(condition, what = "the condition", { timeout = waitCeiling(), interval = 5 } = {}) {
  const started = Date.now();
  const deadline = started + timeout;
  for (;;) {
    const value = await condition();
    if (value) return value;
    if (Date.now() >= deadline) throw hang(`sign of: ${what}`, timeout, Date.now() - started);
    await delay(interval);
  }
}

export async function stays(condition, what = "the condition", windowMs = 50, { interval = 5 } = {}) {
  const deadline = Date.now() + windowMs;
  for (;;) {
    if (!(await condition())) assert.fail(`stopped holding within ${windowMs} ms: ${what}`);
    if (Date.now() >= deadline) return;
    await delay(interval);
  }
}
