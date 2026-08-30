import {
  AUCTION_STORAGE_VERSION,
  auctionStorageKey,
  emptyAuction,
  isValidBid,
  legalMaxBid,
  playerIdKey,
  rehydrateAuction,
  serializeAuction,
  slotsLeft,
} from "./auction-state.js";
import { activeNominationRole } from "./auction-nomination.js";


export const AUCTION_LEGACY_STORAGE_KEY = "fanta-auction-v1";
const STARTING_CREDITS_FLOOR = 25;

const STORAGE_FAILURE =
  "Memoria del browser non disponibile: nulla è stato salvato.";

const ROLE_NAMES = {
  P: "portieri",
  D: "difensori",
  C: "centrocampisti",
  A: "attaccanti",
};

const failure = (message) => ({ ok: false, message });
const success = (message) => ({ ok: true, message });

const readKey = (key) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};

const writeKey = (key, value) => {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
};

const removeKey = (key) => {
  try {
    localStorage.removeItem(key);
  } catch {
    /* Nothing to clean up when storage is unavailable. */
  }
};

const parsed = (raw) => {
  try {
    return JSON.parse(raw || "null");
  } catch {
    return null;
  }
};

export const userTeamStorageKey = (profileId) =>
  `fanta-auction-user-team:${encodeURIComponent(profileId || "default")}`;

export const defaultUserTeamIndex = (rules) => {
  const configured = Number(rules?.userTeam);
  const byIndex =
    Number.isInteger(configured) &&
      configured >= 0 &&
      configured < rules?.participants
      ? configured
      : (rules?.teamNames?.indexOf(rules?.userTeam) ?? -1);
  return Math.max(0, byIndex);
};

const inRange = (index, rules) =>
  Number.isInteger(index) && index >= 0 && index < rules?.participants;

export const readUserTeamIndex = (profileId, rules) => {
  const raw = readKey(userTeamStorageKey(profileId));
  if (typeof raw !== "string" || !raw.trim())
    return defaultUserTeamIndex(rules);
  const stored = Number(raw);
  return inRange(stored, rules) ? stored : defaultUserTeamIndex(rules);
};

const listeners = new Set();

export const notifyAuctionChanged = () => {
  for (const listener of [...listeners]) listener();
};

export const writeUserTeamIndex = (profileId, index) => {
  if (!writeKey(userTeamStorageKey(profileId), String(index)))
    return failure(STORAGE_FAILURE);
  notifyAuctionChanged();
  return success("");
};

/** Same-tab writes come through `notifyAuctionChanged`, other tabs through the
 *  storage event: an open view must never keep showing a superseded auction. */
export const subscribeAuctionChanges = (listener) => {
  listeners.add(listener);
  const onStorage = (event) => {
    if (!event.key || event.key.startsWith("fanta-auction")) listener();
  };
  if (typeof window !== "undefined")
    window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined")
      window.removeEventListener("storage", onStorage);
  };
};

const migrateLegacy = (profileId, players, rules) => {
  if (String(profileId || "default") !== "default") return null;
  const legacy = parsed(readKey(AUCTION_LEGACY_STORAGE_KEY));
  const state = rehydrateAuction(legacy, players, rules);
  if (!state) {
    if (legacy) removeKey(AUCTION_LEGACY_STORAGE_KEY);
    return null;
  }
  if (writeKey(auctionStorageKey(profileId), JSON.stringify(serializeAuction(state))))
    removeKey(AUCTION_LEGACY_STORAGE_KEY);
  return state;
};

export const readAuction = (profileId, players, rules) =>
  rehydrateAuction(parsed(readKey(auctionStorageKey(profileId))), players, rules) ||
  migrateLegacy(profileId, players, rules) ||
  emptyAuction(rules);

export const readAuctionBoard = (profileId, players, rules) => {
  const state = readAuction(profileId, players, rules);
  return {
    teams: state.teams.map((team, index) => ({
      ...team,
      index,
      maxBid: legalMaxBid(team, rules),
      slotsLeft: slotsLeft(team, rules),
    })),
    teamNames: state.teams.map((team) => team.name),
    assigned: state.assigned,
    history: state.history,
    undone: state.undone || [],
    taken: Object.keys(state.assigned).length,
    activeRole: activeNominationRole(state.teams, rules),
    userTeamIndex: readUserTeamIndex(profileId, rules),
  };
};

const compact = ({ playerId, owner, price }) => ({ playerId, owner, price });

const payloadFrom = (state, { history, undone = [], teams }) => ({
  version: AUCTION_STORAGE_VERSION,
  teams: (teams || state.teams).map(({ name, startingCredits }) => ({
    name,
    startingCredits,
  })),
  history: history.map(compact),
  undone: undone.map(compact),
});

const persist = (profileId, payload, players, rules, message, invalid) => {
  if (!rehydrateAuction(payload, players, rules))
    return failure(invalid || "Operazione incompatibile con lo stato dell'asta.");
  return writeKey(auctionStorageKey(profileId), JSON.stringify(payload))
    ? (notifyAuctionChanged(), success(message))
    : failure(STORAGE_FAILURE);
};

const playerFrom = (players, playerId) =>
  (players || []).find(
    (candidate) => playerIdKey(candidate.id) === playerIdKey(playerId),
  ) || null;

const roleName = (role) => ROLE_NAMES[role] || role;

