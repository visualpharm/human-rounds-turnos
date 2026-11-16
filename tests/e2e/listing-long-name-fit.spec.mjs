// Browser regression for the listing engine's internal horizontal overflow
// with long names (objective astra-listing-long-name-fit, 2026-09-05).
//
// The defect: at desktop widths a long center/service name (nowrap cell)
// pushes the table's min-content width past .lst-scroll, so the appointment
// pill and its "More times" link land OUTSIDE the visible listing box — while
// document.scrollWidth still equals the viewport, because .lst-scroll
// (overflow-x:auto) absorbs the overflow internally. A page-only overflow
// probe passes; the bug is invisible to it. That is why this test measures
// every cell's bounding box against the .lst-scroll CLIENT box, not against
// the document.
//
// Reproduction is the real engine on a real served page: the harness boots
// the app against the synthetic example-town pack, we open the home page and
// render TWO extra mounts through the REAL module URL from
// window.SANA_STATIC (the same fingerprinted listing.<hash>.mjs every page
// loads) — a services table (servicio · centro · orden · próximo, the
// cluster/search consumer) and a doctors table (medico · especialidad ·
// centro · orden · próximo with avatar, the /doctors consumer). Rows carry
// FICTIONAL long Spanish and English names; no pack or catalog data is
// touched. Both pages' languages are exercised (es default, en through the
// real persisted language cookie on a LANGUAGES=es,en server).
//
// Acceptance (per width 360/390/768/1024/1280/1440, per language, per consumer):
// no page-level horizontal overflow, no internal .lst-scroll overflow, and
// every cell / appointment text / action link fully inside both the listing
// box and the viewport. Nothing hidden, clipped or truncated to get there.
//
// Port of tests/e2e/listing-long-name-fit.e2e.mjs (2 tests, same titles, same order).
//
// Differences from the node:test original:
// - The app is the worker-scoped `app` fixture (same dbName, `LANGUAGES=es,en`); the page is
//   the runner's `page` (default 1280x720, resized per width exactly as before).
// - Evidence screenshots (full page per width, plus the two mounts at 390 and 1280) go through
//   `saveShot` into the same directory (`LISTING_FIT_QA_DIR`, else the artifacts tree via
//   `artifactPath`, which honours TURNOS_E2E_ARTIFACTS_DIR) and are attached to the report.
//   The original hard-coded `~/.claude/artifacts`, the same default.
// - Waits are web-first where the original called `waitForSelector`/`count()` and compared
//   (`toBeVisible`, `toBeAttached`, `toHaveText` with `useInnerText`); the readiness waits
//   on the native module URL, the chat-open class and the «no CSS transition running»
//   settle stay `waitForFunction`/`evaluate`: they wait for a state, they are not sleeps.
//   The fit probes themselves are unchanged point-in-time `page.evaluate` snapshots.
// - Locators: ids/classes stay CSS (#fit-*, .lst-*, #stz-chat-*): the geometry of exactly
//   those nodes is the claim.
import path from "node:path";
import { test, expect, saveShot, artifactPath } from "./lib/fixtures.mjs";

test.use({
  appOptions: {
    dbName: "sana_turnos_test_e2e_listing_fit",
    // LANGUAGES=es,en: the real ?lang= switch must be enabled for the English
    // leg — the listing's own labels and the date phrases come from the real
    // request-scoped catalog pipeline, not from a fixture dictionary.
    env: { LANGUAGES: "es,en" },
  },
});

const QA_DIR = process.env.LISTING_FIT_QA_DIR ||
  artifactPath("human-rounds", "listing-fit", `${Date.now()}-${process.pid}`);

// 1440: beside the open chat the page rests at 1000px, inside the 901–1120
// container band (TASK-28) — 1280 only passes through it while animating.
const WIDTHS = [360, 390, 768, 1024, 1280, 1440];

const tomorrow = (() => {
  const d = new Date(Date.now() + 864e5);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
    "-" + String(d.getDate()).padStart(2, "0");
})();
const nextWeek = (() => {
  const d = new Date(Date.now() + 12 * 864e5);
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
    "-" + String(d.getDate()).padStart(2, "0");
})();

