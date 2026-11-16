"""Server-side preferences for the authenticated admin-panel user."""

import json
import re

from app.core import db_auth
from app.core.status_error import MessageStatusError

_KEY_RE = re.compile(r"^[a-z0-9_.:-]{1,80}$")


class PreferenceError(MessageStatusError):
    """A domain error rendered as `{"error": str(exc)}` at `.status`."""


def get_items(usuario):
    """Return a user's preferences; may raise ``db.DbUnavailable``."""
    return db_auth.admin_preferences(usuario)


def save_item(usuario, key, value):
    """Save a preference; may raise ``PreferenceError`` or ``db.DbUnavailable``."""
    if not _KEY_RE.match(str(key or "")):
        raise PreferenceError(400, "preferencia inválida")
    if len(json.dumps(value, ensure_ascii=False)) > 20_000:
        raise PreferenceError(400, "preferencia demasiado grande")
    return db_auth.admin_preference_save(usuario, key, value)
