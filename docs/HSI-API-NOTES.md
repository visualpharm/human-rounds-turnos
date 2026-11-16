# HSI REST API — notes for Human Rounds — Turnos integration

Target: **`historiaclinica/hsi:v2.71.2`** at `http://localhost:8280`, Postgres
container `postgresql` (db `hsi-db`, user `postgres`). Institution id **1**
("Hospital Sana Demo"). Every endpoint below was captured empirically by driving
the real Angular UI + sniffing traffic, then re-validated with plain HTTP. All
paths are real `/api/*` (the SPA serves `index.html` for anything else).

Two path roots coexist — **watch the singular/plural**:
- `/api/institution/{id}/...`  (singular) — professionals, sectors, specialties.
- `/api/institutions/{id}/medicalConsultations/...` (plural) — the turnos module
  (diaries, appointments, masterdata).

---

## 1. Auth

**`POST /api/auth`** — body `{"username": "...", "password": "..."}`.
- Response body is literally `true`. The JWT arrives as an **httpOnly cookie**
  `token` (Set-Cookie, `Path=/api`, `Secure`), plus a `refreshToken`
  (`Path=/api/auth/refresh`, 48 h).
- **No altcha is enforced** on this instance for `admin@example.com`, so a scripted
  POST works — no browser needed. (The sibling `sana` project drove a browser only
  because it assumed altcha; not required here.)
- Send the token back on every call as either `Cookie: token=<jwt>` **or**
  `Authorization: Bearer <jwt>`. Access token TTL ≈ 30 min; just re-`login()`.
- `hsi_client.py` reads the token from the `Set-Cookie` header and reuses it.

Whoami / permissions: **`GET /api/account/permissions`** →
`{"roleAssignments":[{"role":"ADMINISTRATIVO","institutionId":1}, ...]}`.

---

## 2. Roles & the "institution scoping" gotcha (READ THIS)

Backoffice role admin (all under `INSTITUTION_ID=-1`):
- `GET /api/backoffice/roles` → catalog. Relevant ids: **5** Administrativo,
  **9** Administrador agenda, **3** Especialista Médico, **4** Profesional de la salud,
  **2** Administrador, **28** API Turnos.
- `GET /api/backoffice/users/1` → the admin user + its roles.
- **`POST /api/backoffice/userroles`** body `{"userId":1,"roleId":9,"institutionId":1}`
  → grants a role (200). (POST role 2 returns 412 — domain-level, not needed.)

To reach the turnos module the admin needs, at institution 1: **role 5
(Administrativo)** to book turnos + manage patients, and **role 9 (Administrador
agenda)** to create diaries. The sibling `setup-institution.mjs` already granted 5;
we add 9.

**THE non-obvious part — a professional is only "bookable" if their `person` is tied
to the institution through a user account.** A healthcare_professional row +
profession + specialty is **not enough**: `GET /api/institution/1/healthcareprofessional`
returns `[]` until the professional's `person` has a `user_person` link to a `users`
row that holds a **clinical `user_role` at that institution** (role 3 or 4). The
`v_clinical_specialty_institution` view joins
`healthcare_professional → person → user_person → user_role(institution_id)`.
`scripts/tools/seed_hsi_demo.py` seeds users + user_person + user_role for exactly this reason.

---

## 3. Patients

- **Exists:** `GET /api/patient/exists?identificationTypeId=1&identificationNumber=<dni>&genderId=<g>`
  → `true` / `false`.