// FICTIONAL catalog rows (never real people or services): long names chosen
// to exceed any column at the widths under test, in both site languages.
const ROWS_ES = {
  servicios: [
    {
      nombre: "Programa de Detección Temprana y Seguimiento de Enfermedades Crónicas No Transmisibles del Partido",
      centro: "Hospital Interzonal de Agudos y Centro de Especialidades Médicas Ambulatorias del Norte",
      proximo: { v: "Mañana, 09:30", kind: "turno", dia: tomorrow, hora: "09:30", sort: "0" + tomorrow + "09:30" },
      href: "/service/fit-1", turno_href: "/service/fit-1/book",
      slot_href: "/service/fit-1/book?slot=" + tomorrow + "T09:30",
      info_href: "/service/fit-1/book?slots=1",
      centro_href: "/center/fit-norte", orden: false,
    },
    {
      nombre: "Clínica médica",
      centro: "Centro de Atención Primaria y Especialidades Médicas Ambulatorias Riverside de las Sierras Grandes",
      proximo: { v: "", kind: "turno", dia: nextWeek, hora: "15:00", sort: "0" + nextWeek + "15:00" },
      href: "/service/fit-2", turno_href: "/service/fit-2/book",
      slot_href: "/service/fit-2/book?slot=" + nextWeek + "T15:00",
      info_href: "/service/fit-2/book?slots=1",
      centro_href: "/center/fit-rincon", orden: true,
    },
    {
      nombre: "Extracciones de laboratorio",
      centro: "Anexo del Hospital Escuela Regional de Alta Complejidad y Centro de Diagnóstico Avanzado del Sur",
      proximo: { v: "Por orden de llegada", kind: "walkin", sort: "1b" },
      href: "/service/fit-3", turno_href: "/service/fit-3",
      info_href: "/service/fit-3", centro_href: "/center/fit-sur",
    },
    {
      nombre: "Consulta de medicina del deportista y evaluación funcional del aparato locomotor",
      centro: "CAPS",
      proximo: { v: "Te confirma el centro", kind: "solicitud", sort: "2" },
      href: "/service/fit-4", turno_href: "/service/fit-4/book",
      centro_href: "/center/fit-caps", orden: true,
    },
    {
      // «No hay turnos disponibles» + «Se habilitan desde el …»: held on
      // one line it took the whole spare width (product owner, 2026-11-13).
      nombre: "Control de salud integral",
      centro: "CAPS",
      proximo: { v: "", kind: "solicitud", habilita: { estado: "fecha", dia: nextWeek, hora: "08:00" }, sort: "2" },
      href: "/service/fit-5", turno_href: "/service/fit-5/book",
      centro_href: "/center/fit-caps", orden: false,
    },
  ],
  medicos: [
    {
      nombre: "Alejo Varela Noguera y de la Fuente",
      avatar_ini: "A", avatar_c: 2,
      especialidad: "Medicina interna y terapéutica clínica del adulto mayor",
      centro: "Hospital Interzonal de Agudos y Centro de Especialidades Médicas Ambulatorias del Norte",
      proximo: { v: "Mañana, 10:15", kind: "turno", dia: tomorrow, hora: "10:15", sort: "0" + tomorrow + "10:15" },
      href: "/doctor/fit-1", turno_href: "/doctor/fit-1/book",
      slot_href: "/doctor/fit-1/book?slot=" + tomorrow + "T10:15",
      info_href: "/doctor/fit-1/book?slots=1",
      centro_href: "/center/fit-norte", orden: true,
    },
    {
      nombre: "Rosa Rivero Cárdenas de Abbatiello",
      avatar_ini: "R", avatar_c: 4,
      especialidad: "Cardiología clínica y ecocardiografía de estrés",
      centro: "Centro de Atención Primaria y Especialidades Médicas Ambulatorias Riverside de las Sierras Grandes",
      proximo: { v: "Por orden de llegada", kind: "walkin", sort: "1b" },
      href: "/doctor/fit-2", turno_href: "/doctor/fit-2",
      info_href: "/doctor/fit-2", centro_href: "/center/fit-rincon", orden: false,
    },
    {
      nombre: "Marta Quiroga",
      avatar_ini: "M", avatar_c: 1,
      especialidad: "Dermatología",
      centro: "CAPS",
      proximo: { v: "", kind: "solicitud", habilita: { estado: "fecha", dia: nextWeek, hora: "08:00" }, sort: "2" },
      href: "/doctor/fit-3", turno_href: "/doctor/fit-3/book",
      centro_href: "/center/fit-caps", orden: true,
    },
  ],
};

