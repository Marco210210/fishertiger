import test from "node:test";
import assert from "node:assert/strict";
import { createLineupUtility } from "../src/lineup-utility.js";

let nextId = 1;
const player = (role, probability, score, overrides = {}) => ({
  id: nextId++,
  ruolo: role,
  squadra: `${role}-${nextId}`,
  p_gioca_per_giornata: [probability, probability],
  voto_puro_mean_per_giornata: [score, score],
  bonus_atteso_per_giornata: [0, 0],
  ...overrides,
});
const rules = {
  participants: 8,
  rosterSlots: { P: 3, D: 8, C: 8, A: 6 },
  formations: [[3, 4, 3]],
  horizons: { currentLeague: { matchdayIndices: [0, 1] } },
};

test("a reserve adds no lineup value after reliable role coverage is full", () => {
  const utility = createLineupUtility(rules);
  const roster = Array.from({ length: 3 }, () => player("D", 1, 6));

  const result = utility.evaluateCandidate(player("D", 0.2, 5), roster);

  assert.equal(result.marginalUtility, 0);
  assert.equal(result.purpose, "DEPTH");
});

test("a reliable reserve adds coverage when a starter may miss votes", () => {
  const utility = createLineupUtility(rules);
  const roster = [
    player("D", 1, 6),
    player("D", 1, 6),
    player("D", 0.5, 6),
  ];

  const result = utility.evaluateCandidate(player("D", 0.5, 5), roster);

  assert.ok(result.marginalUtility > 0);
  assert.equal(result.purpose, "COVERAGE");
});

test("same-club goalkeepers combine into one capped coverage unit", () => {
  const utility = createLineupUtility(rules);
  const starter = player("P", 0.8, 6, { squadra: "Roma" });
  const deputy = player("P", 0.2, 5, { squadra: "Roma" });

  const result = utility.evaluateCandidate(deputy, [starter]);

  assert.equal(result.purpose, "HANDCUFF");
  assert.ok(Math.abs(result.goalkeeper.voteCoverageGain - 0.2) < 1e-9);
});

test("same-club coverage follows explicit goalkeeper hierarchy", () => {
  const utility = createLineupUtility(rules);
  const first = player("P", 0.8, 6, {
    squadra: "Roma",
    gerarchia_portiere: "PRIMO",
  });
  const second = player("P", 0.8, 5, {
    squadra: "Roma",
    gerarchia_portiere: "SECONDO",
  });

  const result = utility.evaluateRoster([first, second]);

  assert.equal(result.goalkeeper.voteCoverage, 1);
  assert.ok(Math.abs(result.utility - 11.6) < 1e-9);
});

test("different-club goalkeepers provide independent fallback coverage", () => {
  const utility = createLineupUtility(rules);
  const first = player("P", 0.8, 6, { squadra: "Roma" });
  const second = player("P", 0.5, 6, { squadra: "Milan" });

  const result = utility.evaluateCandidate(second, [first]);

  assert.equal(result.purpose, "COVERAGE");
  assert.ok(Math.abs(result.goalkeeper.voteCoverageGain - 0.1) < 1e-9);
});

test("daily projections reward complementary goalkeeper fixtures", () => {
  const utility = createLineupUtility(rules);
  const first = player("P", 1, 6, {
    squadra: "Roma",
    voto_puro_mean_per_giornata: [7, 5],
  });
  const complementary = player("P", 1, 6, {
    squadra: "Milan",
    voto_puro_mean_per_giornata: [5, 7],
  });

  const result = utility.evaluateCandidate(complementary, [first]);

  assert.equal(result.purpose, "ROTATION");
  assert.equal(result.marginalUtility, 2);
});

test("only explicitly confirmed inactive players are ineligible", () => {
  const utility = createLineupUtility(rules);
  const unknown = player("A", 0.1, 6, {
    disponibilita: { status: "NON_CLASSIFICATO" },
  });
  const inactive = player("A", 1, 10, {
    disponibilita: { confirmed_inactive: true },
  });

  assert.equal(utility.evaluateCandidate(unknown, []).eligible, true);
  assert.equal(utility.evaluateCandidate(inactive, []).eligible, false);
});

test("independent reserve coverage uses absence probability", () => {
  const utility = createLineupUtility({
    ...rules,
    formations: [[1, 0, 0]],
    bench: { roles: ["D"], maxSubstitutions: 1, mode: "Basic" },
  });
  const result = utility.evaluateRoster([
    player("D", 0.5, 6),
    player("D", 0.5, 6),
  ]);

  assert.equal(result.daily[0].outfieldCoverage, 0.75);
});

test("None mode gives reserves no substitution value", () => {
  const utility = createLineupUtility({
    ...rules,
    formations: [[1, 0, 0]],
    bench: { roles: ["D"], maxSubstitutions: 1, mode: "None" },
  });
  const starter = player("D", 0.5, 6);
  const reserve = player("D", 0.5, 5);

  assert.equal(utility.evaluateCandidate(reserve, [starter]).marginalUtility, 0);
});

test("zero-vote players receive no upside from variance", () => {
  const utility = createLineupUtility(rules);
  const candidate = player("A", 0, 6, {
    voto_puro_std_per_giornata: [10, 10],
  });

  assert.equal(utility.evaluateCandidate(candidate, []).marginalUtility, 0);
  assert.equal(utility.evaluateCandidate(candidate, []).upsideGain, 0);
});

test("defense modifier is evaluated inside a legal formation", () => {
  const utility = createLineupUtility({
    ...rules,
    formations: [[4, 0, 0]],
    defenseModifier: {
      enabled: true,
      requiredDefenders: 4,
      tiers: [{ threshold: 6, bonus: 1 }],
    },
  });
  const roster = [
    player("P", 1, 6),
    ...Array.from({ length: 4 }, () => player("D", 1, 6)),
  ];

  assert.equal(utility.evaluateRoster(roster).daily[0].defenseModifier, 1);
});
