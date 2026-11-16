// Pure completeness predicate shared by the served runner and adversarial
// report controls. Visual review is deliberately a separate gate.
export function requiredPhases(action) {
  if (action === 'nomatch') return ['no-match', action];
  if (action === 'filter') return ['filtered', action];
  if (action === 'tabs') return ['tab-agenda', 'tab-calendario', 'tab-info', action];
  return action === 'hold' ? ['held', action]
    : ['reject', 'calendar-reject'].includes(action) ? ['rejected', action] : [action];
}

export function catalogEvidenceComplete(rows, stories, widths, sourceHash) {
  if (!Array.isArray(rows) || rows.length !== stories.length || !stories.length || !widths.length
      || new Set(stories.map(([id]) => id)).size !== stories.length
      || new Set(rows.map(row => row.id)).size !== rows.length) return false;
  const seenAssets = new Set();
  for (const row of rows) for (const view of Object.values(row.views || {}).flatMap(Object.values)) {
    for (const asset of [...(view.assets || []), view.viewportAsset]) {
      if (!asset?.path || seenAssets.has(asset.path)) return false;
      seenAssets.add(asset.path);
    }
  }
  return stories.every(([id, kind, action]) => {
    const row = rows.find(item => item.id === id);
    return row?.kind === kind && row.action === action && row.assertionComplete === true
      && row.observerInitialized === true && !row.failure && Array.isArray(row.errors) && row.errors.length === 0
      && widths.every(width => requiredPhases(action).every(phase => {
        const view = row.views?.[width]?.[phase], g = view?.geometry, identity = view?.identity;
        return Array.isArray(view?.assets) && view.assets.length === 3
          && view.viewportAsset?.path?.endsWith('-viewport.png') && view.viewportAsset.bytes > 0
          && !view.assets.some(asset => asset.path === view.viewportAsset.path)
          && new Set(view.assets.map(asset => asset.path)).size === 3
          && ['.png', '.html', '.json'].every(ext => view.assets.some(asset => asset.path.endsWith(ext) && asset.bytes > 0))
          && g?.observerSupported === true && g.observerReady === true && Array.isArray(g.shifts)
          && Number.isFinite(g.cls) && g.cls >= 0 && g.cls <= 0.1 && g.overflow === 0
          && Array.isArray(g.escaped) && g.escaped.length === 0
          && identity?.status === 200 && identity.sourceHash === sourceHash
          && Array.isArray(identity.urls) && identity.urls.length === 1;
      }));
  });
}
