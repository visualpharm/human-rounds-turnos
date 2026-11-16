# Public Cut Preflight — Operator Contract

## What This Is

This is a **pre-conditions verification gate**, not a publication action. The preflight checker validates that a candidate repository tree is safe for public publication **before** any network access, git push, tag creation, or visibility change occurs. It performs NO mutations, NO network operations, and NO data moves.

## Core Contract

The preflight checker (`scripts/public_cut_preflight.py`) deterministically verifies a candidate repository against a data-driven policy (`scripts/public_cut_policy.json`). A candidate passes when **all locally-verifiable checks succeed**. External-evidence findings are informational only and do not affect pass/fail.

### Pass Criteria

A passing candidate MUST be:

1. **Forbidden-path free** — No private/control-plane paths present by name
2. **Artifact complete** — All policy-required public artifacts present, regular files (not symlinks), above the per-artifact size floor, and structurally complete against the policy contract (required H2 sections, word-bounded placeholder-marker absence, and approval-record *form* where required). Institutional approval *substance* remains external evidence.
3. **Binary-asset closed** — Every declared binary asset family has a strict manifest whose entries exactly match the discovered files, byte counts, and SHA-256 digests
4. **Credential-clean** — No credential-shaped markers in scanned text files
5. **Git-fresh** — Exactly one commit on main, no remotes, no extra refs, clean state, self-contained
6. **Environment-clean** — No ambient GIT_* overrides that would affect git operations

### Fail-Closed Behavior

The checker fails closed: anything it cannot verify safe is treated as unsafe. Specifically:

- **Forbidden paths fail FIRST** — When ANY forbidden path is present, the check stops immediately after phase 1. Content scan, required-artifact validation, and git history checks are SKIPPED. Only forbidden-path findings are produced.
- **Forbidden paths are NAME-ONLY** — Paths are matched by NAME/pattern/prefix; the content beneath them is NEVER read, opened, or scanned.
- **Symlinks are rejected without following** — File, directory, and root symlinks are all rejected in the forbidden-path phase; their targets are never dereferenced.
- **Credential values NEVER appear in findings** — Secret-marker findings report only the marker class ID and line number; the matched value is never included.
- **Binary values NEVER appear in findings** — Binary-manifest failures report only a verification category; they never expose binary bytes, digests, expected values, or discovered asset filenames.

## What Gets Verified

### Phase 1: Forbidden Paths (fail-first)

Private and control-plane paths are rejected by NAME only. Content beneath them is never read:

- `packs/ar-b-pinamar/` — Forbidden legacy location for a real production town pack
- `packs/*-private/` — Any private pack family
- `CLAUDE.local.md` — Private per-install overrides
- `.claude/` — the whole agent harness directory (hooks, skills, settings, inbox, parking, memory, control plane). A prefix rule may carry an `allow` list of relative paths inside it that may ship; the list is validated (prefix rules only, entries inside the prefix) and is empty today
- `.env`, `.env.*` (except `.env.example`) — Environment secrets
- `humanrounds-pinamar/` — Private operations repository
- `docs/private/` — Private documentation
- `.git/worktrees/`, `commondir` — Git worktree metadata
- All symlinks (file, directory, root)

### Phase 2: External Evidence (informational only)

External evidence is EXPLICITLY EXTERNAL and reported as informational (passed=None). The tool does NOT pretend to verify these locally:

- GitHub repository visibility, protections, and secret-scanning configuration
- Project governance approval and LTS plan
- DPA and data governance approval
- Clinical safety owner approval
- Incident/HA RTO-RPO owner and budget
- First release authorization
- Old credential revocation (e.g., maintainer/Brila Google Places key)
- Meta Business number, templates, and production-access provisioning

These must be verified OUTSIDE the preflight check, through institutional processes, GitHub settings, and provider dashboards.

### Phase 3: Ambient Environment

Ambient GIT_* environment variable overrides are detected and rejected (e.g., `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_*`).

### Phase 4: Required Artifacts

All policy-required public artifacts must exist, be regular files (not symlinks), meet the per-artifact `min_bytes` floor, decode as UTF-8, and satisfy the optional structural fields in `scripts/public_cut_policy.json` (`required_sections` as Markdown H2 headings, word-bounded `placeholder_markers`, and `approval_record` form). The floor catches stubs; it does not prove substance. Approval-record checks verify a dated opaque `record: approval:hr-<16 lowercase hex>` pointer and a real calendar `date: YYYY-MM-DD` — they do not verify that a human approved the document. Institutional approval remains Phase 2 external evidence (`passed=None`). See the policy for the current authoritative list (includes CODE_OF_CONDUCT.md, DATA_MAP.md, governance, DPA, safety, HA, incident response, etc.).

### Phase 5: Closed Binary Asset Sets

