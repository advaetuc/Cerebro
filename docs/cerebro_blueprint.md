# CEREBRO — Blueprint v1.0

> *Your taste has a signal. Cerebro decodes it into films and games.*

**Status:** Pre-code architecture. No source code is specified here, only contracts, structures, and decisions.
**Supersedes:** the name options (Resonance / VibeShift / Synapse.fm) and the rule-based mapping sketch in `spotify_culture_research.md`. The project name is **Cerebro**.
**Audience:** whoever writes the code (human or coding agent). If this file and a verbal instruction disagree, update this file first.

---

## 0. READ THIS FIRST: Platform Reality Check (changes the architecture)

The original concept assumed Spotify's **Audio Features** (energy, valence, acousticness, danceability) are available per track. As of this blueprint's date (Oct 2026) that is **no longer true for newly created Spotify apps**:

| Spotify capability | Status for new apps | Impact on Cerebro |
|---|---|---|
| `GET /audio-features`, `GET /audio-analysis` | Removed Nov 27, 2024 (returns 403) | **Core assumption broken.** Need a replacement signal layer (see §3.3). |
| `GET /recommendations`, related artists, editorial playlists | Removed for new apps | Not needed; Cerebro maps to TMDB/IGDB, not to Spotify. |
| `preview_url` (30s clips) | `null` for new apps | Cannot self-analyze audio from Spotify previews. |
| Development Mode (overhauled Feb 6, 2026) | Owner needs Premium; search capped at 10 results; several catalog/popularity fields removed; very small user allowlist | Prototype only. Public launch needs Extended Quota approval. |
| Extended Quota Mode | Requires a registered business and meaningful scale; approval is not guaranteed | **Launch gating risk.** Plan for it from day one (§12). |

**Consequences baked into this blueprint**

1. A **Vibe Resolver** (§3.3) builds the "Vibe Vector" from *multiple sources with confidence weights* instead of one Spotify call.
2. A **Seed Mode** (no Spotify login; user picks 3 artists) exists so the product works, and can go viral, even before Extended Quota approval.
3. **Phase 0 is a Go/No-Go spike** (§11) that must pass before any UI work begins.
4. Spotify's Developer Terms restrict using Spotify content to train ML/AI models, and restrict caching/branding. The ML roadmap therefore trains on **Cerebro's own first-party feedback** (likes, skips, saves), not on Spotify data. **Legal review of the current Developer Terms is a launch blocker.**

> Policies move quickly. Every item in this section must be re-verified on developer.spotify.com on the day Phase 0 starts.

---

## 1. Executive Summary & Feature Matrix

### 1.1 Product
Cerebro links a user's music identity (Spotify, or manually chosen artists) and returns a **Vibe Profile** (an 8-dimension vector plus an archetype) and **tailored movie and game recommendations** with a transparent *"why this matches you"* explanation. Output is designed to be screenshot-worthy and blendable with friends.

**One-line pitch:** *Spotify Wrapped, but it tells you what to watch and play tonight.*

### 1.2 Virality Engine (what actually drives growth)

| Mechanic | Loop | Why it spreads |
|---|---|---|
| **Vibe Card** (archetype + radar + top 3 picks) | Reveal, share, friend taps link, friend previews in Seed Mode, friend connects, friend shares | Identity-signaling artifact; each card carries a short link + QR |
| **Vibe Blend** | User A creates invite, User B joins, shared compatibility score and "Double Feature" picks, both share | Requires a second person by design (built-in invite) |
| **Archetypes** (8 at MVP, 12 later) | Reveal moment ("You are a *Neon Insomniac*") | Tribal labels get shared and argued about |
| **Signal Streak + Daily Signal** | One pick per day, streak counter, push/email opt-in | Retention habit loop |
| **Vibe Seasons** (monthly snapshots) | "Your vibe drifted 14% toward melancholy this month" | Recurring re-share trigger |
| **Reverse Lens** (Phase 2) | Pick a film/game and see which of your artists "sound like it" | Debate fuel; also generates labeled training data |
| **Roast Mode** (toggle on Vibe Card) | Playful, non-cruel one-liner about taste | High share rate for humor-driven cards |

### 1.3 Feature Matrix

| Feature | MVP (Ph 1) | Growth (Ph 2) | Scale/ML (Ph 3) |
|---|:--:|:--:|:--:|
| Spotify OAuth 2.0 + PKCE, consent toggles, disconnect/delete | ✅ | | |
| **Seed Mode** (3 artists, no login) | ✅ | | |
| Vibe Resolver (multi-source) + Signal Strength meter | ✅ | | |
| Audio DNA dashboard (radar, top genres/artists) | ✅ | | |
| Archetype reveal (8) | ✅ | 12 | |
| Recommendations: Movies (TMDB) + Games (IGDB) | ✅ | | |
| Match Confidence + "Why" explanation (template-based) | ✅ | LLM-polished | |
| Swipe Deck feedback (Tune In / Skip / Seen) | ✅ | | |
| Watchlist | ✅ | | |
| Vibe Card sharing (OG image + PNG export + Web Share) | ✅ | | |
| Aurora wave visualizer reacting to the vector | ✅ | | |
| Signal Streak, XP, badges, Daily Signal | | ✅ | |
| Vibe Blend (2 users) | | ✅ | Group blend (up to 6) |
| Vibe Seasons (history/drift) | | ✅ | |
| Export "Cinematic Night" playlist back to Spotify | | ✅ | |
| Reverse Lens | | ✅ | |
| Weekly Vibe Forecast email + release alerts | | ✅ | |
| Learned re-ranker trained on first-party feedback | | | ✅ |
| Embeddings / two-tower retrieval | | | ✅ |
| Natural-language search ("movie that feels like my Late Night Drive") | | | ✅ |
| Apple Music / Tidal / YouTube Music / Last.fm ingestion | | | ✅ |
| Streaming deep-links (Netflix, Max, Steam, PSN, Xbox) | | Partial | ✅ |
| Cerebro Wrapped (annual) | | | ✅ |

---

## 2. Creative Direction: "Lucid Cybernetics"

