// Delayed/rejected native turnos upload controllers. Source scans cannot
// prove an early paste/click is retained, a held wz-upload blocks service
// evaluation, or a rejected import paints localized recovery instead of a
// blank wizard. Uses the real modules and the same boot contract the
// server emits — not a hand-rolled controller.
import { test, within, hookOptions, openPage } from "./helpers/ceiling.mjs";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { patientSiteSource } from "./helpers/patient-site-source.mjs";
import { until, stays } from "./helpers/wait.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file) => readFileSync(path.join(ROOT, file), "utf8");
const ORIGIN = "https://turnos-upload-native.test";
const PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const LOAD_ERROR = "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.";

// Render the actual server pages.  The temporary, empty env file is set
// before Python imports project modules: this test never reads a checkout
// .env or starts a database-backed application.
const ENV_DIR = mkdtempSync(path.join(os.tmpdir(), "turnos-upload-native-"));
const EMPTY_ENV = path.join(ENV_DIR, "empty.env");
writeFileSync(EMPTY_ENV, "", "utf8");
const RENDER_PAGES = String.raw`
import base64, json, sys
from app.patient_web import site_catalog, site_page_service
from app.modules.turnos import preparation
order = next(s for s in site_catalog._services() if s.get("modo") == "solicitud_con_orden")
schedule = next(s for s in site_catalog._services() if s.get("modo") == "solicitud")
pages = {
  "book": site_page_service._page_service_appointment(order["id"]),
  "book-schedule": site_page_service._page_service_appointment(schedule["id"]),
  "upload-order": preparation._upload_order_page({}),
}
print(json.dumps({k: base64.b64encode(v).decode("ascii") for k, v in pages.items()}))
`;
function renderPages() {
  const output = execFileSync("python3", ["-c", RENDER_PAGES], {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, DATABASE_URL: "", TURNOS_ENV_FILE: EMPTY_ENV },
  });
  return Object.fromEntries(Object.entries(JSON.parse(output)).map(([route, body]) => [
    route, Buffer.from(body, "base64").toString("utf8"),
  ]));
}
const PAGES = renderPages();

function manifestFrom(html) {
  const match = html.match(/<script>window\.SANA_STATIC=(\{[^<]+\});<\/script>/);
  assert.ok(match, "actual server page must publish SANA_STATIC");
  return JSON.parse(match[1]);
}
function controllerPath(html, name) {
  const stem = name.replace(/\.mjs$/, "");
  const match = html.match(new RegExp('"url":"([^"?]*' + stem + '\\.[a-f0-9]{8}\\.mjs)"'));
  assert.ok(match, `actual server page must boot ${name}`);
  return match[1];
}
function localStaticPath(key) {
  return existsSync(path.join(ROOT, "static", key)) ? path.join("static", key) : key;
}
function pageAssets(html, controller) {
  const assets = {};
  for (const [key, url] of Object.entries(manifestFrom(html))) assets[url] = localStaticPath(key);
  assets[controllerPath(html, controller)] = `app/modules/turnos/${controller}`;
  const uploadMap = html.match(/"\/static\/modules\/turnos\/wz-upload\.mjs":"([^"]+)"/);
  assert.ok(uploadMap, "actual server bootstrap must map the native upload dependency");
  assets[uploadMap[1]] = "app/modules/turnos/wz-upload.mjs";
  assets["/static/modules/turnos/wz-upload.mjs"] = "app/modules/turnos/wz-upload.mjs";
  return assets;
}
const ASSETS = {
  book: pageAssets(PAGES.book, "service.mjs"),
  "book-schedule": pageAssets(PAGES["book-schedule"], "service.mjs"),
  "upload-order": pageAssets(PAGES["upload-order"], "upload-order.mjs"),
};
const FILES = Object.assign({}, ...Object.values(ASSETS));
const SERVICE_PATH = controllerPath(PAGES.book, "service.mjs");
const UPLOAD_PATH = PAGES.book.match(/"\/static\/modules\/turnos\/wz-upload\.mjs":"([^"]+)"/)[1];
const ORDER_PATH = controllerPath(PAGES["upload-order"], "upload-order.mjs");
const PREP_PATH = manifestFrom(PAGES.book)["platform/prep.mjs"];

