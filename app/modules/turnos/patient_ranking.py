"""patient_ranking.py — how likely a patient is to show up to their appointment.

The admission desk needs, as a single number, what today only the person
who's worked the desk for years knows: "give this one an easy slot" / "call
that one ahead of time to check in." That's this ranking. It's NOT a
punishment or a moral score: it's a probability estimate, with its
uncertainty, to decide who's worth reminding about their appointment, who
gets offered a freed-up slot first, and who's better served walking up to
the counter instead of going through the web.

What the product owner asked for (2026-07-27), and how each half is met:

  1. "That it measure the percentage of appointments they attended."  → yes,
     but a raw percentage lies with little data: 1 out of 1 isn't 100% of
     anything.
  2. "That it not reward booking a bunch of fake appointments."        → volume
     does NOT raise the score's ceiling, and redundant appointments LOWER it.

The formula
----------
Each past appointment is a Bernoulli trial (came / didn't come) and the
score is the **posterior mean of a Beta-Binomial** (empirical Bayes — the
same method used to rank batting averages or ratings with few votes; the
classic alternative, the Wilson lower bound, was deliberately rejected: it
starts at 0 for anyone with no history, and here "we don't know yet" has to
be the hospital's average, not the worst possible grade):

    score = 100 · (S + α) / (N + α + β)          α = π·k,  β = (1−π)·k

  · S = accumulated success, N = accumulated evidence (see WEIGHTS below).
  · π = the hospital's attendance rate (PRIOR): the starting point for
    someone with no history.
  · k = how much evidence that prior "is worth", measured in appointments
    (PRIOR_WEIGHT). With k=4, four of the person's own appointments already
    weigh as much as the initial belief.

Three properties that fall out of that shape on their own, and are exactly
what was asked for:

  · No history → the score is exactly π. Nobody starts under suspicion.
  · Volume ≠ merit. The score converges to the person's OWN rate; showing
    up to 30 appointments doesn't push you higher than your own rate, it
    just removes the uncertainty. There's no way to game it by booking more.
  · Each miss weighs less as it ages (exponential decay with half-life
    HALF_LIFE_DAYS): someone who used to miss appointments three years ago
    and now always shows up climbs back up on their own, with no one having
    to "forgive" them.

The weights (WEIGHTS): evidence and success are NOT the same thing
----------------------------------------------------
Each fact contributes a mass of evidence `m` and a mass of success `s ≤ m`.
Keeping them separate is what lets us say "this is somewhat bad" without
inventing an appointment that never happened:

  · Showed up                  → (1.00, 1.00)  the ideal case.
  · No-show                    → (1.00, 0.00)  the slot was lost entirely.
  · Confirmed and didn't show  → (1.30, 0.00)  worse than silence: the
    confirmation is exactly what took that slot out of the waitlist, so on
    top of missing it, it couldn't be given to anyone else.
  · Cancelled with 48h+ notice → (0.15, 0.12)  giving notice in time is the
    behavior we want. It barely moves the score: it neither rewards nor
    punishes. It deliberately does NOT count as "showed up" — if
    cancelling counted, booking ten appointments and cancelling all of them
    would give an excellent score, which is exactly the "reward for fake
    appointments" that has to be avoided.
  · Cancelled 12-48h ahead     → (0.50, 0.20)  the waitlist can sometimes
    still recover the slot.
  · Cancelled with <12h notice → (1.00, 0.10)  in practice this is a warned
    no-show: nobody manages to take that slot.
  · Cancelled, no known time   → (0.30, 0.20)  old rows, from before
    `cancelada_en` was saved. When in doubt, in the patient's favor.
  · Redundant appointment      → (0.80, 0.00)  see below.

What does NOT factor in, and why (this is a decision, not an oversight)
------------------------------------------------------------------------
  · **Appointment volume.** A chronic patient —physical therapy, oncology,
    prenatal checkups— books twenty appointments a year and that's exactly
    right. Penalizing "using the hospital a lot" would penalize being sick.
    Waste is measured by REDUNDANCY, not by quantity.
  · **Rejected requests.** A rejection is the hospital's decision (expired
    referral, illegible, wrong specialty). Counting them would punish
    hardest whoever's referring doctor has the worst handwriting, or
    whoever understands the paperwork the least. It's a proxy for hardship,
    not behavior.
  · **Unmarked appointments.** If no one recorded attendance, we don't know
    what happened. Nothing is assumed: it neither adds nor subtracts. This
    is why `n` (effective appointments) is always shown next to the score.

Redundant appointment = the "fake appointment" that CAN actually be detected
----------------------------------------------------------------
Two or more appointments for the SAME service within DUPLICATE_DAYS, of
which at most one could be used. The one that wasn't used occupied a slot
nobody else could take: that's waste, intentional or not. A legitimate
series (ten physical-therapy sessions for the same service in a month) does
NOT fall here: every session that ends in "showed up" is never marked
redundant. And when an appointment already counts as a no-show, the WORSE of
the two weights applies, never the sum: one fact isn't punished twice.

Intended use: display it. No endpoint decides anything based on this score.
Blocking the web channel is still the rule agreed with the hospital
(TURNOS_NO_SHOW_LIMIT no-shows, see booking_requests._no_show_limit) and is
deliberately independent from this: a rule the front desk understands and
can explain, versus a score that informs. Changing that — e.g. sorting the
waitlist by ranking — is the hospital's decision, not the code's.

Without a database, `by_dnis` returns the neutral score for everyone: the
console never goes down over an auxiliary piece of data.
"""

