'use strict';
/* Stagehand happy-path runner. Each config walkthrough runs with Stagehand
 * v3 (3.7.x, the version the fleet standardised on) in env LOCAL; the model
 * goes through OpenRouter (openai/<model> + baseURL) with the key from
 * OPENROUTER_API_KEY, never written anywhere. A failed step or an unmet expect is
 * a serious problem with a screenshot per step; after every step the overlays
 * scan re-runs (SPEC). When Stagehand or the key is unavailable the
 * walkthrough lands in skipped[] instead of crashing.
 *
 * Stagehand v3 reports a step that could not be done as { success: false }
 * rather than throwing, so both forms count as a failed step. v4 is not used:
 * it only accepts a fixed list of vendor model names, no OpenRouter route.
 *
 * A step written "wait N seconds" (N up to 99) only pauses; "wait up to N
 * seconds for "words"" (N up to 999) goes on as soon as the page shows those
 * words and fails when they never come. A date relative to the run is
 * written {{weekday+7}} (./step-dates.js).
 *
 * A walkthrough with media: "fake" gets a browser that has a camera, a
 * microphone and a screen to share (./media.js): a page that records the
 * visitor can be walked like any other. Stagehand v3 reads and acts inside
 * frames by itself, so a site shown in an iframe needs nothing more.
 *
 * opts.setupContext(context, walkthrough) runs on the walkthrough's browser
 * context before its first page opens — the caller's own instrumentation
 * (a signed-in cookie for a page only its owner may open), never part of a
 * consumer's config.
 *
 * Login for role walkthroughs is out of scope for v1: steps start from
 * walkthrough.start; a login redirect mid-run surfaces as a failed step. */

const path = require('node:path');
const { scanOverlaysInPage } = require('./checks/overlays');
const { resolveOpenRouterKey } = require('../config');
const { pageSlug } = require('./report');
const { launchArgs } = require('./media');
const { expandDates } = require('./step-dates');

const RETRY_MS = Number(process.env.USABILITY_KIT_RETRY_MS) >= 0 && process.env.USABILITY_KIT_RETRY_MS !== undefined ? Number(process.env.USABILITY_KIT_RETRY_MS) : 3000;
let StagehandCtor = undefined; // undefined = not tried, null = unavailable

function loadStagehand() {
  if (StagehandCtor !== undefined) return StagehandCtor;
  try {
    const mod = require('@browserbasehq/stagehand');
    StagehandCtor = mod.Stagehand || mod.default || null;
  } catch (e) {
    StagehandCtor = null;
  }
  return StagehandCtor;
}

async function checkOverlays(add, page, name, stepNo) {
  const found = await scanOverlaysInPage(page).catch(() => []);
  if (found.length > 1) {
    add({
      check: 'overlays', severity: 'serious', page: `walkthrough:${name}`, role: 'public', viewport: null,
      title: found.length === 2 ? 'Two overlays open at the same time' : `${found.length} overlays open at the same time`,
      detail: `after step ${stepNo}: ${found.map((x) => x.selector).join(' + ')}`,
      selector: found[0].selector,
    });
  }
}

/* True once any frame of the page shows the words (hidden text does not
 * count: innerText leaves it out). */
