import assert from "node:assert/strict";
import test from "node:test";

import { sourceFvm } from "../src/player-valuation.js";
import { enrichPlayersWithScout, scoutSeasonSlug } from "../src/scout-ai.js";

test("Scout AI enriches matching players and adjusts the market anchor", () => {
  const [player] = enrichPlayersWithScout(
    [{ id: 42, fvm_original: 100 }],
    { players: { "42": { player_id: 42, multiplier: 0.8, status: "out" } } },
  );
  assert.equal(player.scout_ai.status, "out");
  assert.equal(sourceFvm(player), 80);
});

test("Scout AI leaves players without material news unchanged", () => {
  const players = [{ id: 7, fvm_original: 20 }];
  assert.deepEqual(enrichPlayersWithScout(players, { players: {} }), players);
  assert.equal(sourceFvm(players[0]), 20);
  assert.equal(scoutSeasonSlug({ season: { season: "2026/27" } }), "2026-27");
});
