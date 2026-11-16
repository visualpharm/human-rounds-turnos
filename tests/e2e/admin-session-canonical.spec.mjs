// Early admin alert polling against a stub origin that serves the real admin-alerts,
// dates, format, i18n and admin-session modules, with the session module held back:
// the first alert poll must wait for the session module, and the 401 / 2FA-required
// answers must then route through it (login with a safe `next`, Account 2FA setup).
//
// Port of tests/e2e/admin-session-canonical.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The stub origin comes from the `serve` fixture and the page from the runner (the
//   original started its own http.Server and `chromium.launch` per test, via withServer()).
//   The stub does not use the application: The file is `hermetic` (appOptions only names it for
//   tests/e2e-spec-isolation.test.mjs): no app or database is started for it.
// - The hand-made module gate is holdGate(cleanup): it is also released on cleanup, so a
//   failing test never leaves the held response (and the server) hanging.
// - Checks that guard a race (no alert request while the session module is held, the page is
//   still on the screen, `whoami` not called yet) stay point-in-time reads taken right after
//   the module request was seen; the redirect itself is `waitForURL` with the original 4 s.
import { readFileSync } from "node:fs";
import { test, expect, holdGate } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_admin_session_canonical", hermetic: true } });

const sessionSource = readFileSync(new URL("../../static/platform/admin-session.mjs", import.meta.url), "utf8");
const alertsSource = readFileSync(new URL("../../static/platform/admin-alerts.mjs", import.meta.url), "utf8");
const datesSource = readFileSync(new URL("../../static/platform/dates.mjs", import.meta.url), "utf8");
const formatSource = readFileSync(new URL("../../static/platform/format.mjs", import.meta.url), "utf8");
const i18nSource = readFileSync(new URL("../../static/platform/i18n.mjs", import.meta.url), "utf8");

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function script(res, body) {
  res.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8" });
  res.end(body);
}

function page(res) {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(`<!doctype html><html><head><meta charset="utf-8">
<script>
window.SANA_ADMIN_SCREEN = "solicitudes";
window.SANA_STATIC = {
  "platform/dates.mjs": "/static/platform/dates.mjs",
  "platform/format.mjs": "/static/platform/format.mjs",
  "platform/admin-session.mjs": "/static/platform/admin-session.mjs",
  "platform/i18n.mjs": "/platform-i18n.mjs"
};
window.sanaImportStatic = (name) => import(window.SANA_STATIC[name]);
window.SANA_COUNTRY_PHONE = {
  code: "54", exit_prefix: "00", national_length: 10, mobile_prefix: "9",
  mobile_local_suffix: "15", trunk_prefix: "0", areas_2: [], areas_3: [],
  area_default: "2254", final_group: 4, non_geographic: []
};
</script></head>
<body><header class="adm-topbar"><div class="adm-h1row"><h1 id="adm-h1">Solicitudes</h1></div></header>
<script type="module">import { mountAlerts } from '/static/platform/admin-alerts.mjs';
const dispose = mountAlerts({root: document, role: 'admin'});
addEventListener('pagehide', dispose);
await dispose.ready;</script>
</body></html>`);
}

function serveAsset(url, moduleGate) {
  if (url === "/admin/solicitudes") return (res) => page(res);
  if (url === "/static/platform/admin-alerts.mjs") return (res) => script(res, alertsSource);
  if (url === "/static/platform/dates.mjs") return (res) => script(res, datesSource);
  if (url === "/static/platform/format.mjs") return (res) => script(res, formatSource);
  if (url === "/platform-i18n.mjs") return (res) => script(res, i18nSource);
  if (url === "/static/platform/admin-session.mjs") return async (res) => {
    await moduleGate;
    script(res, sessionSource);
  };
  return null;
}

test("early alert polling waits for a delayed session module, then redirects with a safe next", async ({ page, serve, cleanup }) => {
  const gate = holdGate(cleanup);
  let whoamiCalls = 0;
  const origin = await serve(async (req, res) => {
    const url = req.url.split("?")[0];
    const asset = serveAsset(url, gate.held);
    if (asset) return asset(res);
    if (url === "/api/admin/intake/alerts/count") {
      return json(res, 401, { error: "no_autenticado" });
    }
    if (url === "/api/admin/whoami") {
      whoamiCalls += 1;
      return json(res, 401, { error: "no_autenticado" });
    }
    res.writeHead(404).end();
  });
  const countSeen = page.waitForRequest((req) => req.url().includes("/api/admin/intake/alerts/count"));
  const sessionSeen = page.waitForRequest((req) => req.url().endsWith("/static/platform/admin-session.mjs"));
  let counts = 0;
  page.on("request", (req) => { if (req.url().includes("/api/admin/intake/alerts/count")) counts++; });
  await page.goto(`${origin.url}/admin/solicitudes?patient=7`, { waitUntil: "commit" });
  await sessionSeen;
  expect(counts, "required session module gates even the initial poll").toBe(0);
  expect(new URL(page.url()).pathname).toBe("/admin/solicitudes");
  expect(whoamiCalls).toBe(0);
  gate.release();
  await countSeen;
  await page.waitForURL((href) => {
    const next = new URL(href);
    return next.pathname === "/admin/login" && next.searchParams.get("next") === "/admin/solicitudes?patient=7";
  }, { timeout: 4000 });
  expect(whoamiCalls).toBe(1);
});

test("early 2FA-required 403 waits for the delayed session module and opens Account setup", async ({ page, serve, cleanup }) => {
  const gate = holdGate(cleanup);
  let whoamiCalls = 0;
  const origin = await serve(async (req, res) => {
    const url = req.url.split("?")[0];
    const asset = serveAsset(url, gate.held);
    if (asset) return asset(res);
    if (url === "/api/admin/intake/alerts/count") {
      return json(res, 403, { error: "2fa_requerido" });
    }
    if (url === "/api/admin/whoami") {
      whoamiCalls += 1;
      return json(res, 200, { usuario: "u", rol: "admin" });
    }
    res.writeHead(404).end();
  });
  const countSeen = page.waitForRequest((req) => req.url().includes("/api/admin/intake/alerts/count"));
  const sessionSeen = page.waitForRequest((req) => req.url().endsWith("/static/platform/admin-session.mjs"));
  let counts = 0;
  page.on("request", (req) => { if (req.url().includes("/api/admin/intake/alerts/count")) counts++; });
  await page.goto(`${origin.url}/admin/solicitudes`, { waitUntil: "commit" });
  await sessionSeen;
  expect(counts, "required session module gates even the initial poll").toBe(0);
  expect(new URL(page.url()).pathname).toBe("/admin/solicitudes");
  gate.release();
  await countSeen;
  await page.waitForURL((href) => new URL(href).pathname + new URL(href).search === "/admin/account?setup=2fa", { timeout: 4000 });
  expect(whoamiCalls).toBe(0);
});
