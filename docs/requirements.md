# Standing requirements — human-rounds

`docs/requirements.json` is the registry of standing requirements mined from the product owner's
corrections to AI agents in this project. Each entry generalizes one or more real
corrections; `times` is how many corrections it absorbs, `examples` are verbatim
quotes, and `first_seen` is the earliest date it was corrected.

## When to consult this file

- **Before creating or changing any UI control** (table, dropdown, form, filter, map,
  nav item, card, chart, modal, copy, report): read the entries whose `kind` matches
  the control you are about to write, plus every entry with `times` >= 3 — these are
  the rules the product owner has corrected agents on repeatedly.
- Before planning or executing any slice: the `process` entries are binding workflow
  rules (verification, scope, autonomy, reporting), not suggestions.
- If your task seems to conflict with an entry, follow the entry or explicitly update
  it in the same change; silently violating a registry entry is a repeat offense.

## Entry kinds

`kind` is the control an agent will match when writing UI — a "map stays full width"
requirement is kind `map`. `process` covers workflow/verification rules; `any` covers
screen-level or cross-cutting UI rules; `copy` covers user-facing and developer-facing
text.

## How to add an entry

1. Generalize the correction into one imperative English sentence (<= 160 chars) that
   will still be true on the next screen, not just the one that triggered it.
2. Add a JSON object to `docs/requirements.json`:
   `{"id": "<kebab-case>", "kind": "<kind>", "text": "...", "examples": ["<verbatim
   quote>", "..."], "first_seen": "YYYY-MM-DD", "times": N, "status": "active"}`.
3. Merge with an existing entry (bump its `times`, keep the earliest `first_seen`,
   refresh `examples` only if the new quote is clearer) instead of adding a
   near-duplicate.
4. Keep the array ordered by `times` descending, drop one-off task instructions, and
   never delete an entry — set `"status": "retired"` with a replacement note instead.
