# CLAUDE.md — FarmNex route optimizer

Guidance for Claude Code sessions working in this repo.

## What this is
A plug-in component (Python package `farmnex_routes`) for the FarmNex SIH 2026 app. It is
mounted into the main FastAPI backend as a router (standalone demo: `prefix="/routes"`; in FarmNex the
host mounts it at `/api/v2/routes` from its `backend/app/modules/routes_host.py`)
and shares the main Supabase Postgres database.

It does: route optimization for pickups (farmers) and drops (wholesalers), load pooling,
return-trip (backhaul) matching, new-load notifications, GPS tracking with ETAs, per-load fares.

It does NOT do (the main app already has these): logins, driver accounts, vehicle registration,
orders, payments/wallet, Flutter screens.

## FarmNex host facts (checked 2026-09-29 against farmnex_main)
The main backend is **async** (`postgresql+asyncpg://` in `DATABASE_URL`), so the host always sets
`ROUTES_DATABASE_URL`; the host installs this package pinned to a commit, mounts it at
`/api/v2/routes` behind an allow-list + ownership guard, and passes `str(user.public_id)` for every
user id. It has no vehicles table — `rt_vehicles` is the source of truth, filled by host endpoints
via `upsert_vehicle`. Keep route paths and helper signatures stable (the host's guard matches on
path parameter names like `{vehicle_id}`, `{trip_id}`, `{load_id}`, `{order_id}`,
`{notification_id}`); if you add or rename a route, note it in the README so the host can add a
guard rule. The host adds this package to its `backend/pyproject.toml` (what FastAPI Cloud installs)
and `requirements.txt`, pinned to a commit. Full host guide: `docs/integration/route-optimizer.md` in farmnex_main.

## Hard rules
- Only create tables prefixed `rt_`. Never alter, drop or query main-app tables.
  Keep `farmnex_routes/models.py` and `sql/001_create_route_tables.sql` in sync.
- Links to the main app are id strings only: `rt_vehicles.id` = main-app vehicle id;
  `order_id`, `farmer_id`, `buyer_id`, `driver_user_id` are main-app ids.
- Every status change on a load must call `hooks.emit(load, status)` after commit, so the main
  app's `@on_delivery_update` listeners fire.
- `optimizer.py` stays pure Python (no DB, no HTTP) so it can be unit tested.
- Tests must run offline: they set `ROUTING_PROVIDER=haversine` and a SQLite DB (tests/conftest.py).

## Layout
- `router.py` endpoints · `services.py` trip/stop/GPS/ETA logic · `hooks.py` main-app bridge
- `optimizer.py` pickup-and-delivery search · `matching.py` pooling + backhaul + notifications
- `fares.py` fare = road km × tonnes × vehicle rate · `geo.py` OSRM with fallback
- `models.py` tables · `schemas.py` request/response · `config.py` env settings
- `demo/` standalone server, seed data, driver simulator · `docs/` guides

## Commands
```bash
pip install -r requirements.txt && pip install -e .
pytest -q
python demo/run_demo_server.py      # http://localhost:8001/docs
python demo/seed_demo.py
python demo/simulate_driver.py <trip_id>
```
