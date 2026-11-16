# Editable RAG and iterative chat evaluation

## Problem

The assistant mixed three failure modes that a prompt alone could not solve:
live Pinamar catalogue questions, stable administrative knowledge, and model
habits such as appending a local detour to a national answer. Administrators
also needed to update knowledge without a deploy.

## Pattern

- Keep appointments, professionals, locations and hours in deterministic tools.
- Put sourced municipal, provincial and national guidance in a separate
  hierarchical catalogue with jurisdiction, publication state, validity,
  tags, source URL, verification date and review cadence.
- Retrieve only published, currently valid articles; prefer an explicitly
  requested jurisdiction and drop weak lateral matches.
- Return citations separately from answer text so the UI can collapse them.
- Treat high-risk interaction rules as code invariants when models continue to
  ignore prompt wording. Here, an explicit Province/Nation query without
  “Pinamar” deterministically drops a trailing Pinamar recommendation.

## Evaluation loop

Run fixed batches of ten questions by intent family. Review answers manually,
repair the highest-impact repeated defect, retest only affected cases, then
advance to a different family. Keep automatic checks narrow: word bans can
misclassify a correct refusal, and requiring login too early can reward the
wrong flow.

After five batches, broad random prompts had diminishing returns. The next
valuable tests are stateful scenarios: anonymous order upload, time choice,
login handoff, vanished slots and conversation restoration.

## Browser gotcha

An admin tree may update the hash while leaving the editor stale when an older
script is cached. Give article links a direct click handler plus hash-change
fallback, and bump every served script query. Verify with a query on the admin
document itself when testing a newly bumped bundle in a long-lived browser.
