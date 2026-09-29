# FarmNex Route Optimizer

Plug-in component for **FarmNex (SIH 2026)** that handles moving the produce after a farmer and a
wholesaler agree on a deal. Logins, driver accounts, vehicle registration, orders, wallet and all
Flutter screens stay in the main app — this package only adds the routing brain.

> **Start here:** [docs/LINKING_ORDERS.md](docs/LINKING_ORDERS.md) explains, in simple words, how
> the main app and this component connect. [docs/CLAUDE_CODE_PROMPT.md](docs/CLAUDE_CODE_PROMPT.md)
> is a ready prompt to let Claude Code do that integration in the main backend.

| Feature | What it does |
|---|---|
| **Vehicle copy** | The main app sends a copy of each registered vehicle (same id, capacity, current rate, home base) |
| **Order → delivery** | One delivery per main-app order, linked by `order_id`; the main app is told when it's picked up / delivered |
| **Load pooling** | Picks pending loads near a truck that go to the same market, fills it without exceeding capacity |
| **Route optimization** | Finds the best order of pickups (farmers) and drops (wholesalers): collect-before-deliver, never over capacity, Crop Rescue loads delivered first. Exact search up to 5 loads, heuristic beyond |
| **Travel time** | Real road km and time from the free OSRM engine (OpenStreetMap), truck-speed adjusted, plus loading time per stop |
| **Fares** | Each farmer's share = road km × tonnes carried × the vehicle's current rate (sent by the main app) |
| **Return-trip (backhaul)** | When the truck delivers and is empty, it gets notified of loads near it, ranked by *empty km saved* |
| **New-load alerts** | When a farmer posts a load, free trucks within 25 km get a notification |
| **Live GPS tracking** | Driver app sends GPS while open; farmer, buyer and judges see a live map with ETAs |

It is a normal FastAPI `APIRouter`, so it mounts into the main FarmNex backend with two lines,
uses the same Supabase database, and only **adds** its own `rt_*` tables.

---

## Folder layout

```
farmnex_route_optimization/
├── farmnex_routes/            <- the package (this is what the main app imports)
│   ├── __init__.py            exports router + helper functions for the main backend
│   ├── router.py              all API endpoints
│   ├── hooks.py               bridge to the main app (vehicle copy, order -> delivery, status events)
│   ├── fares.py               fare per load
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
├── docs/LINKING_ORDERS.md     plain-language guide: connecting to the main app
├── docs/CLAUDE_CODE_PROMPT.md paste into Claude Code in the main backend repo
├── docs/INTEGRATION.md        extra technical details
├── CLAUDE.md                  rules for Claude Code sessions in this repo
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
| PUT | `/vehicles/{main_app_vehicle_id}` | Main backend: copy vehicle after register / edit / rate change |
| GET | `/vehicles?driver_user_id=` , `/vehicles/{id}` | App |
| PATCH | `/vehicles/{id}/status` | Driver: go online (`available`) / `offline` |
| POST | `/vehicles/{id}/location` | Driver app: GPS ping every ~10 s while open |
| GET | `/vehicles/{id}/current-trip` | Driver app: today's route |
| GET | `/vehicles/{id}/notifications` | Driver app: new-load and return-trip alerts |
| GET | `/vehicles/{id}/backhaul` | Driver app: return loads near me right now |
| POST | `/vehicles/{id}/accept-load/{load_id}` | Driver: accept a return / nearby load |
| POST | `/notifications/{id}/read` | Driver app |
| POST | `/loads` | Main backend: create delivery for an order (same order twice = same delivery) |
| GET | `/loads?status=&farmer_id=&buyer_id=` , `/loads/{id}` | App |
| POST | `/loads/{id}/cancel` | App |
| GET | `/loads/{id}/track` | Tracking by load id |
| GET | `/orders/{order_id}/delivery` | **Buyer / farmer: status, fare, ETAs, map link for an order** |
| POST | `/orders/{order_id}/cancel-delivery` | Main backend, when an order is cancelled |
| POST | `/trips/plan` | Driver: `{vehicle_id}` auto-pools, or pass `load_ids` |
| GET | `/trips/{id}` | App |
| POST | `/trips/{id}/start` , `/trips/{id}/cancel` | Driver |
| POST | `/trips/{id}/stops/{stop_id}/complete` | Driver: picked up / delivered |
| GET | `/track/{trip_id}` | Live JSON (poll every 5-10 s) |
| GET | `/track/{trip_id}/view` | Live map web page (open in a WebView) |

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
6. **Fare** - each farmer pays road km (farm -> buyer) x tonnes x the vehicle's current rate,
   so sharing a truck is cheaper for small farmers.

---

## Git workflow (so GitHub always has your latest changes)

GitHub does not watch your folder by itself - you save changes with a commit and send them
with a push. One-time setup:

```bash
# on github.com: New repository -> name: farmnex_route_optimization -> no README -> Create
cd farmnex_route_optimization
git init
git add .
git commit -m "Route optimizer component"
git branch -M main
git remote add origin https://github.com/atharvpatil1733-art/farmnex_route_optimization.git
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
