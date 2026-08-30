import test from "node:test";
import assert from "node:assert/strict";
import { reconcileSelectedPlayer } from "../src/player-selection.js";

const players = [
  { id: 1, nome: "Malen" },
  { id: 2, nome: "Thuram" },
];

test("a selection the query no longer matches is dropped", () => {
  assert.equal(reconcileSelectedPlayer(players[0], players, "Thuram"), null);
});

test("a selection the query still matches is kept", () => {
  assert.equal(reconcileSelectedPlayer(players[0], players, "mal"), players[0]);
  assert.equal(reconcileSelectedPlayer(players[0], players, "  "), players[0]);
  assert.equal(reconcileSelectedPlayer(players[0], players, ""), players[0]);
});

test("a selection missing from the dataset is dropped", () => {
  assert.equal(reconcileSelectedPlayer({ id: 9, nome: "Altro" }, players, ""), null);
});

test("the reconciled selection is the dataset instance, not the stale copy", () => {
  const stale = { id: 1, nome: "Malen", quotazioni: { attuale: 1 } };
  assert.equal(reconcileSelectedPlayer(stale, players, "Malen"), players[0]);
});

test("no selection reconciles to nothing", () => {
  assert.equal(reconcileSelectedPlayer(null, players, "Malen"), null);
  assert.equal(reconcileSelectedPlayer(undefined, players, ""), null);
});
