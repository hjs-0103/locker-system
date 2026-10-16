"""School Locker Management System - Flask + SQLite."""
import csv
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
REGISTRY = os.path.join(DATA_DIR, "registry.db")

app = Flask(__name__)
def _secret():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    p = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(p):
        with open(p, "w") as fh:
            fh.write(secrets.token_hex(32))
    return open(p).read().strip()


app.config["SECRET_KEY"] = _secret()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(os.environ.get("HTTPS")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'staff');
CREATE TABLE IF NOT EXISTS lockers(
  id INTEGER PRIMARY KEY, number TEXT UNIQUE NOT NULL, location TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'available', combination TEXT DEFAULT '', notes TEXT DEFAULT '',
  layout_id INTEGER, gx INTEGER, gy INTEGER);
CREATE TABLE IF NOT EXISTS layouts(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, cols INTEGER NOT NULL, rows INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS students(
  id INTEGER PRIMARY KEY, student_no TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  grade TEXT DEFAULT '', email TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY, locker_id INTEGER NOT NULL REFERENCES lockers(id),
  student_id INTEGER NOT NULL REFERENCES students(id),
  assigned_at TEXT NOT NULL, released_at TEXT, assigned_by TEXT);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY, assignment_id INTEGER NOT NULL REFERENCES assignments(id),
  amount TEXT DEFAULT '', note TEXT DEFAULT '', stored_name TEXT NOT NULL, original_name TEXT,
  uploaded_at TEXT, uploaded_by TEXT, status TEXT NOT NULL DEFAULT 'pending',
  reviewed_by TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def migrate(con):
    """Bring older school databases up to the current schema."""
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(lockers)")}
    for c in ("layout_id", "gx", "gy"):
        if c not in cols:
            con.execute(f"ALTER TABLE lockers ADD COLUMN {c} INTEGER")
    # older grid-based floor plans -> free-form pixel coordinates
    con.execute("UPDATE lockers SET gx=gx*80, gy=gy*100 WHERE layout_id IN (SELECT id FROM layouts WHERE cols>0)")
    con.execute("UPDATE layouts SET cols=0, rows=0 WHERE cols>0")
    con.commit()


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,30}$")


def tenant_path(slug):
    if not SLUG_RE.match(slug or ""):
        abort(400, "Bad school code")
    return os.path.join(DATA_DIR, f"school_{slug}.db")


def registry():
    con = sqlite3.connect(REGISTRY)
    con.execute("CREATE TABLE IF NOT EXISTS schools(slug TEXT PRIMARY KEY, name TEXT, created_at TEXT)")
    return con


def db(slug=None):
    """Connection to the current school's database (from session, or explicit slug at login)."""
    slug = slug or session.get("school")
    if not slug:
        abort(401)
    key = f"db_{slug}"
    if key not in g:
        con = sqlite3.connect(tenant_path(slug))
        con.row_factory = sqlite3.Row
        migrate(con)
        con.execute("PRAGMA foreign_keys=ON")
        setattr(g, key, con)
    return getattr(g, key)


@app.teardown_appcontext
def close_db(_):
    for k in [k for k in g.__dict__ if k.startswith("db_")]:
        g.pop(k).close()


def create_school(slug, name, username, password):
    """Provision a new school: registry entry + its own database + first admin."""
    reg = registry()
    try:
        reg.execute("INSERT INTO schools VALUES(?,?,?)", (slug, name, now()))
        reg.commit()
    finally:
        reg.close()
    con = sqlite3.connect(tenant_path(slug))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,'admin')",
                (username, generate_password_hash(password)))
    con.execute("INSERT INTO settings VALUES('school_name',?)", (name,))
    con.commit()
    con.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


THEMES = {
    "forest": ("#15803d", "#166534", "#ecfdf5"),
    "navy":   ("#1d4ed8", "#1e40af", "#eff6ff"),
    "maroon": ("#be123c", "#9f1239", "#fff1f2"),
    "teal":   ("#0f766e", "#115e59", "#f0fdfa"),
    "plum":   ("#6d28d9", "#5b21b6", "#f5f3ff"),
    "slate":  ("#0f172a", "#1e293b", "#f1f5f9"),
}


