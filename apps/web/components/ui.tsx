"use client";

import { motion, useReducedMotion } from "framer-motion";
import { useState } from "react";
import type { Artist, Pick } from "../lib/api";

export function AuroraBackdrop() {
  const reduce = useReducedMotion();
  return <div className="aurora" aria-hidden="true">
    <motion.i className="aurora-blob blob-cyan" animate={reduce ? undefined : { x: [0, 24, 0], y: [0, -16, 0] }} transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }} />
    <motion.i className="aurora-blob blob-pink" animate={reduce ? undefined : { x: [0, -20, 0], y: [0, 24, 0] }} transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }} />
    <motion.i className="aurora-blob blob-purple" animate={reduce ? undefined : { x: [0, 12, 0], y: [0, 18, 0] }} transition={{ duration: 25, repeat: Infinity, ease: "easeInOut" }} />
  </div>;
}

export function GlassPanel({ children, lite = false, className = "" }: { children: React.ReactNode; lite?: boolean; className?: string }) {
  return <section className={`glass-panel ${lite ? "glass-lite" : ""} ${className}`}>{children}</section>;
}

export function NeonBorder({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`neon-border ${className}`}><div>{children}</div></div>;
}

export function SignalMeter({ value }: { value: number }) {
  const segments = 20;
  return <div className="signal-wrap">
    <div className="signal-heading"><span>Taste confidence</span><strong>{Math.round(value)}%</strong></div>
    <div className="signal-meter" role="meter" aria-label="Taste confidence" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}>
      {Array.from({ length: segments }, (_, index) => <i key={index} className={index < Math.round(value / 5) ? "on" : ""} />)}
    </div>
  </div>;
}

const DIMENSIONS = ["energy", "valence", "acousticness", "danceability", "instrumentalness", "tempo", "era", "mainstream"] as const;

export function VectorBars({ vector }: { vector: Record<string, number> }) {
  return <div className="vector-grid">
    {DIMENSIONS.map((dimension) => {
      const value = Math.max(0, Math.min(1, vector[dimension] ?? 0));
      return <div className="vector-item" key={dimension}>
        <div className="vector-label"><span>{dimension}</span><span>{Math.round(value * 100)}</span></div>
        <div className="vector-track" role="meter" aria-label={dimension} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}><i style={{ transform: `scaleX(${value})` }} /></div>
      </div>;
    })}
  </div>;
}

export function ArtistChip({ artist, onRemove }: { artist: Artist; onRemove?: () => void }) {
  return <span className="artist-chip">{artist.name}{onRemove && <button type="button" onClick={onRemove} aria-label={`Remove ${artist.name}`}>×</button>}</span>;
}

export function PosterCard({ pick, kind }: { pick: Pick; kind: "movie" | "game" }) {
  const [failed, setFailed] = useState(false);
  return <article className="poster-card">
    <a href={pick.source_url} target="_blank" rel="noreferrer" className="poster-link" aria-label={`${pick.title}${pick.year ? `, ${pick.year}` : ""}`}>
      <div className="poster-image">
        {pick.image_url && !failed ? <img src={pick.image_url} alt={`${pick.title} ${kind} artwork`} loading="lazy" onError={() => setFailed(true)} /> : <div className="poster-fallback" aria-hidden="true"><span>{kind === "movie" ? "FILM" : "GAME"}</span></div>}
        {pick.regional && <span className="regional-badge">Regional</span>}
        <span className="match-badge">{pick.match_pct}% match</span>
      </div>
      <div className="poster-copy"><h3>{pick.title}</h3><span className="poster-year">{pick.year || "A new discovery"}</span><p>{pick.why}</p></div>
    </a>
  </article>;
}

export const ARCHETYPE_DESCRIPTIONS: Record<string, string> = {
  "Neon Insomniac": "High energy, low valence, high danceability — a taste for neo-noir, cyberpunk thrillers, stylish action, rhythm, and cyber RPGs.",
  "Golden Hour Dreamer": "High valence, high acoustic warmth, and mid energy — drawn to coming-of-age stories, romance, road movies, cozy worlds, and narrative indie.",
  "Static Saint": "Low energy and valence with a strong instrumental streak — contemplative cinema and quiet, immersive experiences.",
  "Velvet Rebel": "Mid energy, low valence, low acousticness, and an older-era pull — crime, character drama, immersive RPGs, and stealth.",
  "Solar Sprinter": "High energy, high valence, and high tempo — action-comedy, adventure, platformers, racing, and arcade.",
  "Hollow Wanderer": "Low energy and valence with high acousticness — quiet drama, folk-horror, narrative journeys, and survival-lite worlds.",
  "Chrome Romantic": "High valence and danceability with low acousticness — musicals, romantic comedy, retro-futurism, party, rhythm, and co-op.",
  "Echo Archivist": "A low-mainstream pull toward older eras — classics, cult cinema, documentaries, retro worlds, deep strategy, and niche indie.",
};
