# health_probe — provisional AI risk

Purpose: Comprobación técnica de disponibilidad de IA.

Self-assessed level: **limitado**. Rationale: Contenido institucional/técnico; sin decisiones individuales.

This is a product self-assessment, not a hospital-approved classification under
the unavailable Resolution 9/25 criteria. Owner: municipal authority and the
hospital's designated reviewer. Assessment date: 2026-10-14.

## Data and controls

- mensaje técnico sin datos personales

Texto seudonimizado/restaurado con core/pii.py; esfuerzo razonable.

Admin ejecuta Probar; sonda no procesa pacientes ni cambia decisiones.

Prior notice: `ai.notice.probe` on settings inference integration.

Kill switches: `TURNOS_AI_DISABLED=1`, `TURNOS_FAKE_AI=1` (normal flows are simulated; this is not a universal kill switch).

Provider/model: resolve from the live product dossier via the shared configuration
and `app/core/ai_policy.py`; this file deliberately does not freeze a model/provider.
First deployment and exact last substantial deployment change: unverified.

## Assessment to complete

- **Rights:** No adverse appointment decision or unreviewed clinical write may follow model output. Confirm access paths and the human reviewer with the hospital.
- **Service quality:** Measure errors, fallback behavior and time to human review on institution-approved synthetic cases before accepting clinical use.
- **Data provenance and representativeness:** Validate source/document ownership, authenticity, representativeness and provider training-data assurances; none is proven by this file.
- **Bias and access:** Evaluate age, disability, language, handwriting quality and access to a non-AI channel. No fairness metric has been measured.
- **Labour:** Municipality and hospital determine staffing/training and accountability; the product does not replace that decision.
- **Human oversight:** Admin ejecuta Probar; sonda no procesa pacientes ni cambia decisiones.
- **Environment:** Obtain provider/model energy and hosting evidence. No environmental impact number is asserted.

Approval/signature: **not supplied**. Residual risk and mitigation acceptance:
**pending hospital/municipal assessment**. Cross-reference: [normative map](../ia-pba-2026.md).
