import test from "node:test";
import assert from "node:assert/strict";
import {
  ProfileClientError,
  loadDatasetUrl,
  normalizeDataset,
  rulesFor,
  auctionDatasetPath,
  loadAuctionState,
  saveAuctionState,
} from "./profile-client.js";
import { projectedContribution } from "./player-valuation.js";

const profile = {
  profile_id: "league-a",
  participants: { team_names: ["A", "B"], user_team: "A" },
  credits: { starting: 600 }, roster_slots: { P: 2, D: 7, C: 7, A: 5 },
  formations: { allowed: ["3-4-3"] }, bench_switch: { max_substitutions: 2, mode: "Basic" },
  scoring: { goalkeeper_conceded_goal: -1 }, virtual_goals: { threshold: 65, step: 4 },
  defense_modifier: { enabled: true, required_defenders: 4, tiers: [{ minimum_average: 6.5, bonus: 2 }] },
  standings: { win_points: 3, draw_points: 1, loss_points: 0, tie_breakers: ["goal_difference"], exact_tie_policy: "shared" },
  incomplete_lineup: { policy: "error" }, auction: { minimum_bid: 2, bid_increment: 2, reserve_credits_per_open_slot: 3, nomination_policy: "call" },
};

test("normalizes schema 1.0 metadata and rejects another profile", () => {
  const data = normalizeDataset({ schema_version: "1.0", meta: { profile: { profile_id: "league-a" } }, players: [] }, profile);
  assert.equal(data.legacy, false);
  assert.throws(() => normalizeDataset({ schema_version: "1.0", meta: { profile: { profile_id: "other" } }, players: [] }, profile), (error) => error instanceof ProfileClientError && error.code === "profile_mismatch");
});

test("validates optional goalkeeper hierarchy metadata", () => {
  const envelope = (players) => ({
    schema_version: "1.0",
    model_version: "1.6",
    meta: { profile: { profile_id: "league-a" } },
    players,
  });
  assert.equal(normalizeDataset(envelope([
    { id: 1, ruolo: "P", gerarchia_portiere: "PRIMO/SECONDO" },
    { id: 2, ruolo: "D", gerarchia_portiere: null },
  ]), profile).model_version, "1.6");
  assert.throws(
    () => normalizeDataset(envelope([{ id: 1, ruolo: "P", gerarchia_portiere: "PRIMO/TERZO" }]), profile),
    (error) => error instanceof ProfileClientError && error.code === "invalid_goalkeeper_hierarchy",
  );
  assert.throws(
    () => normalizeDataset(envelope([{ id: 1, ruolo: "D", gerarchia_portiere: "PRIMO" }]), profile),
    (error) => error instanceof ProfileClientError && error.code === "invalid_goalkeeper_hierarchy",
  );
});

test("accepts legacy payloads and resolves profile rules for league engines", () => {
  const legacy = normalizeDataset({ players: [], rules: { participants: 8 } }, profile);
  assert.equal(legacy.legacy, true);
  const rules = rulesFor(profile, legacy);
  assert.deepEqual(rules.formations, [[3, 4, 3]]);
  assert.equal(rules.participants, 2);
  assert.equal(rules.startingCredits, 600);
  assert.equal(rules.auction.reserve, 3);
  assert.equal(rules.defenseModifier.tiers[0].threshold, 6.5);
  assert.equal(rules.defenseModifier.enabled, true);
});

test("normalizes extra formation strings for browser engines", () => {
  const rules = rulesFor({ ...profile, formations: { allowed: ["2-1-7", [6, 3, 1]] } });
  assert.deepEqual(rules.formations, [[2, 1, 7], [6, 3, 1]]);
});

test("keeps historical and current league horizons separate", () => {
  const rules = rulesFor({
    ...profile,
    season: { serie_a_matchdays: 38, fantasy_start_matchday: 5, fantasy_end_matchday: 7 },
  });

  assert.equal(rules.horizons.historical.label, "storico 38");
  assert.deepEqual(rules.horizons.currentLeague.matchdayIndices, [4, 5, 6]);
  assert.equal(rules.horizons.currentLeague.label, "lega corrente 3");
  assert.equal(projectedContribution({ p_gioca_per_giornata: [1, 1, 1, 1, 1, 1, 1], voto_puro_mean_per_giornata: [1, 1, 1, 1, 2, 2, 2], bonus_atteso_per_giornata: [0, 0, 0, 0, 0, 0, 0] }, rules.horizons.currentLeague.matchdayIndices), 6);
});

test("loads and normalizes a dataset URL through an injected fetch", async () => {
  const data = await loadDatasetUrl("/dataset.json", { profile, fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ schema_version: "1.0", meta: { profile: { profile_id: "league-a" } }, players: [] }) }) });
  assert.equal(data.schema_version, "1.0");
  assert.equal(auctionDatasetPath({ profile_id: "league-a", season: { season: "2026/27" } }), "league-a/2026-27/auction_data.json");
});

test("loads and saves a profile-scoped shared auction revision", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: 200,
      json: async () => ({ profile_id: "league-a", revision: calls.length, state: null }),
    };
  };

  await loadAuctionState("league-a", { fetchImpl });
  await saveAuctionState(
    "league-a",
    { version: 2, teams: [], history: [], undone: [] },
    3,
    { fetchImpl },
  );

  assert.equal(calls[0].url, "/api/auction-state/league-a");
  assert.equal(calls[1].options.method, "PUT");
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    state: { version: 2, teams: [], history: [], undone: [] },
    base_revision: 3,
  });
});
