"""log_pii.py — what a log line may say about a person or a secret.

Why: the installation's logs (stdout/stderr of the web process and the
worker) are read by whoever runs the server — the municipality's platform
team for salud.pinamar.gob.ar. They must be anonymous: no name, no DNI, no
phone, no email, no email subject (it can carry the patient's name), no key
or token, ever. Same allowlist spirit as `core/error_tracking` (which this
reuses for free text), applied to our own `print`/`_log` calls instead of
Sentry events.

The masks keep just enough to correlate two lines of the same case
(last digits, the email's domain) and nothing that identifies a person:

  dni("30111222")              -> "*****222"
  phone("+54 9 2254 41-5566")  -> "*********5566"
  email("ana.perez@gmail.com") -> "***@gmail.com"
  text("falló 30111222 a@b.co")-> the `error_tracking._scrub_text` sweep
  exc(e)                       -> "TypeName" / "HTTPError (502)" — never the message
  traceback_text(e)            -> frames + exception types, never a message
  install()                    -> the same for every logging record and
                                  every uncaught exception of the process

Never interpolate a name, a subject or a request/response body into a log
line — there is no mask that makes them safe; log a count or an id instead.
`tests/test_logs_pii.py` runs the real logging paths with synthetic personal
data and fails if any of it reaches stdout/stderr.
"""

import logging
import re
import sys
import threading
import traceback

from app.core.ids import digits as _digits


def phone(value):
    """Everything but the last 4 digits masked."""
    digits = _digits(value)
    return ("*" * max(0, len(digits) - 4)) + digits[-4:]


def dni(value):
    """Everything but the last 3 digits masked."""
    digits = _digits(value)
    return ("*" * max(0, len(digits) - 3)) + digits[-3:]


def email(value):
    """The local part masked, the domain kept (it says which provider failed)."""
    texto = str(value or "").strip()
    if "@" not in texto:
        return "***" if texto else ""
    return "***@" + texto.rsplit("@", 1)[1]


def emails(values):
    """A recipient list (`str` with commas or a list) — each one masked."""
    if isinstance(values, str):
        values = values.split(",")
    return ", ".join(email(v) for v in (values or []) if str(v).strip())


def key(value):
    """A conversation/session key: "tel:<phone>" and "dni:<dni>" keep their
    prefix and the same tail as `phone()`/`dni()`; any other key (a web
    session token, "interview:<id>") keeps its prefix and last 4 chars."""
    texto = str(value or "")
    prefijo, sep, resto = texto.rpartition(":")
    if not sep:
        prefijo, resto = "", texto
    if prefijo == "tel":
        cola = phone(resto)
    elif prefijo == "dni":
        cola = dni(resto)
    else:
        cola = ("*" * max(0, len(resto) - 4)) + resto[-4:]
    return f"{prefijo}:{cola}" if sep else cola


def text(value):
    """Free text that may carry personal data (an exception message, a
    provider error): emails, DNI/CUIL, phones and long numbers swept, cut to
    300 characters."""
    from app.core import error_tracking
    return error_tracking._scrub_text(value) or ""


def provider_code(value):
    """A provider's error code read from its response body (Meta's 131026):
    kept only when it is a number of up to 6 digits — anything longer could
    be a DNI or a phone, and any text is the provider echoing what it got."""
    if isinstance(value, bool):
        return ""
    texto = str(value) if isinstance(value, (int, str)) else ""
    return texto if texto.isdigit() and len(texto) <= 6 else ""


def exc(error):
    """An exception for a log line: its type and, when it has one, its HTTP
    status or errno — never its message. A provider or the database echoes
    what it was given (a name, a key), and no sweep catches a name."""
    code = getattr(error, "code", None) or getattr(error, "status", None) \
        or getattr(error, "errno", None)
    name = type(error).__name__
    if isinstance(code, int) and not isinstance(code, bool):
        return f"{name} ({code})"
    # Postgres errors carry a SQLSTATE ("23505" = unique violation): a code,
    # never a value, and what tells two failures apart.
    state = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
        return f"{name} (SQLSTATE {state})"
    return name


