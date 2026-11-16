# case_chat — provisional AI risk

Purpose: Preguntas sobre un caso sin turnos y síntesis para la mesa.

Self-assessed level: **alto**. Rationale: Contenido clínico o identidad: alto hasta evaluación del hospital.

This is a product self-assessment, not a hospital-approved classification under
the unavailable Resolution 9/25 criteria. Owner: municipal authority and the
hospital's designated reviewer. Assessment date: 2026-11-07.

## Data and controls

- conversación seudonimizada
- motivo de consulta
- datos de salud

Texto seudonimizado/restaurado con core/pii.py; esfuerzo razonable.

When a service has no free slots, the patient tells the hospital why they need
one sooner in a short conversation instead of an empty text box: an opening
question with suggested answers and at most `INTAKE_CASE_MAX_QUESTIONS` (1–3)
clarifying ones, urgency included. The cap is code, not prompt; the patient may
stop earlier. A second call writes a summary for the desk (what they want, how
urgent, key facts). Emergency words get the deterministic guardia / 107 floor
before and regardless of the model. Without an AI key, with the breaker open or
on any model failure the chat closes and the request is still sent with what the
patient wrote; the summary then falls back to their own words.

La mesa lee la síntesis y, si hace falta, la conversación entera (queda en el registro de accesos) y decide si da un sobreturno. Un piso determinista indica guardia o 107 ante señales de urgencia; la IA no da ni niega turnos.

Prior notice: `ai.notice.case` on booking no-slots step, first assistant message (the opening says it is an AI assistant that gives no medical advice).

Kill switches: `TURNOS_AI_DISABLED=1`, `TURNOS_FAKE_AI=1` (normal flows are simulated; this is not a universal kill switch), `module intake off` (the step falls back to the plain text box).

Provider/model: resolve from the live product dossier via the shared configuration
and `app/core/ai_policy.py`; this file deliberately does not freeze a model/provider.
First deployment and exact last substantial deployment change: unverified.

## Assessment to complete

- **Rights:** No adverse appointment decision follows model output: the urgency label is advice to the desk, never a refusal. Confirm with the hospital who reads the requests (municipal decision, 27.10: few people).
- **Service quality:** Measure how often the summary's urgency matches the desk's own reading on institution-approved synthetic cases before relying on it for ordering.
- **Data provenance and representativeness:** Validate provider training-data assurances; none is proven by this file.
- **Bias and access:** Evaluate age, disability, language and literacy; the plain text box remains when AI is off. No fairness metric has been measured.
- **Labour:** Municipality and hospital determine staffing/training and accountability; the product does not replace that decision.
- **Human oversight:** La mesa lee la síntesis y, si hace falta, la conversación entera (queda en el registro de accesos) y decide si da un sobreturno. Un piso determinista indica guardia o 107 ante señales de urgencia; la IA no da ni niega turnos.
- **Environment:** Obtain provider/model energy and hosting evidence. No environmental impact number is asserted.

Approval/signature: **not supplied**. Residual risk and mitigation acceptance:
**pending hospital/municipal assessment**. Cross-reference: [normative map](../ia-pba-2026.md).
