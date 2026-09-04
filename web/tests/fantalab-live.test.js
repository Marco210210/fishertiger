import assert from "node:assert/strict";
import test from "node:test";

import { autoTeamMap, readFantalabConnection, secondsRemaining, writeFantalabConnection } from "../src/fantalab-live.js";

const memory = () => {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
};

test("a FantaLab connection is isolated by profile and sanitized", () => {
  global.localStorage = memory();
  writeFantalabConnection("league-a", { roomUrl: " room ", db: 9, teamMap: { ext: "2", bad: -1 } });
  assert.deepEqual(readFantalabConnection("league-a"), { roomUrl: " room ", db: "9", teamMap: { ext: 2 } });
  assert.deepEqual(readFantalabConnection("league-b"), { roomUrl: "", db: "auto", teamMap: {} });
});

test("team names are matched without punctuation or accents", () => {
  assert.deepEqual(
    autoTeamMap([{ id: "x", name: "L'Àquila FC" }], [{ index: 3, name: "Laquila F.C." }]),
    { x: 3 },
  );
});

test("a complete FantaLab room is mapped by seat position when names differ", () => {
  assert.deepEqual(
    autoTeamMap(
      [
        { id: "away", name: "Nome esterno B", position: 2 },
        { id: "home", name: "Nome esterno A", position: 1 },
      ],
      [
        { index: 0, name: "Squadra locale A" },
        { index: 1, name: "Squadra locale B" },
      ],
    ),
    { away: 1, home: 0 },
  );
});

test("the only remaining team is paired once the complete public room is visible", () => {
  assert.deepEqual(
    autoTeamMap(
      [{ id: "mine" }, { id: "other" }],
      [{ index: 0, name: "Mine" }, { index: 1, name: "Other" }],
      { mine: 0 },
    ),
    { mine: 0, other: 1 },
  );
});

test("the live timer uses the server clock and never goes negative", () => {
  const lot = { last_bid_time: 10_000, timer_seconds: 8, closed: false };
  assert.equal(secondsRemaining(lot, 12_000, 1_000), 6);
  assert.equal(secondsRemaining(lot, 30_000, 1_000), 0);
  assert.equal(secondsRemaining({ ...lot, closed: true }, 12_000, 1_000), null);
});
