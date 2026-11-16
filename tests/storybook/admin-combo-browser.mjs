// Explicit browser acceptance for the built AdminCombo Storybook catalog.
// This runner deliberately fails without its URL so a local unit invocation
// cannot be mistaken for rendered-story verification.
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { stays } from "../helpers/wait.mjs";

const baseUrl = process.env.ADMIN_COMBO_STORYBOOK_URL;
if (!baseUrl) throw new Error("ADMIN_COMBO_STORYBOOK_URL is required for the AdminCombo Storybook browser gate");
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const evidenceDir = process.env.ADMIN_COMBO_STORY_EVIDENCE_DIR
  || resolve(repoRoot, "artifacts/admin-combo-story-coverage/screenshots");
mkdirSync(evidenceDir, { recursive: true });

const failures = [];
const widths = [360, 390, 430, 768, 1280];
const url = (id) => `${baseUrl}/iframe.html?id=${encodeURIComponent(id)}`;

function attach(page, label) {
  page.on("pageerror", (error) => failures.push({ label, pageerror: String(error) }));
  page.on("response", (response) => {
    if (response.status() >= 400) failures.push({ label, status: response.status(), url: response.url() });
  });
}

async function open(page, id) {
  await page.goto(url(id), { waitUntil: "networkidle" });
}

async function assertFitOpen(page, width, label) {
  const fit = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const overflowers = [];
    document.querySelectorAll("*").forEach((element) => {
      const rect = element.getBoundingClientRect();
      if (rect.width > 0 && rect.right > viewport + 1) {
        overflowers.push({ tag: element.tagName.toLowerCase(), className: String(element.className || "").slice(0, 45), right: Math.round(rect.right) });
      }
    });
    return { overflow: document.documentElement.scrollWidth - viewport, overflowers: overflowers.slice(0, 12) };
  });
  assert.ok(fit.overflow <= 1, `${label} at ${width}px page overflow: ${JSON.stringify(fit)}`);
  assert.deepEqual(fit.overflowers, [], `${label} at ${width}px overflowing elements: ${JSON.stringify(fit)}`);
  if (width <= 430) {
    const clippedMenus = await page.locator(".cmb-list:visible").evaluateAll((menus) =>
      menus.filter((menu) => menu.scrollWidth > menu.clientWidth + 1).map((menu) => ({ scroll: menu.scrollWidth, width: menu.clientWidth })));
    assert.deepEqual(clippedMenus, [], `${label} at ${width}px has no horizontal menu scrolling`);
  }
}

async function assertPhoneComboTargets(page, width, label) {
  if (width > 430) return;
  const targets = await page.locator(".cmb-in, .cmb-it, .cmb-none").evaluateAll((elements) =>
    elements.filter((element) => {
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
    }).map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        className: element.className,
        height: rect.height,
        fontSize: getComputedStyle(element).fontSize,
      };
    }),
  );
  const inputs = targets.filter((target) => String(target.className).split(/\s+/).includes("cmb-in"));
  assert.ok(inputs.length, `${label} at ${width}px renders the shared editable input`);
  for (const input of inputs) assert.ok(parseFloat(input.fontSize) >= 16, `${label} at ${width}px avoids iOS input zoom`);
  for (const target of targets) {
    assert.ok(target.height >= 44,
      `${label} at ${width}px has a 44px combo target: ${JSON.stringify(target)}`);
  }
}

