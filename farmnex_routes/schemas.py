"""Request / response shapes for the API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Lat = Field(ge=-90, le=90)
Lng = Field(ge=-180, le=180)


class VehicleSync(BaseModel):
    """Sent by the MAIN backend whenever a driver registers or edits a vehicle in the main app."""

    vehicle_number: str = Field(min_length=4, max_length=20, examples=["MH12AB1234"])
    vehicle_type: Literal["pickup", "tempo", "mini_truck", "truck"] = "tempo"
    capacity_kg: float = Field(gt=0, le=40000)
    refrigerated: bool = False
    rate_per_ton_km: float | None = Field(None, gt=0, description="Vehicle's current rate, Rs per tonne per km")
    base_lat: float = Lat
    base_lng: float = Lng
    base_label: str | None = None
    driver_user_id: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    owner_role: Literal["transporter", "farmer"] = "transporter"


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    driver_user_id: str | None
    driver_name: str | None
    driver_phone: str | None
    owner_role: str
    vehicle_number: str
    vehicle_type: str
    capacity_kg: float
    refrigerated: bool
    rate_per_ton_km: float | None
    base_lat: float
    base_lng: float
    base_label: str | None
    status: str
    last_lat: float | None
    last_lng: float | None
    last_seen_at: datetime | None


class VehicleStatusUpdate(BaseModel):
    status: Literal["available", "offline"]


class LoadCreate(BaseModel):
    order_id: str | None = None
    farmer_id: str | None = None
    farmer_name: str
    farmer_phone: str | None = None
    buyer_id: str | None = None
    buyer_name: str
    buyer_phone: str | None = None
    crop: str
    weight_kg: float = Field(gt=0)
    needs_cold: bool = False
    priority: int = Field(0, ge=0, le=2, description="0 normal, 1 urgent, 2 Crop Rescue")
    pickup_lat: float = Lat
    pickup_lng: float = Lng
    pickup_address: str
    drop_lat: float = Lat
    drop_lng: float = Lng
    drop_address: str


class LoadOut(LoadCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    estimated_fare: float | None
    trip_id: str | None
    created_at: datetime
    delivered_at: datetime | None


class LoadCreated(LoadOut):
    already_existed: bool = False


class PlanTripRequest(BaseModel):
    vehicle_id: str
    load_ids: list[str] | None = Field(
        None, description="Leave empty to let the optimizer pool nearby pending loads automatically"
    )


class StopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    seq: int
    kind: str
    load_id: str
    lat: float
    lng: float
    label: str
    leg_distance_km: float
    leg_duration_min: float
    planned_arrival_min: float
    status: str
    done_at: datetime | None


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    vehicle_id: str
    status: str
    is_backhaul: bool
    total_distance_km: float
    total_duration_min: float
    estimated_cost: float
    routing_source: str
    start_lat: float
    start_lng: float
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    stops: list[StopOut] = []
    geometry: list | None = None
    tracking_url: str | None = None


class LocationPing(BaseModel):
    lat: float = Lat
    lng: float = Lng
    speed_kmph: float | None = Field(None, ge=0)
    heading: float | None = Field(None, ge=0, le=360)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    vehicle_id: str
    kind: str
    load_id: str | None
    title: str
    message: str
    payload: dict | None
    is_read: bool
    created_at: datetime
