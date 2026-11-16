"""core/db_catalog.py — the editable overlays: catalog, translations, legal texts, REFEPS cache.

`catalog_*` (the admin's overlay of centers / services / doctors / settings, and the lock that
serializes schedule ownership), `translation*` (the machine-translation cache of the pack content),
`legal_*` (terms and privacy, admin-editable) and `refeps_*` (the license-verification cache). Split
out of `core/db.py` in stage 3 of the SQLAlchemy Core move (docs/architecture/sqlalchemy-core.md): the
statements are Core, built from `core/schema_model.py`.

Callers write `db_catalog.catalog_upsert(...)`: `core/db.py` re-exports nothing of this module (TASK-57,
`tests/test_db_domain_modules.py`). The functions reach the connection as `db._connect()`, the layers below
as `db_wire._wire`, a sibling as `db_ai.ai_register_append` and their own names bare, all at CALL time,
so a test that patches the owner patches what every caller uses.
"""

import contextlib

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import db_ai
from app.core import db_values
from app.core import db_locks
from app.core import db_wire
from app.core import schema_model as sm
from app.core import sqlcore


# ---- catálogo editable (overlay de centros/servicios/médicos) --------------
#
# La base autorada vive en webdata/*.json (catalog.py la carga y mergea);
# esta tabla es el overlay que edita el admin. Una fila por (tipo, id):
# pisa el doc si el id existe en el JSON base, lo agrega si es nuevo, y lo
# esconde con `borrado=true` sin perder el original.

def catalog_list(tipo):
    """Todas las filas (incluidas las borradas) de un tipo, orden estable por id."""
    t = sm.catalog
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire_all(sqlcore.run(cur, sa.select(t).where(t.c.type == tipo)
                                     .order_by(t.c.id)).fetchall(), "catalog")


def catalog_get(tipo, id_):
    """La fila de overlay de un id puntual, o None si no hay override."""
    t = sm.catalog
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.select(t).where(
            t.c.type == tipo, t.c.id == id_)).fetchone(), "catalog")


@contextlib.contextmanager
def catalog_schedule_lock():
    """Serialize schedule ownership checks and writes; reset/save is one transaction."""
    with db._connect() as conn, conn.cursor() as cur:
        db_locks._advisory_xact_lock(cur, "catalog-schedule-ownership")
        yield conn


def catalog_upsert(tipo, id_, doc, borrado=False, connection=None):
    """Crea o reemplaza el override de (tipo, id) con el doc completo (jsonb).
    Refresca `updated`. Devuelve la fila resultante."""
    t, br = sm.catalog, sm.booking_requests
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, conn.cursor() as cur:
        previous = None
        if tipo == 'medicos' or (tipo == 'config' and id_ == 'chat_voice'):
            db_locks._advisory_xact_lock(cur, "ai_catalog:", db_values._text_value(tipo), ":", db_values._text_value(str(id_)))
            previous = sqlcore.run(cur, sa.select(t.c.doc).where(
                t.c.type == tipo, t.c.id == id_).with_for_update()).fetchone()
        upsert = _pg.insert(t).values(type=tipo, id=id_, doc=doc, deleted=borrado,
                                      updated=sa.func.now())
        saved = db_wire._wire(sqlcore.run(cur, upsert.on_conflict_do_update(
            index_elements=[t.c.type, t.c.id],
            set_={"doc": upsert.excluded.doc, "deleted": upsert.excluded.deleted,
                  "updated": sa.func.now()},
        ).returning(*t.c)).fetchone(), "catalog")
        nuevo_nombre = str((doc or {}).get('nombre') or '').strip() if tipo == 'medicos' else ''
        if nuevo_nombre:
            # Renombrar a un médico renombra sus turnos (2026-10-23): el
            # nombre guardado de cada solicitud con su id es el del catálogo,
            # en la misma transacción que el catálogo — así lo leen igual el
            # mail, HSI, los reportes y el buscador del panel.
            sqlcore.run(cur, sa.update(br).where(
                br.c.assigned_doctor_id == str(id_),
                br.c.assigned_doctor.is_distinct_from(nuevo_nombre),
            ).values(assigned_doctor=nuevo_nombre))
        if tipo == 'medicos':
            from app.core import ai_register
            before = (previous['doc'] if previous else {}).get('entrevista_habilitada')
            db_ai.ai_register_append(ai_register.doctor_enabled_events(
                str(id_), before, doc.get('entrevista_habilitada')), connection=conn)
        if tipo == 'config' and id_ == 'chat_voice':
            from app.core import ai_register
            db_ai.ai_register_append(ai_register.voice_model_events(
                (previous['doc'] if previous else {}).get('model'), doc.get('model')), connection=conn)
            ai_register.observe_inventory(ai_register.inventory(voice_model=doc.get('model')),
                                          connection=conn, record_changes=False)
        return saved


