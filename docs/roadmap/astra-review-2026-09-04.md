# Refactor review and execution order — 2026-09-04

Reviewed baseline: `f25d0365`. This review does not change the maintainer's acceptance
criteria. The fixed row set remains in [the acceptance checklist](audit-acceptance-checklist.md).

## Current checkpoint — 2026-09-06

### Catalog and day-queue native retirement — 2026-09-14

The archived native catalog-editor and day-queue WIP, plus the catalog field
recovery and completed generated locales, are integrated with main `1e09d6ed`
(Tema Human Rounds) as `ae34ab7d` on `codex/refactor-catalog-resume-20260914`.
The classic root `admin-content.js` and `admin-queue.js` controllers are
physically retired: the served `/admin/services`, `/admin/centers`,
`/admin/doctors` catalog screens and the day queue run owned native
controllers (`platform/admin-content.mjs`, `platform/admin-queue.mjs`) with
mount/dispose lifecycle, visible retry and SSE ownership (journeys in
[catalog-served-journeys.md](catalog-served-journeys.md); solution records
indexed in docs/solutions/INDEX.md). The tracked root inventory is now 3
classic `.js` (`admin-app`, `admin-shell`, `nav`), 0 `.mjs`, 4 Python
entrypoints and 0 `.html` — progress on rows 1.1/4.1, not closure.

Merge render proof: exactly five reviewed platform fingerprints moved in the
shared manifest (admin-create-appointment, admin-users, chat-widget, islands,
listing); reversing only those five URLs restores all nine ES/EN/anonymous
account renders to `1e09d6ed` byte-for-byte, and the availability and datos
renders are unchanged (proof artifact:
refactor-resume-20260914/merge-theme-20260914-render-proof.json). Local
ladder at `ae34ab7d`: backend 5,534 tests OK (99 declared external-pack
skips), frontend suite green, routes tier 13 files OK, cassette audit 4/0/0,
browser gate 561 tests with 553 passes, zero failures and eight declared
legacy-mode skips. Released as exact `4dcb9f12` (`ae34ab7d` plus this
documentation only): production and demo both redeployed from the push and
pass their repository-owned smoke suites against that SHA, byte-matching the
local checkout's fingerprinted assets.

### Release update — 2026-09-07

Production and demo now serve `a5138575677d85abb42f89493607f51c45cf093e`.
The photo migration and explicit WebP MIME fix passed the frozen ladder:
5,376 backend tests (99 declared skips), 1,417 frontend tests, 13 route files,
31 cassette tests, four clean audits and 236 browser tests (eight declared skips).
Both live smoke checks and 12 photo journeys pass, including decoded native
image navigation with the module deliberately blocked on each site.

A subsequent page accessibility pass found unnamed center photo links. That
bounded correction is isolated and not yet deployed: hero and gallery use the
center name as alt text, with renderer and real accessible-role regressions.
Broader listing/landmark findings remain in the existing operational queue.
This does not close an original audit row: **20 MET / 38 (52.6%)** remains.

### Earlier photo candidate checkpoint

Last verified production and demo serve `6af2eda6f7ac8f6def756a79f7eafb9b99cfa89a`.
The home navigation reservation was verified live at five widths on both sites.
The isolated next candidate physically retires `photo-zoom.js` in favor of the
native platform module and keeps usable image fallbacks when it cannot load.
Real center acceptance uncovered a three-pixel phone listing overflow; the
shared action pair now wraps without shrinking text or removing either link.
Its browser gate passes **236 tests, zero failures, eight declared skips**.
The full frozen backend/integration/release gate is still pending; this candidate
is NOT deployed. Its remaining root classic inventory is21, not zero.

The audit remains **20 MET /38 (52.6% by equally counted rows)**. Availability's
separate inline photo viewer, other classic controllers, compact result actions
and the older unexplained immediate-input-loss observation remain open.

### Earlier editorial checkpoint

Both running installations serve `87fbda267b63689719f94582c2b05608327c473a`,
verified through actual container revisions, production/demo smoke checks,
34 exact asset checks, four home search journeys and six editorial recovery
journeys on the live demo. The frozen gate passed 5,374 backend tests (99
declared external-pack skips), 1,404 frontend tests (no skips), 13 route files,
31 cassette tests, four cassette audits with zero findings and 229 browser
tests (eight declared mode skips). Failed earlier evidence remains retained.

