'use strict';
/* Fake capture devices for a walkthrough that asks for them with
 * media: "fake" in the consumer's config. Some pages cannot be walked without
 * a camera, a microphone and a screen to share (a recorded research session
 * asks for all three before its first task); a headless browser has none and
 * nobody to answer the permission prompts. Chromium's own test devices stand
 * in: a generated camera picture and tone, every media prompt accepted, and
 * "Entire screen" picked in the share dialog. Nothing real is captured, and a
 * walkthrough that does not ask keeps the plain browser it always had. */

const FAKE_MEDIA_ARGS = Object.freeze([
  '--use-fake-ui-for-media-stream',
  '--use-fake-device-for-media-stream',
  '--auto-select-desktop-capture-source=Entire screen',
]);

/* Launch options for a browser shared by these walkthroughs: { args } when
 * any of them asks for fake media, {} otherwise. */
function launchArgs(walkthroughs) {
  return (walkthroughs || []).some((w) => w && w.media === 'fake') ? { args: [...FAKE_MEDIA_ARGS] } : {};
}

module.exports = { FAKE_MEDIA_ARGS, launchArgs };
