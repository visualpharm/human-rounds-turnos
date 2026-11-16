"""Installation backup policy shared by Settings, setup, worker and CLI."""
import datetime as dt
import json
import os
import re
from urllib.parse import urlsplit

from app.core import backup_crypto as crypto, backup_lock, config, managed_secrets
from app.core.backup_s3 import S3, validate_endpoint

KEY = "TURNOS_BACKUP_RECOVERY_KEY"
OLD_KEYS = "TURNOS_BACKUP_OLD_KEYS_SECRET"
DEFAULTS = {
    "TURNOS_BACKUP_ENABLED": "0",
    "TURNOS_BACKUP_S3_ENDPOINT": "", "TURNOS_BACKUP_S3_BUCKET": "",
    "TURNOS_BACKUP_S3_KEY_ID": "", "TURNOS_BACKUP_S3_SECRET": "",
    "TURNOS_BACKUP_S3_PREFIX": "human-rounds/", "TURNOS_BACKUP_S3_REGION": "auto",
    "TURNOS_BACKUP_INTERVAL_MIN": "1440", "TURNOS_BACKUP_RETAIN_DAILY": "7",
    "TURNOS_BACKUP_RETAIN_WEEKLY": "4", "TURNOS_BACKUP_RETAIN_MONTHLY": "6",
    "TURNOS_BACKUP_RETENTION_APPLY": "0", "TURNOS_BACKUP_UPLOAD_MAX_HOURS": "36",
    "TURNOS_BACKUP_RESTORE_MAX_DAYS": "10", "TURNOS_BACKUP_RESTORE_INTERVAL_DAYS": "7",
    "TURNOS_BACKUP_SCRATCH_DATABASE_URL": "",
    KEY: "", OLD_KEYS: "",
}
PROTECTED = frozenset({KEY, OLD_KEYS, "TURNOS_BACKUP_S3_KEY_ID", "TURNOS_BACKUP_S3_SECRET",
                       "TURNOS_BACKUP_SCRATCH_DATABASE_URL"})
BOOL_KEYS = frozenset({"TURNOS_BACKUP_ENABLED", "TURNOS_BACKUP_RETENTION_APPLY"})
FIELD_LABELS = {
    "TURNOS_BACKUP_ENABLED": ("Encrypted backups", "Copias cifradas"),
    "TURNOS_BACKUP_S3_ENDPOINT": ("R2 / S3 endpoint", "Endpoint R2 / S3"),
    "TURNOS_BACKUP_S3_BUCKET": ("Bucket", "Bucket"),
    "TURNOS_BACKUP_S3_KEY_ID": ("Access key ID", "ID de clave de acceso"),
    "TURNOS_BACKUP_S3_SECRET": ("Secret access key", "Clave secreta de acceso"),
    "TURNOS_BACKUP_S3_PREFIX": ("Object prefix", "Prefijo de objetos"),
    "TURNOS_BACKUP_S3_REGION": ("Region", "Región"),
    "TURNOS_BACKUP_INTERVAL_MIN": ("Backup interval (minutes)", "Intervalo de copia (minutos)"),
    "TURNOS_BACKUP_RETAIN_DAILY": ("Daily copies", "Copias diarias"),
    "TURNOS_BACKUP_RETAIN_WEEKLY": ("Weekly copies", "Copias semanales"),
    "TURNOS_BACKUP_RETAIN_MONTHLY": ("Monthly copies", "Copias mensuales"),
    "TURNOS_BACKUP_RETENTION_APPLY": ("Apply retention deletions", "Aplicar borrados por retención"),
    "TURNOS_BACKUP_UPLOAD_MAX_HOURS": ("Upload freshness (hours)", "Antigüedad máxima de copia (horas)"),
    "TURNOS_BACKUP_RESTORE_MAX_DAYS": ("Restore-test freshness (days)", "Antigüedad máxima de prueba (días)"),
    "TURNOS_BACKUP_RESTORE_INTERVAL_DAYS": ("Restore-test interval (days)", "Intervalo de prueba (días)"),
    "TURNOS_BACKUP_SCRATCH_DATABASE_URL": ("Isolated scratch database URL", "URL de base aislada de prueba"),
}


def snapshot():
    # The recovery keys NEVER consult a DB override.
    return {k: str(config.get(k, default) or default) for k, default in DEFAULTS.items() if k not in {KEY, OLD_KEYS}}


def validate(values):
    for key, value in values.items():
        if key not in DEFAULTS or key in {KEY, OLD_KEYS}:
            raise ValueError("Unknown or installation-managed backup setting")
        value = str(value)
        if key in BOOL_KEYS and value not in {"0", "1"}:
            raise ValueError("Backup switches must be 0 or 1")
        if key == "TURNOS_BACKUP_S3_ENDPOINT" and value:
            validate_endpoint(value)
        if key == "TURNOS_BACKUP_S3_BUCKET" and value and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}", value):
            raise ValueError("Invalid S3 bucket name")
        if key == "TURNOS_BACKUP_S3_REGION" and not re.fullmatch(r"[a-z0-9-]{1,40}", value):
            raise ValueError("Invalid S3 signing region")
        if key == "TURNOS_BACKUP_S3_PREFIX":
            if (not value or value.startswith("/") or not value.endswith("/") or "\\" in value or "\x00" in value
                    or any(p in {"", ".", ".."} for p in value[:-1].split("/"))):
                raise ValueError("Use a relative backup prefix ending in /")
        if key.endswith(("_MIN", "_DAILY", "_WEEKLY", "_MONTHLY", "_HOURS", "_DAYS", "_LIMIT")):
            if not value.isdigit() or not 1 <= int(value) <= 1024**4:
                raise ValueError("Backup intervals and limits must be positive integers")
    return {k: str(v) for k, v in values.items()}


