"""Resolución automática de conflictos de sincronización con HSI.

El personal del hospital no puede "mergear duplicados" ni leer trazas de
sincronización: este módulo actúa como el departamento de IT.  Cada pasada
agrupa los conflictos abiertos de ``hsi_sync_conflicts`` por causa raíz y
aplica la estrategia del grupo; sólo lo que genuinamente necesita una
decisión humana queda abierto — con una propuesta concreta — y es lo único
que muestra la sección «HSI» del admin.

Estrategias, en orden:

- **Obsoletos.**  La solicitud ya está en HSI, se rechazó/canceló o su turno
  pasó: el conflicto se cierra solo.  Un conflicto repetido (misma solicitud,
  misma causa) también — queda uno.
- **Duplicados de ``hsi_appointment_id``.**  El HSI de dev se re-crea desde
  cero (``hsi-up.sh`` hace ``docker rm -f``) y sus ids arrancan en 1 de
  nuevo, así que solicitudes viejas quedan apuntando al appointment de OTRO
  paciente.  Se conserva el vínculo cuya fecha/DNI coincide con lo observado
  en HSI y se limpia el resto; los turnos futuros re-encolados obtienen su
  propio appointment.
- **Sin agenda del profesional.**  Se reintenta tal cual (la agenda puede
  existir ahora) y si sigue faltando se busca OTRO profesional de la misma
  especialidad con el horario prometido libre: la hora del paciente no
  cambia, cambia el profesional, y queda registrado en la resolución.
- **Agenda llena.**  Otro profesional a la misma hora; si no hay, sobreturno
  con el profesional original (la práctica estándar del mostrador).
- **Cambios simultáneos / drift.**  Cuando la edición local ya terminó se
  deja que la próxima reconciliación importe la verdad de HSI; un turno que
  HSI ya no tiene se vuelve a crear si sigue vigente.
- **Sin agenda de la especialidad / HSI canceló el turno.**  No se resuelven
  acá: crear una agenda clínica o revertir una decisión clínica no es de IT.
  Quedan abiertos con la propuesta para el personal.

``resolver_pass`` corre en cada tick de ``hsi_sync`` y a demanda desde
``POST /api/admin/hsi-sync/resolver``; ``estado_panel`` arma el payload de la
sección (porcentaje sincronizado + clusters abiertos + resoluciones
recientes).  Con ``TURNOS_SKIP_HSI=1`` sólo corren las estrategias que no
hablan con HSI (obsoletos, repetidos, duplicados, carreras).

Movido acá desde hsi_resolver.py en la raíz del repo en la extracción a
connectors (fase 6b, 2026-07-30), parte del paquete `emr_sync` — ver
connectors/hsi/connector.json. Movida bit a bit (sin rutas relativas a
__file__). `hsi_resolver.py` en la raíz queda como shim de compatibilidad
sobre este módulo.
"""

import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

from . import normalization
from app.core import log_pii

_ART = timezone(timedelta(hours=-3))


def _log(message):
    print(f"[hsi_resolver] {message}", file=sys.stderr, flush=True)


def _activo():
    if os.environ.get("TURNOS_SKIP_HSI") == "1":
        return False
    # The generic bridge v1 deliberately does not expose HSI's undocumented
    # "find another diary" mutation.  Keep local conflict hygiene, but never
    # let this legacy resolver reach a vendor wire client behind a bridge.
    try:
        from app import connectors
        adapter = connectors.adapter_for(connectors.EMR_SYNC)
        return bool(adapter and adapter.supports_auto_resolution())
    except Exception:  # noqa: BLE001 — resolver must fail closed on config trouble
        return False


def clasificar(reason):
    """Causa raíz de un conflicto a partir de su ``reason`` textual."""
    r = (reason or "").lower()
    if "duplicate_local_hsi_appointment_id" in r:
        return "duplicado"
    if "no hsi diary matches professional" in r:
        return "profesional"
    if "no diary matches" in r or "no diaries exist" in r:
        return "especialidad"
    if "no exact free slot" in r or "no free slot" in r:
        return "sin_hueco"
    if "remote_appointment_cancelled_or_blocked" in r:
        return "remoto_cancelado"
    if r.startswith("concurrent_local_and_hsi"):
        return "carrera"
    if r in ("ambiguous_local_requests_for_hsi_appointment", "hsi_link_while_local_intent_unsettled"):
        return "vinculo"
    if any(m in r for m in ("hsi_checkpoint_drift", "hsi_appointment_missing",
                            "missing_hsi_checkpoint", "hsi_state_change_not_confirmed",
                            "hsi_reassign_not_confirmed", "target_slot_not_in_hsi_diary")):
        return "drift"
    return "otro"


