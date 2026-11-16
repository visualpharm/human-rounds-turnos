"""Doctor self-service projections, sharing catalog and schedule authority."""
from copy import deepcopy
from app.core import db_operators
from app.patient_web import catalog
from app.modules.turnos import schedule_policy
from app.admin_panel import admin_content


def _band_services(doctor, services, cid):
    """Service options for one center's bands: the doctor's own tagged ones
    first (their own order), then their pool at that center alphabetically.
    The room (consultorio) travels with the service — it is a service fact,
    not a doctor field, so the option text is where the doctor reads it."""
    tagged = []
    for entry in doctor.get('centros') or []:
        if entry.get('centro') != cid:
            continue
        for band in entry.get('franjas') or []:
            sid = band.get('servicio')
            if sid and sid not in tagged:
                tagged.append(sid)
    if doctor.get('centro') == cid:
        agenda = doctor.get('agenda') or {}
        for band in agenda.get('franjas') or ([agenda] if agenda.get('dias') else []):
            sid = band.get('servicio')
            if sid and sid not in tagged:
                tagged.append(sid)
    pool = [s for s in services if not s.get('_borrado') and s.get('centro') == cid
            and schedule_policy.belongs(doctor, s)]
    ordered = [next(s for s in pool if s.get('id') == sid) for sid in tagged
               if any(s.get('id') == sid for s in pool)]
    ordered += sorted((s for s in pool if s.get('id') not in tagged),
                      key=lambda s: (s.get('nombre') or '').lower())
    # A legacy tag outside today's pool still belongs to the doctor's own
    # bands: it stays offerable instead of silently vanishing from the form.
    for sid in tagged:
        svc = next((s for s in services if not s.get('_borrado') and s.get('id') == sid), None)
        if svc and all(o.get('id') != sid for o in ordered):
            ordered.append(svc)
    return [{'id': s['id'], 'nombre': s.get('nombre') or s['id'],
             'consultorio': s.get('consultorio') or ''} for s in ordered]


def common_minutes(rows):
    """The appointment length most bands on the page share, said once above
    the cards so each band names its length only where it differs (the product owner,
    02.11: «· turnos de 20 min» on every card). None with fewer than two
    bands — a lone band keeps its own tail."""
    from collections import Counter
    counts = Counter(band.get('cada_min') or 20 for row in rows for band in row.get('franjas') or [])
    if sum(counts.values()) < 2:
        return None
    minutes, n = counts.most_common(1)[0]
    return minutes if n * 2 > sum(counts.values()) else None


def _center_name(names, cid):
    """A center no longer in the catalog reads as words, never its slug
    (the product owner, 02.11: no service keys on screen)."""
    from app.core import i18n
    return names.get(cid) or i18n.t_or('admin.doctor.unknown_center', 'Centro sin nombre', i18n.active())


def schedule_rows(doctor, services, centers):
    names = {c['slug']: c.get('name') or c.get('nombre') or c['slug'] for c in centers}
    assigned = deepcopy(doctor.get('centros') or [])
    if not assigned and doctor.get('centro'):
        agenda = doctor.get('agenda') or {}
        assigned = [{'centro': doctor['centro'], 'franjas': agenda.get('franjas') or ([agenda] if agenda.get('dias') else []), 'excepciones': agenda.get('excepciones') or []}]
    managed = schedule_policy.managed_services(doctor, services)
    rows = []
    for center in assigned:
        cid = center.get('centro')
        owned = [s for s in managed if s.get('centro') == cid]
        if owned:
            for service in owned:
                agenda = service['agenda']
                rows.append({'centro': cid, 'center': _center_name(names, cid), 'consultorio': service.get('consultorio') or service.get('nombre') or '',
                             'franjas': agenda.get('franjas') or [agenda], 'excepciones': agenda.get('excepciones') or [], 'editable': False,
                             'message': admin_content._schedule_message()})
        else:
            rows.append({'centro': cid, 'center': _center_name(names, cid), 'consultorio': center.get('consultorio') or doctor.get('consultorio') or '',
                         'franjas': center.get('franjas') or [], 'excepciones': center.get('excepciones') or [], 'editable': not bool(managed),
                         'message': admin_content._schedule_message() if managed else ''})
    for row in rows:
        row['closures'] = [closure_summary(exc) for exc in row['excepciones']]
        row['closures'] = [line for line in row['closures'] if line]
        row['servicios'] = _band_services(doctor, services, row['centro']) if row['editable'] else []
    return rows


