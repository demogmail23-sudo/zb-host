import telebot
import os
import subprocess
import threading
import time
import uuid
import zipfile
import shutil
import sqlite3
from werkzeug.utils import secure_filename
from telebot.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton

# ===== CONFIGURATION =====
TOKEN = "8508126044:AAFt_8s5-cQFxS9OTvdvaHhgTMToemvM6sg" # ISKO CHANGE KARO (SECURITY!)
bot = telebot.TeleBot(TOKEN)
UPLOAD_LIMIT = 20
DB_NAME = "hosting_pro.db"

# ===== DATABASE SETUP =====
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (uid INTEGER PRIMARY KEY, username TEXT, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, uid INTEGER, path TEXT, name TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Global tracking for active processes (RAM based for speed)
PROCESSES = {} 

# ===== UTILS =====
def get_db_connection():
    return sqlite3.connect(DB_NAME)

# 

# ===== MENU SYSTEM =====
def main_menu():
    m = ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("📤 Upload Project", "📁 My Dashboard")
    m.add("⚡ Server Speed", "📊 Global Stats")
    m.add("🛠 Settings", "💎 Upgrade Plan")
    return m

# ===== COMMANDS =====
@bot.message_handler(commands=['start'])
def start(msg):
    uid = msg.from_user.id
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (uid, username, status) VALUES (?, ?, ?)", 
              (uid, msg.from_user.username, 'Free'))
    conn.commit()
    
    c.execute("SELECT COUNT(*) FROM files WHERE uid=?", (uid,))
    count = c.fetchone()[0]
    conn.close()

    bot.send_message(msg.chat.id,
        f"🚀 **ZAINU HOSTING ENGINE V3**\n\n"
        f"👤 User: {msg.from_user.first_name}\n"
        f"🆔 ID: `{uid}`\n"
        f"📦 Plan: Free User (Limit: {UPLOAD_LIMIT})\n"
        f"📂 Active Files: {count}\n\n"
        f"✅ Python, Node.js & Static HTML Supported.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ===== SECURE UPLOAD =====
@bot.message_handler(func=lambda m: m.text == "📤 Upload Project")
def upload_prompt(msg):
    bot.send_message(msg.chat.id, "📤 Send your `.py`, `.js`, or `.zip` file.\n\n*Tip:* Use `main.py` or `index.js` as entry points.", parse_mode="Markdown")

@bot.message_handler(content_types=['document'])
def handle_upload(msg):
    uid = msg.from_user.id
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM files WHERE uid=?", (uid,))
    if c.fetchone()[0] >= UPLOAD_LIMIT:
        bot.send_message(msg.chat.id, "❌ Storage Full! Delete old files or upgrade.")
        return

    file_info = bot.get_file(msg.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    # Secure naming
    ext = os.path.splitext(msg.document.file_name)[1]
    filename = secure_filename(f"{uid}_{int(time.time())}{ext}")
    save_path = os.path.join("files", filename)
    
    os.makedirs("files", exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(downloaded_file)

    display_name = msg.document.file_name
    final_path = save_path

    if ext == ".zip":
        extract_dir = os.path.join("files", f"dir_{uid}_{int(time.time())}")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(save_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
        final_path = extract_dir
        display_name = f"📦 {msg.document.file_name}"

    c.execute("INSERT INTO files (uid, path, name) VALUES (?, ?, ?)", (uid, final_path, display_name))
    conn.commit()
    conn.close()
    
    bot.send_message(msg.chat.id, f"✅ Project **{display_name}** deployed to storage!", parse_mode="Markdown")

# ===== DASHBOARD =====
@bot.message_handler(func=lambda m: m.text == "📁 My Dashboard")
def dashboard(msg):
    uid = msg.from_user.id
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, name FROM files WHERE uid=?", (uid,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        bot.send_message(msg.chat.id, "📭 No projects found. Upload one to start.")
        return

    kb = InlineKeyboardMarkup()
    for fid, name in rows:
        kb.add(InlineKeyboardButton(name, callback_data=f"manage_{fid}"))
    
    bot.send_message(msg.chat.id, "🛠 **Control Panel**\nSelect a project to manage:", parse_mode="Markdown", reply_markup=kb)

# ===== PROJECT CONTROLS =====
@bot.callback_query_handler(func=lambda c: c.data.startswith("manage_"))
def manage_project(call):
    fid = int(call.data.split("_")[1])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT path, name FROM files WHERE id=?", (fid,))
    file_data = c.fetchone()
    conn.close()

    if not file_data: return

    path, name = file_data
    status = "🟢 Online" if call.from_user.id in PROCESSES else "🔴 Offline"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("▶️ Run", callback_data=f"run_{fid}"),
           InlineKeyboardButton("⛔ Stop", callback_data=f"stop_{fid}"))
    kb.add(InlineKeyboardButton("📜 Logs", callback_data=f"logs_{fid}"),
           InlineKeyboardButton("🗑 Delete", callback_data=f"del_{fid}"))

    bot.edit_message_text(f"📄 **Project:** {name}\n📊 **Status:** {status}\n📂 **Path:** `{path}`",
                          call.message.chat.id, call.message.message_id, 
                          reply_markup=kb, parse_mode="Markdown")

# ===== EXECUTION LOGIC (The "PythonAnywhere" Style) =====
def execution_thread(uid, fid, path, chat_id):
    try:
        # Check for requirements.txt
        if os.path.isdir(path):
            req_path = os.path.join(path, "requirements.txt")
            if os.path.exists(req_path):
                bot.send_message(chat_id, "📦 Installing dependencies...")
                subprocess.run(["pip", "install", "-r", req_path], check=True)

            # Auto-find main file
            main_file = None
            for f in ["main.py", "app.py", "index.js", "bot.py"]:
                if os.path.exists(os.path.join(path, f)):
                    main_file = os.path.join(path, f)
                    break
            if not main_file:
                bot.send_message(chat_id, "❌ Error: Entry file (main.py/index.js) not found!")
                return
            target = main_file
        else:
            target = path

        # Determine runtime
        cmd = ["python3", target] if target.endswith(".py") else ["node", target]
        
        log_file = f"logs/log_{fid}.txt"
        os.makedirs("logs", exist_ok=True)
        
        with open(log_file, "w") as out:
            p = subprocess.Popen(cmd, stdout=out, stderr=out, text=True)
            PROCESSES[uid] = p
            bot.send_message(chat_id, f"🚀 **Running!**\nYour app is now live in the background.", parse_mode="Markdown")
            p.wait() # Wait for process to end
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Execution Error: {str(e)}")
    finally:
        PROCESSES.pop(uid, None)

@bot.callback_query_handler(func=lambda c: c.data.startswith("run_"))
def run_project(call):
    fid = int(call.data.split("_")[1])
    uid = call.from_user.id
    
    if uid in PROCESSES:
        bot.answer_callback_query(call.id, "⚠️ Another project is already running!")
        return

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT path FROM files WHERE id=?", (fid,))
    path = c.fetchone()[0]
    conn.close()

    threading.Thread(target=execution_thread, args=(uid, fid, path, call.message.chat.id)).start()
    bot.answer_callback_query(call.id, "Starting engine...")

# ... (Logs, Stop, and Delete functions follow similar patterns as old code but with DB updates)

print("🔥 ZAINU HOSTING V3 IS ONLINE")
bot.infinity_polling()
