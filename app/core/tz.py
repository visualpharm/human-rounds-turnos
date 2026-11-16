"""tz.py — the installation timezone, read from the active country pack.

Generalizes the repo's old "ART" (Argentina) invariant to "installation
timezone": every module that needs the deployment's local calendar day, hour
band or "now" reads it from HERE instead of hardcoding a zoneinfo string or
(worse) a fixed UTC offset. A fixed offset (``timezone(timedelta(hours=-3))``)
silently breaks the moment a deployment's country observes DST — Argentina
currently doesn't, so every one of the ~30 call sites that used to hardcode
that pattern behaved correctly by accident. `tz.tzinfo()` is zoneinfo-based
(DST-safe for any IANA zone) so the SAME code keeps working the day a
non-Argentina deployment picks a DST-observing timezone. Behavior for
Pinamar/Argentina TODAY is byte-identical to the old fixed-offset code —
`America/Argentina/Buenos_Aires` has had no DST since 2009.

Precedence, same pattern as `format.py`'s `PHONE` block: env override >
`packs/ar/country.json`'s `"timezone"` field (resolved through the active
pack chain, so a non-`ar` country pack overrides it there) > literal
fallback (`_FALLBACK_TZ_NAME`, this repo's only real deployment today, so
the fallback is never wrong in production even if `packs/` is missing or
mid-deploy).

Usage:
    from core import tz
    _ART = tz.tzinfo()                    # a zoneinfo.ZoneInfo, DST-safe
    now_local = datetime.now(tz.tzinfo())
    dia = now_local.date()
    tz.tz_name()                          # "America/Argentina/Buenos_Aires"

Read once at import — this is deployment config, not admin-editable content,
so no mtime-refresh dance (same reasoning `format.py`'s `PHONE` documents).
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core import data_packs
from app.core import settings

_FALLBACK_TZ_NAME = "America/Argentina/Buenos_Aires"


def _load_tz_name():
    env = settings.ProcessSettings.current().timezone
    if env:
        return env
    name = data_packs.value("country.json", "timezone")
    return name if isinstance(name, str) and name else _FALLBACK_TZ_NAME


def _resolve(name):
    """(name, ZoneInfo) for a candidate IANA id — degrades to the literal
    fallback if `name` isn't a zone this system's tzdata knows about (broken
    .env, typo'd pack, mid-deploy checkout). Never raises."""
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return _FALLBACK_TZ_NAME, ZoneInfo(_FALLBACK_TZ_NAME)


TZ_NAME, TZINFO = _resolve(_load_tz_name())


def tz_name():
    """IANA timezone id string for this installation, e.g.
    "America/Argentina/Buenos_Aires"."""
    return TZ_NAME


def tzinfo():
    """`zoneinfo.ZoneInfo` for this installation — DST-safe, use anywhere a
    fixed UTC-offset `timezone(timedelta(hours=...))` used to be hardcoded."""
    return TZINFO


def iso(dt):
    """`dt` in the installation timezone as an ISO-8601 string; None when there is no datetime."""
    return dt.astimezone(TZINFO).isoformat() if dt else None
