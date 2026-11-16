"""core/db_hsi.py — the HSI statements of the persistence layer.

The durable sync journal (`hsi_outbox_*`: the idempotent enqueue, the `FOR UPDATE SKIP LOCKED`
lease, confirm / retry / conflict), the inbox that deduplicates remote observations, the mirror
tables and checkpoints, the sync-conflict log the automatic resolver reads (`hsi_open_conflicts`,
`hsi_resolve_conflict`, `hsi_recently_resolved`), the two booking-row writes the resolver makes
(`clear_hsi_link`, `assign_doctor_hsi`), the numbers of the «HSI» panel and the «Ocultar» marks of the
extension's panel of changes (`hide_hsi_change`, `hsi_hidden_marks`). Split out of `core/db.py`
in stage 4 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md): the statements are
Core, built from `core/schema_model.py`.

Callers write `db_hsi.hsi_outbox_claim(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below
as `db_wire._wire` / `db_values._json_clean`, a sibling as `db_x.<name>` and their own names bare, all at
CALL time, so a test that patches the owner patches what every caller uses.
"""

from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_booking
from app.core import db_wire
from app.core import db_values
from app.core import schema_model as sm
from app.core import sqlcore


def _doc(value):
    """A JSON document column that is nullable: `None` is the SQL NULL (the JSON `null` would
    be a value), anything else is cleaned of datetimes and stored as the document."""
    return sa.null() if value is None else db_values._json_clean(value)


# ---- durable HSI sync journal --------------------------------------------

def hsi_outbox_enqueue(entity_type, entity_id, operation, payload, idempotency_key):
    """Persist one outbound HSI intent.  Repeating the same key is a no-op.

    The caller must derive the key from the immutable Sana request/version,
    never from a retry attempt.  This is what lets replay be exactly-once from
    Sana's perspective even when a process dies between a remote response and
    its local acknowledgement.
    """
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        if entity_type == "appointment" and operation == "create":
            # A patient can change time while an earlier create is leased or
            # awaiting recovery. Its payload-derived key changes, but the
            # remote appointment identity must not. Serialize all producers
            # (approval, backfill and manual retry), not just one caller.
            sqlcore.run(cur, sa.select(sa.func.pg_advisory_xact_lock(
                sa.cast(sa.func.hashtext("hsi-create:" + str(entity_id)), sa.BigInteger))))
            outstanding = sqlcore.run(cur, sa.select(*t.c).where(
                t.c.entity_type == entity_type, t.c.entity_id == str(entity_id),
                t.c.operation == "create", t.c.state != "confirmed",
            ).order_by(t.c.id).limit(1).with_for_update()).fetchone()
            if outstanding is not None:
                return outstanding
        insert = _pg.insert(t).values(
            entity_type=entity_type, entity_id=str(entity_id), operation=operation,
            payload=db_values._json_clean(payload), idempotency_key=idempotency_key)
        return sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.idempotency_key], set_={"updated": sa.func.now()},
        ).returning(*t.c)).fetchone()


class StaleHsiReassignTarget(ValueError):
    """A producer snapshot no longer describes the locked local booking."""


def hsi_outbox_enqueue_reassign(sid, when, build_intent):
    """Queue the current linked move from one locked booking snapshot.

    The appointment event is committed with the local change.  Locking the
    booking before reading it fences a delayed producer for an older target.
    """
    t, bk, cp = sm.hsi_outbox, sm.booking_requests, sm.hsi_checkpoints
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(bk).where(bk.c.id == sid).with_for_update()).fetchone()
        target = db_values._coerce_dt(when)
        if (row is None or row["status"] != "aprobada"
                or not row.get("hsi_appointment_id")
                or target is None or row["appointment_at"] != target):
            raise StaleHsiReassignTarget("stale or unlinked HSI reassignment target")
        remote_id = str(row["hsi_appointment_id"])
        generation = db_booking._appointment_move_generation(cur, sid, target)
        checkpoint_row = sqlcore.run(cur, sa.select(cp.c.remote).where(
            cp.c.entity_type == "appointment", cp.c.entity_id == remote_id)).fetchone()
        checkpoint = checkpoint_row["remote"] if checkpoint_row else None
        current_intent = sqlcore.run(cur, sa.select(*t.c).where(
            t.c.entity_type == "appointment", t.c.entity_id == str(sid), t.c.operation == "reassign",
            t.c.payload["appointment_id"].astext == remote_id,
            t.c.payload["move_generation"].astext == str(generation),
        ).order_by(t.c.id.desc()).limit(1)).fetchone()
        if current_intent is not None:
            if current_intent["state"] in ("pending", "sending", "error"):
                return current_intent
            confirmed_result = current_intent.get("hsi_result") or {}
            confirmed_checkpoint = confirmed_result.get("checkpoint")
            if (current_intent["state"] == "confirmed"
                    and isinstance(confirmed_checkpoint, dict)
                    and bool(confirmed_checkpoint)
                    and confirmed_checkpoint == checkpoint
                    and str(confirmed_result.get("appointment_id") or "") == remote_id
                    and db_values._coerce_dt(confirmed_result.get("actual_turno_dt") or
                                      confirmed_result.get("scheduled_at") or
                                      confirmed_checkpoint.get("scheduled_at")) == target):
                sqlcore.run(cur, sa.update(bk).where(
                    bk.c.id == sid, bk.c.hsi_status.in_(("pendiente", "error")),
                    ~sa.exists().where(
                        t.c.entity_type == "appointment", t.c.entity_id == str(sid),
                        t.c.state.in_(("pending", "sending", "error", "conflict")),
                        t.c.id != current_intent["id"]),
                ).values(hsi_status="ok"))
                return current_intent
            if (current_intent["state"] == "conflict"
                    and current_intent["payload"].get("checkpoint") == checkpoint):
                return current_intent
        # A newer local move cannot carry the predecessor's old remote
        # checkpoint. Deliver the still-recoverable predecessor first; its
        # acknowledgement will queue this generation with the fresh remote
        # checkpoint under the same booking lock. A terminal conflict does
        # not block a corrected intent with a refreshed checkpoint.
        predecessor = sqlcore.run(cur, sa.select(*t.c).where(
            t.c.entity_type == "appointment", t.c.entity_id == str(sid),
            t.c.operation.in_(("create", "reassign")),
            t.c.state.in_(("pending", "sending", "error")),
            t.c.payload["move_generation"].astext.is_distinct_from(str(generation)),
            sa.or_(t.c.operation == "create", t.c.payload["appointment_id"].astext == remote_id),
        ).order_by(t.c.id).limit(1)).fetchone()
        if predecessor is not None:
            return predecessor
        payload, key = build_intent(db_wire._wire(row, "booking_requests"), checkpoint, generation)
        return sqlcore.run(cur, _pg.insert(t).values(
            entity_type="appointment", entity_id=str(sid), operation="reassign",
            payload=db_values._json_clean(payload), idempotency_key=key,
        ).on_conflict_do_update(
            index_elements=[t.c.idempotency_key], set_={"updated": sa.func.now()},
        ).returning(*t.c)).fetchone()


