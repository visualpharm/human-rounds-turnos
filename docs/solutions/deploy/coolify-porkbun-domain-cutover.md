---
title: Coolify and Porkbun custom-domain cutover without breaking old hosts
scope: repo
area: deploy
tags: [coolify, porkbun, dns, tls, redirects]
created: 2026-07-30
refs: [5f1d150, faf4d2b]
status: active
---

## Problem

Move a live Coolify app to a Porkbun-managed hostname while preserving every
old path, query string, TLS, and the pre-launch Basic Auth gate.

## Root cause

DNS alone does not bind a hostname to Coolify or issue its certificate. The
old hostname also returned Basic Auth before application routing, so an
application-level redirect placed after that gate was invisible to visitors.
Recursive resolvers can retain Porkbun's deleted wildcard parking answer for
its full TTL even after authoritative DNS is correct.

## Solution

Add both new and old HTTPS hostnames to Coolify using the API's `domains`
field (not the GET response's `fqdn` field). Redeploy after the binding.
Replace only Porkbun's parking ALIAS/wildcard records with A records to the
Coolify origin, using dry runs first. Redirect the old host with 308 before
Basic Auth, preserving `handler.path` so the query survives. Update canonical
metadata and runtime public-origin settings in the same cutover.

## How to apply

Verify authoritative DNS plus several public resolvers, then validate TLS
directly against the origin with the intended SNI if a local resolver is
still cached. Require 8-12 identical live responses for the new host and old
redirect before declaring the rolling deployment stable.
