# FarmNex Route Optimizer

Plug-in component for **FarmNex (SIH 2026)** that handles everything after a farmer and a
wholesaler agree on a deal:

| Feature | What it does |
|---|---|
| **Vehicle registration** | Driver (or a farmer doing self-delivery) registers vehicle number, type, capacity, refrigeration, home base |
| **Load pooling** | Picks pending loads near a truck that go to the same market, fills it without exceeding capacity |
| **Route optimization** | Finds the best order of pickups (farmers) and drops (wholesalers): collect-before-deliver, never over capacity, Crop Rescue loads delivered first. Exact search up to 5 loads, heuristic beyond |
| **Travel time & cost** | Real road km and time from the free OSRM engine (OpenStreetMap), truck-speed adjusted, plus loading time per stop and an estimated fare |
| **Return-trip (backhaul)** | When the truck delivers and is empty, it gets notified of loads near it, ranked by *empty km saved* |
| **New-load alerts** | When a farmer posts a load, free trucks within 25 km get a notification |
| **Live GPS tracking** | Driver app sends GPS pings; farmer, buyer and judges see a live map with ETAs |

It is a normal FastAPI `APIRouter`, so it mounts into the main FarmNex backend with two lines,
uses the same Supabase database, and only **adds** its own `rt_*` tables.

---

## Folder layout

```
farmnex_route_optimizer/
├── farmnex_routes/            <- the package (this is what the main app imports)
│   ├── __init__.py            exports router, init_db, get_session
│   ├── router.py              all API endpoints
│   ├── services.py            trip planning, stop completion, GPS, ETAs
│   ├── optimizer.py           pickup-and-delivery optimization (pure Python)
│   ├── matching.py            load pooling, backhaul matching, notifications
│   ├── geo.py                 OSRM road distances with offline fallback
│   ├── models.py              rt_* tables (SQLAlchemy)
│   ├── schemas.py             request/response models
│   ├── db.py / config.py      DB connection and settings from env vars
│   └── static/track.html      live tracking map page (Leaflet + OpenStreetMap)
├── sql/001_create_route_tables.sql   run once in Supabase SQL editor
├── demo/
│   ├── run_demo_server.py     run this component alone on port 8001
│   ├── seed_demo.py           Pune/Mumbai demo data + two planned trips
│   └── simulate_driver.py     fake driver phone: moves the truck, completes stops
├── tests/                     pytest (runs offline)
├── docs/INTEGRATION.md        how to plug into the main backend + Flutter
└── .github/workflows/tests.yml  GitHub runs the tests on every push
```

---

## Run it locally (5 minutes)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp .env.example .env            # leave DATABASE_URL empty -> local SQLite
python demo/run_demo_server.py  # API docs at http://localhost:8001/docs
```

In a second terminal:

```bash
python demo/seed_demo.py                          # prints two tracking links
python demo/simulate_driver.py <trip_id_truck_2>  # watch the truck move, ends with a return-trip offer
```

Open the printed `.../view` link in a browser (or on a phone on the same Wi-Fi using your
laptop's IP instead of `localhost`).

Run tests: `pytest -q`

---

## API (all under the prefix you mount, e.g. `/routes`)

| Method | Path | Used by |
|---|---|---|
| POST | `/vehicles` | Driver: register vehicle |
| GET | `/vehicles` , `/vehicles/{id}` | Admin / app |
| PATCH | `/vehicles/{id}/status` | Driver: go online (`available`) / `offline` |
| POST | `/vehicles/{id}/location` | Driver app: GPS ping every 10 s |
| GET | `/vehicles/{id}/current-trip` | Driver app: today's route |
| GET | `/vehicles/{id}/notifications` | Driver app: new-load and return-trip alerts |
| GET | `/vehicles/{id}/backhaul` | Driver app: return loads near me right now |
| POST | `/vehicles/{id}/accept-load/{load_id}` | Driver: accept a return / nearby load |
| POST | `/notifications/{id}/read` | Driver app |
| POST | `/loads` | Main app, when an order needs transport |
| GET | `/loads?status=&farmer_id=&buyer_id=` , `/loads/{id}` | App |
| POST | `/loads/{id}/cancel` | App |
| GET | `/loads/{id}/track` | **Farmer / buyer: my load's pickup & delivery ETA** |
| POST | `/trips/plan` | Driver / dispatcher: `{vehicle_id}` auto-pools, or pass `load_ids` |
| GET | `/trips/{id}` | App |
| POST | `/trips/{id}/start` , `/trips/{id}/cancel` | Driver |
| POST | `/trips/{id}/stops/{stop_id}/complete` | Driver: picked up / delivered |
| GET | `/track/{trip_id}` | Live JSON (poll every 5-10 s) |
| GET | `/track/{trip_id}/view` | Live map web page (open in WebView or browser) |

Full request/response schemas: `http://localhost:8001/docs`.

---

## How the optimization works (for judges)

1. **Pooling** - pending loads within 30 km of the truck, same market direction (drops within
   40 km of each other), highest priority first, until the truck is full (max 4 loads).
2. **Routing matrix** - one OSRM call gives road distance/time between every pair of points;
   times x1.25 because trucks are slower than cars. No internet -> straight-line x1.35 estimate.
3. **Best order** - branch-and-bound search over every valid order (pickup before drop,
   capacity never exceeded). Cost = driving time + 15 min per stop + a penalty on late
   delivery of urgent loads, so Crop Rescue produce reaches the buyer first.
4. **Backhaul** - after the last drop, for each nearby pending load:
   `empty km saved = (empty drive home) - (drive to pickup + drive from drop to home)`.
   The best ones are pushed to the driver as a notification.
5. **ETA** - live: GPS position -> next stop by road, then planned legs + loading time.

---

## Git workflow (so GitHub always has your latest changes)

GitHub does not watch your folder by itself - you save changes with a commit and send them
with a push. One-time setup:

```bash
# on github.com: New repository -> name: farmnex_route_optimizer -> no README -> Create
cd farmnex_route_optimizer
git init
git add .
git commit -m "Route optimizer component"
git branch -M main
git remote add origin https://github.com/atharvpatil1733-art/farmnex_route_optimizer.git
git push -u origin main
```

Every time after you change something:

```bash
git add .
git commit -m "what you changed"
git push
```

(VS Code's Source Control tab or GitHub Desktop do the same with buttons.) Teammates run
`git pull` to get your changes. The green tick on GitHub means the tests passed.