export const assignPlayer = (profileId, players, rules, request) => {
  const state = readAuction(profileId, players, rules);
  const player = playerFrom(players, request?.playerId);
  const owner = Number(request?.owner);
  const price = Number(request?.price);
  const team = state.teams[owner];
  if (!player) return failure("Giocatore non presente nel dataset.");
  if (!team) return failure("Scegli una squadra acquirente.");
  if (state.assigned[playerIdKey(player.id)])
    return failure(`${player.nome} risulta già assegnato.`);
  const role = activeNominationRole(state.teams, rules);
  if (role && player.ruolo !== role)
    return failure(`In questa fase puoi assegnare solo ${roleName(role)}.`);
  if (!Number.isInteger(price) || price < rules.auction.minPrice)
    return failure(
      `Inserisci un prezzo intero di almeno ${rules.auction.minPrice} crediti.`,
    );
  if ((price - rules.auction.minPrice) % rules.auction.increment)
    return failure(
      `Il prezzo deve salire di ${rules.auction.increment} crediti a partire da ${rules.auction.minPrice}.`,
    );
  const legalMax = legalMaxBid(team, rules);
  if (price > legalMax) {
    const reserve =
      Math.max(
        0,
        Object.values(slotsLeft(team, rules)).reduce(
          (sum, count) => sum + count,
          0,
        ) - 1,
      ) * rules.auction.reserve;
    return failure(
      `${team.name} può spendere al massimo ${legalMax} crediti: deve conservarne ${reserve} per completare la rosa.`,
    );
  }
  if (slotsLeft(team, rules)[player.ruolo] < 1)
    return failure(
      `${team.name} non ha più posti per ${roleName(player.ruolo)}.`,
    );
  return persist(
    profileId,
    payloadFrom(state, {
      history: [...state.history, { playerId: player.id, owner, price }],
    }),
    players,
    rules,
    `${player.nome} assegnato a ${team.name} per ${price} crediti.`,
  );
};

export const releasePlayer = (profileId, players, rules, playerId) => {
  const state = readAuction(profileId, players, rules);
  if (!state.assigned[playerIdKey(playerId)])
    return failure("Il giocatore non risulta assegnato.");
  return persist(
    profileId,
    payloadFrom(state, {
      history: state.history.filter(
        (transaction) =>
          playerIdKey(transaction.playerId) !== playerIdKey(playerId),
      ),
      undone: state.undone || [],
    }),
    players,
    rules,
    `${playerFrom(players, playerId)?.nome || "Giocatore"} rimesso tra i disponibili.`,
  );
};

export const undoAssignment = (profileId, players, rules) => {
  const state = readAuction(profileId, players, rules);
  const last = state.history.at(-1);
  if (!last) return failure("Non c'è nessuna assegnazione da annullare.");
  return persist(
    profileId,
    payloadFrom(state, {
      history: state.history.slice(0, -1),
      undone: [...(state.undone || []), last],
    }),
    players,
    rules,
    `Annullata l'assegnazione di ${playerFrom(players, last.playerId)?.nome || "giocatore"}.`,
  );
};

export const redoAssignment = (profileId, players, rules) => {
  const state = readAuction(profileId, players, rules);
  const last = (state.undone || []).at(-1);
  if (!last) return failure("Non c'è nessuna assegnazione da ripristinare.");
  const restored = playerFrom(players, last.playerId);
  if (!restored) return failure("Giocatore non presente nel dataset.");
  return persist(
    profileId,
    payloadFrom(state, {
      history: [...state.history, last],
      undone: state.undone.slice(0, -1),
    }),
    players,
    rules,
    `Ripristinata l'assegnazione di ${restored.nome}.`,
    "Non posso ripristinare l'operazione: budget o slot sono cambiati.",
  );
};

export const resetAuction = (profileId, players, rules) =>
  persist(
    profileId,
    serializeAuction(emptyAuction(rules)),
    players,
    rules,
    "Asta azzerata. Puoi reimpostare i crediti iniziali.",
  );

/** Starting credits describe the league before it starts, so they may only move
 *  while nothing has been bought — including through the redo stack. */
export const setStartingCredits = (
  profileId,
  players,
  rules,
  teamIndex,
  value,
) => {
  const state = readAuction(profileId, players, rules);
  const credits = Number(value);
  if (state.history.length || (state.undone || []).length)
    return failure("I crediti iniziali si cambiano solo prima del primo acquisto.");
  if (!state.teams[teamIndex]) return failure("Squadra non riconosciuta.");
  if (!Number.isInteger(credits) || credits < STARTING_CREDITS_FLOOR)
    return failure(
      `I crediti iniziali devono essere almeno ${STARTING_CREDITS_FLOOR}.`,
    );
  return persist(
    profileId,
    payloadFrom(state, {
      history: [],
      teams: state.teams.map((team, index) =>
        index === teamIndex ? { ...team, startingCredits: credits } : team,
      ),
    }),
    players,
    rules,
    "",
  );
};

export const renameTeam = (profileId, players, rules, teamIndex, name) => {
  const state = readAuction(profileId, players, rules);
  const label = String(name ?? "").trim();
  if (!state.teams[teamIndex]) return failure("Squadra non riconosciuta.");
  if (!label) return failure("Il nome della squadra non può essere vuoto.");
  return persist(
    profileId,
    payloadFrom(state, {
      history: state.history,
      undone: state.undone || [],
      teams: state.teams.map((team, index) =>
        index === teamIndex ? { ...team, name: label } : team,
      ),
    }),
    players,
    rules,
    "",
  );
};

export const clearAuctionData = (profileId) => {
  removeKey(auctionStorageKey(profileId));
  removeKey(userTeamStorageKey(profileId));
};

export const playerAuctionStatus = (board, player) => {
  const record = board?.assigned?.[playerIdKey(player?.id)];
  if (!record) return null;
  return {
    owner: record.owner,
    price: record.price,
    ownerName: board.teamNames[record.owner] || `Squadra ${record.owner + 1}`,
    mine: record.owner === board.userTeamIndex,
  };
};
