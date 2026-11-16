"""session_user.py - the signed-in patient of a request, or None: never raises.

`account_photo` and `calendar_feed` each carried the same read, and `booking_requests._get_user` has a
third. `cuentas.accounts` is imported INSIDE the function on purpose: these callers are loaded before the
accounts store and must not fail, or pull it in, at import time. Each caller keeps its own name for it
(`_user`, `_current_user`), which the suite monkeypatches; `tests/test_repeated_tails_contract.py` pins what
each answered.
"""


def current_or_none(request):
    try:
        from app.cuentas import accounts
    except Exception:  # noqa: BLE001
        return None
    try:
        return accounts.current_user(request)
    except Exception:  # noqa: BLE001
        return None
