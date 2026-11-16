// The humanrounds theme's half of the font-fallback checks (tests in lib/font-fallback-suite.mjs).
import { test } from "./lib/fixtures.mjs";
import { defineFontFallbackTests } from "./lib/font-fallback-suite.mjs";

// No glyph hinting: see admin-font-fallback-pinamar.spec.mjs.
test.use({
  appOptions: { dbName: "sana_turnos_test_e2e_font_fallback_humanrounds", queue: true },
  launchOptions: { args: ["--font-render-hinting=none"] },
});
test.describe.configure({ mode: "serial" });

defineFontFallbackTests("humanrounds");
