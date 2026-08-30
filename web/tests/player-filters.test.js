import test from "node:test";
import assert from "node:assert/strict";
import {
  defaultPlayerFilters,
  normalizePlayerFilters,
  playerFiltersStorageKey,
} from "../src/player-filters.js";

const roles = ["TUTTI", "P", "D", "C", "A"];
const teams = ["TUTTE", "Roma", "Inter"];

test("filters are stored per profile", () => {
  assert.equal(playerFiltersStorageKey("Fantabosco"), "fanta-player-filters-v1:Fantabosco");
  assert.equal(playerFiltersStorageKey(undefined), "fanta-player-filters-v1:default");
});

test("a saved selection round-trips unchanged", () => {
  const saved = { query: "svil", role: "P", team: "Roma", onlyTargets: true, showLive: true };
  assert.deepEqual(normalizePlayerFilters(saved, roles, teams), saved);
});

test("a filter the current dataset cannot satisfy falls back instead of emptying the list", () => {
  const stale = { query: "x", role: "X", team: "Frosinone", onlyTargets: true, showLive: false };
  assert.deepEqual(normalizePlayerFilters(stale, roles, teams), {
    query: "x",
    role: "TUTTI",
    team: "TUTTE",
    onlyTargets: true,
    showLive: false,
  });
});

test("a missing or corrupt payload yields the defaults", () => {
  assert.deepEqual(normalizePlayerFilters(null, roles, teams), defaultPlayerFilters());
  assert.deepEqual(normalizePlayerFilters("nope", roles, teams), defaultPlayerFilters());
  assert.deepEqual(normalizePlayerFilters({ query: 7, onlyTargets: "si" }, roles, teams), defaultPlayerFilters());
});

test("an oversized query is truncated", () => {
  assert.equal(normalizePlayerFilters({ query: "a".repeat(500) }, roles, teams).query.length, 80);
});
