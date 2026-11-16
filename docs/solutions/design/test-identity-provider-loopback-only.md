# Synthetic identity provider is loopback-only

## Problem

The RENAPER/Mi Argentina simulator intentionally creates a session for any
synthetic identity in its editable registry. Treating
`TURNOS_RENAPER_PROVIDER=stub` as the only gate would turn a copied production
setting into arbitrary-DNI account takeover: a visitor could create a profile
matching a real DNI and replace that account's civil fields.

## Solution

`cuentas._test_auth_enabled(handler)` requires all of the following:

- `TURNOS_AUTH_MODE=renaper`;
- `TURNOS_RENAPER_PROVIDER=stub`;
- the HTTP socket peer is a loopback address, **or** the installation declares
  `TURNOS_RENAPER_STUB_DEPLOYED=1` **and** is a demo installation
  (`TURNOS_DEMO_MODE=1`, required since 2026-10-23).

And whatever the gate says, the simulator only enters with TEST people
(2026-10-23): a DNI that already has an account and is not a registered test
person is refused (`409 cuenta_real_no_simulable`). Until then the stub
opened that account and copied its contact data into the registry as
`origen: cuenta_existente`; such a copy is no longer listed, edited or let in,
and `scripts/fixes/purge_renaper_account_copies.py` removes the copies (dry run
by default).

The opt-in (2026-08-02) exists because Pinamar's own installation shows the
RENAPER login as its sign-in screen while the municipality's real credentials
arrive: the hospital has to see the flow it asked for, not the DNI-photo
sign-up. It is a third env nobody sets by accident, it is never inferred from
the other two, and it is only defensible where the site is not open to the
public — `pinamar.humanrounds.org` sits behind HTTP basic auth. The public
demo deliberately does NOT set it and stays on password + DNI photo.

The check uses `handler.client_address`, not `Host`, `CF-Connecting-IP`, or
`X-Forwarded-For`; those headers are not an authority for enabling a test
authentication system. Every simulator page and API route applies the same
gate. Public configuration reports password mode when the gate is unavailable,
so an unimplemented real provider or an accidentally deployed stub never
renders a dead-end federated login.

## Verification

Route tests cover local access, public-peer 404s for list/login, password
fallback for the real provider, and the deployed opt-in (public peer + public
Host reports `renaper` only with the env set —
`test_deployed_installation_can_opt_into_the_simulator`). The local simulator still presents the 3 seeded
clickable identities after the guard is enabled.
