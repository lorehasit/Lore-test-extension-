"""Lore engine — the reusable core behind every surface (VS Code, web, Slack).

Two modes, chosen automatically from the environment:

  MOCK  (no GROQ_API_KEY)  — answers /why with a keyword retriever over the
                             seed corpus. Zero keys, zero cost, always runnable.
  LIVE  (GROQ_API_KEY set) — real mem0 memory (Groq LLM + local fastembed
                             embeddings), real GitHub PR ingestion, LLM-composed
                             cited answers.

mem0 and groq are imported lazily so mock mode has no heavy dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from typing import Optional

from dotenv import load_dotenv

import github_app as gh
from seed_decisions import SEED_DECISIONS

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
EMBEDDER_MODEL = os.getenv("EMBEDDER_MODEL", "thenlper/gte-large").strip()
# Vector-store dimension MUST match the embedder's output, or mem0's default
# (1536) collides with it. gte-large=1024, bge-small-en-v1.5=384, bge-base=768.
EMBEDDER_DIMS = int(os.getenv("EMBEDDER_DIMS", "1024").strip())
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()

# How far back the install-time backfill reaches, in days.
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "15").strip())

# When a query arrives with the placeholder user_id ("demo"), answer against
# this GitHub account's Canon instead — so /why spans every repo the App
# indexed for that account. Set to the org/user login (e.g. "acme-inc").
# Only used when multi-tenant auth is OFF (see LORE_API_KEYS).
LORE_DEFAULT_ACCOUNT = os.getenv("LORE_DEFAULT_ACCOUNT", "").strip()

# Multi-tenant auth (opt-in). A comma-separated list of `key:login` pairs maps
# each secret API key to the GitHub account whose Canon it may read. When set,
# every read endpoint REQUIRES a valid key and answers ONLY that key's account —
# so different users on the same backend can never see each other's data.
#   LORE_API_KEYS=lk_alice_secret:alice-org, lk_bob_secret:bob
# When empty, the backend stays single-tenant (uses LORE_DEFAULT_ACCOUNT).
def _parse_api_keys(raw: str) -> dict:
    out = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, login = pair.split(":", 1)
        key, login = key.strip(), login.strip()
        if key and login:
            out[key] = login
    return out


_API_KEYS = _parse_api_keys(os.getenv("LORE_API_KEYS", "").strip())

# Where the Canon (vector memory) lives. "qdrant" = local file (offline dev);
# "pgvector" = hosted Postgres (the shared team brain) via DATABASE_URL.
VECTOR_STORE = os.getenv("VECTOR_STORE", "qdrant").strip().lower()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

MODE = "live" if GROQ_API_KEY else "mock"

# ---------------------------------------------------------------------------
# Lazy mem0 memory (only built in live mode, only once).
# ---------------------------------------------------------------------------
_memory = None


def _vector_store_config():
    """The Canon's storage. Local Qdrant by default; hosted pgvector when
    VECTOR_STORE=pgvector (one Postgres holds both vectors and app data)."""
    collection = f"lore_{EMBEDDER_DIMS}"
    if VECTOR_STORE == "pgvector":
        if not DATABASE_URL:
            raise RuntimeError("VECTOR_STORE=pgvector requires DATABASE_URL in .env")
        return {
            "provider": "pgvector",
            "config": {
                "connection_string": DATABASE_URL,
                "collection_name": collection,
                "embedding_model_dims": EMBEDDER_DIMS,
            },
        }
    return {
        "provider": "qdrant",
        "config": {
            # Collection name encodes dims so switching embedder models never
            # collides with a stale, wrong-sized collection.
            "collection_name": collection,
            "embedding_model_dims": EMBEDDER_DIMS,
            "path": "qdrant_data",
        },
    }


_np_vector_adapter_done = False


def _register_numpy_vector_adapter():
    """psycopg3 can't hand a raw NumPy array to Postgres, but our local embedder
    (fastembed) returns NumPy arrays. Register a dumper that serialises them to
    pgvector's text form (`[1,2,3]`) so mem0's pgvector store accepts them."""
    global _np_vector_adapter_done
    if _np_vector_adapter_done:
        return
    try:
        import numpy as np
        import psycopg
        from psycopg.adapt import Dumper

        class _NumpyVectorDumper(Dumper):
            def dump(self, obj):
                return b"[" + b",".join(repr(float(x)).encode() for x in obj.tolist()) + b"]"

        psycopg.adapters.register_dumper(np.ndarray, _NumpyVectorDumper)
        _np_vector_adapter_done = True
    except Exception:
        pass


def get_memory():
    """Build the mem0 Memory on first use. LLM and embedder are independent,
    swappable knobs — moving Groq -> Claude later touches only the `llm` block."""
    global _memory
    if _memory is None:
        if VECTOR_STORE == "pgvector":
            _register_numpy_vector_adapter()
        from mem0 import Memory  # imported lazily; heavy

        config = {
            "llm": {
                "provider": "groq",
                "config": {"model": GROQ_MODEL, "api_key": GROQ_API_KEY},
            },
            "embedder": {
                "provider": "fastembed",  # local, free, no key
                "config": {"model": EMBEDDER_MODEL},
            },
            "vector_store": _vector_store_config(),
        }
        _memory = Memory.from_config(config)
    return _memory


# ---------------------------------------------------------------------------
# MOCK retrieval — token-overlap scoring over the seed corpus.
# ---------------------------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9\s]", " ", text.lower()).split() if t]


def _retrieve_seed(query: str) -> Optional[dict]:
    q = _tokenize(query)
    best, best_score = None, 0
    for d in SEED_DECISIONS:
        score = 0
        for t in q:
            if t in d["keys"]:
                score += 3
            elif len(t) > 3 and any(k.startswith(t) for k in d["keys"]):
                score += 2
            if len(t) > 3 and t in d["title"].lower():
                score += 1
        if score > best_score:
            best, best_score = d, score
    return best if best_score > 0 else None


# ---------------------------------------------------------------------------
# Account scoping — the Canon is partitioned per GitHub account so one org's
# decisions never leak into another's, while every repo under that account is
# searchable together.
# ---------------------------------------------------------------------------
def account_scope(login: str) -> str:
    """The mem0 user_id under which an account's whole Canon lives."""
    return f"gh:{(login or '').strip().lower()}" if login else "demo"


