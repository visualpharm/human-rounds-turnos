# Human Rounds refactor finish line

**Status:** Approved on 2026-08-23. This is the binding completion definition
for the current refactor.

## Completion criteria

The refactor is complete only when all of these are true:

1. Every retained post-`664cf0b6` candidate has been deliberately integrated
   into one exact release candidate. Superseded candidates are documented and
   excluded rather than silently abandoned.
2. The exact candidate passes both managed topologies: `local-ci` and
   `local-ci-asgi`. Expected environment skips remain visible and no failing
   gate is waived as unrelated without source-backed diagnosis.
3. `test/test_transport_shapes.py` and `test/test_route_probe.py` pass (they
   replaced `scripts/refactor_architecture_census.py --require-complete` on
   2026-10-28). A FastAPI-owned URL is not sufficient evidence: the retired
   `handle(handler, method)` transport must not exist under any name, so no
   production source defines, references or imports it, rebuilds its response
   writer or fabricates its request, and no live route, dependency or owner
   module may come from it. Adapter-backed routes, legacy-shaped
   dependencies, renamed wrappers, additive frontend twins and copied legacy
   shells never count as retirement. Rollback-only and arbitrary unreachable
   source require separate lifecycle evidence. The tests do not claim to
   detect arbitrary dead source.
4. That exact Git SHA is deployed to both the production and demo
   installations.
5. Production and demo pass their repository-owned smoke suites against the
   deployed SHA, including health, readiness, authentication boundaries,
   fictional demo behavior and static-asset consistency.

Local tests, a frozen worktree, a commit, a push, or one successful deployment
alone does not satisfy this finish line. The tested, deployed and smoked bytes
must be the same exact candidate.

## Separate follow-up tracks

The following work does not keep this refactor open:

- Optional daemon migrations that require independent reliability evidence.
- Public-repository publication and its institutional documents.
- New clinical or Enterprise product capabilities beyond the retained
  refactor candidates.
- Operational approvals, integrations and hospital inputs that are owned by
  external institutions.

These remain real work and keep their own readiness gates. Separating them
prevents an open-ended product roadmap from redefining a bounded refactor after
its engineering acceptance criteria have been met.

## Current state

**Reached on 2026-08-27**, release candidate `d6af4ad1`: both managed
topologies green, `--require-complete` passing, deployed and smoked on the
production and demo installations. `main` has since advanced with ordinary
post-refactor feature work; the finish line binds the candidate, not every
later commit.

The transport guard (`test/test_transport_shapes.py`, with the route probe in
`test/test_route_probe.py`) remains the binding retirement gate, and it stays
green on current `main`. Until 2026-10-28 the gate was the architecture
census script, which last reported, at `fd722b1c` on 2026-08-30:

```
371/371 native-independent · 0 adapted-legacy · 0 rollback-only · complete=true
```

`server.py`, `legacy_shim.py`, `core/routing.py` and `module_router` are
deleted from the tree; no live route can execute through `LegacyHandlerShim`
or `route_via_handle` because neither symbol exists any more, and bringing
either back fails the guard (a `handle` entrypoint, an import of a retired
module, the old response writer or a fabricated request, a route owned by a
module that exposes `handle`). The number of routes is no longer a gate of its
own: the ordered route table is pinned by `test/tier4_composition_inventory.json`.
This is a statement about the transport only, not a claim about the value or
total volume of the broader refactor program.

### Post-finish-line closure — 2026-09-02

The candidate's finish line above was met on 2026-08-27. The follow-up this
section used to describe — 54 legacy `def handle(handler, method)` transports,
test-reachable only — was executed on 2026-08-30/31: every transport is
deleted with its tests re-pointed at the native routers or the module's pure
domain functions, a `def handle(handler` search over non-test Python is
empty, and the census still reports 373/373 native-independent at `22f13b66`.
The admin hash shell (`admin.html`) was retired on 2026-09-01 (`1c7afc93`)
with all 15 declared admin sections served as direct `/admin/<screen>` URLs.

That follow-up period also showed the failure mode this document warns
about: 94 commits and roughly 23k inserted / 22k deleted lines between
`d6af4ad1` and `22f13b66`, one connector boundary reworked four times in 24
hours, and two commits landed with `test/test_app_composition.py` red (the
composition inventory fixture and the 100-line `app.py` ceiling) because the
full managed gate was not run before merging. The programme-level closure —
every acceptance line of the 2026-08-05 external audit scored MET, DEVIATION
or EXTERNAL, plus the bounded items still open — lives in
[audit-acceptance-checklist.md](audit-acceptance-checklist.md), the only list
that may reopen refactor work.
