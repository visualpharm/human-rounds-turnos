# HSI Humanizador — Chrome extension

The front desk re-typed every «Pacientes de hoy» turno into HSI by hand
(professional → agenda → day and hour → DNI and sex → contact → Confirmar).
This Manifest V3 extension does that typing: one «Cargar en HSI» button per
row, and HSI's own «Nuevo turno» is filled on screen, step by step.

It drives HSI's interface in the operator's own logged-in tab. It holds no HSI
credentials and calls no HSI API, so it works wherever the operator can work —
including an HSI behind a captcha login.

## Three ways to work (settings: «¿Cómo cargo los turnos?»)

- **«Cargar el turno con un toque en la fila» (default).** The button per row
  loads the whole turno into HSI's «Nuevo turno».
- **«Solo copiar el DNI y abrir HSI».** The button becomes «Copiar DNI»: the
  document goes to the clipboard and HSI comes up. Nothing is ever typed into
  HSI in this mode — a load request from an out-of-date page is refused with
  the reason.
- **«Completar el paciente mientras escribo el DNI en HSI».** The operator
  opens the slot in HSI by hand and types the DNI; when the cursor leaves the
  box, the patient's half (sex, contact, coverage) fills itself if that DNI is
  exactly one of the turnos of the last «Pacientes de hoy» on screen — memory
  only, never on disk. No match, or two rows with the same document: nothing
  happens. The operator always wins: a field they filled is never overwritten.

## Rules it follows

- **Fill, don't save.** By default it stops at Confirmar and the operator
  presses it. "Guardado" is reported only on HSI's own evidence: its success
  notice about the turno while the dialog closes.
- **«Guardar solo» is earned and opt-in.** After a save HSI confirmed, the
  panel offers to press Confirmar itself next time — on the 1st, 2nd, 3rd,
  5th, 8th and 13th save, then every 20th. Only the operator's "Sí, guardá
  solo" turns it on; the settings page turns it off. Switching to another HSI
  address resets both the switch and the count.
- **Never a guess on a patient's record.** An ambiguous or unknown
  professional, several valid agendas, a taken slot, an unknown sex, or a
  question from HSI ("the patient already has a turno that day") is handed to
  the operator; the load resumes when the screen moves on.
