import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { observeQueueStream } from "./e2e/lib/queue-stream-observer.mjs";

async function fixture(t) {
  const session = new EventEmitter();
  session.send = async () => {};
  session.detach = async () => {};
  const observer = await observeQueueStream({ context: () => ({ newCDPSession: async () => session }) });
  t.after(() => observer.close());
  const response = (id = "queue", status = 200, mimeType = "text/event-stream") => session.emit(
    "Network.responseReceived", { requestId: id, response: { url: "https://fixture.test/api/admin/day-queue/events", status, mimeType } });
  const frame = (eventName, data, requestId = "queue") => session.emit(
    "Network.eventSourceMessageReceived", { requestId, eventName, data: JSON.stringify(data) });
  return { observer, session, response, frame };
}

test("SSE waiter survives response arrival before the ready frame", async (t) => {
  const { observer, response, frame } = await fixture(t);
  const opened = observer.opened();
  response();
  frame("unrelated", {});
  frame("ready", { cursor: 0 });
  assert.equal(await opened, "queue");
});

test("ready requires matching successful stream, not errors or another request", async (t) => {
  const { observer, response, frame } = await fixture(t);
  let settled = false;
  const opened = observer.opened().then((id) => { settled = true; return id; });
  response("bad", 403); frame("ready", { cursor: 0 }, "bad");
  response("html", 200, "text/html"); frame("ready", { cursor: 0 }, "html");
  response(); frame("ready", { cursor: 0 }, "other");
  frame("ready", { cursor: "not-a-cursor" });
  await Promise.resolve();
  assert.equal(settled, false);
  frame("ready", { cursor: 9 });
  assert.equal(await opened, "queue");
});

test("queue update excludes old, wrong-day, malformed and unrelated-stream frames", async (t) => {
  const { observer, session, response, frame } = await fixture(t);
  response(); frame("ready", { cursor: 0 });
  frame("queue-update", { day: "2026-09-09" });
  let settled = false;
  const update = observer.queueUpdate({ after: observer.checkpoint(), day: "2026-09-09" })
    .then((value) => { settled = true; return value; });
  frame("queue-update", { day: "2026-09-10" });
  frame("queue-update", { day: "2026-09-09" }, "unrelated");
  session.emit("Network.eventSourceMessageReceived", { requestId: "queue", eventName: "queue-update", data: "{" });
  await Promise.resolve();
  assert.equal(settled, false);
  frame("queue-update", { day: "2026-09-09" });
  assert.deepEqual(await update, { day: "2026-09-09", requestId: "queue" });
});

test("close cancels outstanding waits and releases protocol listeners", async (t) => {
  const { observer, session } = await fixture(t);
  const cancelled = assert.rejects(observer.opened(), /observer closed/);
  await observer.close();
  await cancelled;
  assert.equal(session.listenerCount("Network.responseReceived"), 0);
  assert.equal(session.listenerCount("Network.eventSourceMessageReceived"), 0);
  await assert.rejects(observer.opened(), /observer closed/);
});
