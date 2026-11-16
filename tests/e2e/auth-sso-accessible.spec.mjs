// Hermetic rendered-DOM accessibility gate for templates/auth-sso.html --
// the LOCAL RENAPER login SIMULATOR served at /auth/sso (not the real
// federated flow, which lives in cuentas/auth_renaper.py + accounts_router.py
// and is deliberately not exercised here).
//
// Chromium loads the template's real markup, CSS and inline script through
// page.route() interception: every request is fulfilled from the test
// (the page, the /api/account/test-identities JSON) or aborted (Google
// Fonts) -- no app server, no database, no network. The template-level gate
// must not require any of that.
//
// Pins the 2026-09-05 accessibility repair (follow-up diagnosis
// astra-81af332c): one semantic h1, AA text contrast on the card and the
// simulator disclosure, >=44px phone targets for the DNI field, the submit
// and every quick-fill button, real label association, zero horizontal
// overflow. Every assertion runs against the RENDERED page (computed
// styles + getBoundingClientRect), never against template source text.
//
// Differences from the node:test original (tests/e2e/auth-sso-accessible.e2e.mjs):
//   - Hermetic as before: the page, the identities and the i18n module are fulfilled by the test and
//     everything else is aborted, so the scenario never touches a server. The file is `hermetic`: no
//     app or database is started (appOptions only names the file for tests/e2e-spec-isolation.test.mjs).
//   - The browser comes from the runner (the original launched its own chromium in a before hook and
//     opened one context per openSsoPage()); each test takes the runner's `page` and sets the width
//     with page.setViewportSize() before the first navigation. The router pages are rendered once
//     per worker in beforeAll (the original rendered them in its before hook).
//   - The optional HUMAN_ROUNDS_EVIDENCE_DIR screenshot is kept (also attached to the report).
//   - Waits are web-first (the 5 s wait for the first quick-fill button is toBeVisible({ timeout: 5000 }));
//     the label click -> focus check is toBeFocused. Every contrast / geometry reading is still one
//     page.evaluate over the live page, unchanged.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { test, expect, ROOT, saveShot } from "./lib/fixtures.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_auth_sso_accessible", hermetic: true } });

const JINJA_FIXTURE = path.join(ROOT, "tests", "test_accounts_jinja.py");
const I18N_ASSET = path.join(ROOT, "static", "platform", "i18n.mjs");

// Synthetic identities in the connector's canonical public shape
// (connectors/renaper/renaper.py normalize_identity). Fictional, local to
// this test -- never a real person. One long name exercises wrapping.
const IDENTITIES = { identidades: [
  { dni: "30111222", nombre: "Ana", apellido: "Gómez", sexo: "F" },
  { dni: "30222333", nombre: "Bruno", apellido: "Díaz", sexo: "M" },
  { dni: "30333444", nombre: "Carla", apellido: "Ferreyra", sexo: "F" },
  { dni: "30444555", nombre: "Diego", apellido: "Ibarra", sexo: "M" },
  { dni: "30555666", nombre: "María Fernanda", apellido: "Rodríguez", sexo: "F" },
  { dni: "30666777", nombre: "Néstor", apellido: "Quintana", sexo: "M" },
] };

// Render through the ACTUAL native account router using the project's existing
// no-DB fixture. This deliberately reuses _ModuleEnvironmentScope,
// _test_auth_on and _native rather than replacing Jinja, i18n bootstrap, or
// the fingerprint manifest with a JavaScript approximation.
const RENDER_SSO = String.raw`
import base64, importlib.util, json, os, sys
root, fixture_path = sys.argv[1:3]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tests"))
spec = importlib.util.spec_from_file_location("accounts_jinja_fixture", fixture_path)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from app.cuentas import accounts_router
pages = {}
with fixture._ModuleEnvironmentScope(), fixture._test_auth_on():
    os.environ["LANGUAGES"] = "es,en"
    fixture._reset_caches()
    for lang in ("es", "en"):
        status, body, headers = fixture._native(
            accounts_router.render_sso, "/auth/sso", lang)
        pages[lang] = {
            "status": status,
            "headers": headers,
            "body": base64.b64encode(body).decode("ascii"),
        }
print(json.dumps(pages, separators=(",", ":")))
`;

function renderRouterPages() {
  const output = execFileSync("python3", ["-c", RENDER_SSO, ROOT, JINJA_FIXTURE], {
    cwd: ROOT,
    encoding: "utf8",
  });
  const encoded = JSON.parse(output);
  return Object.fromEntries(Object.entries(encoded).map(([lang, page]) => [lang, {
    ...page,
    body: Buffer.from(page.body, "base64").toString("utf8"),
  }]));
}

