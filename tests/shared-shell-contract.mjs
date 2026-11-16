// The shared-shell source contract every direct-screen test asserts
// (2026-08-31): templates/admin/base.html owns the ONE title band (#adm-h1 —
// the row the triage bell mounts into) and the shared runtime cohort exactly
// once, and a child template declares only its title ({% block screen_title
// %}) plus its own controller(s), in file order, inside {% block scripts %}.
// The executable proof on the RENDERED response (every direct screen ×
// roles: cohort once, in order, one band, no inline toast) is
// tests/test_admin_shared_shell.py; this helper is the source-level guard
// that keeps each child honest and duplicating nothing.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

export const BASE = readFileSync(join(ROOT, "templates", "admin", "base.html"), "utf8");

// What base.html loads for EVERY authenticated direct screen, in order,
// before any child script: the i18n resolver, toast provider, and red-triage
// bell. The session boundary is canonical admin-session.mjs through the
// manifest emitted in the base head; formatting/date consumers import
// canonical ESM modules the same way.
export const SHARED_RUNTIME = ["admin-shell.js", "platform/admin-alerts.mjs"];

// The one loader of every late-loading screen (2026-11-16).
export const SCREEN_BOOT = "platform/screen-boot.mjs";

export function assertSharedShellContract(assert, template, ownCohort) {
  // base owns the single title band, and its runtime cohort follows it.
  const h1 = BASE.indexOf('<h1 id="adm-h1" class="adm-h1">{% block screen_title %}');
  assert.ok(h1 >= 0, "base.html owns the single #adm-h1 title band");
  let prev = h1;
  for (const asset of SHARED_RUNTIME) {
    const at = BASE.indexOf(`static('${asset}')`);
    assert.ok(at > prev, `base.html loads ${asset} after the band, in cohort order`);
    assert.equal(BASE.indexOf(`static('${asset}')`, at + 1), -1, `${asset} exactly once in base.html`);
    prev = at;
  }
  assert.ok(BASE.indexOf("{% block scripts %}", prev) >= 0,
    "the shared runtime loads before the child's scripts block");
  assert.match(BASE, /static_manifest\(\)/, "base publishes canonical ESM URLs");
  assert.doesNotMatch(BASE, /static\('format\.js'\)/, "the retired formatter script is absent");

  // The child: a title declaration, no band of its own, no shared asset,
  // and exactly its own controller cohort in file order.
  assert.match(template, /\{% block screen_title %\}/, "the child fills the title block, not its own band");
  const bare = template.replace(/\{#[\s\S]*?#\}/g, "");
  assert.doesNotMatch(bare, /<h1\b/, "the single h1 is base.html's");
  assert.doesNotMatch(bare, /adm-topbar|adm-h1row/, "the title band markup is base.html's");
  for (const asset of SHARED_RUNTIME) {
    assert.equal(bare.includes(`static('${asset}')`), false,
      `${asset} is base.html's, not the child's`);
  }
  const blockAt = bare.indexOf("{% block scripts %}");
  assert.ok(blockAt >= 0, "the child has a scripts block");
  const all = [...bare.slice(blockAt).matchAll(/static\('([^']+)'\)/g)].map((m) => m[1]);
  // The shared screen loader is the child's boot, not part of its controller
  // cohort: imported at most once, and then the screen boots through it.
  const boots = all.filter((asset) => asset === SCREEN_BOOT).length;
  assert.ok(boots <= 1, `${SCREEN_BOOT} at most once in the child`);
  if (boots) assert.match(bare, /bootScreen\(\{/, "the child boots through the shared loader");
  const found = all.filter((asset) => asset !== SCREEN_BOOT);
  assert.deepEqual(found, ownCohort,
    "the child's scripts block carries exactly its own cohort, in order");
}
