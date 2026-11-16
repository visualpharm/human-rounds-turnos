# Security Policy

Human Rounds handles patient health data (appointments, referral documents,
DNI/identity information) for real public hospitals. Take vulnerabilities
seriously — a bug here isn't a defaced landing page, it's a healthcare
system's patient data.

## Reporting a vulnerability

**Do not open a public issue.** Report privately through the repository
host's private channel — a private security advisory or a confidential issue —
which only the maintainers can see, so nothing is disclosed publicly until a
fix ships.

Include, as far as you can:

- What the vulnerability is and where it lives (file/endpoint).
- Steps to reproduce, or a proof of concept.
- What you think the impact is (data exposure, auth bypass, injection...).
- Which deployments you believe are affected (the reference Pinamar
  deployment, the engine in general, or both).

## What counts

In scope:

- Authentication/authorization bypass (patient accounts, admin panel,
  roles, sessions, passkeys, 2FA).
- Any path to reading or modifying another patient's data.
- Injection (SQL, command, template) anywhere in the request-handling path.
- Secrets exposure (API keys, session tokens, password hashes) through any
  endpoint, log, or error message.
- Anything that defeats `docs/architecture/data-packs.md`'s isolation
  between installations (one deployment's data leaking into another's).

Out of scope (report as a normal issue instead):

- Missing rate limiting on an endpoint that's already behind
  authentication.
- Findings that require physical access to a deployment's server.
- Issues only reproducible on a deliberately misconfigured install (e.g.
  `TURNOS_ADMIN_PASS` left at a placeholder value in a public deployment —
  that's an operator error, though we'd still like to know if the app
  should be nagging harder about it).

## No PHI in reports

**Never attach real patient data, referral documents, or DNI photos to a
report** — not even as "proof it's real". Describe the vulnerability and
reproduce it with synthetic data (`packs/example-town/`'s fictional
"Sampleton" data, or `TURNOS_FAKE_AI=1`/`TURNOS_FAKE_RENAPER`/
`TURNOS_FAKE_PUCO` test modes) — see [docs/architecture/data-packs.md](docs/architecture/data-packs.md)
and [.env.example](.env.example) for what's available. If a report already
contains real PHI, say so explicitly in the advisory so we can redact it on
receipt.

## Response

This is a volunteer-run, philanthropic project (see [CLAUDE.md](CLAUDE.md))
— no SLA, but security reports get priority over feature work. We'll
acknowledge a report, work the fix privately, and credit you in the
advisory once it's out (unless you'd rather stay anonymous).
