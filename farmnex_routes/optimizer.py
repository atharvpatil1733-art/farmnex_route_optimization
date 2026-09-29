"""Pickup-and-delivery route optimization for one vehicle.

Problem: a truck starts where it is, must collect every load from its farmer and
drop it at its wholesaler. Rules:
  * a load can only be dropped after it has been picked up (precedence)
  * the truck can never carry more than its capacity at any moment
  * urgent loads (Crop Rescue, priority 2) should reach the buyer earlier

Objective (minutes) = total driving time + loading time
                      + priority_weight x priority x arrival time of each urgent drop

For up to EXACT_MAX_STOPS stops (default 10 = 5 loads) we run a branch-and-bound
search over every valid order, so the answer is the true optimum. Beyond that we
use greedy nearest-feasible-stop + relocate local search.

This file is pure Python with no database code, so it is easy to unit test and
explain to judges.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .config import settings
from .geo import Point, distance_matrix

EPS = 1e-6


@dataclass
class LoadSpec:
    load_id: str
    pickup: Point
    drop: Point
    weight_kg: float
    priority: int = 0
    pickup_label: str = ""
    drop_label: str = ""


@dataclass
class PlannedStop:
    load_id: str
    kind: str  # pickup | drop
    point: Point
    label: str
    leg_distance_km: float
    leg_duration_min: float
    arrival_min: float  # minutes after the trip starts
    onboard_after_kg: float


@dataclass
class PlanResult:
    stops: list[PlannedStop]
    total_distance_km: float
    driving_minutes: float
    total_duration_min: float  # driving + loading/unloading
    objective: float
    source: str  # osrm | estimate
    exact: bool


class InfeasiblePlan(ValueError):
    pass


def plan_sequence(
    start: Point,
    loads: Sequence[LoadSpec],
    capacity_kg: float,
    service_min: float | None = None,
    matrix=None,
) -> PlanResult:
    if not loads:
        raise InfeasiblePlan("No loads to plan")
    service = settings.service_minutes if service_min is None else service_min
    pw = settings.priority_weight
    for l in loads:
        if l.weight_kg > capacity_kg + EPS:
            raise InfeasiblePlan(f"Load {l.load_id} ({l.weight_kg} kg) exceeds vehicle capacity {capacity_kg} kg")

    # Stop index i: even = pickup of load i//2, odd = drop of load i//2. Matrix node = i + 1 (0 is start).
    n = 2 * len(loads)
    points: list[Point] = [start]
    for l in loads:
        points += [l.pickup, l.drop]
    dist, dur, source = matrix if matrix is not None else distance_matrix(points)

    def load_of(i: int) -> LoadSpec:
        return loads[i // 2]

    def evaluate(seq: Sequence[int]) -> float:
        t = cost = onboard = 0.0
        cur = 0
        seen: set[int] = set()
        for i in seq:
            l = load_of(i)
            if i % 2 == 0:
                if onboard + l.weight_kg > capacity_kg + EPS:
                    return math.inf
                onboard += l.weight_kg
            else:
                if i - 1 not in seen:
                    return math.inf
                onboard -= l.weight_kg
            t += dur[cur][i + 1]
            if i % 2 == 1:
                cost += pw * l.priority * t
            t += service
            seen.add(i)
            cur = i + 1
        return t + cost

    exact = n <= settings.exact_max_stops
    best_seq: list[int] | None = None

    if exact:
        best = [math.inf]
        holder: list[list[int]] = []
        visited = [False] * n
        seq: list[int] = []

        def dfs(cur: int, t: float, cost: float, onboard: float) -> None:
            if t + cost >= best[0]:
                return  # already worse than the best full route: prune
            if len(seq) == n:
                best[0] = t + cost
                holder[:] = [list(seq)]
                return
            for i in sorted((i for i in range(n) if not visited[i]), key=lambda i: dur[cur][i + 1]):
                l = load_of(i)
                if i % 2 == 0:
                    if onboard + l.weight_kg > capacity_kg + EPS:
                        continue
                    nb = onboard + l.weight_kg
                else:
                    if not visited[i - 1]:
                        continue
                    nb = onboard - l.weight_kg
                arrive = t + dur[cur][i + 1]
                nc = cost + (pw * l.priority * arrive if i % 2 == 1 else 0.0)
                visited[i] = True
                seq.append(i)
                dfs(i + 1, arrive + service, nc, nb)
                seq.pop()
                visited[i] = False

        dfs(0, 0.0, 0.0, 0.0)
        best_seq = holder[0] if holder else None
    else:
        best_seq = _greedy(n, dur, load_of, capacity_kg)
        if best_seq is not None:
            best_seq = _relocate_search(best_seq, evaluate)

    if best_seq is None or math.isinf(evaluate(best_seq)):
        raise InfeasiblePlan("No order of stops fits within the vehicle capacity")

    # Build the detailed plan.
    stops: list[PlannedStop] = []
    t = total_km = drive = onboard = 0.0
    cur = 0
    for i in best_seq:
        l = load_of(i)
        kind = "pickup" if i % 2 == 0 else "drop"
        leg_km, leg_min = dist[cur][i + 1], dur[cur][i + 1]
        t += leg_min
        total_km += leg_km
        drive += leg_min
        onboard += l.weight_kg if kind == "pickup" else -l.weight_kg
        stops.append(
            PlannedStop(
                load_id=l.load_id,
                kind=kind,
                point=l.pickup if kind == "pickup" else l.drop,
                label=l.pickup_label if kind == "pickup" else l.drop_label,
                leg_distance_km=round(leg_km, 2),
                leg_duration_min=round(leg_min, 1),
                arrival_min=round(t, 1),
                onboard_after_kg=round(max(onboard, 0.0), 1),
            )
        )
        t += service
        cur = i + 1

    return PlanResult(
        stops=stops,
        total_distance_km=round(total_km, 2),
        driving_minutes=round(drive, 1),
        total_duration_min=round(t, 1),
        objective=round(evaluate(best_seq), 2),
        source=source,
        exact=exact,
    )


def _greedy(n, dur, load_of, capacity_kg) -> list[int] | None:
    visited = [False] * n
    seq: list[int] = []
    cur, onboard = 0, 0.0
    for _ in range(n):
        choice, choice_t = None, math.inf
        for i in range(n):
            if visited[i]:
                continue
            w = load_of(i).weight_kg
            if i % 2 == 0 and onboard + w > capacity_kg + EPS:
                continue
            if i % 2 == 1 and not visited[i - 1]:
                continue
            # Deliveries get a small preference so the truck frees space and hands over produce sooner.
            t = dur[cur][i + 1] * (0.9 if i % 2 == 1 else 1.0)
            if t < choice_t:
                choice, choice_t = i, t
        if choice is None:
            return None
        visited[choice] = True
        seq.append(choice)
        onboard += load_of(choice).weight_kg if choice % 2 == 0 else -load_of(choice).weight_kg
        cur = choice + 1
    return seq


def _relocate_search(seq: list[int], evaluate) -> list[int]:
    best, best_cost = list(seq), evaluate(seq)
    improved = True
    while improved:
        improved = False
        for i in range(len(best)):
            for j in range(len(best)):
                if i == j:
                    continue
                cand = list(best)
                item = cand.pop(i)
                cand.insert(j, item)
                c = evaluate(cand)
                if c + EPS < best_cost:
                    best, best_cost, improved = cand, c, True
    return best
