"""data_packs.py — layered data packs (phase 2 of the open-source rework,
2026-07-30). Evolves `sitedata.py`'s single-directory swap into a CHAIN of
directories layered by locality level, while keeping `sitedata.path(fname)`
(the call site every other module already uses) working unchanged —
`sitedata.path()` delegates here.

Layout (`packs/<id>/pack.json` = `{id, level (1-5), parent, language, name,
description, compose?}`):

    packs/global/            L1 — engine-agnostic medical content
    packs/ar/                L2 — Argentina
    packs/ar-b/               L3 — Buenos Aires province
    packs/example-town/      L4 — the fictional scaffold for a new town

Private installation packs use the same layout below
``TURNOS_PRIVATE_PACKS_DIR``. Pinamar's reference deployment therefore keeps
``ar-b-pinamar/`` outside this public checkout while the resolver and public
URL contract remain unchanged.

## Two modes

- **Pack mode** (default once `packs/` exists on disk): the active chain is
  `config.get("PACK_CHAIN", default="global,example-town")`, a
  comma-separated list of pack ids, LOW level first. Any pack id in the
  chain that isn't actually scaffolded on disk is silently dropped (a
  half-configured chain degrades, it never crashes).
- **Legacy mode** (`TURNOS_WEBDATA_DIR` set, OR `packs/` doesn't exist at
  all): behaves EXACTLY like the old `sitedata.py` — one directory, no
  layering. `TURNOS_WEBDATA_DIR` always wins over pack mode: it's the
  explicit single-dir override tests and one-off checkouts rely on
  (`test_sitedata_dir.py`'s Sampleton fixture, served from
  `packs/example-town/` this way — `webdata-ejemplo/` retired 2026-07-30).

## Resolution: override vs merge (the design decision this module makes)

Two different semantics are needed for two different KINDS of file, and
callers pick which one they want by calling `path()` or `merged()`:

- **`path(fname)` — whole-file OVERRIDE.** The highest-level pack in the
  chain that HAS the file wins, entirely; lower levels are invisible once a
  higher one defines it. This is for files that describe ONE deployment's
  concrete reality, where "inheriting" a base version makes no sense:
  `sitio.json` (site identity), `centros-salud.json`, `servicios.json`,
  `medicos.json`, `msd_turnos.json`, review/gallery/photo JSON. A town pack
  or center pack supplies the WHOLE current file, not a diff.
- **`merged(fname, list_key, id_key)` — id-keyed list MERGE**, a generic
  primitive: every pack in the chain that has the file contributes its
  list, low-to-high; an item whose `id_key` was already seen REPLACES it in
  place (keeps the base's ordering), a new id is appended. Used directly by
  `compose()` (below) and available to any future caller that needs the
  same overlay shape without going through the composable-file registry.

## Composable files (taxonomies) — the compose registry

Only files registered in `_COMPOSABLE` (data_packs.py) get MERGE semantics;
every other file keeps whole-file OVERRIDE via `path()` (see above) — this
is a closed list, not a general escape hatch. Today: `especialidades.json`
(list `specialties`, id `nombre`) and `clusters.json` (list `clusters`, id
`id`) — the specialty → cluster taxonomy and the directory's cluster
catalog, both meant to COMPOSE across geography (a province extending the
national specialty list with one local entry shouldn't have to retype the
other forty) — and, since phase 3b (2026-08-06), `conocimiento.json` (list
`articles`, id `id`): the assistant's knowledge wiki, so a town-level pack
can carry only its OWN local articles while still inheriting the
country/province/engine-level ones instead of retyping (or, before this,
simply never seeing) them. Callers read a composable file through
`compose(fname, center=None)`, never `merged()` directly and never
`path()`.

`compose()` implements three rules on top of plain `merged()`:

1. **Default = merge**, low level → high level, higher level wins per id —
   exactly `merged()`'s contract.
2. **Escape hatch — `"replace"`.** A pack's `pack.json` may declare
   `"compose": {"<fname>": "replace"}`. From that pack's level DOWN, every
   ancestor is invisible for that one file — the declaring pack's own copy
   is the WHOLE truth. `packs/example-town/pack.json` declares this for
   both `especialidades.json` and `clusters.json`: Sampleton's own
   subset doesn't line up 1:1 with Pinamar's full catalog (e.g. "Nutrición"
   falls in a different cluster in each), so composing it against
   `packs/global/`'s 40 entries would fuse the two catalogs into nonsense —
   this is precisely the case an earlier wiring attempt hit and got
   reverted for (see docs/architecture/data-packs.md).
3. **Tombstone.** An entry `{"id": "x", "_removed": true}` (matched by
   whichever `id_key` that file uses) in a higher-level pack removes id `x`
   from the composed result, at any level, "replace" or not. Re-adding the
   same id at a still-higher level (without `_removed`) brings it back
   (appended, since the tombstone step forgets its original position).

`especialidades.json` and `clusters.json` were this rework's pilot: moved OUT
of `webdata/` into `packs/global/` to prove `path()`'s override chain works
end-to-end for Pinamar's own deployment; phase 3 (2026-07-30) wired them
through `compose()` instead of `path()` so they actually compose across the
chain (`catalog.specialties()`, `site_catalog._clusters()`). Every remaining
Pinamar file followed in later slices (round 1, round 2, and the
binary/public-URL drain, 2026-07-30) — `webdata/` is now EMPTY,
the private `ar-b-pinamar/` pack is Pinamar's real data home, and legacy single-dir
mode (below) is the only thing left that can still point at `webdata/`.

## Center packs (L5)

`active_chain(center=None)` / `resolve_path(fname, center=None)` /
`merged(...)` / `compose(...)` all take an optional `center` (a center's
`slug`, e.g. `"hospital-pinamar"`): when given, and the chain's HIGHEST pack
has a scaffolded `centers/<center>/pack.json` under it, that pack is
appended to the chain as one more (highest-priority) segment — a real
compound id, `"<town-id>/centers/<center>"` (e.g.
`"ar-b-pinamar/centers/hospital-pinamar"`; `_pack_dir()` just joins path
components, so a `/`-bearing id resolves straight to the nested directory,
no special-casing needed). Same degrade-not-crash rule as any other chain
entry: no `center` given, or that center has no pack scaffolded, and the
chain is exactly what `active_chain()` without a center would have
returned — a page for a center with no L5 pack renders identically to
before this existed.

This is `path()`-style whole-file OVERRIDE, not `merged()`/`compose()` — a
center pack's file is either absent (center inherits the town's) or present
(center's file wins for that name), same override contract as every other
non-composable file. It is NOT the same mechanism as the compose registry
above; a center pack augmenting a composable file would still merge (the
`center` param threads through `compose()` too), but no current pack does
that.

**The one real L5 pack today**: the private
`ar-b-pinamar/centers/hospital-pinamar/` directory
carries `preparacion-centro.json`, a small `{"overrides": [{"id": ...,
<fields to replace>}]}` list applied by `site_preparation._prep_section_html()`
on top of the matched `estudios-preparacion.json` entry, ONLY when the
service being rendered belongs to that center (`s.get("centro")`). It
overrides `laboratorio-sangre`'s fasting note with the fact that the
Hospital's own lab sign doesn't publish it online (points patients to
consultorio 5 instead) — a documentary note (`estudios-preparacion-meta.json
:_meta.nota_laboratorio_hospital_pinamar`) that had been sitting unwired
since the file-rename slice, now actually rendered. Every OTHER center
offering the same "Extracciones de laboratorio" service (`caps-ostende`,
`caps-valeria`) has no pack of its own, so they keep the generic note —
proven by `tests/test_data_packs.py`'s `TestCenterPackHospitalPinamar`.

## Retired: the `ar-b-pinamar` legacy fallback

Until 2026-07-30, `resolve_path()`/`merged()` fell back to `webdata/<fname>`
whenever `ar-b-pinamar` was in the active chain and no pack had the file —
Pinamar's locality data hadn't been sorted into `packs/ar-b-pinamar/` yet, so
pack mode becoming the default the moment `packs/` existed couldn't be
allowed to 404 every Pinamar file. That fallback (`_LEGACY_SOURCE_PACK`) is
now DELETED: every file the mass-migration slices moved has a real home
under `packs/ar-b-pinamar/`, so the fallback was dead code — its deletion is
this migration's completion signal (see docs/architecture/data-packs.md).
`TURNOS_WEBDATA_DIR`'s explicit single-dir override mode is unrelated and
unaffected — it still resolves straight to `_LEGACY_WEBDATA_DIR` (or the
override dir), see "Two modes" above.

## Cache-key contract

Every mtime cache built on top of `path()`/`resolve_path()` (`catalog.py`'s
`_JSON_CACHE`, `site_base.py`'s `_CACHE`, `site_identity.py`'s `_CACHE`)
MUST key by the RESOLVED PATH, never the bare filename — otherwise a
pack-chain change (or a file moving between levels) serves a stale
cross-pack collision instead of invalidating. `resolve_path()` is cheap
(a handful of `os.path.isfile` checks) but not free; callers that resolve
on every request should still resolve once and cache the mtime the way the
existing three already do, they just need to fix their cache KEY.
"""

