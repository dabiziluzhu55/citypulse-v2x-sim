"""从会话/交通清单解析车辆 type_id -> powertrain"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


def load_powertrain_by_type(
    *,
    session_dir: Path | None = None,
    traffic_manifest_path: Path | None = None,
) -> tuple[dict[str, str], list[str]]:
    """返回 (type_id -> powertrain, warnings)。

    优先使用 session_manifest 的 vehicle_type_profiles，再回退到 traffic_manifest。
    powertrain 取自 traffic_manifest.vehicle_profiles。
    """

    warnings: list[str] = []
    profiles = _load_vehicle_profiles(traffic_manifest_path, warnings)
    if not profiles:
        return {}, warnings

    type_to_profile = _load_type_profiles(session_dir, traffic_manifest_path, warnings)
    if not type_to_profile:
        warnings.append("缺少 vehicle_type_profiles，燃油强度不可计算。")
        return {}, warnings

    powertrain_by_type: dict[str, str] = {}
    for type_id, profile_id in type_to_profile.items():
        profile = profiles.get(profile_id)
        if profile is None:
            warnings.append(
                f"车辆类型 {type_id!r} 引用未知 profile {profile_id!r}，燃油强度不可计算。"
            )
            return {}, warnings
        powertrain = str(profile.get("powertrain", "")).lower()
        if not powertrain:
            warnings.append(
                f"车辆类型 {type_id!r} 缺少 powertrain，燃油强度不可计算。"
            )
            return {}, warnings
        powertrain_by_type[str(type_id)] = powertrain
    return powertrain_by_type, warnings


def _load_json(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 JSON 失败 %s: %s", path, exc)
        return None
    return raw if isinstance(raw, Mapping) else None


def _load_vehicle_profiles(
    traffic_manifest_path: Path | None,
    warnings: list[str],
) -> dict[str, Mapping[str, Any]]:
    if traffic_manifest_path is None or not traffic_manifest_path.is_file():
        warnings.append("缺少 traffic_manifest，无法解析车辆 powertrain。")
        return {}
    payload = _load_json(traffic_manifest_path)
    if payload is None:
        warnings.append("traffic_manifest 解析失败，燃油强度不可计算。")
        return {}
    raw_profiles = payload.get("vehicle_profiles", {})
    if not isinstance(raw_profiles, Mapping) or not raw_profiles:
        warnings.append("traffic_manifest 缺少 vehicle_profiles，燃油强度不可计算。")
        return {}
    return {
        str(profile_id): item
        for profile_id, item in raw_profiles.items()
        if isinstance(item, Mapping)
    }


def _load_type_profiles(
    session_dir: Path | None,
    traffic_manifest_path: Path | None,
    warnings: list[str],
) -> dict[str, str]:
    if session_dir is not None:
        session_manifest = session_dir / "session_manifest.json"
        if session_manifest.is_file():
            payload = _load_json(session_manifest)
            if payload is not None:
                mapping = payload.get("vehicle_type_profiles", {})
                if isinstance(mapping, Mapping) and mapping:
                    return {
                        str(type_id): str(profile_id)
                        for type_id, profile_id in mapping.items()
                    }

    if traffic_manifest_path is not None and traffic_manifest_path.is_file():
        payload = _load_json(traffic_manifest_path)
        if payload is not None:
            mapping = payload.get("vehicle_type_profiles", {})
            if isinstance(mapping, Mapping) and mapping:
                return {
                    str(type_id): str(profile_id)
                    for type_id, profile_id in mapping.items()
                }
    return {}
