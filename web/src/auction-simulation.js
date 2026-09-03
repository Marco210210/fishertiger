import { teamsFromLeagueCalendar } from "./league-calendar-teams.js";

const playerIds = (team) => (Array.isArray(team?.roster) ? team.roster.map((player) => player?.id) : []);

export const auctionSimulationInput = (board, calendar, rules) => {
  if (!board?.storageReadOk)
    return { complete: false, reason: "Impossibile leggere l'asta salvata nel browser.", rosters: null, aliases: {} };
  if (board.auctionStatus === "incompatible")
    return { complete: false, reason: "L'asta salvata non è compatibile con il dataset o le regole correnti.", rosters: null, aliases: {} };
  const teams = teamsFromLeagueCalendar(calendar);
  if (teams.length !== rules?.participants || new Set(teams).size !== teams.length)
    return { complete: false, reason: "Il calendario della lega non è compatibile con le squadre configurate.", rosters: null, aliases: {} };
  if (!Array.isArray(board.teams) || board.teams.length !== teams.length)
    return { complete: false, reason: "L'asta non contiene tutte le squadre della lega.", rosters: null, aliases: {} };

  const rosters = {};
  const aliases = {};
  const assigned = new Set();
  const expectedSize = Object.values(rules.rosterSlots || {}).reduce((sum, count) => sum + count, 0);
  for (const [index, teamName] of teams.entries()) {
    const team = board.teams[index];
    const roster = team?.roster;
    if (!Array.isArray(roster) || roster.length !== expectedSize)
      return { complete: false, reason: "Completa tutte le rose prima di simulare l'asta reale.", rosters: null, aliases: {} };
    const ids = playerIds(team);
    if (ids.some((id) => !Number.isInteger(id)) || new Set(ids).size !== ids.length || ids.some((id) => assigned.has(id)))
      return { complete: false, reason: "L'asta contiene assegnazioni giocatore non valide.", rosters: null, aliases: {} };
    for (const role of Object.keys(rules.rosterSlots || {})) {
      if (roster.filter((player) => player?.ruolo === role).length !== rules.rosterSlots[role])
        return { complete: false, reason: `La rosa di ${teamName} non rispetta i posti per ruolo.`, rosters: null, aliases: {} };
    }
    ids.forEach((id) => assigned.add(id));
    rosters[teamName] = ids.sort((a, b) => a - b);
    aliases[teamName] = team.name || teamName;
  }
  return { complete: true, reason: "", rosters, aliases };
};

export const sameAuctionRosters = (first, second) =>
  JSON.stringify(Object.entries(first || {}).map(([team, players]) => [team, [...players].sort((a, b) => a - b)]).sort(([a], [b]) => a.localeCompare(b))) ===
  JSON.stringify(Object.entries(second || {}).map(([team, players]) => [team, [...players].sort((a, b) => a - b)]).sort(([a], [b]) => a.localeCompare(b)));