def _resolve_user_id(user_id: str) -> str:
    """Map the extension's placeholder scope onto the configured account, so a
    plain /why answers across all of that account's repos out of the box.
    (Single-tenant path — used only when auth is off.)"""
    if user_id in ("", "demo") and LORE_DEFAULT_ACCOUNT:
        return account_scope(LORE_DEFAULT_ACCOUNT)
    return user_id or "demo"


# ---------------------------------------------------------------------------
# Multi-tenant auth. When LORE_API_KEYS is set, a caller's scope is derived
# from their key and cannot be overridden — real isolation between accounts.
# ---------------------------------------------------------------------------
def auth_enabled() -> bool:
    return bool(_API_KEYS)


def account_for_key(key: str) -> Optional[str]:
    """The GitHub login a key is authorized for, or None if the key is unknown."""
    return _API_KEYS.get((key or "").strip())


def scope_for_key(key: str) -> Optional[str]:
    """The Canon scope (gh:<login>) a key may read, or None if invalid."""
    login = account_for_key(key)
    return account_scope(login) if login else None


def resolve_scope(user_id: str, api_key: str):
    """Decide which Canon scope a request may touch.

    Returns (scope, error). When auth is on, the scope comes from the API key and
    `user_id` is ignored (no cross-account reads). When off, falls back to the
    single-tenant default. `error` is a short string when the request is rejected.
    """
    if auth_enabled():
        scope = scope_for_key(api_key)
        if not scope:
            return None, "missing or invalid Lore API key"
        return scope, None
    return _resolve_user_id(user_id), None


