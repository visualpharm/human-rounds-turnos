# triage — provisional AI risk

Purpose: Resumen y prioridad sugerida de la entrevista.

Self-assessed level: **alto**. Rationale: Contenido clínico o identidad: alto hasta evaluación del hospital.

This is a product self-assessment, not a hospital-approved classification under
the unavailable Resolution 9/25 criteria. Owner: municipal authority and the
hospital's designated reviewer. Assessment date: 2026-10-14.

## Data and controls

- transcript seudonimizado
- especialidad
- catálogo clínico

Texto seudonimizado/restaurado con core/pii.py; esfuerzo razonable.

Médico revisa en Consulta antes de confirmar; piso determinista de señales urgentes; sin cambios de turno.

Prior notice: `ai.notice.clinical` on doctor consultation.

Kill switches: `TURNOS_AI_DISABLED=1`, `TURNOS_FAKE_AI=1`, `module intake off`.

Provider/model: resolve from the live product dossier via the shared configuration
and `app/core/ai_policy.py`; this file deliberately does not freeze a model/provider.
First deployment and exact last substantial deployment change: unverified.

## Assessment to complete

- **Rights:** No adverse appointment decision or unreviewed clinical write may follow model output. Confirm access paths and the human reviewer with the hospital.
- **Service quality:** Measure errors, fallback behavior and time to human review on institution-approved synthetic cases before accepting clinical use.
- **Data provenance and representativeness:** Validate source/document ownership, authenticity, representativeness and provider training-data assurances; none is proven by this file.
- **Bias and access:** Evaluate age, disability, language, handwriting quality and access to a non-AI channel. No fairness metric has been measured.
- **Labour:** Municipality and hospital determine staffing/training and accountability; the product does not replace that decision.
- **Human oversight:** Médico revisa en Consulta antes de confirmar; piso determinista de señales urgentes; sin cambios de turno.
- **Environment:** Obtain provider/model energy and hosting evidence. No environmental impact number is asserted.

Approval/signature: **not supplied**. Residual risk and mitigation acceptance:
**pending hospital/municipal assessment**. Cross-reference: [normative map](../ia-pba-2026.md).
