"""reports.py — admin panel analytics (Pinamar Turnos): the "Reports" section,
with four sub-reports (Management, Doctors, Patients, AI Costs — the last one
served by ai_costs.py, see below) plus four billing/statistics sub-reports
added on 2026-07-24 (Phase 2b of the "doctor day-queue + reports expansion"
plan).

Owner of this module:
  GET /api/admin/reports[?days=30|7|90]                 Period KPIs + charts
                                                          — "Management"
                                                          sub-report (default).
  GET /api/admin/reports/requests.csv[?days=90]          management CSV export
  GET /api/admin/reports/patients[?days=30|7|90]         "Patients" sub-report
                                                          — see _patients_payload().
  GET /api/admin/reports/searches[?days=30|7|90]         raw search log —
                                                          "Searches" sub-report
                                                          (clustering happens
                                                          on the frontend, see
                                                          _searches_full_payload()).
  GET /api/admin/reports/cost-recovery[?days=90]         "Cost recovery"
                                                          sub-report — see
                                                          _cost_recovery_payload().
  GET /api/admin/reports/cost-recovery.csv[?days=90]     billing CSV export
                                                          (WITH deliberate PII,
                                                          see
                                                          _cost_recovery_csv_rows()).
  GET /api/admin/reports/sumar[?days=90]                 "SUMAR+" sub-report
                                                          — see _sumar_payload().
  GET /api/admin/reports/sumar.csv[?days=90]             SUMAR+ CSV export
                                                          (WITH deliberate PII,
                                                          see _sumar_csv_rows()).
  GET /api/admin/reports/statistics[?mes=YYYY-MM]        "Statistics"
                                                          sub-report (C2
                                                          style) — see
                                                          _statistics_payload().
  GET /api/admin/reports/statistics.csv[?mes=YYYY-MM]    aggregated CSV export
                                                          WITHOUT PII — see
                                                          _statistics_csv_rows().
  GET /api/admin/reports/capacity                        "Seasonal capacity"
                                                          sub-report — see
                                                          _capacity_payload().

All routes require the 'admin' role — an operator who only works the request
queue does NOT see management reports, same criterion as admin_users.py
("Users"). The native router owns that gate.

The "Doctors" sub-report (admin-reports.js) does NOT hit this module: it
consumes GET /api/admin/doctors/stats (owned by doctor_appointments.py, which
already existed for the #medicos management listing) — no new endpoint was
needed. Neither does "AI Costs": it still lives in ai_costs.py (GET
/api/admin/costs, exclusive owner of that table and the fair-use gate) — only
the UI moved INSIDE admin-reports.js (it used to be a separate nav item,
"#costos"; see that file's comment for the hash-routing of the four tabs).

Sources:
  - reports_store.py: SQL aggregations over `solicitudes` (medians with
    percentile_cont, Postgres) — see the "management reports" section in
    that file — and over `usuarios`/`dependientes` for Patients — see
    "patient reports", further down in the same file.
  - site_catalog._services()/_week_hours()/_centers()/_cluster_by_id():
    the LIVE catalog (base JSON + admin overlay) for hourly supply by
    specialty and the CSV's readable names. It gets IMPORTED, patient_web/site_*.py
    is not edited (CLAUDE.md: server.py/patient_site.py are the legacy
    monolith, new features go in their own module).
  - store.top_searches(): already exists (searches.py feeds the `busquedas`
    table) — no PII, logging started 2026-07-16.

Everything tolerates empty tables: every aggregation behind the store over an empty
set yields 0/None/[] (percentile_cont over nothing is NULL, not an error) —
never a 500. If the database is flat-out down, the store raises
DbUnavailable and the native router translates it to 503.

Patients: no PII. Neither the aggregation behind the store nor the response here
exposes any patient's first name, last name, or DNI — everything is
aggregated counts/percentages (same discipline as the management CSV, which
only exposes initials).

------------------------------------------------------------------------------
CONTRACT — new keys of GET /api/admin/reports (2026-07-20, story-driven
rework of "Reports" — see .claude/skills/report-design/SKILL.md). This block
is what the frontend (admin-reports.js) can assume; any shape change here has
to update this docstring in the same commit. Every key degrades gracefully
over an empty base (never missing, never a 500): empty lists, 0, or None as
appropriate.

hero: [{"texto": str, "tono": "ok"|"atencion"|"critico"}, ...]  (1 to 3)
    COMPUTED sentences (never constants), in priority order: (1) what the
    team delivered in the period — requests resolved, schedule hours
    offered, appointments recovered from the waitlist — ALWAYS framed
    positively; (2) where the team's next hour pays off most (a cluster with
    zero real supply, the only case with "critico" tone — a genuine capacity
    gap, never a complaint; otherwise the overloaded cluster with the
    biggest opportunity, "atencion" tone); (3) avoidable review time if
    preventable rejections are significant; (4, filler) movement of the
    median wait, ALWAYS "ok" tone — waiting under triage is the system
    working as intended, the direction is never editorialized. With little
    data it falls back to a neutral sentence ("not enough data yet...", "ok"
    tone). See "Editorial angle" in .claude/skills/report-design/SKILL.md.

oferta_demanda: {"clusters": [...], "servicios": [...], "equilibrio": float|None}
    Rows for the "requests vs hours offered" scatter. Each row:
    {"id", "nombre", "centro": str|None, "horas_semana", "solicitudes",
     "solicitudes_por_hora": float|None, "espera_mediana_dias": float|None,
     "sin_oferta": bool, "sugerencia_horas": float|None}.
    "servicios" only includes services with >=1 request in the period.
    "equilibrio" = the period's median solicitudes_por_hora (the scatter's
    reference/iso-line); "sugerencia_horas" = estimated extra weekly hours
    for THAT row to come down to the equilibrium ratio (None if it's already
    at or below it).

heatmap: {"dias": [...7 names, 0=Sunday..6=Saturday, JS Date.getDay()
          convention...], "horas": [7..21], "oferta": [[7 x 15]],
          "demanda": [[7 x 15]], "desajuste": [...up to 5...]}
    oferta[d][h] = how many services/doctors have real schedule coverage at
    that hour (live catalog, doesn't depend on the period). demanda[d][h] =
    appointment-request pressure in the period — the UNION of three sources:
    turno_dt (ART) of approved appointments, day/hour of every turno_holds
    created, and preferencia_horario ("morning"/"afternoon" band with no
    online schedule, spread evenly across Monday-Friday and the band's
    hours — never inventing a day the patient never said). desajuste = the
    top 5 cells where demand-minus-supply is most positive, with a
    plain-text "label" ("Tuesday afternoon").

palancas: {"grupos": [{"id","nombre","n","pct","motivos":[{"motivo","n"}]}],
           "hero": str}
    motivo_rechazo grouped into 3 causes: "prevenible_antes" (the 7
    standard reasons from admin.html:MOTIVOS_STD — all avoidable with better
    controls BEFORE sending the request), "capacidad" (free text mentioning
    lack of slots/schedule), "otro" (the rest). "pct" is over the period's
    total rejections (None if there were none).

funnel: {"holds_creados", "holds_convertidos_en_solicitud", "solicitudes",
         "aprobadas", "rechazadas", "canceladas", "pendientes",
         "asistencia": {"presente","ausente","sin_dato"},
         "recordatorios_enviados", "pasos", "frases", "con_turno"}
    All integer counts for the period (by `created`, same criterion as the
    rest of the report); "aprobadas" is the CURRENT status. "pasos" and
    "frases" are Recorrido's steps and sentences, unfiltered
    (reports_funnel.summary): the funnel on screen shows those, with those
    names. "con_turno" counts every request ever given an appointment
    (db_reports._ever_approved), the number of the hero's first sentence.

lista_espera: {"ofertas_enviadas","aceptadas","expiradas","rechazadas",
               "turnos_recuperados"}
    From `ofertas_turno` (waitlist.py), created in the period.
    "rechazadas" is ALWAYS None: the schema doesn't distinguish an explicit
    rejection ("don't want it") from a timeout expiry — both land in
    'expirada' state. "turnos_recuperados" = "aceptadas" (a freed-up
    appointment someone else ended up taking).

ahorro: {"disponible": bool, ...}
    If `costos_ia` has NO rows yet at all (freshly created database):
    {"disponible": False} and nothing else. If it does: {"disponible": True,
    "gasto_usd", "operaciones", "minutos_ahorrados", "horas_operador",
    "costo_hora_ars", "ahorro_ars", "roi": float|None, "supuestos": [str,...]}.
    Estimation model (constants + envs, ALL listed in "supuestos" so the
    frontend can print them verbatim): manual minutes avoided per recognized
    referral analysis, per recognized DNI OCR read, per PUCO lookup that
    found coverage on sign-up, and per automatic reminder sent. "roi" is
    None if there was no USD spend in the period (can't divide by zero).
    costo_hora_ars is env-overridable with TURNOS_COSTO_HORA_OPERADOR; the
    dollar rate used to compare against USD spend, with
    TURNOS_DOLAR_BLUE_REPORTE — it's NOT a live quote, it's a declared fixed
    assumption.

GET /api/admin/reports/reviews (solo_admin, no period — doesn't depend on
`days`): serves the review analysis built by scripts/fetch_resenas.py +
scripts/analizar_resenas.py, with a cascading fallback:
  1. resenas-analisis.json from the selected private town pack, if it exists,
     {"fuente": "analisis"} (the full shape —_meta/centers/comparison— is
     defined by whatever generates that file, see analizar_resenas.py's own
     field-by-field docs; no structure is validated here, it's just served).
  2. otherwise centros-resenas.json from that pack (the manual
     curation from 2026-07-10) wrapped as {"fuente": "curado", "centers": {...}}
     (keys starting with "_" are dropped, e.g. "_note").
  3. if neither exists, {"fuente": None, "centers": {}} — never a 404/500.

GET /api/admin/reports/searches?days=7|30|90 (solo_admin, 2026-07-22 —
"busquedas" used to live INSIDE the Management payload, see its own
"Searches" sub-report in admin-reports.js): -> {"dias": int, "top": [{"q",
"veces", "con_resultados"}, ...]} from store.top_searches(dias), cap ~300 rows,
ALREADY sorted by veces desc. It's just the raw log — CLUSTERING (queries
that resolve to the same thing, grouped by intent) runs client-side in
admin-reports.js, reusing search.js/SanaSearch (the production matcher)
against /api/services + /api/doctors, so the report's "best match" is the
same one the patient sees. Never empty by mistake: over an empty database
"top" is [].

------------------------------------------------------------------------------
CONTRACT — four new sub-reports (2026-07-24, Phase 2b of the "doctor
day-queue + reports expansion" plan, decision #10). All solo_admin, all
degrade gracefully over an empty base (never missing, never a 500). The page
= aggregates ONLY (report-design law 12: never name/DNI); each one's sibling
CSV is a DELIBERATE PII carve-out for the billing/management team — see the
comment in each _*_csv()'s code.

GET /api/admin/reports/cost-recovery?days=90 -> {"dias", "hero", "kpis", "por_obra_
social", "tendencia", "sin_dato"}. Universe: APPROVED consultations with a
turno_dt ALREADY PAST within the last `days` days (store.reportes_consultas_
realizadas — a window over turno_dt, not over created: this is a report of
"consultations attended", not "requests created"), joined against
usuarios.cobertura by dni (tolerates both jsonb shapes, see _insurers_of).
  kpis: {"consultas_totales", "consultas_con_cobertura", "pct_del_total",
  "pacientes_con_cobertura", "presentes", "ausentes"} — only for rows WITH
  at least one declared health-insurance provider.
  por_obra_social: [{"nombre", "consultas", "presentes", "pacientes"}, ...]
  sorted by consultas desc. A consultation for a patient with two insurance
  providers counts for BOTH (real cost recovery: either one can bill it) —
  "pacientes" is distinct DNI within that provider, not of the total.
  tendencia: [{"semana", "consultas"}] weekly (ISO Monday, ART time) ONLY
  for consultations with coverage (the billable volume over time).
  sin_dato: number of consultations in the period with NO insurance
  provider declared (includes prepaid-only and accounts with no coverage at
  all, even accounts that don't exist) — public cost recovery is managed
  against obra social, not against private prepaid coverage.

GET /api/admin/reports/cost-recovery.csv?days=90 -> CSV with utf-8-sig BOM,
columns fecha,dni,paciente,obra_social,nro_afiliado,servicio,especialidad,
centro,turno,asistencia — ALL consultations in the period (with or without
coverage: the billing office decides what to do with each row).
DELIBERATE PII CARVE-OUT: see the comment in _cost_recovery_csv_rows().

GET /api/admin/reports/sumar?days=90 -> {"dias", "hero", "kpis", "trazadoras",
"tendencia"}. Universe: same as cost-recovery, filtered to the population with
EXCLUSIVELY PUBLIC coverage — no obra_social AND no prepaid plan declared
(coverage absent/null counts the same: an account that never registered, or
that registered without loading coverage, is as much "exclusively public"
as one that explicitly declared it has nothing).
  kpis: {"prestaciones", "pacientes", "pct_del_total"} (pct over the total
  consultations attended in the period, with and without coverage).
  trazadoras: [{"clave": "embarazo"|"nino_sano"|"otros", "label",
  "consultas"}] in THAT fixed order. "embarazo" = service from the "gineco"
  cluster (Gynecology and obstetrics, the only cluster in that universe in
  the catalog); "nino_sano" = service from the "pediatria" cluster OR the
  patient's age at the time of the appointment < 10 years old (from
  solicitudes.nacimiento — covers NON-pediatric studies requested for a
  child, e.g. a lab test); "otros" = everything else.
  tendencia: same shape as cost-recovery, weekly over this population.

GET /api/admin/reports/sumar.csv?days=90 -> CSV with BOM, columns
fecha,dni,paciente,servicio,especialidad,centro,turno,asistencia,trazadora —
only the exclusively-public population. DELIBERATE PII CARVE-OUT, see
_sumar_csv_rows().

GET /api/admin/reports/statistics?mes=YYYY-MM -> {"mes", "hero", "kpis",
"por_especialidad", "edades", "por_centro"}. An invalid or missing `mes`
falls back to the last COMPLETE CALENDAR month in ART time (if today is any
day in July, the default is June). Universe: approved appointments with a
turno_dt inside that month (store.reports_statistics_rows, which
deliberately does NOT bring dni/name — this sub-report goes to the
statistics office, it never carries PII either on the page or in the CSV).
  kpis: {"consultas_totales", "presentes", "ausentes", "sin_marca",
  "pct_asistencia"} (pct over presentes+ausentes, None if none has a mark
  yet).
  por_especialidad: [{"cluster", "label", "consultas", "presentes",
  "ausentes", "sin_marca", "horas_ofrecidas", "consultas_por_hora"}].
  "horas_ofrecidas" = weekly hours from the LIVE catalog (_cluster_hours,
  TODAY's supply, not the historical month's — there's no way to know what
  schedule was in effect in a past month) × weeks in the month;
  "consultas_por_hora" is None if horas_ofrecidas is 0.
  edades: same shape as the "Patients" pyramid (_AGE_BUCKETS with F/M
  breakdown) but with age calculated as of the LAST DAY of the reported
  month (not "today" — a past month shouldn't show the patient's current
  age).
  por_centro: [{"centro", "consultas"}] sorted by consultas desc.

GET /api/admin/reports/statistics.csv?mes=YYYY-MM -> CSV with BOM, ONE row
per specialty (columns especialidad,consultas,presentes,ausentes,
sin_marca,horas_ofrecidas,consultas_por_hora) — aggregated, ZERO PII
possible (the source query doesn't bring dni/name).

GET /api/admin/reports/capacity (solo_admin, NO period — always looks at
"now") -> {"hero", "ocupacion", "demanda_semanal", "sin_oferta",
"temporada"}.
  ocupacion: [{"cluster", "label", "capacidad_14d", "ocupados_14d", "pct"}]
  for the next 14 days. capacidad_14d = real SLOTS walking each service's
  schedule (its own schedule if it has one, once per doctor it manages,
  otherwise its cluster+center doctors' — same fallback criterion as site_availability._week_hours, but in
  slots counting exceptions/vacations via
  doctor_appointments._slots_in_days, not in hours). ocupados_14d = approved
  appointments with turno_dt in those 14 days (store.list_approved_future).
  pct = None if capacidad_14d is 0 (sin_oferta already covers that
  situation separately).
  demanda_semanal: [{"semana", "solicitudes", "busquedas"}] for the last 8
  ISO weeks — CONVERTED demand (requests) and NOT converted (searches),
  store.reports_booking_requests_by_week/_busquedas_por_semana.
  sin_oferta: [{"cluster", "label", "solicitudes"}] — clusters with real
  demand (>=1 request in the last 90 days) and ZERO supply hours registered
  today (reuses _supply_demand(90) filtering sin_oferta=True).
  temporada: {"disponible": False} if TURNOS_SEASON_FACTOR isn't set (a
  seasonal factor is never invented without real data). If it is set:
  {"disponible": True, "factor", "proyeccion", "supuesto"} — "proyeccion" =
  demand observed over the last 8 weeks × factor, "supuesto" is the
  Spanish-language sentence the frontend prints VERBATIM (the factor is a
  declared operator assumption, never a measurement).
------------------------------------------------------------------------------
"""

