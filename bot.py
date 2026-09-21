import os
import time
import json
import threading
import requests
import feedparser
import telebot
import google.generativeai as genai
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

def load_env():
    env_vars = {}
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    env_vars[k] = v
    return env_vars

env = load_env()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or env.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY", "")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

SEEN_FILE = "seen_jobs.txt"
USERS_FILE = "chat_ids.txt"

SUBREDDITS = [
    'slavelabour', 'forhire', 'DoneDirtCheap', 'javascriptjobs',
    'freelance_forhire', 'remotejs', 'hiring', 'freelance',
    'DesignJobs', 'webdevjobs', 'UIUX', 'gameDevClassifieds', 'Jobs4Bitcoins'
]

KEYWORDS = [
    "react", "vue", "angular", "javascript", "html", "css", "tailwind", "next", 
    "frontend", "front-end", "python", "django", "node", "express", "sql", 
    "postgres", "mongodb", "fastapi", "backend", "back-end", ".net", "c#", 
    "fullstack", "full-stack", "mern", "figma", "ui", "ux", "design", 
    "photoshop", "illustrator", "app", "mobile", "react native", "flutter",
    "web", "website", "developer", "programmer"
]

def load_users():
    if not os.path.exists(USERS_FILE):
        return set()
    with open(USERS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_user(chat_id):
    users = load_users()
    if chat_id not in users:
        with open(USERS_FILE, "a") as f:
            f.write(f"{chat_id}\n")

def load_seen_jobs():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_job(job_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{job_id}\n")

def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔍 جستجوی پروژه‌های جدید (۱۰ مورد)")
    markup.row("📋 ساب‌ردیت‌های تحت نظر")
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = str(message.chat.id)
    save_user(chat_id)
    bot.send_message(
        chat_id,
        "سلام! 👋 به ربات هوشمند کاریابی ردیت خوش آمدید.\n\n"
        "این ربات به صورت خودکار پست‌های استخدامی را مانیتور می‌کند و پروژه‌های جدید را با استفاده از هوش مصنوعی برای شما تحلیل می‌کند.\n\n"
        "از دکمه‌های پایین استفاده کنید:",
        reply_markup=get_main_keyboard()
    )

@bot.message_handler(func=lambda message: "تحت نظر" in message.text or "لیست" in message.text)
def list_subreddits_cmd(message):
    chat_id = str(message.chat.id)
    save_user(chat_id)
    msg = "📋 <b>لیست ساب‌ردیت‌هایی که هم‌اکنون در حال جستجو هستند:</b>\n\n"
    for s in SUBREDDITS:
        msg += f"🔹 r/{s}\n"
    bot.send_message(message.chat.id, msg, parse_mode="HTML")

@bot.message_handler(func=lambda message: "جستجوی" in message.text)
def handle_instant_scrape(message):
    chat_id = str(message.chat.id)
    save_user(chat_id)
    bot.send_message(chat_id, "🔍 در حال شخم زدن ساب‌ردیت‌ها برای یافتن دقیقاً ۱۰ پروژه مرتبط... (ممکن است چند ثانیه تا یک دقیقه طول بکشد) ⏳", reply_markup=get_main_keyboard())
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 RedditJobBot/1.0'}
    found_entries = []
    
    for sub in SUBREDDITS:
        if len(found_entries) >= 10:
            break
            
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=100"
        after = None
        
        for _ in range(3):
            if len(found_entries) >= 10:
                break
            
            fetch_url = url
            if after:
                fetch_url += f"&after={after}"
                
            try:
                res = requests.get(fetch_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    posts = data.get('data', {}).get('children', [])
                    after = data.get('data', {}).get('after')
                    
                    for post in posts:
                        if len(found_entries) >= 10:
                            break
                            
                        post_data = post.get('data', {})
                        title = post_data.get('title', '').lower()
                        desc = post_data.get('selftext', '').lower()
                        
                        if '[for hire]' in title or '[offer]' in title:
                            continue
                            
                        search_text = title + " " + desc
                        if any(kw in search_text for kw in KEYWORDS):
                            created_utc = post_data.get('created_utc', 0)
                            date_str = datetime.datetime.utcfromtimestamp(created_utc).strftime('%Y-%m-%d %H:%M UTC')
                            
                            class MockEntry:
                                def __init__(self, t, d, l, c):
                                    self.title = t
                                    self.summary = d
                                    self.link = l
                                    self.published = c
                            
                            entry = MockEntry(
                                post_data.get('title', ''),
                                post_data.get('selftext', ''),
                                "https://www.reddit.com" + post_data.get('permalink', ''),
                                date_str
                            )
                            found_entries.append((entry, sub, post_data.get('num_comments', 0)))
                            
                    if not after:
                        break
                else:
                    break
            except Exception as e:
                print(f"Error fetching {sub}: {e}")
                break

    if not found_entries:
        bot.send_message(chat_id, "حتی در صدها پست اخیر ساب‌ردیت‌ها هم پروژه مرتبطی با تخصص شما یافت نشد! 😔 (ممکن است سرور ردیت دسترسی را موقتاً محدود کرده باشد)")
        return

    bot.send_message(chat_id, f"✅ تعداد {len(found_entries)} پست کاملاً مرتبط پیدا شد. در حال استخراج جزئیات با هوش مصنوعی...")
    
    for entry, sub, comments in found_entries:
        ai_analysis = analyze_with_ai(entry.title, entry.summary)
        
        msg = f"🧪 <b>[جستجوی عمیق]</b>\n\n"
        msg += f"📌 <b>ساب‌ردیت:</b> r/{sub}\n"
        msg += f"📅 <b>تاریخ انتشار:</b> {entry.published}\n"
        msg += f"📋 <b>عنوان:</b> {entry.title}\n"
        msg += f"👥 <b>تعداد رقبا (کامنت‌ها):</b> {comments} نفر\n\n"
        msg += f"{ai_analysis}\n\n"
        msg += f"🔗 <b>لینک:</b>\n{entry.link}"
        try:
            bot.send_message(chat_id, msg, parse_mode="HTML", disable_web_page_preview=True)
            time.sleep(1.5)
        except Exception:
            pass

def get_comment_count(post_url):
    if not post_url.endswith('.json'):
        json_url = post_url.rstrip('/') + '/.json'
    else:
        json_url = post_url
        
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 RedditJobBot/1.0'}
    try:
        res = requests.get(json_url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data[0]['data']['children'][0]['data']['num_comments']
    except Exception:
        pass
    return "نامشخص"

def analyze_with_ai(title, description):
    prompt = f"""
این یک درخواست کار از ردیت است.
خروجی را دقیقاً با این فرمت بفرست (فقط همین متن را بفرست):
🔹 <b>خلاصه کار:</b> (یک جمله کوتاه درباره نیاز کارفرما)
🛠 <b>مهارت‌های مورد نیاز:</b> (لیست تکنولوژی‌های مهم)
💰 <b>بودجه:</b> (اگر ذکر شده بنویس، وگرنه بنویس 'ذکر نشده')

Title: {title}
Description: {description}
"""
    try:
        response = ai_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return "⚠️ مشکل در ارتباط با هوش مصنوعی."

def check_reddit_jobs():
    while True:
        users = load_users()
        if not users:
            time.sleep(10)
            continue
            
        seen_jobs = load_seen_jobs()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 RedditJobBot/1.0'}
        
        for sub in SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/new.rss"
            try:
                response = requests.get(url, headers=headers)
                if response.status_code != 200:
                    continue
                feed = feedparser.parse(response.text)
                for entry in feed.entries:
                    job_id = entry.id
                    if job_id in seen_jobs:
                        continue
                    
                    title = entry.title.lower()
                    desc = entry.summary.lower()
                    
                    if '[for hire]' in title or '[offer]' in title:
                        seen_jobs.add(job_id)
                        save_seen_job(job_id)
                        continue
                    
                    search_text = title + " " + desc
                    
                    if any(kw in search_text for kw in KEYWORDS):
                        comments = get_comment_count(entry.link)
                        ai_analysis = analyze_with_ai(entry.title, entry.summary)
                        
                        pub_date = getattr(entry, 'published', 'نامشخص')
                        
                        msg = f"🚀 <b>پروژه جدید یافت شد!</b>\n\n"
                        msg += f"📌 <b>ساب‌ردیت:</b> r/{sub}\n"
                        msg += f"📅 <b>تاریخ:</b> {pub_date}\n"
                        msg += f"📋 <b>عنوان:</b> {entry.title}\n"
                        msg += f"👥 <b>تعداد رقبا (کامنت‌ها):</b> {comments} نفر\n\n"
                        msg += f"{ai_analysis}\n\n"
                        msg += f"🔗 <b>لینک:</b>\n{entry.link}"
                        
                        for chat_id in users:
                            try:
                                bot.send_message(chat_id, msg, parse_mode="HTML", disable_web_page_preview=True)
                            except Exception:
                                pass
                                
                    seen_jobs.add(job_id)
                    save_seen_job(job_id)
                        
            except Exception:
                pass
                
        time.sleep(5 * 60)

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

if __name__ == "__main__":
    web_thread = threading.Thread(target=run_dummy_server, daemon=True)
    web_thread.start()

    checker_thread = threading.Thread(target=check_reddit_jobs, daemon=True)
    checker_thread.start()
    
    bot.infinity_polling()
