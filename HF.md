# Deploying the Lore backend to Hugging Face Spaces (free)

Hugging Face Spaces runs your Docker container for free (no card, ~16 GB RAM).
Everything you need is in the **`hf-space/`** folder.

## 1. Create the Space
1. Go to **https://huggingface.co** → sign up / log in (free, no card).
2. Top-right **+ → New Space**.
3. Fill in:
   - **Owner:** you
   - **Space name:** `lore-backend`
   - **License:** MIT (or any)
   - **Select the SDK:** **Docker** → **Blank**
   - **Hardware:** **CPU basic • free**
   - **Visibility:** **Public**  *(needed so the GitHub webhook can reach it)*
4. **Create Space.**

## 2. Upload the backend files (easiest — no git needed)
1. In your new Space, click the **Files** tab → **+ Add file → Upload files**.
2. Drag in **all files from** `C:\Users\Lenovo\Downloads\Lore-test-extension-\hf-space\`:
   - `Dockerfile`, `README.md`, `main.py`, `lore_engine.py`,
     `seed_decisions.py`, `requirements.txt`, `requirements-pgvector.txt`
3. Click **Commit changes to main**.

HF starts building immediately (first build ~3–6 min).

## 3. Set your secrets
Space → **Settings** → **Variables and secrets** → **New secret**, add these three
(the non-secret config is already baked into the Dockerfile):
- `GROQ_API_KEY` = your Groq key
- `DATABASE_URL` = your Neon connection string
- `GITHUB_WEBHOOK_SECRET` = any random string (or set later)

Saving secrets triggers a rebuild — let it finish (top of the Space shows
**Building → Running**).

## 4. Verify
Your API base URL is:
```
https://<your-username>-lore-backend.hf.space
```
Open **`https://<your-username>-lore-backend.hf.space/health`** →
should show `"mode":"live"` and `"canon_store":"pgvector"`.
(No re-ingest — it connects to the same Neon Canon that already has your Whys.)

## 5. Point the clients at it
- **VS Code:** Settings (`Ctrl+,`) → search `lore.backendUrl` → set to your `.hf.space` URL.
- **Scribe:** `npx lore init --url https://<you>-lore-backend.hf.space --canon my-repo`
- **GitHub webhook URL:** `https://<you>-lore-backend.hf.space/webhook/github` (see [GITHUB_APP.md](GITHUB_APP.md)).

## Notes
- The Space **sleeps after ~48h idle**; the first request after that wakes it
  (a few seconds). Fine for demos.
- **Public Space** = anyone who knows the URL can call the API. That's expected
  for a webhook backend at this stage; add auth later for production.
- If you change the backend code, re-copy the files from `backend/` into the
  Space (or push via git) to redeploy.
