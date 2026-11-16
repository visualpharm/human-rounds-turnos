# ASGI proxy retirement plan

Status: **IMPLEMENTED AND DUAL-TOPOLOGY VERIFIED LOCALLY** on 2026-08-20.
The exact uncommitted candidate passed managed legacy and ASGI CI across all
five surfaces before integration.

This plan removes the internal stdlib HTTP server and reverse proxy from the
`TURNOS_ASGI=1` topology. It does **not** delete the 33-line `server.py`
rollback entrypoint. The rollback stays until the physical staff-passkey gate
and the post-cutover soak are complete.

## 1. Current boundary

Production and demo both run `TURNOS_ASGI=1`. The candidate has one public
Uvicorn listener: `asgi_main._asgi_mode()` imports no legacy server, and the
final `routers.native_terminal` route handles unmatched requests. The ASGI
lifespan directly owns `ops.web_runtime` startup and module shutdown.

The monolith is already retired as a source-ownership problem:

- `server.py` is a 33-line stable entrypoint.
- `routers.legacy_http_transport` owns the compatibility handler and route
  order.
- `services.legacy_http` owns response, error, cache, MIME, and access-log
  behavior.
- `services.legacy_static` owns public-file policy and descriptor-relative
  private-pack reads.
- `ops.legacy_web_server` owns pack gating, bounded serving, signals, request
  draining, background startup, and module shutdown.

The remaining proxy dependency has four distinct causes. They must not be
collapsed into one rewrite:

1. Protocol exceptions deliberately left off ordinary router adapters:
   `GET/POST /api/whatsapp/webhook`, MCP JSON-RPC/account routes, and
   `POST /api/chat/stream` SSE.
2. Terminal public behavior: late doctor/specialty pages, allowlisted static
   files and data-pack assets, plus exact 403/404 behavior for unknown paths.
3. Process lifecycle: the internal legacy thread currently starts
   `ops.web_runtime` and owns its module-shutdown call.
4. Demo-only transport: `GET /api/demo` and the three passwordless synthetic
   session-entry routes in `ops.demo_installation`. The executable census
   found this family on 2026-08-19; it was absent from the original plan even
   though the demo smoke exercised `/admin/demo-access` on every release.

## 2. Goal and non-goals

The target `TURNOS_ASGI=1` process has one public Uvicorn listener, no
loopback legacy listener, no `legacy-server` thread, no shared proxy client,
and no request that calls `legacy_proxy.forward()`.

The target keeps:

- exact route precedence and module-disable behavior;
- exact protocol responses, streaming, signatures, status codes, headers,
  and request-body limits;
- required-pack verification before any listener or data consumer starts;
- the current worker-supervisor ordering;
- the same background-service and app-module startup/shutdown semantics;
- byte-compatible public/static/dynamic output where the current contract is
  byte-based;
- `server.py` and the dark topology as a same-revision rollback.

This work does not redesign UI, change routes, rename Spanish compatibility
URLs, alter patient data, change schemas, remove the physical-passkey gate,
or delete the rollback transport.

## 3. Slice A — executable fallback census

Status: **IMPLEMENTED AND VERIFIED** on 2026-08-19, then reverified after the
native demo transport. The category-only report is configuration-locked, has
no database URL, denies outbound sockets, and proves that a disabled module
returns its native indistinguishable 404. It also found the previously omitted
four-route demo metadata/session family; B4 now closes that gap. The combined
report classifies all 565 source rows as native, with zero terminal
compatibility and zero protocol exceptions. They represent 382 unique native
routes and no unique fallback route. It drives 287 concrete
parity/smoke/exception requests with outbound sockets denied.

The standing ASGI contract drives every path claimed to be native and fails
loudly on any outbound socket attempt.

Inputs must be derived, not hand-waved:

- the ordered FastAPI inventory in `test/tier4_composition_inventory.json`;
- every scenario family in `scripts/route_parity_check.py`;
- both smoke-script path sets;
- `/datos`, `/datos/`, one synthetic specialty page, and one synthetic doctor
  page;
- every static prefix and representative root asset type;
- the three protocol families that are now closed natively;

The report is category-only and contains no real pack row, token, cookie,
query value, or patient identifier. It emits one of `native`,
`terminal-compatibility`, or `protocol-exception` for every row. Unknown
categories fail the test. The exact exception inventory is checked as a
closed set so a new fallback cannot enter silently.

Acceptance:

- all ordinary route-parity and smoke rows are `native`;
- every concrete claimed row resolves in the native composition;
- the protocol-exception inventory is the exact empty set;
- module-disabled routes still return their native indistinguishable 404 and
  never fall through;
- the catch-all remains physically last until Slice D.

## 4. Slice B — migrate the protocol exceptions

Land each protocol separately and run full focused parity before starting the
next. Do not introduce a generic `LegacyHandlerShim` router for these shapes.

### B1. WhatsApp webhook

