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
ai_model = genai.GenerativeModel('gemini-flash-latest')

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
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen_job(job_id):
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        f.write(f"{job_id}\n")

def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🔍 جستجوی پروژه‌های جدید (۵ مورد)")
    markup.row("📋 ساب‌ردیت‌های تحت نظر")
    return markup

def get_apply_keyboard():
    markup = telebot.types.InlineKeyboardMarkup()
    btn = telebot.types.InlineKeyboardButton("✉️ نوشتن پیشنهاد (Cover Letter)", callback_data="generate_cover_letter")
    markup.add(btn)
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

def analyze_batch_with_ai(jobs):
    if not jobs: return []
    prompt = """You are an AI analyzing job posts. Output ONLY a valid JSON array of objects, one for each job, in the exact same order. Do NOT wrap in markdown blocks like ```json.
Format of each object:
{
  "summary": "یک جمله کوتاه درباره نیاز کارفرما",
  "skills": "لیست تکنولوژی‌های مهم (مثل React, Python)",
  "budget": "مبلغ یا 'ذکر نشده'"
}

Jobs to analyze:
"""
    for i, job in enumerate(jobs):
        prompt += f"--- Job {i} ---\nTitle: {job['title']}\nDesc: {job['desc'][:500]}\n\n"
        
    try:
        response = ai_model.generate_content(prompt)
        text = response.text.strip()
        if text.startswith("```json"): text = text[7:-3].strip()
        if text.startswith("```"): text = text[3:-3].strip()
        results = json.loads(text)
        
        # Ensure we return a list of exactly the same length
        while len(results) < len(jobs):
            results.append({"summary": "نامشخص", "skills": "نامشخص", "budget": "نامشخص"})
        return results
    except Exception as e:
        print(f"Batch AI Error: {e}", flush=True)
        return [{"summary": "⚠️ مشکل در هوش مصنوعی", "skills": "نامشخص", "budget": "نامشخص"} for _ in jobs]

@bot.message_handler(func=lambda message: "جستجوی" in message.text)
def handle_instant_scrape(message):
    chat_id = str(message.chat.id)
    save_user(chat_id)
    bot.send_message(chat_id, "🔍 در حال شخم زدن ساب‌ردیت‌ها برای یافتن پروژه‌های مرتبط... ⏳", reply_markup=get_main_keyboard())
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
    found_entries = []
    
    for sub in SUBREDDITS:
        if len(found_entries) >= 5:
            break
            
        url = f"https://www.reddit.com/r/{sub}/new.rss"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                feed = feedparser.parse(res.text)
                for entry in feed.entries:
                    if len(found_entries) >= 5:
                        break
                        
                    title = entry.title.lower()
                    desc = entry.summary.lower()
                    
                    if '[for hire]' in title or '[offer]' in title:
                        continue
                        
                    search_text = title + " " + desc
                    if any(kw in search_text for kw in KEYWORDS):
                        found_entries.append((entry, sub))
        except Exception as e:
            print(f"Error fetching {sub}: {e}")

    if not found_entries:
        bot.send_message(chat_id, "حتی در صدها پست اخیر ساب‌ردیت‌ها هم پروژه مرتبطی با تخصص شما یافت نشد! 😔")
        return

    bot.send_message(chat_id, f"✅ تعداد {len(found_entries)} پروژه پیدا شد. در حال ارسال یک‌جای همه پروژه‌ها به جمینای برای تحلیلِ دسته‌جمعی... ⚡")
    
    # Batch AI processing
    jobs_for_ai = [{"title": e[0].title, "desc": e[0].summary} for e in found_entries]
    ai_results = analyze_batch_with_ai(jobs_for_ai)
    
    for idx, (entry, sub) in enumerate(found_entries):
        pub_date = getattr(entry, 'published', 'نامشخص')
        
        # safely get AI result
        try:
            ai_data = ai_results[idx]
            ai_analysis = f"🔹 <b>خلاصه کار:</b> {ai_data.get('summary', 'نامشخص')}\n🛠 <b>مهارت‌های مورد نیاز:</b> {ai_data.get('skills', 'نامشخص')}\n💰 <b>بودجه:</b> {ai_data.get('budget', 'نامشخص')}"
        except:
            ai_analysis = "⚠️ مشکل در پارس کردن نتیجه هوش مصنوعی."
        
        msg = f"🧪 <b>[جستجوی عمیق]</b>\n\n"
        msg += f"📌 <b>ساب‌ردیت:</b> r/{sub}\n"
        msg += f"📅 <b>تاریخ انتشار:</b> {pub_date}\n"
        msg += f"📋 <b>عنوان:</b> {entry.title}\n\n"
        msg += f"{ai_analysis}\n\n"
        msg += f"🔗 <b>لینک:</b>\n{entry.link}"
        
        try:
            bot.send_message(chat_id, msg, parse_mode="HTML", disable_web_page_preview=True, reply_markup=get_apply_keyboard())
            time.sleep(0.5)
        except Exception:
            pass

