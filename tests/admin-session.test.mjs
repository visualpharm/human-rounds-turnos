import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { until, stays } from "./helpers/wait.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = readFileSync(join(ROOT, "static/platform/admin-session.mjs"), "utf8");
const MODULE_URL = pathToFileURL(join(ROOT, "static/platform/admin-session.mjs")).href;

const SESSION_CONSUMERS = [
  "static/platform/admin-alerts.mjs", "static/root/admin-app.js", "static/platform/admin-account.mjs", "app/modules/intake/admin-consult.mjs",
  "static/platform/admin-patient-summary.mjs", "static/platform/admin-content.mjs", "static/platform/admin-create-appointment.mjs",
  "static/platform/admin-doctor-patients.mjs", "static/platform/admin-knowledge.mjs", "static/platform/admin-legal.mjs",
  "static/platform/admin-my-appointments.mjs", "static/platform/admin-patients.mjs", "static/platform/admin-preparations.mjs",
  "static/platform/admin-prompts.mjs", "static/platform/admin-queue.mjs", "static/platform/admin-reports.mjs", "static/platform/admin-settings.mjs",
  "static/platform/admin-system.mjs", "static/platform/admin-appearance.mjs",
  "static/platform/admin-users.mjs",
  "app/modules/intake/admin-entrevistas.mjs",
];

function response(status, body, json = true) {
  return { status, clone() { return { json: () => json ? Promise.resolve(body) : Promise.reject(new Error("text")) }; } };
}

let bootLock = Promise.resolve();

async function loadSession(whoami) {
  let releaseLock;
  const next = new Promise((resolve) => { releaseLock = resolve; });
  const previousLock = bootLock;
  bootLock = next;
  await previousLock;
  const location = { href: "", pathname: "/admin/pacientes", search: "?patient=7" };
  let whoamiCalls = 0;
  const previous = {
    fetch: globalThis.fetch,
    window: globalThis.window,
    location: globalThis.location,
    SANA_ADMIN_SCREEN: globalThis.SANA_ADMIN_SCREEN,
  };
  const fetchImpl = async (url) => {
    assert.equal(url, "/api/admin/whoami");
    whoamiCalls += 1;
    return whoami;
  };
  globalThis.fetch = fetchImpl;
  globalThis.window = globalThis;
  globalThis.location = location;
  globalThis.SANA_ADMIN_SCREEN = "pacientes";
  try {
    const api = await import(`${MODULE_URL}?session-test=${Date.now()}-${Math.random()}`);
    return {
      api,
      location,
      whoamiCalls: () => whoamiCalls,
      restore() {
        globalThis.fetch = previous.fetch;
        globalThis.window = previous.window;
        globalThis.location = previous.location;
        globalThis.SANA_ADMIN_SCREEN = previous.SANA_ADMIN_SCREEN;
        releaseLock();
      },
    };
  } catch (err) {
    globalThis.fetch = previous.fetch;
    globalThis.window = previous.window;
    globalThis.location = previous.location;
    globalThis.SANA_ADMIN_SCREEN = previous.SANA_ADMIN_SCREEN;
    releaseLock();
    throw err;
  }
}

test("only confirmed no_autenticado redirects an expired direct page", async () => {
  const live = await loadSession(response(401, { error: "no_autenticado" }));
  try {
    await live.api.confirmar(response(401, { error: "no_autenticado" }));
    assert.equal(live.location.href, "/admin/login?next=" + encodeURIComponent("/admin/pacientes?patient=7"));
  } finally {
    live.restore();
  }
});

test("live-session and non-JSON 401 responses stay local", async () => {
  const live = await loadSession(response(200, { usuario: "u", rol: "admin" }));
  try {
    await live.api.confirmar(response(401, { error: "no_autenticado" }));
    assert.equal(live.location.href, "");
  } finally {
    live.restore();
  }
  const text = await loadSession(response(401, { error: "no_autenticado" }));
  try {
    await text.api.confirmar(response(401, null, false));
    assert.equal(text.location.href, "");
  } finally {
    text.restore();
  }
});

