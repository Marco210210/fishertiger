import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizePlayerNotes,
  playerMark,
  playerNotesStorageKey,
  targetCount,
  withNote,
  withTarget,
} from "../src/player-notes.js";

test("marks are stored per profile", () => {
  assert.equal(playerNotesStorageKey("Fantabosco"), "fanta-player-notes-v1:Fantabosco");
  assert.equal(playerNotesStorageKey(""), "fanta-player-notes-v1:default");
  assert.equal(playerNotesStorageKey("a b/c"), "fanta-player-notes-v1:a%20b%2Fc");
});

test("an unmarked player reads as neither target nor noted", () => {
  assert.deepEqual(playerMark({}, 42), { target: false, note: "" });
  assert.deepEqual(playerMark(null, 42), { target: false, note: "" });
});

test("targets and notes are kept independently and looked up by string id", () => {
  let notes = withTarget({}, 42, true);
  assert.equal(playerMark(notes, "42").target, true);
  notes = withNote(notes, 42, "max 30 crediti");
  assert.deepEqual(playerMark(notes, 42), { target: true, note: "max 30 crediti" });
  notes = withTarget(notes, 42, false);
  assert.deepEqual(playerMark(notes, 42), { target: false, note: "max 30 crediti" });
});

test("clearing both the target and the note drops the entry", () => {
  const notes = withNote(withTarget({}, 42, true), 42, "x");
  const cleared = withNote(withTarget(notes, 42, false), 42, "");
  assert.deepEqual(cleared, {});
});

test("a stored payload is sanitized before it reaches the UI", () => {
  const stored = {
    1: { target: true, note: "ok" },
    2: { target: "yes", note: 7 },
    3: { target: false, note: "" },
    4: "corrupt",
  };
  assert.deepEqual(normalizePlayerNotes(stored), {
    1: { target: true, note: "ok" },
  });
  assert.deepEqual(normalizePlayerNotes(null), {});
  assert.deepEqual(normalizePlayerNotes([1, 2]), {});
});

test("a pasted wall of text is truncated instead of stored whole", () => {
  const notes = withNote({}, 1, "x".repeat(5000));
  assert.equal(playerMark(notes, 1).note.length, 1000);
});

test("the target counter ignores note-only marks", () => {
  const notes = withNote(withTarget({}, 1, true), 2, "solo nota");
  assert.equal(targetCount(notes), 1);
  assert.equal(targetCount({}), 0);
});