def hsi_outbox_claim(limit=20):
    """Atomically lease due work.  `SKIP LOCKED` supports multiple workers."""
    t = sm.hsi_outbox
    ready = (sa.select(t.c.id)
             .where(sa.or_(
                 sa.and_(t.c.state.in_(("pending", "error")), t.c.next_attempt_at <= sa.func.now()),
                 sa.and_(t.c.state == "sending", t.c.lease_expires_at < sa.func.now())))
             .order_by(t.c.next_attempt_at, t.c.id)
             .limit(limit)
             .with_for_update(skip_locked=True)
             .cte("ready"))
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            state="sending", attempts=t.c.attempts + 1,
            lease_expires_at=sa.func.now() + sa.literal_column("interval '5 minutes'", sa.Interval),
            updated=sa.func.now(),
        ).where(t.c.id == ready.c.id).returning(*t.c)).fetchall()


def hsi_outbox_claim_id(id_):
    """Atomically lease one specific outbox row for a synchronous handoff.

    Approval uses this after persisting its create intent.  It must use the
    same pending -> sending lease transition as the background worker before
    network I/O, rather than calling the replay implementation on a raw row.
    """
    t = sm.hsi_outbox
    ready = (sa.select(t.c.id)
             .where(t.c.id == id_, sa.or_(
                 sa.and_(t.c.state.in_(("pending", "error")), t.c.next_attempt_at <= sa.func.now()),
                 sa.and_(t.c.state == "sending", t.c.lease_expires_at < sa.func.now())))
             .with_for_update()
             .cte("ready"))
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            state="sending", attempts=t.c.attempts + 1,
            lease_expires_at=sa.func.now() + sa.literal_column("interval '5 minutes'", sa.Interval),
            updated=sa.func.now(),
        ).where(t.c.id == ready.c.id).returning(*t.c)).fetchone()


def hsi_outbox_reset(id_):
    """Rearma un intento para un reintento manual inmediato.

    El operador que aprieta «Reintentar» espera un intento AHORA: esto pisa el
    backoff exponencial (next_attempt_at futuro) y también rescata un
    'conflict' (p. ej. la agenda ya fue cargada en HSI después del fallo).
    Un 'confirmed' nunca se rearma — repetirlo duplicaría el turno clínico.
    Un lease 'sending' vivo tampoco: sigue siendo de su worker, y rearmarlo
    dejaría dos dueños. Sólo se rearma un lease vencido."""
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            state="pending", next_attempt_at=sa.func.now(), lease_expires_at=None,
            updated=sa.func.now(),
        ).where(t.c.id == id_, sa.or_(
            t.c.state.in_(("pending", "error", "conflict")),
            sa.and_(t.c.state == "sending", t.c.lease_expires_at < sa.func.now())),
        ).returning(*t.c)).fetchone()


def hsi_outbox_inflight(entity_type, entity_id):
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(t).where(
            t.c.entity_type == entity_type, t.c.entity_id == str(entity_id),
            t.c.state.in_(("pending", "sending", "error"))).limit(1)).fetchone() is not None


def hsi_outbox_outstanding(entity_type, entity_id, operation):
    """El intento NO liquidado más antiguo de una operación, o None.

    Todo estado salvo 'confirmed': pending/sending/error son reintento vivo y
    un 'conflict' espera al operador. En los dos casos el EMR puede tener ya
    ese turno (una respuesta perdida tras aceptarlo), así que un segundo
    intento de la misma operación no se acuña al lado: la clave deriva del
    payload, que incluye el horario, y un create con otro horario abriría un
    segundo turno clínico."""
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(*t.c).where(
            t.c.entity_type == entity_type, t.c.entity_id == str(entity_id),
            t.c.operation == operation, t.c.state != "confirmed",
        ).order_by(t.c.id).limit(1)).fetchone()


def hsi_outbox_confirm(id_, result):
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            state="confirmed", hsi_result=db_values._json_clean(result or {}), last_error=None,
            lease_expires_at=None, updated=sa.func.now(),
        ).where(t.c.id == id_).returning(*t.c)).fetchone()


class StaleHsiLease(Exception):
    """An older remote response cannot settle a newly claimed journal lease."""