### 2.1 Design principles
1. **Airy over dense.** Near-black canvas, generous spacing, soft light. Tech detail is *seasoning*, not the meal.
2. **Light has a source.** Glow comes from the aurora behind glass; panels catch it on their top-left edge.
3. **Data is alive.** Waves, glows, and motion are driven by the user's Vibe Vector, never decorative randomness.
4. **Legible first.** Every text pairing meets WCAG AA; effects never carry meaning alone.

### 2.2 Color tokens

| Token | Hex | Use | Contrast on `--void` (approx.) |
|---|---|---|---|
| `--void` | `#05060A` | Page background | n/a |
| `--abyss` | `#0A0B14` | Section backgrounds | n/a |
| `--surface` | `#0F1020` | Opaque fallback panels | n/a |
| `--text-hi` | `#F4F6FF` | Headlines, key numbers | ~18:1 |
| `--text-md` | `#B8BEDC` | Body | ~10:1 |
| `--text-lo` | `#8F95B8` | Captions (≥14px only) | ~6:1 |
| `--cyan` | `#00E5FF` | Primary accent, links, focus ring | ~13:1 |
| `--mint` | `#A7FFE9` | Positive / success glow | ~17:1 |
| `--pink` | `#FF9AD5` | Secondary accent, highlights | ~11:1 |
| `--lavender` | `#C4B5FD` | Tertiary text accent | ~10:1 |
| `--purple` | `#6D28D9` | **Decorative only** (gradients, borders, glows) | ~2.5:1, **never for text** |
| `--violet-deep` | `#3B1A8F` | Aurora base blobs | decorative |
| `--danger` | `#FF6B8A` | Errors | ~8:1 |

**Aurora gradient stops (animated blobs):** `#00E5FF` @ 28% opacity, `#6D28D9` @ 40%, `#FF9AD5` @ 22%, plus `#3B1A8F` @ 50% as the base wash.
**Neon border gradient:** `linear-gradient(135deg, rgba(0,229,255,.7), rgba(109,40,217,.4) 50%, rgba(255,154,213,.6))`.
> Re-verify every pairing with an automated contrast check in CI (axe + Storybook). Text over glass is measured against the *brightest* aurora region behind it, not the base color.

### 2.3 Typography
- **Display & UI:** Geist Sans (or Manrope as alternate), weights 300–600, tight tracking on headlines.
- **Tech symbology / numerals / labels:** Geist Mono (or JetBrains Mono), uppercase micro-labels at 11–12px with +0.08em tracking.
- Loaded via `next/font` (self-hosted, `display: swap`, subsetted).
- Glow: `text-shadow` of 0 0 12px at ~35% accent color on **headlines and big numerals only**. Body text gets no glow.
- Scale (px): 12 / 14 / 16 / 20 / 28 / 40 / 64 (hero, fluid via `clamp`).

### 2.4 Glass system

| Element | Spec |
|---|---|
| **GlassPanel (standard)** | `background: rgba(255,255,255,0.06)`, `backdrop-filter: blur(16px) saturate(140%)`, `1px` border `rgba(255,255,255,0.12)`, inner top-left highlight `inset 1px 1px 0 rgba(255,255,255,0.18)`, radius 20px |
| **GlassPanel (lite)** for lists and cards in scroll areas | No backdrop-filter. `rgba(15,16,32,0.72)` + same border. Looks identical over the dark canvas, costs nothing |
| **Mobile** | blur reduced to 10px; max 3 live blurred surfaces on screen |
| **Fallbacks** | `@supports not (backdrop-filter)` and `prefers-reduced-transparency` → opaque `--surface` at 0.92 alpha |
| **NeonBorder** | 1px gradient border via mask technique; hover raises brightness and spawns a travelling light along the edge (CSS `@property` angle animation) |
| **Grid horizon** | Subtle perspective grid (cyan at 8% opacity) fading into the aurora at the bottom of hero and reveal screens; drawn as a single pre-rendered SVG/CSS layer |
| **Depth layers (z)** | 0 void → 1 aurora → 2 grid horizon → 3 noise (2% grain) → 10 content → 20 glass panels → 30 popovers → 40 toasts → 50 modals |

**GPU rules (non-negotiable):** animate only `transform` and `opacity`; the aurora is one fixed, pre-composited layer (never blurred per card); pause all canvas/rAF work on `visibilitychange: hidden`; cap canvas DPR at 1.5; no more than 4 backdrop-blurred surfaces simultaneously visible.

### 2.5 Vibe Vector → Visual mapping (the "alive" part)

| Vector dimension | Drives |
|---|---|
| Energy | Wave amplitude, aurora drift speed |
| Valence | Hue bias (low → cyan/violet, high → pink/mint), glow warmth |
| Acousticness | Wave smoothness (high = silky sine, low = harmonic-rich/jagged with slight glitch) |
| Danceability | Wave rhythm regularity, bounce in micro-interactions |
| Instrumentalness | Opacity of "lyric noise" particles around the wave |
| Tempo | Oscillation frequency |
| Era | Grain/VHS texture intensity (older → more grain) |
| Mainstream | Brightness/crispness of card borders |

**SignalWave** renders on a single `<canvas>` (2D, path-based) at ≤60fps, throttled to 30fps on low-power devices. Under `prefers-reduced-motion` it renders one static frame.

### 2.6 Animation choreography

**Springs (Framer Motion):** `hover = {stiffness: 260, damping: 22, mass: 0.9}`, `press = {stiffness: 500, damping: 30}`, `layout = {stiffness: 180, damping: 26}`. Durations for non-spring: 180ms (micro), 320ms (panel), 600ms (scene).

