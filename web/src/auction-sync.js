import {
  readAuctionPayload,
  replaceAuctionPayload,
  subscribeAuctionChanges,
} from "./auction-store.js";
import {
  ProfileClientError,
  loadAuctionState,
  saveAuctionState,
} from "./profile-client.js";

const sessions = new Map();
const SAVE_DELAY_MS = 350;
const POLL_DELAY_MS = 2000;
const RETRY_DELAY_MS = 4000;

const payloadSignature = (payload) => JSON.stringify(payload);

const publish = (session, state, detail, record = null) => {
  session.status = {
    state,
    detail,
    revision: session.revision,
    updatedAt: record?.updated_at || session.status?.updatedAt || null,
    updatedBy: record?.updated_by || session.status?.updatedBy || null,
  };
  for (const listener of session.listeners) listener(session.status);
};

const applyRemote = (session, record) => {
  if (!record?.state) return true;
  const result = replaceAuctionPayload(
    session.profileId,
    session.players,
    session.rules,
    record.state,
  );
  if (!result.ok) {
    publish(session, "error", result.message);
    return false;
  }
  session.remoteSignature = payloadSignature(
    readAuctionPayload(session.profileId, session.players, session.rules),
  );
  session.dirty = false;
  return true;
};

const scheduleSave = (session, delay = SAVE_DELAY_MS) => {
  clearTimeout(session.saveTimer);
  session.saveTimer = setTimeout(() => void save(session), delay);
};

const save = async (session) => {
  if (session.stopped || !session.ready) return;
  if (session.saving) {
    session.dirty = true;
    return;
  }
  const payload = readAuctionPayload(
    session.profileId,
    session.players,
    session.rules,
  );
  if (!payload) {
    publish(session, "error", "Il browser non permette di leggere l'asta salvata.");
    return;
  }
  const signature = payloadSignature(payload);
  if (!session.dirty && signature === session.remoteSignature) return;

  session.saving = true;
  session.dirty = false;
  publish(session, "saving", "Salvataggio automatico…");
  try {
    const record = await saveAuctionState(
      session.profileId,
      payload,
      session.revision,
    );
    if (session.stopped) return;
    session.revision = Number(record.revision) || session.revision;
    session.remoteSignature = signature;
    publish(session, "saved", "Salvata sul server", record);
  } catch (error) {
    if (session.stopped) return;
    if (
      error instanceof ProfileClientError &&
      error.code === "auction_state_conflict" &&
      error.details
    ) {
      const current = error.details;
      session.revision = Number(current.revision) || session.revision;
      if (applyRemote(session, current)) {
        publish(
          session,
          "conflict",
          "Asta aggiornata con le modifiche più recenti di un altro dispositivo.",
          current,
        );
      }
    } else {
      session.dirty = true;
      publish(
        session,
        "offline",
        "Salvata nel browser; il server verrà riprovato automaticamente.",
      );
      scheduleSave(session, RETRY_DELAY_MS);
    }
  } finally {
    session.saving = false;
    if (session.dirty && !session.saveTimer) scheduleSave(session);
  }
};

const poll = async (session) => {
  if (session.stopped) return;
  if (!session.saving && !session.dirty) {
    try {
      const record = await loadAuctionState(session.profileId);
      if (session.stopped) return;
      const revision = Number(record.revision) || 0;
      if (revision > session.revision) {
        session.revision = revision;
        if (applyRemote(session, record)) {
          publish(
            session,
            "saved",
            record.updated_by
              ? `Aggiornata da ${record.updated_by}`
              : "Aggiornata da un altro dispositivo",
            record,
          );
        }
      }
    } catch {
      if (!session.stopped)
        publish(session, "offline", "Server non raggiungibile; l'asta resta salvata nel browser.");
    }
  }
  if (!session.stopped)
    session.pollTimer = setTimeout(() => void poll(session), POLL_DELAY_MS);
};

const start = async (session) => {
  session.unsubscribeStore = subscribeAuctionChanges(() => {
    if (session.stopped) return;
    session.dirty = true;
    if (session.ready) scheduleSave(session);
  });
  publish(session, "loading", "Collegamento al salvataggio condiviso…");
  try {
    const record = await loadAuctionState(session.profileId);
    if (session.stopped) return;
    session.revision = Number(record.revision) || 0;
    if (record.state) {
      if (!applyRemote(session, record)) return;
      publish(session, "saved", "Asta condivisa caricata", record);
    } else {
      session.remoteSignature = null;
      session.dirty = true;
    }
  } catch {
    if (session.stopped) return;
    session.dirty = true;
    publish(session, "offline", "Server non raggiungibile; l'asta resta salvata nel browser.");
  }
  session.ready = true;
  if (session.dirty) scheduleSave(session, 0);
  session.pollTimer = setTimeout(() => void poll(session), POLL_DELAY_MS);
};

const stop = (session) => {
  session.stopped = true;
  clearTimeout(session.saveTimer);
  clearTimeout(session.pollTimer);
  session.unsubscribeStore?.();
};

/** One synchronizer is shared by every mounted view of the same league. */
export const subscribeAuctionSync = (profileId, players, rules, listener) => {
  const id = String(profileId || "default");
  const rulesSignature = JSON.stringify(rules);
  let session = sessions.get(id);
  if (session && session.rulesSignature !== rulesSignature) {
    stop(session);
    sessions.delete(id);
    session = null;
  }
  if (!session) {
    session = {
      profileId: id,
      players,
      rules,
      rulesSignature,
      listeners: new Set(),
      refs: 0,
      revision: 0,
      remoteSignature: null,
      ready: false,
      dirty: false,
      saving: false,
      stopped: false,
      saveTimer: null,
      pollTimer: null,
      unsubscribeStore: null,
      status: { state: "loading", detail: "Collegamento al salvataggio condiviso…" },
    };
    sessions.set(id, session);
    void start(session);
  }
  session.refs += 1;
  session.listeners.add(listener);
  listener(session.status);
  return () => {
    session.listeners.delete(listener);
    session.refs -= 1;
    if (session.refs <= 0) {
      stop(session);
      if (sessions.get(id) === session) sessions.delete(id);
    }
  };
};
