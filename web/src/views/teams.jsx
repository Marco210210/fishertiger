import { useRef, useState } from "react";
import { teamLogoUrl } from "../player-media.js";
import {
  Empty,
  Meter,
  PlayerRow,
  Segmented,
} from "../ui.jsx";

const TABS = [
  { value: "rosa", label: "Rosa" },
  { value: "calendario", label: "Calendario" },
  { value: "piazzati", label: "Piazzati" },
];

function ClubBadge({ team, large = false }) {
  const [failed, setFailed] = useState(false);
  const name = typeof team === "string" ? team : team?.squadra || "";
  const url = teamLogoUrl(typeof team === "string" ? name : team);
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();

  return (
    <span className={`club-badge${large ? " club-badge--large" : ""}`}>
      {url && !failed ? (
        <img
          src={url}
          alt=""
          width={large ? 56 : 32}
          height={large ? 56 : 32}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <b aria-hidden="true">{initials}</b>
      )}
    </span>
  );
}

const formatFixtureDate = (date) => {
  if (!date) return null;
  const parsed = new Date(`${date}T12:00:00`);
  return Number.isNaN(parsed.getTime())
    ? date
    : new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short" }).format(parsed);
};

/**
 * One club at a time. The club picker is a horizontal rail rather than a wrapped
 * block of twenty buttons, and rosa / calendario / piazzati are three tabs on
 * one club instead of two separate destinations in the main navigation.
 */