| Scene | Timeline |
|---|---|
| **Landing load** | 0ms aurora fades in (600ms) → 150ms grid horizon draws bottom-up → 300ms headline staggers by word (40ms apart) → 700ms Connect button springs in, then idles with a slow edge-light sweep |
| **Connect click** | Button compresses (press spring) → ring pulse expands → route to Spotify; on return, "Neural link established" tick |
| **Analysis Theater** (loading as spectacle, driven by real SSE events) | Stage ticker: *Reading signal → Resolving vibes → Mapping to cinema → Mapping to games → Composing*. Each stage lights a node on a circuit line; the wave in the background fills in progressively as real data arrives. Never a blank spinner |
| **Archetype Reveal** | Radar draws edge by edge (450ms) → wave locks to final shape → archetype name "glitch-resolves" from scrambled mono characters (≤600ms, once) → gradient frame ignites |
| **Feed entry** | Cards rise 24px + fade, staggered 60ms, capped at the first 6 |
| **Card hover** | 3D tilt ±6° following pointer (`useMotionValue` + `useSpring`), spotlight radial gradient follows cursor via CSS vars `--mx/--my`, border brightens |
| **Swipe Deck** | Drag with rotation, haptic-style overshoot spring, "Tune In" glows mint, "Skip" fades pink |
| **Page transitions** | Crisp 180ms cross-fade with a 1-frame scanline wipe (the "tech" counterpoint to the organic wave) |
| **Share** | Card flips to flat "export mode", shutter-flash overlay, toast "Signal captured" |

**Accessibility of motion:** respect `prefers-reduced-motion` (replace with fades), no flashing >3 per second (WCAG 2.3.1), glitch effects play once and are skipped when reduced motion is set.

### 2.7 Archetypes (MVP set of 8; centroids are tuned in Phase 0 against real data)

| # | Archetype | Vector tendency | Gradient | Cinema mood | Game mood |
|---|---|---|---|---|---|
| 1 | **Neon Insomniac** | high energy, low valence, high danceability | cyan to violet | neo-noir, cyberpunk thriller | stylish action, rhythm, cyber RPG |
| 2 | **Golden Hour Dreamer** | high valence, high acoustic, mid energy | pink to mint | coming-of-age, romance, road movie | cozy, exploration, narrative indie |
| 3 | **Static Saint** | high instrumental, low valence, low energy | violet to ink | slow cinema, sci-fi drama | atmospheric, walking sims, puzzle |
| 4 | **Velvet Rebel** | mid energy, low valence, low acoustic, older era | pink to violet | crime, character drama | immersive RPG, stealth |
| 5 | **Solar Sprinter** | high energy, high valence, high tempo | mint to cyan | action-comedy, adventure | platformers, racing, arcade |
| 6 | **Hollow Wanderer** | low energy, low valence, high acoustic | lavender to void | quiet drama, folk-horror | narrative, survival-lite, indie |
| 7 | **Chrome Romantic** | high valence, high danceability, low acoustic | pink to cyan | musicals, rom-com, retro-futurist | party, rhythm, co-op |
| 8 | **Echo Archivist** | high era, low mainstream | amber-pink to violet | classics, cult, documentary | retro, deep strategy, niche indie |

Assignment: nearest centroid by weighted cosine; tie-breaker by lower-coverage dimensions being ignored. A user also gets a **secondary archetype** ("with traces of…") to improve the personal feel.

---

## 3. Architecture & Data Flow

### 3.1 System overview

```
                  ┌────────────── Vercel (Edge) ──────────────┐
 Browser ───────► │ Next.js App Router • RSC • next/og (cards) │
                  └───────────────┬───────────────────────────┘
                                  │ HTTPS (cookie session, domain .cerebro.app)
                          CloudFront (WAF, TLS)
                                  │
                         ALB ► ECS Fargate
                    ┌─────────────┴──────────────┐
                    │ FastAPI (api service)      │◄──── SSE progress ────┐
                    │ ARQ workers (worker svc)   │                       │
                    └───┬────────┬────────┬──────┘                       │
                        │        │        │                              │
                 Redis A (cache) │   Redis B (queue+sessions)  ◄─────────┘
                 allkeys-lru     │   noeviction
                        │        │
                   PostgreSQL (RDS)   Secrets Manager / KMS
                        │
   External: Spotify Web API • Vibe-signal sources (§3.3) • TMDB • IGDB (via Twitch OAuth)
```

Deliberate deviations from the research doc:
- **API Gateway removed.** SSE streams and long-lived requests fight API Gateway's timeout ceiling and add cost with little benefit. WAF + TLS sit on CloudFront/ALB; rate limiting is enforced in-app (Redis).
- **Two Redis instances, not one.** Cache (evictable, `allkeys-lru`) must never share a node with sessions and job queues (must not evict, `noeviction`).

### 3.2 Auth & ingestion (Spotify OAuth 2.0, Authorization Code + PKCE)

1. `GET /v1/auth/spotify/login` generates `state` and a PKCE `code_verifier`; stores them in a short-lived signed, HttpOnly cookie (TTL 10 min); redirects to Spotify.
2. Spotify redirects to `GET /v1/auth/spotify/callback`. Backend validates `state` (CSRF), exchanges the code, and fetches the profile.
3. Tokens are **encrypted at rest** (AES-256-GCM, envelope keys from AWS KMS; data key cached in memory) and stored in `spotify_accounts`. **Tokens never reach the browser.**
4. Backend creates a session (opaque ID in Redis B, 30-day sliding) and sets an `HttpOnly; Secure; SameSite=Lax` cookie scoped to `.cerebro.app`. Frontend and API share a registrable domain (`cerebro.app` / `api.cerebro.app`) so no third-party-cookie issues.
5. Refresh handled lazily server-side with a per-user Redis lock to prevent double refresh; refresh-token rotation is honored.
6. **Scopes (minimal):** `user-top-read`, `user-read-recently-played`. Phase 2 adds `playlist-modify-private` (export). **No email scope** unless a feature needs it.
7. Redirect URIs: HTTPS in prod; for local dev use the loopback IP literal (`http://127.0.0.1:<port>/…`), not `localhost`.
8. **Consent screen before redirect** (`ConsentSheet`): toggles for (a) analyze listening, (b) store my Vibe history, (c) allow public cards, (d) email me. Stored in `consent_log` with timestamp and policy version.
9. **Disconnect** revokes locally and deletes tokens. **Delete account** hard-deletes all user rows within 30 days (immediately for tokens) and purges Redis keys.

### 3.3 Vibe Resolver (replaces the "Spotify audio features" step)

**Cerebro Vibe Vector (CVV-8)**, each dimension normalized to 0–1 and carrying its own confidence 0–1:

`energy, valence, acousticness, danceability, instrumentalness, tempo, era, mainstream`

**Source tiers (fused by confidence weight):**

