from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import subprocess, os, zipfile, functools, sqlite3, shutil, psutil, time, signal
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ZAINU_PRO_99_ULTRA_SECRET")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'database.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
LOG_DIR = os.path.join(BASE_DIR, 'logs')

# Ensure directories exist
for d in [UPLOAD_DIR, LOG_DIR]: 
    os.makedirs(d, exist_ok=True)

RUNNING_PROCESSES = {}

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
        conn.execute('''CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, path TEXT, 
            status TEXT DEFAULT 'Stopped', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"): 
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# --- AUTH ROUTES ---

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        if not u or not p:
            return render_template("signup.html", error="All fields required")
        hashed_p = generate_password_hash(p, method='pbkdf2:sha256')
        try:
            with get_db() as conn:
                conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", (u, hashed_p))
                conn.commit()
            return redirect(url_for('login'))
        except: 
            return render_template("signup.html", error="Username Already Taken")
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        user = get_db().execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
        if user and check_password_hash(user['password'], p):
            session.update({"logged_in": True, "user": u})
            return redirect(url_for('home'))
        return render_template("login.html", error="Invalid Access Key")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/")
@login_required
def home(): 
    return render_template("index.html", user=session['user'])

# --- API ROUTES ---

@app.route("/api/stats")
@login_required
def stats():
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage('/').percent,
        "active": len(RUNNING_PROCESSES)
    })

@app.route("/upload", methods=["POST"])
@login_required
def upload():
    file = request.files.get('file')
    if not file: return "No file", 400
    fname = secure_filename(file.filename)
    path = os.path.join(UPLOAD_DIR, fname)
    file.save(path)
    
    final_path = path
    if fname.endswith(".zip"):
        ext_dir = os.path.join(UPLOAD_DIR, fname + "_dir")
        os.makedirs(ext_dir, exist_ok=True)
        with zipfile.ZipFile(path, 'r') as z: 
            z.extractall(ext_dir)
        for r, _, fs in os.walk(ext_dir):
            for f in fs:
                if f in ["main.py", "app.py", "bot.py"]: 
                    final_path = os.path.join(r, f)
                    break
    
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO bots (name, path) VALUES (?, ?)", (fname, final_path))
        conn.commit()
    return "OK"

@app.route("/api/files")
@login_required
def list_files():
    files = []
    if os.path.exists(UPLOAD_DIR):
        for filename in os.listdir(UPLOAD_DIR):
            file_path = os.path.join(UPLOAD_DIR, filename)
            if os.path.isfile(file_path):
                files.append({
                    "name": filename,
                    "status": "Online" if filename in RUNNING_PROCESSES else "Offline"
                })
    return jsonify(files)

@app.route("/api/bots")
@login_required
def list_bots():
    bots = get_db().execute("SELECT * FROM bots").fetchall()
    res = []
    for b in bots:
        d = dict(b)
        d['status'] = "Running" if d['name'] in RUNNING_PROCESSES else "Stopped"
        res.append(d)
    return jsonify(res)

# --- EXECUTION LOGIC ---

@app.route("/api/start/<name>")
@login_required
def start_bot(name):
    bot = get_db().execute("SELECT * FROM bots WHERE name=?", (name,)).fetchone()
    if not bot: return "Not Found", 404
    if name in RUNNING_PROCESSES: return "Already Running"
    
    log_file = open(os.path.join(LOG_DIR, f"{name}.log"), "a")
    proc = subprocess.Popen(["python3", bot['path']], stdout=log_file, stderr=log_file)
    RUNNING_PROCESSES[name] = proc
    return "OK"

@app.route("/api/stop/<name>")
@login_required
def stop_bot(name):
    if name in RUNNING_PROCESSES:
        proc = RUNNING_PROCESSES[name]
        proc.terminate()
        del RUNNING_PROCESSES[name]
        return "OK"
    return "Not Running"

@app.route("/api/delete_file/<path:filename>")
@login_required
def delete_file(filename):
    try:
        fname = secure_filename(filename)
        path = os.path.join(UPLOAD_DIR, fname)
        if fname in RUNNING_PROCESSES:
            RUNNING_PROCESSES[fname].terminate()
            del RUNNING_PROCESSES[fname]
        if os.path.exists(path):
            if os.path.isfile(path): os.remove(path)
            else: shutil.rmtree(path)
            with get_db() as conn:
                conn.execute("DELETE FROM bots WHERE name=?", (fname,))
                conn.commit()
            return "OK"
        return "Not Found", 404
    except Exception as e:
        return str(e), 500

@app.route("/api/logs/<name>")
@login_required
def get_logs(name):
    log_path = os.path.join(LOG_DIR, f"{name}.log")
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            return f.read()[-5000:]
    return "No logs available."

if __name__ == "__main__":
    # socketio.run ko bypass karke direct app run karo
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
