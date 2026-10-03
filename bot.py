#!/usr/bin/env python3
"""Reddit -> Telegram job alert bot.

Watches hiring subreddits (r/forhire, r/jobbit, ...) and sends you new
web / app development gigs on Telegram. Optional free AI filter (Groq / Gemini).
"""
import asyncio
import calendar
import html
import json
import logging
import os
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import feedparser
import httpx
from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      LinkPreviewOptions, Update)
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("jobs-bot")


# ----------------------------------------------------------------- config
def env_list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


TOKEN = os.getenv("TELEGRAM_TOKEN", "")
SUBREDDITS = env_list(
    "SUBREDDITS",
    "forhire,jobbit,freelance_forhire,hireaprogrammer,slavelabour,DoneDirtCheap,remotejs,WebDeveloperJobs",
)
DEFAULT_KEYWORDS = (
    "web,website,web app,webapp,wordpress,shopify,wix,webflow,squarespace,react,next.js,nextjs,"
    "vue,nuxt,angular,svelte,node,node.js,nodejs,express,django,flask,fastapi,laravel,php,"
    "ruby on rails,frontend,front-end,front end,backend,back-end,back end,full stack,full-stack,"
    "fullstack,javascript,typescript,html,css,tailwind,api,saas,landing page,e-commerce,ecommerce,"
    "app,apps,mobile app,ios,android,flutter,react native,swift,kotlin,developer,programmer,coder,"
    "software engineer,chrome extension,bot,scraper,scraping,automation,mvp,firebase,supabase"
)
KEYWORDS = [k.lower() for k in env_list("KEYWORDS", DEFAULT_KEYWORDS)]
# Posts whose title matches this are skipped (people offering services, not hiring)
EXCLUDE_RE = re.compile(
    os.getenv("EXCLUDE_REGEX", r"\[\s*(for\s*hire|fh|offer|offering|available)\s*\]|^\s*for\s*hire\b"),
    re.I,
)
FLAIR_EXCLUDE_RE = re.compile(r"for\s*hire|offer|closed|filled", re.I)

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "180"))        # seconds
BOOT_LOOKBACK_MIN = int(os.getenv("BOOT_LOOKBACK_MIN", "60"))   # on start, send posts newer than this
MAX_PER_CYCLE = int(os.getenv("MAX_PER_CYCLE", "15"))
OWNER_CHAT_IDS = {int(x) for x in env_list("OWNER_CHAT_ID", "") if x.lstrip("-").isdigit()}
USER_AGENT = os.getenv("USER_AGENT", "python:tg-reddit-jobs-bot:v1.0 (by /u/jobsbot)")

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")

# Optional AI filter - any OpenAI-compatible endpoint (Groq default, Gemini works too)
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "llama-3.1-8b-instant")

STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))
PORT = int(os.getenv("PORT", "8080"))


