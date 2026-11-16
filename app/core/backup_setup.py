"""Short-lived server-side wizard staging. Cookie holds only an opaque token.

Nothing is persisted until setup's final commit. Restart/expiry requires the
operator to repeat this step; a missing stage must never silently enable backup.
"""
import os
import secrets
import threading
import time
from app.core import backup_crypto, backup_settings

_stages = {}
_lock = threading.RLock()
_TTL = 1800


def _entry(token):
    now = time.monotonic()
    for ident in list(_stages):
        if _stages[ident]["expires"] < now:
            del _stages[ident]
    if token not in _stages:
        raise ValueError("Backup step expired; enter the destination again")
    return _stages[token]


def public(token):
    with _lock:
        try:
            item = _entry(token)
        except ValueError:
            return {}
        return {"fingerprint": item.get("fingerprint"), "configured": bool(item.get("key")),
                "values": {k: v for k, v in item["values"].items() if k not in backup_settings.PROTECTED},
                "probed": item.get("probed", False)}


def begin():
    with _lock:
        now = time.monotonic()
        for ident in list(_stages):
            if _stages[ident]["expires"] < now:
                del _stages[ident]
        if len(_stages) >= 128:
            raise ValueError("Too many active setup sessions")
        token = secrets.token_urlsafe(32)
        _stages[token] = {"expires": now + _TTL, "values": {}, "skip": False}
        return token


def update(token, form):
    with _lock:
        if not token or token not in _stages:
            if token:
                _entry(token)  # expired state is an error, not an implicit reset
            token = begin()
        item = _entry(token)
        values = {k: str(v).strip() for k, v in form.items() if k in backup_settings.DEFAULTS
                  and k not in {backup_settings.KEY, backup_settings.OLD_KEYS}}
        backup_settings.validate(values)
        backup_settings.refuse_locked(values)
        # Empty password controls preserve previously staged credentials.
        for key, value in values.items():
            if value or key not in backup_settings.PROTECTED:
                item["values"][key] = value
        action = form.get("action", "continue")
        if action == "skip":
            item.update(skip=True, values={"TURNOS_BACKUP_ENABLED": "0"}, key=None, ready=True)
            return token, {"continue": True}
        item["skip"] = False
        if action == "generate":
            if item.get("key"):
                raise ValueError("Key already generated; use the saved key")
            key = os.urandom(32)
            item.update(key=backup_crypto.encode_key(key), fingerprint=backup_crypto.key_id(key))
            return token, {"recovery_key": item["key"], "fingerprint": item["fingerprint"]}
        if action == "probe":
            cfg = {**backup_settings.DEFAULTS, **item["values"]}
            backup_settings.client(cfg).probe(cfg["TURNOS_BACKUP_S3_PREFIX"])
            item["probed"] = True
            return token, {"probed": True}
        if action != "continue" or not item.get("key"):
            raise ValueError("Generate and save the recovery key")
        if not all(item["values"].get(k) for k in ("TURNOS_BACKUP_S3_ENDPOINT", "TURNOS_BACKUP_S3_BUCKET",
                                                   "TURNOS_BACKUP_S3_KEY_ID", "TURNOS_BACKUP_S3_SECRET")):
            raise ValueError("Enter endpoint, bucket and both S3 credentials")
        if form.get("key_saved") != "1":
            raise ValueError("Confirm that the recovery key is saved outside this server")
        item["values"]["TURNOS_BACKUP_ENABLED"] = "1"
        item["ready"] = True
        return token, {"continue": True}


def commit(token):
    with _lock:
        item = _entry(token)
        if not item.get("ready"):
            raise ValueError("Complete the backup step")
        secrets_to_save = {backup_settings.KEY: item["key"]} if item.get("key") else {}
        return dict(item["values"]), secrets_to_save


def discard(token):
    with _lock:
        _stages.pop(token, None)
