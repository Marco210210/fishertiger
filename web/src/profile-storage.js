import { clearAuctionData } from "./auction-store.js";
import { playerNotesStorageKey } from "./player-notes.js";
import { playerFiltersStorageKey } from "./player-filters.js";


export const clearProfileBrowserData = (profileId) => {
  const id = String(profileId ?? "").trim();
  if (!id) return;
  clearAuctionData(id);
  for (const key of [playerNotesStorageKey(id), playerFiltersStorageKey(id)]) {
    try {
      localStorage.removeItem(key);
    } catch {
    }
  }
};
