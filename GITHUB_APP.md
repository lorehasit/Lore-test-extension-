# Registering the Lore GitHub App (auto-capture merged PRs)

This connects GitHub to your deployed backend so every **merged PR** is captured
into the Canon automatically — no developer effort.

## Before you start
- The backend must be **deployed with a public URL** (see [DEPLOY.md](DEPLOY.md)).
  You'll need it below as `https://<your-render-url>`.
- Your webhook endpoint is: **`https://<your-render-url>/webhook/github`**

---

## Step 1 — Make a webhook secret
This is a shared password that proves webhooks really come from your app.
Generate a random string (PowerShell):
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```
Copy the output. You'll paste it in **two** places (the GitHub App, and Render).

## Step 2 — Create the GitHub App
1. Go to **https://github.com/settings/apps** → **New GitHub App**.
   *(For an org: Org → Settings → Developer settings → GitHub Apps → New.)*
2. Fill in:
   - **GitHub App name:** `Lore` (if taken, `Lore-yourname`)
   - **Homepage URL:** anything (your repo or landing page)
   - **Webhook → Active:** ✅ checked
   - **Webhook URL:** `https://<your-render-url>/webhook/github`
   - **Webhook secret:** paste the secret from Step 1
3. **Repository permissions** (scroll down):
   - **Pull requests:** **Read-only**  ← the only one needed for capture
   *(Leave everything else "No access." For posting comments later, change this to Read & write.)*
4. **Subscribe to events:** check **Pull request**.
5. **Where can this GitHub App be installed?** → **Only on this account**.
6. Click **Create GitHub App**.

> Optional (for later): on the app page, click **Generate a private key** and save
> the downloaded `.pem` somewhere safe. You don't need it for capture — only for
> the future upgrade that reads full review-comment threads.

## Step 3 — Give the secret to your backend
In the **Render dashboard** → your `lore-backend` service → **Environment**:
- Add / set **`GITHUB_WEBHOOK_SECRET`** = the same secret from Step 1.
- Save → Render redeploys automatically.

*(If `GITHUB_WEBHOOK_SECRET` is empty, the backend accepts unsigned webhooks —
fine for a quick test, but set it for real use.)*

## Step 4 — Install the app on a repo
1. On the app's page → **Install App** (left sidebar).
2. Choose your account → **Only select repositories** → pick a test repo → **Install**.

GitHub immediately sends a **ping**. Verify it worked:
- App page → **Advanced** tab → **Recent Deliveries** → the `ping` should show a
  **green ✓ / 200** response `{"ok":true,"pong":true}`.

---

## Step 5 — Test the real thing
1. In the installed repo, open a PR and **merge it** (an empty change is fine).
2. Check **Advanced → Recent Deliveries** → the `pull_request` delivery should be
   **200** with `{"ok":true,"captured":true,...}`.
3. Confirm it landed in the Canon:
   ```powershell
   Invoke-RestMethod -Uri https://<your-render-url>/canon | Select-Object count
   npx lore recall "why did we <thing from the PR>?" 
   ```
   *(point `lore` at the deployed URL: `npx lore init --url https://<your-render-url>`)*

🎉 If recall returns the PR's reasoning, the automatic GitHub capture is live.

---

## Troubleshooting (Advanced → Recent Deliveries shows everything)
| Symptom | Cause / fix |
|---------|-------------|
| Delivery shows **401** | Secret mismatch — the GitHub App secret ≠ Render's `GITHUB_WEBHOOK_SECRET`. Make them identical, redeploy. |
| Delivery **times out** on first try | Render free tier was asleep (cold start). GitHub retries; or hit `/health` first to wake it. |
| `captured:false, reason: action=opened` | Correct — only **merged** PRs are captured, not opened ones. |
| Nothing in the Canon | Backend in mock mode (`GROQ_API_KEY` unset in Render), or `DATABASE_URL` wrong. Check `/health`. |
| Delivery **404** | Webhook URL wrong — must end in `/webhook/github`. |

## What this does NOT do yet (future upgrade)
Right now it captures the PR **title + description**. To also mine the full
**review-comment discussion** (the richest "why"), we add the App's private-key
authentication and fetch the PR's comments via the GitHub API. That's phase 2.