function deferred() {
  let release;
  let done = false;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release() { if (!done) { done = true; release(); } } };
}


async function pastePng(page) {
  await page.evaluate((b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const file = new File([bytes], "orden.png", { type: "image/png" });
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", {
      value: {
        items: [{ kind: "file", type: "image/png", getAsFile() { return file; } }],
      },
    });
    document.dispatchEvent(event);
  }, PNG_B64);
}

async function imagePasteIsPrevented(page) {
  return page.evaluate(() => {
    const file = new File(["x"], "later.png", { type: "image/png" });
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", {
      value: { items: [{ kind: "file", type: "image/png", getAsFile() { return file; } }] },
    });
    document.dispatchEvent(event);
    return event.defaultPrevented;
  });
}

async function queueEarlyFile(page) {
  await pastePng(page);
  const hasInput = await page.evaluate(() => !!document.getElementById("wz-file"));
  if (!hasInput) return;
  await page.locator("#wz-file").setInputFiles({
    name: "orden.png",
    mimeType: "image/png",
    buffer: Buffer.from(PNG_B64, "base64"),
  });
}

async function fulfillFiles(route, {
  holdService, abortService, holdUpload, abortUpload, holdOrder, abortOrder, holdPrep,
  turnstileSitekey = null,
} = {}) {
  const url = new URL(route.request().url());
  const pathname = url.pathname;
  if (abortService && pathname === SERVICE_PATH) return route.abort("failed");
  if (abortUpload && pathname === UPLOAD_PATH) return route.abort("failed");
  if (abortOrder && pathname === ORDER_PATH) return route.abort("failed");
  if (holdService && pathname === SERVICE_PATH) {
    holdService.requested.release();
    await holdService.release.promise;
  }
  if (holdUpload && pathname === UPLOAD_PATH) {
    holdUpload.requested.release();
    await holdUpload.release.promise;
  }
  if (holdOrder && pathname === ORDER_PATH) {
    holdOrder.requested.release();
    await holdOrder.release.promise;
  }
  if (holdPrep && pathname === PREP_PATH) {
    holdPrep.requested.release();
    await holdPrep.release.promise;
  }
  if (pathname === "/api/analyze-order") {
    return route.fulfill({
      // A reading that names a study: these cases are about the upload's
      // mechanics, and an order that asks for nothing now opens the
      // «No reconocimos» screen with its own upload control (TASK-52).
      json: { ok: true, token: "tok-1", ai: { parece_orden: true, campos: { practicas: { valor: ["Ecografía"] } }, preparacion: [] } },
    });
  }
  if (pathname === "/api/services") {
    return route.fulfill({ json: { servicios: [] } });
  }
  if (pathname === "/api/centers") {
    return route.fulfill({ json: { centros: [] } });
  }
  if (pathname === "/api/account/config") {
    return route.fulfill({ json: { turnstile_sitekey: turnstileSitekey } });
  }
  if (pathname === "/api/preparation-studies") {
    return route.fulfill({ json: { estudios: [] } });
  }
  if (pathname === "/api/account/me") {
    return route.fulfill({ status: 401, json: { error: "unauthorized" } });
  }
  if (pathname.startsWith("/api/service-slots")) {
    return route.fulfill({ json: { medicos: [] } });
  }
  if (pathname.startsWith("/api/i18n/")) {
    return route.fulfill({ json: {} });
  }
  if (FILES[pathname]) {
    return route.fulfill({
      contentType: "text/javascript; charset=utf-8",
      body: read(FILES[pathname]),
    });
  }
  if (pathname === "/book") {
    return route.fulfill({
      contentType: "text/html; charset=utf-8",
      body: PAGES.book,
    });
  }
  if (pathname === "/book-schedule") {
    return route.fulfill({
      contentType: "text/html; charset=utf-8",
      body: PAGES["book-schedule"],
    });
  }
  if (pathname === "/upload-order") {
    return route.fulfill({
      contentType: "text/html; charset=utf-8",
      body: PAGES["upload-order"],
    });
  }
  return route.fulfill({ status: 404, body: "" });
}

