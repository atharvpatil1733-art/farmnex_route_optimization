"""Fill the running server with realistic Pune / Mumbai demo data and plan two trips.

    python demo/seed_demo.py                       # server on localhost:8001
    python demo/seed_demo.py --base-url https://your-backend/routes

Scenario
  Truck 1 (Chakan): auto-pools 3 farmers near Chakan -> Pune Market Yard (Gultekdi).
                    One load is Crop Rescue (priority 2) so it is delivered first.
  Truck 2 (Hadapsar): Uruli Kanchan onions -> Vashi APMC, Navi Mumbai.
                    Two loads are waiting near Vashi, so when Truck 2 delivers it gets
                    a RETURN-TRIP notification instead of driving back empty.
"""
import argparse
import sys
import time

import httpx

PUNE_MARKET_YARD = (18.4870, 73.8660, "Market Yard, Gultekdi, Pune")
VASHI_APMC = (19.0786, 73.0055, "APMC Market, Vashi, Navi Mumbai")

# In the real app these come from the main app's vehicle registration (same ids).
VEHICLES = [
    dict(driver_name="Ramesh Jadhav", driver_phone="9800000001", vehicle_number="MH14AB1234", vehicle_type="mini_truck",
         capacity_kg=2500, rate_per_ton_km=8.5, base_lat=18.7606, base_lng=73.8636, base_label="Chakan"),
    dict(driver_name="Suresh Pawar", driver_phone="9800000002", vehicle_number="MH12CD5678", vehicle_type="truck",
         capacity_kg=6000, rate_per_ton_km=6.0, base_lat=18.5089, base_lng=73.9260, base_label="Hadapsar, Pune"),
]


def load(farmer, crop, kg, pickup, drop, buyer, priority=0):
    return dict(farmer_name=farmer, farmer_phone="9700000000", buyer_name=buyer, crop=crop, weight_kg=kg,
                priority=priority, pickup_lat=pickup[0], pickup_lng=pickup[1], pickup_address=pickup[2],
                drop_lat=drop[0], drop_lng=drop[1], drop_address=drop[2])


LOADS_TRUCK1 = [
    load("Sunita Gaikwad", "Tomato", 800, (18.7350, 73.6750, "Talegaon Dabhade"), PUNE_MARKET_YARD, "Shinde Traders", priority=2),
    load("Ganesh Bhor", "Potato", 900, (18.7700, 73.8400, "Chakan village"), PUNE_MARKET_YARD, "Pune Veg Wholesale"),
    load("Vijay Thorat", "Cabbage", 600, (18.9150, 73.8950, "Rajgurunagar (Khed)"), PUNE_MARKET_YARD, "Shinde Traders"),
]
LOAD_TRUCK2 = load("Anil Kanchan", "Onion", 4000, (18.4867, 74.1383, "Uruli Kanchan"), VASHI_APMC, "Mumbai Onion Co.")
BACKHAUL_LOADS = [
    load("Prakash Mhatre", "Coconut", 1500, (18.9894, 73.1175, "Panvel"), PUNE_MARKET_YARD, "Pune Fruit Mart"),
    load("Deepak Patil", "Rice", 2000, (18.8765, 72.9396, "Uran"), (18.5089, 73.9260, "Hadapsar Grain Market, Pune"), "Hadapsar Grains"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8001/routes")
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base_url, timeout=60)

    stamp = str(int(time.time()))[-4:]  # lets you seed repeatedly without number clashes
    vehicles = []
    for i, v in enumerate(VEHICLES, start=1):
        v = dict(v, vehicle_number=v["vehicle_number"][:-4] + stamp)
        r = c.put(f"/vehicles/demo-vehicle-{i}-{stamp}", json=v)  # what the main backend does after registration
        r.raise_for_status()
        vehicles.append(r.json())
        print(f"Synced vehicle {r.json()['vehicle_number']} ({v['driver_name']}, Rs {v['rate_per_ton_km']}/tonne-km)")
    order_no = iter(range(1, 100))

    for l in LOADS_TRUCK1:
        r = c.post("/loads", json=dict(l, order_id=f"DEMO-{stamp}-{next(order_no)}"))
        r.raise_for_status()
        print(f"Order {r.json()['order_id']}: {l['crop']:8} {l['pickup_address']:28} -> {l['drop_address']}")
    r = c.post("/loads", json=dict(LOAD_TRUCK2, order_id=f"DEMO-{stamp}-{next(order_no)}"))
    r.raise_for_status()
    truck2_load = r.json()["id"]

    print("\nPlanning Truck 1 (automatic pooling)...")
    r = c.post("/trips/plan", json={"vehicle_id": vehicles[0]["id"]})
    if r.status_code >= 400:
        sys.exit(f"Plan failed: {r.text}")
    t1 = r.json()
    for s in t1["stops"]:
        print(f"  {s['seq']}. +{s['planned_arrival_min']:6.0f} min  {s['label']}")
    for l in c.get("/loads", params={"status": "assigned"}).json():
        if l["trip_id"] == t1["id"]:
            print(f"  Fare for order {l['order_id']} ({l['weight_kg']:.0f} kg {l['crop']}): Rs {l['estimated_fare']:.0f}")
    print(f"  Total {t1['total_distance_km']} km, {t1['total_duration_min']:.0f} min, ~Rs {t1['estimated_cost']:.0f} ({t1['routing_source']})")

    print("\nPlanning Truck 2 (Uruli Kanchan -> Vashi)...")
    r = c.post("/trips/plan", json={"vehicle_id": vehicles[1]["id"], "load_ids": [truck2_load]})
    r.raise_for_status()
    t2 = r.json()
    print(f"  Total {t2['total_distance_km']} km, {t2['total_duration_min']:.0f} min")

    for l in BACKHAUL_LOADS:  # posted after Truck 2 left, waiting near Vashi
        c.post("/loads", json=dict(l, order_id=f"DEMO-{stamp}-{next(order_no)}")).raise_for_status()
    print("  2 loads are waiting near Vashi for a return truck.")

    print("\nOpen these live maps (farmer / buyer / judges):")
    print(f"  Truck 1: {t1['tracking_url']}")
    print(f"  Truck 2: {t2['tracking_url']}")
    print(f"\nBuyer/farmer view of an order: {args.base_url}/orders/DEMO-{stamp}-1/delivery")
    print("\nMake the trucks move:")
    print(f"  python demo/simulate_driver.py {t1['id']} --base-url {args.base_url}")
    print(f"  python demo/simulate_driver.py {t2['id']} --base-url {args.base_url}   # ends with a return-trip offer")


if __name__ == "__main__":
    main()
