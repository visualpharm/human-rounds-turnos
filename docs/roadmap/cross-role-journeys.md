# Cross-role journeys

Every other `test/e2e/*.spec.mjs` file drives ONE screen. The journeys below hand
one appointment (or one decision) across several people through a real server,
a throwaway Postgres database and Chromium, and assert what each OTHER person
sees afterwards. J10 (the receptionist's walk-in) was the first; J11-J16 follow
its numbering and the style of [catalog-served-journeys.md](catalog-served-journeys.md).

Each journey is one Playwright Test file, `test/e2e/journey-jNN-<name>.spec.mjs`,
with its own database (`test.use({ appOptions: { dbName } })`, like every spec).
They share `test/e2e/lib/journey.mjs` -- the fixtures `test` extended with
`journey` (the staff accounts, the doctors' interview switch and the local
receiver for the mail provider and the WhatsApp gateway, all started with the
app), `role()` (one browser context per person, page errors collected), staff
login and the desk's request-detail helpers -- and `test/e2e/journey_fixture.py`
(seeds and read-backs through the app's own functions, like `fixture.py`). A
journey is a few `test(...)`s whose hand-overs are `test.step`s.

Rules every journey keeps:

- **Fictional data only** (the `example-town` pack, throwaway DNIs and logins).
  The AI is the app's own fake (`TURNOS_FAKE_AI=1`). Mail and WhatsApp go to a
  receiver on `127.0.0.1` selected by the same environment keys production uses;
  no real key exists, nothing leaves the machine.
- **Correct behaviour is the assertion.** Every check below is a gate. A defect a
  journey finds that has no fix on `main` yet is written as the correct
  behaviour and marked `test.fixme(<reason naming the defect and its fix>)`, so
  it is listed in the report and one line is deleted when the fix lands --
  never asserted as today's behaviour (that would pin the bug), never
  `test.skip` of a setup.
- **No sleeping.** Waits are retrying expects, events or polls on observable
  state; the two server caches that delay what a patient sees (the catalog
  overlay, about 3 s, and freed slots) are waited out by loading the page until
  it shows the state (`gotoUntil`, `expect.poll` over a reload). Pinned for the
  whole suite by `test/e2e-spec-isolation.test.mjs`.

| ID | Given | Action | Expected |
| --- | --- | --- | --- |
| J11 | A signed-in patient, the front desk, two doctor accounts, an admin | The patient books Cardiología online; the desk approves her to a cardiologist; she answers the pre-visit interview; the doctor consults and confirms; she opens the links with no session | The desk is offered the service's cardiologists; the approval is one stored fact (doctor id, name, time) the patient is told by email; the doctor finds her in Mis pacientes and Mis turnos; the prescription and order name that doctor, the patient, what was prescribed; a forged link shows nothing. |
| J11 | The admin renames the doctor after the approval | The doctor and the patient open their screens | The doctor keeps his patient; the patient's appointment reads the new name. |
| J11 | A turn whose phone differs from the patient's account | The doctor opens Mis pacientes and Mis turnos | Both lead with the number given for the turn and show the account's next to it, saying whose it is. |
| J12 | A doctor with an approved appointment today and a finished interview | The doctor confirms the visit from the day queue, then reloads it, reopens the queue, confirms again, and finally replaces it on purpose | The visit reopens showing what was issued; the queue says it was attended; a second confirmation is refused and leaves one prescription; an explicit replacement marks the old `/r/` and `/o/` links as replaced and points to the new ones. |
| J13 | An admin-made service whose hours belong to its doctor; the schedule manager; two patients; the desk | The manager builds the doctor's window; patient A books, the desk approves, A moves it and cancels; patient B watches | Nothing is offered before the window exists, exactly its times after; a held time is not offered to B; a moved or cancelled time is offered again. |
| J13 | A pending request | The patient cancels before the desk decides | The time is offered to the next patient again. |
| J14 | A doctor with an approved appointment ahead; another with a pending request parked on him; another named only by an interview | The admin disables each | The first is refused with the number of appointments; the second is disabled and the desk gets the request back unassigned; the third is disabled, never deleted. |
| J14 | A patient with an approved appointment, a pending request, a session and a calendar link | The desk archives the patient | The desk is told about the appointments ahead and they are cancelled; the patient can no longer get in and the calendar link is dead. |
| J15 | Two doctors, each with a patient; a hand-made clinical role with no doctor behind it | Doctor A walks every door to B's patient (roster, agenda, day queue, interview page and API, request by id, AI summary by DNI) | B's patient is absent everywhere and a direct knock is the same 404 as for an id that does not exist; the hand-made role sees no roster, no doctor picker and no interview of B's. |
| J16 | A signed-in patient, the desk, the local mail/WhatsApp receiver | The patient uploads a referral, picks a time and sends; the desk reads what the AI read and approves; the day before the reminder sweep runs; the patient follows the one-click link | The desk sees the AI reading and the photo; email and WhatsApp name the same service, doctor and time as the approval; the reminder is sent once per channel and a second sweep sends nothing; the answer shows as confirmed in the day queue. |
| J16 | A study with no specialist at its center | The desk opens the request | No doctor of another specialty is preselected or offered. |

## Fixes the journeys wait for

None: every fix the journeys were written against is on `main`, so each check is
a plain gate -- the one valid prescription per visit (K1, J12), the desk offered
only the service's pool of doctors (K2, J13/J16), a doctor reaching only his own
patients (K10, J15), a renamed doctor still having his patient (K3, J11), the
turn's phone leading (K4, J11), the schedule manager's doctor card opening
without a refused request (K8, J13), and a doctor or a patient taken out of
service with live appointments (K6, J14).

## Findings the journeys made

- **A time held by a cancelled request was never released (N1, fixed).**
  `cancel_request` (the patient's own cancel) did not call
  `holds_release_booking_request`, unlike the desk's cancel and the reject: a
  cancelled pending request, or an approved one moved with `reprogramar` and then
  cancelled, left its time hidden from every other patient until the time itself
  passed. Fixed on `main` in `b70a0c00`; J13 now holds the two checks as plain
  gates.
