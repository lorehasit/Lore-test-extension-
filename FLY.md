# Deploying the Lore backend to Fly.io

Fly builds a Docker container (see `backend/Dockerfile`) and runs it on a public
URL. All commands run from the **`backend/`** folder.

> Note: Fly requires a credit card, but costs are tiny — a 1 GB machine that
> auto-stops when idle is roughly a few dollars/month, and near-zero when unused.

## 1. Install the Fly CLI
```powershell
iwr https://fly.io/install.ps1 -useb | iex
```
Then restart your terminal (so `fly` is on PATH).

## 2. Sign up / log in
```powershell
fly auth signup     # or: fly auth login
```

## 3. Pick a unique app name
Open `backend/fly.toml` and change the first line to something unique, e.g.:
```
app = "lore-backend-aryan"
```
(Also set `primary_region` to one near you — `bom` = Mumbai.)

## 4. Create the app (no deploy yet)
```powershell
cd backend
fly launch --no-deploy --copy-config --name lore-backend-aryan --region bom
```
- If it asks to tweak settings, accept the defaults.
- If it offers a **Postgres/Redis** database → choose **No** (we use Neon).

## 5. Set your secrets (encrypted, never in the repo)
```powershell
fly secrets set `
  GROQ_API_KEY="your_groq_key" `
  DATABASE_URL="your_neon_connection_string" `
  GITHUB_WEBHOOK_SECRET="your_webhook_secret"
```
*(The backtick `` ` `` lets the command span multiple lines in PowerShell.)*

## 6. Deploy
```powershell
fly deploy
```
First build takes a few minutes (installs mem0, fastembed, etc.).

## 7. Verify
Your URL is `https://<your-app-name>.fly.dev`.
```powershell
fly open /health
```
Should show `"mode":"live"` and `"canon_store":"pgvector"`.
No re-ingest needed — it connects to the same Neon Canon (already has your Whys).

## 8. Point the clients at it
- **VS Code:** Settings (`Ctrl+,`) → search `lore.backendUrl` → set to `https://<your-app>.fly.dev`.
- **Scribe:** `npx lore init --url https://<your-app>.fly.dev --canon my-repo`
- **GitHub App webhook:** `https://<your-app>.fly.dev/webhook/github` (see [GITHUB_APP.md](GITHUB_APP.md)).

## Useful commands
```powershell
fly logs             # live logs (watch a deploy or a webhook arrive)
fly status           # is it running?
fly secrets list     # names only (values hidden)
fly scale memory 2048   # bump RAM if it OOMs
```

## Notes
- **Cold starts:** with `min_machines_running = 0` the app stops when idle and
  wakes on the next request (~a few seconds + a one-time model download). For a
  live demo, set it to `1` in `fly.toml` and redeploy, or hit `/health` first.
- Every `fly deploy` ships your latest committed code.
