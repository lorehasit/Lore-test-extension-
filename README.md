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
  git hook (Scribe) ─┘       FastAPI            Groq (LLM)         Qdrant / pgvector
                                                fastembed (embeddings)
```

- **Capture (inscribe):** a commit with a `Why:` line → the Scribe → the Canon.
- **Recall:** ask `/why` → semantic search over the Canon → an LLM composes a
  cited answer.

## Repository layout

```
backend/     FastAPI service — the engine (mem0 + Groq + embeddings)
extension/   VS Code extension — the /why sidebar + "Why is this here?"
scribe/      npm package — the Scribe: git hook + `lore` CLI
RUNBOOK.md   how to run and test, step by step (Windows/PowerShell)
LEXICON.md   the vocabulary (Why, Canon, Provenance, Scribe, inscribe, recall)
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

# 3. Scribe (in any git repo)
npm install --save-dev /path/to/scribe
npx lore init --url http://localhost:8000 --canon my-repo
```

Full, beginner-friendly instructions are in **[RUNBOOK.md](RUNBOOK.md)**.

## Modes

| Mode | When | Behaviour |
|------|------|-----------|
| **mock** | no `GROQ_API_KEY` | keyword answers over a seed corpus — zero keys, always runs |
| **live** | `GROQ_API_KEY` set | real mem0 memory, Groq answers, real capture, cited |

## Tech

- **FastAPI** — the API layer (Python).
- **mem0** — the memory layer (fact extraction, embeddings, vector search).
- **Groq** — fast LLM inference (Llama) for extraction + answer composition.
- **fastembed** — local, free embeddings (no key).
- **Qdrant** (local dev) / **pgvector** (hosted shared brain) — the vector store.
- **TypeScript** — the VS Code extension. **Node** — the `lore` CLI.

## Status

Early prototype. Runs locally end-to-end. Next milestones: deploy the backend +
hosted pgvector (shared Canon), then team accounts.

## License

MIT
