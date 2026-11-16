---
title: Password hash migration must lock every credential row
scope: repo
area: security
tags: [passwords, argon2, migration, postgres, auth]
created: 2026-09-03
refs: [maintainer audit row 3.2]
status: active
---

## Problem

Replacing the hand-written password KDF could silently lock out one account
class, overwrite a concurrent password reset, or leave a writer producing the
retired format. Human Rounds has three stores: patient, staff, and owner.

## Root cause

Password hashing was treated as one helper swap even though verification and
writes enter through several independent routes. A read outside the update
transaction also makes a later migration write race a password reset.

## Solution

New writes use `pwdlib`'s Argon2id implementation. The bounded migration
adapter accepts only the exact historical scrypt envelope and delegates KDF
verification to `cryptography`. On successful legacy verification, the owning
patient, staff, or owner row is locked and upgraded in the same transaction.
Malformed, unknown, inactive, and wrong-password cases fail without mutation.

## How to apply

Inventory every writer and verifier before changing a credential format. Test
every store, every role, every writer, wrong credentials, malformed envelopes,
and the real password + TOTP + passkey browser journey. Put `FOR UPDATE` on the
verification read, not on unrelated getters, and never accept a precomputed
setup hash unless it is structurally the current format.
