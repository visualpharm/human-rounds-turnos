"""waitlist_store.py — Turnos-owned persistence adapter for the waitlist and
appointment-offer storage.

Layer separation (2026-08-11): this module is the ONLY place that holds SQL,
a Postgres connection, or a `core.db` wire/coerce primitive for the
`waitlist` and `appointment_offers` tables. The two modules that ORCHESTRATE
that storage — `waitlist.py` (the decision engine, the sweep, the offer run)
and `appointment_offer.py` (the HTTP click side: the offer page + accept/
reject) — depend on THIS adapter, never on `core.db`'s internals. They keep
the orchestration and HTTP ownership; the row-level truth lives here.

What stayed in `waitlist.py` on purpose (orchestration, not persistence):
  - the PURE decision engine (`next_action`), the clock (`_now`), the token
    mint (`_new_token`), the `WINDOW_MIN`/`STAGGER_MIN` cadence;
  - the sweep loop / thread / queue delegation (`start`, `_loop`, ...);
  - the offer-run choreography (`tick`, `release_slot`, `_process_slot`,
    `_advance`, `_create_offer`, `reject_offer`) — i.e. WHEN to read/expire/
    offer-next and the email side effect, composed out of these primitives;
  - the naive→UTC input rule on `release_slot`'s public `slot_dt`.

What moved HERE (persistence): every `SELECT/INSERT/UPDATE` against
`waitlist`/`appointment_offers`, every `db._connect()` context, every
`db_wire._wire`/`db_wire._wire_all` rename, every `db_values._coerce_dt`, and the ON-CONFLICT
insert. The adapter returns WIRED dicts (the same Spanish-key wire format
the orchestrators always consumed), so their branch logic is unchanged.

The four semantics this adapter preserves verbatim (proven by the existing
DB-backed suite — `test_integrity_races.TestWaitlistOfferRace`,
`test_cancel_waitlist_trigger`, `test_business_queue_proof`,
`test_waitlist_queue`, and the engine tests in `test_waitlist`):
  1. cancel → offer → accept: `accept_offer(token)` is the atomic
     read-then-claim that `waitlist.claim` delegates to (one transaction:
     no_existe / idempotent-aceptada / vencida / claim+deactivate);
  2. expiry → next candidate: `live_offer_slots`/`offers_for_slot`/`
     expire_offer` feed `_process_slot`'s expire-then-offer-next;
  3. idempotent reject: `expire_if_live(token)` does the read+conditional-
     expire in one transaction and reports `was_live`, so `reject_offer`
     confirms an already-resolved offer instead of reopening it;
  4. duplicate-offer race: `create_offer` delegates to
     `db_slots.create_appointment_offer`'s `ON CONFLICT ... DO NOTHING` against
     `appointment_offers_live_slot_uniq` — the one canonical insert, so a
     racing `release_slot`/`tick()` on the same slot lands one row + one mail.

`db_slots.create_appointment_offer` (in `core/db.py`) is deliberately reused rather
than re-implemented: it is already the correct ON-CONFLICT insert, the tests
seed through it directly, and a second copy of that SQL would be the kind of
duplication the wire-format contract exists to avoid. The adapter wrapping
it is what lets `waitlist.py` stop naming `db_slots.create_appointment_offer` (and
`core.db`) at all — the dependency direction is orchestrator → adapter →
core.db, never orchestrator → core.db for these two tables.

Query style (2026-10-25, the first module moved to SQLAlchemy Core): every
statement here is a Core `select`/`insert`/`update` over the tables of
`core/schema_model.py`, run on the pooled psycopg cursor of `db._connect()` through
`core/sqlcore.py`. Connections, transactions and the dict rows are the ones this
module always had; only the SQL text became statements. The behavior is pinned
function by function in `tests/test_waitlist_store.py`. The one statement that is
not physically here — the `ON CONFLICT ... DO NOTHING` insert behind `create_offer`
— lives in `db_slots.create_appointment_offer` and was converted in the same step (its
`'ofrecida'` stays a SQL literal, so Postgres matches the partial unique index).
"""
from sqlalchemy import func, insert, literal_column, select, update

from app.core import db, sqlcore
from app.core import db_slots
from app.core import db_values
from app.core import db_wire
from app.core.schema_model import appointment_offers, waitlist


