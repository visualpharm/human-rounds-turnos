// Static contract test for the Tier 6 container artifact-integrity path.
//
// This is Tier 6 slice 9 (artifact integrity) in its BOUNDED PREPARATION form:
// the publish path is authored to produce verifiable machine-readable
// provenance + SBOM, a fail-closed vulnerability decision, an immutable digest
// record, and a digest-pinned Compose install — but NOTHING has run for real.
// No hosted Actions run, no image publication, no pull, no scan result, and no
// real digest exists (GitHub Actions billing is frozen for this account; see
// .github/workflows/release.yml's header). So this test verifies STRUCTURE,
// not execution: it reads files off disk, runs no network call, starts no
// container, and runs no scanner. The first real release must verify each
// claim by hand (docs/architecture/deploy.md).
//
// What it checks:
//   1. release.yml builds ONCE to a UNIQUE NON-RELEASE staging ref BEFORE any
//      release tag exists, with provenance + SBOM attestations ON. The build
//      pushes neither the version tag nor :latest. (staging-first policy.)
//   1b. release.yml ADMISSION-gates the release on main ancestry: a SemVer tag
//       whose commit is not reachable from main is refused before any build
//       (so packages:write is never reached for off-main work).
//   2. EVERY piece of evidence and every check binds to the captured
//      IMAGE@DIGEST — the digest record, the standalone SBOM, and the grype
//      scan all reference image@digest, never a mutable tag.
//   3. Publish ORDER is staging build → scan → promote. Scan failure creates
//      NEITHER the version tag NOR :latest (hard refusal, exit 1). Scan
//      success promotes that EXACT scanned digest to BOTH tags in one
//      `imagetools create` — no rebuild, no mutable-tag lookup. The gate's
//      exact policy is pinned: refuse on a CRITICAL-severity vuln WITH a known
//      upstream fix; below-CRITICAL and no-fix CRITICALs do not block.
//   4. Every third-party action is pinned to a 40-hex SHA (no floating tags).
//   5. The existing strict guardrails are PRESERVED: contents:read (no
//      contents:write), no id-token:write (cosign signing deliberately
//      deferred), the release-ghcr concurrency group, the SemVer if-gate, and
//      the lowercase-image transform.
//   6. docker-compose.release.yml pins the published image via a REQUIRED
//      SANA_IMAGE_DIGEST_HEX (nonempty at config time; the 64-char hex shape is
//      the documented expectation enforced at runtime pull, NOT validated by
//      Compose at config time), with fixed @sha256: prefix and repository, and
//      cancels build:.
//   7. RELEASES.md exists as a digest-record manifest with exactly one row — a
//      clearly-marked SYNTHETIC example with a placeholder digest, and an
//      honest "nothing published" status.
//   8. .env.example documents SANA_IMAGE_DIGEST_HEX + SANA_VERSION.
//
// Pure string contract — like tests/coolify-reference-contract.test.mjs. No
// YAML library is a dependency of this repo, and structure is what this gate
// is for; it catches drift (someone later re-introducing a version-first
// push, or pointing a check at a mutable tag) without over-coupling to exact
// formatting.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(ROOT, p), 'utf8');

const WORKFLOW = read('.github/workflows/release.yml');
const COMPOSE = read('docker-compose.release.yml');
const RELEASES = read('docs/policies/RELEASES.md');
const ENV = read('.env.example');

// A 40-hex-char SHA pin. Used to assert every third-party action is pinned by
// digest, not by a mutable tag — without freezing the exact SHA (a legitimate
// bump keeps the 40-hex shape; replacing the pin with @v6 would fail this).
const SHA_PIN = /@[0-9a-f]{40}\b/;

