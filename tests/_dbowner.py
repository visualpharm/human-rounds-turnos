"""tests/_dbowner.py — which `core/db*.py` module defines a storage function, for the tests that stub one BY NAME.

`core/db.py` is the connection and nothing else, so a test that replaces `hsi_outbox_settle` or `holds_release_booking_request`
replaces it on the module that owns it (every caller looks it up there at call time):

    owner = _dbowner.owner_of("hsi_outbox_settle")
    patch.object(owner, "hsi_outbox_settle", ...)
"""
import importlib

from app.core import db_values

# the package the persistence modules live in, wherever the layout keeps it
_PACKAGE = db_values.__package__
_MODULES = ("db_accounts", "db_ai", "db_analytics", "db_auth", "db_backup", "db_booking", "db_catalog", "db_chat",
            "db_clinical", "db_hsi", "db_operators", "db_reports", "db_slots", "db_schema", "db_locks", "db_wire",
            "db_values")


def owner_of(name):
    """The one module of the persistence layer that has `name` in its namespace (the modules never import a sibling's function
    by name, so a name that is there is defined there — also while a test has it replaced)."""
    for module_name in _MODULES:
        module = importlib.import_module("%s.%s" % (_PACKAGE, module_name))
        if name in vars(module):
            return module
    raise AttributeError("no core/db*.py module defines %r" % name)