def _same_hsi_slot(left, right):
    """Compare normalized identity/slot; state and version change on cancel."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    for key in ("appointment_id", "schedule_id"):
        if not left.get(key) or str(left[key]) != str(right.get(key) or ""):
            return False
    if (left.get("patient_id") is None) != (right.get("patient_id") is None):
        return False
    if left.get("patient_id") is not None and str(left["patient_id"]) != str(right["patient_id"]):
        return False
    old_time = db_values._coerce_dt(left.get("scheduled_at"))
    new_time = db_values._coerce_dt(right.get("scheduled_at"))
    return old_time is not None and old_time == new_time


def _text(value):
    return None if value is None else str(value)


def _remember_remote_appointment(cur, appointment_id, checkpoint):
    """The checkpoint and the mirror row of one remote appointment, in the caller's transaction."""
    sqlcore.run(cur, _checkpoint_upsert(
        "appointment", str(appointment_id), db_values._json_clean(checkpoint)))
    insert = _pg.insert(sm.hsi_appointments).values(
        hsi_appointment_id=str(appointment_id),
        hsi_diary_id=_text(checkpoint.get("schedule_id")),
        hsi_patient_id=_text(checkpoint.get("patient_id")),
        payload=db_values._json_clean(checkpoint))
    new = insert.excluded
    sqlcore.run(cur, insert.on_conflict_do_update(
        index_elements=[sm.hsi_appointments.c.hsi_appointment_id],
        set_={"hsi_diary_id": new.hsi_diary_id, "hsi_patient_id": new.hsi_patient_id,
              "payload": new.payload, "observed_at": sa.func.now()}))


def hsi_outbox_get(id_):
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(*t.c).where(t.c.id == id_)).fetchone()


def hsi_outbox_settle(item, state, result=None, error=None, delay_seconds=0,
                      remote_payload=None, booking_status=None,
                      first_local_create_status=None):
    """Settle one claimed replay attempt and all of its local effects atomically.

    Administrative id-only helpers remain separate. Replay must pass its claim:
    an expired lease that was reset or reclaimed cannot write an outcome. Lock
    booking before outbox, matching ``hsi_outbox_ack_appointment``; neither
    path holds an outbox lock while waiting for a booking lock.
    """
    if state not in ("confirmed", "error", "conflict"):
        raise ValueError("invalid HSI settlement state")
    t, bk, cp = sm.hsi_outbox, sm.booking_requests, sm.hsi_checkpoints
    payload = item.get("payload") or {}
    sid = payload.get("solicitud_id")
    is_cancel = item.get("operation") == "cancel" and sid is not None
    checkpoint = (result or {}).get("checkpoint") if state == "confirmed" else None
    appointment_id = (result or {}).get("appointment_id") if checkpoint else None
    with db._connect() as conn, conn.cursor() as cur:
        local_at_settlement = None
        if (booking_status is not None or is_cancel
                or first_local_create_status is not None) and sid is not None:
            local_at_settlement = sqlcore.run(cur, sa.select(bk.c.status, bk.c.hsi_appointment_id).where(
                bk.c.id == sid).with_for_update()).fetchone()
        current = sqlcore.run(cur, sa.select(*t.c).where(t.c.id == item["id"]).with_for_update()).fetchone()
        if (current is None or current["state"] != "sending"
                or current["attempts"] != item.get("attempts")):
            raise StaleHsiLease("HSI journal lease changed before settlement")
        if first_local_create_status is not None and (
                local_at_settlement is None
                or local_at_settlement["status"] != first_local_create_status
                or local_at_settlement["hsi_appointment_id"] is not None):
            # The no-connector decision was made from a read before this
            # transaction. A concurrent link/status change invalidates its
            # local-only proof; preserve the booking and retry after routing.
            state = "error"
            result = booking_status = None
            error = "unconfigured create changed before local-only settlement"
            delay_seconds = max(int(delay_seconds), 30)
        if is_cancel:
            # R's acknowledgement inserts corrective C2 under the same
            # booking lock. An older C1 is then redundant whatever its remote
            # response; it cannot replace B's checkpoint or clear C2's status.
            latest = sqlcore.run(cur, sa.select(cp.c.remote).where(
                cp.c.entity_type == "appointment",
                cp.c.entity_id == str(payload.get("appointment_id")))).fetchone()
            latest_checkpoint = latest["remote"] if latest else None
            c, r = t.alias("c"), t.alias("r")
            corrections = sqlcore.run(cur, sa.select(c.c.id, c.c.payload).select_from(
                c.join(r, r.c.id == sa.cast(c.c.payload["predecessor_intent_id"].astext, sa.BigInteger))
            ).where(
                c.c.entity_type == "appointment", c.c.entity_id == str(sid),
                c.c.operation == "cancel", c.c.id != item["id"],
                r.c.entity_type == c.c.entity_type, r.c.entity_id == c.c.entity_id,
                r.c.operation.in_(("create", "reassign")), r.c.state == "confirmed")).fetchall()
            current_corrections = [row for row in corrections
                                   if _same_hsi_slot(row["payload"].get("checkpoint"), latest_checkpoint)]
            authoritative_id = max((row["id"] for row in current_corrections), default=None)
            superseded = authoritative_id is not None and (
                not payload.get("predecessor_intent_id") or item["id"] < authoritative_id
                or not _same_hsi_slot(payload.get("checkpoint"), latest_checkpoint))
            if superseded:
                state = "confirmed"
                result = {"estado": "superseded", "reason": "newer cancel intent"}
                error = None
                checkpoint = appointment_id = booking_status = None
            else:
                # A leased or retryable R may already have moved the remote
                # appointment and may still acknowledge B. Until R settles,
                # C1's apparent success cannot truthfully mark the request ok.
                unsettled = sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(t).where(
                    t.c.entity_type == "appointment", t.c.entity_id == str(sid),
                    t.c.operation.in_(("create", "reassign")), t.c.state != "confirmed",
                ).limit(1)).fetchone()
                if unsettled is not None:
                    checkpoint = appointment_id = booking_status = None
                    if state == "confirmed" and (result or {}).get("estado") == "ok":
                        state = "error"
                        error = "cancel outcome awaits unsettled appointment move"
                        delay_seconds = max(int(delay_seconds), 30)
                elif state == "conflict":
                    booking_status = "error"
        if booking_status is not None and sid is not None:
            sqlcore.run(cur, sa.update(bk).where(bk.c.id == sid).values(
                hsi_status=booking_status,
                hsi_patient_id=sa.func.coalesce(_text((result or {}).get("patient_id")), bk.c.hsi_patient_id),
                hsi_appointment_id=sa.func.coalesce(_text((result or {}).get("appointment_id")),
                                                    bk.c.hsi_appointment_id)))
        if checkpoint and appointment_id is not None:
            _remember_remote_appointment(cur, appointment_id, checkpoint)
        if state == "confirmed":
            values = dict(state="confirmed", hsi_result=db_values._json_clean(result or {}), last_error=None,
                          lease_expires_at=None, updated=sa.func.now())
        elif state == "error":
            values = dict(state="error", last_error=str(error)[:2000],
                          next_attempt_at=sa.func.now() + sa.literal(int(delay_seconds), sa.Integer)
                          * sa.literal_column("interval '1 second'", sa.Interval),
                          lease_expires_at=None, updated=sa.func.now())
        else:
            values = dict(state="conflict", last_error=str(error)[:2000],
                          lease_expires_at=None, updated=sa.func.now())
        row = sqlcore.run(cur, sa.update(t).values(**values).where(
            t.c.id == item["id"]).returning(*t.c)).fetchone()
        if state == "conflict":
            sqlcore.run(cur, sa.insert(sm.hsi_sync_conflicts).values(
                entity_type=row["entity_type"], entity_id=row["entity_id"],
                direction=sa.literal_column("'outbound'"), reason=str(error)[:2000],
                local_payload=db_values._json_clean(payload), remote_payload=_doc(remote_payload)))
        return row


