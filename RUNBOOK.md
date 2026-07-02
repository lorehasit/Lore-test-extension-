# Lore — how to start the project

## Prerequisites (one time)
- **Python 3.11+**, **Node.js 18+**, **VS Code**
- A **Groq API key** (https://console.groq.com) in `backend/.env`

> **Windows PowerShell note:** `curl` is an alias for `Invoke-WebRequest` and does
> NOT accept `-X`/`-H`/`-d`. Use `Invoke-RestMethod` (shown below) or call the real
> curl as `curl.exe`. Examples in this file give the PowerShell form.

---

## A. First-time setup (only once)

### 1. Install backend dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 2. Make sure `backend/.env` exists and contains:
```
GROQ_API_KEY=gsk_your_key_here
EMBEDDER_MODEL=BAAI/bge-small-en-v1.5
EMBEDDER_DIMS=384
```
(If `.env` is missing: `cp .env.example .env` then paste your key.)

### 3. Install the extension into VS Code
```bash
code --install-extension "extension/lore-0.1.0.vsix"
```
Then fully restart VS Code.

---

## B. Every time you want to run it

### Step 1 — Start the backend
```bash
cd backend
python -m uvicorn main:app --port 8000
```
Leave this terminal open. Verify: open http://localhost:8000/health
→ it should say `"mode":"live"`.

### Step 2 — Load the decision memory (first run, or if empty)
In a second terminal (PowerShell):
```powershell
Invoke-RestMethod -Uri http://localhost:8000/ingest/seed -Method Post
```
→ `mode: live, ingested: 6`. (The memory persists on disk in
`backend/qdrant_data/`, so you only need this once.)

### Step 3 — Open the Lore panel in VS Code
- Click the **Lore icon** in the left Activity Bar.
- The **status bar** (bottom-left) should read **`Lore: live`**.
- Ask a question in the `/why` box, e.g.:
  - `why is auth stateless?`
  - `why not microservices?`
  - `what made us pick our database?`
- Or select code in any file → right-click → **"Lore: Why is this here?"**

---

## Alternative to the VSIX: run from source (F5)
1. **File → Open Folder →** select the **`extension`** folder (the one with
   `package.json` inside — NOT the repo root).
2. Press **F5** → "Run Lore Extension" opens a second VS Code window.
3. Click the Lore icon there.

---

## Going further
Ingest a real repo's PRs (needs `GITHUB_TOKEN` in `.env`):
```powershell
Invoke-RestMethod -Uri http://localhost:8000/ingest/repo -Method Post `
  -ContentType 'application/json' `
  -Body '{"owner":"lorehasit","repo":"Lore-landing"}'
```

## The Scribe (capture commits automatically)
Install into any git repo so commits with a `Why:` are inscribed into the Canon:
```powershell
cd path\to\your\repo
npm install --save-dev "C:/Users/Lenovo/Downloads/Lore-test-extension-/scribe"
npx lore init --url http://localhost:8000 --canon my-repo
```
Then commit with a reason:
```powershell
git commit -m "feat(auth): short-lived JWTs" -m "Why: Redis failover logged everyone out; stateless tokens remove that SPOF."
lore recall "why is auth stateless?"
lore canon
```
See [LEXICON.md](LEXICON.md) for the full vocabulary and endpoints.

## Switch the Canon to hosted Postgres (pgvector)
For a shared team brain instead of the local file store:
```powershell
pip install -r backend/requirements-pgvector.txt
```
Then in `backend/.env`:
```
VECTOR_STORE=pgvector
DATABASE_URL=postgresql://user:pass@host:5432/db?sslmode=require   # Supabase/Neon
```
Restart the backend and re-ingest. Default (no `VECTOR_STORE`) stays local Qdrant.

## Troubleshooting
| Symptom | Fix |
|---------|-----|
| Status bar says `Lore: offline` | Backend isn't running — do Step 1. |
| `/health` shows `"mode":"mock"` | `GROQ_API_KEY` not set in `.env`. |
| `/why` returns "no decision yet" | Memory empty — run Step 2 (ingest). |
| F5 does nothing | You opened the repo root; open the **`extension`** folder instead. |
| Dimension/shape error on ingest | `EMBEDDER_DIMS` must match the model (bge-small = 384). |