test.after(() => rmSync(ENV_DIR, { recursive: true, force: true }));

// One Chromium per file, a fresh context per page (helpers/ceiling.mjs).
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

async function withPage(run) {
  const page = await openPage(browser, { viewport: { width: 1280, height: 900 } });
  try {
    const failures = [];
    page.on("pageerror", (error) => failures.push(String(error)));
    await run(page, failures);
    assert.deepEqual(failures.filter((msg) => !/failed to fetch|Failed to fetch|Load failed|error loading dynamically imported module/i.test(msg)), []);
  } finally {
    await page.context().close();
  }
}

async function installTurnstileProvider(page) {
  await page.addInitScript(() => {
    window.__turnstileProbe = { events: [], renders: 0, responses: 0, resets: 0 };
    let widgetBox;
    window.turnstile = {
      render(box, options) {
        widgetBox = box;
        window.__turnstileProbe.appearance = options.appearance;
        window.__turnstileProbe.events.push("render");
        window.__turnstileProbe.renders += 1;
        window.__turnstileProbe.connectedAtRender = box.isConnected;
        return 73;
      },
      getResponse(widget) {
        window.__turnstileProbe.connectedAtToken = widgetBox.isConnected;
        window.__turnstileProbe.events.push("token");
        window.__turnstileProbe.responses += 1;
        window.__turnstileProbe.responseWidget = widget;
        return "cf-test-token";
      },
      reset(widget) {
        window.__turnstileProbe.events.push("reset");
        window.__turnstileProbe.resets += 1;
        window.__turnstileProbe.resetWidget = widget;
      },
    };
  });
}

async function assertQueuedUploadUsesMountedTurnstile(page, routePath, holdKey) {
  await installTurnstileProvider(page);
  const requested = deferred();
  const release = deferred();
  const analyses = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/analyze-order") analyses.push(request);
  });
  await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
    [holdKey]: { requested, release },
    turnstileSitekey: "sitekey-test",
  }));
  const navigation = page.goto(ORIGIN + routePath, { waitUntil: "domcontentloaded" });
  await within(requested.promise, "request of the native controller");
  await pastePng(page);
  const analyzed = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/api/analyze-order",
  );
  release.release();
  await navigation;
  await analyzed;
  await stays(() => analyses.length === 1, "one queued file produces one analysis POST", 100);
  assert.equal(analyses.length, 1, "one queued file must produce one analysis POST");
  const payload = analyses[0].postDataJSON();
  assert.equal(payload.turnstile_token, "cf-test-token", "configured upload must never use the empty fail-open token");
  const probe = await page.evaluate(() => window.__turnstileProbe);
  assert.equal(probe.renders, 1, "Turnstile must mount once before queued-file replay");
  assert.equal(probe.connectedAtToken, true, "interactive fallback stays mounted until token exists");
  assert.equal(probe.appearance, "interaction-only");
  assert.equal(probe.connectedAtRender, true, "Turnstile must receive its still-connected host node");
  assert.equal(probe.responses, 1, "the upload must request one token");
  assert.equal(probe.responseWidget, 73);
  assert.deepEqual(probe.events.slice(0, 2), ["render", "token"]);
}

