import { expectedDefenseModifier } from "./defense-modifier.js";
import { normalizeRules } from "./league-rules.js";

const finite = (value, fallback = 0) =>
  Number.isFinite(Number(value)) ? Number(value) : fallback;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const UPSIDE_WEIGHT = 0.1;
const HIERARCHY_RANK = { PRIMO: 0, SECONDO: 1, TERZO: 2 };
const playerKey = (player) =>
  player?.id == null
    ? `${player?.nome || ""}|${player?.ruolo || ""}|${player?.squadra || ""}`
    : String(player.id);
const clubKey = (player) => String(player?.team_id ?? player?.squadra ?? "unknown");

export const isConfirmedInactive = (player) =>
  player?.confirmed_inactive === true ||
  player?.disponibilita?.confirmed_inactive === true;

const availableDays = (players, rules) => {
  const configured = rules.horizons.currentLeague.matchdayIndices;
  if (configured.length) return configured;
  const length = players.reduce(
    (maximum, player) =>
      Math.max(maximum, player?.p_gioca_per_giornata?.length || 0),
    0,
  );
  return Array.from({ length: length || 38 }, (_, day) => day);
};

const dayProjection = (player, day) => {
  const projection = player?.proiezione || {};
  const probability = clamp(
    finite(player?.p_gioca_per_giornata?.[day], finite(projection.p_gioca)),
    0,
    1,
  );
  const vote = finite(
    player?.voto_puro_mean_per_giornata?.[day],
    finite(projection.voto_puro),
  );
  const bonus = finite(
    player?.bonus_atteso_per_giornata?.[day],
    finite(projection.bonus),
  );
  const deviation = Math.max(
    0,
    finite(player?.voto_puro_std_per_giornata?.[day], finite(projection.deviazione)),
  );
  return { probability, vote, score: vote + bonus, deviation };
};

const hierarchyRank = (player) => {
  const ranks = String(player?.gerarchia_portiere || "")
    .split("/")
    .map((rank) => HIERARCHY_RANK[rank])
    .filter(Number.isInteger);
  return ranks.length ? Math.min(...ranks) : 3;
};

const roleDayUtility = (players, count, benchLimit, day) => {
  const options = players
    .map((player) => ({ player, ...dayProjection(player, day) }))
    .sort(
      (left, right) =>
        right.probability * (right.score + UPSIDE_WEIGHT * right.deviation) -
          left.probability * (left.score + UPSIDE_WEIGHT * left.deviation) ||
        playerKey(left.player).localeCompare(playerKey(right.player)),
    );
  const starters = options.slice(0, count);
  const bench = options.slice(count, count + benchLimit);
  const starterUtility = starters.reduce(
    (sum, option) =>
      sum + option.probability * (option.score + UPSIDE_WEIGHT * option.deviation),
    0,
  );
  let missing = [1];
  starters.forEach((starter) => {
    const next = Array(missing.length + 1).fill(0);
    missing.forEach((probability, absent) => {
      next[absent] += probability * starter.probability;
      next[absent + 1] += probability * (1 - starter.probability);
    });
    missing = next;
  });
  let benchUtility = 0;
  let expectedSubstitutions = 0;
  let upside = starters.reduce(
    (sum, option) => sum + option.probability * option.deviation,
    0,
  );
  bench.forEach((option) => {
    const needed = missing.slice(1).reduce((sum, probability) => sum + probability, 0);
    const used = option.probability * needed;
    expectedSubstitutions += used;
    benchUtility += used * (option.score + UPSIDE_WEIGHT * option.deviation);
    upside += used * option.deviation;
    const next = Array(missing.length).fill(0);
    missing.forEach((probability, absent) => {
      next[absent] += probability * (1 - option.probability);
      next[Math.max(0, absent - 1)] += probability * option.probability;
    });
    missing = next;
  });
  return {
    utility: starterUtility + benchUtility,
    starterUtility,
    benchUtility,
    expectedSubstitutions,
    coverage: count - missing.reduce(
      (sum, probability, absent) => sum + absent * probability,
      0,
    ),
    starters,
    upside,
  };
};

