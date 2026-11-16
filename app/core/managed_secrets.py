"""Managed local-secret source boundary.

The local executor calls this module directly; it never evaluates shell text,
prints a source value, or imports a provider selected by a user string.  The
file source is a mode-0600 JSON fixture for tests and review only.  The other
source is one fixed credential vault item and schema, delivered by the local
agents-hub vault broker (``run`` mode) as one injected environment value.  This
module never starts a vault CLI and never holds a vault token; it is not a
``command:...`` escape hatch either.

Coolify is deliberately outside this module.  Its container environment is a
supported production injection path and runs the app directly.
"""

from __future__ import annotations

import json
import os
import re
import stat
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Mapping

from app.core import paths


ROOT = Path(paths.PROJECT_ROOT).resolve()
# A Human Rounds checkout normally lives in a sibling worktree directory.  A
# source beneath this enclosing directory is therefore rejected as well as the
# active root: a secret file cannot be placed in another worktree and then
# accidentally become tracked or copied with project artifacts.
WORKTREE_CONTAINER = ROOT.parent.resolve()


class SecretSourceError(RuntimeError):
    """A source is absent, malformed, or unsafe to use."""


_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SENSITIVE_SUFFIXES = ("_PASS", "_PASSWORD", "_SECRET", "_TOKEN", "_API_KEY", "_APP_KEY")
_SENSITIVE_EXACT = frozenset({
    "DATABASE_URL", "TEST_DATABASE_URL", "PGPASSWORD", "PGPASSFILE",
    "OPENROUTER_MANAGEMENT_KEY", "TURNOS_B2_KEY_ID", "TURNOS_BACKUP_RECOVERY_KEY",
    "TURNOS_BACKUP_S3_KEY_ID", "TURNOS_BACKUP_SCRATCH_DATABASE_URL",
})

# These identifiers are intentionally constants, not environment selectors.
# One source item ("Human Rounds — Managed local credential profiles", field
# managed_profiles_v1, AI vault) represents profile-scoped values for this
# application only.  The broker application ``BROKER_APPLICATION`` binds that
# field to ``BROKER_ENV_NAME``; nothing else in this repo reads the vault.
_PROFILES_SCHEMA = "human-rounds-managed-profiles-v1"
BROKER_ENV_NAME = "TURNOS_MANAGED_PROFILES_V1"
BROKER_APPLICATION = "turnos-managed"


def classify_key(key: str) -> str:
    """Return a deliberately conservative ``protected``/``public`` label.

    This is used only for source-profile admission and dry-run counts.  It
    never receives or returns a value.
    """
    upper = str(key).upper()
    if upper in _SENSITIVE_EXACT or upper.endswith(_SENSITIVE_SUFFIXES):
        return "protected"
    return "public"


def is_runtime_key(key: str) -> bool:
    """Whether a key is an application runtime setting, not arbitrary data."""
    if not _KEY_RE.fullmatch(str(key)):
        return False
    return key.startswith((
        "DATABASE_", "TEST_DATABASE_", "BOOKING_", "TURNOS_", "OPENROUTER_", "MAILGUN_",
        "MAIL_", "MAILJET_", "HSI_", "INTAKE_", "OTP_", "META_", "PG", "SENTRY_", "B2_", "GOOGLE_",
        "HOST", "PORT", "PACK_", "SACOTURNO_",
    ))


def installation_backup_values():
    """Fixed installation-secret namespace, injected env or owner-only env file.

    Never consult catalog/config DB overrides. This is separate from the fixed
    developer credential-vault profiles: municipal hosts need neither that
    developer account nor Coolify to operate unattended.
    """
    from app.core import config
    names = ("TURNOS_BACKUP_RECOVERY_KEY", "TURNOS_BACKUP_OLD_KEYS_SECRET")
    file_values = config.dotenv_values()
    return {key: os.environ.get(key, file_values.get(key, "")) for key in names}


def save_installation_backup_values(pairs):
    from app.core import config
    names = {"TURNOS_BACKUP_RECOVERY_KEY", "TURNOS_BACKUP_OLD_KEYS_SECRET"}
    if not set(pairs) <= names or config.locked():
        raise SecretSourceError("Installation backup secrets cannot be changed")
    # Explicit process injection is operator-owned; an env-file write would
    # appear to succeed while leaving an older environment value authoritative.
    if any(os.environ.get(key) for key in names):
        raise SecretSourceError("Update injected installation backup secrets at their source")
    if not config.save_env_keys(pairs):
        raise SecretSourceError("Could not save installation backup secrets")


class SecretSource(ABC):
    """Internal provider interface; providers return profile-scoped mappings."""

    @abstractmethod
    def values_for(self, profile: str, allowed: Callable[[str], bool]) -> dict[str, str]:
        """Return validated values for one profile without logging them."""


def _profiles_document(doc: object) -> Mapping[str, object]:
    if not isinstance(doc, dict) or not isinstance(doc.get("profiles"), dict):
        raise SecretSourceError("secret source has no profiles mapping")
    return doc


