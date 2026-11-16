---
title: The container's canonical .env lives on the persistent volume, not the image tree
scope: repo
area: deploy
tags: [config, durability, env, docker, setup-wizard]
created: 2026-08-10
refs: [deploy/install-owned-town-packs-live-on-the-volume.md, architecture/deploy.md, architecture/setup-wizard.md]
status: active
---

## Problem

[[deploy/install-owned-town-packs-live-on-the-volume]] made the scaffolded
town pack itself durable — `_scaffold_pack` writes it onto `/app/data/packs`
on the volume. But the three boot keys that NAME that pack
(`TURNOS_PRIVATE_PACKS_DIR`, `TURNOS_REQUIRED_PACKS`, `PACK_CHAIN`) are written
by `config.save_env_keys` to the **effective `.env`**, and the container had
no opinion about where that was: `config._env_path()` fell back to
`config.ENV_PATH` — the checkout-root `.env`, i.e. `/app/.env` in the container.

That path was never baked into the image (`.dockerignore` excludes `.env`), so
`save_env_keys` created `/app/.env` at RUNTIME in the ephemeral container
filesystem. The pack survived a rebuild but its config did not: the next build
wiped the runtime-created file, and the boot keys were gone. The required-pack
gate then refused the now-unnamed private pack (or, under a locked-config
restart, validated-private then served the bundled public default).
Validate-private, resolve-public — the exact configuration-ownership mismatch
the sibling note closed, reopened one rebuild later by a second, overlooked
ephemeral path.

## Root cause

Two ephemeral paths, one durability fix. The first fix moved the *pack* off
the ephemeral image tree; the *config that points at it* was still being
written to an ephemeral runtime file, because `TURNOS_ENV_FILE` (the one knob
that decides the effective `.env`) had a default only for the hermetic E2E
harness, not for the container. There was a single-path reader (`_env_path()`
returns one path, never an overlay) — it just pointed at an ephemeral location
in production by default.

## Solution

A one-line container default, nothing else. The Dockerfile sets

```
ENV ... TURNOS_ENV_FILE=/app/data/turnos.env
```

`_env_path()` is unchanged: it still returns the one effective path, still no
overlay, still no fallback to a second file. The container simply points that
single path at the persistent volume, so `save_env_keys` writes the boot keys
to `/app/data/turnos.env` and every reader reads them back from there. A
rebuild wipes the image tree (including `/app/.env`); the volume — packs and
config together — is untouched.

Three modes, all unchanged except the container default:

- **Container** (compose / Coolify / `docker run`): the image default points at
  `/app/data/turnos.env`. Requires a volume mounted at `/app/data`
  (`docker-compose.yml` does; Coolify needs a persistent storage path — see
  below).
- **Explicit override**: setting `TURNOS_ENV_FILE` in the process (compose
  `environment:`, Coolify env, `docker run -e`) REPLACES the path entirely —
  not merged. This is the same single-effective-path contract the hermetic E2E
  harness relies on, reused for ops.
- **Direct local** (`python3 server.py`, no image): `TURNOS_ENV_FILE` unset →
  `_env_path()` falls back to the checkout `.env` exactly as before.

Bootstrap keys (`DATABASE_URL`, `HOST`, `PORT`) never depended on the file:
they are process-env in every deploy (compose `environment:`, Coolify env),
above the file in the precedence, so a first boot with an empty `turnos.env`
resolves them from the environment.

## Coolify caveat

The volume must actually be mounted at `/app/data` for any of this to be
durable. The Pinamar reference deployment historically had **no** persistent
storage configured there (verified 2026-08-03); that ops gap lives in the
private `humanrounds-pinamar` repo, not this generic contract. An install on a
Coolify app without the persistent storage path is no more durable than the
image — add the path before relying on first-run config surviving a redeploy.

## Rollback

Image-only, and keep the config durable while doing it. Revert the Dockerfile
`ENV` line (drop `TURNOS_ENV_FILE=/app/data/turnos.env`) and redeploy, BUT set
`TURNOS_ENV_FILE=/app/data/turnos.env` as an explicit runtime override
(compose `environment:`, Coolify env, `docker run -e`) so the install keeps
reading its boot keys from the volume — the explicit-override mode above.
The runtime override is what makes the rollback durable.

Do NOT copy `turnos.env` into `/app/.env`: `.dockerignore` excludes `.env`, so
that path is not baked — copying into it recreates the exact ephemeral
runtime file this fix retires, and the next rebuild loses the boot keys again.
Reverting the image with the override OFF only returns direct-local-style
behavior to the checkout-`.env` default; that is the non-durable state, not a
rollback target for an install that already finished first-run on the volume.
No schema migration, no data move, no DB change. The reader, the writer, the
wizard, the data-pack resolver, and the DB bootstrap are all untouched; only
the container default moved.

## How to apply

Don't add a second `.env` reader, an overlay, or a fallback chain — the
single effective path is the whole point (a second reader reopens the
ephemeral-vs-volume divergence this closes). If a deploy needs a different
config location, set `TURNOS_ENV_FILE` explicitly; it replaces the default, it
does not layer on top. Proven synthetic (no real clinic data, no real image
built): `test/test_install_owned_packs.py::TestReplacementContainerLifecycle`
— a simulated rebuild wipes the image tree and the ephemeral container
filesystem while the town still resolves and validates from the untouched
volume config; the same holds under `TURNOS_CONFIG_LOCK=1` with a conflicting
DB `PACK_CHAIN` and the process triplet cleared (served from the volume, not
the DB), and direct process-env precedence is pinned (process env wins over
the volume file under the lock); a missing or corrupt configured private
pack, with the canonical config still present on the retained volume, still
fails closed (the detectable case — the config keeps asserting the
requirement); the contrast case exercises `config.ENV_PATH` truthfully (the
runtime-created ephemeral file, not a baked one) and goes red; and the
explicit-override-replaces pin holds. Honest boundary: a TOTAL `/app/data`
loss removes the canonical config too, so the gate loses the fact that any
pack is required and boot degrades silently to bundled public packs — not
detectable in this same-volume design without moving the requirement-fact
off-volume.