async function assertBlockedTurnstileIsFiniteAndDoesNotPost(page, routePath, holdKey, hostSelector) {
  const requested = deferred();
  const release = deferred();
  const analyses = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/analyze-order") analyses.push(request);
  });
  let providerRequests = 0;
  await page.route("https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit", (route) => {
    providerRequests += 1;
    if (providerRequests === 1) return route.abort("blockedbyclient");
    return route.fulfill({
      contentType: "text/javascript; charset=utf-8",
      body: `window.turnstile={
        render:function(box){window.__retryConnected=box.isConnected;return 91},
        getResponse:function(widget){window.__retryWidget=widget;return "cf-retry-token"},
        reset:function(){}
      };`,
    });
  });
  await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
    [holdKey]: { requested, release },
    turnstileSitekey: "sitekey-test",
  }));
  const started = Date.now();
  const navigation = page.goto(ORIGIN + routePath, { waitUntil: "domcontentloaded" });
  await within(requested.promise, "request of the native controller");
  await queueEarlyFile(page);
  release.release();
  await navigation;
  const host = page.locator(hostSelector);
  await host.getByText("No pudimos verificar que sos una persona.", { exact: false })
    .waitFor({ state: "visible" });
  assert.ok(Date.now() - started < 5000, "blocked provider must reach recovery without an infinite poll");
  assert.equal(analyses.length, 0, "configured provider failure must not POST an empty-token bypass");
  assert.equal(await host.locator("#wz-upzone").count(), 1, "the recoverable upload control must remain available");
  const analyzed = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/api/analyze-order",
  );
  await pastePng(page);
  await analyzed;
  await stays(() => providerRequests === 2 && analyses.length === 1, "the retry loads the provider once and posts once", 50);
  assert.equal(providerRequests, 2, "the next file must retry the failed provider load once");
  assert.equal(analyses.length, 1, "the recovered upload must POST once");
  assert.equal(analyses[0].postDataJSON().turnstile_token, "cf-retry-token");
  assert.deepEqual(await page.evaluate(() => ({
    connected: window.__retryConnected,
    widget: window.__retryWidget,
  })), { connected: true, widget: 91 });
}