| Tier | Source | Provides | Confidence | Notes |
|---|---|---|---|---|
| T0 | Spotify Web API | Top artists/tracks (3 time ranges), recently played, artist genres, track year/duration | n/a (signal selection) | Verify per-field availability for your app in Phase 0 |
| T1 | Third-party audio-feature API keyed by Spotify track ID (e.g., ReccoBeats) | energy, valence, acousticness, danceability, instrumentalness, tempo | 0.8 | Unofficial/third-party; **validate coverage (target ≥80% of top tracks)**; wrap behind an interface so it is swappable |
| T2 | Last.fm (top tags, listener counts) | Mood-tag lexicon → partial energy/valence/acoustic/dance; `mainstream` | 0.5 | Free API; attribution required |
| T3 | **Genre priors table** (hand-curated: ~60 genre families → CVV centroid) | All dims, coarse | 0.35 | Always available; the guaranteed floor |
| T4 | Offline LLM tagging worker (long-tail artists only), cached forever per artist | Dims with rationale | 0.4 | Send only artist names + genre strings; cost-capped; confirm against Spotify terms before enabling |
| T5 (Ph 3) | Self-hosted audio analysis (e.g., Essentia) on licensed/available preview audio | All audio dims | 0.85 | Only if a legal audio source exists |

**Fusion algorithm (spec):**
1. Take the user's candidate set: top tracks (short/medium/long term) plus top artists plus recent plays.
2. Weight each track by rank: `w = 1 / log2(rank + 1)`; time-range blend for "current vibe" = 0.5 short, 0.3 medium, 0.2 long (long-term vector is also kept separately for the *Echo* / drift views).
3. For each track, per dimension, take the confidence-weighted mean across available sources.
4. User vector = weight-averaged track vectors; per-dimension confidence = weighted mean confidence.
5. **Coverage** = share of tracks with a T1/T2 signal. Surfaced to the user as **Signal Strength %** (honest UX and a gamification hook: "link more listening to sharpen your signal").
6. Persist a `vibe_snapshots` row (vector, confidences, archetype, coverage, time range, resolver version).

### 3.4 Mapping engine (Vibe Vector to movies and games)

Two stages: **retrieve candidates**, then **rank them**.

**Stage A: Candidate retrieval (parallel)**
- Derive *query intents* from the vector (e.g., `valence<0.35 & energy>0.6` → Thriller/Crime/Sci-Fi; `acoustic>0.65 & energy<0.4` → Drama/Romance/indie narrative).
- TMDB: ~6 `discover/movie` calls (genre IDs, keyword IDs, date range from `era`, `vote_count.gte` floor, `with_original_language` optional). Genre/keyword IDs are fetched at boot and cached 7 days; **never hardcode IDs**.
- IGDB: one `/multiquery` request (up to 10 sub-queries) over genres, themes, keywords, player perspectives, rating floor. IGDB is rate-limited (~4 req/s), so all calls pass through a Redis token bucket.
- Pool target: ~250 movies + ~150 games per bucket.

**Stage B: Re-rank (in-process, <25ms)**
- **Title Vibe Vector:** each candidate gets a CVV from its metadata: genre centroids, keyword/theme lexicon adjustments (e.g., "neo-noir" lowers valence, raises energy; "cozy"/"farming" raises valence, lowers energy), release era, popularity.
- **Score:** `S = 0.60·cosine(user, title) + 0.20·quality + 0.10·era_fit + 0.10·novelty` (all weights in config, feature-flag tunable). `quality` = Bayesian-adjusted rating with a vote-count floor.
- **Diversification:** MMR (λ≈0.7) so the feed isn't ten near-duplicates; **15% "Wildcard" slots** drawn from adjacent regions of the vector (exploration, and the best source of feedback signal).
- **Hard filters:** already saved/seen/skipped, user content-rating preference, optional platform filter (games).

**Match Confidence (0–99):** monotonic calibration of `S`, **capped by data quality** (e.g., Signal Strength 40% → max 85). Never shows 100.
**"Why this matches you":** deterministic template that names the top two contributing dimensions and the closest listening evidence ("Because your signal runs low-valence and high-energy, like your *synthwave* and *darkwave* artists"). Phase 2 may LLM-polish the wording using only derived tags, never raw listening history.

### 3.5 Caching strategy (Redis A: `allkeys-lru`)

| Key | TTL | Purpose |
|---|---|---|
| `tmdb:genres`, `tmdb:keywords:*`, `igdb:genres/themes` | 7 d | Static taxonomies |
| `tmdb:discover:{hash(params)}` | 24 h | Candidate pools |
| `igdb:mq:{hash(queries)}` | 24 h | Candidate pools |
| `title:{source}:{id}` | 7 d (refresh ≤ 6 mo for TMDB compliance) | Normalized title metadata + title vector |
| `trk:vec:{spotify_track_id}` | 30 d | Resolved per-track vectors (sources T1–T3) |
| `art:tags:{spotify_artist_id}` | 30 d | Last.fm tags / LLM tags / genre prior |
| `rec:{bucket_hash}:{media}` | 12 h | **Ranked pool per quantized vibe bucket** |
| `sp:top:{uid}:{type}:{range}` | 6 h | Spotify top lists (respect rate limits) |
| `lock:{key}` | 10–30 s | Single-flight lock to prevent cache stampede |

**Bucket trick:** quantize the user vector into 5 levels per dimension (ignoring low-confidence dims) and hash. Users with similar taste share the same cached candidate pool, which is why the system gets *faster and cheaper as it grows*. Personal re-rank (Stage B) still runs per user.
**Stampede control:** single-flight lock plus stale-while-revalidate (serve slightly stale pool, refresh in background); TTLs jittered ±10%.
**Redis B (no eviction):** sessions, ARQ queues, rate-limit counters, SSE job state (TTL 1 h).

### 3.6 Request lifecycle & latency budget

```
Connect → /auth callback → POST /v1/analysis (202, job_id)
   → worker: [1] fetch Spotify lists   [2] resolve vectors   [3] fuse + archetype
             [4] retrieve candidates   [5] rank + explain    [6] persist + emit "done"
   → client: SSE /v1/analysis/{id}/events drives the Analysis Theater
   → GET /v1/recommendations (cursor pagination)
```