def school_cfg():
    if not session.get("school"):
        return dict(school="Locker Manager", term="2026 Academic Year", theme="forest", colors=THEMES["forest"], school_logo="")
    rows = dict(db().execute("SELECT key, value FROM settings").fetchall())
    th = rows.get("theme", "forest")
    if th not in THEMES:
        th = "forest"
    return dict(
        school=rows.get("school_name", "My School"),
        term=rows.get("school_term", "2026 Academic Year"),
        theme=th,
        colors=THEMES[th],
        school_logo=rows.get("school_logo", ""),
    )


# ---------- auth / csrf ----------
def login_required(role=None):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if "user" not in session or "school" not in session:
                return redirect(url_for("login"))
            if role == "admin" and session.get("role") != "admin":
                abort(403)
            return f(*a, **k)
        return w
    return deco


@app.before_request
def csrf():
    if request.method == "POST":
        token = request.form.get("_csrf") or request.headers.get("X-CSRF", "")
        if not secrets.compare_digest(token, session.get("_csrf", "x")):
            abort(400, "Invalid CSRF token")


@app.context_processor
def inject():
    session.setdefault("_csrf", secrets.token_hex(16))
    h = datetime.now().hour
    greet = "Good morning" if h < 12 else ("Good afternoon" if h < 18 else "Good evening")
    return dict(csrf_token=session["_csrf"], greet=greet, today=datetime.now().strftime("%a, %d %b %Y"),
                themes=THEMES, **school_cfg())


