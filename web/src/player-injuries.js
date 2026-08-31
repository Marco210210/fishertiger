export const PLAYER_INJURIES_VERSION = 1;

export const playerInjuriesStorageKey = (profileId) =>
  `fanta-player-injuries-v${PLAYER_INJURIES_VERSION}:${encodeURIComponent(profileId || "default")}`;

export const normalizePlayerInjuries = (raw) => {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw).filter(([id, injured]) => /^\d+$/.test(id) && injured === true),
  );
};

export const isPlayerInjured = (injuries, playerId) =>
  injuries?.[String(playerId)] === true;

export const withPlayerInjury = (injuries, playerId, injured) => {
  const key = String(playerId);
  if (!/^\d+$/.test(key)) return normalizePlayerInjuries(injuries);
  const next = { ...normalizePlayerInjuries(injuries) };
  if (injured === true) next[key] = true;
  else delete next[key];
  return next;
};

export const loadPlayerInjuries = (profileId) => {
  try {
    return normalizePlayerInjuries(
      JSON.parse(localStorage.getItem(playerInjuriesStorageKey(profileId)) || "null"),
    );
  } catch {
    return {};
  }
};

export const savePlayerInjuries = (profileId, injuries) => {
  try {
    localStorage.setItem(
      playerInjuriesStorageKey(profileId),
      JSON.stringify(normalizePlayerInjuries(injuries)),
    );
    return true;
  } catch {
    return false;
  }
};
