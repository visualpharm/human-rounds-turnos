// The pinamar theme's half of the font-fallback checks (tests in lib/font-fallback-suite.mjs).
import { test } from "./lib/fixtures.mjs";
import { defineFontFallbackTests } from "./lib/font-fallback-suite.mjs";

// Linux Chromium hints glyph advances to whole pixels, so a fractional size-adjust barely
// moves them and the same text measures up to 4.5% wider in the web font than in its
// fallback (2864 against 2735 px); macOS and Windows lay out with fractional advances.
// This spec measures the faces' metrics, so it runs without hinting on every runner
// (headless Chromium; a headed Linux run ignores the flag).
test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_font_fallback_pinamar", queue: true },
  launchOptions: { args: ["--font-render-hinting=none"] },
});
test.describe.configure({ mode: "serial" });

defineFontFallbackTests("pinamar");