# Live, in-memory record of the most recent install backfill, surfaced via
# /backfill/status so the extension can show "indexing…" progress.
_backfill = {
    "state": "idle",   # idle | running | done | error
    "account": None,
    "repos_total": 0,
    "repos_done": 0,
    "prs_captured": 0,
    "comments_posted": 0,
    "current_repo": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_backfill_lock = threading.Lock()


def backfill_status() -> dict:
    with _backfill_lock:
        return dict(_backfill)


# ---------------------------------------------------------------------------
# Public API: status / ingest / answer
# ---------------------------------------------------------------------------
def status() -> dict:
    return {
        "mode": MODE,
        "llm": f"groq:{GROQ_MODEL}" if MODE == "live" else None,
        "embedder": f"fastembed:{EMBEDDER_MODEL}" if MODE == "live" else None,
        "canon_store": VECTOR_STORE,
        "github_ingestion": bool(GITHUB_TOKEN) and MODE == "live",
        "github_app": gh.app_configured(),
        "auth": "multi-tenant" if auth_enabled() else "single-tenant",
        "tenants": len(_API_KEYS) if auth_enabled() else None,
        "default_account": None if auth_enabled() else (
            account_scope(LORE_DEFAULT_ACCOUNT) if LORE_DEFAULT_ACCOUNT else None),
        "backfill_days": BACKFILL_DAYS,
        "backfill": backfill_status(),
        "decisions_in_seed": len(SEED_DECISIONS),
    }


def inscribe_commit(commit: dict, user_id: str = "demo") -> dict:
    """Inscribe a commit's reasoning (its `Why:`) into the Canon.

    Called by the Scribe (npm git hook). Only commits that declared a `Why:`
    reach here, so the Canon stays high-signal.
    """
    sha = str(commit.get("hash", ""))[:7] or "unknown"
    subject = (commit.get("message") or commit.get("subject") or "").strip()
    why = (commit.get("why") or "").strip()
    author = (commit.get("author") or "").strip()
    canon = (commit.get("repo") or commit.get("canon") or "").strip()

    text = f"{subject}\n\nWhy: {why}" if why else subject
    if not text.strip():
        return {"error": "empty commit"}
    if MODE == "mock":
        return {"mode": "mock", "inscribed": False,
                "note": "mock mode does not persist — set GROQ_API_KEY to inscribe"}

    mem = get_memory()
    mem.add(text, user_id=user_id, infer=False, metadata={
        "source": f"commit {sha}",
        "title": subject[:80],
        "author": author,
        "canon": canon,
    })
    return {"mode": "live", "inscribed": True, "provenance": f"commit {sha}"}


def verify_github_signature(raw: bytes, signature_header: str) -> bool:
    """Confirm a webhook really came from GitHub: HMAC-SHA256 over the raw body,
    keyed by the webhook secret. If no secret is configured (local dev), skip."""
    if not GITHUB_WEBHOOK_SECRET:
        return True
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(), raw, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _strip_bot_noise(text: str) -> str:
    """Remove machine-generated boilerplate (Vercel/bot deploy comments, base64
    status blobs, HTML comments) so summaries and memories reflect real content."""
    if not text:
        return ""
    # Drop the whole "Discussion:" tail if it's only bot chatter, and any long
    # base64-ish blobs the deploy bots leave behind.
    text = re.sub(r"\[vc\]:\s*#\S+", " ", text)              # vercel status token
    text = re.sub(r"\b[A-Za-z0-9+/]{80,}={0,2}\b", " ", text)  # base64 blobs
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)      # html comments
    text = re.sub(r"🤖 Generated with .*$", "", text, flags=re.S)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _summarize_pr(title: str, body: str, threads: str) -> tuple[str, str]:
    """Ask Groq for (understanding, remember): a short read of what the PR does
    and the one-line decision Lore will store. Falls back to the title/body if
    the LLM is unavailable, so the comment always posts."""
    clean_body = _strip_bot_noise(body)
    clean_threads = _strip_bot_noise(threads)
    fallback = (
        (clean_body.split("\n", 1)[0][:240] or title).strip(),
        f"{title}".strip()[:200],
    )
    if MODE != "live" or not GROQ_API_KEY:
        return fallback

    context = f"Title: {title}\n\nDescription:\n{clean_body or '(none)'}"
    if clean_threads:
        context += f"\n\nReview discussion:\n{clean_threads[:2000]}"
    prompt = (
        "You are Lore, an engineering team's decision memory, reacting to a pull "
        "request that was just opened. Read it and respond in EXACTLY this format, "
        "nothing else:\n"
        "UNDERSTOOD: <2-3 sentences: what this PR changes and, if stated, WHY. "
        "Be concrete and technical. Do not invent anything not in the text.>\n"
        "REMEMBER: <one line: the key decision/rationale you'll store in the Canon.>\n\n"
        f"{context}\n"
    )
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        out = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        ).choices[0].message.content.strip()
    except Exception:
        return fallback

    understood, remember = fallback
    m1 = re.search(r"UNDERSTOOD:\s*(.+?)(?:\nREMEMBER:|\Z)", out, re.S)
    m2 = re.search(r"REMEMBER:\s*(.+)\Z", out, re.S)
    if m1:
        understood = m1.group(1).strip()
    if m2:
        remember = m2.group(1).strip()
    return understood, remember


