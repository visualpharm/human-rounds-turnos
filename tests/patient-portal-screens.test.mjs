// Patient screens on an installation whose patients come in only through the
// municipal portal (auth_mode "portal", TURNOS_PATIENT_PORTAL_LOGIN). Source
// contracts in the style of account-form-otp.test.mjs; the browser walk is
// tests/e2e/patient-portal.spec.mjs and the server rules test_patient_portal.py.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(ROOT, p), "utf8");
const form = read("static/platform/account-form.mjs");
const nav = read("static/platform/nav.mjs");
const account = read("static/platform/account.mjs");
const wizard = read("app/modules/turnos/service.mjs");
const catalog = (lang, name) => JSON.parse(read(`locales/${lang}/${name}.json`));

test("portal mode paints one portal entry instead of every form, or the remedy when refused", () => {
  assert.match(form, /state\.mode === "portal" \? portalHtml\(\)/);
  const body = /function portalHtml\(\) \{[\s\S]*?\n    \}/.exec(form);
  assert.ok(body, "portalHtml() must exist");
  // No password, sign-up, recovery or DNI-photo control inside it.
  assert.doesNotMatch(body[0], /passField|sc-swap|sc-forgot|identifierFieldHtml|scan|regHtml/);
  assert.match(body[0], /p\.loginUrl/);
  assert.match(body[0], /p\.identidad/);
  assert.match(body[0], /wa\.me/);
  assert.match(form, /cfg\.auth_mode === "portal"/);
  // The portal link is a plain link, never wired as an identity-provider button.
  assert.match(form, /querySelectorAll\("\.sc-provider\[data-provider\]"\)/);
});