// ── 1. release.yml: build once to a unique non-release staging ref FIRST ───
test('release.yml builds once to a unique non-release staging ref, never a release tag', () => {
  assert.ok(/provenance:\s*true\b/.test(WORKFLOW),
    'provenance must be true (machine-readable SLSA provenance)');
  assert.ok(!/provenance:\s*false\b/.test(WORKFLOW),
    'provenance: false must not reappear');
  assert.ok(/^\s*sbom:\s*true\b/m.test(WORKFLOW),
    'build-push sbom: true must be present (SBOM attestation on the image)');
  // The build pushes ONLY the staging ref. The version tag and :latest are
  // promoted later from the digest; they must not appear in the build's tags.
  assert.ok(/tags:\s*\$\{\{\s*steps\.meta\.outputs\.staging\s*\}\}/.test(WORKFLOW),
    'the build step must push the staging ref, not a version/latest tag');
  assert.ok(!/\$\{\{\s*steps\.meta\.outputs\.image\s*\}\}:\$\{\{\s*steps\.meta\.outputs\.version\s*\}\}/.test(WORKFLOW),
    'the build step must NOT push the version tag (rejected version-first policy)');
  assert.ok(!/\$\{\{\s*steps\.meta\.outputs\.image\s*\}\}:latest/.test(WORKFLOW),
    'the build step must NOT push :latest');
});

test('release.yml logs the honest staging→scan→promote→record ordering', () => {
  // The echo at the end of the metadata step must say the honest ordering.
  assert.ok(/build to \$staging, scan IMAGE@DIGEST, then promote/.test(WORKFLOW),
    'metadata step must log the honest ordering: build to staging, scan IMAGE@DIGEST, then promote');
  assert.ok(!/then promote \$image@\$digest to/.test(WORKFLOW),
    'old ordering log (promote after build) must not appear');
});

test('the staging ref is unique per run and carries no release semantics', () => {
  // staging-run-<RUN_ID>-<RUN_ATTEMPT>: unique per run + retry, so concurrent
  // or re-run releases never share a staging ref, and the name says "staging".
  assert.ok(/staging-run-\$\{RUN_ID\}-\$\{RUN_ATTEMPT\}/.test(WORKFLOW),
    'staging ref must be derived from run_id + run_attempt (unique per run)');
  assert.ok(/RUN_ID:\s*\$\{\{\s*github\.run_id\s*\}\}/.test(WORKFLOW),
    'github.run_id flows into the staging ref');
  assert.ok(/RUN_ATTEMPT:\s*\$\{\{\s*github\.run_attempt\s*\}\}/.test(WORKFLOW),
    'github.run_attempt flows into the staging ref');
});

// ── 1b. release.yml: fail-closed main-ancestry admission check ──────────────
test('release.yml admits a release only if the tagged commit is an ancestor of main', () => {
  // The tag-push trigger fires for ANY v* tag regardless of which branch the
  // tagged commit lives on, so without an admission check a SemVer tag pushed
  // from an unmerged feature branch reaches packages:write and publishes. The
  // fix is fail-closed: the commit being released must be reachable from main.
  // Full-history checkout is required — the default fetch-depth:1 only fetches
  // the triggering ref and carries no main history, so ancestry is unknowable.
  assert.ok(/fetch-depth:\s*0\b/.test(WORKFLOW),
    'checkout must use fetch-depth: 0 so main-ancestry is determinable');
  // The admission step runs git merge-base --is-ancestor <sha> origin/main.
  assert.ok(/git merge-base --is-ancestor[^\n]*origin\/main/.test(WORKFLOW),
    'a step must run git merge-base --is-ancestor <sha> origin/main');
  const admitIdx = WORKFLOW.indexOf('Admit release only if the commit is an ancestor of main');
  assert.ok(admitIdx > -1, 'the admission step must be present by name');
  // It runs BEFORE any build/push (the staging build step must come after it).
  const buildIdx = WORKFLOW.indexOf('Build and push staging ref');
  assert.ok(admitIdx < buildIdx, 'the admission check must run before the build step');
  const admitSection = WORKFLOW.slice(admitIdx, admitIdx + 800);
  assert.ok(/TARGET_SHA:\s*\$\{\{\s*github\.sha\s*\}\}/.test(admitSection),
    'the check tests github.sha — the commit being released');
  assert.ok(/exit 1/.test(admitSection),
    'the check must exit non-zero when the commit is not reachable from main');
  assert.ok(/not reachable from main/.test(admitSection),
    'the refusal message must state the reason (not reachable from main)');
});

