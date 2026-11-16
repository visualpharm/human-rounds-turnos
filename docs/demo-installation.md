# Public demo installation

The public demo is the real Pinamar Turnos application, not a static mock. It
uses the fictional `packs/example-town` catalog ("Sampleton" — retired
from `webdata-ejemplo/` on 2026-07-30, same 7 files, see
docs/architecture/data-packs.md) and an independent PostgreSQL database so
visitors can explore the patient and administrative interfaces without
seeing or changing municipal data.

**Deploy-time hazard**: if the live Coolify demo app still has
`TURNOS_WEBDATA_DIR=/app/webdata-ejemplo` set from before this rename, update
it to `/app/packs/example-town` as PART OF the deploy that ships this repo
change — `webdata-ejemplo/` no longer exists in the checkout, so the old
value would resolve to nothing.

## Required deployment boundaries

- Create a new application and PostgreSQL database. Do not reuse the Pinamar
  production database or its persistent volume.
- Set `TURNOS_DEMO_MODE=1`.
- Set `TURNOS_WEBDATA_DIR=/app/packs/example-town`.
- Set `TURNOS_PUBLIC_ORIGIN` to the public demo origin.
- Generate a unique `TURNOS_ADMIN_SECRET` for signed demo sessions. Do not
  reuse the Pinamar production secret.
- Set a dedicated, daily-capped `OPENROUTER_API_KEY` for the public demo. Do
  not reuse the Pinamar production key.
- Do not attach Mailgun, WhatsApp, HSI, RENAPER or other production
  credentials to the demo application.

Demo mode locks these safety values before feature modules load. The locks also
override settings saved through the demo admin:

- HSI writes and both synchronization workers are off.
- Appointment reminders and waitlist messages are off.
- Every WhatsApp and verification-code send is blocked again at the transport
  boundary, even if a credential is added accidentally.
- Backups and the live AI health worker are off.
- Conversational chat uses the real model through its dedicated capped key.
- Referral/OCR and visitor intake AI use deterministic fake providers;
  uploaded documents and anything a public visitor types are not sent to the
  chat provider. The one exception is the versioned, fully fictional Sofía
  Herrera questionnaire: the daily seed sends that transcript through the
  real intake-analysis pipeline so the doctor console demonstrates an actual
  AI-generated brief and records the model that produced it.
- Catalogue translation uses the real model (`TURNOS_FAKE_TRANSLATE=0`).
  It only ever sends the installation's own published service names, center
  descriptions and specialty labels — no visitor data — and a faked
  translation would show every English visitor `[en] Clínica médica`. The
  `translate_warmup` daemon fills the cache a minute after boot and deletes
  any `[<lang>] …` placeholders an earlier faked sweep left behind.
- Transactional email is off.
- The inherited WebTurno login, booking, cancellation, owner vault and watcher
  routes are unavailable, and the legacy watcher never starts.

## Access

Every surface displays one compact demo bar that states the data is fictional,
links to the patient account, doctor console and management panel, and exposes
the enabled ES/EN selector. `Gestión` opens `/admin/demo-access`, creates a signed
sandbox session and redirects to the real `/admin` interface. This route does
not exist unless demo mode is enabled.

The admin notice links back to the patient site and explains that changes may
be reset. Public visitors never receive or enter a shared password.

## Staying on the same build as production

The demo runs the same code as the production installation, so it has to ship
on the same push — a demo lagging behind is showing visitors a product that no
longer exists. If the demo is a second application on the same repository and
branch, confirm its own webhook actually fires; matching repo and branch is not
enough on its own. The reference deployment's gap and its fix are written up
in the private ops repo `humanrounds-pinamar`
(`<workspace>/human-rounds/humanrounds-pinamar/docs/deploy-coolify.md`,
moved out of this repo 2026-08-05), and `scripts/smoke_demo.py` is the
post-deploy check.

## The day rolls forward on its own

`scripts/seed_demo_all.py` writes everything relative to "now", so a seed run
once at deploy time is a snapshot of that day and nothing else: a week later
the day view is empty and the reports end a week ago. `demo_refresh.py` runs
the seed again once a day, just after local midnight, so the current day is
always a working day. It only ever starts on a demo installation, and
`TURNOS_DEMO_REFRESH_OFF=1` disables it.