test("the refusal names the installation's main center, never «tu centro» when one is set (the product owner 23.10)", () => {
  const body = /function portalHtml\(\) \{[\s\S]*?\n    \}/.exec(form)[0];
  assert.match(form, /centro: cfg\.centro_principal \|\| null/);
  // With a help line: the WhatsApp first, then the admission desk by name.
  assert.match(body, /refused_center_also[\s\S]*?centerPlaceHtml\(c, false\)/);
  // Without one: the desk by name, address and contact; the generic line only
  // when the installation has no main center.
  assert.match(body, /: c\s*\? T\("account\.form\.portal\.refused_desk_center"[\s\S]*?centerPlaceHtml\(c, true\)\s*: T\("account\.form\.portal\.refused_desk"/);
  const place = /function centerPlaceHtml\(c, withContact\) \{[\s\S]*?\n    \}/.exec(form)[0];
  assert.match(place, /'<a href="' \+ esc\(c\.url\) \+ '">' \+ esc\(c\.nombre\)/);
  assert.match(place, /esc\(c\.direccion\)/);
});

test("the booking wizard shows the server's remedy text, never a bare error code", () => {
  assert.match(wizard, /show\(res\.j\.detalle \|\| T\("service\.submit\.blocked_absences"/);
  assert.match(wizard, /show\(\(res\.j && \(res\.j\.detalle \|\| res\.j\.error\)\) \|\| T\(/);
});

test("nav keeps «Ingresar» only and drops «Cerrar sesión» on a portal installation", () => {
  assert.match(nav, /if \(state\.authMode !== "portal"\) out\.push\(\{/);
  assert.match(nav, /state\.authMode === "portal" \? "<\/div>" :/);
  assert.match(nav, /j\.auth_mode === "renaper" \|\| j\.auth_mode === "portal"/);
  // The cached mode survives a reload, so the first paint is already right.
  assert.equal((nav.match(/s\.authMode === "renaper" \|\| s\.authMode === "portal"/g) || []).length, 2);
});

test("«Ingresar» goes to the portal with this page as the way back (the product owner 26.10)", () => {
  const entry = '"/account/portal-login?next=" + encodeURIComponent(location.pathname + location.search)';
  // The portal button never links the bare portal URL: that left the product owner on the
  // portal's home page instead of back on salud.
  const body = /function portalHtml\(\) \{[\s\S]*?\n    \}/.exec(form)[0];
  assert.match(body, /href="' \+ esc\(portalEntryHref\(\)\) \+ '"/);
  assert.doesNotMatch(body, /esc\(p\.loginUrl\)/);
  assert.ok(form.includes("return " + entry + ";"), "account-form builds the entry href");
  // The nav's «Ingresar» is the same entry on a portal installation, our
  // login page elsewhere.
  assert.ok(nav.includes('state.authMode === "portal" && typeof location !== "undefined"\n        ? ' + entry),
    "nav «Ingresar» goes through the portal entry");
  assert.match(nav, /: "\/account\?mode=login" \}\);/);
});

test("/account goes to the portal by itself once, never bouncing between the sites", () => {
  assert.match(form, /if \(!state\.portal\.identidad && state\.portal\.loginUrl &&\s*window\.location\.pathname === "\/account" && portalTripAllowed\(\)\) \{\s*window\.location\.replace\(portalEntryHref\(\)\);/);
  const guard = /function portalTripAllowed\(\) \{[\s\S]*?\n  \}/.exec(form)[0];
  assert.match(guard, /Date\.now\(\) - last < 120000\) return false/);
  // Storage unavailable: stay on the page with its button.
  assert.match(guard, /catch \(e\) \{\s*return false;/);
});

test("account page has no «Cerrar sesión» for a portal session", () => {
  assert.match(account, /portalSession\(\)\?"":'<div class="rail-foot">/);
  assert.match(account, /b&&b\.acceso_portal/);
});

test("a portal session has no assistant (MCP) tab, button, panel or token request: the gateway closes /mcp", () => {
  assert.match(account, /b&&b\.mcp_cerrado/);
  assert.match(account, /!\(mcpClosed\(\) && t==="mcp"\)/);
  assert.match(account, /\(mcpClosed\(\)\?"":navBtn\("mcp"/);
  assert.match(account, /\(mcpClosed\(\)\?'':'<div class="dpanel" id="panel-mcp">/);
  assert.match(account, /if\(!mcpClosed\(\)\) renderMcp\(/);
});

test("Confirmá asks for the name when the account has none and saves it with the contact", () => {
  assert.match(wizard, /\(faltaNombre\(u\) \? nombreSection\(u\) : ""\)/);
  assert.match(wizard, /id="wz-name-' \+ key \+ '"/);
  // The name travels in the account update built by cuentaAActualizar (the
  // phone typed here stays on the turn unless «Guardar también en mi cuenta»).
  assert.match(wizard, /cuentaAActualizar\(u, nombreVal, apellidoVal/);
  assert.match(wizard, /return \{ nombre: nombre, apellido: apellido,/);
  assert.match(wizard, /if \(blur\) return Promise\.resolve\(false\);/);
  // The account switch cannot end a portal session, so it is not offered.
  assert.match(wizard, /AUTH_MODE === "portal" \? "" :/);
  assert.match(wizard, /if \(otra\) otra\.addEventListener/);
});

test("a nameless person is never drawn as a dash: «Para vos» or nothing, and the patient is the current account", () => {
  assert.doesNotMatch(wizard, /personName\(u\) \{[^}]*\|\| "—"/);
  assert.match(wizard, /function pacienteDelTurno\(\)/);
  // Both the Confirmá screen and the sent summary read the patient through it.
  assert.equal((wizard.match(/var paciente = pacienteDelTurno\(\);/g) || []).length, 2);
  assert.match(wizard, /if \(personName\(paciente\)\) rows\.push\(/);
});

test("portal copy exists in Spanish and English with matching fallbacks", () => {
  const keys = {
    "account.form": ["account.form.portal.use_portal", "account.form.portal.lede", "account.form.portal.button",
      "account.form.portal.refused_heading", "account.form.portal.refused_whatsapp", "account.form.portal.refused_desk",
      "account.form.portal.refused_desk_center", "account.form.portal.refused_center_also"],
    service: ["service.confirm.name_title", "service.confirm.first_name", "service.confirm.last_name",
      "service.confirm.name_needed", "service.patient.for_you"],
  };
  for (const [file, list] of Object.entries(keys)) {
    const es = catalog("es", file);
    const en = catalog("en", file);
    for (const key of list) {
      assert.ok(es[key] && en[key], `${key} missing`);
      // A long call wraps its fallback onto the next line.
      const src = (file === "service" ? wizard : form).replace(/,\s*\n\s*/g, ", ");
      if (key !== "account.form.portal.use_portal") {
        assert.ok(src.includes(`T("${key}", null, "${es[key]}")`), `${key} fallback must equal the Spanish catalog`);
      }
    }
  }
});