test("concurrent expired responses coalesce to one whoami confirmation", async () => {
  let release;
  const delayedWhoami = new Promise((resolve) => { release = resolve; });
  const live = await loadSession(delayedWhoami);
  try {
    const expired = response(401, { error: "no_autenticado" });
    const first = live.api.confirmar(expired);
    const second = live.api.confirmar(expired);
    await until(() => live.whoamiCalls() >= 1, "the single whoami confirmation goes out");
    await stays(() => live.whoamiCalls() === 1, "the two expired responses share one whoami call", 5);
    assert.equal(live.whoamiCalls(), 1);
    release(response(401, { error: "no_autenticado" }));
    assert.deepEqual(await Promise.all([first, second]), [true, true]);
    assert.match(live.location.href, /^\/admin\/login\?next=/);
  } finally {
    live.restore();
  }
});

test("only exact 2fa_requerido 403 enters the Account repair path", async () => {
  const required = await loadSession(response(200, {}));
  try {
    await required.api.confirmar(response(403, { error: "2fa_requerido" }));
    assert.equal(required.location.href, "/admin/account?setup=2fa");
  } finally {
    required.restore();
  }
  const ordinary = await loadSession(response(200, {}));
  try {
    await ordinary.api.confirmar(response(403, { error: "rol_no_autorizado" }));
    assert.equal(ordinary.location.href, "");
  } finally {
    ordinary.restore();
  }
});

test("a controller may pass a body it already consumed without losing the exact 2FA transition", async () => {
  const live = await loadSession(response(200, {}));
  try {
    const consumed = { status: 403, clone() { throw new Error("body already used"); } };
    await live.api.confirmar(consumed, { error: "2fa_requerido" });
    assert.equal(live.location.href, "/admin/account?setup=2fa");
  } finally {
    live.restore();
  }
});

test("an already-consumed queue response keeps its explicit 401 body for confirmation", async () => {
  const live = await loadSession(response(401, { error: "no_autenticado" }));
  try {
    const consumed = { status: 401, clone() { throw new Error("body already used"); } };
    await live.api.confirmar(consumed, { error: "no_autenticado" });
    assert.match(live.location.href, /^\/admin\/login\?next=/);
  } finally {
    live.restore();
  }
  const queue = readFileSync(join(ROOT, "static/platform/admin-queue.mjs"), "utf8");
  assert.match(queue, /mod\.confirmar\(r\.res, r\.body, operation\.current\)/,
    "the attendance caller must hand its already-parsed JSON to the session boundary");
});

test("percent-decoded traversal cannot become a login return target", async () => {
  const live = await loadSession(response(200, {}));
  try {
    assert.equal(live.api.nextValido("/admin/%252e%252e/users"), false);
    assert.equal(live.api.nextValido("/admin/users?new=1"), true);
  } finally {
    live.restore();
  }
});

test("the session boundary never monkey-patches fetch", () => {
  assert.doesNotMatch(SOURCE, /window\.fetch\s*=/);
  assert.doesNotMatch(SOURCE, /globalThis\.fetch\s*=/);
});

test("admin-session.mjs is the only implementation and publishes no compatibility global", () => {
  assert.equal(existsSync(join(ROOT, "admin-session.js")), false);
  assert.equal(existsSync(join(ROOT, "admin-session.mjs")), false);
  assert.equal(existsSync(join(ROOT, "static/platform/admin-session.mjs")), true);
  assert.match(SOURCE, /export function confirmar/);
  assert.match(SOURCE, /export function redirigir/);
  assert.match(SOURCE, /export function nextValido/);
  assert.doesNotMatch(SOURCE, /window\.SanaAdminSession|module\.exports/);
});