function i18nPathFrom(html) {
  const matched = html.match(/<script>window\.SANA_STATIC=(\{[^<]+\});<\/script>/);
  expect(matched, "router output must include its actual static manifest").toBeTruthy();
  const url = JSON.parse(matched[1])["platform/i18n.mjs"];
  expect(url || "", "router output must publish the canonical fingerprinted i18n module")
    .toMatch(/^\/static\/platform\/i18n\.[a-f0-9]{8}\.mjs$/);
  return url;
}

let rendered = null;

test.beforeAll(() => {
  rendered = renderRouterPages();
});

async function openSsoPage(page, testInfo, width, height, lang = "es") {
  await page.setViewportSize({ width, height });
  const pageErrors = [];
  const requests = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("request", (request) => requests.push(new URL(request.url()).pathname));
  const routerPage = rendered[lang];
  expect(routerPage?.status, `native /auth/sso must render for ${lang}`).toBe(200);
  expect(routerPage?.headers?.["content-type"]).toBe("text/html; charset=utf-8");
  const html = routerPage.body;
  const i18nPath = i18nPathFrom(html);
  await page.route("**/*", (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/auth/sso") {
      return route.fulfill({ contentType: "text/html; charset=utf-8", body: html });
    }
    if (pathname === "/api/account/test-identities") {
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(IDENTITIES),
      });
    }
    if (pathname === i18nPath) {
      return route.fulfill({
        contentType: "text/javascript; charset=utf-8",
        body: fs.readFileSync(I18N_ASSET, "utf8"),
      });
    }
    return route.abort(); // hermetic: the Google Fonts stylesheet never leaves
  });
  await page.goto("https://sso-local.test/auth/sso?provider=renaper&next=%2Faccount");
  await expect(page.locator(".sso-dev-ids button").first()).toBeVisible({ timeout: 5_000 });
  if (process.env.HUMAN_ROUNDS_EVIDENCE_DIR) {
    await saveShot(testInfo, page, path.join(process.env.HUMAN_ROUNDS_EVIDENCE_DIR, `sso-${lang}-${width}.png`), {
      fullPage: true,
    });
  }
  return { page, pageErrors, requests, i18nPath };
}

