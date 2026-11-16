"""Optional coverage after an already-created, self-booked appointment.

No booking mutation, schema, lookup, message, or dependent coverage write.
The booking is the authorization boundary, never a patient ID from the body.
"""
from app.cuentas import accounts, health_coverage
from app.core import db_booking
from app.core import db_accounts


def _snapshot(coverage):
    return {"supported": True, "cobertura": coverage,
            "revision": health_coverage.revision(coverage),
            "sin_cobertura": bool(isinstance(coverage, dict)
                                  and coverage.get("declarada_sin_cobertura") is True)}


def respond(usuario, request_id, body=None):
    if not usuario:
        return 401, {"error": "auth_requerida"}
    dni = str(usuario.get("dni") or "")
    booking = db_booking.get_booking_request(request_id)
    if not booking or dni not in (booking.get("dni"), booking.get("solicitante_dni")):
        return 404, {"error": "no_encontrado"}
    # A guardian link is a declared relationship, not permission to overwrite
    # another person's account. There is no independent dependent coverage
    # store yet. Even a child who later registers cannot edit a guardian's
    # booking through this narrowly self-booked path.
    if booking.get("dni") != dni or booking.get("solicitante_dni") not in (None, "", dni):
        return (200 if body is None else 403), {"supported": False, "reason": "dependent"}
    user = db_accounts.get_user(dni)
    if not user or not user.get("activo", True):
        return 401, {"error": "auth_requerida"}
    if user.get("identity_kind") == "synthetic":
        return (200 if body is None else 403), {"supported": False, "reason": "identity"}
    current = user.get("cobertura")
    if body is None:
        return 200, _snapshot(current)
    if not isinstance(body, dict) or body.get("choice") not in ("coverage", "none"):
        return 400, {"error": "coverage_choice_required"}
    if not isinstance(body.get("revision"), str):
        return 400, {"error": "coverage_revision_required"}
    if body["choice"] == "none":
        coverage = {"declarada_sin_cobertura": True}
    else:
        if not isinstance(body.get("cobertura"), dict):
            return 400, {"error": "coverage_required"}
        coverage = accounts._norm_coverage(body["cobertura"], dni=dni, existente=current)
        if not health_coverage.primary(coverage):
            return 400, {"error": "coverage_required"}
    # Identical retry after a lost response is safe, including an old revision.
    if coverage == current:
        return 200, {"ok": True, **_snapshot(current)}
    if body["revision"] != health_coverage.revision(current):
        return 409, {"error": "coverage_changed", **_snapshot(current)}
    if not db_accounts.update_self_booking_coverage(request_id, dni, current, coverage):
        return 409, {"error": "coverage_changed"}
    return 200, {"ok": True, **_snapshot(coverage)}