def hsi_outbox_ack_appointment(item, result, followup):
    """Attach a remote appointment and settle its intent in one transaction.

    ``followup`` builds a cancel/reassign intent from the locked, CURRENT
    request. A patient move or cancellation may have committed while the
    remote call was in flight; the remote result must not undo that decision.
    Any failed insert rolls the link and acknowledgement back together.
    """
    t, bk = sm.hsi_outbox, sm.booking_requests
    payload = item["payload"]
    sid = payload["solicitud_id"]
    operation = item["operation"]
    if operation not in ("create", "reassign"):
        raise ValueError("appointment acknowledgement requires create or reassign")
    appointment_id = result.get("appointment_id")
    if not appointment_id:
        raise ValueError("remote appointment id is required")
    with db._connect() as conn, conn.cursor() as cur:
        prior = sqlcore.run(cur, sa.select(bk).where(bk.c.id == sid).with_for_update()).fetchone()
        if prior is None:
            raise ValueError("booking request disappeared before HSI acknowledgement")
        current_item = sqlcore.run(cur, sa.select(*t.c).where(
            t.c.id == item["id"]).with_for_update()).fetchone()
        if (current_item is None or current_item["state"] != "sending"
                or current_item["attempts"] != item["attempts"]):
            raise StaleHsiLease("HSI journal lease changed before acknowledgement")
        linked_id = prior.get("hsi_appointment_id")
        if linked_id and linked_id != str(appointment_id):
            raise ValueError("booking request already links another HSI appointment")
        desired = prior.get("appointment_at")
        sent = db_values._coerce_dt(payload.get("when"))
        moved = desired is not None and sent is not None and desired != sent
        cancelled = prior["status"] == "cancelada"
        if not cancelled and prior["status"] != "aprobada":
            raise ValueError("booking request is no longer approved")
        needs_followup = cancelled or moved
        actual = db_values._coerce_dt(result.get("actual_turno_dt") or payload.get("when"))
        new_time = desired if needs_followup else actual
        updated = sqlcore.run(cur, sa.update(bk).where(bk.c.id == sid).values(
            hsi_patient_id=sa.func.coalesce(_text(result.get("patient_id")), bk.c.hsi_patient_id),
            hsi_appointment_id=sa.func.coalesce(str(appointment_id), bk.c.hsi_appointment_id),
            hsi_status="pendiente" if needs_followup else "ok",
        ).returning(*bk.c)).fetchone()
        if not needs_followup and new_time != desired:
            # The EMR confirmed a different time than the one asked for. The
            # time column has one writer: the common reassignment path, in
            # this transaction, which also records the audit event.
            db_booking.reassign_appointment(sid, new_time, {"estado": "ok"},
                                    actor="hsi:journal", cur=cur)
        checkpoint = result.get("checkpoint")
        if checkpoint:
            _remember_remote_appointment(cur, appointment_id, checkpoint)
        if needs_followup:
            followup_row = db_wire._wire(updated, "booking_requests")
            if not cancelled:
                followup_row["_move_generation"] = db_booking._appointment_move_generation(
                    cur, sid, updated["appointment_at"])
            next_op, next_payload, next_key = followup(
                followup_row, "cancel" if cancelled else "reassign", checkpoint)
            next_item = sqlcore.run(cur, _pg.insert(t).values(
                entity_type="appointment", entity_id=str(sid), operation=next_op,
                payload=db_values._json_clean(next_payload), idempotency_key=next_key,
            ).on_conflict_do_nothing(index_elements=[t.c.idempotency_key]).returning(t.c.id)).fetchone()
            if cancelled and next_item:
                # A cancel that conflicted against R's in-flight move is now
                # represented by the newer checkpoint-specific cancel. Keep
                # its historical result but remove obsolete operator work.
                old_checkpoint = sa.literal(db_values._json_clean(payload.get("checkpoint")), _pg.JSONB)
                for old_cancel in sqlcore.run(cur, sa.update(t).values(
                        state="confirmed",
                        hsi_result=db_values._json_clean({"estado": "superseded", "reason": "corrective cancel queued"}),
                        last_error=None, updated=sa.func.now(),
                ).where(
                    t.c.entity_type == "appointment", t.c.entity_id == str(sid),
                    t.c.operation == "cancel", t.c.state.in_(("conflict", "error")),
                    t.c.id > item["id"], t.c.id < next_item["id"],
                    t.c.payload["checkpoint"] == old_checkpoint,
                ).returning(t.c.payload)).fetchall():
                    conflicts = sm.hsi_sync_conflicts
                    sqlcore.run(cur, sa.update(conflicts).values(
                        resolved_at=sa.func.now(), resolved_by="hsi:journal",
                        resolution=db_values._json_clean({"accion": "superseded", "texto": "Corrective cancel queued"}),
                    ).where(
                        conflicts.c.entity_type == "appointment", conflicts.c.entity_id == str(sid),
                        conflicts.c.direction == "outbound", conflicts.c.resolved_at.is_(None),
                        conflicts.c.local_payload == sa.literal(
                            db_values._json_clean(old_cancel["payload"]), _pg.JSONB)))
        return sqlcore.run(cur, sa.update(t).values(
            state="confirmed", hsi_result=db_values._json_clean(result), last_error=None,
            lease_expires_at=None, updated=sa.func.now(),
        ).where(t.c.id == item["id"]).returning(*t.c)).fetchone()


