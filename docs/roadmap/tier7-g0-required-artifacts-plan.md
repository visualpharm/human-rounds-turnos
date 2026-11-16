# Tier 7 G0 — Required-Artifacts Gate Hardening (existence-only → structural)

**Status: IMPLEMENTED AND VERIFIED in the 2026-08-19 integration candidate. The
body below is the preserved historical execution packet prepared against HEAD
232e33b4; its pre-execution status, ancestry snapshot, and line-number citations are
historical rather than current instructions. The resulting structural gate is now
implemented in `scripts/public_cut_preflight.py`, policy, tests, and publication
documentation, with every required artifact covered.**

**Provenance:** prepared 2026-08-18 by a GLM (z.ai, glm-5.3) background session via
Claude Code, from detached HEAD **232e33b4** ("Retire server.py's dead private-directory
dispatch surface") in the isolated worktree `turnos-glm-tier7-g0-prep`, and corrected
the same day against the independent review. This document is
the only file that worktree writes. Execution happens LATER, on a reconciled integration
head (see [Held-origin dependencies](#held-origin-dependencies-and-reconciliation)).

---

## 1. Problem — the existence-only placeholder gap

The approved Tier 7 publication packet (§2) requires 24 public artifacts. Today the
preflight verifies each one only as **exists + regular file + not a symlink + ≥ min_bytes**,
and the policy sets `min_bytes: 1` for every entry
(`scripts/public_cut_policy.json:6-31`, policy `2.1.0-tier7`). Consequences:

- **`docs/roadmap/tier7-readiness.md:74-76,96,100` demands "real, reviewed, approved
  institutional documents" and says "DO NOT commit placeholders" — but nothing in the
  gate DETECTS a placeholder.** A 26-byte file — `# DATA_MAP.md placeholder` plus its
  trailing newline, the exact shape the fixtures seed (next bullet) — passes
  phase 4 today.
- The proof is in the packet's own tests: the clean-fixture builder seeds every
  artifact as literally `f"# {art} placeholder\n"`
  (`test/test_public_cut_preflight.py:75-77`). The test suite is green against
  placeholder content — that is the gap, demonstrated by the artifact we trust most.
- The checker's reporting is likewise existence-shaped: below-floor files are
  reported with the detail string `"empty"`
  (`scripts/public_cut_preflight.py:440-443`) even when they contain placeholder bytes.

**Why this blocks DATA_MAP and the other publication documents (the "before … can
proceed" ordering):** authoring the 9 missing institutional documents against a gate
that cannot reject a placeholder means the gate provides zero protection for the
authoring stage — the tier7-readiness.md warning would rest on reviewer vigilance
alone. G0 closes the gate first, so every document authored afterwards (G1, separate
plan) lands against a checker that structurally rejects stubs, skeleton sections,
and untraceable approval claims.

**Baseline at HEAD (reproduced 2026-08-18, read-only, offline):** running
`python3 scripts/public_cut_preflight.py .` on the private tree produces exactly one
finding — rule `forbidden_path` / `private-town-pack` on the path
`packs/ar-b-pinamar` — and nothing else, because forbidden paths fail first and every
other phase is skipped (`scripts/public_cut_preflight.py:957-966`). No ellipsis is
allowed in the baseline record: the FIRST execution-phase action (§7, slice A, step
0) captures that finding line verbatim into the suite as the frozen expected bytes;
every later A9 comparison is byte-equality against those captured bytes. This
behavior is correct and MUST be byte-stable after G0 (§8, A9).

**Current artifact inventory at HEAD** (required by policy §6-31; sizes re-verified
2026-08-18 at 232e33b4 and valid ONLY there — re-measured at the re-freeze head):

| State | Artifacts |
|---|---|
| Present, real (15) | `README.md` 13.6K · `LICENSE` 34.5K · `CONTRIBUTING.md` 8.4K · `SECURITY.md` 2.7K · `CHANGELOG.md` 3.7K · `docs/INSTALL.md` 5.1K · `docs/UPGRADING.md` 10.6K · `.github/workflows/release.yml` · `Dockerfile` · `docker-compose.yml` · `docker-compose.release.yml` · the 4 preflight packet files themselves |
| **Missing (9)** | `CODE_OF_CONDUCT.md` · `GOVERNANCE.md` · `MAINTAINERS.md` · `SUPPORT.md` · `DPA.md` · `DATA_MAP.md` · `CLINICAL_SAFETY.md` · `INCIDENT_RESPONSE.md` · `HIGH_AVAILABILITY.md` |

G0 does **not** author the 9 missing documents (tier7-readiness.md:70 forbids
placeholder creation, and G1 authoring is a separate reviewed stage). G0 makes the
gate able to tell the difference.

## 2. Reconciled inputs — the approved Tier 7 publication packet

All six packet files at HEAD are byte-identical to the original approval branch
`codex/tier7-public-cut-preflight` (6457f7a6; verified 2026-08-18 at 232e33b4 by an
empty `git diff` over the six paths AND by `git merge-base --is-ancestor 6457f7a6 HEAD`
exiting 1 — the branch is NOT an ancestor of HEAD; the packet landed via 5f53538a then
the 54a1f04d bypass fix, on the integration line). Both checks are re-run by the
coordinator at the re-freeze head before any execution:

1. `scripts/public_cut_preflight.py` — the checker (6 phases, fail-closed).
2. `scripts/public_cut_policy.json` — `2.1.0-tier7`, data-driven rules.
3. `test/test_public_cut_preflight.py` — the proof suite.
4. `docs/publication/preflight.md` — operator contract (phase 4 = "present, non-empty",
   line 65-67; pass criterion 2, line 16).
5. `docs/roadmap/tier7-readiness.md` — readiness roadmap ("Public Artifact Completion —
   NOT STARTED", lines 72-76).
6. `docs/solutions/security/fail-closed-public-cut-preflight.md` — the fail-closed
   design lesson.

G0 amends items 1-5 in a way that **preserves every property these documents promise**
(name-first rejection, value-free reporting, symlink refusal, fail-first ordering,
external evidence informational-only) and then updates them to describe the stronger
contract. The lesson doc (6) gains one line, not a rewrite.

Constraints inherited from the packet that bound the design:

- **Data-driven:** "the script is generic; every repo-specific rule lives here"
  (policy `_comment`). The per-artifact contract lives in the policy JSON, not in
  Python.
- **Deterministic and value-free:** findings never contain document content, matched
  values, or credential material — only rule IDs, section names, line numbers.
- **External evidence stays external:** institutional approval, GitHub setup, and
  credential provisioning are `passed=None` informational
  (`scripts/public_cut_preflight.py:945-953`) and are verified outside the tool.
  G0 does not move them into locally-verifiable territory — a local script cannot
  prove a human approved a document, and pretending otherwise would make the gate
  LESS truthful.

## 3. Truthfulness spine — what the gate can and cannot claim

The strengthened gate raises the bar from "any non-empty file" to "structurally a real
document with traceable approval FORM". It cannot and does not verify approval
SUBSTANCE. Three precedents anchor this honesty:

1. **Tier 6's `cloud_shared` rename** (`docs/roadmap/tier6-delivery-design.md:408-417`):
   the inference profile was renamed because "nobody has signed or attached an
   OpenRouter/model-provider DPA to this project" — the project already refuses to
   claim paperwork it does not hold. **Therefore `DPA.md`'s contract REQUIRES a
   "Current posture" section that states no DPA is on file** (standard provider ToS,
   `cloud_shared`) and reserves DPA-qualified language for a cited document/date. A
   DPA.md that asserts a signed DPA must FAIL review even if it passes every
   structural rule.
2. **Placeholder ban with detection:** tier7-readiness.md:100 bans placeholders; G0
   gives the ban teeth (§5) while acknowledging byte-floors are stub-catchers, not
   substance-proofs.
3. **External evidence remains `passed=None`** so a green preflight never implies
   institutional approval.

## 4. Scope and bounds

**In scope (G0, all offline, all in-tree):**

1. Policy schema extension → `2.2.0-tier7`: per-artifact structural contract (§5.1).
2. Checker extension: `check_required_artifacts` verifies structure, fail-closed (§5.2).
3. Strict policy validation: unknown keys are `PolicyError`, not silently ignored (§5.3).
4. Test suite: replace placeholder seeding with a policy-derived real-shape builder;
   RED/GREEN proofs for every new rejection class; ordering proofs (§6).
5. Documentation sync: operator contract phase 4, tier7-readiness "Remaining Work",
   solutions lesson addendum (§7, slice C).

**Out of scope (explicit non-goals):**

- Authoring any of the 9 missing documents — that is G1, separately planned and
  reviewed, and happens AFTER this gate lands.
- Any change to phases 1/3/5/6 (forbidden paths, ambient env, secret markers, git
  history) or to their ordering and short-circuit semantics.
- Any LLM/heuristic content judgment — deterministic string/structure checks only.
- Any local verification of institutional approval, GitHub settings, or credentials.
- Any git mutation or publication by the executor: no fetch, no worktree creation,
  no commit, no push, no ref deletion, no release action — all assigned to the later
  Codex/Terra integration coordinator (§7); held refs are preserved untouched (§9).
- Editing the existing `docs/UPGRADING.md`, which today references the local
  control-plane clone path. That reference is real and is reconciled truthfully as a
  named PRE-PUBLIC-CUT CLEANUP owned by the coordinator (removed or sanitized before
  any public cut); G0 only calibrates UPGRADING.md's floor, never edits its text,
  and no new public document repeats that path (§5.1, §5.4).
- Touching the private tree's contents, databases, network, deploy, branches, remotes.
- Renaming/moving any required artifact path (the 24 paths are frozen; only their
  rules strengthen).

## 5. Design

### 5.1 Policy schema v2.2.0-tier7 (draft — settles at review)

Each `required_public_artifacts` entry gains optional fields; `min_bytes` stays but
stops being the only tooth:

```json
{
  "path": "DATA_MAP.md",
  "min_bytes": 2048,
  "required_sections": ["Data categories", "Storage", "Processors and flows",
                        "Retention", "Jurisdictions"],
  "placeholder_markers": ["TODO", "TBD", "FIXME", "XXX", "PLACEHOLDER",
                          "Lorem ipsum", "<fill", "FILL IN"],
  "approval_record": {"required": true, "record_prefix": "approval:"}
}
```

Field semantics (all enforced by the checker; all validated at policy load):

- **`min_bytes`** — existing meaning; floors are calibrated per artifact (§5.4). Honest
  role: catches stubs, proves nothing about substance.
- **`required_sections`** — Markdown H2 headings (`^## …`) that must each appear
  exactly (case-sensitive, trailing-`#`-trimmed) in the file. These headings are BOTH
  the gate rule AND the G1 authoring outline — one contract, two uses.
- **`placeholder_markers`** — case-insensitive, WORD-BOUNDED matches (the marker must
  stand alone as a word, never inside legitimate prose) whose presence fails the
  artifact. The list is PER-ARTIFACT: it intentionally includes the historical test
  seeding shape (`# <path> placeholder`) via the `PLACEHOLDER` marker, plus stub
  VALUES on the approval-record line — `record: pending`, `record: <fill`,
  `record: TBD`-shaped entries. The record-line rejection is NARROWED to stub
  values: a marker must never match the record FIELD name, the `approval:` prefix,
  or a VALID `record: approval:<id>` line — the valid form is exempt from the
  marker scan by construction (its value lives in the closed id alphabet, which
  spells no marker word), so the gate rejects `record: pending` while accepting
  `record: approval:0f1e2d3c4b5a6978` in the same document; T11 pins the
  valid/stub/boundary behavior. Any marker that collides with legitimate prose in
  a real covered artifact (e.g. `SECURITY.md`) is dropped from THAT artifact's
  list, never whitelisted as text. T10 calibration is the anti-false-positive
  gate: every existing covered artifact must PASS, so a marker firing on real
  prose fails the policy, not the document. Report: marker + line number only.
- **`approval_record`** — for institutional documents only (§5.4 table). The file must
  carry an H2 section `## Approval record` containing at least:
  - one `record: approval:<id>` line where the id is STRICTLY `hr-` + exactly 16
    lowercase hex characters (`^hr-[0-9a-f]{16}$`) — a coordinator-issued 64-bit
    opaque token. The alphabet is CLOSED: no `.`, `/`, `:`, `@`, `_`, spaces, or
    uppercase exist in it, so an id CANNOT encode a URL, a filesystem path, a
    repository name, or a host (hex digits plus one fixed hyphen cannot spell any
    of them) and no private control-plane location can ever appear inside a public
    artifact, and
  - one `date: YYYY-MM-DD` line — a strict ISO 8601 calendar date that is
    SEMANTICALLY valid: it must parse as a real calendar date (month 01-12, day valid
    for the month and year, leap years honored). Shape alone is insufficient —
    `2026-13-99`, `2026-02-30`, and `13/99/2026` must ALL fail.
  The gate verifies FORM (dated, opaque evidence pointer). Resolving the opaque id to
  the underlying institutional record happens in the private control plane, outside
  the public tree and outside this tool; the private repository is a FORBIDDEN path
  in any public tree and is never fetched or read. A fabricated pointer remains
  possible; §3 states the residual risk and the external review that owns it.

Entries may omit any field; omitted means "not required for this artifact" (e.g.,
`LICENSE` has no sections — plain text, not Markdown).

### 5.2 Checker extension — `check_required_artifacts` (fail-closed throughout)

Existing mechanics stay: open once with `O_NOFOLLOW`, `fstat` the same descriptor,
reject non-regular (`scripts/public_cut_preflight.py:430-456`). Added, on the same
open descriptor:

1. **Size floor** (existing check, unchanged code path) but the finding detail
   distinguishes `empty` (0 bytes) from `below floor (<N bytes required)` — fixes the
   misreporting noted in §1.
2. **Read up to `max_scan_bytes + 1` bytes.** Read error → FAIL `unreadable`.
   More than `max_scan_bytes` → FAIL `oversized` (never silently skip structure checks
   on a big file; no artifact legitimately exceeds 1 MiB).
3. **Strict UTF-8 decode.** `UnicodeDecodeError` → FAIL `not valid UTF-8` (a governance
   document that cannot decode is not a document; also keeps findings value-free — the
   error is reported as a fact, no bytes echoed).
4. **Section presence.** Parse only lines matching `^##\s+(.*?)\s*#*\s*$`; each
   `required_sections` entry must equal one captured heading. Missing ones are ALL
   reported in one finding as section NAMES (names are policy data, not file content —
   value-free holds).
5. **Placeholder markers.** Case-insensitive scan of the decoded text; first hit per
   marker class reported as marker + line number. Never the surrounding text. A
   line matching the valid approval-record form of item 6 (`record: approval:<id>`
   with a well-formed id) is EXEMPT from the marker scan; stub values on the same
   field (`record: pending`, `record: <fill`) still fail — the field's valid form
   and its stub forms are never confused (T11).
6. **Approval record form** where `approval_record.required` — per §5.1 rules: the
   record id must match `^hr-[0-9a-f]{16}$` exactly, and the date must pass SEMANTIC
   validation — the checker parses the `date:` value as a real calendar date
   (calendar-accurate month/day/leap-year), never merely regex-matching the shape.
   Malformed ids — wrong prefix, wrong length, uppercase or non-hex characters, or
   anything URL/path/repo/host-shaped — and malformed dates — wrong shape
   (`13/99/2026`) or right shape but not a real date (`2026-13-99`, `2026-02-30`) —
   FAIL naming the malformed FIELD, never the offending value.

Ordering guarantee (unchanged): if ANY forbidden path exists, phase 4 never runs
(`run()` at `scripts/public_cut_preflight.py:957-966`) — so a private tree with
forbidden packs never has its documents opened at all. G0 adds zero new reads before
that gate.

### 5.3 Strict policy validation

`_validate_policy` (`scripts/public_cut_preflight.py:135-184`) additionally:

- Rejects unknown keys in artifact entries, `approval_record`, and top-level policy —
  a misspelled `required_sectons` must be exit-2 `PolicyError`, never a silently
  weakened gate.
- Type-checks every new field (`min_bytes` int ≥ 0 — boolean or string fails;
  `required_sections` list of non-empty strings without `\n`; `placeholder_markers`
  non-empty strings; `approval_record` object with boolean `required`) and pins
  `record_prefix` to the EXACT literal `approval:` — any other value, including
  near-misses (`approval-`, `approvals/`, `record-`) and anything path/URL-shaped,
  is exit-2 `PolicyError` — plus the id pattern `^hr-[0-9a-f]{16}$` pinned as a
  policy-level constant, so the policy can neither reintroduce a private
  control-plane path, alter the record prefix, nor loosen the id alphabet in any
  public artifact.
- Bumps and checks `policy_version` prefix (`2.2.0-tier7`); a missing/unknown version
  prefix fails closed at load.

### 5.4 Per-artifact contract (the table G1 authors against)

Institutional set — sections are the authoring outline; all carry approval records.
The `min_bytes` values below are TEMPORARY G0 POLICY FLOORS: slice B writes them
into the policy as real, enforced floor values the moment G0 lands — the gate has
teeth on day one, not only after G1 — and each one is then RECOMPUTED when that
document's final reviewed content settles (G1 authoring plus independent review),
inside the same reviewed change that lands the final document. Calibration follows
content; enforcement starts now:

| Artifact | `min_bytes` | `required_sections` (H2) | Approval record | Grounded in (G1 sources, in-tree) |
|---|---|---|---|---|
| `GOVERNANCE.md` | 1024 | Decision making · Roles · Approvals and review · Changes to this document | yes | tier7-readiness.md "Institutional Readiness"; institutional approval (opaque id only, §5.1) |
| `MAINTAINERS.md` | 512 | Current maintainers · Reachability · Scope · Emeritus | yes | governance approval evidence names them |
| `SUPPORT.md` | 512 | Support channels · Response expectations · LTS · Commercial support | yes | tier7-readiness.md; Tier 6 delivery docs |
| `DPA.md` | 1536 | Current posture · Data processors inventory · Sub-processing · Changes | yes | **tier6-delivery-design.md:408-423 (no DPA on file — the posture section must SAY so)**; legal.md |
| `DATA_MAP.md` | 2048 | Data categories · Storage · Processors and flows · Retention · Jurisdictions | yes | legal.md + pii.py (scrub/restore + referral-image exception), hsi.md, notificaciones-y-lista-espera.md (Mailgun), resumen-paciente.md + laboratorio-paciente.md (AI readings), chat-y-whatsapp.md (Meta/WAHA), deploy.md (no CDN), data-packs.md (private packs excluded) |
| `CLINICAL_SAFETY.md` | 1536 | Safety ownership · Clinical safety case · In-product mitigations · Limitations | yes | entrevista-previa.md (red-flag floor), ia-y-ordenes.md (AI is ONE reading), resumen/laboratorio docs |
| `INCIDENT_RESPONSE.md` | 1024 | Severity levels · Reporting · Response runbook · Post-incident review | yes | deploy.md; fail-closed lesson; backup/restore procedure (ops-internal, cited by opaque id only) |
| `HIGH_AVAILABILITY.md` | 1024 | Availability model · RTO and RPO · Backups · Degradation contracts | yes | deploy.md (single node, no CDN — stated honestly), backup/restore procedure (ops-internal, cited by opaque id only), "Nothing breaks because of email or HSI" invariant |
| `CODE_OF_CONDUCT.md` | 1024 | Our standard · Enforcement · Contact | yes | standard Contributor Covenant shape |

Existing 15 — floors calibrated to reality, sections only where they are a real
must-have, approval records NOT required (they are engineering artifacts, not
institutional approvals):

| Artifact | Floor rule | Sections |
|---|---|---|
| `README.md`, `docs/INSTALL.md`, `docs/UPGRADING.md`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` | < 50% of the file's real size, measured at the reconciled exact head, rounded down to a power of two (calibration test pins it) | none imposed in G0 — headings are chosen at slice B from the files' REAL headings if any are load-bearing; never invented |
| `LICENSE`, `Dockerfile`, compose files, release workflow, the 4 preflight packet files | existing `min_bytes` (raised to a floor ≈ current size × 0.5 max, same calibration rule) | none |

Calibration rule (slice B, mechanical, content-first): floor := largest power of two
strictly below half the artifact's tracked size — computed ONLY where the final
content already exists. For the 15 existing artifacts that is the RECONCILED EXACT
HEAD (never this prep head); for the 9 authored documents it is the moment each
one's final reviewed content lands (G1). Until then the temporary G0 floors above
ARE the enforced policy values — used now, not parked — and each is recomputed in
the same reviewed change that lands that document's final content. No floor number
is final in this document: the §1 inventory sizes
are evidence as of 232e33b4 only, every existing-artifact floor is recomputed at
the reconciled head, every authored-document floor is recomputed after its content
settles, and a test asserts every policy-required artifact that exists in the repo
actually PASSES its own contract — the policy can never drift into a state the real
tree fails silently.

No private repository name or path appears in any PROPOSED public artifact above —
grounding that lives in the private control plane is cited by opaque id only. The
one pre-existing exception is `docs/UPGRADING.md`, whose current text references the
local control-plane clone path; that is reconciled as the named pre-public-cut
cleanup (§4), never by repeating the path in the new documents.

Final section WORDING settles at G1 authoring under independent review; the policy
consumes whatever headings the reviewed contract freezes. G1 may propose heading
changes — as one reviewed policy+document change, landed by the coordinator with the
calibration and RED/GREEN tests re-run.

## 6. Test plan (RED/GREEN, all offline, temp-dir fixtures)

The fixture builder `create_clean_artifacts` (`test/test_public_cut_preflight.py:75-77`)
is replaced by a policy-derived builder: for each entry it emits a minimal REAL-shape
document (title + one line under each `required_sections` heading + the approval block
when required) generated FROM the policy — the suite keeps deriving from the policy
rather than hand-maintaining a second copy (the file's own comment, lines 46-53).

New proofs, each RED against current HEAD logic and GREEN after G0:

| # | Fixture | Must FAIL with | Proves |
|---|---|---|---|
| T1 | every artifact = the historical `# <path> placeholder` seeding PADDED above its `min_bytes` floor with benign filler, so the floor passes and the placeholder-marker scan is the check that actually fires; plus one unpadded case at the seeded 26 bytes (placeholder line + trailing newline) proving the floor still rejects it | `required_artifact` placeholder-marker finding, per padded artifact; `below floor` for the unpadded one | **the literal historical gap is closed — marker detection proven, not masked by the floor** |
| T2 | `DATA_MAP.md` missing exactly one required H2 | finding naming that section name only | structural check, value-free |
| T3 | `GOVERNANCE.md` with `## Approval record` absent / record line wrong prefix / id `hr-0123` (too short) / id `hr-0123456789abcdefG` (non-hex, uppercase) / id `humanrounds-pinamar` (repo-name-shaped) / id `https://example.org` (URL-shaped) / date `13/99/2026` (wrong shape) / date `2026-13-99` and date `2026-02-30` (right shape, IMPOSSIBLE calendar dates — the semantic-date regression §5.1 requires) | approval-form finding naming the malformed field, never the value | form traceability + strict id alphabet + semantic date validity |
| T4 | institutional doc, 6 real-looking bytes (`Hello\n`) | `below floor` (not `empty`) | floor + honest detail string |
| T5 | artifact with invalid UTF-8 bytes | `not valid UTF-8` | decode fails closed |
| T6 | artifact > `max_scan_bytes` | `oversized` | no silent structure skip |
| T7 | strict-schema family, one case per run: artifact entry with `required_sectons` typo / unknown TOP-LEVEL policy key / unknown `approval_record` key / `record_prefix` altered to anything ≠ the exact literal `approval:` (e.g. `approval-`, `approvals/`) / invalid field types — `min_bytes` boolean, `min_bytes` string, `required_sections` non-list, `placeholder_markers` non-string entry / `policy_version` missing / `policy_version` unknown prefix | `PolicyError`, exit 2, for EVERY case in the family | strict schema on all three key scopes + exact `record_prefix` pin + typed fields + version fail-closed |
| T8 | clean fixture per new builder | phase 4 all-PASS | the builder = the contract |
| T9 | forbidden path present AND all artifacts placeholders | ONLY the forbidden-path finding | fail-first ordering preserved (extends the existing ordering proof) |
| T10 | calibration: every policy artifact present in the repo passes its contract | n/a (assert passes) | policy never outpaces reality; no marker false-positives on legitimate prose (e.g. `SECURITY.md`) |
| T11 | valid/section/boundary on the record line: (valid) a document carrying `record: approval:0f1e2d3c4b5a6978` plus boundary-adjacent prose (`refill`, `filled`, the words `pending review` as ordinary prose) PASSES the marker scan; (stub) the same document with `record: pending` or `record: <fill` FAILS; (boundary) `<fill` does not fire inside `refill`/`filled` | no finding for the valid and boundary cases; stub-value finding (marker + line) for the stub cases | marker rejection narrowed to stub VALUES — the valid approval field and word-boundary prose never trip it |

Plus regression: full existing suite stays green (ordering, symlinks, secret-marker
value-freedom, git-history phases untouched).

## 7. Execution slices (on the reconciled integration head — not here)

**Roles, fixed by this plan:** the EXECUTOR authors the slices and runs the §8 offline
proofs in the working tree — and nothing else. The later **Codex/Terra integration
COORDINATOR** owns every git operation and every outward action: fetch, branch/ref
handling, worktree creation, commits, pushes, deletions (including any eventual
deletion of held refs), the re-freeze of this plan at the exact reconciled head,
managed full CI — the `scripts/turnos_managed_exec.py` profiles `local-ci` and
`local-ci-asgi`, run SERIALLY at that exact head (never interleaved; each mode's
ladder green on its own before the next starts), covering every enforced surface —
and publication. The executor performs no fetch, no worktree creation,
no commit, no push, no ref change, no stash, no reset, no deletion, no publication;
held refs are preserved exactly as they are (§9).

- **Slice A — checker + schema teeth:** STEP 0 captures the private-tree preflight
  baseline finding verbatim into the suite as the frozen expected bytes (§1, A9) —
  before any edit; then policy `2.2.0-tier7` schema (empty new-field usage),
  `_validate_policy` strictness, `check_required_artifacts` extension, T1-T9 + T11
  builder + proofs. One coherent, landable unit.
- **Slice B — the contract table:** per-artifact entries per §5.4 with calibration
  floors (T10), placeholders list, approval requirements.
- **Slice C — documentation sync:** operator contract phase 4 rewritten ("present,
  non-empty" → the structural contract); tier7-readiness.md "Public Artifact
  Completion" updated to name G0-closed/G1-open with this plan linked;
  fail-closed lesson doc gains the structural-check paragraph; solutions INDEX entry.
- **Slice D — verification only:** §8 run in full, offline, results recorded and
  handed to the coordinator with the verified working tree. The coordinator lands each
  slice — commit, push, gates re-run — one slice at a time.

## 8. Acceptance evidence (outcome-based; every gate is offline and safe)

A reviewer re-runs these at the execution head. A candidate tree = the test harness's
fixture tree (the tests build it); the private tree is never a candidate.

1. **A1 — placeholder rejection:** T1 fails per artifact with a placeholder finding.
   `python3 -m unittest discover -s test -p "test_public_cut_preflight.py"` (the
   discover form — the repo's unittest-discover guard blocks `test.test_x`).
2. **A2 — structural rejection:** T2/T3 fail naming section/field, findings contain no
   document content (asserted by grep over captured findings output in-test).
3. **A3 — real artifacts still pass:** T10 green.
4. **A4 — fail-first preserved:** T9 produces exactly one forbidden-path finding.
5. **A5 — strict policy:** T7 exits 2 with `PolicyError`.
6. **A6 — determinism:** preflight over the same fixture twice → byte-identical
   findings output (no timestamps, no ordering instability).
7. **A7 — preflight suite, offline, and ONLY that for the executor:**
   `python3 -m unittest discover -s test -p "test_public_cut_preflight.py"` green,
   run locally by the executor — this suite is fully offline and needs no database.
   The full suites are NOT executor gates: the coordinator runs MANAGED full CI at
   the EXACT reconciled integration head — `scripts/turnos_managed_exec.py` profile
   `local-ci`, then profile `local-ci-asgi`, SERIALLY (each mode's ladder green on
   its own before the next starts) — which covers every enforced surface: backend,
   frontend, routes, cassette replay + audit, and e2e, in BOTH flag modes
   (`local-ci-asgi` fixes `TURNOS_ASGI=1` as an executor-owned test condition no
   source or ambient value can select). Hosted CI's push/PR triggers are disabled
   (cost decision, `scripts/ci_local.sh` header), so this managed ladder IS the
   enforcing full CI; nothing weaker substitutes for it at integration.
8. **A8 — value-free, still:** no finding text includes matched marker context or
   credential material (existing proofs + new section-name assertions).
9. **A9 — private-tree baseline byte-stable:** `python3 scripts/public_cut_preflight.py .`
   on the private tree outputs EXACTLY the one forbidden-path finding captured
   verbatim in slice A step 0 — compared as BYTES against that frozen baseline, no
   ellipsis, no paraphrase; no new phases run, no new information disclosed.
10. **A10 — docs truthful:** operator contract and tier7-readiness describe the new
    contract and still state that institutional approval is external evidence.

**Safe-offline definition (hard constraints for the executor):** no network, no
database, no secrets/credential vault, no Docker builds, no `smoke_prod.py`/`smoke_demo.py`,
no live services, and NO git mutation and NO remote contact of any kind — no fetch,
no worktree creation, no commit, no push, no stash, no reset, no ref creation or
deletion; the executor leaves every held ref untouched and hands over a verified
working tree. Managed full CI — the `local-ci`/`local-ci-asgi` ladder with its
DB-backed suites — is likewise deferred to the coordinator, serially at the exact
reconciled head (§7, A7). Every executor gate above runs
airgapped in the worktree using temp-dir fixtures and the local git binary
(read-only) only.

## 9. Held-origin dependencies and reconciliation

1. **`origin/main` and HEAD have DIVERGED — neither is an ancestor of the other**
   (measured 2026-08-18 at 232e33b4 against the locally known origin/main, no fetch
   performed — fetch belongs to the coordinator, §7: merge-base = 13a11d00, the
   `codex/readiness-post-soak-integration` label; origin/main = b63fe81f carries 6
   commits beyond the merge-base that this line does NOT have — 24073b9e…b63fe81f,
   including origin/main's own landing of the public-cut packet inside 0ee2b2cd and
   the held round-2 merge b63fe81f (server.py retirement, waitlist rewrite, OTP
   changes); HEAD = 232e33b4 carries 10 beyond it — 91aaa293…232e33b4, containing
   THIS line's landing of the same packet via 5f53538a + 54a1f04d — so
   `git rev-list --count origin/main..HEAD` = 10 and the reverse count = 6; the six
   packet files are TODAY byte-identical across both lines — empty `git diff
   origin/main HEAD -- <six paths>` — despite landing through different commits on
   each side; this line IS the round-2 readiness integration line,
   `codex/readiness-round2-integration` sitting exactly at 232e33b4. Both sides
   independently re-implement parts of round-2 (server.py retirement, waitlist), so
   the reconciliation is a real merge with possible content conflicts, never a
   fast-forward of either side. These numbers are a snapshot: origin/main may move
   again before the coordinator fetches and merges). "Reconciled" therefore does NOT
   mean fast-forwarding
   either side: the coordinator MERGES BOTH LINES, preserving every commit on each —
   and because the merge can move files both sides touch, the re-freeze RE-RESOLVES
   every dependency in this section (merge-base, divergence counts, packet identity,
   held refs, all citations) at the resulting exact head, before any execution. That
   merge and that re-freeze are coordinator actions, never executor ones. **Every
   file:line citation above is valid at 232e33b4 and MUST be re-verified at the
   re-freeze head** — the executor re-checks anchors before editing (they are all
   inside the six packet files plus the two roadmap docs, which this line owns
   exclusively).
2. **`codex/tier7-public-cut-preflight` (6457f7a6)** — the approved packet's original
   branch; NOT an ancestor of HEAD (`git merge-base --is-ancestor` exits 1) but
   content-identical on all six packet files (empty diff, verified 2026-08-18 at
   232e33b4). The branch is a HELD ref and this plan directs NO deletion: the empty
   diff is recorded here as supersession EVIDENCE for the coordinator, who alone
   decides and performs any ref deletion, under standard branch hygiene, after G0
   lands. (`glm/integrate-public-preflight` sits at the same 6457f7a6 and is held
   under the same rule.)
3. **`glm/tier7-docs-slice-prep`, `glm/tier7-publication-prep`** — two labels sitting
   exactly at 232e33b4 (re-verified 2026-08-18; prior prep worktrees, no unique
   commits). This plan joins that family as a third prep artifact — frozen as of
   232e33b4, re-freeze deferred (Status). None of these branches is an execution
   head, none carries work G0 depends on, and all are PRESERVED: any ref handling
   belongs to the coordinator.
4. **Private ops repo `humanrounds-pinamar`** — holds the external evidence records
   (governance approval, Tier 1.1 revocation confirmation, audit archive). G0 depends
   on its EXISTENCE as the issuer of the opaque `approval:` ids, never on reading it;
   offline execution requires no access. Its name and paths NEVER appear in a public
   artifact — public documents carry only the opaque pointer (§5.1) — and it stays a
   forbidden path in any public tree.
5. **Tier 1.1 remains externally blocked** (maintainer/Brila Google Places key revocation
   confirmation) — orthogonal external evidence; G0 neither closes nor claims it.
6. **Tier 6 delivery design gates Tier 7** (tier6-delivery-design.md:10) — the
   fresh-cut readiness work continues on its own line; G0 touches none of its surface
   (release.yml, compose files are only CALIBRATED, never edited).

## 10. Risks

| Risk | Mitigation |
|---|---|
| Byte floors read as substance-proof | §3/§5.1 state the floor's honest role; sections + placeholder ban carry the weight |
| Fabricated approval pointers pass the form check | §3 boundary statement; external-evidence phase keeps approval `passed=None`; independent review of the candidate + control-plane record is the substance gate |
| Opaque approval ids weaken traceability | Opacity hides only the LOCATION of the evidence, not its existence or date; the control plane resolves ids, and resolving/auditing stays external (§3, §5.1) |
| Section contract over-constrains G1 authors | Headings double as the authoring outline; G1 may amend policy+doc in one reviewed change |
| Policy/test drift | Tests derive from the policy (existing pattern, lines 46-53); T10 calibration pins policy to reality |
| Strict schema breaks a future legitimate field | Extending the schema is a deliberate, reviewed policy-version bump — that friction is the point |
| New file reads leak content via findings | Findings carry names/line numbers only; §8 A8 asserts it; all reads happen after the forbidden-path gate |

## 11. Review checklist for the independent reviewer

1. §1 gap claims: re-run `python3 scripts/public_cut_preflight.py .` (expect exactly
   the one forbidden-path finding that slice A step 0 captures as the frozen
   baseline), re-read `test_public_cut_preflight.py:75-77`, and confirm the §9.1
   divergence counts (`git rev-list --count origin/main..HEAD` and the reverse) at
   the head under review.
2. §2 packet identity: `git diff codex/tier7-public-cut-preflight HEAD -- <six paths>`
   (expect empty) and `git merge-base --is-ancestor 6457f7a6 HEAD` (expect exit 1) at
   a head containing both.
3. §5.4 table: is every required section real and necessary? Is the DPA "Current
   posture" constraint (§3.1) correctly honest? Is the approval-id alphabet
   (`^hr-[0-9a-f]{16}$`) closed against URLs, paths, repo names, and hosts — and are
   the marker rules word-bounded and per-artifact so legitimate `SECURITY.md` prose
   cannot be rejected, and narrowed to stub values so a valid
   `record: approval:<id>` line never trips them (T11)? Does the date rule enforce
   semantic calendar validity, not just shape?
4. §6 T1-T11: sufficient? Each maps to an acceptance item in §8 (T11 → A3/A8).
5. Bounds: confirm nothing in §4 in-scope touches production code beyond the checker,
   policy, tests, and docs — and that §7/§8/§9 assign every git mutation, ref
   deletion, and publication action to the coordinator while preserving held refs.

## References

- [Tier 7 readiness roadmap](tier7-readiness.md) · [Preflight operator
  contract](../publication/preflight.md) · [Fail-closed design
  lesson](../solutions/security/fail-closed-public-cut-preflight.md) ·
  [Open-source readiness plan](open-source-readiness-plan.md) · [Tier 6 delivery
  design](tier6-delivery-design.md) (gates Tier 7; `cloud_shared` honesty precedent) ·
  [Legal/PII architecture](../architecture/legal.md) (DATA_MAP grounding)
