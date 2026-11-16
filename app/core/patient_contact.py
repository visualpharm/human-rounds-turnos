"""patient_contact.py — EL contacto de un paciente en las pantallas del médico,
con de quién es (problems.md №4, 2026-10-23; regla del responsable del producto, hub, 2026-10-23).

«Mis turnos» mostraba el contacto copiado en la solicitud y «Mis pacientes» el
de la cuenta: el mismo paciente con dos teléfonos, sin decir de quién era
ninguno. La regla, una sola, para las pantallas del médico:

  1. Para el TURNO el teléfono principal es el de la SOLICITUD — el que se dio
     al pedir el turno (`contacto_de = "solicitud"`). Es el que usan los
     recordatorios de WhatsApp (notifications/send.py).
  2. El teléfono de la cuenta NUNCA se pisa con el de la solicitud (sólo el
     paciente cambia el suyo): si la cuenta de la persona de contacto tiene
     OTRO número, el médico ve los dos — el segundo en `contacto_cuenta`, con
     `contacto_cuenta_de` = "paciente" o "responsable" (su nombre en
     `contacto_responsable` si tiene cuenta). Si es el mismo número, se
     muestra uno solo, dicho como de la cuenta.
  3. La persona de contacto es quien sacó el turno (`solicitante_dni`); si no
     se sabe, el propio paciente. Para un menor o un familiar a cargo es el
     adulto titular de la cuenta.
  4. Sin teléfono en la solicitud, vale el de la cuenta (`contacto_de` =
     "paciente" | "responsable").
"""

from app.core import format
from app.core.ids import dni as _dni


def _misma_persona(a, b):
    """¿Dos contactos escritos distinto son el mismo? Un mail se compara en
    minúsculas; un teléfono por sus dígitos nacionales (con o sin +54, 9 o
    15)."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if "@" in a or "@" in b:
        return a.lower() == b.lower()
    na, nb = format.tel_national(a), format.tel_national(b)
    return (na == nb) if (na and nb) else (a == b)


def contacto(dni, solicitante_dni=None, contacto_solicitud=None, usuarios=None):
    """{"contacto", "contacto_de", "contacto_responsable", "contacto_cuenta",
    "contacto_cuenta_de"} — `usuarios` es `db_clinical.users_by_dnis` sobre los DNIs de
    contacto (`personas_de`)."""
    dni = _dni(dni)
    persona = _dni(solicitante_dni) or dni
    cuenta = (usuarios or {}).get(persona) or {}
    de_cuenta = "responsable" if (persona and persona != dni) else "paciente"
    nombre = None
    if de_cuenta == "responsable":
        nombre = " ".join(x for x in (cuenta.get("nombre"), cuenta.get("apellido")) if x).strip()
        nombre = format.person_name(nombre) if nombre else None
    propio = (cuenta.get("contacto") or "").strip()
    pedido = str(contacto_solicitud or "").strip()
    vacio = {"contacto": None, "contacto_de": None, "contacto_responsable": None,
             "contacto_cuenta": None, "contacto_cuenta_de": None}
    if not propio and not pedido:
        return vacio
    if not pedido or (propio and _misma_persona(pedido, propio)):
        # sólo la cuenta, o la solicitud trae lo mismo que la cuenta
        return {**vacio, "contacto": propio or pedido,
                "contacto_de": de_cuenta if propio else "solicitud",
                "contacto_responsable": nombre if propio else None}
    return {**vacio, "contacto": pedido, "contacto_de": "solicitud",
            "contacto_responsable": nombre,
            "contacto_cuenta": propio or None,
            "contacto_cuenta_de": de_cuenta if propio else None}


def personas_de(filas):
    """Los DNIs cuya cuenta hay que leer para `contacto()` de estas filas
    (wire names de booking_requests: `dni`, `solicitante_dni`)."""
    return sorted({_dni(f.get("solicitante_dni")) or _dni(f.get("dni")) for f in filas} - {""})
