/** Loads both halves of a generated profile snapshot before publishing either. */
export const adoptLatestPlayerListUpdate = async ({
  request,
  isCurrent,
  loadProfile,
  loadDataset,
  commit,
}) => {
  const profile = await loadProfile();
  if (!isCurrent(request)) return false;

  const dataset = await loadDataset(profile);
  if (!isCurrent(request)) return false;

  commit(profile, dataset);
  return true;
};