import hashlib
import json
import os
import stat

from app.core import config
from app.core import paths
from app.core import settings

_DIR = paths.PROJECT_ROOT
_PACKS_DIR = os.path.join(_DIR, "packs")
_LEGACY_WEBDATA_DIR = os.path.join(_DIR, "webdata")

DEFAULT_CHAIN = ("global", "example-town")

_MANIFEST_CACHE = {}   # pack_id -> (mtime, dict) | (None, None) if missing
_MERGE_CACHE = {}      # (resolved chain tuple, fname) -> (mtimes tuple, list)
_PRIVATE_READY_CACHE = {}  # private pack dir -> (whole-tree stat stamp, ready)
_VERIFIED_REQUIRED_PACKS = set()  # (pack id, exact selected directory)

_INVENTORY_NAME = "INVENTORY.json"
_BINARY_SAMPLE_SIZE = 8


class RequiredPackError(RuntimeError):
    """A configured required pack is unsafe or incomplete for this boot.

    Messages deliberately name the configured pack and failed check, never a
    filesystem value, asset path, or checksum.  Those values can be clinical
    data in a private pack and are not useful in an operator-facing boot log.
    """


def _boot_settings():
    """The install-owned pack keys, read before the DB/migration layer is available.

    Required-pack readiness is intentionally process/effective-``.env`` only:
    process environment wins, then ``TURNOS_ENV_FILE`` via config's public
    parser (``core.settings.InstallationSettings``).  DB settings are
    unavailable by design because consulting them can initialize the catalog
    and run migrations before a bad required pack has been rejected.
    ``TURNOS_CONFIG_LOCK`` is therefore naturally satisfied: the process
    environment already wins without consulting the DB.
    """
    return settings.InstallationSettings.current()