export default function TeamsView({
  data,
  selectedTeam,
  setSelectedTeam,
  openPlayer,
}) {
  const [tab, setTab] = useState("rosa");
  const [isDragging, setIsDragging] = useState(false);
  const chipRail = useRef({ active: false, moved: false, startX: 0, scrollLeft: 0 });
  const team =
    data.teams.find((item) => item.squadra === selectedTeam) || data.teams[0];
  const players = team.player_ids
    .map((id) => data.players.find((player) => player.id === id))
    .filter(Boolean)
    .sort(
      (a, b) => a.ruolo.localeCompare(b.ruolo) || b.fvm_scaled - a.fvm_scaled,
    );
  const pieces = data.set_pieces.filter(
    (piece) => piece.squadra === team.squadra,
  );

  return (
    <div className="stack">
      <div className="page-head">
        <span className="kicker">Serie A</span>
        <h1>Rose, calendario e piazzati</h1>
        <div className="availability-legend" aria-label="Legenda disponibilità giocatori">
          <span><i className="avail avail--good" aria-hidden="true" />Titolare</span>
          <span><i className="avail avail--caution" aria-hidden="true" />Ballottaggio</span>
          <span><i className="avail avail--muted" aria-hidden="true" />Riserva</span>
        </div>
      </div>

      <div
        className={`chip-rail${isDragging ? " is-dragging" : ""}`}
        onPointerDown={(event) => {
          if (event.pointerType === "mouse" && event.button !== 0) return;
          chipRail.current = {
            active: true,
            moved: false,
            startX: event.clientX,
            scrollLeft: event.currentTarget.scrollLeft,
          };
          setIsDragging(true);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const drag = chipRail.current;
          if (!drag.active) return;
          const distance = event.clientX - drag.startX;
          if (Math.abs(distance) > 4) drag.moved = true;
          event.currentTarget.scrollLeft = drag.scrollLeft - distance;
        }}
        onPointerUp={(event) => {
          chipRail.current.active = false;
          setIsDragging(false);
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={() => {
          chipRail.current.active = false;
          setIsDragging(false);
        }}
        onClickCapture={(event) => {
          if (chipRail.current.moved) {
            event.preventDefault();
            event.stopPropagation();
            chipRail.current.moved = false;
          }
        }}
      >
        {data.teams.map((item) => (
          <button
            type="button"
            key={item.squadra}
            className={`chip${item.squadra === team.squadra ? " is-active" : ""}`}
            onClick={() => setSelectedTeam(item.squadra)}
          >
            <ClubBadge team={item} />
            {item.squadra}
          </button>
        ))}
      </div>

      <section className="club-hero">
        <div>
          <ClubBadge team={team} large />
          <span className="kicker">
            {team.coppa_europea || (team.promossa ? "Neopromossa" : "Serie A")}
          </span>
          <h2>{team.squadra}</h2>
        </div>
        <div className="club-figures">
          <div className="stat">
            <span className="stat-label">Punti prec.</span>
            <span className="stat-value">{team.punti_prec}</span>
          </div>
          <div className="stat">
            <span className="stat-label">GF / GS</span>
            <span className="stat-value">
              {team.gf_prec} / {team.gs_prec}
            </span>
          </div>
          <div className="stat">
            <span className="stat-label">xG / xGA</span>
            <span className="stat-value">
              {team.xg_prec ?? "—"} / {team.xga_prec ?? "—"}
            </span>
          </div>
        </div>
        <div className="stack" style={{ gap: "var(--s-2)" }}>
          <Meter label="ATT" value={team.rating_att} color="var(--c-role-a)" />
          <Meter label="DIF" value={team.rating_dif} color="var(--c-role-d)" />
        </div>
      </section>

      <Segmented
        options={TABS}
        value={tab}
        onChange={setTab}
        label="Sezione della squadra"
      />

      {tab === "rosa" ? (
        <section className="card card--flush">
          <div className="section-head" style={{ padding: "var(--s-4)", marginBottom: 0 }}>
            <h2>Rosa nel listone</h2>
            <span className="count">{players.length} giocatori</span>
          </div>
          <div className="roster-grid roster-grid--wide" style={{ padding: "0 var(--s-2) var(--s-2)" }}>
            {players.map((player) => (
              <PlayerRow
                key={player.id}
                player={player}
                className="player-row"
                value={player.fvm_scaled}
                valueLabel="valore"
                onClick={() => openPlayer(player)}
              />
            ))}
          </div>
        </section>
      ) : null}

      {tab === "calendario" ? (
        <section className="card">
          <div className="section-head">
            <div className="fixture-heading">
              <h2>Il cammino della stagione</h2>
              <p className="micro">Casa e trasferta, giornata dopo giornata</p>
            </div>
            <span className="count">{team.fixtures.length} partite</span>
          </div>
          <div className="fixture-summary">
            <span><b>{team.fixtures.filter((fixture) => fixture.venue === "CASA").length}</b> in casa</span>
            <span><b>{team.fixtures.filter((fixture) => fixture.venue !== "CASA").length}</b> in trasferta</span>
            <span className="legend">
              <span>
                <i className="k-home" />
                Casa
              </span>
              <span>
                <i className="k-away" />
                Trasferta
              </span>
            </span>
          </div>
          <div className="fixture-list">
            {team.fixtures.map((fixture) => (
              <div
                key={fixture.matchday}
                className={`fixture-card${fixture.venue === "CASA" ? " is-home" : ""}`}
              >
                <div className="fixture-round">
                  <b>G{fixture.matchday}</b>
                  {formatFixtureDate(fixture.date) ? <small>{formatFixtureDate(fixture.date)}</small> : null}
                </div>
                <div className="fixture-opponent">
                  <ClubBadge team={{ squadra: fixture.opponent, team_id: fixture.opponent_team_id }} />
                  <strong>{fixture.opponent}</strong>
                </div>
                <span className="fixture-venue">{fixture.venue === "CASA" ? "Casa" : "Trasferta"}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {tab === "piazzati" ? (
        <section className="card">
          <div className="section-head">
            <h2>Gerarchie sui piazzati</h2>
          </div>
          {pieces.length ? (
            <div className="stack">
              {pieces.map((piece) => (
                <div key={piece.tipo}>
                  <span className="kicker">{piece.tipo}</span>
                  <div style={{ marginTop: 6 }}>
                    {piece.takers.length ? (
                      piece.takers.map((taker) => (
                        <button
                          type="button"
                          className="taker-line"
                          key={taker.player_id}
                          onClick={() =>
                            openPlayer(
                              data.players.find(
                                (player) => player.id === taker.player_id,
                              ),
                            )
                          }
                        >
                          <span
                            className={`taker-rank${taker.priorita === 1 ? " is-first" : ""}`}
                          >
                            {taker.priorita}
                          </span>
                          <span className="taker-name">{taker.nome}</span>
                        </button>
                      ))
                    ) : (
                      <p className="micro">Gerarchia aperta.</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="Nessuna gerarchia registrata" />
          )}
        </section>
      ) : null}
    </div>
  );
}

const TYPES = ["RIGORI", "PUNIZIONI", "CORNER"];

const TYPE_OPTIONS = [
  { value: "TUTTI", label: "Tutti" },
  ...TYPES.map((type) => ({ value: type, label: type[0] + type.slice(1).toLowerCase() })),
];

/** Set-piece board across the whole league, filterable by type. */
export function SetPiecesView({ data, openPlayer }) {
  const [type, setType] = useState("TUTTI");
  const visible = type === "TUTTI" ? TYPES : [type];

  return (
    <div className="stack">
      <div className="page-head">
        <span className="kicker">Specialisti</span>
        <h1>Rigori, punizioni e corner</h1>
        <p>
          Una gerarchia aperta non ha un primo designato: il modello evita di
          assegnargli un bonus che non è dimostrato.
        </p>
      </div>

      <Segmented
        options={TYPE_OPTIONS}
        value={type}
        onChange={setType}
        label="Tipo di piazzato"
      />

      <div className="setpiece-groups">
        {data.teams.map((team) => (
          <article className="card" key={team.squadra}>
            <div className="section-head">
              <h2 style={{ fontSize: "var(--fs-md)" }}>{team.squadra}</h2>
            </div>
            <div className="stack" style={{ gap: "var(--s-3)" }}>
              {visible.map((kind) => {
                const piece = data.set_pieces.find(
                  (item) => item.squadra === team.squadra && item.tipo === kind,
                );
                return (
                  <div key={kind}>
                    <span className="kicker">{kind}</span>
                    <div style={{ marginTop: 4 }}>
                      {piece?.takers.length ? (
                        piece.takers.map((taker) => (
                          <button
                            type="button"
                            className="taker-line"
                            key={taker.player_id}
                            onClick={() =>
                              openPlayer(
                                data.players.find(
                                  (player) => player.id === taker.player_id,
                                ),
                              )
                            }
                          >
                            <span
                              className={`taker-rank${taker.priorita === 1 ? " is-first" : ""}`}
                            >
                              {taker.priorita}
                            </span>
                            <span className="taker-name">{taker.nome}</span>
                          </button>
                        ))
                      ) : (
                        <p className="micro">Da definire</p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
