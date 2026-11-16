# mcp_assistant — provisional AI risk

Purpose: Herramientas para un asistente externo conectado por MCP.

Self-assessed level: **alto**. Rationale: Contenido clínico o identidad: alto hasta evaluación del hospital.

This is a product self-assessment, not a hospital-approved classification under
the unavailable Resolution 9/25 criteria. Owner: municipal authority and the
hospital's designated reviewer. Assessment date: 2026-10-14.

## Data and controls

- datos de herramientas autorizadas
- órdenes adjuntas
- laboratorio

Token-authorized tool responses to the connected external client may include identity (whoami); they are not passed through the product inference-text filter. Product-side AI readers keep their existing PII protections. This is an existing user-authorized client data-access boundary, not a new provider inference call.

El paciente autoriza la conexión. Las herramientas ofrecen enlaces absolutos para confirmar el horario exacto o la cancelación en la web. Un argumento enviado por la IA externa no prueba consentimiento del paciente.

Prior notice: `ai.notice.mcp` on account assistant connection.

Kill switches: `module chat off`; `TURNOS_AI_DISABLED=1` stops product-side AI readers only.

Provider/model: resolve from the live product dossier via the shared configuration
and `app/core/ai_policy.py`; this file deliberately does not freeze a model/provider.
First deployment and exact last substantial deployment change: unverified.

## Assessment to complete

- **Rights:** No adverse appointment decision or unreviewed clinical write may follow model output. Confirm access paths and the human reviewer with the hospital.
- **Service quality:** Measure errors, fallback behavior and time to human review on institution-approved synthetic cases before accepting clinical use.
- **Data provenance and representativeness:** Validate source/document ownership, authenticity, representativeness and provider training-data assurances; none is proven by this file.
- **Bias and access:** Evaluate age, disability, language, handwriting quality and access to a non-AI channel. No fairness metric has been measured.
- **Labour:** Municipality and hospital determine staffing/training and accountability; the product does not replace that decision.
- **Human oversight:** El paciente autoriza la conexión. Las herramientas ofrecen enlaces absolutos para confirmar el horario exacto o la cancelación en la web. Un argumento enviado por la IA externa no prueba consentimiento del paciente.
- **Environment:** Obtain provider/model energy and hosting evidence. No environmental impact number is asserted.

Approval/signature: **not supplied**. Residual risk and mitigation acceptance:
**pending hospital/municipal assessment**. Cross-reference: [normative map](../ia-pba-2026.md).

The external assistant provider/model are unknown to this product. JSON exports
separately report the runtime configuration of product-side readers. The
municipality must assess the authorized-client disclosure and downstream risks.
