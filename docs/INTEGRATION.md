# Plugging the route optimizer into FarmNex

## 1. Main FastAPI backend

**Option A - install from GitHub (recommended).** Add to the main backend's `requirements.txt`:

```
farmnex-route-optimizer @ git+https://github.com/atharvpatil1733-art/farmnex_route_optimizer.git@main
```

(If the repo is private, FastAPI Cloud can't install it this way - either make it public or use Option B.)

**Option B - copy the folder.** Copy `farmnex_routes/` into the main backend next to `main.py`
and add `sqlalchemy`, `httpx`, `psycopg[binary]` to its requirements.

Then in the main app:

```python
from farmnex_routes import router as routes_router

app.include_router(routes_router, prefix="/routes")
```

That's it. The component reads `DATABASE_URL` (the same Supabase string the main backend
uses). To use a separate DB for testing, set `ROUTES_DATABASE_URL` instead.

### Database
Tables are created automatically on first request (`ROUTES_AUTO_CREATE_TABLES=true`), using
`CREATE TABLE IF NOT EXISTS` for `rt_*` tables only. If you prefer to do it by hand, run
`sql/001_create_route_tables.sql` in the Supabase SQL editor and set the flag to `false`.
Nothing existing is altered or dropped.

If the main backend already has its own SQLAlchemy session dependency, you can make this
component use it:

```python
from farmnex_routes import get_session as routes_get_session
app.dependency_overrides[routes_get_session] = main_app_get_db
```

(With this override, also call `farmnex_routes.init_db()` once at startup, or run the SQL file.)

### Hook: create a load when an order needs transport
When checkout/pre-bidding confirms an order and the farmer picks "platform transport":

```python
import httpx  # or call the function directly - both work

httpx.post(f"{BASE}/routes/loads", json={
    "order_id": order.id,
    "farmer_id": farmer.id, "farmer_name": farmer.name, "farmer_phone": farmer.phone,
    "buyer_id": buyer.id,   "buyer_name": buyer.name,   "buyer_phone": buyer.phone,
    "crop": "Tomato", "weight_kg": 800,
    "priority": 2 if order.from_crop_rescue else 0,
    "needs_cold": False,
    "pickup_lat": farm.lat, "pickup_lng": farm.lng, "pickup_address": farm.address,
    "drop_lat": buyer.lat,  "drop_lng": buyer.lng,  "drop_address": buyer.address,
})
```

Farmer self-delivery: register the farmer's own vehicle with `"owner_role": "farmer"`, then
`POST /routes/trips/plan {"vehicle_id": ..., "load_ids": [load_id]}` - GPS tracking works the same.

## 2. Flutter app

Packages: `http`, `geolocator` (driver GPS), `webview_flutter` (tracking map) or
`flutter_map` + `latlong2` if you want a native map.

### Driver: register vehicle

```dart
final r = await http.post(Uri.parse('$api/routes/vehicles'),
  headers: {'Content-Type': 'application/json'},
  body: jsonEncode({
    'driver_name': name, 'driver_phone': phone, 'vehicle_number': number,
    'vehicle_type': 'mini_truck', 'capacity_kg': 2500, 'refrigerated': false,
    'base_lat': pos.latitude, 'base_lng': pos.longitude, 'base_label': 'Chakan',
  }));
final vehicleId = jsonDecode(r.body)['id'];   // save locally
```

### Driver: send GPS every 10 seconds while a trip is active

```dart
Timer? gpsTimer;
void startGps(String vehicleId) {
  gpsTimer = Timer.periodic(const Duration(seconds: 10), (_) async {
    final p = await Geolocator.getCurrentPosition();
    await http.post(Uri.parse('$api/routes/vehicles/$vehicleId/location'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'lat': p.latitude, 'lng': p.longitude,
                        'speed_kmph': p.speed * 3.6, 'heading': p.heading}));
  });
}
void stopGps() => gpsTimer?.cancel();
```

Android: add `ACCESS_FINE_LOCATION` to `AndroidManifest.xml` and ask permission with
`Geolocator.requestPermission()`. For the demo the app must stay open (background location is
out of scope for the prototype).

### Driver: route screen
`GET /routes/vehicles/{id}/current-trip` -> list `stops` (seq, label, `planned_arrival_min`)
with a "Done" button calling `POST /routes/trips/{trip_id}/stops/{stop_id}/complete`.
If the response has `trip_status == "completed"` and `backhaul_options` is not empty, show a
"Return load available" sheet with an Accept button ->
`POST /routes/vehicles/{id}/accept-load/{load_id}`.

### Driver: notifications
Poll `GET /routes/vehicles/{id}/notifications?unread_only=true` every 30 s and show a banner.

### Farmer / buyer: track my load
`GET /routes/loads/{load_id}/track` returns `pickup.eta_min`, `delivery.eta_min` and a
`tracking_url`. The simplest screen is a WebView:

```dart
WebViewWidget(controller: WebViewController()
  ..setJavaScriptMode(JavaScriptMode.unrestricted)
  ..loadRequest(Uri.parse(trackingUrl)));
```

## 3. Demo-day checklist
- Backend must be reachable from phones (FastAPI Cloud URL, not `localhost`).
- Seed data once: `python demo/seed_demo.py --base-url https://<backend>/routes`.
- If no one is actually driving, run `demo/simulate_driver.py <trip_id> --base-url ...`
  on a laptop so judges see the truck move.
- Public OSRM is free but shared; if it's slow the app automatically falls back to estimates
  (the map page shows "Estimated").
