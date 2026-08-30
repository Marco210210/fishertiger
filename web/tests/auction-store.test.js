import test from "node:test";
import assert from "node:assert/strict";

const store = new Map();
let writesFail = false;
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => {
    if (writesFail) throw new Error("QuotaExceededError");
    store.set(key, String(value));
  },
  removeItem: (key) => store.delete(key),
};

const storageListeners = new Set();
globalThis.window = {
  addEventListener: (type, listener) => {
    if (type === "storage") storageListeners.add(listener);
  },
  removeEventListener: (type, listener) => {
    if (type === "storage") storageListeners.delete(listener);
  },
};
const emitStorageEvent = (key) =>
  [...storageListeners].forEach((listener) => listener({ key }));

const {
  AUCTION_LEGACY_STORAGE_KEY,
  assignPlayer,
  clearAuctionData,
  defaultUserTeamIndex,
  notifyAuctionChanged,
  playerAuctionStatus,
  readAuction,
  readAuctionBoard,
  readUserTeamIndex,
  redoAssignment,
  renameTeam,
  releasePlayer,
  resetAuction,
  setStartingCredits,
  subscribeAuctionChanges,
  undoAssignment,
  userTeamStorageKey,
  writeUserTeamIndex,
} = await import("../src/auction-store.js");
const { auctionStorageKey } = await import("../src/auction-state.js");

const rules = { participants: 3, teamNames: ["Mia", "Altra", "Terza"] };

test("the user team is read from an index or a team name", () => {
  assert.equal(defaultUserTeamIndex({ ...rules, userTeam: 2 }), 2);
  assert.equal(defaultUserTeamIndex({ ...rules, userTeam: "Altra" }), 1);
});

test("an out-of-range or missing user team falls back to the first squad", () => {
  assert.equal(defaultUserTeamIndex({ ...rules, userTeam: 9 }), 0);
  assert.equal(defaultUserTeamIndex({ ...rules, userTeam: "Assente" }), 0);
  assert.equal(defaultUserTeamIndex(rules), 0);
  assert.equal(defaultUserTeamIndex(undefined), 0);
});

test("the chosen team is stored per profile", () => {
  assert.equal(
    userTeamStorageKey("Fantabosco"),
    "fanta-auction-user-team:Fantabosco",
  );
  assert.equal(userTeamStorageKey(null), "fanta-auction-user-team:default");
});

test("a missing stored team keeps the configured one instead of the first squad", () => {
  store.clear();
  const configured = { ...rules, userTeam: 2 };
  assert.equal(readUserTeamIndex("no-storage", configured), 2);
  store.set(userTeamStorageKey("blank"), "");
  assert.equal(readUserTeamIndex("blank", configured), 2);
  store.set(userTeamStorageKey("junk"), "non-un-numero");
  assert.equal(readUserTeamIndex("junk", configured), 2);
});

test("a stored team is preferred over the configured one", () => {
  store.clear();
  writeUserTeamIndex("stored", 1);
  assert.equal(readUserTeamIndex("stored", { ...rules, userTeam: 2 }), 1);
});

test("storing the user team reports a write failure instead of throwing", () => {
  store.clear();
  writesFail = true;
  const result = writeUserTeamIndex("stored", 1);
  writesFail = false;
  assert.equal(result.ok, false);
  assert.match(result.message, /\S/);
});

const board = {
  teamNames: ["Mia", "Altra", "Terza"],
  assigned: { 7: { owner: 1, price: 24 }, 9: { owner: 0, price: 5 } },
  userTeamIndex: 0,
};

test("an assigned player reports its buyer and price", () => {
  assert.deepEqual(playerAuctionStatus(board, { id: 7 }), {
    owner: 1,
    price: 24,
    ownerName: "Altra",
    mine: false,
  });
  assert.equal(playerAuctionStatus(board, { id: 9 }).mine, true);
});

test("a free player, an unknown board and a missing player report nothing", () => {
  assert.equal(playerAuctionStatus(board, { id: 1 }), null);
  assert.equal(playerAuctionStatus(null, { id: 7 }), null);
  assert.equal(playerAuctionStatus(board, null), null);
});

test("a buyer without a stored name still gets a label", () => {
  const partial = { ...board, teamNames: [] };
  assert.equal(playerAuctionStatus(partial, { id: 7 }).ownerName, "Squadra 2");
});

test("subscribers are notified on same-tab writes and released on unsubscribe", () => {
  let calls = 0;
  const unsubscribe = subscribeAuctionChanges(() => (calls += 1));
  notifyAuctionChanged();
  assert.equal(calls, 1);
  unsubscribe();
  notifyAuctionChanged();
  assert.equal(calls, 1);
});

