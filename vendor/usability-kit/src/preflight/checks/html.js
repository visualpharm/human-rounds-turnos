'use strict';
/* html — html-validate with a small safe rule set: no-dup-id,
 * unique-landmark, wcag/h37 (img alt), element-required-attributes. Runs once
 * per page against the serialized DOM of the first viewport. */

let validator = null; // memoized: HtmlValidate instance | false (not installed)

function loadValidator() {
  if (validator !== null) return validator;
  try {
    const { HtmlValidate } = require('html-validate');
    validator = new HtmlValidate({
      rules: {
        'no-dup-id': 'error',
        'unique-landmark': 'error',
        'wcag/h37': 'error',
        'element-required-attributes': 'error',
      },
    });
  } catch (e) {
    validator = false;
  }
  return validator;
}

module.exports = {
  id: 'html',
  async scan(v) {
    if (v.viewport !== v.viewports[0]) return;
    const hv = loadValidator();
    if (!hv) {
      v.skip('html', 'html-validate not installed');
      return;
    }
    const html = await v.page.content();
    const report = await hv.validateString(html);
    for (const result of report.results || []) {
      for (const m of result.messages || []) {
        v.add({
          check: 'html',
          severity: 'minor',
          title: `HTML validation issue: ${m.ruleId}`,
          detail: `${m.message} (line ${m.line}, column ${m.column})`,
          selector: `line:${m.line}:${m.column}`,
          viewport: v.viewport,
        });
      }
    }
  },
};
