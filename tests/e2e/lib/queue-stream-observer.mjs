// Observe the real Chromium EventSource, never an application diagnostic global.
import assert from "node:assert/strict";

const EVENTS_PATH = "/api/admin/day-queue/events";

export async function observeQueueStream(page, { timeoutMs = 10000 } = {}) {
  const session = await page.context().newCDPSession(page);
  const streams = new Map();
  const frames = [];
  const pending = new Set();
  let closed = false;
  // A response can precede its ready frame. Keep unsatisfied waiters registered.
  const wake = () => { for (const waiter of [...pending]) waiter.check(); };
  const response = ({ requestId, response }) => {
    if (new URL(response.url).pathname !== EVENTS_PATH) return;
    streams.set(requestId, response.status === 200 &&
      /^text\/event-stream(?:;|$)/i.test(response.mimeType ||
        Object.entries(response.headers || {}).find(([key]) => key.toLowerCase() === "content-type")?.[1] || ""));
    wake();
  };
  const message = (frame) => { frames.push(frame); wake(); };
  session.on("Network.responseReceived", response);
  session.on("Network.eventSourceMessageReceived", message);
  await session.send("Network.enable");
  function readyRequest(requestId) {
    return streams.get(requestId) === true && frames.some((frame) => {
      if (frame.requestId !== requestId || frame.eventName !== "ready") return false;
      try { return Number.isSafeInteger(JSON.parse(frame.data).cursor); } catch { return false; }
    });
  }
  function waitFor(predicate, label) {
    if (closed) return Promise.reject(new Error("queue stream observer closed"));
    return new Promise((resolve, reject) => {
      let timer;
      const finish = (error, value) => {
        clearTimeout(timer);
        pending.delete(waiter);
        if (error) reject(error); else resolve(value);
      };
      const waiter = {
        check() { const value = predicate(); if (value) finish(null, value); },
        cancel() { finish(new Error("queue stream observer closed")); },
      };
      pending.add(waiter);
      timer = setTimeout(() => finish(new Error(`${label} not observed within ${timeoutMs}ms`)), timeoutMs);
      waiter.check();
    });
  }
  return {
    opened: () => waitFor(() => [...streams.keys()].find(readyRequest), "successful SSE ready frame"),
    checkpoint: () => frames.length,
    queueUpdate({ after, day }) {
      assert.ok(Number.isInteger(after) && after >= 0, "an explicit pre-mutation checkpoint is required");
      assert.match(day, /^\d{4}-\d{2}-\d{2}$/);
      return waitFor(() => {
        for (const frame of frames.slice(after)) {
          if (frame.eventName !== "queue-update" || !readyRequest(frame.requestId)) continue;
          try { if (JSON.parse(frame.data).day === day) return { day, requestId: frame.requestId }; }
          catch { /* malformed data cannot prove delivery */ }
        }
        return null;
      }, "fresh queue-update frame for the mutated day");
    },
    async close() {
      if (closed) return;
      closed = true;
      for (const waiter of [...pending]) waiter.cancel();
      session.off("Network.responseReceived", response);
      session.off("Network.eventSourceMessageReceived", message);
      await session.detach();
    },
  };
}
