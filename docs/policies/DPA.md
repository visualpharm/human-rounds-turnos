# Data Processing Agreement — posture and processor inventory

Human Rounds is a self-hosted appointments-and-services engine for public
health networks. **Each installation is its own data controller.** This
document states the processing posture the code implements and lists every
third party the code can hand personal data to, so an institution can sign
its own DPA on a known footing. It is not legal advice and not a signed
agreement.

## Publication

- The public repository is one commit made with `git archive` from the private main branch; its history is not published.
- Licence AGPL-3.0, copyright Generated Media, Inc.; [LICENSING.md](LICENSING.md) lists the assets that are not under the AGPL.
- Kept out of the release (`export-ignore` in `.gitattributes`): the reference deployment's private data pack, agent harness files (`.claude/`, `CLAUDE.md`), research records made against the real catalog, the login screen's humor images, and installation-only operations notes.
- Everyone who wrote code for the project agreed on 2026-11-11 to its publication under AGPL-3.0.

## Current posture

- **Controller:** the institution that runs an installation. The reference
  deployment, `salud.pinamar.gob.ar`, belongs to the municipality: it hosts
  it, updates it from a release package, holds its backups and sends its mail
  through its own Mailjet account; staff and patients sign in there through
  the municipal portal. The project operates no shared backend, and the
  public-cut preflight (`scripts/public_cut_preflight.py`) keeps patient data
  out of the public repository.
- **Legal basis the product texts assume:** Argentina's Ley 25.326. The
  privacy policy (`app/patient_web/legal.py`, served at `/privacy.html`) names
  the law, the free access right every six months (art. 14 inc. 3) and the
  supervisory authority. Installations elsewhere replace that text from the
  panel (overlay in `legal_docs`).
- **Masking before external AI:** text sent to the model is pseudonymised by
  `app/core/pii.py` (names, document numbers, phones, emails, addresses become
  per-request labels, restored locally; the mapping is never stored). The
  referral image/PDF, the DNI photo used at sign-up and images attached to the
  pre-consult interview travel whole, because reading them is the point. The
  privacy policy says exactly this (`tests/test_legal.py`, `tests/test_pii.py`).
- **Best effort, not a guarantee:** a misspelled or OCR-garbled name can pass
  the filter. Do not sign a stronger promise than the code keeps.
- **Data-subject rights:** a registry of where each person's data lives
  (`app/core/personal_data.py`), an export, a ledger of requests and an
  erase/anonymise tool
  ([derechos-de-los-titulares.md](../architecture/derechos-de-los-titulares.md)).
  A patient deletes their own lab uploads from their account.
- **Secrets** live in the installation's environment (template
  [.env.example](../../.env.example)); the repository ships none.
- Decision needed: whether Generated Media, Inc. is ever a processor for an
  installation (today it is not: no hosted service exists).

## Data processors inventory

A processor is active only if the installation configures it; an
unconfigured one degrades softly ([docs/connectors.md](../connectors.md)).

- **OpenRouter → Google Gemini 3 Flash** (`app/core/ai_policy.py`, one
  reading): referral orders, DNI OCR, booking chat, pre-consult interview,
  chart summary, lab reports, translation. Leaves: masked text; whole referral
  file, DNI photo and interview images. Off: no `OPENROUTER_API_KEY`, or
  `TURNOS_FAKE_AI=1`.
- **Mailgun or Mailjet** (`CONNECTOR_EMAIL_TRANSPORT`): confirmations,
  reminders, cancellations, recovery, staff invitations, alerts. Leaves:
  address, name, appointment, message body. No key = silent skip.
- **Meta WhatsApp Cloud API** (WAHA is loopback development only): codes and
  chat replies. Leaves: phone number and message text.
- **Cloudflare Turnstile:** bot challenge on sign-in and sign-up. Leaves:
  visitor IP and browser signals. No `TURNOS_TURNSTILE_SECRET` = no challenge.
- **HSI** (`emr_sync`): appointments synchronised with the institution's own
  record system. Leaves: patient identity and appointment data.
- **RENAPER, SISA PUCO, REFEPS:** identity, coverage and licence lookups.
  Leaves: DNI or licence number.
- **Mi Salud Digital / WebTurno** (`slot_source`): availability only, no
  patient data.
- **Sentry-compatible error tracking** (`TURNOS_SENTRY_DSN`, off without it):
  an allowlisted event with identifiers masked (`app/core/error_tracking.py`).
  The test sites send nothing since 2026-11-10; the municipal installation
  reports only to its own project.
- **fal.ai:** chat voice transcription, when enabled. Leaves: the audio.
- **Gravatar:** account-photo fallback. Leaves: a hash of the email.
- **Backup storage:** the encrypted full backup goes to an R2/S3 bucket the
  installation owns (`app/ops/backup_full.py`, encrypted before it leaves);
  the older Backblaze B2 copy of `data/` sends files unencrypted when left
  active.
- **Hosting provider:** everything, chosen by the installation.

## Sub-processing

- OpenRouter routes to the model provider; whether either keeps prompts is
  governed by their terms, and the privacy policy says the installation does
  not control them.
- Mail, messaging, bot protection, error tracking and storage providers run
  on their own infrastructure; the code pins no region (Mailgun's endpoint is
  configurable). Bucket regions are chosen when the bucket is created.
- The project adds no sub-processor of its own; the demo regenerates
  fictional data daily (`demo_refresh.py`).
- Decision needed: whether a controller requires a region pin or zero
  retention for the AI route, and who signs those terms for it.

## Changes

This document changes whenever a connector or transport is added, removed,
or starts sending a new data category. The connector checklist
([docs/connectors.md](../connectors.md)) updates this inventory and the
privacy policy's processor paragraph in the same change, and
[DATA_MAP.md](DATA_MAP.md) follows; `tests/test_legal.py` keeps the privacy text honest. Material changes
follow [GOVERNANCE.md](../../.github/GOVERNANCE.md), "Approvals and review".

## Approval record

record: approval:hr-3398dd7254021f18

date: 2026-11-16
