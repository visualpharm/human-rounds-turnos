---
title: .git/hooks es del repo entero, no por worktree — instalar un hook rompe a los hermanos
scope: repo
area: dev
tags: [git, worktrees, hooks, pre-commit, parallel-agents]
created: 2026-08-05
refs: [ea142ed, b93ac60]
status: active
---

## Problem
Tier 3 agrega un `pre-commit` bloqueante (`scripts/pre-commit.sh`, corre la
suite frontend + el tier de rutas) instalado vía `scripts/install-hooks.sh`.
Con ~20 worktrees vivos siendo algo normal en este repo (ver
[dev/git-stash-swaps-live-worktrees.md](git-stash-swaps-live-worktrees.md)),
instalar ese hook automáticamente en cualquier momento significaría que
CUALQUIER worktree hermano — incluidos los que están en una branch anterior
a Tier 3, sin `scripts/pre-commit.sh` en su checkout — empieza a fallar sus
propios `git commit` con "no such file or directory".

## Root cause
`.git/hooks` vive en el `.git` COMÚN del repo (`git rev-parse
--git-common-dir`), no en el worktree individual — igual que el índice y el
stash que ya documenta
[dev/git-stash-swaps-live-worktrees.md](git-stash-swaps-live-worktrees.md).
`scripts/install-hooks.sh` lo confirma explícitamente: resuelve
`git rev-parse --git-common-dir` (no `--git-dir`) antes de escribir en
`hooks/pre-commit`, precisamente porque un `git-dir` por worktree normal
igual comparte ese directorio con todos los demás. Un hook instalado desde
UN worktree se ejecuta en el `git commit` de TODOS los worktrees del mismo
repo, sin importar en qué branch o qué checkout esté cada uno.

## Solution
`scripts/install-hooks.sh` existe pero **deliberadamente no se auto-instala**
(no hay `postCheckout`, no corre en ningún setup script). Instalarlo es un
paso manual, a correr sólo cuando `scripts/pre-commit.sh` (el script al que
apunta el hook) ya existe en TODAS las branches que tienen un worktree vivo
— nunca apenas se escribe el script en una sola branch de trabajo. El
bypass de emergencia (`SKIP_PRECOMMIT=1 git commit`) queda documentado en el
propio mensaje del installer para el caso de que igual se instale temprano.

## How to apply
1. Antes de correr `scripts/install-hooks.sh`: listar los worktrees vivos
   (`git worktree list`) y confirmar que cada uno tiene el script al que el
   hook apunta (`scripts/pre-commit.sh` en este caso) en su checkout — no
   sólo en `main`.
2. Si algún worktree hermano está atrás (branch vieja, sin el script): no
   instalar todavía, o coordinar con esa sesión primero.
3. Cualquier hook nuevo (`pre-commit`, `pre-push`, etc.) que un agente
   quiera instalar en este repo sigue el mismo patrón: script versionado en
   `scripts/`, installer separado, nunca auto-instalado por un `git commit`
   o `npm install` de una sola branch.
4. Si un commit falla con un hook que no reconocés (script no encontrado,
   comportamiento inesperado): sospechar primero que otra sesión instaló el
   hook desde su propio worktree — `.git/hooks/pre-commit` es compartido,
   revisar su contenido antes de asumir que es tuyo.

## Related
- [dev/git-stash-swaps-live-worktrees.md](git-stash-swaps-live-worktrees.md)
  — la misma raíz (`.git` compartido entre worktrees) aplicada a `git
  stash` en vez de a hooks.
