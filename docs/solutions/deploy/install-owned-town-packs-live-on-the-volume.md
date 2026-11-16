---
title: Scaffolded town packs live on the persistent volume, not the image tree
scope: repo
area: deploy
tags: [data-packs, setup-wizard, durability, required-packs]
created: 2026-08-10
refs: [security/required-private-pack-readiness.md, architecture/data-packs.md, architecture/setup-wizard.md]
status: active
---

## Problem

The first-run installer (`ops/setup.py`) scaffolded a brand-new town pack into
`data_packs.packs_dir()` — the repo's `packs/`, which in a container deploy is
baked into the image. A clinic that finished setup therefore had a
seemingly-finished pack that existed **only inside the ephemeral image tree**.
The next rebuild (a new engine release) repopulated `packs/` from the build
context and silently wiped the clinic's own town — `manifest()` no longer
found it, `active_chain()` dropped it, and the site fell through to an empty
chain. Twin of the [[deploy/private-clinical-history-lives-in-postgres]] lesson
for a different artifact.

## Root cause

The durability primitive already existed at the branch head
(`TURNOS_PRIVATE_PACKS_DIR` + `TURNOS_REQUIRED_PACKS`, the reviewed fail-closed
resolver), but the wizard never used it: it wrote to the public image-tree
`packs/` and only set `PACK_CHAIN` (a DB config row). Nothing connected the
scaffolded pack to the volume or the boot gate.

## Solution

`_scaffold_pack` now writes `pack.json` + `sitio.json` + a frozen
`INVENTORY.json` into `data_packs.install_packs_dir()` — a configured
`TURNOS_PRIVATE_PACKS_DIR`, otherwise `<data_dir>/packs` on the persistent
volume (`/app/data/packs` in prod), never the image-tree `packs/`. The commit
step then registers three boot keys together via `config.save_env_keys` (the
only `.env` boot-key writer), because they are deliberately process/`.env`-only
— they can never go through `config.guardar`'s DB layer (consulting the DB can
run migrations before a bad required pack is rejected; see
[[security/required-private-pack-readiness]]):

- `TURNOS_PRIVATE_PACKS_DIR=<install_packs_dir()>` and
  `TURNOS_REQUIRED_PACKS=<slug>` — the resolver finds the town on the volume
  and the boot gate validates it;
- `PACK_CHAIN=<prefix>,<slug>` — the chain that NAMES that required town (see
  the contract below).

The result reuses the reviewed resolver unchanged: the scaffolded town is
discoverable on the volume, resolves through the normal chain, and on every
later boot the readiness gate admits it — or **fails closed** if the volume is
ever unmounted, partially seeded, or corrupt, instead of silently serving an
empty site or falling back to a bundled public copy. Optional invalid private
packs keep their degrade-not-crash fallback. Bundled public packs are
untouched.

## Configuration-ownership contract

The chain that names a required pack must move WITH the required pack. The
private root and required pack are boot keys (process/`.env`-only), so they
survive a locked-config restart (`TURNOS_CONFIG_LOCK=1`, the ops escape hatch
that makes env/`.env` win over whatever an admin saved from the Settings UI).
The first cut of this slice persisted `PACK_CHAIN` to the DB only — so under
the lock the gate still VALIDATED the town (its boot keys survived) while
`active_chain()` fell through to the bundled public default
(`global,ar,ar-b,ar-b-pinamar`) and never served it: **validate-private,
serve-public.** The chain is now a boot key alongside the other two, so a
locked restart serves the town too. `PACK_CHAIN` is ALSO written to the DB
(via `config.guardar`) for normal-operation admin edits — under the lock the
boot-key copy wins, which is exactly the lock's contract (ops pins
install-time config; the admin's live DB value wins in normal operation).
Matched (pre-existing) packs are unchanged: no install-owned volume pack, so
no boot keys and no ownership mismatch to close.

## How to apply

A clinic's scaffolded pack is install-owned data on the volume, not engine
content — never commit one, and never hand-edit `<data>/packs/<slug>` while the
gate is armed (a checksum mismatch refuses boot). The wizard surfaces a clear
error if the effective `.env` is not writable; on a read-only-`.env` deploy,
set `TURNOS_PRIVATE_PACKS_DIR`, `TURNOS_REQUIRED_PACKS`, and `PACK_CHAIN` by
hand (all three boot keys — the chain must name the required pack). Proven
synthetic (no real clinic data): `test/test_install_owned_packs.py` — create
town → simulated rebuild → same validated pack resolves; missing/corrupt
required refuses boot; optional invalid never shadows public; and a
locked-config restart (`TURNOS_CONFIG_LOCK=1`) serves the town from the boot
`.env`, not the bundled public default. The wizard's own persistence +
locked-restart is proven end-to-end in `test/test_setup.py`'s full walk.
