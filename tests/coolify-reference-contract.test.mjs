// Static contract test for docs/deploy/coolify-reference.md.
//
// That doc is Tier 6 slice 11 — the PUBLIC, generic Coolify walkthrough. Its
// own acceptance gate (docs/roadmap/tier6-delivery-design.md, slice 11) is:
// "a fresh read-through by someone who has never touched Pinamar's real
// Coolify app can follow it start to finish with no private-repo
// cross-reference required." This file encodes the parts of that gate a static
// check can enforce, so the doc can't silently drift into prescribing a
// variable that doesn't exist, leaning on a private ops repo, or dropping one
// of the real contract anchors a reader needs.
//
// What it checks:
//   1. The doc exists and is non-trivial (the gate can't pass vacuously).
//   2. It names the real, repo-grounded anchors a reader follows start-to-finish
//      (Dockerfile, /app/data, /setup, /health, /ready, smoke_prod.py, port
//      7700, DATABASE_URL, and the sibling install/upgrade docs).
//   3. It stays generic: no Pinamar instance-specific values and no required
//      cross-reference to the private ops repo or the public reference URL.
//   4. Every NAME=value assignment the doc writes into a fenced block is a
//      REAL variable — declared in .env.example, or a runtime knob attested by
//      the source file that reads it (see RUNTIME_SOURCES), or DATABASE_URL.
//      The doc must never tell an operator to set a variable that does nothing.
//   5. Every runtime knob the doc leans on is confirmed against the named
//      source file that reads it — not against this test's own prose. Each
//      entry in RUNTIME_SOURCES is read from disk and the knob name asserted
//      present, so the allowlist cannot drift into naming a knob the code no
//      longer reads.
//
// It is a static string contract — reads files, no network, no server boot.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DOC = readFileSync(join(ROOT, 'docs/deploy/coolify-reference.md'), 'utf8');
const ENV_EXAMPLE = readFileSync(join(ROOT, '.env.example'), 'utf8');