def catalog_set_boolean_many(tipo, entries, key, value):
    """Atomically set one boolean key without replacing concurrent catalog edits.

    Existing overlay documents are JSON-merged in PostgreSQL.  A missing
    overlay gets the caller's complete current catalog document, then the
    requested key.  That preserves the base-document contract while avoiding
    stale full-document writes for existing doctors.
    """
    t = sm.catalog
    with db._connect() as conn, conn.cursor() as cur:
        rows = []
        for id_, complete_doc in entries:
            previous = None
            if tipo == 'medicos' and key == 'entrevista_habilitada':
                db_locks._advisory_xact_lock(cur, "ai_catalog:", db_values._text_value(tipo), ":", db_values._text_value(str(id_)))
                previous = sqlcore.run(cur, sa.select(t.c.doc).where(
                    t.c.type == tipo, t.c.id == id_).with_for_update()).fetchone()
            initial = dict(complete_doc)
            initial[key] = value
            upsert = _pg.insert(t).values(type=tipo, id=id_, doc=initial,
                                          deleted=sa.false(), updated=sa.func.now())
            row = sqlcore.run(cur, upsert.on_conflict_do_update(
                index_elements=[t.c.type, t.c.id],
                set_={"doc": t.c.doc.concat(sa.func.jsonb_build_object(
                          sa.cast(key, sa.Text), sa.func.to_jsonb(sa.cast(value, sa.Boolean)))),
                      "updated": sa.func.now()},
                where=t.c.deleted == sa.false(),
            ).returning(*t.c)).fetchone()
            if row is None:
                # Raising inside the connection context rolls back prior rows
                # in this batch instead of reviving a concurrently deleted one.
                raise LookupError("catalog item deleted during update")
            rows.append(db_wire._wire(row, "catalog"))
            if tipo == 'medicos' and key == 'entrevista_habilitada':
                from app.core import ai_register
                before = (previous['doc'] if previous else complete_doc).get(key)
                db_ai.ai_register_append(ai_register.doctor_enabled_events(str(id_), before, value), connection=conn)
        return rows


def catalog_set_doctor_email(doctor, email):
    """The doctor's email follows its account's (2026-11-03): one email per
    doctor, whichever screen changed it. Only the `email` key of the overlay
    moves; ``None`` removes it."""
    t = sm.catalog
    email = str(email or "").strip() or None
    initial = {k: v for k, v in doctor.items() if not k.startswith("_")}
    initial.pop("email", None)
    if email:
        initial["email"] = email
    email_key = sa.literal_column("'email'")
    merged = (t.c.doc.concat(sa.func.jsonb_build_object(email_key, sa.cast(email, sa.Text)))
              if email else t.c.doc.op("-")(email_key))
    with db._connect() as conn, conn.cursor() as cur:
        upsert = _pg.insert(t).values(
            type=sa.literal_column("'medicos'"), id=doctor["id"], doc=initial,
            deleted=sa.false(), updated=sa.func.now())
        sqlcore.run(cur, upsert.on_conflict_do_update(
            index_elements=[t.c.type, t.c.id],
            set_={"doc": merged, "updated": sa.func.now()},
            where=t.c.deleted == sa.false(),
        ))


def catalog_mark_deleted(tipo, id_, borrado, connection=None):
    """Cambia el flag borrado de una fila de overlay YA existente. No crea fila
    (para un id sin override todavía, usar catalog_upsert). Devuelve la fila
    actualizada, o None si no había fila."""
    t = sm.catalog
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.update(t).where(
            t.c.type == tipo, t.c.id == id_,
        ).values(deleted=borrado, updated=sa.func.now()).returning(*t.c)).fetchone(), "catalog")


def catalog_delete_row(tipo, id_, connection=None):
    """Hard delete del override: para un id de la base, es 'restaurar el
    original' (queda tal cual el JSON); para un id sólo-DB, lo borra del todo.
    No falla si la fila no existe."""
    t = sm.catalog
    with (contextlib.nullcontext(connection) if connection is not None else db._connect()) as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(t.c.type == tipo, t.c.id == id_))


# ---- translations (machine-translation cache for PACK CONTENT) ------------
#
# Same overlay spirit as `catalog`/`ai_prompts`, one level further out: this
# caches translate.py's OpenRouter output per (source_key, lang), keyed also
# by a hash of the source text so a content edit invalidates the cache but
# TOGGLING the active language back and forth never re-translates (see
# translate.py / docs/architecture/i18n.md). `override=true` rows are an
# admin's own edit — they win regardless of source_hash, exactly like a
# `catalog` row overriding its JSON base.

def translation_get(source_key, lang):
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.select(t).where(
            t.c.source_key == source_key, t.c.lang == lang)).fetchone(), "translations")


def translations_all(lang):
    """{source_key: row} for one language, in ONE query.

    A page render asks for hundreds of translations (every service name,
    every cluster title, every center description). One `translation_get`
    each meant hundreds of round trips per page — enough to make a listing
    page time out. `translate.py` reads the whole language at once and
    memoizes it instead."""
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t).where(t.c.lang == lang)).fetchall()
        return {r["source_key"]: db_wire._wire(r, "translations") for r in rows}


