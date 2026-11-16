"""status_error.py — the typed 4xx/5xx a domain function raises for its native router.

A domain function never writes a response itself: it returns the data or raises one of these with the
HTTP `status` and the JSON `body`, and the router catches it and writes both. Five domains (the patient
summary, the editable AI prompts, the interview's consult and intake admin screens, and the patient
interview) each carried the same `__init__`; they now subclass this and keep their own name, so a router
still catches only the errors of its own domain. `str(exc)` is the body's `error` when the body is a dict.
`tests/test_small_helpers_contract.py` pins what each copy answered. Two more forms follow below
(`BodyStatusError`: `str(exc)` is the body; `MessageStatusError`: the text is the payload; `RemoteError`: the
message first, an optional status after), pinned by
`tests/test_repeated_tails_contract.py`.
"""


class StatusError(Exception):
    def __init__(self, status, body):
        super().__init__(body.get("error") if isinstance(body, dict) else str(body))
        self.status = status
        self.body = body


class BodyStatusError(Exception):
    """Like `StatusError`, but `str(exc)` is the body itself (`exc.args == (body,)`): the form the demo
    access, branding, doctor photo, doctor appointment and legal domains kept."""

    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(body)


class MessageStatusError(Exception):
    """A 4xx the caller turns into `{"error": str(exc)}` at `.status`: the text is the whole payload (the
    account, preferences and doctor-patients domains of the panel)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class RemoteError(MessageStatusError):
    """A failure talking to a remote system: the message first, and the HTTP status when the server answered
    one (the HSI client and the S3 backup transport)."""

    def __init__(self, message, status=None):
        super().__init__(status, message)
