# Linking orders to deliveries — explained simply

> ⚠️ **FarmNex main app: use its guide instead of this one for the integration.**
> The real FarmNex backend differs from what this guide assumes: it has **no vehicles table**, its
> `DATABASE_URL` is **async** (`postgresql+asyncpg://…`, which this sync package can't use — set
> `ROUTES_DATABASE_URL`), its ids are `public_id` UUIDs, and login alone is not enough (any logged-in
> user could mark a load delivered). Follow **`docs/integration/route-optimizer.md` in the
> `farmnex_main` repo** (run `/integrate route-optimizer` there). This file still explains the idea.

## The idea in one picture

Think of FarmNex as a shop with two counters:

- **Counter 1 — the main app.** It knows about people and deals: farmers, buyers, drivers,
  logins, orders, payments, the wallet.
- **Counter 2 — this route component.** It only knows about moving produce: trucks, loads,
  routes, GPS, arrival times.

"Linking orders to deliveries" just means **the two counters pass slips of paper to each other**
so they talk about the same order and the same truck. That's it.

```
 MAIN APP (orders, wallet, drivers)              ROUTE COMPONENT (trucks, routes, GPS)
 ──────────────────────────────────              ─────────────────────────────────────
 Driver registers a truck        ── slip 1 ──►   keeps a copy of the truck (same id)
 Order confirmed, needs a truck  ── slip 2 ──►   creates a "load" for that order id
                                                 plans the route, tracks the truck
 Order status / wallet release   ◄── slip 3 ──   "order X was picked up / delivered"
 Buyer taps "Track my order"     ── slip 4 ──►   returns ETA + live map link
```

The **order id** is the glue. The main app's order id is written on the load, so either side
can always find the other.

Good news: **no existing table changes.** The main app's tables stay exactly as they are.
The component only adds its own `rt_...` tables.

---

## Step 0 — Plug the component into the main backend (one time)

1. In the main backend's `requirements.txt`, add this line (your repo is public, so this works
   on FastAPI Cloud too):

   ```
   farmnex-route-optimizer @ git+https://github.com/atharvpatil1733-art/farmnex_route_optimization.git@main
   ```

2. In the main backend's `main.py` (where `app = FastAPI()` is), add:

   ```python
   from farmnex_routes import router as routes_router
   app.include_router(routes_router, prefix="/routes")
   # The routes have no login of their own. Protect them with the main app's auth:
   # app.include_router(routes_router, prefix="/routes", dependencies=[Depends(your_auth_dependency)])
   ```

3. Make sure the main backend has `DATABASE_URL` set to your Supabase connection string
   (it probably already does). The component uses the same one.

Now every route-component address starts with `/routes/...` on your normal backend URL.

> Whenever you push a change to the component repo, redeploy the main backend so it picks up
> the new version.

---

## Slip 1 — Copy the truck when a driver registers it

**Why:** the route component needs to know the truck's size, type, rate and home base to plan
routes. Registration stays in the main app — we only send a copy.

**Where:** in the main backend, find the code that runs when a driver saves their vehicle
(the "register vehicle" / "update vehicle" endpoint). Right **after** it saves successfully,
add:

```python
from farmnex_routes import session_scope, upsert_vehicle

with session_scope() as s:
    upsert_vehicle(
        s,
        vehicle.id,                          # the id from YOUR vehicles table
        vehicle_number=vehicle.number,
        vehicle_type="mini_truck",           # one of: pickup, tempo, mini_truck, truck
        capacity_kg=vehicle.capacity_kg,
        refrigerated=vehicle.is_refrigerated,
        rate_per_ton_km=vehicle.current_rate,  # Rs per tonne per km (see "Fares" below)
        base_lat=vehicle.lat, base_lng=vehicle.lng, base_label=vehicle.city,
        driver_user_id=driver.id,            # the driver's login id in the main app
        driver_name=driver.name, driver_phone=driver.phone,
    )
```

The names on the right side (`vehicle.number`, `vehicle.current_rate` …) are **placeholders** —
use whatever your main app calls those columns. Call the same code again whenever the driver
edits the vehicle or its rate changes; it updates instead of duplicating.

---

## Slip 2 — Create the delivery when an order needs a truck

**Why:** this is the moment a deal turns into "something needs to be moved".

