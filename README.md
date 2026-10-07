Cerebro Phase 0 backend spike for checking Last.fm signal access.
Requires Python 3.12 and a Last.fm API key.
Copy `services/api/.env.example` to `services/api/.env` and set `LASTFM_API_KEY`.
Install with `python -m pip install -e "services/api[dev]"`.
From `services/api`, run `python -m spike.probe_lastfm --user NAME`.