def _safe_pack_id(pack_id):
    """Whether ``pack_id`` is a relative pack-tree identifier.

    Center packs are compound ids (``town/centers/center``), so slashes are
    valid.  Dot components, empty components, and absolute paths never are.
    """
    if not isinstance(pack_id, str) or not pack_id or os.path.isabs(pack_id):
        return False
    parts = pack_id.replace("\\", "/").split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _inside(path, root):
    """Containment after resolving symlinks, without a string-prefix bug."""
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root)
    except (OSError, ValueError):
        return False


def private_packs_dir():
    """Configured private packs root, or ``None`` when private packs are off.

    This is only an accessor. ``private_root_is_verified`` below is the
    security boundary used before a caller is allowed to serve from it.
    """
    configured = _boot_settings().private_packs_dir
    return os.path.abspath(configured) if configured else None


def _data_dir():
    """The installation's persistent data directory — the mounted volume in
    production (Coolify mounts ``/app/data`` over the image's baked-in
    ``data/``), a local directory in development.  Install-owned artifacts
    that must survive a container rebuild live UNDER here, never in the image
    tree.  ``TURNOS_DATA_DIR`` overrides the path the same way every other
    boot setting is read (process env, then the effective ``.env``)."""
    override = _boot_settings().data_dir
    if override:
        return os.path.abspath(override)
    return os.path.join(_DIR, "data")