| Stage | Cold p95 | Warm p95 |
|---|---|---|
| 1. Spotify fetch (parallel top tracks/artists × ranges) | 700 ms | 5 ms (cache) |
| 2. Resolve vectors (batched T1, parallel T2) | 1,500 ms | 20 ms |
| 3. Fuse + archetype | 30 ms | 30 ms |
| 4. Candidate retrieval | 900 ms | 10 ms (bucket hit) |
| 5. Rank + explain | 40 ms | 40 ms |
| **Total** | **≈ 3.5–5 s** (theater makes it feel intentional) | **< 300 ms** |

API steady-state read endpoints target **p95 < 250 ms**. Public card pages are edge-cached.

### 3.7 Data model (PostgreSQL 16, SQLAlchemy 2.0 async + Alembic)

| Table | Key columns | Notes |
|---|---|---|
| `users` | id (uuid), handle, created_at, locale, deleted_at | |
| `spotify_accounts` | user_id, spotify_user_id, enc_access, enc_refresh, key_id, expires_at, scopes | Encrypted columns only |
| `consent_log` | user_id, flags jsonb, policy_version, at | Append-only |
| `vibe_snapshots` | id, user_id, time_range, vector jsonb, confidence jsonb, coverage, archetype, secondary_archetype, resolver_version, created_at | Monthly history for Vibe Seasons |
| `titles` | id, source (`tmdb`/`igdb`), source_id, media_type, metadata jsonb, title_vector jsonb, fetched_at | Normalized cache of external metadata |
| `recommendations` | id, user_id, snapshot_id, title_id, score, match_pct, why jsonb, rank, served_at | |
| `interactions` | id, user_id, title_id, rec_id, action (`tune_in`/`skip`/`seen`/`save`), at | **The ML gold mine**; partition monthly |
| `library_items` | user_id, title_id, status (`saved`/`watched`/`played`), at | |
| `vibe_cards` | id, user_id, slug (nanoid 10), snapshot_id, template, visibility (`unlisted` default/`public`/`private`), views | |
| `blends` | id, owner_id, partner_id, token, compat jsonb, status, created_at | |
| `gamification` | user_id, xp, level, streak, last_signal_date, badges jsonb | |
| `jobs` | id, user_id, kind, status, error, timings jsonb | Observability |

Indexes: `interactions(user_id, at)`, `recommendations(user_id, served_at desc)`, `vibe_cards(slug)` unique, GIN on `titles.metadata` only if needed. Connection pooling via RDS Proxy (or PgBouncer).

### 3.8 Security & privacy (summary; full checklist in §10)
PKCE + `state`; KMS-backed token encryption; HttpOnly cookies; strict CSP; CSRF protection on mutating routes (double-submit token); per-IP and per-user rate limits (Redis token bucket); Cloudflare Turnstile on Seed Mode; WAF managed rules; secrets in AWS Secrets Manager; no raw listening history ever exposed in public cards (derived data only); unlisted-by-default share slugs; GDPR/CCPA and India DPDP Act-aligned export/delete flows.

---

## 4. Viral Mechanics: Detailed Specs

### 4.1 Vibe Card (the primary share artifact)
- **Templates (MVP):** *Archetype Card* (hero), *Triple Feature* (top 2 films + 1 game), *Roast Card*. Phase 2: *Blend Card*, *Season Card*.
- **Formats:** 1080×1920 (Stories), 1080×1080 (feed), 1200×630 (OG/link preview).
- **Contents:** archetype name + gradient, radar, 3 picks, Signal Strength, short link + QR (`cerebro.app/c/{slug}`), "Find your signal" CTA.
- **Rendering decision (important):** `html2canvas` **does not support `backdrop-filter`** and mishandles complex gradients/masks, so frosted-glass UI would export as flat or broken. Therefore:
  1. **Primary:** server-side render with `next/og` (`ImageResponse`/Satori) on the edge from a dedicated *flattened-glass* design (pre-composited gradients and semi-opaque panels that mimic glass). This also powers link-preview OG images and gives pixel-identical output across devices.
  2. **Fallback / instant-preview:** client-side `html-to-image` (or `html2canvas`) on the same flattened `VibeCardCanvas` component. Fonts embedded; images proxied through `/api/img` to avoid CORS taint.
- **Share targets:** Web Share API (files), download PNG, copy link. Pre-filled caption with archetype.
- **Content rules:** cards use archetype, derived stats, and TMDB/IGDB art. Showing Spotify album art triggers Spotify branding/attribution rules, so MVP cards avoid Spotify imagery and include a "Powered by Spotify data" attribution where required (confirm exact wording with the current guidelines).

### 4.2 Seed Mode (growth unlock)
A visitor without a Spotify connection picks 3 artists (search-as-you-type via Spotify client-credentials search, or a static curated list when quota is limited). The Resolver runs on T2/T3 only, produces an ephemeral vector, and shows a **blurred-then-teaser** result: archetype fully visible, top pick revealed, rest gated behind "Connect Spotify for your full signal." Also the fallback if Spotify access is delayed.

### 4.3 Vibe Blend
1. Owner taps *Blend* → server creates `blends` row + single-use token link.
2. Partner opens link → sees owner's archetype → connects Spotify or uses Seed Mode.
3. **Compatibility** = weighted cosine of vectors + overlap of genre families + "complementarity" bonus; displayed as an orb with merged aurora colors.
4. Output: *Double Feature* (3 movies + 2 co-op/party games both score highly on, using IGDB game modes), plus a shareable Blend Card.
5. Phase 3: group blend up to 6 with a "everyone is at least 70% happy" re-rank.

### 4.4 Gamification
- **Synapse XP & Levels** (Link, Analyze, Rate 10 picks, share, daily visit). Level names escalate: *Spark, Pulse, Signal, Resonant, Cerebro Prime*.
- **Badges:** Night Owl (usage after 1am local), Genre Hopper, Time Traveler (era dimension extremes), Wildcard Winner (liked a wildcard), Blend Buddy.
- **Signal Streak:** one Daily Signal pick per day; streak freeze earned via sharing (not purchase).
- **Swipe Deck:** the most fun way to give feedback; every swipe writes an `interactions` row.
- Ethical guardrail: no dark patterns, no loss-aversion push spam; notifications strictly opt-in with easy off.