def _profile_values(doc: Mapping[str, object], profile: str, allowed: Callable[[str], bool]) -> dict[str, str]:
    profiles = doc["profiles"]
    if not isinstance(profiles, dict):  # narrowed by _profiles_document; keeps type/runtime explicit
        raise SecretSourceError("secret source has no profiles mapping")
    selected = profiles.get(profile)
    if not isinstance(selected, dict):
        raise SecretSourceError("secret source has no mapping for this profile")
    values: dict[str, str] = {}
    for key, value in selected.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise SecretSourceError("secret source has an invalid key")
        if not allowed(key):
            raise SecretSourceError("secret source key is outside this profile")
        if not isinstance(value, str):
            raise SecretSourceError("secret source value is not text")
        values[key] = value
    return values


class FileSecretSource(SecretSource):
    """A strict JSON source for synthetic fixtures and review only.

    Shape::

        {"profiles": {"backend-full": {"DATABASE_URL": "..."}}}

    The file must be absolute, regular, owner-owned, mode 0600, and outside
    this checkout and its sibling-worktree container. Its contents are never
    copied to stdout/stderr by this module.
    """

    def __init__(self, path: str):
        self.path = Path(path)

    @staticmethod
    def _inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _validate_stat(info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode):
            raise SecretSourceError("secret source must be a regular file")
        if info.st_uid != os.getuid():
            raise SecretSourceError("secret source is not owned by this user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise SecretSourceError("secret source mode is not owner-only")

    def _document(self) -> Mapping[str, object]:
        if not self.path.is_absolute():
            raise SecretSourceError("secret source path must be absolute")
        try:
            listed = os.lstat(self.path)
        except OSError as exc:
            raise SecretSourceError("secret source is unavailable") from exc
        if stat.S_ISLNK(listed.st_mode):
            raise SecretSourceError("secret source must not be a symlink")
        self._validate_stat(listed)
        try:
            resolved = self.path.resolve(strict=True)
        except OSError as exc:
            raise SecretSourceError("secret source is unavailable") from exc
        if self._inside(resolved, ROOT) or self._inside(resolved, WORKTREE_CONTAINER):
            raise SecretSourceError("secret source must be outside project worktrees")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = os.open(self.path, flags)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
                raise SecretSourceError("secret source changed while opening")
            self._validate_stat(opened)
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                descriptor = None
                doc = json.load(handle)
        except SecretSourceError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretSourceError("secret source is not valid JSON") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return _profiles_document(doc)

    def values_for(self, profile: str, allowed: Callable[[str], bool]) -> dict[str, str]:
        return _profile_values(self._document(), profile, allowed)


class BrokerSecretSource(SecretSource):
    """Read the one reviewed Human Rounds item as delivered by the vault broker.

    The broker resolves the item field and injects it as ``BROKER_ENV_NAME``
    into this process only; the vault token never reaches it.
    ``turnos_managed_exec`` builds a fresh child environment afterwards, so the
    document, source selector, and all ambient parent variables cannot reach
    the profile command.  Errors are deliberately value-free.

    Expected injected value (JSON, never shell text)::

        {"schema": "human-rounds-managed-profiles-v1",
         "profiles": {"backend-full": {"DATABASE_URL": "..."}}}
    """

    def __init__(self, environ: Mapping[str, str] | None = None):
        values = os.environ if environ is None else environ
        self._raw = values.get(BROKER_ENV_NAME)

    def available(self) -> bool:
        """Whether the broker already injected the document into this process."""
        return bool(self._raw)

    def _document(self) -> Mapping[str, object]:
        if not self._raw:
            raise SecretSourceError("credential vault document was not injected by the broker")
        try:
            document = json.loads(self._raw)
        except (TypeError, json.JSONDecodeError):
            raise SecretSourceError("credential vault item has an unexpected schema") from None
        if not isinstance(document, dict) or document.get("schema") != _PROFILES_SCHEMA:
            raise SecretSourceError("credential vault item has an unexpected schema")
        return _profiles_document(document)

    def values_for(self, profile: str, allowed: Callable[[str], bool]) -> dict[str, str]:
        return _profile_values(self._document(), profile, allowed)


def source_from_spec(spec: str | None, environ: Mapping[str, str] | None = None) -> SecretSource:
    """Construct a provider from an intentionally closed configuration grammar.

    This is a direct injection seam for synthetic tests and review. A provider
    identifier is data, not a command. ``onepassword`` selects only the fixed
    broker-delivered item/schema above; it cannot name an item, vault, command,
    or executable.
    """
    if not spec:
        raise SecretSourceError("managed execution requires a configured secret source")
    if spec.startswith("file:"):
        return FileSecretSource(spec[len("file:"):])
    if spec == "onepassword":
        return BrokerSecretSource(environ)
    raise SecretSourceError("unsupported secret source")


def source_from_environment(environ: Mapping[str, str] | None = None) -> BrokerSecretSource:
    """Build the one live local source; ``file:`` is direct-test-only."""
    values = os.environ if environ is None else environ
    selector = values.get("TURNOS_SECRET_SOURCE")
    if selector not in {None, "", "onepassword"}:
        raise SecretSourceError("live managed execution requires the fixed credential vault source")
    return BrokerSecretSource(values)
