# Reddit → Telegram Jobs Bot

Watches r/forhire, r/jobbit, r/freelance_forhire, r/hireaprogrammer, r/slavelabour,
r/DoneDirtCheap, r/remotejs, r/WebDeveloperJobs and sends you **[Hiring]** posts about
web / app development. `[For Hire]` posts are skipped.

## Commands
`/start` `/latest [n]` `/check` `/status` `/pause` `/resume` `/keywords` `/addkw x` `/delkw x` `/subs`

---

## 🚀 Free deploy (Render + keep-alive pinger)

### 1. Push to GitHub
Create an empty repo at https://github.com/new (private is fine), then:
```powershell
git remote add origin https://github.com/<you>/reddit-jobs-bot.git
git push -u origin main
```
(`.env` is gitignored, so your token never gets uploaded.)

### 2. Deploy on Render (free)
1. Sign up at https://render.com with GitHub.
2. **New → Blueprint** → pick the repo (it reads `render.yaml`).  
   *(Or **New → Web Service** → Runtime Python, Build `pip install -r requirements.txt`, Start `python bot.py`, Instance **Free**.)*
3. Set the env vars:
   - `TELEGRAM_TOKEN` = your bot token
   - `OWNER_CHAT_ID` = leave empty for now
   - `AI_API_KEY` = optional (see below)
4. Deploy. Open your bot in Telegram → `/start`. It replies with your **chat ID** → put it in
   `OWNER_CHAT_ID` on Render (so you stay subscribed after restarts).

### 3. Keep it awake (free)
Render's free plan puts the service to sleep after 15 min with no HTTP traffic. To stop that, ping it:
- https://cron-job.org (free): new cronjob → URL `https://<your-app>.onrender.com/` → every **10 minutes**.
- or https://uptimerobot.com (free): HTTP monitor, 5-minute interval.

> ⚠️ Only run one copy at a time. If it runs on your PC and on Render together, Telegram returns a `Conflict` error.

---

## 🧠 Optional: free AI filter (fewer false positives + summary + budget)
With `AI_API_KEY` set, every keyword match is checked by an LLM, which drops non-dev or "for hire" posts
and adds a one-line summary plus the budget.

| Provider | Get key (free, no card) | Env vars |
|---|---|---|
| **Groq** (default) | https://console.groq.com/keys | `AI_API_KEY=gsk_...` |
| **Google Gemini** | https://aistudio.google.com/apikey | `AI_API_KEY=AIza...`<br>`AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`<br>`AI_MODEL=gemini-2.0-flash` |

## 🔑 Optional: Reddit API credentials
The bot uses Reddit's public JSON and falls back to RSS automatically. If `/status` shows
`403 Blocked` errors (Reddit sometimes blocks datacenter IPs), create a **script** app at
https://www.reddit.com/prefs/apps and set `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET`.

## ⚙️ Customize (env vars)
- `SUBREDDITS`: comma-separated list
- `KEYWORDS`: comma-separated list (replaces the defaults)
- `CHECK_INTERVAL`: seconds between checks (default 180)

## Run locally
```powershell
python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # fill in TELEGRAM_TOKEN
.\.venv\Scripts\python bot.py
```
