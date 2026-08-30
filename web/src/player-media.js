const CDN = "https://content.fantacalcio.it/web";
const CAMPIONCINI_SET = "21";
const PLAYER_SIZES = { small: "small", medium: "medium", card: "card" };

export const MEDIA_STORAGE_KEY = "fanta-player-media";

export const playerImageUrl = (player, size = "small") => {
  const id = player?.id;
  if (id === null || id === undefined || id === "") return null;
  const cut = PLAYER_SIZES[size] || PLAYER_SIZES.small;
  return `${CDN}/campioncini/${CAMPIONCINI_SET}/${cut}/${encodeURIComponent(id)}.png`;
};

export const teamLogoUrl = (team) => {
  const id = typeof team === "string" ? team : team?.team_id;
  return id ? `${CDN}/img/team/${encodeURIComponent(id)}.png` : null;
};

export const readMediaPreference = () => {
  try {
    return localStorage.getItem(MEDIA_STORAGE_KEY) === "on";
  } catch {
    return false;
  }
};

export const writeMediaPreference = (enabled) => {
  try {
    localStorage.setItem(MEDIA_STORAGE_KEY, enabled ? "on" : "off");
  } catch {}
};
