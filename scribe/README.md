# Lore — the Scribe

The **Scribe** captures the *why* behind your commits into your team's **Canon**,
so anyone can **recall** it later with `/why`.

## Interactive mode (the terminal app)
Run `lore` with no arguments for a Claude-Code-style prompt — a banner, then a
`/why` REPL. Quit with `/quit` and your terminal returns to normal.
```bash
lore              # or: lore start
```
Point it at a backend once (any of these):
- a global `~/.lore.json` with `{"backendUrl": "https://…"}`
- a per-repo `.lore.json` (written by `lore init`)
- `lore start --url https://your-backend` or `LORE_BACKEND_URL=…`

Inside the prompt: type a question (or `/why <q>`), `/canon`, `/help`, `/quit`.

## Install
```bash
npm install --save-dev lore   # (local package during dev: npm install --save-dev ../scribe)
npx lore init                 # installs the post-commit hook + writes .lore.json
```

`lore init` options:
- `--url <backendUrl>` — where the Lore backend runs (default `http://localhost:8000`)
- `--canon <name>` — this repo's Canon name (default: the repo folder name)

## Use
Add a `Why:` line to a commit — the Scribe inscribes it automatically:
```bash
git commit -m "feat(auth): short-lived JWTs" \
           -m "Why: Redis failover logged everyone out; stateless tokens remove that SPOF."
```
Commits **without** a `Why:` are ignored, keeping the Canon high-signal.

Then recall reasoning from anywhere:
```bash
lore recall "why is auth stateless?"
lore canon      # everything in this repo's Canon
```

## How it works
`post-commit` hook → `lore capture` reads HEAD's `Why:` → `POST /inscribe` →
the backend distills it into the Canon (mem0) → `lore recall` / `/why` searches it.

The hook fails silently and never blocks a commit.
