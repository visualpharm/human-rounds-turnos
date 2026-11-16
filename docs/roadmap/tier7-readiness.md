# Tier 7 Readiness Roadmap — Public Publication

## Status: Preflight Integrated and Locally Verified, Publication OPEN

**Current State (2026-10-06):** A code-side readiness pass measured a fresh
single-commit export of the tree with `scripts/public_cut_preflight.py`
(ambient `GIT_*` overrides unset). Before: 32 of 37 locally-verifiable checks
passed (86%; denominator = 1 binary-asset set + 12 git-history checks + 24
required artifacts; the 3 external-evidence items are excluded and the
forbidden-path and secret-marker phases produced no findings). Five artifacts
failed: `DPA.md`, `DATA_MAP.md` and `CLINICAL_SAFETY.md` were missing;
`HIGH_AVAILABILITY.md` and `INCIDENT_RESPONSE.md` lacked only the approval
record. After: the three missing documents exist as owner-review drafts built
from the code and `docs/` (processor inventory, table-level data map,
retention defaults, red-flag floor, compose clipping), each carrying short
"Decision needed:" lines where the choice is the product owner's or the hospital's; every
required H2 section, byte floor and placeholder rule passes, so the count is
unchanged at 32/37 by pass/fail but structurally 37/37 — all five remaining
failures are the single finding `malformed approval record field: record`,
which only a real `hr-` approval id from the owner can close. Remaining, by
owner: the five approval ids (the product owner, after the hospital reviews DPA, data map
and clinical safety); GitHub visibility/protections/secret scanning (GitHub
settings); the old Google Places key revocation (the product owner); Meta Business
number, templates and production access (Meta). The earlier paragraph below
is kept as the 2026-08-23 baseline.

**Brought over from `chore/public-cut-readiness` (2026-10-28):** `DPA.md` and `CLINICAL_SAFETY.md` came over
as written on 2026-10-06 (text untouched). The 2026-10-06 root `DATA_MAP.md`
was NOT carried: the maintained draft (470 lines, kept in step with the schema
by `test/test_data_map.py`) superseded it. On 2026-11-10 that maintained map
moved to the root `DATA_MAP.md` for the public cut, still without an approval
record (the test forbids inventing one).

**Current State (2026-08-23):** The preflight checker is integrated, tested,
and documented. Private-pack evacuation is complete and the public-default
candidate is locally verified. Conduct, governance, maintainers and support are
approved and documented. Publication remains OPEN because five required institutional
artifacts and their real approvals do not yet exist; the gate correctly refuses
placeholders.

## What Tier 7 Means

**Tier 7 = Public open-source publication.** The codebase, documentation, and deployment artifacts are published to a public GitHub repository with:

- No private data, credentials, or control-plane state
- No institutional-specific configuration (e.g., packs/ar-b-pinamar)
- Complete governance, DPA, safety, and HA documentation
- Single-commit, self-contained git history
- All required public artifacts present

## Completed Work

### Preflight Checker (COMPLETE)

- **Implementation:** `scripts/public_cut_preflight.py` — deterministic, value-free, fail-closed verification
- **Policy:** `scripts/public_cut_policy.json` — data-driven rules for forbidden paths, required artifacts, secret markers, and external evidence
- **Test Coverage:** `test/test_public_cut_preflight.py` — comprehensive coverage of all phases and edge cases
- **Documentation:** `docs/publication/preflight.md` — operator-facing contract and usage

**Key Properties:**
- Forbidden paths fail FIRST by NAME; content beneath is never read
- Symlinks rejected without following (file, directory, root)
- Credential values NEVER appear in findings (shapes only)
- Git history must be fresh: single commit, no remotes, no extra refs
- Ambient GIT_* overrides detected and rejected
- External evidence (GitHub settings, institutional approval, Meta provisioning) reported as informational, not locally verified

### Fail-Closed Design (COMPLETE)

Lesson documented in `docs/solutions/security/fail-closed-public-cut-preflight.md`:

- Name-based rejection prevents content exposure
- Phase ordering (forbidden paths first) prevents cascading failures
- Value-free reporting prevents credential leakage
- Inspection errors fail-closed with findings, not silent passes

## Remaining Work

### Institutional Readiness (IN PROGRESS)

The following evidence is EXTERNAL to the preflight checker and must be verified through institutional processes:

