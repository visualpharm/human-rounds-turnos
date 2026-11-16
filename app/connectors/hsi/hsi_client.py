"""
hsi_client.py — stdlib-only client for pushing booked turnos into a local HSI
(Historia de Salud Integrada) instance.

Discovered empirically against historiaclinica/hsi:v2.71.2 (see docs/HSI-API-NOTES.md
for the full endpoint map, payloads and gotchas). No third-party deps — urllib only.

Public surface (Agent C imports this — keep the signatures):
    HsiClient(base_url, email, password)
        .login()
        .ensure_patient(dni, first_name, last_name, birth_date_iso, gender) -> patient_id
        .list_diaries() -> [ {diary_id, specialty, professional, ...}, ... ]
        .create_appointment(patient_id, diary_hint, when_iso, professional_hint=None,
                            overturn=False, exact=False) -> {appointment_id, ...}
    push_turno(dni, nombre, apellido, nacimiento_iso, sexo, especialidad, when_iso,
               medico=None, sobreturno=False) -> dict

Successful booking contract:
    {"estado": "ok", "appointment_id": int, "patient_id": int,
     "requested_turno_dt": "...<offset>", "actual_turno_dt": "...<offset>",
     "slot_changed": bool, ...}
    <offset> is the installation timezone's UTC offset for that date (tz.py,
    zoneinfo-based — "-03:00" for Argentina, which has no DST).

`actual_turno_dt` is the source of truth: HSI may move a request to the next
valid, free agenda boundary. Callers must never continue to show `when_iso`
as confirmed when this value differs.

Environment:
    HSI_URL   (default http://localhost:8280)
    HSI_EMAIL (default admin@example.com)
    HSI_PASS  (default SanaAdmin2026)
    HSI_INSTITUTION_ID (default 1)
    TURNOS_SKIP_HSI=1  -> push_turno returns {"skipped": True} without any network

Moved here from repo-root hsi_client.py in the Phase 6b connector extraction
(2026-07-30), part of the `emr_sync` connector package (with hsi_sync.py,
hsi_resolver.py, hsi_resync.py — see connectors/hsi/connector.json,
connectors/hsi/adapter.py). Byte-identical move: no __file__-relative paths
in this file needed fixing. The repo-root `hsi_client.py` compat shim was
retired in Tier 2 batch L (2026-08-07): import `connectors.hsi.hsi_client`
directly (tests inject fakes as the `connectors.hsi` package attribute).
"""

import os
import re
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime, timedelta

from app.core import config
from app.core import text_fold
from app.core.status_error import RemoteError
from app.core import tz as _tz


