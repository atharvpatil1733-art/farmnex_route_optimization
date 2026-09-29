"""Pretend to be the driver's phone: drive the planned route, send GPS pings and
mark each pickup / delivery as done. Great for demos without a real truck.

    python demo/simulate_driver.py <trip_id> [--interval 2] [--pings-per-leg 15]
"""
import argparse
import math
import time

import httpx


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def resample(path, k):
    """k evenly spaced points along a polyline."""
    if len(path) == 1:
        return path * k
    cum = [0.0]
    for p, q in zip(path, path[1:]):
        cum.append(cum[-1] + dist(p, q))
    total = cum[-1] or 1e-9
    out, j = [], 0
    for i in range(1, k + 1):
        target = total * i / k
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        seg = (cum[j + 1] - cum[j]) or 1e-9
        f = (target - cum[j]) / seg
        p, q = path[j], path[j + 1]
        out.append([p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trip_id")
    ap.add_argument("--base-url", default="http://localhost:8001/routes")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between GPS pings")
    ap.add_argument("--pings-per-leg", type=int, default=15)
    args = ap.parse_args()
    c = httpx.Client(base_url=args.base_url, timeout=60)

    trip = c.get(f"/trips/{args.trip_id}").json()
    vid = trip["vehicle_id"]
    if trip["status"] == "planned":
        c.post(f"/trips/{args.trip_id}/start").raise_for_status()
    print(f"Driving trip {args.trip_id}. Live map: {trip['tracking_url']}")

    geom = trip["geometry"] or [[trip["start_lat"], trip["start_lng"]]]
    idx = 0
    pos = [trip["start_lat"], trip["start_lng"]]
    for stop in trip["stops"]:
        if stop["status"] == "done":
            continue
        target = [stop["lat"], stop["lng"]]
        # Follow the road geometry up to the point closest to this stop.
        end = min(range(idx, len(geom)), key=lambda i: dist(geom[i], target), default=idx)
        path = [pos] + geom[idx:end + 1] + [target]
        for p in resample(path, args.pings_per_leg):
            c.post(f"/vehicles/{vid}/location", json={"lat": p[0], "lng": p[1], "speed_kmph": 38}).raise_for_status()
            time.sleep(args.interval)
        pos, idx = target, end
        res = c.post(f"/trips/{args.trip_id}/stops/{stop['id']}/complete").json()
        print(f"  Done: {stop['label']}")
        if res.get("trip_status") == "completed":
            print("\nTrip completed. Truck is empty now.")
            opts = res.get("backhaul_options") or []
            if opts:
                print("RETURN-TRIP OFFERS sent to driver:")
                for o in opts:
                    print(f"  - {o['weight_kg']:.0f} kg {o['crop']} {o['pickup_address']} -> {o['drop_address']}: "
                          f"{o['distance_to_pickup_km']} km away, saves {o['empty_km_saved']} empty km, ~Rs {o['estimated_earning']}")
                print(f"\nAccept the best one:\n  POST {args.base_url}/vehicles/{vid}/accept-load/{opts[0]['load_id']}")
            else:
                print("No return loads nearby.")


if __name__ == "__main__":
    main()
