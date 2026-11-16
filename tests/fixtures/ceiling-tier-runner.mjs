// Run by tests/browser-ceiling.test.mjs as `node --test` in a child process with a tiny
// TURNOS_TEST_BROWSER_CEILING: one test finishes slowly inside the test ceiling, one never ends.
import { setTimeout as delay } from "node:timers/promises";
import { test } from "../helpers/ceiling.mjs";

test("slow but inside the ceiling", async () => {
  await delay(150);
});

test("never ends", async () => {
  await new Promise(() => {});
});
