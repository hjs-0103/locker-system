"""Floor-plan editor: a free-form canvas where lockers are dragged in, named and moved around."""
import sqlite3

from flask import abort, flash, jsonify, redirect, render_template, request, session, url_for

LIMIT = 10000          # canvas coordinate limit (px)
STEP_X, STEP_Y = 72, 110


def register(app, db, login_required, now):

    def state(layout_id):
        d = db()
        lay = d.execute("SELECT * FROM layouts WHERE id=?", (layout_id,)).fetchone() or abort(404)
        lockers = d.execute("""SELECT l.id,l.number,l.gx AS x,l.gy AS y,l.status,l.notes,
                s.id AS student_id, s.name AS student, s.student_no, s.grade,
                a.id AS assignment_id, p.id AS pay_id, p.status AS pay_status, p.amount AS pay_amount,
                p.uploaded_at AS pay_date
            FROM lockers l LEFT JOIN assignments a ON a.locker_id=l.id AND a.released_at IS NULL
            LEFT JOIN students s ON s.id=a.student_id
            LEFT JOIN payments p ON p.id=(SELECT MAX(id) FROM payments WHERE assignment_id=a.id)
            WHERE l.layout_id=?""", (layout_id,)).fetchall()
        free = d.execute("""SELECT id,name,student_no,grade FROM students s WHERE active=1 AND NOT EXISTS
            (SELECT 1 FROM assignments a WHERE a.student_id=s.id AND a.released_at IS NULL)
            ORDER BY name""").fetchall()
        return {"layout": dict(lay), "lockers": [dict(r) for r in lockers], "students": [dict(r) for r in free]}

    def fail(msg, code=400):
        return jsonify(ok=False, error=msg), code

    def locker_or_404(lid):
        return db().execute("SELECT * FROM lockers WHERE id=?", (lid,)).fetchone() or abort(404)

    def layout_or_404(lid):
        return db().execute("SELECT * FROM layouts WHERE id=?", (lid,)).fetchone() or abort(404)

    def coord(v):
        try:
            return max(0, min(LIMIT, int(v)))
        except (TypeError, ValueError):
            return None

    def default_name(layout_id):
        n = db().execute("SELECT COUNT(*) FROM lockers WHERE layout_id=?", (layout_id,)).fetchone()[0] + 1
        while db().execute("SELECT 1 FROM lockers WHERE number=?", (f"Locker {n}",)).fetchone():
            n += 1
        return f"Locker {n}"

    # ---------- pages ----------
    @app.route("/layouts")
    @login_required()
    def layouts():
        rows = db().execute("SELECT * FROM layouts ORDER BY name").fetchall()
        out = []
        for y in rows:
            ls = db().execute("SELECT gx,gy,status FROM lockers WHERE layout_id=?", (y["id"],)).fetchall()
            mx = max([l["gx"] for l in ls] + [1]) + STEP_X
            my = max([l["gy"] for l in ls] + [1]) + STEP_Y
            dots = [dict(x=l["gx"] / mx * 100, y=l["gy"] / my * 100, s=l["status"]) for l in ls]
            out.append(dict(id=y["id"], name=y["name"], total=len(ls),
                            assigned=sum(1 for l in ls if l["status"] == "assigned"), dots=dots,
                            ratio=min(max(mx / my, .6), 2.2)))
        return render_template("layouts.html", layouts=out)

    @app.route("/layouts/create", methods=["POST"])
    @login_required()
    def layout_create():
        name = request.form.get("name", "").strip() or "New floor plan"
        cur = db().execute("INSERT INTO layouts(name,cols,rows) VALUES(?,0,0)", (name[:60],))
        db().commit()
        return redirect(url_for("layout_edit", lid=cur.lastrowid))

    @app.route("/layouts/<int:lid>")
    @login_required()
    def layout_edit(lid):
        return render_template("layout_edit.html", lay=state(lid)["layout"])

    @app.route("/layouts/<int:lid>/delete", methods=["POST"])
    @login_required("admin")
    def layout_delete(lid):
        d = db()
        if d.execute("SELECT 1 FROM lockers WHERE layout_id=? AND status='assigned'", (lid,)).fetchone():
            flash("Release assigned lockers before deleting this floor plan.", "error")
            return redirect(url_for("layouts"))
        d.execute("DELETE FROM lockers WHERE layout_id=? AND id NOT IN (SELECT locker_id FROM assignments)", (lid,))
        d.execute("UPDATE lockers SET layout_id=NULL,gx=NULL,gy=NULL,status='maintenance',notes='Floor plan deleted' WHERE layout_id=?", (lid,))
        d.execute("DELETE FROM layouts WHERE id=?", (lid,))
        d.commit()
        flash("Floor plan deleted.", "ok")
        return redirect(url_for("layouts"))

    # ---------- JSON api ----------
    @app.route("/api/layouts/<int:lid>")
    @login_required()
    def api_layout(lid):
        return jsonify(state(lid))

    @app.route("/api/layouts/<int:lid>/rename", methods=["POST"])
    @login_required()
    def api_layout_rename(lid):
        layout_or_404(lid)
        name = ((request.get_json(force=True) or {}).get("name") or "").strip()[:60]
        if not name:
            return fail("Give the floor plan a name")
        db().execute("UPDATE layouts SET name=? WHERE id=?", (name, lid))
        db().commit()
        return jsonify(ok=True)

    @app.route("/api/layouts/<int:lid>/add", methods=["POST"])
    @login_required()
    def api_add(lid):
        j = request.get_json(force=True) or {}
        lay = layout_or_404(lid)
        x, y = coord(j.get("x")), coord(j.get("y"))
        if x is None or y is None:
            return fail("Invalid position")
        name = (j.get("name") or "").strip()[:40] or default_name(lid)
        try:
            cur = db().execute("INSERT INTO lockers(number,location,layout_id,gx,gy) VALUES(?,?,?,?,?)",
                               (name, lay["name"], lid, x, y))
            db().commit()
        except sqlite3.IntegrityError:
            return fail(f"A locker called “{name}” already exists")
        return jsonify(ok=True, id=cur.lastrowid)

    @app.route("/api/layouts/<int:lid>/addmany", methods=["POST"])
    @login_required()
    def api_addmany(lid):
        """Add N lockers in tidy rows below everything already on the plan."""
        lay = layout_or_404(lid)
        try:
            n = int((request.get_json(force=True) or {}).get("count"))
        except (TypeError, ValueError):
            return fail("Enter a number")
        if not 1 <= n <= 200:
            return fail("Choose between 1 and 200")
        row = db().execute("SELECT MAX(gy) FROM lockers WHERE layout_id=?", (lid,)).fetchone()[0]
        y0 = 0 if row is None else row + STEP_Y
        for i in range(n):
            db().execute("INSERT INTO lockers(number,location,layout_id,gx,gy) VALUES(?,?,?,?,?)",
                         (default_name(lid), lay["name"], lid, (i % 10) * STEP_X, y0 + (i // 10) * STEP_Y))
        db().commit()
        return jsonify(ok=True)

    @app.route("/api/lockers/<int:lid>/move", methods=["POST"])
    @login_required()
    def api_move(lid):
        j = request.get_json(force=True) or {}
        locker_or_404(lid)
        x, y = coord(j.get("x")), coord(j.get("y"))
        if x is None or y is None:
            return fail("Invalid position")
        db().execute("UPDATE lockers SET gx=?,gy=? WHERE id=?", (x, y, lid))
        db().commit()
        return jsonify(ok=True)

    @app.route("/api/lockers/<int:lid>/remove", methods=["POST"])
    @login_required()
    def api_remove(lid):
        l = locker_or_404(lid)
        if l["status"] == "assigned":
            return fail("Release the student first")
        d = db()
        d.execute("DELETE FROM payments WHERE assignment_id IN (SELECT id FROM assignments WHERE locker_id=?)", (lid,))
        d.execute("DELETE FROM assignments WHERE locker_id=?", (lid,))
        d.execute("DELETE FROM lockers WHERE id=?", (lid,))
        d.commit()
        return jsonify(ok=True)

    @app.route("/api/lockers/<int:lid>/edit", methods=["POST"])
    @login_required()
    def api_edit(lid):
        j = request.get_json(force=True) or {}
        locker_or_404(lid)
        name = (j.get("number") or "").strip()[:40]
        if not name:
            return fail("Give the locker a name")
        try:
            db().execute("UPDATE lockers SET number=? WHERE id=?", (name, lid))
            db().commit()
        except sqlite3.IntegrityError:
            return fail(f"A locker called “{name}” already exists")
        return jsonify(ok=True)

    @app.route("/api/lockers/<int:lid>/assign", methods=["POST"])
    @login_required()
    def api_assign(lid):
        d = db()
        sid = (request.get_json(force=True) or {}).get("student_id")
        l = locker_or_404(lid)
        if l["status"] != "available":
            return fail("Locker is not available")
        if not d.execute("SELECT 1 FROM students WHERE id=? AND active=1", (sid,)).fetchone():
            return fail("Student not found")
        if d.execute("SELECT 1 FROM assignments WHERE student_id=? AND released_at IS NULL", (sid,)).fetchone():
            return fail("Student already has a locker")
        d.execute("INSERT INTO assignments(locker_id,student_id,assigned_at,assigned_by) VALUES(?,?,?,?)",
                  (lid, sid, now(), session["user"]))
        d.execute("UPDATE lockers SET status='assigned' WHERE id=?", (lid,))
        d.commit()
        return jsonify(ok=True)

    @app.route("/api/lockers/<int:lid>/release", methods=["POST"])
    @login_required()
    def api_release(lid):
        d = db()
        locker_or_404(lid)
        d.execute("UPDATE assignments SET released_at=? WHERE locker_id=? AND released_at IS NULL", (now(), lid))
        d.execute("UPDATE lockers SET status='available' WHERE id=? AND status='assigned'", (lid,))
        d.commit()
        return jsonify(ok=True)

    @app.route("/api/lockers/<int:lid>/maintenance", methods=["POST"])
    @login_required()
    def api_maint(lid):
        j = request.get_json(force=True) or {}
        l = locker_or_404(lid)
        if l["status"] == "assigned":
            return fail("Release the student first")
        new = "maintenance" if j.get("on") else "available"
        db().execute("UPDATE lockers SET status=?, notes=? WHERE id=?",
                     (new, (j.get("notes") or "") if new == "maintenance" else "", lid))
        db().commit()
        return jsonify(ok=True)
