# Registering the Lore GitHub App (auto-capture + install-time backfill)

This connects GitHub to your deployed backend so that:
- **On install** (or when repos are added), Lore backfills the **last 15 days of
  PRs across every selected repo** — titles, descriptions **and** review
  discussion — and posts a one-time **welcome comment on each open PR**.
- **Going forward**, every **merged PR** is captured into the Canon automatically.
- `/why` then answers across **all** of that account's repos, with citations.

> The backfill + welcome comment need the App's own identity (**App ID + private
> key**), configured in Step 6 below. The webhook secret alone only covers
> merged-PR capture.

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
   - **Pull requests:** **Read & write**  ← read for capture/backfill, write to
     post the welcome comment on open PRs.
   - **Contents:** **Read-only** *(optional — lets Lore enumerate repos on "all
     repositories" installs; not required for "only select repositories").*
   *(Leave everything else "No access.")*
4. **Subscribe to events:** check **Pull request**.
   *(`installation` / `installation_repositories` events are always delivered to
   the App — no subscription needed. They're what trigger the backfill.)*
5. **Where can this GitHub App be installed?** → **Only on this account**.
6. Click **Create GitHub App**.

## Step 2b — Generate the App's private key (required)
On the new App's page:
1. Note the **App ID** (top of the page).
2. Click **Generate a private key** → a `.pem` downloads. Keep it safe — it's the
   App's identity, used to backfill repos and post the welcome comment.

## Step 3 — Give the secret + App identity to your backend
In the **Render dashboard** → your `lore-backend` service → **Environment**, set:
- **`GITHUB_WEBHOOK_SECRET`** = the same secret from Step 1.
- **`GITHUB_APP_ID`** = the App ID from Step 2b.
- **`GITHUB_APP_PRIVATE_KEY`** = the contents of the `.pem`. Dashboards often mangle
  multi-line values, so **base64-encode it first** and paste that:
  ```powershell
  [Convert]::ToBase64String([IO.File]::ReadAllBytes("lore.private-key.pem"))
  ```
  *(The backend auto-detects and decodes base64; raw PEM or `\n`-escaped PEM also work.)*
- **`LORE_DEFAULT_ACCOUNT`** = the org/user login you'll install on (e.g. `acme-inc`),
  so `/why` spans all that account's repos by default.
- *(optional)* **`BACKFILL_DAYS`** = how far back to index (default `15`).
- Save → Render redeploys automatically.

*(If `GITHUB_WEBHOOK_SECRET` is empty, the backend accepts unsigned webhooks —
fine for a quick test, but set it for real use. If `GITHUB_APP_ID` /
`GITHUB_APP_PRIVATE_KEY` are empty, install-time backfill + welcome comments are
skipped and only going-forward merged-PR capture runs.)*

## Step 4 — Install the app (this triggers the backfill)
1. On the app's page → **Install App** (left sidebar).
2. Choose your account → **Only select repositories** (or **All repositories**) →
   pick your repos → **Install**.

GitHub immediately sends a **ping** *and* an **`installation`** event. The
installation event kicks off the backfill in the background:
- Lore indexes the **last 15 days of PRs across every selected repo** (titles,
  bodies, review threads).
- It posts a **welcome comment on each open PR** (the CodeRabbit-style hello).

Watch progress:
```powershell
Invoke-RestMethod -Uri https://<your-render-url>/backfill/status
# => state: running -> done, repos_done/repos_total, prs_captured, comments_posted
```
- Advanced → Recent Deliveries → the `installation` delivery should be **200**
  with `{"ok":true,"backfill":"started",...}`.

---

## Step 5 — Test going-forward capture
1. In an installed repo, open a PR and **merge it** (an empty change is fine).
2. Check **Advanced → Recent Deliveries** → the `pull_request` delivery should be
   **200** with `{"ok":true,"captured":true,...}`.
3. Confirm the Canon answers across your repos:
   ```powershell
   Invoke-RestMethod -Uri https://<your-render-url>/canon | Select-Object count
   npx lore recall "why did we <thing from any indexed PR>?"
   ```
   *(point `lore` at the deployed URL: `npx lore init --url https://<your-render-url>`)*

🎉 If recall returns reasoning from PRs across your repos, the whole flow is live.

---

## Troubleshooting (Advanced → Recent Deliveries shows everything)
| Symptom | Cause / fix |
|---------|-------------|
| Delivery shows **401** | Secret mismatch — the GitHub App secret ≠ Render's `GITHUB_WEBHOOK_SECRET`. Make them identical, redeploy. |
| Delivery **times out** on first try | Render free tier was asleep (cold start). GitHub retries; or hit `/health` first to wake it. |
| `captured:false, reason: action=opened` | Correct — only **merged** PRs are captured, not opened ones. |
| Nothing in the Canon | Backend in mock mode (`GROQ_API_KEY` unset in Render), or `DATABASE_URL` wrong. Check `/health`. |
| Delivery **404** | Webhook URL wrong — must end in `/webhook/github`. |
| `installation` returns `GitHub App auth not configured` | Set `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY` in Render, redeploy, then re-trigger by adding a repo (or uninstall/reinstall). |
| `/backfill/status` shows `state: error` | Read its `error` field — usually a bad App ID/private key (can't mint a token) or mock mode. |
| No welcome comment on open PRs | The App needs **Pull requests: Read & write**; a repo with no open PRs gets none (expected). |
| Only some repos indexed | Backfill only reaches back `BACKFILL_DAYS` (default 15). PRs untouched in that window are skipped. |

## What this captures
- **On install / repos-added:** the last `BACKFILL_DAYS` of **all** PRs
  (open, closed, merged) across every selected repo — title, body **and** review
  discussion — plus a one-time welcome comment on open PRs.
- **Going forward:** each **merged** PR (a merge = a finalized decision), with its
  review discussion when App auth is configured.
- **Retrieval:** `/why` and `/lore` search the whole account's Canon, so one
  question spans every indexed repo.
