import { useEffect, useMemo, useRef, useState } from "react";

import { apiUrl } from "../profile-client.js";
import {
  renameTeam,
  setStartingCredits,
  syncLiveAssignments,
} from "../auction-store.js";
import { useAuctionBoard } from "../use-auction-store.js";
import {
  autoTeamMap,
  mappedTeamIndex,
  readFantalabConnection,
  secondsRemaining,
  writeFantalabConnection,
} from "../fantalab-live.js";
import AuctionView from "./auction.jsx";

const errorMessage = async (response) => {
  try {
    const payload = await response.json();
    return payload?.error?.message || `FantaLab ha risposto con stato ${response.status}.`;
  } catch {
    return `FantaLab ha risposto con stato ${response.status}.`;
  }
};

const shortId = (value) => (value ? `…${String(value).slice(-6)}` : "sconosciuta");
const POLL_INTERVAL_MS = 1200;

const importRoomSetup = (profileId, players, rules, board, snapshot, teamMap) => {
  const participants = Number(snapshot.room?.participants);
  if (!Number.isInteger(participants) || !participants) return "profile";
  if (participants !== board.teams.length) return "mismatch";
  if (snapshot.teams?.length !== participants) return "partial";

  for (const external of snapshot.teams) {
    const index = Number(teamMap[external.id]);
    const local = board.teams[index];
    if (!local) continue;
    if (external.name && external.name !== local.name)
      renameTeam(profileId, players, rules, index, external.name);
    const credits = Number(
      external.starting_credits ?? snapshot.room?.starting_credits,
    );
    if (
      !board.history.length &&
      !board.undone.length &&
      Number.isInteger(credits) &&
      credits >= 25 &&
      credits !== local.startingCredits
    )
      setStartingCredits(profileId, players, rules, index, credits);
  }
  return "imported";
};

