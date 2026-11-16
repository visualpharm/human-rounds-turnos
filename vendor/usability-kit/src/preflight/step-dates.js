'use strict';
/* Dates relative to the run, for a walkthrough step that books something: a
 * step is written once in a consumer's config, and a literal date in it is in
 * the past a week later. Before a step reaches its driver:
 *
 *   {{date+N}}        the day N days from today
 *   {{weekday+N}}     the first Monday-to-Friday on or after that day
 *   {{weekday+N..M}}  a Monday-to-Friday between N and M days ahead, spread by
 *                     the clock (the minute of the run), so two runs close
 *                     together rarely ask for the same day
 *
 * Written the way the config's language writes a date (es-AR: 14/10/2026,
 * en-US: 10/14/2026). Anything else in the step is left as it is. */

const TOKEN = /\{\{(date|weekday)\+(\d{1,3})(?:\.\.(\d{1,3}))?\}\}/g;

function written(day, language) {
  try {
    return new Intl.DateTimeFormat(language || 'en-US', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(day);
  } catch (e) {
    return new Intl.DateTimeFormat('en-US', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(day);
  }
}

function expandDates(step, { now = new Date(), language = 'en-US' } = {}) {
  return String(step).replace(TOKEN, (all, kind, from, to) => {
    const lo = Number(from);
    const hi = to === undefined ? lo : Math.max(lo, Number(to));
    const spread = hi > lo ? (now.getHours() * 60 + now.getMinutes()) % (hi - lo + 1) : 0;
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + lo + spread, 12);
    if (kind === 'weekday') while (day.getDay() === 0 || day.getDay() === 6) day.setDate(day.getDate() + 1);
    return written(day, language);
  });
}

module.exports = { expandDates };
