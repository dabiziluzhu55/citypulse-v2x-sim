import random
from simulation.sumo.building.build_traffic import _route_contains

def test_route_matching_equivalent_for_empty_repeated_and_missing_edges():
    rng=random.Random(42)
    for _ in range(5000):
        route=tuple(rng.choice('abcdef') for _ in range(rng.randrange(60)))
        path=tuple(rng.choice('abcdefz') for _ in range(rng.randrange(8)))
        expected=any(tuple(route[i:i+len(path)])==path for i in range(len(route)-len(path)+1))
        assert _route_contains(route,path)==expected
        assert _route_contains(list(route),list(path))==expected