// ── 2. release.yml: every evidence + check binds to IMAGE@DIGEST ────────────
test('release.yml binds the SBOM, the scan, and promotion to the captured IMAGE@DIGEST', () => {
  // The digest the build produced is captured.
  assert.ok(/steps\.build\.outputs\.digest/.test(WORKFLOW),
    'the build digest is captured');
  // The image@digest reference form appears as a literal in the workflow
  // (image-output @ digest-output) — this is the binding every check shares.
  assert.ok(/\$\{\{\s*steps\.meta\.outputs\.image\s*\}\}@\$\{\{\s*steps\.build\.outputs\.digest\s*\}\}/.test(WORKFLOW),
    'IMAGE@DIGEST reference form must be literal in the workflow');
  // SBOM and scan both take image: <image>@<digest>, never a mutable tag.
  const sbomBlock = WORKFLOW.slice(WORKFLOW.indexOf('anchore/sbom-action'));
  const sbomImageLine = /^\s*image:.*$/m.exec(sbomBlock);
  assert.ok(sbomImageLine && /@\$\{\{\s*steps\.build\.outputs\.digest\s*\}\}/.test(sbomImageLine[0]),
    'SBOM must scan IMAGE@DIGEST, not a mutable tag');
  const scanBlock = WORKFLOW.slice(WORKFLOW.indexOf('anchore/scan-action'));
  const scanImageLine = /^\s*image:.*$/m.exec(scanBlock);
  assert.ok(scanImageLine && /@\$\{\{\s*steps\.build\.outputs\.digest\s*\}\}/.test(scanImageLine[0]),
    'scan must scan IMAGE@DIGEST, not a mutable tag');
});

test('release.yml records the immutable digest ONLY after successful promotion', () => {
  assert.ok(/GITHUB_STEP_SUMMARY/.test(WORKFLOW),
    'the digest is posted to the run summary');
  assert.ok(/release-manifest/.test(WORKFLOW),
    'a release-manifest artifact is produced');
  // Both record steps are gated on scan success, so a rejected staging
  // candidate is NOT recorded as a release.
  const recordIdx = WORKFLOW.indexOf('Record immutable digest');
  const recordSection = WORKFLOW.slice(recordIdx, recordIdx + 400);
  assert.ok(/if:\s*steps\.scan\.outcome\s*==\s*'success'/.test(recordSection),
    'the digest record must be gated on scan success');
  assert.ok(/uses:\s*actions\/upload-artifact@[0-9a-f]{40}/.test(WORKFLOW),
    'artifacts uploaded via actions/upload-artifact, SHA-pinned');
  assert.ok(/uses:\s*anchore\/sbom-action@[0-9a-f]{40}/.test(WORKFLOW),
    'standalone SBOM via anchore/sbom-action, SHA-pinned');
  assert.ok(/format:\s*spdx-json\b/.test(WORKFLOW),
    'standalone SBOM is SPDX JSON (machine-readable)');
});

test('release.yml emits a RELEASES.md-ledger-aligned record row', () => {
  // The recorded row must match RELEASES.md's real-release ledger columns
  // EXACTLY — | version tag | immutable digest reference | date | notes | — so
  // a human copies one line straight into the ledger instead of reformatting.
  const recordIdx = WORKFLOW.indexOf('Record immutable digest');
  const recordSection = WORKFLOW.slice(recordIdx, recordIdx + 1600);
  assert.ok(/Append this row to the RELEASES\.md real-release ledger/.test(recordSection),
    'summary must instruct pasting a row into the real-release ledger');
  // Paste-ready 4-column markdown table row, version column first.
  assert.ok(/ledger_row=.*\| \$VERSION \|/.test(recordSection),
    'ledger row must start with the version column (| $VERSION | ...)');
  assert.ok(/\$today/.test(recordSection),
    'ledger row must include a publication date');
  // The TSV mirrors the ledger columns: version, full @ref, date. The @ref
  // already carries the digest, so the old bare-digest column is gone.
  assert.ok(/"\$VERSION" "\$ref" "\$today"/.test(recordSection),
    'release-manifest.tsv columns are version, full $ref, date (ledger-aligned)');
  assert.ok(!/"\$DIGEST" "\$ref"/.test(recordSection),
    'old TSV shape (version/digest/ref) must be gone — the @ref carries the digest');
});