Waitlist offers, DNI scanning, age/preparation utilities and the three editorial
controllers are now integrated and their obsolete assets retired. Editorial
remounts preserve unsaved drafts and discard stale translation callbacks;
negative controls demonstrate both protections. Current tracked root inventory:
**22 classic `.js`, two `.mjs`, four Python entrypoints, zero HTML artifacts**.

Home cold-load navigation stability is the next isolated verification slice.
It must pass real held-request geometry and observer checks against an unchanged
old-source negative control before integration. Compact result actions and an
older unexplained immediate-input-loss observation remain separately open.

The original audit is still **20 MET / 38 (52.6% by equally counted rows)**.
These releases do not themselves close the remaining root topology or Storybook
inventory/enforcement acceptance rows. No overall 100% claim is justified.

### Earlier September 6 checkpoint

Both running installations now serve `fb87f270581fc56e64667904410f98d421a8cad4`,
verified through their actual containers' `SOURCE_COMMIT`, production/demo smoke
checks and byte-matched immutable assets. The frozen candidate passed 5,370
backend tests (99 declared external-pack skips), 1,394 frontend tests (no skips),
13 route files, 31 cassette tests, a four-cassette audit with no findings and
208 browser tests (eight declared mode skips). Main was fast-forwarded to that
exact tested revision; unrelated local work was preserved.

The five shared primitives, seven admin primitives, public directory/tiles,
upload controller and appointment-confirmation controller are integrated.
The confirmation page now preserves early affirmative/decline intent through
delayed loading and has executable recovery/lifecycle tests. The old classic
confirmation asset is absent locally and returns 404 on both live sites.

The current tracked root inventory is **26 classic `.js`, six `.mjs`, four
Python entrypoints and zero HTML artifacts**. This is a smaller remaining
inventory, not closure of rows 1.1 or 4.1. The next bounded implementation is
the waitlist-offer controller: one server-rendered owner, canonical module
placement, physical classic retirement, and real bootstrap/controller tests
for early accept/reject, duplicate actions and loading/network recovery.
That new slice is isolated from the deployed revision and has no release proof
yet. Continue shared/root dependency groups after its integration gate.

The original audit remains **20 MET / 38 (52.6% by equally counted rows)**.
Storybook coverage for the shared combo now has rendered mobile, long-label and
keyboard checks, but a component-without-story blocking build and complete
inventory are still missing: row 4.4 stays OPEN. Neither the release nor the
worktree cleanup promotes an audit row by itself.

## Historical checkpoint — 2026-09-05

The deployed production and demo revision is `52d547e6`, verified by both running
containers' `SOURCE_COMMIT` and live smoke checks. Live booking measurement
evidence is being recaptured after a saved-report discrepancy. Its immutable gate passed 5,354 backend
tests (99 declared private-pack skips), 1,365 frontend tests, 13 route files,
31 cassette tests and 198 browser tests (eight declared mode skips).
The earlier `45bb9fe0` release supplied the independent Account/SSO label,
contrast and actual input hit-test proof; those contracts remain enforced.
Newer local work is not covered by either release. The next primitive-placement
full gate caught an old `format` path expectation in the Users screen test;
the exact `platform/format` correction passes all six focused screen tests,
but the candidate still requires a fresh full green gate before release.

Row 3.5's earlier note was stale: manual and automatic retry proofs are integrated
and all 20 load-bearing tests passed without skips in deployed `52d547e6`.
See [the queue evidence](audit-row35-queue-evidence.md). The fixed audit is now
20 MET / 38 (52.6% by equally counted criteria), with 18 OPEN, not 100%.

Coordinator `d7b254b2` additionally integrates canonical native Interviews and
Users controllers. Both classic implementations are physically removed. Their
independent acceptance covers stale intake mounts, real doctor-account consumer
recovery and an email-save button that completes while an optional Users import
is still held. A deliberately awaited-import regression fails that browser test.
Every changed Account raw-response pin has an eight-page ES/EN proof that reverses
only the explained manifest delta; source scans do not substitute for behavior.

