import test from "node:test";
import assert from "node:assert/strict";

const store = new Map();
let readsFail = false;
let writesFail = false;
globalThis.localStorage = {
  getItem: (key) => {
    if (readsFail) throw new Error("SecurityError");
    return store.has(key) ? store.get(key) : null;
  },
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
  readAuctionPayload,
  readFantalabUnsold,
  readUserTeamIndex,
  redoAssignment,
  renameTeam,
  replaceAuctionPayload,
  releasePlayer,
  resetAuction,
  setStartingCredits,
  subscribeAuctionChanges,
  syncLiveAssignments,
  undoAssignment,
  userTeamStorageKey,
  writeUserTeamIndex,
} = await import("../src/auction-store.js");
const { auctionStorageKey, reconcileAuctionDraft } = await import("../src/auction-state.js");

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
  assert.equal(board.storageReadOk, true);
});

test("a genuinely missing auction can be created", () => {
  store.clear();
  assert.equal(
    assignPlayer(PROFILE, livePlayers, liveRules, {
      playerId: 1,
      owner: 0,
      price: 4,
    }).ok,
    true,
  );
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 1);
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

test("a browser read failure blocks every auction mutation without overwriting", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  const before = store.get(auctionStorageKey(PROFILE));
  let notifications = 0;
  const unsubscribe = subscribeAuctionChanges(() => (notifications += 1));
  readsFail = true;
  try {
    const operations = [
      () => assignPlayer(PROFILE, livePlayers, liveRules, { playerId: 2, owner: 0, price: 4 }),
      () => releasePlayer(PROFILE, livePlayers, liveRules, 1),
      () => undoAssignment(PROFILE, livePlayers, liveRules),
      () => redoAssignment(PROFILE, livePlayers, liveRules),
      () => resetAuction(PROFILE, livePlayers, liveRules),
      () => setStartingCredits(PROFILE, livePlayers, liveRules, 0, 50),
      () => renameTeam(PROFILE, livePlayers, liveRules, 0, "Nuovo"),
    ];
    for (const operation of operations) {
      const result = operation();
      assert.equal(result.ok, false);
      assert.match(result.message, /leggere l'asta salvata/i);
      assert.equal(store.get(auctionStorageKey(PROFILE)), before);
    }
    assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).storageReadOk, false);
    assert.equal(notifications, 0);
  } finally {
    readsFail = false;
    unsubscribe();
  }
});

test("auction mutations recover by re-reading the preserved snapshot", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 1,
    owner: 0,
    price: 4,
  });
  readsFail = true;
  try {
    assert.equal(
      assignPlayer(PROFILE, livePlayers, liveRules, {
        playerId: 2,
        owner: 0,
        price: 4,
      }).ok,
      false,
    );
  } finally {
    readsFail = false;
  }
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

test("a cross-tab assignment invalidates an open nomination", () => {
  resetStore();
  let draft = { playerId: 2, query: "Bomber", price: "12" };
  const unsubscribe = subscribeAuctionChanges(() => {
    draft = reconcileAuctionDraft(
      draft,
      livePlayers,
      readAuctionBoard(PROFILE, livePlayers, liveRules),
    );
  });
  store.set(
    auctionStorageKey(PROFILE),
    JSON.stringify({
      version: 2,
      teams: [
        { name: "Mia", startingCredits: 30 },
        { name: "Rivale", startingCredits: 30 },
      ],
      history: [{ playerId: 2, owner: 1, price: 12 }],
      undone: [],
    }),
  );
  emitStorageEvent(auctionStorageKey(PROFILE));
  unsubscribe();
  assert.deepEqual(draft, { playerId: null, query: "", price: "" });
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

test("a server snapshot can be copied to another profile without mixing their storage", () => {
  resetStore();
  assignPlayer(PROFILE, livePlayers, liveRules, {
    playerId: 2,
    owner: 1,
    price: 12,
  });
  const payload = readAuctionPayload(PROFILE, livePlayers, liveRules);
  assert.equal(
    replaceAuctionPayload("second-league", livePlayers, liveRules, payload).ok,
    true,
  );
  assert.equal(readAuctionBoard("second-league", livePlayers, liveRules).taken, 1);

  resetAuction("second-league", livePlayers, liveRules);
  assert.equal(readAuctionBoard("second-league", livePlayers, liveRules).taken, 0);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 1);
});

test("FantaLab ledger imports are idempotent and never overwrite a conflict", () => {
  resetStore();
  const purchases = [
    { purchase_id: "one", player_id: 1, buyer_team_id: "ext-a", price: 4 },
    { purchase_id: "two", player_id: 2, buyer_team_id: "unknown", price: 3 },
    { purchase_id: "skip", player_id: 2, buyer_team_id: null, price: 0, unsold: true },
  ];

  const first = syncLiveAssignments(PROFILE, livePlayers, liveRules, purchases, { "ext-a": 0 });
  const second = syncLiveAssignments(PROFILE, livePlayers, liveRules, purchases, { "ext-a": 0 });

  assert.equal(first.synced, 1);
  assert.equal(first.pending, 1);
  assert.equal(second.synced, 0);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 1);

  // FantaLab's ledger always reflects every purchase, including this
  // corrected one (same purchase_id, different team/price) — a real caller
  // never sends a partial list, only ever the full current ledger.
  const conflict = syncLiveAssignments(
    PROFILE,
    livePlayers,
    liveRules,
    [{ purchase_id: "one", player_id: 1, buyer_team_id: "ext-b", price: 9 }, purchases[1], purchases[2]],
    { "ext-b": 1 },
  );
  assert.equal(conflict.conflicts, 1);
  assert.equal(conflict.retracted, 0);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).assigned["1"].owner, 0);
});

