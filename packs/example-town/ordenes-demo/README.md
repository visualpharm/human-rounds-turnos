# Synthetic referral-image corpus

This directory contains the public demo and offline benchmark corpus for
`ai_referral.py`. Every JPEG is synthetic, created from scratch, and visually
reviewed. No file contains, reproduces, or derives from a real patient document.

The adjacent `assets.json` is the publication boundary. It pins the exact
closed set, byte counts, digests, JPEG media type, synthetic provenance, and an
opaque dated review attestation. `golden.json` records the intended workload
outcomes used by the demo seeder and future mocked profile benchmarks.

The corpus deliberately covers clean digital orders, difficult handwriting,
folded phone photographs, foreign jurisdictions, a non-medical document,
English and Portuguese referrals, and a valid prescriber with two professional
registration numbers. Model output is nondeterministic, so the expected
verdicts are review anchors rather than a promise that every hosted model will
produce identical text.

## Cases

| File | Scenario | Expected verdict |
|---|---|---|
| `bad-handwriting-cardiologia-1.jpg` | Difficult handwritten cardiology referral | `dudosa` |
| `bad-handwriting-laboratorio-2.jpg` | Handwritten laboratory order without a legible registration stamp | `dudosa` |
| `bad-handwriting-traumatologia-3.jpg` | Legible handwritten ankle radiography order | `parece_correcta` |
| `english-referral-1.jpg` | English cardiology referral from the fictional local network | `parece_correcta` |
| `foreign-jurisdiction-chile-interconsulta.jpg` | Chilean interconsultation form | `dudosa`, foreign jurisdiction |
| `photographed-folded-imagenes-2.jpg` | Folded phone photograph of an imaging order | `parece_correcta` |
| `photographed-folded-laboratorio-1.jpg` | Folded and glared phone photograph of a laboratory order | `parece_correcta` |
| `portuguese-sus-referral.jpg` | Portuguese Brazilian referral | `dudosa`, foreign jurisdiction |
| `synthetic-bad-handwriting-ecografia-1.jpg` | Handwritten obstetric ultrasound order with a correction | `dudosa` |
| `synthetic-clean-digital-ecografia-1.jpg` | Clean abdominal ultrasound order | `parece_correcta` |
| `synthetic-clean-digital-laboratorio-1.jpg` | Clean laboratory panel request | `parece_correcta` |
| `synthetic-clean-digital-resonancia-1.jpg` | Clean knee MRI order | `parece_correcta` |
| `synthetic-derivacion-neurologia-1.jpg` | Typed neurology referral letter | `parece_correcta` |
| `synthetic-negative-wrong-document-1.jpg` | Dog-grooming invoice | `documento_equivocado` |
| `synthetic-two-license-cardiologia-1.jpg` | Cardiology referral with both national and provincial registrations | `parece_correcta` |

## Offline fake-AI behavior

`TURNOS_FAKE_AI=1` is intentionally simpler than the golden corpus. It uses a
deterministic stub and does not attempt OCR. The golden data is therefore used
for seed consistency and mocked workload contracts; it does not imply that the
offline fake must reproduce every real-model branch.
