"""Distances, travel times and road geometry.

Uses the free OSRM routing engine (real Indian road network from OpenStreetMap,
no API key). If OSRM can't be reached, everything falls back to a straight-line
estimate so the demo never breaks.
"""
from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Sequence

import httpx

from .config import settings

log = logging.getLogger("farmnex_routes.geo")

Point = tuple[float, float]  # (lat, lng)


def haversine_km(a: Point, b: Point) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def estimate_road_km(a: Point, b: Point) -> float:
    return haversine_km(a, b) * settings.road_factor


def estimate_minutes(road_km: float) -> float:
    return road_km / settings.avg_speed_kmph * 60


def _coords(points: Sequence[Point]) -> str:
    return ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)


def _use_osrm() -> bool:
    return settings.routing_provider == "osrm"


def _estimate_matrix(points: Sequence[Point]):
    n = len(points)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                km = estimate_road_km(points[i], points[j])
                dist[i][j] = km
                dur[i][j] = estimate_minutes(km)
    return dist, dur


def distance_matrix(points: Sequence[Point]):
    """Return (distance_km[i][j], duration_min[i][j], source)."""
    if _use_osrm() and 2 <= len(points) <= 100:
        try:
            r = httpx.get(
                f"{settings.osrm_url}/table/v1/driving/{_coords(points)}",
                params={"annotations": "distance,duration"},
                timeout=settings.http_timeout_s,
            )
            data = r.json()
            if data.get("code") == "Ok":
                est_d, est_t = _estimate_matrix(points)
                n = len(points)
                dist = [[0.0] * n for _ in range(n)]
                dur = [[0.0] * n for _ in range(n)]
                for i in range(n):
                    for j in range(n):
                        d = data["distances"][i][j]
                        t = data["durations"][i][j]
                        dist[i][j] = d / 1000 if d is not None else est_d[i][j]
                        dur[i][j] = t / 60 * settings.truck_time_factor if t is not None else est_t[i][j]
                return dist, dur, "osrm"
            log.warning("OSRM table returned %s, using estimate", data.get("code"))
        except Exception as exc:  # network down, timeout, rate limit...
            log.warning("OSRM table failed (%s), using estimate", exc)
    dist, dur = _estimate_matrix(points)
    return dist, dur, "estimate"


def route_geometry(points: Sequence[Point]):
    """Road polyline through the points in order. Returns ([[lat, lng], ...], source)."""
    if _use_osrm() and len(points) >= 2:
        try:
            r = httpx.get(
                f"{settings.osrm_url}/route/v1/driving/{_coords(points)}",
                params={"overview": "full", "geometries": "geojson"},
                timeout=settings.http_timeout_s,
            )
            data = r.json()
            if data.get("code") == "Ok":
                coords = data["routes"][0]["geometry"]["coordinates"]
                return [[round(lat, 6), round(lng, 6)] for lng, lat in coords], "osrm"
        except Exception as exc:
            log.warning("OSRM route failed (%s), using straight lines", exc)
    return [[lat, lng] for lat, lng in points], "estimate"


@lru_cache(maxsize=1024)
def _leg_cached(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> tuple[float, float]:
    a, b = (a_lat, a_lng), (b_lat, b_lng)
    if _use_osrm():
        try:
            r = httpx.get(
                f"{settings.osrm_url}/route/v1/driving/{_coords([a, b])}",
                params={"overview": "false"},
                timeout=settings.http_timeout_s,
            )
            data = r.json()
            if data.get("code") == "Ok":
                route = data["routes"][0]
                return route["distance"] / 1000, route["duration"] / 60 * settings.truck_time_factor
        except Exception as exc:
            log.warning("OSRM leg failed (%s), using estimate", exc)
    km = estimate_road_km(a, b)
    return km, estimate_minutes(km)


def leg(a: Point, b: Point) -> tuple[float, float]:
    """(road_km, minutes) for a single A->B leg. Rounded + cached so live tracking
    polls don't hammer the routing server."""
    return _leg_cached(round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3))
