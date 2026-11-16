# Offline AI-profile conformance benchmark

`scripts/bench_ai_profiles.py` is an offline transport-and-caller conformance
harness. It is not a provider, model-quality, clinical-quality, residency,
latency, capacity, or cost benchmark.

It runs the three supported inference profiles (`cloud_shared`,
`hosted_jurisdiction`, and `self_hosted_vllm`) against the same deterministic
fake transport. No provider endpoint is contacted. Placeholder destinations
and keys exercise the profile routing contract without exposing credentials.

The harness verifies the public synthetic referral corpus manifest and golden
file before it opens any JPEG. It then invokes the production callers for:

- all 15 referral fixtures through `ai_referral.analyze_referral_3pass`;
- one complete `chat_agent.chat_full` turn whose required
  `search_services` tool call is executed through an injected offline tool;
- one catalog translation through `translate._call_openrouter`;
- one DNI OCR request through `ocr_dni.leer_frente`.

The output has exactly three profile rows. `local_p50_ms` and
`local_p95_ms` measure only local harness execution. Provider latency and
provider cost are always `null`, because no provider was contacted.
Every row must contain exactly 18 fake-transport calls: 15 referrals, one
chat model call, one translation, and one DNI OCR call. The harness records
only each request's category and routing metadata, never its payload. It
requires every call to use the selected profile's exact expected placeholder
destination and placeholder key.

Run it against a candidate whose synthetic corpus and `golden.json` have
already passed the publication preflight:

```sh
python3 scripts/bench_ai_profiles.py --format table
python3 scripts/bench_ai_profiles.py --format json
```

The referral score checks only that the deterministic transport response
travels through the real caller and produces the fixture's expected verdict.
`expected_field_presence` checks that fields named by each golden case remain
present after production reconciliation. Neither score claims extracted-value
accuracy. The present golden corpus also has no `ilegible` / manual-review
fixture, so that condition is a known coverage gap rather than a passing
benchmark claim.

The harness restores process globals, configuration environment, fake
transport, and socket functions even when a workload fails. It forces
`TURNOS_CONFIG_LOCK=1` and `TURNOS_ENV_FILE=/dev/null` while each profile runs,
and patches chat storage/catalog seams so the benchmark performs zero DB or
catalog reads. Response fixtures are fully shape-validated before workloads
start. Any attempted socket use or malformed fixture fails closed.