- **A patient HSI does not have gets HSI's own alta, then the turno continues
  by itself.** The load presses the dialog's «Crear paciente», fills HSI's
  patient search with what the row carries (DNI, sex, first name, surname,
  birth date — only the empty boxes) and presses HSI's «Buscar» (a search, it
  only reads) in every mode. HSI lands on its own new-patient screen with the
  data prefilled; saving the patient is the operator's «Guardar», unless
  «guardar solo» is on. The moment HSI's own evidence says the patient exists
  (its success notice, or the new patient's profile), the same job goes back to
  loading the turno by itself and stops at Confirmar like always — nobody
  presses the row again. If the alta is left unfinished (the operator walks off
  the screen, closes the tab, ten minutes pass), the job ends with the reason
  and the row's button comes back. The stage survives HSI's full-page
  navigations; if the row lacks a datum the alta needs, the panel asks
  for exactly that datum and goes on with the answer (memory only) — nobody
  is sent back to Turnos to fix the row.
- **Coverage is chosen only when the row names it beyond doubt.** The contact
  step's coverage select lists the coverages the patient's record already
  holds; the row's `cobertura` is selected on an unambiguous match — over
  HSI's own preselect, which is a default, not the operator's hand. Without a
  clear match, HSI's value stands. «Actualizar Cobertura» is an action, not a
  coverage, and is never clicked by the load.
- **Patient data stays in the browser.** Nothing is logged or sent anywhere. A
  turno in transit lives in `chrome.storage.session` (memory only). Local
  storage keeps settings, the save count, the ids of saved turnos — and the
  recent-loads list in its reduced form: first name + surname initial, last
  three digits of the DNI, the hour, the outcome. Never a whole name, never a
  whole document. The whole DNI of a recent load lives in memory alone, for
  the popup's «Abrir en HSI».
- **One HSI address, chosen by the operator.** The Province's HSI
  (`https://shc.ms.gba.gov.ar`) is the default and comes first; the test copy
  (`http://localhost:8280`) is only ever an explicit choice. The manifest
  carries permission for the test copy only; any other address — the
  Province's included — is granted by the operator on the settings page's
  «Guardar», through Chrome's own permission prompt.
- **A turno goes to its own center's institution.** Each center is a different
  institution in HSI with its own number. Human Rounds keeps it on the center
  («Institución en HSI» in the panel's «Centros») and the row carries it as
  `hsi_institucion`; that number comes first, except on the test copy of HSI
  (the numbers are the real HSI's). A row without one — a center not filled
  in, or a site that does not send it yet — falls back to this extension's own
  table (`INSTITUCIONES` in `lib/shared.js`, numbers from the municipality's
  HSI administrator, 2026-10-27, matched by the row's `centro_id` or
  `centro`), else the one the operator chose in the settings («Otra, escribo
  el número»), else the one they picked in HSI for that center the first time
  — remembered per center (`institucionPorCentro`). Switching to another HSI
  forgets the per-center picks. The settings' «i» explains where HSI shows the
  number (`…/institucion/<n>/…`) and opens the desk's guide, which ships inside
  the extension (`guia/guia.html#institucion`).

- **A new Human Rounds site never switches the extension off.** The
  manifest names pinamar.humanrounds.org, the demo and the local copy. Any
  other site (`TURNOS_OPCIONALES` in `lib/shared.js`: salud.pinamar.gob.ar)
  rides on the optional permission, because a new required host makes Chrome
  disable the extension for everyone who has it until they accept again. The
  operator allows it once — «Activar en salud.pinamar.gob.ar» in the popup,
  or that choice under «¿Dónde ves los turnos?» on «Guardar» — and the row
  script is registered for that site's `/admin/` (an open tab gets it at once).
  «¿Dónde ves los turnos?» is one place, radios like the HSI block: a known
  site, or «Otra dirección» with the operator's own address (`turnosPropio`),
  allowed on «Guardar» the same way. Settings saved by the old checkboxes map
  onto it (`turnosElegido`: an address of their own wins, else the site).
  The panel's «Turno creado» offers the extension on both lists.
- **Every place a turno is made offers the same control** (0.6.0). Besides the
  rows of «Pacientes de hoy», the panel draws the extension's hook
  (`[data-hr-hsi]` + `data-hr-hsi-dia`, `static/platform/hsi-sync.mjs`) on
  «Turno creado», on «Recién aprobadas» above Solicitudes (the approvals made
  on this page) and in the detail of an approved request dated today or later.
  The extension paints its states on it: a turning icon while it loads, a
  check once HSI saved it, the reason when it failed.
- **«Cargar en HSI apenas apruebo o creo un turno» (settings, off by
  default).** A control marked `data-hr-hsi-auto` — a turno created in «Crear
  turno» or approved in Solicitudes — loads by itself and reads «Envío
  automático a HSI». HSI takes one turno at a time: the others wait as «en
  cola» and go one after another, each stopping at Confirmar like a click
  would (unless «guardar solo» is on). Not offered in «Solo copiar el DNI»;
  turned on, it acts on turnos made from then on, never on what was already on
  screen, and never twice for the same turno.
- **What changes after the load is passed on too** (0.7.0). The computer keeps,
  for 60 days, only the *place* of every turno it put in HSI (day, hour,
  doctor; `turnosEnHsi` in the local storage, never a name or a document). On
  load, every five minutes and when the tab comes back, the list asks
  `GET /api/admin/day-queue/changes?ids=…` (the panel's own log shows each
  ask) and compares. A box in the corner lists what differs, one button each:
  - **Cancelled** (by the desk, by the patient, or a «No» in WhatsApp) →
    «Cancelar en HSI…». It opens the turno in HSI's agenda (hour on the grid,
    then the DNI in the window — a turno of someone else is never touched),
    presses «Cancelar turno», writes a fixed reason with no data and **stops:
    «Si, cancelar turno» is always the operator's**, even with «guardar solo».
  - **Moved by the patient** → «Mover en HSI…»: the old place is cancelled
    like above and the turno is loaded at the new one like any «Cargar en
    HSI». If the cancel went through and the load did not, the box offers the
    load again («Aprobado de nuevo»).
  - **Back from the waiting list** is the same request reassigned: it comes
    as «Mover» or, if it had been cancelled in HSI already, «Aprobado de
    nuevo» — the ordinary load.
  - **Showed up** → «Marcar presente en HSI…»: the turno goes to
    **«Confirmado»** (the only attendance state HSI enables; «Ausente» and
    «Atendido» are disabled in its screen for everyone, so they cannot be
    marked from here). With «guardar solo» the final «No, confirmar turno» is
    pressed by the extension, otherwise it stops.
  Success is proven only by HSI's own notice («El turno fue cancelado
  exitosamente», «Estado de turno actualizado a Confirmado exitosamente»). A
  turno nobody loaded from this computer is never touched, and the server push
  to HSI (when it is the active driver) stays the only path for those.
- **The question under «Cargar en HSI» is not lost** (0.7.4). When the press
  needs an answer (no sex or no name on the turno), the question stays on top
  of the page even if the queue repaints meanwhile.
- **What has passed stays out of the box** (0.7.3). HSI never changes a turno
  whose hour has passed, so a place behind the installation's hour (the panel
  answers it as `ahora`; an older panel leaves this computer's clock) is never
  cancelled, moved or loaded: a move keeps only the half still ahead, presence
  is marked until that day ends. A refusal from HSI for a turno that passed
  meanwhile is no error: the row goes with «Ya pasó, queda así en HSI». Any
  other refusal keeps its reason and adds **«Ocultar»**: the desk leaves HSI as
  it is. It hides the row on this computer at once and, through
  `POST /api/admin/day-queue/changes/<id>/hide`, on every computer of the desk,
  with a line in the turno's history; the row comes back only if the turno
  changes again.

## The turno window, tidied (0.7.1)

On HSI's own window of a turno (`mat-dialog-container app-appointment`),
`lib/hsi-tidy.js` + `content-hsi.css` show only what is there: the patient's
name is the link to the patient (HSI's «Ir a Paciente» stays in the page,
hidden, and the name presses it), «Editar información del paciente» is gone,
«Cobertura médica» and «Teléfono» appear only when HSI has them (with neither,
the turno takes the full width), the internal « - ID n» leaves the DNI line,
and the «Turno» heading, the usual «Presencial» and who booked it are hidden.
Only the visible layer changes: elements get `data-hr-oculto`, nothing of
HSI's is removed or edited, and the four state buttons, «Imprimir turno»,
«Cancelar turno» and the observation/label links stay (the extension drives
them by position). A modality other than «Presencial» is still shown.

## The toolbar icon

While a load runs, a small sync mark on the icon; clicking it opens the
recent loads (newest first, at most twenty). Each entry says how it ended —
«Guardado», the reason it did not save, or «DNI copiado» — and a failed one
offers «Abrir en HSI», which brings HSI up and copies the whole DNI while the
browser still remembers it (it says so once it does not).

## Layout

| File | Role |
|---|---|
| `content-turnos.js` | «Pacientes de hoy»: the button per row, the row's status |
| `background.js` | carries the turno to the HSI tab and the result back |
| `content-hsi.js` | the loader: steps, panel, hand-offs to the operator |
| `lib/hsi-dom.js` | HSI's selectors (Angular Material) in one place |
| `lib/shared.js` | settings and the pure matching/date helpers (unit-tested) |
| `options.*` | settings page |
| `popup.*` | the toolbar popup: recent loads, reduced form only |

The row's data comes from `GET /api/admin/day-queue`, which carries `sexo`
because HSI finds a patient by document **and** sex, and `nacimiento` for the
patient alta.

## Test

- Unit: `node --test tests/turnos-hsi-extension.test.mjs` (repo root).
- End to end, against the HSI **test** copy only — it books appointments:
  `HSI_ADMIN_PASS=… node extensions/hsi-humanizador/test/e2e-hsi.mjs [outDir]`.
  It loads the extension in Chromium, serves a «Pacientes de hoy» fixture with
  the real row hooks, and checks every rule above against the live HSI screen.
  `test/e2e-hsi-cambios.mjs` (same command) covers the changes after the
  load: cancelled, moved, back from the waiting list, showed up.

HSI selectors were taken from v2.71.2 (test copy) and compared with a screen
recording of v2.77.6. If HSI changes a screen, the loader stops with a message
instead of typing into the wrong place; the fix goes in `lib/hsi-dom.js`.

## Package

`python3 scripts/build_extension.py hsi-humanizador` → `extensions/dist/hsi-humanizador.zip`
(for the desk) and `hsi-humanizador-store.zip` (for the Chrome Web Store; listing
texts in `store/`, upload with `scripts/publish_extension.py`).
An upload is not a publication: without `--publish` the script leaves a draft,
and even a submitted version reaches Web Store users only after the review
passes. Check the item's state before telling anyone the version is out.
The unit suite fails when the zip is older than the sources. The desk's guide
(Spanish) is `extensions/dist/guia-turnos-a-hsi.pdf`, built from
`guia/` by `scripts/build_hsi_extension_guide.mjs`.
