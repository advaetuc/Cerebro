# Spike scripts

- `probe_lastfm`: Last.fm user probe: `python -m spike.probe_lastfm --user NAME` or `--profiles PATH`.
- `probe_catalog`: TMDB/IGDB retrieval probe: `python -m spike.probe_catalog [--cold]`.
- `run_resolver`: resolve cached profiles: `python -m spike.run_resolver`.
- `run_ranking`: evaluate fixtures: `python -m spike.run_ranking --profiles spike/fixtures/profiles.json`.
- `explain_pick`: inspect a title's ranking path: `python -m spike.explain_pick --profile p03 --title "Gully Boy"`.
- `print_family_profiles`: print retrieval profile rows: `python -m spike.print_family_profiles`.
- `verify_anchors`: verify configured catalog anchors: `python -m spike.verify_anchors`.
- `lastfm_client`: Last.fm HTTP client module used by the probes; no standalone command.
