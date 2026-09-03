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
