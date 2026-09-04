import { useEffect, useState } from "react";

import { playerImageUrl, teamLogoUrl } from "./player-media.js";
import { scoutStatusLabel, scoutTone } from "./scout-ai.js";

const samePlayer = (left, right) => String(left) === String(right);

export const setPiecesForPlayer = (groups, playerId) =>
  (groups || []).flatMap((group) =>
    (group.takers || [])
      .filter((taker) => samePlayer(taker.player_id, playerId))
      .map((taker) => ({
        type: group.tipo,
        priority: Number(taker.priorita) || null,
        team: group.squadra,
      })),
  );

export const automaticInjury = (player) => {
  const scout = player?.scout_ai;
  const availability = String(player?.disponibilita?.status || "").toUpperCase();
  if (["out", "doubt", "monitor"].includes(scout?.status)) {
    return {
      active: true,
      status: scout.status,
      label: scoutStatusLabel(scout.status),
      note: scout.summary || scout.headline || player?.disponibilita?.nota || "",
    };
  }
  if (/INFORT|INDISPON|FUORI/.test(availability)) {
    return {
      active: true,
      status: "out",
      label: availability.replaceAll("_", " "),
      note: player?.disponibilita?.nota || "",
    };
  }
  return { active: false, status: "neutral", label: "Disponibile", note: "" };
};

function SafeImage({ src, className, size }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (!src || failed) return null;
  return (
    <img
      className={className}
      src={src}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}

export function PlayerPortrait({ player, large = false }) {
  return (
    <SafeImage
      className={`player-avatar${large ? " player-avatar--lg" : ""}`}
      src={playerImageUrl(player, large ? "medium" : "small")}
      size={large ? 72 : 32}
    />
  );
}

export function ClubCrest({ team, large = false }) {
  return (
    <SafeImage
      className={`team-logo${large ? " team-logo--lg" : ""}`}
      src={teamLogoUrl(team)}
      size={large ? 28 : 18}
    />
  );
}

const rate = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "—";
};

const pieceLabel = (piece) => {
  const name = String(piece.type || "Piazzato").toLocaleLowerCase("it");
  return `${name.charAt(0).toLocaleUpperCase("it")}${name.slice(1)}${piece.priority ? ` · ${piece.priority}ª scelta` : ""}`;
};

/** Compact, shared intelligence block used by both the list and live auction. */
export function PlayerSignals({ player, setPieces = [], compact = false }) {
  const injury = automaticInjury(player);
  const scout = player?.scout_ai;
  const events = player?.event_rates || {};
  return (
    <div className={`player-signals${compact ? " player-signals--compact" : ""}`}>
      <div className="player-signals__grid">
        <div><small>Quotazione</small><b>{player?.quotazioni?.attuale ?? "—"}</b></div>
        <div><small>Fantavoto atteso</small><b>{Number(player?.proiezione?.fantavoto || 0).toFixed(2)}</b></div>
        <div><small>Gol / 90</small><b>{rate(events.gol)}</b></div>
        <div><small>Assist / 90</small><b>{rate(events.assist)}</b></div>
      </div>

      {setPieces.length ? (
        <div className="player-signals__pieces" aria-label="Gerarchie sui piazzati">
          <strong>Piazzati</strong>
          {setPieces.map((piece) => (
            <span className="pill pill--brand" key={`${piece.type}:${piece.priority}`}>
              {pieceLabel(piece)}
            </span>
          ))}
        </div>
      ) : (
        <p className="micro">Nessuna gerarchia rilevata su rigori, punizioni o corner.</p>
      )}

      {injury.active && !scout ? (
        <div className={`notice notice--${scoutTone(injury.status)}`}>
          <b>{injury.label}</b>
          {injury.note ? <p>{injury.note}</p> : null}
        </div>
      ) : null}

      {scout ? (
        <div className={`notice notice--${scoutTone(scout.status)} scout-card`}>
          <div className="scout-card__head">
            <b>Scout AI · {scoutStatusLabel(scout.status)}</b>
            <span>{scout.impact_percent > 0 ? "+" : ""}{scout.impact_percent}% sul valore</span>
          </div>
          <strong>{scout.headline}</strong>
          <p>{scout.summary}</p>
          {scout.sources?.length ? (
            <span className="scout-card__sources">
              {scout.sources.map((source, index) => (
                <a href={source} target="_blank" rel="noreferrer" key={source}>Fonte {index + 1}</a>
              ))}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
