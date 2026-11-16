"""notifications/ — patient emails, WhatsApp/OTP transport, confirmations
and reminders.

Tier 2 batch B (docs/roadmap/tier2-physical-modularization-plan.md): a
domain package, not a module — shared infra other code calls into, never
disabled at runtime. Deliberately minimal: no re-export facade. Importers
use explicit submodules:

    from notifications import send as notifications    # notify_received/approved/rejected/reminder/offer
    from notifications import mail                       # send(): the ONE email entry point (picks the provider)
    from notifications import mailgun as email_mailgun  # Mailgun HTTP transport (default email_transport connector)
    from notifications import confirmations              # yes/no reply to the day-before reminder
    from notifications import reminders                  # day-before reminder daemon
    from notifications import otp_send                   # WhatsApp/SMS OTP + free-text transport

`send.py` keeps the name `send` rather than `notifications.py` to avoid a
submodule shadowing the package name it lives in; `mailgun.py` avoids
colliding with the stdlib `email` package.
"""
