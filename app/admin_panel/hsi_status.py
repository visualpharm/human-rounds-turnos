"""Where HSI stands on this installation, in words the panel can show.

One reading for the three places that name it — «Sistema», «Conectores» and
`/admin/hsi` — so they can never disagree: how a turno reaches HSI here (the
server sends it, the desk loads it with the browser extension, or nothing
does), why, and which center has its institution number in HSI.

Read-only by construction: the connector registry, two settings, the
extension's own files and the catalog. It never opens a socket and never
writes; the journal counters are read only when the server is the one that
sends, and a database that does not answer leaves them out instead of
failing the screen.
"""

from app.core import config, hsi_extension, i18n

SERVER = "servidor"
EXTENSION = "extension"
OFF = "apagado"


def _t(key, es, **kw):
    """Same catalog as «Sistema» (locales/*/admin.system.status.json)."""
    return i18n.t_or("admin.system.status.hsi." + key, es, i18n.active(), **kw)


def _mode_text(modo):
    if modo == SERVER:
        return _t("mode_server", "Envío desde el servidor")
    if modo == EXTENSION:
        return _t("mode_extension", "Carga con la extensión del navegador")
    return _t("mode_off", "Apagado")


def _reason_text(modo, motivo):
    """Whole sentences, one per case: a translated half glued to another
    half reads wrong in the other nine languages."""
    if modo == SERVER:
        return _t("reason_server",
                  "El conector HSI está activo: el servidor envía a HSI cada turno aprobado.")
    if modo == EXTENSION:
        if motivo == "skip":
            return _t("reason_extension_skip",
                      "El envío desde el servidor está deshabilitado en esta instalación. "
                      "La recepción carga los turnos en HSI con la extensión del navegador.")
        if motivo == "otro_conector":
            return _t("reason_extension_other",
                      "Hay otro sistema clínico conectado. "
                      "La recepción carga los turnos en HSI con la extensión del navegador.")
        return _t("reason_extension_no_connector",
                  "El conector HSI no está activo, así que el servidor no envía turnos. "
                  "La recepción los carga en HSI con la extensión del navegador.")
    if motivo == "skip":
        return _t("reason_off_skip",
                  "El envío desde el servidor está deshabilitado y la extensión del navegador "
                  "no funciona en este sitio: los turnos no llegan a HSI.")
    if motivo == "otro_conector":
        return _t("reason_off_other",
                  "Hay otro sistema clínico conectado; los turnos no llegan a HSI.")
    return _t("reason_off_no_connector",
              "El conector HSI no está activo y la extensión del navegador no funciona "
              "en este sitio: los turnos no llegan a HSI.")


def _centers():
    """Every live center with its institution number in HSI ("" when the
    center has none), in catalog order. A catalog that cannot be read is an
    empty list: the screen still names the mode."""
    try:
        from app.patient_web import catalog
        rows = catalog.centers()
    except Exception:  # noqa: BLE001 — the state of HSI never fails on the catalog
        return []
    out = []
    for c in rows or []:
        slug = str(c.get("slug") or "")
        if not slug:
            continue
        out.append({
            "slug": slug,
            "nombre": str(c.get("name") or slug),
            "institucion": str(c.get("hsi_institution") or ""),
        })
    return out


def centers_summary(centros):
    """(valor, detalle) of the one line that names the institution numbers:
    «2 de 5 centros con número» and, under it, each center with its number
    and the ones that have none called out by name."""
    total = len(centros)
    con = [c for c in centros if c["institucion"]]
    sin = [c for c in centros if not c["institucion"]]
    if not total:
        return _t("centers_none", "Sin centros cargados"), None
    if total == 1:
        # One center is a sentence of its own: «0 de 1 centros» agrees in no language.
        valor = _t("centers_all_one", "El centro tiene número") if con \
            else _t("centers_missing_one", "El centro no tiene número")
    elif not sin:
        valor = _t("centers_all", "Los {total} centros tienen número", total=total)
    else:
        valor = _t("centers_some", "{n} de {total} centros con número", n=len(con), total=total)
    partes = ["%s %s" % (c["nombre"], c["institucion"]) for c in con]
    if sin:
        partes.append(_t("centers_missing", "Sin número: {names}",
                         names=", ".join(c["nombre"] for c in sin)))
    return valor, " · ".join(partes)