# ---- waitlist (the queue of patients wanting something sooner) ------------

def opt_in(solicitud_id, dni, email, servicio_id, centro_slug, turno_dt):
    """Idempotent per active (solicitud_id): returns the existing row's id if
    the patient already opted in, else inserts and returns the new id."""
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, select(waitlist.c.id).where(
            waitlist.c.request_id == solicitud_id, waitlist.c.active)).fetchone()
        if row:
            return row["id"]
        return sqlcore.run(cur, insert(waitlist).values(
            request_id=solicitud_id, dni=dni, email=email, service_id=servicio_id,
            center_slug=centro_slug, appointment_at=db_values._coerce_dt(turno_dt),
        ).returning(waitlist.c.id)).fetchone()["id"]


def opt_out(solicitud_id):
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, update(waitlist).where(
            waitlist.c.request_id == solicitud_id).values(active=False))


def waitlist_row(id_):
    """The `waitlist` row by its id — `appointment_offer` uses it to resolve
    which solicitud an accepted offer belongs to."""
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, select(waitlist).where(waitlist.c.id == id_)).fetchone()
        return db_wire._wire(row, "waitlist")


def queue_for_slot(servicio_id, centro_slug, slot_dt, exclude_ids):
    """Active candidates for a released slot, FIFO: same service+center, whose
    OWN appointment is later than the released one (the slot is an
    improvement), minus the waitlist ids already offered it. The orchestrator
    passes the already-offered set (`ya_ofrecidos`) so an expired/rejected
    offer doesn't loop back to the same patient."""
    with db._connect() as conn, conn.cursor() as cur:
        rows = db_wire._wire_all(sqlcore.run(cur, select(waitlist).where(
            waitlist.c.active,
            waitlist.c.service_id == servicio_id,
            waitlist.c.center_slug == centro_slug,
            waitlist.c.appointment_at > db_values._coerce_dt(slot_dt),
        ).order_by(waitlist.c.created)).fetchall(), "waitlist")
    return [r for r in rows if r["id"] not in exclude_ids]


# ---- appointment_offers (one row per slot offered to one patient) ----------

def create_offer(slot_dt, servicio_id, centro_slug, lista_espera_id, token,
                 enviada_en, expira_en):
    """Inserts an offer competing against `appointment_offers_live_slot_uniq`
    (the only 'ofrecida' offer per slot). Returns the new id, or None if
    another worker already offered this slot (`ON CONFLICT DO NOTHING`) — the
    caller reads None as "skip silently, no duplicate mail". Delegates to
    `db_slots.create_appointment_offer`: the canonical ON-CONFLICT insert (also what
    the tests seed through), so there is one copy of that SQL."""
    return db_slots.create_appointment_offer(
        slot_dt, servicio_id, centro_slug, lista_espera_id, token,
        enviada_en, expira_en)


def offer_by_token(token):
    """The offer backing an email link — the token IS the authentication."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(_offer_by_token(cur, token), "appointment_offers")


def offers_for_slot(servicio_id, centro_slug, slot_dt):
    """Every offer row for one slot, oldest first — `_process_slot` walks them
    to find the live one and the already-offered waitlist ids."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, select(appointment_offers).where(
            appointment_offers.c.slot_at == db_values._coerce_dt(slot_dt),
            appointment_offers.c.service_id == servicio_id,
            appointment_offers.c.center_slug == centro_slug,
        ).order_by(appointment_offers.c.id)).fetchall(), "appointment_offers")


def live_offer_slots():
    """The distinct (service, center, slot) tuples that currently have a live
    'ofrecida' offer — `tick` sweeps exactly these."""
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, select(
            appointment_offers.c.slot_at, appointment_offers.c.service_id,
            appointment_offers.c.center_slug,
        ).distinct().where(appointment_offers.c.status == "ofrecida")).fetchall(),
            "appointment_offers")


def live_offer_times():
    """[(service_id, slot_at)] of the offers a patient can still claim right now: 'ofrecida' AND
    their window not over. The wizard's list of taken times reads this -- an offer holds its time
    like a soft lock for as long as it lives: an expired window (before the sweep marks it) or a
    rejected/accepted offer holds nothing."""
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, select(appointment_offers.c.service_id, appointment_offers.c.slot_at).where(
            appointment_offers.c.status == "ofrecida",
            appointment_offers.c.expires_at > func.now())).fetchall()
    return [(r["service_id"], r["slot_at"]) for r in rows]


