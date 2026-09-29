"""How much a delivery costs.

Fare depends on the LOAD the truck carries and the vehicle's CURRENT rate:

    fare = road km (farmer -> buyer) x tonnes carried x rate per tonne-km
    (never below MIN_FARE)

The main app sends each vehicle's current rate (`rate_per_ton_km`) when it syncs the
vehicle, so when truck rates change you only update the rate in the main app.
If no rate was sent, a default for that vehicle type is used (see .env.example).

When several farmers share one truck (pooling), each farmer pays only for their own
weight and distance, so small farmers get cheaper transport.
"""
from __future__ import annotations

from .config import settings


def rate_per_ton_km(vehicle) -> float:
    rate = getattr(vehicle, "rate_per_ton_km", None)
    if not rate:
        rate = settings.default_rate_per_ton_km.get(vehicle.vehicle_type, settings.default_rate_per_ton_km["tempo"])
        if vehicle.refrigerated:
            rate *= settings.reefer_multiplier
    return rate


def load_fare(vehicle, road_km: float, weight_kg: float) -> float:
    fare = road_km * (weight_kg / 1000.0) * rate_per_ton_km(vehicle)
    return float(round(max(fare, settings.min_fare)))
