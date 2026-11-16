---
title: git stash es de todo el repo, no por worktree
scope: repo
area: dev
tags: [git, worktrees, parallel-agents, stash]
created: 2026-08-05
refs: []
status: active
---

## Problem
Con ~20 worktrees vivos durante la traducción del sitio (~31 PRs, ~29 agentes
en paralelo), un agente corrió `git stash` para aislar un cambio propio. El
pop terminó aplicado sobre el checkout de OTRO agente: el trabajo sin
commitear de una sesión apareció en el checkout principal y una integración
lo barrió sin revisar; el worktree de otra sesión se llenó de archivos
ajenos que nunca tocó.

## Root cause
`git stash` opera sobre el `.git` compartido. Todos los worktrees de un
mismo repo comparten ese `.git` (HEAD e índice sí son por worktree, el
stash NO) — nada avisa que un `stash pop` va a aterrizar en un checkout
distinto al que generó el stash.

## Solution
Nada se perdió porque se detectó a tiempo (un diff inesperado en la sesión
que recibió el pop), pero el mecanismo en sí es peligroso apenas hay más de
un worktree activo al mismo tiempo.

## How to apply
Para aislar un cambio con worktrees vivos: copiar el archivo AFUERA del
repo, o commitear a una branch propia y comparar contra esa branch. Nunca
`git stash` mientras otra sesión pueda estar trabajando en el mismo repo —
ni "por las dudas", ni "va a ser rápido". Relacionado:
[[parallel-session-no-hunk-split]], skill `using-git-worktrees`.
