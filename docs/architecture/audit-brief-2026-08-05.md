# Historical audit brief — 2026-08-05

> This briefing is a preserved meeting artifact (translated to English from its
> original Russian). Its file counts, ownership notes, rollout status, and
> open-work list are not current architecture. Use `CLAUDE.md` and the English
> documents linked from its "Where the detail lives" table for the maintained
> contract.

# Architecture audit: briefing for the maintainer and a reviewer

Prepared for the product meeting of 2026-08-05, following the town hall of 2026-08-04.
Purpose of this document: for the audit to start from real problems rather than from a day of orienting in the repository.

Repository: `visualpharm/sana-turnos` (private, commits go here).
`visualpharm/human-rounds` is the public name of the same product after the rename.

## What this system is

An open medical platform for a city's public healthcare.
It lives in Pinamar, 50 thousand residents, with load multiplying in the summer.

Scope: booking a doctor, pre-visit history intake by voice or through a QR code on the consulting-room door,
triage, a doctor panel with SOAP and differential diagnosis, referrals, parsing 10 years of archived PDFs,
handoff to WhatsApp.

## Numbers

| Metric | Value |
|---|---|
| Python | 112 357 lines, 331 files |
| JS / MJS / TS | 42 220 lines, 141 files |
| HTML / CSS | 9 596 lines |
| Total files in git | 1 602 |
| Tests | 2 654 in 133 files |
| Connectors to external systems | 12 |
| Architecture documents | 22, in `docs/architecture/`, counting this file |

## Where the layers are smeared

This answers the maintainer's question "how well are the layers separated, so that one thing can be changed without rewriting everything".
Four facts, each checkable with one command.

- **101 Python modules and 58 JS files sit in the repository root.** There is no package structure.
  Anyone imports from anywhere. Check: `git ls-files '*.py' | grep -vc '/'`
- **64 non-test modules import `db` directly.** There is no data-access layer as a boundary,
  `db.py` is a shared bus. Check: `rg -l "^\s*(from db import|import db$)" -g '*.py' -g '!tests/**'`
- **Raw SQL lives in 15 modules besides `db.py`.** That is, the schema has leaked into business logic and into scripts.
  Check: `rg -l "SELECT |INSERT INTO |UPDATE .+ SET " -g '*.py' -g '!tests/**' -g '!db.py'`
- **There is no web framework.** `server.py` runs on the standard library's `http.server.ThreadingHTTPServer`,
  routing and dispatch are hand-written. The only external dependencies are `psycopg`, `Pillow`, `segno`.

God files, the places worth starting to look:

| File | Lines |
|---|---|
| `db.py` | 5 405 |
| `patient_web/site_*.py` | 4 664 |
| `server.py` | 3 757 |
| `booking_requests.py` | 2 734 |
| `accounts.py` | 2 193 |
| `reports.py` | 2 024 |
| `admin-content.js` | 2 875 |
| `service.js` | 2 334 |
| `admin-reports.js` | 2 267 |

## What is already separated properly

So that the audit does not rewrite what already stands in place.

- `app/connectors/` — 12 external systems isolated by folder: `hsi`, `renaper`, `puco`, `refeps`,
  `webturno`, `waha`, `meta`, `msd`, `marqui`, `alepo`, `local_history`.
- `app/modules/` — `intake` and `turnos` were extracted as modules, the result of a 13-hour refactoring.
- `migrations/` — migrations live separately.
- 2 654 tests. Unit coverage exists; what usually breaks is what tests do not cover: layout and end-to-end scenarios.
- 21 architecture documents were written before this brief, by subsystem: queue, authorization,
  chat and WhatsApp, reports, i18n, HSI, settings, wizard.

## Questions the maintainer raised, and where to look for answers

1. **Cost of the refactoring.** The bulk of it is spreading the 101 root modules across layers
   and moving SQL out of 15 modules behind the `db.py` boundary. The god files above are 80 percent of the work.
2. **Development speed will drop roughly fourfold after the split into layers.** This is a business decision, not a technical one.
   It has to be made before the audit, not after.
3. **A dedicated person for maintenance.** the maintainer's thesis: one person cannot handle 3-4 projects face to face.
   The numbers above are needed precisely to estimate how many person-months that is.
4. **QA.** Rita, Danya, Seryozha, Elmir asked for priorities and criticality. Proposed order:
   booking and triage scenarios first, where the cost of an error is highest, then the admin panel, then reports.
5. **WhatsApp handoff through Meta Developer.** The most painful integration, keys and hooks
   are spread over three places in the Meta console. See `app/connectors/meta` and `app/connectors/waha`.

## What is out of the audit's scope

The AI gives no medical advice, and this is not an unfinished feature but a boundary set by law.
As soon as the system gives advice, it becomes a medical device and requires licensing.
Triage and history intake are allowed, treatment recommendations are not.
