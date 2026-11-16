'use strict';
/* axe — axe-core violations via @axe-core/playwright. Impact maps
 * critical→critical, serious→serious, moderate→moderate, minor→minor; rule
 * color-contrast is disabled (the contrast check owns it, with rendered-color
 * math axe cannot do). Runs once per page at the first configured viewport:
 * the accessibility tree barely depends on width. */

const IMPACT = { critical: 'critical', serious: 'serious', moderate: 'moderate', minor: 'minor' };
let builder = null;

function loadBuilder() {
  if (builder) return builder;
  try {
    const { AxeBuilder } = require('@axe-core/playwright');
    builder = AxeBuilder;
  } catch (e) {
    builder = false;
  }
  return builder;
}

module.exports = {
  id: 'axe',
  async scan(v) {
    if (v.viewport !== v.viewports[0]) return;
    const AxeBuilder = loadBuilder();
    if (!AxeBuilder) {
      v.skip('axe', '@axe-core/playwright not installed');
      return;
    }
    const result = await new AxeBuilder({ page: v.page }).disableRules(['color-contrast']).analyze();
    for (const violation of result.violations || []) {
      const nodes = violation.nodes || [];
      v.add({
        check: 'axe',
        severity: IMPACT[violation.impact] || 'minor',
        title: violation.help || `Accessibility violation: ${violation.id}`,
        detail: `${violation.id} (${violation.impact || 'minor'}), ${nodes.length} node(s): ${nodes.slice(0, 3).map((n) => (n.target || []).join(' ')).join(', ')}`,
        selector: nodes.length ? (nodes[0].target || []).join(' ') : '',
        viewport: v.viewport,
      });
    }
  },
};
