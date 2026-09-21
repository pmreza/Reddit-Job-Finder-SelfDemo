import os
import time
import json
import threading
import requests
import feedparser
import telebot
import google.generativeai as genai
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

DB_FILE = "users_db.json"
SEEN_FILE = "seen_jobs.txt"
SUBS_FILE = "subreddits.json"
DEFAULT_SUBS = ['slavelabour', 'forhire', 'DoneDirtCheap', 'javascriptjobs']

CATEGORIES = {
    "frontend": ["react", "vue", "angular", "javascript", "html", "css", "tailwind", "next", "frontend", "front-end"],
    "backend": ["python", "django", "node", "express", "sql", "postgres", "mongodb", "fastapi", "backend", "back-end", ".net", "c#"],
    "fullstack": ["fullstack", "full-stack", "mern", "django", "react"],
    "design": ["figma", "ui", "ux", "design", "photoshop", "illustrator"]
}

category_names = {
    "frontend": "برنامه‌نویسی فرانت‌اند",
    "backend": "برنامه‌نویسی بک‌اند",
    "fullstack": "توسعه فول‌استک",
    "design": "طراحی وب و UI/UX"
}

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f)

def load_seen_jobs():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f)

def save_seen_job(job_id):
    with open(SEEN_FILE, "a") as f:
        f.write(f"{job_id}\n")

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

def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔍 جستجوی فوری (۳ پست آخر)")
    markup.row("➕ افزودن ساب‌ردیت", "📋 لیست ساب‌ردیت‌ها")
    markup.row("⚙️ تغییر تخصص")
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = telebot.types.InlineKeyboardMarkup()
    for key, name in category_names.items():
        markup.add(telebot.types.InlineKeyboardButton(name, callback_data=f"cat_{key}"))
    
    bot.send_message(
        message.chat.id,
        "سلام! 👋 به ربات هوشمند کاریابی ردیت خوش آمدید.\n\n"
        "لطفاً تخصص خود را از لیست زیر انتخاب کنید:",
        reply_markup=markup
    )
    bot.send_message(
        message.chat.id,
        "برای دسترسی سریع به امکانات، از دکمه‌های پایین استفاده کنید:",
        reply_markup=get_main_keyboard()
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    category = call.data.split('_')[1]
    chat_id = str(call.message.chat.id)
    
    db = load_db()
    db[chat_id] = category
    save_db(db)
    
    bot.edit_message_text(
        f"✅ تخصص شما روی **{category_names[category]}** تنظیم شد!",
        chat_id=chat_id,
        message_id=call.message.message_id,
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['addsub'])
def add_subreddit_cmd(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "⚠️ لطفاً نام ساب‌ردیت را بنویسید. مثال:\n`/addsub pythonjobs`", parse_mode="Markdown")
        return
    process_add_sub_string(message.chat.id, parts[1])

def process_add_sub_string(chat_id, sub_name):
    new_sub = sub_name.replace('r/', '').strip()
    subs = load_subs()
    if new_sub not in subs:
        subs.append(new_sub)
        save_subs(subs)
        bot.send_message(chat_id, f"✅ ساب‌ردیت **r/{new_sub}** به لیست جستجو اضافه شد.", parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "این ساب‌ردیت از قبل در لیست وجود دارد.")

@bot.message_handler(commands=['listsubs'])
def list_subreddits_cmd(message):
    subs = load_subs()
    msg = "📋 **لیست ساب‌ردیت‌های فعال:**\n\n"
    for s in subs:
        msg += f"🔹 r/{s}\n"
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "⚙️ تغییر تخصص")
def btn_change_specialty(message):
    send_welcome(message)

@bot.message_handler(func=lambda message: message.text == "📋 لیست ساب‌ردیت‌ها")
def btn_list_subs(message):
    list_subreddits_cmd(message)

