import io, os, re, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
from app import app

tok = lambda r: re.search(r'name="_csrf" value="(\w+)"', r.get_data(as_text=True)).group(1)


def school(slug, name):
    c = app.test_client()
    t = tok(c.get("/signup"))
    r = c.post("/signup", data={"_csrf": t, "name": name, "school": slug, "username": "boss", "password": "password1"})
    print(slug, "signup", r.status_code)
    t = tok(c.get("/lockers"))
    c.post("/lockers/add", data={"_csrf": t, "prefix": "A-", "start": 1, "end": 5, "location": "Block A"})
    d = io.BytesIO(b"student_no,name,grade,email\n1,Ann,9A,a@x.com\n2,Bob,9B,\n")
    c.post("/students/import", data={"_csrf": t, "file": (d, "s.csv")})
    c.post("/lockers/1/assign", data={"_csrf": t, "student_id": 1})
    for p in ["/", "/lockers", "/students", "/history", "/settings", "/export/lockers.csv"]:
        assert c.get(p).status_code == 200, p
    return c


a = school("alpha-school", "Alpha")
b = school("beta-school", "Beta")
# isolation: Beta's dashboard shows its own name, and logging in to alpha with beta creds fails
c = app.test_client()
t = tok(c.get("/login"))
r = c.post("/login", data={"_csrf": t, "school": "alpha-school", "username": "boss", "password": "wrong"})
print("bad login stays:", r.status_code)
r = c.post("/login", data={"_csrf": t, "school": "alpha-school", "username": "boss", "password": "password1"})
print("good login:", r.status_code)
print("Alpha name shown:", b"Alpha" in c.get("/").data, "| Beta leak:", b"Beta" in c.get("/lockers").data)
print("dup slug:", b"taken" in app.test_client().post("/signup", data={"_csrf": tok(app.test_client().get("/signup")), "name": "x"}).data)
print("anon redirect:", app.test_client().get("/lockers").status_code)
print("path traversal:", app.test_client().post("/login", data={"_csrf": "x", "school": "../x"}).status_code)
