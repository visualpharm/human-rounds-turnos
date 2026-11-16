# Tier 6 — data, compliance, delivery (design)

Scope per [open-source-readiness-plan.md](open-source-readiness-plan.md)'s Tier 6:
(a) extract `packs/ar-b-pinamar` (real doctors' photos/names) to a private repo,
glued by config; (b) three switchable AI inference profiles, benchmarked on an
anonymized referral corpus; (c) WAHA demoted to dev-only, Meta documented as the
deployment path; (d) versioned releases + ghcr images, `docker-compose.yml` as
the minimal install path, a Helm chart, Coolify as a documented reference.

**This tier gates Tier 7.** The fresh-cut public repo can only be honest the
day `packs/ar-b-pinamar` — real names, real photos, real matrícula numbers —
is no longer reachable from ANY commit in the tree that gets published. Every
other Tier 6 item is real value on its own; this one is a hard precondition.

**Revised 2026-08-06** against a Codex audit of the first draft (adversarial
continuation review of `origin/main` at `91dce079`) that found the design's
own execution order contradicted its own safety rule, plus seven other real
gaps — fail-closed behavior for a required pack, operational integrity for
the private-data move, credential/naming honesty in Area B, unproven
self-hosted claims, an under-scoped benchmark, an unproven `workflow_dispatch`
claim under the Actions billing lock, and missing artifact-integrity work.
All eight are incorporated below, each marked "Codex correction N" at its
landing point, and the execution-slice order now structurally enforces the
safety sequencing rather than describing it in prose. **Slice 0 is integrated
on `main` via `89743366`. Slices 1–5 were executed on 2026-08-20: the private
repository and encrypted restore-drilled backup exist, production resolves the
verified volume, and the public-removal candidate deletes the in-tree pack.**
A hermetic synthetic evacuation rehearsal (2026-08-19)
proves inventory / archive / restore / integrity / in-process render identity
on fictional packs only — it does not satisfy slice 2's encrypted-backup gate.
Independent review the same day required three corrections now in the frozen
rehearsal: import and full-suite isolation from the checked-in private pack,
real CLI before/after render parity with scratch cleanup, and archive bytes
that ignore source mtime and ownership.

## What exists today and is NOT re-done

Audited directly against the CURRENT worktree (`origin/main` as of this
design), not against the roadmap doc's own `## Status` section, which is
stale — it still shows Tier 4/5 as `[ ]`/`[~]` while the actual tree has
**Tier 4's full route migration closed** (`fa6b22da` "Tier 4 slice 8 lote de
cierre: migra TODO lo que quedaba (28 archivos), route migration queda
cerrada", 40 `*_router.py` FastAPI files, `alembic/`, `procrastinate`,
`static_fingerprint.py`, `health.py`, `asgi_main.py` all present and wired)
and **Tier 5 mid-flight** (ES-module facades, vendored Preact+HTM islands,
one server-routed admin screen pilot on `intake`). Anyone picking this design
up should re-run this same check before assuming anything below is still
current — this tree moves fast.

