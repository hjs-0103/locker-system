"""Proof-of-payment uploads for locker assignments."""
import os
import secrets

from flask import (abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

ALLOWED = {".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".png": b"\x89PNG", ".pdf": b"%PDF", ".webp": b"RIFF"}
MAX_BYTES = 5 * 1024 * 1024


def register(app, db, login_required, now, data_dir):

    def upload_dir():
        d = os.path.join(data_dir, "uploads", session["school"])
        os.makedirs(d, exist_ok=True)
        return d

    def fail(msg, code=400):
        return jsonify(ok=False, error=msg), code

    @app.route("/api/assignments/<int:aid>/proof", methods=["POST"])
    @login_required()
    def api_proof(aid):
        d = db()
        a = d.execute("SELECT * FROM assignments WHERE id=?", (aid,)).fetchone() or abort(404)
        f = request.files.get("file")
        if not f or not f.filename:
            return fail("Choose a photo or PDF first")
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED:
            return fail("Only JPG, PNG, WEBP or PDF files are allowed")
        data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return fail("File is too large (max 5 MB)")
        if not data.startswith(ALLOWED[ext]) or (ext == ".webp" and data[8:12] != b"WEBP"):
            return fail("That file doesn't look like a valid " + ext[1:].upper())
        stored = secrets.token_hex(16) + ext
        with open(os.path.join(upload_dir(), stored), "wb") as fh:
            fh.write(data)
        d.execute("""INSERT INTO payments(assignment_id,amount,note,stored_name,original_name,uploaded_at,uploaded_by)
                     VALUES(?,?,?,?,?,?,?)""",
                  (aid, request.form.get("amount", "").strip()[:20], request.form.get("note", "").strip()[:200],
                   stored, os.path.basename(f.filename)[:120], now(), session["user"]))
        d.commit()
        return jsonify(ok=True)

    @app.route("/api/payments/<int:pid>/review", methods=["POST"])
    @login_required()
    def api_review(pid):
        status = (request.get_json(force=True) or {}).get("status")
        if status not in ("approved", "rejected", "pending"):
            return fail("Invalid status")
        db().execute("UPDATE payments SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                     (status, session["user"], now(), pid))
        db().commit()
        return jsonify(ok=True)

    @app.route("/proof/<int:pid>")
    @login_required()
    def proof_file(pid):
        p = db().execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone() or abort(404)
        resp = send_from_directory(upload_dir(), p["stored_name"])
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    @app.route("/payments")
    @login_required()
    def payments():
        st = request.args.get("status", "pending")
        sql = """SELECT p.*, s.name, s.student_no, l.number FROM payments p
                 JOIN assignments a ON a.id=p.assignment_id JOIN students s ON s.id=a.student_id
                 JOIN lockers l ON l.id=a.locker_id"""
        args = []
        if st in ("pending", "approved", "rejected"):
            sql += " WHERE p.status=?"
            args.append(st)
        rows = db().execute(sql + " ORDER BY p.id DESC LIMIT 300", args).fetchall()
        counts = {r[0]: r[1] for r in db().execute("SELECT status, COUNT(*) FROM payments GROUP BY status")}
        unpaid = db().execute("""SELECT l.number, l.location, s.name, s.student_no, a.assigned_at FROM assignments a
            JOIN lockers l ON l.id=a.locker_id JOIN students s ON s.id=a.student_id
            WHERE a.released_at IS NULL AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.assignment_id=a.id)
            ORDER BY a.id DESC""").fetchall()
        return render_template("payments.html", rows=rows, st=st, counts=counts, unpaid=unpaid)

    @app.route("/payments/<int:pid>/review", methods=["POST"])
    @login_required()
    def payment_review(pid):
        status = request.form.get("status")
        if status in ("approved", "rejected"):
            db().execute("UPDATE payments SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
                         (status, session["user"], now(), pid))
            db().commit()
            flash(f"Payment {status}.", "ok")
        return redirect(request.referrer or url_for("payments"))

    @app.errorhandler(413)
    def too_big(_):
        if request.path.startswith("/api/"):
            return fail("File is too large (max 5 MB)", 413)
        return "File too large (max 5 MB).", 413

