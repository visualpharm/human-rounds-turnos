# Browser extensions

One folder per extension: `extensions/<name>/` with its `manifest.json`.

- `tests/` (development) and `store/` (Chrome Web Store listing texts, privacy
  policy, `item.json` with the store item id) never ship inside the package.
- Build: `python3 scripts/build_extension.py [name]` → `extensions/dist/<name>.zip`
  (unzip and load in `chrome://extensions`) and `<name>-store.zip` (manifest at
  the root, for the store).
- Publish: `python3 scripts/publish_extension.py <name> [--publish]` with
  `CWS_CLIENT_ID`, `CWS_CLIENT_SECRET`, `CWS_REFRESH_TOKEN` in the environment.
- Test: a `tests/<name>-extension.test.mjs` at the repo root pins the rules and
  that the built zip matches the sources.

A new extension copies this shape; nothing else in the repo needs to know it.