// ── 3. release.yml: ordering + fail-closed gate + exact-digest promotion ───
test('release.yml orders the publish as staging build → scan → promote → record', () => {
  // Byte-offset ordering: the staging build step, then the scan step, then the
  // promote step, then the record step. A re-ordering (e.g. recording before
  // the scan, or promoting before the scan) breaks the contract this path is
  // built around. The digest record must happen AFTER successful promotion so
  // a rejected staging candidate is not recorded as a release.
  const buildIdx = WORKFLOW.indexOf('Build and push staging ref');
  const scanIdx = WORKFLOW.indexOf('Vulnerability scan');
  const promoteIdx = WORKFLOW.indexOf('Promote scanned digest');
  const recordIdx = WORKFLOW.indexOf('Record immutable digest');
  assert.ok(buildIdx > -1 && scanIdx > -1 && promoteIdx > -1 && recordIdx > -1,
    'staging build, scan, promote, and record step markers must all be present');
  assert.ok(buildIdx < scanIdx,
    'the staging build must come before the scan');
  assert.ok(scanIdx < promoteIdx,
    'the scan must come before the digest promotion');
  assert.ok(promoteIdx < recordIdx,
    'the digest record must come after successful promotion');
});

test('release.yml has a fail-closed vulnerability gate around the release', () => {
  assert.ok(/fail-build:\s*true\b/.test(WORKFLOW),
    'scan must fail-build on a match');
  assert.ok(/severity-cutoff:\s*critical\b/.test(WORKFLOW),
    'gate threshold is CRITICAL');
  assert.ok(/only-fixed:\s*true\b/.test(WORKFLOW),
    'gate is fixed-upstream CVEs only (a fix exists to move to)');
  // The resulting policy is stated crisply: refuse on a CRITICAL-severity
  // vulnerability WITH a known upstream fix; below-CRITICAL and no-fix
  // CRITICALs do NOT block. This pins the exact policy, not just the knobs.
  assert.ok(/CRITICAL-severity vulnerability WITH a known upstream fix/.test(WORKFLOW),
    'scan policy must be stated as: CRITICAL-severity with a known upstream fix');
  assert.ok(/continue-on-error:\s*true\b/.test(WORKFLOW),
    'scan is non-fatal so the refusal step can fire and report');
  assert.ok(/if:\s*steps\.scan\.outcome\s*==\s*'failure'/.test(WORKFLOW),
    'a refusal step fires when the scan fails');
  assert.ok(/if:\s*steps\.scan\.outcome\s*==\s*'success'/.test(WORKFLOW),
    'promotion only happens when the scan succeeds');
});

test('scan failure creates neither a version tag nor :latest', () => {
  // The refusal step is a hard exit (non-zero), and the promote step is gated
  // on scan success — so on failure NO release tag is created. Only the
  // non-release staging ref exists.
  const refuseIdx = WORKFLOW.indexOf("steps.scan.outcome == 'failure'");
  const refuseExit = WORKFLOW.indexOf('exit 1', refuseIdx);
  assert.ok(refuseExit > -1 && refuseExit < refuseIdx + 400,
    'the refusal step must exit non-zero on scan failure');
});

