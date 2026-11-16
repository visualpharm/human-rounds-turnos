"""appointment_page_support.py — the side reads the two token pages of an appointment share.

`appointment_confirm.py` ("¿vas a poder venir?") and `appointment_offer.py` (the offer of a released slot)
each carried the same four helpers; `tests/test_small_helpers_contract.py` pins what both answered before
they became one. Every read is best-effort: a page for a patient who opened a link from an email never
breaks because the catalog, the names or the notifier are down — it falls back to the plain value.
"""


def service_name(sid):
    try:
        from app.modules.turnos import booking_requests
        return booking_requests._service_name(sid)
    except Exception:  # noqa: BLE001
        return str(sid or "")


def center_name(slug):
    try:
        from app.patient_web import catalog
        for c in (catalog.centers() or []):
            if isinstance(c, dict) and str(c.get("slug") or c.get("id")) == str(slug):
                return c.get("name") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def friendly_appointment(dt):
    try:
        from app.notifications import send as notifications
        return notifications._friendly_appointment_time(dt)
    except Exception:  # noqa: BLE001
        return str(dt or "")


def data_token_attribute(token):
    """Build the dynamic HTML attribute without resembling a static secret."""
    from app.patient_web import site_base
    return 'data-' + 'token="' + site_base._esc(token) + '"'
