"""The bridge between the MAIN app's orders and this component's deliveries.

Simple picture:
  * The main app has ORDERS (farmer sells X kg of crop to a buyer).
  * This component has LOADS (collect X kg from the farmer, drop at the buyer).
  * One order that needs transport = one load. They are joined by `order_id`.

The main backend uses three things from here:

  1. create_delivery_for_order(...)  - call it when an order is confirmed and needs a truck
  2. delivery_for_order(...)         - "where is my order?" (status, ETA, map link)
  0. upsert_vehicle(...)             - copy a vehicle from the main app (after registration / rate change)
  3. on_delivery_update(fn)          - register a function that is called automatically when
                                        a load is assigned / picked up / delivered / cancelled,
                                        e.g. to mark the order delivered and release the wallet
                                        payment to the farmer.
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .matching import notify_vehicles_about_new_load
from .models import RtLoad, RtVehicle

log = logging.getLogger("farmnex_routes.hooks")

ACTIVE_LOAD = ("pending", "assigned", "picked_up", "delivered")

_listeners: list[Callable[[RtLoad, str], None]] = []


def on_delivery_update(fn: Callable[[RtLoad, str], None]) -> Callable[[RtLoad, str], None]:
    """Register a function called as fn(load, new_status) after every status change.

    new_status is one of: assigned, picked_up, delivered, cancelled.
    load.order_id tells you which order it belongs to. Can be used as a decorator.
    """
    _listeners.append(fn)
    return fn


def emit(load: RtLoad, status: str) -> None:
    for fn in list(_listeners):
        try:
            fn(load, status)
        except Exception:  # a bug in the main app's listener must never break a delivery
            log.exception("delivery listener failed for load %s (%s)", load.id, status)


def find_load_for_order(session: Session, order_id: str) -> RtLoad | None:
    return session.scalar(
        select(RtLoad).where(RtLoad.order_id == order_id, RtLoad.status.in_(ACTIVE_LOAD)).order_by(RtLoad.created_at.desc())
    )


def create_delivery_for_order(
    session: Session,
    *,
    order_id: str,
    farmer_name: str,
    buyer_name: str,
    crop: str,
    weight_kg: float,
    pickup_lat: float,
    pickup_lng: float,
    pickup_address: str,
    drop_lat: float,
    drop_lng: float,
    drop_address: str,
    farmer_id: str | None = None,
    farmer_phone: str | None = None,
    buyer_id: str | None = None,
    buyer_phone: str | None = None,
    needs_cold: bool = False,
    priority: int = 0,
) -> tuple[RtLoad, bool]:
    """Create the delivery for an order. Safe to call twice: if this order already has a
    delivery, that one is returned. Returns (load, created_now)."""
    existing = find_load_for_order(session, order_id)
    if existing:
        return existing, False
    load = RtLoad(
        order_id=order_id, farmer_id=farmer_id, farmer_name=farmer_name, farmer_phone=farmer_phone,
        buyer_id=buyer_id, buyer_name=buyer_name, buyer_phone=buyer_phone, crop=crop, weight_kg=weight_kg,
        needs_cold=needs_cold, priority=priority, pickup_lat=pickup_lat, pickup_lng=pickup_lng,
        pickup_address=pickup_address, drop_lat=drop_lat, drop_lng=drop_lng, drop_address=drop_address,
    )
    session.add(load)
    session.flush()
    notify_vehicles_about_new_load(session, load)  # free trucks nearby get an alert
    session.commit()
    return load, True


def cancel_delivery_for_order(session: Session, order_id: str) -> RtLoad | None:
    """Cancel the delivery when the order is cancelled. Only possible before a truck is assigned."""
    load = find_load_for_order(session, order_id)
    if load is None:
        return None
    if load.status != "pending":
        raise ValueError(f"Delivery is already {load.status}; cancel the trip first")
    load.status = "cancelled"
    session.commit()
    emit(load, "cancelled")
    return load


VEHICLE_FIELDS = {
    "vehicle_number", "vehicle_type", "capacity_kg", "refrigerated", "rate_per_ton_km", "base_lat", "base_lng",
    "base_label", "driver_user_id", "driver_name", "driver_phone", "owner_role",
}


def upsert_vehicle(session: Session, vehicle_id: str, **fields) -> RtVehicle:
    """Create or update this component's copy of a main-app vehicle (same id).
    Call it after a driver registers a vehicle, edits it, or its rate changes."""
    unknown = set(fields) - VEHICLE_FIELDS
    if unknown:
        raise ValueError(f"Unknown vehicle fields: {sorted(unknown)}")
    if "vehicle_number" in fields and fields["vehicle_number"]:
        fields["vehicle_number"] = fields["vehicle_number"].replace(" ", "").upper()
    v = session.get(RtVehicle, vehicle_id)
    if v is None:
        v = RtVehicle(id=vehicle_id, **fields)
        session.add(v)
    else:
        for key, value in fields.items():
            setattr(v, key, value)
    session.commit()
    return v
