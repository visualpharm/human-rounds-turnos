#!/usr/bin/env python3
"""Package a Chrome extension kept under extensions/<name>/.

    python3 scripts/build_extension.py            # every extension
    python3 scripts/build_extension.py hsi-humanizador # one

For each extension it writes, under extensions/dist/:
  <name>.zip        the folder a desk unzips and loads in chrome://extensions
  <name>-store.zip  the same files with manifest.json at the zip root, which
                    is the shape the Chrome Web Store accepts
  <name>.sha256     the digest of the sources both were built from

tests/turnos-hsi-extension.test.mjs recomputes that digest, so a zip left
behind by a code change fails the suite. The zips are deterministic (sorted
entries, fixed timestamps): unchanged sources produce identical bytes.
"""
import hashlib
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSIONS = os.path.join(ROOT, "extensions")
DIST = os.path.join(EXTENSIONS, "dist")
# Development-only and store-listing content never ships inside the extension.
SKIP_DIRS = {"test", "store"}
SKIP_FILES = {".DS_Store", "README.md"}


def names():
    return sorted(d for d in os.listdir(EXTENSIONS)
                  if os.path.isfile(os.path.join(EXTENSIONS, d, "manifest.json")))


def shipped_files(src):
    out = []
    for base, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_FILES:
                continue
            full = os.path.join(base, name)
            out.append((os.path.relpath(full, src).replace(os.sep, "/"), full))
    return sorted(out)


def digest(files):
    h = hashlib.sha256()
    for rel, full in files:
        h.update(rel.encode("utf-8") + b"\0")
        with open(full, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def write_zip(path, files, prefix):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, full in files:
            info = zipfile.ZipInfo(prefix + rel, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, "rb") as fh:
                zf.writestr(info, fh.read())


def build(name):
    src = os.path.join(EXTENSIONS, name)
    files = shipped_files(src)
    if not any(rel == "manifest.json" for rel, _ in files):
        sys.exit("manifest.json not found under %s" % src)
    os.makedirs(DIST, exist_ok=True)
    folder = os.path.join(DIST, name + ".zip")
    write_zip(folder, files, name + "/")
    write_zip(os.path.join(DIST, name + "-store.zip"), files, "")
    with open(os.path.join(DIST, name + ".sha256"), "w", encoding="utf-8") as fh:
        fh.write(digest(files) + "\n")
    print("%s (%d files, %d bytes) + %s-store.zip"
          % (os.path.relpath(folder, ROOT), len(files), os.path.getsize(folder), name))


def main():
    wanted = sys.argv[1:] or names()
    for name in wanted:
        if name not in names():
            sys.exit("no extension named %r under extensions/" % name)
        build(name)


if __name__ == "__main__":
    main()
