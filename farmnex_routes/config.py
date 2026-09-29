"""All tunable settings for the route optimizer, read from environment variables.

Every value has a sensible default so the package works out of the box for a demo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Database: ROUTES_DATABASE_URL wins, then the main app's DATABASE_URL, then a local SQLite file.
    database_url: str
    auto_create_tables: bool

    # Routing engine: "osrm" (free, real roads) or "haversine" (offline estimate).
    routing_provider: str
    osrm_url: str
    http_timeout_s: float

    # Estimation constants (used when OSRM is unavailable, and for quick scoring).
    road_factor: float          # straight-line km x this = rough road km
    avg_speed_kmph: float       # loaded truck average speed on Maharashtra roads
    truck_time_factor: float    # OSRM gives car times; trucks are slower
    service_minutes: float      # loading / unloading time per stop

    # Pooling, backhaul and notification radii (km).
    pool_radius_km: float
    pool_drop_radius_km: float
    max_loads_per_trip: int
    backhaul_radius_km: float
    new_load_notify_radius_km: float

    # Optimizer.
    exact_max_stops: int        # up to this many stops we search every valid order
    priority_weight: float      # how strongly urgent (Crop Rescue) drops are pulled earlier

    # Tracking.
    gps_stale_seconds: int
    # Optional, e.g. https://api.farmnex.app : used to build tracking links when the app sits
    # behind a proxy that hides https. Empty = derive from the incoming request.
    public_base_url: str = ""

    # Fares. The main app sends each vehicle's CURRENT rate (Rs per tonne per km) when it
    # syncs the vehicle. These defaults are only used if it doesn't.
    default_rate_per_ton_km: dict = field(default_factory=dict)
    reefer_multiplier: float = 1.4
    min_fare: float = 300

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("ROUTES_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "sqlite:///./farmnex_routes_dev.db",
            auto_create_tables=_bool("ROUTES_AUTO_CREATE_TABLES", True),
            routing_provider=os.getenv("ROUTING_PROVIDER", "osrm").lower(),
            osrm_url=os.getenv("OSRM_URL", "https://router.project-osrm.org").rstrip("/"),
            http_timeout_s=_float("ROUTING_HTTP_TIMEOUT_S", 6),
            road_factor=_float("ROAD_FACTOR", 1.35),
            avg_speed_kmph=_float("AVG_TRUCK_SPEED_KMPH", 35),
            truck_time_factor=_float("TRUCK_TIME_FACTOR", 1.25),
            service_minutes=_float("SERVICE_MINUTES_PER_STOP", 15),
            pool_radius_km=_float("POOL_RADIUS_KM", 30),
            pool_drop_radius_km=_float("POOL_DROP_RADIUS_KM", 40),
            max_loads_per_trip=_int("MAX_LOADS_PER_TRIP", 4),
            backhaul_radius_km=_float("BACKHAUL_RADIUS_KM", 30),
            new_load_notify_radius_km=_float("NEW_LOAD_NOTIFY_RADIUS_KM", 25),
            exact_max_stops=_int("EXACT_MAX_STOPS", 10),
            priority_weight=_float("PRIORITY_WEIGHT", 0.15),
            gps_stale_seconds=_int("GPS_STALE_SECONDS", 120),
            public_base_url=os.getenv("ROUTES_PUBLIC_BASE_URL", "").strip().rstrip("/"),
            default_rate_per_ton_km={
                "pickup": _float("DEFAULT_RATE_PICKUP", 12),
                "tempo": _float("DEFAULT_RATE_TEMPO", 10),
                "mini_truck": _float("DEFAULT_RATE_MINI_TRUCK", 8),
                "truck": _float("DEFAULT_RATE_TRUCK", 6),
            },
            reefer_multiplier=_float("REEFER_MULTIPLIER", 1.4),
            min_fare=_float("MIN_FARE", 300),
        )


settings = Settings.from_env()
