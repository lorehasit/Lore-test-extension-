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


@app.get("/health")
def health():
    return {"ok": True, **engine.status()}


@app.get("/memories")
def memories(user_id: str = "demo"):
    return engine.list_memories(user_id)


@app.post("/ingest/seed")
def ingest_seed(user_id: str = "demo"):
    return engine.ingest_seed(user_id)


@app.post("/ingest/repo")
def ingest_repo(req: IngestRepoRequest):
    return engine.ingest_repo(req.owner, req.repo, req.user_id)


@app.post("/why")
def why(req: WhyRequest):
    """Recall a decision — composed answer with provenance."""
    return engine.answer_why(req.question, req.user_id)


@app.post("/lore")
def lore(req: LoreSearch):
    """Free search across the Canon — matching Whys with provenance."""
    return engine.search_canon(req.query, req.user_id)


@app.post("/inscribe")
def inscribe(c: CommitPayload):
    """The Scribe inscribes a commit's Why into the Canon."""
    return engine.inscribe_commit(c.model_dump(), c.user_id)


@app.get("/canon")
def canon(user_id: str = "demo"):
    """Everything currently in the Canon."""
    return engine.list_memories(user_id)


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
    if x_github_event != "pull_request":
        return {"ok": True, "ignored": x_github_event}
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")
    return engine.handle_pull_request_event(payload)