1. **GitHub Publication Setup**
   - Repository visibility: Public
   - Branch protections: main branch protected
   - Secret scanning: Enabled
   - Push/merge restrictions: Configured
   - Publishing entity: Authorized and maintained

2. **Project Governance Approval**
   - Maintainers: Named and reachable — complete 2026-08-23
   - Support/LTS posture: Approved — complete 2026-08-23
   - Community conduct policy: Approved — complete 2026-08-23
   - DPA and data governance: Approved
   - Clinical safety owner: Approved
   - Incident/HA RTO-RPO: Owner and budget confirmed
   - First release: Authorized

3. **Credential and Platform Provisioning**
   - Old the maintainer/Brila Google Places key: Revoked
   - Meta Business number: Configured
   - Meta templates: Configured
   - Meta production access: Verified

**NOTE:** DO NOT create placeholder governance/DPA/safety/HA documents merely to satisfy the policy. These must be real, approved institutional documents.

### Public Artifact Completion (G0 CLOSED / G1 OPEN)

The required-artifacts gate now enforces a **structural** contract, not existence-only: size floor, required H2 sections, word-bounded placeholder-marker rejection, and approval-record *form* (opaque `hr-` + 16 lowercase hex id, semantically valid ISO date). Policy `2.3.0-tier7`. See [Required-artifacts gate hardening](tier7-g0-required-artifacts-plan.md). G0 did **not** author the nine institutional documents missing at that stage.

**G0 (closed):** all 24 policy-required artifacts reject padded historical `# <path> placeholder` stubs, alongside skeleton sections and untraceable approval claims. Byte floors catch short stubs; they do not prove substance.

**G1 (open):** five institutional documents remain unapproved: `DPA.md`,
`DATA_MAP.md`, `CLINICAL_SAFETY.md`, `INCIDENT_RESPONSE.md`, and
`HIGH_AVAILABILITY.md`. Four exist at the repository root as drafts awaiting
the owner's approval record (2026-10-06); the data map joined them at the root
on 2026-11-10 (pinned by `test/test_data_map.py`), also without an approval
record, so all five still fail the approval-record check. `CODE_OF_CONDUCT.md`, `GOVERNANCE.md`,
`MAINTAINERS.md`, and `SUPPORT.md` were approved and authored on 2026-08-23.
Do not commit placeholders.
Institutional approval substance stays external evidence.

**Current Status:** The five files above exist as fact-based drafts and pass
every structural check; each still fails the gate on its absent approval
record, by design, until the product owner records a real `hr-` id. Their open
"Decision needed:" lines are the hospital's and the owner's calls.

### Data Pack Isolation (COMPLETE — 2026-08-20)

The real Pinamar production pack is now held in a dedicated private repository,
restore-drilled encrypted backup, and verified production volume. The public
tree:

1. contains no private town pack;
2. defaults to `packs/example-town/`;
3. resolves explicitly configured private packs through
   `TURNOS_PRIVATE_PACKS_DIR`; and
4. fails closed when a required private pack is absent or corrupt.

## Readiness Criteria

Tier 7 publication is ready when ALL of the following are true:

1. **Preflight Check Passes** — A candidate tree runs `scripts/public_cut_preflight.py` with exit code 0
2. **Institutional Approvals Complete** — Governance, DPA, safety, HA, and first-release approvals obtained
3. **GitHub Setup Complete** — Repository is public, protected, and scanned
4. **Credentials Revoked/Provisioned** — Old keys revoked, new platforms configured
5. **No Private Data** — All private packs, configs, and institutional references removed
6. **Required Artifacts Real** — All governance/DPA/safety/HA documents are real, not placeholders

## Next Steps

1. **DO NOT commit placeholders** — Governance/DPA/safety/HA documents must be real
2. **DO NOT publish yet** — Institutional readiness is incomplete
3. **USE the preflight** — Run `scripts/public_cut_preflight.py` on any candidate tree before publication
4. **VERIFY externally** — Check GitHub settings, institutional approvals, and provider dashboards manually

## References

- [Preflight operator contract](../publication/preflight.md)
- [Required-artifacts gate hardening (G0)](tier7-g0-required-artifacts-plan.md)
- [Fail-closed design lesson](../solutions/security/fail-closed-public-cut-preflight.md)
- [Preflight implementation](../../scripts/public_cut_preflight.py)
- [Preflight policy](../../scripts/public_cut_policy.json)