test("classic twins and window.WzUpload are physically retired", () => {
  assert.equal(existsSync(path.join(ROOT, "service.js")), false);
  assert.equal(existsSync(path.join(ROOT, "wz-upload.js")), false);
  assert.equal(existsSync(path.join(ROOT, "upload-order.js")), false);
  const service = read("app/modules/turnos/service.mjs");
  const upload = read("app/modules/turnos/wz-upload.mjs");
  const order = read("app/modules/turnos/upload-order.mjs");
  const site = patientSiteSource();
  const prep = read("app/modules/turnos/preparation.py");
  assert.match(service, /export function boot/);
  assert.match(order, /export function boot/);
  assert.match(upload, /export \{ CSS as css, zoneHtml, wire, adoptPaintedInput, fileToOrden, analyzeHeaders \}/);
  assert.match(service, /from "\.\/wz-upload\.mjs"/);
  assert.match(order, /from "\.\/wz-upload\.mjs"/);
  assert.doesNotMatch(service, /window\.WzUpload\s*[=.]/);
  assert.doesNotMatch(order, /window\.WzUpload\s*[=.]/);
  assert.doesNotMatch(upload, /window\.WzUpload\s*[=.]/);
  assert.doesNotMatch(service, /\bWzUpload\s*=/);
  assert.doesNotMatch(order, /\bWzUpload\s*=/);
  assert.doesNotMatch(upload, /\bWzUpload\s*=/);
  assert.match(site, /_turnos_controller_scripts\("service\.mjs"/);
  assert.match(site, /takePendingFile: takePendingFile/);
  assert.match(site, /uploadUnavailable: uploadUnavailable/);
  assert.match(prep, /_turnos_controller_scripts\("upload-order\.mjs"/);
  assert.doesNotMatch(site, /static_fingerprint\.url\(["']service\.js["']\)/);
  assert.doesNotMatch(site, /static_fingerprint\.url\(["']wz-upload\.js["']\)/);
  assert.doesNotMatch(prep, /static_fingerprint\.url\(["']upload-order\.js["']\)/);
});

test("the Upload zone story imports the real named exports", () => {
  const story = read("stories/UploadZone.stories.js");
  assert.match(story, /from ["']\.\.\/app\/modules\/turnos\/wz-upload\.mjs["']/);
  assert.match(story, /zoneHtml\(\)/);
  assert.match(story, /wire\(/);
  assert.doesNotMatch(story, /WzUpload|wz-upload\.js|async function|async \(\)/);
});

for (const routePath of ["/book", "/upload-order"]) {
  test(`${routePath}: a failed photo viewer preserves the actual uploaded image`, async () => {
    await withPage(async (page) => {
      let viewerRequests = 0;
      let analyzedImage;
      await page.route(`${ORIGIN}/**`, (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (/\/platform\/photo-zoom\.[a-f0-9]{8}\.mjs$/.test(pathname)) {
          viewerRequests += 1;
          return route.abort("failed");
        }
        if (pathname === "/api/analyze-order") {
          analyzedImage = route.request().postDataJSON().orden;
          return route.fulfill({
            json: { ok: true, token: "photo-fixture", ai: {
              documento: { es_orden: false, tipo: "otro" }, campos: {}, preparacion: [],
            } },
          });
        }
        return fulfillFiles(route);
      });
      await page.goto(ORIGIN + routePath, { waitUntil: "load" });
      await page.locator("#wz-file").waitFor({ state: "attached" });
      await pastePng(page);
      const imageLink = page.locator("a.wz-err-img-btn[data-photozoom]");
      await imageLink.waitFor({ state: "visible" });
      assert.equal(viewerRequests, 1, "actual server markup must request the canonical viewer");
      assert.equal(await imageLink.getAttribute("download"), "order-image");
      const downloading = page.waitForEvent("download");
      await imageLink.click();
      const download = await downloading;
      assert.equal(await download.failure(), null);
      assert.ok(analyzedImage.data_b64, "real upload converter must produce the analyzed image");
      assert.deepEqual(readFileSync(await download.path()), Buffer.from(analyzedImage.data_b64, "base64"));
      assert.equal(page.url(), ORIGIN + routePath, "fallback must preserve the form");
    });
  });
}

test("early paste while service.mjs is held is retained; no window.WzUpload", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdService: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/book", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the service module");
    const delayed = await page.evaluate(() => ({
      zone: !!document.getElementById("wz-upzone"),
      wzUpload: typeof window.WzUpload,
      recovery: /No pudimos cargar esta pantalla/.test(document.body.innerText),
    }));
    assert.equal(delayed.zone, true, "server-first upload zone must stay while the controller is held");
    assert.equal(delayed.wzUpload, "undefined");
    assert.equal(delayed.recovery, false);
    await queueEarlyFile(page);
    const analyze = page.waitForRequest((req) => new URL(req.url()).pathname === "/api/analyze-order");
    release.release();
    await navigation;
    await analyze;
    assert.equal(await page.evaluate(() => typeof window.WzUpload), "undefined");
    assert.match(
      await page.locator("#svc-form-host").innerText(),
      /Estamos leyendo tu orden|Revisamos los datos|Contanos tu caso|Buscando turnos/,
    );
  });
});

test("queued service upload mounts configured Turnstile before replay and posts its token once", async () => {
  await withPage(async (page) => {
    await assertQueuedUploadUsesMountedTurnstile(page, "/book", "holdService");
  });
});

test("blocked Turnstile provider gives service finite recovery, then retries without an empty token", async () => {
  await withPage(async (page) => {
    await assertBlockedTurnstileIsFiniteAndDoesNotPost(page, "/book", "holdService", "#svc-form-host");
  });
});

test("holding wz-upload.mjs independently holds service evaluation and keeps first paint", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdUpload: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/book", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the wz-upload module");
    const delayed = await page.evaluate(() => ({
      zone: !!document.getElementById("wz-upzone"),
      wzUpload: typeof window.WzUpload,
      bootRan: !!document.querySelector(".wz-processing, .wz-steps"),
    }));
    assert.equal(delayed.zone, true);
    assert.equal(delayed.wzUpload, "undefined");
    assert.equal(delayed.bootRan, false, "service.mjs must not evaluate while wz-upload.mjs is held");
    await queueEarlyFile(page);
    const analyze = page.waitForRequest((req) => new URL(req.url()).pathname === "/api/analyze-order");
    release.release();
    await navigation;
    await analyze;
    assert.equal(await page.evaluate(() => typeof window.WzUpload), "undefined");
    assert.match(
      await page.locator("#svc-form-host").innerText(),
      /Estamos leyendo tu orden|Revisamos los datos|Contanos tu caso|Buscando turnos/,
    );
  });
});

test("post-import dependency delay retains only the latest service upload", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdPrep: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/book", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the prep dependency");
    await pastePng(page);
    await pastePng(page);
    const analyses = [];
    page.on("request", (req) => {
      if (new URL(req.url()).pathname === "/api/analyze-order") analyses.push(req);
    });
    release.release();
    await navigation;
    await until(() => analyses.length >= 1, "the captured latest file drains after upload wiring");
    await stays(() => analyses.length === 1, "the captured latest file drains once", 350);
    assert.equal(analyses.length, 1, "the captured latest file must drain once after upload wiring");
  });
});

test("preloaded order hands bootstrap paste capture back to the document", async () => {
  await withPage(async (page) => {
    await page.addInitScript(() => {
      sessionStorage.setItem("st_order_pending", JSON.stringify({ token: "test-accepted", ai: {} }));
    });
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route));
    await page.goto(ORIGIN + "/book?order=test-accepted", { waitUntil: "load" });
    await page.locator("#svc-form-host .sec").waitFor({ state: "visible" });
    // The server HTML itself ships the upload step inside .sec, so ".sec
    // visible" only proves the shell painted — not that the native
    // controller decided. This solicitud_con_orden page boots with the
    // bootstrap paste capture installed, and the controller releases it in
    // the same breath as its preloaded-order decision (uploadUnavailable);
    // wait for THAT handoff instead of racing the module boot, which loses
    // under parallel suite load.
    await page.waitForFunction(() => {
      const file = new File(["x"], "probe.png", { type: "image/png" });
      const event = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "clipboardData", {
        value: { items: [{ kind: "file", type: "image/png", getAsFile() { return file; } }] },
      });
      document.dispatchEvent(event);
      return !event.defaultPrevented;
    });
    assert.equal(await page.locator("#wz-upzone").count(), 0, "accepted-order route must not render an upload control");
    assert.equal(await imagePasteIsPrevented(page), false,
      "the accepted-order route must not retain the bootstrap upload paste listener");
  });
});

