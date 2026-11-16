# Recovering chat bookings and keeping navigation deterministic

## Problem

A booking tool call can commit the request and its slot hold, then lose the HTTP
response during a process restart. Retrying the same conversational action sees
the held slot as unavailable and tells the patient the booking failed even
though it exists.

Free-form assistant prose also creates avoidable risks: spending model tokens
on obvious sidetracks, describing a useful page without giving the patient a
reliable path to it, and retreating into a generic medical disclaimer instead
of leading the patient through the network's administrative route.

## Pattern

1. Recover an exact active request by the natural key
   `(authorized patient, service, local date, local time)` before checking
   current availability. Never use an unverified dependent DNI as a lookup key.
2. Retry the same browser request after a transient connection loss. The
   server-side natural-key recovery makes that retry idempotent.
3. Resolve links from catalog and tool results in application code. Return
   `{label, href}` objects and accept only same-site relative paths in the
   widget; never trust a model-generated URL.
4. Refuse obvious off-topic and model-identity questions before the model call.
   Keep a prompt-level scope boundary as defense in depth.
5. Bound cost independently: per-IP hourly rate limit, per-conversation daily
   user-turn limit, smaller history/message budgets, and a short output cap.
6. Treat care routing as navigation, not diagnosis. Include the installation's
   closed specialty list in each model turn, verify the likely specialty with
   `search_services`, and, when it requires a referral, return the concrete
   General Medicine entry path with exactly two real appointment choices.
   Do not rely on the prompt alone: explicit "which doctor?" turns force the
   `search_services` function with its query constrained to an enum of installed
   specialties. Application code renders the referral route from that tool
   result, so the model cannot invent or multiply appointment times.
7. Keep the public demo's chat real with a dedicated capped provider key while
   leaving referral/OCR/document AI deterministic. The demo proves the
   conversational product without sending uploaded clinical documents to an
   external model.

## Verification

- The same exact slot request returns the original request ID and creates only
  one row.
- A non-linked DNI never reaches the recovery query.
- Wrong documents expose the same upload control again and never show login to
  an authenticated patient.
- Internal service/centre links are derived from the catalog and render as
  normal named links. If prose names a site section such as `Médicos y
  profesionales`, application code adds its canonical same-site link even
  when the model did not call the corresponding search tool.
- A referral-gated specialty result includes a General Medicine entry route
  with two actual bookable times rather than another permission question.
- The deployed demo chat response is not the deterministic `Recibido (fake)`
  fixture; document and referral analysis remain fake in demo mode.
- The full Python suite and browser-side chat tests pass, and the rendered
  centre link reaches the expected destination page.
