import { teamsFromLeagueCalendar } from "./league-calendar-teams.js";

const playerIds = (team) => (Array.isArray(team?.roster) ? team.roster.map((player) => player?.id) : []);

const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);

const rosterReport = (board, rules) => {
  const expectedSize = Object.values(rules?.rosterSlots || {}).reduce(
    (sum, count) => sum + Number(count || 0),
    0,
  );
  return (board?.teams || []).map((team, index) => {
    const roster = Array.isArray(team?.roster) ? team.roster : [];
    const roles = Object.fromEntries(
      Object.entries(rules?.rosterSlots || {}).map(([role, expected]) => {
        const current = roster.filter((player) => player?.ruolo === role).length;
        return [role, { current, expected: Number(expected), missing: Math.max(0, Number(expected) - current) }];
      }),
    );
    const startingCredits = number(team?.startingCredits ?? rules?.startingCredits);
    const credits = number(team?.credits);
    return {
      index,
      name: team?.name || `Squadra ${index + 1}`,
      rosterSize: roster.length,
      expectedSize,
      credits,
      spent: Math.max(0, startingCredits - credits),
      projectedValue: Math.round(roster.reduce((sum, player) => sum + number(player?.fvm_scaled), 0)),
      roles,
      complete: roster.length === expectedSize && Object.values(roles).every((role) => role.current === role.expected),
    };
  });
};

const incompleteReason = (report) => {
  const incomplete = report.filter((team) => !team.complete);
  if (!incomplete.length) return "";
  const details = incomplete.slice(0, 4).map((team) => {
    const missing = Object.entries(team.roles)
      .filter(([, role]) => role.missing)
      .map(([role, value]) => `${value.missing} ${role}`)
      .join(", ");
    return `${team.name} (${missing || `${team.rosterSize}/${team.expectedSize}`})`;
  });
  if (incomplete.length > details.length)
    details.push(`altre ${incomplete.length - details.length} rose`);
  return `Mancano giocatori in ${incomplete.length} rose: ${details.join("; ")}.`;
};

export const auctionSimulationInput = (board, calendar, rules) => {
  const report = rosterReport(board, rules);
  const expectedAssignments = report.reduce((sum, team) => sum + team.expectedSize, 0);
  const assignedCount = report.reduce((sum, team) => sum + team.rosterSize, 0);
  const context = { report, assigned: assignedCount, expectedAssignments };
  if (!board?.storageReadOk)
    return { ...context, complete: false, reason: "Impossibile leggere lo stato condiviso dell'asta.", rosters: null, aliases: {} };
  if (board.auctionStatus === "incompatible")
    return { ...context, complete: false, reason: "L'asta salvata non è compatibile con il dataset o le regole correnti.", rosters: null, aliases: {} };
  const teams = teamsFromLeagueCalendar(calendar);
  if (teams.length !== rules?.participants || new Set(teams).size !== teams.length)
    return { ...context, complete: false, reason: "Carica un calendario compatibile per calcolare la previsione della stagione.", rosters: null, aliases: {} };
  if (!Array.isArray(board.teams) || board.teams.length !== teams.length)
    return { ...context, complete: false, reason: "L'asta non contiene tutte le squadre della lega.", rosters: null, aliases: {} };

  const rosters = {};
  const aliases = {};
  const assigned = new Set();
  const expectedSize = Object.values(rules.rosterSlots || {}).reduce((sum, count) => sum + count, 0);
  for (const [index, teamName] of teams.entries()) {
    const team = board.teams[index];
    const roster = team?.roster;
    if (!Array.isArray(roster) || roster.length !== expectedSize)
      return { ...context, complete: false, reason: incompleteReason(report), rosters: null, aliases: {} };
    const ids = playerIds(team);
    if (ids.some((id) => !Number.isInteger(id)) || new Set(ids).size !== ids.length || ids.some((id) => assigned.has(id)))
      return { ...context, complete: false, reason: "L'asta contiene assegnazioni giocatore non valide.", rosters: null, aliases: {} };
    for (const role of Object.keys(rules.rosterSlots || {})) {
      if (roster.filter((player) => player?.ruolo === role).length !== rules.rosterSlots[role])
        return { ...context, complete: false, reason: `La rosa di ${team.name || teamName} non rispetta i posti per ruolo.`, rosters: null, aliases: {} };
    }
    ids.forEach((id) => assigned.add(id));
    rosters[teamName] = ids.sort((a, b) => a - b);
    aliases[teamName] = team.name || teamName;
  }
  return { ...context, complete: true, reason: "", rosters, aliases };
};

export const sameAuctionRosters = (first, second) =>
  JSON.stringify(Object.entries(first || {}).map(([team, players]) => [team, [...players].sort((a, b) => a - b)]).sort(([a], [b]) => a.localeCompare(b))) ===
  JSON.stringify(Object.entries(second || {}).map(([team, players]) => [team, [...players].sort((a, b) => a - b)]).sort(([a], [b]) => a.localeCompare(b)));
