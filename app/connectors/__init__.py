"""connectors — capability interfaces + registry for external-system integrations.

Phase 6b of the open-source rework (`~/.claude/plans/large-rewrok-before-open-
robust-chipmunk.md`), 2026-07-30. Generalizes the provider seam `renaper.py`
already implemented (fail-closed real provider + a local `stub`, chosen by
config) into a framework every external integration can follow, so a PR that
wants to plug Pinamar Turnos into a DIFFERENT country's identity/insurance/EMR
system knows exactly where to land.

## The eight capabilities

    identity_verification   who is this patient, verified against a national ID
                             system (Argentina: RENAPER/Mi Argentina OIDC)
    insurance_lookup        what health coverage does this patient have
                             (Argentina: PUCO/SISA WS131)
    license_lookup          is this professional's license real and active
                             (Argentina: REFEPS)
    emr_sync                push booked appointments into the clinic's
                             electronic medical record (Argentina: HSI)
    slot_source              live third-party appointment availability to show
                             instead of (or alongside) Sana's own agenda
                             (Argentina: Mi Salud Digital / MSD)
    otp_transport            deliver a one-time code or free-text message to a
                             patient's phone (Argentina: WhatsApp via WAHA or
                             Meta's Cloud API)
    clinical_history_lookup  a patient's prior clinical history in a legacy or
                             external record system, by national ID, for the
                             chart summary (Argentina: Alepo/Marqui, the two
                             digitized-PDF archives Pinamar's paper history
                             lives in)
    email_transport          deliver a transactional email through a
                             provider's HTTP API (Mailgun — the default —
                             or Mailjet; every caller goes through
                             `notifications.mail.send()`)

Each capability has a small ABC below. It documents the METHOD SHAPE a new
connector must implement. Since the registry rewire (2026-08-20) the five
capability consumers that already used connector modules — the synthetic-
identity login (`cuentas/accounts.py`), the registration coverage attach
(same file), the admin REFEPS check (`modules/turnos/booking_requests.py`),
the wizard's third-party slots (`patient_web/patient_site.py`) and the chart
summary's history fan-out (`ai/chart_summary.py`) — resolve their adapter
through `adapter_for(capability)` (or, for the fan-out, through
`_instantiate_adapter(id)` per discovered id) instead of importing the
concrete module. `emr_sync` and `otp_transport` stay the two documented
exceptions (see docs/connectors.md "What's wired live vs. what's the target
shape" for the one-by-one accounting).

## The degradation contract (THE contract — read this before implementing an ABC)

**No connector configured for a capability → that capability degrades exactly
like it does today, it never crashes and it never raises past the caller who
knows how to handle "not available".** This mirrors `TURNOS_SKIP_HSI=1`
(emr_sync becomes a no-op, appointments still book, `hsi_estado` just reads
'skip'/'pendiente') and `puco.disponible()` (no SISA credentials + public
fallback off → the registration form's coverage lookup silently hides,
patients type their obra social by hand — nothing breaks). Every ABC method
below must document what "not configured" returns or raises for THAT
capability, and every connector implementing it must honor that shape.
`ProviderUnavailable` (below) is the shared, capability-agnostic way to signal
"this connector cannot fulfill the request right now" (network down, no
credentials, remote system unreachable) — as opposed to `IdentityNotFound`
which reports a definite, successful "no" answer from the provider.

## Registry

Each connector is a directory under `connectors/<id>/` with a `connector.json`
manifest:

    {
      "id": "renaper",
      "country": "AR",
      "capability": "identity_verification",
      "config_keys": ["TURNOS_FAKE_RENAPER", "TURNOS_RENAPER_CLIENT_ID", ...],
      "description": "..."
    }

`list_connectors()` discovers every manifest under `connectors/`.
`active_connector_id(capability)` reads `config.get("CONNECTOR_<CAPABILITY>")`
(uppercased, e.g. `CONNECTOR_IDENTITY_VERIFICATION`) — the RAW value an
installation stored for that capability (`None` when nothing is stored).
Nothing in this module enforces that only one connector implements a
capability; `manage.py` is where an operator picks one.

`resolve(capability)` is what consumers actually go through (via
`adapter_for()`). It returns `(connector_id | None, source)`:

    explicit   the stored value names a connector that implements the
               capability — that one.
    off        the stored value is the reserved sentinel `"off"` (what
               `disable()` / `manage.py conn disable` write): nothing is
               routed, the capability degrades per its ABC contract, and no
               default is consulted.
    default    nothing is stored: the ONE manifest for the capability whose
               `"default"` is the JSON boolean `true`, or — when none is
               flagged — the sole connector implementing the capability.
               Several flagged, or several unflagged implementers, means no
               default (`(None, None)`). This tier is what makes an
               installation that never ran `conn enable` behave exactly as it
               did before the rewire: renaper/puco/refeps/msd are flagged.
               `emr_sync` is explicitly excluded from this tier: institutional
               integrations always require an operator selection, even when
               only one connector package is installed.
    invalid    anything else stored (unknown id, wrong capability, broken
               manifest, a value that is not a Python identifier) — fail
               closed: `(None, "invalid")`, never the default, so an operator
               typo surfaces in `manage.py conn list/info` instead of being
               papered over.

`adapter_for(capability)` instantiates the resolved connector's `adapter.py`
class (the unique concrete subclass of the capability's ABC defined in that
module) — a fresh instance per call, adapters are stateless one-liners that
delegate to their `_impl` module at call time, so tests that monkeypatch
`connectors.<id>.<impl>` keep working. Any import/instantiation failure
degrades to `None`; `manage.py conn info` is the operator-facing truth.
"""

