# Booking calendars and walk-in service pages

## Problem

Doctor and centre entry points lost their context inside the booking wizard.
“More times” opened an unfiltered booking page, date headings were not
interactive, and the public service page repeated the same mode and schedule
as metadata, a callout, and a live status.

## Pattern

- Carry a catalog doctor ID in booking URLs and merge that doctor's local
  agenda ahead of the generic service response. Keep other professionals below
  it instead of filtering them out.
- Match external schedule names by normalized, order-independent name tokens,
  then link the resolved catalog professional.
- Render public and admin calendars through the same month-grid component.
  The caller supplies semantic day classes; public booking disables dates
  without slots.
- Let the public booking card use the wide flow column. On tablet and desktop,
  render the 2 visible months side by side; stack them below 700 px. Keep the
  single calendar trigger after every visible schedule, because people should
  review the concrete dates and times before asking for alternatives. The
  calendar filters the whole result set rather than one doctor's agenda. The
  initial view shows every upcoming day returned by the real agenda. Expand
  the calendar below that trigger; choosing an available date immediately
  collapses it and filters the schedules to that day. Do not add a second
  “Prefiero otro horario” control below the same content.
- Use the added width for the appointment controls too: keep slot buttons at a
  consistent compact width so complete clinic sessions fit in deliberate rows
  instead of leaving avoidable orphan controls.
- In public and admin schedule summaries, lift facts shared by every row into
  a group heading. Show the centre name once without a redundant “Centre”
  label, then list weekday and time. Omit the office entirely when its visible
  name is the same across the whole schedule; show it only when the schedule
  genuinely spans multiple offices.
- A walk-in page has one sequence: title, editable one-line description,
  requirements, linked centre/contact details, then one weekly hours grid.
  Do not also render the legacy `cartel` on that surface.
- A point-in-time notice is not a closing range. If the source publishes only
  the start time, show the time and an explicit source limitation instead of
  inventing a closing hour.

## Verification

Check the rendered flow at 360, 390, 768, 834, 1024, 1280, and 1440 px.
Confirm there is no horizontal overflow, the 2 calendar months share one row
where they fit and stack on phones, the chosen doctor is first, unavailable
calendar days are disabled, the initial schedule contains multiple upcoming
days, selecting a calendar date closes the calendar, address and phone links
have the correct schemes, and repeated mode or schedule text is absent from
the visible page.