# ------------------------------------------------------------------ state
class State:
    def __init__(self) -> None:
        self.chats: set[int] = set(OWNER_CHAT_IDS)
        self.paused: set[int] = set()
        self.seen: deque[str] = deque(maxlen=5000)
        self.seen_set: set[str] = set()
        self.keywords: list[str] = list(KEYWORDS)
        self.recent: deque[dict] = deque(maxlen=50)
        self.booted = False
        self.last_check: float | None = None
        self.last_error: str | None = None
        self.source: str | None = None
        self.load()

    def add_seen(self, pid: str) -> None:
        if pid in self.seen_set:
            return
        if len(self.seen) == self.seen.maxlen:
            self.seen_set.discard(self.seen[0])
        self.seen.append(pid)
        self.seen_set.add(pid)

    def load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.chats |= set(d.get("chats", []))
            self.paused = set(d.get("paused", []))
            for pid in d.get("seen", []):
                self.add_seen(pid)
            if d.get("keywords"):
                self.keywords = d["keywords"]
        except Exception as e:  # noqa: BLE001
            log.warning("Could not load state: %s", e)

    def save(self) -> None:
        try:
            STATE_FILE.write_text(json.dumps({
                "chats": sorted(self.chats),
                "paused": sorted(self.paused),
                "seen": list(self.seen),
                "keywords": self.keywords,
            }), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("Could not save state: %s", e)


# ----------------------------------------------------------------- reddit
class Reddit:
    """Fetches newest posts. Tries OAuth (if configured) -> JSON -> RSS."""

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
        self.token: str | None = None
        self.token_exp = 0.0
        self.preferred: str | None = None

    @staticmethod
    def _from_json(j: dict) -> list[dict]:
        posts = []
        for c in j["data"]["children"]:
            d = c["data"]
            posts.append({
                "id": d["name"],
                "title": d.get("title", ""),
                "body": d.get("selftext", "") or "",
                "sub": d.get("subreddit", ""),
                "author": d.get("author", ""),
                "url": "https://www.reddit.com" + d.get("permalink", ""),
                "created": float(d.get("created_utc", time.time())),
                "flair": d.get("link_flair_text") or "",
            })
        return posts

    async def _oauth_token(self) -> str:
        if self.token and time.time() < self.token_exp - 60:
            return self.token
        r = await self.client.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
        )
        r.raise_for_status()
        j = r.json()
        self.token = j["access_token"]
        self.token_exp = time.time() + j.get("expires_in", 3600)
        return self.token

    async def fetch_oauth(self, multi: str) -> list[dict]:
        tok = await self._oauth_token()
        r = await self.client.get(
            f"https://oauth.reddit.com/r/{multi}/new",
            params={"limit": 100, "raw_json": 1},
            headers={"Authorization": f"bearer {tok}"},
        )
        r.raise_for_status()
        return self._from_json(r.json())

    async def fetch_json(self, multi: str) -> list[dict]:
        r = await self.client.get(f"https://www.reddit.com/r/{multi}/new.json", params={"limit": 100, "raw_json": 1})
        r.raise_for_status()
        return self._from_json(r.json())

    async def fetch_rss(self, multi: str) -> list[dict]:
        r = await self.client.get(f"https://www.reddit.com/r/{multi}/new/.rss", params={"limit": 100})
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        posts = []
        for e in feed.entries:
            raw = (e.get("content") or [{}])[0].get("value") or e.get("summary", "")
            body = html.unescape(re.sub(r"<[^>]+>", " ", raw))
            body = re.sub(r"\s+", " ", body)
            body = re.sub(r"submitted by\s+/u/\S+.*$", "", body).strip()
            tp = e.get("published_parsed") or e.get("updated_parsed")
            posts.append({
                "id": e.get("id") or e.get("link", ""),
                "title": html.unescape(e.get("title", "")),
                "body": body,
                "sub": (e.get("tags") or [{}])[0].get("term", ""),
                "author": e.get("author", "").replace("/u/", ""),
                "url": e.get("link", ""),
                "created": float(calendar.timegm(tp)) if tp else time.time(),
                "flair": "",
            })
        return posts

    async def fetch(self) -> tuple[str, list[dict]]:
        multi = "+".join(SUBREDDITS)
        methods = []
        if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            methods.append(("oauth", self.fetch_oauth))
        methods += [("json", self.fetch_json), ("rss", self.fetch_rss)]
        if self.preferred:  # try last working method first
            methods.sort(key=lambda m: m[0] != self.preferred)
        errors = []
        for name, fn in methods:
            try:
                posts = await fn(multi)
                self.preferred = name
                return name, posts
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                log.warning("Fetch via %s failed: %s", name, e)
        raise RuntimeError(" | ".join(errors))


# ---------------------------------------------------------------- filters
_kw_cache: dict[str, re.Pattern] = {}


def match_keywords(text: str, keywords: list[str]) -> list[str]:
    text = text.lower()
    hits = []
    for k in keywords:
        pat = _kw_cache.get(k)
        if pat is None:
            pat = _kw_cache[k] = re.compile(r"(?<![\w])" + re.escape(k) + r"(?![\w])")
        if pat.search(text):
            hits.append(k)
    return hits


AI_SYSTEM = (
    "You screen Reddit posts for a freelance web & mobile app developer. "
    'Reply ONLY with JSON: {"relevant": true|false, "summary": "<=25 words: what the client needs", '
    '"budget": "budget/rate if mentioned, else empty string"}. '
    "relevant=true ONLY if someone is HIRING / willing to pay for work a web or mobile developer can do "
    "(websites, web apps, mobile apps, frontend/backend, APIs, WordPress/Shopify, bots, scripts, automation). "
    "relevant=false for people offering their own services, non-dev work (design-only, writing, video, "
    "marketing, data entry, VA), or unpaid requests."
)


