import test from "node:test";
import assert from "node:assert/strict";
import {
  evaluateAuction,
  evaluateOverview,
  evaluateRequest,
} from "../src/simulation.worker.js";

const LIMITS = { P: 3, D: 8, C: 8, A: 6 };
let nextId = 1;

const player = (role, value = 6, overrides = {}) => ({
  id: nextId++,
  nome: `${role}-${nextId}`,
  ruolo: role,
  squadra: "Test",
  fvm_scaled: 1,
  p_gioca_per_giornata: [1],
  voto_puro_mean_per_giornata: [value],
  bonus_atteso_per_giornata: [0],
  ...overrides,
});

const rosterWithOpenSlots = (open) =>
  Object.entries(LIMITS).flatMap(([role, limit]) =>
    Array.from({ length: limit - (open[role] || 0) }, () => player(role)),
  );

const team = (name, credits, open) => ({
  name,
  credits,
  roster: rosterWithOpenSlots(open),
});

const payloadFor = ({ candidate, teams, owner = 0, remaining = [] }) => ({
  player: candidate,
  owner,
  teams,
  mine: teams[0],
  remaining,
  assigned: {},
});

test("repeated evaluation is deterministic", () => {
  const candidate = player("A", 10, { fvm_scaled: 8 });
  const alternative = player("A", 7, { fvm_scaled: 4 });
  const teams = [team("Mine", 50, { A: 1 }), team("Opponent", 40, { A: 1 })];
  const payload = payloadFor({
    candidate,
    teams,
    remaining: [candidate, alternative],
  });

  assert.deepEqual(evaluateAuction(payload), evaluateAuction(payload));
});

test("selected player is removed from its own replacement pool", () => {
  const candidate = player("A", 20, { nome: "Candidate" });
  const alternative = player("A", 8, { nome: "Alternative" });
  const teams = [team("Mine", 30, { A: 1 })];

  const result = evaluateAuction(
    payloadFor({
      candidate,
      teams,
      remaining: [candidate, alternative],
    }),
  );

  assert.equal(result.summary.replacementValue, 8);
  assert.deepEqual(
    result.alternatives.map((item) => item.id),
    [alternative.id],
  );
});

test("league demand cutoff replaces the best-player benchmark", () => {
  const candidate = player("A", 10, { nome: "Candidate", fvm_scaled: 10 });
  const best = player("A", 20, { nome: "Best", fvm_scaled: 12 });
  const cutoff = player("A", 7, { nome: "Cutoff", fvm_scaled: 6 });
  const teams = [
    team("Mine", 100, { A: 1 }),
    team("Opponent", 100, { A: 1 }),
  ];

  const result = evaluateAuction(
    payloadFor({ candidate, teams, remaining: [candidate, best, cutoff] }),
  );

  assert.equal(result.summary.replacementRank, 2);
  assert.equal(result.summary.replacementValue, 7);
  assert.equal(result.summary.marginalValue, 3);
  assert.ok(result.maxBid > 0);
});

test("reservation price stays anchored to market instead of consuming all credits", () => {
  const candidate = player("A", 20, { fvm_scaled: 1 });
  const alternative = player("A", 19, { fvm_scaled: 9 });
  const teams = [{ name: "Mine", credits: 100, roster: [] }];

  const result = evaluateAuction(
    {
      ...payloadFor({ candidate, teams, remaining: [candidate, alternative] }),
      rules: {
        participants: 2,
        startingCredits: 100,
        rosterSlots: { A: 1 },
        auction: {
          roleBudgetPercentages: { A: 100 },
          roleBudgetFlexibilityPercent: 5,
        },
      },
    },
  );

  assert.ok(result.maxBid > 0);
  assert.ok(result.maxBid <= result.summary.marketValueCap);
  assert.ok(result.maxBid <= result.summary.roleBudgetCap);
  assert.ok(result.maxBid <= result.summary.estimatedMarketPrice * 1.25);
  assert.ok(result.idealMin < result.idealMax);
});

test("confidence is capped when no auction prices have been observed", () => {
  const candidate = player("A", 10);
  const alternative = player("A", 8);
  const teams = [team("Mine", 100, { P: 1, D: 1, C: 1, A: 1 })];

  const result = evaluateAuction(
    payloadFor({ candidate, teams, remaining: [candidate, alternative] }),
  );

  assert.ok(result.confidence <= 0.58);
});

test("legal max preserves one credit for every slot remaining after purchase", () => {
  const candidate = player("A", 10);
  const teams = [team("Mine", 100, { P: 1, D: 1, C: 1, A: 1 })];
  const remaining = [
    candidate,
    player("A"),
    player("P"),
    player("D"),
    player("C"),
  ];

  const result = evaluateAuction(payloadFor({ candidate, teams, remaining }));

  assert.equal(result.summary.slotsOpen, 4);
  assert.equal(result.summary.reservedCredits, 3);
  assert.equal(result.legalMax, 97);
  assert.ok(result.maxBid <= result.legalMax);
});

test("owner index changes the evaluated team summary and limits", () => {
  const candidate = player("C", 10);
  const teams = [
    team("Rich", 100, { C: 1 }),
    team("Constrained", 25, { P: 1, D: 1, C: 1, A: 1 }),
  ];
  const remaining = [
    candidate,
    player("C"),
    player("P"),
    player("D"),
    player("A"),
  ];

  const rich = evaluateAuction(
    payloadFor({ candidate, teams, owner: 0, remaining }),
  );
  const constrained = evaluateAuction(
    payloadFor({ candidate, teams, owner: 1, remaining }),
  );

  assert.equal(rich.summary.owner, 0);
  assert.equal(rich.summary.ownerName, "Rich");
  assert.equal(rich.legalMax, 100);
  assert.equal(constrained.summary.owner, 1);
  assert.equal(constrained.summary.ownerName, "Constrained");
  assert.equal(constrained.legalMax, 22);
});