from datetime import datetime, timedelta, timezone

from app.core import config
from app.core import tz as _tz

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)


def _env(nombre, default):
    """A config number, tolerantly: a typo in the environment falls back to
    the default instead of taking down the console."""
    try:
        valor = float(config.get(nombre, "") or default)
    except (TypeError, ValueError):
        return float(default)
    return valor


def PRIOR():
    """The hospital's attendance rate: where someone with no history starts.

    0.80 comes from what leadership reported in the 2026-07-24 meeting:
    historical no-show rate runs around 30% and drops to 18-20% when the
    appointment is booked less than a month ahead (which is what today's
    site default of 31 days does, booking_horizon.DEFAULT_DAYS). Left configurable so
    it can be recalibrated against real data once there's a year of our own
    history.
    """
    return min(0.99, max(0.01, _env("TURNOS_RANKING_PRIOR", 0.80)))


def PRIOR_WEIGHT():
    """How many appointments' worth the prior is "worth". Higher = more
    conservative with little history (harder to go up or down)."""
    return max(0.5, _env("TURNOS_RANKING_PRIOR_PESO", 4.0))


def HALF_LIFE_DAYS():
    """After how many days a fact weighs half as much. A year: behavior from
    three years ago barely counts, last month's counts in full."""
    return max(30.0, _env("TURNOS_RANKING_SEMIVIDA_DIAS", 365.0))


def WINDOW_DAYS():
    """Anything older than this isn't even read. Caps the query's cost and
    avoids dragging along forever a history that no longer says anything."""
    return max(90.0, _env("TURNOS_RANKING_VENTANA_DIAS", 1095.0))


def DUPLICATE_DAYS():
    """Window within which two appointments for the same service are
    considered the same request (and so one of the two is redundant)."""
    return max(1.0, _env("TURNOS_RANKING_DUPLICADO_DIAS", 30.0))


# (evidence, success) per fact type. See the module docstring for the
# reasoning behind each row; touching these numbers changes the policy, not
# the calculation.
WEIGHTS = {
    "vino": (1.00, 1.00),
    "falto": (1.00, 0.00),
    "falto_confirmado": (1.30, 0.00),
    "cancelo_temprano": (0.15, 0.12),
    "cancelo_medio": (0.50, 0.20),
    "cancelo_tarde": (1.00, 0.10),
    "cancelo_sin_dato": (0.30, 0.20),
    "redundante": (0.80, 0.00),
}

# Notice hours separating an early / mid / late cancellation.
EARLY_NOTICE_H = 48
LATE_NOTICE_H = 12

