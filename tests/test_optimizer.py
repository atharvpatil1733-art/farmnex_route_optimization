import itertools
import math

import pytest

from farmnex_routes.optimizer import InfeasiblePlan, LoadSpec, plan_sequence

START = (18.76, 73.86)  # Chakan
MARKET = (18.487, 73.866)  # Pune Market Yard


def _check_valid(plan, loads, capacity):
    seen, onboard = set(), 0.0
    weights = {l.load_id: l.weight_kg for l in loads}
    for s in plan.stops:
        if s.kind == "pickup":
            onboard += weights[s.load_id]
        else:
            assert s.load_id in seen, "drop before pickup"
            onboard -= weights[s.load_id]
        assert onboard <= capacity + 1e-6, "capacity exceeded"
        seen.add(s.load_id)
    assert len(plan.stops) == 2 * len(loads)


def test_single_load_is_pickup_then_drop():
    loads = [LoadSpec("a", (18.77, 73.84), MARKET, 500)]
    plan = plan_sequence(START, loads, 1000)
    assert [s.kind for s in plan.stops] == ["pickup", "drop"]
    assert plan.total_distance_km > 0 and plan.exact


def test_precedence_and_capacity_respected():
    loads = [
        LoadSpec("a", (18.735, 73.675), MARKET, 800),
        LoadSpec("b", (18.77, 73.84), MARKET, 900),
        LoadSpec("c", (18.915, 73.895), MARKET, 600),
    ]
    plan = plan_sequence(START, loads, 1500)  # cannot carry all three at once
    _check_valid(plan, loads, 1500)


def test_exact_search_matches_brute_force():
    loads = [
        LoadSpec("a", (18.80, 73.70), (18.50, 73.90), 300),
        LoadSpec("b", (18.70, 73.95), (18.45, 73.80), 300),
        LoadSpec("c", (18.90, 73.85), (18.55, 73.75), 300),
    ]
    plan = plan_sequence(START, loads, 1000, service_min=0)

    # Brute force every valid order with the same cost function.
    from farmnex_routes.geo import distance_matrix

    pts = [START] + [p for l in loads for p in (l.pickup, l.drop)]
    _, dur, _ = distance_matrix(pts)
    best = math.inf
    for perm in itertools.permutations(range(6)):
        if any(perm.index(2 * k) > perm.index(2 * k + 1) for k in range(3)):
            continue
        t, cur = 0.0, 0
        for i in perm:
            t += dur[cur][i + 1]
            cur = i + 1
        best = min(best, t)
    assert plan.driving_minutes == pytest.approx(best, abs=0.2)


def test_crop_rescue_priority_delivered_earlier():
    # Two buyers in opposite directions; the normal one is slightly closer.
    def loads(p):
        return [
            LoadSpec("normal", (18.761, 73.861), (18.76, 73.63), 200, priority=0),
            LoadSpec("urgent", (18.759, 73.859), (18.76, 74.10), 200, priority=p),
        ]

    plain = plan_sequence(START, loads(0), 1000)
    assert [s.load_id for s in plain.stops if s.kind == "drop"][0] == "normal"
    rescue = plan_sequence(START, loads(2), 1000)
    assert [s.load_id for s in rescue.stops if s.kind == "drop"][0] == "urgent"


def test_heuristic_path_for_many_loads():
    loads = [LoadSpec(f"l{i}", (18.7 + i * 0.02, 73.8 + i * 0.01), MARKET, 100) for i in range(7)]
    plan = plan_sequence(START, loads, 400)  # 14 stops -> heuristic
    assert not plan.exact
    _check_valid(plan, loads, 400)


def test_overweight_load_rejected():
    with pytest.raises(InfeasiblePlan):
        plan_sequence(START, [LoadSpec("x", START, MARKET, 5000)], 1000)
