"""Generate public browser configuration from an explicit allowlist only."""
import json
import os
from pathlib import Path

PUBLIC_KEYS = {
    "baiduMapAk": "CITYPULSE_BROWSER_BAIDU_AK",
    "tiandituToken": "CITYPULSE_BROWSER_TIANDITU_TOKEN",
    "cartoBasemapKey": "CITYPULSE_BROWSER_CARTO_KEY",
    "amapMapKey": "CITYPULSE_BROWSER_AMAP_KEY",
}


def render(environ):
    values = {}
    for key, variable in PUBLIC_KEYS.items():
        value = environ.get(variable, "")
        if environ.get(variable + "_FILE"):
            if value:
                raise ValueError(f"Specify only {variable} or {variable}_FILE")
            value = Path(environ[variable + "_FILE"]).read_text(encoding="utf-8")
        values[key] = value.strip()
    # JSON escaping also handles quotes and newlines; no shell interpolation.
    return "window.__CITYPULSE_CONFIG__ = " + json.dumps(values, ensure_ascii=True) + ";\n"


if __name__ == "__main__":
    output = Path(os.environ.get("CITYPULSE_RUNTIME_CONFIG_PATH", "/usr/share/nginx/html/runtime-config.js"))
    temporary = output.with_suffix(".tmp")
    temporary.write_text(render(os.environ), encoding="utf-8")
    temporary.replace(output)
