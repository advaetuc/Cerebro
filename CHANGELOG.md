# Changelog

All notable changes to Cerebro are documented here.

## [0.2.0] - 2026-10-10

### Added

- Family-share-based candidate allocation with anchor guarantees and genre MMR.
- Normalized anchor resolution across punctuation, accents, leading articles, TMDB original titles, and IGDB alternative names; duplicate matches select the most-popular record.
- Anchor recommendation filters and caps, family-specific Bayesian shrinkage, and borrowed game anchors for Indian music families.
- `python -m spike.explain_pick` diagnostic output for resolved anchor, provenance, filters, component scores, and slot decisions.
- Ranking harness checks for Hindi, Punjabi, Gully Boy, family shares, and cross-profile title repetition.
- Maintenance, development notes, and per-script spike documentation.
- Backend and frontend version metadata updated to 0.2.0.

### Fixed

- Root cause for Gully Boy missing from p03: the direct anchor had no protected family slot and incurred era/mainstream fit penalties, so global ranking could displace it. The diagnostic now resolves TMDB ID 491625; anchor treatment and family slot guarantees put it at rank 1.
- Punjabi anchor diagnosis: Jatt & Juliet resolves to ID 124380 and ranks first for p02; Udta Punjab resolves to ID 398535 but can still be displaced by the final slot/MMR selection.
- TMDB and IGDB anchor lookups now handle title variants and select the strongest duplicate match instead of relying on the first exact result.

### Known limitations

- The refreshed live harness passes the p06 no-picks-below-0.10 target.
- The live harness missed the cross-profile repeat target: `Tony Hawk's Pro Skater 2`, `Hustle & Flow`, `Def Jam: Fight for NY`, and `Need for Speed: Underground 2` recur across at least three profiles' top fives.
- This target miss is recorded without further tuning in this pass.

## [0.1.0] - 2026-10-06

### Added

- Last.fm-first Vibe Resolver, eight archetypes, and ten listening fixture profiles.
- Async TMDB and IGDB catalog clients, provider diagnostics, and catalog latency probes.
- FastAPI analyze/search/health API and the Next.js recommendation interface.
- Family-aware catalog profiles, candidate scoring, explanations, and initial ranking evaluation harness.
- Render and Vercel deployment configuration, CI, and provider attribution.