Booking first-paint correction is a bounded regression, not an extra audit row.
Real small, large, exhausted and rejected-load schedules are exercised at five
widths. Independent GLM review found a schedule reservation surviving into later
steps; the actual browser red control confirmed it, and the corrected 184-case
browser gate passed. Screenshot review then found mobile chat/help-text overlap;
its text-range intersection red control reproduced the overlap. Corrected source
`524e5f05` passes all 184 browser cases, 1,327 frontend tests and 20 Account
contracts; phone/desktop screenshots were inspected. Combined release is pending.

After the five shared-primitives placement on deployed base `52d547e6`,
the tracked root inventory is 30 classic JavaScript files, 15 ESM files,
four Python entrypoints and zero HTML artifacts. The five canonical implementations
and every consumer now use `static/platform`; obsolete asset URLs are closed.
The last producer in the earlier `Sana*` inventory, `SanaChat`, is physically retired;
the native widget and shared bootstrap own queued intent and degraded loading.
Other names, including `WzUpload` and remaining route globals, still need migration.
Configuration such as `SANA_STATIC`
is not an implementation substitute. Rows 1.1 and 4.1 remain OPEN.

Next bounded dependency slices, after the combined release gate:

1. Verify and release the five shared primitives placement (format, dates,
   hours, calendar-grid and islands). Root verification passed 1,365 frontend
   tests, 198 browser tests with eight declared skips, and a literal eight-page
   Account reverse-delta proof. The full combined release gate is still required.
2. Move the seven admin primitives (session, preferences, table, combo,
   filter-controls, control-fit and patient-summary) with their consumer gates.
3. Recount and move canonical code into owning packages before declaring the
   package ESM-only. Zero known globals alone does not satisfy the root-layout
   criterion or prove every compatibility branch is gone.

GLM's read-only preparation supplied the producer/consumer map. Its proposed
deletion checks are retained as supplementary guards, not counted as behavioral
tests. No new acceptance rows or percentage weighting have been invented: the
original checklist now has 20 MET / 38 equally counted rows, not 100%.

## Assessment of the earlier review

The original Fable review of September 2–3 correctly identified repeated
re-audits, incompatible completion claims, and insufficient verification of
integrated changes. A fixed checklist and a test result tied to a commit are
useful corrections. The backend transport migration is substantial real work;
it should not be repeated.

I disagree with the earlier conclusion that the audit was complete after
recording ESM, hosted CI, visual regression and versioned releases as
deviations. A scope decision is not evidence that the requested behavior
exists. The current checklist correctly restores those items to OPEN.
Likewise, a failing gate cannot be made acceptable just by calling a failure
environmental; fix the prerequisite or keep the result unverified.

Recent Codex commits do physically replace implementations and update their
consumers. However, passing the route census and string-based source tests
does not establish correct browser initialization. The preference cutover
`3a8e4983` starts `preferencesReady` in `admin-table.js` but never consumes it:
the queue creates its width controller during script evaluation and retains
the empty preference snapshot. Automatically discovered tables and column
selectors share the same ordering risk. GLM correctly noted that the snapshot
bug also exists before that cutover; comparison with its parent confirms this.
It is a pre-existing defect missed by the cutover, not proof the ESM change
introduced it. Fix it before moving another dependency.

## Findings and corrections

1. **Preference hydration:** exercise delayed module and server responses,
   saved widths and column selections, and user changes while loading. Repair
   the shared table engine so all consumers benefit. Test the actual module's
   behavior; a mock that cannot reproduce cache replacement is insufficient.
2. **Retirement enforcement:** `f25d0365` added a Python test for five retired
   files, but pre-commit did not run that test. The replacement checker covers
   all 17 completed client-module retirements, uses `lstat` to reject dangling
   symlinks, and runs directly in pre-commit and the frontend suite.
3. **Honest inventory:** the previous nine-global number counted `window`
   assignments but omitted eight exports through `root`. There are 17 known
   `Sana*` global producers at this baseline, including i18n and intake. This
   is an inventory of those exports, not a proof that every browser global
   has been found. Git-tracked root inventory is 48 classic JS and 17 ESM.
