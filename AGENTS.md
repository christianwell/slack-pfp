# AGENTS.md

Guidance for working on **Slack PFP** 

## Project layout

```
app.py            Flask web app (OAuth, dashboard, editor, preview, /healthz)
main.py           background worker loop (polls Last.fm, sets Slack photo/status)
core.py           rendering pipeline + Last.fm/Slack helpers + DEFAULT_CONFIG
db.py             per-user SQLite store (users.db), Fernet-encrypted tokens
slack_oauth.py    Slack OAuth v2 helpers (single-workspace)
assets/           bundled default art (pfp.png, frame.png, holiday overlays)
templates/        dashboard.html, editor.html, landing.html, …
static/           emoji-picker.js
uploads/          per-user uploaded overlays (gitignored; .gitkeep tracked)
```

Bundled images live in `assets/`. Reference them in `DEFAULT_CONFIG` as
`assets/<name>.png`. `core._item_image` also resolves bare legacy filenames
(e.g. `hat.png`) from `assets/` so old stored per-user configs keep working — do
not break that fallback.

## Local development

```bash
pip install -r requirements.txt        # or use the venv
python -m py_compile app.py core.py main.py db.py slack_oauth.py   # quick sanity
```

Always verify before deploying:

- Compile all modules (above).
- Smoke-test rendering changes against `core.create_profile_image` /
  `core.build_base_image` with `core.DEFAULT_CONFIG`.
- For web/route changes, exercise them with Flask's `app.test_client()`
  (set `FERNET_KEY` + `SECRET_KEY` env vars and a temp `db.DB_PATH`).

Never commit or copy secrets/state: `.env`, `users.db*`, `.cache`,
`config.json`, `state.json`, `emoji_cache.json`, `uploads/` (all gitignored).



A healthy worker logs lines like `Now playing: <song> by <artist>` and a periodic
`Cycle: N users in Xs`. `/healthz` returns `{"ok": true, "users": …, "active": …}`.
