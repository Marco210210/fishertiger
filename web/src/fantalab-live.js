const CONNECTION_VERSION = 1;

export const fantalabConnectionKey = (profileId) =>
  `fanta-fantalab-v${CONNECTION_VERSION}:${encodeURIComponent(profileId || "default")}`;

const cleanConnection = (value) => ({
  roomUrl: typeof value?.roomUrl === "string" ? value.roomUrl : "",
  db:
    value?.db === "default" || value?.db === "auto" ||
    (Number.isInteger(Number(value?.db)) && Number(value.db) >= 0 && Number(value.db) <= 19)
      ? String(value.db)
      : "auto",
  teamMap:
    value?.teamMap && typeof value.teamMap === "object" && !Array.isArray(value.teamMap)
      ? Object.fromEntries(
          Object.entries(value.teamMap)
            .filter(([key, index]) => key && Number.isInteger(Number(index)) && Number(index) >= 0)
            .map(([key, index]) => [key, Number(index)]),
        )
      : {},
});

export const readFantalabConnection = (profileId) => {
  try {
    return cleanConnection(JSON.parse(localStorage.getItem(fantalabConnectionKey(profileId)) || "null"));
  } catch {
    return cleanConnection(null);
  }
};

export const writeFantalabConnection = (profileId, value) => {
  const clean = cleanConnection(value);
  try {
    localStorage.setItem(fantalabConnectionKey(profileId), JSON.stringify(clean));
    return { ok: true, value: clean };
  } catch {
    return { ok: false, value: clean };
  }
};

const comparable = (value) =>
  String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9]/g, "")
    .toLowerCase();

export const autoTeamMap = (externalTeams, localTeams, current = {}) => {
  const result = { ...current };
  for (const external of externalTeams || []) {
    if (Number.isInteger(Number(result[external.id]))) continue;
    const wanted = comparable(external.name);
    if (!wanted) continue;
    const matches = (localTeams || []).filter((team) => comparable(team.name) === wanted);
    if (matches.length === 1) result[external.id] = matches[0].index;
  }
  const completePositions =
    externalTeams?.length === localTeams?.length &&
    new Set((externalTeams || []).map((team) => Number(team.position))).size ===
      localTeams?.length &&
    (externalTeams || []).every(
      (team) =>
        Number.isInteger(Number(team.position)) &&
        Number(team.position) >= 1 &&
        Number(team.position) <= localTeams.length,
    );
  if (completePositions) {
    for (const external of externalTeams) {
      if (!Number.isInteger(Number(result[external.id])))
        result[external.id] = Number(external.position) - 1;
    }
  }
  // Public rooms reveal anonymous teams only after their first purchase. When
  // every configured team is now visible and all but one are already mapped,
  // the final pairing is unambiguous and can be completed safely.
  const externalRows = externalTeams || [];
  const localRows = localTeams || [];
  if (externalRows.length === localRows.length) {
    const missingExternal = externalRows.filter(
      (team) => !Number.isInteger(Number(result[team.id])),
    );
    const used = new Set(Object.values(result).map(Number));
    const missingLocal = localRows.filter((team) => !used.has(Number(team.index)));
    if (missingExternal.length === 1 && missingLocal.length === 1)
      result[missingExternal[0].id] = missingLocal[0].index;
  }

  // The public RTDB deliberately omits team names and only reveals a team
  // once it leads a lot or buys a player. Leaving those ids unmapped meant the
  // corresponding purchases stayed pending forever, so rival rosters and
  // credits appeared frozen. Give every newly observed external id the first
  // unused local seat. The backend returns external ids in a stable order and
  // `current` is persisted per league, therefore the pairing never moves on a
  // later poll. A user can still correct the displayed name/seat once from the
  // mapping controls.
  const used = new Set(
    Object.values(result)
      .map(Number)
      .filter((index) => Number.isInteger(index)),
  );
  for (const external of externalRows) {
    if (!external?.id || Number.isInteger(Number(result[external.id]))) continue;
    const local = localRows.find((team) => !used.has(Number(team.index)));
    if (!local) break;
    result[external.id] = Number(local.index);
    used.add(Number(local.index));
  }
  return result;
};

export const mappedTeamIndex = (externalId, teamMap, teamCount) => {
  if (!externalId) return null;
  const index = Number(teamMap?.[externalId]);
  return Number.isInteger(index) && index >= 0 && index < Number(teamCount)
    ? index
    : null;
};

export const secondsRemaining = (lot, serverTimeMs, nowMs) => {
  if (!lot || lot.closed || !Number.isFinite(Number(lot.last_bid_time))) return null;
  const duration = Number(lot.timer_seconds);
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const clockOffset = Number(serverTimeMs) - Number(nowMs);
  const elapsed = (Number(nowMs) + clockOffset - Number(lot.last_bid_time)) / 1000;
  return Math.max(0, duration - elapsed);
};
