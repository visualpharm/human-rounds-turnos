import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { installDom } from "./entrevistas-dom.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "../..");
const world = installDom();
globalThis.window.SANA_STATIC = {
  "platform/format.mjs": pathToFileURL(join(ROOT, "static/platform/format.mjs")).href,
  "platform/admin-session.mjs": pathToFileURL(join(ROOT, "static/platform/admin-session.mjs")).href,
  "platform/i18n.mjs": pathToFileURL(join(ROOT, "static/platform/i18n.mjs")).href,
  "platform/admin-preferences.mjs": pathToFileURL(join(ROOT, "static/platform/admin-preferences.mjs")).href,
  "platform/admin-table.mjs": pathToFileURL(join(ROOT, "static/platform/admin-table.mjs")).href,
  "platform/admin-filter-controls.mjs": pathToFileURL(join(ROOT, "static/platform/admin-filter-controls.mjs")).href,
};
globalThis.window.sanaImportStatic = (name) => import(globalThis.window.SANA_STATIC[name]);
globalThis.SANA_COUNTRY_PHONE = { area: "2254" };

try {
  const Story = await import("../../stories/Interviews.stories.js");
  const rendered = Story.Doctors();
  assert.ok(rendered && typeof rendered === "object", "Doctors must return a DOM Node synchronously");
  assert.equal(typeof rendered.then, "undefined", "Doctors must not return a Promise");
  assert.equal(rendered.tagName, "MAIN");
  // The story's synchronous Node contract does not mean all native imports
  // and preference hydration have finished. Keep the fake browser alive until
  // those real dependencies settle, otherwise a late pagehide registration
  // runs against a restored/undefined window (timing-dependent false red).
  await Promise.all(Object.values(window.SANA_STATIC).map((url) => import(url)));
  const preferences = await import(window.SANA_STATIC["platform/admin-preferences.mjs"]);
  await preferences.load();
  await new Promise((resolve) => setImmediate(resolve));
} finally {
  world.restore();
}