test("a purchase deleted from FantaLab's ledger is retracted from the local roster", () => {
  resetStore();
  const purchases = [
    { purchase_id: "one", player_id: 1, buyer_team_id: "ext-a", price: 4 },
    { purchase_id: "two", player_id: 2, buyer_team_id: "ext-a", price: 6 },
  ];
  const teamMap = { "ext-a": 0 };

  const first = syncLiveAssignments(PROFILE, livePlayers, liveRules, purchases, teamMap);
  assert.equal(first.synced, 2);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 2);

  // FantaLab's admin deleted "one" (e.g. it was a test bid); the ledger
  // FantaLab now serves no longer contains it, while "two" stays untouched.
  const after = syncLiveAssignments(
    PROFILE,
    livePlayers,
    liveRules,
    [purchases[1]],
    teamMap,
  );
  assert.equal(after.retracted, 1);
  assert.equal(after.synced, 0);
  const board = readAuctionBoard(PROFILE, livePlayers, liveRules);
  assert.equal(board.taken, 1);
  assert.equal(board.assigned["1"], undefined);
  assert.equal(board.assigned["2"].owner, 0);
  assert.equal(board.teams[0].credits, board.teams[0].startingCredits - 6);

  // Retraction only ever touches a slot FantaLab itself filled: an operator's
  // own manual assignment for the freed player must survive the next poll.
  assignPlayer(PROFILE, livePlayers, liveRules, { playerId: 1, owner: 1, price: 2 });
  const untouched = syncLiveAssignments(PROFILE, livePlayers, liveRules, [purchases[1]], teamMap);
  assert.equal(untouched.retracted, 0);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).assigned["1"].owner, 1);
});

test("resetting the auction also clears stale FantaLab provenance", () => {
  resetStore();
  const teamMap = { "ext-a": 0 };
  syncLiveAssignments(
    PROFILE,
    livePlayers,
    liveRules,
    [{ purchase_id: "reused-id", player_id: 1, buyer_team_id: "ext-a", price: 4 }],
    teamMap,
  );
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 1);

  assert.equal(resetAuction(PROFILE, livePlayers, liveRules).ok, true);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 0);

  // FantaLab can reuse the same ledger key across unrelated test rounds; a
  // provenance entry left over from before the reset must not make this
  // fresh sync look like a no-op.
  const after = syncLiveAssignments(
    PROFILE,
    livePlayers,
    liveRules,
    [{ purchase_id: "reused-id", player_id: 1, buyer_team_id: "ext-a", price: 4 }],
    teamMap,
  );
  assert.equal(after.synced, 1);
  assert.equal(after.retracted, 0);
  assert.equal(readAuctionBoard(PROFILE, livePlayers, liveRules).taken, 1);
});

test("FantaLab imports rival purchases into separate rosters and updates both budgets", () => {
  resetStore();
  const multiTeamRules = {
    ...liveRules,
    auction: { ...liveRules.auction, nomination: "call" },
  };
  const result = syncLiveAssignments(
    PROFILE,
    livePlayers,
    multiTeamRules,
    [
      { purchase_id: "ours", player_id: 1, buyer_team_id: "external-a", price: 4 },
      { purchase_id: "theirs", player_id: 2, buyer_team_id: "external-b", price: 8 },
    ],
    { "external-a": 0, "external-b": 1 },
  );

  const board = readAuctionBoard(PROFILE, livePlayers, multiTeamRules);
  assert.equal(result.synced, 2);
  assert.equal(result.pending, 0);
  assert.equal(board.teams[0].roster.length, 1);
  assert.equal(board.teams[1].roster.length, 1);
  assert.equal(board.teams[0].credits, board.teams[0].startingCredits - 4);
  assert.equal(board.teams[1].credits, board.teams[1].startingCredits - 8);
});

test("a player FantaLab calls and leaves unsold is tracked, and clears once bought", () => {
  resetStore();
  const teamMap = { "external-a": 0 };

  const called = syncLiveAssignments(
    PROFILE,
    livePlayers,
    liveRules,
    [{ purchase_id: "gone", player_id: 1, buyer_team_id: null, price: 0, unsold: true }],
    teamMap,
  );
  assert.equal(called.synced, 0);
  assert.deepEqual(readFantalabUnsold(PROFILE), ["1"]);
  assert.deepEqual(readAuctionBoard(PROFILE, livePlayers, liveRules).unsold, ["1"]);

  // Called again later and actually bought this time: no longer unsold, and
  // the purchase itself still syncs normally.
  const bought = syncLiveAssignments(
    PROFILE,
    livePlayers,
    liveRules,
    [{ purchase_id: "bought", player_id: 1, buyer_team_id: "external-a", price: 4 }],
    teamMap,
  );
  assert.equal(bought.synced, 1);
  assert.deepEqual(readFantalabUnsold(PROFILE), []);
});