from __future__ import annotations

import abc
import copy
import importlib
import importlib.machinery
import inspect
import json
import os
import stat
import sys

from app.core import config

ROOT = os.path.dirname(os.path.abspath(__file__))

# Reserved sentinel: stored as CONNECTOR_<CAP> by `disable()` to mean "route
# nothing for this capability" — distinct from "never configured" (which falls
# through to the default tier). Reserved everywhere: never discovered as a
# connector id, never loadable, never enable-able, never instantiable.
OFF = "off"

# ---- capabilities -----------------------------------------------------------

IDENTITY_VERIFICATION = "identity_verification"
INSURANCE_LOOKUP = "insurance_lookup"
LICENSE_LOOKUP = "license_lookup"
EMR_SYNC = "emr_sync"
SLOT_SOURCE = "slot_source"
OTP_TRANSPORT = "otp_transport"
CLINICAL_HISTORY_LOOKUP = "clinical_history_lookup"
EMAIL_TRANSPORT = "email_transport"

# Institutional connectors are never selected by package-count accident.
# Core distributions may add or remove Enterprise/vendor packages over time;
# that must not turn the last remaining integration into an implicit default.
_EXPLICIT_ONLY_CAPABILITIES = frozenset({EMR_SYNC})

CAPABILITIES = (
    IDENTITY_VERIFICATION,
    INSURANCE_LOOKUP,
    LICENSE_LOOKUP,
    EMR_SYNC,
    SLOT_SOURCE,
    OTP_TRANSPORT,
    CLINICAL_HISTORY_LOOKUP,
    EMAIL_TRANSPORT,
)


# ---- exceptions ---------------------------------------------------------------
# Moved here from renaper.py in the extraction slice that follows this one
# (renaper.py becomes a compat shim over connectors/renaper/renaper.py, which
# imports these FROM here — connectors/__init__.py is the single owner now,
# never re-defined per connector, per this repo's "reuse renaper.py's
# exceptions, don't invent parallel ones" rule). Domain-specific exceptions
# that predate this framework and don't share this shape (`refeps.RefepsError`,
# `hsi_client.HsiError`/`HsiConflict`) are left as they are — they are not
# "provider unavailable" signals in the same sense, they carry richer failure
# detail their own callers already handle.

class ProviderUnavailable(Exception):
    """The active connector for a capability cannot fulfill the request right
    now (no credentials, network down, remote system unreachable, or no
    connector configured at all). Callers must treat this exactly like "the
    capability degraded" — never a crash, never surfaced as a raw error to a
    patient. This is the connector framework's ONE generic exception; a new
    connector should raise this (or a subclass) rather than inventing a
    parallel "unavailable" signal."""


class EmrConflict(ProviderUnavailable):
    """The remote EMR rejected an idempotent intent because its observed
    state no longer matches Core's checkpoint.

    The generic journal stores the supplied ``remote`` snapshot as a durable
    conflict.  Wire adapters translate their vendor exception into this type;
    Core never imports a vendor exception class.
    """

    def __init__(self, message, remote=None):
        super().__init__(message)
        self.remote = remote


class EmrRejected(ProviderUnavailable):
    """The remote EMR deterministically rejected a valid Core intent.

    Unlike a transport outage this result must not be retried forever.  The
    journal records it as an operator-visible conflict with the bridge's
    stable, vendor-neutral code.
    """

    def __init__(self, message, code="rejected", remote=None):
        super().__init__(message)
        self.code = str(code or "rejected")
        self.remote = remote