def closure_summary(exc):
    """Schedule exception labels use the canonical friendly-date formatter."""
    from app.core import i18n
    from app.patient_web import site_schedule
    start = exc.get('desde_fecha') or exc.get('fecha')
    end = exc.get('hasta_fecha') or start
    first = site_schedule._friendly_date(start)
    last = site_schedule._friendly_date(end)
    if not first or not last:
        return ''
    kind = exc.get('tipo') or 'cerrado'
    if kind == 'cerrado':
        line = i18n.t_or('admin.doctor.closure_range', 'Sin atención: {first} a {last}', i18n.active(), first=first, last=last) if start != end else i18n.t_or('admin.doctor.closure_day', 'Sin atención: {day}', i18n.active(), day=first)
    else:
        line = i18n.t_or('admin.doctor.extra_hours' if kind == 'extra' else 'admin.doctor.blocked_hours',
            'Turnos extra: {day}, {from} a {to}' if kind == 'extra' else 'Sin atención: {day}, {from} a {to}',
            i18n.active(), day=first, **{'from': exc.get('desde') or '', 'to': exc.get('hasta') or ''})
    return i18n.t_or('admin.doctor.closure_reason', '{hours} ({reason})', i18n.active(), hours=line, reason=exc['motivo']) if exc.get('motivo') else line


HOLIDAYS_IN_LINE = 3


def upcoming_holidays(today=None):
    """What «Mis agendas» says about the site holidays (site_holidays.py), in
    ONE line of meaning (the product owner, 2026-10-27: «no turnos are given on them, so it is
    one line of meaning, not a noisy list»): on a holiday no agenda offers a
    turno (`site_availability._day_exceptions` closes the day for every slot
    surface), so the doctor reads that sentence with the nearest dates,
    date first and never «Feriado» on every row; the full list with the
    names opens on request. {} when there is no upcoming holiday."""
    from app.core import i18n
    from app.modules.turnos import site_holidays
    from app.patient_web import site_base, site_schedule
    items = site_holidays.upcoming_labeled(today)
    if not items:
        return {}
    lang = i18n.active()
    hoy = today or site_base._today_art()
    rows = [{'fecha': h['fecha'], 'nombre': h['nombre'],
             'dia': site_schedule._day_month(h['fecha'], hoy=hoy)} for h in items]
    if len(rows) == 1:
        line = i18n.t_or('admin.doctor.holidays.one', 'En los feriados no se dan turnos. El próximo es el {day} ({name}).',
                         lang, day=rows[0]['dia'], name=rows[0]['nombre'])
        return {'line': line, 'rows': []}
    near = [r['dia'] for r in rows[:HOLIDAYS_IN_LINE]]
    dates = i18n.t_or('admin.doctor.days_join', '{first} y {last}', lang, first=', '.join(near[:-1]), last=near[-1])
    line = i18n.t_or('admin.doctor.holidays.line', 'En los feriados no se dan turnos. Los próximos: {dates}.', lang, dates=dates)
    return {'line': line, 'rows': rows,
            'more': i18n.t_or('admin.doctor.holidays.more', 'Ver los {n} feriados', lang, n=len(rows))}


def onboarding_missing(identity):
    """What the doctor still has to give before the onboarding counts as done
    (the product owner, 2026-10-19: everything is optional at invite and asked here). The
    email is required — the owner wants it collected; a center too, because
    without one the doctor can never appear for patients."""
    own = db_operators.operator_doctor_id(identity['usuario'])
    doctor = next((d for d in catalog.admin_list('medicos') if d.get('id') == own and not d.get('_borrado')), None)
    if not doctor:
        return []  # the unlinked case keeps its own answer (cuenta_sin_medico)
    missing = [field for field in ('nombre', 'especialidad', 'email') if not str(doctor.get(field) or '').strip()]
    if not (doctor.get('centros') or doctor.get('centro')):
        missing.append('centro')
    return missing


def missing_message(missing):
    from app.core import i18n
    labels = {'nombre': i18n.t_or('admin.doctor.missing.name', 'tu nombre', i18n.active()),
              'especialidad': i18n.t_or('admin.doctor.missing.specialty', 'tu especialidad', i18n.active()),
              'email': i18n.t_or('admin.doctor.missing.email', 'tu email', i18n.active()),
              'centro': i18n.t_or('admin.doctor.missing.center', 'dónde atendés', i18n.active())}
    return i18n.t_or('admin.doctor.missing.before_finish', 'Antes de empezar, completá {fields}.', i18n.active(),
                     fields=', '.join(labels[m] for m in missing))


