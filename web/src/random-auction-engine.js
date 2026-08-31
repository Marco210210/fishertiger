import { normalizeRules } from "./league-rules.js";
import { ROLE_ORDER, isRoleNomination } from "./auction-nomination.js";
import { createLineupUtility, isConfirmedInactive } from "./lineup-utility.js";
import { projectedContribution } from "./player-valuation.js";

const hashSeed = (seed) => {
  const text = String(seed ?? 1);
  let hash = 2166136261;
  for (let index = 0; index < text.length; index++) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
};

const seededRandom = (seed) => {
  let state = hashSeed(seed);
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value ^= value + Math.imul(value ^ (value >>> 7), 61 | value);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
};

const creditsForTeams = (startingCredits, teamCount, rosterSize) => {
  const supplied = startingCredits;
  const credits = Array.isArray(supplied)
    ? supplied.slice()
    : Array(teamCount).fill(supplied);

  if (
    credits.length !== teamCount ||
    credits.some((credit) => !Number.isInteger(credit) || credit < rosterSize)
  ) {
    throw new RangeError(
      `startingCredits must be an integer of at least ${rosterSize}, or an array of ${teamCount} such integers`,
    );
  }
  return credits;
};

const shuffled = (items, random) => {
  const result = items.slice();
  for (let index = result.length - 1; index > 0; index--) {
    const selected = Math.floor(random() * (index + 1));
    [result[index], result[selected]] = [result[selected], result[index]];
  }
  return result;
};

const ARCHETYPES = ["value", "aggressive", "stars_and_scrubs", "need_driven"];