def _pr_understanding_comment(title: str, body: str, threads: str) -> str:
    """Lore's per-PR comment: a brief read of the PR and what it will remember."""
    understood, remember = _summarize_pr(title, body, threads)
    return (
        "## 🧠 Lore\n\n"
        "**What I understood from this PR**\n\n"
        f"{understood}\n\n"
        "**What I'll remember**\n\n"
        f"> {remember}\n\n"
        "_I inscribe this into the Canon when the PR merges — then anyone can ask "
        "why it was done via `npx lore recall \"…\"` or the Lore editor extension._"
    )


def handle_pull_request_event(payload: dict, user_id: str = "demo") -> dict:
    """React to pull-request webhooks:
      • opened / reopened / ready_for_review → comment with related past decisions.
      • closed & merged → inscribe the PR (a merge = a finalized decision).
    """
    action = payload.get("action")
    pr = payload.get("pull_request") or {}
    repo_full = (payload.get("repository") or {}).get("full_name", "")
    owner = repo_full.split("/")[0] if "/" in repo_full else ""
    repo_name = repo_full.split("/")[1] if "/" in repo_full else repo_full
    number = pr.get("number")
    title = pr.get("title", "")
    body = (pr.get("body") or "").strip()
    author = (pr.get("user") or {}).get("login", "")
    url = pr.get("html_url", "")
    install_id = (payload.get("installation") or {}).get("id")
    scope = account_scope(owner)

    # --- A new PR: post Lore's read of it and what it will remember ---
    if action in ("opened", "reopened", "ready_for_review"):
        if MODE == "mock":
            return {"ok": True, "commented": False, "mode": "mock", "pr": number}
        if not (install_id and gh.app_configured()):
            return {"ok": True, "commented": False,
                    "reason": "App auth not configured — cannot post comment"}
        token = gh.installation_token(install_id)
        if not token:
            return {"ok": True, "commented": False, "reason": "no installation token"}
        threads = gh.fetch_pr_threads(token, owner, repo_name, number)
        comment = _pr_understanding_comment(title, body, threads)
        posted = gh.post_issue_comment(token, owner, repo_name, number, comment)
        return {"ok": True, "commented": posted, "pr": number, "action": action}

    # --- Otherwise only merged PRs are captured into the Canon ---
    if action != "closed" or not pr.get("merged", False):
        return {"ok": True, "captured": False,
                "reason": f"action={action}, merged={pr.get('merged')}"}

    if MODE == "mock":
        return {"ok": True, "captured": False, "mode": "mock",
                "pr": number, "note": "set GROQ_API_KEY to capture"}

    # Pull the review discussion too, if the App can authenticate for this install.
    threads = ""
    if install_id and gh.app_configured():
        token = gh.installation_token(install_id)
        if token:
            threads = gh.fetch_pr_threads(token, owner, repo_name, number)

    clean_body = _strip_bot_noise(body)
    threads = _strip_bot_noise(threads)
    text = f"PR #{number}: {title}" + (f"\n\n{clean_body}" if clean_body else "")
    if threads:
        text += f"\n\nDiscussion:\n{threads}"

    mem = get_memory()
    mem.add(text, user_id=scope, infer=False, metadata={
        "source": f"PR #{number}",
        "title": title[:80],
        "author": author,
        "canon": repo_full,
        "repo": repo_full,
        "url": url,
    })
    return {"ok": True, "captured": True, "pr": number, "repo": repo_full, "scope": scope}