test("every runtime consumer awaits the ESM session module instead of skipping a pending import", () => {
  for (const file of SESSION_CONSUMERS) {
    const src = readFileSync(join(ROOT, file), "utf8");
    const nativeDep = file === "static/platform/admin-users.mjs"
      || file === "static/platform/admin-create-appointment.mjs";
    if (nativeDep) {
      assert.match(src, /await importDep\(["']platform\/admin-session\.mjs["']/,
        `${file} must top-level-await the session module`);
    } else if (["static/platform/admin-preparations.mjs", "static/platform/admin-account.mjs"].includes(file)) {
      // Native lifecycle controller: one dependencies promise that awaits the
      // session import; .loaded ends the page's load deadline on it and
      // .ready gates the first render on it.
      assert.match(src, /var dependencies = Promise\.all\(\[[\s\S]*?importStatic\("platform\/admin-session\.mjs"\)[\s\S]*?\]\);[\s\S]*?dispose\.loaded = dependencies\.then\([\s\S]*?dispose\.ready = dependencies\.then\(/,
        `${file} must gate first render on the session import`);
    } else if (file === "app/modules/intake/admin-consult.mjs") {
      // Native lifecycle controller: first render is gated by the mount's
      // ready promise, which awaits the session import inside its Promise.all.
      assert.match(src, /dispose\.ready = Promise\.all\(\[[\s\S]*?importStatic\("platform\/admin-session\.mjs"\)[\s\S]*?\]\)/,
        `${file} must gate first render on the session import`);
    } else {
      if (file === "static/platform/admin-patients.mjs") {
        assert.match(src, /var dependencies = Promise\.all\(\[[\s\S]*importStatic\("platform\/admin-session\.mjs"\)[\s\S]*?dispose\.loaded = dependencies\.then\([\s\S]*?dispose\.ready = dependencies\.then\(/);
        assert.match(src, /Session = take\(mods\[0\][\s\S]*return route\(\)/);
      } else assert.match(src, /sessionReady/, `${file} must retain an explicit session-load gate`);
      assert.match(
        src,
        /(?:sanaImportStatic|importStatic|importDep)\(["']platform\/admin-session\.mjs["'](?:\)|,)|SANA_STATIC\[["']platform\/admin-session\.mjs["']\]/,
        `${file} must resolve admin-session.mjs through the fingerprint manifest`,
      );
    }
    assert.doesNotMatch(src, /window\.SanaAdminSession|admin-session\.js/,
      `${file} must not keep the retired global or classic file`);
    assert.doesNotMatch(src, /if\s*\(\s*!window\.SanaAdminSession/,
      `${file} must not skip auth handling while the module is pending`);
  }
});

// 2026-09-08 HSI cutover: the classic admin-hsi.js consumer is physically
// retired. Its replacement resolves the session module inside the mount's
// own lifecycle — no sessionReady variable, but the same fail-closed
// manifest import, the same confirmar boundary, and an ownership predicate
// so a disposed mount can never navigate the fresh screen.
test("the native HSI controller resolves admin-session.mjs through the manifest and confirms with an owner predicate", () => {
  const hsi = readFileSync(join(ROOT, "static/platform/admin-hsi.mjs"), "utf8");
  assert.match(hsi, /importStatic\("platform\/admin-session\.mjs"\)/);
  assert.match(hsi, /take\(mods\[0\], "platform\/admin-session\.mjs", function \(m\) \{ return typeof m\.confirmar === "function"; \}\)/);
  assert.match(hsi, /return Session\.confirmar\(r\.response, r\.json, current\)\.then\(function \(transitioned\) \{/);
  assert.doesNotMatch(hsi, /window\.SanaAdminSession|admin-session\.js/);
});

test("an early 401 waits for a delayed session import instead of skipping auth handling", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const live = await loadSession(response(401, { error: "no_autenticado" }));
  try {
    const sessionReady = gate.then(() => live.api);
    const pending = sessionReady.then((mod) => mod.confirmar(response(401, { error: "no_autenticado" })));
    await stays(() => live.whoamiCalls() === 0 && live.location.href === "", "the early 401 neither confirms nor redirects before the import", 5);
    assert.equal(live.whoamiCalls(), 0);
    assert.equal(live.location.href, "");
    release();
    assert.equal(await pending, true);
    assert.match(live.location.href, /^\/admin\/login\?next=/);
  } finally {
    live.restore();
  }
});

test("delayed session import still sends exact 2FA repair after an early 403", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const live = await loadSession(response(200, {}));
  try {
    const sessionReady = gate.then(() => live.api);
    const pending = sessionReady.then((mod) => mod.confirmar(response(403, { error: "2fa_requerido" })));
    await stays(() => live.location.href === "", "the early 403 does not redirect before the import", 5);
    assert.equal(live.location.href, "");
    release();
    assert.equal(await pending, true);
    assert.equal(live.location.href, "/admin/account?setup=2fa");
  } finally {
    live.restore();
  }
});
