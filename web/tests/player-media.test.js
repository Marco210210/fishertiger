import test from "node:test";
import assert from "node:assert/strict";
import { playerImageUrl, teamLogoUrl } from "../src/player-media.js";

test("player artwork uses the listone id served by the Fantacalcio CDN", () => {
  assert.equal(
    playerImageUrl({ id: 5841 }),
    "https://content.fantacalcio.it/web/campioncini/21/small/5841.png",
  );
  assert.equal(
    playerImageUrl({ id: 5841 }, "medium"),
    "https://content.fantacalcio.it/web/campioncini/21/medium/5841.png",
  );
});

test("an unknown size falls back to the light cut instead of a broken path", () => {
  assert.equal(
    playerImageUrl({ id: 1 }, "huge"),
    "https://content.fantacalcio.it/web/campioncini/21/small/1.png",
  );
});

test("a player without an id has no artwork", () => {
  assert.equal(playerImageUrl(null), null);
  assert.equal(playerImageUrl({}), null);
});

test("team crests accept both a team record and its slug", () => {
  const expected = "https://content.fantacalcio.it/web/img/team/roma.png";
  assert.equal(teamLogoUrl("roma"), expected);
  assert.equal(teamLogoUrl({ team_id: "roma" }), expected);
  assert.equal(teamLogoUrl({}), null);
  assert.equal(teamLogoUrl(null), null);
});
