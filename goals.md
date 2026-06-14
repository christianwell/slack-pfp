# Slack PFP — Goals & Roadmap

Turn the single-user, self-hosted Slack now-playing PFP/status updater into a
multi-tenant service that **anyone in Hack Club can sign into and use**, hosted
at `slackpfp.christianwell.xyz`.

> **Status (implementation):** the full multi-tenant code path is built and
> tested locally — Slack OAuth login, encrypted per-user SQLite storage,
> per-user dashboard/editor/account, onboarding with live Last.fm validation,
> and the throttled multi-user worker. What remains is **operator setup**
> (register the Slack app, DNS/HTTPS, fill `.env`) — see `README.md`.

---

## Vision

A user visits `slackpfp.christianwell.xyz`, clicks **"Add to Slack"**, authorizes
the app, enters their Last.fm username, and from then on their Slack profile photo
shows their current album art (framed) and their status reads:

```
🎵  90210 (feat. Kacy Hill) - Travis Scott │ by slackpfp.christianwell.xyz
```

Holiday overlays and the Figma-style overlay editor still work, but per-user.

## Current codebase snapshot

- Single-user Flask login still uses static `WEB_USER` / `WEB_PASSWORD` in `app.py`.
- Dashboard settings and updater state still live in global `config.json` and `state.json` via `core.py`.
- The updater loop in `main.py` still reads one shared `LASTFM_USERNAME` and one shared Slack token from `.env`.
- Status branding lives in the `status_format` string itself (default
  `{song} - {artist} │ by slackpfp.christianwell.xyz`); there is no separate suffix.
- The updater sets the Slack **status** and **photo independently**, so a
  rate-limited photo upload can no longer block the status from updating.

---

## Key decisions

### Last.fm: one shared app key, ask users only for their username  ✅ decided
- A Last.fm API key identifies the **app**, not the user. `user.getrecenttracks`
  needs only **your single app key + the user's public username**.
- **Do NOT** ask every user for their own key — bad UX, unnecessary.
- **Constraint — rate limits:** ~5 requests/sec per key/IP. Polling every user
  every few seconds does not scale. Need a **staggered scheduler**:
  - Single shared poll loop iterating all users, throttled to stay under ~5 req/s.
  - Rough capacity: ~5 req/s × 60s cycle ≈ **300 users per cycle** on one key.
  - Beyond that: lengthen the cycle, shard keys, or add a request queue.
- **Private scrobbles:** users with private Last.fm profiles can't be read with
  username-only. Defer; show a clear "make your scrobbles public" hint. (Optional
  later: full Last.fm web-auth flow for a per-user session key.)

### Status branding  ✅ shipped (single-user)
- Branding is part of the configurable `status_format`, not a separate field.
  Default: `{song} - {artist} │ by slackpfp.christianwell.xyz`.
- Available placeholders: `{song}`, `{artist}`, `{album}`.
- Truncated to Slack's **100-char** status limit (ellipsis on overflow).
- A bad/unknown placeholder falls back to `{song} - {artist}` instead of failing.
- Implemented: `core.format_status` (replaces the old `apply_status_suffix`).

### User-uploaded overlay storage: Hack Club CDN vs our disk  ✅ shipped Option 1
The worry: once this is multi-user, every custom overlay a user uploads lands on
**our** server disk. Could we push those to `cdn.hackclub.com` (per-user API key
or upload-on-their-behalf) so user content isn't hosted by us? Keep the bundled
default assets (hat, cake, frame, holiday art) in the repo regardless.

**Grounded facts about Hack Club CDN v4** (from `cdn.hackclub.com/docs/api`):
- Per-user API keys (`sk_cdn_...`), **created manually** in the CDN web dashboard
  after Hack Club OAuth — there is **no programmatic key issuance**.
- `POST /api/v4/upload` takes a real multipart file; `POST /api/v4/upload_from_url`
  mirrors by URL; `DELETE /api/v4/upload/:id`; `GET /api/v4/me` for quota.
- Per-user quotas: unverified ~50 MB, verified ~50 GB. Returns a permanent public
  URL like `https://cdn.hackclub.com/<id>/<filename>`.

**The catch this changes:** our worker still has to **download each overlay and
composite it server-side with Pillow** to build the Slack photo. So a CDN only
offloads *persistent storage, bandwidth, and per-user quota/attribution* — the
bytes still flow through our process at render time, and we still must validate
image content. "Not on my server at all" isn't actually achievable here.

