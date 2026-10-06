Cerebro Phase 0 backend spike for checking Spotify API access.
Requires Python 3.12 and a Spotify developer app client ID.
Copy `services/api/.env.example` to `services/api/.env` and set the client ID.
Install with `python -m pip install -e "services/api[dev]"`.
From `services/api`, run `python -m spike.probe_spotify`.
