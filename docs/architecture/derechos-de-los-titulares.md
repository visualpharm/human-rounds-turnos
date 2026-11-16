# Data-subject rights — find, export, correct, erase (Ley 25.326 arts. 14-16)

The privacy policy promises «Podés pedir ver tus datos, corregirlos, actualizarlos o que los
borremos» (`app/patient_web/legal.py`, sections 7-8). This is the code that answers such a request.
Every legal statement below is a **working reading, to be confirmed by the hospital's counsel**
(open questions at the end).

| Right | Article | Deadline | What the code does |
|---|---|---|---|
| Access | art. 14 | 10 calendar days from the request; free, again after 6 months unless a legitimate interest | `scripts/subject_export.py`, the admin action `GET /api/admin/patients/{dni}/data-export` |
| Rectification / update / suppression | art. 16 | 5 working days; suppression does not proceed when a legal duty to keep the data exists (art. 16 inc. 5); the data is flagged «under review» while a correction is verified (art. 16 inc. 6) | the ledger (`under_review`), `app/core/subject_erase.py` / `scripts/subject_erase.py` (erase / anonymise / block) |
| Clinical-record retention | Ley 26.529 art. 18 | 10 years (per the owner) | why care records are kept, not deleted — see the `disputed` entries |

## The pieces

- **`app/core/personal_data.py` — the registry.** One `Entry` per place a person's data lives (a table, or
  one part of it: `chat_messages` has a web, a WhatsApp and an interview part): the personal columns, how
  to FIND one person's rows (`find`: selectors by DNI, phone, email, chat key, parent rows, free-text
  sweep), the columns that never leave (`secret`), and what erasure does (`erase`: `delete` / `anonymise` /
  `keep`, with `blank` for the columns an anonymised row loses). `DNI_MOVED_COLUMNS` in `app/core/db.py` is
  the DNI registry `change_dni` follows; this one is wider (phone-, email-, request-id- and free-text
  keyed places) and is what the tools read. **Adding a table or a personal column = adding it to the
  registry in the same change** — `tests/test_personal_data.py` reads the live schema and fails when a
  table is in neither `REGISTRY`, `STAFF_TABLES` nor `NO_PERSONAL_DATA`, when a column that looks like a
  DNI / phone / email / name / birth date / address / IP is in no entry, when a secret-looking column
  would be exported, or when a child table is listed after its parent (erasure runs in registry order,
  children first, so a future `FOREIGN KEY … RESTRICT` never trips it).