def hsi_outbox_retry(id_, error, delay_seconds):
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            state="error", last_error=str(error)[:2000],
            next_attempt_at=sa.func.now() + sa.literal(int(delay_seconds), sa.Integer)
            * sa.literal_column("interval '1 second'", sa.Interval),
            lease_expires_at=None, updated=sa.func.now(),
        ).where(t.c.id == id_).returning(*t.c)).fetchone()


def hsi_outbox_conflict(id_, reason, local_payload=None, remote_payload=None):
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.update(t).values(
            state="conflict", last_error=str(reason)[:2000], updated=sa.func.now(),
        ).where(t.c.id == id_).returning(*t.c)).fetchone()
        if row:
            sqlcore.run(cur, sa.insert(sm.hsi_sync_conflicts).values(
                entity_type=row["entity_type"], entity_id=row["entity_id"],
                direction=sa.literal_column("'outbound'"), reason=str(reason)[:2000],
                local_payload=_doc(local_payload), remote_payload=_doc(remote_payload)))
        return row


def hsi_inbox_record(source_key, entity_type, entity_id, payload):
    """Durably deduplicate a remote observation before reconciling it."""
    t = sm.hsi_inbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, _pg.insert(t).values(
            source_key=source_key, entity_type=entity_type, entity_id=str(entity_id),
            payload=db_values._json_clean(payload),
        ).on_conflict_do_nothing(index_elements=[t.c.source_key]).returning(*t.c)).fetchone()


def hsi_inbox_finish(source_key, state='confirmed', conflict=None):
    t = sm.hsi_inbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            state=state, conflict=_doc(conflict), reconciled_at=sa.func.now(),
        ).where(t.c.source_key == source_key).returning(*t.c)).fetchone()


def hsi_mirror_patient(hsi_patient_id, dni, payload):
    t = sm.hsi_patients
    dni = str(dni).strip() or None if dni is not None else None  # an empty DNI is no data
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            hsi_patient_id=str(hsi_patient_id), dni=dni, payload=db_values._json_clean(payload))
        new = insert.excluded
        # An observation without a DNI (an appointment listing's stub, which carries only the
        # patient's id) registers the id for the patient poll but never overwrites what the poll
        # already learned: neither the DNI nor the full record.
        return sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.hsi_patient_id],
            set_={"dni": sa.func.coalesce(new.dni, t.c.dni), "payload": new.payload,
                  "observed_at": sa.func.now()},
            where=sa.or_(
                sa.and_(new.dni.is_not(None),
                        sa.or_(t.c.dni.is_distinct_from(new.dni),
                               t.c.payload.is_distinct_from(new.payload))),
                # No DNI on a row with none: only a record that adds something replaces the
                # payload. A stub (`{"id": ...}`) is contained in what is stored, so it never
                # swaps a full record for itself (that flip-flop changed the row every tick).
                sa.and_(new.dni.is_(None), t.c.dni.is_(None),
                        ~t.c.payload.contains(new.payload))),
        ).returning(*t.c)).fetchone()


def hsi_mirror_schedule(hsi_diary_id, hsi_professional_id, payload):
    t = sm.hsi_doctor_schedules
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            hsi_diary_id=str(hsi_diary_id),
            hsi_professional_id=str(hsi_professional_id) if hsi_professional_id is not None else None,
            payload=db_values._json_clean(payload))
        new = insert.excluded
        return sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.hsi_diary_id],
            set_={"hsi_professional_id": new.hsi_professional_id, "payload": new.payload,
                  "observed_at": sa.func.now()},
            where=sa.or_(t.c.hsi_professional_id.is_distinct_from(new.hsi_professional_id),
                         t.c.payload.is_distinct_from(new.payload)),
        ).returning(*t.c)).fetchone()


def hsi_mirror_appointment(hsi_appointment_id, hsi_diary_id, hsi_patient_id, payload):
    t = sm.hsi_appointments
    with db._connect() as conn, conn.cursor() as cur:
        insert = _pg.insert(t).values(
            hsi_appointment_id=str(hsi_appointment_id),
            hsi_diary_id=str(hsi_diary_id) if hsi_diary_id is not None else None,
            hsi_patient_id=str(hsi_patient_id) if hsi_patient_id is not None else None,
            payload=db_values._json_clean(payload))
        new = insert.excluded
        row = sqlcore.run(cur, insert.on_conflict_do_update(
            index_elements=[t.c.hsi_appointment_id],
            set_={"hsi_diary_id": new.hsi_diary_id, "hsi_patient_id": new.hsi_patient_id,
                  "payload": new.payload, "observed_at": sa.func.now()},
            where=sa.or_(t.c.hsi_diary_id.is_distinct_from(new.hsi_diary_id),
                         t.c.hsi_patient_id.is_distinct_from(new.hsi_patient_id),
                         t.c.payload.is_distinct_from(new.payload)),
        ).returning(*t.c)).fetchone()
        sqlcore.run(cur, _checkpoint_upsert(
            "appointment", str(hsi_appointment_id), db_values._json_clean(payload)))
        return row