def workspace_data(identity):
    doctors = deepcopy(catalog.admin_list('medicos'))
    services = catalog.admin_list('servicios')
    centers = catalog.admin_list('centros')
    if identity.get('rol') == 'medico':
        own = db_operators.operator_doctor_id(identity['usuario'])
        doctors = [d for d in doctors if d.get('id') == own and not d.get('_borrado')]
    else:
        doctors = [d for d in doctors if not d.get('_borrado')]
    from app.core import format as display_format
    for doctor in doctors:
        doctor['nombre'] = display_format.person_name(doctor.get('nombre') or '')
    rows = [dict(row, doctor_id=d['id'], doctor_name=d.get('nombre') or '') for d in doctors for row in schedule_rows(d, services, centers)]
    legacy = (doctors[0].get('nombre') or '').split() if doctors else []
    from app.patient_web import doctor_photos
    photo = doctor_photos.preview_payload(doctors[0]['id']) if identity.get('rol') == 'medico' and doctors else None
    # Location-first onboarding: every OTHER center of the network is listed
    # with the concrete next step (ask administration) instead of being
    # invisible. Which centers a doctor may attend stays admin-owned — the
    # self-service save rejects additional centers outside `doctor.centros`.
    # The existing first-center exception for an unassigned invitation stays
    # in admin_content.edit_item; this informational list grants nothing.
    other_centers = []
    if identity.get('rol') == 'medico' and doctors:
        assigned = {r['centro'] for r in rows}
        other_centers = [{'slug': c['slug'], 'name': c.get('name') or c.get('nombre') or c['slug']}
                         for c in centers if not c.get('_borrado') and c.get('slug') and c.get('slug') not in assigned]
    return {'doctors': doctors, 'photo': photo, 'schedules': rows, 'editable': any(r['editable'] for r in rows),
            'other_centers': other_centers, 'common_minutes': common_minutes(rows),
            # Only next to hours that exist: a doctor with no schedule yet
            # has nothing a holiday could close (progressive disclosure).
            'holidays': upcoming_holidays() if any(r['franjas'] for r in rows) else {},
            'first_name': ' '.join(legacy[:-1]) if len(legacy) > 1 else ' '.join(legacy), 'last_name': legacy[-1] if len(legacy)>1 else '',
            'days': [{'id': d, 'key': 'admin.content.hours.day.' + key, 'name_key': 'date.day.' + key,
                      'label': label, 'name': name}
                     for d, key, label, name in zip([1, 2, 3, 4, 5, 6, 0], ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'],
                                                    ['Lu', 'Ma', 'Mi', 'Ju', 'Vi', 'Sá', 'Do'],
                                                    ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'])],
            'specialties': catalog.specialties(),
            # Invited without a center: the onboarding asks where they see
            # patients (one select), then their hours there.
            'needs_center': bool(doctors) and not (doctors[0].get('centros') or doctors[0].get('centro')),
            'center_options': [{'slug': c['slug'], 'name': c.get('name') or c.get('nombre') or c['slug']}
                               for c in centers if c.get('slug') and not c.get('_borrado')]}


# ---- The doctor's own public profile on Cuenta (2026-10-27) ------------------
# Cuenta shows the profile and edits it in place. Only the four self-service
# fields travel, and only to the doctor linked to the session: there is no id
# in the request, so a doctor can never reach another doctor's record.

PROFILE_FIELDS = ('nombre_pila', 'apellido', 'especialidad', 'telefono')


def _own_doctor(identity):
    own = db_operators.operator_doctor_id(identity['usuario'])
    if not own:
        return None
    return next((d for d in catalog.admin_list('medicos')
                 if d.get('id') == own and not d.get('_borrado')), None)


def own_profile(identity):
    """`{item, especialidades}` for the session's doctor; `item` None when the
    account has no linked doctor."""
    from app.core import format as display_format
    doctor = _own_doctor(identity)
    if not doctor:
        return {'ok': True, 'item': None, 'especialidades': []}
    nombre = display_format.person_name(doctor.get('nombre') or '')
    legacy = nombre.split()
    return {'ok': True, 'item': {
        'nombre': nombre,
        'nombre_pila': doctor.get('nombre_pila') or (' '.join(legacy[:-1]) if len(legacy) > 1 else ' '.join(legacy)),
        'apellido': doctor.get('apellido') or (legacy[-1] if len(legacy) > 1 else ''),
        'especialidad': doctor.get('especialidad') or '',
        'telefono': doctor.get('telefono') or '',
    }, 'especialidades': [s.get('nombre') for s in catalog.specialties() if s.get('nombre')]}


def save_own_profile(identity, body):
    """Save the four fields through the doctor self-service edit (the same
    own-row/own-fields gate the onboarding uses), the schedule untouched."""
    from app.core import i18n
    doctor = _own_doctor(identity)
    if not doctor:
        raise admin_content.ContentError(404, 'cuenta_sin_medico')
    body = body or {}
    clean = {k: str(body.get(k) or '').strip() for k in PROFILE_FIELDS if k in body}
    merged = {k: clean.get(k, str(doctor.get(k) or '').strip()) for k in PROFILE_FIELDS}
    if not (merged['nombre_pila'] and merged['apellido'] and merged['especialidad']):
        raise admin_content.ContentError(400, i18n.t_or(
            'admin.srv.account.profile_required', 'Completá tu nombre, tu apellido y tu especialidad.', i18n.active()))
    if 'especialidad' in clean and clean['especialidad'] not in {s.get('nombre') for s in catalog.specialties()}:
        raise admin_content.ContentError(400, i18n.t_or(
            'admin.srv.account.profile_specialty_unknown', 'Elegí tu especialidad de la lista.', i18n.active()))
    if len(merged['telefono']) > 40:
        raise admin_content.ContentError(400, i18n.t_or(
            'admin.srv.account.profile_phone_long', 'El teléfono tiene que tener hasta 40 caracteres.', i18n.active()))
    admin_content.edit_item('medicos', doctor['id'], dict(clean, _agenda_sin_cambios=True), identity)
    return own_profile(identity)