import csv
import io
import json
import math
import os
import statistics
import datetime as _dt
from urllib.parse import parse_qs

from app.admin_panel import reports_store as store
from app.core import config
from app.core.format import resolved_by_text
from app.core import i18n
from app.patient_web import catalog
from app.patient_web import site_availability, site_catalog, site_centers
from app.core import sitedata
from app.core import text_fold
from app.modules.turnos import doctor_appointments
from app.core import tz as _tz

_PERIODS = (7, 30, 90)
_DEFAULT_DAYS = 30
_DEFAULT_DAYS_CSV = 90

# Mirror of admin.html:MOTIVOS_STD (the 7 closed reasons offered by the
# rejection select) — any motivo_rechazo that doesn't match exactly falls
# through here as free text and groups as "Otro motivo". If a new reason is
# added to the select, add it here too or the report will count it as Otro.
_STANDARD_REASONS = {
    "La orden está vencida.",
    "El documento subido no es una orden médica.",
    "La orden no se lee con claridad.",
    "La orden está a nombre de otra persona.",
    "El DNI de la orden no coincide con el de la cuenta.",
    "La orden no corresponde al servicio solicitado.",
    "Falta la firma o la matrícula del médico.",
}
def _other_reason_label():
    """"Otro motivo": ya no es una constante de módulo (se congelaba en
    español al importar, antes de que ningún request eligiera idioma — mismo
    bug ya visto en patient_web/site_*.py) — se resuelve por request."""
    return i18n.t_or("admin.srv.reports.motivo.otro", "Otro motivo", i18n.active())


_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)


def _plural(n, key_base, singular_es, plural_es):
    """Sustantivo en singular o plural según `n`, resuelto por i18n — evita
    tener una plantilla de oración distinta por idioma sólo por la
    concordancia (el inglés también flexiona, "request"/"requests")."""
    lang = i18n.active()
    if i18n.plural_category(n, lang) == "one":
        return i18n.t_or(key_base + "_singular", singular_es, lang)
    return i18n.t_or(key_base + "_plural", plural_es, lang)


def _days_param(query, default=_DEFAULT_DAYS):
    """Parse the native request's raw query string in one place."""
    qs = parse_qs(query)
    raw = (qs.get("days") or [str(default)])[0]
    try:
        d = int(raw)
    except (TypeError, ValueError):
        return default
    return d if d in _PERIODS else default


# ---------------------------------------------------------------- live catalog

def _cluster_label(cid, lang=None):
    c = site_catalog._cluster_by_id(cid) if cid else None
    if c and c.get("nombre"):
        return c["nombre"]
    return i18n.t_or("admin.srv.reports.cluster.sin_especialidad", "Sin especialidad", lang or i18n.active()) if not cid else cid


def _service_map():
    """servicio_id -> {"nombre","cluster"} over the live catalog (JSON +
    admin overlay) — a single pass, reused by _specialties() and the CSV."""
    out = {}
    for s in site_catalog._services():
        sid = s.get("id")
        if not sid:
            continue
        out[sid] = {
            "nombre": s.get("nombre") or s.get("top_label") or sid,
            "cluster": s.get("cluster") or None,
        }
    return out


def _center_map():
    return {c.get("slug"): c.get("name") or c.get("slug") for c in site_centers._centers()}


def _cluster_hours():
    """cluster -> weekly hours offered, summing site_availability._week_hours()
    over every service in the live catalog (same signal listado.js uses to
    sort by popularity — here it measures supply, not demand)."""
    total = {}
    for s in site_catalog._services():
        cid = s.get("cluster") or None
        if cid is None:
            continue
        total[cid] = total.get(cid, 0.0) + float(site_availability._week_hours(s) or 0)
    return total


# ---------------------------------------------------------------- GET /api/admin/reports

def _reports_payload(dias):
    """Pure domain function behind GET /api/admin/reports."""
    kpis = store.reports_kpis(dias)
    oferta_demanda = _supply_demand(dias)
    palancas = _levers(dias)
    funnel = _funnel(dias)
    lista_espera = _waitlist_report(dias)
    return {
        "dias": dias,
        "kpis": kpis,
        "especialidades": _specialties(dias),
        "rechazos": _rejections(dias),
        "volumen": store.reports_volume(dias),
        "hero": _hero(dias, oferta_demanda, kpis, palancas, funnel, lista_espera),
        "oferta_demanda": oferta_demanda,
        "heatmap": _heatmap(dias),
        "palancas": palancas,
        "funnel": funnel,
        "lista_espera": lista_espera,
        "ahorro": _savings(dias, funnel["recordatorios_enviados"]),
    }


def _specialties(dias):
    smap = _service_map()
    horas = _cluster_hours()

    conteo = {}
    for sid, cantidad in store.reports_booking_requests_by_service(dias):
        cid = (smap.get(sid) or {}).get("cluster") or "otras"
        conteo[cid] = conteo.get(cid, 0) + cantidad

    esperas = {}
    for sid, creada, turno_dt in store.reports_approved_appointment(dias):
        if creada is None or turno_dt is None:
            continue
        cid = (smap.get(sid) or {}).get("cluster") or "otras"
        esperas.setdefault(cid, []).append((turno_dt - creada).total_seconds() / 86400.0)

    out = []
    for cid in set(conteo) | set(horas):
        sol = conteo.get(cid, 0)
        h = horas.get(cid, 0.0)
        if sol == 0 and h <= 0:
            continue  # no demand and no supply registered: nothing to count
        sin_oferta = h <= 0 and sol > 0
        esp = esperas.get(cid) or []
        out.append({
            "cluster": cid,
            "nombre": _cluster_label(cid),
            "solicitudes": sol,
            "horas_semana": round(h, 1),
            "ratio": None if h <= 0 else round(sol / h, 2),
            "sin_oferta": sin_oferta,
            "espera_mediana_dias": round(statistics.median(esp), 1) if esp else None,
        })
    # Most over-demanded first: no supply registered comes first (infinite
    # demand relative to supply), then by ratio desc.
    out.sort(key=lambda r: (0 if r["sin_oferta"] else 1, -(r["ratio"] or 0), -r["solicitudes"]))
    return out


def _rejections(dias):
    agg = {}
    for motivo, cantidad in store.reports_rejections(dias):
        label = motivo if motivo in _STANDARD_REASONS else _other_reason_label()
        agg[label] = agg.get(label, 0) + cantidad
    out = [{"motivo": k, "cantidad": v} for k, v in agg.items()]
    out.sort(key=lambda r: -r["cantidad"])
    return out


# ---------------------------------------------------------- GET /api/admin/reports/searches

def _searches_payload(dias):
    """[{"q","veces","con_resultados"}] for the last `dias`, cap ~300 rows —
    the raw log that feeds the "Searches" sub-report (intent clustering runs
    client-side, see the module docstring)."""
    top = store.top_searches(dias)  # [(q, veces, con_resultados)], already sorted by veces desc
    return [{"q": q, "veces": veces, "con_resultados": cr} for (q, veces, cr) in top[:300]]


def _searches_full_payload(dias):
    return {"dias": dias, "top": _searches_payload(dias)}