def install_packs_dir():
    """The durable, install-owned packs root a scaffolded town is written to.

    A configured ``TURNOS_PRIVATE_PACKS_DIR`` wins (the operator already
    pointed the resolver at their own volume); otherwise the default is
    ``<data_dir>/packs`` ON THE PERSISTENT VOLUME.  This is deliberately NOT
    the image-tree ``packs/`` that ``packs_dir()`` returns: a pack scaffolded
    into the image tree is wiped on the next rebuild — the durability bug this
    accessor exists to close.  The first-run installer writes a scaffolded
    town here, then records this same root in ``TURNOS_PRIVATE_PACKS_DIR`` so
    the reviewed private-pack resolver both finds it and fails closed if the
    volume is ever missing or corrupt.  See ``setup-wizard.md``.
    """
    configured = private_packs_dir()
    if configured:
        return configured
    return os.path.join(_data_dir(), "packs")


def _private_root_is_verified(root=None):
    """A private root must be a real directory, never a symlink itself."""
    root = private_packs_dir() if root is None else root
    return bool(root and os.path.isdir(root) and not os.path.islink(root))


def private_root_is_verified():
    """Public, value-free readiness predicate for static-serving callers."""
    return _private_root_is_verified()


def _private_pack_dir(pack_id, require_manifest=True):
    """A safe private replacement for ``pack_id``, if one is usable.

    Optional private packs are intentionally conservative: a malformed
    private stub does not shadow a valid public pack.  Required packs use the
    stricter verifier below and fail closed instead of taking this fallback.
    """
    root = private_packs_dir()
    if not _safe_pack_id(pack_id) or not _private_root_is_verified(root):
        return None
    candidate = os.path.join(root, *pack_id.split("/"))
    if not os.path.isdir(candidate) or not _inside(candidate, root):
        return None
    if require_manifest:
        manifest_path = os.path.join(candidate, "pack.json")
        try:
            with open(manifest_path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or doc.get("id") != pack_id:
            return None
        inventory_id = pack_id
        inventory_dir = candidate
        if "/" in pack_id:
            # Center packs are covered by their top-level town pack's closed
            # inventory rather than carrying a second, overlapping inventory.
            inventory_id = pack_id.split("/", 1)[0]
            inventory_dir = os.path.join(root, inventory_id)
            try:
                with open(os.path.join(inventory_dir, "pack.json"),
                          encoding="utf-8") as f:
                    root_doc = json.load(f)
            except (OSError, ValueError):
                return None
            if (not isinstance(root_doc, dict)
                    or root_doc.get("id") != inventory_id):
                return None
        if not _private_pack_is_ready(inventory_id, inventory_dir):
            return None
    return candidate


def _private_pack_is_ready(pack_id, directory):
    """Non-throwing private-pack integrity gate.

    Required packs have already crossed the exhaustive pre-listener verifier;
    request-time resolution must not rescan their entire closed inventory.
    Optional packs retain the historical immediate mutation detector because
    they have no boot admission boundary to rely on.
    """
    if (pack_id in _required_pack_ids()
            and (pack_id, os.path.abspath(directory)) in _VERIFIED_REQUIRED_PACKS):
        return True
    try:
        stamp = _private_tree_stamp(directory)
    except OSError:
        return False
    cached = _PRIVATE_READY_CACHE.get(directory)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        _verify_inventory(pack_id, directory)
        ready = True
    except RequiredPackError:
        ready = False
    _PRIVATE_READY_CACHE[directory] = (stamp, ready)
    return ready


def _private_tree_stamp(directory):
    """Cheap mutation detector for an optional pack's files and directories."""
    def entry(rel, path):
        st = os.lstat(path)
        return (rel, stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns,
                st.st_ctime_ns, st.st_dev, st.st_ino)

    # Include the root itself: replacing the mounted/private pack directory
    # must invalidate a warmed eligibility result even if child names match.
    stamp = [entry("", directory)]
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in sorted(dirs + names):
            path = os.path.join(current, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            stamp.append(entry(rel, path))
    return tuple(stamp)


def _pack_dir(pack_id):
    private = _private_pack_dir(pack_id)
    if private:
        return private
    return os.path.join(_PACKS_DIR, pack_id)


def pack_dir(pack_id):
    """Return the selected directory for one safe pack identifier.

    Offline maintenance tools use this public accessor instead of rebuilding
    a checkout-relative path that would bypass ``TURNOS_PRIVATE_PACKS_DIR``.
    A malformed identifier is rejected rather than joined outside either
    pack root.
    """
    if not _safe_pack_id(pack_id):
        raise ValueError("invalid pack identifier")
    return _pack_dir(pack_id)


def private_pack_dir(pack_id):
    """Return one verified private pack directory or fail closed.

    Maintenance scripts that write installation data must never fall back to
    an absent checkout directory: doing so could silently recreate a private
    town pack inside the public repository.
    """
    if not _safe_pack_id(pack_id):
        raise ValueError("invalid pack identifier")
    directory = _private_pack_dir(pack_id)
    if not directory:
        raise RequiredPackError("private pack unavailable")
    return directory


def packs_root_exists():
    """Whether this checkout has a packs/ directory at all — the switch
    between pack mode and pure legacy mode."""
    return os.path.isdir(_PACKS_DIR)


def packs_dir():
    """Absolute path to packs/ — public accessor for callers outside this
    module that need the boundary root (e.g. server.py's /data/<f> compat
    static route, which must accept resolve_path() results that now land
    under packs/ instead of only under webdata/)."""
    return _PACKS_DIR


def manifest(pack_id):
    """This pack's pack.json (id, level, parent, language, name,
    description), or None if it isn't scaffolded (no pack.json)."""
    p = os.path.join(_pack_dir(pack_id), "pack.json")
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        _MANIFEST_CACHE[pack_id] = (None, None)
        return None
    cached = _MANIFEST_CACHE.get(pack_id)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    _MANIFEST_CACHE[pack_id] = (mtime, data)
    return data


def _required_pack_ids():
    raw = _boot_settings().required_packs
    if not raw:
        return ()
    return tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))