const clubGoalkeeperOption = (players, day, key) => {
  const groups = new Map();
  players.forEach((player) => {
    const rank = hierarchyRank(player);
    if (!groups.has(rank)) groups.set(rank, []);
    groups.get(rank).push({ player, ...dayProjection(player, day) });
  });
  let remaining = 1;
  let probability = 0;
  let weightedScore = 0;
  let weightedVote = 0;
  let weightedDeviation = 0;
  [...groups.entries()].sort(([left], [right]) => left - right).forEach(([, group]) => {
    const rawProbability = group.reduce((sum, option) => sum + option.probability, 0);
    const used = Math.min(remaining, rawProbability);
    if (used <= 0 || rawProbability <= 0) return;
    weightedScore += used * group.reduce(
      (sum, option) => sum + option.probability * option.score,
      0,
    ) / rawProbability;
    weightedVote += used * group.reduce(
      (sum, option) => sum + option.probability * option.vote,
      0,
    ) / rawProbability;
    weightedDeviation += used * group.reduce(
      (sum, option) => sum + option.probability * option.deviation,
      0,
    ) / rawProbability;
    probability += used;
    remaining -= used;
  });
  return {
    key,
    probability,
    score: probability ? weightedScore / probability : 0,
    vote: probability ? weightedVote / probability : 0,
    deviation: probability ? weightedDeviation / probability : 0,
  };
};

const goalkeeperDayUtility = (players, day) => {
  const clubs = new Map();
  players.forEach((player) => {
    const key = clubKey(player);
    if (!clubs.has(key)) clubs.set(key, []);
    clubs.get(key).push(player);
  });
  const options = [...clubs.entries()]
    .map(([key, clubPlayers]) => clubGoalkeeperOption(clubPlayers, day, key))
    .sort(
      (left, right) =>
        right.score + UPSIDE_WEIGHT * right.deviation -
          (left.score + UPSIDE_WEIGHT * left.deviation) ||
        right.probability - left.probability ||
        left.key.localeCompare(right.key),
    );
  let unavailable = 1;
  let utility = 0;
  let upside = 0;
  options.forEach((option) => {
    const used = unavailable * option.probability;
    utility += used * (option.score + UPSIDE_WEIGHT * option.deviation);
    upside += used * option.deviation;
    unavailable *= 1 - option.probability;
  });
  const primary = options[0];
  return {
    utility,
    upside,
    coverage: 1 - unavailable,
    primaryClub: primary?.key ?? null,
    primary: primary
      ? { probability: primary.probability, vote: primary.vote }
      : null,
  };
};

const formationCounts = (formation) => ({
  D: Number(formation?.[0]) || 0,
  C: Number(formation?.[1]) || 0,
  A: Number(formation?.[2]) || 0,
});

const benchLimitFor = (rules, role) =>
  rules.bench.mode === "None"
    ? 0
    : rules.bench.roles.filter((item) => item === role).length;

