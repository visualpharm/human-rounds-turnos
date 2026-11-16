"""mail_transport.py - what the two email connectors (Mailgun, Mailjet) share when they hand a message to their provider module.

`html` goes only when there is one: a text-only sender keeps calling the provider's `send` with exactly the
three arguments it always did. The provider module is looked up by the caller at CALL time (the suite patches
`notifications.mailgun.send` and `connectors.mailjet.mailjet.send`), so the connectors pass the module, never
a function bound at import. `tests/test_repeated_tails_contract.py` pins what each connector answered.
"""


def send(provider, to, subject, text, html=None):
    return provider.send(to, subject, text, **({"html": html} if html is not None else {}))