async function shows(page, words, withinMs) {
  const seen = (text) => Boolean(document.body && document.body.innerText.includes(text));
  const until = Date.now() + withinMs;
  for (;;) {
    const frames = typeof page.frames === 'function' ? page.frames() : [page];
    for (const frame of frames) {
      if (await frame.evaluate(seen, words).catch(() => false)) return true;
    }
    if (Date.now() >= until) return false;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

async function runWalkthroughs({ config, opts, baseUrl, add, skipped, shotsDir, walkthroughs }) {
  const record = (entry) => { if (Array.isArray(walkthroughs)) walkthroughs.push(entry); };
  const all = config.walkthroughs || [];
  const list = all.filter(w=>w.driver!=='playwright');
  if(!opts.noWalkthrough)await require('./scripted-walkthrough').runScripted({config,opts,baseUrl,add,skipped,shotsDir,walkthroughs});
  if (!list.length || opts.noWalkthrough) {
    if (all.length&&opts.noWalkthrough) skipped.push({ kind: 'check', name: 'walkthrough', check: 'walkthrough', reason: 'disabled by --no-walkthrough' });
    return;
  }
  const Stagehand = opts.stagehand || loadStagehand();
  if (!Stagehand) {
    skipped.push({ kind: 'check', name: 'walkthrough', check: 'walkthrough', reason: '@browserbasehq/stagehand not installed (needs Node >= 22)' });
    return;
  }
  const key = await resolveOpenRouterKey(config);
  if (!key) {
    skipped.push({ kind: 'check', name: 'walkthrough', check: 'walkthrough', reason: 'no OpenRouter key (OPENROUTER_API_KEY unset)' });
    return;
  }
  const model = (config.llm && config.llm.model) || 'z-ai/glm-5.3-flash';

  for (const w of list) {
    let stagehand;
    let failed = false;
    const flag = (p) => { failed = true; add(p); };
    const stepFailed = (stepNo, step, why) => flag({
      check: 'walkthrough', severity: 'serious', page: w.start, role: w.role || 'public', viewport: null,
      title: 'Walkthrough step failed',
      detail: `${w.name}, step ${stepNo} (“${String(step).slice(0, 80)}”): ${String(why).slice(0, 120)}`,
      selector: '',
    });
    try {
      stagehand = new Stagehand({
        env: 'LOCAL',
        verbose: 0,
        /* The browser speaks the site's language (config.language): a page
         * that follows the visitor's language shows the walkthrough what
         * its real visitors see. */
        localBrowserLaunchOptions: { headless: true, ...(config.language ? { locale: config.language } : {}), ...launchArgs([w]) },
        model: { modelName: `openai/${model}`, apiKey: key, baseURL: 'https://openrouter.ai/api/v1', openaiEndpointFormat: 'chat' },
      });
      await stagehand.init();
      if (opts.setupContext) await opts.setupContext(stagehand.context, w);
      const page = await stagehand.context.newPage();
      await page.goto(baseUrl + w.start, { waitUntil: 'load', timeout: 45000 });

      let stepNo = 0;
      for (const written of w.steps || []) {
        /* {{weekday+7}} and the like become real dates (./step-dates.js). */
        const step = expandDates(written, { language: config.language });
        stepNo += 1;
        try {
          /* "wait 5 seconds" is a pause, not an action for the model: a map or
           * a price that loads after the last click needs time, not a click. */
          const pause = /^\s*wait\s+(\d{1,2})\s+seconds?\s*$/i.exec(String(step));
          if (pause) { await new Promise((resolve) => setTimeout(resolve, Number(pause[1]) * 1000)); continue; }
          /* A page that gets ready in its own time (an upload, a device
           * check) is waited for by what it shows, never by a guessed pause. */
          const until = /^\s*wait\s+up\s+to\s+(\d{1,3})\s+seconds?\s+for\s+["“«](.+)["”»]\s*$/i.exec(String(step));
          if (until) {
            if (!await shows(page, until[2], Number(until[1]) * 1000)) stepFailed(stepNo, step, `the page never showed “${until[2]}”`);
            continue;
          }
          /* A model that finds nothing to do on a page still settling is
           * asked once more on a fresh look; twice is the page's failure. */
          let res = await stagehand.act(step, { page }).catch((e) => ({ success: false, message: e.message || String(e) }));
          if (res && res.success === false) {
            await new Promise((resolve) => setTimeout(resolve, RETRY_MS));
            res = await stagehand.act(step, { page });
          }
          if (res && res.success === false) stepFailed(stepNo, step, res.message || 'the step could not be done');
        } catch (e) {
          stepFailed(stepNo, step, e.message || e);
        }
        await checkOverlays(add, page, w.name, stepNo);
        if (shotsDir) {
          try {
            await page.screenshot({ path: path.join(shotsDir, `walkthrough-${pageSlug(w.name)}-${stepNo}.png`) });
          } catch (e) { /* best effort */ }
        }
      }

      /* What the page looked like when the expectation was judged: the
       * evidence a person checks the model's yes against. */
      if (shotsDir) {
        try { await page.screenshot({ path: path.join(shotsDir, `walkthrough-${pageSlug(w.name)}-end.png`) }); } catch (e) { /* best effort */ }
      }
      if (w.expect) {
        /* Only a yes passes and a no fails at once. A reply that is neither
         * (the model echoing its output schema, a provider error) says
         * nothing about the page: the question is asked again, three times
         * at most, and no answer at all still fails. */
        let answer = null;
        for (let attempt = 0; attempt < 3 && answer === null; attempt += 1) {
          try {
            const res = await stagehand.extract(`Does the page now show: ${w.expect}? Answer strictly yes or no.`, { page });
            const said = String((res && (res.extraction !== undefined ? res.extraction : res.yes)) ?? '');
            if (/^\s*yes\b/i.test(said)) answer = true;
            else if (/^\s*no\b/i.test(said)) answer = false;
          } catch (e) {
            answer = null;
          }
        }
        if (answer !== true) {
          flag({
            check: 'walkthrough', severity: 'serious', page: w.start, role: w.role || 'public', viewport: null,
            title: 'Walkthrough expectation not met',
            detail: `${w.name}: expected “${String(w.expect).slice(0, 100)}”`,
            selector: '',
          });
        }
      }
      record({ name: w.name, start: w.start, steps: (w.steps || []).length, ran: true, ok: !failed });
    } catch (e) {
      record({ name: w.name, start: w.start, steps: (w.steps || []).length, ran: false, ok: false });
      skipped.push({
        kind: 'check', name: 'walkthrough', check: 'walkthrough',
        reason: `${w.name}: ${String(e.message || e).slice(0, 120)}`,
      });
    } finally {
      if (stagehand && typeof stagehand.close === 'function') await stagehand.close().catch(() => {});
    }
  }
}

module.exports = { runWalkthroughs };