def translations_drop_pii_placeholders(lang):
    """Remove poisoned machine cache rows; preserve all human overrides."""
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.delete(t).where(
            t.c.lang == lang,
            sa.not_(sa.func.coalesce(t.c.override, sa.false())),
            t.c.content.op("~*")(r'\[(teléfono|telefono|phone|telephone|email|correo|e-mail|documento|document|nombre|name)\s+[0-9]+\]'),
        )).rowcount


def translations_drop_prefix(lang, prefix):
    """Delete cached translations whose content starts with `prefix`, and
    that no admin has overridden. Used to clear the fake provider's
    "[en] …" markers off an installation that swept before its real
    translation key was switched on. Returns how many went."""
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        deleted = sqlcore.run(cur, sa.delete(t).where(
            t.c.lang == lang, t.c.content.like(prefix + "%"),
            sa.func.coalesce(t.c.override, sa.false()) == sa.false()))
        conn.commit()
        return deleted.rowcount


def translations_delete_prefix(prefix):
    """Delete every cached translation whose `source_key` starts with
    `prefix`, in every language and overrides included — for translations of
    patient data that must go when the data goes (`labs.reading.<hash>.`).
    Returns how many went."""
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        deleted = sqlcore.run(cur, sa.delete(t).where(
            t.c.source_key.startswith(prefix, autoescape=True)))
        conn.commit()
        return deleted.rowcount


def translation_upsert(source_key, lang, source_hash, content, model=None,
                        override=False, updated_by=None):
    """Creates or replaces the cached (source_key, lang) translation.
    Called both by translate.py's machine path (override=False) and by an
    admin's manual edit (override=True, source_hash can be "" — an override
    doesn't need to match anything to keep winning)."""
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        upsert = _pg.insert(t).values(
            source_key=source_key, lang=lang, source_hash=source_hash, content=content,
            model=model, override=bool(override), updated=sa.func.now(),
            updated_by=updated_by)
        return db_wire._wire(sqlcore.run(cur, upsert.on_conflict_do_update(
            index_elements=[t.c.source_key, t.c.lang],
            set_={"source_hash": upsert.excluded.source_hash,
                  "content": upsert.excluded.content, "model": upsert.excluded.model,
                  "override": upsert.excluded.override, "updated": sa.func.now(),
                  "updated_by": upsert.excluded.updated_by},
        ).returning(*t.c)).fetchone(), "translations")


def translation_clear_override(source_key, lang):
    """Restores the machine translation: deletes the row entirely so the
    next `translate()` call re-generates it fresh (same "restore = delete
    the override row" contract as `catalog_delete_row`/`prompt_delete`)."""
    t = sm.translations
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(t.c.source_key == source_key, t.c.lang == lang))


# ---- legal_docs (terms + privacy, admin-editable) -------------------------
#
# Overlay again: no row = the text that ships in legal.py is what the site
# serves. A row exists only after someone edited that document from the
# panel, and "restore the original" is `legal_delete`, not a rewrite.

def legal_get(doc):
    t = sm.legal_docs
    with db._connect() as conn, conn.cursor() as cur:
        return db_wire._wire(sqlcore.run(cur, sa.select(t).where(t.c.doc == doc)).fetchone(),
                     "legal_docs")


def legal_save(doc, body, usuario=None):
    t = sm.legal_docs
    with db._connect() as conn, conn.cursor() as cur:
        upsert = _pg.insert(t).values(doc=doc, body=body, updated=sa.func.now(),
                                      updated_by=usuario)
        return db_wire._wire(sqlcore.run(cur, upsert.on_conflict_do_update(
            index_elements=[t.c.doc],
            set_={"body": upsert.excluded.body, "updated": sa.func.now(),
                  "updated_by": upsert.excluded.updated_by},
        ).returning(*t.c)).fetchone(), "legal_docs")


def legal_delete(doc):
    """Volver al original: se va la fila entera."""
    t = sm.legal_docs
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.delete(t).where(t.c.doc == doc))


# ---- cache de verificación de matrícula (REFEPS) ----------------------------

def refeps_get(matricula):
    """{items, checked} cacheado para ese número, o None si nunca se consultó."""
    t = sm.refeps_cache
    with db._connect() as conn, conn.cursor() as cur:
        # `t.c["items"]`: a column named `items` is shadowed by the collection's own method
        return sqlcore.run(cur, sa.select(t.c["items"], t.c.ok, t.c.checked).where(
            t.c.license_number == matricula)).fetchone()


def refeps_set(matricula, items, ok=True):
    """Guarda/actualiza el resultado de una consulta a REFEPS."""
    t = sm.refeps_cache
    with db._connect() as conn, conn.cursor() as cur:
        upsert = _pg.insert(t).values(license_number=matricula, items=items, ok=ok,
                                      checked=sa.func.now())
        return db_wire._wire(sqlcore.run(cur, upsert.on_conflict_do_update(
            index_elements=[t.c.license_number],
            set_={"items": upsert.excluded["items"], "ok": upsert.excluded.ok,
                  "checked": sa.func.now()},
        ).returning(*t.c)).fetchone(), "refeps_cache")