TEXTOS = {
    "duplicado": {
        "titulo": "Dos solicitudes apuntan al mismo turno de HSI",
        "propuesta": "El sistema conserva el vínculo correcto y re-agenda el resto solo.",
    },
    "profesional": {
        "titulo": "Sin agenda del profesional en HSI",
        "propuesta": "Cargá en HSI la agenda de ese profesional, o cambiá el "
                     "profesional del turno desde la cola de solicitudes.",
    },
    "especialidad": {
        "titulo": "Sin agenda de la especialidad en HSI",
        "propuesta": "Hay que crear la agenda en HSI (Gestión de agendas); "
                     "después el sistema reintenta solo.",
    },
    "sin_hueco": {
        "titulo": "Agenda llena en HSI",
        "propuesta": "Reprogramá el turno desde la cola o ampliá el horario "
                     "de la agenda en HSI.",
    },
    "remoto_cancelado": {
        "titulo": "HSI canceló o bloqueó el turno",
        "propuesta": "Revisá la solicitud y avisale al paciente antes de re-agendar.",
    },
    "carrera": {
        "titulo": "Cambios simultáneos",
        "propuesta": "Se reconcilia solo en la próxima sincronización; si "
                     "persiste, revisá la solicitud.",
    },
    "drift": {
        "titulo": "HSI cambió el turno por su cuenta",
        "propuesta": "El sistema reintenta con el estado actual de HSI; si "
                     "persiste, revisá la solicitud.",
    },
    "vinculo": {
        "titulo": "Turno cargado a mano en HSI sin vínculo seguro",
        "propuesta": "Si sobra una solicitud, rechazala; si sobra un turno en HSI, cancelalo allí. "
                     "Cuando queda uno solo, el sistema lo vincula y cierra el aviso.",
    },
    "otro": {
        "titulo": "Otro conflicto",
        "propuesta": "Revisá el detalle de la solicitud.",
    },
}


def _entre_comillas(reason):
    m = re.search(r"'([^']+)'", reason or "")
    return m.group(1) if m else None


def detalle_es(conflicto):
    """Una oración en castellano para el personal, sin jerga del journal."""
    reason = str(conflicto.get("reason") or "")
    cluster = clasificar(reason)
    nombre = _entre_comillas(reason)
    if cluster == "duplicado":
        return "El vínculo con HSI quedó apuntando al turno de otro paciente."
    if cluster == "profesional":
        return "HSI no tiene agenda de %s." % nombre if nombre else \
               "HSI no tiene agenda del profesional asignado."
    if cluster == "especialidad":
        return "No hay ninguna agenda de %s cargada en HSI." % nombre if nombre else \
               "No hay una agenda de esa especialidad cargada en HSI."
    if cluster == "sin_hueco":
        return "La agenda de HSI no tiene libre el horario del turno."
    if cluster == "remoto_cancelado":
        return "HSI canceló o bloqueó este turno."
    if cluster == "carrera":
        return "HSI y Sana cambiaron este turno al mismo tiempo."
    if cluster == "drift":
        return "HSI muestra este turno distinto de lo que Sana tiene registrado."
    if cluster == "vinculo":
        if reason.lower().startswith("ambiguous"):
            return ("En HSI hay un turno de este paciente con el mismo profesional y horario que "
                    "más de una solicitud aprobada.")
        return ("Este turno se cargó a mano en HSI mientras el envío automático seguía pendiente: "
                "puede quedar dos veces en HSI.")
    return reason[:160]


# ---------------------------------------------------------------------------
# helpers


def _sid(conflicto):
    try:
        return int(conflicto.get("entity_id"))
    except (TypeError, ValueError):
        return None


def _turno_futuro(row, now):
    t = (row or {}).get("turno_dt")
    if t is None:
        return False
    if isinstance(t, str):
        try:
            t = datetime.fromisoformat(t)
        except ValueError:
            return False
    aware = t if t.tzinfo else t.replace(tzinfo=_ART)
    return aware >= now


