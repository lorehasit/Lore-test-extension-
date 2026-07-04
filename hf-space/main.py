"""Lore backend — FastAPI service the VS Code extension (and future surfaces) call.

Run:
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000

Works with zero keys (mock mode). Add GROQ_API_KEY to .env to go live.
"""

import json

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import lore_engine as engine

app = FastAPI(title="Lore", version="0.1.0")

# VS Code webviews call from an opaque origin; allow all for local dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class WhyRequest(BaseModel):
    question: str
    user_id: str = "demo"


class LoreSearch(BaseModel):
    query: str
    user_id: str = "demo"


class CommitPayload(BaseModel):
    hash: str = ""
    message: str = ""
    why: str = ""
    author: str = ""
    repo: str = ""
    branch: str = ""
    user_id: str = "demo"


class IngestRepoRequest(BaseModel):
    owner: str
    repo: str
    user_id: str = "demo"


# ---------------------------------------------------------------------------
# Auth helpers. A caller presents their key via `Authorization: Bearer <key>`,
# an `X-Lore-Key` header, or `?key=`. When multi-tenant auth is on, the scope is
# derived from the key and a caller can never read another account's Canon.
# ---------------------------------------------------------------------------
def _api_key(authorization: str, x_lore_key: str, key: str) -> str:
    if x_lore_key:
        return x_lore_key.strip()
    if key:
        return key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _scope(user_id: str, authorization: str, x_lore_key: str, key: str) -> str:
    scope, err = engine.resolve_scope(user_id, _api_key(authorization, x_lore_key, key))
    if err:
        raise HTTPException(status_code=401, detail=err)
    return scope


def _require_account(account: str, authorization: str, x_lore_key: str, key: str) -> str:
    """For admin actions bound to a GitHub account (backfill/ingest). When auth
    is on, the key's account must match `account`; returns the effective login."""
    if not engine.auth_enabled():
        return account
    login = engine.account_for_key(_api_key(authorization, x_lore_key, key))
    if not login:
        raise HTTPException(status_code=401, detail="missing or invalid Lore API key")
    if account and account.lower() != login.lower():
        raise HTTPException(status_code=403, detail="key not authorized for this account")
    return account or login


@app.get("/health")
def health():
    return {"ok": True, **engine.status()}


@app.get("/memories")
def memories(user_id: str = "demo", authorization: str = Header(None),
             x_lore_key: str = Header(None), key: str = ""):
    return engine.list_memories(_scope(user_id, authorization, x_lore_key, key))


@app.post("/ingest/seed")
def ingest_seed(user_id: str = "demo", authorization: str = Header(None),
                x_lore_key: str = Header(None), key: str = ""):
    return engine.ingest_seed(_scope(user_id, authorization, x_lore_key, key))


@app.post("/ingest/repo")
def ingest_repo(req: IngestRepoRequest, authorization: str = Header(None),
                x_lore_key: str = Header(None), key: str = ""):
    _require_account(req.owner, authorization, x_lore_key, key)
    return engine.ingest_repo(req.owner, req.repo, req.user_id)


@app.post("/why")
def why(req: WhyRequest, authorization: str = Header(None),
        x_lore_key: str = Header(None), key: str = ""):
    """Recall a decision — composed answer with provenance."""
    return engine.answer_why(req.question, _scope(req.user_id, authorization, x_lore_key, key))


@app.post("/lore")
def lore(req: LoreSearch, authorization: str = Header(None),
         x_lore_key: str = Header(None), key: str = ""):
    """Free search across the Canon — matching Whys with provenance."""
    return engine.search_canon(req.query, _scope(req.user_id, authorization, x_lore_key, key))


@app.post("/inscribe")
def inscribe(c: CommitPayload, authorization: str = Header(None),
             x_lore_key: str = Header(None), key: str = ""):
    """The Scribe inscribes a commit's Why into the Canon."""
    return engine.inscribe_commit(c.model_dump(),
                                  _scope(c.user_id, authorization, x_lore_key, key))


@app.get("/canon")
def canon(user_id: str = "demo", authorization: str = Header(None),
          x_lore_key: str = Header(None), key: str = ""):
    """Everything currently in the Canon."""
    return engine.list_memories(_scope(user_id, authorization, x_lore_key, key))


@app.get("/backfill/status")
def backfill_status():
    """Progress of the most recent install-time backfill (for the extension's
    'indexing…' indicator)."""
    return engine.backfill_status()


@app.post("/backfill/run")
def backfill_run(account: str = "", authorization: str = Header(None),
                 x_lore_key: str = Header(None), key: str = ""):
    """Manually kick off a backfill for an already-installed account (no webhook
    needed). `account` = the org/user login; omit to use the first installation.
    With auth on, the key's account is used and must match `account` if given."""
    account = _require_account(account, authorization, x_lore_key, key)
    return engine.trigger_backfill(account or None)


@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
):
    """GitHub App webhook — auto-captures merged PRs into the Canon."""
    raw = await request.body()
    if not engine.verify_github_signature(raw, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")
    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    if x_github_event not in ("pull_request", "installation", "installation_repositories"):
        return {"ok": True, "ignored": x_github_event}
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    # Install (or repos-added) → backfill the last N days across all selected repos
    # and greet open PRs. PR merges → capture that single decision.
    if x_github_event in ("installation", "installation_repositories"):
        return engine.handle_installation_event(payload)
    return engine.handle_pull_request_event(payload)
