import { useEffect, useState } from "react";
import {
  Empty,
  Meter,
  PlayerRow,
  RoleChip,
  ROLE_LABELS,
  Sheet,
  formatTier,
} from "../ui.jsx";
import {
  isPlayerInjured,
  loadPlayerInjuries,
  savePlayerInjuries,
  withPlayerInjury,
} from "../player-injuries.js";
import { automaticInjury } from "../player-intelligence.jsx";

const TOP_TIERS = ["SUPER TOP", "TOP", "SEMITOP"];

/**
 * Landing screen. It answers "what is in this dataset and where do I start",
 * then hands over to the three working screens. Nothing here is a decision aid;
 * the auction screen owns that job.
 */
export default function OverviewView({ data, profileId, openPlayer, openTeam, openRole }) {
  const [injuries, setInjuries] = useState(() => loadPlayerInjuries(profileId));
  const [injuryManagerOpen, setInjuryManagerOpen] = useState(false);
  const [injuryQuery, setInjuryQuery] = useState("");
  const [injuryWarning, setInjuryWarning] = useState("");

  useEffect(() => {
    setInjuries(loadPlayerInjuries(profileId));
    setInjuryManagerOpen(false);
    setInjuryQuery("");
    setInjuryWarning("");
  }, [profileId]);

  const roleCounts = Object.keys(ROLE_LABELS).map((role) => ({
    role,
    count: data.players.filter((player) => player.ruolo === role).length,
  }));
  const top = data.players
    .filter((player) => TOP_TIERS.includes(formatTier(player.guida_asta_fascia)))
    .sort((a, b) => b.fvm_scaled - a.fvm_scaled)
    .slice(0, 8);
  const injured = data.players.filter(
    (player) => isPlayerInjured(injuries, player.id) || automaticInjury(player).active,
  );
  const matchdays = data.calendario_serie_a?.length
    ? Math.round(data.calendario_serie_a.length / 10)
    : null;
  const setPlayerInjured = (playerId, value) => {
    const next = withPlayerInjury(injuries, playerId, value);
    setInjuries(next);
    setInjuryWarning(
      savePlayerInjuries(profileId, next)
        ? ""
        : "Stato non salvato: la memoria del browser non è disponibile.",
    );
  };

  return (
    <div className="stack stack--lg">
      <section className="hero">
        <div className="stack">
          <span className="kicker">Database offline</span>
          <h1>Tutto il tuo fanta, in una vista sola.</h1>
          <p>
            Proiezioni, storico, guide, calendario e gerarchie sui piazzati.
            Durante l&apos;asta non serve rete.
          </p>
        </div>
        <div className="hero-figures">
          <div className="stat">
            <span className="stat-label">Giocatori</span>
            <span className="stat-value">{data.players.length}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Squadre</span>
            <span className="stat-value">{data.teams.length}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Giornate</span>
            <span className="stat-value">{matchdays ?? "n/d"}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Piazzati</span>
            <span className="stat-value">{data.set_pieces.length}</span>
          </div>
        </div>
      </section>

      <section>
        <div className="section-head">
          <h2>Il listone per reparto</h2>
          <span className="count">tocca per filtrare</span>
        </div>
        <div className="role-strip">
          {roleCounts.map((item) => (
            <button
              type="button"
              className="role-tile"
              key={item.role}
              onClick={() => openRole(item.role)}
            >
              <RoleChip role={item.role} />
              <b>{item.count}</b>
              <small>{ROLE_LABELS[item.role]}</small>
            </button>
          ))}
        </div>
      </section>

      <div className="overview-split stack">
        <section className="card card--flush">
          <div className="section-head" style={{ padding: "var(--s-4)", marginBottom: 0 }}>
            <div>
              <span className="kicker">Prime scelte</span>
              <h2>Valore più alto</h2>
            </div>
          </div>
          <div className="rows">
            {top.map((player, index) => (
              <PlayerRow
                key={player.id}
                player={player}
                rank={String(index + 1).padStart(2, "0")}
                value={player.fvm_scaled}
                valueLabel="valore"
                className="player-row"
                onClick={() => openPlayer(player)}
              />
            ))}
          </div>
        </section>

        <section className="card card--flush">
          <div className="section-head" style={{ padding: "var(--s-4)", marginBottom: 0 }}>
            <div>
              <span className="kicker">Da monitorare</span>
              <h2>Infortunati e dubbi</h2>
            </div>
            <div className="overview-card-actions">
              <span className="count">{injured.length}</span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setInjuryManagerOpen(true)}
              >
                Gestisci
              </button>
            </div>
          </div>
          {injured.length ? (
            <div className="rows">
              {injured.slice(0, 8).map((player) => (
                <PlayerRow
                  key={player.id}
                  player={player}
                  value={player.fvm_scaled}
                  valueLabel="valore"
                  className="player-row"
                  onClick={() => openPlayer(player)}
                />
              ))}
            </div>
          ) : (
            <Empty title="Nessun indisponibile segnalato">
              Usa Gestisci per aggiungere un avviso senza modificare valori o
              consigli d&apos;asta.
            </Empty>
          )}
        </section>
      </div>

      <InjuryManager
        open={injuryManagerOpen}
        onClose={() => setInjuryManagerOpen(false)}
        players={data.players}
        injured={injured}
        injuries={injuries}
        query={injuryQuery}
        setQuery={setInjuryQuery}
        setPlayerInjured={setPlayerInjured}
        warning={injuryWarning}
      />

      <section>
        <div className="section-head">
          <h2>Le venti di Serie A</h2>
          <span className="count">attacco / difesa</span>
        </div>
        <div className="club-grid">
          {data.teams.map((team) => (
            <button
              type="button"
              className="club-tile"
              key={team.squadra}
              onClick={() => openTeam(team.squadra)}
            >
              <strong>{team.squadra}</strong>
              <Meter
                label="ATT"
                value={team.rating_att}
                color="var(--c-role-a)"
              />
              <Meter
                label="DIF"
                value={team.rating_dif}
                color="var(--c-role-d)"
              />
              <small>
                {team.coppa_europea || (team.promossa ? "Neopromossa" : "—")}
              </small>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function InjuryManager({
  open,
  onClose,
  players,
  injured,
  injuries,
  query,
  setQuery,
  setPlayerInjured,
  warning,
}) {
  const normalizedQuery = query.trim().toLocaleLowerCase("it");
  const matches = normalizedQuery
    ? players
      .filter((player) =>
        `${player.nome} ${player.squadra}`.toLocaleLowerCase("it").includes(normalizedQuery),
      )
      .sort((a, b) => a.nome.localeCompare(b.nome, "it"))
      .slice(0, 30)
    : [];

  return (
    <Sheet open={open} onClose={onClose} title="Gestisci infortunati" wide>
      <div className="injury-manager stack">
        <div className="injury-manager-note">
          <strong>Solo promemoria</strong>
          <p>
            Le segnalazioni Scout compaiono automaticamente. Qui puoi aggiungere
            anche un promemoria manuale, separato per asta.
          </p>
        </div>

        <label className="injury-search">
          <span>Cerca giocatore</span>
          <input
            className="input"
            type="search"
            value={query}
            placeholder="Nome o squadra"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>

        {warning ? <p className="injury-manager-warning" role="alert">{warning}</p> : null}

        {normalizedQuery ? (
          <InjuryPlayerList
            title="Risultati"
            players={matches}
            injuries={injuries}
            setPlayerInjured={setPlayerInjured}
            empty="Nessun giocatore trovato."
          />
        ) : null}

        <InjuryPlayerList
          title={`Segnalati (${injured.length})`}
          players={injured}
          injuries={injuries}
          setPlayerInjured={setPlayerInjured}
          empty="Nessun giocatore segnalato. Cerca un giocatore per iniziare."
        />
      </div>
    </Sheet>
  );
}

function InjuryPlayerList({ title, players, injuries, setPlayerInjured, empty }) {
  return (
    <section className="injury-list">
      <div className="injury-list-title">
        <h3>{title}</h3>
      </div>
      {players.length ? (
        <div className="injury-list-rows">
          {players.map((player) => {
            const manual = isPlayerInjured(injuries, player.id);
            const detected = automaticInjury(player).active;
            const marked = manual || detected;
            return (
              <div className="injury-manager-row" key={player.id}>
                <RoleChip role={player.ruolo} />
                <span>
                  <strong>{player.nome}</strong>
                  <small>{player.squadra}</small>
                </span>
                <button
                  type="button"
                  className={`btn btn--sm${marked ? " btn--danger" : ""}`}
                  aria-pressed={marked}
                  disabled={detected && !manual}
                  onClick={() => setPlayerInjured(player.id, !manual)}
                >
                  {detected && !manual
                    ? "Da Scout"
                    : manual
                      ? "Rimuovi promemoria"
                      : "Segna infortunato"}
                </button>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="injury-list-empty">{empty}</p>
      )}
    </section>
  );
}
