import test from "node:test";
import assert from "node:assert/strict";
import {
  isPlayerInjured,
  normalizePlayerInjuries,
  playerInjuriesStorageKey,
  withPlayerInjury,
} from "../src/player-injuries.js";

test("injuries are stored per profile", () => {
  assert.equal(playerInjuriesStorageKey("Fantabosco"), "fanta-player-injuries-v1:Fantabosco");
  assert.equal(playerInjuriesStorageKey("a b/c"), "fanta-player-injuries-v1:a%20b%2Fc");
});

test("a player can be marked injured and available again", () => {
  const injured = withPlayerInjury({}, 42, true);
  assert.equal(isPlayerInjured(injured, "42"), true);
  assert.deepEqual(withPlayerInjury(injured, 42, false), {});
});

test("stored injury data accepts only numeric IDs with true status", () => {
  assert.deepEqual(
    normalizePlayerInjuries({ 1: true, 2: false, player: true, 3: "yes" }),
    { 1: true },
  );
  assert.deepEqual(normalizePlayerInjuries([1, 2]), {});
  assert.deepEqual(normalizePlayerInjuries(null), {});
});

test("invalid player IDs cannot be added", () => {
  assert.deepEqual(withPlayerInjury({ 1: true }, "not-an-id", true), { 1: true });
});
