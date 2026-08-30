import { useEffect, useState } from "react";
import { readAuctionBoard, subscribeAuctionChanges } from "./auction-store.js";

export const useAuctionBoard = (profileId, players, rules, enabled = true) => {
  const rulesSignature = JSON.stringify(rules);
  const [board, setBoard] = useState(() =>
    enabled ? readAuctionBoard(profileId, players, rules) : null,
  );

  useEffect(() => {
    const refresh = () =>
      setBoard(enabled ? readAuctionBoard(profileId, players, rules) : null);
    refresh();
    return enabled ? subscribeAuctionChanges(refresh) : undefined;
  }, [enabled, profileId, players, rulesSignature]);

  return board;
};
