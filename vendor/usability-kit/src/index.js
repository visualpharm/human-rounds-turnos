'use strict';
/* usability-kit — library entry (package.json main). Getters keep hosts that
 * only mount the panel from loading Playwright-backed preflight code. */

module.exports = {
  get config() { return require('./config'); },
  get runPreflight() { return require('./preflight').runPreflight; },
  get report() { return require('./preflight/report'); },
  get store() { return require('./store'); },
  get panel() {
    const api = require('./panel/api');
    return {
      render: require('./panel/render').render,
      createHandler: api.createHandler,
      page: api.page,
    };
  },
};
