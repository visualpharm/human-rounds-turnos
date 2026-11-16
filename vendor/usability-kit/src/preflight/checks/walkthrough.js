'use strict';
/* walkthrough — see src/preflight/walkthrough.js; this module only keeps the
 * check registry entry pointing at the runner. */

const { runWalkthroughs } = require('../walkthrough');

module.exports = {
  id: 'walkthrough',
  finishGlobal(f) {
    const { shotsDir } = f;
    return runWalkthroughs({ ...f, shotsDir });
  },
};
