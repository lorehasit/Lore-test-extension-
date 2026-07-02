# Deploying the Lore backend to Render

Goal: put the backend on a public URL so the extension, the Scribe, and other
people can use Lore without running anything locally.

## Prerequisites
- The repo is on GitHub (it is: `lorehasit/Lore-test-extension-`).
- Your **Neon** `DATABASE_URL` and your **Groq** API key on hand.
- The Canon is already seeded in Neon (6 Whys), so no re-ingest is needed —
  the deployed backend connects to the same database.

## Deploy (≈5 minutes, mostly clicking)

1. Go to **https://render.com** → sign up (use **Continue with GitHub**).
2. Click **New +** → **Blueprint**.
3. Select the **`Lore-test-extension-`** repository. Render detects
   `render.yaml` and shows a service called **lore-backend**.
4. Click **Apply**. Render will prompt for the two secrets:
   - **GROQ_API_KEY** → paste your Groq key
   - **DATABASE_URL** → paste your Neon connection string
   (The other settings come from `render.yaml` automatically.)
5. Click **Create / Deploy** and wait for the build (first build ~3–6 min —
   it installs mem0, fastembed, etc.).

## Verify
When the deploy finishes you get a URL like `https://lore-backend-xxxx.onrender.com`.

- Open **`https://<your-url>/health`** in a browser → should show
  `"mode":"live"` and `"canon_store":"pgvector"`.
- Ask a question:
  ```
  POST https://<your-url>/why   body: {"question":"why is auth stateless?"}
  ```
  It should answer from your Neon Canon.

## Point the clients at the deployed URL

**VS Code extension:** open Settings (`Ctrl+,`) → search **`lore.backendUrl`** →
set it to `https://<your-url>`. (No reinstall needed.)

**The Scribe (in any repo):**
```bash
npx lore init --url https://<your-url> --canon my-repo
```

Now anyone with the extension installed can use Lore — no local backend.

## Free-tier notes (important for demos)
- Render's free web service **spins down after ~15 min of inactivity**, so the
  **first request after idle is slow** (cold start ~30–60s, plus a one-time
  embedding-model download). For a live investor demo, hit `/health` a minute
  before to warm it up, or use Render's paid Starter tier (~$7/mo) to keep it
  always on.
- Free tier is 512 MB RAM. If the service restarts under load, that's the limit
  — the Starter tier fixes it.

## Redeploys
Every `git push` to `main` triggers an automatic redeploy. Secrets and config
persist in the Render dashboard.