const ROWS_EN = {
  servicios: [
    {
      nombre: "Comprehensive Community Health Screening and Chronic Disease Follow-Up Program",
      centro: "Northside Community Specialty Clinic and Ambulatory Diagnostic Center of the Northern Metropolitan Region",
      proximo: { v: "", kind: "turno", dia: tomorrow, hora: "09:30", sort: "0" + tomorrow + "09:30" },
      href: "/service/fit-1", turno_href: "/service/fit-1/book",
      slot_href: "/service/fit-1/book?slot=" + tomorrow + "T09:30",
      info_href: "/service/fit-1/book?slots=1",
      centro_href: "/center/fit-north", orden: false,
    },
    {
      nombre: "Pediatric Developmental Assessment and Early Intervention Referral Clinic",
      centro: "Eastside Teaching Hospital Annex and Regional Outpatient Specialty Wing",
      proximo: { v: "", kind: "turno", dia: nextWeek, hora: "15:00", sort: "0" + nextWeek + "15:00" },
      href: "/service/fit-2", turno_href: "/service/fit-2/book",
      slot_href: "/service/fit-2/book?slot=" + nextWeek + "T15:00",
      info_href: "/service/fit-2/book?slots=1",
      centro_href: "/center/fit-east", orden: true,
    },
    {
      nombre: "Walk-in blood draw",
      centro: "Southside Neighborhood Health Center and Community Care Annex",
      proximo: { v: "In order of arrival", kind: "walkin", sort: "1b" },
      href: "/service/fit-3", turno_href: "/service/fit-3",
      info_href: "/service/fit-3", centro_href: "/center/fit-south",
    },
    {
      nombre: "Comprehensive health check-up",
      centro: "CAPS",
      proximo: { v: "", kind: "solicitud", habilita: { estado: "fecha", dia: nextWeek, hora: "08:00" }, sort: "2" },
      href: "/service/fit-5", turno_href: "/service/fit-5/book",
      centro_href: "/center/fit-caps", orden: false,
    },
  ],
  medicos: [
    {
      nombre: "Magdalena Teresa Fitzwilliam-Ordóñez y Bermejillo",
      avatar_ini: "M", avatar_c: 1,
      especialidad: "Internal medicine and adult primary care",
      centro: "Northside Community Specialty Clinic and Ambulatory Diagnostic Center of the Northern Metropolitan Region",
      proximo: { v: "", kind: "turno", dia: tomorrow, hora: "10:15", sort: "0" + tomorrow + "10:15" },
      href: "/doctor/fit-1", turno_href: "/doctor/fit-1/book",
      slot_href: "/doctor/fit-1/book?slot=" + tomorrow + "T10:15",
      info_href: "/doctor/fit-1/book?slots=1",
      centro_href: "/center/fit-north", orden: true,
    },
    {
      nombre: "Martha Quiroga",
      avatar_ini: "M", avatar_c: 3,
      especialidad: "Dermatology",
      centro: "CAPS",
      proximo: { v: "", kind: "solicitud", habilita: { estado: "fecha", dia: nextWeek, hora: "08:00" }, sort: "2" },
      href: "/doctor/fit-3", turno_href: "/doctor/fit-3/book",
      centro_href: "/center/fit-caps", orden: false,
    },
  ],
};