class IdentityNotFound(Exception):
    """The provider was reached and gave a definite, successful answer: this
    identity does not exist in its registry. Distinct from
    ProviderUnavailable — this is a real "no", not a degraded "couldn't ask"."""


class InvalidIdentity(ValueError):
    """A caller tried to store or submit an identity/record that fails the
    connector's own validation (shape, required fields) before ever reaching
    the network."""


# ---- ABCs ---------------------------------------------------------------------
# Deliberately small: one or two abstract methods each, matching the shape
# the six existing Argentina integrations already settled on independently.
# A new country's connector implements the ABC; it does not have to match
# every incidental keyword argument of the Argentina implementation.

class IdentityVerificationConnector(abc.ABC):
    """Verifies a patient's identity against a national ID system.

    Degradation: with no connector active, identity verification via this
    capability is simply not offered — the site falls back to its own
    password-based accounts (`TURNOS_AUTH_METHODS` already models this for
    RENAPER: "password" alone is always the anti-lockout floor). A configured
    connector that cannot reach its provider raises ProviderUnavailable; a
    successful lookup that finds no such identity raises IdentityNotFound.
    """

    capability = IDENTITY_VERIFICATION

    @abc.abstractmethod
    def authenticate(self, dni, **kwargs):
        """Return the identity record (dict) for `dni`, or raise
        IdentityNotFound / ProviderUnavailable."""
        raise NotImplementedError


class InsuranceLookupConnector(abc.ABC):
    """Looks up a patient's health coverage by national ID number.

    Degradation: with no connector active (or a configured one that cannot be
    reached), the registration form's "figura en el padrón" assist simply
    doesn't populate — the patient types their obra social by hand, exactly
    like `puco.disponible()` returning False today. This capability signals
    "couldn't check" via a boolean/empty-list return, NOT an exception — the
    caller is a background lookup behind a form field, not a gate."""

    capability = INSURANCE_LOOKUP

    @abc.abstractmethod
    def available(self):
        """True if there is any way to consult this connector right now
        (fake mode, credentials, or a fallback public query)."""
        raise NotImplementedError

    @abc.abstractmethod
    def lookup(self, national_id):
        """(ok, coverages): ok=False -> could not consult; ok=True + [] ->
        consulted, no coverage on file; ok=True + [{...}, ...] -> found."""
        raise NotImplementedError

    def cached(self, national_id):
        """Coverages already known from a recent `lookup()` of this id — a
        list, never a network call; `[]` when nothing is cached. The
        registration POST's server-side coverage attach reads this (the form
        already triggered `lookup()` while the patient was typing, through
        the connector's own transport endpoint), so a connector that keeps no
        cache simply never attaches a "per the registry" coverage. Not
        abstract: the default is "nothing cached"."""
        return []


class LicenseLookupConnector(abc.ABC):
    """Looks up a health professional's license/registration by number.

    Degradation: with no connector active, the admin's order-verification
    flow simply doesn't offer the "check REFEPS" assist — staff verify the
    professional manually, same as before this capability existed."""

    capability = LICENSE_LOOKUP

    @abc.abstractmethod
    def search_by_license(self, license_number):
        """List of candidate professional records for this license number
        (may be empty). Raises ProviderUnavailable if the query itself
        failed (network/parse), never for "no results"."""
        raise NotImplementedError


