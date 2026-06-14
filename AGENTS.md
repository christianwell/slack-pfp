# AGENTS.md

Guidance for working on **Slack PFP** and deploying it to the production host.

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

## Production host

| | |
|---|---|
| SSH | `christian@rpi5` |
| Deploy dir | `/home/christian/slack-pfp` |
| Python | `/home/christian/slack-pfp/venv/bin/python` |
| Worker service | `slack-pfp.service` → `main.py` |
| Web service | `slack-pfp-web.service` → `app.py` (waitress, port `8091`) |

> ⚠️ The deploy dir is `/home/christian/slack-pfp` — **not**
> `/home/christian/vscode/slack-pfp` (that path is a stray copy; ignore it).

## Deploying changes

`scp` on this host needs the legacy protocol flag **`-O`** (the remote has no
sftp subsystem). From the workspace root:

```bash
# 1. Copy changed source files (only what you changed)
scp -O app.py core.py main.py db.py slack_oauth.py \
    README.md goals.md LICENSE AGENTS.md \
    christian@rpi5:/home/christian/slack-pfp/

# 2. Copy changed templates / static / assets as needed
scp -O templates/*.html christian@rpi5:/home/christian/slack-pfp/templates/
scp -O static/*         christian@rpi5:/home/christian/slack-pfp/static/
scp -O assets/*         christian@rpi5:/home/christian/slack-pfp/assets/

# 3. Verify it imports on the server, then restart both services
ssh christian@rpi5 "cd /home/christian/slack-pfp && \
  venv/bin/python -m py_compile app.py core.py main.py db.py slack_oauth.py && \
  sudo systemctl restart slack-pfp.service slack-pfp-web.service"

# 4. Confirm healthy
ssh christian@rpi5 "systemctl is-active slack-pfp.service slack-pfp-web.service && \
  curl -s http://127.0.0.1:8091/healthz"
```

When deploying **renamed/moved assets**, also delete the stale originals on the
server (e.g. `ssh christian@rpi5 "rm -f /home/christian/slack-pfp/<old>"`), and
never overwrite the server's `.env`, `users.db*`, or `uploads/`.

## Checking logs

```bash
ssh christian@rpi5 "sudo journalctl -u slack-pfp.service -u slack-pfp-web.service \
  --since '2 minutes ago' --no-pager | tail -30"
```

A healthy worker logs lines like `Now playing: <song> by <artist>` and a periodic
`Cycle: N users in Xs`. `/healthz` returns `{"ok": true, "users": …, "active": …}`.
