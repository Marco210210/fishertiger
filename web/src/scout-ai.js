import { apiUrl } from "./profile-client.js";

export const scoutSeasonSlug = (profile) =>
  String(profile?.season?.season || "")
    .trim()
    .replace("/", "-");

export const loadScoutAi = async (profile, { apiBase = "" } = {}) => {
  const slug = scoutSeasonSlug(profile);
  if (!/^20\d{2}-\d{2}$/.test(slug)) return null;
  const response = await fetch(apiUrl(`/api/scout/${slug}`, apiBase));
  if (!response.ok) return null;
  return response.json();
};

/** The Claude-researched companion snapshot. Most of it stays purely
 * informational, visible only in the dedicated Claude section — see
 * mergeScoutSnapshots for the one exception. */
export const loadScoutAiClaude = async (profile, { apiBase = "" } = {}) => {
  const slug = scoutSeasonSlug(profile);
  if (!/^20\d{2}-\d{2}$/.test(slug)) return null;
  const response = await fetch(apiUrl(`/api/scout-claude/${slug}`, apiBase));
  if (!response.ok) return null;
  return response.json();
};

/** Folds a Claude entry into the official snapshot everywhere in the app
 * (the advisor, injury flags, the Listone) — but only when it is explicitly
 * marked `promoted`. An operator reviews new Claude findings in their own
 * section first; only once they are trusted does a future update mark them
 * promoted, so this never surfaces unreviewed research as if it were the
 * verified official source. */
export const mergeScoutSnapshots = (official, claude) => {
  const officialPlayers = official?.players || {};
  const claudePlayers = claude?.players || {};
  const merged = { ...officialPlayers };
  for (const [id, reading] of Object.entries(claudePlayers)) {
    if (reading?.promoted) merged[id] = reading;
  }
  return { ...(official || {}), players: merged };
};

export const enrichPlayersWithScout = (players, snapshot) => {
  const readings = snapshot?.players || {};
  return (players || []).map((player) => {
    const reading = readings[String(player.id)] || null;
    return reading
      ? { ...player, scout_ai: reading, scout_multiplier: Number(reading.multiplier) || 1 }
      : player;
  });
};

export const scoutTone = (status) =>
  ({ out: "stop", doubt: "warn", monitor: "warn", positive: "go" })[status] || "info";

export const scoutStatusLabel = (status) =>
  ({ out: "Fuori", doubt: "In dubbio", monitor: "Da monitorare", positive: "Notizia positiva", neutral: "Neutro" })[status] || "Da monitorare";
