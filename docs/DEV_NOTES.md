# Development notes

## Phase 0 outcomes

- GO: Last.fm-first is the product direction; Spotify was dropped because the development app required Premium.
- Last.fm tags are mostly genre, origin, and language; mood dimensions come from genre-family priors.
- Era runs oldest to newest; decade tags are low-confidence until MusicBrainz is considered.
- Catalog retrieval feasibility was established; provider latency and candidate overlap remain tracked performance limits.
- Ten cached listening profiles ground the archetype and ranking evaluation.
- Cached Phase 0 evidence: usable fallback tags covered 99.33% of fixture artists; archetype calibration matched 9/10 expected profiles and used six distinct primaries.
- The catalog spike reported roughly 129 ms cold movie p50 and 1.28 s cold game p50 on the recorded run; these are machine/provider dependent.

## Decisions and cuts

- Keep the prototype stateless: no Postgres, Redis, ARQ, authentication, or playlist export.
- Keep catalog pools in memory and under `CACHE_DIR`; use existing Last.fm response caches for offline profile fixtures.
- Use family-aware catalog retrieval, reviewed title anchors, Bayesian quality shrinkage, and genre-set MMR.
- Use deterministic explanation templates; no LLM ranking or explanation calls.
- Skip the Vibe Card and all additional UI work.
- The refreshed final harness passes p06 family-share constraints; repeated titles remain the sole failed harness target and are recorded without further tuning.

## Resume commands

Run these from the repository root unless noted:

```powershell
cd services/api
python -m pip install -e ".[dev]"
ruff check app spike tests
pytest
python -m spike.run_resolver
python -m spike.run_ranking --profiles spike/fixtures/profiles.json
python -m spike.explain_pick --profile p03 --title "Gully Boy"
python -m spike.verify_anchors
```

Frontend checks from `apps/web`: `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build`.

Set `LASTFM_API_KEY`, `TMDB_READ_TOKEN`, `TWITCH_CLIENT_ID`, and `TWITCH_CLIENT_SECRET` in `services/api/.env` for live provider calls. The ranking harness reuses Last.fm disk cache entries and calls TMDB/IGDB as needed.
