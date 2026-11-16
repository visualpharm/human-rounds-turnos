# Public health campaigns and service announcements

## Problem

Public-health actions had accumulated as one-off promotional fragments. They did not share the directory's canonical row or action treatment, campaign pages stopped at a slogan, and there was no safe way to attach a time-sensitive operational notice to the affected services. That made search results inconsistent and encouraged hard-coding an unverified shortage into patient copy.

## Pattern

- Keep campaign content in `enlaces.json`, including structured sections, eligibility or safety notes, contact actions, and source-informed operational limits. The renderer stays generic and the data pack owns the local facts.
- Put directory actions through the shared `accion` listing preset. Informational results then inherit the same right-hand action column, mobile stacking, and accessible link behavior as every other result.
- Store temporary operational messages in `announcements.json`. An announcement must be explicitly active and target canonical `service_ids` or `cluster_ids`; rendering belongs immediately below the service title.
- Never activate a shortage notice from an incomplete recollection. Confirm the exact vaccine or service first, then publish the smallest patient-useful statement: what is unavailable, what remains available, and what the patient should do.
- Phrase capabilities that were discussed but not guaranteed as a contact request. For example, ask Zoonosis whether it can coordinate a trap cage instead of promising equipment availability.

## Gotchas

- Search links arrive asynchronously from `/api/links`. If a visitor has already typed a query, render again after that payload arrives or the campaign row remains invisible until the next keystroke.
- Editing a private pack requires regenerating its `INVENTORY.json`; otherwise the deployment correctly rejects the modified pack.
- Public-health safety copy is functional content. Blood eligibility needs the final professional-assessment caveat; animal-bite copy needs immediate washing and same-day care; rodent cleanup must prohibit dry sweeping.
- A local database failure can delay or break API-backed directory data while the static campaign page still renders. Browser verification must wait for the asynchronous payload and assert the final result, not only first paint.

## Verification

Run the campaign renderer tests, listing tests, pack-localization tests, and a real-browser probe at mobile and desktop widths. Check the searched campaign row and its action after data settles, the telemedicine hover transition, the WhatsApp fallback, and zero horizontal overflow on every campaign page.
