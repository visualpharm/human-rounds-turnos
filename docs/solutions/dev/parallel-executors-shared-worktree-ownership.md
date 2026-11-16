---
title: Paralelizar ejecutores en UN worktree — reparto de archivos + un solo committer
scope: repo
area: dev
tags: [parallel-agents, worktrees, git, orchestration]
created: 2026-08-05
refs: [b93ac60, docs/roadmap/tier3-harness-design.md#execution-slices]
status: active
---

## Problem
Tier 2 + Tier 3 se ejecutaron en oleadas de agentes ejecutores concurrentes
(5 pares el mismo día) sobre el MISMO worktree (`wt-tier2`, branch
`tier2-modularization`) en vez de un worktree por agente. Correr `git add` /
`git commit` desde dos ejecutores a la vez sobre el mismo `.git/index` es una
carrera de escritura: el segundo commit puede perder los cambios del primero,
duplicar hunks, o commitear un índice a medio staging.

## Root cause
Un `git commit` no es atómico entre procesos distintos que comparten el mismo
`.git/index` — no hay lock que serialice `add`+`commit` de dos agentes
lanzados en paralelo. El diseño de Tier 3 ya asume esto al enumerar los
"execution slices" como **"sequenced, each independently landable, one
executor each"** (`docs/roadmap/tier3-harness-design.md` — sección
"Execution slices") y marcando explícitamente qué pares son independientes
por archivo/DB antes de lanzarlos ("Independent of #1 (different
files/DBs)").

## Solution
Cuando el orquestador necesita paralelismo real dentro de un solo worktree:
1. **Reparto de archivos EXPLÍCITO por agente antes de lanzar** — cada
   ejecutor recibe una lista cerrada de paths que puede tocar; dos agentes
   nunca reciben el mismo archivo en la misma oleada.
2. **UN solo agente commitea, por lista explícita de paths** (`git add
   <path1> <path2> ...`, nunca `git add -A`/`git add .`) — los demás dejan
   sus ediciones sin commitear en el árbol y terminan ahí; el orquestador (o
   el agente designado) hace el commit final después de que todos
   terminaron.
3. **Cualquier archivo que aparezca en `git status` y no esté en la lista
   propia es territorio ajeno** — no tocarlo, no repararlo, no "limpiarlo".
   Si un ejecutor lo necesita, es una señal de que el reparto de archivos
   estaba mal hecho, no una invitación a resolverlo localmente.
4. Verificar el reparto contra el árbol ACTUAL antes de lanzar, no contra un
   plan escrito horas antes — mismo gotcha que
   [dev/scope-drawn-against-moving-repo.md](scope-drawn-against-moving-repo.md).

Resultado medido: 5 pares concurrentes en las oleadas de Tier 2/3, cero
choques de índice, cero commits pisados.

## How to apply
1. Antes de lanzar N ejecutores en el mismo worktree: escribir la lista de
   paths de cada uno, verificar que no se solapan.
2. Instruir a cada ejecutor: edita sólo tus paths, no commiteas (o commiteas
   sólo esos paths si sos el agente designado).
3. Esperar a que todos terminen; el commit final lista los paths
   explícitamente, nunca `-A`.
4. Si un ejecutor reporta necesitar un archivo fuera de su lista, parar y
   re-repartir — no dejar que lo edite igual.

## Related
- [dev/parallel-session-no-hunk-split.md](parallel-session-no-hunk-split.md) —
  qué hacer cuando el reparto YA falló y dos sesiones editaron el mismo
  archivo (árbol combinado, no hunk-split). Esta entrada es la prevención;
  esa es el rescate.
- [dev/scope-drawn-against-moving-repo.md](scope-drawn-against-moving-repo.md)
  — el reparto se desactualiza si el repo se mueve mientras tanto.
