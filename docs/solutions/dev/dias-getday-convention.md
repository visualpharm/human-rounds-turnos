---
title: Días de semana cruzando JS↔Python — convención getDay() 0=domingo
scope: repo
area: dev
tags: [agenda, dias, getDay, isoweekday, catalogo, horarios]
created: 2026-07-11
refs: [catalogo.py:_validar_agenda, hospital.py:_demo_proximo, admin-contenido.js:DIAS_UI]
status: active
---

## Problem
`agenda.dias` (y `agenda_demo.dias`) viaja entre el editor del admin (JS), la
validación (Python) y el cálculo del próximo slot (Python). Las dos capas usan
numeraciones de día distintas por defecto y el bug es invisible con datos de
lunes-a-sábado: sólo el domingo delata la diferencia.

## Root cause
JS `Date.getDay()` = 0=domingo..6=sábado; Python `isoweekday()` = 1=lunes..7=domingo.
El médico demo (`dias:[1,4]` = lunes y jueves) coincide en AMBAS convenciones,
así que los datos existentes no desambiguan — un implementador eligió ISO 1-7
y los tests seguían verdes.

## Solution
Convención canónica del repo: **`dias` = JS `getDay()`, 0=domingo..6=sábado**
(igual que horarios.js/semana.js). Python matchea con `d.isoweekday() % 7`
(`hospital._demo_proximo`); `catalogo._validar_agenda` rechaza fuera de 0-6.

## How to apply
Cualquier campo nuevo con días de semana usa esta convención. Si un test de
agenda no cubre domingo, no prueba la convención — agregá un caso con 0.