async def ai_check(client: httpx.AsyncClient, post: dict) -> dict | None:
    """Returns AI verdict or None if AI disabled / failed (fail-open)."""
    if not AI_API_KEY:
        return None
    try:
        r = await client.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": AI_MODEL,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": AI_SYSTEM},
                    {"role": "user", "content": f"Title: {post['title']}\n\nBody: {post['body'][:2500]}"},
                ],
            },
            timeout=30,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(json)?|```$", "", content.strip()).strip()
        return json.loads(content)
    except Exception as e:  # noqa: BLE001
        log.warning("AI check failed: %s", e)
        return None


# -------------------------------------------------------------- formatting
def ago(ts: float) -> str:
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def format_post(p: dict) -> tuple[str, InlineKeyboardMarkup]:
    meta = "r/{} · u/{} · {}".format(p["sub"], p["author"], ago(p["created"]))
    lines = [
        f"💼 <b>{html.escape(p['title'][:250])}</b>",
        f"<i>{html.escape(meta)}</i>",
    ]
    if p.get("budget"):
        lines.append(f"💰 {html.escape(str(p['budget']))}")
    if p.get("summary"):
        lines.append(f"🧠 {html.escape(str(p['summary']))}")
    elif p.get("body"):
        body = p["body"].strip()
        snippet = body[:350] + ("…" if len(body) > 350 else "")
        lines.append("\n" + html.escape(snippet))
    if p.get("kws"):
        lines.append("\n🏷 " + html.escape(", ".join(p["kws"][:6])))

    buttons = [InlineKeyboardButton("🔗 Open post", url=p["url"])]
    if p.get("author") and p["author"] not in ("[deleted]", ""):
        buttons.append(InlineKeyboardButton(
            "✉️ DM author", url=f"https://www.reddit.com/message/compose/?to={p['author']}"))
    return "\n".join(lines), InlineKeyboardMarkup([buttons])


NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def send_post(bot, chat_id: int, p: dict) -> None:
    text, kb = format_post(p)
    await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb,
                           link_preview_options=NO_PREVIEW)


async def broadcast(bot, st: State, p: dict) -> None:
    for chat_id in list(st.chats - st.paused):
        try:
            await send_post(bot, chat_id, p)
        except Forbidden:
            log.info("Chat %s blocked the bot; removing", chat_id)
            st.chats.discard(chat_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Send to %s failed: %s", chat_id, e)
        await asyncio.sleep(0.3)


# -------------------------------------------------------------- main loop
async def check(context: ContextTypes.DEFAULT_TYPE) -> None:
    st: State = context.bot_data["state"]
    rd: Reddit = context.bot_data["reddit"]
    ai_client: httpx.AsyncClient = context.bot_data["ai_client"]

    try:
        source, posts = await rd.fetch()
    except Exception as e:  # noqa: BLE001
        st.last_error = str(e)[:400]
        log.error("All fetch methods failed: %s", st.last_error)
        return
    st.last_check, st.source, st.last_error = time.time(), source, None

    posts.sort(key=lambda p: p["created"])
    now = time.time()
    candidates = []
    for p in posts:
        if p["id"] in st.seen_set:
            continue
        st.add_seen(p["id"])
        if EXCLUDE_RE.search(p["title"]) or FLAIR_EXCLUDE_RE.search(p["flair"]):
            continue
        kws = match_keywords(f"{p['title']} {p['body']}", st.keywords)
        if not kws:
            continue
        p["kws"] = kws
        if not st.booted and now - p["created"] > BOOT_LOOKBACK_MIN * 60:
            st.recent.append(p)  # old: keep for /latest, don't push
            continue
        candidates.append(p)
    st.booted = True

    sent = 0
    for p in candidates:
        if sent >= MAX_PER_CYCLE:
            st.recent.append(p)
            continue
        verdict = await ai_check(ai_client, p)
        if verdict is not None:
            if not verdict.get("relevant", True):
                log.info("AI rejected: %s", p["title"][:80])
                continue
            p["summary"] = verdict.get("summary") or ""
            p["budget"] = verdict.get("budget") or ""
        st.recent.append(p)
        await broadcast(context.bot, st, p)
        sent += 1

    log.info("Checked via %s: %d posts, %d new matches sent", source, len(posts), sent)
    st.save()


# --------------------------------------------------------------- commands
HELP = (
    "🤖 <b>Reddit Jobs Bot</b>\n"
    "I watch hiring subreddits and ping you about web / app dev gigs.\n\n"
    "/latest [n] – last matching posts\n"
    "/check – check Reddit right now\n"
    "/status – bot status\n"
    "/pause · /resume – stop/start alerts\n"
    "/keywords – show keywords\n"
    "/addkw word – add keyword\n"
    "/delkw word – remove keyword\n"
    "/subs – watched subreddits"
)


def _st(ctx: ContextTypes.DEFAULT_TYPE) -> State:
    return ctx.bot_data["state"]


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    cid = update.effective_chat.id
    st.chats.add(cid)
    st.paused.discard(cid)
    st.save()
    await update.message.reply_html(
        f"✅ Subscribed! I'll check every {CHECK_INTERVAL // 60} min.\n\n"
        f"Your chat ID: <code>{cid}</code>\n"
        f"(Put it in the <code>OWNER_CHAT_ID</code> env var on your host so you stay subscribed after restarts.)\n\n"
        + HELP
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(HELP)


async def cmd_latest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    n = 5
    if ctx.args and ctx.args[0].isdigit():
        n = max(1, min(15, int(ctx.args[0])))
    items = list(st.recent)[-n:]
    if not items:
        await update.message.reply_text("Nothing yet – try /check in a moment.")
        return
    for p in items:
        await send_post(ctx.bot, update.effective_chat.id, p)
        await asyncio.sleep(0.2)


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔎 Checking Reddit…")
    ctx.job_queue.run_once(check, 0)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    cid = update.effective_chat.id
    last = ago(st.last_check) if st.last_check else "never"
    msg = (
        f"<b>Status</b>\n"
        f"Alerts: {'⏸ paused' if cid in st.paused else '▶️ on' if cid in st.chats else '❌ not subscribed (/start)'}\n"
        f"Last check: {last} via {st.source or '-'}\n"
        f"Interval: {CHECK_INTERVAL}s\n"
        f"AI filter: {'on (' + html.escape(AI_MODEL) + ')' if AI_API_KEY else 'off'}\n"
        f"Reddit OAuth: {'on' if REDDIT_CLIENT_ID else 'off'}\n"
        f"Keywords: {len(st.keywords)} · Subreddits: {len(SUBREDDITS)}\n"
        f"Subscribers: {len(st.chats)}"
    )
    if st.last_error:
        msg += f"\n\n⚠️ Last error:\n<code>{html.escape(st.last_error)}</code>"
    await update.message.reply_html(msg)


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    st.paused.add(update.effective_chat.id)
    st.save()
    await update.message.reply_text("⏸ Paused. /resume to continue.")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    st.chats.add(update.effective_chat.id)
    st.paused.discard(update.effective_chat.id)
    st.save()
    await update.message.reply_text("▶️ Resumed.")


async def cmd_keywords(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html("<b>Keywords:</b>\n" + html.escape(", ".join(_st(ctx).keywords)))


async def cmd_addkw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    kw = " ".join(ctx.args).strip().lower()
    if not kw:
        await update.message.reply_text("Usage: /addkw nextjs")
        return
    if kw not in st.keywords:
        st.keywords.append(kw)
        st.save()
    await update.message.reply_text(f"➕ Added '{kw}'")


async def cmd_delkw(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    st = _st(ctx)
    kw = " ".join(ctx.args).strip().lower()
    if kw in st.keywords:
        st.keywords.remove(kw)
        st.save()
        await update.message.reply_text(f"➖ Removed '{kw}'")
    else:
        await update.message.reply_text(f"'{kw}' is not in the list.")


async def cmd_subs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Watching: " + ", ".join(f"r/{s}" for s in SUBREDDITS))


# ---------------------------------------------------- health check server
class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # silence
        pass


def start_health_server() -> None:
    server = HTTPServer(("0.0.0.0", PORT), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server on :%d", PORT)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("latest", "Last matching posts"),
        BotCommand("check", "Check Reddit now"),
        BotCommand("status", "Bot status"),
        BotCommand("pause", "Pause alerts"),
        BotCommand("resume", "Resume alerts"),
        BotCommand("keywords", "Show keywords"),
        BotCommand("addkw", "Add keyword"),
        BotCommand("delkw", "Remove keyword"),
        BotCommand("subs", "Watched subreddits"),
        BotCommand("help", "Help"),
    ])


def main() -> None:
    if not TOKEN:
        raise SystemExit("Set the TELEGRAM_TOKEN environment variable.")
    start_health_server()

    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.bot_data["state"] = State()
    app.bot_data["reddit"] = Reddit()
    app.bot_data["ai_client"] = httpx.AsyncClient(timeout=30)

    for name, fn in [
        ("start", cmd_start), ("help", cmd_help), ("latest", cmd_latest), ("check", cmd_check),
        ("status", cmd_status), ("pause", cmd_pause), ("resume", cmd_resume),
        ("keywords", cmd_keywords), ("addkw", cmd_addkw), ("delkw", cmd_delkw), ("subs", cmd_subs),
    ]:
        app.add_handler(CommandHandler(name, fn))

    app.job_queue.run_repeating(check, interval=CHECK_INTERVAL, first=5)
    log.info("Bot starting. Subreddits: %s", ", ".join(SUBREDDITS))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