@bot.message_handler(func=lambda message: message.text == "➕ افزودن ساب‌ردیت")
def btn_add_sub(message):
    msg = bot.send_message(message.chat.id, "لطفاً نام ساب‌ردیت جدید را تایپ کنید (بدون r/):", reply_markup=telebot.types.ReplyKeyboardRemove())
    bot.register_next_step_handler(msg, step_add_sub)

def step_add_sub(message):
    process_add_sub_string(message.chat.id, message.text)
    bot.send_message(message.chat.id, "بازگشت به منوی اصلی", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda message: message.text == "🔍 جستجوی فوری (۳ پست آخر)")
def handle_instant_scrape(message):
    chat_id = str(message.chat.id)
    bot.send_message(chat_id, "در حال جستجو و تحلیل ۳ پست مرتبط اخیر... (چند ثانیه زمان می‌برد) ⏳", reply_markup=get_main_keyboard())
    
    db = load_db()
    category = db.get(chat_id, "frontend")
    keywords = CATEGORIES.get(category, [])
    
    current_subs = load_subs()
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    found_entries = []
    
    for sub in current_subs:
        url = f"https://www.reddit.com/r/{sub}/new.rss"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                feed = feedparser.parse(res.text)
                for entry in feed.entries:
                    title = entry.title.lower()
                    desc = entry.summary.lower()
                    if '[for hire]' in title or '[offer]' in title:
                        continue
                    search_text = title + " " + desc
                    if any(kw in search_text for kw in keywords):
                        found_entries.append((entry, sub))
        except Exception:
            pass

    if not found_entries:
        bot.send_message(chat_id, "پروژه مرتبطی در پست‌های اخیر یافت نشد.")
        return

    found_entries = found_entries[:3]
    
    for entry, sub in found_entries:
        comments = get_comment_count(entry.link)
        ai_analysis = analyze_with_ai(entry.title, entry.summary, category)
        
        msg = f"🧪 **[جستجوی فوری]**\n\n"
        msg += f"📌 **ساب‌ردیت:** r/{sub}\n"
        msg += f"📋 **عنوان:** {entry.title}\n"
        msg += f"👥 **تعداد رقبا (کامنت‌ها):** {comments} نفر\n\n"
        msg += f"{ai_analysis}\n\n"
        msg += f"🔗 **لینک:**\n{entry.link}"
        try:
            bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception:
            pass

def get_comment_count(post_url):
    if not post_url.endswith('.json'):
        json_url = post_url.rstrip('/') + '/.json'
    else:
        json_url = post_url
        
    headers = {'User-Agent': 'Mozilla/5.0'}
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
این یک درخواست کار از ردیت است.
خروجی را دقیقاً با این فرمت بفرست (فقط همین متن):
🔹 **خلاصه کار:** (یک جمله کوتاه)
🛠 **مهارت‌های مورد نیاز:** (لیست تکنولوژی‌ها)
💰 **بودجه:** (اگر ذکر شده بنویس، وگرنه بنویس 'ذکر نشده')

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
        db = load_db()
        if not db:
            time.sleep(10)
            continue
            
        seen_jobs = load_seen_jobs()
        headers = {'User-Agent': 'Mozilla/5.0'}
        
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
                    
                    for chat_id, category in list(db.items()):
                        keywords = CATEGORIES.get(category, [])
                        if any(kw in search_text for kw in keywords):
                            comments = get_comment_count(entry.link)
                            ai_analysis = analyze_with_ai(entry.title, entry.summary, category)
                            
                            msg = f"🚀 **پروژه جدید یافت شد!**\n\n"
                            msg += f"📌 **ساب‌ردیت:** r/{sub}\n"
                            msg += f"📋 **عنوان:** {entry.title}\n"
                            msg += f"👥 **تعداد رقبا (کامنت‌ها):** {comments} نفر\n\n"
                            msg += f"{ai_analysis}\n\n"
                            msg += f"🔗 **لینک:**\n{entry.link}"
                            
                            try:
                                bot.send_message(chat_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
                            except Exception:
                                pass
                            break
                            
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