test('scan success promotes the EXACT scanned digest to version and :latest without rebuilding', () => {
  // One imagetools create promotes the same digest to BOTH tags. The source
  // operand is $IMAGE@$DIGEST (the exact bytes that were scanned), not a
  // mutable tag like $IMAGE:$VERSION — so :version and :latest resolve to the
  // SAME digest, with no second build and no tag-to-tag copy.
  const promoteIdx = WORKFLOW.indexOf('Promote scanned digest');
  const promoteSection = WORKFLOW.slice(promoteIdx, promoteIdx + 800);
  // Extract the single-line run command
  const runLineMatch = /^\s*run:\s*.+$/m.exec(promoteSection);
  assert.ok(runLineMatch, 'Promote step must have a run command');
  const runLine = runLineMatch[0];
  assert.ok(/-t\s+"\$IMAGE:\$VERSION"/.test(runLine),
    'promote must tag the version tag');
  assert.ok(/-t\s+"\$IMAGE:latest"/.test(runLine),
    'promote must tag :latest in the SAME step');
  assert.ok(/"\$IMAGE@\$DIGEST"/.test(runLine),
    'promote source must be the exact scanned digest (IMAGE@DIGEST), not a mutable tag');
});

// ── 4. release.yml: every third-party action SHA-pinned ─────────────────────
test('every third-party action in release.yml is pinned to a 40-hex SHA', () => {
  const actionLines = [...WORKFLOW.matchAll(/^\s*uses:\s*(\S+)/gm)].map((m) => m[1]);
  assert.equal(actionLines.length, 9, `found ${actionLines.length} action uses; expected exactly 9`);
  // Normalize each pinned action ref to its identity (strip the SHA) before comparing.
  const normalizedIds = actionLines.map((line) => line.replace(SHA_PIN, '@'));
  // Exact ordered multiset: each action appears exactly as many times as it is used,
  // in workflow order. The post-promotion recording (upload release-manifest) comes
  // after all promotion-dependent steps (SBOM generation, scan, SARIF upload).
  const expectedMultiset = [
    'actions/checkout@',
    'docker/setup-qemu-action@',
    'docker/setup-buildx-action@',
    'docker/login-action@',
    'docker/build-push-action@',
    'anchore/sbom-action@',
    'anchore/scan-action@',
    'actions/upload-artifact@', // SARIF
    'actions/upload-artifact@', // release-manifest (post-promotion recording)
  ];
  const unpinned = actionLines.filter((u) => !SHA_PIN.test(u));
  assert.deepEqual(unpinned, [],
    `these action uses are not SHA-pinned (supply-chain weakening): ${unpinned.join(', ')}`);
  assert.deepEqual(normalizedIds, expectedMultiset,
    'action multiset does not match expected set; ensure each action is used exactly once except upload-artifact (twice)');
});

// ── 5. release.yml: strict guardrails preserved ─────────────────────────────
test('release.yml preserves the existing strict release guardrails', () => {
  // Minimal token scope, unchanged. The negative checks are line-anchored so
  // they match a real YAML permission entry (indented `key: value` at line
  // start), not the workflow's own prose explaining why the permission is OFF.
  assert.ok(/^\s*contents:\s*read\b/m.test(WORKFLOW), 'contents: read preserved');
  assert.ok(/^\s*packages:\s*write\b/m.test(WORKFLOW), 'packages: write preserved');
  assert.ok(!/^\s*contents:\s*write\b/m.test(WORKFLOW),
    'contents:write must NOT be granted (no auto-commit of the digest record)');
  assert.ok(!/^\s*id-token:\s*write\b/m.test(WORKFLOW),
    'id-token:write must stay absent until real cosign signing is wired');
  // One-release-at-a-time, no cancel mid-push.
  assert.ok(/group:\s*release-ghcr\b/.test(WORKFLOW), 'release-ghcr concurrency group preserved');
  // Manual dispatch from main / tag push from a v-tag only.
  assert.ok(/startsWith\(github\.ref,\s*'refs\/tags\/v'\)/.test(WORKFLOW),
    'SemVer tag-push trigger gate preserved');
  // GHCR rejects uppercase; the lowercase transform must stay.
  assert.ok(/tr '\[:upper:\]'/.test(WORKFLOW), 'lowercase-image transform preserved');
  // The whole-workflow Docker-safe SemVer regex guard.
  assert.ok(/semver_re=/.test(WORKFLOW), 'SemVer regex guard preserved');
});