def _checkpoint_upsert(entity_type, entity_id, remote):
    """`INSERT .. ON CONFLICT (entity_type, entity_id) DO UPDATE` of one checkpoint (the
    statement, not run: `hsi_checkpoint_set` adds the `RETURNING`)."""
    t = sm.hsi_checkpoints
    insert = _pg.insert(t).values(entity_type=entity_type, entity_id=entity_id, remote=remote)
    return insert.on_conflict_do_update(
        index_elements=[t.c.entity_type, t.c.entity_id],
        set_={"remote": insert.excluded.remote, "observed_at": sa.func.now()})


def hsi_checkpoint_get(entity_type, entity_id):
    t = sm.hsi_checkpoints
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(t.c.remote).where(
            t.c.entity_type == entity_type, t.c.entity_id == str(entity_id))).fetchone()
        return row["remote"] if row else None


def hsi_checkpoint_set(entity_type, entity_id, remote):
    t = sm.hsi_checkpoints
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, _checkpoint_upsert(
            entity_type, str(entity_id), db_values._json_clean(remote)).returning(*t.c)).fetchone()


def hsi_known_patient_ids(limit=500):
    t = sm.hsi_patients
    with db._connect() as conn, conn.cursor() as cur:
        return [r["hsi_patient_id"] for r in sqlcore.run(cur, sa.select(t.c.hsi_patient_id)
                                                         .order_by(t.c.observed_at.desc())
                                                         .limit(limit)).fetchall()]


def hsi_booking_requests_by_appointment(hsi_appointment_id):
    t = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, sa.select(t).where(
            t.c.hsi_appointment_id == str(hsi_appointment_id)).order_by(t.c.created.desc())
        ).fetchall(), "booking_requests")


def hsi_appointments_by_dni(dni):
    """Remote HSI appointments for an authenticated Sana patient.

    These are read-only imported records until an operator links them to a
    local solicitud.  Showing them prevents a patient from seeing an empty
    account when the appointment originated in HSI.
    """
    a, p = sm.hsi_appointments.alias("a"), sm.hsi_patients.alias("p")
    s, q = sm.hsi_doctor_schedules.alias("s"), sm.booking_requests.alias("q")
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(
            a.c.hsi_appointment_id, a.c.payload.label("appointment"), s.c.payload.label("schedule"),
        ).select_from(
            a.join(p, p.c.hsi_patient_id == a.c.hsi_patient_id)
            .outerjoin(s, s.c.hsi_diary_id == a.c.hsi_diary_id)
        ).where(
            p.c.dni == str(dni),
            sa.func.coalesce(a.c.payload["state"].astext, "unknown") != "cancelled",
            ~sa.exists().where(q.c.hsi_appointment_id == a.c.hsi_appointment_id),
        ).order_by(a.c.observed_at.desc())).fetchall()


# The appointment event that records a request linked after the fact to an HSI appointment it
# was loaded as by hand (the desk's Chrome extension knows no HSI id): `hsi_link_request`.
HSI_LINKED = "hsi_vinculada"


def hsi_diary_professional(hsi_diary_id):
    """The professional's name the schedule mirror holds for one HSI diary, or None. Read by
    `hsi_journal._link_unlinked_request`."""
    s = sm.hsi_doctor_schedules
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(s.c.payload["professional_name"].astext.label("name")).where(
            s.c.hsi_diary_id == _text(hsi_diary_id))).fetchone()
        return row["name"] if row else None


def hsi_mirror_patient_dni(hsi_patient_id):
    """The DNI the patient mirror holds for one HSI patient, or None. Read by
    `hsi_journal._link_unlinked_request` when an appointment's own patient carries none."""
    p = sm.hsi_patients
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(p.c.dni).where(
            p.c.hsi_patient_id == _text(hsi_patient_id))).fetchone()
        return row["dni"] if row else None


def hsi_unlinked_approved_at(dni, when):
    """Approved requests of `dni` at the minute of `when` (an aware datetime) that link no HSI
    appointment yet — the coarse candidates for an HSI appointment observed without a local link
    (the caller narrows them to the diary's doctor once it resolves it). Wire rows, oldest first."""
    bk = sm.booking_requests
    start = when.replace(second=0, microsecond=0)
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, sa.select(bk).where(
            bk.c.dni == str(dni), bk.c.status == "aprobada", bk.c.hsi_appointment_id.is_(None),
            bk.c.appointment_at >= start, bk.c.appointment_at < start + timedelta(minutes=1),
        ).order_by(bk.c.id)).fetchall(), "booking_requests")


def hsi_unlinked_approved_minutes(since):
    """The `appointment_at` of every approved request from `since` (an aware datetime) on that
    links no HSI appointment yet: ONE read per journal tick, so an unchanged HSI appointment
    replayed every tick (most are the hospital's own) is first looked up in memory by its minute
    (`hsi_journal._RelinkPool`) and only a minute with a candidate pays the per-appointment
    queries of `hsi_journal._link_unlinked_request`."""
    bk = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        return [r["appointment_at"] for r in sqlcore.run(cur, sa.select(bk.c.appointment_at).where(
            bk.c.status == "aprobada", bk.c.hsi_appointment_id.is_(None),
            bk.c.appointment_at >= since)).fetchall()]