async function holdProvider(page) {
  const requested = deferred();
  const release = deferred();
  await page.route("https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit", async (route) => {
    requested.release();
    await release.promise;
    await route.fulfill({
      contentType: "text/javascript; charset=utf-8",
      body: `window.turnstile={
        render:function(){return 88},
        getResponse:function(){return "cf-held-token"},
        reset:function(){}
      };`,
    });
  });
  return { requested, release };
}

async function assertPostWireFilesCoalesce(page, routePath, hostSelector) {
  const provider = await holdProvider(page);
  const analyses = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/analyze-order") analyses.push(request);
  });
  await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { turnstileSitekey: "sitekey-test" }));
  await page.goto(ORIGIN + routePath, { waitUntil: "domcontentloaded" });
  await page.locator(hostSelector + " #wz-file").waitFor({ state: "attached" });
  await within(provider.requested.promise, "request of the Turnstile provider");
  await pastePng(page);
  await pastePng(page);
  const analyzed = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/api/analyze-order",
  );
  provider.release.release();
  await analyzed;
  await stays(() => analyses.length === 1, "multiple post-wire files coalesce into one analysis POST", 100);
  assert.equal(analyses.length, 1, "multiple post-wire files must coalesce into one analysis POST");
  assert.equal(analyses[0].postDataJSON().turnstile_token, "cf-held-token");
}