# Band cutoffs. The score is continuous; the bands are the language the desk
# speaks in ("this one always comes", "that one misses a lot"). Three, drawn
# as three bars (the product owner, 2026-10-27: «three bars, not five. Less
# precision»): the old «Suele venir» (70–84) merged into «Viene siempre».
# A client that still holds the retired slug "suele-venir" (a saved filter)
# reads it as "viene-siempre" (static/platform/attendance-forecast.mjs).
BANDS = (
    (70, "viene-siempre", "Viene siempre", "good"),
    (50, "falta-a-veces", "Falta a veces", "warn"),
    (0, "falta-seguido", "Falta seguido", "bad"),
)

# Below this effective evidence the score is almost all prior: it says "no
# history yet" instead of pretending to know something.
MIN_HISTORY = 0.75


def _now(ahora=None):
    if ahora is None:
        return datetime.now(timezone.utc)
    if ahora.tzinfo is None:
        return ahora.replace(tzinfo=timezone.utc)
    return ahora


def _dt(valor):
    """A Postgres timestamptz, an ISO string, or nothing."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decay(fecha, ahora):
    """0.5 ** (days / half-life), capped to [0, 1]. A fact in the future
    (clock drift, tomorrow's appointment someone over-marked) weighs 1, not
    more."""
    if not fecha:
        return 1.0
    dias = (ahora - fecha).total_seconds() / 86400.0
    if dias <= 0:
        return 1.0
    return 0.5 ** (dias / HALF_LIFE_DAYS())


def _classify_cancellation(turno_dt, cancelada_en):
    """How much notice the cancellation gave. Without `cancelada_en` (rows
    from before it was saved) nothing is guessed: 'no known time', which
    weighs little and in the patient's favor."""
    if not turno_dt or not cancelada_en:
        return "cancelo_sin_dato"
    horas = (turno_dt - cancelada_en).total_seconds() / 3600.0
    if horas >= EARLY_NOTICE_H:
        return "cancelo_temprano"
    if horas >= LATE_NOTICE_H:
        return "cancelo_medio"
    return "cancelo_tarde"


def events_from_requests(rows, ahora=None):
    """Translates `solicitudes` rows into scoreable facts.

    Returns a list of dicts {tipo, fecha, servicio_id, solicitud_id}. A row
    that says nothing (pending, rejected, approved but unmarked) generates
    no fact: silence isn't evidence.
    """
    ahora = _now(ahora)
    corte = ahora - timedelta(days=WINDOW_DAYS())
    eventos = []
    for row in rows or []:
        estado = (row.get("estado") or "").strip()
        turno_dt = _dt(row.get("turno_dt"))
        asistencia = (row.get("asistencia") or "").strip()
        tipo = None
        fecha = turno_dt

        if asistencia == "presente":
            tipo = "vino"
        elif asistencia == "ausente":
            # Confirming and not showing up is worse than not answering: the
            # confirmation is exactly what prevented re-offering the slot.
            tipo = ("falto_confirmado"
                    if (row.get("confirmacion") or "").strip() == "si" else "falto")
        elif estado == "cancelada" and turno_dt:
            # Only a cancellation of an ALREADY GRANTED appointment counts.
            # Withdrawing a request the hospital hadn't confirmed yet
            # doesn't free up any slot, so it's not waste of anything.
            cancelada_en = _dt(row.get("cancelada_en"))
            tipo = _classify_cancellation(turno_dt, cancelada_en)
            fecha = cancelada_en or turno_dt

        if not tipo:
            continue
        fecha = fecha or _dt(row.get("created")) or ahora
        if fecha < corte:
            continue
        eventos.append({
            "tipo": tipo,
            "fecha": fecha,
            "servicio_id": (row.get("servicio_id") or "").strip(),
            "solicitud_id": row.get("id"),
        })

    return _mark_redundant(rows, eventos, ahora)


def _mark_redundant(rows, eventos, ahora):
    """Upgrades to `redundante` the appointments that were surplus.

    Two appointments for the same service less than DUPLICATE_DAYS apart:
    at most one was usable. The one NOT used occupied a slot nobody else
    could take.

    Rules that avoid over-punishing:
      · an appointment the person SHOWED UP TO is never redundant (so a
        physical-therapy series, ten sessions of the same service in a
        month, never falls here);
      · if the appointment already counts as a no-show, the WORSE of the
        two weights applies, never the sum — one fact, one penalty.
    """
    ventana = timedelta(days=DUPLICATE_DAYS())
    por_servicio = {}
    for row in rows or []:
        # Only appointments actually GRANTED occupy a slot. A pending or
        # rejected request didn't reserve anything.
        if (row.get("estado") or "") not in ("aprobada", "cancelada"):
            continue
        turno_dt = _dt(row.get("turno_dt"))
        if not turno_dt:
            continue
        por_servicio.setdefault((row.get("servicio_id") or "").strip(), []).append(
            (turno_dt, row))

    por_id = {}
    for ev in eventos:
        if ev.get("solicitud_id") is not None:
            por_id[ev["solicitud_id"]] = ev

    for _, items in por_servicio.items():
        items.sort(key=lambda t: t[0])
        grupo = []
        grupos = []
        for turno_dt, row in items:
            if grupo and turno_dt - grupo[0][0] > ventana:
                grupos.append(grupo)
                grupo = []
            grupo.append((turno_dt, row))
        if grupo:
            grupos.append(grupo)

        for grupo in grupos:
            if len(grupo) < 2:
                continue
            # The one that was used (or, if none was, the first) is the
            # legitimate one.
            usados = [i for i, (_, r) in enumerate(grupo)
                      if (r.get("asistencia") or "") == "presente"]
            legitimos = set(usados) if usados else {0}
            for i, (turno_dt, row) in enumerate(grupo):
                if i in legitimos or (row.get("asistencia") or "") == "presente":
                    continue
                sid = row.get("id")
                ev = por_id.get(sid)
                m_red, s_red = WEIGHTS["redundante"]
                if ev is None:
                    eventos.append({
                        "tipo": "redundante", "fecha": turno_dt,
                        "servicio_id": (row.get("servicio_id") or "").strip(),
                        "solicitud_id": sid,
                    })
                    continue
                m_ev, s_ev = WEIGHTS.get(ev["tipo"], (0.0, 0.0))
                # "Worse" = the one that leaves less success per unit of
                # evidence; tied, the one that weighs more. The two are
                # never summed.
                peor = (s_red / m_red if m_red else 0) < (s_ev / m_ev if m_ev else 0)
                if peor or (s_red == s_ev and m_red > m_ev):
                    ev["tipo"] = "redundante"
                ev["redundante"] = True

    return eventos


def _plural(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


def _reasons(crudos):
    """The three lines the front desk needs to read, in order of weight. The
    score without the "why" doesn't help anyone see patients."""
    fuera = []
    if crudos["falto_confirmado"]:
        fuera.append(_plural(crudos["falto_confirmado"],
                             "vez que confirmó y no vino",
                             "veces que confirmó y no vino"))
    if crudos["falto"]:
        fuera.append(_plural(crudos["falto"], "ausencia", "ausencias"))
    if crudos["redundante"]:
        fuera.append(_plural(crudos["redundante"],
                             "turno repetido del mismo servicio",
                             "turnos repetidos del mismo servicio"))
    if crudos["cancelo_tarde"]:
        fuera.append(_plural(crudos["cancelo_tarde"],
                             "cancelación sobre la hora",
                             "cancelaciones sobre la hora"))
    if crudos["vino"]:
        fuera.append(_plural(crudos["vino"], "turno al que vino",
                             "turnos a los que vino"))
    if crudos["cancelo_temprano"] or crudos["cancelo_medio"]:
        n = crudos["cancelo_temprano"] + crudos["cancelo_medio"]
        fuera.append(_plural(n, "cancelación avisada", "cancelaciones avisadas"))
    return fuera[:3]


def neutral():
    """The score for someone with no history: the hospital's average. It's
    also what's returned when the DB doesn't answer — never a 0, which
    would read as 'this one always misses'."""
    puntaje = round(100 * PRIOR())
    return {
        "puntaje": puntaje, "banda": "sin-historial", "etiqueta": "Sin historial",
        "nivel": "none", "n": 0.0, "hechos": 0, "ultimo": None, "sin_historial": True,
        "vino": 0, "falto": 0, "cancelo": 0, "redundante": 0,
        "motivos": [], "detalle": "Todavía no tiene turnos con asistencia registrada.",
    }


def score(eventos, ahora=None):
    """The calculation, over already-built facts. Pure and testable: touches
    neither the DB nor the clock except via `ahora`."""
    ahora = _now(ahora)
    S = 0.0
    N = 0.0
    crudos = {k: 0 for k in WEIGHTS}
    ultimo = None
    for ev in eventos or []:
        tipo = ev.get("tipo")
        if tipo not in WEIGHTS:
            continue
        m, s = WEIGHTS[tipo]
        w = _decay(ev.get("fecha"), ahora)
        N += w * m
        S += w * s
        crudos[tipo] += 1
        f = ev.get("fecha")
        if f and (ultimo is None or f > ultimo):
            ultimo = f
    # When they were last seen. Doesn't factor into the calculation (the
    # decay already accounts for it): it's for the "Last appointment" column
    # in the list, which is what the desk looks at to know if a record is
    # still active.
    #
    # ALWAYS converted to ART before extracting the date: the process runs
    # with the system's TZ (seen as America/New_York in dev), so trusting
    # the offset Postgres hands back moves the 21:00 appointment to the
    # previous day. Same care as calendar_feed.py.
    ultimo = ultimo.astimezone(_ART).date().isoformat() if ultimo is not None else None

    k = PRIOR_WEIGHT()
    alfa = PRIOR() * k
    beta = (1.0 - PRIOR()) * k
    p = (S + alfa) / (N + alfa + beta)
    puntaje = int(round(100 * p))

    if N < MIN_HISTORY:
        out = neutral()
        out["puntaje"] = puntaje
        out["n"] = round(N, 2)
        out["vino"] = crudos["vino"]
        out["falto"] = crudos["falto"] + crudos["falto_confirmado"]
        out["cancelo"] = (crudos["cancelo_temprano"] + crudos["cancelo_medio"]
                          + crudos["cancelo_tarde"] + crudos["cancelo_sin_dato"])
        out["redundante"] = crudos["redundante"]
        out["hechos"] = sum(crudos.values())
        out["ultimo"] = ultimo
        return out

    banda, etiqueta, nivel = "falta-seguido", "Falta seguido", "bad"
    for corte, slug, texto, clase in BANDS:
        if puntaje >= corte:
            banda, etiqueta, nivel = slug, texto, clase
            break

    return {
        "puntaje": puntaje,
        "banda": banda,
        "etiqueta": etiqueta,
        "nivel": nivel,
        "n": round(N, 2),
        # `n` is the evidence with decay applied (math, with decimals);
        # `hechos` are appointments counted one by one, which is what the
        # desk can verify by looking at the history. `hechos` is what's
        # shown.
        "hechos": sum(crudos.values()),
        "ultimo": ultimo,
        "sin_historial": False,
        "vino": crudos["vino"],
        "falto": crudos["falto"] + crudos["falto_confirmado"],
        "cancelo": (crudos["cancelo_temprano"] + crudos["cancelo_medio"]
                    + crudos["cancelo_tarde"] + crudos["cancelo_sin_dato"]),
        "redundante": crudos["redundante"],
        "motivos": _reasons(crudos),
        "detalle": "",
    }


def from_requests(rows, ahora=None):
    """Shortcut: a patient's rows → a score."""
    return score(events_from_requests(rows, ahora), ahora)


def by_dnis(dnis, ahora=None):
    """Score for several patients in ONE query (the console's list is 200
    rows: a request per patient isn't an option).

    Best-effort by contract: without a DB, or if the query fails, everyone
    comes back with the neutral score. An auxiliary piece of data doesn't
    take down the screen.
    """
    limpios = sorted({str(d or "").strip() for d in dnis if str(d or "").strip()})
    out = {dni: neutral() for dni in limpios}
    if not limpios:
        return out
    try:
        from app.core import db_booking
        filas = db_booking.booking_requests_for_ranking(limpios, int(WINDOW_DAYS()))
    except Exception:  # noqa: BLE001 — see docstring: never breaks the caller
        return out
    por_dni = {}
    for row in filas:
        por_dni.setdefault(str(row.get("dni") or "").strip(), []).append(row)
    for dni in limpios:
        out[dni] = from_requests(por_dni.get(dni, []), ahora)
    return out


def by_dni(dni, ahora=None):
    return by_dnis([dni], ahora).get(str(dni or "").strip(), neutral())
