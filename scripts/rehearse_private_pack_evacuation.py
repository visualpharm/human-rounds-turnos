#!/usr/bin/env python3
"""Hermetic rehearsal of evacuating a fictional private town pack.

Generates a temporary ``ar-b-pinamar`` stub with invented names and bytes,
stamps ``INVENTORY.json``, archives it, restores it outside the public tree,
re-hashes every file, and proves identical in-process rendering of
``/centers``, one center page, one service page, and the doctor-photo bytes
that ``/webdata/doctor-photos/<id>.webp`` would serve.

It never reads the checkout's Pinamar pack, never talks to a network, DB,
Docker, or secret store, and never touches PHI.  Corruption, symlinks, and
archive path-escape fail closed before any render callback runs.  The
executable path compares representative renders before and after restore,
always deletes its scratch tree, and writes archives whose bytes ignore
source mtime and ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PACK_ID = "ar-b-pinamar"
CENTER_SLUG = "hospital-ficcion"
SERVICE_ID = "consulta-ficcion"
PHOTO_REL = "doctor-photos/ficcion.webp"
PHOTO_ROUTE = "/webdata/" + PHOTO_REL
PHOTO_BYTES = (
    b"RIFF\x24\x00\x00\x00WEBPVP8 "
    b"human-rounds-synthetic-evac-photo-v1\n"
)
ARCHIVE_MTIME = 0
ARCHIVE_UID = 0
ARCHIVE_GID = 0
ARCHIVE_MODE = 0o644
CHECKOUT_PRIVATE = os.path.join(os.path.realpath(ROOT), "packs", PACK_ID)
_CHECKOUT_PRIVATE_LEXICAL = os.path.join(os.path.abspath(ROOT), "packs", PACK_ID)
_RUNTIME_ENV = (
    "TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS", "TURNOS_WEBDATA_DIR",
    "PACK_CHAIN", "TURNOS_CONFIG_LOCK", "TURNOS_ENV_FILE", "TURNOS_DATA_DIR",
    "TURNOS_PUBLIC_ORIGIN", "TURNOS_THEME_DEFAULT", "TURNOS_FAKE_AI",
    "TURNOS_SKIP_HSI",
)


class EvacuationError(RuntimeError):
    """The rehearsal refused to proceed.  Messages never echo private bytes."""


def _checkout_roots():
    return (CHECKOUT_PRIVATE, _CHECKOUT_PRIVATE_LEXICAL)


def _under(path, root):
    return path == root or path.startswith(root + os.sep)


def assert_not_checkout_private(path):
    """Refuse the checked-in Pinamar pack without opening any child of it."""
    abs_path = os.path.abspath(path)
    if any(_under(abs_path, root) for root in _checkout_roots()):
        raise EvacuationError("checkout private pack is out of bounds")
    real = os.path.realpath(abs_path)
    if any(_under(real, root) for root in _checkout_roots()):
        raise EvacuationError("checkout private pack is out of bounds")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def seed_synthetic_pack(directory):
    """Write a temporary fictional town pack.  ``directory`` is the pack root."""
    assert_not_checkout_private(directory)
    if os.path.exists(directory):
        raise EvacuationError("synthetic pack target already exists")
    os.makedirs(directory)
    _write(os.path.join(directory, "pack.json"),
           json.dumps({"id": PACK_ID, "example": True}, indent=2) + "\n")
    _write(os.path.join(directory, "sitio.json"), json.dumps({
        "city": "Ficcion",
        "province": "Greenfield",
        "hospital": "Hospital Ficcion",
        "network": "Hospital Ficcion",
        "network_long": "Hospital Ficcion",
        "main_center": CENTER_SLUG,
        "emergency_number": "112",
    }, indent=2) + "\n")
    _write(os.path.join(directory, "centros-salud.json"), json.dumps({
        "centros": [{
            "slug": CENTER_SLUG,
            "name": "Hospital Ficcion",
            "address": "Calle Ficcion 1",
            "phone": "(011) 4000-0000",
            "description": "Hospital municipal ficticio.",
            "jtbd": "Atencion de un pueblo ficticio.",
            "jobs": ["Consulta"],
            "bookable": True,
        }],
    }, indent=2) + "\n")
    _write(os.path.join(directory, "servicios.json"), json.dumps({
        "servicios": [{
            "id": SERVICE_ID,
            "nombre": "Consulta",
            "centro": CENTER_SLUG,
            "modo": "walkin",
            "descripcion": "Consulta en el hospital ficticio.",
            "horario": "Lunes a viernes de 8 a 12 h",
            "cluster": "clinica",
        }],
    }, indent=2) + "\n")
    _write(os.path.join(directory, PHOTO_REL), PHOTO_BYTES)
    return directory


def stamp_private_inventory(directory):
    """Write ``INVENTORY.json`` through the reviewed private-pack owner."""
    assert_not_checkout_private(directory)
    from app.core import data_packs
    data_packs.write_inventory(directory)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(directory):
    found = {}
    for current, dirs, names in os.walk(directory, followlinks=False):
        for name in list(dirs):
            path = os.path.join(current, name)
            if os.path.islink(path):
                raise EvacuationError("pack symlink invalid")
        for name in names:
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                raise EvacuationError("pack file invalid")
            rel = os.path.relpath(path, directory).replace(os.sep, "/")
            found[rel] = path
    return found


def _tarinfo(rel, path):
    info = tarfile.TarInfo(name=PACK_ID + "/" + rel)
    info.size = os.path.getsize(path)
    info.mtime = ARCHIVE_MTIME
    info.uid = ARCHIVE_UID
    info.gid = ARCHIVE_GID
    info.uname = ""
    info.gname = ""
    info.mode = ARCHIVE_MODE
    info.type = tarfile.REGTYPE
    return info


def archive_pack(src, dest):
    """Write a plain tar of ``src`` with ``PACK_ID`` as the top member."""
    assert_not_checkout_private(src)
    assert_not_checkout_private(dest)
    if os.path.exists(dest):
        raise EvacuationError("archive already exists")
    files = _regular_files(src)
    tmp = dest + ".partial"
    try:
        with tarfile.open(tmp, "w", format=tarfile.USTAR_FORMAT,
                          encoding="utf-8", errors="strict") as tar:
            for rel in sorted(files):
                path = files[rel]
                with open(path, "rb") as payload:
                    tar.addfile(_tarinfo(rel, path), payload)
        os.rename(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        if os.path.exists(dest):
            os.unlink(dest)
        raise
    return dest


def _validate_tar_member(info):
    name = info.name.replace("\\", "/")
    if name.startswith("/") or name.startswith("./") or not name:
        raise EvacuationError("archive member unsafe")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts) or parts[0] != PACK_ID:
        raise EvacuationError("archive member unsafe")
    if info.issym() or info.islnk() or info.isdir():
        raise EvacuationError("archive member unsafe")
    if not info.isreg():
        raise EvacuationError("archive member unsafe")
    return "/".join(parts[1:])


def restore_pack(archive, dest_root):
    """Extract ``archive`` into ``dest_root/PACK_ID``, rejecting escapes."""
    assert_not_checkout_private(archive)
    assert_not_checkout_private(dest_root)
    target = os.path.join(dest_root, PACK_ID)
    assert_not_checkout_private(target)
    if os.path.exists(target):
        raise EvacuationError("restore target already exists")
    if not os.path.isfile(archive):
        raise EvacuationError("archive missing")
    try:
        with tarfile.open(archive, "r") as tar:
            members = tar.getmembers()
            if not members:
                raise EvacuationError("archive empty")
            planned = []
            for info in members:
                rel = _validate_tar_member(info)
                if not rel:
                    raise EvacuationError("archive member unsafe")
                dest = os.path.normpath(os.path.join(target, rel))
                if dest != target and not dest.startswith(target + os.sep):
                    raise EvacuationError("archive member unsafe")
                planned.append((info, dest))
            os.makedirs(target)
            for info, dest in planned:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                src = tar.extractfile(info)
                if src is None:
                    raise EvacuationError("archive member unsafe")
                with src:
                    payload = src.read()
                if len(payload) != info.size:
                    raise EvacuationError("archive member unsafe")
                with open(dest, "wb") as out:
                    out.write(payload)
    except Exception:
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def verify_restored_bytes(directory):
    """Re-hash every restored file against ``INVENTORY.json`` (full coverage)."""
    assert_not_checkout_private(directory)
    inventory_path = os.path.join(directory, "INVENTORY.json")
    try:
        with open(inventory_path, encoding="utf-8") as f:
            inventory = json.load(f)
    except (OSError, ValueError) as exc:
        raise EvacuationError("inventory invalid") from exc
    entries = inventory.get("files") if isinstance(inventory, dict) else None
    if not isinstance(entries, list) or not entries:
        raise EvacuationError("inventory invalid")
    expected = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise EvacuationError("inventory invalid")
        rel = entry["path"]
        if not isinstance(rel, str) or not rel or rel == "INVENTORY.json":
            raise EvacuationError("inventory invalid")
        expected[rel] = (entry["size"], entry["sha256"])
    actual = _regular_files(directory)
    actual.pop("INVENTORY.json", None)
    if set(expected) != set(actual):
        raise EvacuationError("inventory incomplete")
    for rel, path in actual.items():
        size, checksum = expected[rel]
        try:
            if os.path.getsize(path) != size or _sha256(path) != checksum:
                raise EvacuationError("inventory checksum mismatch")
        except OSError as exc:
            raise EvacuationError("pack file invalid") from exc


def verify_then_render(directory, render):
    """Integrity first.  ``render`` must not run if verification fails."""
    verify_restored_bytes(directory)
    return render()


def remove_in_tree_pack(packs_dir):
    """Delete the in-tree synthetic private pack, never the checkout copy."""
    target = os.path.join(packs_dir, PACK_ID)
    assert_not_checkout_private(target)
    if os.path.isdir(target):
        shutil.rmtree(target)
    return target


def render_representative_routes():
    """In-process HTML for centers/center/service plus photo bytes."""
    from app.core import i18n
    from app.patient_web import site_page_centers, site_page_service
    from app.core import data_packs

    i18n.bind_lang("es")
    centers = site_page_centers._page_centers()
    center = site_page_centers._page_center(CENTER_SLUG)
    service = site_page_service._page_service(SERVICE_ID)
    if not centers or not center or not service:
        raise EvacuationError("representative route rendered empty")
    photo_path = data_packs.resolve_path(PHOTO_REL)
    assert_not_checkout_private(photo_path)
    with open(photo_path, "rb") as f:
        photo = f.read()
    return {
        "centers": centers,
        "center": center,
        "service": service,
        "photo": photo,
    }


def _clear_render_caches():
    from app.core import config
    from app.core import data_packs
    data_packs.invalidate()
    config._reset_cache_para_tests()
    if "app.patient_web.site_catalog" not in sys.modules:
        return
    from app.core import site_identity
    from app.patient_web import catalog
    from app.patient_web import site_base, site_catalog
    catalog._JSON_CACHE.clear()
    catalog.invalidate()
    site_base._CACHE.clear()
    site_catalog._SERVICES_MEMO.clear()
    site_identity._CACHE.clear()


def _bind_pack_runtime(packs_dir, legacy_dir):
    from app.core import data_packs
    data_packs._PACKS_DIR = packs_dir
    data_packs._LEGACY_WEBDATA_DIR = legacy_dir


def main(argv=None):
    """Run one disposable rehearsal in a scratch tree and print a value-free ok."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        raise SystemExit("rehearse_private_pack_evacuation accepts no arguments")
    from app.core import data_packs
    saved_packs = data_packs._PACKS_DIR
    saved_legacy = data_packs._LEGACY_WEBDATA_DIR
    saved_env = {key: os.environ.get(key) for key in _RUNTIME_ENV}
    saved_database = os.environ.pop("DATABASE_URL", None)
    tmp = tempfile.mkdtemp(prefix="human-rounds-evac-")
    try:
        os.environ["TURNOS_FAKE_AI"] = "1"
        os.environ["TURNOS_SKIP_HSI"] = "1"
        os.environ["PACK_CHAIN"] = PACK_ID
        os.environ["TURNOS_PUBLIC_ORIGIN"] = "https://ficcion.example"
        os.environ["TURNOS_THEME_DEFAULT"] = "humanrounds"
        os.environ["TURNOS_ENV_FILE"] = os.devnull
        for key in ("TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS",
                    "TURNOS_WEBDATA_DIR", "TURNOS_CONFIG_LOCK", "TURNOS_DATA_DIR"):
            os.environ.pop(key, None)
        packs_dir = os.path.join(tmp, "packs")
        legacy = os.path.join(tmp, "legacy")
        external = os.path.join(tmp, "external")
        restored_root = os.path.join(tmp, "restored")
        os.makedirs(packs_dir)
        os.makedirs(legacy)
        os.makedirs(external)
        os.makedirs(restored_root)
        _bind_pack_runtime(packs_dir, legacy)
        in_tree = seed_synthetic_pack(os.path.join(packs_dir, PACK_ID))
        stamp_private_inventory(in_tree)
        _clear_render_caches()
        before = render_representative_routes()
        archive = os.path.join(external, PACK_ID + ".tar")
        archive_pack(in_tree, archive)
        restored = restore_pack(archive, restored_root)
        verify_restored_bytes(restored)
        remove_in_tree_pack(packs_dir)
        os.environ["TURNOS_PRIVATE_PACKS_DIR"] = restored_root
        os.environ["TURNOS_REQUIRED_PACKS"] = PACK_ID
        os.environ["PACK_CHAIN"] = PACK_ID
        _clear_render_caches()
        data_packs.verify_required_packs()
        after = verify_then_render(restored, render_representative_routes)
        if before != after:
            raise EvacuationError("representative render mismatch")
        print("rehearsal ok")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        data_packs._PACKS_DIR = saved_packs
        data_packs._LEGACY_WEBDATA_DIR = saved_legacy
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if saved_database is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_database
        try:
            _clear_render_caches()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