test("subscribers are notified when another tab rewrites the auction", () => {
  let calls = 0;
  const unsubscribe = subscribeAuctionChanges(() => (calls += 1));
  emitStorageEvent(auctionStorageKey("qualsiasi"));
  assert.equal(calls, 1);
  emitStorageEvent("fanta-player-notes-v1:altro");
  assert.equal(calls, 1, "unrelated keys are ignored");
  unsubscribe();
  emitStorageEvent(auctionStorageKey("qualsiasi"));
  assert.equal(calls, 1);
});

const liveRules = {
  participants: 2,
  teamNames: ["Mia", "Rivale"],
  userTeam: 0,
  startingCredits: 30,
  rosterSlots: { P: 1, A: 1 },
  auction: { minPrice: 2, increment: 2, reserve: 2, nomination: "call" },
};
const livePlayers = [
  { id: 1, nome: "Portiere", ruolo: "P" },
  { id: 2, nome: "Bomber", ruolo: "A" },
];
const PROFILE = "test-live";
const resetStore = () => {
  store.clear();
  store.set(
    auctionStorageKey(PROFILE),
    JSON.stringify({
      version: 2,
      teams: [
        { name: "Mia", startingCredits: 30 },
        { name: "Rivale", startingCredits: 30 },
      ],
      history: [],
      undone: [],
    }),
  );
};

test("a profile with no saved auction still gets an empty, usable board", () => {
  store.clear();
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.equal(board.taken, 0);
  assert.deepEqual(board.teamNames, ["Mia", "Rivale"]);
  assert.equal(board.teams[0].maxBid, 28);
});

test("assigning from the players page lands in the auction the other view reads", () => {
  resetStore();
  const result = assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 2,
    owner: 1,
    price: 12,
  });
  assert.equal(result.ok, true);
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.deepEqual(playerAuctionStatus(board, { id: 2 }), {
    owner: 1,
    price: 12,
    ownerName: "Rivale",
    mine: false,
  });
  assert.equal(board.teams[1].credits, 18);
});

test("an assignment the auction view would reject is refused and changes nothing", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 2,
    owner: 1,
    price: 12,
  });
  const before = store.get(auctionStorageKey(PROFILE));
  for (const [request, reason] of [
    [{ playerId: 2, owner: 0, price: 4 }, "already assigned"],
    [{ playerId: 1, owner: 0, price: 3 }, "off the bid increment"],
    [{ playerId: 1, owner: 0, price: 1 }, "below the minimum"],
    [{ playerId: 1, owner: 0, price: 40 }, "over the legal maximum"],
    [{ playerId: 1, owner: 9, price: 4 }, "unknown buyer"],
    [{ playerId: 99, owner: 0, price: 4 }, "unknown player"],
  ]) {
    const rejected = assignPlayer(PROFILE, livePlayers, liveRules, request);
    assert.equal(rejected.ok, false, reason);
    assert.match(rejected.message, /\S/);
  }
  assert.equal(store.get(auctionStorageKey(PROFILE)), before);
});

test("a by-role nomination phase blocks the roles that are not in auction yet", () => {
  resetStore();
  const byRole = {
    ...liveRules,
    auction: { ...liveRules.auction, nomination: "call_by_role" },
  };
  const early = assignPlayer(PROFILE, livePlayers, byRole, {
    playerId: 2,
    owner: 0,
    price: 4,
  });
  assert.equal(early.ok, false);
  assert.equal(
    assignPlayer(PROFILE, livePlayers, byRole, {
      playerId: 1,
      owner: 0,
      price: 4,
    }).ok,
    true,
  );
});

test("releasing a player gives the credits and the slot back", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 2,
    owner: 0,
    price: 12,
  });
  assert.equal(releasePlayer(PROFILE, livePlayers, liveRules, 2).ok, true);
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.equal(board.taken, 0);
  assert.equal(board.teams[0].credits, 30);
  assert.equal(releasePlayer(PROFILE, livePlayers, liveRules, 2).ok, false);
});

test("a write from this page notifies the mirrors", () => {
  resetStore();
  let calls = 0;
  const unsubscribe = subscribeAuctionChanges(() => (calls += 1));
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  assert.equal(calls, 1);
  unsubscribe();
});

test("a mutation applies to the newest persisted auction, not to a stale snapshot", () => {
  resetStore();
  const openView = readAuction(PROFILE, livePlayers, liveRules);
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  assert.equal(openView.history.length, 0, "the snapshot predates the write");
  assert.equal(
    assignPlayer(PROFILE, livePlayers, liveRules, {
      playerId: 2,
      owner: 0,
      price: 4,
    }).ok,
    true,
  );
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 2);
});