@bot.callback_query_handler(func=lambda call: call.data == "generate_cover_letter")
def handle_cover_letter(call):
    bot.answer_callback_query(call.id, "در حال نوشتن پیام... ⏳")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    
    processing_msg = bot.send_message(call.message.chat.id, "✍️ <b>جمینای در حال نوشتن بهترین کاورلتر برای این پروژه است...</b>", parse_mode="HTML")
    
    prompt = f"""
شما یک فریلنسر حرفه‌ای هستید.
یک پیام دایرکت (DM) یا کاورلتر کوتاه، جذاب و بسیار حرفه‌ای به زبان انگلیسی برای کارفرمای این آگهی کار بنویسید.
نکات مهم:
- پیام باید نهایتاً ۳ پاراگراف کوتاه باشد.
- مستقیم سر اصل مطلب بروید و نشان دهید که مشکلشان را می‌فهمید.
- جای خالی برای قرار دادن لینک پورتفولیو در متن بگذارید مانند [Link to my portfolio/website].
- لحن: حرفه‌ای، بااعتمادبه‌نفس و دوستانه.

این اطلاعات آگهی است که کاربر برای آن درخواست می‌دهد:
{call.message.text}
"""
    try:
        response = ai_model.generate_content(prompt)
        cover_letter = response.text.strip()
        
        reply_text = f"✉️ <b>کاورلتر آماده برای ارسال:</b>\n\n<code>{cover_letter}</code>\n\n(متن بالا را کپی کنید، لینک‌های خودتان را جایگزین کنید و برای کارفرما بفرستید)"
        bot.edit_message_text(reply_text, chat_id=call.message.chat.id, message_id=processing_msg.message_id, parse_mode="HTML")
    except Exception as e:
        print(f"Gemini error in cover letter: {e}", flush=True)
        bot.edit_message_text("⚠️ متاسفانه در ارتباط با هوش مصنوعی مشکلی پیش آمد.", chat_id=call.message.chat.id, message_id=processing_msg.message_id)

def check_reddit_jobs():
    while True:
        users = load_users()
        if not users:
            time.sleep(10)
            continue
            
        seen_jobs = load_seen_jobs()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'}
        
        new_entries = []
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
                        new_entries.append((entry, sub))
                        
                    seen_jobs.add(job_id)
                    save_seen_job(job_id)
            except Exception:
                pass
                
        if new_entries:
            jobs_for_ai = [{"title": e[0].title, "desc": e[0].summary} for e in new_entries]
            ai_results = analyze_batch_with_ai(jobs_for_ai)
            
            for idx, (entry, sub) in enumerate(new_entries):
                pub_date = getattr(entry, 'published', 'نامشخص')
                try:
                    ai_data = ai_results[idx]
                    ai_analysis = f"🔹 <b>خلاصه کار:</b> {ai_data.get('summary', 'نامشخص')}\n🛠 <b>مهارت‌های مورد نیاز:</b> {ai_data.get('skills', 'نامشخص')}\n💰 <b>بودجه:</b> {ai_data.get('budget', 'نامشخص')}"
                except:
                    ai_analysis = "⚠️ مشکل در پارس کردن نتیجه هوش مصنوعی."
                
                msg = f"🚀 <b>پروژه جدید یافت شد!</b>\n\n"
                msg += f"📌 <b>ساب‌ردیت:</b> r/{sub}\n"
                msg += f"📅 <b>تاریخ:</b> {pub_date}\n"
                msg += f"📋 <b>عنوان:</b> {entry.title}\n\n"
                msg += f"{ai_analysis}\n\n"
                msg += f"🔗 <b>لینک:</b>\n{entry.link}"
                
                for chat_id in users:
                    try:
                        bot.send_message(chat_id, msg, parse_mode="HTML", disable_web_page_preview=True, reply_markup=get_apply_keyboard())
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