Every policy-registered binary family is refused unless it has a strict adjacent `assets.json` manifest. The manifest has exactly two top-level keys: `manifest_version` (`"1.0"`) and `assets`. Every asset entry has exactly `path`, `sha256`, `bytes`, `media_type`, `provenance`, and `review`, is path-safe, sorted, and unique. For the registered JPEG family, `media_type` must be `image/jpeg`; the checker verifies the JPEG signature before it hashes the file.

`provenance` is exact and deliberately narrow: `classification: synthetic`, `source: from_scratch`, and `from_scratch`, `no_real`, and `no_derived` all set to `true`. `review` is exact too: `attested: true`, an opaque `reviewer_id` in the existing `hr-<16 lowercase hex>` form, and a real calendar date. These fields make a public claim inspectable without embedding binary content, patient material, a digest, or a reviewer name in failure output. Empty manifests, symlink roots/manifests, registered-extension directories, non-regular entries, and every unexpected or omitted file fail closed.

The candidate's files with the registered extensions must exactly equal the manifest list; every file is then opened with `O_NOFOLLOW`, signature-checked, measured, and hashed.

The `packs/example-town/ordenes-demo` corpus is registered with a reviewed
manifest and contains only from-scratch synthetic replacements. Its manifest is
generated from those reviewed files, never inferred from a prior corpus. Any
missing, extra, changed, non-JPEG, or nonregular asset makes the candidate fail
closed.

### Phase 6: Secret Markers

Text files are scanned for credential SHAPES only. Values are never reported. Marker classes include:

- API keys (Anthropic, OpenRouter, Google, AWS, GitHub, GitLab, Slack)
- Private key blocks
- Secret assignment patterns

Installation-private markers (`private_marker_classes` in the policy) are scanned the same way, and a finding names only the class, the path and the line:

- `regex` classes: tailnet host names, Coolify resource ids written next to the word "coolify"
- `token-sha256` class: names of real people and private hosts (a host as its first two labels), matched as salted SHA-256 digests of case- and accent-folded words and word pairs
- `public-ip` class: any globally routable IPv4/IPv6 address, or one in the shared address space where tailnet nodes live, outside the exempt prefixes (the test tree); documentation, private and loopback ranges pass, and the listed networks (as digests) are flagged everywhere, tests included

The digests live in `scripts/public_cut_private_digests.json`, named by each class's `hashes_file`. That file is export-ignored and forbidden in a cut: a salted digest of a short name can be reversed with a dictionary, so neither the names nor their digests ship. Where the file is absent (a public clone), each class that needs it is reported as external evidence (`passed=None`) instead of passing silently.

## Building the cut

`scripts/public_cut_build.py --ref <commit> --summary-dir <dir>` builds the candidate end to end and publishes nothing:

1. `git archive` of the commit (honouring `export-ignore` in `.gitattributes`) into a new, empty directory outside the repository
2. a fresh one-commit repository there, branch `main`, neutral author, no remotes
3. the preflight above
4. gitleaks over the cut with the cut's own `.gitleaks.toml` (default rules plus reviewed path/line allowlists) and `.gitleaksignore` (reviewed fingerprints of synthetic test fixtures)
5. a summary (`public-cut-summary.json` and `.md`): files kept and left out, findings by class with path and line, and references from kept files to left-out ones. It never contains a matched value.

A moved line in a reviewed fixture shows up again as a new gitleaks finding and is reviewed before it is added to `.gitleaksignore`.

### Phase 7: Git History

The git history must represent a fresh, self-contained single commit:

- Toplevel matches candidate root
- Ordinary `.git` directory (not a file or symlink)
- Clean working tree (no staged or unstaged changes)
- HEAD on main branch (not detached, not other branch)
- Self-contained (git-dir equals common-dir; not a linked worktree)
- Not a shallow clone
- No grafts, no replace refs, no alternates
- No configured remotes
- Only the default branch exists (no tags, no extra branches, no stash)
- Exactly one commit reachable across all refs


## Current Status

The real Pinamar pack was evacuated to a dedicated private repository and a
verified production volume on 2026-08-20. The public tree now clears the
forbidden-path phase; publication remains blocked by later publication
findings, including the missing approved institutional artifacts.

Publication remains OPEN. The preflight gate exists to ensure any future public cut is clean, self-contained, and free of private data, control-plane state, and credential shapes BEFORE any network or visibility operation.

## Usage

```bash
# Run preflight against a candidate tree
python3 scripts/public_cut_preflight.py /path/to/candidate

# Exit codes: 0 = pass, 1 = policy violation, 2 = usage/environment error
```

## Policy Customization

The policy file (`scripts/public_cut_policy.json`) is data-driven. To add a new forbidden path, artifact requirement, or secret marker class, edit the policy JSON. The script itself is generic and requires no code changes for policy updates.

## See Also

- [Fail-closed preflight design lesson](../solutions/security/fail-closed-public-cut-preflight.md)
- [Tier 7 readiness roadmap](../roadmap/tier7-readiness.md)
