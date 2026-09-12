"""Exercise Redis API startup and exports while forbidding all SUMO/kernel imports.

No Redis service is needed: unavailable Redis must produce a degraded API, never
an implicit local simulation. The map/export paths are also exercised directly.
"""
import importlib.abc
import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['SIMULATION_MANAGER_MODE'] = 'redis'
os.environ['CITYPULSE_ENV_FILE'] = ''
os.environ.pop('SUMO_HOME', None)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--imports-only', action='store_true')
parser.add_argument('--redis-url', help='Optional isolated Redis instance for healthy API integration')
args = parser.parse_args()
if args.redis_url:
    os.environ['CITYPULSE_REDIS_STATE_URL'] = args.redis_url
    os.environ['CITYPULSE_REDIS_KEY_PREFIX'] = 'citypulse-isolation-validation'


class NoKernel(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'simulation', 'libsumo', 'sumolib', 'traci'}:
            raise AssertionError(f'Backend attempted forbidden import: {fullname}')


sys.meta_path.insert(0, NoKernel())

from fastapi.testclient import TestClient
from unittest.mock import patch
from backend.app.main import create_app
from backend.app.core.config import Settings, get_settings
from backend.app.services.map_service import MapService
from backend.app.services.scenario_export_service import ScenarioExportService
from simulation_protocol.catalog import load_catalog
from simulation_protocol.scenario import compile_session_scenario, ScenarioCompilationError

with tempfile.TemporaryDirectory() as directory:
    os.environ['SUMO_SESSION_ROOT'] = directory
    get_settings.cache_clear()
    with patch('backend.app.main.probe_redis_manager', return_value=(False, 'isolated test')):
        with TestClient(create_app()) as client:
            result = client.get('/api/v1/health')
            assert result.status_code == 200, result.text
            payload = result.json()
            assert payload['simulation_manager_mode'] == 'redis'
            assert payload['status'] == 'degraded'
    if args.imports_only:
        print('PASS: real Redis-mode lifespan without SUMO imports; unavailable Redis remains degraded')
        sys.exit(0)
    if args.redis_url:
        with TestClient(create_app()) as client:
            health = client.get('/api/v1/health').json()
            assert health['status'] == 'ok', health
            for n in range(1, 21):
                response = client.get(f'/api/v1/maps/demo_{n}/geojson')
                assert response.status_code == 200, response.text
            print('PASS: healthy Redis API and 20 HTTP map responses')
    settings = Settings(_env_file=None, simulation_manager_mode='redis', sumo_session_root=directory)
    maps = MapService(settings, None)
    lon, lat = maps.xy_to_lonlat(0, 0)
    assert lon is not None and lat is not None
    assert maps.lane_center_lonlat('missing') == (None, None)
    catalog = load_catalog(settings.generated_dir)
    mapped = 0
    for identifier in catalog.intersections:
        if (settings.generated_dir / 'geojson' / f'{identifier}.roads.wgs84.geojson').is_file():
            maps.get_geojson(identifier, 600)
            mapped += 1
    assert mapped == 20, f'Expected 20 pre-generated maps, found {mapped}; run build_map_artifacts.py in the SUMO environment'
    # An invalid compile request must reach pure validation without importing a kernel.
    try:
        compile_session_scenario('isolation-test', (), 'invalid-period', generated_dir=settings.generated_dir, session_root=Path(directory))
    except ScenarioCompilationError:
        pass
    else:
        raise AssertionError('Invalid scenario was accepted')
    # Exercise successful scenario export, including zip generation, without a kernel.
    from backend.app.schemas.simulations import StartSimulationRequest
    from types import SimpleNamespace
    import io
    import zipfile
    service = ScenarioExportService(settings, SimpleNamespace(catalog=lambda: catalog))
    request = StartSimulationRequest(scenario_preset_id='xiongan_20', period='morning_peak', duration_seconds=30)
    filename, contents = service.export_zip(request)
    with zipfile.ZipFile(io.BytesIO(contents)) as bundle:
        assert any(name.endswith('.sumocfg') for name in bundle.namelist())
    print(f'PASS: pure scenario export {filename}, {len(contents)} bytes')
    assert not any(name.split('.')[0] in {'simulation', 'libsumo', 'sumolib', 'traci'} for name in sys.modules)
    print(f'PASS: Redis lifespan, health, map projection guards and {mapped} GeoJSON responses; no SUMO/kernel modules loaded')