class EmrSyncConnector(abc.ABC):
    """Pushes a booked appointment into the clinic's electronic medical
    record.

    Degradation: this is `hsi_client.push_turno`'s existing contract, made
    generic — with no connector active (or `TURNOS_SKIP_HSI`-equivalent set),
    push_appointment returns {"skipped": True} and the booking still
    completes locally; an unreachable configured connector must NEVER raise
    past the approval flow (approving always persists locally even if EMR
    sync fails — CLAUDE.md, do not relitigate) — it returns an "error"-shaped
    result for the caller's own resync bookkeeping instead of raising."""

    capability = EMR_SYNC

    def external_clinical_history(self, identifier):
        """Optional read-only history lookup by patient identifier.

        Raw vendor dictionaries stay outside clinical_history_lookup and AI.
        The explicit Enterprise bridge does not implement this capability yet.
        """
        return {"available": False, "reason": "not_supported", "items": []}

    @abc.abstractmethod
    def push_appointment(self, dni, first_name, last_name, birth_date_iso,
                          gender, specialty, when_iso, **kwargs):
        """Result dict — see hsi_client.py's module docstring for the full
        successful-booking shape ({"estado": "ok", "appointment_id": ..., ...})."""
        raise NotImplementedError

    def execute_intent(self, operation, payload, idempotency_key):
        """Execute one versioned Core appointment intent.

        This is the durable-journal boundary.  Implementations receive only
        the normalized Core payload and a stable idempotency key; they never
        receive a Core database connection.  Legacy connectors may support
        only ``create`` until their full operation mapping is implemented.
        """
        if operation == "create":
            return self.push_appointment(
                payload.get("dni"), payload.get("nombre"), payload.get("apellido"),
                payload.get("nacimiento"), payload.get("sexo"),
                payload.get("especialidad") or payload.get("servicio_id"),
                payload.get("when"), medico=payload.get("medico"),
                sobreturno=bool(payload.get("sobreturno")),
            )
        raise ProviderUnavailable(f"EMR connector does not support {operation}")

    def pull_observations(self, from_date, to_date, known_patient_ids):
        """Return normalized ``schedules``, ``appointments`` and ``patients``.

        The Enterprise HTTP bridge implements the protocol endpoint; a legacy
        in-process connector can adapt its vendor reads here while migration is
        in progress.  No direct Core-to-vendor import is permitted.
        """
        raise ProviderUnavailable("EMR connector does not provide observations")

    def probe_intent(self, idempotency_key):
        """Return the connector's settled result for a previously sent intent.

        This is the crash-window reclaim of the durable journal: a lease that
        is not the row's first means an earlier ``execute_intent`` with the
        same stable key may already have been accepted remotely while the
        local acknowledgement never landed.  The journal probes before it
        re-sends anything.

        Safe default: ``None`` — "this connector cannot answer probes".  The
        journal then keeps its pre-probe behavior exactly (re-execute the
        intent with the same idempotency key and let the connector's own
        dedup de-duplicate), which is what every legacy in-process connector
        relies on, so adding probe support is never a behavior change for
        connectors that do not opt in.  A bridged implementation returns a
        dict ``{"found": bool, "result": {...}}`` where ``result`` carries
        the same normalized shape ``execute_intent`` returned (or would
        have); ``found`` False is a definite "never executed", not an error.
        """
        return None

    def observe_intent(self, operation, payload):
        """Read a remote outcome when this connector cannot probe intent keys.

        Optional and read-only. Return an execute_intent-shaped result only
        when the requested operation is positively observed. ``None`` means
        uncertain: unlike ``probe_intent``, absence cannot prove the intent
        was never executed. Core currently uses this solely for a reclaimed,
        locally cancelled reassign.
        """
        return None

    def supports_auto_resolution(self):
        """Whether the connector implements the legacy routing operations.

        False is safe: Core still records, presents and resolves local-only
        conflicts, but never sends an undocumented vendor-specific mutation.
        """
        return False

    def run_conflict_resolution(self, now=None):
        """One automatic pass over the journal's open EMR conflicts.

        This is the ONLY entry point Core's sync sweep and the admin
        «Recheck» action use — the whole resolver (classification, local-only
        cleanup and, for connectors whose ``supports_auto_resolution()`` is
        True, the vendor routing mutations) lives on the connector side of
        the boundary.  The return value is the connector's own summary dict
        and must be safe to echo to the admin API.  Default for connectors
        without a resolver: an empty summary, never an exception.
        """
        return {}

    def conflict_panel(self, limit=60):
        """The admin conflict-monitor payload for this connector, or None.

        Reads the journal's own conflict tables; None means "no monitor for
        this connector" and the caller renders the degraded panel.  Never
        raises past the caller.
        """
        return None

    def apply_conflict_resolution(self, action, conflict_ids,
                                  resolved_by="operador", now=None):
        """Apply one operator action (move / overbook / reject) to the given
        open conflicts.

        Returns ``{"ok": bool, "resultados": [...]}`` plus, on failure, an
        ``error`` code for the admin API's 409.  Default for connectors
        without a resolver: the documented refusal — vendor actions are
        unavailable, never half-applied.
        """
        return {"ok": False, "error": "resolucion_no_disponible",
                "resultados": []}


class SlotSourceConnector(abc.ABC):
    """Supplies live third-party appointment availability for a Sana
    servicio, so the patient wizard can show real bookable slots instead of
    (or alongside) the request-with-order fallback.

    Degradation: no connector active, stale/missing data dump, or no
    matching agenda for this servicio -> empty result, the wizard falls back
    to solicitud-con-orden cleanly (exactly `msd_data.turnos_for_servicio`'s
    contract today: "Missing / stale file -> returns empty, UI degrades
    cleanly")."""

    capability = SLOT_SOURCE

    @abc.abstractmethod
    def slots_for_service(self, servicio):
        """{} when there is no live agenda for this servicio, else a dict
        describing available slots (see msd_data.turnos_for_servicio's
        docstring for the current shape)."""
        raise NotImplementedError