const browser = await chromium.launch({ headless: true });
try {
  for (const width of widths) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage();
    attach(page, `selection-${width}`);
    try {
      await open(page, "admin-combo-tipeable--seleccion-ordinaria");
      const input = page.locator(".cmb-in").last();
      await input.focus();
      await page.locator(".cmb-list").last().waitFor({ state: "visible" });
      await assertFitOpen(page, width, "selection list open");
      await assertPhoneComboTargets(page, width, "selection list open");
      if (width === 360) {
        const smallFont = await page.addStyleTag({ content: ".cmb-in{font-size:13px!important}" });
        await assert.rejects(() => assertPhoneComboTargets(page, width, "negative small-font control"), /iOS input zoom/);
        await smallFont.evaluate((element) => element.remove());
        const shortTarget = await page.addStyleTag({ content: ".cmb-in{min-height:0!important;height:38px!important}" });
        await assert.rejects(() => assertPhoneComboTargets(page, width, "negative short-target control"), /44px combo target/);
        await shortTarget.evaluate((element) => element.remove());
        await assertPhoneComboTargets(page, width, "restored production controls");
      }
      await page.screenshot({ path: `${evidenceDir}/admin-combo-open-${width}.png`, fullPage: true });
      await input.press("ArrowDown");
      await input.press("Enter");
      assert.notEqual(await page.locator(".cmb").last().locator(".fr-svc-sel").inputValue(), "",
        "ArrowDown + Enter commits a canonical catalog value");

      await input.fill("not a real service");
      await input.press("Enter");
      await input.evaluate((element) => element.blur());
      await stays(async () => await page.locator(".cmb").last().locator(".fr-svc-sel").inputValue() === "cardiologia-sampleton-hospital",
        "the canonical value survives the unmatched keyboard attempt", 150);
      assert.equal(await page.locator(".cmb").last().locator(".fr-svc-sel").inputValue(), "cardiologia-sampleton-hospital",
        "the strict catalog policy retains the canonical value after an unmatched keyboard attempt");
    } finally {
      await context.close();
    }

    const longContext = await browser.newContext({ viewport: { width, height: 900 } });
    const longPage = await longContext.newPage();
    attach(longPage, `long-labels-${width}`);
    try {
      await open(longPage, "admin-combo-tipeable--etiquetas-largas");
      const input = longPage.locator(".cmb-in").last();
      await input.focus();
      await longPage.locator(".cmb-list").last().waitFor({ state: "visible" });
      assert.match(await longPage.locator(".cmb-list").last().innerText(), /Curaciones y control de heridas/);
      await assertFitOpen(longPage, width, "long-label list open");
      await assertPhoneComboTargets(longPage, width, "long-label list open");
      await longPage.screenshot({ path: `${evidenceDir}/admin-combo-long-labels-${width}.png`, fullPage: true });
    } finally {
      await longContext.close();
    }
  }

  const policyContext = await browser.newContext({ viewport: { width: 390, height: 900 } });
  const policyPage = await policyContext.newPage();
  attach(policyPage, "policy-and-hours");
  try {
    await open(policyPage, "admin-combo-tipeable--busqueda-sin-resultados");
    assert.ok(await policyPage.locator(".cmb-none").count() >= 2, "no-match scenes render the shared empty state");
    await assertPhoneComboTargets(policyPage, 390, "no-match result");
    await open(policyPage, "admin-combo-tipeable--texto-libre");
    assert.equal(await policyPage.locator(".med-sel").inputValue(), "Dra. Nueva", "doctor policy accepts free text");
    assert.equal(await policyPage.locator(".fr-svc-sel").inputValue(), "cardiologia-sampleton-hospital", "service policy rejects free text");
    await open(policyPage, "admin-horarios-del-médico--un-centro-sin-cierres");
    assert.ok(await policyPage.locator(".fr-svc-sel").count() >= 2, "DoctorHours uses injected combo inputs");
    assert.equal(await policyPage.locator(".cnt-franja-svc select").count(), 0, "DoctorHours does not render a native service select");
    await policyPage.locator(".cmb-in").last().focus();
    await assertPhoneComboTargets(policyPage, 390, "DoctorHours shared controls");
    await assertFitOpen(policyPage, 390, "DoctorHours open service list");
  } finally {
    await policyContext.close();
  }
  assert.deepEqual(failures, []);
} finally {
  await browser.close();
}
