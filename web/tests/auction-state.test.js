import test from "node:test";
import assert from "node:assert/strict";
import { auctionPriceAtOrBelow, draftForQuery, draftPlayer, emptyAuction, emptyDraft, legalMaxBid, nearestAuctionPrice, reconcileAuctionDraft, rehydrateAuction, serializeAuction } from "../src/auction-state.js";

const rules = { participants: 2, teamNames: ["Mine", "Other"], startingCredits: 20, rosterSlots: { P: 1, A: 1 }, auction: { minPrice: 2, increment: 2, reserve: 2 } };
const players = [{ id: 1, ruolo: "P" }, { id: 2, ruolo: "A" }];

test("rehydrates compact transactions and preserves player references", () => {
  const saved = { version: 2, teams: [{ name: "Mine", startingCredits: 20 }, { name: "Other", startingCredits: 20 }], history: [{ playerId: 1, owner: 0, price: 4 }], undone: [] };
  const state = rehydrateAuction(saved, players, rules);
  assert.equal(state.teams[0].roster[0], players[0]);
  assert.deepEqual(serializeAuction(state).history, saved.history);
});

test("rejects corrupt or incompatible auction state", () => {
  assert.equal(rehydrateAuction({ teams: [], history: [] }, players, rules), null);
  assert.equal(rehydrateAuction({ version: 2, teams: [{ name: "Mine", startingCredits: 20 }, { name: "Other", startingCredits: 20 }], history: [{ playerId: 99, owner: 0, price: 4 }] }, players, rules), null);
});

test("reserves credits for remaining configured slots", () => {
  assert.equal(legalMaxBid(emptyAuction(rules).teams[0], rules), 18);
});

test("an empty nomination draft selects nobody", () => {
  const draft = emptyDraft();
  assert.deepEqual(draft, { playerId: null, query: "", price: "" });
  assert.equal(draftPlayer(draft, players), null);
});

test("a nomination draft resolves its player by id across dataset reloads", () => {
  const draft = { ...emptyDraft(), playerId: 2, query: "Tal", price: "12" };
  assert.equal(draftPlayer(draft, players), players[1]);
  // A regenerated dataset hands back equal-but-distinct player objects.
  assert.deepEqual(draftPlayer(draft, [{ id: 1, ruolo: "P" }, { id: 2, ruolo: "A" }]), players[1]);
  // Ids arriving as strings must still match.
  assert.equal(draftPlayer({ ...draft, playerId: "2" }, players), players[1]);
});

test("a nomination draft for a player the dataset no longer has selects nobody", () => {
  assert.equal(draftPlayer({ ...emptyDraft(), playerId: 99 }, players), null);
  assert.equal(draftPlayer({ ...emptyDraft(), playerId: 1 }, []), null);
  assert.equal(draftPlayer(null, players), null);
});

test("editing a selected player's query invalidates the nomination and price", () => {
  const selected = { playerId: 2, query: "Player A", price: "11" };
  assert.deepEqual(draftForQuery(selected, [{ id: 2, nome: "Player A" }], "Player B"), {
    playerId: null,
    query: "Player B",
    price: "",
  });
  assert.deepEqual(draftForQuery(selected, [{ id: 2, nome: "Player A" }], "Player A"), selected);
});

test("an externally assigned player invalidates the whole nomination", () => {
  const draft = { playerId: 2, query: "Bomber", price: "12" };
  assert.deepEqual(
    reconcileAuctionDraft(draft, players, {
      assigned: { 2: { owner: 1, price: 12 } },
      activeRole: null,
      storageReadOk: true,
    }),
    emptyDraft(),
  );
});

test("an active-role change invalidates a nomination from the old phase", () => {
  const draft = { playerId: 1, query: "Portiere", price: "4" };
  assert.deepEqual(
    reconcileAuctionDraft(draft, players, {
      assigned: {},
      activeRole: "A",
      storageReadOk: true,
    }),
    emptyDraft(),
  );
});

test("a player removed by a dataset refresh invalidates the nomination", () => {
  const draft = { playerId: 2, query: "Bomber", price: "12" };
  assert.deepEqual(
    reconcileAuctionDraft(draft, [players[0]], {
      assigned: {},
      activeRole: null,
      storageReadOk: true,
    }),
    emptyDraft(),
  );
});

test("a valid nomination survives board updates and unreadable storage", () => {
  const draft = { playerId: 2, query: "Bomber", price: "12" };
  assert.equal(
    reconcileAuctionDraft(draft, players, {
      assigned: {},
      activeRole: "A",
      storageReadOk: true,
    }),
    draft,
  );
  assert.equal(
    reconcileAuctionDraft(draft, players, {
      assigned: {},
      activeRole: "P",
      storageReadOk: false,
    }),
    draft,
  );
});

test("custom minimum and increment snap every generated price to the legal grid", () => {
  const custom = {
    ...rules,
    auction: { minPrice: 2, increment: 3, reserve: 2 },
  };
  const team = { ...emptyAuction(custom).teams[0], credits: 20 };
  assert.equal(auctionPriceAtOrBelow(20, custom), 20);
  assert.equal(auctionPriceAtOrBelow(19, custom), 17);
  assert.equal(nearestAuctionPrice(12, 19, custom), 11);
  assert.equal(nearestAuctionPrice(13, 19, custom), 14);
  assert.equal(nearestAuctionPrice(99, 19, custom), 17);
  assert.equal(legalMaxBid(team, custom), 17);
  for (const price of [2, 5, 8, 11, 14, 17])
    assert.equal((price - custom.auction.minPrice) % custom.auction.increment, 0);
});