**When:** at the point in the main app where an order becomes final **and** transport is needed:
- a normal marketplace order is confirmed/paid and the farmer chose platform transport, or
- a Pre-Bidding winner has paid the 20% advance and harvest is ready, or
- a Crop Rescue sale is accepted (send `priority=2` so it's delivered first).

Right after the order is saved as confirmed, add:

```python
from farmnex_routes import create_delivery_for_order, session_scope

with session_scope() as s:
    load, created_now = create_delivery_for_order(
        s,
        order_id=str(order.id),
        farmer_id=str(farmer.id), farmer_name=farmer.name, farmer_phone=farmer.phone,
        buyer_id=str(buyer.id),   buyer_name=buyer.name,   buyer_phone=buyer.phone,
        crop=order.crop_name,
        weight_kg=order.quantity_kg,
        pickup_lat=farmer.lat, pickup_lng=farmer.lng, pickup_address=farmer.address,
        drop_lat=buyer.lat,    drop_lng=buyer.lng,    drop_address=buyer.address,
        priority=2 if order.is_crop_rescue else 0,   # 0 normal, 1 urgent, 2 Crop Rescue
        needs_cold=False,
    )
```

What happens automatically after this:
- Free trucks within 25 km get a "New load near you" notification.
- If the same order is sent twice by mistake, the existing delivery is returned — never two.

**If the order is cancelled** before a truck is assigned:

```python
from farmnex_routes import cancel_delivery_for_order, session_scope
with session_scope() as s:
    cancel_delivery_for_order(s, str(order.id))
```

**Farmer delivering it themselves?** Sync the farmer's own vehicle with Slip 1 using
`owner_role="farmer"`, then plan a trip for that vehicle with just this load
(`POST /routes/trips/plan` with `vehicle_id` and `load_ids`). Tracking works the same.

---

## Slip 3 — Hear back when the load moves (and release the payment)

**Why:** the main app must know when produce is picked up and delivered — e.g. to mark the
order "Delivered" and, for Pre-Bidding, **release the wallet money to the farmer only after
the buyer receives the produce**.

**How:** you give the component a small function once, at startup. The component calls it
by itself every time a delivery changes. Put this in the main backend (e.g. in `main.py`
right after `include_router`):

```python
from farmnex_routes import on_delivery_update

@on_delivery_update
def when_delivery_changes(load, status):
    # status is one of: "assigned", "picked_up", "delivered", "cancelled", "pending"
    if not load.order_id:
        return
    order_id = load.order_id

    if status == "assigned":
        pass  # e.g. notify farmer + buyer: "Truck assigned"
    elif status == "picked_up":
        pass  # e.g. notify buyer: "Your produce is on the way"
    elif status == "delivered":
        pass  # e.g. mark order delivered, then call your EXISTING wallet-release function
              #      release_payment_to_farmer(order_id)
```

Replace the `pass` lines with calls to functions your main app already has (notifications,
wallet release). If one of your functions crashes, the delivery still goes through — the error
is only written to the log.

---

## Slip 4 — "Where is my order?" for buyers and farmers

The Flutter order screen only needs the **order id**. It calls:

```
GET  https://<your-backend>/routes/orders/<order_id>/delivery
```

and gets back something like:

```json
{
  "status": "picked_up",
  "estimated_fare": 313,
  "pickup":   { "eta_min": null, "status": "done" },
  "delivery": { "eta_min": 42.5, "eta_at": "2026-10-02T05:50:00+00:00" },
  "vehicle":  { "vehicle_number": "MH14AB1234", "driver_name": "Ramesh", "driver_phone": "98…" },
  "tracking_url": "https://<your-backend>/routes/track/<trip>/view?load=<load>"
}
```

Show the status and ETA on the order card, and a **Track** button that opens `tracking_url`
in a WebView — that page is the live map with the moving truck.

If it answers **404**, the order has no delivery yet (not confirmed, or self-pickup).

---

## What the driver screens (already in your Flutter app) call

The driver is logged in through the main app, and you know their vehicle id.

| Screen action | Call |
|---|---|
| Find today's trip | `GET /routes/vehicles/{vehicle_id}/current-trip` |
| No trip yet? Get one planned (auto-pools nearby farmers) | `POST /routes/trips/plan` with `{"vehicle_id": "..."}` |
| Start driving | `POST /routes/trips/{trip_id}/start` |
| Send location every ~10 s while the app is open | `POST /routes/vehicles/{vehicle_id}/location` with `{"lat":..,"lng":..}` |
| "Picked up" / "Delivered" button | `POST /routes/trips/{trip_id}/stops/{stop_id}/complete` |
| Return-load alerts | `GET /routes/vehicles/{vehicle_id}/notifications?unread_only=true` |
| Accept a return load | `POST /routes/vehicles/{vehicle_id}/accept-load/{load_id}` |

When the last "Delivered" is tapped, the answer includes `backhaul_options` — the return loads
near the truck. Show them straight away.

---

## Fares

`fare for one farmer = road km (farm → buyer) × tonnes carried × vehicle's current rate`

- The vehicle's current rate (`rate_per_ton_km`) comes from the main app via Slip 1, so when
  truck rates change you update them in the main app only and re-send the vehicle.
- When one truck carries several farmers' loads, each farmer pays only for their own weight
  and distance. The trip total is the sum of all shares.
- A minimum fare (default Rs 300) applies. Defaults for vehicles synced without a rate are in
  `.env.example`.

The fare appears as `estimated_fare` on the order's delivery (Slip 4) once a truck is assigned.
The main app decides how and when it's actually charged.

---

## Checklist

- [ ] Step 0: requirement line + two lines in `main.py`
- [ ] Slip 1: `upsert_vehicle` after vehicle register/edit
- [ ] Slip 2: `create_delivery_for_order` when an order is confirmed and needs transport
- [ ] Slip 2b: `cancel_delivery_for_order` when an order is cancelled
- [ ] Slip 3: `@on_delivery_update` function → order status + wallet release
- [ ] Slip 4: order screen calls `/routes/orders/{id}/delivery`, Track button opens `tracking_url`
- [ ] Driver screens point at the calls in the table above

`docs/CLAUDE_CODE_PROMPT.md` has a ready prompt to paste into Claude Code in the main backend
repo so it can do these steps for you against your real code.
