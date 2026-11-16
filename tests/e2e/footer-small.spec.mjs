// Public footer: its notice runs the footer's full width (the product owner, 2026-10-28,
// screenshot of «Turno confirmado»: the Decreto paragraph no longer a 72-character column).
// Demo review 2026-11-15: the footer is key information, so the notice and the links are
// 15px and «Urgencias» 16px (it was 13/14px, «smaller than the page»), and every line —
// the «Funciona con Human Rounds» credit too — is in the body's ink. Both themes. The disposable example-town fixture carries no priority notice, so this spec points
// the app at a throwaway copy of it with the Pinamar-style `priority_notice` and
// `emergency_number: 107` (legacy TURNOS_WEBDATA_DIR mode always wins over the pack chain).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  test, expect, saveShot, artifactPath, setTheme, note,
} from "./lib/fixtures.mjs";

const NOTICE = "De acuerdo con el Decreto 1116/26, tienen prioridad para acceder a los turnos, estudios y "
  + "prestaciones médicas programadas los residentes permanentes de Pinamar (con al menos 6 meses de "
  + "domicilio legal y real) y los propietarios de inmuebles en el Partido, según la disponibilidad de cada servicio.";

function noticePack() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "footer-small-pack-"));
  fs.cpSync(path.join(import.meta.dirname, "..", "..", "packs", "example-town"), dir, { recursive: true });
  const file = path.join(dir, "sitio.json");
  const sitio = JSON.parse(fs.readFileSync(file, "utf8"));
  sitio.emergency_number = "107";
  sitio.priority_notice = NOTICE;
  fs.writeFileSync(file, JSON.stringify(sitio, null, 1));
  return dir;
}

const PACK_DIR = noticePack();
test.use({ appOptions: { dbName: "sana_turnos_test_e2e_footer_small", env: { TURNOS_WEBDATA_DIR: PACK_DIR, PACK_CHAIN: "" } } });
test.afterAll(() => { fs.rmSync(PACK_DIR, { recursive: true, force: true }); });

const EVIDENCE = artifactPath("human-rounds", "footer-small-2026-10-28", "after");
const ROUTES = ["/", "/about.html"];
const WIDTHS = [390, 768, 1280, 1440];

function probe(page) {
  return page.evaluate(() => {
    const footer = document.querySelector("footer");
    const notice = footer.querySelector(".fnotice:not(.furg)");
    const urg = footer.querySelector(".furg");
    const lineHeight = parseFloat(getComputedStyle(notice).lineHeight);
    const nr = notice.getBoundingClientRect();
    // The notice's own container (the footer, or about's inner box) is what it must fill.
    const box = notice.parentElement;
    const fr = box.getBoundingClientRect();
    const fs = getComputedStyle(box);
    const contentWidth = fr.width - parseFloat(fs.paddingLeft) - parseFloat(fs.paddingRight);
    const links = [...footer.querySelectorAll("a:not(.stz-link)")].map((a) => getComputedStyle(a).fontSize);
    const linkColors = [...footer.querySelectorAll("a:not(.stz-link)")].map((a) => getComputedStyle(a).color);
    return {
      noticeSize: getComputedStyle(notice).fontSize,
      noticeColor: getComputedStyle(notice).color,
      textColor: getComputedStyle(document.body).color,
      urgSize: getComputedStyle(urg).fontSize,
      noticeWidth: nr.width,
      footerWidth: contentWidth,
      noticeLines: Math.round(nr.height / lineHeight),
      links,
      linkColors,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    };
  });
}

for (const theme of ["pinamar", "humanrounds"]) {
  test(`small footer, notice on the full width, ${theme} theme`, async ({ app, newContext, request, cleanup }, testInfo) => {
    test.setTimeout(180_000);
    cleanup(() => setTheme(app.dbName, "humanrounds").catch(() => {}));
    await setTheme(app.dbName, theme);
    // core/theme.py caches theme.current() for ~3s: wait until the server serves the new one.
    await expect.poll(async () => {
      const html = await (await request.get(app.baseUrl + "/")).text();
      return /<html[^>]*\sdata-theme="([^"]*)"/.exec(html)?.[1];
    }, { message: `the server never started serving the ${theme} theme`, timeout: 10_000 }).toBe(theme);

    for (const route of ROUTES) {
      for (const width of WIDTHS) {
        const context = await newContext({ viewport: { width, height: 800 } });
        const page = await context.newPage();
        try {
          await page.goto(route, { waitUntil: "load", timeout: 20_000 });
          await page.locator("footer .fnotice:not(.furg)").waitFor({ state: "attached", timeout: 10_000 });
          await page.evaluate(() => document.fonts.ready.then(() => undefined));
          const where = `${route} at ${width}px, ${theme}`;
          const p = await probe(page);
          expect(p.noticeSize, where).toBe("15px");
          expect(p.urgSize, where).toBe("16px");
          expect(p.links.length, `${where}: footer links present`).toBeGreaterThan(0);
          for (const size of p.links) expect(size, `${where}: footer links are 15px`).toBe("15px");
          for (const color of p.linkColors) expect(color, `${where}: footer links and credit in the body's ink`).toBe(p.textColor);
          expect(p.noticeColor, `${where}: the notice is in the body's ink, not grey`).toBe(p.textColor);
          expect(p.noticeWidth, `${where}: the notice is the footer's full width`).toBeGreaterThanOrEqual(p.footerWidth - 1);
          expect(p.scrollWidth, `${where}: horizontal overflow`).toBeLessThanOrEqual(p.clientWidth);
          if (width >= 1280) expect(p.noticeLines, `${where}: about two lines on a desktop`).toBeLessThanOrEqual(3);
          if (width === 390 || width === 1280) {
            await page.locator("footer").scrollIntoViewIfNeeded();
            await saveShot(testInfo, page, path.join(EVIDENCE, `${theme}-${route.replace(/[^a-z]+/g, "_")}-${width}.png`));
          }
          note(testInfo, `ok ${where}: ${p.noticeLines} lines`);
        } finally { await context.close(); }
      }
    }
  });
}
