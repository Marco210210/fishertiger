import { useMemo } from "react";

import { sameAuctionRosters } from "../auction-simulation.js";

const percentage = (value) => `${(Number(value || 0) * 100).toFixed(1)}%`;

/**
 * Real-roster analysis. The old random auction and sample rosters deliberately
 * stay out of this screen: FantaLab's shared ledger is the only source that is
 * useful to the people running the actual auction.
 */
export default function SimulationView({
  season,
  data,
  onRerun,
  isSimulating,
  simulationStatus,
  auctionInput = {
    complete: false,
    reason: "Asta non disponibile.",
    report: [],
    assigned: 0,
    expectedAssignments: 0,
    rosters: null,
    aliases: {},
  },
}) {
  const report = auctionInput.report || [];
  const currentPrediction = Boolean(
    season?.meta?.roster_mode === "auction" &&
      auctionInput.complete &&
      sameAuctionRosters(season.rosters, auctionInput.rosters),
  );
  const sortedReport = useMemo(
    () => [...report].sort((a, b) => b.projectedValue - a.projectedValue || a.name.localeCompare(b.name)),
    [report],
  );
  const run = () => onRerun({ rosterMode: "auction", rosters: auctionInput.rosters });

  return (
    <div className="stack stack--lg">
      <div className="page-head">
        <span className="kicker">Dati reali FantaLab</span>
        <h1>Analisi delle rose</h1>
        <p>
          Confronta le rose importate dall&apos;asta condivisa. Qui non vengono
          inventati acquisti e non viene simulata un&apos;altra asta.
        </p>
      </div>

      <section className="roster-analysis-summary" aria-label="Stato dell'asta reale">
        <span><small>Giocatori importati</small><b>{auctionInput.assigned}/{auctionInput.expectedAssignments || "—"}</b></span>
        <span><small>Rose complete</small><b>{report.filter((team) => team.complete).length}/{report.length || "—"}</b></span>
        <span><small>Fonte</small><b>FantaLab live</b></span>
      </section>

      {sortedReport.length ? (
        <section className="roster-analysis-grid" aria-label="Confronto rose reali">
          {sortedReport.map((team, rank) => (
            <article className={`card roster-analysis-card${team.complete ? " is-complete" : " is-incomplete"}`} key={team.index}>
              <div className="roster-analysis-head">
                <span className="sim-medal">{rank + 1}</span>
                <div>
                  <h2>{team.name}</h2>
                  <p>{team.complete ? "Rosa completa" : `${team.rosterSize}/${team.expectedSize} giocatori`}</p>
                </div>
              </div>
              <div className="roster-analysis-numbers">
                <span><small>Valore rosa</small><b>{team.projectedValue}</b></span>
                <span><small>Spesi</small><b>{team.spent}</b></span>
                <span><small>Rimasti</small><b>{team.credits}</b></span>
              </div>
              <div className="roster-role-progress" aria-label={`Composizione di ${team.name}`}>
                {Object.entries(team.roles).map(([role, value]) => (
                  <span className={value.missing ? "is-missing" : ""} key={role}>
                    <b>{role}</b> {value.current}/{value.expected}
                  </span>
                ))}
              </div>
            </article>
          ))}
        </section>
      ) : (
        <div className="notice notice--warn">Le rose condivise non sono ancora disponibili.</div>
      )}

      <section className="card stack">
        <div className="section-head">
          <div>
            <span className="kicker">Funzione opzionale</span>
            <h2>Previsione della stagione</h2>
          </div>
        </div>
        <p className="muted">
          Serve solo dopo l&apos;asta: usa calendario, regole e rose reali per
          stimare probabilità di vittoria, podio e punti attesi. Non suggerisce
          offerte e non modifica nessuna rosa.
        </p>
        <div className={`notice notice--${auctionInput.complete ? "go" : "warn"}`} role="status">
          {auctionInput.complete
            ? "Tutte le rose sono complete: la previsione può essere calcolata."
            : auctionInput.reason}
        </div>
        <div className="btn-row" style={{ alignItems: "center" }}>
          <button
            type="button"
            className="btn btn--primary"
            onClick={run}
            disabled={isSimulating || !auctionInput.complete}
          >
            {isSimulating ? "Calcolo in corso…" : "Calcola sulle rose reali"}
          </button>
          {simulationStatus ? <span className="micro" role="status">{simulationStatus}</span> : null}
        </div>
      </section>

      {currentPrediction ? (
        <SeasonPrediction season={season} aliases={auctionInput.aliases} data={data} />
      ) : (
        <div className="card">
          <div className="empty">
            <h2>Previsione non calcolata</h2>
            <p>
              {auctionInput.complete
                ? "Premi il pulsante quando vuoi confrontare le possibilità delle rose reali."
                : "Il confronto delle rose qui sopra funziona già; la previsione si sblocca quando non manca più nessun giocatore."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function SeasonPrediction({ season, aliases, data }) {
  const rows = Object.entries(season.teams || {}).sort(
    ([, a], [, b]) => Number(b.rank_probabilities?.[0] || 0) - Number(a.rank_probabilities?.[0] || 0),
  );
  return (
    <section className="stack">
      <div className="section-head">
        <div>
          <span className="kicker">{season.iterations.toLocaleString("it-IT")} stagioni elaborate</span>
          <h2>Probabilità stimate</h2>
        </div>
        <span className="count">{data.calendario_lega?.matchdays?.length || "n/d"} giornate</span>
      </div>
      <div className="card card--flush">
        <div className="rows">
          {rows.map(([team, result], index) => (
            <div className="row sim-row" key={team}>
              <span className={`sim-medal${index < 3 ? " is-podium" : ""}`}>{index + 1}</span>
              <span className="row-main">
                <span className="row-title">{aliases?.[team] || team}</span>
                <span className="row-sub">
                  Podio {percentage(result.top3_probability)} · {Number(result.expected_points || 0).toFixed(1)} punti attesi
                </span>
              </span>
              <span className="player-metric">
                <b>{percentage(result.rank_probabilities?.[0])}</b>
                <small>vittoria</small>
              </span>
            </div>
          ))}
        </div>
      </div>
      <p className="micro">
        Sono stime probabilistiche basate sui dati disponibili, non una previsione certa della classifica finale.
      </p>
    </section>
  );
}
