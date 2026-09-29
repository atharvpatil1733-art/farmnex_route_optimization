"""Tables owned by the route optimizer. All are prefixed `rt_` so they never clash
with the main FarmNex tables. Keep sql/001_create_route_tables.sql in sync."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RtVehicle(Base):
    """A copy of the vehicle registered in the MAIN app (same id). The main app keeps
    it up to date by calling PUT /vehicles/{id} - this component never registers vehicles."""

    __tablename__ = "rt_vehicles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # = main app's vehicle id
    driver_user_id: Mapped[str | None] = mapped_column(String(64), index=True)  # main app driver login id
    driver_name: Mapped[str | None] = mapped_column(String(120))
    driver_phone: Mapped[str | None] = mapped_column(String(20))
    owner_role: Mapped[str] = mapped_column(String(20), default="transporter")  # transporter | farmer
    vehicle_number: Mapped[str] = mapped_column(String(20))
    vehicle_type: Mapped[str] = mapped_column(String(20), default="tempo")  # pickup|tempo|mini_truck|truck
    capacity_kg: Mapped[float] = mapped_column(Float)
    refrigerated: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_per_ton_km: Mapped[float | None] = mapped_column(Float)  # current rate, sent by the main app
    base_lat: Mapped[float] = mapped_column(Float)
    base_lng: Mapped[float] = mapped_column(Float)
    base_label: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="available")  # available|on_trip|offline
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lng: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RtLoad(Base):
    """One shipment: collect from a farmer, deliver to a wholesaler/buyer."""

    __tablename__ = "rt_loads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str | None] = mapped_column(String(64), index=True)  # the main app's order id
    farmer_id: Mapped[str | None] = mapped_column(String(64))
    farmer_name: Mapped[str] = mapped_column(String(120))
    farmer_phone: Mapped[str | None] = mapped_column(String(20))
    buyer_id: Mapped[str | None] = mapped_column(String(64))
    buyer_name: Mapped[str] = mapped_column(String(120))
    buyer_phone: Mapped[str | None] = mapped_column(String(20))
    crop: Mapped[str] = mapped_column(String(60))
    weight_kg: Mapped[float] = mapped_column(Float)
    needs_cold: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)  # 0 normal, 1 urgent, 2 crop rescue
    pickup_lat: Mapped[float] = mapped_column(Float)
    pickup_lng: Mapped[float] = mapped_column(Float)
    pickup_address: Mapped[str] = mapped_column(String(255))
    drop_lat: Mapped[float] = mapped_column(Float)
    drop_lng: Mapped[float] = mapped_column(Float)
    drop_address: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | assigned | picked_up | delivered | cancelled
    estimated_fare: Mapped[float | None] = mapped_column(Float)  # this farmer's share, set when planned
    trip_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_rt_loads_status", "status"),
        # One live delivery per main-app order, even if two requests race.
        Index(
            "ux_rt_loads_active_order",
            "order_id",
            unique=True,
            postgresql_where=text("order_id is not null and status <> 'cancelled'"),
            sqlite_where=text("order_id is not null and status <> 'cancelled'"),
        ),
    )


class RtTrip(Base):
    __tablename__ = "rt_trips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vehicle_id: Mapped[str] = mapped_column(String(64), ForeignKey("rt_vehicles.id"))
    status: Mapped[str] = mapped_column(String(20), default="planned")
    # planned | in_progress | completed | cancelled
    is_backhaul: Mapped[bool] = mapped_column(Boolean, default=False)
    total_distance_km: Mapped[float] = mapped_column(Float, default=0)
    total_duration_min: Mapped[float] = mapped_column(Float, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    routing_source: Mapped[str] = mapped_column(String(20), default="estimate")  # osrm | estimate
    start_lat: Mapped[float] = mapped_column(Float)
    start_lng: Mapped[float] = mapped_column(Float)
    geometry: Mapped[list | None] = mapped_column(JSON)  # [[lat, lng], ...] for drawing the map
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_rt_trips_vehicle_status", "vehicle_id", "status"),)


class RtTripStop(Base):
    __tablename__ = "rt_trip_stops"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trip_id: Mapped[str] = mapped_column(String(36), ForeignKey("rt_trips.id", ondelete="CASCADE"))
    load_id: Mapped[str] = mapped_column(String(36), ForeignKey("rt_loads.id"))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # pickup | drop
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(255))
    leg_distance_km: Mapped[float] = mapped_column(Float)
    leg_duration_min: Mapped[float] = mapped_column(Float)
    planned_arrival_min: Mapped[float] = mapped_column(Float)  # minutes after trip start
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_rt_trip_stops_trip", "trip_id", "seq"),)


class RtLocation(Base):
    """GPS pings sent by the driver's phone."""

    __tablename__ = "rt_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(64), ForeignKey("rt_vehicles.id"))
    trip_id: Mapped[str | None] = mapped_column(String(36))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    speed_kmph: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_rt_locations_vehicle_time", "vehicle_id", "recorded_at"),)


class RtNotification(Base):
    __tablename__ = "rt_notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    vehicle_id: Mapped[str] = mapped_column(String(64), ForeignKey("rt_vehicles.id"))
    kind: Mapped[str] = mapped_column(String(30))  # backhaul | new_load_nearby
    load_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_rt_notifications_vehicle", "vehicle_id", "is_read"),)