- **`app/core/subject_export.py` — the copy (art. 14).** READ ONLY (the whole export is one read-only
  transaction). One JSON file per registry entry, plus the files on disk (referral images / PDFs, the
  profile photo, the person's own lab files — soft-deleted ones included while they still hold bytes; a
  file a guardian uploaded for a dependent is the dependent's), plus `manifest.json` listing **every place searched, the empty ones included**, what was
  withheld and why, and what is not reachable from the database (anonymous web chat, HSI, external
  processors, backups). Tokens, password hashes and one-time codes are dropped by column. The DNI, phone
  and email appear masked in the manifest.
  - A phone or email that **another account also uses** is left out of the search (the WhatsApp thread
    and the one-time codes are found by phone only) and the manifest says so; `--confirm-shared` is the
    operator's explicit word that it is this person's.
  - A **dependent is another person**: the requests a guardian made for a dependent are counted, not
    handed to the guardian (the dependent gets their own copy on their own request); the lines other
    accounts hold about a **minor** are not handed to the minor.
  - Rows are found by DNI, by phone / email / chat key (only for the places keyed by them), through their
    parent rows (a request's events), or — for the HSI journals, the search box and the backup file
    names — by a **text sweep** for the DNI as a whole number. The sweep is a sequential scan: on a large
    HSI journal run the export (and later the erasure) off-hours. A waiting-list row is found by email only
    when it has no DNI of its own.
  - The staff access log is withheld (evidence of staff access, not the person's data); whether to
    disclose its lines is Q4.
- **`scripts/subject_export.py` and the admin action.** The script writes one zip (mode 0600) and prints
  counts only. The admin action (`GET /api/admin/patients/{dni}/data-export`, administrator only, permission
  `usuarios`) streams the same zip. Both leave an `exportacion` row in the clinical access log **before**
  anything is built, and refuse (503) when that row cannot be written; an unknown patient is a 404 with no
  row. In the panel it is «Descargar todos los datos del paciente», a secondary button next to «Ver quién
  abrió sus datos» on the patient ficha, shown only with `ver_accesos` (the same permission); product owner,
  q779598. The access-log lines stay out of the export until Q4 is answered.
- **`app/core/subject_requests.py` + `scripts/subject_requests.py` — the ledger (art. 14-16).** Alembic
  `0028_data_subject_requests`: `data_subject_requests` (kind, day received, due day, channel, how the
  identity was verified, status `received / under_review / answered / refused`, outcome) and
  `data_subject_request_events` (the trail, **append-only by trigger**). The person is stored as the HMAC
  of the DNI (`access_log.dni_hash`), never the DNI. Due day: access = received + 10 calendar days;
  rectification / erasure = received + 5 **working** days (Saturdays, Sundays and the installation's
  holidays, `site_holidays`, skipped; day 1 is the next working day). Rules the ledger enforces: nothing is
  answered before the identity check is recorded; a refusal must state its legal basis; a closed request
  does not reopen. `is_under_review(dni)` is the art. 16 inc. 6 flag, `overdue()` the list of what is
  late, `next_free_access_on(dni)` the six-month rule. The clock never stops here (whether an incomplete
  request stops it is Q9).

- **`app/core/subject_erase.py` + `scripts/subject_erase.py` — erase / anonymise / block (art. 16).**
  **Dry-run by default** (`plan()`: counts per registry entry, the files, what blocks). `apply()` is the one
  function that writes and it refuses without (a) a ledger request of kind `erasure` for **this** person
  with a recorded identity check, (b) a named authorisation (`--authorised-by`: who in the hospital, or which
  counsel's answer, allows it). Refused with nothing written while the person has live appointments (cancel
  them first, with the patient's agreement), an appointment still to be sent or cancelled in HSI (the resync
  daemon would push an empty identity), an HSI intent still pending, or an unresolved sync conflict. **One
  request, one erasure**: a request that already has its `erased` event is refused (a DNI registered again
  later is a new person). One database transaction; the files go after it commits (a file that can't be removed is reported and
  noted in the ledger, never skipped silently); the ledger gets an `erased` event with the counts —
  `subject_requests.erasures()` is the list of people to erase again from a database restored from
  an older backup (`scripts/reapply_erasures.py`, below). The clinical access log is never touched.
  - `--mode anonymise` (recommended): class A (`delete`) goes — sessions, tokens, chats and their AI memory,
    waiting list and offers, the person's dependents' links, caches (summaries, lab readings), HSI mirrors,
    search-box lines with their DNI, the profile photo. What may be the care record stays **without the
    person**: the DNI-keyed identity first moves to a random `ANON-<16 hex>` key with `db_accounts.change_dni_in` (the
    mechanism of a DNI correction, so nobody who registers with that DNI later inherits it), then the personal
    fields are emptied (`blank`): the account (name, birth date, contact, email, address, coverage, RENAPER
    claims, password hash, tokens, `active = false`), the booking's copy of name / birth date / sex / contact /
    note / AI reading / referral file, the typed name on interviews, the bytes and text of lab uploads
    (the stub row stays), a confirmed HSI payload. What stays, under the random key: interviews and their
    transcript, consultations (prescriptions, orders), the health profile, imported history, attendance and
    appointment events, the booking rows (ids, service, dates, status, HSI ids). Steps run children first
    (registry order). Two checks fail — and roll everything back — instead of letting a registry bug through:
    after the blank step, any `blank` column that still holds a value; after the move, any entry that
    still finds the person. A file a guardian uploaded for a dependent is the dependent's and is not touched
    (only its `uploaded_by_dni` follows the move).
  - `--mode block`: the same class A, plus the account is deactivated and every token revoked, but the care
    records stay as they are **under the person's own DNI** (restricted use, not anonymised); the HSI
    mirrors and the referral files are kept.
  - **After a restore — `app/core/erasure_reapply.py` + `scripts/reapply_erasures.py` (P5).** An erased person
    survives in every older backup, so the erasures are carried out again on the restored database before the
    app serves anything. The tombstones are the `erased` events of the ledger (`subject_requests.erasures()`:
    person as an HMAC of the DNI, mode, instant) of the restored database and of every `--from-file` written
    by `--export` on the live installation (owner-only, with a fingerprint of the access-log key — a file from
    another key is refused; kept with the recovery key: a backup older than the erasure has no ledger line of
    it; after a recovery, export again with the old file as `--from-file`, the recovered ledger does not hold
    its lines). Each DNI-shaped value of a registry DNI column, and each `dni:<dni>` chat key, is hashed and
    compared with the tombstones. **Dry-run by default** (read-only transaction), `--apply` writes: one
    transaction per person with the same steps and proofs as the erasure (`subject_erase.carry_out`), files
    after the commit (a file that fails is counted and not retried: remove it by hand), an `erasure_reapplied`
    line in the request's trail when the restored ledger has it. Idempotent (`anonymise` moves the DNI to a
    random key, `block` finds nothing left to change). A DNI erased, registered again and erased again is two
    people: the erasure that applies to an account is the first one after its `created`; an account newer than
    every erasure of its DNI is a new person and is left alone. A person with a live appointment, a pending
    HSI intent or an unresolved sync conflict is **not touched** by default (the erasure's own rule; its
    proofs do not look at those HSI rows): counted as `blocked`, exit code 2 on `--apply`; resolve what the
    restore brought back and run again, or pass `--despite-blockers`. Prints counts only. A restore into an
    installation with another `TURNOS_ACCESS_LOG_KEY` finds nobody: keep the key. Not covered: places found
    only by phone, email or free text (a person with nothing left under their DNI), legacy B2 file versions
    and the processors (manual plan, Q6).
  - The tool does **not** erase: the copies in encrypted backups (they expire by rotation; re-apply after a
    restore), the copies at external processors, the anonymous web chat (an IP is not an identity), HSI
    itself. The report says so.
  - **Doctors' and centers' photos are not in this tool** (2026-11-06). Its subjects are people found by DNI;
    staff and doctors as data subjects are Q8. A doctor's portrait and its private originals
    (`data/medico-fotos/<id>.webp`, `data/medico-fotos/.originals/<id>/`) go with the photo's «Quitar», or
    when the doctor is deleted for good (nothing names it: `admin_content.delete_item` calls
    `doctor_photos.erase` before the row goes; a disabled doctor keeps them); a center's photo
    (`data/centro-fotos/<slug>.<ext>`) the same way. `reapply_erasures.py` does not carry such a deletion
    over a restore: the restored database has the doctor again and the restored volume its photo, so the
    two stay consistent, and the deletion is repeated in the panel.

## Procedure for an operator

1. **Register** the request the day it arrives: `scripts/subject_requests.py add <access|rectification|erasure>
   --dni … --by … [--received …] [--channel …]`. It prints the due day.
2. **Verify the identity** (DNI shown in person, or a signed copy, or logged into the account) and record how:
   `identity <id> --how …`. If it cannot be verified, answer within the deadline by `refuse <id> --basis
   "identity could not be verified"`.
3. **Access:** `scripts/subject_export.py --dni … --request <id>` (or the admin action), read the manifest,
   write the cover note (what is included, what is not and who to ask, the six-month rule), hand the zip to the
   registered email or in person, then `answer <id> --outcome …`.
4. **Correction:** `review <id>` flags the data; export first and keep the «before» in the ticket; correct the
   account in Pacientes → ficha, then the copies (`booking_requests`, `interviews.entered_*`) — a correction
   is still a multi-table operation; a wrong DNI is `db_accounts.change_dni`; HSI holds its own copy, which is the HSI
   administrator's step. `answer <id>`.
5. **Erasure:** register it (`add erasure`), verify the identity, `scripts/subject_erase.py --dni …` (the dry-run),
   read the plan with the hospital's authorisation in hand, cancel what blocks, then `--apply --request <id>
   --authorised-by "…"`; tell the person what was erased, what is kept and why (art. 16 inc. 5), and that
   copies in backups expire by rotation; refresh the tombstone file (`scripts/reapply_erasures.py --export …`); `answer <id>` (or `refuse <id> --basis …` when nothing can go).
6. **Weekly:** `scripts/subject_requests.py list --overdue` (exit code 1 when something is late).

## Open questions for the hospital's counsel

- **Q1** What is the «historia clínica» in our database? HSI is the record and our rows are copies; which of
  `booking_requests`, `interviews` (+ transcript), `consultations`, `patient_health_profile`, uploaded labs and
  `clinical_history_records` must be kept 10 years (Ley 26.529 art. 18), counted from the last visit or from creation?
- **Q2** Delete or restrict? For what must be kept: block (no use outside care) vs anonymise (names blanked, ids kept
  to reconcile with HSI). Is «anonymised» acceptable when the HSI id still re-identifies the person in the
  hospital's own system?
- **Q3** Who is the responsible party (art. 6) on salud and on pinamar.humanrounds.org, who receives and signs
  requests, who may run the queries? The channel to write into the policy. (2026-10-27: on salud the policy
  names the controller from `SITE_ID_DATA_CONTROLLER` — the Municipalidad de Pinamar, Secretaría de Salud — and
  sends requests through the channel its §8 explains, with no placeholder on the public page; the open question for
  counsel stays in `legal.PENDIENTES_ASESORIA`; see legal.md.)
- **Q4** Keep the access log despite an erasure request (we propose yes: evidence, append-only, 10 years)? May the
  person be shown who opened their data, or only counts / roles?
- **Q5** HSI: who is the contact for corrections and erasure there? May we delete our mirror knowing the inbound
  sync recreates it?
- **Q6** Backups and processors: is it acceptable that an erased person remains in encrypted backups until rotation?
  Are there data-processing agreements with the AI, e-mail, WhatsApp, voice and error-tracking providers?
- **Q7** Identity standard for remote requests, for deceased persons and for minors.
- **Q8** Staff and doctors as data subjects: same procedure?
- **Q9** Does an incomplete request stop the 10 / 5-day clock? What must a refusal cite?
