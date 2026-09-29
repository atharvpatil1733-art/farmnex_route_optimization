"""End-to-end API flow: register -> loads -> plan -> GPS -> deliver -> return-trip offer."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from farmnex_routes import router

app = FastAPI()
app.include_router(router, prefix="/routes")
client = TestClient(app)

MARKET = dict(drop_lat=18.487, drop_lng=73.866, drop_address="Market Yard, Pune")
VASHI = dict(drop_lat=19.0786, drop_lng=73.0055, drop_address="APMC Vashi")


def _vehicle(number, lat, lng, cap=3000):
    r = client.post("/routes/vehicles", json=dict(
        driver_name="Test Driver", driver_phone="9800000000", vehicle_number=number,
        vehicle_type="mini_truck", capacity_kg=cap, base_lat=lat, base_lng=lng))
    assert r.status_code == 201, r.text
    return r.json()


def _load(crop, kg, lat, lng, drop, priority=0):
    r = client.post("/routes/loads", json=dict(
        farmer_name="Farmer", buyer_name="Wholesaler", crop=crop, weight_kg=kg, priority=priority,
        pickup_lat=lat, pickup_lng=lng, pickup_address=f"{crop} farm", **drop))
    assert r.status_code == 201, r.text
    return r.json()


def test_duplicate_vehicle_rejected():
    _vehicle("MH01ZZ0001", 18.5, 73.9)
    r = client.post("/routes/vehicles", json=dict(
        driver_name="X Y", driver_phone="9800000000", vehicle_number="mh01 zz0001",
        capacity_kg=100, base_lat=18.5, base_lng=73.9))
    assert r.status_code == 409


def test_full_trip_with_tracking_and_backhaul():
    truck = _vehicle("MH14TT0001", 18.76, 73.86)
    l1 = _load("Tomato", 800, 18.735, 73.675, MARKET, priority=2)
    assert l1["vehicles_notified"] >= 1  # truck is free and nearby
    _load("Potato", 900, 18.77, 73.84, MARKET)
    _load("Mango", 500, 19.0, 73.2, VASHI)  # far away, should not be pooled

    r = client.post("/routes/trips/plan", json={"vehicle_id": truck["id"]})
    assert r.status_code == 201, r.text
    trip = r.json()
    assert len(trip["stops"]) == 4
    assert trip["tracking_url"].endswith(f"/routes/track/{trip['id']}/view")

    # Truck is busy now
    assert client.post("/routes/trips/plan", json={"vehicle_id": truck["id"]}).status_code == 409

    # A return load waiting near the market
    back = _load("Onion", 1000, 18.50, 73.88, dict(drop_lat=18.77, drop_lng=73.85, drop_address="Chakan"))

    client.post(f"/routes/trips/{trip['id']}/start")
    client.post(f"/routes/vehicles/{truck['id']}/location", json={"lat": 18.75, "lng": 73.80, "speed_kmph": 30})
    snap = client.get(f"/routes/track/{trip['id']}").json()
    assert snap["location"]["is_live"] and snap["next_stop"]["eta_min"] > 0

    # Drop before pickup is refused
    first_drop = next(s for s in trip["stops"] if s["kind"] == "drop")
    assert client.post(f"/routes/trips/{trip['id']}/stops/{first_drop['id']}/complete").status_code == 409

    result = None
    for s in trip["stops"]:
        result = client.post(f"/routes/trips/{trip['id']}/stops/{s['id']}/complete").json()
    assert result["trip_status"] == "completed"
    assert any(o["load_id"] == back["id"] for o in result["backhaul_options"])

    notes = client.get(f"/routes/vehicles/{truck['id']}/notifications").json()
    assert any(n["kind"] == "backhaul" for n in notes)

    r = client.post(f"/routes/vehicles/{truck['id']}/accept-load/{back['id']}")
    assert r.status_code == 201 and r.json()["is_backhaul"]

    track = client.get(f"/routes/loads/{back['id']}/track").json()
    assert track["pickup"]["eta_min"] is not None

    html = client.get(f"/routes/track/{r.json()['id']}/view").text
    assert "/routes/track/" in html and "__DATA_URL__" not in html


def test_cancel_returns_loads_to_pending():
    truck = _vehicle("MH12CC0002", 18.52, 73.93)
    l = _load("Garlic", 300, 18.53, 73.94, MARKET)
    trip = client.post("/routes/trips/plan", json={"vehicle_id": truck["id"], "load_ids": [l["id"]]}).json()
    client.post(f"/routes/trips/{trip['id']}/cancel")
    assert client.get(f"/routes/loads/{l['id']}").json()["status"] == "pending"
    assert client.get(f"/routes/vehicles/{truck['id']}").json()["status"] == "available"
