import { playerIdKey } from "./auction-state.js";

/**
 * Keeps a selection honest against what the list is currently showing.
 *
 * A player picked before the filters changed must not stay in the detail panel:
 * the panel drives the assignment, so a stale selection assigns the wrong man.
 * The dataset instance wins over the caller's copy, which may come from a
 * previous profile.
 */
export const reconcileSelectedPlayer = (selected, eligiblePlayers) => {
  if (!selected) return null;
  return (eligiblePlayers || []).find(
    (candidate) => playerIdKey(candidate.id) === playerIdKey(selected.id),
  ) || null;
};
