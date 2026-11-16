// platform/analytics.mjs — the anonymous page/step beacon of the analytics stream.
//
// Loaded by nav.mjs only when the server set window.SANA_EV (TURNOS_ANALYTICS=1). It sends the page template
// (not the query string), the referrer's host, the language and two random ids and nothing else: `jid`
// (a visit, kept in sessionStorage — it dies with the tab and rotates after 30 minutes of silence) and `pl`
// (this page load). No cookie, no persistent id; the server adds the device class from the User-Agent and
// drops the IP. A step raised by another module goes through the queue window.__hrq (it may run before this
// file arrives); this file drains it and then owns it. Every failure is swallowed: statistics never break a page.

const KEY = "hr_jid";
const IDLE_MS = 30 * 60 * 1000;
const ENDPOINT = "/api/ev";
const MAX_QUEUE = 20;

function rid() {
  try {
    const a = new Uint8Array(9);
    crypto.getRandomValues(a);
    return Array.from(a, (b) => b.toString(36).padStart(2, "0")).join("").slice(0, 14);
  } catch (e) {
    return Math.random().toString(36).slice(2, 12) + Date.now().toString(36);
  }
}

function visit() {
  const now = Date.now();
  try {
    const raw = sessionStorage.getItem(KEY + "_t");
    let jid = sessionStorage.getItem(KEY);
    if (!jid || !raw || now - Number(raw) > IDLE_MS) {
      jid = rid();
      sessionStorage.setItem(KEY, jid);
    }
    sessionStorage.setItem(KEY + "_t", String(now));
    return jid;
  } catch (e) {
    return rid();
  }
}

const PL = rid();
let timer = null;
const pending = [];

function flush() {
  timer = null;
  if (!pending.length) return;
  const batch = pending.splice(0, MAX_QUEUE);
  const body = JSON.stringify({ events: batch });
  let sent = false;
  try {
    sent = !!(navigator.sendBeacon && navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" })));
  } catch (e) { /* fall through to fetch */ }
  if (!sent) {
    try {
      fetch(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true })
        .catch(() => {});
    } catch (e) { /* statistics never break a page */ }
  }
  if (pending.length) flush();
}

function emit(e) {
  if (pending.length >= 100) return;
  e.jid = visit();
  e.pl = PL;
  try { if (navigator.webdriver) e.wd = 1; } catch (err) { /* no navigator */ }
  pending.push(e);
  if (!timer) timer = setTimeout(flush, 1500);
}

function pageView() {
  let lang = "";
  try { lang = (document.documentElement.lang || "").slice(0, 2); } catch (e) { /* none */ }
  let ref = "";
  try { ref = document.referrer ? new URL(document.referrer).hostname : ""; } catch (e) { /* none */ }
  emit({ t: "page_view", path: location.pathname, ref, lang });
}

function drain() {
  const q = window.__hrq || [];
  while (q.length) {
    const item = q.shift();
    if (item && item.t === "step") emit({ t: "step", step: item.step, service: item.service || "" });
  }
  window.__hrq = { push(item) { if (item && item.t === "step") emit({ t: "step", step: item.step, service: item.service || "" }); } };
}

try {
  pageView();
  drain();
  addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
} catch (e) { /* statistics never break a page */ }
