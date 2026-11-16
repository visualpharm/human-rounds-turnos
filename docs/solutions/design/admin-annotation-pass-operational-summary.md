# Admin annotation pass: decision cards and operational summary

## Outcome

The admin queue follows the design handoff for requests without referrals and extends the same hierarchy to requests with referrals: patient and extracted fields stay in the left form column, the source document owns the right column, and approve/reject controls remain close to the decision data instead of stretching beneath the document.

At medium widths the sidebar narrows, content gutters match the gaps between cards, and the navigation folds into a burger at 900 px. The bulk-action group switches as one intentional grid, so no control is stranded or causes horizontal page overflow.

## Decision defaults

- Every pending card contains complete approve and reject paths.
- The suggested path receives the stronger color; the alternative remains visible in gray.
- Doctor-bearing services preselect the next available professional, preferring the patient's requested time-of-day. Laboratory requests omit the doctor control.
- Controls use one shared content-sized width and switch together at container breakpoints.
- Insurance member numbers use provider-specific grouping for OSDE and Swiss Medical.

## Operational report summary

The summary no longer leads with catalog or throughput counts. Its cards answer current operating questions:

- pending-request median age and oldest age;
- 14-day booked-slot load factor, with occupied and available distribution flush to the card edge;
- distinct patients in 30 days with a 12-month activity series;
- highest-rated center as a positive benchmark;
- 30-day AI burn projected only from complete calendar days, excluding the first partial and current day;
- login-panel share as a percentage and donut.

## Divider and HSI safeguards

The project-level divider audit now requires every separator to be justified between unbounded text regions; bounded controls and chart cards must rely on their own chrome.

HSI patient links are rendered only when an exact `hsi_patient_id` exists. The installed HSI version does not expose a verified deep route for creating an appointment, so the create control opens the HSI application rather than inventing a brittle route.
