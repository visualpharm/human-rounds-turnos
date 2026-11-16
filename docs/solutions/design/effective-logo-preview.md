# Settings previews must show the effective logo

The settings preview originally loaded `/logo.png` directly. That is only the
legacy Sana heart. The Pinamar theme replaces the logo in the site navigation
through CSS with `/pinamar-emblema.svg`, so the settings screen could show a
different brand from the one people actually saw across the site.

The preview now resolves the same effective state:

- an uploaded custom logo uses the cache-busted `/logo.png` route;
- the Pinamar theme uses `/pinamar-emblema.svg`;
- the Sana theme uses the built-in `/logo.png`.

Keep this distinction when adding another brand surface: `/logo.png` is the
custom-or-legacy asset route, not always the logo currently visible under the
active theme. The direct admin sidebar keeps that same asset mapping: its
white-tile defect was the CSS `var(--panel)` background, not the PNG asset;
the repair makes that background transparent while preserving the existing
Pinamar emblem and uploaded-logo `!important` override.

Verification lives in `test/admin-theme-ui.test.mjs`,
`test/admin-css-drift.test.mjs`, and `test_branding.py`; rendered evidence is
in the `Pinamar logo preview QA` section of `design-qa.md`.