# ---------- routes ----------
def school_exists(slug):
    reg = registry()
    try:
        return bool(SLUG_RE.match(slug or "")) and reg.execute(
            "SELECT 1 FROM schools WHERE slug=?", (slug,)).fetchone() is not None
    finally:
        reg.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        slug = request.form.get("school", "").strip().lower()
        u = None
        if school_exists(slug):
            u = db(slug).execute("SELECT * FROM users WHERE username=?",
                                 (request.form["username"].strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form["password"]):
            session.clear()
            session.update(user=u["username"], role=u["role"], school=slug)
            return redirect(url_for("dashboard"))
        flash("Invalid school code, username or password.", "error")
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        f = request.form
        slug = f["school"].strip().lower()
        name, user, pw = f["name"].strip(), f["username"].strip(), f["password"]
        if not SLUG_RE.match(slug):
            flash("School code: 3-31 chars, lowercase letters, digits, dashes.", "error")
        elif not name or not user or len(pw) < 8:
            flash("Fill all fields; password needs 8+ characters.", "error")
        elif school_exists(slug):
            flash("That school code is taken.", "error")
        else:
            create_school(slug, name, user, pw)
            session.clear()
            session.update(user=user, role="admin", school=slug)
            return redirect(url_for("dashboard"))
    return render_template("signup.html")



@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    if "user" not in session or "school" not in session:
        return render_template("landing.html")
    d = db()
    stats = {s: d.execute("SELECT COUNT(*) FROM lockers WHERE status=?", (s,)).fetchone()[0]
             for s in ("available", "assigned", "maintenance")}
    stats["total"] = sum(stats.values())
    stats["students"] = d.execute("SELECT COUNT(*) FROM students WHERE active=1").fetchone()[0]
    stats["unassigned"] = d.execute("""SELECT COUNT(*) FROM students s WHERE active=1 AND NOT EXISTS
        (SELECT 1 FROM assignments a WHERE a.student_id=s.id AND a.released_at IS NULL)""").fetchone()[0]
    stats["pending_pays"] = d.execute("SELECT COUNT(*) FROM payments WHERE status='pending'").fetchone()[0]
    plans = d.execute("""SELECT y.id, y.name,
            COUNT(l.id) AS total,
            SUM(CASE WHEN l.status='assigned' THEN 1 ELSE 0 END) AS assigned,
            SUM(CASE WHEN l.status='available' THEN 1 ELSE 0 END) AS free
        FROM layouts y LEFT JOIN lockers l ON l.layout_id=y.id
        GROUP BY y.id ORDER BY y.name""").fetchall()
    recent = d.execute("""SELECT a.*, l.number, s.name FROM assignments a
        JOIN lockers l ON l.id=a.locker_id JOIN students s ON s.id=a.student_id
        ORDER BY a.id DESC LIMIT 8""").fetchall()
    return render_template("dashboard.html", stats=stats, plans=plans, recent=recent)


@app.route("/lockers")
@login_required()
def lockers():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    sql = """SELECT l.*, s.name AS student, s.id AS student_id, s.student_no
             FROM lockers l LEFT JOIN assignments a ON a.locker_id=l.id AND a.released_at IS NULL
             LEFT JOIN students s ON s.id=a.student_id WHERE 1=1"""
    args = []
    if q:
        sql += " AND (l.number LIKE ? OR l.location LIKE ? OR s.name LIKE ?)"
        args += [f"%{q}%"] * 3
    if status:
        sql += " AND l.status=?"
        args.append(status)
    rows = db().execute(sql + " ORDER BY l.location, CAST(l.number AS INTEGER), l.number", args).fetchall()
    studs = db().execute("""SELECT * FROM students s WHERE active=1 AND NOT EXISTS
        (SELECT 1 FROM assignments a WHERE a.student_id=s.id AND a.released_at IS NULL)
        ORDER BY name""").fetchall()
    return render_template("lockers.html", lockers=rows, students=studs, q=q, status=status)


@app.route("/lockers/add", methods=["POST"])
@login_required()
def locker_add():
    """Add one locker, or a range (start..end) with optional prefix."""
    f = request.form
    prefix, loc = f.get("prefix", "").strip(), f.get("location", "").strip()
    try:
        start = int(f["start"])
        end = int(f.get("end") or start)
    except ValueError:
        flash("Start/end must be numbers.", "error")
        return redirect(url_for("lockers"))
    if end < start or end - start > 999:
        flash("Invalid range (max 1000 at once).", "error")
        return redirect(url_for("lockers"))
    added = 0
    for n in range(start, end + 1):
        try:
            db().execute("INSERT INTO lockers(number,location,combination) VALUES(?,?,?)",
                         (f"{prefix}{n}", loc, f.get("combination", "")))
            added += 1
        except sqlite3.IntegrityError:
            pass
    db().commit()
    flash(f"Added {added} locker(s).", "ok")
    return redirect(url_for("lockers"))


@app.route("/lockers/<int:lid>/assign", methods=["POST"])
@login_required()
def locker_assign(lid):
    d = db()
    sid = request.form.get("student_id", type=int)
    l = d.execute("SELECT * FROM lockers WHERE id=?", (lid,)).fetchone() or abort(404)
    if l["status"] != "available":
        flash("Locker is not available.", "error")
    elif d.execute("SELECT 1 FROM assignments WHERE student_id=? AND released_at IS NULL", (sid,)).fetchone():
        flash("Student already has a locker.", "error")
    elif not d.execute("SELECT 1 FROM students WHERE id=? AND active=1", (sid,)).fetchone():
        flash("Student not found.", "error")
    else:
        d.execute("INSERT INTO assignments(locker_id,student_id,assigned_at,assigned_by) VALUES(?,?,?,?)",
                  (lid, sid, now(), session["user"]))
        d.execute("UPDATE lockers SET status='assigned' WHERE id=?", (lid,))
        d.commit()
        flash(f"Locker {l['number']} assigned.", "ok")
    return redirect(request.referrer or url_for("lockers"))


@app.route("/lockers/<int:lid>/release", methods=["POST"])
@login_required()
def locker_release(lid):
    d = db()
    d.execute("UPDATE assignments SET released_at=? WHERE locker_id=? AND released_at IS NULL", (now(), lid))
    d.execute("UPDATE lockers SET status='available' WHERE id=? AND status='assigned'", (lid,))
    d.commit()
    flash("Locker released.", "ok")
    return redirect(request.referrer or url_for("lockers"))


@app.route("/lockers/<int:lid>/status", methods=["POST"])
@login_required()
def locker_status(lid):
    """Toggle maintenance (only for lockers that aren't assigned)."""
    d = db()
    l = d.execute("SELECT * FROM lockers WHERE id=?", (lid,)).fetchone() or abort(404)
    if l["status"] == "assigned":
        flash("Release the locker first.", "error")
    else:
        new = "available" if l["status"] == "maintenance" else "maintenance"
        d.execute("UPDATE lockers SET status=?, notes=? WHERE id=?",
                  (new, request.form.get("notes", l["notes"]), lid))
        d.commit()
    return redirect(request.referrer or url_for("lockers"))


@app.route("/lockers/<int:lid>/delete", methods=["POST"])
@login_required()
def locker_delete(lid):
    d = db()
    d.execute("DELETE FROM payments WHERE assignment_id IN (SELECT id FROM assignments WHERE locker_id=?)", (lid,))
    d.execute("DELETE FROM assignments WHERE locker_id=?", (lid,))
    d.execute("DELETE FROM lockers WHERE id=?", (lid,))
    d.commit()
    flash("Locker deleted.", "ok")
    return redirect(url_for("lockers"))


@app.route("/students")
@login_required()
def students():
    q = request.args.get("q", "").strip()
    sql = """SELECT s.*, l.number AS locker, l.id AS locker_id FROM students s
             LEFT JOIN assignments a ON a.student_id=s.id AND a.released_at IS NULL
             LEFT JOIN lockers l ON l.id=a.locker_id WHERE s.active=1"""
    args = []
    if q:
        sql += " AND (s.name LIKE ? OR s.student_no LIKE ? OR s.grade LIKE ?)"
        args = [f"%{q}%"] * 3
    rows = db().execute(sql + " ORDER BY s.grade, s.name", args).fetchall()
    free = db().execute("SELECT * FROM lockers WHERE status='available' ORDER BY location, CAST(number AS INTEGER)").fetchall()
    return render_template("students.html", students=rows, free=free, q=q)


@app.route("/students/add", methods=["POST"])
@login_required()
def student_add():
    f = request.form
    try:
        db().execute("INSERT INTO students(student_no,name,grade,email) VALUES(?,?,?,?)",
                     (f["student_no"].strip(), f["name"].strip(), f.get("grade", "").strip(),
                      f.get("email", "").strip()))
        db().commit()
        flash("Student added.", "ok")
    except sqlite3.IntegrityError:
        flash("Student number already exists.", "error")
    return redirect(url_for("students"))


@app.route("/students/import", methods=["POST"])
@login_required()
def student_import():
    """CSV columns: student_no,name,grade,email (header row required)."""
    file = request.files.get("file")
    if not file:
        flash("Choose a CSV file.", "error")
        return redirect(url_for("students"))
    text = file.read().decode("utf-8-sig", errors="replace")
    added = skipped = 0
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if not r.get("student_no") or not r.get("name"):
            skipped += 1
            continue
        try:
            db().execute("INSERT INTO students(student_no,name,grade,email) VALUES(?,?,?,?)",
                         (r["student_no"], r["name"], r.get("grade", ""), r.get("email", "")))
            added += 1
        except sqlite3.IntegrityError:
            skipped += 1
    db().commit()
    flash(f"Imported {added} students, skipped {skipped}.", "ok")
    return redirect(url_for("students"))


@app.route("/students/<int:sid>/deactivate", methods=["POST"])
@login_required()
def student_deactivate(sid):
    """Archive a student (e.g. graduated) and free their locker."""
    d = db()
    d.execute("""UPDATE lockers SET status='available' WHERE id IN
        (SELECT locker_id FROM assignments WHERE student_id=? AND released_at IS NULL)""", (sid,))
    d.execute("UPDATE assignments SET released_at=? WHERE student_id=? AND released_at IS NULL", (now(), sid))
    d.execute("UPDATE students SET active=0 WHERE id=?", (sid,))
    d.commit()
    flash("Student archived and locker released.", "ok")
    return redirect(url_for("students"))


@app.route("/history")
@login_required()
def history():
    rows = db().execute("""SELECT a.*, l.number, l.location, s.name, s.student_no FROM assignments a
        JOIN lockers l ON l.id=a.locker_id JOIN students s ON s.id=a.student_id
        ORDER BY a.id DESC LIMIT 500""").fetchall()
    return render_template("history.html", rows=rows)


@app.route("/export/<kind>.csv")
@login_required()
def export(kind):
    d = db()
    if kind == "lockers":
        cur = d.execute("""SELECT l.number,l.location,l.status,l.combination,s.student_no,s.name,s.grade
            FROM lockers l LEFT JOIN assignments a ON a.locker_id=l.id AND a.released_at IS NULL
            LEFT JOIN students s ON s.id=a.student_id ORDER BY l.location, l.number""")
    elif kind == "history":
        cur = d.execute("""SELECT l.number,s.student_no,s.name,a.assigned_at,a.released_at,a.assigned_by
            FROM assignments a JOIN lockers l ON l.id=a.locker_id JOIN students s ON s.id=a.student_id
            ORDER BY a.id""")
    else:
        abort(404)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([c[0] for c in cur.description])
    w.writerows([tuple(r) for r in cur.fetchall()])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={kind}.csv"})


LOGO_SIGS = {".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".webp": b"RIFF"}


@app.route("/school-logo")
@login_required()
def school_logo_file():
    r = db().execute("SELECT value FROM settings WHERE key='school_logo'").fetchone()
    if not r or not r["value"]:
        abort(404)
    folder = os.path.join(DATA_DIR, "uploads", session["school"])
    resp = send_from_directory(folder, r["value"])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/settings", methods=["GET", "POST"])
@login_required("admin")
def settings():
    d = db()
    if request.method == "POST":
        act = request.form["action"]
        if act == "school":
            d.execute("INSERT OR REPLACE INTO settings VALUES('school_name',?)", (request.form["school_name"].strip(),))
            if "school_term" in request.form:
                d.execute("INSERT OR REPLACE INTO settings VALUES('school_term',?)", (request.form["school_term"].strip(),))
            if request.form.get("theme") in THEMES:
                d.execute("INSERT OR REPLACE INTO settings VALUES('theme',?)", (request.form["theme"],))
            logo = request.files.get("logo")
            if logo and logo.filename:
                ext = os.path.splitext(logo.filename)[1].lower()
                raw = logo.read(2 * 1024 * 1024 + 1)
                if ext not in LOGO_SIGS or not raw.startswith(LOGO_SIGS[ext]) or len(raw) > 2 * 1024 * 1024:
                    flash("Logo must be a valid PNG, JPG or WEBP image (max 2 MB).", "error")
                    return redirect(url_for("settings"))
                folder = os.path.join(DATA_DIR, "uploads", session["school"])
                os.makedirs(folder, exist_ok=True)
                fname = "logo_" + secrets.token_hex(6) + ext
                with open(os.path.join(folder, fname), "wb") as fh:
                    fh.write(raw)
                d.execute("INSERT OR REPLACE INTO settings VALUES('school_logo',?)", (fname,))
            flash("School profile updated.", "ok")
        elif act == "remove_logo":
            d.execute("DELETE FROM settings WHERE key='school_logo'")
            flash("School logo removed.", "ok")
        elif act == "user":
            try:
                d.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                          (request.form["username"].strip(), generate_password_hash(request.form["password"]),
                           request.form["role"]))
            except sqlite3.IntegrityError:
                flash("Username taken.", "error")
        elif act == "password":
            d.execute("UPDATE users SET password_hash=? WHERE username=?",
                      (generate_password_hash(request.form["password"]), session["user"]))
            flash("Password changed.", "ok")
        elif act == "year_end":
            d.execute("UPDATE assignments SET released_at=? WHERE released_at IS NULL", (now(),))
            d.execute("UPDATE lockers SET status='available' WHERE status='assigned'")
            flash("All lockers released for the new year.", "ok")
        d.commit()
        return redirect(url_for("settings"))
    users = d.execute("SELECT username, role FROM users").fetchall()
    return render_template("settings.html", users=users)


@app.errorhandler(403)
def forbidden(_):
    return "Forbidden - admin only.", 403


import layouts as _layouts  # noqa: E402
import payments as _payments  # noqa: E402

app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
_layouts.register(app, db, login_required, now)
_payments.register(app, db, login_required, now, DATA_DIR)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
