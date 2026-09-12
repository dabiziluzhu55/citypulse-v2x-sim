"""Runtime browser config must not publish unrelated server credentials."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('browser_config', ROOT / 'frontend/docker/write_runtime_config.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_public_allowlist_and_json_escaping(tmp_path):
    key = tmp_path / 'browser_key'
    key.write_text('example-"quoted"\nvalue', encoding='utf-8')
    rendered = module.render({
        'CITYPULSE_BROWSER_BAIDU_AK_FILE': str(key),
        'CITYPULSE_LLM_API_KEY': 'server-secret-not-public',
        'CITYPULSE_REDIS_STATE_URL': 'redis://user:password@redis/1',
    })
    values = json.loads(rendered.removeprefix('window.__CITYPULSE_CONFIG__ = ').rstrip(';\n'))
    assert values['baiduMapAk'] == 'example-"quoted"\nvalue'
    assert set(values) == set(module.PUBLIC_KEYS)
    assert 'server-secret' not in rendered
    assert 'password' not in rendered


def test_environment_change_requires_no_rebuild():
    assert module.render({'CITYPULSE_BROWSER_BAIDU_AK': 'first'}) != module.render({'CITYPULSE_BROWSER_BAIDU_AK': 'second'})


def test_settings_load_mounted_secret(tmp_path):
    from backend.app.core.config import Settings
    (tmp_path / 'citypulse_llm_api_key').write_text('server-only-example', encoding='utf-8')
    settings = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert settings.llm_api_key == 'server-only-example'