const runRandomAuction = (players, options = {}) => {
  if (!Array.isArray(players)) throw new TypeError("players must be an array");
  const rules = normalizeRules(options.rules);
  const slots = rules.rosterSlots;
  const roles = ROLE_ORDER.filter((role) => slots[role] != null);
  const teamCount = rules.participants;
  const rosterSize = Object.values(slots).reduce((sum, count) => sum + count, 0);
  const { minPrice, increment, reserve, nomination } = rules.auction;

  const pools = Object.fromEntries(roles.map((role) => [role, []]));
  const seenIds = new Set();
  for (const player of players) {
    if (!roles.includes(player?.ruolo) || isConfirmedInactive(player)) continue;
    if (player.id == null)
      throw new TypeError("every eligible player needs an id");
    const key = `${typeof player.id}:${String(player.id)}`;
    if (seenIds.has(key))
      throw new RangeError(`duplicate player id: ${player.id}`);
    seenIds.add(key);
    pools[player.ruolo].push(player);
  }

  for (const role of roles) {
    const required = teamCount * slots[role];
    if (pools[role].length < required) {
      throw new RangeError(
        `not enough ${role} players: ${pools[role].length} supplied, ${required} required`,
      );
    }
  }

  const random = seededRandom(options.seed);
  const initialCredits = creditsForTeams(options.startingCredits ?? rules.startingCredits, teamCount, rosterSize);
  const credits = initialCredits.slice();
  const needs = Array.from({ length: teamCount }, () => ({ ...slots }));
  const rosters = Array.from({ length: teamCount }, () => []);
  const events = [];
  const sales = [];
  const utility = createLineupUtility(rules);
  const gainCaches = Array.from({ length: teamCount }, () => new Map());
  const projectedValues = new Map(
    players.map((player) => [
      `${typeof player.id}:${String(player.id)}`,
      projectedContribution(
        player,
        rules.horizons.currentLeague.matchdayIndices,
      ),
    ]),
  );
  const outfieldCapacity = (role) => {
    const formationIndex = { D: 0, C: 1, A: 2 }[role];
    const starters = formationIndex == null
      ? 0
      : Math.max(...rules.formations.map((formation) => Number(formation[formationIndex]) || 0));
    const bench = rules.bench.mode === "None"
      ? 0
      : rules.bench.roles.filter((item) => item === role).length;
    return starters + bench;
  };
  const contextualGain = (player, owner) => {
    const key = `${typeof player.id}:${String(player.id)}`;
    if (gainCaches[owner].has(key)) return gainCaches[owner].get(key);
    const hasProjection =
      Array.isArray(player?.p_gioca_per_giornata) || player?.proiezione?.p_gioca != null;
    const gain = hasProjection
      ? player.ruolo === "P"
        ? (() => {
          const evaluation = utility.evaluateCandidate(player, rosters[owner]);
          return Math.max(evaluation.marginalUtility, evaluation.depthUtility);
        })()
        : (() => {
          const value = projectedValues.get(key) || 0;
          const ownedValues = rosters[owner]
            .filter((owned) => owned.ruolo === player.ruolo)
            .map((owned) => projectedValues.get(`${typeof owned.id}:${String(owned.id)}`) || 0)
            .sort((left, right) => right - left);
          const capacity = outfieldCapacity(player.ruolo);
          if (ownedValues.length < capacity) return value;
          return Math.max(0, value - (ownedValues[capacity - 1] || 0), value * 0.01);
        })()
      : Math.max(1, Number(player?.fvm_scaled) || 1);
    gainCaches[owner].set(key, gain);
    return gain;
  };
  const teamArchetypes = Array.from(
    { length: teamCount },
    (_, index) => ARCHETYPES[index % ARCHETYPES.length],
  );
  const playerOrder = (left, right) =>
    String(left.nome_norm ?? left.nome ?? "").localeCompare(
      String(right.nome_norm ?? right.nome ?? ""),
      "it",
      { sensitivity: "base" },
    ) || String(left.id).localeCompare(String(right.id), "it", { numeric: true });

  const requiredSales = teamCount * rosterSize;
  let callNumber = 0;
  let deck = [];
  let deckRole = null;
  let salesInSweep = 0;
  let completedSweep = false;
  let forceCompletion = false;
  while (sales.length < requiredSales) {
    const roleMode = isRoleNomination(nomination);
    const activeRole = roleMode
      ? roles.find((role) => needs.some((team) => team[role] > 0))
      : null;
    const eligible = (activeRole ? pools[activeRole] : roles.flatMap((role) => pools[role]))
      .filter((player) => needs.some((team) => team[player.ruolo] > 0));
    if (!eligible.length) throw new RangeError("auction cannot complete from remaining supply");
    if (!deck.length || deckRole !== activeRole) {
      forceCompletion = completedSweep && salesInSweep === 0;
      completedSweep = true;
      salesInSweep = 0;
      deckRole = activeRole;
      if (forceCompletion) {
        deck = eligible.slice().sort((left, right) => {
          const bestGain = (candidate) => Math.max(
            ...rosters.map((roster, owner) =>
              needs[owner][candidate.ruolo]
                ? contextualGain(candidate, owner)
                : -Infinity,
            ),
          );
          return bestGain(right) - bestGain(left) || playerOrder(left, right);
        });
        const best = deck[0];
        const bestGain = best
          ? Math.max(...rosters.map((_, owner) =>
            needs[owner][best.ruolo] ? contextualGain(best, owner) : -Infinity,
          ))
          : 0;
        if (!(bestGain > 0)) {
          throw new RangeError(
            "auction cannot complete with positive-utility players from the remaining supply",
          );
        }
      } else if (["alphabetical", "alphabetical_by_role"].includes(nomination)) {
        deck = eligible.slice().sort(playerOrder);
      } else {
        deck = shuffled(eligible, random);
      }
    }
    const player = deck.shift();
    if (!pools[player.ruolo].includes(player) || !needs.some((team) => team[player.ruolo] > 0)) continue;
    const role = player.ruolo;
    const nominator = nomination === "random" || nomination === "random_by_role"
      ? Math.floor(random() * teamCount)
      : callNumber % teamCount;
    callNumber++;
    const fvm = Math.max(1, Number(player.fvm_scaled) || 1);
    const roleDemand = needs.reduce(
      (sum, current) => sum + current[role],
      0,
    );
    const replacementMarketValues = pools[role]
      .filter((item) => item !== player)
      .map((item) => projectedValues.get(`${typeof item.id}:${String(item.id)}`) || 0)
      .sort((left, right) => right - left);
    const replacementGain = replacementMarketValues[
      Math.min(
        replacementMarketValues.length - 1,
        Math.max(0, roleDemand - 1),
      )
    ] || 0;

    const bids = needs
      .map((teamNeeds, owner) => {
        if (!teamNeeds[role]) return null;
        const slotsOpen = roles.reduce(
          (sum, candidateRole) => sum + teamNeeds[candidateRole],
          0,
        );
        const legalMax = credits[owner] - Math.max(minPrice, reserve) * (slotsOpen - 1);
        if (legalMax < minPrice) return null;
        const candidateGain = contextualGain(player, owner);
        const archetype = teamArchetypes[owner];
        if (candidateGain <= 0) return null;
        const threshold = archetype === "aggressive"
          ? 0.85
          : archetype === "value"
            ? 1.1
            : archetype === "stars_and_scrubs" && fvm < 10
              ? 1.15
              : 0.95;
        if (!forceCompletion && candidateGain < replacementGain * threshold) return null;
        const needPressure = 0.9 + 0.2 * (teamNeeds[role] / slots[role]);
        const budgetScale = initialCredits[owner] / Number(rules.startingCredits || 500);
        const behavior = archetype === "aggressive"
          ? 1.1
          : archetype === "value"
            ? 0.9
            : archetype === "stars_and_scrubs"
              ? fvm >= 10 ? 1.15 : 0.8
              : 1 + 0.1 * (teamNeeds[role] / slots[role]);
        const estimate = Math.round(
          fvm * budgetScale * needPressure * behavior * (0.78 + random() * 0.44),
        );
        if (!forceCompletion && estimate < minPrice) return null;
        const capped = Math.min(legalMax, Math.max(minPrice, estimate));
        return {
          owner,
          maximum: minPrice + Math.floor((capped - minPrice) / increment) * increment,
          tieBreaker: random(),
        };
      })
      .filter(Boolean)
      .sort(
        (left, right) =>
          right.maximum - left.maximum || right.tieBreaker - left.tieBreaker,
      );

    const winner = bids[0];
    if (!winner) {
      events.push({ type: "unsold", playerId: player.id, nominator, callNumber });
      continue;
    }
    const runnerUp = bids[1]?.maximum ?? 0;
    const nextBid = Math.max(minPrice, runnerUp + increment);
    const price = Math.min(winner.maximum, nextBid);
    credits[winner.owner] -= price;
    needs[winner.owner][role]--;
    rosters[winner.owner].push(player);
    gainCaches[winner.owner].clear();
    pools[role].splice(pools[role].indexOf(player), 1);
    salesInSweep++;
    const event = {
      type: "sale",
      playerId: player.id,
      owner: winner.owner,
      price,
      nominator,
      callNumber,
      saleNumber: sales.length + 1,
    };
    events.push(event);
    sales.push({
      playerId: event.playerId,
      owner: event.owner,
      price: event.price,
      nominator: event.nominator,
      callNumber: sales.length + 1,
    });
  }

  return {
    events,
    sales,
    remainingPlayerIds: roles.flatMap((role) => pools[role].map((player) => player.id)),
    teamArchetypes,
  };
};

/** Sale-only compatibility API used by the existing auction and league replay. */
export const generateRandomAuction = (players, options = {}) =>
  runRandomAuction(players, options).sales;

export const generateRandomAuctionReplay = (players, options = {}) =>
  runRandomAuction(players, options);