def _strip_accents(s):
    """Lowercase + no accents, to keyword-match free text (rejection reasons,
    preferencia_horario) without depending on how each person typed it."""
    return text_fold.strip_nonspacing((s or "").lower())


# ---------------------------------------------------------- supply vs demand

def _aggregate_requests_and_waits(dias):
    """({servicio_id: cantidad}, {servicio_id: [espera_dias,...]}) — the
    atomic unit (per SERVICE) that _supply_demand summarizes both by service
    and by cluster (summing). Two small queries already used by
    _specialties; calling them again here is simpler than chaining caches
    between functions that can be requested independently (e.g. just the
    palancas tests, which don't even touch this)."""
    conteo = {}
    for sid, cantidad in store.reports_booking_requests_by_service(dias):
        conteo[sid] = conteo.get(sid, 0) + cantidad
    esperas = {}
    for sid, creada, turno_dt in store.reports_approved_appointment(dias):
        if creada is None or turno_dt is None:
            continue
        esperas.setdefault(sid, []).append((turno_dt - creada).total_seconds() / 86400.0)
    return conteo, esperas


def _median_ratio(filas):
    ratios = [f["solicitudes_por_hora"] for f in filas if f["solicitudes_por_hora"] is not None]
    return round(statistics.median(ratios), 2) if ratios else None


# Below this many requests in the period the ratio rests on too little to advise hours (a handful of
# requests would read as «add 40 hours»).
MIN_SOLICITUDES_SUGERENCIA = 20


def _hours_suggestion(fila, equilibrio):
    """Estimated EXTRA weekly hours for this row to come down to the
    period's equilibrium ratio (median of solicitudes_por_hora). None if
    it's already at or below it — there's nothing to suggest, and supply is
    never subtracted — or if the row has fewer than MIN_SOLICITUDES_SUGERENCIA
    requests in the period."""
    if not equilibrio or equilibrio <= 0:
        return None
    if fila["solicitudes"] < MIN_SOLICITUDES_SUGERENCIA:
        return None
    horas = fila["horas_semana"] or 0.0
    objetivo = fila["solicitudes"] / equilibrio
    extra = objetivo - horas
    return round(extra, 1) if extra > 0.05 else None


def _supply_demand(dias):
    """Rows for the "requests vs hours offered" scatter — by cluster AND by
    service (only services with >=1 request in the period), plus the
    period's median ratio (the scatter's reference) and an extra-hours
    suggestion per row. See the contract in the module docstring."""
    smap = _service_map()
    cmap = _center_map()
    horas_cluster = _cluster_hours()
    conteo_srv, esperas_srv = _aggregate_requests_and_waits(dias)

    conteo_cluster, esperas_cluster = {}, {}
    for sid, cantidad in conteo_srv.items():
        cid = (smap.get(sid) or {}).get("cluster") or "otras"
        conteo_cluster[cid] = conteo_cluster.get(cid, 0) + cantidad
    for sid, dias_espera in esperas_srv.items():
        cid = (smap.get(sid) or {}).get("cluster") or "otras"
        esperas_cluster.setdefault(cid, []).extend(dias_espera)

    clusters = []
    for cid in set(conteo_cluster) | set(horas_cluster):
        sol = conteo_cluster.get(cid, 0)
        h = horas_cluster.get(cid, 0.0)
        if sol == 0 and h <= 0:
            continue
        esp = esperas_cluster.get(cid) or []
        clusters.append({
            "id": cid,
            "nombre": _cluster_label(cid),
            "centro": None,
            "horas_semana": round(h, 1),
            "solicitudes": sol,
            "solicitudes_por_hora": None if h <= 0 else round(sol / h, 2),
            "espera_mediana_dias": round(statistics.median(esp), 1) if esp else None,
            "sin_oferta": h <= 0 and sol > 0,
        })

    servicios = []
    for s in site_catalog._services():
        sid = s.get("id")
        sol = conteo_srv.get(sid, 0)
        if sol <= 0:
            continue  # only services with real demand in the period
        h = float(site_availability._week_hours(s) or 0)
        esp = esperas_srv.get(sid) or []
        servicios.append({
            "id": sid,
            "nombre": s.get("nombre") or s.get("top_label") or sid,
            "centro": cmap.get(s.get("centro")),
            "horas_semana": round(h, 1),
            "solicitudes": sol,
            "solicitudes_por_hora": None if h <= 0 else round(sol / h, 2),
            "espera_mediana_dias": round(statistics.median(esp), 1) if esp else None,
            "sin_oferta": h <= 0,
        })

    equilibrio = _median_ratio(clusters + servicios)
    for fila in clusters:
        fila["sugerencia_horas"] = _hours_suggestion(fila, equilibrio)
    for fila in servicios:
        fila["sugerencia_horas"] = _hours_suggestion(fila, equilibrio)

    clusters.sort(key=lambda r: (0 if r["sin_oferta"] else 1, -(r["solicitudes_por_hora"] or 0), -r["solicitudes"]))
    servicios.sort(key=lambda r: (0 if r["sin_oferta"] else 1, -(r["solicitudes_por_hora"] or 0), -r["solicitudes"]))
    return {"clusters": clusters, "servicios": servicios, "equilibrio": equilibrio}


# --------------------------------------------------------------------- hero
#
# Editorial angle (the product owner, 2026-07-20 — see "Editorial angle" in
# .claude/skills/report-design/SKILL.md): this report's reader is the
# management of a public health system, not a patient comparing comfort. The
# medical/administrative team is the hero of the data — a finding is never
# narrated as staff failure, always as a burden that can be lifted off them
# (automation, upfront controls, better schedule fit). The report opens on
# a positive note (what the system delivered in the period) and only then
# points to the best next hour of investment. "critico" stays RESERVED for
# clusters with zero real supply hours (a genuine capacity gap) — never for
# the movement of the wait, which under triage is the system working as it
# should.

def _num_es(x, nd=1):
    """A number of the report copy through the one formatter: thousands
    grouped, at most `nd` decimals, never a trailing zero («115,3», «0,44»,
    «1.466»), in the active language's digits."""
    if x is None:
        return "s/d"
    return i18n.format_number(x, nd) or "0"


def _pct_es(x):
    """The digits of a percentage whose copy carries its own sign
    («{pct}% del total»): the same rule as every percentage of the panel —
    a decimal only under 10 or just short of 100."""
    if x is None:
        return "s/d"
    return (i18n.format_percent(x) or "0%").rstrip("%")


def _hero_lead(funnel, oferta_demanda, lista_espera):
    """First sentence: ALWAYS what the system delivered in the period (it
    never opens with a problem) — requests resolved, schedule hours the
    team made available, freed-up appointments that got recovered."""
    lang = i18n.active()
    # Every request that was given an appointment (db_reports._ever_approved): the step «Les dieron
    # turno» of the funnel below and of Recorrido, with the default step order.
    aprobadas = funnel.get("con_turno") or 0
    if not aprobadas:
        return None
    solicitud = _plural(aprobadas, "admin.srv.reports.noun.solicitud", "solicitud", "solicitudes")
    texto = i18n.t_or("admin.srv.reports.hero.lead", "El equipo resolvió {n} {solicitud} en el período",
                       lang, n=_num_es(aprobadas, 0), solicitud=solicitud)
    horas_total = sum(r["horas_semana"] for r in oferta_demanda["clusters"])
    recuperados = lista_espera.get("turnos_recuperados") or 0
    colas = []
    if horas_total > 0:
        # half-up like the shown {horas} (round() would say «1 horas» for 0,5)
        colas.append(i18n.tn_or("admin.srv.reports.hero.lead_horas", math.floor(horas_total + 0.5),
                                "con {horas} hora semanal de agenda ofrecida",
                                "con {horas} horas semanales de agenda ofrecidas", lang, horas=_num_es(horas_total, 0)))
    if recuperados > 0:
        turno = _plural(recuperados, "admin.srv.reports.noun.turno", "turno", "turnos")
        colas.append(i18n.t_or("admin.srv.reports.hero.lead_recuperados",
                                "recuperando {n} {turno} que se habían liberado", lang,
                                n=_num_es(recuperados, 0), turno=turno))
    if colas:
        joiner = i18n.t_or("admin.srv.reports.hero.lead_join", " y ", lang)
        texto += ", " + joiner.join(colas)
    return {"texto": texto + ".", "tono": "ok"}


def _hero_investment(oferta_demanda):
    """Second sentence: where the team's next hour pays off most. A cluster
    with zero real supply is the only situation named "critico" (a genuine
    capacity gap, not a complaint); an overloaded cluster that DOES have
    supply gets named as an investment opportunity, "atencion" tone."""
    candidatos = [r for r in oferta_demanda["clusters"] if r["solicitudes"] >= MIN_SOLICITUDES_SUGERENCIA]
    if not candidatos:
        return None
    lang = i18n.active()
    candidatos.sort(key=lambda r: (0 if r["sin_oferta"] else 1, -(r["solicitudes_por_hora"] or 0)))
    top = candidatos[0]
    if top["sin_oferta"]:
        return {
            "texto": i18n.tn_or(
                "admin.srv.reports.hero.investment_critico", top["solicitudes"],
                "{nombre} concentró {n} solicitud sin ninguna hora de agenda asignada todavía: ahí es donde "
                "una hora más del equipo rinde más.",
                "{nombre} concentró {n} solicitudes sin ninguna hora de agenda asignada todavía: ahí es donde "
                "una hora más del equipo rinde más.",
                lang, nombre=top["nombre"]),
            "tono": "critico",
        }
    equilibrio = oferta_demanda.get("equilibrio")
    extra = top.get("sugerencia_horas")
    if top["solicitudes_por_hora"] and equilibrio and top["solicitudes_por_hora"] > equilibrio * 1.3 and extra:
        # With a small schedule and heavy demand, the equilibrium math can
        # yield an impossible number ("add 132 weekly hours") — beyond a
        # reasonable reinforcement, the sentence stops prescribing hours and
        # names the pressure instead (round 2026-07-22, "weird text").
        if extra > 40:
            return {
                "texto": i18n.t_or(
                    "admin.srv.reports.hero.investment_lejos",
                    "{nombre} recibe {ratio} solicitudes por hora ofrecida, lejos del equilibrio del período "
                    "({equilibrio}): la demanda excede lo que un refuerzo puntual puede cubrir y conviene revisar "
                    "cuánta puede absorber otra agenda o una derivación distinta.",
                    lang, nombre=top["nombre"], ratio=_num_es(top["solicitudes_por_hora"], 2),
                    equilibrio=_num_es(equilibrio, 2)),
                "tono": "atencion",
            }
        return {
            "texto": i18n.t_or(
                "admin.srv.reports.hero.investment_inversion",
                "Sumar {extra} horas semanales a {nombre} es la inversión de mayor impacto del período: hoy "
                "sostiene {ratio} solicitudes por hora ofrecida.",
                lang, extra=_num_es(extra), nombre=top["nombre"], ratio=_num_es(top["solicitudes_por_hora"], 2)),
            "tono": "atencion",
        }
    return None


def _hero_wait(dias, kpis):
    """Filler sentence: the movement of the median days from the request to
    its turno (the same span as the Gestión tile of that name — not how long
    a pending request has gone unanswered). Steady or shorter is "ok"; a
    longer wait is "atencion" (analytics review 13.11: it is not the norm),
    worded without blame — under triage it is the system prioritizing what's
    urgent."""
    lang = i18n.active()
    actual = kpis.get("mediana_dias_turno")
    previa = store.reports_median_wait_prior(dias)
    if actual is None or previa is None:
        return None
    texto = i18n.t_or("admin.srv.reports.hero.wait_base",
                       "Del pedido al turno pasaron {actual} días (mediana) en el período",
                       lang, actual=_num_es(actual))
    if abs(actual - previa) >= 0.5:
        texto += i18n.t_or("admin.srv.reports.hero.wait_previous",
                            ", frente a {previa} días en el período anterior", lang, previa=_num_es(previa))
    texto += i18n.t_or("admin.srv.reports.hero.wait_suffix",
                        ": la prioridad clínica ordena primero los casos más urgentes.", lang)
    return {"texto": texto, "tono": "atencion" if actual - previa >= 0.5 else "ok"}