4. **Historical versus current proof:** the September 3 full-ladder and live
   sign-off belongs to its recorded commit. Later focused green checks do not
   transfer that release proof to the newest main. Report candidate testing
   and deployed-version verification separately.
5. **Agent progress:** `claude-glm-once` captures child stdout until exit.
   Terminal silence alone is not a stall. Inspect session progress and tool
   outcomes; permit one bounded attempt per objective/base, then use Cursor,
   then Codex/Sol. No Claude inference is authorized for this work.
6. **False green routes:** running pre-commit in a clean worktree without
   database variables reported success while route tests skipped. The route
   runner now rejects missing configuration and validates each file's unittest
   summary with the existing coverage guard. Pre-commit resolves the fixed
   managed routes profile when database variables are absent. No database
   prerequisite is replaced by a skip.

## Execution order

1. Close the reproduced preference regression and enforce physical retirement.
   Acceptance: failure on the baseline, green behavioral/browser tests after
   the fix, no lost interaction during load, and a restored retired file
   causes the actual pre-commit command to fail.
2. Finish rows 4.1 and 1.1 by dependency groups: shared table/session/i18n
   dependencies, their admin consumers, then patient search/listing/account
   and chat consumers. Each group moves implementation and all callers once;
   include destination package placement in the same group where practical.
   Do not count renames or a number of commits as progress.
3. Close local proof gaps that unblock multiple rows: complete component-story
   enforcement (4.4), then one fresh
   contributor exercise (2.4/7.1). Keep HSI disabled on the reference deployment.
4. Verify one frozen candidate with the complete managed ladder and the
   representative patient/staff journeys, including failures and cold loads.
   Record the tested SHA, then verify that SHA at the release target. A new
   shared dependency change invalidates the affected candidate evidence.
5. Complete hosted PR enforcement, visual baselines, release-image delivery,
   public-cut protection, and institutional documents against their existing
   rows. A billing approval or institutional sign-off remains separate from
   coding throughput; do not replace it with a local test or placeholder.

At the September 4 baseline the checklist recorded 19 MET and 19 OPEN rows: **50% of
equally counted acceptance rows**, not 50% of development effort or a finish
date. No row is upgraded by this review alone. Full audit completion requires
all 38 rows to pass, or an explicitly approved change to an acceptance line.

## Integrated-candidate verification — 2026-09-04

Do not treat the first preference fix as release-ready. At `a05ccc12`, the
complete managed ladder ran 5,285 backend tests: eight failures and 99 declared
external-pack skips. Frontend, routes, 31 cassette tests and cassette audit
passed. The browser run hung in the requests screen: the new hydration callback
reconstructed its column picker on every notification, including after the
preferences promise had already resolved. That is a real regression in this
candidate, not an environment failure.

`fc812477` fixes the loop. New bounded unit and real-browser cases recreate the
picker from its callback; both fail against the preceding candidate and pass
afterwards. Early edits and width reset/merge cases still pass. A fixed managed
`e2e-asgi` profile allows the entire browser surface to run independently while
preserving DB-only injection; it does not replace the full release ladder.

The next combined candidate, `591a8119`, includes main's doctor-account and
sidebar follow-ups through `2113e8a1`. Its browser run completed: 101 passed,
four reported failures (three leaf failures plus their parent), eight obsolete
legacy-mode skips. The previously frozen request list, rejection persistence,
mobile layout, patient booking, passkeys, preparation upload and live queue
updates passed. The failures are two interview expectations predating the
approved compact-screen redesign and one Settings history assertion racing
asynchronous URL canonicalization. Correct the assertions against the approved
behavior and rerun; do not restore duplicate titles or accept a weakened URL.

Release remains held. Remaining backend findings include account HTML snapshots
that predate physical module cutovers, actual private material in the public
tree, and a worker-shutdown timeout. The worker failure needs deterministic
phase evidence before changing runtime cancellation; a blind stress rerun or
larger timeout is not a fix. Resolve this list before starting another ESM
group or claiming a current complete-ladder pass.