Status: **IMPLEMENTED AND FULLY ASGI-VERIFIED** on the combined candidate.
Exact handler/native parity, disabled-module concealment, malformed and
best-effort acknowledgments, trailing-slash compatibility, zero outbound
sockets, and event-loop responsiveness are pinned. The managed ASGI gate ran
4,491 backend tests with zero skips plus frontend, routes, cassettes, and E2E.

Create a dedicated FastAPI router beside `modules/chat/whatsapp_chat.py` and
reuse its existing Meta GET handshake, signature verification, provider
discrimination, payload normalization, media handling, and business
functions. Preserve Meta and WAHA status/body behavior exactly. Use recorded
cassettes or synthetic local media; never call live Meta, WAHA, AI, or storage
services.

Gate malformed signatures, replay/idempotency behavior, text and every media
kind, provider-disabled behavior, module-disabled 404, body limits, and zero
network under the outbound guard. The native route must win before the
catch-all in the composition inventory.

### B2. MCP JSON-RPC and account routes

Status: **IMPLEMENTED AND FULLY ASGI-VERIFIED** on the combined candidate.
The native adapter keeps JSON-RPC/account policy in the existing handler;
framing, auth, rate-limit/lease, account read/rotation, body-limit, module
disable, zero-socket, and event-loop contracts are pinned. The exact managed
ASGI gate passed 4,498 backend tests with zero skips plus frontend, routes,
cassettes, and E2E.

Give `modules/chat/mcp_api.py` a native transport boundary for `POST /mcp`,
the current GET/DELETE `/mcp` 405 response, `GET /api/account/mcp`, and
`POST /api/account/mcp/regenerate`. Preserve JSON-RPC 2.0 request IDs,
notifications, explicit batch rejection, tool errors, HTTP auth, account
ownership, file-parameter guards, and response content types. Transport
parsing belongs in the router; tool execution and authorization stay in the
existing owner.

Gate malformed JSON, wrong versions, unknown methods/tools, notifications,
authorized and unauthorized account calls, image/file parameters, module
disable, and zero provider/network access. Do not add framework-generated
validation bodies where the current API returns JSON-RPC errors.

### B3. Chat SSE

Status: **IMPLEMENTED AND FULLY ASGI-VERIFIED** on the combined candidate.
The native response keeps fast errors as ordinary JSON, emits status before a
slow safe result, splits final UTF-8 text by Python characters, joins work on
disconnect, and preserves trailing-slash compatibility. The exact managed
ASGI gate passed 4,510 backend tests with zero skips plus frontend, routes,
cassettes, and E2E.

Implement `POST /api/chat/stream` with a native `StreamingResponse`; never
buffer the complete stream into a final response. Preserve event framing,
ordering, keepalive behavior, disconnect cancellation, error termination,
authorization, request-size limits, and cleanup of generators/tasks.

Gate first-event latency, multi-event ordering, disconnect before first byte,
disconnect mid-stream, generator error, client cancellation, and no orphaned
task or open response. Use the fake AI transport and deny real sockets.

After B3, the census's protocol-exception set must be empty because B4 is
already implemented.

### B4. Demo metadata and synthetic session entry

Status: **IMPLEMENTED AND FULLY ASGI-VERIFIED** on the combined candidate.

Give `ops.demo_installation` a native router for `GET /api/demo`,
`GET /account/demo-access`, `GET /admin/demo-doctor-access`, and
`GET /admin/demo-access`. Preserve the disabled-mode indistinguishable 404,
seeded-patient/doctor preconditions, cookie bytes, no-store redirects, account
bootstrap response, and demo-only safety locks. This family is separate from
ordinary account/admin authentication: it exists only for the fictional public
sandbox and must never be enabled by the Pinamar production configuration.

Gate disabled mode, all three roles, missing seed rows, exact redirects/cookies,
read-only mutation denial after each synthetic session, and zero external
network. The four routes now classify native, and the later B1-B3 work closes
the protocol-exception set completely.

## 5. Slice C — native terminal compatibility

Status: **IMPLEMENTED AND FOCUSED-VERIFIED** on the combined candidate. The
transport-neutral page result, descriptor-held public/private static resolver,
dark/native GET/HEAD/error parity, mutation and nonregular-file refusal, and
zero-proxy census are pinned. Managed dual-topology CI remains before review.

Replace the reverse-proxy catch-all with one ASGI terminal router. It remains
last and handles only the late dynamic pages, public files/data assets, and
bounded error responses.

### C1. Late availability pages

Refactor `patient_web.availability_pages` so rendering returns an immutable
response description (status, headers, body) that both the stdlib handler and
ASGI terminal router can emit. Keep the exact matching rules:

- GET only; query ignored for matching;
- percent decoding and repeated-slash behavior unchanged;
- one segment is a specialty lookup, two a doctor lookup;
- reserved prefixes and zero/three-plus segments fall through;
- missing or corrupt snapshots fail to the existing terminal error behavior;
- runtime theme, identity, fingerprint, SEO, photo, and Logo.dev markup are
  resolved per request, never frozen at import.

The dark handler remains a consumer of the same renderer. Golden byte hashes
for the existing synthetic profile/listing matrix must remain unchanged.

### C2. Static and data-pack delivery