# ---------------------------------------------------------------------------
# Install-time backfill — the moment the App is installed, capture the last
# BACKFILL_DAYS of PRs across EVERY selected repo, then greet open PRs.
# ---------------------------------------------------------------------------
def _pr_text(pr: dict, threads: str) -> str:
    number, title = pr.get("number"), pr.get("title", "")
    body = _strip_bot_noise((pr.get("body") or "").strip())
    threads = _strip_bot_noise(threads)
    state = "merged" if pr.get("merged_at") else pr.get("state", "open")
    text = f"PR #{number} ({state}): {title}"
    if body:
        text += f"\n\n{body}"
    if threads:
        text += f"\n\nDiscussion:\n{threads}"
    return text


def _welcome_body(account: str, repos: list[str]) -> str:
    """CodeRabbit-style greeting posted on open PRs when Lore is installed."""
    repo_list = ", ".join(f"`{r}`" for r in repos[:8])
    more = "" if len(repos) <= 8 else f" (+{len(repos) - 8} more)"
    return (
        "## 🧠 Lore is now active\n\n"
        "Thanks for installing **Lore** — your team's engineering decision memory.\n\n"
        f"I'm indexing the **last {BACKFILL_DAYS} days** of pull requests "
        f"(titles, descriptions **and** review discussion) across the selected "
        f"repositories for **{account}**:\n\n> {repo_list}{more}\n\n"
        "Once indexing finishes, ask me *why* anything was built the way it is — "
        "in your editor via the Lore extension or `npx lore recall \"...\"` — and "
        "I'll answer from the real decisions across **all** these repos, with "
        "citations back to the PRs.\n\n"
        "_No action needed. This comment is a one-time hello._"
    )