def read_journal():
    """The server-side journal, read ONCE for «Sistema» and «Conectores» so
    the two can never count it differently: {"ultima": the last transmission
    HSI confirmed, as a datetime in the installation's timezone, or None,
    "en_cola": waiting or being sent, "errores": stopped on an error,
    "conflictos": waiting for a person, "metricas": `db_hsi.hsi_metrics()`
    as it is}. Raises when the database does not answer; call it only in
    server mode."""
    from app.core import db_hsi, tz

    m = db_hsi.hsi_metrics()
    outbox = m.get("outbox") or {}
    ultima = db_hsi.hsi_last_confirmed()
    return {
        "ultima": ultima.astimezone(tz.tzinfo()) if ultima else None,
        "en_cola": int(outbox.get("pending") or 0) + int(outbox.get("sending") or 0),
        "errores": int(outbox.get("error") or 0),
        "conflictos": len(db_hsi.hsi_open_conflicts()),
        "metricas": m,
    }


def journal():
    """`read_journal()` for the wire, or None when it cannot be read: the
    instant as ISO text with the installation's offset (the screen reads the
    day and the hour from it literally, never through the reader's clock) and
    the three counters."""
    try:
        diario = read_journal()
    except Exception:  # noqa: BLE001 — no database, no counters; the mode is still true
        return None
    return {
        "ultima": diario["ultima"].isoformat() if diario["ultima"] else None,
        "en_cola": diario["en_cola"],
        "errores": diario["errores"],
        "conflictos": diario["conflictos"],
    }


def _extension():
    """(runs here, where to install it): the desk draws «Cargar en HSI» on
    `hsi_extension.store_url()` and offers the installation from `install()`
    — the second can have nothing to offer (no published store page, no ZIP
    in the image) on a site the first still serves. The mode follows either,
    so the panel never says «Apagado» over a desk that is loading turnos."""
    try:
        instalar = hsi_extension.install()
        return bool(instalar or hsi_extension.store_url()), instalar
    except Exception:  # noqa: BLE001 — unreadable extension files: it does not run here
        return False, None


def snapshot(con_diario=False):
    """The state of HSI on this installation.

    `modo` is how a turno reaches HSI: `servidor` (the HSI connector is the
    selected one and the server-side push is not disabled), `extension` (the
    server does not send, and the desk's browser extension runs on this
    site) or `apagado` (neither). `motivo` is why: `conector`, `skip` (the
    connector is selected but this installation does not send),
    `otro_conector` or `sin_conector`.
    """
    from app import connectors

    emr_id, _source = connectors.resolve(connectors.EMR_SYNC)
    skip = config.get("TURNOS_SKIP_HSI", "") == "1"
    extension = None
    if emr_id == "hsi" and not skip:
        modo, motivo = SERVER, "conector"
    else:
        motivo = "skip" if emr_id == "hsi" else ("otro_conector" if emr_id else "sin_conector")
        funciona, extension = _extension()
        modo = EXTENSION if funciona else OFF
    centros = _centers()
    resumen, detalle = centers_summary(centros)
    out = {
        "modo": modo,
        "motivo": motivo,
        "conector": emr_id or None,
        "modo_texto": _mode_text(modo),
        "motivo_texto": _reason_text(modo, motivo),
        "extension": extension,
        "centros": centros,
        "con_numero": sum(1 for c in centros if c["institucion"]),
        "total": len(centros),
        "centros_texto": resumen,
        "centros_detalle": detalle,
    }
    if con_diario and modo == SERVER:
        out["diario"] = journal()
    return out
