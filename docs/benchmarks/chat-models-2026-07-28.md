# Sana Turnos chat model benchmark — 2026-07-28

## Recommendation

Use `google/gemini-3.5-flash-lite` for a limited production canary, with the
current `google/gemini-3-flash-preview` retained as the fallback. It was the
only model to pass all seven task-specific scenarios and was both faster and
cheaper in this run.

## Results

| Model | Passed | Median latency | Estimated cost for 7 scenarios |
|---|---:|---:|---:|
| Gemini 3.5 Flash Lite | 7/7 | 0.890 s | $0.004556 |
| Gemini 3 Flash (current) | 6/7 | 2.366 s | $0.006567 |
| DeepSeek V4 Flash | 6/7 | 4.443 s | $0.002098 |
| Tencent HY3 | 5/7 | 2.808 s | $0.002386 |

The scenarios were sanitized versions of the observed conversation: finding
Cardiology, choosing the hospital, booking an exact slot, replacing a wrong
document, urgent chest pain, a model-identity sidetrack, and a Berlin sidetrack.
The current model failed the final booking action after an identity lookup.
Gemini 3.5 Flash Lite completed that two-tool sequence correctly.

## External context

- [Arena text leaderboard](https://arena.ai/leaderboard/text): Gemini 3 Flash
  ranks above Gemini 3.5 Flash Lite overall, so this task result should not be
  interpreted as a general intelligence ranking.
- [OpenRouter model catalog](https://openrouter.ai/models): live prices used
  for the cost calculation were $0.50/$3.00 per million input/output tokens for
  Gemini 3 Flash and $0.30/$2.50 for Gemini 3.5 Flash Lite.

## Method and limitation

The reusable harness is `scripts/benchmark_chat_models.py`. It calls each model
once per scenario with the production tool schemas and deterministic checks.
This is a product regression benchmark, not a clinical-safety evaluation or a
statistically stable latency study. A production canary should therefore keep
the current model available as a fallback and monitor booking-tool completion.
