import { useMemo, useState } from "react";

import { PlayerRow } from "../ui.jsx";
import { scoutStatusLabel, scoutTone } from "../scout-ai.js";

export default function ScoutAiView({ data, snapshot, openPlayer }) {
  const [filter, setFilter] = useState("all");
  const statusCounts = useMemo(() => {
    const counts = { all: 0, out: 0, doubt: 0, monitor: 0, positive: 0 };
    const playerIds = new Set(data.players.map((player) => String(player.id)));
    for (const reading of Object.values(snapshot?.players || {})) {
      if (!playerIds.has(String(reading.player_id))) continue;
      counts.all += 1;
      if (reading.status in counts) counts[reading.status] += 1;
    }
    return counts;
  }, [data.players, snapshot]);
  const rows = useMemo(() => {
    const byId = new Map(data.players.map((player) => [String(player.id), player]));
    return Object.values(snapshot?.players || {})
      .map((reading) => ({ reading, player: byId.get(String(reading.player_id)) }))
      .filter(({ player, reading }) => player && (filter === "all" || reading.status === filter))
      .sort((left, right) => left.reading.impact_percent - right.reading.impact_percent);
  }, [data.players, snapshot, filter]);

  return (
    <div className="stack stack--lg scout-page">
      <div className="page-head">
        <span className="kicker">Scout · notizie verificate</span>
        <h1>Scout AI</h1>
        <p>Qui compaiono solo notizie capaci di cambiare davvero un prezzo: infortuni, rientri, titolarità, mercato e gerarchie sui piazzati.</p>
      </div>

      <div className="scout-summary card">
        <div><small>Aggiornato</small><b>{snapshot?.generated_at?.slice(0, 16).replace("T", " ") || "non ancora"}</b></div>
        <div><small>Provider</small><b>{snapshot?.provider || "nessuno"}</b></div>
        <div><small>Segnalazioni</small><b>{Object.keys(snapshot?.players || {}).length}</b></div>
      </div>

      <div className="segmented scout-filter" role="group" aria-label="Filtra notizie AI">
        {[["all", "Tutte"], ["out", "Fuori"], ["doubt", "Dubbi"], ["monitor", "Monitor"], ["positive", "Positive"]].map(([value, label]) => (
          <button type="button" key={value} className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>{label} <b>{statusCounts[value]}</b></button>
        ))}
      </div>

      {rows.length ? (
        <div className="rows scout-rows">
          {rows.map(({ player, reading }) => (
            <div className="scout-row" key={player.id}>
              <PlayerRow player={player} onClick={() => openPlayer(player)} value={`${reading.impact_percent > 0 ? "+" : ""}${reading.impact_percent}%`} valueLabel="impatto" />
              <span className={`pill pill--${scoutTone(reading.status)}`}>{scoutStatusLabel(reading.status)}</span>
              <span className="scout-row__copy"><b>{reading.headline}</b>{reading.summary}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="card">
          <p className="muted">Nessuna segnalazione Scout disponibile per questo filtro. Il motore d'asta continua a usare dati e proiezioni normali.</p>
        </div>
      )}
    </div>
  );
}