After those browser assertions were corrected, the standalone managed browser
gate completed with **105 passed, zero failed and eight legacy-mode skips**
(113 reported tests including the HSI parent). No product fallback or assertion
was removed: the Settings case still requires the exact canonical general URL,
and interviews now checks the approved shared title and real doctor controls.
Phone 390px and desktop 1280px connector screenshots were also inspected: the
disabled checkbox and its sole HSI label are visible without clipping.
Admin-users contracts (6), public-cut preflight (127), password hashing (8) and
managed-executor tests (10) pass independently. This is not a fresh backend-full
pass and does not resolve its remaining account, public-tree or worker findings.

## Account and public-tree follow-up

`2b51711d` resolves the account snapshot failures after reviewing the changes
since `216dca10`: the client-loaded asset map gained canonical modules, directory
moved to ESM, the account template retired its classic preparation loader, and
English bootstrap keys changed. The account router itself did not change.
All 19 account contracts pass. A new real-browser journey follows a submitted
request into the patient account, proves the canonical preparation module loads
without the old script/global, and checks the request again after reload.
The full browser surface now reports 106 passed, zero failed, eight legacy skips.

`eb8813c1` sanitizes the current public tree. Private meeting source is preserved
outside the repository; R1–R10 remain in a public engineering requirements file.
Public fixtures use fictional identities and no longer describe installation
login failures. The existing optional vault backup now sends both synthetic
account credentials through stdin rather than putting a password in process
arguments. Public-tree hygiene, 127 preflight tests, two backup-helper tests and
the doctor-account unit/browser checks pass. This does not rewrite Git history;
a current-tree audit is not a claim that historical private material vanished.

One frontend pre-commit attempt reported a failure but discarded its full log,
leaving only the final passing cases. A fresh captured run passed all 1,224 tests;
the original failed case remains unidentified, not dismissed as a flake. The
hook now preserves its complete failed report, with a behavioral test proving
that an early failure survives a long passing tail. The commit's full frontend
and managed routes gates passed. Keep this evidence gap explicit in release review.

Known remaining full-ladder blocker: worker shutdown. GLM's bounded audit hit
command guards and a turn limit, not quota exhaustion; Cursor's fallback
confirmed unbounded startup/shutdown waits but did not establish the historical
failing phase. Its proposed half-second success check would not prove cleanup
and was rejected. Add deterministic phase/await-chain diagnostics in the test
child before the next lifecycle run; do not change the production budget or
cancel unregister work merely to make a test return. Concurrent main/UI changes
still require integration and another frozen-candidate verification.

## Accelerated execution contract

The product owner requested a redefined finite goal and parallel execution on September 4.
Engineering completion means the agreed implementation-level module/package
cutovers, complete verification on one frozen candidate, safe integration, and
the exact served revision passing production/demo journeys. It is distinct from
all 38 original audit rows; institutional or publication criteria cannot be
silently removed to produce a 100% headline.

The bounded milestones are: (1) close reproduced regressions with real evidence;
(2) migrate shared dependencies and every consumer, physically retiring the old
implementation and placing the new one with its owner; (3) prove durable offline
retry, enforce meaningful component stories, and run the fresh-contributor
exercise; (4) freeze and verify the integrated candidate; (5) verify its exact
deployed revision. Hosted/publication and institutional sign-offs remain a
separate explicit acceptance track. No code change reopens the original row set.

Three GLM lanes and a Terra diagnostic writer were started independently. GLM
finish-contract and story reviews completed successfully as glm-5.3. Accepted
findings: obsolete Coolify topology/private-pack guidance, missing blocking
story enforcement, and story paths that reproduce markup or exercise only a
fallback. The Coolify recipe and Dockerfile comments now describe the actual
ASGI-only entrypoint and durable private-pack path. Seven doc contracts and six
actual entrypoint-mode tests pass; a clean independent Coolify install remains
unproven. The image already defaults to ASGI, so the old prose did not prove
that every container install failed: explicitly clearing the flag is what fails.

