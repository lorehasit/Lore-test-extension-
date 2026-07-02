# The Lore lexicon

A small, deliberate vocabulary. Every term maps to one real concept.

| Term | What it is | In the code / CLI |
|------|-----------|-------------------|
| **a Why** | The atomic unit — one captured decision + its reasoning | a memory in mem0 |
| **the Canon** | A team/repo's authoritative body of Whys | a mem0 store / `user_id` scope · `GET /canon` |
| **Provenance** | The sources a Why is grounded in (commits, PRs, threads) | `metadata.source` · shown under answers |
| **the Scribe** | The npm git-hook client that captures commits into the Canon | `scribe/` package |
| **inscribe** | Verb: capture a Why into the Canon (Scribe → Canon) | `POST /inscribe` |
| **recall** | Verb: query the Canon | `POST /why` · `lore recall` |

## Commands
- `/why <question>` — **recall** a decision (composed answer + provenance).
- `/lore <query>` — free **search** across the Canon (matching Whys).
- `lore init` — install the **Scribe** in a repo.
- `lore recall "<q>"` · `lore canon` · `lore log` — CLI.

## The commit convention
The Scribe reads one optional trailer. No `Why:` → nothing captured (keeps the Canon high-signal):

```
feat(auth): move to short-lived JWTs

Why: Redis failover logged everyone out; stateless tokens remove that SPOF.
```

## Backend endpoints
| Endpoint | Meaning |
|----------|---------|
| `POST /why` | recall — composed, cited answer |
| `POST /lore` | free search across the Canon |
| `POST /inscribe` | Scribe inscribes a commit's Why |
| `GET /canon` | everything in the Canon |
| `POST /ingest/seed` · `POST /ingest/repo` | seed / GitHub-PR ingestion |
| `GET /health` | mode + Canon store |

> Rule of the lexicon: coin sparingly, and never ship the vocabulary ahead of the value.