def _art_iso(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    aware = dt if dt.tzinfo else dt.replace(tzinfo=_ART)
    return aware.astimezone(_ART).replace(microsecond=0).isoformat()


def _iso_or_none(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return value


def _especialidad_de(row):
    try:
        from app.modules.turnos import booking_requests
        return booking_requests._service_name(row.get("servicio_id")) or row.get("servicio_id") or ""
    except Exception:  # noqa: BLE001
        return row.get("servicio_id") or ""


_HONORIFICOS = {"dr", "dra", "lic", "od", "bioq", "tec", "klgo", "klga"}


def _tokens_nombre(nombre):
    try:
        from app.connectors.hsi import hsi_client
        norm = hsi_client._norm(nombre)
    except Exception:  # noqa: BLE001
        norm = (nombre or "").lower()
    return set(re.findall(r"[a-z0-9]+", norm)) - _HONORIFICOS


def _mismo_profesional(a, b):
    ta, tb = _tokens_nombre(a), _tokens_nombre(b)
    return bool(ta) and bool(tb) and (ta.issubset(tb) or tb.issubset(ta))


def _titulo_nombre(nombre):
    return " ".join(p.capitalize() for p in str(nombre or "").split())


def _asignar_profesional(sid, nombre, sobreturno=None, medico_id=None):
    """Guarda el profesional con el que HSI agendó el turno. Nombre e id van
    juntos (2026-10-23): el nombre de HSI se resuelve contra el catálogo — si
    es UN médico, su id y su nombre del catálogo (así «Mis pacientes» y el
    índice médico+horario lo siguen viendo); si no, el nombre tal cual con id
    NULL. `medico_id` = el mismo médico que ya tenía la fila (sobreturno)."""
    from app.core import db_hsi
    try:
        from app.modules.turnos import doctor_appointments
        nombre, medico_id = doctor_appointments.resolve_pair(nombre, medico_id)
    except Exception as exc:  # noqa: BLE001 — sin catálogo: nombre de HSI, sin id
        _log(f"resolve_pair {sid}: {log_pii.exc(exc)}")
        medico_id = None
    return db_hsi.assign_doctor_hsi(sid, nombre, sobreturno=sobreturno, medico_id=medico_id)


def _cerrar(conflicto, accion, texto, extra=None, resuelto_por="resolver"):
    from app.core import db_hsi
    resolucion = {"accion": accion, "texto": texto}
    if extra:
        resolucion.update(extra)
    return db_hsi.hsi_resolve_conflict(conflicto["id"], resolucion, resuelto_por)


# ---------------------------------------------------------------------------
# estrategias


def _obsoleto(conflicto, cluster, row, now):
    """Cierra conflictos que ya no describen un problema real."""
    from app.core import db_hsi
    if _sid(conflicto) is not None and row is None:
        _cerrar(conflicto, "obsoleto", "La solicitud ya no existe.")
        return True
    ruteo = cluster in ("profesional", "especialidad", "sin_hueco")
    if row is not None:
        if row.get("hsi_estado") == "ok" and (ruteo or cluster == "drift"):
            _cerrar(conflicto, "obsoleto", "El turno ya está agendado en HSI.")
            return True
        if ruteo and row.get("estado") in ("rechazada", "cancelada"):
            _cerrar(conflicto, "obsoleto",
                    "La solicitud se %s: ya no hay turno que agendar."
                    % ("rechazó" if row.get("estado") == "rechazada" else "canceló"))
            return True
        if ruteo and not _turno_futuro(row, now):
            _cerrar(conflicto, "obsoleto",
                    "El turno ya pasó; HSI no agenda turnos pasados.")
            return True
        # A hand-loaded HSI turno the journal could not link safely. Two candidates: settled once
        # this one is linked (the journal retries every tick, so rejecting the extra one links the
        # other) or no longer approved. A link refused while the server's own send was in flight:
        # settled once the request links that very HSI turno or is no longer approved — linked to
        # another one, HSI holds the turno twice and the conflict stays for the desk.
        reason = str(conflicto.get("reason") or "")
        hand_loaded = str((conflicto.get("remote_payload") or {}).get("id") or "")
        if ((reason == "ambiguous_local_requests_for_hsi_appointment"
             and (row.get("hsi_appointment_id") or row.get("estado") != "aprobada"))
                or (reason == "hsi_link_while_local_intent_unsettled"
                    and (row.get("estado") != "aprobada"
                         or (hand_loaded and str(row.get("hsi_appointment_id") or "") == hand_loaded)))):
            _cerrar(conflicto, "obsoleto", "La solicitud ya está vinculada o dejó de estar aprobada.")
            return True
        if cluster == "carrera":
            try:
                inflight = db_hsi.hsi_outbox_inflight("appointment", row["id"])
            except Exception:  # noqa: BLE001
                inflight = True
            if not inflight:
                _cerrar(conflicto, "obsoleto",
                        "La edición local terminó; la próxima sincronización "
                        "reconcilia este turno con HSI.")
                return True
    return False


def _elegir_dueno(rows, remoto):
    """De las solicitudes que comparten un appointment, la dueña legítima.

    Coincidir en fecha+hora con lo observado en HSI vale más que coincidir en
    DNI (el checkpoint de un push propio no trae DNI); a igual puntaje gana la
    más nueva — la que se creó contra la instancia actual de HSI."""
    remoto = remoto or {}
    persona = remoto.get("patient") or {}
    dni_remoto = str(persona.get("document_number") or "")
    try:
        observed = datetime.fromisoformat(
            str(remoto.get("scheduled_at") or "").replace("Z", "+00:00")
        ).astimezone(_ART)
    except ValueError:
        observed = None

    def puntos(r):
        p = 0
        t = r.get("turno_dt")
        if t is not None and observed:
            if isinstance(t, str):
                try:
                    t = datetime.fromisoformat(t)
                except ValueError:
                    t = None
            if t is not None:
                loc = (t if t.tzinfo else t.replace(tzinfo=_ART)).astimezone(_ART)
                if loc.replace(second=0, microsecond=0) == observed.replace(second=0, microsecond=0):
                    p += 2
        if dni_remoto and str(r.get("dni") or "") == dni_remoto:
            p += 1
        return p

    return max(rows, key=lambda r: (puntos(r), r.get("id") or 0))


def _resolver_duplicados(items, now, resumen):
    """Re-vincula los grupos de solicitudes que comparten un appointment."""
    from app.core import db_booking
    from app.core import db_hsi
    por_appt = {}
    for conflicto, row in items:
        appt = (row or {}).get("hsi_appointment_id") \
            or ((conflicto.get("remote_payload") or {}).get("id"))
        if not appt:
            _cerrar(conflicto, "obsoleto", "El vínculo duplicado ya se limpió.")
            resumen["resueltos"] += 1
            continue
        por_appt.setdefault(str(appt), []).append(conflicto)

    for appt_id, conflictos in por_appt.items():
        rows = db_hsi.hsi_booking_requests_by_appointment(appt_id)
        if len(rows) > 1:
            remoto = db_hsi.hsi_checkpoint_get("appointment", appt_id) \
                or (conflictos[0].get("remote_payload") or {})
            dueno = _elegir_dueno(rows, remoto)
            for r in rows:
                if r["id"] == dueno["id"]:
                    continue
                if r.get("estado") == "aprobada" and _turno_futuro(r, now):
                    # Vuelve a la cola durable y consigue SU propio turno HSI.
                    db_hsi.clear_hsi_link(r["id"], "pendiente")
                    try:
                        from app.modules.turnos import hsi_journal
                        hsi_journal.enqueue_appointment(db_booking.get_booking_request(r["id"]) or r)
                    except Exception as exc:  # noqa: BLE001 — backfill_pending lo adopta igual
                        _log(f"enqueue tras limpiar {r['id']}: {log_pii.exc(exc)}")
                else:
                    # Histórico: su push fue real contra la instancia anterior
                    # de HSI; sólo pierde el vínculo que hoy es de otro.
                    db_hsi.clear_hsi_link(r["id"], r.get("hsi_estado") or "skip")
                resumen["revinculados"] += 1
        texto = ("El turno %s de HSI quedó vinculado a una sola solicitud; "
                 "el resto se re-agenda solo." % appt_id)
        for conflicto in conflictos:
            _cerrar(conflicto, "revinculado", texto)
            resumen["resueltos"] += 1


def _persistir_ok(sid, result, patient_id):
    """La misma persistencia que hsi_sync._replay_one tras un push exitoso."""
    from app.core import db_booking
    from app.core import db_hsi
    result = normalization.result_from_hsi(result)
    result["estado"] = "ok"
    for key in ("patient_id", "appointment_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    result.setdefault("patient_id", str(patient_id))
    result.setdefault("checkpoint", {
        "appointment_id": result.get("appointment_id"), "schedule_id": result.get("schedule_id"),
        "patient_id": str(patient_id), "scheduled_at": result.get("actual_turno_dt")
        or result.get("requested_turno_dt"), "state": "scheduled", "version": None,
    })
    db_booking.reassign_appointment(sid, result.get("actual_turno_dt") or result.get("requested_turno_dt"),
                       result, actor="hsi:resolver")
    checkpoint = result.get("checkpoint")
    if checkpoint and result.get("appointment_id"):
        db_hsi.hsi_checkpoint_set("appointment", result["appointment_id"], checkpoint)
        db_hsi.hsi_mirror_appointment(result["appointment_id"], checkpoint.get("schedule_id"),
                                  checkpoint.get("patient_id"), checkpoint)
    # Los intentos viejos del journal quedan confirmados: ya no hay nada que
    # reintentar para esta solicitud (cancel/attendance no se tocan).
    try:
        for intent in db_hsi.hsi_outbox_by_entity(str(sid), ("conflict", "error", "pending")):
            if intent.get("operation") in ("create", "reassign"):
                db_hsi.hsi_outbox_confirm(intent["id"], result)
    except Exception:  # noqa: BLE001
        pass


def _otro_profesional(client, especialidad, excepto, intentar):
    """El primer profesional de la especialidad con el horario prometido libre."""
    vistos = []
    for diario in client.diaries_for_specialty(especialidad):
        prof = diario.get("professional") or ""
        if not prof or any(_mismo_profesional(prof, v) for v in vistos):
            continue
        vistos.append(prof)
        if excepto and _mismo_profesional(prof, excepto):
            continue
        result = intentar(professional_hint=prof, exact=True)
        if result:
            return result, _titulo_nombre(prof)
        if len(vistos) >= 6:   # una especialidad municipal no tiene más staff útil
            break
    return None, None


def _resolver_ruteo(conflicto, cluster, row, resumen, client):
    """Sin agenda del profesional / agenda llena: reintento con escalación."""
    from app.connectors.hsi import hsi_client
    especialidad = _especialidad_de(row)
    medico = row.get("medico_asignado")
    when = _art_iso(row["turno_dt"])
    try:
        patient_id = client.ensure_patient(
            str(row.get("dni") or ""), row.get("nombre") or "", row.get("apellido") or "",
            _iso_or_none(row.get("nacimiento")), row.get("sexo") or "X")
    except Exception as exc:  # noqa: BLE001 — HSI caído: el conflicto queda abierto
        _log(f"ensure_patient {row.get('id')}: {log_pii.exc(exc)}")
        return False

    def intentar(**kwargs):
        try:
            return client.create_appointment(patient_id, especialidad, when, **kwargs)
        except hsi_client.HsiError:
            return None
        except Exception as exc:  # noqa: BLE001
            _log(f"create_appointment {row.get('id')}: {log_pii.exc(exc)}")
            return None

    result, nota = None, None
    if cluster == "especialidad":
        # La agenda pudo haberse creado después del fallo (es exactamente lo
        # que la propuesta le pide al personal): un reintento a la hora
        # prometida lo verifica sin molestar a nadie.
        result = intentar(professional_hint=medico or None, exact=True,
                          overturn=bool(row.get("sobreturno")))
        if result:
            nota = ("accion", "reintentado",
                    "Ya existe la agenda de %s en HSI: el turno se agendó."
                    % _especialidad_de(row))
        elif medico:
            # El médico asignado puede ser de OTRA especialidad (una Ecografía
            # asignada a una cardióloga): probar con quien sí tiene la agenda.
            result, prof = _otro_profesional(client, especialidad, medico, intentar)
            if result:
                _asignar_profesional(row["id"], prof)
                resumen["reasignados"] += 1
                nota = ("accion", "reasignado",
                        "%s no atiende %s en HSI: el turno quedó a la misma "
                        "hora con %s." % (_titulo_nombre(medico),
                                          _especialidad_de(row), prof))
    elif cluster == "profesional":
        if medico:
            # La agenda puede existir ahora (se cargó después del fallo).
            result = intentar(professional_hint=medico, exact=True,
                              overturn=bool(row.get("sobreturno")))
            if result:
                nota = ("accion", "reintentado",
                        "Ahora existe la agenda de %s en HSI: el turno se agendó."
                        % _titulo_nombre(medico))
        if not result:
            result, prof = _otro_profesional(client, especialidad, medico, intentar)
            if result:
                _asignar_profesional(row["id"], prof)
                resumen["reasignados"] += 1
                nota = ("accion", "reasignado",
                        "HSI no tiene agenda de %s: el turno quedó a la misma "
                        "hora con %s." % (_titulo_nombre(medico) or "ese profesional", prof))
    elif cluster == "sin_hueco":
        if not medico:
            # Sin profesional asignado el push ya recorrió toda la agenda de la
            # especialidad: no hay hueco que un reintento automático encuentre.
            return False
        result, prof = _otro_profesional(client, especialidad, medico, intentar)
        if result:
            _asignar_profesional(row["id"], prof)
            resumen["reasignados"] += 1
            nota = ("accion", "reasignado",
                    "La agenda de %s estaba llena: el turno quedó a la misma "
                    "hora con %s." % (_titulo_nombre(medico), prof))
        else:
            result = intentar(professional_hint=medico, exact=True, overturn=True)
            if result:
                _asignar_profesional(row["id"], _titulo_nombre(medico), sobreturno=True,
                                     medico_id=row.get("medico_asignado_id"))
                resumen["sobreturnos"] += 1
                nota = ("accion", "sobreturno",
                        "La agenda estaba llena: quedó como sobreturno de %s "
                        "a la hora prometida." % _titulo_nombre(medico))
    if not result or not nota:
        return False
    _persistir_ok(row["id"], result, patient_id)
    _cerrar(conflicto, nota[1], nota[2])
    resumen["resueltos"] += 1
    resumen["reintentos_ok"] += 1
    return True


def _resolver_drift(conflicto, row, now, resumen):
    """HSI cambió/perdió el turno: re-plantea la intención con datos frescos."""
    from app.core import db_booking
    from app.core import db_hsi
    reason = str(conflicto.get("reason") or "")
    if "hsi_appointment_missing" in reason and row.get("estado") == "cancelada":
        _cerrar(conflicto, "obsoleto",
                "HSI ya no tiene el turno: la cancelación quedó efectiva.")
        resumen["resueltos"] += 1
        return True
    if row.get("estado") != "aprobada" or not _turno_futuro(row, now):
        return False
    try:
        from app.modules.turnos import hsi_journal
        if "hsi_appointment_missing" in reason:
            db_hsi.clear_hsi_link(row["id"], "pendiente")
            hsi_journal.enqueue_appointment(db_booking.get_booking_request(row["id"]) or row)
            _cerrar(conflicto, "revinculado",
                    "HSI ya no tenía este turno: se vuelve a agendar solo.")
            resumen["resueltos"] += 1
            return True
        # El payload viejo llevaba un checkpoint vencido; una intención nueva
        # lo lee de nuevo y el replay decide contra el HSI actual.
        intent = hsi_journal.enqueue_reassign(row, row.get("turno_dt"))
        if intent and db_hsi.hsi_outbox_reset(intent["id"]) and _activo():
            outcome = hsi_journal.replay_item(intent["id"])
            if outcome and outcome.get("state") == "confirmed":
                _cerrar(conflicto, "reintentado",
                        "Se re-agendó con el estado actual de HSI.")
                resumen["resueltos"] += 1
                resumen["reintentos_ok"] += 1
                return True
    except Exception as exc:  # noqa: BLE001
        _log(f"drift {row.get('id')}: {log_pii.exc(exc)}")
    return False


# ---------------------------------------------------------------------------
# pasada completa + panel


def resolver_pass(now=None, client_factory=None):
    """Una pasada de resolución sobre todos los conflictos abiertos."""
    from app.core import db_booking
    from app.core import db_hsi
    now = now or datetime.now(timezone.utc)
    resumen = {"resueltos": 0, "revinculados": 0, "reintentos_ok": 0,
               "reasignados": 0, "sobreturnos": 0, "abiertos": 0}
    try:
        abiertos = db_hsi.hsi_open_conflicts()
    except Exception as exc:  # noqa: BLE001 — sin DB no hay journal que resolver
        _log(f"sin journal: {log_pii.exc(exc)}")
        return resumen

    vivos, vistos = [], {}
    for conflicto in abiertos:   # ordenados del más nuevo al más viejo
        sid = _sid(conflicto)
        row = db_booking.get_booking_request(sid) if sid is not None else None
        cluster = clasificar(conflicto.get("reason"))
        if _obsoleto(conflicto, cluster, row, now):
            resumen["resueltos"] += 1
            continue
        # Los tres clusters de ruteo son etapas del MISMO push fallido: sólo
        # el conflicto más nuevo de la solicitud describe el estado actual
        # (una solicitud no puede estar a la vez "sin agenda" y "sin hueco").
        if cluster in ("profesional", "especialidad", "sin_hueco"):
            clave = ("ruteo", conflicto.get("entity_id"))
        else:
            clave = (conflicto.get("entity_id"), conflicto.get("reason"))
        if clave in vistos:
            _cerrar(conflicto, "repetido",
                    "Reemplazado por el conflicto #%s, más nuevo." % vistos[clave])
            resumen["resueltos"] += 1
            continue
        vistos[clave] = conflicto["id"]
        vivos.append((conflicto, cluster, row))

    _resolver_duplicados([(c, r) for c, cl, r in vivos if cl == "duplicado"],
                         now, resumen)

    client = None
    if _activo() and any(cl in ("profesional", "sin_hueco", "especialidad")
                         for _, cl, _ in vivos):
        try:
            if client_factory is not None:
                client = client_factory()
            else:
                from app.connectors.hsi import hsi_client
                client = hsi_client.HsiClient()
                client.login()
        except Exception as exc:  # noqa: BLE001 — HSI caído: quedan abiertos
            _log(f"sin HSI: {log_pii.exc(exc)}")
            client = None

    for conflicto, cluster, row in vivos:
        if cluster == "duplicado":
            continue
        if cluster in ("profesional", "sin_hueco", "especialidad") and client is not None and row:
            if _resolver_ruteo(conflicto, cluster, row, resumen, client):
                continue
        if cluster == "drift" and row:
            if _resolver_drift(conflicto, row, now, resumen):
                continue
        resumen["abiertos"] += 1

    if _activo() and resumen["revinculados"]:
        try:
            from app.modules.turnos import hsi_journal
            hsi_journal.replay(limit=10)   # los re-encolados salen ya, no en 5 min
        except Exception as exc:  # noqa: BLE001
            _log(f"replay tras revincular: {log_pii.exc(exc)}")
    return resumen


# ---------------------------------------------------------------------------
# resoluciones a un click (los botones de la sección «HSI»)

MOTIVO_RECHAZO_HSI = ("No pudimos confirmar el turno en el horario elegido. "
                      "Pedí el turno de nuevo con otro horario y lo confirmamos "
                      "enseguida.")


def _turno_amigable(dt):
    try:
        from app.notifications import send as notifications
        return notifications._friendly_appointment_time(dt)
    except Exception:  # noqa: BLE001
        return str(dt)


def aplicar_resolucion(accion, conflicto_ids, resuelto_por="operador",
                       client_factory=None, now=None):
    """Aplica la resolución elegida por el operador a los conflictos marcados.

    - ``mover``: agenda en HSI el primer horario libre desde el pedido (con
      cualquier profesional de la especialidad), actualiza el turno local y le
      avisa al paciente por email.
    - ``sobreturno``: mantiene la hora prometida como sobreturno (primero con
      el profesional asignado, después con cualquiera que atienda a esa hora).
    - ``rechazar``: rechaza la solicitud con el motivo estándar, libera el
      slot y le avisa al paciente.

    Devuelve un resultado por conflicto — lo que no se pudo, dice por qué.
    """
    from app.core import db_booking
    from app.core import db_hsi
    from app.core import db_slots
    now = now or datetime.now(timezone.utc)
    resultados = []
    client = None
    if accion in ("mover", "sobreturno"):
        if not _activo():
            return {"ok": False, "error": "hsi_desactivado", "resultados": []}
        try:
            if client_factory is not None:
                client = client_factory()
            else:
                from app.connectors.hsi import hsi_client
                client = hsi_client.HsiClient()
                client.login()
        except Exception as exc:  # noqa: BLE001
            _log(f"sin HSI: {log_pii.exc(exc)}")
            return {"ok": False, "error": "hsi_no_disponible", "resultados": []}
    elif accion != "rechazar":
        return {"ok": False, "error": "accion_desconocida", "resultados": []}

    abiertos = {c["id"]: c for c in db_hsi.hsi_open_conflicts()}
    for cid in conflicto_ids:
        conflicto = abiertos.get(cid)
        if not conflicto:
            resultados.append({"conflicto_id": cid, "ok": True,
                               "detalle": "Ya estaba resuelto."})
            continue
        sid = _sid(conflicto)
        row = db_booking.get_booking_request(sid) if sid is not None else None
        if not row or row.get("estado") != "aprobada":
            resultados.append({"conflicto_id": cid, "solicitud_id": sid, "ok": False,
                               "detalle": "La solicitud ya no está aprobada."})
            continue
        if accion == "rechazar":
            db_booking.reject(sid, MOTIVO_RECHAZO_HSI, resuelta_por=resuelto_por, desde=("aprobada",))
            try:
                db_slots.holds_release_booking_request(sid)
            except Exception:  # noqa: BLE001
                pass
            try:
                from app.notifications import send as notifications
                notifications.notify_rejected(db_booking.get_booking_request(sid) or row)
            except Exception:  # noqa: BLE001
                pass
            _cerrar(conflicto, "rechazado",
                    "La solicitud se rechazó y se le avisó al paciente.",
                    resuelto_por=resuelto_por)
            resultados.append({"conflicto_id": cid, "solicitud_id": sid, "ok": True,
                               "detalle": "Rechazada; se le avisó al paciente."})
            continue
        resultados.append(_mover_o_sobreturno(client, accion, conflicto, row,
                                              resuelto_por, now))
    return {"ok": True, "resultados": resultados}


def _mover_o_sobreturno(client, accion, conflicto, row, resuelto_por, now):
    from app.core import db_booking
    from app.connectors.hsi import hsi_client
    sid, cid = row["id"], conflicto["id"]
    especialidad = _especialidad_de(row)
    medico = row.get("medico_asignado")
    turno = row.get("turno_dt")
    if isinstance(turno, str):
        turno = datetime.fromisoformat(turno)
    base = turno if turno.tzinfo else turno.replace(tzinfo=_ART)
    if base < now:
        base = now      # un horario ya pasado se busca desde ahora
    when = _art_iso(base)
    try:
        patient_id = client.ensure_patient(
            str(row.get("dni") or ""), row.get("nombre") or "", row.get("apellido") or "",
            _iso_or_none(row.get("nacimiento")), row.get("sexo") or "X")
    except Exception as exc:  # noqa: BLE001
        _log(f"ensure_patient {sid}: {log_pii.exc(exc)}")
        return {"conflicto_id": cid, "solicitud_id": sid, "ok": False,
                "detalle": "HSI no respondió."}

    def intentar(**kwargs):
        try:
            return client.create_appointment(patient_id, especialidad, when, **kwargs)
        except hsi_client.HsiError as exc:
            intentar.motivo = str(exc)
            return None
        except Exception as exc:  # noqa: BLE001
            intentar.motivo = str(exc)
            return None
    intentar.motivo = ""

    result, prof_final, es_sobreturno = None, None, False
    if accion == "mover":
        # No-exacto: el primer hueco libre >= lo pedido, en la agenda que
        # corresponda a la especialidad (con el médico asignado si tiene).
        result = medico and intentar(professional_hint=medico, exact=False)
        if not result:
            result = intentar(exact=False)
    else:   # sobreturno a la hora prometida
        es_sobreturno = True
        result = medico and intentar(professional_hint=medico, exact=True, overturn=True)
        if not result:
            result, prof_final = _otro_profesional(
                client, especialidad, medico,
                lambda **kw: intentar(overturn=True, **{**kw}))
    if not result:
        motivo = motivo_conflicto_es(intentar.motivo)
        return {"conflicto_id": cid, "solicitud_id": sid, "ok": False, "detalle": motivo}

    _persistir_ok(sid, result, patient_id)
    nuevo_prof = prof_final or _titulo_nombre(result.get("professional") or "")
    if medico and nuevo_prof and not _mismo_profesional(medico, nuevo_prof):
        _asignar_profesional(sid, nuevo_prof, sobreturno=es_sobreturno or None)
    elif es_sobreturno:
        _asignar_profesional(sid, medico or nuevo_prof, sobreturno=True,
                             medico_id=row.get("medico_asignado_id") if medico else None)
    actualizado = db_booking.get_booking_request(sid) or row
    try:
        from app.notifications import send as notifications
        notifications.notify_approved(actualizado)
    except Exception:  # noqa: BLE001
        pass
    cuando = _turno_amigable(actualizado.get("turno_dt"))
    if accion == "mover":
        texto = "Se movió al primer horario libre (%s) y se le avisó al paciente." % cuando
    else:
        texto = "Quedó como sobreturno (%s) y se le avisó al paciente." % cuando
    _cerrar(conflicto, accion == "mover" and "movido" or "sobreturno", texto,
            resuelto_por=resuelto_por)
    return {"conflicto_id": cid, "solicitud_id": sid, "ok": True, "detalle": texto}


def motivo_conflicto_es(motivo):
    low = (motivo or "").lower()
    if "no diary matches" in low or "no diaries exist" in low:
        return "HSI no tiene una agenda de esa especialidad."
    if "no hsi diary matches professional" in low:
        return "HSI no tiene agenda de ese profesional."
    if "no exact free slot" in low or "no free slot" in low:
        return "No hay hueco libre en la agenda de HSI."
    return "HSI no aceptó el turno." if motivo else "HSI no aceptó el turno."


RESOL_LABEL = {
    "mover": "Mover al primer horario libre",
    "sobreturno": "Agendar como sobreturno",
    "rechazar": "Rechazar la solicitud",
}

# Resoluciones aplicables por cluster, la PRIMERA = la recomendada (la que va
# como botón principal en la tabla; el resto caen en el desplegable "Otra
# opción"). "mover" reprograma al primer hueco libre (no sobrecarga a nadie y
# el paciente igual queda agendado), por eso encabeza todo lo reparable.
_RESOL_POR_CLUSTER = {
    "profesional": ["mover", "sobreturno", "rechazar"],
    "sin_hueco": ["mover", "sobreturno", "rechazar"],
    "especialidad": ["mover", "rechazar"],
    "drift": ["mover", "rechazar"],
    "carrera": ["mover", "rechazar"],
    "remoto_cancelado": ["rechazar"],
    "vinculo": ["rechazar"],
    "otro": ["rechazar"],
}


def resoluciones_para(cluster, activo=True):
    """Las resoluciones que un operador puede aplicar a un conflicto del
    cluster dado, la primera recomendada. Sin HSI (modo skip) sólo queda
    rechazar — mover/sobreturno necesitan hablar con HSI."""
    accs = list(_RESOL_POR_CLUSTER.get(cluster, ["rechazar"]))
    if not activo:
        accs = [a for a in accs if a == "rechazar"] or ["rechazar"]
    return [{"accion": a, "label": RESOL_LABEL[a]} for a in accs]


def estado_panel(limit=60):
    """El payload de la sección «HSI» del admin: % sincronizado + la tabla de
    conflictos abiertos (uno por fila, con sus resoluciones) + el registro de
    lo que se resolvió solo."""
    from app.core import db_booking
    from app.core import db_hsi
    metricas = db_hsi.hsi_metrics()
    futuras, futuras_ok = metricas.get("futuras") or 0, metricas.get("futuras_ok") or 0
    total, total_ok = metricas.get("total") or 0, metricas.get("total_ok") or 0
    activo = _activo()
    conflictos = []
    for conflicto in db_hsi.hsi_open_conflicts(limit):
        cluster = clasificar(conflicto.get("reason"))
        sid = _sid(conflicto)
        row = db_booking.get_booking_request(sid) if sid is not None else None
        paciente = " ".join(p for p in ((row or {}).get("nombre"),
                                        (row or {}).get("apellido")) if p) or None
        turno = (row or {}).get("turno_dt")
        conflictos.append({
            "conflicto_id": conflicto["id"],
            "solicitud_id": sid,
            "fecha": _art_iso(turno) if turno else None,
            "paciente": paciente,
            "medico": (row or {}).get("medico_asignado"),
            "problema": detalle_es(conflicto),
            "cluster": cluster,
            "resoluciones": resoluciones_para(cluster, activo),
            "created": conflicto.get("created").isoformat()
            if isinstance(conflicto.get("created"), datetime) else conflicto.get("created"),
        })
    conflictos.sort(key=lambda c: c["fecha"] or "9999")

    resueltos, vistos = [], set()
    for r in db_hsi.hsi_recently_resolved(30):
        resolucion = r.get("resolution") or {}
        if resolucion.get("accion") in ("repetido", "obsoleto"):
            continue    # ruido de limpieza: al personal le importa lo que se ARREGLÓ
        texto = resolucion.get("texto") or "Resuelto automáticamente."
        if texto in vistos:
            continue    # dos conflictos del mismo grupo cierran con el mismo texto
        vistos.add(texto)
        resueltos.append({
            "solicitud_id": _sid(r),
            "texto": texto,
            "cuando": r.get("resolved_at").isoformat()
            if isinstance(r.get("resolved_at"), datetime) else r.get("resolved_at"),
        })
    return {
        "metricas": {
            "futuras": futuras, "futuras_ok": futuras_ok,
            "pct_futuras": round(100 * futuras_ok / futuras) if futuras else None,
            "total": total, "total_ok": total_ok,
            "pct_total": round(100 * total_ok / total) if total else None,
            "outbox": metricas.get("outbox") or {},
        },
        "abiertos": len(conflictos),
        "conflictos": conflictos,
        "resueltos": resueltos[:20],
    }