def _hero(dias, oferta_demanda, kpis, palancas, funnel, lista_espera):
    """1 to 3 computed sentences (never constants) to open the report —
    report-design law 1: "the dashboard should first say what it wants to
    say and then prove it with data". Priority order: the period's delivery
    (positive, almost always present) > best next hour of investment >
    review time given back to the team by avoiding rejections > movement of
    the wait (neutral filler). Falls back to a neutral sentence if none
    applies."""
    candidatas = [
        _hero_lead(funnel, oferta_demanda, lista_espera),
        _hero_investment(oferta_demanda),
        ({"texto": palancas["hero"], "tono": "atencion"}
         if next((g for g in palancas["grupos"] if g["id"] == "prevenible_antes" and g["n"] >= 3), None)
         else None),
        _hero_wait(dias, kpis),
    ]
    frases = [c for c in candidatas if c][:3]
    if not frases:
        frases.append({
            "texto": i18n.t_or("admin.srv.reports.hero.sin_datos",
                                "Todavía no hay suficientes datos en este período para identificar un patrón claro.",
                                i18n.active()),
            "tono": "ok",
        })
    return frases


# ----------------------------------------------------------------- heatmap

# Claves de date.json (namespace ya traducido por otra unidad) en la
# convención JS Date.getDay() (0=domingo..6=sábado). NO se resuelve en una
# constante de módulo: eso se congelaría en español al importar, antes de
# que ningún request eligiera idioma (mismo bug ya visto en patient_web/site_*.py).
_WEEKDAY_KEYS = ("date.day.sun", "date.day.mon", "date.day.tue", "date.day.wed",
                  "date.day.thu", "date.day.fri", "date.day.sat")


def _weekday_names(lang=None):
    lang = lang or i18n.active()
    return [i18n.t(k, lang).lower() for k in _WEEKDAY_KEYS]


_HEATMAP_HOUR_START = 7
_HEATMAP_HOUR_END = 21  # inclusive
_HOUR_BANDS = {"manana": range(8, 12), "tarde": range(14, 18)}
# A band preference (no online schedule) doesn't say WHICH day — it's spread
# evenly only across business days (the hospital's catalog doesn't offer
# weekend bands): never inventing a day the patient never said.
_BUSINESS_DAYS_JS = (1, 2, 3, 4, 5)


def _hour_band(h, lang=None):
    lang = lang or i18n.active()
    if h < 12:
        return i18n.t_or("admin.srv.reports.band.manana", "mañana", lang)
    if h < 18:
        return i18n.t_or("admin.srv.reports.band.tarde", "tarde", lang)
    return i18n.t_or("admin.srv.reports.band.noche", "noche", lang)


def _weekday_js_from_iso(diso):
    """YYYY-MM-DD -> JS Date.getDay() convention (0=Sunday..6=Saturday, the
    same one hours.mjs/platform/week.mjs and patient_web/site_*.py use: see
    docs/solutions/dev/dias-getday-convention.md)."""
    try:
        d = _dt.date.fromisoformat(str(diso)[:10])
    except (ValueError, TypeError):
        return None
    return (d.weekday() + 1) % 7


def _weekday_hour_art(dt):
    """(dia_js, hora) in Buenos Aires time from a timestamptz — NEVER trust
    the process TZ (same criterion as calendar_feed.py/
    reports_volume)."""
    if dt is None:
        return None, None
    try:
        local = dt.astimezone(_ART) if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc).astimezone(_ART)
    except (ValueError, OverflowError, OSError):
        return None, None
    return (local.weekday() + 1) % 7, local.hour


def _band_from_preference(pref):
    """The band is ALWAYS the first segment before " · " (service.js:
    commit() builds "A la mañana|A la tarde|Cualquier horario" + " · " +
    desdeLabel()). WATCH OUT: checking the whole string is a bug —
    desdeLabel() can append "desde mañana" ("mañana" = CALENDAR tomorrow,
    "tomorrow") onto a preference that's actually "A la tarde" (afternoon),
    and "manana" would win the check for coming first. Only the first
    segment is the time band; whatever comes after " · " is the "starting
    from" date, not the hour."""
    primera = (pref or "").split(" · ")[0]
    t = _strip_accents(primera)
    if "manana" in t:
        return "manana"
    if "tarde" in t:
        return "tarde"
    return None


def _supply_grid():
    """oferta[dia_js][hora] = how many services/doctors have a REAL schedule
    covering that hour — live catalog, doesn't depend on the period (same
    sources as site_availability._week_hours: the service's own schedule + each
    doctor's schedule/bands)."""
    grid = [[0] * 24 for _ in range(7)]
    fuentes = []
    for s in site_catalog._services():
        if s.get("agenda"):
            fuentes.append(s["agenda"])
    for m in site_catalog._doctors():
        if m.get("agenda"):
            fuentes.append(m["agenda"])
        for c in (m.get("centros") or []):
            for fr in (c.get("franjas") or []):
                fuentes.append({"franjas": [fr]})
    for ag in fuentes:
        if not isinstance(ag, dict):
            continue
        franjas = ag.get("franjas") or ([ag] if ag.get("dias") else [])
        for fr in franjas:
            try:
                dh = int(str(fr.get("desde") or "0:00").split(":")[0])
                hh = int(str(fr.get("hasta") or "0:00").split(":")[0])
            except (ValueError, TypeError):
                continue
            for d in (fr.get("dias") or []):
                if not isinstance(d, int) or not (0 <= d <= 6):
                    continue
                for h in range(max(0, dh), min(24, max(dh, hh))):
                    grid[d][h] += 1
    return grid


def _demand_grid(dias):
    """demanda[dia_js][hora] = appointment-request pressure in the period —
    the union of turno_dt (approved), turno_holds (chosen, taken or not) and
    preferencia_horario (band, spread out). See the module docstring."""
    grid = [[0.0] * 24 for _ in range(7)]
    for _sid, _creada, turno_dt in store.reports_approved_appointment(dias):
        wd, hh = _weekday_hour_art(turno_dt)
        if wd is None:
            continue
        grid[wd][hh] += 1.0
    for dia, hora in store.reports_holds_day_hour(dias):
        wd = _weekday_js_from_iso(dia)
        if wd is None:
            continue
        try:
            hh = int(str(hora).split(":")[0])
        except (ValueError, TypeError):
            continue
        if 0 <= hh <= 23:
            grid[wd][hh] += 1.0
    for pref, _creada in store.reports_time_preference(dias):
        banda = _band_from_preference(pref)
        if not banda:
            continue
        horas = _HOUR_BANDS[banda]
        peso = 1.0 / (len(_BUSINESS_DAYS_JS) * len(horas))
        for d in _BUSINESS_DAYS_JS:
            for h in horas:
                grid[d][h] += peso
    return grid


def _heatmap(dias):
    lang = i18n.active()
    nombres_dia = _weekday_names(lang)
    oferta = _supply_grid()
    demanda = _demand_grid(dias)
    horas = list(range(_HEATMAP_HOUR_START, _HEATMAP_HOUR_END + 1))
    oferta_out = [[oferta[d][h] for h in horas] for d in range(7)]
    demanda_out = [[round(demanda[d][h], 1) for h in horas] for d in range(7)]

    celdas = []
    for d in range(7):
        for h in horas:
            diff = demanda[d][h] - oferta[d][h]
            if diff > 0:
                celdas.append((diff, d, h))
    celdas.sort(key=lambda x: -x[0])
    desajuste = [
        {
            "dia": nombres_dia[d],
            "hora": h,
            "oferta": oferta[d][h],
            "demanda": round(demanda[d][h], 1),
            "diferencia": round(diff, 1),
            "label": i18n.t_or("admin.srv.reports.heatmap.label", "{dia} a la {franja}", lang,
                                dia=nombres_dia[d].capitalize(), franja=_hour_band(h, lang)),
        }
        for diff, d, h in celdas[:5]
    ]
    return {"dias": nombres_dia, "horas": horas, "oferta": oferta_out, "demanda": demanda_out, "desajuste": desajuste}


# --------------------------------------------------------- levers (rejections)

# The 7 standard reasons (admin.html:MOTIVOS_STD, mirrored in
# _STANDARD_REASONS above) are ALL avoidable with better controls BEFORE
# sending the request (expired/wrong/illegible order, filed under someone
# else's name, DNI that doesn't match, doesn't match the service, missing
# signature/license) — the wizard could have flagged it before it ever
# reached the queue.
_CAPACITY_KEYWORDS = ("no hay turno", "sin cupo", "sin disponibilidad", "agenda completa",
                 "no hay disponibilidad", "sin horarios", "no hay horarios", "sin agenda",
                 "sin turnos disponibles", "falta de cupo")

# Deliberately short: these are treemap tile labels — a long name gets
# truncated mid-word ("Evitable con mejores control...", round 2026-07-22)
# and the tile stops being readable. Función, no diccionario de módulo: se
# congelaría en español al importar, antes de que ningún request eligiera
# idioma (mismo bug ya visto en patient_web/site_*.py).
def _group_labels(lang=None):
    lang = lang or i18n.active()
    return {
        "prevenible_antes": i18n.t_or("admin.srv.reports.group.prevenible_antes",
                                       "Evitable antes de enviar", lang),
        "capacidad": i18n.t_or("admin.srv.reports.group.capacidad",
                                "Falta de capacidad o agenda", lang),
        "otro": _other_reason_label(),
    }


_GROUP_ORDER = ("prevenible_antes", "capacidad", "otro")


def _classify_reason(motivo):
    if motivo in _STANDARD_REASONS:
        return "prevenible_antes"
    t = _strip_accents(motivo)
    if any(k in t for k in _CAPACITY_KEYWORDS):
        return "capacidad"
    return "otro"


def _levers(dias):
    """Rejections as an improvement LEVER: groups motivo_rechazo into
    avoidable-before-sending / lack-of-capacity / other (see the module
    contract). Unlike _rejections (which collapses everything non-standard
    into "Otro motivo" for the simple ranking), here the free text is
    scanned word by word to separate "sin cupo" (no slots) from a genuinely
    different reason.

    Editorial framing (2026-07-20): an avoidable rejection isn't "the
    patient got it wrong" — it's a full review cycle the team had to redo
    (review, reject, wait for resubmission, review again). This section's
    hero names it as review time that can be given back to the team with
    better controls when the order is uploaded, not as patient friction."""
    lang = i18n.active()
    etiquetas = _group_labels(lang)
    sin_motivo = i18n.t_or("admin.srv.reports.motivo.sin_registrar", "Sin motivo registrado", lang)
    crudo = store.reports_rejections(dias)  # [(motivo, cantidad)]
    total = sum(c for _, c in crudo)

    por_grupo = {g: {} for g in _GROUP_ORDER}
    for motivo, cantidad in crudo:
        g = _classify_reason(motivo)
        m = motivo or sin_motivo
        por_grupo[g][m] = por_grupo[g].get(m, 0) + cantidad

    grupos = []
    for g in _GROUP_ORDER:
        n = sum(por_grupo[g].values())
        motivos = sorted(
            ({"motivo": m, "n": c} for m, c in por_grupo[g].items()),
            key=lambda r: -r["n"],
        )
        grupos.append({
            "id": g,
            "nombre": etiquetas[g],
            "n": n,
            "pct": round(100.0 * n / total, 1) if total else None,
            "motivos": motivos,
        })
    grupos.sort(key=lambda r: -r["n"])

    prevenible = next((g for g in grupos if g["id"] == "prevenible_antes"), None)
    if not total:
        hero = i18n.t_or("admin.srv.reports.hero.levers_none",
                          "Todavía no hubo rechazos que revisar en este período.", lang)
    elif prevenible and prevenible["n"]:
        hero = i18n.t_or(
            "admin.srv.reports.hero.levers_prevenible",
            "El {pct}% de los rechazos de este período le costó al equipo un ciclo extra de revisión que se "
            "podía evitar: mejores controles al subir la orden liberan ese tiempo para atender.",
            lang, pct=_pct_es(prevenible['pct']))
    else:
        hero = i18n.t_or(
            "admin.srv.reports.hero.levers_ok",
            "Los rechazos de este período no se concentran en causas evitables antes del envío: la revisión "
            "del equipo ya está bien dirigida.",
            lang)
    return {"grupos": grupos, "hero": hero}