// Renders both mounts through the REAL module the page itself ships, as
// direct children of .wrap — the ≥1121px breakout (.wrap>[data-listado]) is
// part of the bug's conditions, so the mounts must sit exactly where the
// server puts its own listings.
async function injectFitMounts(page, lang) {
  await page.evaluate(async ({ lang }) => {
    const rows = lang === "en" ? window.__FIT_ROWS_EN : window.__FIT_ROWS_ES;
    const mod = await import(window.SANA_STATIC["platform/listing.mjs"]);
    const wrap = document.querySelector(".wrap");
    // The doctors' table three ways: the search page (every column), a
    // center's page (no Centro) and a study's «Quién atiende» (no Centro, no
    // Orden) — the 901–1120 grid must have one track per shown column.
    const DOCTOR_COLS = {
      // /doctors keeps Centro on a phone too (review 2026-11-15, P6/D17)
      "fit-medicos": ["medico", "especialidad", "centro", "orden", "proximo"],
      "fit-medicos-centro": ["medico", "especialidad", "orden", "proximo"],
      "fit-medicos-estudio": ["medico", "especialidad", "proximo"],
    };
    for (const id of ["fit-servicios", ...Object.keys(DOCTOR_COLS)]) {
      document.getElementById(id)?.remove();
      const el = document.createElement("div");
      el.className = "sec";
      el.id = id;
      wrap.appendChild(el);
      mod.render(el, id === "fit-servicios"
        ? { cols: ["servicio", "centro", "proximo", "orden"], rows: rows.servicios,
            options: { rowHref: "href", search: true, searchGroup: "fit-cluster",
                       searchPlaceholder: "Servicio, centro o localidad",
                       searchLabel: "Buscar servicio, centro o localidad" } }
        : { cols: DOCTOR_COLS[id],
            rows: rows.medicos,
            options: { title: "Médicos y profesionales", rowHref: "href", searchGroup: "fit-cluster" } });
    }
  }, { lang });
}

// The probe the page-only assertion cannot replace: every visible datum of
// the mounts must fit inside BOTH its .lst-scroll's client box and the
// viewport. Returns the offenders (empty = pass) plus both overflow metrics.
async function probeFit(page, lang) {
  return page.evaluate(({ lang }) => {
    const vw = document.documentElement.clientWidth;
    const pageHOverflow = document.documentElement.scrollWidth - vw;
    const bad = [];
    const internal = [];
    const mounts = ["fit-servicios", "fit-medicos", "fit-medicos-centro", "fit-medicos-estudio"]
      .map((id) => document.getElementById(id));
    for (const mount of mounts) {
      if (!mount) { bad.push({ lang, mount: "missing", cls: "", what: "mount not rendered" }); continue; }
      mount.querySelectorAll(".lst-scroll").forEach((sc) => {
        if (sc.scrollWidth > sc.clientWidth + 1) {
          internal.push({ mount: mount.id, over: sc.scrollWidth - sc.clientWidth });
        }
      });
      const cells = mount.querySelectorAll(
        ".lst-table th, .lst-table td, .lst-time, .lst-more, .lst-timecell, .lst-time-note, " +
        ".lst-name, .lst-txt, .lst-open, .lst-open-note, .lst-when, .lst-orden, .lst-orden-sin, " +
        ".lst-person-specialty, .lst-action, .lst-closed, .lst-closed-when");
      // A cell whose content is taller than the cell paints over the next
      // row (the doctor grid's fixed 56px cell under «Más horarios», 2026-11-13).
      mount.querySelectorAll(".lst-table tbody td").forEach((td) => {
        if (td.offsetParent && td.scrollHeight > td.clientHeight + 1) {
          bad.push({ lang, mount: mount.id, cls: String(td.className).slice(0, 50),
                     what: `spills: content ${td.scrollHeight}px tall in a ${td.clientHeight}px cell` });
        }
      });
      // A grid row with more tracks than shown cells leaves an empty track
      // mid-row (a center's page in the 901–1120 band, 2026-11-13).
      mount.querySelectorAll(".lst-table tbody tr").forEach((tr) => {
        const cs = getComputedStyle(tr);
        if (cs.display !== "grid") return;
        const tracks = cs.gridTemplateColumns.trim().split(/\s+/).length;
        const shown = [...tr.children].filter((td) => getComputedStyle(td).display !== "none").length;
        if (tracks !== shown) {
          bad.push({ lang, mount: mount.id, cls: "lst-table tr",
                     what: `${tracks} grid tracks for ${shown} shown cells` });
        }
      });
      cells.forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) return;
        const sc = el.closest(".lst-scroll");
        const limit = sc ? sc.getBoundingClientRect().left + sc.clientWidth : vw;
        const cap = Math.min(limit, vw);
        if (r.right > cap + 1 || r.left < -1) {
          bad.push({ lang, mount: mount.id, cls: String(el.className).slice(0, 60),
                     what: `ends at ${Math.round(r.right)}px, limit ${Math.round(cap)}px` });
        }
      });
    }
    const avatars = [...document.querySelectorAll('[id^="fit-medicos"] .lst-linkcell--av')].map((link) => {
      const avatar = link.querySelector(".lst-av")?.getBoundingClientRect();
      const name = link.querySelector(".lst-name")?.getBoundingClientRect();
      const copy = link.querySelector(".lst-person-copy")?.getBoundingClientRect();
      const action = link.closest("tr")?.querySelector(".lst-td-proximo")?.getBoundingClientRect();
      return {
        display: getComputedStyle(link).display,
        avatarRight: Math.round(avatar?.right || 0),
        nameLeft: Math.round(name?.left || 0),
        copyBottom: copy?.bottom || 0,
        actionTop: action?.top || 0,
        stacked: getComputedStyle(link.closest("tr")).display === "block",
      };
    });
    return { vw, pageHOverflow, internal, bad: bad.slice(0, 12), avatars };
  }, { lang });
}

