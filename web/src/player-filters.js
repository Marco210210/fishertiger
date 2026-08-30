export const PLAYER_FILTERS_VERSION = 1;

const QUERY_MAX_LENGTH = 80;

export const playerFiltersStorageKey = (profileId) =>
  `fanta-player-filters-v${PLAYER_FILTERS_VERSION}:${encodeURIComponent(profileId || "default")}`;

export const defaultPlayerFilters = () => ({
  query: "",
  role: "TUTTI",
  team: "TUTTE",
  onlyTargets: false,
  showLive: false,
});

export const normalizePlayerFilters = (raw, roles = [], teams = []) => {
  const source = raw && typeof raw === "object" ? raw : {};
  const fallback = defaultPlayerFilters();
  return {
    query:
      typeof source.query === "string"
        ? source.query.slice(0, QUERY_MAX_LENGTH)
        : fallback.query,
    role: roles.includes(source.role) ? source.role : fallback.role,
    team: teams.includes(source.team) ? source.team : fallback.team,
    onlyTargets: source.onlyTargets === true,
    showLive: source.showLive === true,
  };
};

export const loadPlayerFilters = (profileId, roles, teams) => {
  try {
    return normalizePlayerFilters(
      JSON.parse(
        localStorage.getItem(playerFiltersStorageKey(profileId)) || "null",
      ),
      roles,
      teams,
    );
  } catch {
    return defaultPlayerFilters();
  }
};

export const savePlayerFilters = (profileId, filters) => {
  try {
    localStorage.setItem(
      playerFiltersStorageKey(profileId),
      JSON.stringify(filters),
    );
  } catch {}
};
