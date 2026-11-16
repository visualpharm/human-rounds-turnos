// The harness itself under test: two same-label fixtures get physically separate
// databases, and tearing one down leaves the other usable.
//
// Port of tests/e2e/harness-db-isolation.e2e.mjs (1 test, same title).
//
// Differences from the node:test original:
// - The test never touches a browser: it calls the harness directly (`startApp` is
//   setupHarness with launchBrowser:false, the same entry the worker fixture uses), so the
//   two apps it boots are exactly the original's minus the Chromium process each one used
//   to launch for nothing; the runner launches no browser either, because the test asks
//   for neither `page` nor `browser`. Teardown stays an explicit try/finally, as the test
//   tears the first app down in the middle of its body.
// - The file is `hermetic` (no worker-scoped app is started for it): the two apps under test are the
//   whole subject, and keep the original shared label LABEL.
// - `requireOk` is the harness's own, with the fixtures' skip hook (gate mode: a missing
//   prerequisite fails; `npm run e2e:dev`: skips with the reason); `assert.rejects` is
//   `expect(promise).rejects.toThrow(regex)` with the same message.
// - The test boots two apps in one body, so it gets a 120 s timeout (the original had none).
import { test, expect, startApp } from "./lib/fixtures.mjs";
import { queryRequest, requireOk, teardownHarness } from "./lib/harness.mjs";

test.use({ appOptions: { dbName: "sana_turnos_test_e2e_harness_isolation_file", hermetic: true } });

const LABEL = "sana_turnos_test_e2e_harness_isolation";

test("same-label E2E fixtures use isolated databases and one teardown leaves the other usable", async () => {
  test.setTimeout(120_000);
  const skip = { skip: (reason) => test.skip(true, reason) };
  let first;
  let second;
  try {
    first = await startApp({ dbName: LABEL, requests: true });
    requireOk(first, skip);
    second = await startApp({ dbName: LABEL, requests: true });
    requireOk(second, skip);

    expect(first.dbName).not.toBe(second.dbName);
    expect(first.dbName).toMatch(/^sana_turnos_test_[a-zA-Z0-9_]+$/);
    expect(second.dbName).toMatch(/^sana_turnos_test_[a-zA-Z0-9_]+$/);
    expect((await queryRequest(first.dbName, first.fixture.requests[0].dni)).found).toBe(true);
    expect((await queryRequest(second.dbName, second.fixture.requests[0].dni)).found).toBe(true);

    const removedDbName = first.dbName;
    const removedDni = first.fixture.requests[0].dni;
    await teardownHarness(first);
    first = null;
    await expect(
      queryRequest(removedDbName, removedDni),
      "teardown removed the first physical database",
    ).rejects.toThrow(/database .* does not exist/i);
    expect((await queryRequest(second.dbName, second.fixture.requests[0].dni)).found).toBe(true);
  } finally {
    if (first) await teardownHarness(first);
    if (second) await teardownHarness(second);
  }
});