export default function LiveAuctionView({
  data,
  openPlayer,
  rules,
  profileId,
  draft,
  setDraft,
  apiBase,
}) {
  const [connection, setConnection] = useState(() => readFantalabConnection(profileId));
  const [active, setActive] = useState(false);
  const [snapshot, setSnapshot] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [roomNotice, setRoomNotice] = useState("");
  const [now, setNow] = useState(Date.now());
  const board = useAuctionBoard(profileId, data.players, rules);
  const teamMapRef = useRef(connection.teamMap);
  const liveRef = useRef({ players: data.players, rules, board });

  useEffect(() => {
    liveRef.current = { players: data.players, rules, board };
  }, [data.players, rules, board]);

  useEffect(() => {
    const next = readFantalabConnection(profileId);
    teamMapRef.current = next.teamMap;
    setConnection(next);
    setActive(false);
    setSnapshot(null);
    setError("");
    setMessage("");
    setRoomNotice("");
  }, [profileId]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!active || !connection.roomUrl.trim()) return undefined;
    let cancelled = false;
    let timer = null;
    let resolvedDb = connection.db;
    let shouldContinue = true;

    const poll = async () => {
      try {
        const response = await fetch(apiUrl("/api/fantalab/snapshot", apiBase), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ room_url: connection.roomUrl, db: resolvedDb }),
        });
        if (!response.ok) throw new Error(await errorMessage(response));
        const next = await response.json();
        if (cancelled) return;
        const discoveredDb = resolvedDb === "auto" ? String(next.db) : null;
        if (discoveredDb) resolvedDb = discoveredDb;
        setSnapshot(next);
        setError("");

        const current = liveRef.current;
        const mapped = autoTeamMap(
          next.teams,
          current.board.teams,
          teamMapRef.current,
        );
        const mappingChanged =
          JSON.stringify(mapped) !== JSON.stringify(teamMapRef.current);
        if (mappingChanged || discoveredDb) {
          teamMapRef.current = mapped;
          setConnection((current) => {
            const saved = writeFantalabConnection(profileId, {
              ...current,
              db: discoveredDb || current.db,
              teamMap: mapped,
            });
            return saved.value;
          });
        }

        const setup = importRoomSetup(
          profileId,
          current.players,
          current.rules,
          current.board,
          next,
          mapped,
        );
        setRoomNotice(
          setup === "mismatch"
            ? `FantaLab indica ${next.room.participants} squadre, ma il profilo ne contiene ${current.board.teams.length}: correggi il profilo prima di importare le rose.`
            : setup === "imported"
              ? "Nomi, posizioni e crediti della stanza FantaLab sono stati importati nell’asta."
              : "FantaLab non espone pubblicamente la configurazione completa: uso squadre e crediti del profilo e importerò gli acquisti in tempo reale.",
        );

        const sync = syncLiveAssignments(
          profileId,
          current.players,
          current.rules,
          next.purchases,
          mapped,
        );
        if (sync.synced || sync.conflicts)
          setMessage(sync.message || `${sync.synced} acquisti sincronizzati.`);

        const player = current.players.find((candidate) =>
          String(candidate.id) === String(next.lot?.player_id),
        );
        if (player && !next.lot?.closed) {
          setDraft({
            playerId: player.id,
            query: player.nome,
            price: String(next.lot.price || current.rules.auction.minPrice),
          });
        }
      } catch (failure) {
        if (!cancelled) {
          const detail = failure instanceof Error
            ? failure.message
            : "Sincronizzazione FantaLab non riuscita.";
          setError(detail);
          // Automatic discovery probes every public FantaLab namespace. If the
          // room is not visible yet, stop instead of repeating that scan each
          // second; reconnecting or choosing the shard starts a fresh attempt.
          if (connection.db === "auto" && detail.includes("Stanza non trovata automaticamente")) {
            shouldContinue = false;
            setActive(false);
          }
        }
      } finally {
        if (!cancelled && shouldContinue)
          timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [active, connection.roomUrl, connection.db, apiBase, profileId, setDraft]);

  const saveConnection = (next) => {
    const stored = writeFantalabConnection(profileId, next);
    teamMapRef.current = stored.value.teamMap;
    setConnection(stored.value);
    if (!stored.ok) setError("Il browser non ha potuto salvare la configurazione FantaLab.");
  };

  const connect = () => {
    if (!connection.roomUrl.trim()) {
      setError("Incolla prima il link della stanza FantaLab.");
      return;
    }
    saveConnection(connection);
    setSnapshot(null);
    setError("");
    setActive(true);
  };

  const updateMapping = (externalId, localIndex) => {
    const nextMap = { ...teamMapRef.current };
    if (localIndex === "") delete nextMap[externalId];
    else {
      const target = Number(localIndex);
      for (const [otherId, mappedIndex] of Object.entries(nextMap)) {
        if (otherId !== externalId && Number(mappedIndex) === target)
          delete nextMap[otherId];
      }
      nextMap[externalId] = target;
    }
    saveConnection({ ...connection, teamMap: nextMap });
  };

  const timer = secondsRemaining(snapshot?.lot, snapshot?.server_time_ms, now);
  const teamNames = useMemo(
    () => Object.fromEntries((snapshot?.teams || []).map((team) => {
      const localIndex = Number(connection.teamMap?.[team.id]);
      return [
        team.id,
        team.name || board.teams[localIndex]?.name || shortId(team.id),
      ];
    })),
    [snapshot?.teams, connection.teamMap, board.teams],
  );
  const currentLeader = teamNames[snapshot?.lot?.leader_team_id] || shortId(snapshot?.lot?.leader_team_id);
  const liveOwnerIndex = mappedTeamIndex(
    snapshot?.lot?.leader_team_id,
    connection.teamMap,
    board.teams.length,
  );
  const unmappedTeams = (snapshot?.teams || []).filter(
    (team) => !Number.isInteger(Number(connection.teamMap?.[team.id])),
  );
  const teamActivity = useMemo(
    () => Object.fromEntries((snapshot?.teams || []).map((team) => [
      team.id,
      (snapshot?.purchases || []).filter(
        (purchase) => !purchase.unsold && purchase.buyer_team_id === team.id,
      ),
    ])),
    [snapshot?.teams, snapshot?.purchases],
  );
  const pendingPurchases = (snapshot?.purchases || []).filter(
    (purchase) =>
      !purchase.unsold &&
      !Number.isInteger(Number(connection.teamMap?.[purchase.buyer_team_id])),
  ).length;
  const participantCount = snapshot?.room?.participants || board.teams.length;
  const startingCredits = snapshot?.room?.starting_credits || rules.startingCredits;
  const lastUpdated = snapshot?.server_time_ms
    ? new Date(snapshot.server_time_ms).toLocaleTimeString("it-IT", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";

  return (
    <div className="stack stack--lg">
      <section className="card live-connect">
        <div className="live-connect__head">
          <div>
            <span className="kicker">FantaLab · sola lettura</span>
            <h1>Asta sincronizzata</h1>
            <p className="muted">Incolla il link della stanza. AstaFanta Support leggerà chiamata, prezzo e acquisti senza fare offerte al posto tuo.</p>
          </div>
          <span className={`live-state ${active && !error ? "is-online" : ""}`}>
            <i /> {active && !error ? "collegata" : active ? "riconnessione" : "disconnessa"}
          </span>
        </div>
        <div className="live-connect__form">
          <label className="field live-connect__url">
            <span className="field-label">Link stanza</span>
            <input
              className="input"
              value={connection.roomUrl}
              onChange={(event) => setConnection((current) => ({ ...current, roomUrl: event.target.value }))}
              placeholder="https://app.fantalab.it/asta?asta=…"
              disabled={active}
            />
          </label>
          <label className="field">
            <span className="field-label">Shard</span>
            <select
              className="select"
              value={connection.db}
              onChange={(event) => setConnection((current) => ({ ...current, db: event.target.value }))}
              disabled={active}
            >
              <option value="auto">Automatico</option>
              <option value="default">Default</option>
              {Array.from({ length: 20 }, (_, index) => <option value={index} key={index}>{index}</option>)}
            </select>
          </label>
          <button type="button" className={`btn ${active ? "" : "btn--primary"}`} onClick={() => active ? setActive(false) : connect()}>
            {active ? "Disconnetti" : "Collega"}
          </button>
        </div>
        {error ? <p className="notice notice--stop" role="alert">{error}</p> : null}
        {message ? <p className="notice notice--info" role="status">{message}</p> : null}
        {pendingPurchases ? (
          <p className="notice notice--stop" role="alert">
            {pendingPurchases} {pendingPurchases === 1 ? "acquisto è in attesa" : "acquisti sono in attesa"}: abbina qui sotto la squadra FantaLab anonima. Appena la scegli, giocatori e crediti vengono recuperati automaticamente.
          </p>
        ) : null}
        {roomNotice ? <p className="micro" role="status">{roomNotice}</p> : null}
        {snapshot ? (
          <p className="micro">
            {snapshot.room?.name || `Stanza ${shortId(snapshot.room_id)}`} · shard {snapshot.db}
            {` · ${participantCount} squadre · ${startingCredits} crediti`}
            {` · aggiornamento ogni ${(POLL_INTERVAL_MS / 1000).toLocaleString("it-IT")} s`}
            {lastUpdated ? ` · ultimo ${lastUpdated}` : ""}
          </p>
        ) : null}
      </section>

      {snapshot ? (
        <section className={`live-lot ${snapshot.lot?.closed ? "is-closed" : ""}`}>
          <div>
            <span className="kicker">Ora in asta</span>
            <strong>{snapshot.lot?.name || "In attesa della prossima chiamata"}</strong>
            {snapshot.lot?.team ? <span>{snapshot.lot.team} · {snapshot.lot.role || "ruolo n/d"}</span> : null}
          </div>
          {snapshot.lot ? (
            <div className="live-lot__numbers">
              <span><small>Prezzo</small><b>{snapshot.lot.price}</b></span>
              <span><small>Leader</small><b>{currentLeader}</b></span>
              <span><small>Timer</small><b>{timer == null ? "—" : timer.toFixed(1)}</b></span>
            </div>
          ) : null}
        </section>
      ) : null}

      {snapshot?.teams?.length ? (
        <section className="card stack">
          <div>
            <span className="kicker">{unmappedTeams.length ? "Da controllare" : "Sincronizzazione automatica"}</span>
            <h2>Squadre FantaLab riconosciute</h2>
            <p className="muted">FantaLab nasconde i nomi nella lettura pubblica: ogni nuovo ID viene assegnato automaticamente a una squadra libera e resta stabile. Se il nome locale non corrisponde, correggilo qui una sola volta.</p>
          </div>
          <div className="live-team-map">
            {snapshot.teams.map((team) => (
              <label className="field" key={team.id}>
                <span className="field-label">
                  {team.name || `Squadra FantaLab ${shortId(team.id)}`}
                  {teamActivity[team.id]?.length
                    ? ` · ${teamActivity[team.id].slice(-2).map((purchase) => `${purchase.name} ${purchase.price} cr`).join(", ")}`
                    : " · nessun acquisto"}
                </span>
                <select
                  className="select"
                  value={Number.isInteger(Number(connection.teamMap?.[team.id])) ? connection.teamMap[team.id] : ""}
                  onChange={(event) => updateMapping(team.id, event.target.value)}
                >
                  <option value="">Scegli squadra…</option>
                  {board.teams.map((local) => <option value={local.index} key={local.index}>{local.name}</option>)}
                </select>
              </label>
            ))}
          </div>
        </section>
      ) : null}

      <AuctionView
        data={data}
        openPlayer={openPlayer}
        rules={rules}
        profileId={profileId}
        draft={draft}
        setDraft={setDraft}
        liveOwnerIndex={liveOwnerIndex}
      />
    </div>
  );
}
