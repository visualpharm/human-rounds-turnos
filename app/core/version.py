"""version.py — which build is running: the git commit, or "unknown".

`/health` answers its short form (`short()`, the first 7 hex characters —
`core/health.py`) and Sentry tags each event with the full commit as the
`release`. Read from the environment only — no git, no file, no
subprocess: `/health` must never hang, and the image has no `.git`.

Where the commit comes from, first that is set wins:
  - `SOURCE_COMMIT` — the Dockerfile's build arg, baked into the image's ENV;
    Coolify passes it (and also injects it at runtime).
  - `COOLIFY_GIT_COMMIT_SHA` — the same, the name `ops/backup_archive.py`
    already reads.
  - `RAILWAY_GIT_COMMIT_SHA` — Railway injects it at runtime.
Anything that is not a 7–40 character hex commit (empty, a branch name, a
placeholder) is "unknown": a probe or a smoke check never sees garbage.
"""

import os
import re

UNKNOWN = "unknown"
SHORT_LEN = 7
_ENV_KEYS = ("SOURCE_COMMIT", "COOLIFY_GIT_COMMIT_SHA", "RAILWAY_GIT_COMMIT_SHA")
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")


def commit(environ=None):
    environ = os.environ if environ is None else environ
    for key in _ENV_KEYS:
        value = (environ.get(key) or "").strip().lower()
        if value:
            return value if _COMMIT_RE.match(value) else UNKNOWN
    return UNKNOWN


def short(environ=None):
    """The commit as `git log --oneline` abbreviates it (7 characters), or
    "unknown" — what `/health` shows (the product owner 2026-10-23: status and the short
    build sha, nothing else)."""
    full = commit(environ)
    return UNKNOWN if full == UNKNOWN else full[:SHORT_LEN]
