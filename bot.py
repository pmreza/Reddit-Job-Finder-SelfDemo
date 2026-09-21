import time
import threading
import requests
import feedparser
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import google.generativeai as genai
import json
import os

# ----------------- CONFIGURATION -----------------
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

DB_FILE = "users_db.json"
SEEN_JOBS_FILE = "seen_jobs.txt"

SUBREDDITS = ['slavelabour', 'forhire', 'DoneDirtCheap', 'javascriptjobs']

# Categories and their keywords
CATEGORIES = {
    "frontend": ["frontend", "front-end", "react", "vue", "javascript", "js", "css", "html", "web"],
    "backend": ["backend", "back-end", ".net", "c#", "python", "django", "node", "api", "sql", "database"],
    "fullstack": ["full stack", "fullstack", "full-stack", "mern", "react", ".net", "django", "web"],
    "design": ["design", "ui", "ux", "figma", "wordpress", "web design", "elementor"]
}

# ----------------- DATABASE FUNCTIONS -----------------
def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4)

def load_seen_jobs():
    if not os.path.exists(SEEN_JOBS_FILE):
        return set()
    with open(SEEN_JOBS_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f)

def save_seen_job(job_id):
    with open(SEEN_JOBS_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{job_id}\n")

# ----------------- TELEGRAM HANDLERS -----------------
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    chat_id = str(message.chat.id)
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("💻 توسعه فرانت‌اند (Frontend)", callback_data="cat_frontend"))
    markup.row(InlineKeyboardButton("⚙️ توسعه بک‌اند (Backend)", callback_data="cat_backend"))
    markup.row(InlineKeyboardButton("🚀 فول‌استک (Full Stack)", callback_data="cat_fullstack"))
    markup.row(InlineKeyboardButton("🎨 طراحی وب / وردپرس", callback_data="cat_design"))
    
    welcome_text = (
        "سلام! به دستیار هوشمند کاریابی ردیت خوش آمدید 🤖\n\n"
        "من به طور مداوم سایت ردیت را بررسی می‌کنم و پروژه‌های مناسب را با استفاده از هوش مصنوعی تحلیل می‌کنم.\n\n"
        "👇 لطفاً حوزه تخصصی که دنبال پروژه‌های آن هستید را انتخاب کنید:"
    )
    bot.send_message(chat_id, welcome_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    chat_id = str(call.message.chat.id)
    category = call.data.replace('cat_', '')
    
    db = load_db()
    db[chat_id] = category
    save_db(db)
    
    category_names = {
        "frontend": "توسعه فرانت‌اند",
        "backend": "توسعه بک‌اند",
        "fullstack": "فول‌استک",
        "design": "طراحی وب / وردپرس"
    }
    
    bot.answer_callback_query(call.id, "تنظیمات شما ذخیره شد!")
    bot.edit_message_text(
        f"✅ تخصص شما روی **{category_names[category]}** تنظیم شد!\n\n"
        f"از این به بعد، هر پروژه‌ای که در این زمینه منتشر شود، ابتدا توسط هوش مصنوعی (Gemini) تحلیل شده و سپس برای شما ارسال می‌شود. منتظر باشید...",
        chat_id=chat_id,
        message_id=call.message.message_id,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['test'])
def test_system(message):
    chat_id = str(message.chat.id)
    bot.send_message(chat_id, "در حال تست سیستم: دریافت جدیدترین پست ردیت و تحلیل توسط هوش مصنوعی... (این کار چند ثانیه زمان می‌برد) ⏳")
    
    db = load_db()
    category = db.get(chat_id, "frontend")
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 RedditJobMonitor/1.0'}
    try:
        response = requests.get("https://www.reddit.com/r/slavelabour/new.rss", headers=headers)
        feed = feedparser.parse(response.text)
        
        test_entry = None
        for entry in feed.entries:
            if '[for hire]' not in entry.title.lower() and '[offer]' not in entry.title.lower():
                test_entry = entry
                break
                
        if not test_entry and feed.entries:
            test_entry = feed.entries[0]
            
        if test_entry:
            comments = get_comment_count(test_entry.link)
            ai_analysis = analyze_with_ai(test_entry.title, test_entry.summary, category)
            
            msg = f"🧪 **[پیام تستی]** این نشان می‌دهد سیستم به درستی کار می‌کند!\n\n"
            msg += f"📌 **ساب‌ردیت:** r/slavelabour\n"
            msg += f"📋 **عنوان:** {test_entry.title}\n"
            msg += f"👥 **تعداد رقبا (کامنت‌ها):** {comments} نفر\n\n"
            msg += f"{ai_analysis}\n\n"
            msg += f"🔗 **لینک:**\n{test_entry.link}"
            
            bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        bot.send_message(chat_id, "خطا در برقراری ارتباط با سرور ردیت یا هوش مصنوعی.")

SUBS_FILE = "subreddits.json"
DEFAULT_SUBS = ['slavelabour', 'forhire', 'DoneDirtCheap', 'javascriptjobs']

def load_subs():
    if not os.path.exists(SUBS_FILE):
        return DEFAULT_SUBS
    with open(SUBS_FILE, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return DEFAULT_SUBS

def save_subs(subs):
    with open(SUBS_FILE, 'w', encoding='utf-8') as f:
        json.dump(subs, f, indent=4)

@bot.message_handler(commands=['addsub'])
def add_subreddit(message):
    chat_id = str(message.chat.id)
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(chat_id, "⚠️ لطفاً نام ساب‌ردیت را وارد کنید. مثال:\n`/addsub pythonjobs`", parse_mode="Markdown")
        return
    
    new_sub = parts[1].replace('r/', '').strip()
    subs = load_subs()
    if new_sub not in subs:
        subs.append(new_sub)
        save_subs(subs)
        bot.send_message(chat_id, f"✅ ساب‌ردیت **r/{new_sub}** با موفقیت به لیست جستجو اضافه شد.", parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "این ساب‌ردیت از قبل در لیست وجود دارد.")

@bot.message_handler(commands=['listsubs'])
def list_subreddits(message):
    subs = load_subs()
    msg = "📋 **لیست ساب‌ردیت‌های فعال:**\n\n"
    for s in subs:
        msg += f"🔹 r/{s}\n"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

# ----------------- AI & REDDIT FUNCTIONS -----------------
def get_comment_count(post_url):
    """Fetch comment count directly from public JSON without API key."""
    if not post_url.endswith('.json'):
        json_url = post_url.rstrip('/') + '/.json'
    else:
        json_url = post_url
        
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 RedditJobMonitor/1.0'}
    try:
        res = requests.get(json_url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data[0]['data']['children'][0]['data']['num_comments']
    except Exception:
        pass
    return "نامشخص"

def analyze_with_ai(title, description, category):
    prompt = f"""
تو یک دستیار ارشد کاریابی هستی. یک آگهی کار از سایت ردیت دریافت می‌کنی.
کاربر من به دنبال پروژه‌های مرتبط با '{category}' است.
آگهی زیر را بخوان و تحلیل کن:
Title: {title}
Description: {description}

لطفاً خروجی را دقیقاً به زبان فارسی و با این فرمت برگردان (فقط همین متن را بده و هیچ چیز اضافه‌ای ننویس):

📝 **تحلیل هوش مصنوعی:** (یک خلاصه ۲ الی ۳ جمله‌ای از اینکه کارفرما دقیقاً چه می‌خواهد و آیا برای این تخصص مناسب است یا نه)
💡 **مهارت‌های مورد نیاز:** (لیست تکنولوژی‌هایی که خواسته شده یا حدس می‌زنی لازم است)
💰 **بودجه تخمینی:** (اگر در متن گفته شده بنویس، اگر نه بنویس 'در متن ذکر نشده')
"""
    try:
        response = ai_model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"AI Error: {e}")
        return "⚠️ متأسفانه در ارتباط با هوش مصنوعی مشکلی پیش آمد."

def check_reddit_jobs():
    while True:
        db = load_db()
        if not db:
            time.sleep(10)
            continue
            
        seen_jobs = load_seen_jobs()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 RedditJobMonitor/1.0'}
        
        current_subs = load_subs()
        for sub in current_subs:
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
                    
                    # Find which users should get this
                    for chat_id, category in list(db.items()):
                        keywords = CATEGORIES.get(category, [])
                        if any(kw in search_text for kw in keywords):
                            
                            # 1. Get comment count (no API key needed!)
                            comments = get_comment_count(entry.link)
                            
                            # 2. Analyze with AI
                            ai_analysis = analyze_with_ai(entry.title, entry.summary, category)
                            
                            # 3. Send message
                            msg = f"🟢 **پروژه جدید پیدا شد!**\n\n"
                            msg += f"📌 **ساب‌ردیت:** r/{sub}\n"
                            msg += f"📋 **عنوان:** {entry.title}\n"
                            msg += f"👥 **تعداد رقبا (کامنت‌ها):** {comments} نفر\n\n"
                            msg += f"{ai_analysis}\n\n"
                            msg += f"🔗 **لینک اصلی پروژه:**\n{entry.link}"
                            
                            try:
                                bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
                            except Exception as e:
                                print(f"Telegram error: {e}")
                                
                            break # don't send the same job multiple times to the same user if categories overlap
                            
                    seen_jobs.add(job_id)
                    save_seen_job(job_id)
                        
            except Exception as e:
                print(f"Error checking {sub}: {e}")
                
        time.sleep(5 * 60)

from http.server import BaseHTTPRequestHandler, HTTPServer

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"Dummy web server listening on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    # Start dummy web server for Render
    web_thread = threading.Thread(target=run_dummy_server, daemon=True)
    web_thread.start()

    # Start reddit checker
    checker_thread = threading.Thread(target=check_reddit_jobs, daemon=True)
    checker_thread.start()
    
    print("Bot is running with AI capabilities! Waiting for users...")
    bot.infinity_polling()