test('release.yml is honest that the artifact-integrity path has never run', () => {
  assert.ok(/HAS NEVER RUN/.test(WORKFLOW),
    'the never-run warning header must stay');
  assert.ok(/EQUALLY UNPROVEN/.test(WORKFLOW),
    'the new steps must be called out as equally unproven');
});

// ── 6. docker-compose.release.yml: digest-only install with @sha256: prefix hardcoded ────────────────────
test('docker-compose.release.yml enforces digest-only consumption with fixed @sha256: prefix', () => {
  // Fail-closed: SANA_IMAGE_DIGEST_HEX is REQUIRED (the :? interpolation), so an
  // unset override refuses to start instead of silently using :latest.
  assert.ok(/\$\{SANA_IMAGE_DIGEST_HEX:\?/.test(COMPOSE),
    'SANA_IMAGE_DIGEST_HEX must be required (:? — fail-closed when unset)');
  // The image line must be: image: ghcr.io/visualpharm/sana-turnos@sha256:${SANA_IMAGE_DIGEST_HEX:?...}
  // The repository, @ separator, and sha256: PREFIX are FIXED in the file.
  // The variable provides ONLY the 64-character hex digest payload.
  const imageLineMatch = /^\s*image:\s*"ghcr\.io\/visualpharm\/sana-turnos@sha256:\$\{SANA_IMAGE_DIGEST_HEX:\?[^}]*\}"/m.exec(COMPOSE);
  assert.ok(imageLineMatch,
    'image line must be exactly: ghcr.io/visualpharm/sana-turnos@sha256:${SANA_IMAGE_DIGEST_HEX:?...} (fixed repo + @sha256: prefix, variable only in hex payload position)');
  assert.ok(/build:\s*!reset\b/.test(COMPOSE),
    'build: is reset (cancel the base file\'s build: .)');
  assert.ok(/pull_policy:/.test(COMPOSE),
    'pull_policy is explicit');
  // Compose's ONLY config-time guarantee is NONEMPTY (:?). It does NOT
  // regex-validate 64-hex at config time — that is a documented expectation
  // enforced at runtime pull. The :? message must not overclaim 64-hex
  // validation (the old "must be 64-hex digest only" wording implied a
  // config-time format check Compose does not perform).
  assert.ok(/\$\{SANA_IMAGE_DIGEST_HEX:\?[^}]*nonempty/.test(COMPOSE),
    'the :? contract must be stated as nonempty (presence), not 64-hex validation');
  assert.ok(/checks only that it is set|NONEMPTY check only|NO regex validation/.test(COMPOSE),
    'must state Compose checks only presence, not the 64-hex format');
  assert.ok(!/\$\{SANA_IMAGE_DIGEST_HEX:\?[^}]*must be 64-hex digest only/.test(COMPOSE),
    'the :? message must NOT claim "must be 64-hex digest only" — Compose does not validate the format at config time');
  assert.ok(/64-hex|64-character hex/.test(COMPOSE),
    'still documents the expected 64-hex payload shape');
  assert.ok(/no sha256: prefix/.test(COMPOSE),
    'states that variable does not include sha256: prefix');
  assert.ok(/structural hardening/.test(COMPOSE),
    'explains the structural hardening that prevents mutable tag references');
  assert.ok(/Malformed values[\s\S]*?fail later at runtime pull/.test(COMPOSE),
    'states that malformed values fail at runtime pull, not config parse');
  assert.ok(!/:v<version>/.test(COMPOSE),
    'must NOT offer a mutable tag alternative');
  assert.ok(/RELEASES\.md/.test(COMPOSE),
    'cross-references RELEASES.md for the recorded digest');
  assert.ok(/UNPROVEN/.test(COMPOSE),
    'honest that no artifact has ever been pulled through it');
  // The old variable name must NOT appear — we migrated to SANA_IMAGE_DIGEST_HEX.
  assert.ok(!/SANA_IMAGE_DIGEST\}/.test(COMPOSE),
    'old SANA_IMAGE_DIGEST variable must NOT appear (migrated to SANA_IMAGE_DIGEST_HEX)');
});

