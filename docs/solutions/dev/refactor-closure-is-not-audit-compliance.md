---
title: Refactor closure is not literal audit compliance
scope: repo
area: dev
tags: [audit, acceptance, refactor, evidence]
created: 2026-09-03
refs: [f5a10e76]
status: active
---

## Problem

A bounded refactor was called 100% complete by counting audit requirements
marked `DEVIATION` or `EXTERNAL` as closed. The architecture was genuinely
migrated, but the wording implied that the maintainer's literal audit had also passed.

## Root cause

One percentage combined three different scopes: executable architecture,
engineering-program decisions, and public/institutional readiness. A deliberate
scope exclusion answered “will the refactor keep running?” but not “is the audit
acceptance line met?”

## Solution

Keep three explicit completion statements. Architecture is measured by the
executable census. Engineering closure requires both managed profiles on the
exact SHA plus exact-SHA deploys and live journeys. Literal audit compliance
counts only `MET`; `DEVIATION` and `EXTERNAL` remain visible debt.

## How to apply

Before saying “100%,” name the scope and its executable proof. Never translate
an accepted deviation into a passed acceptance line. Close the bounded refactor
without suppressing verified regressions or the separate publication backlog.