def backfill_installation(installation_id: int, account: str,
                          repos: list[dict], post_welcome: bool = True) -> dict:
    """Worker (run in a background thread): index recent PRs for every repo,
    then leave a welcome comment on each open PR. Progress is published to the
    module-level `_backfill` record for /backfill/status."""
    scope = account_scope(account)
    since = gh.days_ago(BACKFILL_DAYS)
    repo_names = [r.get("full_name") or f"{account}/{r.get('name','')}" for r in repos]

    with _backfill_lock:
        _backfill.update({
            "state": "running", "account": scope, "repos_total": len(repos),
            "repos_done": 0, "prs_captured": 0, "comments_posted": 0,
            "current_repo": None, "error": None,
            "started_at": time.time(), "finished_at": None,
        })

    if MODE != "live":
        with _backfill_lock:
            _backfill.update({"state": "error",
                              "error": "live mode required (set GROQ_API_KEY)",
                              "finished_at": time.time()})
        return backfill_status()

    token = gh.installation_token(installation_id)
    if not token:
        with _backfill_lock:
            _backfill.update({"state": "error",
                              "error": "could not mint installation token "
                                       "(check GITHUB_APP_ID / private key)",
                              "finished_at": time.time()})
        return backfill_status()

    mem = get_memory()
    captured = comments = 0
    try:
        for r in repos:
            full = r.get("full_name") or f"{account}/{r.get('name','')}"
            owner, _, name = full.partition("/")
            with _backfill_lock:
                _backfill["current_repo"] = full

            for pr in gh.list_recent_prs(token, owner, name, since):
                threads = gh.fetch_pr_threads(token, owner, name, pr["number"])
                mem.add(_pr_text(pr, threads), user_id=scope, infer=False, metadata={
                    "source": f"PR #{pr['number']}",
                    "title": (pr.get("title") or "")[:80],
                    "author": (pr.get("user") or {}).get("login", ""),
                    "canon": full,
                    "repo": full,
                    "url": pr.get("html_url", ""),
                })
                captured += 1
                with _backfill_lock:
                    _backfill["prs_captured"] = captured

            if post_welcome:
                body = _welcome_body(account, repo_names)
                for pr in gh.list_open_prs(token, owner, name):
                    if gh.post_issue_comment(token, owner, name, pr["number"], body):
                        comments += 1
                        with _backfill_lock:
                            _backfill["comments_posted"] = comments

            with _backfill_lock:
                _backfill["repos_done"] += 1

        with _backfill_lock:
            _backfill.update({"state": "done", "current_repo": None,
                              "finished_at": time.time()})
    except Exception as e:  # never let a background thread die silently
        with _backfill_lock:
            _backfill.update({"state": "error", "error": f"{type(e).__name__}: {e}",
                              "finished_at": time.time()})
    return backfill_status()


def trigger_backfill(account: Optional[str] = None) -> dict:
    """Manually start a backfill without waiting for a webhook. Looks up the
    App's installations (as the App itself), optionally filters to `account`,
    and runs the backfill for the match in a background thread.

    This is what to call when the App is already installed (so GitHub won't
    re-send `installation.created`) and you just want to index now."""
    if not gh.app_configured():
        return {"ok": False, "error": "GitHub App auth not configured "
                "(set GITHUB_APP_ID + GITHUB_APP_PRIVATE_KEY)"}
    if MODE != "live":
        return {"ok": False, "error": "live mode required (set GROQ_API_KEY)"}

    installs = gh.list_app_installations()
    if not installs:
        return {"ok": False, "error": "App has no installations, or the App "
                "JWT was rejected (check GITHUB_APP_ID / private key)"}

    if account:
        want = account.strip().lower()
        installs = [i for i in installs
                    if (i.get("account") or {}).get("login", "").lower() == want]
        if not installs:
            return {"ok": False, "error": f"App is not installed on '{account}'. "
                    "Install it there first."}

    inst = installs[0]
    installation_id = inst.get("id")
    login = (inst.get("account") or {}).get("login", "")

    token = gh.installation_token(installation_id)
    repos = gh.list_installation_repos(token) if token else []
    if not repos:
        return {"ok": False, "account": login,
                "error": "no repositories visible to this installation"}

    threading.Thread(
        target=backfill_installation,
        args=(installation_id, login, repos),
        daemon=True,
    ).start()
    return {"ok": True, "backfill": "started", "account": login,
            "repos": len(repos), "days": BACKFILL_DAYS}


def handle_installation_event(payload: dict) -> dict:
    """Webhook entrypoint for `installation` / `installation_repositories`.

    Fires on install and on repos-added. Kicks off the backfill in a background
    thread so the webhook responds immediately (GitHub times out at ~10s)."""
    action = payload.get("action")
    # Any of these can mean "there are (new) repos to index now": a fresh install,
    # repos added, permissions just accepted, or an install un-suspended.
    if action not in ("created", "added", "new_permissions_accepted", "unsuspend"):
        return {"ok": True, "ignored": f"installation action={action}"}

    inst = payload.get("installation") or {}
    installation_id = inst.get("id")
    account = (inst.get("account") or {}).get("login", "")

    # `installation` payloads carry `repositories`; `installation_repositories`
    # carry `repositories_added`. Either may be absent for "all repos" installs.
    repos = payload.get("repositories") or payload.get("repositories_added") or []

    if not gh.app_configured():
        return {"ok": True, "captured": False,
                "note": "GitHub App auth not configured — set GITHUB_APP_ID and "
                        "GITHUB_APP_PRIVATE_KEY to enable backfill"}
    if not installation_id:
        return {"ok": True, "captured": False, "note": "no installation id"}

    # If GitHub didn't enumerate repos (an "all repositories" install), ask the API.
    if not repos:
        token = gh.installation_token(installation_id)
        if token:
            repos = gh.list_installation_repos(token)

    if not repos:
        return {"ok": True, "captured": False, "account": account,
                "note": "no repositories to index"}

    threading.Thread(
        target=backfill_installation,
        args=(installation_id, account, repos),
        daemon=True,
    ).start()

    return {"ok": True, "backfill": "started", "account": account,
            "repos": len(repos), "days": BACKFILL_DAYS}


