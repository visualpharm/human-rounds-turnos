# "SSH keys storage directory is not writable" means the Coolify host's disk is full

**Date:** 2026-08-03 · **Area:** deploy

## Symptom

A push to `main` lands, prod keeps serving the previous build, and the Coolify
deployment for that commit is `failed` with:

```
Deployment failed: SSH keys storage directory is not writable. Run on the host:
sudo chown -R 9999 /data/coolify/ssh && sudo chmod -R 700 /data/coolify/ssh && docker restart coolify
Location: /var/www/html/app/Models/PrivateKey.php:280
```

## The message is wrong — do NOT run the chown

The permissions were already exactly what the error demands
(`drwx------ 9999`). The directory was not writable because **there was no disk
space left**:

```
/dev/sda1  38G  36G  53M  100% /
```

Coolify writes an ephemeral key file per deployment, the write failed on ENOSPC,
and it reported the only cause it knows about. Running the suggested
`chown -R`/`chmod -R` changes nothing and hides the real problem.

## Diagnosis

```bash
ssh coolify 'df -h / ; docker system df'
```

`docker system df` showed 23.6 GB reclaimable images and 3.1 GB of build cache
on a 38 GB disk. Every failed and superseded build leaves layers behind, and
this repo's image is large.

## Fix

```bash
ssh coolify 'docker builder prune -f && docker image prune -f && df -h /'
```

Freed 3.1 GB immediately (build cache), enough to unblock deploys. Deliberately
NOT run: `docker image prune -a` (removes images no running container
references — that is Coolify's rollback history) and anything touching
`--volumes` (that is application data).

## The second-order failure: a wedged deployment blocks the queue

The deployment whose job died on the full disk stayed `in_progress` forever.
Coolify serialises deployments per application, so every later push for that app
sat in `queued` behind a phantom. There is no cancel endpoint —
`DELETE /api/v1/deployments/<uuid>` returns 404.

What clears it: `ssh coolify 'docker restart coolify'`. Coolify reconciles
in-flight deployment records on boot. It restarts only Coolify's own container —
deployed applications and the proxy are separate containers and keep serving.
Check first that no OTHER app is mid-deploy (`GET /api/v1/deployments` returns
only active ones), because a restart does kill a genuinely running build.
Afterwards, re-trigger: `POST /api/v1/deploy?uuid=<app-uuid>&force=true`.

## Useful facts confirmed while doing this

- App UUIDs: prod `<prod-app-uuid>`, demo `<demo-app-uuid>`.
  `GET /api/v1/applications` lists them; a UUID that returns all-null fields is
  simply the wrong UUID, not a permissions problem.
- The API token is `COOLIFY_ACCESS_TOKEN` in the operator's private secrets file (not
  `COOLIFY_API_TOKEN`).
- `GET /api/v1/deployments` returns ONLY active deployments; per-application
  history is `GET /api/v1/deployments/applications/<uuid>?take=N`.
- Root SSH to the host is the `coolify` entry in `~/.ssh/config` (Hetzner origin
  IP — the public name is behind Cloudflare, so port 22 is not reachable there).

## Rule

**A Coolify deploy that fails on anything filesystem-shaped: check `df -h` on
the host before believing the error.** And when prod does not show a pushed
change, check the deployment's *status*, not just the page — a failed deploy and
a slow deploy look identical from the browser.