Rejected or qualified GLM claims: the suggested network-request plus CSS-class
story check does not prove the real renderer ran (an unused import and copied
markup could satisfy it). Existing Python tests do exercise the advisory story
hook, contrary to the broad claim that no tests reference stories. Historical
workflow comments establish a past billing restriction, not current quota.
Live GitHub lookup still found the latest hosted runs were failed August 6 runs,
so hosted acceptance remains unverified without claiming the current cause.
The proposed local/external row counts overlap and are not a new completeness
percentage. No checklist row was promoted by either review.

Worker diagnostics now have a managed real-Postgres proof: hold the synthetic
worker-table lock, observe the installed prune coroutine waiting, dump code
locations without runtime data, release it, then require graceful unregister
and no new worker row. Diagnostic signals require an explicit ready handshake;
the normal child does not receive them. Collection happens inside the original
shutdown deadline and the complete bounded snapshot survives a long log tail.
The instrumented worker suite then ran 162 tests: 161 passed and one failed,
with zero skips. The rapid-spawn/SIGTERM case exceeded its unchanged 45-second
deadline. Its snapshot located shutdown inside native side-task cancellation,
waiting for the periodic deferrer asleep until its next tick. Prune and register
had completed; unregister had not been reached. This falsifies the startup-wait
hypothesis for this occurrence. A targeted cancellation-preservation regression
and fix are now being developed; the exact cancellation-consumption window is
not yet proven. Do not repeat the stress run until that bounded proof passes.

The HSI retry GLM writer exhausted 60 turns without edits, after command-guard
denials. That is an execution failure, not evidence of quota exhaustion. Its
objective/base must not be retried. Cursor/Grok owns the isolated
`codex/hsi-retry-acceptance` worktree as fallback; its output still requires
independent behavioral verification.

The targeted correction is now independently verified: an actual installed
psycopg-pool reset catch consumes cancellation in the controlled regression;
the stock deferrer sleeps again, while the guarded deferrer preserves the
cancellation and lets native unregister finish. The two focused tests pass.
After that meaningful runtime change, the full worker suite passes all 162
tests in 250.446 seconds with zero skips, including the rapid-start/SIGTERM
case. The controlled real-Postgres diagnostic proof also passes. This closes
the reproduced shutdown regression, not the entire refactor or deployment.

HSI manual durable replay proof 8cb61519 is committed/pushed on its isolated
branch after independent managed Postgres verification (one test, zero skips)
and green frontend/routes commit gates. Root replaced a fixed near-future
appointment date with a relative future date. No live HSI setting changed.

## Full ladder at 18fece3c

The complete run finished with frontend, routes, 31 cassette replays, cassette
audit and browser gates green. Backend unittest reported 5,296 tests and no
assertion failures, with 99 skips; the enforcing coverage guard recognized only
97 exact external-pack skip lines and rejected the remaining two. Overall
result is RED, not a successful backend or full-ladder acceptance. Earlier
references to all 99 as declared pack skips must not be used as current proof.

The runner deleted its complete temporary backend log after printing the final
60 lines, so the two skip identities were lost. Log retention is now being
corrected for all six surfaces, with a behavioral shell-runner test proving
that unexpected skips still reject the run and the complete report survives.
The guard's allowlist is unchanged; it additionally emits bounded unmatched
skip evidence on failure. A single read-only GLM diagnosis is investigating the
skip paths before the next combined gate. Current main has advanced independently;
preserve its unrelated E2E WIP and integrate only released commits.

GLM completed its bounded read-only diagnosis. Its enumeration accounts for
99 expected private-pack skip records and identifies a plausible stdout/stderr
interleaving defect. That does not recover the two original test IDs, so its
categorical claim that the old run had no unexpected skip is not accepted as
historical proof. A real child unittest now reproduces the mechanism: stderr
noise splits the verbose result line and the old parser rejects an allowed
skip. Actual TestResult.skipped retains the exact identity and reason.

The local and hosted backend commands now use a thin stdlib discovery runner
that writes an atomic private JSON result from the real TestResult, and the
coverage guard validates those records instead of parsing merged log lines.
The exact external-pack reason remains the only allowed skip. Unknown reasons,
missing/malformed reports, test errors/failures and unexpected successes still
fail. Hosted triggers are unchanged; this is not a hosted-CI acceptance claim.
A fresh full retained report is required before declaring the backend green.