def traceback_text(error):
    """A traceback for a log line: the frames (file, line, function, the
    code line) and the chain of exception TYPES — never a message, so it
    is as anonymous as `exc()` and still says where it broke."""
    chain, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append(error)
        if error.__cause__ is not None:
            error = error.__cause__
        elif not error.__suppress_context__:
            error = error.__context__
        else:
            error = None
    out = []
    for i, err in enumerate(reversed(chain)):
        if i:
            out.append("\nDuring handling of the above exception, another exception occurred:\n\n")
        if err.__traceback__ is not None:
            out.append("Traceback (most recent call last):\n")
            out.extend(traceback.format_tb(err.__traceback__))
        line = exc(err)
        subs = getattr(err, "exceptions", None)
        if isinstance(subs, (list, tuple)) and subs:
            line += " [" + ", ".join(exc(s) for s in subs) + "]"
        out.append(line + "\n")
    return "".join(out)


def print_traceback(error=None, file=None):
    """`traceback.print_exc()` without values: the current (or given)
    exception through `traceback_text`, to stderr."""
    if error is None:
        error = sys.exc_info()[1]
    if error is not None:
        print(traceback_text(error), end="", file=file or sys.stderr, flush=True)


def _safe_arg(value):
    return exc(value) if isinstance(value, BaseException) else value


def _scrub_record(record):
    """What `install()` does to every LogRecord, from any library (uvicorn's
    «Exception in ASGI application», asyncio's «Task exception was never
    retrieved», procrastinate's failed job, a `logger.exception`): the
    traceback is pre-rendered without values — `Formatter` uses a ready
    `exc_text` as is — and an exception passed as the message or as an
    argument is reduced to its type."""
    info = record.exc_info
    if info and isinstance(info, tuple) and info[1] is not None and not record.exc_text:
        record.exc_text = traceback_text(info[1]).rstrip("\n")
    if isinstance(record.msg, BaseException):
        record.msg = exc(record.msg)
    if isinstance(record.args, tuple) and record.args:
        record.args = tuple(_safe_arg(a) for a in record.args)
    elif isinstance(record.args, dict) and record.args:
        record.args = {k: _safe_arg(v) for k, v in record.args.items()}
    return record


def _excepthook(kind, value, tb):
    print_traceback(value)


def _thread_excepthook(args):
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "?")
    print(f"Exception in thread {name}:", file=sys.stderr, flush=True)
    print_traceback(args.exc_value)


def install():
    """Idempotent, once per process (asgi_main.py, worker.py), before
    anything can fail: every LogRecord goes through `_scrub_record`, and an
    uncaught exception in the main thread or any other thread prints
    `traceback_text` instead of the stock traceback with its message."""
    factory = logging.getLogRecordFactory()
    if not getattr(factory, "_log_pii", False):
        def scrubbed(*args, **kwargs):
            return _scrub_record(factory(*args, **kwargs))
        scrubbed._log_pii = True
        logging.setLogRecordFactory(scrubbed)
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


class AccessLogFilter(logging.Filter):
    """uvicorn's access line logs the full request target, and the panel
    asks `/api/admin/patient-summary?dni=…` — so the target loses its query
    and fragment and its id/token path segments (`error_tracking._scrub_path`,
    the same rule the Sentry events follow). The client IP stays: it is what
    the line is for (abuse), and the privacy policy says so."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            from app.core import error_tracking
            record.args = args[:2] + (error_tracking._scrub_path(args[2]),) + args[3:]
        return True


def install_access_log_filter():
    """Idempotent: one filter on `uvicorn.access`, however many boots."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogFilter) for f in logger.filters):
        logger.addFilter(AccessLogFilter())
