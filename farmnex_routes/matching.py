"""Load pooling and return-trip (backhaul) matching.

Pooling  : pick pending loads near the truck that go to the same market area,
           so one truck serves several small farmers in one trip.
Backhaul : when a truck finishes its deliveries it is usually empty far from home.
           We look for pending loads near it, preferably heading back towards its
           base, and notify the driver. The key number shown to judges is
           "empty km saved".
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .geo import Point, haversine_km
from .models import RtLoad, RtNotification, RtVehicle


def vehicle_position(v: RtVehicle) -> Point:
    if v.last_lat is not None and v.last_lng is not None:
        return (v.last_lat, v.last_lng)
    return (v.base_lat, v.base_lng)


def _compatible_pending_loads(session: Session, vehicle: RtVehicle):
    q = select(RtLoad).where(RtLoad.status == "pending", RtLoad.weight_kg <= vehicle.capacity_kg)
    if not vehicle.refrigerated:
        q = q.where(RtLoad.needs_cold.is_(False))
    return session.scalars(q).all()


def select_loads_for_vehicle(session: Session, vehicle: RtVehicle, start: Point) -> list[RtLoad]:
    cands = []
    for l in _compatible_pending_loads(session, vehicle):
        d = haversine_km(start, (l.pickup_lat, l.pickup_lng))
        if d <= settings.pool_radius_km:
            cands.append((l, d))
    # Urgent (Crop Rescue) loads first, then the closest.
    cands.sort(key=lambda x: (-x[0].priority, x[1]))

    chosen: list[RtLoad] = []
    total = 0.0
    for l, _ in cands:
        if len(chosen) >= settings.max_loads_per_trip:
            break
        if total + l.weight_kg > vehicle.capacity_kg:
            continue
        if chosen:
            anchor = (chosen[0].drop_lat, chosen[0].drop_lng)
            if haversine_km(anchor, (l.drop_lat, l.drop_lng)) > settings.pool_drop_radius_km:
                continue  # different market direction: leave it for another truck
        chosen.append(l)
        total += l.weight_kg
    return chosen


def find_backhaul_options(session: Session, vehicle: RtVehicle, position: Point, limit: int = 5) -> list[dict]:
    home = (vehicle.base_lat, vehicle.base_lng)
    rf = settings.road_factor
    empty_home_km = haversine_km(position, home) * rf
    rate = settings.rate_for(vehicle.vehicle_type, vehicle.refrigerated)

    options = []
    for l in _compatible_pending_loads(session, vehicle):
        pickup, drop = (l.pickup_lat, l.pickup_lng), (l.drop_lat, l.drop_lng)
        to_pickup = haversine_km(position, pickup) * rf
        if to_pickup > settings.backhaul_radius_km * rf:
            continue
        loaded_km = haversine_km(pickup, drop) * rf
        drop_to_home = haversine_km(drop, home) * rf
        empty_with_load = to_pickup + drop_to_home
        options.append(
            {
                "load_id": l.id,
                "crop": l.crop,
                "weight_kg": l.weight_kg,
                "priority": l.priority,
                "pickup_address": l.pickup_address,
                "drop_address": l.drop_address,
                "pickup": [l.pickup_lat, l.pickup_lng],
                "drop": [l.drop_lat, l.drop_lng],
                "distance_to_pickup_km": round(to_pickup, 1),
                "loaded_km": round(loaded_km, 1),
                "empty_km_without": round(empty_home_km, 1),
                "empty_km_with": round(empty_with_load, 1),
                "empty_km_saved": round(empty_home_km - empty_with_load, 1),
                "estimated_earning": round(loaded_km * rate),
            }
        )
    options.sort(key=lambda o: (-o["empty_km_saved"], -o["estimated_earning"]))
    return options[:limit]


def notify_backhaul(session: Session, vehicle: RtVehicle, position: Point) -> list[dict]:
    options = find_backhaul_options(session, vehicle, position)
    if options:
        top = options[0]
        session.add(
            RtNotification(
                vehicle_id=vehicle.id,
                kind="backhaul",
                load_id=top["load_id"],
                title=f"{len(options)} return load(s) near you",
                message=(
                    f"Best: {top['weight_kg']:.0f} kg {top['crop']} from {top['pickup_address']} "
                    f"to {top['drop_address']} ({top['distance_to_pickup_km']} km away). "
                    f"Saves ~{top['empty_km_saved']} empty km, earn ~Rs {top['estimated_earning']}."
                ),
                payload={"options": options},
            )
        )
    return options


def notify_vehicles_about_new_load(session: Session, load: RtLoad) -> int:
    """When a farmer posts a load, tell free trucks within range. Returns how many were notified."""
    q = select(RtVehicle).where(RtVehicle.status == "available", RtVehicle.capacity_kg >= load.weight_kg)
    if load.needs_cold:
        q = q.where(RtVehicle.refrigerated.is_(True))
    count = 0
    pickup = (load.pickup_lat, load.pickup_lng)
    for v in session.scalars(q):
        d = haversine_km(vehicle_position(v), pickup)
        if d > settings.new_load_notify_radius_km:
            continue
        session.add(
            RtNotification(
                vehicle_id=v.id,
                kind="new_load_nearby",
                load_id=load.id,
                title="New load near you",
                message=f"{load.weight_kg:.0f} kg {load.crop} at {load.pickup_address} ({d * settings.road_factor:.1f} km) "
                f"to {load.drop_address}.",
                payload={"load_id": load.id, "distance_km": round(d * settings.road_factor, 1)},
            )
        )
        count += 1
    return count