def hsi_link_request(sid, hsi_appointment_id, hsi_patient_id, dni=None, medico_id=None, minute=None,
                     actor="hsi:inbound"):
    """Links approved request `sid` to the HSI appointment it already is (`hsi_status` "ok") and
    records the `HSI_LINKED` event, in one transaction. Writes nothing to HSI. `dni`, `medico_id`
    and `minute` (an aware datetime; its seconds are ignored) re-assert, now under the row lock,
    the match the candidate search made — a request the desk moved or reassigned between the
    search and the lock is refused, never linked at its old place. Returns `(wire row, None)`, or
    `(None, reason)` when the link would not be safe: the request is no longer approved, already
    links an appointment or no longer matches ("not_linkable"), another request links this
    appointment ("appointment_already_linked"), or the journal is still sending an appointment
    intent of the request (pending, sending, error — `hsi_outbox_inflight`'s states), which could
    open the same turno in HSI a second time ("local_intent_unsettled"). An intent HSI refused for
    good ('conflict') does not block: that refusal is exactly why the desk loads it by hand."""
    bk, ob = sm.booking_requests, sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(db_booking._LOCK_ROW.run(cur, sid=sid).fetchone(), "booking_requests")
        if not row or row.get("estado") != "aprobada" or row.get("hsi_appointment_id"):
            return None, "not_linkable"
        actual = db_values._coerce_dt(row.get("turno_dt"))
        if (dni is not None and str(row.get("dni") or "") != str(dni)
                or medico_id is not None and str(row.get("medico_asignado_id") or "") != str(medico_id)
                or minute is not None and (not actual or actual.replace(second=0, microsecond=0)
                                           != minute.replace(second=0, microsecond=0))):
            return None, "not_linkable"
        if sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(bk).where(
                bk.c.hsi_appointment_id == str(hsi_appointment_id)).limit(1)).fetchone():
            return None, "appointment_already_linked"
        if sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(ob).where(
                ob.c.entity_type == "appointment", ob.c.entity_id == str(sid),
                ob.c.state.in_(("pending", "sending", "error"))).limit(1)).fetchone():
            return None, "local_intent_unsettled"
        updated = db_wire._wire(sqlcore.run(cur, sa.update(bk).where(bk.c.id == sid).values(
            hsi_appointment_id=str(hsi_appointment_id),
            hsi_patient_id=sa.func.coalesce(bk.c.hsi_patient_id, _text(hsi_patient_id)),
            hsi_status="ok",
        ).returning(*bk.c)).fetchone(), "booking_requests")
        db_booking._log_appointment_event(cur, sid, HSI_LINKED, actor, row.get("estado"), row.get("estado"),
                                          row.get("turno_dt"), row.get("turno_dt"),
                                          row.get("asistencia"), row.get("asistencia"))
        return updated, None


def hsi_future_unlinked_appointments(from_day):
    """Mirrored HSI appointments from `from_day` ("YYYY-MM-DD") on that are not cancelled and
    belong to no local request, with the payload of their diary's professional (None when the
    diary is unknown). The coarse cut is on the ISO text of `scheduled_at`; the caller parses it
    exactly, so one malformed row never fails the query. Read by `modules/turnos/hsi_busy.py`
    to keep an hour created straight in HSI from being offered twice."""
    a, s, q = sm.hsi_appointments.alias("a"), sm.hsi_doctor_schedules.alias("s"), sm.booking_requests.alias("q")
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(
            a.c.hsi_appointment_id, a.c.hsi_diary_id, a.c.payload.label("appointment"),
            s.c.payload.label("schedule"),
        ).select_from(
            a.outerjoin(s, s.c.hsi_diary_id == a.c.hsi_diary_id)
        ).where(
            sa.func.coalesce(a.c.payload["state"].astext, "unknown") != "cancelled",
            sa.func.coalesce(a.c.payload["scheduled_at"].astext, "") >= str(from_day),
            ~sa.exists().where(q.c.hsi_appointment_id == a.c.hsi_appointment_id),
        ).order_by(a.c.hsi_appointment_id)).fetchall()


def hsi_conflict(entity_type, entity_id, direction, reason, local_payload=None, remote_payload=None):
    t = sm.hsi_sync_conflicts
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.insert(t).values(
            entity_type=entity_type, entity_id=str(entity_id), direction=direction,
            reason=str(reason)[:2000], local_payload=_doc(local_payload),
            remote_payload=_doc(remote_payload),
        ).returning(*t.c)).fetchone()


def hsi_conflict_open(entity_type, entity_id, reason):
    """Whether an unresolved conflict of `entity_id` with exactly this reason exists — so a link the
    journal retries every tick (`hsi_journal._relink_unchanged`) opens it once, not once a tick."""
    t = sm.hsi_sync_conflicts
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(t).where(
            t.c.entity_type == entity_type, t.c.entity_id == str(entity_id),
            t.c.reason == str(reason)[:2000], t.c.resolved_at.is_(None)).limit(1)).fetchone() is not None


def hsi_sync_status(limit=100):
    """Observable recovery state for the authenticated operations API."""
    outbox, conflicts = sm.hsi_outbox, sm.hsi_sync_conflicts
    with db._connect() as conn, conn.cursor() as cur:
        counts = {r["state"]: r["count"] for r in sqlcore.run(cur, sa.select(
            outbox.c.state, sa.func.count().label("count")).group_by(outbox.c.state)).fetchall()}
        pending = sqlcore.run(cur, sa.select(outbox).where(outbox.c.state != "confirmed")
                              .order_by(outbox.c.updated.desc()).limit(limit)).fetchall()
        open_conflicts = sqlcore.run(cur, sa.select(conflicts).where(conflicts.c.resolved_at.is_(None))
                                     .order_by(conflicts.c.created.desc()).limit(limit)).fetchall()
        return {"counts": counts, "outbox": pending, "conflicts": open_conflicts}


# ---- resolutor automático de conflictos (hsi_resolver.py) -----------------

def hsi_open_conflicts(limit=500):
    """Conflictos sin resolver, del más nuevo al más viejo (el resolutor
    deduplica quedándose con el primero que ve = el más reciente)."""
    t = sm.hsi_sync_conflicts
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(t.c.resolved_at.is_(None))
                           .order_by(t.c.created.desc(), t.c.id.desc()).limit(limit)).fetchall()


def hsi_resolve_conflict(id_, resolucion, resuelto_por="resolver"):
    """Cierra un conflicto dejando registrado qué se hizo (jsonb resolution).
    Nunca re-cierra: un conflicto ya resuelto no se pisa."""
    t = sm.hsi_sync_conflicts
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.update(t).values(
            resolved_at=sa.func.now(), resolution=db_values._json_clean(resolucion or {}),
            resolved_by=resuelto_por,
        ).where(t.c.id == id_, t.c.resolved_at.is_(None)).returning(*t.c)).fetchone()


def hsi_recently_resolved(limit=40):
    t = sm.hsi_sync_conflicts
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(
            t.c.resolved_at.is_not(None), t.c.resolution.is_not(None),
        ).order_by(t.c.resolved_at.desc()).limit(limit)).fetchall()