test("service upload coalesces post-wire files while Turnstile mounts", async () => {
  await withPage(async (page) => {
    await assertPostWireFilesCoalesce(page, "/book", "#svc-form-host");
  });
});

test("service ignores a delayed upload mount after the user leaves its step", async () => {
  await withPage(async (page) => {
    const provider = await holdProvider(page);
    const analyses = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/analyze-order") analyses.push(request);
    });
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { turnstileSitekey: "sitekey-test" }));
    await page.goto(ORIGIN + "/book", { waitUntil: "domcontentloaded" });
    await page.locator("#wz-noorden").waitFor({ state: "visible" });
    await provider.requested.promise;
    await pastePng(page);
    await page.locator("#wz-noorden").click();
    provider.release.release();
    await stays(() => analyses.length === 0, "a file from the discarded upload step does not POST", 150);
    const host = page.locator("#svc-form-host");
    assert.equal(analyses.length, 0, "a file from the discarded upload step must not POST");
    assert.match(await host.innerText(), /Cómo conseguir|orden|pedir/i);
    assert.doesNotMatch(await host.innerText(), /No pudimos verificar que sos una persona/,
      "a stale mount must not paint Turnstile recovery onto the next step");
  });
});

test("held native controller delegates early keyboard chooser and removes that delegate at handoff", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdService: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/book", { waitUntil: "domcontentloaded" });
    await requested.promise;
    await page.evaluate(() => {
      window.__uploadChooserClicks = 0;
      const nativeClick = HTMLInputElement.prototype.click;
      HTMLInputElement.prototype.click = function () {
        if (this.id === "wz-file") window.__uploadChooserClicks += 1;
        return nativeClick.call(this);
      };
    });
    await page.locator("#wz-upzone").press("Enter");
    await page.locator("#wz-upzone").press(" ");
    assert.equal(await page.evaluate(() => window.__uploadChooserClicks), 2,
      "server-first zone must delegate Enter and Space while module is held");
    release.release();
    await navigation;
    // The server-painted zone is visible from the first paint, so its visibility
    // says nothing about the handoff. The controller replaces the zone, adopts the
    // painted input (dropping data-first-paint), wires it and removes the
    // bootstrap delegate in ONE task: wait for that, or the press below can focus
    // the old zone and send its key after the zone is gone (0 clicks).
    await page.locator("#svc-form-host #wz-file:not([data-first-paint])").waitFor({ state: "attached" });
    await page.locator("#wz-upzone").waitFor({ state: "visible" });
    // the held-page keyboard activation guard (a Date.now() deadline on the zone) has expired
    await page.waitForFunction(() => {
      const zone = document.getElementById("wz-upzone");
      return !zone._turnosKeyboardUntil || Date.now() >= zone._turnosKeyboardUntil;
    });
    const before = await page.evaluate(() => window.__uploadChooserClicks);
    await page.locator("#wz-upzone").press("Enter");
    assert.equal(await page.evaluate(() => window.__uploadChooserClicks), before + 1,
      "bootstrap delegate must be removed once the native wire owns keyboard input");
  });
});

test("rejected service.mjs paints localized schedule recovery, not a blank host", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortService: true }));
    await page.goto(ORIGIN + "/book-schedule", { waitUntil: "load" });
    const host = page.locator("#svc-form-host");
    await host.locator(".svc-sub").waitFor({ state: "visible" });
    assert.match(await host.innerText(), /No pudimos cargar esta pantalla/);
    assert.equal(await page.evaluate(() => document.getElementById("svc-form-host").classList.contains("svc-form-host--schedule-recovery")), true);
    assert.equal(await page.evaluate(() => document.getElementById("svc-form-host").classList.contains("svc-form-host--schedule-boot")), false);
    assert.equal(await page.evaluate(() => typeof window.WzUpload), "undefined");
  });
});