**Options:**
1. **Our disk, namespaced per user** (default) — simplest, frictionless onboarding,
   but uses our storage and puts abuse/attribution on us. Add per-user quotas +
   content validation + cap counts/sizes.

   **Storage is not actually scary if we re-encode on upload.** The final Slack
   photo is capped at 512×512 (`core.prepare_image_for_slack`, `max_size=512`),
   and an overlay is composited at `scale` ≤ 1.0 of that — so **no overlay ever
   needs to be stored larger than ~512px wide.** Today we keep the *raw* upload
   (up to 5 MB each, `app.MAX_CONTENT_LENGTH`); that gap is the only reason
   storage looks expensive.
   - Resize each upload to ≤512px + `optimize=True` on save → ~20–150 KB per file
     instead of multiple MB (zero quality loss, since it's rendered small anyway).
   - Rough disk use at 10 overlays/user, ~150 KB each: 100 users ≈ 150 MB,
     1,000 users ≈ 1.5 GB, 10,000 users ≈ 15 GB. (Raw 5 MB files would be
     ~50 GB at 1,000 users — that's the scenario to avoid.)
   - Add a per-user **count cap** (~10–20) and a **byte budget** (~10 MB), reject
     when exceeded. Deletion already frees files (`app._delete_item_image`);
     bundled default assets never count toward a user's quota.
   - To-do: GIFs don't shrink the same way — cap animated uploads tighter or
     flatten to a single frame.
2. **User's own CDN key** — user pastes their `sk_cdn_` key; uploads + quota +
   abuse attribution live under *their* Hack Club account. Costs: manual key step
   (hurts the 1-field onboarding), another secret to encrypt at rest, extra
   failure modes (revoked key / quota 402 → overlay breaks).
3. **One operator CDN key for everyone** — we upload all user overlays under *our*
   single CDN account via `/api/v4/upload`. Frictionless for users and offloads
   our disk/bandwidth, but everything counts against our 50 GB and abuse is
   attributed to us.

**Shipped:** Option 1 (per-user dirs + quotas + validation). `app._save_upload`
re-encodes every upload to a ≤512px optimized PNG via `core.save_overlay_upload`
(Pillow open also validates the bytes), stores it under `uploads/<slack_user_id>/`,
and enforces a per-user **count cap** (`MAX_OVERLAYS_PER_USER`, default 20) and
**byte budget** (`MAX_UPLOAD_BYTES`, 10 MB). Deletion frees files; bundled assets
never count. Option 2 (user's own CDN key) remains a possible future advanced
setting; Option 3 only if storage/bandwidth ever becomes the bottleneck.

### Slack auth: OAuth v2, per-user tokens  ✅ built (`slack_oauth.py`)
- Use Slack **OAuth 2.0** ("Add to Slack" / Sign in with Slack).
- Scopes needed (user token, since we edit the signed-in user's own profile):
  - `users.profile:write` — set status + (status) — **user token**
  - `users.profile:read`
  - `users:write` — `users.setPhoto` (profile photo) — **user token**
  - `emoji:read` — validate/preview custom emojis
- Store the **per-user access token** securely (encrypted at rest).
- Restrict to the Hack Club workspace (team `T0266FRGM`) — reject other teams
  on the OAuth callback.

### Data storage  ✅ built + wired (`db.py`)
- Move from flat `config.json` / `state.json` (single user) to **per-user records**.
- Start simple: **SQLite** (one row per user: slack_user_id, team_id, encrypted
  token, lastfm_username, config JSON, state JSON). Migrate to Postgres only if
  needed.
- Encrypt Slack tokens at rest (e.g. Fernet key from env).
- **Done:** `db.py` implements this — a `User` dataclass + `load_user` /
  `save_user` (upsert) / `delete_user` / `all_users`, SQLite at `users.db`
  (WAL, per-thread connection), per-user config backfilled from
  `core.DEFAULT_CONFIG`, and an `onboarding` state column matching the state
  machine below. Slack tokens are Fernet-encrypted at rest via `FERNET_KEY`
  (generate with `python -m db keygen`, store in `.env`, never commit).
- **Wired:** `app.py` and `main.py` now load/save `db.User` rows everywhere; the
  global `config.json`/`state.json` reads are gone. SQLite uses WAL with a
  per-thread connection so the web app and worker can both touch it safely.

### Hosting  ⬜ to set up
- Domain `slackpfp.christianwell.xyz` → reverse proxy (Caddy/nginx) → waitress.
- HTTPS required for Slack OAuth redirect URL.
- systemd services already exist; add the web app behind the proxy.

---

## Architecture (target)

```diagram
            ╭──────────────────────────────────────────────╮
            │  slackpfp.christianwell.xyz (Flask + waitress)│
            │                                              │
 Browser ──▶│  /  login (Sign in with Slack OAuth)         │
            │  /dashboard  per-user settings + editor      │
            │  /oauth/callback  store encrypted user token │
            ╰───────────────┬──────────────────────────────╯
                            │ reads/writes
                            ▼
                   ╭──────────────────╮
                   │ SQLite (users)   │  slack_token(enc), lastfm_user,
                   │                  │  config json, state json
                   ╰────────┬─────────╯
                            │ shared poll loop reads all users
                            ▼
          ╭─────────────────────────────────────────────╮
          │ Worker (staggered, ≤5 req/s to Last.fm)      │
          │  for each user: getrecenttracks → build pfp  │
          │  → users.setPhoto + users.profile.set        │
          ╰───────────────┬───────────────┬──────────────╯
                          ▼               ▼
                    Last.fm API      Slack Web API
                  (1 shared key)   (per-user tokens)
```

---

## Roadmap

### Phase 0 — current (single-user) ✅
- Now-playing PFP + status, holiday overlays, Figma-style editor, Hack Club
  emoji picker/validation, status branding baked into the configurable format.

### Phase 1 — Slack OAuth + multi-user data model ✅ (code) / ⬜ operator setup
1. ⬜ Register Slack app, configure OAuth redirect + scopes, restrict to Hack
   Club. *(Operator step — code reads it all from `.env`; see `.env.example`.)*
2. ✅ SQLite schema + token encryption — `db.py` (`User`, load/save/delete/all,
   Fernet-encrypted tokens, per-user config/state, onboarding column).
3. ✅ "Sign in with Slack" flow; create/update user row on callback — `slack_oauth.py`
   + `app.oauth_callback` (CSRF state check, team `T0266FRGM` restriction, re-auth
   refreshes token & reactivates disconnected users).
4. ✅ Session = signed-in Slack user (static `WEB_USER`/`WEB_PASSWORD` removed).
5. ✅ Per-user config/state — every route loads/saves a `db.User`; the global
   `config.json`/`state.json` single-user path is retired.

### Phase 2 — multi-user worker ✅
1. ✅ Single shared scheduler iterating all active users, throttled to
   ≤`LASTFM_MAX_RPS` req/s with a min-interval gate (`main.main`).
2. ✅ Per-user now-playing detection, restore-default, holiday logic
   (`main.process_user`, per-user in-memory runtime state).
3. ✅ Backoff + disable on repeated Slack auth failures — `AuthFailure` +
   `AUTH_FAIL_LIMIT`; revoked tokens flip the user to `disconnected`.

### Phase 3 — onboarding & polish ✅
1. ✅ Landing page explaining the service + "Add to Slack" (`landing.html`).
2. ✅ Last.fm username setup + live "scrobbles must be public" validation
   (`onboarding.html`, `/api/lastfm/validate`, `core.validate_lastfm_user`).
3. ✅ Per-user holiday/overlay editor (existing editor, scoped to the user),
   including a drag-and-drop **album cover** editor (`/editor/album`) — the
   framed album-art badge's position + size are now per-user config
   (`album_scale`/`album_anchor`/`album_offset_x`/`album_offset_y`) instead of a
   hardcoded bottom-left corner (`core.create_profile_image` + `core.album_badge`).
4. ✅ Account page: pause/resume, disconnect, delete data (`account.html`).

See the dedicated **Onboarding flow** section below for the step-by-step UX.

### Phase 4 — scale & ops 🟡 (built what's needed now)
1. ✅ Rate-limit headroom logged each cycle; sharding Last.fm keys deferred until
   actually needed (capacity note unchanged).
2. ✅ Metrics/health endpoint (`/healthz`); per-user logs are keyed by Slack id
   and never log tokens.
3. ✅ Backup + key-rotation guidance documented in `README.md` (back up
   `users.db` + `.env`; rotate `FERNET_KEY` or have users re-authorize).

---

## Onboarding flow ✅ built

A new user should go from "never heard of it" to "my PFP updates automatically"
in well under a minute, with no token wrangling. Target: 3 clicks + 1 text field.

### Steps

```diagram
 ╭──────────╮   ╭──────────────╮   ╭────────────────╮   ╭───────────────╮   ╭──────────╮
 │ Landing  │──▶│ Add to Slack │──▶│ Last.fm step   │──▶│ First preview │──▶│ Done /   │
 │ page     │   │ (OAuth)      │   │ enter username │   │ + go live     │   │ dashboard│
 ╰──────────╯   ╰──────────────╯   ╰────────────────╯   ╰───────────────╯   ╰──────────╯
```

1. **Landing page** — what it does, a sample PFP/status, "Add to Slack" button,
   and a one-line privacy note ("we only read your public Last.fm scrobbles and
   set your own Slack photo/status"). Hack-Club-only notice.
2. **Add to Slack (OAuth)** — Slack consent screen; on callback verify team
   `T0266FRGM`, store the encrypted user token, create/load the user row, and
   start a logged-in session.
3. **Last.fm step** — single text field for the username. Validate live by
   calling `user.getrecenttracks`:
   - ✅ found + public → green check, show the most recent track as proof.
   - ⚠️ private/empty → inline hint with a link to Last.fm privacy settings
     ("make your scrobbles public"), let them retry. Don't hard-block.
4. **First preview + go live** — render their actual PFP (default photo + frame +
   current album art) so they see the result before committing. One toggle:
   "Start updating my Slack profile". Defaults sensible (status on, frame on,
   default branding format).
5. **Done → dashboard** — drop them on the existing per-user dashboard with a
   small "you're live" banner and pointers to holidays/overlays/account.

### Onboarding state machine (per user)

`new → slack_connected → lastfm_set → active`
(plus `paused` and `disconnected` reachable from `active`).
Resume onboarding at the right step if a user drops off (e.g. connected Slack
but never set Last.fm → land them back on step 3).

### Edge cases to handle
- User closes the tab mid-flow → next login resumes at the first incomplete step.
- Wrong workspace on OAuth → friendly "Hack Club only" page, no row created.
- Private Last.fm scrobbles → soft warning, allow finishing; worker shows a
  "can't read scrobbles" status on the account page until fixed.
- Re-running "Add to Slack" for an existing user → update token, skip to dashboard.
- Last.fm username typo'd → revalidate on the account page anytime.

### Nice-to-haves (later)
- Optional "Sign in with Last.fm" web-auth to support private profiles.
- Sample/demo mode on the landing page (animated example, no login).
- Email-free; everything keyed off the Slack identity.

---

## Open questions
- Bot token vs user token for `users.setPhoto` — **decision: user token** with
  `users:write` (we edit the signed-in user's own photo). Still needs a live
  smoke test with the real Slack app to confirm scopes behave as expected.
- Do we need a Slack app **distribution** review for cross-workspace, or keep it
  Hack-Club-only (single workspace install)? Hack-Club-only is simpler.
- Storage of uploaded overlay images per user — see the "User-uploaded overlay
  storage: Hack Club CDN vs our disk" decision above (leaning per-user dirs by
  default, optional CDN key for power users).
- Abuse/safety: cap uploads, validate image content, per-user quotas.

---

## Security checklist (Phase 1+)
- [x] Encrypt Slack tokens at rest; key from env, never in repo.
      (`db.py` Fernet via `FERNET_KEY`; tokens stored as ciphertext BLOB.)
- [x] Verify OAuth `state` param (CSRF) on callback. (`app.oauth_callback`)
- [x] Restrict installs to team `T0266FRGM`. (`slack_oauth.exchange_code`)
- [x] Never log tokens; scrub logs. (worker logs Slack user ids only)
- [x] HTTPS only; secure session cookies. (`SESSION_COOKIE_*`; `COOKIE_SECURE=1`
      behind HTTPS — operator must terminate TLS via the reverse proxy.)
- [x] Handle token revocation gracefully (disable user, prompt re-auth).
      (`AuthFailure` → `disconnected`; re-running "Add to Slack" reactivates.)