def _offset_str(naive_dt):
    """ISO-8601 UTC offset (e.g. '-03:00') for a naive LOCAL datetime,
    computed from the installation timezone via zoneinfo — DST-safe for any
    IANA zone. A fixed '-03:00' literal was only ever safe because
    America/Argentina/Buenos_Aires currently observes no DST; this varies by
    date the moment a deployment's timezone does."""
    off = naive_dt.replace(tzinfo=_tz.tzinfo()).utcoffset()
    total_min = int(off.total_seconds() // 60)
    sign = "-" if total_min < 0 else "+"
    total_min = abs(total_min)
    return "%s%02d:%02d" % (sign, total_min // 60, total_min % 60)


def _offset_str_for(date_str, hms_str):
    """`_offset_str` from a 'YYYY-MM-DD' date string + 'HH:MM[:SS]' string —
    the shape HSI's own date/hour fields come in."""
    return _offset_str(datetime.fromisoformat("%sT%s" % (date_str, hms_str)))

# ---- catalogs (from HSI masterdata, stable) --------------------------------
# /api/person/masterdata/genders
GENDER_MAP = {
    "F": 1, "FEMENINO": 1, "FEMALE": 1,
    "M": 2, "MASCULINO": 2, "MALE": 2,
    "X": 3,
}
IDENTIFICATION_TYPE_DNI = 1        # /api/person/masterdata/identificationTypes
PATIENT_TYPE_ID = 7                # HSI "typeId" used by the empadronamiento POST
COUNTRY_ARGENTINA = 14
# HSI dayWeekId is 1=Monday .. 7=Sunday, identical to Python date.isoweekday().


class HsiError(RemoteError):
    """Any failure talking to HSI (network, auth, validation, no-slot)."""


class HsiConflict(HsiError):
    """Remote HSI record drifted from the checkpoint; requires review."""

    def __init__(self, message, remote=None):
        super().__init__(message)
        self.remote = remote


# Sana practice/signage names -> the HSI clinical specialty that owns the
# diary. Shared by _match_diary and diaries_for_specialty.
_SPECIALTY_SYNONYMS = {
    "ergometria": "cardiologia",
    "electrocardiograma": "cardiologia",
    "ecocardiograma": "cardiologia",
    "holter": "cardiologia",
    "mapa": "cardiologia",
    # Imaging practices book into the imaging service's diary.
    "ecografia": "diagnostico por imagenes",
    "ecografia infantil": "diagnostico por imagenes",
    "ecografia obstetrica": "diagnostico por imagenes",
    "ecodoppler": "diagnostico por imagenes",
    "mamografia": "diagnostico por imagenes",
    "radiografia": "diagnostico por imagenes",
    "tomografia": "diagnostico por imagenes",
    # HSI's catalog names differ from the Pinamar signage.
    "neumologia": "neumonologia",
    "laboratorio": "bioquimica clinica",
    "laboratorio y extracciones": "bioquimica clinica",
    "audiometria": "fonoaudiologia",
    "otoemisiones acusticas": "fonoaudiologia",
    "pie diabetico": "diabetologia",
    "traumatologia - pie diabetico": "traumatologia",
    "consultorio nino sano": "pediatria",
    "consultorio de nino enfermo": "pediatria",
}


def _norm(s):
    """lowercase + strip accents, for accent-insensitive specialty matching."""
    if s is None:
        return ""
    return text_fold.strip_accents(str(s)).strip().lower()


def _gender_id(gender):
    if gender is None:
        raise HsiError("gender is required (M/F/X)")
    key = _norm(gender).upper()
    if key in GENDER_MAP:
        return GENDER_MAP[key]
    if str(gender) in ("1", "2", "3"):
        return int(gender)
    raise HsiError("unknown gender %r (use M/F/X)" % (gender,))


def _iso_date(value):
    """Accept 'yyyy-mm-dd' or full ISO datetime -> 'yyyy-mm-dd'."""
    if not value:
        raise HsiError("birth_date_iso is required (yyyy-mm-dd)")
    v = str(value).strip().replace("Z", "").replace("z", "")
    return v[:10]


def _parse_when(when_iso):
    """Parse 'when' into a naive datetime. Accepts ISO date or datetime."""
    v = str(when_iso).strip().replace("Z", "").replace("z", "")
    # drop timezone offset if present (we treat times as institution-local)
    v = re.sub(r"[+-]\d{2}:?\d{2}$", "", v)
    try:
        if "T" in v or " " in v:
            v2 = v.replace(" ", "T")
            return datetime.fromisoformat(v2)
        d = datetime.fromisoformat(v)
        return d
    except ValueError:
        raise HsiError("could not parse when_iso %r" % (when_iso,))


def _t2s(t):
    """time 'HH:MM' or 'HH:MM:SS' -> seconds since midnight."""
    parts = [int(x) for x in str(t).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _s2hms(sec):
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return "%02d:%02d:%02d" % (h, m, s)


class HsiClient:
    def __init__(self, base_url=None, email=None, password=None):
        # config.get() (DB Settings > env > .env > default) instead of a bare
        # os.environ.get — fase 4 (2026-07-30): the "HSI" cluster in Settings
        # needs these to actually take effect, not just document them.
        self.base = (base_url or config.get("HSI_URL", default="http://localhost:8280")).rstrip("/")
        self.email = email or config.get("HSI_EMAIL", default="admin@example.com")
        self.password = password or config.get("HSI_PASS", default="SanaAdmin2026")
        self.institution_id = int(config.get("HSI_INSTITUTION_ID", default="1"))
        self.token = None

    # ---- low level ---------------------------------------------------------
    def _url(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _request(self, method, path, params=None, body=None, expect_json=True):
        url = self._url(path, params)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            # HSI issues the JWT as an httpOnly cookie (Path=/api); send it back
            # verbatim. It also accepts it as a Bearer header.
            headers["Cookie"] = "token=" + self.token
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            finally:
                e.close()
            raise HsiError("HSI %s %s -> %s: %s" % (method, path, e.code, detail), status=e.code)
        except urllib.error.URLError as e:
            raise HsiError("HSI %s %s unreachable: %s" % (method, path, e.reason))
        raw = resp.read().decode("utf-8", "replace")
        set_cookie = resp.headers.get_all("Set-Cookie") or []
        if not expect_json:
            return raw, set_cookie
        if raw == "":
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def _get(self, path, params=None):
        return self._request("GET", path, params=params)

    def _post(self, path, body=None, params=None):
        return self._request("POST", path, params=params, body=body)

    def _put(self, path, body=None, params=None):
        return self._request("PUT", path, params=params, body=body)

    # ---- auth --------------------------------------------------------------
    def login(self):
        """POST /api/auth -> stash the JWT from the Set-Cookie 'token'.

        No altcha is enforced on this instance for admin@example.com, so a plain
        scripted POST works (verified). Body echoes `true`; the JWT lives in the
        httpOnly `token` cookie.
        """
        raw, cookies = self._request(
            "POST", "/api/auth",
            body={"username": self.email, "password": self.password},
            expect_json=False,
        )
        token = None
        for c in cookies:
            m = re.search(r"(?:^|\s)token=([^;]+)", c)
            if m:
                token = m.group(1)
                break
        if not token:
            raise HsiError("login succeeded but no token cookie was returned")
        self.token = token
        return token

    def _ensure_login(self):
        if not self.token:
            self.login()

    # ---- patients ----------------------------------------------------------
    def find_patient(self, dni):
        """Read the existing minimal-search path; ambiguity never picks a patient.

        Unlike ensure_patient, this method never creates a patient. Production
        must validate that the clinical account permits DNI-only minimal search.
        """
        self._ensure_login()
        found = self._get("/api/patient/minimalsearch", {
            "identificationTypeId": IDENTIFICATION_TYPE_DNI,
            "identificationNumber": str(dni).strip(),
        })
        if not isinstance(found, list) or len(found) != 1:
            return None
        return int(found[0])

    @staticmethod
    def external_failure(exc):
        """Do not log exception messages: URLs and bodies can contain PHI."""
        status = getattr(exc, "status", None) or getattr(exc, "code", None)
        reason = {403: "forbidden", 404: "not_deployed"}.get(status, "error")
        return {"available": False, "reason": reason, "items": []}

    def _external_read(self, path, params=None):
        if not config.external_history_enabled():
            return {"available": False, "reason": "flag_off", "items": []}
        try:
            self._ensure_login()
            raw = self._get(path, params)
            # The response schema is UNKNOWN. Preserve dictionaries verbatim;
            # do not guess wrapper fields or silently accept mixed/scalar lists.
            if isinstance(raw, dict):
                items = [raw] if raw else []
            elif isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
                items = raw
            else:
                return {"available": False, "reason": "error", "items": []}
            return {"available": True, "reason": "", "items": items}
        except Exception as exc:
            return self.external_failure(exc)

    def external_clinical_history(self, patient_id):
        if not config.external_history_enabled():
            return {"available": False, "reason": "flag_off", "items": []}
        try:
            path = ("/api/institutions/%d/patient/%d/outpatient/consultations/"
                    "getExternalClinicalHistoryList") % (self.institution_id, int(patient_id))
            return self._external_read(path)
        except Exception as exc:
            return self.external_failure(exc)

    def patient_location(self, identifier):
        return self._external_read(
            "/api/masterfile-federacion-service/Patient/patient-location",
            {"identifier": str(identifier)})

    def federated_patient(self, subject, custodian):
        return self._external_read("/api/masterfile-federacion-service/Patient",
                                   {"subject": str(subject), "custodian": str(custodian)})

    def ensure_patient(self, dni, first_name, last_name, birth_date_iso, gender):
        """Return the HSI patient id for `dni`, creating a minimal patient if absent.

        Search: GET /api/patient/minimalsearch (returns [patientId,...]).
        Create: POST /api/patient/institution/{id} (returns the new id).
        """
        self._ensure_login()
        gid = _gender_id(gender)
        dni = str(dni).strip()
        found = self._get(
            "/api/patient/minimalsearch",
            {"identificationTypeId": IDENTIFICATION_TYPE_DNI,
             "identificationNumber": dni, "genderId": gid},
        )
        if isinstance(found, list) and found:
            return int(found[0])

        bdate = _iso_date(birth_date_iso)
        body = {
            "birthDate": bdate + "T03:00:00.000Z",
            "firstName": first_name,
            "genderId": gid,
            "identificationTypeId": IDENTIFICATION_TYPE_DNI,
            "identificationNumber": dni,
            "lastName": last_name,
            "middleNames": None,
            "otherLastNames": None,
            "personAge": None,
            "cuil": None,
            "email": None,
            "ethnicityId": None,
            "educationLevelId": None,
            "occupationId": None,
            "genderSelfDeterminationId": None,
            "mothersLastName": None,
            "nameSelfDetermination": None,
            "phonePrefix": None,
            "phoneNumber": None,
            "religion": None,
            "deprivedOfLiberty": False,
            "nationality": None,
            "apartment": None, "cityId": None, "floor": None, "number": None,
            "postcode": None, "quarter": None, "street": None,
            "countryId": COUNTRY_ARGENTINA,
            "departmentId": None, "provinceId": None,
            "typeId": PATIENT_TYPE_ID,
            "comments": None,
            "identityVerificationStatusId": None,
            "generalPractitioner": {"fullName": None, "phoneNumber": None, "generalPractitioner": True},
            "pamiDoctor": {"fullName": None, "phoneNumber": None, "generalPractitioner": False},
            "auditType": "UNAUDITED",
            "fileIds": [],
            "militaryObservations": None,
        }
        pid = self._post("/api/patient/institution/%d" % self.institution_id, body=body)
        try:
            return int(pid)
        except (TypeError, ValueError):
            raise HsiError("patient create returned unexpected id: %r" % (pid,))

    # ---- diaries -----------------------------------------------------------
    def _professionals(self):
        return self._get("/api/institution/%d/healthcareprofessional" % self.institution_id) or []

    def _diaries_for_professional(self, hp_id):
        return self._get(
            "/api/institutions/%d/medicalConsultations/diary" % self.institution_id,
            {"healthcareProfessionalId": hp_id, "includeExpired": "false"},
        ) or []

    def diary_detail(self, diary_id):
        return self._get(
            "/api/institutions/%d/medicalConsultations/diary/%d" % (self.institution_id, diary_id))

    def create_diary(self, healthcare_professional_id, doctors_office_id,
                     clinical_specialty_id, start_date, end_date,
                     week_days=(1, 2, 3, 4, 5), from_time="08:00:00", to_time="12:00:00",
                     appointment_duration=30, alias=None, overturn_count=0,
                     windows=None):
        """Create a diary (agenda) with the given weekly opening hours. Returns diary id.

        week_days uses HSI dayWeekId (1=Mon..7=Sun). Used by tools/seed_hsi_demo.py.
        `windows` (optional) overrides week_days/from_time/to_time with explicit
        per-day slots: [(dayWeekId, "HH:MM:SS", "HH:MM:SS"), ...] — one diary
        can hold several weekly franjas (tools/seed_hsi_medicos.py mirrors the
        Sana agenda shape 1:1).
        """
        self._ensure_login()
        if windows is None:
            windows = [(d, from_time, to_time) for d in week_days]
        opening = [{
            "ageRangeList": [],
            "openingHours": {"dayWeekId": d, "from": desde, "to": hasta},
            "externalAppointmentsAllowed": False,
            "medicalAttentionTypeId": 1,          # 1 = Programada
            "overturnCount": int(overturn_count),
            "protectedAppointmentsAllowed": False,
            "referralOrderRequired": None,
            "patientVirtualAttentionAllowed": False,
            "secondOpinionVirtualAttentionAllowed": False,
            "onSiteAttentionAllowed": True,
            "regulationProtectedAppointmentsAllowed": False,
        } for d, desde, hasta in windows]
        body = {
            "bookingRestriction": None,
            "ageRangeList": [],
            "appointmentDuration": appointment_duration,
            "healthcareProfessionalId": healthcare_professional_id,
            "doctorsOfficeId": doctors_office_id,
            "predecessorProfessionalId": None,
            "hierarchicalUnitId": None,
            "startDate": start_date,
            "endDate": end_date,
            "automaticRenewal": False,
            "includeHoliday": False,
            "professionalAssignShift": False,
            "diaryOpeningHours": opening,
            "clinicalSpecialtyId": clinical_specialty_id,
            "alias": alias,
            "diaryAssociatedProfessionalsId": [],
            "careLines": [],
            "practicesId": [],
            "protectedAppointmentsPercentage": None,
            "institutionId": self.institution_id,
            "diaryLabelDto": [],
        }
        diary_id = self._post(
            "/api/institutions/%d/medicalConsultations/diary" % self.institution_id, body=body)
        try:
            return int(diary_id)
        except (TypeError, ValueError):
            raise HsiError("diary create returned unexpected id: %r" % (diary_id,))

    def list_diaries(self):
        """All active diaries in the institution, flattened with specialty + professional.

        HSI has no unfiltered list endpoint, so we fan out over professionals.
        """
        self._ensure_login()
        out = []
        for p in self._professionals():
            for d in self._diaries_for_professional(p["id"]):
                out.append({
                    "diary_id": d["id"],
                    "specialty": d.get("clinicalSpecialtyName"),
                    "professional": p.get("fullName"),
                    "professional_id": p["id"],
                    "office": d.get("doctorsOfficeDescription"),
                    "start_date": d.get("startDate"),
                    "end_date": d.get("endDate"),
                    "appointment_duration": d.get("appointmentDuration"),
                    "expired": d.get("isExpired"),
                })
        return out

    def diaries_for_specialty(self, diary_hint):
        """Every active diary whose specialty matches `diary_hint` (translated).

        hsi_resolver iterates these to find ANOTHER professional with the
        promised slot free when the assigned one has no diary / no room.
        """
        hint = _SPECIALTY_SYNONYMS.get(_norm(diary_hint), _norm(diary_hint))
        out = []
        for d in self.list_diaries():
            nd = _norm(d.get("specialty"))
            if nd == hint or (hint and (hint in nd or nd in hint)):
                out.append(d)
        return out

    def _match_diary(self, diary_hint, professional_hint=None):
        # Sana services may name a requested diagnostic practice while HSI
        # diaries are catalogued under the owning clinical specialty. Keep the
        # translation explicit so an Ergometría approval reaches Cardiology
        # instead of becoming a local-only appointment.
        hint = _SPECIALTY_SYNONYMS.get(_norm(diary_hint), _norm(diary_hint))
        diaries = self.list_diaries()
        if not diaries:
            raise HsiError("no diaries exist in HSI (run tools/seed_hsi_demo.py first)")
        if professional_hint:
            wanted = set(re.findall(r"[a-z0-9]+", _norm(professional_hint)))
            # Sana names carry honorifics ("Dra. Martin Galvan"); HSI persons
            # don't. Requiring the title token made every named push fail.
            wanted -= {"dr", "dra", "lic", "od", "bioq", "tec", "klgo", "klga"}
            diaries = [d for d in diaries if wanted and wanted.issubset(
                set(re.findall(r"[a-z0-9]+", _norm(d.get("professional"))))) ]
            if not diaries:
                raise HsiError("no HSI diary matches professional %r" % professional_hint)
        # exact-ish match first, then substring either direction
        exact = [d for d in diaries if _norm(d["specialty"]) == hint]
        if exact:
            return exact[0]
        partial = [d for d in diaries
                   if hint and (hint in _norm(d["specialty"]) or _norm(d["specialty"]) in hint)]
        if partial:
            return partial[0]
        avail = sorted({d["specialty"] for d in diaries})
        raise HsiError("no diary matches %r; available specialties: %s"
                       % (diary_hint, ", ".join(avail)))

    def _booked_hours(self, diary_id, on_date):
        """Set of 'HH:MM:SS' already taken (non-cancelled) on `on_date`."""
        appts = self._get(
            "/api/institutions/%d/medicalConsultations/appointments/diary/%d"
            % (self.institution_id, diary_id),
            {"from": on_date, "to": on_date},
        ) or []
        # Keep the facts which produced this occupancy view.  create_appointment
        # can then recognise a retry for the same patient+slot without issuing
        # a second request (and without changing the long-standing test seam
        # where `_booked_hours` is substituted).
        if not hasattr(self, "_appointments_cache"):
            self._appointments_cache = {}
        self._appointments_cache[(int(diary_id), str(on_date))] = appts
        taken = set()
        for a in appts:
            # appointmentStateId 4 = Cancelado -> slot is free again
            if a.get("appointmentStateId") == 4:
                continue
            if a.get("date") == on_date and a.get("hour"):
                taken.add(a["hour"])
        return taken

    def appointments_for_diary(self, diary_id, from_date, to_date):
        """Return raw HSI appointments for a diary/range.

        This is intentionally public: the sync worker needs remote facts for
        reconciliation, not just a set of occupied hours.
        """
        self._ensure_login()
        appointments = self._get(
            "/api/institutions/%d/medicalConsultations/appointments/diary/%d"
            % (self.institution_id, int(diary_id)),
            {"from": str(from_date), "to": str(to_date)},
        )
        if not isinstance(appointments, list) or any(not isinstance(a, dict) for a in appointments):
            raise HsiError("hsi_diary_appointments_unverified")
        return appointments

    def patient_basicdata(self, patient_id):
        """Read a known patient's current HSI identity (no global enumeration)."""
        self._ensure_login()
        return self._get("/api/patient/%s/basicdata" % int(patient_id)) or {}

    @staticmethod
    def appointment_checkpoint(appointment):
        """The immutable remote facts used for optimistic mutation checks."""
        checkpoint = {k: appointment.get(k) for k in
                      ("id", "updatedOn", "appointmentStateId", "diaryId", "date", "hour", "patientId")}
        checkpoint["patientId"] = checkpoint["patientId"] or (appointment.get("patient") or {}).get("id")
        return checkpoint

    def read_appointment(self, appointment_id, checkpoint):
        """Re-read a linked appointment through HSI's supported diary query."""
        if not checkpoint or not checkpoint.get("diaryId") or not checkpoint.get("date"):
            raise HsiConflict("missing_hsi_checkpoint")
        appointments = self.appointments_for_diary(checkpoint["diaryId"], checkpoint["date"], checkpoint["date"])
        remote = next((a for a in appointments if str(a.get("id")) == str(appointment_id)), None)
        if not remote:
            raise HsiConflict("hsi_appointment_missing")
        # The per-diary endpoint may omit the diary id because it is already
        # encoded in the URL.  Restore that context before comparing the
        # checkpoint captured by the institution-wide polling path.
        remote = dict(remote)
        remote.setdefault("diaryId", checkpoint["diaryId"])
        actual = self.appointment_checkpoint(remote)
        expected = self.appointment_checkpoint(checkpoint)
        # updatedOn is preferred when present; the clinical tuple is always
        # compared so older HSI payloads without updatedOn remain safe.
        fields = ("updatedOn", "appointmentStateId", "diaryId", "date", "hour", "patientId")
        if any(expected.get(k) is not None and actual.get(k) != expected.get(k) for k in fields):
            raise HsiConflict("hsi_checkpoint_drift")
        return remote

    def change_appointment_state(self, appointment_id, checkpoint, state_id):
        """Move an appointment to a verified HSI state, idempotently.

        The same supported ``change-state`` endpoint handles Cancelado (4),
        Ausente (3), Atendido/Presente (5), and Reserva ausente (9). We first
        recognise the requested remote state so a crash after HSI accepted the
        mutation cannot create a false checkpoint-drift conflict on replay.
        """
        if state_id not in (3, 4, 5, 9):
            raise HsiError("unsupported appointment state: %r" % (state_id,))
        existing = next((a for a in self.appointments_for_diary(
            checkpoint["diaryId"], checkpoint["date"], checkpoint["date"])
            if str(a.get("id")) == str(appointment_id)), None)
        if existing and existing.get("appointmentStateId") == state_id:
            existing = dict(existing); existing.setdefault("diaryId", checkpoint["diaryId"])
            return {"estado": "ok", "appointment_id": str(appointment_id),
                    "patient_id": (existing.get("patient") or {}).get("id") or existing.get("patientId"),
                    "checkpoint": self.appointment_checkpoint(existing), "deduplicated": True}
        if not existing and state_id == 4:
            return self._confirm_cancel_absence(appointment_id, checkpoint,
                                                deduplicated=True)
        remote = self.read_appointment(appointment_id, checkpoint)
        self._put("/api/institutions/%d/medicalConsultations/appointments/%s/change-state"
                  % (self.institution_id, appointment_id),
                  params={"appointmentStateId": state_id},
                  body={"reason": None, "patientIdentificationBarCode": None, "reasonId": None})
        # The mutation increments HSI's version (``updatedOn``).  Returning a
        # checkpoint synthesized from the pre-write response made the next
        # legitimate attendance correction look like concurrent drift.  Read
        # the authoritative result before acknowledging the durable intent.
        confirmed = next((a for a in self.appointments_for_diary(
            checkpoint["diaryId"], checkpoint["date"], checkpoint["date"])
            if str(a.get("id")) == str(appointment_id)), None)
        if not confirmed and state_id == 4:
            return self._confirm_cancel_absence(appointment_id, remote)
        if not confirmed or confirmed.get("appointmentStateId") != state_id:
            raise HsiConflict("hsi_state_change_not_confirmed")
        confirmed = dict(confirmed)
        confirmed.setdefault("diaryId", checkpoint["diaryId"])
        return {"estado": "ok", "appointment_id": str(appointment_id),
                "patient_id": (confirmed.get("patient") or {}).get("id") or confirmed.get("patientId"),
                "checkpoint": self.appointment_checkpoint(confirmed)}

    def _confirm_cancel_absence(self, appointment_id, observed, deduplicated=False):
        """Absence on one day is not proof when a reassign may have moved the ID.

        HSI v2.71 hides genuinely cancelled appointments. Search this diary's
        complete, finite date range first; an unreadable range is uncertainty,
        never a fabricated cancellation acknowledgement.
        """
        diary_id = observed.get("diaryId")
        day = observed.get("date")
        if not diary_id or not day:
            raise HsiError("hsi_cancel_absence_unverified")
        detail = self.diary_detail(diary_id) or {}
        try:
            start = date.fromisoformat(detail["startDate"])
            end = date.fromisoformat(detail["endDate"])
            current = date.fromisoformat(day)
        except (KeyError, TypeError, ValueError) as exc:
            raise HsiError("hsi_cancel_absence_unverified") from exc
        if not (start <= current <= end) or (end - start).days > 366:
            raise HsiError("hsi_cancel_absence_unverified")
        found = next((a for a in self.appointments_for_diary(
            diary_id, start.isoformat(), end.isoformat())
            if str(a.get("id")) == str(appointment_id)), None)
        if found:
            found = dict(found)
            found.setdefault("diaryId", diary_id)
            if found.get("appointmentStateId") != 4:
                raise HsiConflict("hsi_checkpoint_drift", remote=found)
            confirmed = self.appointment_checkpoint(found)
        else:
            cancelled = dict(observed)
            cancelled["appointmentStateId"] = 4
            cancelled["updatedOn"] = None
            confirmed = self.appointment_checkpoint(cancelled)
        return {"estado": "ok", "appointment_id": str(appointment_id),
                "patient_id": confirmed.get("patientId"),
                "checkpoint": confirmed, "deduplicated": deduplicated}

    def cancel_appointment(self, appointment_id, checkpoint):
        return self.change_appointment_state(appointment_id, checkpoint, 4)

    def mark_attendance(self, appointment_id, checkpoint, asistencia):
        states = {"ausente": 3, "presente": 5}
        if asistencia not in states:
            raise HsiError("invalid attendance: %r" % (asistencia,))
        return self.change_appointment_state(appointment_id, checkpoint, states[asistencia])

    def reassign_appointment(self, appointment_id, checkpoint, when_iso):
        when = _parse_when(when_iso)
        diary_id = checkpoint["diaryId"]
        target_date = when.date().isoformat()
        target_hour = _s2hms(when.hour * 3600 + when.minute * 60)
        # HSI's update-date route is not idempotent-key aware.  Recognise a
        # prior successful move before comparing the old checkpoint so a
        # crash after the remote commit cannot turn into a false conflict.
        remote = None
        for day in dict.fromkeys((checkpoint["date"], target_date)):
            remote = next((a for a in self.appointments_for_diary(diary_id, day, day)
                           if str(a.get("id")) == str(appointment_id)), None)
            if remote:
                remote = dict(remote); remote.setdefault("diaryId", diary_id)
                break
        if not remote:
            # A concurrent HSI reprogram can move the same appointment away
            # from both Sana's checkpoint date and Sana's requested target.
            # Scan the bounded diary window before declaring it missing so the
            # operator sees the actual remote fact and no edit is overwritten.
            detail = self.diary_detail(diary_id)
            start = detail.get("startDate") or min(checkpoint["date"], target_date)
            end = detail.get("endDate") or (date.fromisoformat(max(checkpoint["date"], target_date))
                                             + timedelta(days=90)).isoformat()
            remote = next((a for a in self.appointments_for_diary(diary_id, start, end)
                           if str(a.get("id")) == str(appointment_id)), None)
            if remote:
                remote = dict(remote); remote.setdefault("diaryId", diary_id)
                raise HsiConflict("hsi_checkpoint_drift", remote=remote)
        if remote and remote.get("appointmentStateId") != 4 and remote.get("date") == target_date and remote.get("hour") == target_hour:
            checkpoint_out = self.appointment_checkpoint(remote)
            return {"estado": "ok", "appointment_id": str(appointment_id),
                    "patient_id": checkpoint_out.get("patientId"),
                    "actual_turno_dt": "%sT%s%s" % (target_date, target_hour, _offset_str_for(target_date, target_hour)),
                    "checkpoint": checkpoint_out, "deduplicated": True}
        remote = self.read_appointment(appointment_id, checkpoint)
        detail = self.diary_detail(diary_id)
        want_sec = when.hour * 3600 + when.minute * 60
        opening_id = None
        for start, end, oh_id in self._slots_for_weekday(detail, when.date().isoweekday()):
            if start <= want_sec < end and (want_sec - start) % (int(detail.get("appointmentDuration") or 30) * 60) == 0:
                opening_id = oh_id; break
        if opening_id is None:
            raise HsiConflict("target_slot_not_in_hsi_diary")
        body = {"appointmentId": int(appointment_id),
                "date": {"date": {"year": when.year, "month": when.month, "day": when.day},
                         "time": {"hours": when.hour, "minutes": when.minute, "seconds": when.second}},
                "openingHoursId": opening_id,
                "modality": remote.get("modality") or "ON_SITE_ATTENTION",
                "patientEmail": None, "recurringAppointmentTypeId": None}
        self._put("/api/institutions/%d/medicalConsultations/appointments/%s/update-date"
                  % (self.institution_id, appointment_id), body=body)
        updated = next((a for a in self.appointments_for_diary(diary_id, target_date, target_date)
                        if str(a.get("id")) == str(appointment_id)), None)
        if not updated:
            raise HsiConflict("hsi_reassign_not_confirmed")
        updated = dict(updated); updated.setdefault("diaryId", diary_id)
        checkpoint_out = self.appointment_checkpoint(updated)
        actual = "%sT%s%s" % (checkpoint_out["date"], checkpoint_out["hour"],
                               _offset_str_for(checkpoint_out["date"], checkpoint_out["hour"]))
        return {"estado": "ok", "appointment_id": str(appointment_id),
                "patient_id": checkpoint_out.get("patientId"), "actual_turno_dt": actual,
                "checkpoint": checkpoint_out}

    def list_appointments(self, from_date, to_date):
        """Enumerate all appointments by walking HSI's per-diary endpoint."""
        self._ensure_login()
        out = []
        for diary in self.list_diaries():
            for appointment in self.appointments_for_diary(
                    diary["diary_id"], from_date, to_date):
                item = dict(appointment)
                item.setdefault("diaryId", diary["diary_id"])
                item["_sana_diary"] = diary
                out.append(item)
        return out

    def _slots_for_weekday(self, detail, weekday):
        """[(start_sec, end_sec, opening_hours_id)] for a given isoweekday (1..7)."""
        out = []
        for oh in detail.get("diaryOpeningHours", []):
            block = oh.get("openingHours", {})
            if block.get("dayWeekId") == weekday:
                out.append((_t2s(block["from"]), _t2s(block["to"]), block["id"]))
        out.sort()
        return out

    def create_appointment(self, patient_id, diary_hint, when_iso, professional_hint=None,
                           overturn=False, exact=False):
        """Book `patient_id` into the diary matching `diary_hint`, near `when_iso`.

        Slot alignment: HSI agendas have fixed weekly opening hours + a fixed
        appointment duration. By default we pick the earliest free slot boundary
        on/after the requested datetime. ``exact=True`` is used for an explicitly
        assigned professional: moving that booking would bypass Sana's collision
        check and could create a duplicate local doctor slot.
        """
        self._ensure_login()
        diary = self._match_diary(diary_hint, professional_hint=professional_hint)
        detail = self.diary_detail(diary["diary_id"])
        duration = int(detail.get("appointmentDuration") or 30)
        start_date = detail.get("startDate")
        end_date = detail.get("endDate")
        d0 = date.fromisoformat(start_date) if start_date else date(1970, 1, 1)
        d1 = date.fromisoformat(end_date) if end_date else date(2999, 1, 1)

        want = _parse_when(when_iso)
        want_day = want.date() if exact else max(want.date(), d0)
        want_sec = want.hour * 3600 + want.minute * 60

        for offset in range(0, 1 if exact else 60):
            day = want_day + timedelta(days=offset)
            if day > d1:
                break
            blocks = self._slots_for_weekday(detail, day.isoweekday())
            if not blocks:
                continue
            iso_day = day.isoformat()
            taken = self._booked_hours(diary["diary_id"], iso_day)
            remote_appointments = getattr(self, "_appointments_cache", {}).get(
                (int(diary["diary_id"]), str(iso_day)), [])
            # A prior non-exact create may have been snapped forward by HSI.
            # If the worker died before acknowledging it, do NOT reconsider a
            # newly-free earlier boundary on replay: the existing patient+diary
            # appointment is the durable confirmation for this create intent.
            existing = next((a for a in remote_appointments
                             if a.get("appointmentStateId") != 4
                             and str((a.get("patient") or {}).get("id")) == str(patient_id)
                             and a.get("date") == iso_day and a.get("hour")), None)
            if existing and (not exact or existing.get("hour") == _s2hms(want_sec)):
                requested = want.replace(microsecond=0).isoformat() + _offset_str(want)
                actual = "%sT%s%s" % (iso_day, existing["hour"], _offset_str_for(iso_day, existing["hour"]))
                return {"estado": "ok", "appointment_id": int(existing["id"]),
                        "patient_id": patient_id, "diary_id": diary["diary_id"],
                        "specialty": diary["specialty"], "professional": diary["professional"],
                        "date": iso_day, "hour": existing["hour"],
                        "requested_turno_dt": requested, "actual_turno_dt": actual,
                        "slot_changed": requested != actual, "sobreturno": bool(overturn),
                        "deduplicated": True}
            # candidate slot boundaries this day
            min_sec = want_sec if offset == 0 else 0
            for (b_from, b_to, oh_id) in blocks:
                if exact:
                    # The requested minute must itself be one of this opening
                    # range's fixed boundaries; never create an HSI appointment
                    # at another time after Sana already checked the doctor.
                    if (want_sec < b_from or want_sec + duration * 60 > b_to
                            or (want_sec - b_from) % (duration * 60)):
                        continue
                    sec = want_sec
                else:
                    sec = b_from
                # snap up to the first boundary >= min_sec (non-exact mode)
                if not exact and min_sec > b_from:
                    steps = (min_sec - b_from + duration * 60 - 1) // (duration * 60)
                    sec = b_from + steps * duration * 60
                while sec + duration * 60 <= b_to:
                    hhmmss = _s2hms(sec)
                    # Remote HSI has no idempotency-key header.  Before a
                    # retry creates anything, recognise the appointment we
                    # already made for this patient at this diary+slot.
                    same = next((a for a in remote_appointments
                                 if a.get("appointmentStateId") != 4
                                 and a.get("date") == iso_day and a.get("hour") == hhmmss
                                 and str((a.get("patient") or {}).get("id")) == str(patient_id)), None)
                    if same:
                        requested = want.replace(microsecond=0).isoformat() + _offset_str(want)
                        actual = "%sT%s%s" % (iso_day, hhmmss, _offset_str_for(iso_day, hhmmss))
                        return {"estado": "ok", "appointment_id": int(same["id"]),
                                "patient_id": patient_id, "diary_id": diary["diary_id"],
                                "specialty": diary["specialty"], "professional": diary["professional"],
                                "date": iso_day, "hour": hhmmss,
                                "requested_turno_dt": requested, "actual_turno_dt": actual,
                                "slot_changed": requested != actual, "sobreturno": bool(overturn),
                                "deduplicated": True}
                    # A normal booking must find a free agenda boundary. An
                    # operator-approved sobreturno is the single explicit
                    # exception: HSI receives overturn=true for that exact
                    # boundary, rather than silently moving it to another slot.
                    if overturn or hhmmss not in taken:
                        result = self._book(patient_id, diary, detail, iso_day, hhmmss, oh_id,
                                            overturn=bool(overturn))
                        requested = want.replace(microsecond=0).isoformat() + _offset_str(want)
                        actual = "%sT%s%s" % (iso_day, hhmmss, _offset_str_for(iso_day, hhmmss))
                        # The date/hour we just sent is the HSI-confirmed slot;
                        # retaining both values makes an agenda snap observable
                        # to the caller rather than silently communicating the
                        # operator's original request.
                        result.update({
                            "estado": "ok",
                            "requested_turno_dt": requested,
                            "actual_turno_dt": actual,
                            "slot_changed": requested != actual,
                            "sobreturno": bool(overturn),
                        })
                        return result
                    if exact:
                        break
                    sec += duration * 60
        raise HsiError(
            "%s slot for %r at %s within the diary window %s..%s"
            % ("no exact free" if exact else "no free", diary_hint, when_iso,
               start_date, end_date))

    def _book(self, patient_id, diary, detail, iso_day, hhmmss, opening_hours_id,
              overturn=False):
        body = {
            "date": iso_day,
            "diaryId": diary["diary_id"],
            "hour": hhmmss,
            "openingHoursId": opening_hours_id,
            "overturn": bool(overturn),
            "patientId": patient_id,
            "modality": "ON_SITE_ATTENTION",
            "patientEmail": None,
            "applicantHealthcareProfessionalEmail": None,
            "practices": [],
            "transcribedServiceRequest": None,
            "orderPending": False,
        }
        appt_id = self._post(
            "/api/institutions/%d/medicalConsultations/appointments" % self.institution_id,
            body=body)
        try:
            appt_id = int(appt_id)
        except (TypeError, ValueError):
            raise HsiError("appointment create returned unexpected id: %r" % (appt_id,))
        return {
            "appointment_id": appt_id,
            "patient_id": patient_id,
            "diary_id": diary["diary_id"],
            "specialty": diary["specialty"],
            "professional": diary["professional"],
            "date": iso_day,
            "hour": hhmmss,
            "opening_hours_id": opening_hours_id,
            "institution_id": self.institution_id,
        }


def push_turno(dni, nombre, apellido, nacimiento_iso, sexo, especialidad, when_iso,
               medico=None, sobreturno=False):
    """Convenience: login + ensure patient + book, in one call.

    Returns the create_appointment dict, or {"skipped": True} if TURNOS_SKIP_HSI=1.
    Raises HsiError on any failure.
    """
    if os.environ.get("TURNOS_SKIP_HSI") == "1":
        return {"skipped": True}
    client = HsiClient()
    client.login()
    patient_id = client.ensure_patient(dni, nombre, apellido, nacimiento_iso, sexo)
    result = client.create_appointment(patient_id, especialidad, when_iso,
                                       professional_hint=medico,
                                       overturn=bool(sobreturno),
                                       exact=bool(medico))
    result.setdefault("checkpoint", {
        "id": result.get("appointment_id"), "updatedOn": None, "appointmentStateId": 1,
        "diaryId": result.get("diary_id"), "date": result.get("date"),
        "hour": result.get("hour"), "patientId": patient_id,
    })
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Push a turno into HSI (demo).")
    ap.add_argument("--dni", default="99887766")
    ap.add_argument("--nombre", default="JUAN")
    ap.add_argument("--apellido", default="PRUEBA")
    ap.add_argument("--nacimiento", default="1980-05-05")
    ap.add_argument("--sexo", default="M")
    ap.add_argument("--especialidad", default="Cardiología")
    ap.add_argument("--when", default=None, help="ISO datetime; default = tomorrow 09:00")
    ap.add_argument("--list-diaries", action="store_true")
    args = ap.parse_args()

    if args.list_diaries:
        c = HsiClient(); c.login()
        print(json.dumps(c.list_diaries(), indent=2, ensure_ascii=False))
    else:
        when = args.when or (datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
                             + timedelta(days=1)).isoformat(timespec="seconds")
        result = push_turno(args.dni, args.nombre, args.apellido, args.nacimiento,
                            args.sexo, args.especialidad, when)
        print(json.dumps(result, indent=2, ensure_ascii=False))
