# Runtime controls

`TURNOS_AI_DISABLED=1` disables internal AI inference, including voice upload
and transcription. Fake mode remains usable for demo dictation without
provider egress. A connected external MCP assistant is controlled by its
client; disabling the chat module disables its product tools.

Settings use the process-local configuration cache, with a TTL of **3 seconds**
(`app/core/config.py:_TTL`). A settings write invalidates the writing process's
cache immediately. Other web/worker processes observe a database change at
their next read after that TTL; an already dispatched provider request is not
cancelled. Environment changes require updating/restarting each process.

The register is refreshed explicitly once during ASGI startup. Settings and
voice-model writes append evidence and update snapshots in the same database
transaction. Register GETs and Sistema polling read the ledger without source
parsing or writes. If the startup baseline is missing, the register reports
unavailable rather than claiming zero pending. Functionality versions are
maintained explicitly per entry in `app/core/ai_register.py`; bump the owning
version with a real functional change.

Image routing is unchanged. Referral, DNI and laboratory uploads can fall
back to a `:free` model. The `retention_risk` field is shown in the register
and its export/print view: free-tier retention, training use and downstream
provider policies are unverified. Complete identifying images may reach that
fallback. The hospital must verify the provider's applicable terms.

Public/institutional translations preserve public phone numbers and email
addresses. They do not use the patient scrubber. Cache reads remove only
machine rows containing leaked identity placeholders, preserving human
overrides. Patient triage and profile extraction still scrub outgoing text
and restore provider output locally.

Health-profile proposals are interview-scoped `health_profile_proposal`
metadata in `chat_messages`, separate from `patient_health_profile`. They
never enter interview prompts or chat compaction. The doctor sees the proposal,
can apply it to the editor, and explicitly saves reviewed values when confirming
the consultation. Only the confirmed profile informs subsequent interviews.
Unsaved editor changes block consultation confirmation unless saving the
reviewed profile is selected; browser navigation warns before discarding them.

Web/WhatsApp booking proposals bind service, patient and exact slot to the
account and conversation. A subsequent explicit `Confirmar` reply, handled
before another model turn, consumes the proposal once (10-minute expiry).
Only that transport confirmation permits the service's deterministic automatic
confirmation rules. The assistant has no approval/rejection/cancellation tool.
MCP arguments cannot prove a patient reply: the external client receives an
absolute link with the exact selected slot for the patient's web confirmation.
No model-supplied `confirmed` boolean grants authorization.

WhatsApp disclosure is dedicated `ai_disclosure` conversation metadata.
Unbounded indexed lookup also recognizes earlier system markers. Both are
excluded from model context and compaction; only successful delivery records
the flag.