A running process, boot log or `/health` response is not completion evidence.
After every complete refresh, PostgreSQL keeps one bounded operational record
for the latest attempt and last complete day. The existing admin-only System
surface is green only when the latest attempt completed assets, referral repair
and a read-back of the current demo day. Missing, partial, failed, stale or
unreadable records are visibly non-green. This status exposes no demo records,
configuration or credentials, and it does not exist outside demo mode.

The day itself is generated from the doctors' own agendas for that date, not
from fixed hours: every appointment lands on a slot that actually exists,
roughly one slot in seven stays free (there has to be something to give away
at the counter), what has already happened is marked present/absent, and the
last two are deliberately left unmarked so the day view shows its
"not registered yet" warning. A data package meant for a public demo should
therefore give its doctors agendas covering **every day of the week** —
otherwise the demo shows an empty day whenever a visitor arrives on an
uncovered one. In `packs/example-town` the weekdays carry most services;
Saturday mornings add pediatrics, nutrition, ENT and dermatology, and Sunday
mornings only what a public hospital plausibly runs then (pediatrics and
obstetrics at the maternity, general practice, traumatology). A doctor's own hours there are copied
from the agenda of the services that schedule them, so the doctor's public
page, the booking slots, the day queue and the printed day sheet show the
same hours. Services without an agenda (laboratory, X-ray) get appointments
only on the days their hours name: Monday to Friday.

Besides the day rollover, every tick of the same daemon (five minutes) runs
the **showcase keep-alive** (`seed_demo_all._asegurar_catalogo_demo` +
`_asegurar_turno_destacado`, wired in `demo_refresh._mantener_escenarios`).
It re-asserts the demo's own catalog overlay — the walkthrough service
(`medicina-general-riverside-clinic`) open every day 8–20, symptom capture on
for its doctor (`entrevista_habilitada`) — and keeps Sofía Herrera's
walkthrough alive at any hour: her same-day appointment with the demo doctor
stays approved and, while his agenda has a slot left, ahead of the clock (a
lapsed slot moves to the next free one of the same day; after the day's last
slot it stays where it was, on today's list — never tomorrow's, never a slot
outside the agenda — until the midnight reseed), and a visitor who cancels it
from the patient demo sees it back on its own slot within five minutes. Each
row is repaired in its own transaction, and a step that fails waits for the
next tick. The
overlay row is always rebuilt from the pack's current base item, so a pack
change flows through; her second, days-out approved appointment keeps «Mis
turnos» non-empty whatever a visitor cancels. A row that carries a sample
order image belongs to the person **on the image** (the reading, the row and
the account name the same person), so the referral card never shows one
name beside another's picture; an image that names one of the seed's own
patients is that patient — one person, one account. The overlay's `horario`
is a parsed string and names the days the way the pack does («Lunes a
domingo de 8 a 20 h»).

## Verification

Before publishing, verify:

1. `/api/demo` returns `demo: true`.
2. `/api/services`, `/api/doctors` and `/api/centers` contain only fictional
   records from `packs/example-town`.
3. `/admin/demo-access` redirects to `/admin` and `/api/admin/whoami` identifies
   the user as `demo` with the `admin` role.
4. The application has no HSI, email or WhatsApp credentials. Its dedicated,
   capped AI key handles the conversational assistant and the single fictional
   seeded questionnaire; referral/OCR and visitor intake remain deterministic.
5. `/app` redirects to `/`, while `POST /api/login`, `/api/book` and
   `/api/cancel` return `404 no_disponible_en_demo`.
6. The public and admin pages have no horizontal overflow at 360, 390, 768,
   834, 1024, 1280 and 1440 pixels.
7. `POST /api/chat` returns a real catalog-grounded response and never
   `Recibido (fake)`.
8. Sofía Herrera's seeded interview has 8 transcript messages, a non-empty
   clinical brief, and a real model id (never `demo-seed`, `fake`,
   `sin_clave_api` or `fallback`).
9. `/admin/demo-doctor-access` lands on `/admin/cola` («Hoy»), and Sofía
   Herrera's card there shows the «Síntomas» block and an «Iniciar consulta»
   that opens `/admin/consult` — at any hour of the day, not only before her
   slot.
10. From `/account/demo-access` the walkthrough appointment offers
   «Cancelar turno» and «Al calendario»; after cancelling it, it is back
   within five minutes (the keep-alive tick).
11. A request with an attached order image reads as the person on the image:
   the name in the reading's `paciente` field matches the picture, and an
   image that prints no DNI closes on the name («el nombre del paciente
   coincide»), never a wrong DNI.
