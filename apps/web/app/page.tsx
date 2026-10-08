"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { analyze, searchArtists, type Artist, type VibeResult } from "../lib/api";
import { ARCHETYPE_DESCRIPTIONS, ArtistChip, AuroraBackdrop, GlassPanel, NeonBorder, PosterCard, SignalMeter, VectorBars } from "../components/ui";

type Mode = "lastfm" | "seed";
type ViewState = { status: "idle" } | { status: "loading" } | { status: "result"; result: VibeResult } | { status: "error"; message: string };
const STAGES = ["reading listening data", "resolving genres", "finding your archetype", "searching film and game catalogs", "ranking picks"];
const ARCHETYPE_ACCENTS: Record<string, string> = {
  "Neon Insomniac": "var(--cyan)", "Golden Hour Dreamer": "var(--pink)", "Static Saint": "var(--lavender)", "Velvet Rebel": "var(--pink)",
  "Solar Sprinter": "var(--mint)", "Hollow Wanderer": "var(--lavender)", "Chrome Romantic": "var(--cyan)", "Echo Archivist": "var(--pink)",
};

export default function Home() {
  const [mode, setMode] = useState<Mode>("lastfm");
  const [state, setState] = useState<ViewState>({ status: "idle" });
  const [username, setUsername] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Artist[]>([]);
  const [selected, setSelected] = useState<Artist[]>([]);
  const [searching, setSearching] = useState(false);
  const [stage, setStage] = useState(0);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    try {
      const cached = sessionStorage.getItem("cerebro:last-result");
      if (cached) setState({ status: "result", result: JSON.parse(cached) as VibeResult });
    } catch { sessionStorage.removeItem("cerebro:last-result"); }
  }, []);

  useEffect(() => {
    if (mode !== "seed" || query.trim().length < 2) { setResults([]); return; }
    let active = true;
    const timer = window.setTimeout(() => {
      setSearching(true);
      searchArtists(query.trim()).then((data) => { if (active) setResults(data.artists); })
        .catch(() => { if (active) setResults([]); })
        .finally(() => { if (active) setSearching(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [mode, query]);

  useEffect(() => {
    if (state.status !== "loading") return;
    const timer = window.setInterval(() => setStage((current) => (current + 1) % STAGES.length), 900);
    return () => window.clearInterval(timer);
  }, [state.status]);

  const canSubmit = useMemo(() => mode === "lastfm" ? username.trim().length > 0 : selected.length === 3, [mode, username, selected]);

  function addArtist(artist: Artist) {
    if (selected.length >= 3 || selected.some((item) => item.name.toLowerCase() === artist.name.toLowerCase())) return;
    setSelected((current) => [...current, artist]);
    setQuery("");
    setResults([]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || state.status === "loading") return;
    setState({ status: "loading" });
    setStage(0);
    try {
      const result = await analyze(mode === "lastfm"
        ? { mode, username: username.trim() }
        : { mode, artists: selected.map((artist) => artist.name) });
      sessionStorage.setItem("cerebro:last-result", JSON.stringify(result));
      setState({ status: "result", result });
    } catch (error) {
      setState({ status: "error", message: error instanceof Error ? error.message : "Something went wrong. Try again." });
    }
  }

  function reset() {
    sessionStorage.removeItem("cerebro:last-result");
    setState({ status: "idle" });
  }

  const result = state.status === "result" ? state.result : null;
  const accent = result ? ARCHETYPE_ACCENTS[result.archetype] || "var(--cyan)" : "var(--cyan)";

  return <main className="site-shell" style={{ "--accent": accent } as React.CSSProperties}>
    <AuroraBackdrop />
    <header className="topbar"><a className="wordmark" href="/" onClick={(event) => { event.preventDefault(); reset(); }}>CEREBRO<span> / VIBE SYSTEM</span></a><span className="topbar-index">BETA</span></header>
    <AnimatePresence mode="wait">
      {state.status === "loading" ? <motion.section key="loading" className="loading-view" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
        <div className="loading-orbit" aria-hidden="true"><i /><i /><i /></div>
        <p className="eyebrow">ANALYSIS THEATER</p><h1>Finding your <em>shape.</em></h1>
        <div className="stage-list" aria-live="polite">{STAGES.map((label, index) => <div key={label} className={`stage-line ${index === stage ? "active" : index < stage ? "complete" : ""}`}><span className="stage-mark">{index < stage ? "✓" : `0${index + 1}`}</span><span>{label}</span>{index === stage && <motion.i className="stage-progress" animate={reduceMotion ? undefined : { scaleX: [0.08, 1] }} transition={{ duration: 0.88, ease: "easeInOut" }} />}</div>)}</div>
        <p className="loading-note">Your listening data stays yours.</p>
      </motion.section> : result ? <motion.div key="result" className="result-view" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
        <section className="result-hero">
          <p className="eyebrow">YOUR LISTENING ARCHETYPE</p>
          <h1 className="archetype-title">{result.archetype}</h1>
          <p className="archetype-description">{ARCHETYPE_DESCRIPTIONS[result.archetype] || "A distinct listening profile shaped by the sounds you return to."}</p>
          <p className="secondary-trace">TRACES OF <strong>{result.secondary}</strong></p>
        </section>
        <div className="result-layout">
          <div className="result-main">
            <GlassPanel className="vector-panel"><div className="section-title"><div><p className="eyebrow">YOUR VIBE VECTOR</p><h2>Eight dimensions</h2></div><span className="mono-note">8 DIMENSIONS</span></div><VectorBars vector={result.vector} /></GlassPanel>
            <section className="recommendation-section"><div className="section-title"><div><p className="eyebrow">01 / THE BIG SCREEN</p><h2>Films in your orbit</h2></div><span className="section-count">{result.movies.length} PICKS</span></div>{result.movies.length > 0 ? <div className="poster-grid">{result.movies.map((pick) => <PosterCard key={`movie-${pick.id}`} pick={pick} kind="movie" />)}</div> : <p className="empty-picks">Movies are unavailable right now. Try again in a minute.</p>}</section>
            <section className="recommendation-section"><div className="section-title"><div><p className="eyebrow">02 / THE PLAYABLE</p><h2>Worlds to step into</h2></div><span className="section-count">{result.games.length} PICKS</span></div>{result.games.length > 0 ? <div className="poster-grid">{result.games.map((pick) => <PosterCard key={`game-${pick.id}`} pick={pick} kind="game" />)}</div> : <p className="empty-picks">Games are unavailable right now. Try again in a minute.</p>}</section>
          </div>
          <aside className="result-side">
            <GlassPanel className="signal-panel"><SignalMeter value={result.signal_pct} /><p className="signal-explainer">Based on genre tags from your top artists. More listening history raises it.</p></GlassPanel>
            <GlassPanel lite className="families-panel"><p className="eyebrow">TOP GENRE FAMILIES</p><div className="family-chips">{result.top_families.map((family) => <span className="family-chip" key={family.id}>{family.id.replaceAll("-", " ")}<small>{Math.round(family.share * 100)}%</small></span>)}</div></GlassPanel>
            {result.degraded && <div className="degraded-note" role="status"><i /> Some catalog filters didn&apos;t load, so picks may be broader.</div>}
            <button className="button button-quiet try-again" onClick={reset}>Try again <span aria-hidden="true">↗</span></button>
          </aside>
        </div>
        <footer className="attribution">{result.attribution.map((line) => <p key={line}>{line}</p>)}</footer>
      </motion.div> : <motion.section key="landing" className="landing-view" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
        <div className="landing-copy"><p className="eyebrow"><span className="live-dot" /> A MIRROR FOR YOUR TASTE</p><h1>Find the shape<br />of your <em>sound.</em></h1><p className="pitch">Your listening history has a cinematic side. Discover the films and games that move in your frequency.</p><div className="landing-footnote"><span>01</span><p>Music in. Mood out.<br /><strong>A little more you, everywhere.</strong></p></div></div>
        <NeonBorder className="form-frame"><GlassPanel className="input-panel"><div className="panel-heading"><span className="eyebrow">BUILD YOUR PROFILE</span></div>
          <div className="mode-switch" role="group" aria-label="Choose your input mode"><button type="button" aria-pressed={mode === "lastfm"} className={mode === "lastfm" ? "selected" : ""} onClick={() => setMode("lastfm")}>Last.fm username</button><button type="button" aria-pressed={mode === "seed"} className={mode === "seed" ? "selected" : ""} onClick={() => setMode("seed")}>Pick 3 artists</button></div>
          <form onSubmit={submit}>
            {mode === "lastfm" ? <div className="field-wrap"><label htmlFor="username">Your public Last.fm username</label><div className="input-shell"><span className="input-prefix">@</span><input id="username" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="your_username" autoComplete="username" /></div><p className="field-hint">We only read public listening data.</p></div> : <div className="field-wrap"><label htmlFor="artist-search">Choose three artists <span>({selected.length}/3)</span></label><div className="selected-artists">{selected.map((artist) => <ArtistChip key={artist.name} artist={artist} onRemove={() => setSelected((items) => items.filter((item) => item.name !== artist.name))} />)}</div><div className="input-shell"><span className="search-icon" aria-hidden="true">⌕</span><input id="artist-search" role="combobox" aria-autocomplete="list" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={selected.length === 3 ? "Three artists selected" : "Search an artist"} disabled={selected.length === 3} autoComplete="off" aria-expanded={results.length > 0} aria-controls="artist-results" />{searching && <span className="searching-dot" aria-label="Searching" />}</div>{results.length > 0 && <ul className="artist-results" id="artist-results" role="listbox" aria-label="Artist search results">{results.map((artist) => <li key={`${artist.name}-${artist.listeners}`}><button type="button" role="option" aria-selected="false" onClick={() => addArtist(artist)}><span>{artist.name}</span><small>{artist.listeners ? `${Number(artist.listeners).toLocaleString()} listeners` : "Last.fm artist"}</small></button></li>)}</ul>}<p className="field-hint">Search and select exactly three. Any mix works.</p></div>}
            {state.status === "error" && <p className="form-error" role="alert">{state.message}</p>}
            <button className="button button-primary" type="submit" disabled={!canSubmit}>Reveal my vibe <span aria-hidden="true">↗</span></button>
          </form><p className="privacy-note"><span>◇</span> No account needed. Your data isn’t stored.</p>
        </GlassPanel></NeonBorder>
      </motion.section>}
    </AnimatePresence>
    <footer className="site-footer"><span>© CEREBRO / AN EXPERIMENT IN TASTE</span><span>DESIGNED FOR THE FREQUENCIES BETWEEN</span></footer>
  </main>;
}