// Every documented variable name in .env.example: lines like `# PORT=7700` or
// `# TURNOS_PUCO_GWT_PERM_OSDE=`. This is the public, canonical name list.
const ENV_EXAMPLE_VARS = new Set(
  [...ENV_EXAMPLE.matchAll(/^#\s*([A-Z][A-Z0-9_]+)\s*(?:=|\[)/gm)]
    .map((m) => m[1])
);

// Runtime knobs the doc leans on that are NOT yet listed in .env.example
// (which this tier is frozen from editing). The value is the source file that
// reads the knob — the test opens each one and asserts the knob name is
// literally present, so the list is grounded in code, not in this comment.
// If a knob later lands in .env.example, leave it here too: the union only
// ever relaxes the check, never tightens it.
// The process keys are declared (and read) in core/settings.py since 2026-11-11.
const RUNTIME_SOURCES = {
  TURNOS_ASGI: 'app/core/settings.py',
  TURNOS_QUEUE_WORKER: 'app/core/settings.py',
  TURNOS_WORKER_HEALTH_PORT: 'app/core/settings.py',
  TURNOS_OUTBOUND_KILL: 'app/core/settings.py',
  TURNOS_TIMEZONE: 'app/core/settings.py',
};

// DATABASE_URL is the one required boot variable and is deliberately never
// written into .env.example (it is credential-bearing). PORT/HOST are baked
// into the Dockerfile and also appear commented in .env.example, so they are
// captured by ENV_EXAMPLE_VARS; DATABASE_URL is asserted here explicitly.
const REAL_VARS = new Set([
  ...ENV_EXAMPLE_VARS,
  ...Object.keys(RUNTIME_SOURCES),
  'DATABASE_URL',
]);

// A backtick token "looks like an env var we'd set" if it starts with one of
// the project's env prefixes, or is one of the bare names. Tokens like
// `Dockerfile`, `EXPOSE`, `CMD`, `HEAD`, `SIGTERM` match the uppercase shape
// but no prefix, so they are never checked — the prefix filter scopes the
// check to actual configuration names.
const ENV_PREFIXES = [
  'TURNOS_', 'OPENROUTER_', 'MAILGUN_', 'MAIL_', 'OTP_', 'META_', 'HSI_',
  'SITE_ID_', 'LANG', 'PACK_CHAIN', 'OWNER_', 'WEBTURNO_', 'SACOTURNO_',
  'GOOGLE_PLACES_API_KEY',
];
const BARE_KNOWN = new Set(['DATABASE_URL', 'PORT', 'HOST']);

function looksLikeEnvVar(token) {
  if (BARE_KNOWN.has(token)) return true;
  return ENV_PREFIXES.some((p) => token.startsWith(p));
}

// Is `token` (already stripped of a trailing `*`) a real variable, or a real
// family prefix (e.g. the doc's `TURNOS_B2_*` shorthand for the TURNOS_B2_
// cluster)? A family mention is acceptable iff at least one REAL_VAR starts
// with `<token>` (token already ends in `_` for the glob form).
function isRealEnvVar(token) {
  if (REAL_VARS.has(token)) return true;
  if (token.endsWith('_')) return [...REAL_VARS].some((v) => v.startsWith(token));
  return false;
}

// The fenced code blocks in the doc (content between ``` fences), in order.
function fencedBlocks(md) {
  // Splitting on fence lines (``` optionally followed by a language tag)
  // yields alternating text/code segments; odd indices are the code.
  const segs = md.split(/^```[^\n]*$/m);
  const blocks = [];
  for (let i = 1; i < segs.length; i += 2) blocks.push(segs[i]);
  return blocks;
}

// Every NAME=value assignment the doc writes into a fenced block — the
// variables it explicitly tells an operator to SET. Only uncommented
// assignments count (`NAME=...` at line start); `# NAME=...` is documentation,
// not a prescription.
const DOC_FENCED_ASSIGNS = fencedBlocks(DOC).flatMap((block) =>
  [...block.matchAll(/^\s*([A-Z][A-Z0-9_]+)=/gm)].map((m) => m[1])
);

// Every env-var-shaped backtick token in the doc, with a trailing `*` (the
// doc's "family" shorthand) stripped.
const DOC_ENV_TOKENS = [...DOC.matchAll(/`([A-Z][A-Z0-9_]{2,}\*?)`/g)]
  .map((m) => m[1].replace(/\*$/, ''))
  .filter(looksLikeEnvVar);

test('the coolify reference doc exists and is non-trivial', () => {
  assert.ok(DOC.length > 2000, 'docs/deploy/coolify-reference.md is missing or stub-length');
});

test('operator instructions retain the required native topology and durable private packs', () => {
  assert.ok(fencedBlocks(DOC).some(block => /^TURNOS_ASGI=1$/m.test(block)),
    'minimal operator environment must retain the required native topology');
  assert.doesNotMatch(DOC, /leave `TURNOS_ASGI` unset|call `server\.main\(\)`|no Helm\s+chart/);
  for (const variable of ['TURNOS_PRIVATE_PACKS_DIR', 'TURNOS_REQUIRED_PACKS', 'PACK_CHAIN']) {
    assert.ok(DOC.includes('`' + variable + '`'), 'missing durable-pack contract: ' + variable);
  }
});

test('the linked deployment contract does not prescribe the retired server fallback', () => {
  const contract = readFileSync(join(ROOT, 'docs/architecture/deploy.md'), 'utf8');
  assert.doesNotMatch(contract, /Unsetting it remains the same-revision rollback|calls `server\.main\(\)`|Both work in EITHER\s+serving mode|before either server accepts traffic/);
  assert.match(contract, /`TURNOS_ASGI=1` is the only supported serving mode/);
  assert.match(contract, /Clearing or changing this flag causes a boot error/);
  assert.match(contract, /previously verified image/);
  assert.match(contract, /`app\/routers\/health\.py`/);
});

test('the doc names the repo-grounded anchors a reader follows', () => {
  // A reader who has never seen this repo still needs these contract anchors
  // to follow the recipe. If one disappears, the walkthrough has a hole.
  const anchors = [
    'Dockerfile', '/app/data', '/setup', '/health', '/ready',
    'scripts/smoke_prod.py', '7700', 'DATABASE_URL',
    'INSTALL.md', 'UPGRADING.md', 'deploy.md',
  ];
  for (const a of anchors) {
    assert.ok(DOC.includes(a), `doc is missing anchor "${a}"`);
  }
});

test('the doc is generic — no Pinamar instance specifics or private-repo dependency', () => {
  // Slice-11 gate: followable with no private-repo cross-reference. These
  // tokens are either private (ops repo name, real host IP, prototype basic
  // auth) or instance-specific (the public reference URL, which a generic
  // walkthrough must not lean on). Placeholder domains are used instead.
  const forbidden = [
    'humanrounds-pinamar', // private ops repo
    'pinamar.humanrounds.org', // public reference URL — keep the doc placeholder-only
  ];
  for (const token of forbidden) {
    assert.ok(!DOC.includes(token),
      `doc must stay generic; found instance-specific/private token "${token}"`);
  }
  // No real host address: an IPv4 in the doc is a documentation (RFC 5737),
  // private or loopback address, never a server's.
  const placeholderNet = /^(192\.0\.2|198\.51\.100|203\.0\.113)\.|^(10|127)\.|^192\.168\.|^0\.0\.0\.0$/;
  for (const m of DOC.matchAll(/(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])/g)) {
    assert.match(m[0], placeholderNet, `doc names a real host address (${m.index})`);
  }
  assert.ok(DOC.includes('your-domain.example'),
    'doc should use placeholder domains (your-domain.example)');
  // No real basic-auth pair: every credential handed to smoke_prod.py is the
  // placeholder, and no URL carries inline user:pass@ credentials.
  for (const m of DOC.matchAll(/smoke_prod\.py\s+\S+\s+(\S+:\S+)/g)) {
    assert.equal(m[1], 'YOUR_BASIC_USER:YOUR_BASIC_PASS',
      `doc passes a non-placeholder basic-auth credential: "${m[1]}"`);
  }
  assert.ok(!/https?:\/\/[^\s\/@]+:[^\s\/@]+@/.test(DOC),
    'doc must not embed user:pass@ credentials in a URL');
});

test('every runtime knob the doc leans on is attested by its named source file', () => {
  // Grounding the allowlist in code, not in this test's own prose: open each
  // source file and assert the knob name is literally read there. If a knob
  // is renamed or removed in code, this fails and the doc/allowlist must be
  // updated to match — the allowlist cannot drift into naming a dead knob.
  for (const [knob, src] of Object.entries(RUNTIME_SOURCES)) {
    const text = readFileSync(join(ROOT, src), 'utf8');
    assert.ok(text.includes(knob),
      `runtime knob ${knob} is not read in its attested source ${src} ` +
      `— the allowlist drifted from code`);
  }
});

test('every fenced NAME=value assignment the doc prescribes is a real variable', () => {
  // The worst doc bug here is telling an operator to set a variable that does
  // nothing (a typo, or a held-Tier-6 knob that isn't in code yet). Every
  // NAME=value the doc writes into a fenced block must resolve to a real name.
  assert.ok(DOC_FENCED_ASSIGNS.length >= 4,
    `fenced-assignment discovery matched too few (${DOC_FENCED_ASSIGNS.length}); the gate is off`);
  const unknown = [...new Set(DOC_FENCED_ASSIGNS)].filter((t) => !REAL_VARS.has(t));
  assert.deepEqual(unknown, [],
    `doc prescribes env vars in fenced blocks that are not real ` +
    `(not in .env.example, the runtime allowlist, or DATABASE_URL): ${unknown.join(', ')}`);
});

test('every env-var-shaped backtick name the doc mentions is a real variable', () => {
  // Belt-and-suspenders: prose backtick mentions (`OPENROUTER_API_KEY`,
  // `TURNOS_B2_*`, …) are also checked, so a typo'd var name in a sentence is
  // caught even when it is not a fenced prescription.
  assert.ok(DOC_ENV_TOKENS.length >= 6,
    `env-token discovery matched nothing (${DOC_ENV_TOKENS.length}); the gate is off`);
  const unknown = DOC_ENV_TOKENS.filter((t) => !isRealEnvVar(t));
  assert.deepEqual(unknown, [],
    `doc names env vars that are not real (not in .env.example, the runtime ` +
    `allowlist, or a real family prefix): ${[...new Set(unknown)].join(', ')}`);
});
