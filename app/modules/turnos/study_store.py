"""Postgres study store. Only token digests and sanitized anonymous evidence.

Query style (2026-10-25, SQLAlchemy Core stage 2): every statement is a Core
`select`/`insert`/`update`/`delete` over the `study_*` tables of
`core/schema_model.py`, run on the pooled cursor of `db._connect()` through
`core/sqlcore.py`; behavior is pinned function by function in
`tests/test_study_store_db.py`."""
import hashlib
import re
import secrets
from urllib.parse import urlsplit

from sqlalchemy import Integer, case, delete, func, insert, literal, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core import db, config, sqlcore
from app.core.schema_model import study_events, study_findings, study_sessions, study_task_results
from app.modules.turnos import study


def _hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def _ago(count, unit):
    """`now() - <count> * interval '1 <unit>'`."""
    return func.now() - literal(count, Integer) * literal_column(f"interval '1 {unit}'")


def create_session(role, locale, consent, user_agent_class):
    if role not in study.ROLES or consent is not True:
        raise ValueError("consent or role")
    if user_agent_class not in ("mobile", "tablet", "desktop"):
        raise ValueError("user agent class")
    token = secrets.token_urlsafe(32)
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, insert(study_sessions).values(
            token_hash=_hash(token), role=role, locale=locale if locale in ("es", "en") else "es",
            consent_at=func.now(), user_agent_class=user_agent_class))
    return token


def session(token):
    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        return None
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, select(study_sessions).where(
            study_sessions.c.token_hash == _hash(token))).fetchone()


def _locked_session(cur, token):
    return sqlcore.run(cur, select(study_sessions).where(
        study_sessions.c.token_hash == _hash(token)).with_for_update()).fetchone()


def _active(cur, token):
    # Serializes writes, finish and cap checks for the same participant.
    row = _locked_session(cur, token)
    if not row:
        raise LookupError("session")
    if row["finished_at"] is not None:
        raise ValueError("finished")
    return row


def completed(token):
    row = session(token)
    if row is None:
        raise LookupError("session")
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, select(study_task_results.c.task_id, study_task_results.c.outcome).where(
            study_task_results.c.session_id == row["id"])).fetchall()


def add_events(token, events):
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError("events")
    with db._connect() as conn, conn.cursor() as cur:
        row = _active(cur, token)
        clean = [study.sanitize_event(e, row["role"]) for e in events]
        stored = sqlcore.run(cur, select(func.count().label("n")).select_from(study_events).where(
            study_events.c.session_id == row["id"])).fetchone()["n"]
        clean = clean[:max(0, 3000 - stored)]
        for e in clean:
            sqlcore.run(cur, insert(study_events).values(
                session_id=row["id"], task_id=e["task_id"], t=e["t"], kind=e["kind"],
                target=e["target"], path=e["path"]))
        return len(clean)


def _result(cur, row, result):
    sqlcore.run(cur, pg_insert(study_task_results).values(
        session_id=row["id"], task_id=result["task_id"], outcome=result["outcome"],
        ease=result["ease"], comment=result["comment"], duration_ms=result["duration_ms"],
        started_at=_ago(result["duration_ms"], "millisecond"),
    ).on_conflict_do_nothing(index_elements=["session_id", "task_id"]))


def save_result(token, payload):
    with db._connect() as conn, conn.cursor() as cur:
        row = _active(cur, token)
        result = study.sanitize_result(payload, row["role"])
        _result(cur, row, result)
        return {"task_id": result["task_id"]}


def finish(token, answers):
    if not isinstance(answers, dict):
        raise ValueError("answers")
    if answers.get("F3", "") not in ("", "yes", "maybe", "no"):
        raise ValueError("F3")
    clean = {key: study.safe_comment(answers.get(key, "")) for key in ("F1", "F2", "F3")}
    with db._connect() as conn, conn.cursor() as cur:
        # Retrying finish is harmless (e.g. a lost acknowledgement).
        row = _locked_session(cur, token)
        if not row:
            raise LookupError("session")
        if row["finished_at"]:
            return
        for tid in sorted(study.task_ids(row["role"])):
            _result(cur, row, {"task_id": tid, "outcome": "skipped", "ease": None, "comment": "", "duration_ms": 0})
        for tid, comment in clean.items():
            _result(cur, row, {"task_id": tid, "outcome": "ok", "ease": None, "comment": comment, "duration_ms": 0})
        sqlcore.run(cur, update(study_sessions).where(
            study_sessions.c.id == row["id"]).values(finished_at=func.now()))


def report():
    with db._connect() as conn, conn.cursor() as cur:
        sessions = sqlcore.run(cur, select(
            study_sessions.c.id, study_sessions.c.role, study_sessions.c.locale,
            study_sessions.c.started_at, study_sessions.c.finished_at,
        ).order_by(study_sessions.c.id)).fetchall()
        results = sqlcore.run(cur, select(study_task_results, study_sessions.c.role).join(
            study_sessions, study_sessions.c.id == study_task_results.c.session_id,
        ).order_by(study_task_results.c.session_id, study_task_results.c.task_id)).fetchall()
        findings = sqlcore.run(cur, select(study_findings).order_by(study_findings.c.id)).fetchall()
    return {"sessions": sessions, "results": results, "findings": findings}


