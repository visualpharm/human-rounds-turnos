"""Replayable, scoped PostgreSQL notifications for the day queue.

``day_queue_events`` is the durable source of truth. PostgreSQL ``NOTIFY``
only wakes an SSE connection quickly; a missed notification, a new ASGI
worker, and an EventSource reconnect all converge by replaying rows after the
browser's ``Last-Event-ID``. Stored events contain routing metadata only,
never patient or attendance data.
"""

from __future__ import annotations

import asyncio
import json
import logging

import anyio
import sqlalchemy as sa

from app.core import db, log_pii, sqlcore
from app.core import db_slots

_CHANNEL = "turnos_day_queue"
_KEEPALIVE_SECONDS = 15


class _Listener:
    """One non-blocking LISTEN connection for one SSE response."""

    @classmethod
    async def open(cls):
        import psycopg

        url = db._db_url()
        if not url:
            raise db.DbUnavailable("DATABASE_URL no configurada")
        listener = cls()
        try:
            listener._conn = await psycopg.AsyncConnection.connect(
                url, autocommit=True, connect_timeout=5,
            )
        except psycopg.Error as exc:
            # This connection is outside the pool, so the pooled `with` never translates its
            # failures: a connect timeout or refusal is the same «database unavailable» (503).
            unavailable = db._unavailable_error(exc)
            if unavailable is None:
                raise
            raise unavailable from exc
        try:
            await sqlcore.run_async(listener._conn, sa.text("LISTEN " + sqlcore.quote_identifier(_CHANNEL)))
        except BaseException as exc:
            await close_quietly(listener)
            unavailable = db._unavailable_error(exc)
            if unavailable is None:
                raise
            raise unavailable from exc
        return listener

    async def wait(self, timeout):
        async for notification in self._conn.notifies(
                timeout=timeout, stop_after=1):
            return notification
        return None

    async def close(self):
        await self._conn.close()


async def close_quietly(listener):
    """Close the LISTEN connection without ever replacing the outcome that led here.

    A close that raises (the connection is already gone) is logged by type and swallowed, so it
    cannot turn the 503 of the first failure into a 500, nor hide the error a stream re-raises.
    The close is shielded from cancellation: when the request is being cancelled it still
    finishes, and closing twice is harmless."""
    with anyio.CancelScope(shield=True):
        try:
            await listener.close()
        except Exception as exc:  # noqa: BLE001 — the close must never raise
            logging.getLogger("uvicorn.error").warning(
                "day-queue listener close failed: %s", log_pii.exc(exc))


def _is_outage(exc):
    """True when `exc` means the database stopped answering: the pooled reads' `DbUnavailable`
    family, or the LISTEN connection's own psycopg error that `db._unavailable_error` classifies
    as one (this connection is not translated by a pooled `with`). A deadlock, a protocol
    violation and any other error are not outages and keep their normal path."""
    return isinstance(exc, db.UNAVAILABLE_ERRORS) or db._unavailable_error(exc) is not None


def _cursor(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _visible(identity, event):
    """Mirror the domain's center and doctor ownership gates exactly."""
    centers = identity.get("centros_permitidos")
    if centers is not None and (event.get("center_slug") or "") not in centers:
        return False
    if identity.get("rol") != "medico":
        return True

    medico_id = identity.get("medico_id")
    if not medico_id:
        return False
    # `_belongs_to` is the established id-first/name-fallback ownership rule.
    # Duplicating it here would let name-only historic appointments drift.
    from app.modules.turnos import doctor_appointments

    return doctor_appointments._belongs_to(
        {
            "medico_asignado_id": event.get("assigned_doctor_id"),
            "medico_asignado": event.get("assigned_doctor"),
        },
        medico_id,
        identity.get("medico_nombre") or "",
    )


def _frame(event_name, data, event_id=None):
    lines = []
    if event_id is not None:
        lines.append("id: %s" % int(event_id))
    lines.append("event: %s" % event_name)
    lines.append("data: %s" % json.dumps(data, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


async def open_feed(last_event_id=None):
    """Everything the feed needs from the database before its response starts: the LISTEN
    connection and the starting cursor. Returns `(listener, cursor)`.

    A database that cannot be reached, or that drops between the connect and the cursor read,
    is the standard 503 (`DbUnavailable`) and EventSource retries; the listener is closed on
    that path. A first connection reconciles the server-rendered page at `ready`, so it starts at
    the current high-water mark rather than replaying the entire historical log. Reconnects
    replay after the SSE cursor."""
    listener = await _Listener.open()
    try:
        cursor = (await asyncio.to_thread(db_slots.day_queue_event_high_water)
                  if last_event_id is None else _cursor(last_event_id))
    except BaseException:
        await close_quietly(listener)
        raise
    return listener, cursor


async def stream(listener, cursor, identity, disconnected):
    """Yield a reconnect-safe SSE feed scoped before browser delivery.

    `listener` and `cursor` come from `open_feed`, awaited by the route BEFORE the response
    starts. A database lost once the stream is running ends the stream cleanly with one log
    line: the headers are already sent, and the browser's reconnect replays from `Last-Event-ID`.
    Errors that are not an outage keep their normal path."""
    try:
        yield _frame("ready", {"cursor": cursor}, cursor)
        while not await disconnected():
            pending = await asyncio.to_thread(db_slots.day_queue_events_after, cursor)
            if pending:
                for event in pending:
                    cursor = int(event["id"])
                    if _visible(identity, event):
                        yield _frame(
                            "queue-update",
                            {"day": event["appointment_day"].isoformat()},
                            cursor,
                        )
                continue
            notification = await listener.wait(_KEEPALIVE_SECONDS)
            if notification is None:
                yield b": keepalive\n\n"
    except Exception as exc:
        if not _is_outage(exc):
            raise
        logging.getLogger("uvicorn.error").warning(
            "day-queue event stream ended, database unavailable: %s", log_pii.exc(exc))
    finally:
        await close_quietly(listener)
