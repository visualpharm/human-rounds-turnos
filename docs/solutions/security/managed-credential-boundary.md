# Managed local credential boundary

## Decision

The active Claude Bash hook is now
`.claude/hooks/managed-credential-boundary.py`, a small structural policy.
It does not try to trace a secret through shell variables, nested syntax, or a
growing list of reader programs.  A command is either a simple credential-free
command, narrow metadata on protected local config, an exact managed profile,
or it is rejected.

The former parser and its runnable test were removed from the repository so
test discovery cannot execute an inactive credential reader.  The separate
historical decision record is intentionally non-executable; it is not an
additional control.

## Local execution

`scripts/turnos_managed_exec.py` accepts no arbitrary command text.  It
builds fixed argv for these profiles: focused/full backend tests, routes,
legacy local CI, executor-fixed ASGI local CI, ASGI app, worker, the
worker-jobs audit, the full browser gate (`e2e-asgi`) and the narrow `e2e-*`
browser diagnostics, which reuse `local-ci`'s DB-only mapping with constant
argv. The Claude hook's `_PROFILES` is exactly this set
(`test_credential_boundary` pins the equality), so a failed browser check can
be re-run alone instead of through the full local CI.  It reads a
profile mapping through `core.managed_secrets.SecretSource`, creates a fresh
child environment, sets `TURNOS_ENV_FILE` to an empty device, and calls
`execve` without a shell.  Inherited application variables, `HOME`, and
Python path controls do not reach the child.

The live managed executor has one source only: the fixed Human Rounds item in
the AI vault and one versioned JSON field. That item exists and is the local
source for managed profiles. Since 2026-10-16 the repo never starts a vault
CLI and never holds a vault token (owner rule: the credential vault through the hub's
quiet broker only). When the document is absent, `turnos_managed_exec.py`
re-executes itself once under the local agents-hub vault broker in `run` mode
(fixed interpreter, config and core paths under the user's home, fixed
environment, hop marker `TURNOS_MANAGED_BROKER_HOP`). The broker application
`turnos-managed` (in `~/.config/agents-hub-vault/config.json`, outside the
repo) runs `/opt/homebrew/bin/python3 <this script> <same args>` with
`PYTHONPATH` cleared and binds the item's `managed_profiles_v1` field to
`TURNOS_MANAGED_PROFILES_V1`. `BrokerSecretSource` parses that one value,
checks the schema and profile scope, and the executor then builds the fresh
child environment, so neither the document nor the token reaches the profile
child. A hop that still lacks the document fails closed instead of looping.
The selector is validated before the hop, so a rejected command never reaches
the vault.

`file:/absolute/path` remains a direct injected test/review seam through the
internal source API only; `source_from_environment()` and the managed CLI
reject it before opening a path. The file source uses `lstat` plus no-follow
open/fstat, requires the current uid and owner-only mode, and rejects a
resolved source inside this checkout or its sibling-worktree container.

Both checkout `.env` copies have been removed. The project `.env.example` is
now a public configuration reference only; credentials belong in the fixed
provider for managed local profiles or in production's direct environment
injection. `local-ci` never opens a checkout `.env` and managed profiles never
overlay one. The ASGI CI profile is deliberately a separate fixed profile:
it uses the same local-CI argv and DB-only `local-ci` source mapping as legacy
local CI, then injects `TURNOS_ASGI=1` after source validation. The source and
caller cannot select that mode; legacy `local-ci` remains unset.
`core.config.env_file_path()` and `core.db._load_env_file()` use the same
`TURNOS_ENV_FILE` replacement path. Coolify/production direct environment
injection remains supported and does not use the local wrapper.

## Hook contract

The interim accidental-leak hook denies ambient environment dumps
unconditionally. It checks every command-list segment and literal recursive
shell payload, then rejects source and dynamic shell forms, dynamic command
position/function/parameter/brace/substitution syntax, interpreter inline
forms (including PHP `-r`), `env -S`, globs, `xargs`, `find -exec`, `while
read`, heredocs, direct connection-string or secret-key assignment, and every
direct non-metadata read of `.env`-shaped files. Redirection is denied
globally, including harmless-looking output and input redirection, because a
structural hook cannot safely distinguish their targets. Credential-free
`&&` and literal pipeline composition remains usable. `stat`, `ls`, `test`,
and `wc` remain available for metadata; `grep -c` and `grep -l` are the only
content-scanning forms allowed on protected files; public templates such as
`.env.example` remain readable.

This is an accidental-leak boundary, not protection against a malicious
same-UID process that can inspect files, process memory, or a provider parent.
The completed source-removal migration removes checkout credential material,
but it does not protect profile-child or provider-parent values from a
malicious same-UID process.

**Codex does not execute Claude Code PreToolUse hooks.** The hook is therefore
real enforcement for a Claude Code project session only.  Codex must invoke
the managed wrapper directly for any local profile requiring credential
material; this repository does not pretend that `.claude/settings.json`
enforces Codex commands.

## Migration gate

`scripts/turnos_secret_migration.py` remains a read-only `--dry-run` and
`--self-check` audit for a user-nominated path. It reports file mode plus
protected/public/malformed counts and makes zero writes; it never emits paths,
key names, or values, and does not treat a nominated path as an approved
source. The root-controlled, nonprinting validation for the fixed provider is
`turnos_managed_exec.py --profile <profile> --check`; it fetches only the fixed
item and never runs a child command. The completed migration does not make
routine use of a nominated source path appropriate.

## Verification

`test/test_credential_boundary.py` covers every bypass family that made the
regex/data-flow parser unshippable, along with safe metadata and public-config
false positives. `test/test_managed_secrets.py`, `test/test_managed_exec.py`,
`test/test_config_secret_boundary.py`, and
`test/test_checkout_env_reader_contract.py` use synthetic fixtures only and
cover source mode, fixed-provider schema/argv, profile scoping, scrubbed child
environments, silent checks, dry-run output, and the shared effective config
path without opening checkout `.env` under `TURNOS_ENV_FILE=/dev/null`.