def has_live_offer(servicio_id, centro_slug, slot_dt):
    """True if this slot already has an 'ofrecida' or 'aceptada' offer — the
    pre-check `release_slot` makes before starting an offer run. NOT atomic
    with the subsequent insert by design; the insert's ON CONFLICT
    (`create_offer`) is what actually settles a race (see `waitlist._create_
    offer`'s docstring)."""
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, select(literal_column("1")).select_from(appointment_offers).where(
            appointment_offers.c.slot_at == db_values._coerce_dt(slot_dt),
            appointment_offers.c.service_id == servicio_id,
            appointment_offers.c.center_slug == centro_slug,
            appointment_offers.c.status.in_(("ofrecida", "aceptada")),
        )).fetchone() is not None


def expire_offer(offer_id):
    """Marks a specific offer 'expirada' (its window lapsed without being
    accepted)."""
    with db._connect() as conn, conn.cursor() as cur:
        _set_offer_status(cur, offer_id, "expirada")


def expire_if_live(token):
    """The persistence half of `reject_offer` ("I don't want it"). One
    transaction: read the offer by token; if it is still 'ofrecida', mark it
    'expirada' and return `(offer, True)`; if it's already resolved
    (aceptada/expirada/vacante), return `(offer, False)` unchanged; if there
    is no such token, return `(None, False)`. The orchestrator reads
    `was_live` to decide whether to offer the slot to the next candidate — an
    already-resolved offer is confirmed, never reopened (idempotent reject)."""
    with db._connect() as conn, conn.cursor() as cur:
        o = db_wire._wire(_offer_by_token(cur, token), "appointment_offers")
        if not o:
            return (None, False)
        if o["estado"] != "ofrecida":
            return (o, False)
        _set_offer_status(cur, o["id"], "expirada")
        o["estado"] = "expirada"
        return (o, True)


def mark_slot_vacant(servicio_id, centro_slug, slot_dt):
    """No live offer and no one left to offer: the slot is marked 'vacante'
    (seen on a walk-in basis)."""
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, update(appointment_offers).where(
            appointment_offers.c.slot_at == db_values._coerce_dt(slot_dt),
            appointment_offers.c.service_id == servicio_id,
            appointment_offers.c.center_slug == centro_slug,
            appointment_offers.c.status == "ofrecida",
        ).values(status="vacante"))


def accept_offer(token, ahora):
    """The persistence half of `claim` — the patient clicked the email link
    within their window. One transaction, identical branching to the original
    inline `waitlist.claim`:
      (False, "no_existe")   no offer has this token
      (True,  oferta)        already 'aceptada' — idempotent
      (False, "vencida")     not 'ofrecida' any more, or the window expired
      (True,  oferta)        claimed: status -> 'aceptada' + the waitlist row
                             deactivated, `estado` mirrored on the returned dict
    The HSI reassignment is wired ON TOP of this in
    `appointment_offer._accept`, not here."""
    with db._connect() as conn, conn.cursor() as cur:
        o = db_wire._wire(_offer_by_token(cur, token), "appointment_offers")
        if not o:
            return (False, "no_existe")
        if o["estado"] == "aceptada":
            return (True, o)  # idempotent: already claimed
        if o["estado"] != "ofrecida" or ahora >= o["expires_at"]:
            return (False, "vencida")
        _set_offer_status(cur, o["id"], "aceptada")
        sqlcore.run(cur, update(waitlist).where(
            waitlist.c.id == o["lista_espera_id"]).values(active=False))
        o["estado"] = "aceptada"
        return (True, o)


# ---- statements shared by more than one function ---------------------------

def _offer_by_token(cur, token):
    """The raw `appointment_offers` row behind a token, on the caller's cursor
    (so a read-then-update stays in one transaction)."""
    return sqlcore.run(cur, select(appointment_offers).where(
        appointment_offers.c.token == token)).fetchone()


def _set_offer_status(cur, offer_id, status):
    sqlcore.run(cur, update(appointment_offers).where(
        appointment_offers.c.id == offer_id).values(status=status))
