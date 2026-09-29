"""FastAPI router. Mount it in the main FarmNex backend with:

    from farmnex_routes import router as routes_router
    app.include_router(routes_router, prefix="/routes")
"""
from __future__ import annotations

from importlib import resources

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services as svc
from .config import settings
from .db import get_session
from .hooks import cancel_delivery_for_order, create_delivery_for_order, emit, upsert_vehicle
from .matching import find_backhaul_options, notify_vehicles_about_new_load, vehicle_position
from .models import RtLoad, RtNotification, RtTrip, RtTripStop, RtVehicle
from .schemas import (
    LoadCreate,
    LoadCreated,
    LoadOut,
    LocationPing,
    NotificationOut,
    PlanTripRequest,
    StopOut,
    TripOut,
    VehicleSync,
    VehicleOut,
    VehicleStatusUpdate,
)

router = APIRouter(tags=["Route Optimization"])


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.ServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail)


def _get(session, model, obj_id, name):
    return _run(svc.get_or_404, session, model, obj_id, name)


def _tracking_url(request: Request, trip_id: str) -> str:
    url = request.url_for("tracking_view", trip_id=trip_id)
    if settings.public_base_url:  # behind a proxy that hides https: use the configured public origin
        return settings.public_base_url + url.path
    return str(url)


def _trip_out(session: Session, trip: RtTrip, request: Request) -> TripOut:
    out = TripOut.model_validate(trip)
    out.stops = [StopOut.model_validate(s) for s in svc.trip_stops(session, trip.id)]
    out.geometry = trip.geometry
    out.tracking_url = _tracking_url(request, trip.id)
    return out


# ---------------------------------------------------------------- vehicles
@router.put(
    "/vehicles/{vehicle_id}",
    response_model=VehicleOut,
    summary="Main backend: copy a vehicle from the main app (create or update)",
)
def sync_vehicle(vehicle_id: str, body: VehicleSync, session: Session = Depends(get_session)):
    """Call this from the main backend right after a driver registers or edits a vehicle.
    Use the MAIN app's vehicle id in the URL, so both systems talk about the same truck."""
    return upsert_vehicle(session, vehicle_id, **body.model_dump())


@router.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(status: str | None = None, driver_user_id: str | None = None, session: Session = Depends(get_session)):
    q = select(RtVehicle).order_by(RtVehicle.created_at)
    if status:
        q = q.where(RtVehicle.status == status)
    if driver_user_id:
        q = q.where(RtVehicle.driver_user_id == driver_user_id)
    return session.scalars(q).all()


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut)
def get_vehicle(vehicle_id: str, session: Session = Depends(get_session)):
    return _get(session, RtVehicle, vehicle_id, "Vehicle")


@router.patch("/vehicles/{vehicle_id}/status", response_model=VehicleOut, summary="Driver goes online / offline")
def set_vehicle_status(vehicle_id: str, body: VehicleStatusUpdate, session: Session = Depends(get_session)):
    v = _get(session, RtVehicle, vehicle_id, "Vehicle")
    if v.status == "on_trip":
        raise HTTPException(409, "Finish or cancel the current trip first")
    v.status = body.status
    session.commit()
    return v


@router.post("/vehicles/{vehicle_id}/location", status_code=202, summary="GPS ping from the driver's phone")
def post_location(vehicle_id: str, body: LocationPing, session: Session = Depends(get_session)):
    v = _get(session, RtVehicle, vehicle_id, "Vehicle")
    ping = svc.record_location(session, v, body.lat, body.lng, body.speed_kmph, body.heading)
    return {"ok": True, "trip_id": ping.trip_id}


@router.get("/vehicles/{vehicle_id}/current-trip", response_model=TripOut | None)
def current_trip(vehicle_id: str, request: Request, session: Session = Depends(get_session)):
    _get(session, RtVehicle, vehicle_id, "Vehicle")
    trip = svc.active_trip(session, vehicle_id)
    return _trip_out(session, trip, request) if trip else None


