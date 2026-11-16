"""core/subject_export.py — the copy of one person's data (Ley 25.326 art. 14, P2).

READ ONLY: the whole export runs in a read-only transaction; nothing in this
module writes to the database. What it reads is decided by
`core/personal_data.REGISTRY` — one JSON file per registry entry, plus the
files that live on disk (referral images / PDFs, the profile photo, the lab
files the person uploaded), plus a manifest that lists EVERY place searched,
the ones that came back empty included, and what is not reachable from here
(anonymous chats, HSI, processors, backups).

Secrets never leave: tokens, password hashes and one-time codes are dropped by
column (`Entry.secret`); the lab file bytes go out as files, not as hex in JSON.

The person is identified by the clinical DNI; the phone and the email widen the
search to the places keyed by them (the WhatsApp thread, one-time codes, the
waiting list). A phone or email that ALSO belongs to another account is left out
and said so in the manifest — a thread shared with someone else is not handed to
the first person who asks (`resolve_subject(..., confirm_shared=True)` is the
operator's explicit word that it is theirs).

A dependent is another person: the requests a guardian made FOR a dependent are
not part of the guardian's copy (the manifest counts them; the dependent gets
theirs on their own request), and the lines other accounts hold about a MINOR
are not handed to the minor.
"""
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from app.core import access_log, db, personal_data, sqlcore, tz
from app.core import db_values
from app.core.personal_data import Subject
from app.core.schema_model import booking_requests, metadata, patient_lab_uploads, users

# The catalog view `_bytea_columns` reads (not a table of the model).
_columns_view = sa.table("columns", sa.column("column_name"), sa.column("table_schema"),
                         sa.column("table_name"), sa.column("data_type"), schema="information_schema")

_DNI = re.compile(r"^\d{7,9}$")

# Not reachable from the database — the manifest says so, with who to ask.
NOT_REACHABLE = (
    {"what": "anonymous web chat (keyed by IP)",
     "why": "an IP address is not an identity; it can be searched only on a precise request "
            "with the IP and a time window"},
    {"what": "clinical record kept in HSI",
     "why": "HSI is the province's system of record; ask its administrator"},
    {"what": "copies at external processors (AI provider, e-mail provider, WhatsApp, voice "
            "transcription)", "why": "they hold their own copy under their own terms"},
    {"what": "encrypted backups", "why": "older archives keep what has since been erased until "
            "they rotate out"},
    {"what": "staff access log (who opened the record)",
     "why": "evidence of staff access, not the person's data; disclosure is for the hospital's "
            "counsel to decide"},
)


def clean_dni(value):
    """The DNI's digits when they are 7 to 9 of them, else ""."""
    digits = access_log.clean_dni(value)
    return digits if _DNI.match(digits) else ""


def _last10(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def mask(value, keep=3):
    value = str(value or "")
    return ("*" * max(0, len(value) - keep)) + value[-keep:] if value else ""


def resolve_subject(dni, phone=None, email=None, confirm_shared=False):
    """The `Subject` for a DNI: the phone and email default to the account's own.
    One that another account also uses is left out (and noted) unless
    `confirm_shared`. Raises ValueError for a DNI that isn't one."""
    digits = clean_dni(dni)
    if not digits:
        raise ValueError("not a DNI: 7 to 9 digits")
    notes = []
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.text("SET LOCAL transaction_read_only = on"))
        user = sqlcore.run(cur, sa.select(users.c.contact, users.c.email).where(
            users.c.dni == digits)).fetchone() or {}
        phone10 = _last10(phone) or _last10(user.get("contact"))
        mail = str(email or user.get("email") or "").strip().lower()
        if phone10 and not confirm_shared:
            if sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(users).where(
                    users.c.dni != digits,
                    db_values._last_ten_digits(sa.func.coalesce(users.c.contact, sa.literal_column("''")))
                    == phone10
            ).limit(sa.literal_column("1"))).fetchone():
                notes.append("the phone is also on another account: the places keyed by phone "
                             "(WhatsApp thread, one-time codes) were left out")
                phone10 = ""
        if mail and not confirm_shared:
            if sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(users).where(
                    users.c.dni != digits, sa.func.lower(users.c.email) == mail
            ).limit(sa.literal_column("1"))).fetchone():
                notes.append("the email is also on another account: the places keyed by email "
                             "(waiting list) were left out")
                mail = ""
    return Subject(digits, phone10=phone10, emails=(mail,) if mail else (),
                   dni_hash=access_log.dni_hash(digits), notes=tuple(notes))


def _bytea_columns(cur, table):
    c = _columns_view.c
    return [r["column_name"] for r in sqlcore.run(cur, sa.select(c.column_name).where(
        c.table_schema == sa.literal_column("'public'"), c.table_name == table,
        c.data_type == sa.literal_column("'bytea'"))).fetchall()]


def _select_rows(cur, entry, subject, extra=None):
    """The entry's rows of the subject as JSON objects, secrets and bytes removed.
    `extra` is a further Core condition."""
    t = metadata.tables[entry.table].alias("t")
    drop = list(entry.secret)
    byte_sizes = []
    for col in _bytea_columns(cur, entry.table):
        drop.append(col)
        byte_sizes.append(sa.func.jsonb_build_object(
            sa.literal_column("'%s_bytes'" % col.replace("'", "''")), sa.func.length(t.c[col])))
    expr = sa.func.to_jsonb(sa.literal_column("t")).op("-")(
        sa.cast(sa.literal(drop, ARRAY(sa.Text)), ARRAY(sa.Text)))
    for size in byte_sizes:
        expr = expr.op("||")(size)
    where = personal_data.where_clause(entry, subject)
    if extra is not None:
        where = sa.and_(where, extra)
    return [r["row"] for r in sqlcore.run(cur, sa.select(expr.label("row")).select_from(t)
                                          .where(where)).fetchall()]