// ── 7. RELEASES.md: digest-record manifest, genuinely empty ledger ─────────
test('RELEASES.md has a genuinely empty real-release ledger', () => {
  assert.ok(RELEASES.length > 800, 'RELEASES.md is missing or stub-length');
  assert.ok(/sha256/.test(RELEASES), 'records sha256 digests');
  assert.ok(/Real-release ledger/.test(RELEASES),
    'must have a "Real-release ledger" section');
  // The ledger section ends with the empty-state note, which includes the asterisks.
  const ledgerIdx = RELEASES.indexOf('Real-release ledger');
  const ledgerEndIdx = RELEASES.indexOf('### Format illustration', ledgerIdx);
  const ledgerSection = RELEASES.slice(ledgerIdx, ledgerEndIdx > -1 ? ledgerEndIdx : ledgerIdx + 600);
  assert.ok(/\*\(Empty — no image has ever been published from this repo\. See status below\.\)\*/.test(ledgerSection),
    'ledger must state it is empty within the section, with asterisks');
  assert.ok(/SYNTHETIC FORMAT EXAMPLE/.test(RELEASES),
    'the example row is clearly marked synthetic, not a real release');
  // Placeholder all-zero digest — never a real digest presented as real.
  assert.ok(/sha256:0{64}/.test(RELEASES),
    'the example uses an obvious placeholder digest');
  assert.ok(/No image has ever been published/.test(RELEASES),
    'states plainly that nothing has been published');
  // Exactly ONE example row: count `v0.0.0-example`-shaped version cells.
  const exampleRows = RELEASES.match(/v0\.0\.0-example/g) || [];
  assert.equal(exampleRows.length, 1,
    `expected exactly one synthetic example row, found ${exampleRows.length}`);
  // The example must be in a separate "Format illustration" section, not in the ledger.
  assert.ok(/Format illustration.*NOT a real release/.test(RELEASES),
    'example must be in a "Format illustration" section, clearly marked as not a real release');
});

// ── 8. .env.example documents the release-install variables ─────────────────
test('.env.example documents SANA_IMAGE_DIGEST_HEX as hex-only and SANA_VERSION', () => {
  assert.ok(/^#\s*SANA_IMAGE_DIGEST_HEX=/m.test(ENV), 'SANA_IMAGE_DIGEST_HEX documented');
  assert.ok(/^#\s*SANA_VERSION=/m.test(ENV), 'SANA_VERSION documented');
  // Digest hex payload ONLY — no sha256: prefix in the variable.
  assert.ok(/64-character hex digest payload/.test(ENV),
    '.env.example documents the 64-character hex digest payload (no sha256: prefix)');
  assert.ok(/@sha256: prefix are[\s\S]*?FIXED/.test(ENV),
    'states that the repository and @sha256: prefix are fixed in docker-compose.release.yml');
  assert.ok(/structural[\s\S]*?hardening/.test(ENV),
    'explains the structural hardening that prevents mutable tag references');
  assert.ok(!/:v<version>/.test(ENV),
    'must NOT offer a mutable tag alternative');
  assert.ok(/SANA_VERSION is the human-readable companion/.test(ENV),
    'SANA_VERSION is informational only, not consumed by Compose');
  // The variable is the hex payload ONLY (no sha256: prefix, no @ separator).
  assert.ok(!/SANA_IMAGE_DIGEST_HEX=@/.test(ENV),
    'SANA_IMAGE_DIGEST_HEX must NOT include @ separator (that is fixed in the Compose file)');
  assert.ok(!/SANA_IMAGE_DIGEST_HEX=sha256:/.test(ENV),
    'SANA_IMAGE_DIGEST_HEX must NOT include sha256: prefix (that is fixed in the Compose file)');
  // The old variable name must NOT appear — we migrated to SANA_IMAGE_DIGEST_HEX.
  assert.ok(!/^#\s*SANA_IMAGE_DIGEST=/m.test(ENV),
    'old SANA_IMAGE_DIGEST variable must NOT appear (migrated to SANA_IMAGE_DIGEST_HEX)');
});
