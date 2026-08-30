import test from "node:test";
import assert from "node:assert/strict";
import { createRequestGate } from "../src/latest-request.js";
import { adoptLatestPlayerListUpdate } from "../src/player-list-adoption.js";

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

test("a player-list update publishes profile and dataset in one commit", async () => {
  const gate = createRequestGate();
  const request = gate.claim();
  const profile = { profile_id: "a" };
  const dataset = { players: [] };
  const commits = [];

  const adopted = await adoptLatestPlayerListUpdate({
    request,
    isCurrent: gate.isCurrent,
    loadProfile: async () => profile,
    loadDataset: async (loadedProfile) => {
      assert.equal(loadedProfile, profile);
      assert.equal(commits.length, 0);
      return dataset;
    },
    commit: (...values) => commits.push(values),
  });

  assert.equal(adopted, true);
  assert.deepEqual(commits, [[profile, dataset]]);
});

test("a profile switch prevents an older player-list update from committing", async () => {
  const gate = createRequestGate();
  const request = gate.claim();
  const pendingProfile = deferred();
  let datasetLoads = 0;
  let commits = 0;
  const adoption = adoptLatestPlayerListUpdate({
    request,
    isCurrent: gate.isCurrent,
    loadProfile: () => pendingProfile.promise,
    loadDataset: async () => {
      datasetLoads += 1;
      return {};
    },
    commit: () => {
      commits += 1;
    },
  });

  gate.claim();
  pendingProfile.resolve({ profile_id: "a" });

  assert.equal(await adoption, false);
  assert.equal(datasetLoads, 0);
  assert.equal(commits, 0);
});

test("a switch while the update dataset is loading prevents its commit", async () => {
  const gate = createRequestGate();
  const request = gate.claim();
  const pendingDataset = deferred();
  let commits = 0;
  const adoption = adoptLatestPlayerListUpdate({
    request,
    isCurrent: gate.isCurrent,
    loadProfile: async () => ({ profile_id: "a" }),
    loadDataset: () => pendingDataset.promise,
    commit: () => {
      commits += 1;
    },
  });

  await Promise.resolve();
  gate.claim();
  pendingDataset.resolve({ players: [] });

  assert.equal(await adoption, false);
  assert.equal(commits, 0);
});
