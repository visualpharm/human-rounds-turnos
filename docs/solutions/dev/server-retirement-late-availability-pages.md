---
title: Extract broad dynamic pages without changing route precedence
scope: repo
area: dev
tags: [server-retirement, routing, availability]
created: 2026-08-19
refs: []
status: active
---

## Problem

The remaining public doctor and specialty renderers occupied more than 500
lines in `server.py`. Their paths are unusually broad:
`/<specialty>/` and `/<specialty>/<doctor>`.

## Root cause

Moving those patterns directly into a FastAPI router would claim every
one- or two-segment path before later module routes. FastAPI does not continue
to another router when a matched handler decides the row does not exist.

## Solution

Move the matcher, renderers, helpers, and HTML transport together into
`patient_web.availability_pages`. Keep one `handle(handler, "GET")`
delegation at the original late position in `Handler.do_GET`: after registered
routes and the generic `/api` 404, before data/static serving. Frozen synthetic
doctor and specialty bytes prove rendering did not drift; handler tests pin
status, headers, body, known-prefix refusal, and ordering. Pass the original
pre-dispatch path explicitly because compatibility handlers may rewrite
`handler.path` in place while the old matcher kept using its captured value.

## How to apply

For another broad legacy matcher, extract ownership first and preserve its
exact delegation point. Do not mount it as a native catch-all until the ASGI
composition has an explicit fallthrough contract. Freeze representative bytes
before moving code and test both handled and declined paths.
