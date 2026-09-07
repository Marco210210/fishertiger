import test from "node:test";
import assert from "node:assert/strict";
import { datasetFreshness } from "../src/dataset-freshness.js";

const profile = {
  dataset_configuration_hash: "dataset-config",
  simulation_configuration_hash: "simulation-config",
  current_sources: [
    { name: "player_list", required: true },
    { name: "league_calendar", required: false },
  ],
  history_sources: [{ name: "stats_2024_25", required: true }],
};
const dataset = (meta) => ({ meta: { profile: { dataset_configuration_hash: "dataset-config", ...meta } } });

test("a dataset generated from the active profile is current", () => {
  assert.equal(datasetFreshness(profile, dataset({})), "dataset corrente");
});
test("an optional source that was never provided is not a change", () => {
  const data = dataset({
    source_fingerprints: [
      { name: "player_list", exists: true },
      { name: "league_calendar", exists: false },
    ],
  });
  assert.equal(datasetFreshness(profile, data), "dataset corrente");
});

test("a missing required source is reported", () => {
  const data = dataset({
    source_fingerprints: [{ name: "player_list", exists: false }],
  });
  assert.equal(datasetFreshness(profile, data), "fonti cambiate");
});

test("a source replaced at the same path is reported from its live hash", () => {
  const data = dataset({
    source_fingerprints: [{ group: "current_sources", name: "player_list", exists: true, sha256: "old" }],
  });
  const current = [{ group: "current_sources", name: "player_list", exists: true, sha256: "new" }];
  assert.equal(datasetFreshness(profile, data, current), "fonti cambiate");
});

test("matching live source hashes keep the dataset current", () => {
  const fingerprints = [{ group: "current_sources", name: "player_list", exists: true, sha256: "same" }];
  assert.equal(datasetFreshness(profile, dataset({ source_fingerprints: fingerprints }), fingerprints), "dataset corrente");
});

test("a source the profile no longer declares counts as required", () => {
  const data = dataset({
    source_fingerprints: [{ name: "dropped_source", exists: false }],
  });
  assert.equal(datasetFreshness(profile, data), "fonti cambiate");
});

test("a profile edited after generation needs a regeneration", () => {
  assert.equal(
    datasetFreshness({ ...profile, dataset_configuration_hash: "changed" }, dataset({})),
    "dataset da rigenerare",
  );
});

test("save-only profile changes do not make a dataset stale", () => {
  assert.equal(
    datasetFreshness({ ...profile, name: "Renamed" }, dataset({})),
    "dataset corrente",
  );
});

test("a dataset without freshness metadata is not claimed to be current", () => {
  assert.equal(datasetFreshness(profile, { meta: {} }), "dataset da rigenerare");
  assert.equal(datasetFreshness(profile, undefined), "dataset da rigenerare");
});
