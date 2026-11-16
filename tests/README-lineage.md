# Data-lineage contracts
Run: `node --test test/lineage-*.test.mjs`.
Files stay flat so the existing `test/*.test.mjs` gate discovers them.
All eight recommended scenarios in the lineage input are covered:
- `lineage-service-identity-fallback`: cleared server phone cannot fall back to storage; live phone wins.
- `lineage-account-mirror-stale`: both identity writers must preserve server clears exactly.
- `lineage-nav-cache-expiry`: a settled 401 removes cached account chrome and request state.
- `lineage-admin-queue-attendance-server-time`: attendance displays server time under client clock skew.
- `lineage-admin-solicitudes-assign-merge`: assign and approve reconcile the entire authoritative row.
- `lineage-admin-slots-cache-invalidation`: reopening assignment excludes a slot consumed by approval.
- `lineage-account-requests-cancel-id-keyed`: both repainted and retained cards cancel their own request after prepend.
- `lineage-admin-queue-overlay-freshness`: SSE refresh replaces identity and AI fields in the overlay.
Queue/nav use native mounts and the existing DOM fixtures (nav fixture copied locally).
Private legacy functions execute verbatim in VM contexts with transport/DOM boundary doubles.
No assertion checks source spelling as proof of the behavior; extraction fails loudly if it drifts.
These are client contract tests, not real-browser or backend integration coverage.
The other inventory occurrences have no recommended scenario and are not claimed as covered.
When adding an identity/state field, name its server owner, seed an older UI value,
change or clear the server value, trigger the real refresh/mutation, and assert the
rendered value or outgoing payload matches server truth. Include stable-ID reorder
coverage for actions, and server-time coverage for timestamps. Never bless stale copies.
