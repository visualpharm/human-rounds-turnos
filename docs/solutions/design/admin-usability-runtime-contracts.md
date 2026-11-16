# Admin usability must follow runtime behavior

Date: 2026-09-04

## Problem

The doctor editor mixed 20 px text inputs with smaller selects and actions,
clipped a normal email address, and stacked photo controls unnecessarily.
The reference-deployment counterpart's meeting also showed that the appointment limit's name did not explain
its scope. Broader rendered review found a service editor that could not open.

## Causes and corrections

- Shared CSS lost to later injected controller styles. Verify computed font,
  height, and content width in every editor, including schedule controls.
  Keep mobile rules after the corresponding desktop declarations.
- Text-field selectors also reached nested language checkboxes. Exclude
  checkbox/radio controls and size their labels as usable click targets.
- Services called `SanaHoursEditor.capacityDescriptionHtml()` without loading
  `admin-hours-editor.js`. Load the helper before both service and doctor
  renderers; keep calendar/user payload restricted to doctors. The direct
  screen test now verifies this dependency and its fingerprint/order.
- The booking limit is checked by patient DNI and service, with doctor,
  service, then site precedence. The UI must describe pending requests and
  future same-service appointments made through online booking. It must not
  imply capacity per time slot or a doctor-only counter.
- A day change must not hide an active queue filter. Every active filter
  needs a visible selected value and a way to clear it.
- Both catalog and slot promises need modal identity guards. Closing and
  reopening a modal must not let the old callback replace its new context.

## Verification

The first usability pass missed catalog table presentation: direct catalog
routes omitted admin-table.js, whose injected CSS owned base cell padding
and the column chooser. Rows fit the viewport with browser-default 1 px
padding, so a zero-overflow result falsely appeared acceptable. Inspect
actual populated rows, computed padding/alignment, and chooser open/closed
states in every route that consumes the shared component. Keep base table
presentation in the linked shared stylesheet and separately declare the
behavior dependency wherever the shared table engine is used.

Do not apply a large simple-form scale indiscriminately to operational
tables, settings and navigation. Respect each surface's density and inspect
Pinamar as well as demo theme rendering. Wiki navigation also needs expanded,
searched and selected states; its initial empty pane alone proves nothing.

Exercise actual direct-screen forms and list interactions with the disposable
browser harness, not only source-pattern assertions. Check 360, 390, 768,
834, 1024, 1280, and 1440 px widths, keyboard/label focus, tab persistence,
photo cancellation, combined filters, changed-day no-match recovery, and
appointment context removal. Verify the served commit and deployed assets
on production and demo before closing the usability work.

## Explicit saves and dense-table follow-up

The September 4 clarity revision replaces Wiki blur-autosave with one explicit
transaction. Protect dirty drafts when leaving; title generation changes the
draft only. A delayed save must preserve edits made during the request, and a
new article must acquire its returned ID before another save can occur. A
response for an editor that was closed must not change the selected article.

Render long editable titles, not only short fixtures. Single-line title inputs
clipped even at desktop width. Give the body the available mobile height and
keep validation/status near the action that produced it.

Check selection-cell content width, default checkbox margins and inherited
ellipsis when diagnosing stray dots. Removing resize handles alone did not
remove the visible checkbox artifact. Likewise, action hierarchy must use the
actual primary-button class: an exclusion for a nonexistent primary class
shrunk Save to the same size as Disable while source assertions still passed.

The restored admin brand participates in the canonical custom-logo selector
alongside the public navigation. A theme-only fallback cannot represent an
uploaded institutional logo. Test both themes and the custom override.

## Form persistence follow-up — 2026-10-15

- A role CRUD API is not a usable roles screen. Mount the real controller and
  consume the server role catalog in Users; preserve an assigned disabled role
  during unrelated edits instead of silently selecting a different role.
- Verify the assigned account itself: login continuation, server-rendered
  sidebar, allowed and denied APIs, then role disablement in the same session.
  Both HTML and API dependencies bind effective permissions. Module navigation
  reads `screen.path`; admin-capable screens follow grants, doctor-only screens
  retain their role restriction. Keep validated editor query state across login.
- An acknowledgement identifies the stored record, not the latest draft.
  Validate its identity and item shape before changing the URL or form. After a
  create, attach its ID to both the form and the retained draft; the next write
  must update that record. Read the current DOM, including changes without events.
- Each editorial route transition invalidates earlier requests. Save, Restore
  and history mutations share one lock. Preserve pending drafts through lifecycle
  teardown; a temporary readonly flag must not hide a draft from capture.
- Checkbox drafts store and restore `checked`, not the checkbox's `value`.
- Regressions: `admin-editorial-save-browser.test.mjs`,
  `admin-draft-preservation-browser.test.mjs`, catalog lifecycle tests and
  `e2e/admin-roles-save.spec.mjs`. The latter exercises ASGI and throwaway Postgres;
  intercepted browser fixtures prove client transitions, not live delivery.
- Shared asset/catalog changes move Account snapshots. Exact reversal of only
  the reviewed URLs and fourteen catalog keys restores all nine baseline renders:
  `docs/solutions/security/forms3-render-proof.json`.