test("overview uses the selected owner instead of the first team", () => {
  const teams = [
    team("Rich", 100, {}),
    team("Mine", 25, { P: 1, D: 1, C: 1, A: 1 }),
  ];
  const remaining = [player("P"), player("D"), player("C"), player("A")];

  const result = evaluateOverview({
    teams,
    owner: 1,
    mine: teams[1],
    remaining,
    assigned: {},
  });

  assert.equal(result.summary.owner, 1);
  assert.equal(result.summary.ownerName, "Mine");
  assert.equal(result.summary.credits, 25);
  assert.equal(result.summary.slotsOpen, 4);
  assert.equal(result.summary.reservedCredits, 4);
});

test("a full candidate role returns INELIGIBLE", () => {
  const candidate = player("A", 10);
  const teams = [team("Mine", 100, {})];

  const result = evaluateAuction(
    payloadFor({ candidate, teams, remaining: [candidate] }),
  );

  assert.equal(result.kind, "candidate");
  assert.equal(result.recommendation, "INELIGIBLE");
  assert.equal(result.maxBid, 0);
  assert.equal(result.legalMax, 0);
  assert.match(result.reasons[0], /Nessuno slot A/);
});

test("overview reserves one credit per open slot and uses configured role targets", () => {
  const teams = [team("Mine", 100, { P: 1, D: 1, C: 1, A: 1 })];
  const remaining = [player("P"), player("D"), player("C"), player("A")];

  const result = evaluateOverview({ teams, remaining, assigned: {} });

  assert.equal(result.kind, "overview");
  assert.equal(result.summary.credits, 100);
  assert.equal(result.summary.slotsOpen, 4);
  assert.equal(result.summary.reservedCredits, 4);
  assert.equal(result.summary.spendableCredits, 96);
  assert.deepEqual(
    Object.fromEntries(
      Object.entries(result.rolePlan).map(([role, plan]) => [
        role,
        plan.budgetTarget,
      ]),
    ),
    { P: 35, D: 90, C: 125, A: 250 },
  );
});

test("role budget is a soft cap on a candidate bid", () => {
  const candidate = player("A", 20, { fvm_scaled: 50 });
  const alternative = player("A", 10, { fvm_scaled: 10 });
  const teams = [team("Mine", 100, { P: 1, D: 1, C: 1, A: 1 })];
  const customRules = {
    startingCredits: 100,
    rosterSlots: LIMITS,
    auction: {
      roleBudgetPercentages: { P: 20, D: 30, C: 40, A: 10 },
      roleBudgetFlexibilityPercent: 5,
    },
  };

  const result = evaluateAuction({
    ...payloadFor({
      candidate,
      teams,
      remaining: [
        candidate,
        alternative,
        player("P", 5),
        player("D", 5),
        player("C", 5),
      ],
    }),
    rules: customRules,
  });

  assert.equal(result.summary.roleBudgetTarget, 10);
  assert.equal(result.summary.roleBudgetCap, 10);
  assert.ok(result.maxBid <= 10);
});

test("source FVM outliers are reported without blocking valuation", () => {
  const candidate = player("D", 20, {
    fvm_original: 100,
    fvm_scaled: 75,
  });
  const remaining = [
    candidate,
    ...Array.from({ length: 12 }, () =>
      player("D", 10, { fvm_original: 1, fvm_scaled: 0.75 }),
    ),
  ];
  const teams = [team("Mine", 100, { D: 1 })];

  const result = evaluateAuction(
    payloadFor({ candidate, teams, remaining }),
  );

  assert.equal(result.summary.sourceFvm, 100);
  assert.equal(result.summary.outliers[0].code, "source_fvm_high");
  assert.ok(result.maxBid > 0);
});

test("overview represents every role in plans and priorities", () => {
  const teams = [team("Mine", 80, { P: 1, D: 1, C: 1, A: 1 })];
  const remaining = [player("P"), player("D"), player("C"), player("A")];

  const result = evaluateOverview({ teams, remaining, assigned: {} });

  assert.deepEqual(
    Object.keys(result.rolePlan).sort(),
    Object.keys(LIMITS).sort(),
  );
  assert.deepEqual(
    result.priorities.map((item) => item.role).sort(),
    Object.keys(LIMITS).sort(),
  );
});

test("completed overview roles are not urgent", () => {
  const teams = [team("Mine", 50, { A: 1 })];
  const remaining = [player("A"), player("A")];

  const result = evaluateOverview({ teams, remaining, assigned: {} });
  const completed = result.priorities.filter((item) => item.role !== "A");

  assert.ok(completed.every((item) => item.urgency === "COMPLETO"));
  assert.equal(result.priorities.at(-1).urgency, "COMPLETO");
});

test("every answer carries back the id of the request that asked for it", () => {
  const teams = [team("Mine", 80, { A: 1 })];
  const candidate = player("A");

  const advice = evaluateRequest({
    ...payloadFor({ candidate, teams, remaining: [player("A")] }),
    requestId: 7,
  });
  const overview = evaluateRequest({
    mode: "overview",
    teams,
    remaining: [player("A")],
    assigned: {},
    requestId: 8,
  });

  assert.equal(advice.kind, "candidate");
  assert.equal(advice.requestId, 7);
  assert.equal(overview.kind, "overview");
  assert.equal(overview.requestId, 8);
});

test("a request without an id is answered with a null id, not a stale one", () => {
  const teams = [team("Mine", 80, { A: 1 })];
  const answer = evaluateRequest(
    payloadFor({ candidate: player("A"), teams, remaining: [player("A")] }),
  );
  assert.equal(answer.requestId, null);
});