Split policy/resolution from stdlib emission in `services.legacy_static`.
The shared resolver returns one closed result family:

- regular public checkout file;
- regular verified private-pack file with an owned descriptor;
- branded 404 body;
- denied 403 body.

The ASGI emitter must not use `FileResponse` blindly: it adds headers the
legacy transport may not send. Preserve exact status, content type, length,
cache policy, conditional behavior, HEAD semantics, and 403/404 distinction.
For private assets, traverse from a held root descriptor with
`O_DIRECTORY|O_NOFOLLOW`, stream from the verified final descriptor, and
close it on success, disconnect, cancellation, and every exception.

Revalidate path and directory identity around reads. Never reopen a verified
private path by name. Preserve the current allowlisted extensions/prefixes,
dotfile/source/PHI denial, NUL rejection, no directory listing, and stable
`/webdata/` plus `/data/` resolution.

### C3. Terminal precedence

The terminal router runs after health, platform, and module routers. Within
it, order is:

1. late availability-page match;
2. data/static asset resolution;
3. bounded 403 or branded 404.

It must never turn an unknown path into FastAPI's JSON 404 or shadow a future
exact route. The composition guard pins it as the final route.

After C3, the fallback census must show no call to `legacy_proxy.forward()`.

## 6. Slice D — remove proxy/thread ownership from ASGI

Status: **INTEGRATED, DEPLOYED, AND DUAL-TOPOLOGY VERIFIED**. The proxy service
and catch-all router are deleted. Focused lifecycle, readiness, composition,
and single-listener gates are green. The current managed legacy and ASGI gates
each pass the backend, frontend, route, cassette replay/audit, and E2E surfaces
with the explicit expected-skip allowlist. The reviewed
candidate was integrated and deployed to production and demo on 2026-08-20;
both labels run the native one-listener topology. The operational soak closed
on 2026-08-21; unsetting `TURNOS_ASGI` remains an available same-revision
rollback.

Only after Slices B and C are green:

- replace `routers.legacy_fallback` in `app.py` with the native terminal
  router;
- remove `legacy_proxy.startup()` and `legacy_proxy.shutdown()` from the ASGI
  lifespan;
- move `ops.web_runtime.start_background_services()` and
  `ops.web_runtime.stop_app_modules()` into the ASGI lifespan, exactly once;
- change `asgi_main._proxy_mode()` into a single-listener ASGI mode: required
  pack preflight, worker supervision, then Uvicorn—no `server` import, event,
  thread, or port 7701;
- delete `routers/legacy_fallback.py` and `services/legacy_proxy.py` only when
  production imports and tests have zero references;
- remove `TURNOS_ASGI_LEGACY_PORT` from current configuration/docs;
- retain `_dark_mode()` and `server.main()` unchanged as rollback.

Startup/shutdown gates:

- required-pack failure occurs before worker spawn, background startup, or
  socket bind;
- background services start once, not once per lifespan callback or worker;
- app modules start healthy and stop once on normal exit and startup failure;
- SIGTERM drains native streaming responses and background/module ownership;
- queue worker supervision retains its current budgets and external mode;
- no process binds 7701 and no `legacy-server` thread exists;
- `/health` and `/ready` become green only under their existing contracts.

## 7. Verification ladder

Run DB-backed gates serially.

1. Per-slice focused protocol or terminal tests.
2. Fallback census with outbound sockets denied.
3. Static/security, cache, HTTP response, access-log, route-order,
   composition, module-runtime, required-pack, graceful-shutdown, and proxy
   trust suites.
4. All server-retirement suites and route parity.
5. Full Python and Node suites.
6. Managed `local-ci`, then managed `local-ci-asgi` on the exact final SHA.
7. A real subprocess proof that ASGI binds only the public port, answers
   health/readiness, and exits zero on SIGTERM with no child/thread residue.
8. Build and deploy demo first; require exact deployment SHA, full demo smoke,
   protocol cassettes against synthetic/local peers, static/dynamic route
   matrix, and current durable refresh.
9. Deploy production by plain fast-forward and exact forced deployment;
   require production smoke, unauthenticated admin denial, and non-PHI public
   route/static checks.

The live acceptance output must contain no cookies, credentials, request
payloads, pack rows, phone numbers, or patient data.

## 8. Rollback and deletion boundary

Before the physical passkey check, rollback is same-revision configuration:
unset `TURNOS_ASGI` and redeploy the exact SHA so `asgi_main._dark_mode()` calls
the retained `server.main()` entrypoint. No schema or data rollback is
involved in this plan.

Do not delete the dark transport, `server.py`, compatibility handler/mixins,
or their tests until all are true:

- production has soaked on the proxy-free ASGI topology for the accepted
  period and through at least one daily background cycle;
- a real stored hospital passkey succeeds on the deployed SHA;
- password+TOTP rollback authentication also succeeds;
- no fallback or internal-port dependency appears in logs, smoke, or the
  executable census;
- The product owner explicitly authorizes removing the same-revision rollback.

That later deletion is a separate reviewed release. Proxy retirement is not
permission to erase the rollback early.
