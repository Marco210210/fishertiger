import test from "node:test";
import assert from "node:assert/strict";
import { evaluateAuction, evaluateOverview } from "../src/simulation.worker.js";

const rules = {
  participants: 4,
  teamNames: ["Arco", "Borgo", "Cima", "Duna"],
  rosterSlots: { P: 1, D: 4, C: 4, A: 3 },
  formations: [[4, 4, 2]],
  startingCredits: 120,
  bench: { maxSubstitutions: 2, switch: true },
  scoring: { goalkeeperConceded: -1 },
  virtualGoals: { threshold: 60, increment: 4 },
  defenseModifier: { requiredDefenders: 4, tiers: [{ threshold: 6, bonus: 2 }] },
  standings: { win: 2, draw: 1, loss: 0, tieBreakers: ["season_fantasy_score"], exactTie: "shared" },
  incompleteLineup: "error",
  auction: { minPrice: 2, increment: 2, reserve: 2, nomination: "rotation" },
  calendario_lega: [
    [["Arco", "Borgo"], ["Cima", "Duna"]],
    [["Arco", "Cima"], ["Borgo", "Duna"]],
  ],
};

const players = [];
let id = 1;
for (const [role, slots] of Object.entries(rules.rosterSlots)) {
  for (let index = 0; index < slots * rules.participants + 4; index++) {
    players.push({ id: id++, nome: `${role}-${index}`, ruolo: role, fvm_scaled: 10 + index, p_gioca_per_giornata: [1, 1], voto_puro_mean_per_giornata: [6.5, 6.5], voto_puro_std_per_giornata: [0, 0], bonus_atteso_per_giornata: [0, 0], gol_subiti_per_giornata: [0, 0] });
  }
}

test("worker plans custom roles and preserves the configured two-credit reserve", () => {
  const roster = players.filter((player) => player.ruolo === "P").slice(0, 1);
  const teams = [{ name: "Arco", credits: 30, roster }];
  const candidate = players.find((player) => player.ruolo === "D");
  const data = { rules, teams, mine: teams[0], owner: 0, player: candidate, remaining: players, assigned: {} };
  assert.equal(evaluateAuction(data).legalMax, 10);
  assert.deepEqual(Object.keys(evaluateOverview(data).rolePlan).sort(), ["A", "C", "D", "P"]);
});

test("worker advice respects a custom minimum plus increment grid", () => {
  const customRules = {
    ...rules,
    auction: { ...rules.auction, minPrice: 2, increment: 3 },
  };
  const teams = [{ name: "Arco", credits: 31, roster: [] }];
  const advice = evaluateAuction({
    rules: customRules,
    teams,
    mine: teams[0],
    owner: 0,
    player: players.find((player) => player.ruolo === "P"),
    remaining: players,
    assigned: {},
  });
  for (const price of [
    advice.idealMin,
    advice.idealMax,
    advice.maxBid,
    advice.legalMax,
    advice.summary.estimatedMarketPrice,
  ].filter((price) => price > 0))
    assert.equal((price - 2) % 3, 0);
});
