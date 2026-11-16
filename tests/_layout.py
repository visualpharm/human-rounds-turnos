"""tests/_layout.py — module names inside the application package, for the tests that read imports.

The source guards (which module may import which) speak of `core`, `admin_panel`,
`modules.turnos`: the application's own packages. In the code those are `app.core`, ... since the
application became one package (docs/LAYOUT.md). A guard compares `in_app(node.module)` with the
short name, so it states the rule once and keeps meaning it whatever the layout.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core import paths  # noqa: E402

# "app." — what every application module name starts with
APP_PREFIX = paths.__name__[: -len("core.paths")]


def in_app(dotted):
    """`app.core.db` -> `core.db`; a name outside the application package (or None) unchanged."""
    if dotted and APP_PREFIX and dotted.startswith(APP_PREFIX):
        return dotted[len(APP_PREFIX):]
    return dotted


def full(name):
    """`core.db` -> `app.core.db`: the importable name of a module of the application."""
    return APP_PREFIX + name
