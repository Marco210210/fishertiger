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
  return result;
};

export const secondsRemaining = (lot, serverTimeMs, nowMs) => {
  if (!lot || lot.closed || !Number.isFinite(Number(lot.last_bid_time))) return null;
  const duration = Number(lot.timer_seconds);
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const clockOffset = Number(serverTimeMs) - Number(nowMs);
  const elapsed = (Number(nowMs) + clockOffset - Number(lot.last_bid_time)) / 1000;
  return Math.max(0, duration - elapsed);
};
