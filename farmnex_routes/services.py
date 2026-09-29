"""Business logic that ties the database to the optimizer. The router calls these."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .geo import leg, route_geometry
from .matching import notify_backhaul, select_loads_for_vehicle, vehicle_position
from .models import RtLoad, RtLocation, RtTrip, RtTripStop, RtVehicle, utcnow
from .optimizer import InfeasiblePlan, LoadSpec, plan_sequence

ACTIVE = ("planned", "in_progress")


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:  # SQLite drops the timezone
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_or_404(session: Session, model, obj_id: str, name: str):
    obj = session.get(model, obj_id)
    if obj is None:
        raise ServiceError(404, f"{name} {obj_id} not found")
    return obj


def active_trip(session: Session, vehicle_id: str) -> RtTrip | None:
    return session.scalar(
        select(RtTrip).where(RtTrip.vehicle_id == vehicle_id, RtTrip.status.in_(ACTIVE)).order_by(RtTrip.created_at.desc())
    )


def trip_stops(session: Session, trip_id: str) -> list[RtTripStop]:
    return list(session.scalars(select(RtTripStop).where(RtTripStop.trip_id == trip_id).order_by(RtTripStop.seq)))


# ---------------------------------------------------------------- planning
def plan_trip(session: Session, vehicle: RtVehicle, load_ids: list[str] | None = None, is_backhaul: bool = False) -> RtTrip:
    if vehicle.status == "offline":
        raise ServiceError(409, "Vehicle is offline")
    existing = active_trip(session, vehicle.id)
    if existing:
        raise ServiceError(409, f"Vehicle already has an active trip {existing.id}")

    start = vehicle_position(vehicle)
    if load_ids:
        loads = list(session.scalars(select(RtLoad).where(RtLoad.id.in_(load_ids))))
        missing = set(load_ids) - {l.id for l in loads}
        if missing:
            raise ServiceError(404, f"Loads not found: {sorted(missing)}")
        for l in loads:
            if l.status != "pending":
                raise ServiceError(409, f"Load {l.id} is {l.status}, not pending")
            if l.needs_cold and not vehicle.refrigerated:
                raise ServiceError(409, f"Load {l.id} needs a refrigerated vehicle")
    else:
        loads = select_loads_for_vehicle(session, vehicle, start)
        if not loads:
            raise ServiceError(404, "No pending loads near this vehicle")

    specs = [
        LoadSpec(
            load_id=l.id,
            pickup=(l.pickup_lat, l.pickup_lng),
            drop=(l.drop_lat, l.drop_lng),
            weight_kg=l.weight_kg,
            priority=l.priority,
            pickup_label=f"Collect {l.weight_kg:.0f} kg {l.crop} from {l.farmer_name}, {l.pickup_address}",
            drop_label=f"Deliver {l.weight_kg:.0f} kg {l.crop} to {l.buyer_name}, {l.drop_address}",
        )
        for l in loads
    ]
    try:
        plan = plan_sequence(start, specs, vehicle.capacity_kg)
    except InfeasiblePlan as exc:
        raise ServiceError(422, str(exc))

    geometry, _ = route_geometry([start] + [s.point for s in plan.stops])
    # Prototype fare: every planned km x the vehicle's per-km rate.
    fare = plan.total_distance_km * settings.rate_for(vehicle.vehicle_type, vehicle.refrigerated)
    trip = RtTrip(
        vehicle_id=vehicle.id,
        is_backhaul=is_backhaul,
        total_distance_km=plan.total_distance_km,
        total_duration_min=plan.total_duration_min,
        estimated_cost=round(fare),
        routing_source=plan.source,
        start_lat=start[0],
        start_lng=start[1],
        geometry=geometry,
    )
    session.add(trip)
    session.flush()
    for seq, s in enumerate(plan.stops, start=1):
        session.add(
            RtTripStop(
                trip_id=trip.id,
                load_id=s.load_id,
                seq=seq,
                kind=s.kind,
                lat=s.point[0],
                lng=s.point[1],
                label=s.label,
                leg_distance_km=s.leg_distance_km,
                leg_duration_min=s.leg_duration_min,
                planned_arrival_min=s.arrival_min,
            )
        )
    for l in loads:
        l.status = "assigned"
        l.trip_id = trip.id
    vehicle.status = "on_trip"
    session.commit()
    return trip


def start_trip(session: Session, trip: RtTrip) -> RtTrip:
    if trip.status != "planned":
        raise ServiceError(409, f"Trip is {trip.status}")
    trip.status = "in_progress"
    trip.started_at = utcnow()
    session.commit()
    return trip


def complete_stop(session: Session, trip: RtTrip, stop: RtTripStop) -> dict:
    if trip.status not in ACTIVE:
        raise ServiceError(409, f"Trip is {trip.status}")
    if stop.trip_id != trip.id:
        raise ServiceError(404, "Stop does not belong to this trip")
    if stop.status == "done":
        raise ServiceError(409, "Stop already completed")
    stops = trip_stops(session, trip.id)
    if stop.kind == "drop":
        pickup = next(s for s in stops if s.load_id == stop.load_id and s.kind == "pickup")
        if pickup.status != "done":
            raise ServiceError(409, "Collect this load before delivering it")

    if trip.status == "planned":
        trip.status = "in_progress"
        trip.started_at = utcnow()
    stop.status = "done"
    stop.done_at = utcnow()
    load = session.get(RtLoad, stop.load_id)
    load.status = "picked_up" if stop.kind == "pickup" else "delivered"

    vehicle = session.get(RtVehicle, trip.vehicle_id)
    vehicle.last_lat, vehicle.last_lng, vehicle.last_seen_at = stop.lat, stop.lng, utcnow()

    result: dict = {"stop_id": stop.id, "load_status": load.status, "trip_status": trip.status, "backhaul_options": []}
    if all(s.status == "done" for s in stops):
        trip.status = "completed"
        trip.completed_at = utcnow()
        vehicle.status = "available"
        result["trip_status"] = "completed"
        # Truck is now empty: look for a return load right here.
        result["backhaul_options"] = notify_backhaul(session, vehicle, (stop.lat, stop.lng))
    session.commit()
    return result


def cancel_trip(session: Session, trip: RtTrip) -> RtTrip:
    if trip.status not in ACTIVE:
        raise ServiceError(409, f"Trip is {trip.status}")
    for s in trip_stops(session, trip.id):
        load = session.get(RtLoad, s.load_id)
        if load.status == "assigned":
            load.status, load.trip_id = "pending", None
    trip.status = "cancelled"
    session.get(RtVehicle, trip.vehicle_id).status = "available"
    session.commit()
    return trip


# ---------------------------------------------------------------- GPS
def record_location(session: Session, vehicle: RtVehicle, lat: float, lng: float, speed=None, heading=None) -> RtLocation:
    trip = active_trip(session, vehicle.id)
    ping = RtLocation(vehicle_id=vehicle.id, trip_id=trip.id if trip else None, lat=lat, lng=lng, speed_kmph=speed, heading=heading)
    vehicle.last_lat, vehicle.last_lng, vehicle.last_seen_at = lat, lng, utcnow()
    if vehicle.status == "offline":
        vehicle.status = "on_trip" if trip else "available"
    session.add(ping)
    session.commit()
    return ping


def tracking_snapshot(session: Session, trip: RtTrip) -> dict:
    """Everything the farmer / buyer / judge map needs, including live ETAs."""
    vehicle = session.get(RtVehicle, trip.vehicle_id)
    stops = trip_stops(session, trip.id)
    now = utcnow()
    last_seen = _aware(vehicle.last_seen_at)
    has_gps = vehicle.last_lat is not None
    pos = vehicle_position(vehicle)
    stale = (not has_gps) or last_seen is None or (now - last_seen).total_seconds() > settings.gps_stale_seconds

    pending = [s for s in stops if s.status != "done"]
    etas: dict[str, float] = {}
    if pending:
        if trip.status == "planned" and not trip.started_at:
            # Not started: planned offsets from now.
            base = pending[0].planned_arrival_min
            first_leg = leg(pos, (pending[0].lat, pending[0].lng))[1]
            for s in pending:
                etas[s.id] = first_leg + (s.planned_arrival_min - base)
        else:
            t = leg(pos, (pending[0].lat, pending[0].lng))[1]
            etas[pending[0].id] = t
            for prev, s in zip(pending, pending[1:]):
                t += settings.service_minutes + s.leg_duration_min
                etas[s.id] = t

    trail = list(
        session.scalars(
            select(RtLocation).where(RtLocation.trip_id == trip.id).order_by(RtLocation.recorded_at.desc()).limit(200)
        )
    )[::-1]

    def stop_json(s: RtTripStop) -> dict:
        eta = etas.get(s.id)
        load = session.get(RtLoad, s.load_id)
        return {
            "id": s.id,
            "seq": s.seq,
            "kind": s.kind,
            "load_id": s.load_id,
            "crop": load.crop,
            "weight_kg": load.weight_kg,
            "party_name": load.farmer_name if s.kind == "pickup" else load.buyer_name,
            "label": s.label,
            "lat": s.lat,
            "lng": s.lng,
            "status": s.status,
            "done_at": _aware(s.done_at).isoformat() if s.done_at else None,
            "eta_min": round(eta, 1) if eta is not None else None,
            "eta_at": (now + timedelta(minutes=eta)).isoformat() if eta is not None else None,
        }

    return {
        "trip_id": trip.id,
        "status": trip.status,
        "is_backhaul": trip.is_backhaul,
        "total_distance_km": trip.total_distance_km,
        "total_duration_min": trip.total_duration_min,
        "estimated_cost": trip.estimated_cost,
        "routing_source": trip.routing_source,
        "vehicle": {
            "id": vehicle.id,
            "vehicle_number": vehicle.vehicle_number,
            "vehicle_type": vehicle.vehicle_type,
            "driver_name": vehicle.driver_name,
            "driver_phone": vehicle.driver_phone,
            "refrigerated": vehicle.refrigerated,
        },
        "location": {
            "lat": pos[0],
            "lng": pos[1],
            "recorded_at": last_seen.isoformat() if last_seen else None,
            "is_live": not stale,
        },
        "next_stop": stop_json(pending[0]) if pending else None,
        "eta_final_min": round(etas[pending[-1].id], 1) if pending else 0,
        "stops": [stop_json(s) for s in stops],
        "geometry": trip.geometry or [],
        "trail": [[p.lat, p.lng] for p in trail],
        "generated_at": now.isoformat(),
    }