The commit gate also captured a frontend intermittent failure with its full
log: service-age-module.test.mjs checked gateLookups after eight setImmediate
turns, before cold dynamic imports necessarily finished. The test now awaits
real dependency completion and an explicit gate lookup notification, bounded
by the test timeout; no service runtime behavior changed. Focused cases and
the full frontend suite pass. This identifies the newly captured occurrence,
not the identity of the earlier failure whose original log was lost.

## Verified candidate and recovery integration — 2026-09-04

Frozen `5beef3db` completed the full managed ASGI ladder with exit zero.
The retained actual TestResult records 5,304 tests, no errors or failures, and
99 skips all carrying the exact external private-pack reason. Frontend passed
1,226 tests; routes, 31 cassette replays, cassette audit and browser gate passed
(106 browser passes, eight declared legacy-mode skips). This is local evidence,
not a hosted-CI result or a promotion of any of the 38 audit rows.

The separate UI recovery owner released integration ownership after reporting
both production and demo serving `627a265e`, green post-rollout smokes, and
inspected live phone/desktop captures. Its four recovery worktrees were retired;
the primary and Astra candidate remain. The recovery is now being integrated
into the isolated candidate, not directly into production.

The only textual conflict is in the Interviews browser tests. The latest
approved requirements explicitly remove the general-default UI. Replace the
obsolete positive assertions for `ent-sistema` and `ent-default-title` with
absence checks, while retaining the shared title, no duplicate inner title,
actual doctor rows, visible effective-state checkboxes and the recovery's
section heading checks. No catalog, Wiki or other recovery test is removed.
The automatically combined table engine retains preference hydration and the
new container ResizeObserver; the combined browser gate must verify both.
Full integrated acceptance remains pending until run against a frozen SHA.

The first combined browser run had one failure: an older Wiki assertion waited
for the inner heading deliberately removed by the compact-navigation redesign.
It now asserts the shared `#adm-h1` title, absence of the duplicate inner title,
and retains the actual item-link check. After that concrete correction, the
combined browser suite passes 118 tests with eight declared legacy-mode skips
and zero failures. Combined frontend passes 1,251 tests. Full backend-inclusive
verification is still required before this integration can advance main.

The full `8dd9434c` ladder then returned RED: 5,326 backend tests, seven
failures, zero errors, 99 exact external-pack skips; every other surface was
green. The failures were reviewed individually, not waived. Four snapshot
failures come from the new `admin-filter-controls.mjs` manifest entry. Root
independently rendered both archived revisions in Spanish and English and
proved removing that single new entry makes every other account/reset byte
identical; five corresponding snapshot literals were deliberately refreshed.
The overbooking store contract now documents its exact new count function and
exception and checks argument forwarding/identity; its stray direct-database
reference was only prose and now correctly names the store. The synthetic PDF
token fixture is shorter; the public-cut detector is unchanged. These are
targeted fixes requiring focused verification and a new full frozen gate.

## Resumed closure checkpoint — 2026-09-05

The new frozen `12f47f10` run completed 5,327 backend tests with one error
and the same 99 explicitly allowed private-pack skips. Approval returned 200
in the explicit-HSI path but its expected durable outbox entry was absent.
All other full-ladder surfaces passed. That remaining error is not waived;
the candidate is not release-ready. A bounded GLM diagnosis and an independent
Codex investigation of background-thread/configuration races own this blocker.

The separate session-module candidate's full browser run completed 107 passes,
13 failures and eight declared legacy skips. Its log proves that a fixed-name
fixture database disappeared while suites overlapped. Backend run-scoping did
not cover this browser harness. Repair the browser harness with unique physical
database identities, propagate them through every fixture operation, and prove
two same-label fixtures survive each other's teardown before rerunning the
session gate. Do not treat the clustered failures as either proven application
regressions or acceptable infrastructure exceptions.

Execution is resumed through the active scheduled continuation, despite the
legacy goal record remaining blocked in the app. Immediate sequencing is:

1. Diagnose and correct the single backend error with a reproducing test.
2. Verify browser-database isolation and the actual session caller boundaries.
3. Incorporate the separately verified UI release `0a8123b7`, preserving both
   its behavior and the refactor's hydration/session fixes.