class OtpTransportConnector(abc.ABC):
    """Delivers a short text message (an OTP code, or free chat text) to a
    patient's phone over one messaging channel.

    Degradation: no connector configured (or configured but missing
    credentials) -> send() returns False, logged, never raises — the calling
    flow (registration, password reset, the booking chat bot) continues
    without the message ever blocking it, exactly like `otp_send.py`'s
    existing best-effort contract for both its WhatsApp transports."""

    capability = OTP_TRANSPORT

    @abc.abstractmethod
    def send(self, destination, text):
        """True if the message was handed off successfully; False on any
        failure or missing configuration. Never raises."""
        raise NotImplementedError


class ClinicalHistoryLookupConnector(abc.ABC):
    """Looks up a patient's prior clinical history in a legacy/external record
    system, by national ID, for the "resumen de historia clínica" feature
    (docs/roadmap/resumen-historia-clinica.md) to fold in alongside this
    repo's own `solicitudes` data.

    Degradation: with no connector active, the chart summary simply stays at
    its "our data only" v1 (this repo's own solicitudes/ai_json) — exactly
    the roadmap doc's designed fallback, never a missing-feature error. A
    configured connector that cannot reach its provider raises
    ProviderUnavailable; the summary generator treats that exactly like "not
    configured" and proceeds with what it already has locally."""

    capability = CLINICAL_HISTORY_LOOKUP

    @abc.abstractmethod
    def lookup_by_dni(self, dni):
        """[{...}, ...] of prior history entries for this patient (may be
        empty — a real "nothing on file" answer), or raises
        ProviderUnavailable if the query itself failed."""
        raise NotImplementedError


class EmailTransportConnector(abc.ABC):
    """Delivers one transactional email (patient notices, staff invitations
    and resets, the disk alert) through an email provider's HTTP API.

    Callers never reach a provider directly: they call
    `notifications.mail.send()`, which resolves this capability (Mailgun is
    the flagged default, so an installation that selects nothing keeps
    sending exactly as before) and applies the shared MAIL_ENABLED switch.

    Degradation: no provider routed, missing credentials or a provider
    failure -> send() returns False, logged, never raises. Email is
    fire-and-forget everywhere: a booking, an approval or an account
    creation persists locally whatever happens here."""

    capability = EMAIL_TRANSPORT

    @abc.abstractmethod
    def send(self, to, subject, text, html=None):
        """True when the provider accepted the message; False on any
        failure or missing configuration. Never raises."""
        raise NotImplementedError

    @abc.abstractmethod
    def missing_config(self):
        """[config key, ...] this provider still needs before it can send —
        empty when it is ready. Never a value, only key names."""
        raise NotImplementedError

    @abc.abstractmethod
    def probe(self):
        """Live check of the sending identity (domain or sender) with the
        provider: `{"target": str, "state": str | None, "error": str | None}`,
        where state "active" means mail can go out. Never raises."""
        raise NotImplementedError


_ABC_BY_CAPABILITY = {
    IDENTITY_VERIFICATION: IdentityVerificationConnector,
    INSURANCE_LOOKUP: InsuranceLookupConnector,
    LICENSE_LOOKUP: LicenseLookupConnector,
    EMR_SYNC: EmrSyncConnector,
    SLOT_SOURCE: SlotSourceConnector,
    OTP_TRANSPORT: OtpTransportConnector,
    CLINICAL_HISTORY_LOOKUP: ClinicalHistoryLookupConnector,
    EMAIL_TRANSPORT: EmailTransportConnector,
}


def abc_for_capability(capability):
    """The ABC class a connector.json's "capability" value must implement,
    or None for an unrecognized capability string."""
    return _ABC_BY_CAPABILITY.get(capability)


# ---- registry: discovery ------------------------------------------------------

def _connectors_dir():
    return ROOT


def _valid_connector_id(connector_id):
    """True only for an importable, root-local connector package name."""
    return (isinstance(connector_id, str) and connector_id != OFF
            and connector_id.isidentifier())


def _open_root_fd():
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(_connectors_dir(), flags)


def _open_connector_dir_fd(root_fd, connector_id):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(connector_id, flags, dir_fd=root_fd)