def _fail(pack_id, check):
    raise RequiredPackError("required pack %r failed readiness: %s" % (pack_id, check))


def _selected_required_dir(pack_id):
    """Return the selected raw directory so a bad private pack cannot hide.

    The optional resolver deliberately falls back to public content for a bad
    private candidate.  A required pack must inspect that candidate instead:
    a mounted-but-corrupt replacement is a boot failure, not an excuse to
    serve an older public copy.
    """
    private_root = private_packs_dir()
    if private_root:
        if not _private_root_is_verified(private_root):
            _fail(pack_id, "private root invalid")
        # Once an installation configures a private root, an explicitly
        # required pack must come from that root.  Falling back to a bundled
        # public copy would mask an unmounted/partially seeded private volume.
        return os.path.join(private_root, *pack_id.split("/"))
    return os.path.join(_PACKS_DIR, *pack_id.split("/"))


def _allowed_pack_roots():
    roots = [_PACKS_DIR]
    private_root = private_packs_dir()
    if private_root and _private_root_is_verified(private_root):
        roots.append(private_root)
    return roots


def _inventory_entries(pack_id, directory):
    """Read and strictly validate the private-pack inventory format."""
    inventory_path = os.path.join(directory, _INVENTORY_NAME)
    try:
        with open(inventory_path, encoding="utf-8") as f:
            inventory = json.load(f)
    except (OSError, ValueError):
        _fail(pack_id, "inventory invalid")
    if not isinstance(inventory, dict) or set(inventory) != {"files"}:
        _fail(pack_id, "inventory invalid")
    entries = inventory.get("files")
    if not isinstance(entries, list) or not entries:
        _fail(pack_id, "inventory invalid")

    parsed = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            _fail(pack_id, "inventory invalid")
        rel, size, checksum = entry["path"], entry["size"], entry["sha256"]
        if (not isinstance(rel, str) or not rel or os.path.isabs(rel)
                or any(part in ("", ".", "..") for part in rel.replace("\\", "/").split("/"))
                or rel == _INVENTORY_NAME):
            _fail(pack_id, "inventory invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _fail(pack_id, "inventory invalid")
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            _fail(pack_id, "inventory invalid")
        if rel in parsed:
            _fail(pack_id, "inventory invalid")
        parsed[rel] = (size, checksum)
    return parsed


def _actual_pack_files(pack_id, directory):
    """Return every non-inventory file, rejecting all symlink indirection."""
    found = set()
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in list(dirs):
            path = os.path.join(current, name)
            if os.path.islink(path):
                _fail(pack_id, "pack symlink invalid")
        for name in names:
            path = os.path.join(current, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            if rel == _INVENTORY_NAME:
                continue
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                _fail(pack_id, "pack file invalid")
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                _fail(pack_id, "pack file invalid")
            found.add(rel)
    return found


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_inventory(pack_id, directory):
    entries = _inventory_entries(pack_id, directory)
    actual = _actual_pack_files(pack_id, directory)
    if set(entries) != actual or "pack.json" not in entries:
        _fail(pack_id, "inventory incomplete")

    json_files = sorted(path for path in actual if path.endswith(".json"))
    binary_files = sorted(path for path in actual if not path.endswith(".json"))
    # Deterministic and content-independent: a changed filename cannot choose
    # itself out of the sample merely by sorting after the known JSON files.
    binary_sample = sorted(binary_files, key=lambda path: hashlib.sha256(path.encode("utf-8")).hexdigest())[:_BINARY_SAMPLE_SIZE]
    for rel in json_files + binary_sample:
        path = os.path.join(directory, *rel.split("/"))
        expected_size, expected_hash = entries[rel]
        try:
            if os.path.getsize(path) != expected_size or _sha256(path) != expected_hash:
                _fail(pack_id, "inventory checksum mismatch")
        except OSError:
            _fail(pack_id, "pack file invalid")


def write_inventory(directory):
    """Write ``INVENTORY.json`` for every regular file under ``directory``.

    The strict inverse of ``_inventory_entries``: same relative-path
    normalisation, same ``size`` + ``sha256``, deterministic sort, and it
    skips the inventory itself plus anything that is not a regular file
    (``_actual_pack_files`` would reject a symlink there at verify time
    anyway).  Used by the first-run installer when it scaffolds an
    install-owned town pack, so the reviewed readiness gate admits that pack
    on the next boot instead of failing closed on a missing inventory.  No
    caller may use this to paper over a pack the installer did not just
    create — it only ever reflects the bytes that are on disk.
    """
    entries = []
    for current, _dirs, names in os.walk(directory, followlinks=False):
        for name in sorted(names):
            path = os.path.join(current, name)
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            if rel == _INVENTORY_NAME:
                continue
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            entries.append({"path": rel, "size": os.path.getsize(path),
                            "sha256": _sha256(path)})
    entries.sort(key=lambda e: e["path"])
    with open(os.path.join(directory, _INVENTORY_NAME), "w", encoding="utf-8") as f:
        json.dump({"files": entries}, f, indent=2)
        f.write("\n")


def verify_required_packs():
    """Fail closed for each explicitly required pack before accepting traffic.

    Optional packs retain the historical degrade-not-crash contract.  This
    function is intentionally called once at process startup; it does not add
    checksum I/O to request-time resolution.
    """
    verified = []
    for pack_id in _required_pack_ids():
        if not _safe_pack_id(pack_id):
            _fail(pack_id, "identifier invalid")
        directory = _selected_required_dir(pack_id)
        if not os.path.isdir(directory):
            _fail(pack_id, "directory missing")
        if not any(_inside(directory, root) for root in _allowed_pack_roots()):
            _fail(pack_id, "directory outside allowed root")
        manifest_path = os.path.join(directory, "pack.json")
        try:
            with open(manifest_path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            _fail(pack_id, "manifest invalid")
        if not isinstance(doc, dict) or doc.get("id") != pack_id:
            _fail(pack_id, "manifest identity invalid")
        _verify_inventory(pack_id, directory)
        verified.append((pack_id, os.path.abspath(directory)))
    _VERIFIED_REQUIRED_PACKS.update(verified)


def list_packs():
    """Every scaffolded pack's manifest, discovered from packs/*/pack.json
    (unordered) — for an admin "choose your chain" UI, a future slice."""
    if not packs_root_exists():
        return []
    out = []
    for name in sorted(os.listdir(_PACKS_DIR)):
        m = manifest(name)
        if m:
            out.append(m)
    return out


def active_chain(center=None):
    """The resolved pack chain (tuple of ids, LOW level first), or None in
    legacy single-dir mode.

    None when: `TURNOS_WEBDATA_DIR` is set (explicit override always wins),
    OR `packs/` doesn't exist on this checkout at all. Otherwise
    `config.get("PACK_CHAIN")` (comma-separated ids) or the default
    `global,example-town`, filtered down to ids that are actually
    scaffolded (a chain naming an unscaffolded pack degrades instead of
    crashing) — empty after filtering also means "legacy mode" (None).

    `center` (a center's `slug`) appends one more, highest-priority segment
    — `"<chain[-1]>/centers/<center>"` — IF that pack is scaffolded (has a
    `pack.json`); otherwise the chain is exactly what it would have been
    without `center` (degrade, never crash — see module docstring, "Center
    packs (L5)"). `center=None` (the default) never touches the chain, so
    every existing caller of `active_chain()` is unaffected."""
    if settings.ProcessSettings.current().webdata_dir:
        return None
    if not packs_root_exists():
        return None
    raw = config.get("PACK_CHAIN")
    if raw:
        chain = tuple(p.strip() for p in raw.split(",") if p.strip())
    else:
        chain = DEFAULT_CHAIN
    chain = tuple(p for p in chain if manifest(p) is not None)
    if not chain:
        return None
    if center:
        center_id = f"{chain[-1]}/centers/{center}"
        if manifest(center_id) is not None:
            chain = chain + (center_id,)
    return chain


def resolve_path(fname, center=None):
    """Full path to `fname` under the active chain (or the legacy single
    dir). See the module docstring for override-vs-merge semantics. Doesn't
    check existence beyond what's needed to pick the winning pack — a
    totally-missing file still returns a path (inside the lowest-level pack,
    or webdata/ in legacy mode), same contract as the old `sitedata.path()`:
    callers already handle `OSError` on open/getmtime.

    `center`: see `active_chain()` — passes straight through, so a center
    pack's own copy of `fname` (if any) outranks every other pack in the
    chain. `TURNOS_WEBDATA_DIR`'s single-dir override mode ignores `center`
    entirely (there's no chain to extend)."""
    override = settings.ProcessSettings.current().webdata_dir
    if override:
        return os.path.join(os.path.abspath(override), fname)
    chain = active_chain(center=center)
    if not chain:
        return os.path.join(_LEGACY_WEBDATA_DIR, fname)
    for pack_id in reversed(chain):  # highest level first: override wins
        candidate = os.path.join(_pack_dir(pack_id), fname)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(_pack_dir(chain[0]), fname)


def value(fname, *keys):
    """What `fname` (resolved through the active chain) holds under `keys` — `value("country.json", "phone")` is
    the `phone` block —, or None when the file is missing or is not JSON (`OSError`, `ValueError`: the two the
    loaders always caught) or an entry on the way is missing/empty. A file or an entry of the wrong SHAPE (a
    list or a string where an object is expected) raises `AttributeError`, exactly as the hand-written loaders
    did: a broken pack must fail loudly at import. The read-once-at-import settings of the connectors,
    `format.py` and `tz.py` all start here and keep their own check of the value and their own literal fallback."""
    try:
        with open(resolve_path(fname), encoding="utf-8") as f:
            node = json.load(f)
    except (OSError, ValueError):
        return None
    for key in keys:
        node = (node or {}).get(key)
    return node


def pack_setting(env_key, fallback, fname, *keys):
    """A text setting with the precedence the connectors' endpoints use: the environment variable `env_key`
    (`config.get`) > the text at `keys` in the pack file `fname` > the literal `fallback`."""
    override = config.get(env_key)
    if override:
        return override
    text = value(fname, *keys)
    return text if isinstance(text, str) and text else fallback


def _read_json(path):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            return mtime, json.load(f)
    except (OSError, ValueError):
        return None, None


def merged(fname, list_key, id_key="id"):
    """The id-keyed MERGE of `fname` across the active chain (see module
    docstring) — returns the merged list (not the `{list_key: [...]}`
    wrapper; re-wrap it if a caller needs that shape).

    Legacy mode (no active chain): just `path(fname)`'s one file, list at
    `list_key` — identical to what a direct `json.load` + `[list_key]`
    already does today, so wiring an existing reader over to `merged()` is
    a no-op change in legacy/single-dir deployments.
    """
    chain = active_chain()
    if not chain:
        _, data = _read_json(resolve_path(fname))
        return list((data or {}).get(list_key) or [])

    cache_key = (chain, fname)
    mtimes = []
    per_pack = []
    for pack_id in chain:
        p = os.path.join(_pack_dir(pack_id), fname)
        mtime, data = _read_json(p)
        mtimes.append(mtime)
        if data is not None:
            per_pack.append(data)
    mtimes = tuple(mtimes)
    cached = _MERGE_CACHE.get(cache_key)
    if cached and cached[0] == mtimes:
        return list(cached[1])

    out = []
    index = {}
    for data in per_pack:
        for item in (data.get(list_key) or []):
            iid = item.get(id_key)
            if iid in index:
                out[index[iid]] = item
            else:
                index[iid] = len(out)
                out.append(item)

    _MERGE_CACHE[cache_key] = (mtimes, out)
    return list(out)


# --------------------------------------------------------- compose registry
#
# Files composable across the chain (see module docstring, "Composable
# files"): fname -> (list_key, id_key). A CLOSED list — every file NOT in
# here keeps whole-file override via `path()`/`resolve_path()`, never
# `compose()`. Adding a file here is a real design decision (it starts
# composing instead of overriding), not something a caller opts into ad hoc.
_COMPOSABLE = {
    "especialidades.json": ("specialties", "nombre"),
    "clusters.json": ("clusters", "id"),
    "conocimiento.json": ("articles", "id"),
    # Golden conversations for the wiki (patient_web/knowledge_golden.py):
    # each pack ships the questions its own articles must keep answering.
    "conversaciones-doradas.json": ("conversations", "id"),
}


def is_composable(fname):
    return fname in _COMPOSABLE


def compose(fname, center=None):
    """The composed result of a REGISTERED composable file (`_COMPOSABLE`)
    across the active chain — low level -> high level, higher level wins
    per id (same replace-in-place/append contract as `merged()`), UNLESS a
    pack in the chain declares `"compose": {fname: "replace"}` in its
    `pack.json`: from that pack's level down every ancestor is invisible
    for this file, so the declaring pack's own copy is the whole truth (see
    module docstring — this is packs/example-town's escape hatch for its
    especialidades.json/clusters.json subset). A `{id_key: ..., "_removed":
    true}` entry in a higher pack tombstones that id out of the composed
    result, "replace" or not.

    Raises KeyError if `fname` isn't registered — every other file keeps
    whole-file override via `path()` (module docstring, item 5)."""
    list_key, id_key = _COMPOSABLE[fname]
    chain = active_chain(center=center)
    if not chain:
        _, data = _read_json(resolve_path(fname, center=center))
        items = list((data or {}).get(list_key) or [])
        return [it for it in items if not it.get("_removed")]

    # A pack that declares "replace" for this file makes every ancestor
    # below it invisible: scan from the top, first "replace" wins, and the
    # effective chain starts there.
    effective_chain = chain
    for pack_id in reversed(chain):
        m = manifest(pack_id) or {}
        if (m.get("compose") or {}).get(fname) == "replace":
            effective_chain = chain[chain.index(pack_id):]
            break

    cache_key = (effective_chain, fname, "__compose__")
    mtimes = []
    per_pack = []
    for pack_id in effective_chain:
        p = os.path.join(_pack_dir(pack_id), fname)
        mtime, data = _read_json(p)
        mtimes.append(mtime)
        if data is not None:
            per_pack.append(data)
    mtimes = tuple(mtimes)
    cached = _MERGE_CACHE.get(cache_key)
    if cached and cached[0] == mtimes:
        return list(cached[1])

    items = {}   # id -> item, insertion order = first-seen position
    for data in per_pack:
        for item in (data.get(list_key) or []):
            iid = item.get(id_key)
            if item.get("_removed"):
                items.pop(iid, None)
            else:
                items[iid] = item
    out = list(items.values())

    _MERGE_CACHE[cache_key] = (mtimes, out)
    return out


def invalidate():
    """Discards this module's own caches (manifests + merge results). Tests
    that flip TURNOS_WEBDATA_DIR/PACK_CHAIN mid-run call this alongside
    clearing catalog._JSON_CACHE/patient_site._CACHE/site_identity._CACHE —
    same pattern as catalog.invalidate()."""
    _MANIFEST_CACHE.clear()
    _MERGE_CACHE.clear()
    _PRIVATE_READY_CACHE.clear()