### 4.5 Retention loops
Daily Signal → streak → weekly Vibe Forecast email → monthly Vibe Season drift card → release alerts ("A new film matches your signal at 94%") → Blend invites. Instrument each loop (see §9 metrics).

---

## 5. Component & Service Tree

### 5.1 Frontend (Next.js App Router, TypeScript strict, Tailwind, Framer Motion)

```
apps/web/
├─ app/
│  ├─ layout.tsx                    # fonts, ThemeProvider, AuroraBackdrop (persistent)
│  ├─ page.tsx                      # Landing (SSG)
│  ├─ connect/page.tsx              # ConsentSheet + SpotifyConnectBtn
│  ├─ seed/page.tsx                 # Seed Mode
│  ├─ analyze/page.tsx              # AnalysisTheater (SSE)
│  ├─ dashboard/page.tsx            # AudioDNAPanel + ArchetypeReveal
│  ├─ discover/page.tsx             # DiscoveryFeed / SwipeDeck
│  ├─ library/page.tsx              # WatchlistGrid
│  ├─ blend/[token]/page.tsx        # Blend join + BlendReveal
│  ├─ c/[slug]/page.tsx             # Public Vibe Card (SSR + ISR, edge cached)
│  ├─ c/[slug]/opengraph-image.tsx  # next/og generator
│  ├─ settings/page.tsx             # consent, disconnect, delete, export
│  └─ api/img/route.ts              # image proxy (CORS-safe for exports)
├─ components/
│  ├─ system/        AuroraBackdrop, GridHorizon, NoiseOverlay, GlassPanel, GlassPanelLite,
│  │                 NeonBorder, GlowText, MagneticButton, CursorSpotlight, TechGlyph,
│  │                 SignalWave (canvas), MotionProvider (reduced-motion/transparency)
│  ├─ auth/          SpotifyConnectBtn, ConsentSheet, ScopeExplainer, DisconnectDialog
│  ├─ analysis/      AnalysisTheater, StageTicker, SignalStrengthMeter
│  ├─ dashboard/     AudioDNAPanel, VibeVectorRadar (SVG), ArchetypeReveal,
│  │                 TopArtistsConstellation, VibeSeasonsTimeline
│  ├─ discovery/     DiscoveryFeed, GlassVibeCard, MatchMeter, WhyThisMatch,
│  │                 FeedbackBar, SwipeDeck, MediaTabs, WildcardBadge
│  ├─ library/       WatchlistGrid, WatchlistButton
│  ├─ share/         VibeCardCanvas, VibeCardTemplates, ShareSheet, QRBadge
│  ├─ blend/         BlendInvite, BlendReveal, CompatibilityOrb
│  ├─ gamification/  SynapseXPBar, BadgeShelf, StreakFlame, DailySignalCard
│  └─ search/ (Ph3)  VibeSearchBox
├─ lib/              api client (generated from OpenAPI), query keys, vibe→visual mapper,
│                    motion presets, analytics
├─ hooks/            useSSE, useVibeVector, useReducedMotion, usePointerTilt, useShare
├─ stores/           Zustand (ui + motion state only)
└─ styles/           tokens.css (CSS variables), glass.css, globals.css
```

- **Server state:** TanStack Query. **UI state:** Zustand. **Schemas:** Zod, with types generated from FastAPI's OpenAPI (`openapi-typescript`) so contracts have one source of truth.
- **Rendering:** landing and static pages SSG; dashboard client-fetched behind session; public cards SSR + ISR (`revalidate` 1 h) at the edge.
- **Perf budget:** LCP < 2.0 s (4G), initial JS ≤ 180 KB gz, CLS < 0.05, INP < 200 ms. Heavy visuals (`SignalWave`, constellation) are dynamically imported and gated by `IntersectionObserver`.
- **Testing:** Vitest + Testing Library; Storybook with axe contrast checks; Playwright E2E with a mocked Spotify.

### 5.2 Backend (FastAPI, Python 3.12, Pydantic v2, async everywhere)

```
services/api/app/
├─ main.py                 # app factory, middleware (request-id, CORS, security headers, rate-limit)
├─ core/                   # config (pydantic-settings), security, crypto (KMS), logging, errors
├─ api/v1/routers/         # auth, me, analysis, vibe, recommendations, library, cards, blends,
│                          # gamification, search, meta
├─ services/
│  ├─ spotify_client.py    # async httpx, token refresh, backoff, quota awareness
│  ├─ vibe/                # resolver.py, sources/{reccobeats,lastfm,genre_priors,llm_tagger}.py,
│  │                       # fusion.py, archetypes.py
│  ├─ mapping/             # intents.py, title_vectors.py, scoring.py, diversify.py, explain.py
│  ├─ tmdb_client.py       # async, cached, attribution-aware
│  ├─ igdb_client.py       # Twitch OAuth client-credentials, multiquery, token bucket
│  ├─ blend.py, gamification.py, cache.py (single-flight, SWR), ratelimit.py
├─ workers/                # ARQ: analysis_job, refresh_taxonomies, snapshot_monthly,
│                          # warm_buckets, email_digest (Ph2)
├─ models/                 # SQLAlchemy 2.0 models
├─ schemas/                # Pydantic request/response models
└─ migrations/             # Alembic
```

**Endpoint contract (all under `/v1`; auth = session cookie unless noted)**

