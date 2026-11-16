"""paths.py — where the installation's files are on disk: the one place a path is derived from
this file's location.

    PROJECT_ROOT   the repository / image root (`/app` in the container): data/, templates/,
                   static/, locales/, packs/, alembic/, scripts/, extensions/ ...
    CODE_ROOT      the directory that holds the application packages (core/, modules/, ...)
    CODE_DIR       CODE_ROOT relative to PROJECT_ROOT ("." while the packages sit at the root)
    ROOT_WEB_ASSETS / ROOT_WEB_ASSETS_DIR
                   the files the site serves at the root of its URL space (`/admin-app.js`,
                   `/logo.png`; fingerprinted as `/static/<name>`) and the directory, relative to
                   PROJECT_ROOT, that holds them

Every module that needs one of these reads it from here instead of counting `dirname()` calls
up from its own `__file__`, so moving a module never moves the files it reads or writes
(tests/test_repo_paths.py pins that no other module derives the root from `__file__`).
"""

import os

CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(CODE_ROOT)
CODE_DIR = os.path.relpath(CODE_ROOT, PROJECT_ROOT)

ROOT_WEB_ASSETS = frozenset({
    "admin-app.js", "admin-direct.css", "admin-gate.css", "admin-shell.css", "admin-shell.js",
    "human-rounds.css", "tokens.css", "logo.png", "logo.svg", "pinamar-emblema.svg",
})
ROOT_WEB_ASSETS_DIR = "static/root"


def project_path(*parts):
    """A path under PROJECT_ROOT."""
    return os.path.join(PROJECT_ROOT, *parts)


def code_path(*parts):
    """A path under CODE_ROOT (a module's own files: `modules/turnos/confirm.mjs`)."""
    return os.path.join(CODE_ROOT, *parts)


def root_web_asset(name):
    """Disk path of one root web asset (`admin-app.js`)."""
    if name not in ROOT_WEB_ASSETS:
        raise ValueError("%r is not a root web asset" % (name,))
    return os.path.normpath(os.path.join(PROJECT_ROOT, ROOT_WEB_ASSETS_DIR, name))