def search_canon(query: str, user_id: str = "demo", limit: int = 8) -> dict:
    """Free search across the Canon (the `/lore` command) — returns matching
    Whys with their provenance, without composing a narrative answer."""
    query = (query or "").strip()
    if not query:
        return {"mode": MODE, "count": 0, "results": []}

    if MODE == "mock":
        hit = _retrieve_seed(query)
        results = ([{"why": hit["answer"], "provenance": hit["sources"]}] if hit else [])
        return {"mode": "mock", "count": len(results), "results": results}

    mem = get_memory()
    res = mem.search(query, filters={"user_id": _resolve_user_id(user_id)}, limit=limit)
    hits = res.get("results", []) if isinstance(res, dict) else (res or [])
    results = [{"why": h.get("memory", ""),
                "provenance": h.get("metadata", {}).get("source", "memory")} for h in hits]
    return {"mode": "live", "count": len(results), "results": results}


def list_memories(user_id: str = "demo") -> dict:
    """Return exactly what's stored in memory right now (for inspection)."""
    if MODE == "mock":
        return {"mode": "mock", "count": len(SEED_DECISIONS),
                "memories": [{"memory": d["answer"],
                              "source": (d["sources"][0][1] if d["sources"] else d["title"])}
                             for d in SEED_DECISIONS]}
    mem = get_memory()
    res = mem.get_all(filters={"user_id": _resolve_user_id(user_id)})
    items = res.get("results", []) if isinstance(res, dict) else (res or [])
    out = [{"memory": m.get("memory", ""),
            "source": m.get("metadata", {}).get("source", "?")} for m in items]
    return {"mode": "live", "count": len(out), "memories": out}


def ingest_seed(user_id: str = "demo") -> dict:
    """Load the seed decisions into memory. In mock mode they're always
    searchable, so this is a no-op that just reports readiness."""
    if MODE == "mock":
        return {"mode": "mock", "ingested": len(SEED_DECISIONS),
                "note": "seed corpus is always searchable in mock mode"}
    mem = get_memory()
    n = 0
    for d in SEED_DECISIONS:
        source = d["sources"][0][1] if d["sources"] else d["title"]
        # infer=False: store our already-clean text verbatim, skipping mem0's
        # heavy LLM extraction (which blows the free-tier token/min limit).
        mem.add(d["answer"], user_id=user_id, infer=False,
                metadata={"source": source, "title": d["title"], "area": d["meta"]})
        n += 1
    return {"mode": "live", "ingested": n}


