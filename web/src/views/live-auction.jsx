import { useEffect, useMemo, useRef, useState } from "react";

import { apiUrl } from "../profile-client.js";
import { syncLiveAssignments } from "../auction-store.js";
import { useAuctionBoard } from "../use-auction-store.js";
import {
  autoTeamMap,
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
  const [now, setNow] = useState(Date.now());
  const board = useAuctionBoard(profileId, data.players, rules);
  const teamMapRef = useRef(connection.teamMap);

  useEffect(() => {
    const next = readFantalabConnection(profileId);
    teamMapRef.current = next.teamMap;
    setConnection(next);
    setActive(false);
    setSnapshot(null);
    setError("");
    setMessage("");
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
        if (resolvedDb === "auto") resolvedDb = String(next.db);
        setSnapshot(next);
        setError("");

        const mapped = autoTeamMap(next.teams, board.teams, teamMapRef.current);
        if (JSON.stringify(mapped) !== JSON.stringify(teamMapRef.current)) {
          teamMapRef.current = mapped;
          setConnection((current) => {
            const saved = writeFantalabConnection(profileId, { ...current, teamMap: mapped });
            return saved.value;
          });
        }

        const sync = syncLiveAssignments(profileId, data.players, rules, next.purchases, mapped);
        if (sync.synced || sync.conflicts)
          setMessage(sync.message || `${sync.synced} acquisti sincronizzati.`);

        const player = data.players.find((candidate) =>
          String(candidate.id) === String(next.lot?.player_id),
        );
        if (player && !next.lot?.closed) {
          setDraft({
            playerId: player.id,
            query: player.nome,
            price: String(next.lot.price || rules.auction.minPrice),
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
        if (!cancelled && shouldContinue) timer = window.setTimeout(poll, 1200);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [active, connection.roomUrl, connection.db, apiBase, profileId, data.players, rules, board.teams, setDraft]);

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
    else nextMap[externalId] = Number(localIndex);
    saveConnection({ ...connection, teamMap: nextMap });
  };

  const timer = secondsRemaining(snapshot?.lot, snapshot?.server_time_ms, now);
  const teamNames = useMemo(
    () => Object.fromEntries((snapshot?.teams || []).map((team) => [team.id, team.name || shortId(team.id)])),
    [snapshot?.teams],
  );
  const currentLeader = teamNames[snapshot?.lot?.leader_team_id] || shortId(snapshot?.lot?.leader_team_id);
  const unmappedTeams = (snapshot?.teams || []).filter(
    (team) => !Number.isInteger(Number(connection.teamMap?.[team.id])),
  );

  return (
    <div className="stack stack--lg">
      <section className="card live-connect">
        <div className="live-connect__head">
          <div>
            <span className="kicker">FantaLab · sola lettura</span>
            <h1>Asta sincronizzata</h1>
            <p className="muted">Incolla il link della stanza. Fishertiger leggerà chiamata, prezzo e acquisti senza fare offerte al posto tuo.</p>
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
        {snapshot ? (
          <p className="micro">
            {snapshot.room?.name || `Stanza ${shortId(snapshot.room_id)}`} · shard {snapshot.db}
            {snapshot.room?.participants ? ` · ${snapshot.room.participants} squadre` : ""}
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

      {snapshot && unmappedTeams.length ? (
        <section className="card stack">
          <div>
            <span className="kicker">Una volta sola</span>
            <h2>Abbina le squadre FantaLab</h2>
            <p className="muted">Gli acquisti vengono importati appena ogni identificativo esterno è associato alla squadra corretta.</p>
          </div>
          <div className="live-team-map">
            {unmappedTeams.map((team) => (
              <label className="field" key={team.id}>
                <span className="field-label">{team.name || `Squadra FantaLab ${shortId(team.id)}`}</span>
                <select className="select" value="" onChange={(event) => updateMapping(team.id, event.target.value)}>
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
      />
    </div>
  );
}
