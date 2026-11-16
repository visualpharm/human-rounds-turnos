# Teleconsulta — design thinking for a future feature

> Status: **roadmap only, nothing built**. Surfaced on the admin panel's «Hoja de ruta»
> (`admin-roadmap.js`, both the SIMPLE and TECNICO variants) as a "Más adelante" card.
> This doc is the design sketch to work from when it's picked up.

## Why this, why now

Regional context (from the 2026-07-08 meeting with the hospital's leadership and follow-up
notes): neighboring hospitals in the area have had to suspend specialist guardias
(observed with gynecology in Villa Gesell) for lack of professionals physically available.
Buenos Aires province is actively expanding telemedicine as policy. A public hospital in
Pinamar sits in the same boat — some specialties are thin on the ground locally, but the
patient still needs to be seen.

**The pitch in one line:** let a specialist who does not live in Pinamar attend a Pinamar
patient over video, booked through the exact same request/approval flow that already
exists for in-person appointments. No new patient-facing paradigm — the wizard doesn't
change shape, only what happens at the appointed time changes.

## Which specialties fit

Not every service is a video candidate. Good early candidates:

- **Salud mental** (psicología/psiquiatría) — talk-based follow-ups translate well to video,
  and demand for mental-health services already outstrips local supply in many towns.
- **Dermatología (triage)** — a first-look photo consult can filter who actually needs an
  in-person exam (biopsy, procedure) from who doesn't. Not a full substitute, a triage layer.
- **Controles / seguimiento (follow-ups)** — a patient already diagnosed and just checking in
  doesn't need to be in the room; first visits and anything requiring hands-on exam still do.
- **Ginecología / obstetricia** — directly motivated by the Villa Gesell guardia suspension;
  a remote specialist could cover routine consults while an in-person guardia gap exists.

Bad candidates: anything requiring physical examination, vitals, procedures, or specimen
collection — walk-in and `walkin_con_orden` services stay untouched by this idea entirely.

## Video provider options

Two real choices, not an exhaustive market scan:

- **Jitsi Meet, self-hosted** — no per-minute cost, runs on the same infra pattern as the
  rest of the stack (the product owner's own server), full control over recording/retention policy. More
  ops burden: TURN/STUN, room lifecycle, mobile browser quirks.
- **Google Meet links** — zero infra, everyone already knows the UI, but ties the appointment
  to a Google account somewhere (whose? the hospital's institutional account, presumably) and
  gives up control over data residency/retention — worth checking against ley 27.678's data
  handling expectations before committing.

No recommendation baked in here — this is exactly the kind of "self-hosted vs. hosted"
trade-off that should get a fresh look (self-hosted OSS video has moved fast) at build time,
not decided in a doc written months earlier.

## Consent + legal frame — Ley 27.678 (telesalud)

Argentina's telehealth law (Ley 27.678, sancionada 2022) establishes the legal basis for
remote healthcare acts performed by registered professionals, subject to patient consent and
record-keeping equivalent to an in-person consult. Practical implications for this feature:

- **Explicit informed consent** before the first teleconsulta — a checkbox/step is not
  enough on its own; needs sign-off from whoever owns compliance at the hospital (the reference-deployment counterpart
  reference deployment / la Secretaría) on the actual consent text and where it's recorded.
- **The remote professional must be habilitado** to practice (matrícula) in the province
  where the patient is physically located at the time of the consult — matters for recruiting
  specialists "who don't live in Pinamar."
- **Record-keeping**: the consult still needs to land in the patient's clinical history (HSI)
  like any other appointment — this repo's existing push-to-HSI path already does that
  regardless of modality; the video call itself is not clinical record, the resulting note is.

None of this is legal advice — flag to the reference-deployment counterpart/the Secretaría before shipping, not after.

## How it maps onto the existing flow

The whole point of sketching this now is that the existing architecture barely needs to
change shape — modality is a property of *how* the appointment happens, not a new kind of
appointment:

1. **Booking (`hospital.py`, the wizard)** — a service (or a service+doctor pair) gains a
   `modalidad` flag: `presencial` (default, today's only value) or `video`. A video-flagged
   service skips office/consultorio display in the informational page (`/service/<id>`) and
   the wizard's "Cuándo" step behaves exactly as it does today — same agenda, same slots,
   same soft-lock (`reservas.py`). The patient picks a time the same way; nothing about
   `request_appointment`/`POST /api/requests` changes shape, `modalidad` just rides along on
   the service/agenda record like `edad`/`pami` do today.
2. **Approval (`solicitudes.py`)** — on approval, if the resolved service/doctor pair is
   `modalidad:"video"`, the operator's approval action also generates the video link (Jitsi
   room slug or a Meet link, whichever provider wins) instead of just confirming a time+place.
   Same code path as today's `_push_hsi` and standard-motivo approval — this is an *addition*
   to what happens on approve, not a fork of the approval logic.
3. **Notification (`notificaciones.py`)** — `notificar_aprobada` already sends the friendly
   appointment time + center + preparation by email; for a video appointment it also carries
   the join link. The existing WhatsApp channel (`whatsapp_chat.py` / the chat engine) is the
   natural second delivery surface, since a link is exactly the kind of thing a patient wants
   handed to them right before the call, not just baked into a day-old email.
4. **The doctor's calendar** — `medico_turnos.py`'s existing per-doctor appointment listing
   (`GET /api/admin/doctors/<id>/appointments`, `#mis-turnos`) is where the doctor already
   sees today's patients; a video appointment just needs the join link surfaced in that same
   table (same place a no-show gets marked, same place the order gets viewed) — no new UI
   surface, one more column/action.
5. **HSI** — the appointment still gets pushed the same way (`hsi_client.push_turno`); HSI
   has no concept of "modality" today as far as this repo's integration goes, so the video
   fact would need to either ride in a note field or simply not be HSI's concern (the
   *appointment* happened, video vs. in-person is this app's metadata, not HSI's).

## Open questions for the product owner / the reference-deployment counterpart

1. **Which specialty goes first?** Salud mental and the Villa-Gesell-adjacent ginecología
   case are the two motivated by real, observed gaps — worth confirming which one the
   hospital actually wants solved first, rather than guessing.
2. **Who are the remote specialists?** This only works if there's an actual roster of
   professionals willing to see Pinamar patients remotely — a technical build with no
   professional lined up to use it doesn't help anyone. Is this recruiting HSI professionals
   from a neighboring HSI-connected hospital, or an entirely separate arrangement?
3. **Jitsi self-hosted vs. Meet** — infra-ops trade-off above; needs an owner for whichever
   side loses (either Jitsi ops, or the Google-account/data-residency question).
4. **Consent text and where it's recorded** — needs sign-off from whoever owns legal/
   compliance at the Secretaría, not something to improvise in code.
5. **Does a video consult still need a `consultorio`/office assignment for HSI's sake**, or
   is a video-only "virtual office" the right modeling choice in HSI's own data model? Only
   The reference-deployment counterpart can answer what HSI expects here.