| Method & Path | Auth | Purpose | Cache |
|---|---|---|---|
| `GET /auth/spotify/login` | none | Begin OAuth (PKCE + state) | no |
| `GET /auth/spotify/callback` | none | Complete OAuth, set session | no |
| `POST /auth/logout` | yes | End session | no |
| `GET /me` | yes | Profile, consent, gamification summary | no |
| `PATCH /me/consent` | yes | Update consent flags | no |
| `DELETE /me` | yes | Delete account and data | no |
| `POST /analysis` | yes | Start analysis job → `202 {job_id}` | no |
| `GET /analysis/{job_id}` | yes | Job status/result | no |
| `GET /analysis/{job_id}/events` | yes | **SSE** progress stream | no |
| `GET /vibe/current` | yes | Latest vector, archetype, Signal Strength | 60 s private |
| `GET /vibe/history` | yes | Snapshots for Vibe Seasons | 5 min private |
| `POST /vibe/seed` | none + Turnstile | Ephemeral vector from 3 artists | bucket cache |
| `GET /seed/artists?q=` | none + rate limit | Artist search for Seed Mode | 1 h |
| `GET /recommendations?type=movie\|game\|both&cursor=` | yes | Ranked feed | `rec:` bucket |
| `GET /recommendations/{id}/why` | yes | Expanded explanation | 1 h |
| `POST /recommendations/{id}/feedback` | yes | `tune_in`/`skip`/`seen` | no |
| `GET /daily-signal` | yes | Today's single pick + streak | until midnight (user tz) |
| `GET /library` · `POST /library` · `DELETE /library/{id}` | yes | Watchlist CRUD | no |
| `POST /cards` | yes | Create card from snapshot + template → slug | no |
| `GET /cards/{slug}` | none | Public card data (respects visibility) | CDN 1 h |
| `PATCH /cards/{slug}/visibility` | yes | unlisted / public / private | no |
| `POST /blends` | yes | Create invite | no |
| `POST /blends/{token}/join` | yes or seed | Join + compute | no |
| `GET /blends/{id}` | yes | Result | 5 min private |
| `GET /gamification/profile` | yes | XP, level, streak, badges | no |
| `POST /playlists/export` (Ph2) | yes | Create "Cinematic Night" playlist | no |
| `POST /search/vibe` (Ph3) | yes | Natural-language vibe search | no |
| `GET /healthz`, `/readyz` | none | Liveness / readiness | no |

**Conventions:** cursor pagination; RFC 7807 problem+json errors; request-ID on every response; idempotency keys on `POST /cards` and `POST /blends`; versioned resolver (`resolver_version`) stored with every snapshot so scores are reproducible.

---

## 6. ML & Intelligence Roadmap

| Stage | What | Data used | Trigger |
|---|---|---|---|
| **v1 (MVP)** | Rule/centroid mapping + cosine re-rank | Taxonomy tables, T1–T3 | Launch |
| **v1.5** | Weight tuning via online experiments (GrowthBook/PostHog) | Interactions | ≥ 5k swipes |
| **v2** | Learned re-ranker (LightGBM) on user vector × title vector × context | **First-party** interactions only | ≥ 100k labeled interactions |
| **v2.5** | Collaborative signal: item-item co-"tune in" across vibe buckets | Interactions | After v2 |
| **v3** | Two-tower embeddings + `pgvector` ANN retrieval for titles | Title metadata text + interactions | Catalog > 100k titles in use |
| **v3** | LLM natural-language vibe search; query → structured intent → existing ranker | Derived tags only | Phase 3 |

**Guardrails:** do not train on Spotify-sourced content (§0); offline eval (NDCG@10 against held-out feedback) must beat the previous model before shipping; every model behind a flag with instant rollback; log model version with every served recommendation.

---

## 7. Accessibility, Privacy & Compliance Checklist

- WCAG 2.2 AA: contrast verified in CI; visible focus ring (2px `--cyan` with offset); full keyboard operation (SwipeDeck has button equivalents); SSE progress in an `aria-live="polite"` region; radar chart has a text table alternative; no information by color alone.
- Motion: reduced-motion and reduced-transparency honored globally; no flash >3 Hz.
- Privacy: privacy policy + consent log; data-minimal scopes; data export (JSON) and deletion; public cards expose derived data only; cookie banner only if non-essential cookies are used (analytics configured cookieless where possible); age gate 13+ (16 in applicable EU regions); regional compliance review (GDPR, CCPA, India DPDP).
- Third-party terms: Spotify Developer Terms (caching, ML, branding, attribution), TMDB (attribution, metadata refresh cadence, **commercial use requires a license**), IGDB/Twitch (**free tier is for non-commercial use; commercial use needs a partnership**), Last.fm (attribution, ToS). **Monetization is gated on these.**

---

## 8. Deployment & Scaling Strategy

### 8.1 Topology
- **Frontend:** Vercel (edge network, ISR, `next/og` on edge, preview deployment per PR).
- **Edge for API:** CloudFront (TLS, WAF managed rules, bot control, cache for `/v1/cards/*` public GETs) → ALB (idle timeout raised for SSE) → ECS Fargate.
- **Compute:** ECS Fargate services `api` (FastAPI via Uvicorn/Gunicorn workers) and `worker` (ARQ), same image, different command. Containers are non-root with read-only root FS.
- **Data:** RDS PostgreSQL 16 (Multi-AZ from Stage 2, automated backups + PITR), RDS Proxy; ElastiCache Redis ×2 (cache / queue+sessions) with encryption in transit and at rest.
- **Secrets & keys:** Secrets Manager, KMS for token encryption. **Network:** private subnets for ECS/RDS/Redis; NAT egress to third-party APIs; VPC endpoints for ECR/S3/Secrets.
- **Domains:** `cerebro.app` (Vercel), `api.cerebro.app` (CloudFront).

### 8.2 Scaling stages

| Stage | Users | Shape |
|---|---|---|
| **Lean launch** | 0–5k | 1 `api` task (0.5 vCPU/1 GB) ×2 for HA, 1 `worker`, RDS `t4g.small` single-AZ, ElastiCache `t4g.micro` ×2 |
| **Traction** | 5k–100k | API autoscale 2–10 (target CPU 55% and ALB request count), workers autoscale on **queue depth** (custom CloudWatch metric), RDS Multi-AZ + RDS Proxy, bucket-cache warming job for the top ~500 buckets |
| **Scale** | 100k+ | Read replica for history/analytics queries, partition `interactions`, Redis cluster mode for cache tier, split `worker` into `ingest` and `rank` pools, move analytics events to a warehouse |

**Viral-spike protection:** public card pages and OG images are CDN-cached (the only unauthenticated hot path); Seed Mode is rate-limited and CAPTCHA'd; worker queue has back-pressure ("You're #12 in line, aurora warming up" UX) rather than failing; third-party calls use circuit breakers and serve stale cache when TMDB/IGDB/Last.fm degrade.

