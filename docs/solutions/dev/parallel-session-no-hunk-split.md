---
title: No hunk-split cuando un subagent editó ARRIBA del trabajo de la sesión hermana
scope: repo
area: dev
tags: [git, parallel-sessions, staging, subagents]
created: 2026-07-10
refs: [43064cb, 4a15847]
status: active
---

## Problem
Dos sesiones Claude editan el repo a la vez (patrón habitual acá: A y B sobre
hospital.py/index.html). Quiero commitear SOLO mi trabajo con staging selectivo
(`git apply --cached` por hunks), pero mis edits (o los de mi subagent) se
hicieron encima del estado in-flight de la otra sesión.

## Root cause
Filtrar hunks produce un estado del archivo que NUNCA existió ni fue testeado:
mi hunk asume líneas vecinas que puso la sesión B. El commit "limpio" puede no
correr. El staging selectivo (parking 2026-07-10, sesión A) sólo es válido si
los edits son realmente independientes por zonas del archivo.

## Solution
Cuando los edits están entrelazados: NO filtrar. Esperar quiescencia del árbol
(`git diff | shasum` idéntico con 90s de separación), correr las DOS suites
sobre el árbol combinado (unittest discover + node --test), commitear los bytes
exactos testeados y push. Precedente: 4a15847 y 43064cb salieron así, verdes.

## How to apply
1. ¿Mis hunks tocan líneas escritas por la otra sesión? → árbol combinado.
2. Quiescencia: `git diff | shasum` estable 90s.
3. Suite completa sobre el árbol combinado; server :7700 reiniciado con esos bytes.
4. Commit todo junto con mensaje que nombra ambos trabajos. Nunca "medio commit".
