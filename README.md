# Locker Manager – multi-school web app (SaaS)

Schools sign up at `/signup`, get a **school code**, and log in from any browser at `/login`.
Each school has its own private database (`data/school_<code>.db`); a registry tracks all schools.

Features per school: drag-and-drop floor plan builder (mouse and touch), click a locker to assign a
student, proof-of-payment uploads with approve/reject review, lockers list, students (manual/CSV import),
maintenance, history, CSV exports, staff/admin users, year-end reset. Fully responsive (phone bottom-tab nav).
Uploaded proofs live in `data/uploads/<school>/` and are only served to logged-in users of that school.

## Run locally
```
pip install -r requirements.txt
python run.py            # http://localhost:8000  -> click "Create your account"
```

## Host online (so schools can use it)
```
docker build -t lockers .
docker run -d -p 8000:8000 -v lockers-data:/data lockers
```
Put it behind HTTPS (Caddy/nginx/Cloudflare, or a host like Render/Fly.io/Railway with a persistent
disk mounted at `/data`). Back up the `/data` volume. Optional env: `SECRET_KEY`, `PORT`, `DATA_DIR`.

## Business notes
Not yet built: billing/subscriptions, trial limits, email, your own super-admin panel.