### 8.3 CI/CD & environments
- **Environments:** `dev` (local Docker Compose with Postgres, two Redis, Spotify mock), `staging`, `prod`. Infrastructure as code with Terraform (or AWS CDK in Python if you prefer a single language).
- **Pipeline (GitHub Actions):** lint (ruff, mypy, ESLint, tsc) → unit tests → contract tests (OpenAPI diff gate) → build image → push to ECR → Alembic migration as a one-off ECS task → rolling/blue-green deploy via CodeDeploy → smoke tests → auto-rollback on failed health checks. Frontend deploys via Vercel with Playwright against the preview URL.
- **Migrations:** forward-only, backward-compatible within one release (expand → migrate → contract).

### 8.4 Observability & product analytics
OpenTelemetry traces (API → worker → external calls) to CloudWatch/Grafana; Sentry (web + API); structured JSON logs with no PII or tokens; alarms on p95 latency, 5xx rate, queue depth, third-party error rates, Spotify 429s. **PostHog** for funnels and flags.

**North-star and loop metrics:** Time-to-Reveal; Connect→Reveal completion rate; **Share rate** (cards shared / reveals); **K-factor** (new users per sharer, via `/c/{slug}` referrals); Blend completion rate; D1/D7/D30 retention; Daily Signal streak distribution; Signal Strength distribution (health of the Vibe Resolver); recommendation Tune-In rate (quality proxy).

---

## 9. Testing & Quality Gates
- **Resolver golden set:** 50 fixture listener profiles with expected archetypes; regression-tested on every resolver change.
- **Mapping tests:** property tests (monotonicity: raising valence never raises "dark thriller" affinity), diversity tests (no >3 near-duplicates in top 10).
- **Load tests (k6):** 500 concurrent Analysis jobs; viral card-page burst of 5k rps against CDN.
- **Chaos:** TMDB/IGDB/Last.fm failures must degrade gracefully (stale cache plus banner), never a blank screen.
- **Visual regression:** Storybook snapshots for GlassPanel states and card templates; contrast assertions fail the build.

---

## 10. Security Checklist (release gate)
OAuth `state` + PKCE ✔ · tokens KMS-encrypted ✔ · no tokens/PII in logs ✔ · HttpOnly/Secure/SameSite cookies ✔ · CSRF protection ✔ · strict CSP + HSTS + frame-ancestors none ✔ · per-IP and per-user rate limits ✔ · Turnstile on anonymous endpoints ✔ · input validation (Pydantic) ✔ · SSRF-safe image proxy (allow-list TMDB/IGDB hosts, size and type caps) ✔ · dependency scanning (Dependabot, pip-audit, npm audit) ✔ · least-privilege IAM per service ✔ · RDS/Redis not publicly reachable ✔ · automated backups and tested restore ✔ · incident runbook and token-revocation procedure ✔ · pen-test before Extended Quota application.

---

## 11. Phased Delivery Plan

| Phase | Goal | Exit criteria |
|---|---|---|
| **Phase 0: Feasibility spike (Go/No-Go)** | Prove the data layer before building UI | (1) Spotify dev app created; document which endpoints/fields return 200 for *your* app. (2) T1 coverage ≥ 80% on 200 real top tracks. (3) T2/T3 produce sensible archetypes for 10 hand-picked test profiles. (4) TMDB/IGDB candidate retrieval < 1 s cold. (5) Legal read of Spotify/TMDB/IGDB terms for your intended (non-)commercial use. **If (1)–(3) fail, pivot to Seed-Mode-first.** |
| **Phase 1: MVP** | Reveal → Recommend → Share | Auth, Seed Mode, Resolver, dashboard, feed, swipe feedback, watchlist, Vibe Card export, design system, core analytics. Lighthouse/a11y gates green |
| **Phase 2: Growth** | Loops | Blend, streaks/XP/badges, Daily Signal, Vibe Seasons, playlist export, email forecast, Reverse Lens, 12 archetypes |
| **Phase 3: Scale & Intelligence** | Compounding quality | Learned re-ranker, embeddings, NL search, additional music sources, group blends, Wrapped |

**Suggested build order inside Phase 1:** design tokens + `GlassPanel` + `AuroraBackdrop` → auth/session → Resolver (with fixtures) → mapping engine → `AnalysisTheater` + SSE → dashboard/reveal → feed + `GlassVibeCard` → feedback + library → Vibe Card render → hardening + load tests.

---

## 12. Risks & Open Decisions

| Risk / Decision | Severity | Mitigation / Owner decision needed |
|---|---|---|
| Spotify Extended Quota approval may be slow or denied; dev mode allows only a tiny user set | **High** | Seed-Mode-first launch; start application early; keep the Resolver source-agnostic so Last.fm/Apple Music/YouTube Music can be primary |
| Third-party audio-feature API is unofficial and may change or vanish | High | Interface-wrapped; cache 30 d; T2/T3 floor; budget time for T5 self-hosted analysis |
| Spotify terms on ML, caching, branding | High | Counsel review; first-party-only ML; minimal Spotify data retention; attribution text |
| TMDB/IGDB commercial licensing | High if monetizing | Decide: non-commercial portfolio/community product vs. licensed commercial product *before* Phase 2 |
| Glass blur performance on low-end phones | Medium | GlassPanelLite default in lists, mobile blur 10px, perf budget in CI |
| Vibe accuracy perceived as "random" | Medium | Explanations, Signal Strength meter, golden-set testing, easy feedback to correct it |
| Cost creep (LLM tagging, RDS/ECS) | Medium | LLM only for long-tail artists with hard monthly cap; lean launch topology |
| Minor/under-13 users | Medium | Age gate and policy |

**Decisions the product owner must confirm (defaults in bold):**
1. Monetization stance: **non-commercial at launch** (keeps TMDB/IGDB free tiers valid).
2. Seed Mode as launch hero: **yes**.
3. LLM tagging (T4): **off until Phase 0 shows genre priors + Last.fm are insufficient**.
4. IaC tool: **Terraform**.
5. Roast Mode: **ship at MVP as an opt-in toggle** with a tone guide (playful, never cruel; no sensitive-attribute jokes).

---

*End of blueprint. Any change to the Vibe Vector schema, scoring weights, or endpoint contracts requires a version bump here (`resolver_version`, `api v1`) before code changes.*