@router.get("/vehicles/{vehicle_id}/backhaul", summary="Return loads near the truck right now")
def backhaul_options(vehicle_id: str, session: Session = Depends(get_session)):
    v = _get(session, RtVehicle, vehicle_id, "Vehicle")
    return {"vehicle_id": v.id, "position": vehicle_position(v), "options": find_backhaul_options(session, v, vehicle_position(v))}


@router.post(
    "/vehicles/{vehicle_id}/accept-load/{load_id}",
    response_model=TripOut,
    status_code=201,
    summary="Driver accepts a return / nearby load",
)
def accept_load(vehicle_id: str, load_id: str, request: Request, session: Session = Depends(get_session)):
    v = _get(session, RtVehicle, vehicle_id, "Vehicle")
    trip = _run(svc.plan_trip, session, v, [load_id], is_backhaul=True)
    return _trip_out(session, trip, request)


@router.get("/vehicles/{vehicle_id}/notifications", response_model=list[NotificationOut])
def notifications(vehicle_id: str, unread_only: bool = False, session: Session = Depends(get_session)):
    _get(session, RtVehicle, vehicle_id, "Vehicle")
    q = select(RtNotification).where(RtNotification.vehicle_id == vehicle_id).order_by(RtNotification.created_at.desc())
    if unread_only:
        q = q.where(RtNotification.is_read.is_(False))
    return session.scalars(q.limit(50)).all()


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: str, session: Session = Depends(get_session)):
    n = _get(session, RtNotification, notification_id, "Notification")
    n.is_read = True
    session.commit()
    return n


# ---------------------------------------------------------------- loads
@router.post("/loads", response_model=LoadCreated, status_code=201, summary="Create a delivery (farmer -> buyer)")
def create_load(body: LoadCreate, session: Session = Depends(get_session)):
    """If order_id is given and that order already has a delivery, the existing one is returned
    (so calling this twice by mistake never creates two deliveries)."""
    data = body.model_dump()
    if body.order_id:
        load, created = create_delivery_for_order(session, **data)
    else:
        data.pop("order_id")
        load = RtLoad(**data)
        session.add(load)
        session.flush()
        notify_vehicles_about_new_load(session, load)
        session.commit()
        created = True
    out = LoadCreated.model_validate(load)
    out.already_existed = not created
    return out


@router.get("/loads", response_model=list[LoadOut])
def list_loads(status: str | None = None, farmer_id: str | None = None, buyer_id: str | None = None, session: Session = Depends(get_session)):
    q = select(RtLoad).order_by(RtLoad.created_at.desc())
    if status:
        q = q.where(RtLoad.status == status)
    if farmer_id:
        q = q.where(RtLoad.farmer_id == farmer_id)
    if buyer_id:
        q = q.where(RtLoad.buyer_id == buyer_id)
    return session.scalars(q.limit(200)).all()


@router.get("/loads/{load_id}", response_model=LoadOut)
def get_load(load_id: str, session: Session = Depends(get_session)):
    return _get(session, RtLoad, load_id, "Load")


@router.post("/loads/{load_id}/cancel", response_model=LoadOut)
def cancel_load(load_id: str, session: Session = Depends(get_session)):
    load = _get(session, RtLoad, load_id, "Load")
    if load.status != "pending":
        raise HTTPException(409, f"Only pending loads can be cancelled (this one is {load.status})")
    load.status = "cancelled"
    session.commit()
    emit(load, "cancelled")
    return load


# ---------------------------------------------------------------- orders (main app ids)
@router.get("/orders/{order_id}/delivery", summary="Buyer / farmer: where is my order?")
def order_delivery(order_id: str, request: Request, session: Session = Depends(get_session)):
    info = svc.delivery_for_order(
        session, order_id, tracking_url_for=lambda tid: _tracking_url(request, tid)
    )
    if info is None:
        raise HTTPException(404, f"Order {order_id} has no delivery")
    return info


