"""claim_gate.py — what an account the desk has not confirmed may read (TASK-68, security review 2026-11).

A sign-up by password and DNI photo proves nothing about the number, so the account holding a number
nobody checked (an open `dni_claims` row, status `unverified` or `pending`) may still book, but every read of
clinical data keyed by its number answers as if there were none: its own appointment list and detail, the
calendar feed, the lab results (page, signed link, assistant tool). `hidden(dni)` is the one question every
such read asks; with `TURNOS_DNI_CLAIMS=0` (the demo, a portal installation) nothing is ever hidden.
"""

from app.core import config
from app.cuentas import accounts_store as store


def enabled():
    return str(config.get("TURNOS_DNI_CLAIMS", "1")).strip().lower() not in ("0", "false", "off", "no")


def hidden(dni):
    """True while the account with this key has an open claim the desk has not resolved."""
    if not dni or not enabled():
        return False
    return store.open_claim_of(str(dni)) is not None
