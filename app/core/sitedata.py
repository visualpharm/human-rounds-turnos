"""sitedata.py — where the locality's DATA package (webdata) lives.

Pinamar Turnos' code is generic; the locality's data (centers, services,
doctors, photos, preparations) is a swappable package. TURNOS_WEBDATA_DIR
points at another directory (e.g. packs/example-town/ or another municipality's
data checkout); without the env, the repo's webdata/, as always. The env is
read at boot: changing it live is not supported (catalog.py/patient_site.py's
caches key by resolved path, not just by directory).

`path()` is a thin compat shim over `data_packs.resolve_path()` (phase 2,
2026-07-30): every call site here (~15 modules) keeps working unchanged,
`data_packs.py` IS the implementation now — see its module docstring for the
pack-chain / override-vs-merge design. `webdata_dir()` stays exactly what it
always was (the single legacy directory, TURNOS_WEBDATA_DIR or webdata/,
never pack-aware) — callers that want "the one webdata-shaped directory"
(e.g. `system_status.py` displaying its name) still get that, while callers
that want "the file that's actually active right now" should prefer
`path()`, which may resolve into `packs/` instead.
"""

import os

from app.core import data_packs
from app.core import paths
from app.core import settings

_DIR = paths.PROJECT_ROOT


def webdata_dir():
    """Directory of the active LEGACY data package (abspath) — not
    pack-aware, see module docstring."""
    override = settings.ProcessSettings.current().webdata_dir
    if override:
        return os.path.abspath(override)
    return os.path.join(_DIR, "webdata")


def path(*parts, center=None):
    """Path to a file within the active data package — pack-chain aware
    (data_packs.resolve_path), falling back to the single legacy directory
    when there's no packs/ or TURNOS_WEBDATA_DIR is set.

    `center` (a center's slug): see data_packs.py's "Center packs (L5)" —
    when given, a scaffolded `centers/<center>/` pack outranks every other
    pack in the chain for this one lookup. Defaults to None (untouched),
    so every existing call site keeps behaving exactly as before."""
    return data_packs.resolve_path(os.path.join(*parts), center=center)
