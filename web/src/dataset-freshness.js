import { sameAuctionRosters } from "./auction-simulation.js";

const list = (value) => (Array.isArray(value) ? value : []);

const missesRequiredSource = (profile, fingerprints) => {
  const declared = [
    ...list(profile?.current_sources),
    ...list(profile?.history_sources),
  ];
  return list(fingerprints).some((source) => {
    if (source?.exists !== false) return false;
    const match = declared.find((item) => item.name === source.name);
    return match ? match.required !== false : true;
  });
};

const changedSourceContent = (generated, current) => {
  if (!Array.isArray(current)) return false;
  return list(generated).some((source) => {
    const match = current.find((item) => item.group === source.group && item.name === source.name);
    if (!match || match.exists !== source.exists) return true;
    return source.exists && match.sha256 !== source.sha256;
  });
};

export const datasetFreshness = (profile, data, currentSources) => {
  const meta = data?.meta?.profile;
  if (!meta?.dataset_configuration_hash) return "dataset da rigenerare";
  if (meta.dataset_configuration_hash !== profile?.dataset_configuration_hash)
    return "dataset da rigenerare";
  if (missesRequiredSource(profile, meta.source_fingerprints))
    return "fonti cambiate";
  if (changedSourceContent(meta.source_fingerprints, currentSources))
    return "fonti cambiate";
  return "dataset corrente";
};

export const simulationFreshness = (profile, data, season, auction = null) => {
  const datasetHash = data?.meta?.profile?.dataset_input_hash;
  const current = datasetHash &&
    season?.meta?.dataset_input_hash === datasetHash &&
    season?.meta?.simulation_configuration_hash ===
      profile?.simulation_configuration_hash
  if (!current) return "simulazione da aggiornare";
  if (season?.meta?.roster_mode !== "auction") return "simulazione corrente";
  return auction?.complete && sameAuctionRosters(season.rosters, auction.rosters)
    ? "simulazione corrente"
    : "simulazione da aggiornare";
};
