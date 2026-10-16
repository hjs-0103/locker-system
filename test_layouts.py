import io, os, re, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
from app import app

c = app.test_client()
tok = lambda r: re.search(r'name="_csrf" value="(\w+)"', r.get_data(as_text=True)).group(1)
c.post("/signup", data={"_csrf": tok(c.get("/signup")), "name": "S", "school": "test-school", "username": "boss", "password": "password1"})
t = tok(c.get("/layouts"))
H = {"X-CSRF": t}
P = lambda u, b=None: c.post(u, json=b or {}, headers=H).get_json()

r = c.post("/layouts/create", data={"_csrf": t, "name": "Main hallway"})
print("create", r.status_code, r.headers["Location"])
print("rename plan", P("/api/layouts/1/rename", {"name": "Gym corridor"}))
print("drop locker", P("/api/layouts/1/add", {"x": 100, "y": 50}))
print("named locker", P("/api/layouts/1/add", {"x": 200, "y": 50, "name": "Gym 1"}))
print("dup name", P("/api/layouts/1/add", {"x": 300, "y": 50, "name": "Gym 1"}))
print("add many", P("/api/layouts/1/addmany", {"count": 12}))
print("too many", P("/api/layouts/1/addmany", {"count": 999}))
d = c.get("/api/layouts/1").get_json()
print(d["layout"]["name"], len(d["lockers"]), [l["number"] for l in d["lockers"]][:4])
print("move", P("/api/lockers/1/move", {"x": 400, "y": 300}), [(l["x"], l["y"]) for l in c.get("/api/layouts/1").get_json()["lockers"] if l["id"] == 1])
print("rename locker", P("/api/lockers/1/edit", {"number": "Library A"}))
print("rename clash", P("/api/lockers/1/edit", {"number": "Gym 1"}))
c.post("/students/add", data={"_csrf": t, "student_no": "7", "name": "Zed", "grade": "9"})
print("assign", P("/api/lockers/1/assign", {"student_id": 1}))
print("remove assigned", P("/api/lockers/1/remove"))
print("release", P("/api/lockers/1/release"))
print("remove", P("/api/lockers/1/remove"))
print("pages", [c.get(p).status_code for p in ["/layouts", "/layouts/1", "/lockers", "/"]])

# payments
png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
P2 = lambda u, **kw: c.post(u, headers=H, **kw).get_json()
P("/api/lockers/2/assign", {"student_id": 1})
aid = [l for l in c.get("/api/layouts/1").get_json()["lockers"] if l["id"] == 2][0]["assignment_id"]
print("bad ext", P2(f"/api/assignments/{aid}/proof", data={"file": (io.BytesIO(b"x"), "a.exe")}, content_type="multipart/form-data"))
print("fake png", P2(f"/api/assignments/{aid}/proof", data={"file": (io.BytesIO(b"nope"), "a.png")}, content_type="multipart/form-data"))
print("good", P2(f"/api/assignments/{aid}/proof", data={"file": (io.BytesIO(png), "p.png"), "amount": "150"}, content_type="multipart/form-data"))
l2 = [l for l in c.get("/api/layouts/1").get_json()["lockers"] if l["id"] == 2][0]
print("status", l2["pay_status"], "| view", c.get(f"/proof/{l2['pay_id']}").status_code, "| approve", P(f"/api/payments/{l2['pay_id']}/review", {"status": "approved"}))
print("payments page", c.get("/payments").status_code, "| anon proof", app.test_client().get(f"/proof/{l2['pay_id']}").status_code)
print("no csrf", c.post("/api/layouts/1/add", json={}).status_code)

# school logo upload & removal
r = c.post("/settings", data={"_csrf": t, "action": "school", "school_name": "Oakridge High", "school_term": "2026 Term 1", "school_color": "navy", "logo": (io.BytesIO(png), "crest.png")}, content_type="multipart/form-data", follow_redirects=True)
assert b"School profile updated" in r.data and b"school-logo" in r.data
rl = c.get("/school-logo")
assert rl.status_code == 200 and rl.data.startswith(b"\x89PNG")
rl.close()
r2 = c.post("/settings", data={"_csrf": t, "action": "remove_logo"}, follow_redirects=True)
assert b"Logo removed" in r2.data
print("logo test: OK")
