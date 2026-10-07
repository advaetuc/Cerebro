# Last.fm-first pivot

This note amends the Cerebro blueprint for Phase 0. Spotify is dropped because
the developer app requires Premium and the project is using Free. No UI work
starts before the revised Phase 0 criteria are evaluated.

## Input and signals

- Input is a public Last.fm username using an API key, or artist picks in Seed Mode.
- T1 third-party audio features are parked. T2 Last.fm tags are primary.
- T3 genre priors remain the signal floor. T4 LLM tagging stays off.
- Last.fm periods map to blueprint ranges: short = `1month`, medium = `6month`,
  long = `overall`.
- Seed Mode artist search uses Last.fm `artist.search`.
- Era is inferred from decade tags at low confidence for now; MusicBrainz is a later option.
- Playlist export is removed. Spotify branding and Spotify ML-terms constraints no longer apply.

## Revised Phase 0 criteria

1. Verify the Last.fm endpoints used by the spike.
2. At least 80% of top artists have three or more usable cleaned tags.
3. Ten test profiles produce sensible archetypes.
4. TMDB and IGDB candidate retrieval each complete in under one second cold.
5. Read the current Last.fm, TMDB, and IGDB terms for intended use.

If criteria 1–3 fail, keep the product Seed-Mode-first and reassess the signal floor.