# ---------------------------------------------------------------- funnel

def _funnel(dias):
    # Imported here: reports_funnel imports this module at load time.
    from app.admin_panel import reports_funnel
    f = store.reports_funnel(dias)
    recorrido = reports_funnel.summary(dias)
    return {
        "pasos": recorrido["pasos"],
        "frases": recorrido["frases"],
        "con_turno": recorrido["aprobadas"],
        "holds_creados": f["holds_creados"],
        "holds_convertidos_en_solicitud": f["holds_convertidos_en_solicitud"],
        "solicitudes": f["solicitudes"],
        "aprobadas": f["aprobadas"],
        "rechazadas": f["rechazadas"],
        "canceladas": f["canceladas"],
        "pendientes": f["pendientes"],
        "asistencia": {"presente": f["presente"], "ausente": f["ausente"], "sin_dato": f["sin_dato_asistencia"]},
        "recordatorios_enviados": f["recordatorios_enviados"],
    }


# ------------------------------------------------------------ waitlist

def _waitlist_report(dias):
    counts = dict(store.reports_appointment_offers(dias))
    ofrecidas = counts.get("ofrecida", 0)
    aceptadas = counts.get("aceptada", 0)
    expiradas = counts.get("expirada", 0)
    vacantes = counts.get("vacante", 0)
    return {
        "ofertas_enviadas": ofrecidas + aceptadas + expiradas + vacantes,
        "aceptadas": aceptadas,
        "expiradas": expiradas,
        # The schema doesn't distinguish an explicit rejection from a
        # timeout expiry (waitlist.reject_offer also leaves 'expirada') —
        # never inventing a number the database can't back up.
        "rechazadas": None,
        "turnos_recuperados": aceptadas,
    }


# ---------------------------------------------------------- estimated savings

_MIN_ANALYZE_REFERRAL_MANUAL = 4.0   # operator reading/transcribing/validating the order by hand
_MIN_ANALYZE_REFERRAL_AI = 0.5       # reviewing the AI's prefill
_MIN_OCR = 2.0                     # manual entry of the DNI data (per side)
_MIN_PUCO = 2.0                    # asking/typing the insurance provider by hand
_MIN_REMINDER = 3.0            # manual call or message the day before
_DEFAULT_HOUR_COST_ARS = 6000.0   # municipal administrative hour, 2026 estimate (with payroll taxes)
_DEFAULT_BLUE_DOLLAR_ARS = 1300.0   # only to compare USD spend, NOT a live quote


def _float_env(key, default):
    v = config.get(key)
    if not v:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _has_puco(cobertura):
    if not isinstance(cobertura, dict):
        return False
    obras = cobertura.get("obras_sociales")
    if isinstance(obras, list) and any(isinstance(o, dict) and o.get("origen") == "puco" for o in obras):
        return True
    singular = cobertura.get("obra_social")
    return isinstance(singular, dict) and singular.get("origen") == "puco"


def _savings(dias, recordatorios_enviados):
    """Estimate of hours the automation GIVES BACK to the team (not a cost
    cut): every minute the AI/PUCO/automatic reminders avoid typing by hand
    is one more minute of coverage to see patients — framed that way in
    "supuestos", not as money saved (see "Editorial angle" in
    .claude/skills/report-design/SKILL.md)."""
    if not store.ai_costs_has_rows():
        return {"disponible": False}

    resumen = store.costs_summary(dias)
    por_op = {r["operacion"]: r for r in store.costs_by_operation(dias)}
    reconocidas = lambda op: (por_op.get(op) or {}).get("reconocidas") or 0  # noqa: E731

    puco_encontradas = sum(1 for c in store.reports_puco_found(dias) if _has_puco(c))

    minutos = (
        (_MIN_ANALYZE_REFERRAL_MANUAL - _MIN_ANALYZE_REFERRAL_AI) * reconocidas("analizar_orden")
        + _MIN_OCR * (reconocidas("ocr_frente") + reconocidas("ocr_dorso"))
        + _MIN_PUCO * puco_encontradas
        + _MIN_REMINDER * recordatorios_enviados
    )

    costo_hora_ars = _float_env("TURNOS_COSTO_HORA_OPERADOR", _DEFAULT_HOUR_COST_ARS)
    dolar_ars = _float_env("TURNOS_DOLAR_BLUE_REPORTE", _DEFAULT_BLUE_DOLLAR_ARS)
    horas = minutos / 60.0
    ahorro_ars = horas * costo_hora_ars
    gasto_usd = float(resumen.get("total_usd") or 0.0)
    gasto_ars = gasto_usd * dolar_ars

    return {
        "disponible": True,
        "gasto_usd": round(gasto_usd, 4),
        "operaciones": int(resumen.get("operaciones") or 0) + puco_encontradas + recordatorios_enviados,
        "minutos_ahorrados": round(minutos, 1),
        "horas_operador": round(horas, 1),
        "costo_hora_ars": costo_hora_ars,
        "ahorro_ars": round(ahorro_ars, 0),
        "roi": round(ahorro_ars / gasto_ars, 1) if gasto_ars > 0 else None,
        "supuestos": _savings_assumptions(costo_hora_ars, dolar_ars),
    }


def _savings_assumptions(costo_hora_ars, dolar_ars):
    lang = i18n.active()
    return [
        i18n.t_or(
            "admin.srv.reports.savings.assumption_ai",
            "Análisis de orden con IA reconocida: le devuelve al equipo {ahorro} min por caso (antes "
            "leía/transcribía/validaba la orden a mano ≈{manual} min; ahora revisa el prellenado de la IA "
            "≈{ai} min).",
            lang,
            ahorro=f"{_MIN_ANALYZE_REFERRAL_MANUAL - _MIN_ANALYZE_REFERRAL_AI:.1f}",
            manual=f"{_MIN_ANALYZE_REFERRAL_MANUAL:.0f}", ai=f"{_MIN_ANALYZE_REFERRAL_AI:.1f}"),
        i18n.t_or(
            "admin.srv.reports.savings.assumption_ocr",
            "OCR del DNI (frente o dorso) reconocido: le devuelve {min} min por lectura que antes se cargaban "
            "a mano.",
            lang, min=f"{_MIN_OCR:.0f}"),
        i18n.t_or(
            "admin.srv.reports.savings.assumption_puco",
            "Consulta PUCO con cobertura encontrada al registrarse: le devuelve {min} min por cuenta que "
            "antes se preguntaban/tipeaban a mano.",
            lang, min=f"{_MIN_PUCO:.0f}"),
        i18n.t_or(
            "admin.srv.reports.savings.assumption_reminder",
            "Recordatorio automático del día anterior: le devuelve {min} min por turno que antes eran una "
            "llamada o mensaje manual.",
            lang, min=f"{_MIN_REMINDER:.0f}"),
        i18n.t_or(
            "admin.srv.reports.savings.assumption_hour_cost",
            "Costo de la hora administrativa municipal usado para traducir ese tiempo en horas de cobertura: "
            "AR$ {costo} (estimación 2026).",
            lang, costo=_num_es(costo_hora_ars, 0)),
        i18n.t_or(
            "admin.srv.reports.savings.assumption_dollar",
            "Dólar usado sólo para comparar el gasto de IA en USD contra las horas devueltas: AR$ {dolar} "
            "(no es una cotización en vivo).",
            lang, dolar=_num_es(dolar_ars, 0)),
        i18n.t_or(
            "admin.srv.reports.savings.assumption_reconocidas",
            "Sólo cuentan las llamadas de IA marcadas 'reconocidas' (sacaron algo útil): un documento "
            "ilegible no libera tiempo, el equipo lo revisa igual — sin que eso penalice a nadie.",
            lang),
    ]


# ------------------------------------------------------------------ reviews