def run_analysis():
    # The 'auto' literal stays a literal in the ON CONFLICT predicate: Postgres
    # matches it to the partial unique index `study_findings_auto` by its text.
    auto = literal_column("'auto'")
    with db._connect() as conn, conn.cursor() as cur:
        results = sqlcore.run(cur, select(study_task_results)).fetchall()
        events = sqlcore.run(cur, select(study_events).order_by(
            study_events.c.session_id, study_events.c.t, study_events.c.id)).fetchall()
        sessions = sqlcore.run(cur, select(study_sessions.c.id, study_sessions.c.finished_at)).fetchall()
        now = sqlcore.run(cur, select(func.now().label("t"))).fetchone()["t"]
        candidates = study.analyze(results, events, sessions, now)
        for candidate in candidates:
            insertion = pg_insert(study_findings).values(
                title=candidate["title"], detail=candidate["detail"], role=candidate["role"],
                task_id=candidate["task_id"], kind=candidate["kind"], severity=candidate["severity"],
                source=auto, evidence_summary=candidate["evidence_summary"])
            sqlcore.run(cur, insertion.on_conflict_do_update(
                index_elements=[study_findings.c.task_id, study_findings.c.kind],
                index_where=study_findings.c.source == auto,
                set_={"evidence_summary": insertion.excluded.evidence_summary,
                      "detail": insertion.excluded.detail, "updated_at": func.now()}))
        return len(candidates)


def add_finding(payload, actor):
    role = payload.get("role")
    if role not in study.ROLES or payload.get("task_id") not in study.task_ids(role, final=True):
        raise ValueError("role or task")
    severity = payload.get("severity", "medium")
    title = study.safe_comment(payload.get("title", "")).strip()
    if severity not in ("low", "medium", "high") or not title:
        raise ValueError("title or severity")
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, insert(study_findings).values(
            title=title[:200], detail=study.safe_comment(payload.get("detail", "")), role=role,
            task_id=payload["task_id"], severity=severity, source="manual", decided_by=actor,
            decided_at=func.now(),
        ).returning(study_findings.c.id)).fetchone()["id"]


def update_finding(fid, payload, actor):
    status = payload.get("status")
    commit = str(payload["commit"]) if "commit" in payload else None
    url = str(payload["live_url"]) if "live_url" in payload else None
    if status not in study.STATUSES or (commit and not re.fullmatch(r"[a-fA-F0-9]{7,40}", commit)):
        raise ValueError("status or commit")
    if url:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("live URL must be https without private parameters")
    fix_note = study.safe_comment(payload["fix_note"]) if "fix_note" in payload else None
    # A reviewer can correct the evidence of a finding they wrote;
    # an automatic finding's detail stays what the analysis derived.
    detail = study.safe_comment(payload["detail"]) if "detail" in payload else None
    f = study_findings.c
    with db._connect() as conn, conn.cursor() as cur:
        row = sqlcore.run(cur, update(study_findings).where(f.id == fid).values({
            "status": status,
            "fix_note": func.coalesce(literal(fix_note), f.fix_note),
            "commit": func.coalesce(literal(commit), f.commit),
            "live_url": func.coalesce(literal(url[:1000] if url is not None else None), f.live_url),
            "detail": case((f.source == "manual", func.coalesce(literal(detail), f.detail)), else_=f.detail),
            "decided_by": actor, "decided_at": func.now(), "updated_at": func.now(),
        }).returning(f.id)).fetchone()
        if not row:
            raise LookupError("finding")


def purge(days=None, limit=1000):
    if days is None:
        days = study.retention_days()
    days = max(1, int(days))
    with db._connect() as conn, conn.cursor() as cur:
        # Match this schema's no-FK convention without orphaning research data.
        # The same row lock used by every write closes purge/write races.
        ids = [row["id"] for row in sqlcore.run(cur, select(study_sessions.c.id).where(
            study_sessions.c.started_at < _ago(days, "day")).order_by(study_sessions.c.id).limit(limit)
            .with_for_update(skip_locked=True)).fetchall()]
        if ids:
            sqlcore.run(cur, delete(study_events).where(study_events.c.session_id.in_(ids)))
            sqlcore.run(cur, delete(study_task_results).where(study_task_results.c.session_id.in_(ids)))
            sqlcore.run(cur, delete(study_sessions).where(study_sessions.c.id.in_(ids)))
        count = len(ids)
        # Decisions and summaries have the same bounded retention, no indefinite evidence archive.
        sqlcore.run(cur, delete(study_findings).where(study_findings.c.id.in_(
            select(study_findings.c.id).where(study_findings.c.created_at < _ago(days, "day")).limit(limit))))
        return count
