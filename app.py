from flask import Flask, request, redirect, url_for, render_template, session, flash
import hashlib
import os
import secrets
import sqlite3
import random
import string
from datetime import datetime, timedelta, timezone
from functools import wraps
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.environ.get("SUBSCRIPTIONS_DB", "subscriptions.db")
OWNER_CODE = os.environ.get("OWNER_ADMIN_CODE", "HH35-ADMIN-OWNER-2026")
FRIEND_CODE = os.environ.get("FRIEND_ADMIN_CODE", "HH35-ADMIN-FRIEND-2026")
DURATIONS = {"day": ("يوم", 1), "week": ("أسبوع", 7), "month": ("شهر", 30), "year": ("سنة", 365)}
NAME_LENGTHS = {"double": ("ثنائي", 2), "triple": ("ثلاثي", 3), "quad": ("رباعي", 4), "penta": ("خماسي", 5)}
CHECK_SOURCES = [
    {"name": "DISBOARD", "url": "https://www.disboard.org/search?keyword={value}"},
    {"name": "Top.gg", "url": "https://top.gg/search?q={value}"},
    {"name": "Discord.me", "url": "https://discord.me/search?q={value}"},
    {"name": "Discadia", "url": "https://discadia.com/search?query={value}"},
    {"name": "Discords.com", "url": "https://discords.com/search?q={value}"},
]


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, duration TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT, device_hash TEXT,
            disabled INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL DEFAULT 'owner'
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS admin_access (
            role TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1
        )""")
        connection.execute("INSERT OR IGNORE INTO admin_access(role, enabled) VALUES ('owner', 1), ('friend', 1)")
        connection.commit()


def now():
    return datetime.now(timezone.utc)


def device_hash():
    raw = f"{request.remote_addr or ''}|{request.headers.get('User-Agent', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def status(row):
    if row["disabled"]:
        return "معطل"
    if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= now():
        return "منتهي"
    return "فعال"


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_role"):
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def role_for_code(value):
    if secrets.compare_digest(value, OWNER_CODE):
        return "owner"
    if secrets.compare_digest(value, FRIEND_CODE):
        with db() as connection:
            row = connection.execute("SELECT enabled FROM admin_access WHERE role='friend'").fetchone()
        if row and row["enabled"]:
            return "friend"
    return None


def check_public_sources(value):
    query = value.strip().lower()
    results = []
    for source in CHECK_SOURCES:
        url = source["url"].format(value=requests.utils.quote(query))
        try:
            response = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            found = query in response.text.lower()
            results.append({"name": source["name"], "status": "مستخدم" if found else "لم يظهر", "detail": "ظهر في نتائج الموقع" if found else "لا توجد إشارة واضحة في النتائج العامة", "url": url})
        except requests.RequestException:
            results.append({"name": source["name"], "status": "غير قابل للتأكيد", "detail": "تعذر الوصول إلى المصدر", "url": url})
    return results


def generate_names(length, amount=40):
    # These are candidate names only. Discord does not expose anonymous availability checking.
    alphabet = string.ascii_lowercase + string.digits
    candidates = set()
    while len(candidates) < amount:
        chars = [random.choice(alphabet) for _ in range(length)]
        if length >= 3 and random.random() < 0.35:
            chars[random.randrange(1, length - 1)] = "."
        candidate = "".join(chars).strip(".")
        if candidate and ".." not in candidate:
            candidates.add(candidate)
    return sorted(candidates)


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        value = request.form.get("code", "").strip()
        role = role_for_code(value)
        if role:
            session.clear(); session["admin_role"] = role
            return redirect(url_for("admin"))
        with db() as connection:
            row = connection.execute("SELECT * FROM codes WHERE code = ?", (value,)).fetchone()
            if not row:
                flash("الكود غير صحيح", "error")
            elif status(row) != "فعال":
                flash(f"لا يمكن استخدام الكود: {status(row)}", "error")
            elif row["device_hash"] and row["device_hash"] != device_hash():
                flash("هذا الكود مرتبط بجهاز آخر", "error")
            else:
                expires = now() + timedelta(days=DURATIONS[row["duration"]][1])
                connection.execute("UPDATE codes SET device_hash = COALESCE(device_hash, ?), expires_at = COALESCE(expires_at, ?) WHERE id = ?", (device_hash(), expires.isoformat(), row["id"]))
                connection.commit(); session.clear(); session["subscriber"] = True
                return redirect(url_for("subscriber"))
    return render_template("home.html")


@app.route("/names", methods=["GET", "POST"])
def names():
    selected = request.form.get("kind", "double") if request.method == "POST" else "double"
    amount = int(request.form.get("amount", 40)) if request.method == "POST" else 40
    amount = max(10, min(amount, 100))
    results = generate_names(NAME_LENGTHS.get(selected, NAME_LENGTHS["double"])[1], amount) if request.method == "POST" else []
    return render_template("names.html", results=results, selected=selected, name_lengths=NAME_LENGTHS)


@app.route("/checker", methods=["GET", "POST"])
def checker():
    results = []
    if request.method == "POST":
        value = request.form.get("value", "").strip().lower()
        kind = request.form.get("kind", "invite")
        if kind == "invite":
            shortcut = value.replace("https://discord.gg/", "").replace("http://discord.gg/", "").replace("discord.gg/", "").strip(" /")
            invite_url = f"https://discord.com/api/v9/invites/{shortcut}"
            try:
                response = requests.get(invite_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code == 200: results.append({"name": "Discord", "status": "مستخدم", "detail": "الاختصار موجود في Discord", "url": invite_url})
                elif response.status_code in (400, 404): results.append({"name": "Discord", "status": "غير موجود", "detail": "لم يتم العثور على الاختصار", "url": invite_url})
                else: results.append({"name": "Discord", "status": "غير قابل للتأكيد", "detail": "استجابة غير واضحة من Discord", "url": invite_url})
            except requests.RequestException:
                results.append({"name": "Discord", "status": "غير قابل للتأكيد", "detail": "تعذر الوصول إلى Discord", "url": invite_url})
        else: results = check_public_sources(value)
    return render_template("checker.html", results=results)


@app.route("/subscriber")
def subscriber():
    if not session.get("subscriber"): return redirect(url_for("home"))
    return render_template("subscriber.html")


@app.route("/admin")
@admin_required
def admin():
    role = session["admin_role"]
    with db() as connection:
        rows = connection.execute("SELECT * FROM codes ORDER BY id DESC" if role == "owner" else "SELECT * FROM codes WHERE created_by='friend' ORDER BY id DESC").fetchall()
        friend_enabled = connection.execute("SELECT enabled FROM admin_access WHERE role='friend'").fetchone()["enabled"]
    return render_template("admin.html", codes=rows, status=status, durations=DURATIONS, role=role, friend_enabled=friend_enabled)


@app.post("/admin/codes")
@admin_required
def create_code():
    duration = request.form.get("duration")
    if duration not in DURATIONS: flash("المدة غير صحيحة", "error"); return redirect(url_for("admin"))
    while True:
        value = "hh35-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))
        try:
            with db() as connection:
                connection.execute("INSERT INTO codes (code, duration, created_at, created_by) VALUES (?, ?, ?, ?)", (value, duration, now().isoformat(), session["admin_role"])); connection.commit()
            flash(f"تم إنشاء الكود: {value}", "success"); break
        except sqlite3.IntegrityError: continue
    return redirect(url_for("admin"))


@app.post("/admin/codes/<int:code_id>/disable")
@admin_required
def disable_code(code_id):
    with db() as connection:
        row = connection.execute("SELECT created_by FROM codes WHERE id=?", (code_id,)).fetchone()
        if row and (session["admin_role"] == "owner" or row["created_by"] == "friend"):
            connection.execute("UPDATE codes SET disabled=1 WHERE id=?", (code_id,)); connection.commit()
    return redirect(url_for("admin"))


@app.post("/admin/codes/<int:code_id>/delete")
@admin_required
def delete_code(code_id):
    with db() as connection:
        row = connection.execute("SELECT created_by FROM codes WHERE id=?", (code_id,)).fetchone()
        if row and (session["admin_role"] == "owner" or row["created_by"] == "friend"):
            connection.execute("DELETE FROM codes WHERE id=?", (code_id,)); connection.commit()
    return redirect(url_for("admin"))


@app.post("/admin/friend/toggle")
@admin_required
def toggle_friend():
    if session["admin_role"] != "owner": return redirect(url_for("admin"))
    with db() as connection:
        row = connection.execute("SELECT enabled FROM admin_access WHERE role='friend'").fetchone(); connection.execute("UPDATE admin_access SET enabled=? WHERE role='friend'", (0 if row["enabled"] else 1,)); connection.commit()
    return redirect(url_for("admin"))


@app.get("/logout")
def logout():
    session.clear(); return redirect(url_for("home"))


init_db()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