test("undo and redo move one assignment at a time through storage", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  assert.equal(undoAssignment(PROFILE, livePlayers, liveRules).ok, true);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 0);
  assert.equal(undoAssignment(PROFILE, livePlayers, liveRules).ok, false);
  assert.equal(redoAssignment(PROFILE, livePlayers, liveRules).ok, true);
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.equal(board.taken, 1);
  assert.equal(board.teams[0].credits, 26);
  assert.equal(redoAssignment(PROFILE, livePlayers, liveRules).ok, false);
});

test("an assignment clears the redo stack", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  undoAssignment(PROFILE, livePlayers, liveRules);
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 2,
    owner: 0,
    price: 4,
  });
  assert.equal(redoAssignment(PROFILE, livePlayers, liveRules).ok, false);
});

test("resetting clears the saved auction", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  assert.equal(resetAuction(PROFILE, livePlayers, liveRules).ok, true);
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.equal(board.taken, 0);
  assert.equal(board.teams[0].credits, 30);
});

test("starting credits can be set only while no player has been assigned", () => {
  resetStore();
  assert.equal(
    setStartingCredits(PROFILE, livePlayers, liveRules, 0, 50).ok,
    true,
  );
  assert.equal(
    readAuctionBoard(PROFILE, livePlayers, liveRules).teams[0].credits,
    50,
  );
  assert.equal(
    setStartingCredits(PROFILE, livePlayers, liveRules, 0, 10).ok,
    false,
    "below the floor",
  );
  assert.equal(
    setStartingCredits(PROFILE, livePlayers, liveRules, 9, 50).ok,
    false,
    "unknown team",
  );
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  assert.equal(
    setStartingCredits(PROFILE, livePlayers, liveRules, 0, 60).ok,
    false,
    "auction started",
  );
});

test("a browser that refuses to write reports it and keeps the saved auction", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  const before = store.get(auctionStorageKey(PROFILE));
  writesFail = true;
  const result = assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 2,
    owner: 0,
    price: 4,
  });
  writesFail = false;
  assert.equal(result.ok, false);
  assert.match(result.message, /memoria del browser/i);
  assert.equal(store.get(auctionStorageKey(PROFILE)), before);
});

test("the legacy single-profile auction is migrated to the profile-scoped key", () => {
  store.clear();
  store.set(
    AUCTION_LEGACY_STORAGE_KEY,
    JSON.stringify({
      teams: [
        { name: "Mia", credits: 26 },
        { name: "Rivale", credits: 30 },
      ],
      history: [{ playerId: 1, owner: 0, price: 4 }],
    }),
  );
  const board = readAuctionBoard("default", livePlayers, liveRules);
  assert.equal(board.taken, 1);
  assert.equal(board.teams[0].credits, 26);
  assert.equal(store.has(auctionStorageKey("default")), true, "migrated");
  assert.equal(store.has(AUCTION_LEGACY_STORAGE_KEY), false, "legacy retired");
});

test("the legacy auction is never adopted by another profile", () => {
  store.clear();
  store.set(
    AUCTION_LEGACY_STORAGE_KEY,
    JSON.stringify({
      teams: [
        { name: "Mia", credits: 26 },
        { name: "Rivale", credits: 30 },
      ],
      history: [{ playerId: 1, owner: 0, price: 4 }],
    }),
  );
  assert.equal(readAuctionBoard("altro", livePlayers, liveRules).taken, 0);
});

test("a team can be renamed at any point without touching the roster", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  assert.equal(renameTeam(PROFILE, livePlayers, liveRules, 0, "Nuovo").ok, true);
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.deepEqual(board.teamNames, ["Nuovo", "Rivale"]);
  assert.equal(board.taken, 1);
  assert.equal(board.teams[0].credits, 26);
});

test("a blank name or an unknown team is refused", () => {
  resetStore();
  assert.equal(renameTeam(PROFILE, livePlayers, liveRules, 0, "   ").ok, false);
  assert.equal(renameTeam(PROFILE, livePlayers, liveRules, 9, "Nuovo").ok, false);
  assert.deepEqual(
    readAuctionBoard(PROFILE, livePlayers, liveRules).teamNames,
    ["Mia", "Rivale"],
  );
});

test("clearing a profile removes its auction and its chosen team", () => {
  resetStore();
  writeUserTeamIndex(PROFILE, 1);
  clearAuctionData(PROFILE);
  assert.equal(store.has(auctionStorageKey(PROFILE)), false);
  assert.equal(store.has(userTeamStorageKey(PROFILE)), false);
});
