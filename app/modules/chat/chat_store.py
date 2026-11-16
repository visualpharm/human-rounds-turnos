"""Persistence boundary for chat orchestration and tool execution.

The chat engine and tool registry own orchestration, privacy handling, session
rollover, recovery, and their best-effort failure policies. This adapter owns
none of those decisions: each function is a verbatim, one-call delegation to
the existing ``core.db`` implementation. It creates a module-to-storage seam;
it does not decompose or relocate ``core/db.py``.

Dependency direction is ``chat_agent/chat_tools -> chat_store -> core.db``.
Keeping delegates signature-identical preserves existing return shapes,
transactions, and exceptions for the callers' fallback handlers.
"""

from app.core import db_booking
from app.core import db_accounts
from app.core import db_slots
from app.core import db_chat


def chat_save_message(canal, clave, mensaje):
    return db_chat.chat_save_message(canal, clave, mensaje)


def chat_metadata_get(canal, clave, kind):
    return db_chat.chat_metadata_get(canal, clave, kind)


def chat_take_booking_proposal(canal, clave):
    return db_chat.chat_take_booking_proposal(canal, clave)


def linked_whatsapp_claves(dni, contact=None, grant_days=30):
    return db_chat.linked_whatsapp_claves(dni, contact, grant_days)


def web_claves_for_dni(dni, limit=3):
    return db_chat.web_claves_for_dni(dni, limit)


def chat_history_multi(pares, limit=200):
    return db_chat.chat_history_multi(pares, limit)


def chat_summaries_latest_covers(patient_key):
    return db_chat.chat_summaries_latest_covers(patient_key)


def chat_summary_save(patient_key, summary, covers_until, message_count=0, channel="merged"):
    return db_chat.chat_summary_save(patient_key, summary, covers_until, message_count, channel)


def chat_summaries_recent(patient_key, limit=3):
    return db_chat.chat_summaries_recent(patient_key, limit)


def chat_compaction_get(patient_key):
    return db_chat.chat_compaction_get(patient_key)


def chat_compaction_save(patient_key, summary, covers_until, message_count=0):
    return db_chat.chat_compaction_save(patient_key, summary, covers_until, message_count)


def chat_compaction_clear(patient_key):
    return db_chat.chat_compaction_clear(patient_key)


def chat_first_user_message(canal, clave, since=None):
    return db_chat.chat_first_user_message(canal, clave, since)


def chat_new_session_marker_at(canal, clave):
    return db_chat.chat_new_session_marker_at(canal, clave)


def chat_count_user_messages(canal, clave, hours=24):
    return db_chat.chat_count_user_messages(canal, clave, hours)


def get_dependent(adulto_dni, dni):
    return db_slots.get_dependent(adulto_dni, dni)


def active_booking_request_by_slot(dni, servicio_id, dia, hora):
    return db_slots.active_booking_request_by_slot(dni, servicio_id, dia, hora)


def list_by_dni(dni):
    return db_booking.list_by_dni(dni)


def list_dependents(adulto_dni):
    return db_slots.list_dependents(adulto_dni)


def get_user(dni):
    return db_accounts.get_user(dni)
