# Technical integration notes

For the step-by-step, plain-language guide, read **[LINKING_ORDERS.md](LINKING_ORDERS.md)** first.
This page only has the extra technical details.

## Mounting

```python
# requirements.txt of the main backend (repo is public, so FastAPI Cloud can install it)
farmnex-route-optimizer @ git+https://github.com/atharvpatil1733-art/farmnex_route_optimization.git@main
```

```python
from farmnex_routes import router as routes_router
app.include_router(routes_router, prefix="/routes")
```

**Security:** the router has no authentication of its own (the main app owns logins). Anyone who can
reach `/routes` can see driver phones and live locations and mark stops delivered, which can release
payments. Mount it behind the main app's auth:
`app.include_router(routes_router, prefix="/routes", dependencies=[Depends(your_auth_dependency)])`.
The live-map page (`/track/{trip_id}/view`) and its JSON are opened from a WebView, so allow them
through your auth, or pass the token in the WebView headers.

**HTTPS links:** behind a proxy that terminates TLS, run uvicorn with
`--proxy-headers --forwarded-allow-ips="*"`, or set `ROUTES_PUBLIC_BASE_URL=https://your-backend`
so `tracking_url` is always `https://...` (Android WebViews block plain `http`).

## Database

- Uses `DATABASE_URL` (same Supabase string as the main backend). Set `ROUTES_DATABASE_URL`
  to point the component at a separate test database instead.
- `rt_*` tables are created on first use (`CREATE TABLE IF NOT EXISTS`). To create them by hand,
  run `sql/001_create_route_tables.sql` in the Supabase SQL editor and set
  `ROUTES_AUTO_CREATE_TABLES=false`.
- Existing FarmNex tables are never read, altered or dropped. The only link to them is by id
  values stored in text columns (`order_id`, `farmer_id`, `buyer_id`, `driver_user_id`, and
  `rt_vehicles.id` = main app vehicle id).

## What the component owns vs. the main app

| Main app (already built) | This component |
|---|---|
| Logins, driver accounts, vehicle registration | Copy of each vehicle (`upsert_vehicle` / `PUT /routes/vehicles/{id}`) |
| Orders, checkout, Pre-Bidding, Crop Rescue sales | One delivery ("load") per order that needs transport |
| Wallet, payment release, notifications to users | Calls your `@on_delivery_update` function on every status change |
| Flutter screens (driver, buyer, farmer) | JSON endpoints + a live map page (`tracking_url`) to open in a WebView |
| Vehicle rates | Uses the rate you send to compute each farmer's fare |

## Python helpers (call from main backend code, no HTTP needed)

```python
from farmnex_routes import (
    session_scope,               # with session_scope() as s: ...
    upsert_vehicle,              # upsert_vehicle(s, vehicle_id, **fields)
    create_delivery_for_order,   # -> (load, created_now); idempotent per order_id
    cancel_delivery_for_order,   # only while status is "pending"
    delivery_for_order,          # dict: status, fare, pickup/delivery ETA, vehicle
    on_delivery_update,          # decorator: fn(load, status)
)
```

Delivery statuses: `pending` → `assigned` → `picked_up` → `delivered`, or `cancelled`.
If a trip is cancelled, its loads go back to `pending` (your listener gets `"pending"`).

Listener notes: it runs inside the request that changed the status, after the change is
saved. Keep it quick. Exceptions are logged, never raised.

## Same thing over HTTP (if another service needs it)

| Method | Path |
|---|---|
| PUT | `/routes/vehicles/{main_app_vehicle_id}` |
| POST | `/routes/loads` (with `order_id`) |
| GET | `/routes/orders/{order_id}/delivery` |
| POST | `/routes/orders/{order_id}/cancel-delivery` |

## Demo day

- Phones must reach the backend (FastAPI Cloud URL, not `localhost`).
- Seed once: `python demo/seed_demo.py --base-url https://<backend>/routes`.
- No one driving? Run `python demo/simulate_driver.py <trip_id> --base-url ...` on a laptop.
- The public OSRM routing server is free but shared; if it's slow the component falls back to
  estimates automatically (the map shows "Estimated").