def clear_hsi_link(sid, hsi_estado):
    """Suelta el vínculo de una solicitud con un appointment de HSI que no le
    pertenece (ids duplicados tras re-crear la instancia, turno borrado en
    HSI).  El nuevo hsi_estado lo decide el resolutor: 'pendiente' re-encola
    un turno futuro; un histórico conserva su estado."""
    t = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.update(t).values(
            hsi_appointment_id=None, hsi_patient_id=None, hsi_status=hsi_estado,
        ).where(t.c.id == sid).returning(*t.c)).fetchone(), "booking_requests")


def assign_doctor_hsi(sid, medico, sobreturno=None, medico_id=None):
    """El resolutor reasignó el turno a otro profesional (o lo marcó
    sobreturno) para poder agendarlo en HSI. Nombre e id van juntos
    (2026-10-23): el resolutor ya resolvió el nombre de HSI contra el catálogo
    (`hsi_resolver._asignar_profesional`) — id + nombre del catálogo si es UNO,
    si no el nombre tal cual con id NULL."""
    t = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.update(t).values(
            assigned_doctor=medico, assigned_doctor_id=str(medico_id or "").strip() or None,
            overbooking=sa.func.coalesce(sobreturno, t.c.overbooking),
        ).where(t.c.id == sid).returning(*t.c)).fetchone(), "booking_requests")


def hsi_outbox_by_entity(entity_id, states=("conflict",)):
    t = sm.hsi_outbox
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(t).where(
            t.c.entity_type == sa.literal_column("'appointment'"), t.c.entity_id == str(entity_id),
            t.c.state.in_(list(states))).order_by(t.c.id.desc())).fetchall()


def hsi_metrics():
    """Los números del panel «HSI»: cuántos turnos aprobados están realmente
    agendados en HSI (futuros = lo accionable; total = histórico) + el estado
    de la cola durable."""
    requests, outbox = sm.booking_requests, sm.hsi_outbox
    future = requests.c.appointment_at >= sa.func.now()
    with db._connect() as conn, conn.cursor() as cur:
        metricas = dict(sqlcore.run(cur, sa.select(
            sa.func.count().filter(future).label("futuras"),
            sa.func.count().filter(future, requests.c.hsi_status == "ok").label("futuras_ok"),
            sa.func.count().label("total"),
            sa.func.count().filter(requests.c.hsi_status == "ok").label("total_ok"),
        ).where(requests.c.status == "aprobada", requests.c.appointment_at.is_not(None))).fetchone())
        metricas["outbox"] = {r["state"]: r["n"] for r in sqlcore.run(cur, sa.select(
            outbox.c.state, sa.func.count().label("n")).group_by(outbox.c.state)).fetchall()}
        return metricas


def hsi_last_confirmed():
    """When the journal last confirmed a transmission to HSI (aware datetime), or None when it never
    has. Its own function: `hsi_metrics()` is the exact dict the «HSI» panel reads.

    `confirmed` also closes an item that never travelled: one cancelled before it was sent («skip»)
    and one a newer intent replaced («superseded»). Those are not transmissions — with HSI down they
    would keep the date fresh while nothing arrives."""
    outbox = sm.hsi_outbox
    outcome = sa.func.coalesce(outbox.c.hsi_result["estado"].astext, "")
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, sa.select(sa.func.max(outbox.c.updated).label("ultima")).where(
            outbox.c.state == "confirmed", outcome.notin_(("skip", "superseded")))).fetchone()
        return (row or {}).get("ultima")


# ---- «Ocultar» on the extension's panel of changes ------------------------
# A change HSI refuses to take (a closed agenda, a turno that has passed) is hidden for the whole desk
# (2026-11-05). The mark is an ordinary history row of the turno -- who hid it and when stay there --
# and its *_to columns are the state it hid: the next change of state, time or attendance is another
# change, and shows again. Moved here from core/db_booking.py on 2026-11-06 (that module is held to
# 1500 lines by test_db_domain_modules); the row is still written by db_booking._log_appointment_event.
HSI_HIDDEN = "hsi_oculto"

_events = sm.appointment_events
_HSI_HIDDEN_MARKS = sqlcore.prepare(sa.select(
    _events.c.request_id, _events.c.status_to, _events.c.appointment_to, _events.c.attendance_to).where(
    _events.c.request_id.in_(sa.bindparam("ids", expanding=True, type_=_events.c.request_id.type)),
    _events.c.kind == sa.literal_column("'%s'" % HSI_HIDDEN)).order_by(_events.c.id))


def hide_hsi_change(sid, actor):
    """Records that `actor` hid request `sid`, as it is now, from the extension's panel of changes.
    Returns the row hidden, or None when there is none; nothing else of the turno changes."""
    with db._connect() as conn, conn.cursor() as cur:
        row = db_wire._wire(db_booking._LOCK_ROW.run(cur, sid=sid).fetchone(), "booking_requests")
        if not row:
            return None
        db_booking._log_appointment_event(cur, sid, HSI_HIDDEN, actor, row.get("estado"), row.get("estado"),
                                          row.get("turno_dt"), row.get("turno_dt"),
                                          row.get("asistencia"), row.get("asistencia"))
        return row


def hsi_hidden_marks(ids):
    """`{request id: (status, appointment, attendance)}`: the state each request was in when it was
    last hidden from the extension's panel; requests never hidden are absent."""
    ids = [int(i) for i in ids or ()]
    if not ids:
        return {}
    with db._connect() as conn, conn.cursor() as cur:
        return {r["request_id"]: (r["status_to"], r["appointment_to"], r["attendance_to"])
                for r in _HSI_HIDDEN_MARKS.run(cur, ids=ids).fetchall()}


def hsi_change_hidden(row, mark):
    """Whether a wire `row` is still in the state its hide `mark` recorded."""
    return bool(row and mark) and mark == (row.get("estado"), row.get("turno_dt"), row.get("asistencia"))
