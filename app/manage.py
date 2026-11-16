#!/usr/bin/env python3
"""manage.py — operator CLI for Human Rounds — Turnos app modules, connectors and
public demo installation.

Phase 6 of the open-source rework. Three command groups:

    manage.py mod list
    manage.py mod info <id>
    manage.py mod enable <id>
    manage.py mod disable <id>
        Every app module discovered under modules/*/module.json (today:
        just `turnos` — see docs/modules.md for how a second module plugs
        in). `enable`/`disable` write through config.py exactly like `conn`
        does below (DB-backed override, ENABLED_MODULES key, same
        TURNOS_CONFIG_LOCK escape hatch) and take effect immediately — no
        restart, same as any other config.py-backed setting — because
        routing.py's dispatch() re-checks app_modules.route_enabled() on
        every request. `disable` refuses to remove the LAST enabled module.

    manage.py conn list
    manage.py conn info <id>
    manage.py conn enable <id> [--set KEY=VALUE ...]
    manage.py conn disable <capability>
        The connector registry (connectors/__init__.py) — capability,
        country, the row's routing status for its capability, and which of
        its config_keys are set (never prints the actual value — masked,
        same "presence not content" contract as the Settings admin panel).
        The `active` column is per row, from `connectors.resolve()`:
        `active (explicit)` / `active (default)` for the connector that is
        routed, `off` on EVERY row of a disabled capability, `invalid:<value>`
        on every row of a capability whose stored value names nothing usable
        (fail-closed: the default is NOT used), `no` otherwise. `enable`/
        `disable` write through config.py exactly like Settings does
        (DB-backed override) — needs a reachable DATABASE_URL, same as any
        other config.guardar() caller. Under TURNOS_CONFIG_LOCK=1 the write
        still lands but is ignored at read time until the lock is lifted
        (env/.env values win meanwhile); the CLI says so instead of refusing.

    manage.py demo seed
    manage.py demo reset
        scripts/seed_demo_all.py's fictional data for the public demo
        (demo.humanrounds.org) — `seed` deletes-then-recreates its own
        marked rows (idempotent, safe to run daily); `reset` wipes every
        transactional table first (including whatever a visitor typed).
        Both hard no-op unless TURNOS_DEMO_MODE is on, even against a
        reachable DATABASE_URL — never touches a real installation.

No third-party deps — stdlib argparse only, so this runs in the same bare
Python 3 environment as server.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

if not __package__:  # run as a file (`python3 app/manage.py`): the project root comes first
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import log_pii  # noqa: E402
from app.core import paths  # noqa: E402

ROOT = paths.PROJECT_ROOT


# ---- mod ----------------------------------------------------------------
#
# app_modules.py owns discovery + enable/disable state — one engine, shared
# with routing.py's dispatch() and the admin API (GET /api/admin/modules).
# This file just formats its output for the CLI.

def cmd_mod_list(args):
    from app.core import app_modules

    modules = app_modules.list_modules()
    if not modules:
        print("(no hay módulos registrados bajo modules/)")
        return 0
    for m in modules:
        estado = m.get("status", "enabled" if m.get("enabled") else "disabled")
        print(f"{m['id']:<12} {estado:<10} {m.get('description', '')[:80]}")
        if m.get("error"):
            print(f"             -> {m['error']}")
    return 0


def cmd_mod_info(args):
    from app.core import app_modules

    manifest = app_modules.load_manifest(args.id)
    if not manifest:
        print(f"módulo desconocido: {args.id!r}", file=sys.stderr)
        return 1
    print(f"id:          {manifest['id']}")
    print(f"name:        {manifest.get('name', '')}")
    print(f"status:      {manifest.get('status', 'enabled' if app_modules.is_enabled(args.id) else 'disabled')}")
    if manifest.get("error"):
        print(f"error:       {manifest['error']}")
    print(f"requires:    {', '.join(manifest.get('requires') or []) or '(ninguno)'}")
    print(f"entrypoint:  {manifest.get('entrypoint') or '(ninguno)'}")
    print(f"description: {manifest.get('description', '')}")
    routes = ", ".join(f"{r['module']} [{'/'.join(r['methods'])}]" for r in manifest.get("routes") or [])
    print(f"routes:         {routes}")
    print(f"admin_sections: {', '.join(s['id'] for s in manifest.get('admin_sections') or [])}")
    print(f"db_tables:      {', '.join(manifest.get('db_tables') or [])}")
    return 0


def cmd_mod_enable(args):
    from app.core import app_modules

    try:
        app_modules.enable(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — most likely: no reachable DATABASE_URL
        print(f"no se pudo guardar (¿DATABASE_URL alcanzable?): {log_pii.exc(exc)}", file=sys.stderr)
        return 1
    print(f"{args.id}: habilitado")
    return 0


def cmd_mod_disable(args):
    from app.core import app_modules

    try:
        app_modules.disable(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"no se pudo guardar (¿DATABASE_URL alcanzable?): {log_pii.exc(exc)}", file=sys.stderr)
        return 1
    print(f"{args.id}: deshabilitado")
    return 0


# ---- conn -----------------------------------------------------------------

# Exact operator-facing diagnostics (English, pinned by tests/test_manage_cli.py).
_LOCK_READ_NOTE = "(TURNOS_CONFIG_LOCK=1: DB values are ignored)"
_LOCK_SAVED_NOTE = ("saved, but TURNOS_CONFIG_LOCK=1: env/.env values take "
                    "precedence until the lock is removed")


def _conn_row_status(connector_id, capability, resolved):
    """The `active` cell for one connector row, given `connectors.resolve()`'s
    answer for its capability — defined PER ROW so capabilities with several
    implementers (slot_source: msd + webturno; clinical_history_lookup;
    otp_transport) read correctly: `off`/`invalid:<value>` on every row of
    that capability, `active (<source>)` only on the routed one, `no` else."""
    from app import connectors

    connector_id_active, source = resolved
    if source == "off":
        return "off"
    if source == "invalid":
        return f"invalid:{connectors.active_connector_id(capability)}"
    if connector_id_active == connector_id:
        return f"active ({source})"
    return "no"


def cmd_conn_list(args):
    from app import connectors
    from app.core import config

    manifests = connectors.list_connectors()
    if not manifests:
        print("(no hay connectors registrados bajo connectors/)")
        return 0
    if config.locked():
        print(_LOCK_READ_NOTE)
    header = f"{'id':<12} {'capability':<22} {'country':<8} {'active':<18} config_keys"
    print(header)
    print("-" * len(header))
    resolved = {}
    for m in manifests:
        capability = m["capability"]
        if capability not in resolved:
            resolved[capability] = connectors.resolve(capability)
        row_status = _conn_row_status(m["id"], capability, resolved[capability])
        status = connectors.config_key_status(m["id"])
        keys_str = ", ".join(f"{k}={v}" for k, v in status.items()) or "(none)"
        print(f"{m['id']:<12} {capability:<22} {m.get('country', '?'):<8} {row_status:<18} {keys_str}")
    return 0


def cmd_conn_info(args):
    from app import connectors
    from app.core import config

    manifest = connectors.load_manifest(args.id)
    if not manifest:
        print(f"connector desconocido: {args.id!r}", file=sys.stderr)
        return 1
    if config.locked():
        print(_LOCK_READ_NOTE)
    capability = manifest["capability"]
    resolved = connectors.resolve(capability)
    active_id, source = resolved
    print(f"id:          {manifest['id']}")
    print(f"capability:  {capability}")
    print(f"country:     {manifest.get('country', '?')}")
    print(f"active:      {_conn_row_status(manifest['id'], capability, resolved)}"
          + (f" (active for this capability: {active_id}, {source})"
             if active_id and active_id != manifest["id"] else ""))
    print(f"description: {manifest.get('description', '')}")
    status = connectors.config_key_status(manifest["id"])
    if status:
        print("config_keys:")
        for key, state in status.items():
            print(f"  {key:<32} {state}")
    else:
        print("config_keys: (none required)")
    return 0


def _parse_set_values(pairs):
    values = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise SystemExit(f"--set espera KEY=VALUE, recibí: {raw!r}")
        key, _, value = raw.partition("=")
        values[key.strip()] = value
    return values


def cmd_conn_enable(args):
    from app import connectors

    try:
        values = _parse_set_values(args.set)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        connectors.enable(args.id, values)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — most likely: no reachable DATABASE_URL
        print(f"no se pudo guardar (¿DATABASE_URL alcanzable?): {log_pii.exc(exc)}", file=sys.stderr)
        return 1
    manifest = connectors.load_manifest(args.id)
    print(f"{args.id} habilitado para {manifest['capability']}"
          + (f" (seteadas: {', '.join(values)})" if values else ""))
    from app.core import config
    if config.locked():
        print(_LOCK_SAVED_NOTE, file=sys.stderr)
    missing = [k for k, v in connectors.config_key_status(args.id).items() if v == "missing"]
    if missing:
        print(f"OJO: todavía faltan config_keys: {', '.join(missing)}", file=sys.stderr)
    return 0


def cmd_conn_disable(args):
    from app import connectors

    if args.id not in connectors.CAPABILITIES:
        # Ergonomía: aceptar también un connector id y resolver su capability.
        manifest = connectors.load_manifest(args.id)
        if not manifest:
            print(f"ni una capability ni un connector reconocido: {args.id!r}", file=sys.stderr)
            return 1
        capability = manifest["capability"]
    else:
        capability = args.id
    try:
        connectors.disable(capability)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"no se pudo guardar (¿DATABASE_URL alcanzable?): {log_pii.exc(exc)}", file=sys.stderr)
        return 1
    # `disable` stores the reserved `off` sentinel: the capability degrades
    # per its contract and the installation default is NOT consulted — this
    # is "off", not "back to default". `conn enable <id>` overwrites it.
    print(f"{capability}: off (no connector routed; degrades per its contract "
          f"until `conn enable <id>`)")
    from app.core import config
    if config.locked():
        print(_LOCK_SAVED_NOTE, file=sys.stderr)
    return 0


# ---- auth -----------------------------------------------------------------

def cmd_auth_password_inventory(args):
    """Print aggregate legacy-password counts without identities or hashes."""
    from app.core import db_accounts

    try:
        counts = db_accounts.legacy_password_hash_inventory()
    except Exception:  # noqa: BLE001 — operator gets a value-free failure
        print("password inventory unavailable (check DATABASE_URL)", file=sys.stderr)
        return 2
    total = sum(counts.values())
    for label in ("patients", "staff", "owners"):
        print(f"{label}: {counts[label]}")
    print(f"total: {total}")
    if args.require_zero and total:
        print("legacy password compatibility is still required", file=sys.stderr)
        return 1
    return 0


# ---- demo -------------------------------------------------------------------
#
# scripts/seed_demo_all.py owns the actual logic (importable, also runnable
# standalone) — this is just the CLI wiring, same shape as `mod`/`conn`
# above. Both subcommands hard no-op unless TURNOS_DEMO_MODE is on, even if
# DATABASE_URL happens to point at a real installation.

def cmd_demo_seed(args):
    import scripts.seed_demo_all as seed_demo_all

    seed_demo_all.seed(force=getattr(args, "force", False))
    return 0


def cmd_demo_reset(args):
    import scripts.seed_demo_all as seed_demo_all

    seed_demo_all.reset()
    return 0


# ---- CLI --------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="manage.py", description="App modules + connectors CLI (Phase 6).")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="encrypted installation backup and explicit recovery")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_sub.add_parser("run", help="take and verify one complete encrypted snapshot").set_defaults(func=cmd_backup)
    verify = backup_sub.add_parser("verify", help="download, decrypt and hash-check the latest upload")
    verify.add_argument("--restore-test", action="store_true", help="also restore into the configured isolated scratch cluster")
    verify.set_defaults(func=cmd_backup)
    restore = backup_sub.add_parser("restore", help="restore an archive only into a new directory")
    restore.add_argument("archive")
    restore.add_argument("--into", required=True)
    restore.add_argument("--key-file", help="operator saved recovery key file; no server config required")
    restore.add_argument("--database-url", help="explicit empty target DB (prefer --database-url-file to keep credentials off argv)")
    restore.add_argument("--database-url-file", help="owner-only file with the explicit empty target DB URL")
    restore.set_defaults(func=cmd_backup)

    mod = sub.add_parser("mod", help="app modules (modules/*/module.json)")
    mod_sub = mod.add_subparsers(dest="mod_command", required=True)
    mod_sub.add_parser("list", help="list registered app modules").set_defaults(func=cmd_mod_list)

    mod_info = mod_sub.add_parser("info", help="detail for one app module")
    mod_info.add_argument("id")
    mod_info.set_defaults(func=cmd_mod_info)

    mod_enable = mod_sub.add_parser("enable", help="enable an app module")
    mod_enable.add_argument("id")
    mod_enable.set_defaults(func=cmd_mod_enable)

    mod_disable = mod_sub.add_parser("disable", help="disable an app module (refused if it's the last one enabled)")
    mod_disable.add_argument("id")
    mod_disable.set_defaults(func=cmd_mod_disable)

    conn = sub.add_parser("conn", help="connectors (connectors/*/connector.json)")
    conn_sub = conn.add_subparsers(dest="conn_command", required=True)
    conn_sub.add_parser("list", help="list connectors + config-key status").set_defaults(func=cmd_conn_list)

    conn_info = conn_sub.add_parser("info", help="detail for one connector")
    conn_info.add_argument("id")
    conn_info.set_defaults(func=cmd_conn_info)

    conn_enable = conn_sub.add_parser("enable", help="make <id> the active connector for its capability")
    conn_enable.add_argument("id")
    conn_enable.add_argument("--set", action="append", metavar="KEY=VALUE",
                              help="also persist a config key (repeatable)")
    conn_enable.set_defaults(func=cmd_conn_enable)

    conn_disable = conn_sub.add_parser("disable", help="switch a capability off (stores the reserved `off` sentinel; the default is not used)")
    conn_disable.add_argument("id", help="a capability name, or a connector id (its capability is switched off)")
    conn_disable.set_defaults(func=cmd_conn_disable)

    auth = sub.add_parser("auth", help="authentication migration operations")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_inventory = auth_sub.add_parser(
        "password-inventory",
        help="aggregate password hashes that still require legacy verification",
    )
    auth_inventory.add_argument(
        "--require-zero", action="store_true",
        help="exit non-zero while any legacy password row remains",
    )
    auth_inventory.set_defaults(func=cmd_auth_password_inventory)

    demo = sub.add_parser("demo", help="public demo installation (scripts/seed_demo_all.py)")
    demo_sub = demo.add_subparsers(dest="demo_command", required=True)
    demo_seed = demo_sub.add_parser(
        "seed", help="marker-based delete-then-recreate of the demo's fictional data (idempotent); "
                     "skips the recreate when today is already seeded unless --force"
    )
    demo_seed.add_argument(
        "--force", action="store_true",
        help="reseed even if today's data is already there (photos/language always refresh either way)")
    demo_seed.set_defaults(func=cmd_demo_seed)
    demo_sub.add_parser(
        "reset", help="wipe every transactional table (incl. visitor junk) and reseed"
    ).set_defaults(func=cmd_demo_reset)

    return parser


def cmd_backup(args):
    from pathlib import Path
    from app.core import backup_crypto, backup_settings, config, db
    from app.ops import backup_full
    # Importing already read config once, before the database could answer; drop that empty answer.
    config._invalidate_db_cache()
    try:
        if args.backup_command == "run":
            result = backup_full.run()
        elif args.backup_command == "verify":
            result = backup_full.verify(restore_test=args.restore_test)
        else:
            keys = backup_settings.keys() if not args.key_file else {}
            if args.key_file:
                text = Path(args.key_file).read_text().strip()
                key = backup_crypto.decode_key(text)
                keys[backup_crypto.key_id(key)] = key
            url = args.database_url
            if args.database_url_file:
                p = Path(args.database_url_file)
                if p.is_symlink() or p.stat().st_mode & 0o077:
                    raise ValueError("Database URL file must be owner-only and not a symlink")
                url = p.read_text().strip()
            result = backup_full.restore_local(args.archive, args.into, keys, database_url=url, live_url=db._db_url())
        # Only public operational fields, never manifest file paths/credentials.
        print(json.dumps({k: result.get(k) for k in ("id", "archive_id", "status", "uploaded", "integrity_verified", "restore_tested", "reference_check") if k in result}))
        return 0
    except Exception as exc:
        # Tool/auth exceptions can embed credential-bearing URLs. Output only
        # reviewed domain errors, never arbitrary exception strings.
        safe = isinstance(exc, (backup_full.BackupError, backup_crypto.FormatError))
        print(str(exc) if safe else "Backup operation failed; check destination, key and installation configuration", file=sys.stderr)
        return 1


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