test("no-upload and rejected-upload pages leave no bootstrap capture listener", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route));
    await page.goto(ORIGIN + "/book-schedule", { waitUntil: "load" });
    const noUploadPrevented = await page.evaluate(() => {
      const event = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "clipboardData", { value: { items: [{ kind: "file", type: "image/png", getAsFile() { return new File(["x"], "x.png", { type: "image/png" }); } }] } });
      document.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(noUploadPrevented, false, "booking-only path must not retain upload capture");
  });
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortService: true }));
    await page.goto(ORIGIN + "/book", { waitUntil: "load" });
    await page.locator("#svc-form-host .svc-sub").waitFor({ state: "visible" });
    const failedPrevented = await page.evaluate(() => {
      const event = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "clipboardData", { value: { items: [{ kind: "file", type: "image/png", getAsFile() { return new File(["x"], "x.png", { type: "image/png" }); } }] } });
      document.dispatchEvent(event);
      return event.defaultPrevented;
    });
    assert.equal(failedPrevented, false, "rejected controller must remove early upload capture");
  });
});

test("early paste while upload-order.mjs is held is retained", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdOrder: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/upload-order", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the upload-order module");
    assert.equal(await page.evaluate(() => document.getElementById("prep-host").innerHTML), "");
    assert.equal(await page.evaluate(() => typeof window.WzUpload), "undefined");
    await queueEarlyFile(page);
    const analyze = page.waitForRequest((req) => new URL(req.url()).pathname === "/api/analyze-order");
    release.release();
    await navigation;
    await analyze;
    assert.match(
      await page.locator("#prep-host").innerText(),
      /Leyendo tu orden|Dónde sacar el turno|Buscando dónde sacar/,
    );
  });
});

test("queued upload-order mounts configured Turnstile before replay and posts its token once", async () => {
  await withPage(async (page) => {
    await assertQueuedUploadUsesMountedTurnstile(page, "/upload-order", "holdOrder");
  });
});

test("blocked Turnstile provider gives upload-order finite recovery, then retries without an empty token", async () => {
  await withPage(async (page) => {
    await assertBlockedTurnstileIsFiniteAndDoesNotPost(page, "/upload-order", "holdOrder", "#prep-host");
  });
});

test("post-import dependency delay retains upload-order paste until its real wire", async () => {
  await withPage(async (page) => {
    const requested = deferred();
    const release = deferred();
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, {
      holdPrep: { requested, release },
    }));
    const navigation = page.goto(ORIGIN + "/upload-order", { waitUntil: "domcontentloaded" });
    await within(requested.promise, "request of the upload-order prep dependency");
    await pastePng(page);
    const analyze = page.waitForRequest((req) => new URL(req.url()).pathname === "/api/analyze-order");
    release.release();
    await navigation;
    await analyze;
  });
});

test("upload-order coalesces post-wire files while Turnstile mounts", async () => {
  await withPage(async (page) => {
    await assertPostWireFilesCoalesce(page, "/upload-order", "#prep-host");
  });
});

test("rejected upload-order.mjs paints localized recovery in #prep-host", async () => {
  await withPage(async (page) => {
    await page.route(`${ORIGIN}/**`, (route) => fulfillFiles(route, { abortOrder: true }));
    await page.goto(ORIGIN + "/upload-order", { waitUntil: "load" });
    const host = page.locator("#prep-host");
    await host.locator(".svc-sub").waitFor({ state: "visible" });
    assert.match(await host.innerText(), /No pudimos cargar esta pantalla/);
    assert.equal(await page.evaluate(() => typeof window.WzUpload), "undefined");
  });
});
