'use strict';
/* Pure ranking shared by the Node store and the isomorphic panel renderer.
 * No filesystem, no DOM — safe to load in Node and in the browser (UMD:
 * module.exports on Node, window.UsabilityKitRank in the browser; load this
 * file before panel/render.js when not using a bundler). */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.UsabilityKitRank = factory(); /* eslint-disable-line no-param-reassign */
})(typeof self !== 'undefined' ? self : this, function () {
  /* critical worst; the spec's deterministic order is severity, then check, page. */
  const SEV_ORDER = { critical: 0, serious: 1, moderate: 2, minor: 3 };

  const sevIdx = (f) => SEV_ORDER[f.severity] ?? SEV_ORDER.minor;

  /* Owner importance first: the explicit rating, else severity-derived
   * (critical=3 · serious=2 · moderate=1 · minor=0). Rating 0 = Not now. */
  function effectiveRating(f) {
    if (f.rating != null) return f.rating;
    return 3 - sevIdx(f);
  }

  /* Top n open/failing findings: rating, then severity, then participants hit.
   * "Not now" (rating 0) findings never rank. Key breaks ties deterministically. */
  function worst(store, n) {
    const count = n == null ? 5 : n;
    const list = (store.findings || []).filter(
      (f) => (f.status === 'open' || f.status === 'failing') && f.rating !== 0,
    );
    list.sort(
      (a, b) =>
        effectiveRating(b) - effectiveRating(a) ||
        sevIdx(a) - sevIdx(b) ||
        (b.participants_hit || 0) - (a.participants_hit || 0) ||
        String(a.key).localeCompare(String(b.key)),
    );
    return list.slice(0, count);
  }

  /* The findings table order: severity, then check, then page, then key. */
  function bySeverity(findings) {
    return [...(findings || [])].sort(
      (a, b) =>
        sevIdx(a) - sevIdx(b) ||
        String(a.check || '').localeCompare(String(b.check || '')) ||
        String((a.pages || [])[0] || '').localeCompare(String((b.pages || [])[0] || '')) ||
        String(a.key).localeCompare(String(b.key)),
    );
  }

  /* Open filter shows what still needs attention: open + failing. */
  function statusFilter(findings, status) {
    const all = findings || [];
    if (!status || status === 'all') return all;
    if (status === 'open') return all.filter((f) => f.status === 'open' || f.status === 'failing');
    return all.filter((f) => f.status === status);
  }

  function counts(findings) {
    const all = findings || [];
    const c = { total: all.length, open: 0, fixed: 0, confirmed: 0, failing: 0 };
    for (const f of all) if (c[f.status] != null) c[f.status] += 1;
    return c;
  }

  return { SEV_ORDER, effectiveRating, worst, bySeverity, statusFilter, counts };
});