for (const lang of ["es", "en"]) {
  test(`listing keeps long ${lang.toUpperCase()} names inside the listing and the viewport at every width`, async ({ page, app }, testInfo) => {
    test.setTimeout(120_000);
    if (lang === "en") {
      await page.context().addCookies([{ name: "lang", value: "en", url: app.baseUrl }]);
    }
    await page.goto("/", { waitUntil: "load", timeout: 15000 });
    // The home page's empty #resultados is intentionally hidden before a
    // search. This regression mounts its own real-listing consumers, so the
    // genuine readiness boundary is the server-supplied native module URL.
    await page.waitForFunction(() => window.SANA_STATIC &&
      window.SANA_STATIC["platform/listing.mjs"], null, { timeout: 15000 });
    await page.evaluate((rows) => {
      window.__FIT_ROWS_ES = rows.es;
      window.__FIT_ROWS_EN = rows.en;
    }, { es: ROWS_ES, en: ROWS_EN });

    if (lang === "en") {
      const english = await page.evaluate(() => ({
        language: document.documentElement.lang,
        next: window.SANA_I18N?.catalog?.["listing.col.proximo"],
        book: window.SANA_I18N?.catalog?.["listing.book_verb"],
        more: window.SANA_I18N?.catalog?.["listing.more_times"],
      }));
      expect(english, "English rows must use the real active English catalog, not Spanish fallbacks").toEqual({
        language: "en", next: "Next availability", book: "Book", more: "More times",
      });
    }

    const failures = [];
    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
      // Reload at the actual viewport. This exercises the same CSS media
      // query branch a visitor receives, rather than reusing the initial
      // desktop page after a resize.
      await page.reload({ waitUntil: "load", timeout: 15000 });
      await page.waitForFunction(() => window.SANA_STATIC &&
        window.SANA_STATIC["platform/listing.mjs"], null, { timeout: 15000 });
      await page.evaluate((rows) => {
        window.__FIT_ROWS_ES = rows.es;
        window.__FIT_ROWS_EN = rows.en;
      }, { es: ROWS_ES, en: ROWS_EN });
      await injectFitMounts(page, lang);
      await expect(page.locator("#fit-servicios .lst-table tbody tr").first()).toBeVisible({ timeout: 10000 });
      await expect(page.locator("#fit-servicios[data-listado]"),
        "render() must mark the injected mount as data-listado so the desktop breakout is exercised").toBeAttached();
      if (lang === "en") {
        const labels = page.locator("#fit-servicios");
        await expect(labels).toHaveText(/Book/, { useInnerText: true });
        await expect(labels).toHaveText(/More times/, { useInnerText: true });
        await expect(labels).not.toHaveText(/Sacar|Más horarios/, { useInnerText: true });
        if (width >= 1024) {
          await expect(page.locator("#fit-servicios .lst-col-proximo").first())
            .toHaveText(/next availability/i, { useInnerText: true });
        }
      }
      const probe = await probeFit(page, lang);
      await saveShot(testInfo, page, path.join(QA_DIR, `listing-fit-${lang}-${width}.png`), { fullPage: true });
      if (width === 390 || width === 1280) {
        await saveShot(testInfo, page.locator("#fit-servicios"),
          path.join(QA_DIR, `listing-fit-services-${lang}-${width}.png`));
        await saveShot(testInfo, page.locator("#fit-medicos"),
          path.join(QA_DIR, `listing-fit-doctors-${lang}-${width}.png`));
      }
      if (probe.pageHOverflow > 1) {
        failures.push(`width ${width}: page scrolls sideways by ${probe.pageHOverflow}px`);
      }
      for (const row of probe.internal) {
        failures.push(`width ${width}: #${row.mount} overflows .lst-scroll internally by ${row.over}px`);
      }
      for (const row of probe.bad) {
        failures.push(`width ${width}: .${row.cls} in #${row.mount}: ${row.what}`);
      }
      for (const avatar of probe.avatars) {
        if (avatar.display !== "inline-flex" || avatar.avatarRight > avatar.nameLeft) {
          failures.push(`width ${width}: doctor avatar is not beside its name (${JSON.stringify(avatar)})`);
        }
        if (avatar.stacked && avatar.copyBottom > avatar.actionTop + 1) {
          failures.push(`width ${width}: booking action overlaps doctor identity/specialty (${JSON.stringify(avatar)})`);
        }
      }
      if (width >= 1024) {
        // One visible chat control at a time: the nav button when the full menu
        // fits (the launcher yields to it), the floating launcher in the burger layouts.
        const launcher = page.locator("#stz-chat-launcher");
        await ((await launcher.isVisible()) ? launcher : page.locator('[data-nav-accion="abrir-chat"]:visible').first()).click();
        await page.waitForFunction(() => document.body.classList.contains("stzc-chat-open"));
        // The page narrows with a 160ms width transition; measure the
        // layout it settles on, not a frame in the middle of the slide (plus
        // two frames for the listing's resize observers to re-lay it out).
        // A transition cancelled by a newer one rejects its `finished`
        // (AbortError): wait until none is running at all.
        await page.evaluate(async () => {
          for (;;) {
            const running = document.getAnimations().filter((a) =>
              a instanceof CSSTransition && a.playState === "running");
            if (!running.length) break;
            await Promise.all(running.map((a) => a.finished.catch(() => {})));
          }
          await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
        });
        const split = await probeFit(page, lang);
        expect(split.internal, `chat-open ${lang}/${width}: internal overflow`).toEqual([]);
        expect(split.bad, `chat-open ${lang}/${width}: clipped data/actions`).toEqual([]);
        expect(split.pageHOverflow).toBe(0);
        for (const row of split.avatars) {
          expect(!row.stacked || row.copyBottom <= row.actionTop + 1,
            `chat-open ${lang}/${width}: booking overlaps specialty`).toBe(true);
        }
        await page.locator("#stz-chat-panel .stzc-close").click();
      }
    }
    expect(failures,
      "long names must fit inside the listing at every width (nothing clipped, scrolled away or truncated)").toEqual([]);
  });
}