- **DNI → patient id:** `GET /api/patient/minimalsearch?identificationTypeId=1&identificationNumber=<dni>&genderId=<g>`
  → `[patientId, ...]` (empty if none). `genderId` is **required** (400 without it).
  This is the reliable lookup for `ensure_patient`. (`/api/patient/search?searchFilterStr=<json>`
  exists but is a fuzzy person/RENAPER-style search and returned `[]` for a known DNI —
  don't use it for id lookup.)
- **Create minimal patient:** **`POST /api/patient/institution/{id}`** → returns the
  new patient id (201). Minimal body that works:
  ```json
  {"birthDate":"1980-05-05T03:00:00.000Z","firstName":"JUAN","lastName":"PRUEBA",
   "genderId":2,"identificationTypeId":1,"identificationNumber":"99887766",
   "countryId":14,"typeId":7,"auditType":"UNAUDITED",
   "generalPractitioner":{"fullName":null,"phoneNumber":null,"generalPractitioner":true},
   "pamiDoctor":{"fullName":null,"phoneNumber":null,"generalPractitioner":false},
   "fileIds":[]}
  ```
  (Every other field can be `null`.) `typeId:7` = the patient type HSI uses;
  `countryId:14` = Argentina. RENAPER is not consulted on this path.

Catalogs: `GET /api/person/masterdata/genders` → 1 Femenino, 2 Masculino, 3 X.
`GET /api/person/masterdata/identificationTypes` → 1 DNI (also CI/LC/LE/Pasaporte...).

---

## 4. Diaries (agendas)

Prerequisites for a diary: a **healthcare professional** (bookable, see §2), a
**clinicalSpecialty** the professional is associated to, a **sector** and a
**doctorsOffice** (consultorio).

- **Professionals:** `GET /api/institution/{id}/healthcareprofessional`
  → `[{id, licenceNumber, firstName, lastName, identificationNumber, fullName}]`.
- **Professional's specialties:** `GET /api/institution/{id}/clinicalspecialty/professional/{hpId}`
  → `[{id, name}]`.
- **Sectors of a type:** `GET /api/institution/{id}/sector/sectoroftype/1` (1 = Ambulatorio)
  → `[{id, description, hasDoctorsOffice}]`.
- **Diaries of a professional:** `GET /api/institutions/{id}/medicalConsultations/diary?healthcareProfessionalId=<hpId>&includeExpired=false`
  → `[{id, clinicalSpecialtyName, doctorsOfficeDescription, startDate, endDate, appointmentDuration, isExpired, ...}]`.
  There is **no unfiltered list** (`GET .../diary` with no params → `[]`); enumerate
  professionals then fan out. `list_diaries()` does this.
- **Diary detail:** `GET /api/institutions/{id}/medicalConsultations/diary/{diaryId}`
  → includes `diaryOpeningHours[].openingHours = {id, dayWeekId, from, to}` and
  `appointmentDuration`. The `openingHours.id` is the **`openingHoursId`** you must
  pass when booking. `dayWeekId` is **1 = Monday … 7 = Sunday** (== Python `isoweekday()`).

- **Create diary:** **`POST /api/institutions/{id}/medicalConsultations/diary`**
  → returns the diary id. Captured payload:
  ```json
  {"appointmentDuration":30,"healthcareProfessionalId":1,"doctorsOfficeId":1,
   "clinicalSpecialtyId":131,"startDate":"2026-07-08","endDate":"2026-09-30",
   "institutionId":1,"automaticRenewal":false,"includeHoliday":false,
   "professionalAssignShift":false,"alias":null,"ageRangeList":[],
   "diaryAssociatedProfessionalsId":[],"careLines":[],"practicesId":[],
   "diaryLabelDto":[],"bookingRestriction":null,"predecessorProfessionalId":null,
   "hierarchicalUnitId":null,"protectedAppointmentsPercentage":null,
   "diaryOpeningHours":[
     {"openingHours":{"dayWeekId":1,"from":"08:00:00","to":"12:00:00"},
      "medicalAttentionTypeId":1,"onSiteAttentionAllowed":true,
      "externalAppointmentsAllowed":false,"protectedAppointmentsAllowed":false,
      "regulationProtectedAppointmentsAllowed":false,
      "patientVirtualAttentionAllowed":false,
      "secondOpinionVirtualAttentionAllowed":false,
      "overturnCount":0,"referralOrderRequired":null,"ageRangeList":[]}
   ]}
  ```
  One `diaryOpeningHours` entry per weekday. `medicalAttentionTypeId:1` = Programada
  (`GET .../medicalConsultations/masterdata/medicalAttention` for the enum).
  Opening hours must fit inside the doctorsOffice `openingTime..closingTime`.
  HSI rejects `overturn:true` when the matching opening range has
  `overturnCount:0`. The demo Cardiología diary uses `overturnCount:2`; the
  idempotent seed updates existing opening rows as well as newly created ones.
- **Delete diary via REST is blocked** (`DELETE .../diary/{id}` → 400). If you must
  remove one, do it in the DB (delete `appointment_assn`, `appointment`,
  `diary_opening_hours`, `diary_booking_restriction`, then `diary`, in that FK order).

---

## 5. Appointments (booking a turno)

- **Existing turnos in a diary/date range:**
  `GET /api/institutions/{id}/medicalConsultations/appointments/diary/{diaryId}?from=YYYY-MM-DD&to=YYYY-MM-DD`
  → `[{id, patient:{id, person:{...}}, date, hour, overturn, appointmentStateId, ...}]`.
  Use it to find taken slots. `appointmentStateId`: 1 Asignado, 2 Confirmado,
  4 Cancelado (free again), 6 Reserva, 7 Bloqueo.
- **Book:** **`POST /api/institutions/{id}/medicalConsultations/appointments`**
  → returns the appointment id (200). Captured payload:
  ```json
  {"date":"2026-07-13","diaryId":1,"hour":"08:00:00","openingHoursId":1,
   "overturn":false,"patientId":136,"modality":"ON_SITE_ATTENTION",
   "patientEmail":null,"applicantHealthcareProfessionalEmail":null,
   "practices":[],"transcribedServiceRequest":null,"orderPending":false}
  ```
  Required essence: `date` + `hour` must land on a real slot boundary
  (`openingHours.from + k*appointmentDuration`) of the `openingHoursId`'s weekday,
  and not already be taken. `modality:"ON_SITE_ATTENTION"` for a presencial turno.
  On success the UI toasts "Turno confirmado".

**Slot alignment** (implemented in `create_appointment`): from the requested
datetime, scan forward day-by-day (bounded by the diary's `startDate..endDate`),
skip weekdays with no opening hours, snap to the first free boundary `≥` the
requested time, and skip boundaries already booked. Booking a taken 09:00 rolls to
09:30, etc.

When Sana assigns a named professional, `push_turno` uses exact-slot mode:
HSI must accept that same agenda boundary or the handoff fails closed. It must
not snap forward, because Sana's local same-doctor collision check applies to
the operator-approved time and moving it could create a duplicate local slot.

---

## 6. Verified end-to-end

Seed (`scripts/tools/seed_hsi_demo.py`) then `push_turno('99887766','JUAN','PRUEBA',
'1980-05-05','M','Cardiología','2026-07-09T09:00')` → **appointment id 2**, Thu
2026-07-09 09:00, Dr MARIA CARDIODEMO, diary "DEMO Cardiología". Confirmed in the
HSI agenda UI ("09:00 PRUEBA, JUAN." on Jueves jul 9) and via the appointments GET.
