---
title: A brieflysites.com specific A record pointing at the wrong Coolify host IP looks identical to "not deployed yet"
scope: repo
area: deploy
tags: [coolify, dns, cloudflare, traefik]
created: 2026-08-06
refs: [896f72a8, 37308df1]
status: active
---

## Problem

`salud.brieflysites.com` 503'd "no available server" (Traefik's generic
unmatched-router message). Registered the domain on the sana-turnos Coolify
app (`domains` field, confirmed via GET), redeployed twice (once plain, once
`force=true`), confirmed the generated `custom_labels` included a correct
`Host(\`salud.brieflysites.com\`)` router with `certresolver=letsencrypt` —
and it STILL 503'd, for ~25 minutes of polling, with Traefik serving its
default self-signed cert instead of a real one.

## Root cause

The Cloudflare A record I added for the new hostname pointed at
`<host-B-ip>` — copied from a sibling tracking card's note earlier the same
day ("added a specific proxied A record ... to the current Sao Paulo host
<host-B-ip>"). That IP is **not** the live Coolify/Traefik host. The
actual, current, single production host — confirmed by
`.claude/ref/services.md` (updated 2026-08-05, describes ONE Hetzner box,
28 containers, disk just resized) and by direct testing — is
`<host-A-ip>`, the same IP `*.brieflysites.com`'s own wildcard record
already points to. `coolify.brieflysites.com` itself (the control-plane API)
also resolves through that same wildcard, so `<host-A-ip>` is where the
Coolify app config actually lives.

Every Coolify-side step I took (domain registration, redeploy, label
generation) was correct and landed on the right host — because the Coolify
API calls went through `coolify.brieflysites.com`, which points at
`<host-A-ip>`. Only the Cloudflare DNS record for the new hostname was
wrong, sending browser traffic to a different box that has no idea about
`salud.brieflysites.com`. Symptom looked exactly like "the domain isn't
registered/deployed yet" (503, self-signed default cert) because that's
genuinely true — just on the IP the DNS record was pointing to, not the one
serving `coolify.brieflysites.com` and everything else.

Never re-use an IP from another session's notes without checking it against
the source of truth (`services.md`) or the wildcard record itself. The
sibling card's `sana-turnos.brieflysites.com` -> `<host-B-ip>` record
[was still live and apparently working at the time of this note] — worth
re-verifying if it 503s again; it may share the same trap, or `216.238.*`
may be a legitimate secondary path (Hetzner floating IP from the 2026-08-05
disk rescale) that just happened not to have `salud` registered on it.

## Solution

- Diagnose "registered but still 503" by testing the origin directly, NOT
  through Cloudflare: `curl -H "Host: <domain>" https://<ip>/` against BOTH
  the IP you think is right and the wildcard's actual target
  (`dig` the zone via the Cloudflare API, `GET /zones/<id>/dns_records`, and
  read the `*.brieflysites.com` record's `content` — do not trust `dig` on
  the hostname itself, it always returns Cloudflare edge IPs when proxied).
- Cross-check any host IP against `.claude/ref/services.md` before trusting
  a number pulled from a tracking card note or a different session's transcript.
- Once confirmed, `PATCH` the Cloudflare A record's `content` to the correct
  IP (`api.cloudflare.com/client/v4/zones/<zone>/dns_records/<record_id>`) —
  fast, no redeploy needed, Cloudflare edge picks it up in well under a
  minute for a proxied record.
- A plain self-signed "TRAEFIK DEFAULT CERT" served for your hostname (check
  with `openssl s_client -connect <ip>:443 -servername <host> ... | openssl
  x509 -noout -issuer`) means Traefik has NO router for that Host at all on
  that specific IP — it is not a slow-ACME-issuance symptom, don't just wait
  it out; verify the IP first.

## How to apply

Any "I registered the domain and redeployed but it's still 503" symptom:
before assuming Coolify/Traefik is broken or ACME is slow, verify the
Cloudflare record's `content` IP against the zone's own wildcard record and
against `.claude/ref/services.md`, by hitting the origin IP directly with
`-H "Host: ..."` and comparing to a known-working domain on the same IP.
