"""End-to-end API flow: vehicle sync -> order delivery -> plan -> GPS -> deliver -> return-trip offer."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from farmnex_routes import on_delivery_update, router

app = FastAPI()
app.include_router(router, prefix="/routes")
client = TestClient(app)

MARKET = dict(drop_lat=18.487, drop_lng=73.866, drop_address="Market Yard, Pune")
VASHI = dict(drop_lat=19.0786, drop_lng=73.0055, drop_address="APMC Vashi")

events: list[tuple[str | None, str]] = []
on_delivery_update(lambda load, status: events.append((load.order_id, status)))


def _vehicle(vid, lat, lng, cap=3000, rate=None):
    r = client.put(f"/routes/vehicles/{vid}", json=dict(
        driver_name="Test Driver", driver_phone="9800000000", vehicle_number=f"MH14{vid[-6:]}",
        vehicle_type="mini_truck", capacity_kg=cap, rate_per_ton_km=rate, base_lat=lat, base_lng=lng))
    assert r.status_code == 200, r.text
    return r.json()


def _load(crop, kg, lat, lng, drop, priority=0, order_id=None):
    r = client.post("/routes/loads", json=dict(
        order_id=order_id, farmer_name="Farmer", buyer_name="Wholesaler", crop=crop, weight_kg=kg,
        priority=priority, pickup_lat=lat, pickup_lng=lng, pickup_address=f"{crop} farm", **drop))
    assert r.status_code == 201, r.text
    return r.json()


def test_vehicle_sync_creates_then_updates():
    _vehicle("veh-sync-01", 18.5, 73.9, cap=1000)
    v = _vehicle("veh-sync-01", 18.5, 73.9, cap=1500, rate=9)
    assert v["capacity_kg"] == 1500 and v["rate_per_ton_km"] == 9
    assert len([x for x in client.get("/routes/vehicles").json() if x["id"] == "veh-sync-01"]) == 1


def test_same_order_never_gets_two_deliveries():
    a = _load("Grapes", 100, 21.14, 79.08, MARKET, order_id="ORD-DUP")
    b = _load("Grapes", 100, 21.14, 79.08, MARKET, order_id="ORD-DUP")
    assert a["id"] == b["id"] and b["already_existed"]


def test_full_trip_with_tracking_backhaul_and_order_events():
    truck = _vehicle("veh-trip-01", 18.76, 73.86, rate=8)
    _load("Tomato", 800, 18.735, 73.675, MARKET, priority=2, order_id="ORD-1")
    _load("Potato", 900, 18.77, 73.84, MARKET, order_id="ORD-2")
    _load("Mango", 500, 19.0, 73.2, VASHI, order_id="ORD-FAR")  # far away, should not be pooled

    r = client.post("/routes/trips/plan", json={"vehicle_id": truck["id"]})
    assert r.status_code == 201, r.text
    trip = r.json()
    assert len(trip["stops"]) == 4
    assert trip["tracking_url"].endswith(f"/routes/track/{trip['id']}/view")
    assert ("ORD-1", "assigned") in events

    # Fare = km x tonnes x rate, and the trip cost is the sum of the farmers' shares
    order1 = client.get("/routes/orders/ORD-1/delivery").json()
    order2 = client.get("/routes/orders/ORD-2/delivery").json()
    assert order1["estimated_fare"] > 0
    assert trip["estimated_cost"] == order1["estimated_fare"] + order2["estimated_fare"]
    assert order1["tracking_url"].endswith("?load=" + order1["load_id"])

    assert client.post("/routes/trips/plan", json={"vehicle_id": truck["id"]}).status_code == 409  # busy

    back = _load("Onion", 1000, 18.50, 73.88, dict(drop_lat=18.77, drop_lng=73.85, drop_address="Chakan"), order_id="ORD-BACK")

    client.post(f"/routes/trips/{trip['id']}/start")
    client.post(f"/routes/vehicles/{truck['id']}/location", json={"lat": 18.75, "lng": 73.80, "speed_kmph": 30})
    snap = client.get(f"/routes/track/{trip['id']}").json()
    assert snap["location"]["is_live"] and snap["next_stop"]["eta_min"] > 0

    first_drop = next(s for s in trip["stops"] if s["kind"] == "drop")
    assert client.post(f"/routes/trips/{trip['id']}/stops/{first_drop['id']}/complete").status_code == 409

    result = None
    for s in trip["stops"]:
        result = client.post(f"/routes/trips/{trip['id']}/stops/{s['id']}/complete").json()
    assert result["trip_status"] == "completed"
    assert ("ORD-1", "picked_up") in events and ("ORD-1", "delivered") in events
    assert client.get("/routes/orders/ORD-1/delivery").json()["delivered_at"] is not None
    assert any(o["load_id"] == back["id"] for o in result["backhaul_options"])

    notes = client.get(f"/routes/vehicles/{truck['id']}/notifications").json()
    assert any(n["kind"] == "backhaul" for n in notes)

    r = client.post(f"/routes/vehicles/{truck['id']}/accept-load/{back['id']}")
    assert r.status_code == 201 and r.json()["is_backhaul"]
    assert client.get("/routes/orders/ORD-BACK/delivery").json()["pickup"]["eta_min"] is not None

    html = client.get(f"/routes/track/{r.json()['id']}/view").text
    assert "/routes/track/" in html and "__DATA_URL__" not in html


def test_cancel_trip_and_cancel_order_delivery():
    truck = _vehicle("veh-cancel-01", 18.52, 73.93)
    l = _load("Garlic", 300, 18.53, 73.94, MARKET, order_id="ORD-CANCEL")
    trip = client.post("/routes/trips/plan", json={"vehicle_id": truck["id"], "load_ids": [l["id"]]}).json()
    assert client.post("/routes/orders/ORD-CANCEL/cancel-delivery").status_code == 409  # truck assigned
    client.post(f"/routes/trips/{trip['id']}/cancel")
    assert client.get(f"/routes/loads/{l['id']}").json()["status"] == "pending"
    assert client.get(f"/routes/vehicles/{truck['id']}").json()["status"] == "available"
    assert client.post("/routes/orders/ORD-CANCEL/cancel-delivery").status_code == 200
    assert ("ORD-CANCEL", "cancelled") in events
    assert client.get("/routes/orders/ORD-CANCEL/delivery").status_code == 404


def test_python_helpers_for_main_backend():
    """What the main backend does in its own code (no web calls)."""
    from farmnex_routes import create_delivery_for_order, delivery_for_order, session_scope, upsert_vehicle

    with session_scope() as s:
        upsert_vehicle(s, "main-app-veh-99", vehicle_number="mh 12 xy 9999", vehicle_type="truck",
                       capacity_kg=5000, rate_per_ton_km=6.5, base_lat=18.52, base_lng=73.85)
        load, created = create_delivery_for_order(
            s, order_id="ORD-PY-1", farmer_name="F", buyer_name="B", crop="Onion", weight_kg=1000,
            pickup_lat=21.0, pickup_lng=79.0, pickup_address="A", drop_lat=21.1, drop_lng=79.1, drop_address="B")
        assert created
        assert delivery_for_order(s, "ORD-PY-1")["status"] == "pending"
    assert client.get("/routes/vehicles/main-app-veh-99").json()["vehicle_number"] == "MH12XY9999"
