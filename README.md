# Lore — decision memory for engineering teams

**Ask `/why` a technical decision was made and get the real reasoning — the
trade-offs, the constraints, who was involved — with sources.**

The *why* behind your architecture lives in closed PRs, dead Slack threads, and
the heads of people who might leave. Lore captures it and hands it back the
instant anyone asks.

---

## How it works (one engine, many surfaces)

One backend (the "engine") does all the thinking; thin clients just talk to it.

```
  VS Code extension ─┐
  lore CLI  ─────────┤──►  Lore backend  ──►  mem0 (memory)  ──►  vector store
  git hook (Scribe) ─┤       FastAPI            Groq (LLM)         Qdrant / pgvector
  GitHub App ────────┘                          fastembed (embeddings)
```

- **Capture (inscribe):** a commit with a `Why:` line (via the Scribe), or a
  **merged PR** (via the GitHub App), → the Canon.
- **Backfill:** installing the GitHub App on an account indexes the last
  `BACKFILL_DAYS` of PRs across every selected repo in one shot, so recall
  works immediately without waiting for new merges.
- **Recall:** ask `/why` (or `/lore`) → semantic search over the Canon → an
  LLM composes a cited answer, scoped to one GitHub account's whole Canon.

## Repository layout

```
backend/       FastAPI service — the engine (mem0 + Groq + embeddings + GitHub App)
hf-space/      mirrored copy deployed to Hugging Face Spaces (see HF.md)
extension/     VS Code extension — the /why sidebar + "Why is this here?"
scribe/        npm package — the Scribe: git hook + `lore` CLI
RUNBOOK.md     how to run and test, step by step (Windows/PowerShell)
LEXICON.md     the vocabulary (Why, Canon, Provenance, Scribe, inscribe, recall)
DEPLOY.md      deploying the backend (Render/Fly)
HF.md          deploying the backend to Hugging Face Spaces (free)
GITHUB_APP.md  registering the GitHub App for auto-capture + install-time backfill
```

## Quick start

```bash
# 1. Backend
cd backend
pip install -r requirements.txt
cp .env.example .env          # add a GROQ_API_KEY to go "live" (runs in mock mode without one)
uvicorn main:app --port 8000

# 2. Extension
cd extension
npm install && npm run compile
# then open the extension/ folder in VS Code and press F5

# 3. CLI (global install, from this repo)
npm install -g ./scribe
lore                          # interactive REPL: banner + /lore prompt, /canon, /help
lore recall "why was X built this way"
lore canon                    # list everything in the Canon

# In any git repo, to capture commit-level "Why:" reasoning too:
npx lore init --url http://localhost:8000 --canon my-repo
```

Full, beginner-friendly instructions are in **[RUNBOOK.md](RUNBOOK.md)**.
To go from "runs on my machine" to a real, always-on backend that GitHub can
reach, see **[HF.md](HF.md)** (free) or **[DEPLOY.md](DEPLOY.md)**, then
**[GITHUB_APP.md](GITHUB_APP.md)** to wire up auto-capture.

## Modes

| Mode | When | Behaviour |
|------|------|-----------|
| **mock** | no `GROQ_API_KEY` | keyword answers over a seed corpus — zero keys, always runs |
| **live** | `GROQ_API_KEY` set | real mem0 memory, Groq answers, real capture, cited |

## Accounts & auth

Every memory is scoped to a GitHub account (`gh:<login>`), so one org's
decisions never mix with another's while every repo under that account is
searchable together. Two modes, controlled by backend env vars:

- **Single-tenant** (default) — `LORE_DEFAULT_ACCOUNT=<login>` answers every
  unauthenticated request from that one account's Canon. Simple, but the
  Canon is then world-readable to anyone who knows the backend URL.
- **Multi-tenant** — set `LORE_API_KEYS=key:login,...` and every request must
  present its key (`X-Lore-Key` header, `Authorization: Bearer`, or `?key=`);
  a key can only ever read its own account. This is the safer mode for a
  backend anyone else can reach — see the tracking issue below.

## Tech

- **FastAPI** — the API layer (Python).
- **mem0** — the memory layer (fact extraction, embeddings, vector search).
- **Groq** — fast LLM inference (Llama) for extraction + answer composition.
- **fastembed** — local, free embeddings (no key).
- **Qdrant** (local dev) / **pgvector** (hosted shared brain) — the vector store.
- **TypeScript** — the VS Code extension. **Node** (zero deps) — the `lore` CLI.

## Status

Deployed and running live: a Hugging Face-hosted backend + GitHub App capture
real merged PRs across multiple repos, and the CLI/extension recall from them
with citations. Hardening work (auth-by-default, tests/CI, idempotent
re-indexing, retrieval quality) is tracked as open issues on this repo —
see the **[issue tracker](../../issues)** for the current roadmap.

## License

MIT
