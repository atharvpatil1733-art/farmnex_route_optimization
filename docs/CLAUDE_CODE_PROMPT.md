# Prompt for Claude Code — integrate the route component into the main backend

> ⚠️ **FarmNex main app: use its guide instead of this one for the integration.**
> The real FarmNex backend differs from what this guide assumes: it has **no vehicles table**, its
> `DATABASE_URL` is **async** (`postgresql+asyncpg://…`, which this sync package can't use — set
> `ROUTES_DATABASE_URL`), its ids are `public_id` UUIDs, and login alone is not enough (any logged-in
> user could mark a load delivered). Follow **`docs/integration/route-optimizer.md` in the
> `farmnex_main` repo** (run `/integrate route-optimizer` there). This file still explains the idea.

Open Claude Code **in the main FarmNex backend repo** and paste everything below the line.

---

I want to integrate my route-optimization component into this FastAPI backend.
The component is a Python package in my public repo
https://github.com/atharvpatil1733-art/farmnex_route_optimization (package name `farmnex_routes`).
Read its `docs/LINKING_ORDERS.md` and `CLAUDE.md` first.

Rules:
- Do NOT delete, rename or alter any existing table, column or Supabase setup. The component
  creates its own `rt_*` tables; nothing else in the database changes.
- Do not change existing API behaviour for the Flutter app, only add to it.
- Show me the plan and the exact files you will touch before editing.

Tasks:
1. Add `farmnex-route-optimizer @ git+https://github.com/atharvpatil1733-art/farmnex_route_optimization.git@main`
   to requirements and mount it: `app.include_router(routes_router, prefix="/routes")`.
2. Find where a driver registers or edits a vehicle. After it saves, call
   `upsert_vehicle(...)` with the main app's vehicle id, number, type (map ours to one of
   pickup / tempo / mini_truck / truck), capacity in kg, refrigerated flag, current rate in
   Rs per tonne per km, home location, and the driver's user id, name and phone.
3. Find where an order is confirmed and needs platform transport (marketplace order, Pre-Bidding
   win after the advance is paid, Crop Rescue sale). Call `create_delivery_for_order(...)` there
   with the order id, farmer and buyer details and their coordinates. Crop Rescue orders use
   `priority=2`. Where an order is cancelled, call `cancel_delivery_for_order(...)`.
4. Register an `@on_delivery_update` listener that, using functions this backend ALREADY has:
   notifies farmer and buyer on "assigned" and "picked_up", and on "delivered" marks the order
   delivered and releases the wallet payment to the farmer (Pre-Bidding rule: farmer is paid only
   after the buyer receives the produce).
5. Tell me which Flutter screens should call which `/routes/...` endpoints (list in
   LINKING_ORDERS.md) — do not edit the Flutter app unless I ask.
6. Explain every change in simple words; I am not very technical.

If you can't find where something happens (e.g. vehicle registration or order confirmation),
ask me instead of guessing.
