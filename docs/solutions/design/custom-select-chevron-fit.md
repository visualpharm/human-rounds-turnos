# Custom select chevrons need measured option width

## Problem

The selected text could technically fit inside a custom `<select>`, but its
last characters rendered underneath the chevron. Other filters only fit their
default label and clipped a longer, realistic option after selection.

## Contract

- A custom arrow owns its own horizontal space: icon width, distance to the
  outer edge, and at least 8 px between the icon and text.
- Admin selects measure every option with the rendered font. The result
  includes left/right padding and both borders.
- A control group changes together to a grid or stack before any control must
  become narrower than its content.
- Public filters measure the selected option at every width and remain one
  unbroken row while all selected labels fit. Their measured widths are the
  flex bases, then every control grows to consume its share of the remaining
  row width. When the labels do not fit, the whole group switches to one
  full-width control per row; it never falls into an accidental 2+1 layout.
- Search fields reserve room for the complete placeholder, including native
  search-field chrome.

## Implementation

`admin-control-fit.mjs` writes `--control-fit-width` on every `.adm-sel`,
including dynamically rendered controls, measured from the SELECTED option
(the product owner, 2026-09-15: a filter reads as its current value with the chevron
right after it, so its width follows that value and changes on `change`). Admin toolbars and decision rows
consume that width, while their responsive rules decide when the whole group
reflows. `listing.js` uses the same spacing contract for its custom public
chevrons, measures the selected labels rather than the longest hidden option,
uses the available parent width as the content-driven reflow threshold, and
lets every control grow from its measured width so no free row space is left.

## Verification

Check at 1280 px, an intermediate notebook width, and 390 px. Select the
longest option, not only the default. For each custom select, compare rendered
width with the measured width and confirm the document has zero horizontal
overflow.
