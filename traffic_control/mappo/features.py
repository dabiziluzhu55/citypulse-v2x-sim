"""部署版 MAPPO 特征构建适配器。

与算法端 ``algorithms/mappo/features.IPPOV8FeatureBuilder`` 同接口：直接复用
已解耦的 ``traffic_control.ippo.controller.StateBuilder``（get_all_states /
build_phase_features / build_action_mask / get_phase_order / max_state_dim /
max_phases），不再依赖 ``algorithms``。
"""

from __future__ import annotations

from traffic_control.ippo.controller import StateBuilder as IPPOV8FeatureBuilder


CENTRALIZED_STATE_SCHEMA = "centralized_local_obs_pool_v1"
IPPO_V8_LOCAL_OBSERVATION_SCHEMA = "ippo_v8_local_obs_112_plus_identity_v1"
IPPO_V8_IDENTITY_OFFSET = 9


__all__ = [
    "CENTRALIZED_STATE_SCHEMA",
    "IPPO_V8_LOCAL_OBSERVATION_SCHEMA",
    "IPPO_V8_IDENTITY_OFFSET",
    "IPPOV8FeatureBuilder",
]