def _open_manifest_fd(connector_dir_fd):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open("connector.json", flags, dir_fd=connector_dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("connector manifest is not a regular file")
        return fd
    except Exception:
        os.close(fd)
        raise


def _local_module_file_identity(connector_id, filename):
    """Expected absolute path and stable identity for one local module file.

    Package and adapter source must be regular, non-symlink files below the
    descriptor-pinned connector root. This check brackets importlib so a
    preloaded or path-shadowed module cannot satisfy a local manifest.
    """
    root_fd = connector_fd = file_fd = None
    try:
        root_fd = _open_root_fd()
        connector_fd = _open_connector_dir_fd(root_fd, connector_id)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(filename, flags, dir_fd=connector_fd)
        st = os.fstat(file_fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        expected = os.path.abspath(os.path.join(_connectors_dir(), connector_id, filename))
        return expected, _stat_key(st)
    except OSError:
        return None
    finally:
        for fd in (file_fd, connector_fd, root_fd):
            if fd is not None:
                os.close(fd)


def _stat_key(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)


def list_connector_ids():
    """Every subdirectory of connectors/ that has a connector.json, sorted.
    The reserved `OFF` name, non-package ids, symlinked directories and
    symlinked/non-regular manifests are never connectors."""
    try:
        root_fd = _open_root_fd()
    except OSError:
        return []
    try:
        try:
            entries = os.listdir(root_fd)
        except OSError:
            return []
        out = []
        for name in entries:
            if not _valid_connector_id(name):
                continue
            connector_fd = manifest_fd = None
            try:
                connector_fd = _open_connector_dir_fd(root_fd, name)
                manifest_fd = _open_manifest_fd(connector_fd)
            except OSError:
                continue
            finally:
                if manifest_fd is not None:
                    os.close(manifest_fd)
                if connector_fd is not None:
                    os.close(connector_fd)
            out.append(name)
        return sorted(out)
    finally:
        os.close(root_fd)


# (root, id) -> (full stat identity, parsed manifest | None). `resolve()` runs on
# the patient site's listing loops (one slot_source resolution per service
# cell), and its default tier has to look at every manifest for the
# capability — parsing ~a dozen JSON files per cell would be the only real
# cost of the rewire. A stat-validated memo keeps it at one stat per manifest:
# a rewritten manifest changes its safety identity and is re-read on the next call, so
# tests that swap `ROOT` or rewrite a temp manifest never see a stale parse.
_MANIFEST_MEMO = {}


def load_manifest(connector_id):
    """The parsed connector.json for `connector_id`, or None if it doesn't
    exist / doesn't parse / is malformed. Never raises — a broken manifest
    degrades to "not found", same spirit as the rest of this module.

    Malformed = the manifest is not a JSON object, its `id` differs from the
    directory name, or it carries a `"default"` that is not a JSON boolean
    (`"true"` the string, a number, …) — a non-boolean default could never
    select an installation default, so the whole manifest is refused rather
    than silently half-honored. The reserved `OFF` id never loads."""
    if not _valid_connector_id(connector_id):
        return None
    memo_path = (os.path.abspath(_connectors_dir()), connector_id)
    root_fd = connector_fd = manifest_fd = None
    try:
        root_fd = _open_root_fd()
        connector_fd = _open_connector_dir_fd(root_fd, connector_id)
        manifest_fd = _open_manifest_fd(connector_fd)
        before = _stat_key(os.fstat(manifest_fd))
    except OSError:
        _MANIFEST_MEMO.pop(memo_path, None)
        for fd in (manifest_fd, connector_fd, root_fd):
            if fd is not None:
                os.close(fd)
        return None
    try:
        memo = _MANIFEST_MEMO.get(memo_path)
        after_stat = _stat_key(os.fstat(manifest_fd))
        if memo is not None and memo[0] == before == after_stat:
            return copy.deepcopy(memo[1]) if memo[1] is not None else None
        chunks = []
        while True:
            chunk = os.read(manifest_fd, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_read = _stat_key(os.fstat(manifest_fd))
        if before != after_read:
            _MANIFEST_MEMO.pop(memo_path, None)
            return None
        manifest = _parse_manifest_bytes(b"".join(chunks), connector_id)
        _MANIFEST_MEMO[memo_path] = (after_read, copy.deepcopy(manifest))
        return copy.deepcopy(manifest) if manifest is not None else None
    except OSError:
        _MANIFEST_MEMO.pop(memo_path, None)
        return None
    finally:
        for fd in (manifest_fd, connector_fd, root_fd):
            if fd is not None:
                os.close(fd)


def _parse_manifest_bytes(payload, connector_id):
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("id") != connector_id:
        return None
    if manifest.get("capability") not in _ABC_BY_CAPABILITY:
        return None
    if "default" in manifest and not isinstance(manifest["default"], bool):
        return None
    return manifest


def list_connectors():
    """[manifest, ...] for every discovered connector, sorted by id."""
    out = []
    for connector_id in list_connector_ids():
        manifest = load_manifest(connector_id)
        if manifest:
            out.append(manifest)
    return out


def connectors_for_capability(capability):
    """[manifest, ...] for every connector implementing `capability`."""
    return [m for m in list_connectors() if m.get("capability") == capability]


# ---- registry: active connector + config-key status ---------------------------

def _config_key_for_capability(capability):
    return "CONNECTOR_" + capability.upper()


def active_connector_id(capability):
    """The RAW value stored for `capability` (via `manage.py conn enable
    <id>`, through config.py the same way every other runtime setting is) —
    a connector id, the `OFF` sentinel after `conn disable`, or None when
    nothing is stored. This is the stored-value contract `manage.py` and the
    tests pin; `resolve()` is the interpretation consumers use (it adds the
    default tier and fails closed on an invalid value)."""
    raw = config.get(_config_key_for_capability(capability))
    return None if raw is None or raw == "" else raw


def active_connector(capability):
    """The manifest dict of the EXPLICITLY stored connector for `capability`,
    or None (unset, `OFF`, unknown id, or a connector of another capability).
    Does not consult the default tier — see `resolve()` for that."""
    connector_id = active_connector_id(capability)
    if not connector_id:
        return None
    manifest = load_manifest(connector_id)
    if not manifest or manifest.get("capability") != capability:
        return None
    return manifest


def _default_for(capability):
    """The default tier of `resolve()`: `(id, "default")` for the unique
    manifest flagged `"default": true` among the capability's connectors, else
    for the sole implementer when none is flagged; `(None, None)` otherwise
    (no implementer, several unflagged, or several flagged). Institutional
    capabilities in `_EXPLICIT_ONLY_CAPABILITIES` always return `(None, None)`;
    adding or removing a vendor package must never change their selection."""
    if capability in _EXPLICIT_ONLY_CAPABILITIES:
        return None, None
    manifests = connectors_for_capability(capability)
    flagged = [m["id"] for m in manifests if m.get("default") is True]
    if len(flagged) == 1:
        return flagged[0], "default"
    if not flagged and len(manifests) == 1:
        return manifests[0]["id"], "default"
    return None, None


def resolve(capability):
    """`(connector_id | None, source)` for `capability`, where `source` is one
    of "explicit", "off", "default", "invalid", or None (nothing configured
    and no default — degrade per the capability's ABC contract). See the
    module docstring for the four states; the rule is: an explicit stored
    value is honored exactly or fails closed, and only an ABSENT value falls
    through to the default tier."""
    raw = config.get(_config_key_for_capability(capability))
    if raw is None or raw == "":
        return _default_for(capability)
    raw = str(raw)
    if raw == OFF:
        return None, "off"
    if raw.isidentifier():
        manifest = load_manifest(raw)
        if manifest and manifest.get("capability") == capability:
            return raw, "explicit"
    return None, "invalid"


def adapter_for(capability):
    """The ABC instance to call for `capability`, or None when nothing is
    routed (off / invalid / no default / the adapter cannot be instantiated)
    — the caller then degrades exactly as its ABC's docstring says. A fresh
    instance per call: adapters are stateless one-liners over their `_impl`
    module, so this stays cheap and keeps every `connectors.<id>.<impl>`
    monkeypatch visible."""
    connector_id, _source = resolve(capability)
    if not connector_id:
        return None
    return _instantiate_adapter(connector_id)


def _instantiate_adapter(connector_id):
    """Instantiate `connectors/<id>/adapter.py`'s connector class, or None.

    The id-level entry point (`adapter_for()` is the capability-level one):
    fan-out capabilities such as `clinical_history_lookup`, where EVERY
    discovered archive answers, call this per discovered id. Resolution is
    through the real package path (`importlib.import_module(
    "connectors.<id>.adapter")`, never a file-path loader), so `from
    connectors import …` and `from . import x as _impl` inside adapters keep
    working. Among the module's OWN classes (`__module__` equal to the
    adapter module's name) the unique concrete strict subclass of the
    capability's ABC is instantiated with no arguments. Zero or several
    candidates, a reserved/non-identifier id, a missing or malformed
    manifest, or any import/instantiation error → None (degradation
    contract; `manage.py conn info` is where the operator sees why)."""
    if not connector_id or connector_id == OFF:
        return None
    connector_id = str(connector_id)
    if not connector_id.isidentifier():
        return None
    manifest = load_manifest(connector_id)
    if not manifest:
        return None
    abc_cls = abc_for_capability(manifest.get("capability"))
    if abc_cls is None:
        return None
    package_identity = _local_module_file_identity(connector_id, "__init__.py")
    adapter_identity = _local_module_file_identity(connector_id, "adapter.py")
    if package_identity is None or adapter_identity is None:
        return None
    package_name = f"{__name__}.{connector_id}"
    module_name = f"{__name__}.{connector_id}.adapter"
    package_dir = os.path.dirname(adapter_identity[0])
    package_spec = importlib.machinery.PathFinder.find_spec(package_name, list(__path__))
    adapter_spec = importlib.machinery.PathFinder.find_spec(module_name, [package_dir])
    if (package_spec is None or adapter_spec is None
            or os.path.abspath(str(package_spec.origin or "")) != package_identity[0]
            or os.path.abspath(str(adapter_spec.origin or "")) != adapter_identity[0]):
        return None
    preloaded_package = sys.modules.get(package_name)
    if preloaded_package is not None:
        package_path = os.path.abspath(str(getattr(preloaded_package, "__file__", "")))
        package_origin = os.path.abspath(str(getattr(
            getattr(preloaded_package, "__spec__", None), "origin", "")))
        if package_path != package_identity[0] or package_origin != package_identity[0]:
            return None
    preloaded = sys.modules.get(module_name)
    if preloaded is not None:
        preloaded_path = os.path.abspath(str(getattr(preloaded, "__file__", "")))
        preloaded_origin = os.path.abspath(str(getattr(
            getattr(preloaded, "__spec__", None), "origin", "")))
        if preloaded_path != adapter_identity[0] or preloaded_origin != adapter_identity[0]:
            return None
    try:
        mod = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 — ImportError, SyntaxError, anything the adapter's import raises
        return None
    module_path = os.path.abspath(str(getattr(mod, "__file__", "")))
    module_origin = os.path.abspath(str(getattr(getattr(mod, "__spec__", None), "origin", "")))
    if module_path != adapter_identity[0] or module_origin != adapter_identity[0]:
        return None
    if (_local_module_file_identity(connector_id, "__init__.py") != package_identity
            or _local_module_file_identity(connector_id, "adapter.py") != adapter_identity):
        return None
    candidates = []
    for obj in vars(mod).values():
        if (inspect.isclass(obj) and obj.__module__ == mod.__name__
                and obj is not abc_cls and issubclass(obj, abc_cls)
                and not inspect.isabstract(obj)
                and not any(obj is c for c in candidates)):
            candidates.append(obj)
    if len(candidates) != 1:
        return None
    try:
        return candidates[0]()
    except Exception:  # noqa: BLE001 — a constructor that needs arguments or raises
        return None


def config_key_status(connector_id):
    """{"KEY": "set"|"missing", ...} for every config_keys entry in this
    connector's manifest — `manage.py conn list/info` reads this to show
    which keys still need a value. Never reveals the actual value (masking is
    the CALLER's job when it wants to display one; this only reports
    presence)."""
    manifest = load_manifest(connector_id) or {}
    status = {}
    for key in manifest.get("config_keys") or []:
        status[key] = "set" if config.get(key) else "missing"
    return status


def is_configured(connector_id):
    """True if every config_keys entry for this connector has a value. A
    connector with an empty config_keys list (nothing to configure — e.g. a
    connector that only needs a FAKE_* toggle, already covered by its own
    env) is always considered configured."""
    status = config_key_status(connector_id)
    return all(v == "set" for v in status.values())


def enable(connector_id, values=None):
    """Make `connector_id` the active connector for its own capability
    (overwriting an earlier `OFF`), and persist any given config-key values
    (dict) through config.py the same way Settings does. Raises ValueError if
    the id isn't a real connector — the reserved `OFF` name included. Under
    `TURNOS_CONFIG_LOCK=1` the write still lands but config.get() ignores DB
    values until the lock is lifted (env/.env win meanwhile) — the CLI says
    so; the write is not refused."""
    manifest = load_manifest(connector_id)
    if not manifest:
        raise ValueError(f"connector desconocido: {connector_id!r}")
    overrides = dict(values or {})
    overrides[_config_key_for_capability(manifest["capability"])] = connector_id
    return config.guardar(overrides)


def disable(capability):
    """Stop routing `capability` through any connector: store the reserved
    `OFF` sentinel (NOT an empty value — `config.guardar` deletes empty keys,
    which would silently fall back to the default tier instead of switching
    the capability off). Config keys the connector needed stay stored —
    disabling isn't "forget the credentials", it's "stop routing". Raises
    ValueError for an unrecognized capability."""
    if capability not in CAPABILITIES:
        raise ValueError(f"capability desconocida: {capability!r}")
    return config.guardar({_config_key_for_capability(capability): OFF})