export const createLineupUtility = (inputRules = {}) => {
  const rules = normalizeRules(inputRules);

  const evaluateRoster = (inputRoster = []) => {
    const roster = inputRoster.filter((player) => !isConfirmedInactive(player));
    const days = availableDays(roster, rules);
    let goalkeeperCoverage = 0;
    let totalUpside = 0;
    const daily = days.map((day) => {
      const goalkeeper = goalkeeperDayUtility(
        roster.filter((player) => player.ruolo === "P"),
        day,
      );
      goalkeeperCoverage += goalkeeper.coverage;
      const formations = rules.formations.length ? rules.formations : [[3, 4, 3]];
      const outfield = formations.reduce((best, formation) => {
        const counts = formationCounts(formation);
        const byRole = Object.fromEntries(
          ["D", "C", "A"].map((role) => [
            role,
            roleDayUtility(
              roster.filter((player) => player.ruolo === role),
              counts[role],
              benchLimitFor(rules, role),
              day,
            ),
          ]),
        );
        const expectedSubstitutions = ["D", "C", "A"].reduce(
          (sum, role) => sum + byRole[role].expectedSubstitutions,
          0,
        );
        const scale = expectedSubstitutions > rules.bench.maxSubstitutions
          ? rules.bench.maxSubstitutions / expectedSubstitutions
          : 1;
        const lineupUtility = ["D", "C", "A"].reduce(
          (sum, role) => sum + byRole[role].starterUtility + byRole[role].benchUtility * scale,
          0,
        );
        const defense = expectedDefenseModifier({
          ...rules.defenseModifier,
          goalkeeper: goalkeeper.primary,
          defenders: byRole.D.starters.map((option) => ({
            probability: option.probability,
            vote: option.vote,
          })),
        });
        const result = {
          utility: lineupUtility + defense,
          coverage: ["D", "C", "A"].reduce(
            (sum, role) => sum + byRole[role].coverage,
            0,
          ),
          upside: ["D", "C", "A"].reduce(
            (sum, role) => sum + byRole[role].upside,
            0,
          ),
          defense,
        };
        return result.utility > best.utility ? result : best;
      }, { utility: 0, coverage: 0, upside: 0, defense: 0 });
      totalUpside += goalkeeper.upside + outfield.upside;
      return {
        day,
        utility: goalkeeper.utility + outfield.utility,
        goalkeeperCoverage: goalkeeper.coverage,
        outfieldCoverage: outfield.coverage,
        primaryGoalkeeperClub: goalkeeper.primaryClub,
        defenseModifier: outfield.defense,
      };
    });
    return {
      utility: daily.reduce((sum, item) => sum + item.utility, 0),
      defenseModifier: daily.reduce(
        (sum, item) => sum + item.defenseModifier,
        0,
      ),
      matchdays: days.length,
      upside: totalUpside,
      daily,
      goalkeeper: {
        voteCoverage: days.length ? goalkeeperCoverage / days.length : 0,
      },
    };
  };

  const evaluateCandidate = (player, roster = []) => {
    const baseline = evaluateRoster(roster);
    if (isConfirmedInactive(player)) {
      return {
        eligible: false,
        purpose: "INACTIVE",
        marginalUtility: 0,
        baselineUtility: baseline.utility,
        utility: baseline.utility,
        upsideGain: 0,
        defenseGain: 0,
        baselineDefenseModifier: baseline.defenseModifier,
        goalkeeper: {
          voteCoverageGain: 0,
          sameClub: false,
          rotationStarts: 0,
          homeRotationStarts: 0,
        },
      };
    }
    const withCandidate = evaluateRoster([...roster, player]);
    const standalone = evaluateRoster([player]);
    const marginalUtility = withCandidate.utility - baseline.utility;
    const sameClub =
      player?.ruolo === "P" &&
      roster.some(
        (owned) => owned.ruolo === "P" && clubKey(owned) === clubKey(player),
      );
    const voteCoverageGain =
      withCandidate.goalkeeper.voteCoverage - baseline.goalkeeper.voteCoverage;
    const candidateClub = clubKey(player);
    const rotationDays = player?.ruolo === "P"
      ? withCandidate.daily.filter(
        (item, index) =>
          item.primaryGoalkeeperClub === candidateClub &&
          baseline.daily[index]?.primaryGoalkeeperClub !== candidateClub,
      )
      : [];
    const homeRotationStarts = rotationDays.filter(
      (item) => player?.venue_per_giornata?.[item.day] === "CASA",
    ).length;
    const purpose =
      marginalUtility <= 1e-9
        ? "DEPTH"
        : player?.ruolo === "P" && !roster.some((owned) => owned.ruolo === "P")
          ? "STARTER"
        : player?.ruolo === "P" && sameClub && voteCoverageGain > 1e-9
          ? "HANDCUFF"
          : player?.ruolo === "P" && rotationDays.length > 0
            ? "ROTATION"
            : roster.some((owned) => owned.ruolo === player?.ruolo)
              ? "COVERAGE"
              : "STARTER";
    return {
      eligible: true,
      purpose,
      marginalUtility,
      baselineUtility: baseline.utility,
      utility: withCandidate.utility,
      upsideGain: withCandidate.upside - baseline.upside,
      depthUtility: standalone.utility * 0.01,
      defenseGain:
        withCandidate.defenseModifier - baseline.defenseModifier,
      baselineDefenseModifier: baseline.defenseModifier,
      goalkeeper: {
        voteCoverageGain,
        sameClub,
        rotationStarts: rotationDays.length,
        homeRotationStarts,
      },
    };
  };

  return { evaluateRoster, evaluateCandidate };
};