4. Run the frozen integrated gates, then continue the existing dependency,
   story and contributor milestones above. Release proof remains exact-SHA.

No acceptance row was closed by restarting execution. At that restart the original
38-row audit had 19 MET / 19 OPEN; hosted, publication and institutional obligations
remain visible instead of being silently relabeled as finished engineering.

The settings-cache race is now fixed with generation-checked publication.
Independent deterministic save-only and reset-only tests fail on the old
reader and pass on the correction; both the delayed caller and later connector
resolution retain the new value. Existing configuration20 and managed connector4
tests pass. The module suite requires a synthetic signing key when run alone;
with that explicit ephemeral prerequisite its43 tests pass. No production
credential was read or copied. Full-suite causal closure still needs a fresh run.

Session migration `bbb1a5f7` was independently verified with1260 frontend tests,
79 focused backend tests, the real database-isolation proof and121 browser
passes with eight declared legacy skips. The new UI integration separately
passed122 browser tests before the session branch was merged. The combined
browser suite passed125 with eight declared skips. A subsequent independent
review found a changed exception sentinel causing existing controller catches
to paint a generic error during confirmed session redirects. Restoring the
existing `401` contract has a real controller/catch red-green regression;
ordinary403 remains visible. The final frozen ladder must include this last
correction. A source-only assertion was updated to match behavior, not used as
the behavioral proof. None of these local runs is a deployment claim.

Full frozen `748ca837` verification returned five backend failures across
5,329 tests, zero errors and 99 exact allowed private-pack skips. The former
explicit-HSI failure did not recur. Frontend, routes, cassette and browser
surfaces all passed. Four failures were account snapshots: independent raw
es/en account/reset comparison proves precisely the new session-module
manifest entry and updated patient-summary-module fingerprint are the only
differences. Five corresponding expected hashes were deliberately refreshed.
For later client-manifest changes, run this focused account gate before the
full ladder; do not spend a full run rediscovering known snapshot coupling.

The fifth failure happened before the intended cache race: unrelated suite
activity could populate its cold cache before the designated reader reached
the barrier. A warmed-cache setup reproduces that failure. Each unchanged
controlled scenario now runs in a fresh, secret-free process. Two old-reader
failures remain demonstrable; both corrected scenarios pass even with a
concurrent parent-process cache polluter. Independent review confirmed exactly
two discovered tests, no recursion, no inherited credentials and no skips.
Focused accounts19 and race2 pass. A new full frozen run remains required.

The full `3031789a` run returned one backend failure among5,329 tests,
zero errors and99exact allowed pack skips; all other surfaces passed. The
remaining QR-start failure returned404 before DNI matching. Focused QR30
passed, so the two-booking case is not a deterministic matching regression.
An isolated real door-token write/resolver experiment reproduces a catalog
publication race: an old read can hide the new token, and an old failure can
restore a cooldown after invalidation. Both old-code scenarios fail; both
generation-guarded scenarios pass, including the delayed caller's own result.
Root independently repeated the actual HEAD-function red proof and new-code
green proof, then managed QR30. This demonstrates the failure mechanism, not
the exact historical thread schedule, which the full log did not record.

Native table source `4a606cd0` physically retires the classic implementation
and updates eight runtime consumers. Root verification passed1,273 frontend
tests,126 browser cases (eight declared legacy skips), real three-header
ownership/sibling and standalone-story checks, plus19 account contracts.
An unnecessary generic-owner deactivation was removed: the existing selector
reservation already passed the supposed negative case. Only the new table
manifest entry changes raw account/reset bodies against3031789a.

Combined `4e955141` full verification returned5,331 backend tests, two failures,
one error and99exact allowed pack skips; all other surfaces passed. The prior
QR failure did not recur. Remaining failures were missed table-cutover test
pins: classic cohort count, client-loaded cohort membership, and Users classic
script ordering. They now assert the actual native manifest entry instead,
including immutable byte identity and no-store404 for retired classic paths.
Focused cache40 and Users6 pass with zero skips. Run these two contracts and
account snapshots before future shared-asset full ladders; none may be waived
as a generic snapshot issue. The combined full gate is still required.
