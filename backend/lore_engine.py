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

import os
import re
import time
from typing import Optional

from dotenv import load_dotenv

from seed_decisions import SEED_DECISIONS

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
EMBEDDER_MODEL = os.getenv("EMBEDDER_MODEL", "thenlper/gte-large").strip()
# Vector-store dimension MUST match the embedder's output, or mem0's default
# (1536) collides with it. gte-large=1024, bge-small-en-v1.5=384, bge-base=768.
EMBEDDER_DIMS = int(os.getenv("EMBEDDER_DIMS", "1024").strip())
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

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


def get_memory():
    """Build the mem0 Memory on first use. LLM and embedder are independent,
    swappable knobs — moving Groq -> Claude later touches only the `llm` block."""
    global _memory
    if _memory is None:
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
# Public API: status / ingest / answer
# ---------------------------------------------------------------------------
def status() -> dict:
    return {
        "mode": MODE,
        "llm": f"groq:{GROQ_MODEL}" if MODE == "live" else None,
        "embedder": f"fastembed:{EMBEDDER_MODEL}" if MODE == "live" else None,
        "canon_store": VECTOR_STORE,
        "github_ingestion": bool(GITHUB_TOKEN) and MODE == "live",
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
    mem.add(text, user_id=user_id, metadata={
        "source": f"commit {sha}",
        "title": subject[:80],
        "author": author,
        "canon": canon,
    })
    return {"mode": "live", "inscribed": True, "provenance": f"commit {sha}"}


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
    res = mem.search(query, filters={"user_id": user_id}, limit=limit)
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
    res = mem.get_all(filters={"user_id": user_id})
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
        mem.add(d["answer"], user_id=user_id,
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
    n = 0
    for pr in resp.json():
        if not pr.get("merged_at"):
            continue
        body = (pr.get("body") or "").strip()
        text = f"PR #{pr['number']}: {pr['title']}\n\n{body}"
        mem.add(text, user_id=user_id, metadata={
            "source": f"PR #{pr['number']}",
            "title": pr["title"],
            "url": pr.get("html_url", ""),
        })
        n += 1
    return {"mode": "live", "repo": f"{owner}/{repo}", "ingested": n}


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
    results = mem.search(question, filters={"user_id": user_id}, limit=6)
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