def _is_minor(cur, dni):
    row = sqlcore.run(cur, sa.select(users.c.birth_date).where(users.c.dni == dni)).fetchone()
    born = row and row.get("birth_date")
    if not born:
        return False  # unknown age: the person asking is treated as of age, they showed their DNI
    today = datetime.now(tz.tzinfo()).date()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years < 18


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "file"))[:80] or "file"


def export_subject(subject, fileobj, now=None):
    """Write the person's data as a zip into `fileobj` (binary, seekable) and
    return the manifest (also the zip's `manifest.json`)."""
    now = now or datetime.now(tz.tzinfo())  # the installation's clock, like the ledger's dates
    searched = []
    files = []
    manifest = {
        "generated_at": now.isoformat(timespec="seconds"),
        "subject": {"dni": mask(subject.dni), "dni_hash": subject.dni_hash,
                    "phone": mask(subject.phone10), "emails": [mask(e, 8) for e in subject.emails]},
        "read_only": True,
        "notes": list(subject.notes),
        "searched": searched,
        "files": files,
        "not_reachable": list(NOT_REACHABLE),
    }
    with db._connect() as conn, conn.cursor() as cur, \
            zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as z:
        sqlcore.run(cur, sa.text("SET LOCAL transaction_read_only = on"))
        minor = _is_minor(cur, subject.dni)
        for entry in personal_data.REGISTRY:
            item = {"table": entry.table, "part": entry.part, "erase_class": entry.erase}
            if not entry.export:
                item.update(exported=False, withheld=entry.withheld)
                searched.append(item)
                continue
            extra = None
            if entry.table == "dependents" and minor:
                # a minor's own lines (where they are the dependent) are the guardian's to ask for
                extra = sa.column("adult_dni") == sa.cast(sa.literal(subject.dni, sa.Text), sa.Text)
                item["note"] = ("lines other accounts hold about this person as a dependent were "
                                "left out: the person is a minor")
            rows = _select_rows(cur, entry, subject, extra)
            item.update(exported=True, found=len(rows))
            searched.append(item)
            z.writestr(f"data/{entry.label}.json",
                       json.dumps(rows, ensure_ascii=False, indent=1, default=str))
        made_for_others = sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(
            booking_requests).where(booking_requests.c.requester_dni == subject.dni,
                                    booking_requests.c.dni != subject.dni)).fetchone()["n"]
        manifest["requests_made_for_others"] = {
            "count": made_for_others,
            "why": "they are the data of the dependents, who receive their own copy on their own "
                   "request"}
        _add_files(cur, z, subject, files)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


def _add_files(cur, z, subject, files):
    """Referral files, the profile photo and the lab files the person uploaded."""
    b = booking_requests.c
    for r in sqlcore.run(cur, sa.select(b.id, b.referral_path).where(
            b.dni == subject.dni, b.referral_path.is_not(None))).fetchall():
        path = r["referral_path"]
        item = {"kind": "referral", "request_id": r["id"]}
        if path and os.path.isfile(path):
            z.write(path, f"files/referrals/{r['id']}{os.path.splitext(path)[1]}")
            item["included"] = True
        else:
            item.update(included=False, why="the file is no longer on disk (retention sweep)")
        files.append(item)
    from app.cuentas import account_photo
    for p in account_photo.photo_paths(subject.dni)[:1]:
        z.write(p, f"files/profile-photo{os.path.splitext(p)[1]}")
        files.append({"kind": "profile_photo", "included": True})
    # Only the person's own files (`dni`): one a guardian uploaded for a dependent is the
    # dependent's. Ids first, then the bytes one file at a time — a patient with dozens of
    # multi-MB PDFs must not be held in memory at once.
    lab = patient_lab_uploads.c
    for r in sqlcore.run(cur, sa.select(lab.id, lab.deleted, lab.archivo.is_not(None).label("has_bytes"))
                         .where(lab.dni == subject.dni).order_by(lab.id)).fetchall():
        item = {"kind": "lab_upload", "id": r["id"], "deleted": r["deleted"] is not None,
                "included": False}
        if r["has_bytes"]:
            blob = sqlcore.run(cur, sa.select(lab.filename, lab.archivo).where(lab.id == r["id"])).fetchone()
            z.writestr(f"files/lab/{r['id']}-{_safe_name(blob['filename'])}", bytes(blob["archivo"]))
            item["included"] = True
        files.append(item)


def export_to_path(subject, path):
    """Write the zip to `path` (0600) and return the manifest."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w+b") as fh:
        return export_subject(subject, fh)


def export_to_tempfile(subject):
    """(manifest, size, file): the zip in a temp file at position 0, spilled to
    disk above 8 MB — the lab files can be large. The CALLER closes the file
    (`iter_and_close` does it when it is streamed)."""
    tmp = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        manifest = export_subject(subject, tmp)
        size = tmp.seek(0, os.SEEK_END)
        tmp.seek(0)
    except BaseException:
        tmp.close()
        raise
    return manifest, size, tmp


def iter_and_close(fh, chunk=64 * 1024):
    """Stream a file in chunks and close it, whether or not the client reads it all."""
    try:
        while True:
            block = fh.read(chunk)
            if not block:
                return
            yield block
    finally:
        fh.close()


def export_to_bytes(subject):
    """The zip as bytes: (manifest, bytes). For small cases and tests; the admin
    action streams `export_to_tempfile` instead."""
    manifest, _size, tmp = export_to_tempfile(subject)
    with tmp:
        return manifest, tmp.read()
