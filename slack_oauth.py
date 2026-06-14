
import os
import secrets
from urllib.parse import urlencode

import requests

# User-token scopes (we modify the signed-in user's own profile + photo).
USER_SCOPES = [
    "users.profile:write",  # set status text/emoji
    "users.profile:read",
    "users:write",          # users.setPhoto
    "emoji:read",           # validate/preview custom emojis
]

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
ACCESS_URL = "https://slack.com/api/oauth.v2.access"


def client_id() -> str:
    return os.getenv("SLACK_CLIENT_ID", "")


def client_secret() -> str:
    return os.getenv("SLACK_CLIENT_SECRET", "")


def redirect_uri() -> str:
    return os.getenv("SLACK_REDIRECT_URI", "")


def allowed_team_id() -> str:
    return os.getenv("ALLOWED_TEAM_ID", "T0266FRGM")


def is_configured() -> bool:
    return bool(client_id() and client_secret() and redirect_uri())


def new_state() -> str:
    """A CSRF state token to store in the session and verify on callback."""
    return secrets.token_urlsafe(32)


def authorize_url(state: str) -> str:
    """Build the Slack consent URL. ``team`` pre-selects the Hack Club workspace."""
    params = {
        "client_id": client_id(),
        "user_scope": ",".join(USER_SCOPES),
        "redirect_uri": redirect_uri(),
        "state": state,
        "team": allowed_team_id(),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


class OAuthError(Exception):
    """Raised when the OAuth exchange fails or the workspace is not allowed."""


def exchange_code(code: str) -> dict:
    """Exchange an OAuth code for a per-user token.

    Returns {"slack_user_id", "team_id", "user_token"}. Raises OAuthError on
    failure or if the install is for a workspace other than the allowed team.
    """
    resp = requests.post(
        ACCESS_URL,
        data={
            "client_id": client_id(),
            "client_secret": client_secret(),
            "code": code,
            "redirect_uri": redirect_uri(),
        },
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        raise OAuthError(data.get("error", "oauth_exchange_failed"))

    team_id = (data.get("team") or {}).get("id", "")
    if team_id != allowed_team_id():
        raise OAuthError("wrong_workspace")

    authed_user = data.get("authed_user") or {}
    user_token = authed_user.get("access_token", "")
    slack_user_id = authed_user.get("id", "")
    if not user_token or not slack_user_id:
        raise OAuthError("missing_user_token")

    return {
        "slack_user_id": slack_user_id,
        "team_id": team_id,
        "user_token": user_token,
    }