- **The pack chain (`core/data_packs.py`)** — `packs/global` → `packs/ar` →
  `packs/ar-b` → an optional private town pack, override/merge/compose resolution,
  L5 per-center packs, the `example-town` synthetic pack. `webdata/` is
  **empty** — every one of its ~35 files (JSON + 7 binary/photo directories:
  `centro-photos/`, `centros-brila-info/`, `doctor-photos/`, `medico-photos/`,
  `photos-cache/`, `fuentes/`, `talleres/`) were later evacuated to the
  private deployment pack (`docs/architecture/data-packs.md`, phase 2b slices
  6-9). The **25 MB, 475-file** real dataset is no longer tracked in this
  repository. It includes real doctor names, real headshots, and a Google Places API key history
  (Tier 1.1, still open pending the maintainer/Brila's rotation confirmation). The
  single physical-path choke point is `data_packs._pack_dir(pack_id)`
  (`core/data_packs.py:347-351`, `os.path.join(_PACKS_DIR, pack_id)`) — every
  resolver (`resolve_path`, `merged`, `compose`) calls it, nothing else
  constructs a pack's directory path. `packs_dir()` (`core/data_packs.py:360-365`)
  is the one public boundary accessor other modules use to validate a
  resolved path is inside the packs tree (`server.py`'s `/webdata/` and
  `/data/<f>` compat static routes).
- **`/webdata/<path>` is a PUBLIC, cache-baked URL contract**, not an
  implementation detail. `patient_site.py` and `servicios.json`'s own `foto`
  fields build literal `/webdata/...` URLs; `server.py`'s `do_GET` resolves
  them through `sitedata.path()`/`data_packs.resolve_path()` before falling
  through to static serving. This route is still legacy (`server.py`, not yet
  a `*_router.py`) — the Tier 4 strangler proxy passes it through unmodified.
  Any pack-extraction mechanism MUST keep resolving through this same path,
  or every already-cached `/webdata/doctor-photos/<id>.webp` URL 404s.
- **The demo (`demo.humanrounds.org`) never touches `ar-b-pinamar`.**
  `docs/demo-installation.md` sets `TURNOS_WEBDATA_DIR=/app/packs/example-town`
  — **legacy single-directory mode**, not the pack chain. `example-town` is
  fully synthetic (50 fictional doctors, `packs/example-town/medicos.json`),
  ships in the public tree, and has zero dependency on Pinamar's pack.
  **Verified this session**: the roadmap's open item "verify weekly agenda
  coverage" — counting `franjas[].dias` across all 50 fictional doctors —
  covers all 7 weekdays (Mon-Fri 23-34 doctors/day, Sat 11, Sun 5); no day of
  the week renders an empty demo queue. This item is DONE, not a Tier 6 slice.
- **`ai_client.py` is a single hardcoded path to ONE provider.** `call()`
  POSTs to `OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"`
  (`ai_client.py:40`) with backup-KEY failover (`OPENROUTER_API_KEY_BACKUP`,
  same provider, sticky 5 min on 402/403 — `ai_client.py:44-142`). Five
  callers (`ai_orden.py`, `chat_agent.py`, `prompts.py`, `translate.py`,
  `ocr_dni.py`) build their own request bodies and call `ai_client.call()`.
  There is no provider abstraction today — `pii.py`'s own docstring states it
  plainly: "todas [las llamadas] salen por `ai_client.call()` a OpenRouter."
  One model, `TURNOS_AI_MODEL` (default `google/gemini-3-flash-preview`),
  ~$0.001/order for referral reading (N=1 reading, `ia-y-ordenes.md`).
- **Settings already has the exact extension shape Tier 6b needs.**
  `settings_admin.INTEGRATIONS` (`settings_admin.py:46-51+`) is a declarative
  registry — 8 cards, each a list of `(config_key, label, "text"|"secret")`
  tuples, generic GET/POST/masking/`/probar` machinery. The OpenRouter card
  already has `TURNOS_AI_MODEL`/`TURNOS_AI_MODEL_FALLBACK` as plain `"text"`
  fields sitting next to the secret key fields — adding a base-URL/profile
  field is the same shape, not a new mechanism.
- **`pii.py`'s scrub/restore doctrine is provider-agnostic already** — it
  operates on the text BEFORE it reaches `ai_client.call()`, with zero
  knowledge of which provider is on the other end. The one deliberate
  exception — the referral image/PDF travels unscrubbed, because reading the
  whole order IS the task — is a legal-copy decision (`legal.py`), not a
  transport one. Both facts hold unchanged under every AI profile below.
- **WhatsApp already has two providers behind ONE webhook, selected by env,
  not hardcoded to either.** `whatsapp_chat.py`'s `/api/whatsapp/webhook`
  normalizes WAHA and Meta Cloud API inbound to one shape; outbound transport
  is `otp_send.wa_provider()` (2026-08-14 transport contract: Meta by
  explicit pin or token; WAHA only as an explicit dev opt-in against a
  loopback URL — a remote WAHA URL never sends; otherwise no-op). **Meta is
  the only production transport and is still in TEST mode**
  (`chat-y-whatsapp.md`: "A test number only talks to the ≤5 verified
  recipients added on the app's API Setup page") — going to production on
  Meta requires Meta Business verification and WhatsApp Business API
  production access, an operational approval process, not a config flip.
  This is a real gap Tier 6c has to document, not just a code toggle.
- **The minimal-install path is already substantially built.**
  `docker-compose.yml` (app + Postgres 16, healthchecks, `/app/data` volume,
  `/setup` first-run wizard), `install.sh`, `docs/INSTALL.md` (Docker Compose
  + bare metal + systemd/launchd), `CONTRIBUTING.md`, and
  `docs/UPGRADING.md`'s "Release conventions" section (semver policy, when
  `v1.0.0` starts, `CHANGELOG.md` discipline) all exist and are current.
  **What does NOT exist**: any `ghcr.io` publish workflow, a Helm chart, or a
  PUBLIC generic Coolify how-to (the only Coolify doc in existence is the
  PRIVATE `humanrounds-pinamar` repo's Pinamar-specific one — real app UUIDs,
  real webhook secrets — which a stranger cloning the OSS repo can never see
  and shouldn't need to).
- **A real, load-bearing drift found while auditing this**:
  `docs/UPGRADING.md`'s "What upstream owns vs. what's yours" table currently
  lists `packs/ar-b-pinamar` under **"Upstream (this repo)"** — i.e. it
  documents Pinamar's real data as something the public engine ships. That
  statement becomes false the moment Tier 6a lands and must be corrected in
  the SAME commit that removes the pack from the tree (see Area A below) —
  flagged here so it isn't missed as a "someone else's doc" edit.
- **The module contract (Tier 2), test harness (Tier 3), and connectors
  framework (`docs/connectors.md`) are separate mechanisms this design does
  NOT reuse for (a)/(b).** A data pack is not a `modules/<id>/module.json`
  app module (it has no routes/nav/lifecycle — `core/data_packs.py` is a pure file
  resolver) and AI inference is not one of `connectors.md`'s "seven
  capabilities" (those are all country-specific government integrations —
  RENAPER, PUCO, HSI; AI inference is connector-agnostic, called from every
  country's deployment identically). Both extension points below are new,
  intentionally thin, single-choke-point additions — not a fit for either
  existing framework.

## Area A — `packs/ar-b-pinamar` → private repo, glued by config

### Repo shape

**Recommended: a NEW dedicated private repo, `humanrounds-pinamar-data`,
sibling to the existing ops repo `humanrounds-pinamar`.** Not folded into the
ops repo. Reasoning:
- The ops repo's own README already scopes it as "deploy config, audits, ops
  notes" — 25 MB of binary doctor photos + PII is a different KIND of
  content with a different blast radius if leaked (a photo + a real name +
  a matrícula number is directly re-identifying; a Coolify webhook secret
  pointer is not), and mixing them means anyone who needs ops-repo access
  (a future contractor debugging a deploy) also gets doctor-photo access by
  default, with no way to separate the grants later without a repo split
  anyway.
- **Its layout should mirror the pack tree directly**: the data repo's root
  IS a `packs/` directory (`packs/ar-b-pinamar/...`, same relative layout as
  today) — not a flat dump. This makes the repo naturally extensible if
  Human Rounds ever runs a second town's reference deployment: a second
  private pack (`packs/ar-b-<othertown>/`) lands in the SAME data repo,
  versioned and access-controlled together, without inventing a second glue
  mechanism.
- A fresh `git init` for the new repo, not a filtered history export from
  `sana-turnos`. The pack's history in THIS repo already carries the
  Google-Places-key incident (Tier 1.1) and 900+ commits of iteration —
  re-exporting that history into the "more private, not less" destination
  gains nothing and re-plants the same secret-in-history problem one repo
  over. A single "initial import" commit of the CURRENT `packs/ar-b-pinamar/`
  tree is the entire migration.

### The glue mechanism — one function, additive

`data_packs._pack_dir(pack_id)` (`core/data_packs.py:347-351`) is the single
choke point every resolver already goes through. Extend it, don't replace it:

```python
def _pack_dir(pack_id):
    private_root = config.get("TURNOS_PRIVATE_PACKS_DIR")
    if private_root:
        candidate = os.path.join(private_root, pack_id)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(_PACKS_DIR, pack_id)
```

- `TURNOS_PRIVATE_PACKS_DIR` unset (every OSS installation, the demo, CI, any
  fresh clone) → behavior is **byte-identical** to today, because the
  `if private_root` branch never fires. This is the same "degrade, never
  crash" contract every other pack-chain knob already follows
  (`docs/architecture/data-packs.md`'s repeated framing).
- Set (Pinamar's own local dev and prod) → `ar-b-pinamar` resolves from the
  private root FIRST; any OTHER pack id in the chain (`global`, `ar`, `ar-b`,
  `example-town`) still resolves from the public `packs/` tree, because the
  private root is only consulted when the id's directory actually exists
  there. **This "degrades silently, like a missing pack does today" default
  is exactly what Codex's audit flagged as unsafe for `ar-b-pinamar` once it
  is a REQUIRED pack in prod** — see "Fail-closed readiness contract" below,
  which overrides the silent-degrade default for any pack id an install
  explicitly marks required. The silent-degrade behavior stays correct and
  unchanged for every OPTIONAL pack (the vast majority — a missing `ar-b`
  override for a service nobody customized locally is still fine to drop).
- `packs_dir()` (`core/data_packs.py:360-365`) — the boundary accessor
  `server.py`'s `/webdata/` and `/data/<f>` static routes use to validate a
  resolved path is "inside the packs tree, not a path-traversal escape" —
  needs a matching `private_packs_dir()` accessor, and both static-route
  boundary checks grow a second allowed root. This is the ONE other file
  this slice touches for correctness, not a redesign of either route.
- The `/webdata/<path>` URL contract is untouched. A request for
  `/webdata/doctor-photos/gonzalez-juan.webp` still resolves through
  `sitedata.path()` → `data_packs.resolve_path()` → `_pack_dir("ar-b-pinamar")`
  → now the private root — same URL, same response bytes, different
  physical disk location. Zero HTML/JSON/JS changes anywhere in the app.

### Fail-closed readiness contract (Codex correction 2)

A configured private-pack root that is missing, incomplete, corrupt, or a
path-boundary escape must never boot a plausible-looking but incomplete
clinical deployment. The existing pack chain's "degrade, never crash"
doctrine is right for OPTIONAL packs and wrong for a REQUIRED one — Pinamar
prod without `ar-b-pinamar` is not a degraded Pinamar prod, it is a
different, broken deployment serving synthetic-looking gaps (missing doctor
photos, missing centers) to real patients.

- **New config knob, `TURNOS_REQUIRED_PACKS`** (comma-separated pack ids,
  e.g. `ar-b-pinamar` on Pinamar's prod/demo-with-real-data installs, unset
  everywhere else — the public OSS default, the from-scratch demo, and CI
  never set it). Read once at boot, alongside every other `config.get()`
  startup read.
- **Boot-time readiness check, new `data_packs.verify_required_packs()`**,
  called from the pre-listener bootstrap path before Alembic convergence or
  any server socket accepts requests. The legacy runner is available only for
  the one-time PARTIAL-shape bridge, not normal boot. For each id in
  `TURNOS_REQUIRED_PACKS`:
  1. The resolved directory (`_pack_dir(pack_id)`) must exist and be a real
     directory, not a broken symlink.
  2. `pack.json` must exist, parse as valid JSON, and match the pack id it
     claims to be (`pack.json`'s own declared id must equal `pack_id`,
     catching a wrong pack silently symlinked into the right slot).
  3. The resolved physical path must be inside an allowed root — either
     `packs_dir()` or `private_packs_dir()` — verified with
     `os.path.realpath()` before the containment check, so a symlink
     (inside the allowed root) that POINTS outside it is caught, not just a
     literal `../` in the configured path.
  4. A **content-checksum spot-check**: the pack's own inventory manifest
     (see "Operational integrity" below — the same manifest the encrypted
     backup is verified against) is re-hashed for a fixed sample of files
     (all JSON manifests + a fixed-seed random sample of binary assets, not
     every file on every boot — that's the restore drill's job, not a
     per-request cost) and compared against the recorded checksums.
  - **Any failure of 1-4 is fatal at boot**: log the specific pack id and
    which check failed, exit non-zero, never start accepting requests. This
    is the opposite of every other pack-chain failure mode in this repo on
    purpose — a required pack is a go/no-go gate, not a chain link that
    silently drops.
- **Adversarial test plan** (`test/test_required_packs_readiness.py`, new),
  one test per failure shape, each proving the process refuses to boot (or,
  for the equivalent request-time check on `/webdata/`, refuses to serve)
  rather than degrading:
  - **Missing**: `TURNOS_REQUIRED_PACKS=ar-b-pinamar` set, the directory
    absent entirely (fresh clone, never seeded) → boot fails, exit non-zero,
    log names `ar-b-pinamar` and "directory missing."
  - **Corrupt**: directory present, `pack.json` truncated / not valid JSON /
    declares a different pack id → boot fails, log names the specific
    mismatch.
  - **Symlink escape**: `private_packs_dir()/ar-b-pinamar` is a symlink
    pointing at `/etc` (or any path outside `packs_dir()`/
    `private_packs_dir()`) → boot fails; a second case where the pack
    directory is real but ONE file inside it (e.g. a doctor photo) is a
    symlink pointing outside the allowed roots → that specific resolution
    is refused, not just logged (this is the same class of bug as an
    unchecked `os.path.join` path-traversal, proven with a real symlink
    fixture, not a mock).
  - **Path-boundary escape**: `TURNOS_PRIVATE_PACKS_DIR` itself set to a
    value whose resolved pack subdirectory (`os.path.join(private_root,
    pack_id)`) escapes both allowed roots after `realpath()` resolution
    (e.g. `pack_id` containing `../../`, or a crafted `TURNOS_PRIVATE_PACKS_DIR`
    itself pointing somewhere unexpected) → refused with the same fatal
    boot behavior, not served.
  - **Partial checksum mismatch**: manifest present, one sampled file's
    hash doesn't match its recorded checksum (simulating a bad `git pull`,
    a half-written volume mount, or silent corruption) → boot fails,
    naming the specific file and expected/actual hash prefix (never the
    full hash of PII-adjacent content in a log line beyond what's needed to
    debug — file path + short hash prefix is enough).
  - **Not required, same shapes**: the SAME five fixtures above, but with
    `TURNOS_REQUIRED_PACKS` unset (or not naming this pack id) — boot
    succeeds, the pack silently drops from the chain exactly as today,
    proving the required/optional distinction is the only thing that
    changed, not the underlying resolver.

### Local dev

`git clone` `humanrounds-pinamar-data` next to the main checkout (matching
the existing convention: `<workspace>/human-rounds/humanrounds-pinamar` is
already a sibling of `<workspace>/human-rounds/turnos`), set
`TURNOS_PRIVATE_PACKS_DIR=<workspace>/human-rounds/humanrounds-pinamar-data/packs`
in `.env` (gitignored, already the pattern for every other secret). One line
in `CLAUDE.local.md` records this — never in the tracked `CLAUDE.md`.

### Deploy — the `/app/data` volume, not a rebuild-time secret

The roadmap's own framing ("env data path locally, `/app/data` volume in
deploy") is the answer, and it matches `deploy.md`'s EXISTING doctrine for
this exact volume ("`/app/data` is the persistent volume... anything the app
writes at runtime... needs to live under `/app/data` or it disappears on the
next rebuild"). Once `packs/ar-b-pinamar` is gone from the public tree, the
Coolify container is rebuilt FROM THE PUBLIC REPO — there is no in-tree copy
to ship, by construction. So:
- `TURNOS_PRIVATE_PACKS_DIR=/app/data/private-packs` on the Pinamar prod (and
  Pinamar-owned demo, if a Pinamar-specific demo ever needs real names —
  today it doesn't) Coolify app only.
- The private data is seeded onto that persistent volume ONCE (`git clone
  humanrounds-pinamar-data /app/data/private-packs`-shaped, run as a Coolify
  "post-deployment command" or a one-time manual step over SSH into the
  container/volume) — it survives every subsequent rebuild because it's on
  the persistent volume, exactly like uploaded referral photos already do.
  A pack update (a new doctor's photo) is a `git pull` inside that mounted
  clone, not a redeploy — genuinely simpler than the current "commit a photo
  to `sana-turnos`, push, wait for a full app rebuild" loop.
- Rejected alternative: bake the private repo into the image via a
  multi-stage Dockerfile + a build-time deploy key. Rejected because it
  re-couples "ship a new doctor photo" to "rebuild and redeploy the whole
  app image," the exact coupling the persistent-volume approach removes, and
  because a deploy key with read access to the private data repo would then
  need to live in Coolify's build-time secrets, a wider blast radius than a
  runtime-only credential.

### Operational integrity for the private move (Codex correction 3)

A `git clone` of `humanrounds-pinamar-data` is necessary but not sufficient
— "the data moved to a private repo" is not the same claim as "the data is
safe, recoverable, and provably absent from the public tree." Four things
this design commits to, each with its own gate in the execution slices below:

- **Access policy.** `humanrounds-pinamar-data` starts with exactly two
  collaborators: The product owner (owner) and reference-deployment counterpart (Pinamar's technical
  counterpart, `CLAUDE.local.md`) if/when he needs direct read access for an
  ops task — not the broader set of people who might have `humanrounds-pinamar`
  (ops repo) access for unrelated Coolify/deploy work. Recorded as a named
  collaborator list in the repo's own README, reviewed any time someone's
  role on the project changes, not left to GitHub's default org-visibility
  settings.
- **Checksum/inventory manifest.** This public-cut repository's
  `scripts/pack_inventory.py` is a read-only, aggregate handoff for the
  public pack roots; it never writes or inspects a private pack. Private
  byte integrity belongs to `core.data_packs.write_inventory()` and
  `core.data_packs.verify_required_packs()` in the private installation.
  That private `INVENTORY.json` is regenerated on every private content
  change and reviewed as a normal diff; it is the source of truth for the
  boot-time readiness check and the restore drill.
- **Encrypted backup, independent of the git remote.** GitHub hosting the
  private repo is not a backup — a deleted repo, a revoked org, or a GitHub
  outage during an active incident are all real failure modes for the ONLY
  copy of real patients' referring-doctor data. A periodic (weekly,
  cheap enough to automate later; manual for the first cutover) encrypted
  archive (`age`-encrypted tarball, recipient key stored in credential vault AI —
  not a passphrase in a script) of the current `packs/ar-b-pinamar/` tree
  lands in a SECOND location the product owner already controls outside GitHub (Google
  Drive, per this project's existing `gd`/`gog` access) — a location that
  does not depend on the same credential/account boundary as the GitHub org
  the private repo lives in.
- **Restore drill, run once before slice 2 (below) is allowed to proceed,
  not merely designed.** Decrypt the backup on a scratch machine/directory,
  restore the tree, re-hash every file against `INVENTORY.json`, and boot a
  local server pointed at the restored copy (`TURNOS_PRIVATE_PACKS_DIR`
  aimed at the restored tree) far enough to serve `/`, one service page,
  and one `/webdata/doctor-photos/<id>.webp` request successfully. A backup
  nobody has ever restored is a hypothesis, not a backup — this drill is
  what converts it into a proven one.
- **Publication scan, proving absence, not merely asserting it.** Before
  slice 4 (below) removes `packs/ar-b-pinamar/` from the public tree, and
  again as that removal commit's own gate: a scan across (a) the public
  tree's working directory, (b) the removal commit's own diff (`git show
  <sha>` — proving the commit deletes, not renames-and-re-adds elsewhere),
  and (c) any built Docker image's layers (`docker save` + grep, or `docker
  history` for a stray `COPY` of the pack) for every real doctor's name and
  every filename recorded in `INVENTORY.json` — zero hits required on all
  three surfaces before the removal is considered done, not just committed.
  This scan is the operational proof behind Tier 6's own framed precondition
  ("the fresh-cut public repo can only be honest the day `packs/ar-b-pinamar`
  is no longer reachable from ANY commit in the tree that gets published") —
  today that precondition is a claim; the scan is what makes it checkable.

### `UPGRADING.md` correction (part of this slice, not a follow-up)

The "What upstream owns vs. what's yours" table's "Upstream (this repo)"
column drops `packs/ar-b-pinamar` and its "Local (your installation)" column
gains a line: "any private/town-specific pack, referenced via
`TURNOS_PRIVATE_PACKS_DIR` — see `docs/architecture/data-packs.md`." Ships in
the SAME commit that removes the pack from the tree, per the drift noted
above.

## Area B — three switchable AI inference profiles

### The core technical insight

`ai_client._post()` (`ai_client.py:97-106`) builds a standard OpenAI-shape
chat-completions request (`{"model":…, "messages":…}` etc., `Authorization:
Bearer <key>`) and POSTs it to a URL. **OpenRouter, most "hosted in a named
jurisdiction" LLM API providers, AND vLLM's own built-in OpenAI-compatible
server (`vllm serve ... --api-key`) all speak this exact same wire format.**
The abstraction Tier 6b needs is therefore genuinely thin: `OPENROUTER_URL`
(currently a module constant) becomes a per-profile `base_url`, alongside the
existing per-profile `api_key`/model fields — not a rewrite of `call()`'s
request-building or response-parsing, because those don't change across
profiles.

### The three profiles

Renamed from the original design's `cloud_dpa`/`hosted_jurisdiction`/
`self_hosted_vllm` per Codex correction 4: **`cloud_dpa` asserted a Data
Processing Agreement that is not actually recorded anywhere** — nobody has
signed or attached an OpenRouter/model-provider DPA to this project, so the
name overclaimed a compliance posture the design never established. Renamed
to **`cloud_shared`** — honest about what it actually is (a shared,
multi-tenant cloud API reached over the public internet, governed by
whichever provider's standard terms of service, no DPA on file) — reserving
a DPA-qualified name for the day one is actually signed and can be cited by
document/date in this file.

1. **`cloud_shared`** (default, = today, unchanged). `base_url =
   https://openrouter.ai/api/v1/chat/completions`, `TURNOS_AI_MODEL` as
   today. Same backup-key failover. Cheapest, zero operational burden,
   governed by OpenRouter's + the model provider's standard terms of
   service — NOT a DPA, unless and until one is signed and recorded here.
2. **`hosted_jurisdiction`**. Any OpenAI-compatible endpoint the operator
   points at, with a documented data-residency/DPA claim for THAT
   deployment's own jurisdiction — this design does not hardcode a vendor
   (a specific vendor pick needs the same WebSearch-before-recommending
   discipline as any other model choice, done at implementation time against
   THEN-current offerings, not baked into an architecture doc). Same
   `base_url`/`api_key`/model shape; no failover logic needed unless the
   operator configures a second key for that same vendor.
3. **`self_hosted_vllm`**. `base_url` points at an operator-run vLLM
   instance (own hardware or a rented GPU box the operator controls) serving
   an open-weight VISION-capable model (referral reading needs image input —
   `ai_orden.py`'s PDF→PNG conversion feeds an image, not text). See
   "Self-hosted claims are acceptance conditions, not assumptions" below
   (Codex correction 5) — "no data leaves the network" is NOT an automatic
   property of pointing `base_url` at a private IP.

### Credentials — truthful per-profile, never OpenRouter reuse (Codex correction 4)

The original design proposed reusing `OPENROUTER_API_KEY`/
`OPENROUTER_API_KEY_BACKUP` as "whichever profile is active" — Codex's
audit correctly flags this as a real risk, not a naming nitpick: a
provider-named secret sitting in a field that a mutable `base_url` can
repoint at ANY host means an operator who changes `AI_INFERENCE_BASE_URL`
to a `hosted_jurisdiction` vendor is one settings-panel typo away from
sending an OpenRouter-scoped key to the wrong host, and `urllib.request`
(what `ai_client.py` uses today) **follows redirects by default** — a
compromised or misconfigured `hosted_jurisdiction` endpoint could 30x the
request somewhere else entirely and still receive the credential on the
follow-up.

- **New, provider-agnostic credential fields**: `AI_INFERENCE_API_KEY` /
  `AI_INFERENCE_API_KEY_BACKUP`, distinct from `OPENROUTER_API_KEY` /
  `OPENROUTER_API_KEY_BACKUP`. `cloud_shared` is the one profile allowed to
  fall back to the legacy `OPENROUTER_API_KEY` fields when the new ones are
  blank (backward compatibility for every existing install that has never
  heard of this tier) — `hosted_jurisdiction` and `self_hosted_vllm` MUST
  use the new fields; there is no OpenRouter key to fall back to for a
  non-OpenRouter host, so an unset `AI_INFERENCE_API_KEY` under either of
  those two profiles is treated as "no credential configured," not as "try
  the OpenRouter key against this other host."
- **Scheme validation.** `hosted_jurisdiction` and `cloud_shared` MUST use
  `https://` — a plain `http://` base URL is rejected at config-save time
  (same validation point `settings_admin.py`'s enum check for
  `AI_INFERENCE_PROFILE` already lives at, extended with a URL-scheme
  check). `self_hosted_vllm` is the ONE named local-only exception: `http://`
  is accepted, but only when the host resolves to `localhost`, `127.0.0.1`,
  or an RFC 1918 private range — an operator pointing `self_hosted_vllm` at
  a public `http://` host still gets rejected, because "self-hosted" is a
  network-boundary claim, not just a profile label.
- **Destination validation.** `cloud_shared`'s `base_url` is NOT operator-
  editable at all — it stays the hardcoded OpenRouter endpoint, exactly like
  today; only `hosted_jurisdiction` and `self_hosted_vllm` expose an
  editable `AI_INFERENCE_BASE_URL` field, because those are the only two
  profiles where a configurable destination is the entire point.
- **Redirect validation.** `ai_client._post()`'s request call switches from
  bare `urllib.request.urlopen()` to a redirect-refusing opener (a custom
  `urllib.request.HTTPRedirectHandler` subclass that raises instead of
  following, or an explicit `allow_redirects=False`-equivalent) for every
  profile — a 30x response is treated as a hard failure (surfaced to
  `ai_health` exactly like a 5xx), never silently followed. This closes the
  credential-leak-via-redirect path regardless of which profile is active.
- **`/probar` validates the SAME three things before declaring a profile
  healthy**: scheme rule satisfied, resolved destination matches the
  configured `base_url`'s host (no silent DNS/redirect substitution), and
  the actual 1-token completion succeeds — not just the completion, as
  today.

### Where the switch lives

Extends `settings_admin.INTEGRATIONS`'s existing OpenRouter card shape — a
new field, `AI_INFERENCE_PROFILE` (`text`, enum-validated
`cloud_shared|hosted_jurisdiction|self_hosted_vllm`, same validation pattern
`LANG_SWITCHER_MODE` already uses), `AI_INFERENCE_BASE_URL` (editable only
for the two non-`cloud_shared` profiles, per the destination-validation rule
above), and the new `AI_INFERENCE_API_KEY`/`AI_INFERENCE_API_KEY_BACKUP`
fields sitting next to (not replacing) the existing `OPENROUTER_API_KEY`/
`TURNOS_AI_MODEL` fields — three profiles, one active credential pair at a
time, but the credential pair is now named for what it is (an AI-inference
key) rather than for the one provider that used to be the only option.
`ai_health.probe()` (already the `/probar` handler for `openrouter`) is
extended per the redirect/destination validation above, not left unchanged
as the original design proposed.

`pii.py` and `legal.py`'s referral-image exception are untouched — they are
policy, not transport, and apply identically regardless of which profile is
active (see "What exists today," `pii.py` doctrine bullet above).

### Self-hosted claims are acceptance conditions, not assumptions (Codex correction 5)

"Self-hosted means no data leaves the network" is a claim this design must
NOT make automatically true just because `base_url` points at a private IP.
It is only true when every one of the following is independently confirmed
for a SPECIFIC deployment, and the design's job is to state these as a
checklist an operator (or the product owner, for Pinamar's own eventual self-hosted
profile) walks through and signs off on — not a property `self_hosted_vllm`
grants by construction:

- **Endpoint boundary**: the vLLM instance has no public ingress — reachable
  only from the app server's own network (a VPC/private network, a VPN, or
  literally the same box), verified with an actual port-scan/reachability
  test FROM outside that boundary returning nothing, not just "the security
  group looks right."
- **No third-party telemetry**: the vLLM server and its host OS have no
  outbound telemetry/crash-reporting/update-check that could carry request
  content or metadata off-box (vLLM itself, the GPU driver stack, and the
  base OS image all need this checked individually — an "air-gapped-looking"
  box that still phones home for driver telemetry is not actually air-gapped).
- **Logs stay in the boundary**: request/response logs (including any debug
  logging `ai_client.py` or the vLLM server itself produces) are written to
  storage inside the same network boundary, never shipped to an external
  log aggregator by default.
- **Backups stay in the boundary**: if the host or its logs are backed up
  at all, the backup destination is inside the same boundary too — an
  otherwise-perfect self-hosted setup that backs up to a third-party cloud
  bucket has just re-opened the exact gap it was built to close.
- **Host boundary is understood, not assumed**: "operator-run hardware" and
  "a rented GPU box" are NOT equivalent trust boundaries — a rented GPU
  instance is still inside a cloud provider's infrastructure, with that
  provider's own access model, however private the network looks from the
  app's side. The design's job is to name this distinction explicitly in
  whatever operator-facing documentation ships with this profile, not paper
  over it with "self-hosted" as if the two were interchangeable.

This checklist ships as documentation (a `self_hosted_vllm` setup section in
`docs/INSTALL.md` or a dedicated doc), reviewed and checked off per
deployment, before that deployment's operator or this project's own README
is allowed to state "no data leaves your network" as a property of choosing
this profile.

### Benchmark plan — scope and public-claim boundary (Codex correction 6)

**The shared client serves four distinct workloads, not one**:
`ai_orden.py`'s referral reading, `chat_agent.py`'s chat/tool-use,
`translate.py`'s translation, and `ocr_dni.py`'s DNI OCR. Codex's audit is
right that a benchmark scoped to referral reading alone cannot honestly
back a public claim like "these three profiles work for Human Rounds" —
that claim spans all four. This design picks **narrowing the public claim,
not widening the golden-set benchmark to all four workloads**, for a
concrete reason: referral reading is the one workload with a well-defined,
human-labelable structured-accuracy metric (a verdict + extracted fields
against a golden JSON) — chat quality, translation quality, and OCR
accuracy each need a DIFFERENT evaluation methodology (a rubric or a
reference-translation BLEU-style score or a field-level OCR accuracy set)
that is real, separate scoping work this design should not silently fold
into "extend the corpus a bit." Building all four honestly is future work,
not a corpus-extension line item.

- **Public claim, narrowed**: the README/docs may say "the three AI
  inference profiles are benchmarked for referral reading" — never
  "benchmarked" or "supported" unqualified, and never implying chat,
  translation, or OCR quality was measured across profiles.
- **Minimum functional gate for the other three workloads, still required**
  before ANY non-`cloud_shared` profile is marked ready to recommend: a
  smoke test per workload (one real chat turn with a tool call, one real
  translation request, one real DNI OCR request) that must SUCCEED
  end-to-end (parses, returns a well-formed response in the expected shape)
  against a newly-configured profile — this is not a quality benchmark, it
  is a "does this profile even work for the other three call sites, or does
  it silently break translation the moment someone switches away from
  OpenRouter" regression gate. `scripts/bench_ai_profiles.py` (below) runs
  this smoke gate for all four call sites even though it only SCORES
  referral reading.
- The golden-set referral benchmark described below is otherwise unchanged
  from the original design.

**The corpus cannot be real patient orders** — a referral image is the one
deliberate PII exception (travels unscrubbed to whichever profile is active,
`pii.py`'s own documented carve-out), so a benchmark corpus that's meant to
be reusable, committable, and safe to hand a self-hosted/local model runner
during development has to be hand-built, not sampled from production.
`packs/example-town/ordenes-demo/` already has the seed of this (two
photographed-order fixtures used by the demo's fake-AI path) — extend it
into a real golden set:
- 15-25 synthetic referral images/PDFs, hand-authored (not real scans),
  covering every branch of `ai_orden.py`'s verdict taxonomy:
  `parece_correcta`, `dudosa`, `ilegible`, `documento_equivocado` (a few
  distinct wrong-document shapes — a receipt, a different clinic's letterhead),
  and at least one case each for the identity-matching edge cases already
  documented as load-bearing in `ia-y-ordenes.md` (member-number-vs-DNI
  confusion, inverted first/last name, a stamp carrying two matrícula
  numbers, a práctica that validates the specialty despite a wrong AI
  specialty guess).
- A hand-labeled golden JSON per fixture (expected verdict + expected
  extracted fields), reviewed by a human once, versioned alongside the
  images.
- `scripts/bench_ai_profiles.py` (new): runs `ai_orden.analizar_orden_3pass`
  against the golden set once per configured profile (env override per run,
  no new code path — this literally exercises `TURNOS_PRIVATE_PACKS_DIR`-style
  env switching, nothing more), scores verdict accuracy + extracted-field
  accuracy against the golden labels, and records cost (from each provider's
  own response usage/pricing where available) and p50/p95 latency. Output:
  one row per profile in a comparison table — this is a REPORT the operator
  reads before choosing a profile for their own deployment, not a CI gate
  (a self-hosted vLLM profile isn't available in CI at all).
- `TURNOS_FAKE_AI=1`'s existing deterministic stub stays exactly as-is for
  the regular test suite — the benchmark script is a separate, manually-run
  tool, not a new test-suite dependency.
- **Same script also runs the four-workload smoke gate** (chat/tool-use,
  translation, DNI OCR, plus referral reading as the fourth) described
  above — a `--smoke-only` mode for when a profile is being verified for
  "does it work at all" rather than benchmarked for referral quality, so
  the pass/fail smoke gate doesn't require the full 15-25-fixture golden
  set to exist for a quick sanity check.

## Area C — WAHA demoted to dev-only, Meta as the documented path

**This is a documentation and default-recommendation change, not a code
removal.** The dual-provider mechanism (`whatsapp_chat.py`'s one webhook,
`otp_send.wa_provider()`'s env-selected outbound transport) already supports
exactly the target end-state — nothing to build. What Tier 6c actually does:

- `docs/INSTALL.md` and `docs/architecture/settings.md`'s WhatsApp
  integration card copy get a documented recommendation order: **Meta Cloud
  API for any real deployment** (no self-hosted container, no phone-pairing
  session to keep alive, backed by Meta's own uptime), **WAHA for local dev
  and a from-scratch demo checkout** (zero external approval needed to see
  WhatsApp booking work end-to-end on a laptop).
- A new runbook section — **Meta WhatsApp Business production setup** — is
  the actual missing piece. `chat-y-whatsapp.md` already documents the
  ENVS (`META_WA_TOKEN`, `META_WA_PHONE_NUMBER_ID`, etc.) but not the
  OPERATIONAL steps a new deployment has to complete before those envs do
  anything for real patients: Meta Business Manager verification, WhatsApp
  Business API production access request (moving off the ≤5-recipient TEST
  tier), and template/category considerations if the deployment ever needs
  to message a patient FIRST (today it doesn't — the patient always writes
  first, opening the 24h free-form window, per `chat-y-whatsapp.md`'s own
  note — but a reminder/notification use case would need an
  Authentication/Utility template, a real approval step worth flagging even
  though it's out of THIS tier's scope to build).
- `docker-compose.yml`/`install.sh` stay WAHA-silent (neither references it
  today) — nothing to change there; a fresh install works fully without
  WhatsApp of any kind (`TURNOS_WA_WEBHOOK_SECRET` unset = route doesn't
  exist, already the documented fail-closed default).
- **Not removed**: WAHA support in `whatsapp_chat.py`/`otp_send.py`. Pinamar
  runs no patient-live WhatsApp transport at all (Meta TEST tier only — see
  the runbook), and since the 2026-08-14 transport contract WAHA is
  loopback-dev-only in code, so nothing patient-facing depends on it — but
  ripping WAHA out would still be a behavior change to the dev workflow with
  no corresponding user-facing benefit, and ripping out code
  is explicitly not what "demoted to dev-only" asked for.

## Area D — versioned releases, ghcr, docker-compose, Helm, Coolify reference

### What's already done (per "What exists today" above)

`docker-compose.yml`, `install.sh`, `docs/INSTALL.md`, `CONTRIBUTING.md`,
and `UPGRADING.md`'s semver/CHANGELOG release-conventions policy. This
design does not re-do any of it.

### Gap 1 — `ghcr.io` publish workflow (Codex correction 7: neither trigger runs today)

`.github/workflows/release.yml` (new): on a `v*` tag push, `docker build`
the existing `Dockerfile` and push to `ghcr.io/visualpharm/sana-turnos:<tag>`
+ `:latest`. **Known constraint, not solved by this design**: GitHub
Actions is billing-frozen for this account (`tier3-harness-design.md`'s
"Verification status" — `ci.yml`/`nightly.yml`/`chromatic.yml` already
exist, are correct, and have never executed for the same reason).

**Correction from the original design, which got this wrong**: the original
text treated `workflow_dispatch` as an escape hatch — "not self-triggering
on tag push until the billing freeze lifts... `workflow_dispatch`-
triggerable" — and proposed proving the workflow by running it manually via
`workflow_dispatch`. **A GitHub Actions billing lock blocks `workflow_dispatch`
runs exactly as it blocks tag-push-triggered runs — both consume the same
blocked Actions minutes.** There is no dispatch-based escape hatch while the
freeze is active; treating `workflow_dispatch` as executable evidence (as
the original design's slice gate did) would have been proving something
that never actually ran.

- `release.yml` ships committed and correct, but **UNPROVEN and un-runnable
  by either trigger** until the billing freeze lifts — say this plainly in
  the workflow file's own header comment, not just in this design doc.
- **The actual, current publication path is a human running `docker build
  && docker push` locally** against the same `Dockerfile`, tagged to match
  the git tag — this is not a temporary stand-in for `workflow_dispatch`,
  it is the ONLY working path today, and stays the path until a real
  `workflow_dispatch` (or tag-push) run is independently confirmed to have
  actually executed (a run URL/log, not "should work").
- Nothing in this tier, this design, or any commit message may describe
  `release.yml` as "proven," "tested," or "working" based on a local
  `docker build` alone — that proves the Dockerfile and local credentials,
  not that the GitHub Actions workflow itself runs. Keep those two claims
  separate until Actions billing actually unfreezes and a run is watched
  succeed.

### Gap 2 — Helm chart

`charts/human-rounds/` (new): a thin chart, NOT a rewrite of the env-var
contract — `values.yaml` maps 1:1 onto the SAME variables `.env.example`
already documents (single source of truth stays `.env.example`; the chart
adds Kubernetes plumbing around it, never a second place that lists what a
variable does). Contents: `Deployment` (the app container, `ghcr.io` image
from Gap 1), `Service`, `PersistentVolumeClaim` (mirrors `docker-compose.yml`'s
`app_data:/app/data`), `Secret` template for `DATABASE_URL`/API keys, and an
OPTIONAL Postgres subchart dependency (default OFF — `docker-compose.yml`
ships its own Postgres for a zero-dependency single-command demo; a real k8s
deployment more often already has a managed Postgres and just needs
`DATABASE_URL` pointed at it, so the chart's default assumes "bring your
own database" and the bundled-Postgres option is the opt-in, not the
default — inverted from docker-compose's own default on purpose, because
the two install paths serve different operators).
`values.yaml`'s default `image.tag` is a real semver tag (e.g. `v1.0.0`),
with an optional `image.digest` field that, when set, wins over the tag
(`image: "...@sha256:<digest>"` rendered instead of `...:<tag>`) — see Gap
4 below for why a digest-pinned option matters for a reproducible
deployment, not just a convenient one.

### Gap 3 — public, generic Coolify reference doc

`docs/deploy/coolify-reference.md` (new, PUBLIC repo): a genericized
walkthrough — create app, point at the `Dockerfile`, set `PORT`/
`DATABASE_URL`/`.env` vars, configure the webhook, verify with
`scripts/smoke_prod.py`'s pattern — with placeholder values throughout, NOT
Pinamar's real app UUIDs/webhook secret/domain (those stay exactly where
they are, in the private `humanrounds-pinamar` ops repo). This is the
missing half of `deploy.md`'s own framing ("Coolify as a documented
reference, not the only path") — today `deploy.md` is generic-but-abstract
and the only CONCRETE Coolify walkthrough is private. A stranger cloning the
OSS repo who wants to use Coolify has nothing to follow today.

### Gap 4 — artifact integrity (Codex correction 8)

A publishable image is not just "a `docker build` that runs" — an
open-source project asking strangers to `docker pull` and run a container
carries a supply-chain obligation this design's original Gap 1/2 scope
didn't cover. Five concrete additions, all landing alongside Gap 1's
`release.yml`/manual-publish path (none of them depend on Actions billing
unfreezing — the digest/SBOM/scan/signing steps run in the SAME local
`docker build && docker push` session that's already the real publication
path today):

- **Immutable image digests.** Every published tag (`:v1.0.0`, `:latest`)
  is ALSO referenceable by its immutable `sha256` digest
  (`ghcr.io/visualpharm/sana-turnos@sha256:<digest>`) — `docker push`
  already returns this digest; it gets recorded in the release's
  `CHANGELOG.md` entry (or a `RELEASES.md` manifest) so "what exactly did
  `v1.0.0` point at" is answerable even if the `:v1.0.0` tag is ever
  force-moved (tags are mutable by design; digests are not — the Helm
  chart and Compose's pinned-image override, both below, reference the
  digest for anything meant to be reproducible, the tag only for
  human-readable convenience).
- **SBOM.** `syft ghcr.io/visualpharm/sana-turnos:<tag> -o spdx-json`
  (or the CycloneDX equivalent) generated at publish time, attached as a
  release artifact — answers "what's actually in this image" for anyone
  doing their own compliance review before installing.
- **Vulnerability scan.** `grype` (or `trivy`) against the built image
  BEFORE pushing `:latest` — a scan that finds a CRITICAL-severity,
  fixed-upstream CVE blocks the `:latest` move (the versioned tag can still
  publish with the finding noted, since pulling an old fixed version is
  always an option, but `:latest` should not knowingly point at one). Scan
  output attached as a release artifact next to the SBOM.
- **Provenance/signing decision.** Keyless `cosign` signing via GitHub
  OIDC is the eventual right answer but genuinely NEEDS Actions to run (the
  OIDC token only exists inside an Actions job) — **not available while
  billing is frozen**, so this design records the decision rather than
  building around a mechanism that can't work yet: sign with `cosign` once
  hosted Actions runs are real; until then, the SBOM + vuln-scan artifacts
  above (published alongside the image, checksummed) are the interim
  integrity evidence, explicitly weaker than a real signature and labeled
  as such in `docs/deploy/`.
- **Pull-and-run install test.** Before ANY tag is declared publishable,
  a fresh machine (or a machine with no local image cache) runs `docker
  pull ghcr.io/visualpharm/sana-turnos:<tag>` (never a local `build`) and
  `docker compose up` using that PULLED image, reaching `/setup` — proving
  the published artifact is actually installable by a stranger, not just
  that it built successfully on the machine that pushed it.

**Compose and Helm must consume the SAME versioned artifact, not drift into
two deployment contracts.** Today's `docker-compose.yml` does `build: .` —
correct for "clone the repo and try it," but once a ghcr image exists,
leaving Compose permanently source-building while Helm pulls
`ghcr.io/.../sana-turnos:<tag>` means the two install paths can silently
diverge (a bug fixed in the image but not yet reflected in a stale local
`Dockerfile` state, or vice versa). Fix: `docker-compose.yml` keeps `build:
.` as the DEV default (unchanged — a from-source checkout should build from
source), and a second, thin override —
`docker-compose.release.yml` (`docker compose -f docker-compose.yml -f
docker-compose.release.yml up`) — replaces `build: .` with `image:
ghcr.io/visualpharm/sana-turnos:${SANA_VERSION:?set SANA_VERSION}`, so a
released-artifact install (what the Helm chart and Gap 3's Coolify doc
both effectively do) and Compose's own "install the released version"
path are reading the SAME `SANA_VERSION` tag/digest, not two independently
maintained version strings. `.env.example` gains `SANA_VERSION` as a
documented, optional variable (blank = irrelevant to the `build:` dev
path).

## Out of scope for this design

- **Picking the actual `hosted_jurisdiction` vendor.** Needs a WebSearch-
  backed evaluation done at implementation time (per CLAUDE.md's own
  "Model selection — always WebSearch before recommending" rule), against
  whatever the market looks like then — not something to freeze into an
  architecture doc written today.
- **Picking the actual self-hosted vLLM model.** Same reasoning — needs a
  vision-capable open-weight model evaluated for order-reading accuracy at
  implementation time, via the benchmark script this design specifies.
- **Ripping out WAHA, or forcing Pinamar's own prod to switch providers.**
  Area C is documentation + a runbook; Pinamar's prod carries exactly one
  configured WhatsApp provider — Meta, free text on the TEST tier (chat
  replies inside the patient-opened 24h window; the OTP is refused pending
  an approved Authentication template) — so there is no provider cutover to
  force, and any production-access decision is an operational
  call for whoever runs the deployment, not this design's.
- **Actually running `docker build && docker push` for a first ghcr image,
  or cutting the first `v1.0.0` tag.** Both are Tier 7 (or later)
  execution acts, gated on this tier's slices landing and on `main` being
  the fresh-cut-ready state Tier 7 needs.
- **A second Pinamar-shaped demo showing real (rather than synthetic) data.**
  Nothing today asks for this and `demo-installation.md`'s explicit
  boundary rules would need re-deriving from scratch if it ever did.
- **Any change to `pii.py`'s scrub/restore logic itself.** Area B's profiles
  are a transport-layer switch; the privacy doctrine each one is fed through
  is unchanged and explicitly out of scope to relitigate here.
- **Migrating `packs/global`/`packs/ar`/`packs/ar-b` anywhere.** Only
  `ar-b-pinamar` (Pinamar-specific, real names/photos) moves. The three
  levels above it are engine-generic content (a national taxonomy, country
  phone/DNI rules, provincial MSD mapping) and correctly ship with the
  public engine — Area A's own repo-shape reasoning explicitly does not
  apply to them.

## Execution slices (sequenced, each independently landable, one gate each)

**Reordered per Codex correction 1.** The original slice order listed public
removal (then slice 2) before the prod volume was seeded and verified
(then slice 3) — contradicting this design's own stated safety rule in
prose. That was a real defect, not a documentation nit: the numbering
IS the enforced order (each slice's gate below explicitly requires the
PRIOR slice's gate to have passed), so the fix is structural, not an added
warning sentence. **The private repo, its backup, and Pinamar prod's volume
are now created, backed up, restored-drilled, and verified — slices 1-4 —
strictly before the removal commit (now slice 5) exists at all.**

0. **`_pack_dir()`/`private_packs_dir()` extension AND the fail-closed
   readiness contract, together** (`TURNOS_REQUIRED_PACKS`,
   `data_packs.verify_required_packs()`, the boundary/symlink/checksum
   checks — see "Fail-closed readiness contract" in Area A above). Ships
   first, alone, purely additive, proven BEFORE any real data moves. Gate:
   `TURNOS_PRIVATE_PACKS_DIR`/`TURNOS_REQUIRED_PACKS` both unset → full
   existing suite green, byte-identical behavior; private root set to a
   complete synthetic `ar-b-pinamar` stub (`pack.json` + `INVENTORY.json`) →
   `resolve_path()` proves it wins over the
   in-tree copy; the full adversarial suite (missing / corrupt / symlink-
   escape / path-boundary-escape / checksum-mismatch, each proven BOTH
   required-and-fails-closed and optional-and-degrades-silently) green.

   **Implementation boundary (2026-08-10):** readiness config is
   process/effective-`.env` only, not DB Settings, so both legacy and ASGI
   preflight run before migrations. `asgi_main._asgi_mode()` invokes the same
   core verifier synchronously before Uvicorn startup;
   `server.main()` invokes it before its own socket bind. This adds no
   production configuration and moves no private data.
1. **Create `humanrounds-pinamar-data`; set its access policy (named
   collaborators, recorded in the repo's own README); seed it with the
   CURRENT `packs/ar-b-pinamar/` tree; generate `INVENTORY.json` with
   `core.data_packs.write_inventory()` in the same seeding commit; verify
   `TURNOS_PRIVATE_PACKS_DIR` against it locally.** Gate: a local server
   boot with the private root pointed at the new repo's clone renders `/`,
   one service page, one center page byte-identical to today's in-tree
   version (same golden-page harness, pointed at the new location);
   `INVENTORY.json` covers 100% of the seeded files.
2. **Encrypted backup + restore drill (Codex correction 3), independent of
   the GitHub remote.** Produce the first `age`-encrypted archive of
   `packs/ar-b-pinamar/`, land it in a SECOND location outside the GitHub
   org (Drive), THEN run the restore drill: decrypt on a scratch
   machine/directory, re-hash every file against `INVENTORY.json`, boot a
   local server against the restored copy far enough to serve `/`, one
   service page, one `/webdata/doctor-photos/<id>.webp` request. **Gate,
   hard-blocking slice 3: the restore drill must succeed from the encrypted
   backup alone (not from the live git clone) before Pinamar prod is
   touched at all** — an unrestorable backup discovered AFTER prod already
   depends on this being the only copy is the failure mode this ordering
   exists to prevent.

   **Implementation boundary (2026-08-19):** `scripts/rehearse_private_pack_evacuation.py`
   (proven by `test/test_private_pack_evacuation_rehearsal.py`) is a bounded
   synthetic rehearsal of this slice's restore shape, not the slice itself.
   It generates a temporary fictional `ar-b-pinamar` stub, stamps
   `INVENTORY.json` via `data_packs.write_inventory()`, archives and restores
   it outside the public tree, re-hashes every file, and proves identical
   in-process rendering of `/centers`, one center page, one service page, and
   the doctor-photo bytes behind `/webdata/doctor-photos/<id>.webp`.
   The real CLI compares those representative renders before and after
   restore and deletes its scratch tree on success and failure. Archive bytes
   are independent of source mtime and ownership. Importing the proof and
   running its suite never `open`/`scandir`/`walk` beneath the checked-in
   Pinamar pack. Corruption, missing files, source symlinks, and tar
   path-escape fail closed before any render callback. The rehearsal never
   uses network, DB, Docker, secrets, or PHI, and does not produce or restore
   an `age`-encrypted archive of the real tree.
3. **Seed Pinamar prod's `/app/data/private-packs` volume; flip
   `TURNOS_PRIVATE_PACKS_DIR` there; add `ar-b-pinamar` to prod's
   `TURNOS_REQUIRED_PACKS` (slice 0's fail-closed contract now actually
   armed for real traffic); verify with `smoke_prod.py`** (extended to
   probe a handful of `/webdata/doctor-photos/<id>.webp` URLs for `200`).
   **Additionally, in the SAME maintenance window: deliberately prove the
   fail-closed path once** — briefly point `TURNOS_PRIVATE_PACKS_DIR` at an
   empty/unmounted path with `TURNOS_REQUIRED_PACKS` still set, confirm the
   process refuses to boot (not "serves degraded"), then restore the real
   mount. Gate: `scripts/smoke_prod.py` green including the new probes,
   AND the fail-closed refusal observed once, first-hand, on the real
   deployment target — not only in the adversarial unit tests from slice 0.
   **This slice's gate must pass, independently confirmed, before slice 5
   (public removal) may be committed — see slice 5's own gate.**
4. **`ai_client.py` grows a `base_url` parameter (default = today's
   `OPENROUTER_URL`); new `AI_INFERENCE_API_KEY`/`AI_INFERENCE_API_KEY_BACKUP`
   fields (never `OPENROUTER_API_KEY` reuse outside the documented
   `cloud_shared` legacy-fallback case); Settings gains `AI_INFERENCE_PROFILE`
   (`cloud_shared|hosted_jurisdiction|self_hosted_vllm`) /
   `AI_INFERENCE_BASE_URL`; scheme/destination/redirect validation; the
   `self_hosted_vllm` local-only exception (Codex correction 4).** Moved
   ahead of the removal slice deliberately — it has no dependency on the
   private-pack move and there is no reason to gate unrelated Area B work
   behind Area A's prod cutover. Gate: `test_ai_client.py` extended — a
   configured non-default `base_url` is actually used; failover logic
   UNCHANGED when profile stays `cloud_shared`; a non-`https://` scheme is
   rejected for `hosted_jurisdiction` at config-save time; the same
   `http://` scheme is ACCEPTED for `self_hosted_vllm` only against
   `localhost`/`127.0.0.1`/RFC 1918 and REJECTED against a public host; a
   mocked 30x response from any profile's endpoint is refused, not
   followed; no behavior change when the new fields are absent (existing
   tests pass unmodified).

   **Implementation boundary (2026-08-10):** the slice is transport and
   Settings-only. `cloud_shared` retains the fixed OpenRouter request URL and
   sticky primary/backup behavior. `hosted_jurisdiction` and
   `self_hosted_vllm` use only `AI_INFERENCE_*` credentials; all existing
   caller guards route through `ai_client.available()`. A call snapshots its
   validated destination and selected keys before its first attempt, then
   reuses that snapshot for failover. No PII policy, request body, model
   selection, benchmark, deploy, or data-pack behavior is changed.
5. **Remove `packs/ar-b-pinamar/` from THIS repo's tracked tree; correct
   `UPGRADING.md`'s ownership table in the same commit; run the
   publication scan (Codex correction 3) as this commit's own gate, not
   a follow-up.** **Hard dependency, enforced by this doc's own ordering,
   not by convention: this slice may only be authored after slice 2's
   restore-drill gate AND slice 3's prod-seeded-and-verified gate are BOTH
   independently confirmed done.** Gate: `git grep` for any remaining
   hardcoded `packs/ar-b-pinamar` path outside `core/data_packs.py`'s own
   resolver and this design doc/architecture docs returns nothing; the
   publication scan (working tree + this commit's own diff + a built
   Docker image's layers, checked against every name/filename in
   `INVENTORY.json`) returns zero hits; full suite green against
   `TURNOS_PRIVATE_PACKS_DIR` pointed at the new repo (CI/test fixtures
   that need SOME `ar-b-pinamar` content — if any — get a small scaffolded
   fixture, not the real data).
6. **Benchmark corpus + `scripts/bench_ai_profiles.py`, run once against
   `cloud_shared` only (the only profile with real credentials at this
   point), including the four-workload smoke gate (Codex correction 6).**
   Gate: the referral-reading golden-set script runs, produces a scored
   comparison table (trivial, one row) — proves the harness before there's
   anything else to compare it against; the `--smoke-only` four-workload
   gate (chat/tool-use, translation, DNI OCR, referral reading) passes
   against `cloud_shared`.
7. **Meta production-access runbook + WAHA/Meta recommendation copy in
   INSTALL.md/settings.md.** Gate: doc review only — no code changes, no
   automated gate; the "Configurar →" deep-link pattern
   (`docs/architecture/settings.md`'s existing mechanism) gets a line for
   whichever new copy references a Settings field, if any.
8. **`release.yml` (ghcr publish, `on: push: tags: v*` + `workflow_dispatch`
   as committed-but-unproven while Actions billing is frozen — Codex
   correction 7).** Gate: a human runs `docker build && docker push`
   locally against the Dockerfile, once, tagged to match a real version
   string — this IS the gate (not `workflow_dispatch`, which is equally
   blocked by the billing lock and proves nothing if "run"). The workflow
   file itself stays unproven and is documented as such until a real
   Actions run (either trigger) is independently observed to succeed.
9. **Artifact integrity (Codex correction 8): SBOM, vulnerability scan,
   recorded digest, `docker-compose.release.yml` pinned-image override,
   pull-and-run install test.** Gate: `syft`/`grype` (or equivalent) run
   against the slice-8 image; zero unresolved CRITICAL CVEs before that
   image is tagged `:latest`; the image's digest recorded in
   `CHANGELOG.md`; a machine with NO local build cache runs `docker pull`
   + `docker compose -f docker-compose.yml -f docker-compose.release.yml
   up` and reaches `/setup` using the pulled artifact, not a local build.
10. **`charts/human-rounds/` Helm chart, `image.digest` optional override
    wired to slice 9's recorded digest.** Gate: `helm lint` clean; `helm
    template` output reviewed for parity against `docker-compose.yml`'s env
    var set (every var either has a values.yaml mapping or is explicitly
    noted as compose-only); a real `helm install` against a local
    kind/minikube cluster reaches `/setup` (mirrors `docker-compose.yml`'s
    own quick-start gate), tested once by tag and once by digest.
11. **`docs/deploy/coolify-reference.md` (public, generic).** Gate: doc
    review; a fresh read-through by someone who has never touched Pinamar's
    real Coolify app can follow it start to finish with no private-repo
    cross-reference required.

## Risk register

| Risk | Blast radius | Mitigation | Rollback |
|---|---|---|---|
| Slice 5 (public removal) is committed before slices 2-3 (backup restore-drilled, prod volume seeded and verified) are independently confirmed done | Prod serves 404s for every doctor photo, every service page referencing a doctor, with no recent verified restore point | The doc's own slice ORDER now enforces this (slice 5's gate explicitly names slices 2 and 3 as hard prerequisites) rather than relying on a risk-register footnote read at the wrong time — this is the structural fix Codex's correction 1 asked for | Revert the removal commit; `packs/ar-b-pinamar/` was never rewritten, only moved, so the revert is a clean file restore; the restored backup (slice 2) is the independent fallback if the git history itself is somehow unusable |
| The encrypted backup (slice 2) is never actually restorable — a bad `age` recipient key, a corrupted archive, wrong assumptions about `INVENTORY.json` coverage | Discovered only during a real incident, when it's too late to fix | The restore drill is a HARD GATE on slice 2 itself, run before slice 3 touches prod, not a "should work" assumption — this is the entire point of distinguishing "backed up" from "restore-drilled" | Re-run the backup + drill from the still-live git clone; nothing about the private repo itself is lost, only the untested backup copy needs redoing |
| `TURNOS_REQUIRED_PACKS`'s fail-closed boot check has a bug that fails closed even when the pack IS actually fine (false positive) | Pinamar prod refuses to boot on a routine deploy, an outage for real patients caused by the SAFETY mechanism itself | Slice 3's deliberate maintenance-window fail-closed drill is run against a KNOWN-bad state first (proving the mechanism catches real problems) — the same test suite from slice 0 also proves the KNOWN-good state boots clean, so a false positive is a regression the adversarial suite's "not required, same shapes" tests are designed to catch before prod ever sees it | Unset `TURNOS_REQUIRED_PACKS` temporarily (degrades to today's silent-drop behavior, restoring availability) while the false-positive is diagnosed against the passing test suite |
| `TURNOS_PRIVATE_PACKS_DIR`'s directory-existence/checksum checks are evaluated on every resolution with no caching | A slow/unavailable mounted volume adds latency to every pack-chain read on the hot path | Same performance profile every other `os.path`-based check in `core/data_packs.py` already has (manifest/merge caches key by mtime); the readiness contract's checksum spot-check runs at BOOT only, not per-request, keeping the hot path unaffected — not a NEW risk class for the boundary check, just a new call site of an existing pattern; revisit only if profiling shows it matters | None needed — reverts to no-op the moment the env var is unset |
| A future contributor adds a NEW Pinamar-specific file straight into `webdata/` or a hand-built path, missing the (by-then-empty, by-then-private) `ar-b-pinamar` convention entirely | Silent reintroduction of real PII into the public tree | This is exactly what `sitedata.path()`/`data_packs.resolve_path()`'s existing invariant already guards against (CLAUDE.md's own banned pattern, `os.path.join(..., "webdata", ...)`) — the publication scan from slice 5 is the concrete, re-runnable check; worth also wiring as a `git grep`-based CI check for any NEW top-level path under `packs/ar-b-pinamar` appearing in a PUBLIC-repo diff, once Tier 7's fresh-cut repo exists and has its own CI | Delete the reintroduced file; the private repo is the sole source of truth going forward |
| A misconfigured `hosted_jurisdiction`/`self_hosted_vllm` `base_url` sends an `AI_INFERENCE_API_KEY` to an unintended host via a followed redirect | Credential leak to a third party outside the intended jurisdiction/network boundary | Redirect-refusal + scheme/destination validation (slice 4) close this at the transport layer, proven by a mocked-30x test, not left as a theoretical concern | Rotate the leaked key; the redirect-refusal fix prevents recurrence, not just this one incident |
| `hosted_jurisdiction`/`self_hosted_vllm` profiles are configured but never actually exercised by a real deployment before Tier 7 ships | The three-profile CLAIM in Tier 7's public README is aspirational, not proven | The narrowed public claim (Codex correction 6 — "benchmarked for referral reading," never unqualified "benchmarked") already scopes this honestly; slice 6's benchmark harness runs against any profile the moment credentials exist, so the first real self-hosted/regional deployment proves it for real rather than this design overclaiming a result that doesn't exist yet | None needed — the mechanism degrades to "only `cloud_shared` configured" exactly like today if nobody ever sets the other two |
| GitHub Actions billing freeze outlives this tier, so `release.yml` never actually runs by EITHER trigger and manual `docker build`/`push` becomes the PERMANENT release path by default, not a temporary one | Every tagged release depends on a human remembering to run the manual command AND the manual artifact-integrity steps (SBOM/scan/digest) — a missed release, or a released image with a skipped vuln scan, is silent | `CHANGELOG.md`'s existing "every release gets an entry before the tag" discipline is the natural trip-wire for a missed release; slice 9's pull-and-run test is the trip-wire for a broken/unscanned one — both surface at the first real install attempt, not silently forever; worth revisiting once billing status is known, not solved by this design | Re-run the manual build/push (+ SBOM/scan/digest steps) for the missed or defective tag; nothing about the tag itself needs to change |
| Helm chart's env-var contract drifts from `.env.example` over time (two files describing the same variables) | A k8s operator following `values.yaml` sets a variable that no longer does what the chart's comments claim | `.env.example` stays the single source of truth by design (chart values map onto it, never restate its semantics) — a lightweight `scripts/check_helm_env_parity.py` (grep both files, diff the variable-name sets) is a cheap guard worth adding in slice 10 itself, not deferred | Regenerate `values.yaml`'s variable list from `.env.example`; it's a mechanical sync, not a design change |
| `docker-compose.yml` (dev, `build:`) and `docker-compose.release.yml` (pinned `image:`) drift into two deployment contracts if `SANA_VERSION` isn't kept current | An operator following the "release" path runs an old pinned image without realizing a newer one exists | `CHANGELOG.md`'s release-entry discipline names the current `SANA_VERSION`; `docs/INSTALL.md`'s release-path section points at the CHANGELOG as the version source, not a hardcoded default in the compose file itself | Bump `SANA_VERSION` and re-run `docker compose ... up`; no data-path change |

## Decisions for the product owner

- **Q: Confirm the private-pack repo shape — a NEW dedicated
  `humanrounds-pinamar-data` repo (root = a `packs/` directory, fresh
  history), sibling to the existing `humanrounds-pinamar` ops repo, rather
  than folding the data into the ops repo or exporting `sana-turnos`'
  filtered history?**
  A (recommended): yes, new dedicated repo, fresh history. Keeps the
  ops-docs access grant separate from the doctor-photo/PII access grant,
  and a root-level `packs/` layout is directly extensible to a second town's
  private pack later without inventing a second glue mechanism. Fresh
  history avoids re-planting the already-remediated Google-Places-key
  incident in the destination that's supposed to be MORE locked down, not
  equally exposed.
- **Q: Default AI inference profile for new (non-Pinamar) installations —
  stays `cloud_shared` (OpenRouter, today's behavior, renamed from the
  original design's `cloud_dpa` per Codex correction 4 — no DPA is actually
  on file), or should the three-profile Settings UI ship with NO default
  selected, forcing an explicit choice on first setup?**
  A (recommended): `cloud_shared` stays the default. It's the only profile
  with zero additional operator setup (an install with no
  `AI_INFERENCE_PROFILE` set at all already works today via
  `OPENROUTER_API_KEY`) — forcing an explicit choice would break the
  "clone, `docker compose up`, works" promise `docker-compose.yml`'s own
  quick-start section makes, for a decision most new installations don't
  have the information to make on day one anyway. An institution that
  specifically needs `hosted_jurisdiction`/`self_hosted_vllm` for a
  compliance reason will already know that going in and can set it
  explicitly.
- **Q: Does Pinamar's OWN prod deployment need to seed
  `/app/data/private-packs`, be backed up, restore-drilled, and cut over
  BEFORE slice 5 (public removal, renumbered per correction 1) can land at
  all, or is there a window where the product owner is fine with Pinamar prod briefly
  running the soon-to-be-deleted in-tree copy while the private repo is
  being stood up?**
  A (recommended): no window — slices 2-3 (backup restore-drilled, prod
  volume seeded + verified + fail-closed-proven) are hard gates before
  slice 5 (public removal) deploys, exactly as reordered above. The
  alternative (removal first, seed-and-verify after) has a strictly worse
  failure mode (broken doctor photos in front of real patients, with no
  proven restore point) for zero benefit — there's no reason to invert the
  order.
- **Q: Where does the encrypted backup (slice 2) actually live — Google
  Drive (already `gd`/`gog`-reachable from this project), or a different
  second location the product owner prefers?**
  A (recommended): Google Drive, in a folder scoped to the product owner alone (not a
  shared team Drive) — it's already an existing, working access path for
  this project, genuinely independent of the GitHub org the private data
  repo lives in, and needs no new credential/service to stand up. Revisit
  only if the product owner wants a THIRD independent copy for extra redundancy, which
  this design treats as beyond what the restore-drill gate requires.
- **Q: Is Meta Business production-verification for Pinamar's own WhatsApp
  number something the product owner wants to pursue as part of closing out Area C, or
  does Pinamar stay on Meta's TEST tier for now (its prod runs configured
  Meta free text there only — no OTP transport exists until an approved
  Authentication template ships), with the runbook
  written for FUTURE installations only?**
  A (recommended): write the runbook regardless (it's needed for any future
  installation either way), but treat Pinamar's own production-access
  request as the product owner's call, not something this design schedules — it's an
  external approval process with its own timeline, not an engineering slice.
- **Q: Artifact signing (Codex correction 8) — ship the first releases with
  only SBOM + vuln-scan as integrity evidence (no cryptographic signature,
  since keyless `cosign` needs a real GitHub Actions OIDC run this account
  can't do while billing is frozen), or hold publication until Actions
  billing unfreezes so real signing is possible from the first release?**
  A (recommended): ship with SBOM + vuln-scan now, label the gap explicitly
  in `docs/deploy/`, and sign retroactively (or forward-only, going by tag)
  once Actions billing unfreezes. Holding ALL publication hostage to a
  billing decision outside this project's control blocks real value
  (Tier 7's fresh-cut repo, actual installs) for a gap that's honestly
  labeled either way — the labeled gap is a smaller cost than the delay.
