"""GitHub App authentication + REST helpers.

The webhook-secret alone proves a webhook is genuine, but it can't *act* on
GitHub (read every selected repo, post a welcome comment). For that we need the
App's own identity:

    App JWT  (signed with the App's private key, RS256)
        └─> Installation access token  (per-installation, short-lived)
                └─> normal REST calls scoped to that installation's repos

Everything here is best-effort: if the App isn't configured (no App ID / private
key), `app_configured()` returns False and callers skip the App-powered paths.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

API = "https://api.github.com"

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "").strip()
# The private key can be supplied inline (paste the whole PEM into the env var,
# newlines and all — or base64-encode it to avoid newline mangling) or as a path
# to the downloaded .pem file.
_PRIVATE_KEY_INLINE = os.getenv("GITHUB_APP_PRIVATE_KEY", "").strip()
_PRIVATE_KEY_PATH = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()


def _load_private_key() -> str:
    """Return the PEM private key text, from inline env or a file path."""
    if _PRIVATE_KEY_INLINE:
        raw = _PRIVATE_KEY_INLINE
        # Allow a base64-encoded PEM (survives env-var systems that strip newlines).
        if "BEGIN" not in raw:
            try:
                raw = base64.b64decode(raw).decode()
            except Exception:
                pass
        # Allow literal "\n" sequences that some dashboards store instead of newlines.
        return raw.replace("\\n", "\n")
    if _PRIVATE_KEY_PATH and os.path.exists(_PRIVATE_KEY_PATH):
        with open(_PRIVATE_KEY_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def app_configured() -> bool:
    """True when we have everything needed to mint installation tokens."""
    return bool(GITHUB_APP_ID) and bool(_load_private_key())


# ---------------------------------------------------------------------------
# App JWT  ->  installation token
# ---------------------------------------------------------------------------
def _app_jwt() -> str:
    """Sign a short-lived JWT as the App itself (RS256, per GitHub's spec)."""
    import jwt  # PyJWT, imported lazily so mock mode needs no crypto deps

    now = int(time.time())
    payload = {
        "iat": now - 60,       # backdate 60s to tolerate clock skew
        "exp": now + 9 * 60,   # GitHub rejects anything over 10 minutes
        "iss": GITHUB_APP_ID,
    }
    return jwt.encode(payload, _load_private_key(), algorithm="RS256")


# Installation tokens are valid ~1h; cache them so a backfill doesn't remint
# on every repo. Keyed by installation id -> (token, expiry_epoch).
_token_cache: dict[int, tuple[str, float]] = {}


def installation_token(installation_id: int) -> Optional[str]:
    """Fetch (and cache) an installation access token for `installation_id`."""
    if not app_configured():
        return None
    cached = _token_cache.get(installation_id)
    if cached and cached[1] - 120 > time.time():
        return cached[0]

    resp = requests.post(
        f"{API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {_app_jwt()}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        return None
    data = resp.json()
    token = data.get("token", "")
    # "2024-01-01T00:00:00Z" -> epoch; default to +55m if absent.
    exp = time.time() + 55 * 60
    if data.get("expires_at"):
        try:
            exp = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            pass
    _token_cache[installation_id] = (token, exp)
    return token


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ---------------------------------------------------------------------------
# REST helpers (all take an installation token)
# ---------------------------------------------------------------------------
def list_app_installations() -> list[dict]:
    """Every account this App is installed on. Authenticated as the App itself
    (JWT), so it works without a webhook — the basis for a manual backfill."""
    if not app_configured():
        return []
    out, page = [], 1
    while True:
        resp = requests.get(
            f"{API}/app/installations?per_page=100&page={page}",
            headers={
                "Authorization": f"Bearer {_app_jwt()}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            break
        batch = resp.json()
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def list_installation_repos(token: str, limit: int = 100) -> list[dict]:
    """Every repo this installation can see. Used when a webhook payload does
    not enumerate repos itself (e.g. an 'all repositories' installation)."""
    repos, page = [], 1
    while True:
        resp = requests.get(
            f"{API}/installation/repositories?per_page=100&page={page}",
            headers=_headers(token), timeout=30,
        )
        if resp.status_code != 200:
            break
        batch = resp.json().get("repositories", [])
        repos.extend(batch)
        if len(batch) < 100 or len(repos) >= limit:
            break
        page += 1
    return repos[:limit]


def list_recent_prs(token: str, owner: str, repo: str, since: datetime,
                    max_prs: int = 100) -> list[dict]:
    """All PRs (open/closed/merged) updated on/after `since`, newest first.

    GitHub's list endpoint has no server-side date filter, so we walk pages
    sorted by `updated` and stop as soon as we pass the cutoff."""
    out, page = [], 1
    while len(out) < max_prs:
        resp = requests.get(
            f"{API}/repos/{owner}/{repo}/pulls"
            f"?state=all&sort=updated&direction=desc&per_page=50&page={page}",
            headers=_headers(token), timeout=30,
        )
        if resp.status_code != 200:
            break
        batch = resp.json()
        if not batch:
            break
        stop = False
        for pr in batch:
            updated = _parse_dt(pr.get("updated_at"))
            if updated and updated < since:
                stop = True
                break
            out.append(pr)
        if stop or len(batch) < 50:
            break
        page += 1
    return out[:max_prs]


def fetch_pr_threads(token: str, owner: str, repo: str, number: int,
                     max_comments: int = 40) -> str:
    """Concatenate a PR's discussion — issue comments + inline review comments —
    into a single block. This is the richest 'why' signal in a PR."""
    lines: list[str] = []
    for kind, path in (
        ("comment", f"issues/{number}/comments"),
        ("review", f"pulls/{number}/comments"),
    ):
        resp = requests.get(
            f"{API}/repos/{owner}/{repo}/{path}?per_page={max_comments}",
            headers=_headers(token), timeout=30,
        )
        if resp.status_code != 200:
            continue
        for c in resp.json():
            author = (c.get("user") or {}).get("login", "?")
            body = (c.get("body") or "").strip()
            if body:
                lines.append(f"[{kind}] @{author}: {body}")
    return "\n".join(lines)


def list_open_prs(token: str, owner: str, repo: str, limit: int = 50) -> list[dict]:
    resp = requests.get(
        f"{API}/repos/{owner}/{repo}/pulls?state=open&per_page={limit}",
        headers=_headers(token), timeout=30,
    )
    return resp.json() if resp.status_code == 200 else []


def post_issue_comment(token: str, owner: str, repo: str, number: int,
                       body: str) -> bool:
    """Comment on a PR/issue (PRs share the issues comment endpoint)."""
    resp = requests.post(
        f"{API}/repos/{owner}/{repo}/issues/{number}/comments",
        headers=_headers(token), json={"body": body}, timeout=30,
    )
    return resp.status_code in (200, 201)


# ---------------------------------------------------------------------------
def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)
