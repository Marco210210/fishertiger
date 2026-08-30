import test from "node:test";
import assert from "node:assert/strict";
import { reconcileSelectedPlayer } from "../src/player-selection.js";

const players = [
  { id: 1, nome: "Malen" },
  { id: 2, nome: "Thuram" },
];

test("a selection excluded from the filtered rows is dropped", () => {
  assert.equal(reconcileSelectedPlayer(players[0], [players[1]]), null);
});

test("a selection included in the filtered rows is kept", () => {
  assert.equal(reconcileSelectedPlayer(players[0], players), players[0]);
});

test("a selection missing from the dataset is dropped", () => {
  assert.equal(reconcileSelectedPlayer({ id: 9, nome: "Altro" }, players), null);
});

test("the reconciled selection is the dataset instance, not the stale copy", () => {
  const stale = { id: 1, nome: "Malen", quotazioni: { attuale: 1 } };
  assert.equal(reconcileSelectedPlayer(stale, players), players[0]);
});

test("string and numeric forms of an id reconcile", () => {
  assert.equal(reconcileSelectedPlayer({ id: "1" }, players), players[0]);
});

test("no selection or eligible rows reconciles to nothing", () => {
  assert.equal(reconcileSelectedPlayer(null, players), null);
  assert.equal(reconcileSelectedPlayer(players[0], []), null);
});
