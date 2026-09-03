import assert from "node:assert/strict";
import test from "node:test";

import { auctionSimulationInput, sameAuctionRosters } from "../src/auction-simulation.js";

const rules = { participants: 2, rosterSlots: { P: 1, D: 1 } };
const calendar = { teams: ["Calendario A", "Calendario B"] };
const board = {
  storageReadOk: true,
  auctionStatus: "valid",
  teams: [
    { name: "Alias A", roster: [{ id: 2, ruolo: "D" }, { id: 1, ruolo: "P" }] },
    { name: "Alias B", roster: [{ id: 4, ruolo: "D" }, { id: 3, ruolo: "P" }] },
  ],
};

test("builds complete roster input from owner indexes, not renamed aliases", () => {
  const result = auctionSimulationInput(board, calendar, rules);

  assert.equal(result.complete, true);
  assert.deepEqual(result.rosters, { "Calendario A": [1, 2], "Calendario B": [3, 4] });
  assert.deepEqual(result.aliases, { "Calendario A": "Alias A", "Calendario B": "Alias B" });
});

test("requires complete compatible auction rosters", () => {
  const result = auctionSimulationInput({ ...board, teams: [{ ...board.teams[0], roster: [board.teams[0].roster[0]] }, board.teams[1]] }, calendar, rules);

  assert.equal(result.complete, false);
  assert.match(result.reason, /Completa tutte/);
});

test("roster comparison ignores aliases and player order but sees ownership changes", () => {
  assert.equal(sameAuctionRosters({ A: [2, 1], B: [4, 3] }, { B: [3, 4], A: [1, 2] }), true);
  assert.equal(sameAuctionRosters({ A: [1, 2], B: [3, 4] }, { A: [1, 3], B: [2, 4] }), false);
});
