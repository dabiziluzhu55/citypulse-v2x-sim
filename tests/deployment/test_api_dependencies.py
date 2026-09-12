from types import SimpleNamespace
import pytest
from backend.app.api.deps import get_map_service, get_scenario_export_service
from backend.app.core.exceptions import RedisUnavailableAppError, SumoHomeUnavailableError


def request(mode='redis', ready=True):
    state = SimpleNamespace(
        artifacts_ready=True, simulation_manager_mode=mode,
        sumo_home_configured=False, redis_ready=ready,
        simulation_manager_ready=ready, simulation_service=object(),
        map_service=object(), scenario_export_service=object(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_redis_map_does_not_require_sumo_home():
    req = request()
    assert get_map_service(req) is req.app.state.map_service


def test_local_map_still_requires_sumo_home():
    with pytest.raises(SumoHomeUnavailableError):
        get_map_service(request('local'))


@pytest.mark.parametrize('dependency', [get_map_service, get_scenario_export_service])
def test_unavailable_redis_returns_service_error(dependency):
    with pytest.raises(RedisUnavailableAppError):
        dependency(request(ready=False))
