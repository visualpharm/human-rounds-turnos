# "La base de datos no está disponible" means the Coolify host's disk is full

**Date:** 2026-08-05 · **Area:** deploy

## Symptom

The admin panel loads, the nav works, filters render — and the list body says
**"La base de datos no está disponible."** `GET /api/admin/requests` returns
`503`. The patient site keeps serving fine, because catalog pages come from the
data pack and never touch Postgres.

It hits **prod and demo at once**, which is the tell: they are separate
applications with separate Postgres containers, so a shared failure is a shared
resource — the host.

## Root cause

`/dev/sda1 38G 37G 0 100% /`. Postgres cannot write, and the checkpointer takes
the whole instance down with it:

```
PANIC: could not write to file "pg_logical/replorigin_checkpoint.tmp": No space left on device
LOG: checkpointer process (PID …) was terminated by signal 6: Aborted
LOG: all server processes terminated; reinitializing
```

It then loops forever: recover → checkpoint → ENOSPC → PANIC. The container
still reports `Up N days` (the entrypoint never exits), so `docker ps` looks
fine unless you read the `(unhealthy)` marker. `coolify-db` and an unrelated
`mongo:7` were in the same loop, which is why the Coolify panel 504s at the same
time — a second tell that this is the host and not the app.

## Diagnosis

```bash
ssh coolify 'df -h / ; docker system df ; docker ps --format "{{.Names}} | {{.Status}}"'
```

Map an app to its database before reading the right logs — the containers are
named by UUID, so grepping for "postgres" or "turnos" finds nothing useful:

```bash
ssh coolify 'docker exec <app-container> env | grep ^DATABASE_URL'
```

Prod → `<prod-db-uuid>` (db `sana_turnos`), demo →
`<demo-db-uuid>` (db `postgres`).

## Fix

```bash
ssh coolify 'docker builder prune -af ; docker image prune -af ; df -h /'
```

Freed 13 GB (37 G → 24 G used). **Both Postgres instances recovered on their
own within seconds** — no restart, no intervention: the crash loop was already
retrying, and the next retry found space, replayed WAL and reported `database
system is ready to accept connections`. No data was lost (`booking_requests`
intact on both). `coolify-db` and the mongo container recovered the same way.

`docker image prune -af` is safe here despite what
[the 2026-08-03 note](ssh-keys-not-writable-means-disk-full.md) says: "unused"
means backing **no container, running or stopped**, so it only costs a re-pull
or rebuild. It does drop Coolify's rollback history — an acceptable trade when
prod's database is down.

## Why the existing guard did not catch it

`/usr/local/bin/disk-guard.sh` (cron `/etc/cron.d/disk-guard`) has existed since
2026-07-30 and had already been tightened once. It still missed this:

- **Hourly is too coarse for this host.** The 10:00 run finished clean at 73%.
  By 10:45 the disk was at 100% — a simultaneous prod+demo deploy burned ~10 GB
  in 45 minutes, entirely inside one hourly gap.
- **Age-filtered pruning is useless during a rebuild burst.** The `>=75%` branch
  pruned images `until=6h`, but the image a fresh build had just superseded was
  minutes old, so the branch reclaimed nothing exactly when it mattered.

Both fixed on 2026-08-05: the cron runs **every 10 minutes** (the script exits
silently below 70% and holds a `flock`, so the cadence is free), and past **75%**
it prunes every unused image regardless of age. Emergency threshold 85% → 82%.

That 75% was itself tuned down from 78% within the hour, by watching the guard
run against real churn: right after a prod+demo redeploy the disk sat at 77%,
and the age-filtered `>=70%` branch reclaimed **0 B of images** — the two
superseded 1.09 GB images were minutes old. 78% was one point too high to catch
the very state the branch exists for. **Pick these thresholds by watching a real
deploy, not by reasoning about them.**

## Rule

**Any "database is unavailable" on a Coolify-hosted app: run `df -h` on the host
before reading a single line of application code.** Postgres does not report
ENOSPC as a connection error, and the app layer cannot tell "disk full" from
"database down". Prod and demo failing together always points at the host.
