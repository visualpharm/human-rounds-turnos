"""Optional WebTurno slot source for private-clinic services.

MSD remains the installation default. An operator may explicitly select
WebTurno for services carrying its `clinic_token` and `idme`; the adapter
normalizes its response to the shared slot-source shape. The retired public
clinic directory remains retired, and the operator CLI remains available as
`python3 -m connectors.webturno.webturno`.
"""
