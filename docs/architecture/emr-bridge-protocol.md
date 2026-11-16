# EMR Bridge Protocol v1

Human Rounds Core communicates with an institution-owned Enterprise bridge
over HTTPS.  Core remains self-hostable: it owns the PostgreSQL journal,
leases, retries, idempotency keys, mirrors, checkpoints, reconciliation, and
the admin conflict monitor.  The bridge owns vendor wire calls, credentials,
field/state mappings, operational seed tooling, and wire recordings.

The bridge is configured only when `CONNECTOR_EMR_SYNC=emr_bridge`,
`EMR_BRIDGE_URL`, and `EMR_BRIDGE_TOKEN` are present.  There is no localhost
or vendor default.  An absent or invalid bridge configuration degrades the EMR
capability explicitly; it must not create retrying network traffic.

## Authentication and isolation

Core sends `Authorization: Bearer <EMR_BRIDGE_TOKEN>`.  The bridge must not
receive Core's database URL or write Core Postgres directly.  Core must never
send an HSI/vendor credential to the bridge.  The token is an API credential,
not a replacement for the institution's vendor credentials.

## Endpoints

`POST /v1/intents` accepts:

```json
{"operation":"create","idempotency_key":"stable-core-key","payload":{"local_request_id":"123","patient":{"national_id":"30111222","first_name":"Ana","last_name":"Paz","birth_date":"1980-01-02","gender":"X"},"specialty":"Cardiology","scheduled_at":"2026-09-02T09:30:00-03:00","professional_name":"Dr Example","overbook":false}}
```

`cancel` carries `local_request_id`, `appointment_id`, and the opaque
`checkpoint`. `reassign` adds `scheduled_at`. `attendance` adds
`attendance: "attended" | "no_show"`.

It returns `{"result":{"status":"confirmed","appointment_id":"5001","patient_id":"90001","schedule_id":"31","scheduled_at":"2026-09-02T09:30:00-03:00","checkpoint":{}}}`.
A `409` response is a checkpoint conflict and returns
`{"error":"...","remote":{...}}`. A deterministic business rejection uses
HTTP `422` with `{"error":"...","code":"slot_unavailable","retryable":false,"remote":{}}`;
Core records it as a conflict rather than retrying it as an outage.

`POST /v1/observations` accepts `from_date`, `to_date`, and
`known_patient_ids`.  It returns:

```json
{"observations":{"schedules":[{"id":"schedule-7","professional_id":"staff-2","specialty":"Cardiology","professional_name":"Dr Example"}],"appointments":[{"id":"appointment-8","schedule_id":"schedule-7","patient_id":"patient-3","scheduled_at":"2026-09-02T09:30:00-03:00","state":"scheduled","patient":{"id":"patient-3","document_number":"30111222","given_name":"Ana","family_name":"Example"}}],"patients":[{"id":"patient-3","document_number":"30111222","given_name":"Ana","family_name":"Example"}],"cursor":"optional"}}
```

Appointment states are `scheduled`, `cancelled`, `blocked`, `no_show`, or
`attended`.  A bridge maps its institution's wire state codes,
date/time representation, patient document fields, and schedule identifiers
to this contract.  Core rejects malformed observations; it does not carry a
vendor-shaped compatibility parser.

The optional cursor is reserved for batched bridge implementations.  Core v1
uses bounded date windows and previously mirrored patient IDs, never requests
a global patient enumeration.

`GET /v1/health` and `GET /v1/capabilities` provide deployment checks and
declared operation support.

`POST /v1/intents/probe` accepts:

```json
{"idempotency_key":"stable-core-key"}
```

It returns `{"found":false}` when the bridge has never settled that key, or
`{"found":true,"result":{...}}` with the exact normalized result previously
returned (or ready to be returned) by `POST /v1/intents`.  Core probes a
reclaimed journal lease before re-sending its intent so it can adopt a remote
success whose original response was lost.

The `idempotency_key` contract is mandatory, not advisory: a bridge must store
the settled result before acknowledging an intent, and every later
`POST /v1/intents` carrying the same key must return that same result without
executing the mutation again.  Core falls back to such a keyed re-send when a
bridge cannot answer a probe, answers `found:false`, or returns an unavailable
or malformed probe response.  This fallback is what prevents duplicate
appointments when the probe path itself is impaired.

## Compatibility boundary

The `hsi` connector remains the temporary, explicit legacy adapter while the
Enterprise service is built.  Its HSI HTTP client is confined to
`app/connectors/hsi/`; Core callers and the durable journal use the generic
`EmrSyncConnector` methods.  The legacy automatic routing resolver runs only
for adapters that explicitly advertise that vendor-specific capability.