def _reviews_payload():
    p = sitedata.path("resenas-analisis.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            data = dict(data)
            data["fuente"] = "analisis"
            return data

    centers = {}
    p2 = sitedata.path("centros-resenas.json")
    if os.path.exists(p2):
        try:
            with open(p2, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            centers = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    return {"fuente": "curado" if centers else None, "centers": centers}


# ---------------------------------------------------------------- CSV export

def _initials(nombre, apellido):
    n = (nombre or "").strip()
    a = (apellido or "").strip()
    out = "".join(x[0].upper() + "." for x in (n, a) if x)
    return out or "—"


def _fmt_dt(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        v = v.astimezone(_ART)
    except (ValueError, OverflowError, OSError):
        pass
    return v.strftime("%d/%m/%Y %H:%M")


_REQUESTS_CSV_HEADER = ["fecha", "paciente", "servicio", "especialidad", "centro", "estado",
                        "motivo_rechazo", "resuelta_por", "resuelta_en", "turno"]
_REQUESTS_CSV_FILENAME = "solicitudes.csv"


def _requests_csv_rows(dias):
    rows = store.reports_export_rows(dias)
    smap = _service_map()
    cmap = _center_map()
    out = []
    nombres = doctor_appointments.resolver_names(r.get("resuelta_por") for r in rows)
    for r in rows:
        sid = r.get("servicio_id")
        info = smap.get(sid) or {}
        out.append([
            _fmt_dt(r.get("created")),
            _initials(r.get("nombre"), r.get("apellido")),
            info.get("nombre") or sid or "",
            _cluster_label(info.get("cluster"), lang="es") if info.get("cluster") else "",
            cmap.get(r.get("centro_slug"), r.get("centro_slug") or ""),
            r.get("estado") or "",
            r.get("motivo_rechazo") or "",
            resolved_by_text(r.get("resuelta_por"), nombres.get(r.get("resuelta_por")), short=True),
            _fmt_dt(r.get("resuelta_en")),
            _fmt_dt(r.get("turno_dt")),
        ])
    return out


def _csv_bytes(header, rows):
    """utf-8-sig-encoded CSV body — Excel on Windows/AR opens accented
    characters correctly without asking about encoding."""
    buf = io.StringIO()
    # A cell that starts like a spreadsheet formula gets the apostrophe (security review 2026-11: a patient's
    # name «=HYPERLINK(...)» ran in the coordinator's Excel); the same guard as the table downloads.
    from app.admin_panel import table_export
    w = csv.writer(buf)
    w.writerow([table_export.safe_cell(c) for c in header])
    for r in rows:
        w.writerow([table_export.safe_cell(c) for c in r])
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- GET /api/admin/reports/patients

_AGE_BUCKETS = (
    (0, 17, "0–17"),
    (18, 29, "18–29"),
    (30, 44, "30–44"),
    (45, 59, "45–59"),
    (60, 74, "60–74"),
    (75, None, "75+"),
)
_TOP_INSURERS_N = 10


def _age_of(nacimiento, hoy):
    """Full years old as of `hoy`. None if the data isn't usable (outside a
    reasonable human range — a bad data entry shouldn't dirty the bucket)."""
    if nacimiento is None:
        return None
    a = hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))
    return a if 0 <= a < 130 else None


def _age_bucket(edad):
    for lo, hi, label in _AGE_BUCKETS:
        if edad >= lo and (hi is None or edad <= hi):
            return label
    return None


def _age_distribution():
    """Age buckets broken down by sex (F/M; X or no data only add to the
    total) — feed the "Patients" sub-report's population pyramid (round
    2026-07-22: a single-dimension chart doesn't tell any story; the age×sex
    pyramid does). No PII: aggregated counts."""
    return _age_distribution_of(store.reports_patients_ages(), _dt.date.today())


def _insurers_of(cobertura):
    """Insurance provider names for ONE account, tolerating both jsonb
    shapes (see the docstring of store.reports_patients_coverage)."""
    if not isinstance(cobertura, dict):
        return []
    obras = cobertura.get("obras_sociales")
    if isinstance(obras, list):
        return [o["nombre"].strip() for o in obras
                if isinstance(o, dict) and isinstance(o.get("nombre"), str) and o["nombre"].strip()]
    singular = cobertura.get("obra_social")
    if isinstance(singular, dict) and isinstance(singular.get("nombre"), str) and singular["nombre"].strip():
        return [singular["nombre"].strip()]
    if isinstance(singular, str) and singular.strip():
        return [singular.strip()]
    return []


def _has_private_plan(cobertura):
    if not isinstance(cobertura, dict):
        return False
    p = cobertura.get("prepaga")
    if isinstance(p, dict):
        return bool(p.get("nombre"))
    if isinstance(p, str):
        return bool(p.strip())
    return False


def _insurer_key(nombre):
    """The insurer as one row: case, accents and runs of spaces do not tell «IOMA» from «ioma »
    nor «Medifé» from «Medife»."""
    return " ".join(text_fold.plain(nombre).split())


def _insurer_entries(cobertura):
    """[(key, spelling)] of ONE account: the single place where insurer names are
    folded (every report that groups insurers goes through it). An account naming
    the same insurer twice yields it once."""
    vistas = {}
    for nombre in _insurers_of(cobertura):
        nombre = " ".join(nombre.split())
        vistas.setdefault(_insurer_key(nombre), nombre)
    return list(vistas.items())


class _InsurerSpellings:
    """Spellings seen per insurer key; `name(key)` is the most used one (a tie goes
    to the first in alphabetical order)."""

    def __init__(self):
        self._seen = {}

    def add(self, key, spelling):
        esc = self._seen.setdefault(key, {})
        esc[spelling] = esc.get(spelling, 0) + 1

    def name(self, key):
        return sorted(self._seen[key].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _coverage_report(total_cuentas):
    filas = store.reports_patients_coverage()
    con_obra = 0
    con_prepaga = 0
    top = {}        # key -> accounts that name it
    escrituras = _InsurerSpellings()
    for cobertura in filas:
        entradas = _insurer_entries(cobertura)
        if entradas:
            con_obra += 1
        for clave, nombre in entradas:
            escrituras.add(clave, nombre)
            top[clave] = top.get(clave, 0) + 1
        if _has_private_plan(cobertura):
            con_prepaga += 1
    top_ordenado = sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_INSURERS_N]
    return {
        "pct_con_obra_social": round(100.0 * con_obra / total_cuentas, 1) if total_cuentas else None,
        "pct_con_prepaga": round(100.0 * con_prepaga / total_cuentas, 1) if total_cuentas else None,
        "top_obras_sociales": [{"nombre": escrituras.name(k), "cantidad": c} for k, c in top_ordenado],
    }


def _activity(dias):
    """How many distinct patients requested an appointment in the period,
    and the average requests per active patient. `db.
    reportes_pacientes_solicitudes_por_paciente` already returns only
    counts (never the DNI) — there's nothing to anonymize here."""
    counts = store.reports_patients_booking_requests_by_patient(dias)
    pacientes = len(counts)
    total = sum(counts)
    return {
        "pacientes_con_solicitud": pacientes,
        "solicitudes_totales": total,
        "promedio_por_paciente": round(total / pacientes, 1) if pacientes else None,
    }


def _patients_payload(dias):
    kpis = store.reports_patients_kpis(dias)
    return {
        "dias": dias,
        "kpis": kpis,
        "cobertura": _coverage_report(kpis["total_cuentas"]),
        "edades": _age_distribution(),
        "volumen": store.reports_patients_volume(dias),
        "actividad_mensual": store.reports_patients_active_months(12),
        "dependientes": store.reports_patients_dependents(),
        "actividad": _activity(dias),
    }


# ---------------------------------------------------------------- login panel

def _login_panel_payload(dias):
    stats = store.stats_panel_login(dias)
    return {
        "dias": dias,
        "sesiones": stats["sesiones"],
        "logins": stats["logins"],
        "errores": stats["errores"],
        "sesiones_con_error": stats["sesiones_con_error"],
        "ms_mediana_login": stats["ms_mediana_login"],
    }


# ------------------------------------------------------------------ shared CSV

def _full_name(nombre, apellido):
    """Unlike _initials (management CSV), the billing CSVs (recupero/
    SUMAR+) carry the FULL name — the insurance provider/program needs it to
    register the patient, initials aren't enough."""
    n = (nombre or "").strip()
    a = (apellido or "").strip()
    out = " ".join(x for x in (n, a) if x)
    return out or "—"


def _week_of(dt):
    """ISO Monday (YYYY-MM-DD, Buenos Aires time) of the week of a
    timestamptz — grouped in Python because the full timestamptz is already
    in memory here (same week criterion as store.reportes_
    solicitudes_por_semana, which groups with date_trunc('week', ...) in
    SQL)."""
    if dt is None:
        return None
    local = dt.astimezone(_ART) if dt.tzinfo else dt.replace(tzinfo=_dt.timezone.utc).astimezone(_ART)
    lunes = local.date() - _dt.timedelta(days=local.weekday())
    return lunes.isoformat()


def _trend(filas, pertenece):
    """[{"semana","consultas"}] weekly (ISO Monday) of the rows from
    `store.reports_consultations_completed` that match `pertenece(fila)` —
    reused by recupero (with coverage) and SUMAR+ (exclusively-public
    population)."""
    counts = {}
    for f in filas:
        if not pertenece(f):
            continue
        s = _week_of(f.get("turno_dt"))
        if not s:
            continue
        counts[s] = counts.get(s, 0) + 1
    return [{"semana": k, "consultas": v} for k, v in sorted(counts.items())]


# --------------------------------------------------------- cost recovery

def _by_insurer(filas):
    """[{"nombre","consultas","presentes","pacientes"}] aggregated by
    insurance provider, sorted by consultas desc. A consultation for a
    patient with TWO insurance providers counts for BOTH (either one can
    bill that recovery) — "pacientes" is distinct DNI WITHIN that
    provider."""
    agg = {}
    escrituras = _InsurerSpellings()
    for f in filas:
        for clave, nombre in _insurer_entries(f.get("cobertura")):
            escrituras.add(clave, nombre)
            e = agg.setdefault(clave, {"consultas": 0, "presentes": 0, "pacientes": set()})
            e["consultas"] += 1
            if f.get("asistencia") == "presente":
                e["presentes"] += 1
            if f.get("dni"):
                e["pacientes"].add(f["dni"])
    out = [
        {"nombre": escrituras.name(k), "consultas": v["consultas"], "presentes": v["presentes"],
         "pacientes": len(v["pacientes"])}
        for k, v in agg.items()
    ]
    out.sort(key=lambda r: (-r["consultas"], r["nombre"]))
    return out


def _hero_cost_recovery(dias, kpis, por_obra_social):
    lang = i18n.active()
    n = kpis["consultas_con_cobertura"]
    if not n:
        return [{"texto": i18n.t_or("admin.srv.reports.hero.recupero_none",
                                     "Todavía no hay consultas con obra social registrada en este período.", lang),
                  "tono": "ok"}]
    pct = kpis["pct_del_total"]
    consulta = _plural(n, "admin.srv.reports.noun.consulta", "consulta", "consultas")
    texto = i18n.t_or(
        "admin.srv.reports.hero.recupero_base",
        "El equipo atendió {n} {consulta} con obra social registrada en los últimos {dias} días",
        lang, n=_num_es(n, 0), consulta=consulta, dias=dias)
    if pct is not None:
        texto += i18n.t_or("admin.srv.reports.hero.pct_del_total",
                            ", un {pct}% del total de consultas realizadas", lang, pct=_pct_es(pct))
    texto += "."
    frases = [{"texto": texto, "tono": "ok"}]
    if por_obra_social:
        top = por_obra_social[0]
        frases.append({
            "texto": i18n.t_or(
                "admin.srv.reports.hero.recupero_top",
                "{nombre} concentra {n} de esas consultas: es la obra social con más volumen para gestionar "
                "el recupero.",
                lang, nombre=top["nombre"], n=_num_es(top["consultas"], 0)),
            "tono": "ok",
        })
    return frases[:2]


def _cost_recovery_payload(dias):
    filas = store.reports_consultations_completed(dias)
    total = len(filas)
    con_cobertura = [f for f in filas if _insurers_of(f.get("cobertura"))]
    n_con_cobertura = len(con_cobertura)
    kpis = {
        "consultas_totales": total,
        "consultas_con_cobertura": n_con_cobertura,
        "pct_del_total": round(100.0 * n_con_cobertura / total, 1) if total else None,
        "pacientes_con_cobertura": len({f["dni"] for f in con_cobertura if f.get("dni")}),
        "presentes": sum(1 for f in con_cobertura if f.get("asistencia") == "presente"),
        "ausentes": sum(1 for f in con_cobertura if f.get("asistencia") == "ausente"),
    }
    por_obra_social = _by_insurer(filas)
    return {
        "dias": dias,
        "hero": _hero_cost_recovery(dias, kpis, por_obra_social),
        "kpis": kpis,
        "por_obra_social": por_obra_social,
        "tendencia": _trend(filas, lambda f: bool(_insurers_of(f.get("cobertura")))),
        "sin_dato": total - n_con_cobertura,
    }


def _insurers_with_member_id(cobertura):
    """[(nombre, afiliado)] for ONE account, tolerating both jsonb shapes
    (see _insurers_of) — the billing CSV also needs the member number when
    it's on file (afiliado="" if there isn't one)."""
    if not isinstance(cobertura, dict):
        return []
    obras = cobertura.get("obras_sociales")
    if isinstance(obras, list):
        return [(o.get("nombre"), o.get("afiliado") or "")
                 for o in obras if isinstance(o, dict) and o.get("nombre")]
    singular = cobertura.get("obra_social")
    if isinstance(singular, dict) and singular.get("nombre"):
        return [(singular["nombre"], singular.get("afiliado") or "")]
    if isinstance(singular, str) and singular.strip():
        return [(singular.strip(), "")]
    return []


_COST_RECOVERY_CSV_HEADER = ["fecha", "dni", "paciente", "obra_social", "nro_afiliado", "servicio",
                             "especialidad", "centro", "turno", "asistencia"]
_COST_RECOVERY_CSV_FILENAME = "recupero.csv"


def _cost_recovery_csv_rows(dias):
    """DELIBERATE PII CARVE-OUT: this CSV is the handoff to the billing/cost
    recovery office (SAMO/health insurance) — it needs dni + full name +
    member number to be able to file the claim. The ON-SCREEN report
    (`_cost_recovery_payload`) stays at aggregates by insurance provider,
    with none of these columns; only this CSV, gated behind solo_admin,
    exposes identity (report-design law 12: the page never carries
    name/DNI, the export does when the destination requires it)."""
    filas = store.reports_consultations_completed(dias)
    smap = _service_map()
    cmap = _center_map()
    out = []
    for f in filas:
        sid = f.get("servicio_id")
        info = smap.get(sid) or {}
        pares = _insurers_with_member_id(f.get("cobertura"))
        out.append([
            _fmt_dt(f.get("created")),
            f.get("dni") or "",
            _full_name(f.get("nombre"), f.get("apellido")),
            "; ".join(n for n, _a in pares),
            "; ".join(a for _n, a in pares if a),
            info.get("nombre") or sid or "",
            _cluster_label(info.get("cluster"), lang="es") if info.get("cluster") else "",
            cmap.get(f.get("centro_slug"), f.get("centro_slug") or ""),
            _fmt_dt(f.get("turno_dt")),
            f.get("asistencia") or "",
        ])
    return out


# --------------------------------------------------------------------- SUMAR+

# "gineco" = "Ginecología y obstetricia" and "pediatria" = "Pediatría" in
# webdata/clusters.json — the only real catalog clusters that match the two
# tracers (there's no separate "obstetricia" cluster).
_CLUSTER_PREGNANCY = "gineco"
_CLUSTER_WELL_CHILD = "pediatria"
_WELL_CHILD_AGE_MAX = 9  # <10 years old, even if the service isn't pediatric (e.g. a lab test ordered for a kid)

def _tracer_labels(lang=None):
    """No es constante de módulo por el mismo motivo que _group_labels: se
    congelaría en español al importar."""
    lang = lang or i18n.active()
    return {
        "embarazo": i18n.t_or("admin.srv.reports.tracer.embarazo", "Embarazo", lang),
        "nino_sano": i18n.t_or("admin.srv.reports.tracer.nino_sano", "Niño sano", lang),
        "otros": i18n.t_or("admin.srv.reports.tracer.otros", "Otras prestaciones", lang),
    }


_TRACER_ORDER = ("embarazo", "nino_sano", "otros")


def _is_public_only(cobertura):
    """SUMAR+ population: no insurance provider AND no prepaid plan
    declared. Absent/None coverage counts the same as an explicitly empty
    one — an account that never registered (or that registered without
    loading coverage) is as much "exclusively public" as one that
    explicitly declared it has nothing; there's no way to distinguish the
    reason with the data available, and both cases are real candidates for
    the program."""
    return not _insurers_of(cobertura) and not _has_private_plan(cobertura)


def _age_at_appointment(nacimiento, turno_dt):
    """Full years old AT THE TIME OF THE APPOINTMENT (not "today" — for the
    nino_sano tracer, what matters is the age they had when seen, not the
    current age if the report runs later)."""
    if nacimiento is None or turno_dt is None:
        return None
    local = turno_dt.astimezone(_ART) if turno_dt.tzinfo else turno_dt.replace(tzinfo=_dt.timezone.utc).astimezone(_ART)
    return _age_of(nacimiento, local.date())


def _tracer_of(fila, smap):
    cluster = (smap.get(fila.get("servicio_id")) or {}).get("cluster")
    if cluster == _CLUSTER_PREGNANCY:
        return "embarazo"
    edad = _age_at_appointment(fila.get("nacimiento"), fila.get("turno_dt"))
    if cluster == _CLUSTER_WELL_CHILD or (edad is not None and edad <= _WELL_CHILD_AGE_MAX):
        return "nino_sano"
    return "otros"


def _hero_sumar(dias, kpis, trazadoras_map):
    lang = i18n.active()
    n = kpis["prestaciones"]
    if not n:
        return [{
            "texto": i18n.t_or(
                "admin.srv.reports.hero.sumar_none",
                "Todavía no hubo prestaciones a población con cobertura exclusivamente pública en este período.",
                lang),
            "tono": "ok",
        }]
    pct = kpis["pct_del_total"]
    prestacion = _plural(n, "admin.srv.reports.noun.prestacion", "prestación", "prestaciones")
    texto = i18n.t_or(
        "admin.srv.reports.hero.sumar_base",
        "El sistema atendió {n} {prestacion} a población con cobertura exclusivamente pública en los últimos "
        "{dias} días",
        lang, n=_num_es(n, 0), prestacion=prestacion, dias=dias)
    if pct is not None:
        texto += i18n.t_or("admin.srv.reports.hero.pct_del_total",
                            ", un {pct}% del total de consultas realizadas", lang, pct=_pct_es(pct))
    texto += "."
    frases = [{"texto": texto, "tono": "ok"}]
    top = max(((k, v) for k, v in trazadoras_map.items() if k != "otros"), key=lambda kv: kv[1], default=None)
    if top and top[1] > 0:
        frases.append({
            "texto": i18n.t_or("admin.srv.reports.hero.sumar_top",
                                "{label} concentra {n} de esas prestaciones en el período.",
                                lang, label=_tracer_labels(lang)[top[0]], n=_num_es(top[1], 0)),
            "tono": "ok",
        })
    return frases[:2]


def _sumar_payload(dias):
    filas = store.reports_consultations_completed(dias)
    smap = _service_map()
    total = len(filas)
    poblacion = [f for f in filas if _is_public_only(f.get("cobertura"))]
    n = len(poblacion)
    kpis = {
        "prestaciones": n,
        "pacientes": len({f["dni"] for f in poblacion if f.get("dni")}),
        "pct_del_total": round(100.0 * n / total, 1) if total else None,
    }
    trazadoras_map = {"embarazo": 0, "nino_sano": 0, "otros": 0}
    for f in poblacion:
        trazadoras_map[_tracer_of(f, smap)] += 1
    etiquetas_trazadora = _tracer_labels()
    trazadoras = [{"clave": k, "label": etiquetas_trazadora[k], "consultas": trazadoras_map[k]} for k in _TRACER_ORDER]
    return {
        "dias": dias,
        "hero": _hero_sumar(dias, kpis, trazadoras_map),
        "kpis": kpis,
        "trazadoras": trazadoras,
        "tendencia": _trend(filas, lambda f: _is_public_only(f.get("cobertura"))),
    }


_SUMAR_CSV_HEADER = ["fecha", "dni", "paciente", "servicio", "especialidad", "centro", "turno", "asistencia",
                     "trazadora"]
_SUMAR_CSV_FILENAME = "sumar.csv"


def _sumar_csv_rows(dias):
    """DELIBERATE PII CARVE-OUT: same criterion as `_cost_recovery_csv_rows`
    — this is the SUMAR+ program's operational handoff (needs dni + full
    name to register each patient), never the on-screen report.

    This CSV is a fixed handoff format for a government program (SUMAR+),
    not admin-panel chrome: it stays in Spanish regardless of the admin's
    own language, same as the column header above (report-design CSVs are
    a wire contract with an external office, not UI copy)."""
    filas = store.reports_consultations_completed(dias)
    smap = _service_map()
    cmap = _center_map()
    poblacion = [f for f in filas if _is_public_only(f.get("cobertura"))]
    etiquetas_trazadora_es = _tracer_labels("es")
    out = []
    for f in poblacion:
        sid = f.get("servicio_id")
        info = smap.get(sid) or {}
        out.append([
            _fmt_dt(f.get("created")),
            f.get("dni") or "",
            _full_name(f.get("nombre"), f.get("apellido")),
            info.get("nombre") or sid or "",
            _cluster_label(info.get("cluster"), lang="es") if info.get("cluster") else "",
            cmap.get(f.get("centro_slug"), f.get("centro_slug") or ""),
            _fmt_dt(f.get("turno_dt")),
            f.get("asistencia") or "",
            etiquetas_trazadora_es[_tracer_of(f, smap)],
        ])
    return out


# ------------------------------------------------------------------ statistics

def _month_label(year, month, lang=None):
    """"enero de 2026" / "January 2026" — usa el catálogo date.month_full.*
    (ya traducido por otra unidad) en vez de una tabla propia congelada en
    español."""
    lang = lang or i18n.active()
    nombre_mes = i18n.t(f"date.month_full.{month}", lang)
    return i18n.t_or("admin.srv.reports.statistics.month_label", "{month} de {year}",
                      lang, month=nombre_mes, year=year)


def _month_param(query):
    """(year, month) from the ?mes=YYYY-MM query; invalid or absent falls
    back to the last COMPLETE CALENDAR month in ART time (if today is any
    day in July, the default is June). `query` is a raw query string, same
    transport-agnostic contract as `_days_param`."""
    qs = parse_qs(query)
    raw = (qs.get("mes") or [""])[0]
    try:
        y_s, m_s = raw.split("-")
        year, month = int(y_s), int(m_s)
        if len(y_s) == 4 and 1 <= month <= 12:
            return year, month
    except (ValueError, AttributeError):
        pass
    hoy = _dt.datetime.now(_ART).date()
    ultimo_dia_anterior = hoy.replace(day=1) - _dt.timedelta(days=1)
    return ultimo_dia_anterior.year, ultimo_dia_anterior.month


def _month_range(year, month):
    """(desde_iso, hasta_iso) of a calendar month — hasta_iso EXCLUSIVE (the
    first day of the following month)."""
    desde = _dt.date(year, month, 1)
    hasta = _dt.date(year + 1, 1, 1) if month == 12 else _dt.date(year, month + 1, 1)
    return desde.isoformat(), hasta.isoformat()


def _age_distribution_of(pares, referencia):
    """The F/M pyramid from _AGE_BUCKETS over a (nacimiento, sexo) list and a
    reference date. `_age_distribution()` passes today; the statistics
    sub-report calculates age as of the last day of the REPORTED MONTH (a
    past month shouldn't show the current age)."""
    counts = {label: {"cantidad": 0, "f": 0, "m": 0} for _, _, label in _AGE_BUCKETS}
    for nacimiento, sexo in pares:
        edad = _age_of(nacimiento, referencia)
        if edad is None:
            continue
        label = _age_bucket(edad)
        if not label:
            continue
        counts[label]["cantidad"] += 1
        if sexo == "F":
            counts[label]["f"] += 1
        elif sexo == "M":
            counts[label]["m"] += 1
    return [{"rango": label, **counts[label]} for _, _, label in _AGE_BUCKETS]


def _hero_statistics(mes_label, kpis):
    lang = i18n.active()
    n = kpis["consultas_totales"]
    if not n:
        return [{"texto": i18n.t_or("admin.srv.reports.hero.stats_none",
                                     "Todavía no hay consultas registradas para {mes}.", lang, mes=mes_label),
                  "tono": "ok"}]
    consulta = _plural(n, "admin.srv.reports.noun.consulta", "consulta", "consultas")
    texto = i18n.t_or("admin.srv.reports.hero.stats_base",
                       "El hospital registró {n} {consulta} en {mes}", lang, n=_num_es(n, 0), consulta=consulta, mes=mes_label)
    pct = kpis["pct_asistencia"]
    if pct is not None:
        texto += i18n.t_or("admin.srv.reports.hero.stats_asistencia",
                            ", con un {pct}% de asistencia efectiva sobre las que ya tienen marca",
                            lang, pct=_pct_es(pct))
    texto += "."
    return [{"texto": texto, "tono": "ok"}]


def _statistics_aggregates(filas, smap, cmap, desde_iso, hasta_iso):
    """Aggregations shared by _statistics_payload() and
    _statistics_csv_rows() — a
    single pass over `filas` (avoids fetching/iterating the query twice for
    the page and the CSV)."""
    horas_cluster = _cluster_hours()
    desde = _dt.date.fromisoformat(desde_iso)
    hasta = _dt.date.fromisoformat(hasta_iso)
    semanas_mes = max((hasta - desde).days / 7.0, 1.0 / 7.0)

    por_cluster = {}
    por_centro = {}
    presentes = ausentes = sin_marca = 0
    for sid, centro_slug, asistencia, _nac, _sexo in filas:
        cid = (smap.get(sid) or {}).get("cluster") or "otras"
        e = por_cluster.setdefault(cid, {"consultas": 0, "presentes": 0, "ausentes": 0, "sin_marca": 0})
        e["consultas"] += 1
        if asistencia == "presente":
            e["presentes"] += 1
            presentes += 1
        elif asistencia == "ausente":
            e["ausentes"] += 1
            ausentes += 1
        else:
            e["sin_marca"] += 1
            sin_marca += 1
        centro_nombre = cmap.get(centro_slug, centro_slug
                                  or i18n.t_or("admin.srv.reports.centro.sin_centro", "Sin centro", i18n.active()))
        por_centro[centro_nombre] = por_centro.get(centro_nombre, 0) + 1

    por_especialidad = []
    for cid, e in por_cluster.items():
        horas = round(horas_cluster.get(cid, 0.0) * semanas_mes, 1)
        por_especialidad.append({
            "cluster": cid,
            "label": _cluster_label(cid),
            "consultas": e["consultas"],
            "presentes": e["presentes"],
            "ausentes": e["ausentes"],
            "sin_marca": e["sin_marca"],
            "horas_ofrecidas": horas,
            "consultas_por_hora": round(e["consultas"] / horas, 2) if horas > 0 else None,
        })
    por_especialidad.sort(key=lambda r: -r["consultas"])

    por_centro_out = sorted(
        ({"centro": k, "consultas": v} for k, v in por_centro.items()),
        key=lambda r: -r["consultas"],
    )

    kpis = {
        "consultas_totales": len(filas),
        "presentes": presentes,
        "ausentes": ausentes,
        "sin_marca": sin_marca,
        "pct_asistencia": round(100.0 * presentes / (presentes + ausentes), 1) if (presentes + ausentes) else None,
    }
    return kpis, por_especialidad, por_centro_out


def _statistics_payload(year, month):
    desde_iso, hasta_iso = _month_range(year, month)
    filas = store.reports_statistics_rows(desde_iso, hasta_iso)
    smap = _service_map()
    cmap = _center_map()
    kpis, por_especialidad, por_centro = _statistics_aggregates(filas, smap, cmap, desde_iso, hasta_iso)
    referencia = _dt.date.fromisoformat(hasta_iso) - _dt.timedelta(days=1)
    edades = _age_distribution_of([(f[3], f[4]) for f in filas], referencia)
    mes_label = _month_label(year, month)
    return {
        "mes": f"{year:04d}-{month:02d}",
        "hero": _hero_statistics(mes_label, kpis),
        "kpis": kpis,
        "por_especialidad": por_especialidad,
        "edades": edades,
        "por_centro": por_centro,
    }


_STATISTICS_CSV_HEADER = ["especialidad", "consultas", "presentes", "ausentes", "sin_marca", "horas_ofrecidas",
                          "consultas_por_hora"]
_STATISTICS_CSV_FILENAME = "estadistica.csv"


def _statistics_csv_rows(year, month):
    """Aggregated by specialty — the source query (`store.
    reports_statistics_rows`) doesn't bring dni/name: this CSV is
    structurally incapable of carrying PII, it goes to the statistics
    office with no carve-out needed (unlike recupero.csv/sumar.csv)."""
    desde_iso, hasta_iso = _month_range(year, month)
    filas = store.reports_statistics_rows(desde_iso, hasta_iso)
    smap = _service_map()
    cmap = _center_map()
    _kpis, por_especialidad, _por_centro = _statistics_aggregates(filas, smap, cmap, desde_iso, hasta_iso)
    return [
        [r["label"], r["consultas"], r["presentes"], r["ausentes"], r["sin_marca"], r["horas_ofrecidas"],
         r["consultas_por_hora"] if r["consultas_por_hora"] is not None else ""]
        for r in por_especialidad
    ]


# ----------------------------------------------------------- seasonal capacity

_CAPACITY_DEMAND_DAYS = 90  # fixed "recent demand" window for sin_oferta — capacity doesn't take ?days=
_CAPACITY_WEEKS = 8
_CAPACITY_OCCUPANCY_DAYS = 14
# A specialty with no hours leads the capacity hero only when it holds at
# least this share of the period's requests: 1 request out of 10 000 is
# listed below, not the headline.
_CAPACITY_UNMET_SHARE = 0.02


def _slots_from_schedule(agenda, dias, ahora):
    """Real slots from an `agenda` {dias,desde,hasta,cada_min} OR
    {franjas:[...],excepciones:[...]} over the next `dias` days, walking it
    with doctor_appointments._slots_in_days (counts exceptions/closures) — 0
    if the schedule has nothing usable."""
    if not isinstance(agenda, dict):
        return 0
    if agenda.get("franjas"):
        franjas = agenda.get("franjas") or []
    elif agenda.get("dias"):
        franjas = [agenda]
    else:
        return 0
    excepciones = agenda.get("excepciones") or []
    return doctor_appointments._slots_in_days(franjas, excepciones, dias, ahora)


def _capacity_14d():
    """{cluster: capacity in real SLOTS over the next 14 days} — same
    fallback criterion as site_availability._week_hours (the service's own
    schedule if it has one; otherwise its cluster+center doctors'), but in
    SLOTS walking day by day with doctor_appointments._slots_in_days (which
    respects exceptions/vacations) instead of summing nominal weekly hours.
    Same gotcha as _week_hours: a service with no schedule of its own sums
    up the ENTIRE capacity of doctors that match cluster+center, so two
    services in the same cluster+center can double-count the same doctor's
    capacity — inherited behavior, not new here.
    A service's own schedule applies to EACH doctor it manages
    (schedule_policy.managed_services, the relation the calendar and the
    booking engine use), so it counts once per such doctor, at least once:
    counted a single time, a service run by three doctors showed 161 %."""
    from app.modules.turnos import schedule_policy
    ahora = _dt.datetime.now(_ART)
    medicos = site_catalog._doctors()
    servicios = site_catalog._services()
    gestionados = {}
    for m in medicos:
        if m.get("_borrado"):
            continue
        for s in schedule_policy.managed_services(m, servicios):
            gestionados[s.get("id")] = gestionados.get(s.get("id"), 0) + 1
    out = {}
    for s in servicios:
        cid = s.get("cluster")
        if not cid:
            continue
        propio = _slots_from_schedule(s.get("agenda"), _CAPACITY_OCCUPANCY_DAYS, ahora)
        if propio:
            out[cid] = out.get(cid, 0) + propio * max(1, gestionados.get(s.get("id"), 0))
            continue
        for m in medicos:
            if cid not in catalog.doctor_clusters(m):
                continue
            for c in (m.get("centros") or []):
                if c.get("centro") != s.get("centro"):
                    continue
                franjas = c.get("franjas") or []
                excepciones = c.get("excepciones") or []
                out[cid] = out.get(cid, 0) + doctor_appointments._slots_in_days(
                    franjas, excepciones, _CAPACITY_OCCUPANCY_DAYS, ahora)
    return out


def _weekly_demand():
    sol = dict(store.reports_booking_requests_by_week(_CAPACITY_WEEKS))
    bus = dict(store.reports_searches_by_week(_CAPACITY_WEEKS))
    # The store's window is the last 8×7 days, so its oldest Monday holds only
    # the tail of a week: drawn, that week fell to almost zero (the filled
    # demo, 15.11: «Lunes 14 de septiembre» at 0 beside 2 thousand). Kept:
    # the current week and the 7 whole ones before it.
    hoy = _dt.datetime.now(_ART).date()
    primero = (hoy - _dt.timedelta(days=hoy.weekday() + 7 * (_CAPACITY_WEEKS - 1))).isoformat()
    semanas = sorted(s for s in set(sol) | set(bus) if s >= primero)
    return [{"semana": s, "solicitudes": sol.get(s, 0), "busquedas": bus.get(s, 0)} for s in semanas]


def _currently_unmet():
    """(rows of the specialties with demand and no hours, every request of the window)."""
    od = _supply_demand(_CAPACITY_DEMAND_DAYS)
    total = sum(c["solicitudes"] for c in od["clusters"])
    return [{"cluster": c["id"], "label": c["nombre"], "solicitudes": c["solicitudes"]}
            for c in od["clusters"] if c["sin_oferta"]], total


def _season(demanda_semanal):
    """{"disponible": False} if TURNOS_SEASON_FACTOR isn't set — a seasonal
    factor is NEVER invented without real data (report-design law 10). When
    set: projects the demand observed over the last 8 weeks × factor, with
    the assumption printed verbatim so the frontend shows it without
    reinterpreting it."""
    raw = os.environ.get("TURNOS_SEASON_FACTOR")
    if not raw:
        return {"disponible": False}
    try:
        factor = float(raw)
    except (TypeError, ValueError):
        return {"disponible": False}
    observado = sum(r["solicitudes"] for r in demanda_semanal)
    lang = i18n.active()
    return {
        "disponible": True,
        "factor": factor,
        "proyeccion": round(observado * factor, 1),
        "supuesto": i18n.t_or(
            "admin.srv.reports.season.assumption",
            "Se asume que la demanda de temporada alta multiplica por {factor}× la demanda observada en las "
            "últimas 8 semanas (variable de entorno TURNOS_SEASON_FACTOR) — todavía no hay datos propios de "
            "temporadas anteriores para medirlo, así que esto es una proyección declarada, no una medición.",
            lang, factor=_num_es(factor, 2)),
    }


def _capacity_rule(ocupacion, sin_oferta, total_solicitudes=None):
    """("sin_horas" | "atencion" | "ok" | "sin_medida", top row or None) — the ONE conclusion
    of the capacity report. The page's headline is written from it and the
    headline carries it as `regla`, which the Reportes card follows: the card
    once named «1 — Vacunas y prevención» while the page said the capacity
    covers the demand (15.11).
    Below the share, together, the specialties with no hours yield the
    headline to a fuller one, or to the «ok» line: it speaks of the
    specialties with data, and they stay listed below the chart. On the
    demo «1 solicitud» of Vacunas led against 21 000 turnos."""
    if sin_oferta and not (
            total_solicitudes
            and sum(r["solicitudes"] for r in sin_oferta) < total_solicitudes * _CAPACITY_UNMET_SHARE):
        return "sin_horas", None
    candidatos = [r for r in ocupacion if r["pct"] is not None]
    if not candidatos:
        # No specialty has hours to measure: «covers the demand» would be
        # a claim about nothing.
        return "sin_medida", None
    top = max(candidatos, key=lambda r: r["pct"])
    if top["pct"] >= 90:
        return "atencion", top
    return "ok", top


def _hero_capacity(ocupacion, sin_oferta, total_solicitudes=None):
    regla, top = _capacity_rule(ocupacion, sin_oferta, total_solicitudes)
    return [dict(item, regla=regla) for item in _hero_capacity_texto(regla, top, sin_oferta)]


def _hero_capacity_texto(regla, top, sin_oferta):
    lang = i18n.active()
    if regla == "sin_horas":
        return _hero_sin_horas(sin_oferta, lang)
    if regla == "atencion":
        return [{
            "texto": i18n.t_or(
                "admin.srv.reports.hero.capacity_atencion",
                "{label} está al {pct}% de su capacidad de los próximos 14 días ({ocupados} de {capacidad} "
                "turnos): sumar horas ahí es la inversión de mayor impacto.",
                lang, label=top["label"], pct=_pct_es(top["pct"]),
                ocupados=_num_es(top["ocupados_14d"], 0), capacidad=_num_es(top["capacidad_14d"], 0)),
            "tono": "atencion",
        }]
    if regla == "sin_medida":
        return [{
            "texto": i18n.t_or(
                "admin.srv.reports.hero.capacity_sin_medida",
                "Todavía no hay capacidad medible para comparar contra la demanda.",
                lang),
            "tono": "ok",
        }]
    return [{
        "texto": i18n.t_or(
            "admin.srv.reports.hero.capacity_ok",
            "La capacidad ofrecida cubre la demanda de los próximos 14 días en todas las especialidades con "
            "datos.",
            lang),
        "tono": "ok",
    }]


def _hero_sin_horas(sin_oferta, lang):
    top = max(sin_oferta, key=lambda r: r["solicitudes"])
    # One request reads «1 solicitud», not «1 solicitudes» (Opus review 2026-11-04);
    # tn_or so Russian 21 keeps its singular and 2–4 / 5–20 their own forms.
    texto = i18n.tn_or(
        "admin.srv.reports.hero.capacity_critico", top["solicitudes"],
        "{label} concentró {n} solicitud en los últimos {dias} días sin ninguna hora de agenda "
        "registrada: es la prioridad de capacidad de los próximos 14 días.",
        "{label} concentró {n} solicitudes en los últimos {dias} días sin ninguna hora de agenda "
        "registrada: es la prioridad de capacidad de los próximos 14 días.",
        lang, label=top["label"], dias=_CAPACITY_DEMAND_DAYS)
    return [{"texto": texto, "tono": "critico"}]


def _capacity_payload():
    smap = _service_map()
    capacidad = _capacity_14d()
    ahora = _dt.datetime.now(_ART)
    limite = ahora + _dt.timedelta(days=_CAPACITY_OCCUPANCY_DAYS)

    # `hasta=limite` (2026-08-06 audit): el corte de 14 días ya se conocía
    # acá, así que SQL descarta lo que cae después en vez de traerlo para
    # tirarlo abajo.
    ocupados_cluster = {}
    for row in store.list_approved_future(ahora, hasta=limite):
        turno_dt = row.get("turno_dt")
        if not turno_dt or turno_dt > limite:
            continue
        cid = (smap.get(row.get("servicio_id")) or {}).get("cluster") or "otras"
        ocupados_cluster[cid] = ocupados_cluster.get(cid, 0) + 1

    ocupacion = []
    for cid in set(capacidad) | set(ocupados_cluster):
        cap = capacidad.get(cid, 0)
        ocu = ocupados_cluster.get(cid, 0)
        if cap <= 0 and ocu <= 0:
            continue
        ocupacion.append({
            "cluster": cid,
            "label": _cluster_label(cid),
            "capacidad_14d": cap,
            "ocupados_14d": ocu,
            "pct": round(100.0 * ocu / cap, 1) if cap > 0 else None,
        })
    ocupacion.sort(key=lambda r: (-(r["pct"] if r["pct"] is not None else -1), -r["ocupados_14d"]))

    sin_oferta, total_solicitudes = _currently_unmet()
    demanda_semanal = _weekly_demand()
    return {
        "hero": _hero_capacity(ocupacion, sin_oferta, total_solicitudes),
        "ocupacion": ocupacion,
        "demanda_semanal": demanda_semanal,
        "sin_oferta": sin_oferta,
        "temporada": _season(demanda_semanal),
    }