def ingest_repo(owner: str, repo: str, user_id: str = "demo", limit: int = 25) -> dict:
    """Pull merged PRs from a GitHub repo and store them as memory (live only)."""
    if MODE != "live":
        return {"error": "live mode required — set GROQ_API_KEY in .env", "mode": "mock"}
    if not GITHUB_TOKEN:
        return {"error": "GITHUB_TOKEN required for repo ingestion"}

    import requests

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    url = (f"https://api.github.com/repos/{owner}/{repo}/pulls"
           f"?state=closed&per_page={limit}&sort=updated&direction=desc")
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return {"error": f"GitHub API {resp.status_code}", "detail": resp.text[:300]}

    mem = get_memory()
    scope = account_scope(owner)
    n = 0
    for pr in resp.json():
        if not pr.get("merged_at"):
            continue
        body = (pr.get("body") or "").strip()
        text = f"PR #{pr['number']}: {pr['title']}\n\n{body}"
        mem.add(text, user_id=scope, infer=False, metadata={
            "source": f"PR #{pr['number']}",
            "title": pr["title"],
            "canon": f"{owner}/{repo}",
            "repo": f"{owner}/{repo}",
            "url": pr.get("html_url", ""),
        })
        n += 1
    return {"mode": "live", "repo": f"{owner}/{repo}", "ingested": n, "scope": scope}


_NO_MATCH = (
    "I don't have a recorded decision for that yet. In production, Lore keeps "
    "learning from every new PR, thread, and retro — so this gap fills itself "
    "over time. Try asking about auth, the database, microservices, or feature flags."
)


def answer_why(question: str, user_id: str = "demo") -> dict:
    """Answer a /why question. Returns {answer, sources, mode, latency_s}."""
    t0 = time.time()
    question = (question or "").strip()
    if not question:
        return {"answer": "Ask me why something is built the way it is.",
                "sources": [], "mode": MODE, "latency_s": 0.0}

    if MODE == "mock":
        hit = _retrieve_seed(question)
        latency = round(time.time() - t0, 3)
        if not hit:
            return {"answer": _NO_MATCH, "sources": [], "mode": "mock", "latency_s": latency}
        return {"answer": hit["answer"], "sources": hit["sources"],
                "mode": "mock", "latency_s": latency}

    # --- live: semantic search + Groq compose ---
    mem = get_memory()
    # Newer mem0 requires entity scoping via filters= rather than a top-level user_id.
    results = mem.search(question, filters={"user_id": _resolve_user_id(user_id)}, limit=6)
    hits = results.get("results", []) if isinstance(results, dict) else (results or [])

    if not hits:
        return {"answer": _NO_MATCH, "sources": [], "mode": "live",
                "latency_s": round(time.time() - t0, 3)}

    context = "\n".join(
        f"- {h.get('memory', '')} (source: {h.get('metadata', {}).get('source', 'memory')})"
        for h in hits
    )
    prompt = (
        "You are Lore, an engineering team's decision memory. Using ONLY the recorded "
        "decisions below, answer the question. Explain the reasoning and trade-offs, name "
        "who was involved if recorded, and cite sources inline like [PR #482]. If the "
        "decisions don't cover it, say so plainly.\n\n"
        f"Recorded decisions:\n{context}\n\nQuestion: {question}\n\nAnswer:"
    )

    from groq import Groq

    try:
        client = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        answer = completion.choices[0].message.content.strip()
    except Exception as e:
        # Surface the real cause instead of a bare 500. Most common: a rotated/
        # invalid GROQ_API_KEY (restart the backend after editing .env), or rate limits.
        return {
            "answer": f"⚠️ Groq call failed: {type(e).__name__}: {str(e)[:200]}. "
                      "Most likely an invalid/rotated GROQ_API_KEY — update .env and "
                      "RESTART the backend. (Or you hit Groq rate limits.)",
            "sources": [], "mode": "live", "error": True,
            "latency_s": round(time.time() - t0, 3),
        }

    seen, sources = set(), []
    for h in hits:
        src = h.get("metadata", {}).get("source", "memory")
        if src in seen:
            continue
        seen.add(src)
        kind = "PR" if str(src).lower().startswith("pr") else "memory"
        sources.append([kind, src])

    # Prefer only the sources the model actually cited; fall back to the top
    # couple of hits if it cited none explicitly. Keeps the chips relevant.
    cited = [s for s in sources if s[1] and s[1] in answer]
    sources = cited or sources[:2]

    return {"answer": answer, "sources": sources, "mode": "live",
            "latency_s": round(time.time() - t0, 3)}