// One full rendered audit. Everything is measured in the live page:
// WCAG relative-luminance contrast against the first opaque ancestor
// background, bounding-box geometry, and the real inline script's own
// ready-state wiring (the "input" event is dispatched through the page's
// actual listener, not by toggling classes from outside).
async function auditPage(page) {
  return page.evaluate(() => {
    const parseColor = (value) => {
      const m = /rgba?\(([^)]+)\)/.exec(value || "");
      if (!m) return null;
      const p = m[1].split(",").map((s) => parseFloat(s));
      return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
    };
    const lum = (c) => {
      const f = (v) => {
        v /= 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
    };
    const over = (fg, bg) => ({
      r: fg.r * fg.a + bg.r * (1 - fg.a),
      g: fg.g * fg.a + bg.g * (1 - fg.a),
      b: fg.b * fg.a + bg.b * (1 - fg.a),
      a: 1,
    });
    const ratio = (a, b) => {
      const x = lum(a), y = lum(b);
      return Math.round(((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)) * 100) / 100;
    };
    const backdrop = (el) => {
      for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
        const c = parseColor(getComputedStyle(n).backgroundColor);
        if (c && c.a > 0) return c;
      }
      return { r: 255, g: 255, b: 255, a: 1 };
    };
    const textRatio = (el) => {
      if (!el) return null;
      const bg = backdrop(el);
      const fg = over(parseColor(getComputedStyle(el).color) || { r: 0, g: 0, b: 0, a: 1 }, bg);
      return ratio(fg, bg);
    };
    const px = (el) => Math.round(el.getBoundingClientRect().height * 10) / 10;
    const fontPx = (el) => parseFloat(getComputedStyle(el).fontSize);
    const $ = (sel) => document.querySelector(sel);
    const dni = $("#sso-dni");
    const btn = $("#sso-btn");
    const quick = [...document.querySelectorAll(".sso-dev-ids button")];
    const h1 = $("h1");
    const label = $("label[for='sso-dni']");
    const result = {
      h1Count: document.querySelectorAll("h1").length,
      h1: h1 ? {
        text: (h1.innerText || "").split("\n").map((s) => s.trim())
          .filter(Boolean).join(" "),
        heightPx: px(h1),
        ratio: textRatio(h1),
      } : null,
      labelWired: !!(label && dni && dni.labels && [...dni.labels].includes(label)),
      contrast: {
        logoMark: textRatio($(".sso-logo-r")),
        logoSub: textRatio($(".sso-logo-sub")),
        fieldLabel: textRatio($("label.sso-label")),
        dniText: textRatio(dni),
        dniPlaceholder: (() => {
          if (!dni) return null;
          const bg = backdrop(dni);
          const c = parseColor(getComputedStyle(dni, "::placeholder").color);
          return c ? ratio(over(c, bg), bg) : null;
        })(),
        submitIdle: textRatio(btn),
        devText: textRatio($(".sso-dev p")),
        devLink: textRatio($(".sso-dev a")),
        quickFill: quick.length ? textRatio(quick[0]) : null,
      },
      underline: dni ? (() => {
        const c = parseColor(getComputedStyle(dni).borderBottomColor);
        return c ? ratio(c, backdrop(dni)) : null;
      })() : null,
      logoMarkBorder: (() => {
        const mark = $(".sso-logo-r");
        if (!mark) return null;
        const c = parseColor(getComputedStyle(mark).borderTopColor);
        return c ? ratio(c, backdrop(mark)) : null;
      })(),
      targets: {
        dniPx: dni ? px(dni) : null,
        submitPx: btn ? px(btn) : null,
        quickPx: quick.map(px),
      },
      minFontPx: Math.min(
        ...[$(".sso-logo-sub"), $(".sso-label"), $(".sso-submit"), $(".sso-err"),
          $(".sso-dev p"), $(".sso-dev a"), ...quick]
          .filter(Boolean).map(fontPx),
      ),
      bodyTextWrap: getComputedStyle(document.body).textWrap,
      overflow: {
        by: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        pastEdge: [...document.querySelectorAll("*")]
          .filter((el) => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.right > document.documentElement.clientWidth + 1;
          })
          .slice(0, 8)
          .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)}`),
      },
    };
    // Ready-state wiring through the page's REAL input listener (the same
    // code path a keystroke takes), then re-measure the enabled submit.
    dni.value = "30111222";
    dni.dispatchEvent(new Event("input", { bubbles: true }));
    result.ready = {
      hasListo: btn.classList.contains("listo"),
      submitReady: textRatio(btn),
    };
    return result;
  });
}

test("one visible semantic h1 names the simulator, label is wired, script intact (390px)", async ({ page }, testInfo) => {
  const { pageErrors } = await openSsoPage(page, testInfo, 390, 844);
  const audit = await auditPage(page);
  expect(audit.h1Count, "the page must expose exactly one h1").toBe(1);
  expect(audit.h1.text, "the h1 is the existing visible heading text").toBe("Registro Nacional de las Personas");
  expect(audit.h1.heightPx, "the h1 must render visibly").toBeGreaterThan(0);
  expect(audit.labelWired, "label[for=sso-dni] must be programmatically associated with the input").toBe(true);
  await page.locator("label[for='sso-dni']").click();
  await expect(page.locator("#sso-dni"), "clicking the visible document label must focus the DNI input").toBeFocused();
  expect(audit.ready.hasListo, "typing 7+ digits through the real listener must mark the submit ready").toBe(true);
  expect(pageErrors, "the template's inline script must not throw").toEqual([]);
});

test("router-rendered English page loads its fingerprinted i18n module before wiring the simulator", async ({ page }, testInfo) => {
  const { pageErrors, requests, i18nPath } = await openSsoPage(page, testInfo, 390, 844, "en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("label[for='sso-dni']")).toHaveText("ID Number", { useInnerText: true });
  await expect(page.locator("#sso-btn")).toHaveText("Sign In", { useInnerText: true });
  await expect(page.locator(".sso-dev")).toHaveText(/Local simulator\./, { useInnerText: true });
  expect(requests.includes(i18nPath),
    "the browser must request the exact fingerprinted i18n asset named by router output").toBe(true);
  expect(pageErrors, "the real English bootstrap and inline simulator must not throw").toEqual([]);
});

test("every text and the field underline meet AA contrast in the rendered card", async ({ page }, testInfo) => {
  await openSsoPage(page, testInfo, 390, 844);
  const audit = await auditPage(page);
  const cases = Object.entries(audit.contrast)
    .filter(([, v]) => v !== null);
  for (const [name, value] of cases) {
    expect(value, `${name} contrast ${value}:1 -- AA for normal text needs 4.5:1`).toBeGreaterThanOrEqual(4.5);
  }
  expect(audit.underline,
    `DNI underline contrast ${audit.underline}:1 -- WCAG 1.4.11 non-text needs 3:1`).toBeGreaterThanOrEqual(3);
  expect(audit.logoMarkBorder,
    `logo-mark border contrast ${audit.logoMarkBorder}:1 -- WCAG 1.4.11 non-text needs 3:1`).toBeGreaterThanOrEqual(3);
  expect(audit.h1.ratio,
    `h1 contrast ${audit.h1.ratio}:1 -- AA for normal text needs 4.5:1`).toBeGreaterThanOrEqual(4.5);
  expect(audit.ready.submitReady,
    `ready submit contrast ${audit.ready.submitReady}:1 -- AA needs 4.5:1`).toBeGreaterThanOrEqual(4.5);
  expect(audit.minFontPx,
    `smallest rendered human-facing text ${audit.minFontPx}px -- the simulator floor is 16px`).toBeGreaterThanOrEqual(16);
  expect(audit.bodyTextWrap, "body must inherit text-wrap:pretty").toBe("pretty");
});

test("DNI field, submit and every quick-fill button are >=44px phone targets without overflow", async ({ newContext }, testInfo) => {
  for (const width of [390, 360]) {
    // A fresh context per width, as in the original.
    const widthPage = await (await newContext()).newPage();
    await openSsoPage(widthPage, testInfo, width, width === 390 ? 844 : 740);
    const audit = await auditPage(widthPage);
    expect(audit.targets.dniPx,
      `DNI input is ${audit.targets.dniPx}px tall at ${width}px -- needs >=44px`).toBeGreaterThanOrEqual(43.5);
    expect(audit.targets.submitPx,
      `submit is ${audit.targets.submitPx}px tall at ${width}px -- needs >=44px`).toBeGreaterThanOrEqual(43.5);
    expect(audit.targets.quickPx.length, "six synthetic quick-fill buttons render").toBe(6);
    for (const h of audit.targets.quickPx) {
      expect(h, `quick-fill button is ${h}px tall at ${width}px -- needs >=44px`).toBeGreaterThanOrEqual(43.5);
    }
    expect(audit.overflow.by,
      `page overflows ${width}px viewport by ${audit.overflow.by}px`).toBeLessThanOrEqual(1);
    expect(audit.overflow.pastEdge,
      `elements past the right edge at ${width}px: ${audit.overflow.pastEdge.join(", ")}`).toEqual([]);
  }
});

test("quick-fill click still fills the DNI through the real page script (390px)", async ({ page }, testInfo) => {
  await openSsoPage(page, testInfo, 390, 844);
  await page.locator(".sso-dev-ids button").first().click();
  const after = await page.evaluate(() => ({
    value: document.getElementById("sso-dni").value,
    listo: document.getElementById("sso-btn").classList.contains("listo"),
  }));
  expect(after.value, "clicking a synthetic identity fills the DNI field").toBe(IDENTITIES.identidades[0].dni);
  expect(after.listo, "the submit turns ready after a quick fill").toBe(true);
});

test("the wedge greeting passes large-text AA where the wedge renders (1280px)", async ({ page }, testInfo) => {
  await openSsoPage(page, testInfo, 1280, 900);
  const audit = await auditPage(page);
  expect(audit.overflow.by, `page overflows 1280px viewport by ${audit.overflow.by}px`).toBeLessThanOrEqual(1);
  const wedge = await page.evaluate(() => {
    const parseColor = (value) => {
      const m = /rgba?\(([^)]+)\)/.exec(value || "");
      if (!m) return null;
      const p = m[1].split(",").map((s) => parseFloat(s));
      return { r: p[0], g: p[1], b: p[2], a: 1 };
    };
    const hola = document.querySelector(".sso-hola");
    if (!hola) return null;
    // The greeting sits on the wedge's FRONT polygon (::before, the
    // z-index:1 layer under the text), not the soft back layer.
    const front = parseColor(
      getComputedStyle(document.querySelector(".sso-wedge"), "::before").backgroundColor);
    const text = parseColor(getComputedStyle(hola).color);
    const lum = (c) => {
      const f = (v) => {
        v /= 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      };
      return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
    };
    const x = lum(text), y = lum(front);
    return Math.round(((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)) * 100) / 100;
  });
  // 30-44px at weight 500 is WCAG large text: 3:1 is the bar.
  expect(wedge !== null && wedge >= 3,
    `greeting on the wedge is ${wedge}:1 -- large-text AA needs 3:1`).toBe(true);
});
