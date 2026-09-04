import { useEffect, useState } from "react";
import { readAuctionBoard, subscribeAuctionChanges } from "./auction-store.js";
import { subscribeAuctionSync } from "./auction-sync.js";

export const useAuctionBoard = (profileId, players, rules, enabled = true) => {
  const rulesSignature = JSON.stringify(rules);
  const [board, setBoard] = useState(() =>
    enabled ? readAuctionBoard(profileId, players, rules) : null,
  );
  const [syncStatus, setSyncStatus] = useState({
    state: enabled ? "loading" : "disabled",
    detail: enabled ? "Collegamento al salvataggio condiviso…" : "",
  });

  useEffect(() => {
    const refresh = () =>
      setBoard(enabled ? readAuctionBoard(profileId, players, rules) : null);
    refresh();
    return enabled ? subscribeAuctionChanges(refresh) : undefined;
  }, [enabled, profileId, players, rulesSignature]);

  useEffect(() => {
    if (!enabled) {
      setSyncStatus({ state: "disabled", detail: "" });
      return undefined;
    }
    return subscribeAuctionSync(profileId, players, rules, setSyncStatus);
  }, [enabled, profileId, players, rulesSignature]);

  return board ? { ...board, syncStatus } : board;
};