def refuse_locked(values):
    """Saving the rotation switched on (TURNOS_BACKUP_RETENTION_APPLY=1) from
    the panel or the setup is refused on the municipal installation, where
    the application deletes no backup (`backup_lock.BackupWriteForbidden`).
    Not part of `validate`: a running backup with the switch already on must
    still run (it only skips the rotation, loudly)."""
    if str(values.get("TURNOS_BACKUP_RETENTION_APPLY", "")).strip() == "1" and backup_lock.locked():
        backup_lock.refuse("switching on the rotation of the full backups")


def keys():
    values = managed_secrets.installation_backup_values()
    result = {}
    if values.get(OLD_KEYS):
        try:
            for ident, encoded in json.loads(values[OLD_KEYS]).items():
                key = crypto.decode_key(encoded)
                if crypto.key_id(key) != ident:
                    raise ValueError("Retained key id mismatch")
                result[ident] = key
        except (TypeError, AttributeError, json.JSONDecodeError):
            raise ValueError("Invalid retained backup keys") from None
    if values.get(KEY):
        key = crypto.decode_key(values[KEY])
        result[crypto.key_id(key)] = key
    return result


def key_status():
    value = managed_secrets.installation_backup_values().get(KEY)
    try:
        fingerprint = crypto.key_id(crypto.decode_key(value)) if value else None
        return {"configured": bool(value), "fingerprint": fingerprint}
    except crypto.FormatError:
        return {"configured": False, "fingerprint": None, "invalid": True}


def generate_key(*, rotate=False):
    if config.locked():
        raise ValueError("Backup configuration is locked")
    existing = keys()
    if existing and not rotate:
        raise ValueError("Recovery key already exists; use Rotate")
    key = os.urandom(32)
    encoded = crypto.encode_key(key)
    pairs = {KEY: encoded}
    if existing:
        pairs[OLD_KEYS] = json.dumps({ident: crypto.encode_key(old) for ident, old in existing.items()})
    managed_secrets.save_installation_backup_values(pairs)
    return {"recovery_key": encoded, "fingerprint": crypto.key_id(key)}


def sanitize_env(text):
    lines, dropping_quote = [], None
    for line in text.splitlines(keepends=True):
        # Reject multiline quoted secrets rather than retaining their continuation.
        if dropping_quote:
            if dropping_quote in line:
                dropping_quote = None
            continue
        candidate = line.strip()
        if candidate.startswith("export "):
            candidate = candidate[7:].lstrip()
        name, sep, value = candidate.partition("=")
        if sep and name.strip() in {KEY, OLD_KEYS}:
            value = value.strip()
            if value.startswith(("'", '"')) and not value[1:].endswith(value[0]):
                dropping_quote = value[0]
            continue
        lines.append(line)
    if dropping_quote:
        raise ValueError("Unclosed recovery-secret assignment")
    return "".join(lines)


def configured(cfg=None):
    cfg = cfg or snapshot()
    return (cfg.get("TURNOS_BACKUP_ENABLED") == "1" and all(cfg.get(k) for k in
            ("TURNOS_BACKUP_S3_ENDPOINT", "TURNOS_BACKUP_S3_BUCKET", "TURNOS_BACKUP_S3_KEY_ID", "TURNOS_BACKUP_S3_SECRET"))
            and key_status()["configured"])


def client(cfg=None):
    cfg = cfg or snapshot()
    return S3(cfg["TURNOS_BACKUP_S3_ENDPOINT"], cfg["TURNOS_BACKUP_S3_BUCKET"], cfg["TURNOS_BACKUP_S3_KEY_ID"],
              cfg["TURNOS_BACKUP_S3_SECRET"], cfg["TURNOS_BACKUP_S3_REGION"])


def system_state(rows, cfg, *, now=None, plaintext=False):
    now = now or dt.datetime.now(dt.timezone.utc)
    uploaded = next((r for r in rows if r.get("uploaded")), None)
    tested = next((r for r in rows if r.get("restore_tested")), None)
    def when(row, key):
        value = row.get(key) or row.get("finished_at") or row["started_at"]
        return dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    state = "ok"
    if not configured(cfg):
        state = "unconfigured"
    elif (not uploaded or now - when(uploaded, "uploaded_at") > dt.timedelta(hours=int(cfg["TURNOS_BACKUP_UPLOAD_MAX_HOURS"]))
          or not tested or now - when(tested, "restore_tested_at") > dt.timedelta(days=int(cfg["TURNOS_BACKUP_RESTORE_MAX_DAYS"]))):
        state = "overdue"
    elif (not uploaded.get("integrity_verified") or uploaded.get("status") in {"partial", "failed"}
          or rows[0].get("restore_error")):
        state = "partial"
    return {"state": state, "last_success": when(uploaded, "uploaded_at").isoformat() if uploaded else None,
            "size": uploaded.get("size", 0) if uploaded else 0,
            "uploaded": bool(uploaded), "integrity_verified": bool(uploaded and uploaded.get("integrity_verified")),
            "restore_tested": bool(tested), "restore_tested_at": when(tested, "restore_tested_at").isoformat() if tested else None,
            "destination_host": urlsplit(cfg["TURNOS_BACKUP_S3_ENDPOINT"]).hostname or "",
            "plaintext_active": plaintext, "last_error": rows[0].get("error") if rows else None}