@router.post("/orders/{order_id}/cancel-delivery", response_model=LoadOut)
def order_cancel_delivery(order_id: str, session: Session = Depends(get_session)):
    try:
        load = cancel_delivery_for_order(session, order_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if load is None:
        raise HTTPException(404, f"Order {order_id} has no delivery")
    return load


@router.get("/loads/{load_id}/track", summary="Farmer / buyer tracks their own load")
def track_load(load_id: str, request: Request, session: Session = Depends(get_session)):
    load = _get(session, RtLoad, load_id, "Load")
    if not load.trip_id:
        return {"load_id": load.id, "status": load.status, "message": "Waiting for a vehicle to be assigned", "trip": None}
    trip = session.get(RtTrip, load.trip_id)
    snap = svc.tracking_snapshot(session, trip)
    mine = [s for s in snap["stops"] if s["load_id"] == load.id]
    return {
        "load_id": load.id,
        "status": load.status,
        "pickup": next((s for s in mine if s["kind"] == "pickup"), None),
        "delivery": next((s for s in mine if s["kind"] == "drop"), None),
        "tracking_url": _tracking_url(request, trip.id) + f"?load={load.id}",
        "trip": snap,
    }


# ---------------------------------------------------------------- trips
@router.post("/trips/plan", response_model=TripOut, status_code=201, summary="Optimize and create a trip for a vehicle")
def plan_trip(body: PlanTripRequest, request: Request, session: Session = Depends(get_session)):
    v = _get(session, RtVehicle, body.vehicle_id, "Vehicle")
    trip = _run(svc.plan_trip, session, v, body.load_ids)
    return _trip_out(session, trip, request)


@router.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(trip_id: str, request: Request, session: Session = Depends(get_session)):
    return _trip_out(session, _get(session, RtTrip, trip_id, "Trip"), request)


@router.post("/trips/{trip_id}/start", response_model=TripOut)
def start_trip(trip_id: str, request: Request, session: Session = Depends(get_session)):
    trip = _run(svc.start_trip, session, _get(session, RtTrip, trip_id, "Trip"))
    return _trip_out(session, trip, request)


@router.post("/trips/{trip_id}/stops/{stop_id}/complete", summary="Driver marks pickup / delivery done")
def complete_stop(trip_id: str, stop_id: str, session: Session = Depends(get_session)):
    trip = _get(session, RtTrip, trip_id, "Trip")
    stop = _get(session, RtTripStop, stop_id, "Stop")
    return _run(svc.complete_stop, session, trip, stop)


@router.post("/trips/{trip_id}/cancel", response_model=TripOut)
def cancel_trip(trip_id: str, request: Request, session: Session = Depends(get_session)):
    trip = _run(svc.cancel_trip, session, _get(session, RtTrip, trip_id, "Trip"))
    return _trip_out(session, trip, request)


# ---------------------------------------------------------------- tracking
@router.get("/track/{trip_id}", name="tracking_data", summary="Live location + ETAs (poll every 5-10 s)")
def tracking_data(trip_id: str, session: Session = Depends(get_session)):
    return svc.tracking_snapshot(session, _get(session, RtTrip, trip_id, "Trip"))


@router.get("/track/{trip_id}/view", name="tracking_view", response_class=HTMLResponse, summary="Live map page")
def tracking_view(trip_id: str, request: Request, session: Session = Depends(get_session)):
    _get(session, RtTrip, trip_id, "Trip")
    data_url = request.url_for("tracking_data", trip_id=trip_id)
    data_url = settings.public_base_url + data_url.path if settings.public_base_url else str(data_url)
    html = resources.files("farmnex_routes").joinpath("static/track.html").read_text(encoding="utf-8")
    return html.replace("__DATA_URL__", data_url)
