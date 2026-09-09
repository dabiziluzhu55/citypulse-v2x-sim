"""Protocol adapter for Cloud/Vehicle actors with an unchanged IPPO Road.

This module never creates a simulator or starts training. Configure it before
an externally managed episode; finalize its exported data after tripinfo closes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path

import torch

from .contracts import CALLBACK_INTERVAL, HORIZON, PERIODS, SCHEMA_VERSION, vehicle_observation
from .road.hooks import FrozenRoadHooks
from .communication.transport import MessageBus, PermissionBook, digest, jsonable
from .vehicle.feedback import EpisodeBuffer
from traffic_control.cov2x.communication.bridge import CVJointV1EventBridge

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
IPPO_CHECKPOINT = REPO / "traffic_control/ippo/models/ippo_v8_20tls_ep160.pt"
IPPO_SHA256 = "4055ec30bcd03c65572720cea38e51a338f466c351e21124be5fa683e6339449"
CANONICAL_TOPOLOGY = REPO / "traffic_control/cov2x/models/cv_joint_v1_canonical_topology.json"
_config = {}
_state = {}
_event_sink = None
_bridge = None
_ippo_environment_before = None


def _load_ippo():
    defaults = {"IPPO_MODE": "model", "IPPO_JOINT_ROAD": "off",
                "IPPO_MODEL_PATH": str(IPPO_CHECKPOINT), "IPPO_ACTION_INTERVAL": "15",
                "IPPO_MAX_GREEN_FACTOR": "2", "IPPO_EFFECTIVE_DEMAND": "on"}
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    # Product IPPO resolves aliases before IPPO_MODEL_PATH; clear the
    # caller's alias while this candidate enforces its pinned path.
    os.environ.pop("IPPO_MODEL_ALIAS", None)
    if os.environ["IPPO_MODE"].lower() != "model":
        raise ValueError("Road must use frozen IPPO model mode")
    if os.environ["IPPO_JOINT_ROAD"].lower() not in ("off", "0", "false", ""):
        raise ValueError("joint Road correction is forbidden")
    path = Path(os.environ["IPPO_MODEL_PATH"]).resolve()
    if path != IPPO_CHECKPOINT or hashlib.sha256(path.read_bytes()).hexdigest() != IPPO_SHA256:
        raise ValueError("frozen IPPO checkpoint identity changed")
    from traffic_control.ippo import controller as ic
    return ic


def _lane_vclass_from_payload(payload):
    result = {}
    for lanes in (payload.get("edge_lanes", {}) or {}).values():
        if isinstance(lanes, dict):
            values = lanes.values()
        else:
            values = lanes or ()
        for item in values:
            if not isinstance(item, dict):
                continue
            lane_id = item.get("lane_id")
            if lane_id is None:
                continue
            allowed = item.get("allowed_vehicle_classes", item.get("allowed", ()))
            disallowed = item.get("disallowed_vehicle_classes", item.get("disallowed", ()))
            if isinstance(allowed, str):
                allowed = tuple(allowed.split())
            if isinstance(disallowed, str):
                disallowed = tuple(disallowed.split())
            result[str(lane_id)] = (tuple(allowed or ()), tuple(disallowed or ()))
    return result


def _make_builder(payload):
    from .observations import FeatureBuilder
    return FeatureBuilder(
        canonical=CANONICAL_TOPOLOGY,
        lane_vclass=_lane_vclass_from_payload(payload),
    )


def _new_policy(n_movements, seed):
    from .model import JointPolicy
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(int(seed))
        return JointPolicy(n_movements)


def _model_version(policy):
    from .model import policy_version
    return policy_version(policy)


def _make_rng(seed):
    from .model import SamplingRNG
    return SamplingRNG(int(seed))


def _source_version():
    return hashlib.sha256("".join(str(p.relative_to(HERE)) + hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(HERE.rglob("*.py"))
                                  if not p.name.startswith(('.', 'test_'))).encode()).hexdigest()


def configure(policy=None, *, model_seed=0, sampling_seed=0, training=False,
              period, trace_path=None):
    if _state.get("active"):
        raise RuntimeError("cannot reconfigure an active episode")
    if period not in PERIODS:
        raise ValueError("unknown period")
    if training:
        raise ValueError("cv_joint_v1 deployment is inference-only")
    _config.clear()
    _config.update(policy=policy, model_seed=int(model_seed), sampling_seed=int(sampling_seed),
                   training=False, period=period, trace_path=trace_path)


def _metadata(ic):
    return {"action_interval": float(ic._action_interval),
            "decision_interval": float(ic._decision_interval),
            "minimum_green": float(ic._minimum_green),
            "max_green_factor": float(ic._max_green_factor),
            "effective_demand": bool(ic._effective_demand_enabled),
            "phase_orders": deepcopy(ic._phase_orders),
            "obs_dim": int(ic._obs_dim), "act_dim": int(ic._act_dim)}


def _restore_ippo_environment():
    global _ippo_environment_before
    if _ippo_environment_before is None:
        return
    for key, value in _ippo_environment_before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _ippo_environment_before = None


def set_v2x_event_sink(sink):
    global _event_sink
    if sink is not None and not callable(sink) and not callable(getattr(sink, "emit", None)):
        raise TypeError("V2X event sink must be callable or provide emit()")
    _event_sink = sink
    bridge = _state.get("bridge") or _bridge
    if bridge is not None:
        bridge.set_event_sink(sink)


def drain_v2x_events():
    bridge = _state.get("bridge") or _bridge
    if bridge is None:
        raise RuntimeError("CV Joint V1 is not initialized")
    return bridge.drain()


def initialize(payload):
    global _ippo_environment_before, _bridge
    if not _config:
        raise RuntimeError("configure the CV controller before initialize")
    if _state.get("active"):
        finish({"reason": "reinitialized", "simulation_time": _state.get("now", 0.)})
    _state.clear()
    _state.update(active=False, hooks=None, trace_file=None, now=0., last_step=-1,
                  ippo_initialized=False, inline_sequence=0)
    _ippo_environment_before = {
        key: os.environ.get(key)
        for key in (
            "IPPO_MODE", "IPPO_JOINT_ROAD", "IPPO_MODEL_ALIAS",
            "IPPO_MODEL_PATH", "IPPO_ACTION_INTERVAL", "IPPO_MAX_GREEN_FACTOR",
            "IPPO_EFFECTIVE_DEMAND",
        )
    }
    try:
        if payload.get("period", _config["period"]) != _config["period"]:
            raise ValueError("initialization period differs from configured period")
        ic = _load_ippo(); _state["ic"] = ic
        response = ic.initialize(dict(payload))
        _state["ippo_initialized"] = True
        for parameter in ic._model.parameters():
            parameter.requires_grad_(False)
        ic._model.eval()
        metadata = _metadata(ic)
        expected = {"action_interval": 15., "decision_interval": 5., "minimum_green": 5.,
                    "max_green_factor": 2., "effective_demand": True, "obs_dim": 132, "act_dim": 4}
        if any(metadata[key] != value for key, value in expected.items()):
            raise ValueError("frozen IPPO runtime configuration changed")
        builder = _make_builder(payload)
        if not builder.movement_keys:
            raise ValueError("empty controlled movement catalog")
        policy = _config["policy"]
        if policy is None:
            policy = _new_policy(len(builder.movement_keys), _config["model_seed"])
        if policy.n_movements != len(builder.movement_keys):
            raise ValueError("policy movement catalog size mismatch")
        policy.eval()
        weight_version = _model_version(policy)
        source_version = _source_version()
        catalog = [key.token for key in builder.movement_keys]
        version = digest({"weights": weight_version, "catalog": catalog,
                          "schema": SCHEMA_VERSION, "source": source_version,
                          "road": IPPO_SHA256, "speed_fraction": .10})
        episode_id = str(payload["episode_id"])
        buffer = EpisodeBuffer(episode_id, version, _config["period"], seed=payload.get("seed"))
        bus = MessageBus(episode_id)
        bridge = CVJointV1EventBridge(episode_id, event_sink=_event_sink)
        _bridge = bridge
        hooks = FrozenRoadHooks(ic)
        _state.update(episode_id=episode_id, policy=policy, builder=builder,
                      collection_mode="sampled" if _config["training"] else "deterministic",
                      model_seed=_config["model_seed"], sampling_seed=_config["sampling_seed"],
                      weight_version=weight_version, source_version=source_version,
                      policy_version=version, catalog=catalog, buffer=buffer,
                      bus=bus, bridge=bridge, permissions=PermissionBook(bus), hooks=hooks,
                      rng=_make_rng(_config["sampling_seed"]), road_margins={}, active_caps={},
                      metadata=metadata, feedback={}, trace_hash=hashlib.sha256(),
                      counts={k: 0 for k in ("callbacks", "cloud_decisions", "vehicle_decisions",
                                            "speed_requests", "lane_requests", "native_release_requests",
                                            "held_permission_uses", "vehicle_receipts")})
        hooks.install()
        if _config["trace_path"] is not None:
            path = Path(_config["trace_path"]); path.parent.mkdir(parents=True, exist_ok=True)
            _state["trace_file"] = path.open("x")
        _state["active"] = True
        return response
    except BaseException:
        _cleanup()
        raise


def _cleanup():
    _state["active"] = False
    try:
        if _state.get("ippo_initialized") and _state.get("ic") is not None:
            try:
                _state["ic"].finish({
                    "reason": "controller_cleanup",
                    "simulation_time": _state.get("now", 0.),
                })
            except BaseException:
                pass
            _state["ippo_initialized"] = False
        if _state.get("hooks") is not None:
            _state["hooks"].close()
    finally:
        if _state.get("permissions") is not None:
            _state["permissions"].clear()
        _state.get("active_caps", {}).clear()
        if _state.get("trace_file") is not None:
            _state["trace_file"].close(); _state["trace_file"] = None
        _restore_ippo_environment()


def _send(kind, source, destination, step, now, payload, parents=(), valid_until=None):
    bus = _state["bus"]
    msg = bus.send(kind, source, destination, step, now,
                   now + CALLBACK_INTERVAL if valid_until is None else valid_until,
                   payload, parents=parents)
    _state["bridge"].observe_message(msg, payload)
    received = bus.consume(msg, destination, now)
    return msg, received


def _trace(payload, response, input_hash):
    ic = _state["ic"]
    item = {"time": _state["now"], "step_id": int(payload["step_id"]),
            "input_sha256": input_hash, "actions": response.get("actions", {}),
            "road_pending": deepcopy(ic._pending_signal_commands),
            "road_execution": deepcopy(ic._signal_execution_stats),
            "counts": dict(_state["counts"])}
    line = json.dumps(jsonable(item), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    _state["trace_hash"].update(line.encode())
    if _state["trace_file"] is not None:
        _state["trace_file"].write(line)


def _step(payload):
    if not _state.get("active"):
        raise RuntimeError("CV controller is not active")
    if str(payload.get("episode_id")) != _state["episode_id"]:
        raise ValueError("cross-episode callback")
    now = float(payload["simulation_time"]); step = int(payload["step_id"])
    if not math.isfinite(now) or now < _state["now"] or step <= _state["last_step"]:
        raise ValueError("non-monotone or duplicate callback")
    _state["now"] = now; _state["last_step"] = step
    counts = _state["counts"]; counts["callbacks"] += 1
    input_hash = digest({k: v for k, v in payload.items() if k != "episode_id"})
    ic = _state["ic"]; buffer = _state["buffer"]; policy = _state["policy"]
    # The Cloud-side recorder consumes the delivered execution-feedback packet.
    feedback_payload = {key: deepcopy(payload.get(key)) for key in
                        ("episode_id", "step_id", "simulation_time", "vehicles", "previous_action_results")}
    _, delivered_feedback = _send("vehicle_feedback", "vehicle", "cloud", step, now, feedback_payload)
    receipts = buffer.observe_receipts(delivered_feedback)
    counts["vehicle_receipts"] += len(receipts)
    if now >= HORIZON:
        response = ic.step(dict(payload))
        _state["active_caps"].clear()
        _trace(payload, response, input_hash)
        bridge = _state["bridge"]
        bridge.sync(_state["bus"].events)
        batch = bridge.inline_batch(
            f"{_state['episode_id']}:{step}",
            after_sequence=_state["inline_sequence"],
        )
        _state["inline_sequence"] = batch["last_sequence"]
        response = deepcopy(response)
        response["v2x"] = batch
        return response

    vehicles = deepcopy(payload.get("vehicles", {}))
    intersections = deepcopy(payload.get("intersections", {}))
    v_to_r, road_input = _send("vehicle_state", "vehicle", "road", step, now, {"vehicles": vehicles})
    v_to_c, cloud_input = _send("vehicle_state", "vehicle", "cloud", step, now, {"vehicles": vehicles})
    if digest(road_input["vehicles"]) != digest(vehicles) or digest(cloud_input["vehicles"]) != digest(vehicles):
        raise ValueError("delivered vehicle data disagrees with the frozen sensor snapshot")
    road_payload = dict(payload); road_payload["vehicles"] = road_input["vehicles"]
    road_observations = ic._state_builder.get_all_states(road_payload)
    # IPPO's private -inf sentinel means no decision yet. On the wire it is
    # represented explicitly as missing, rather than invalid JSON Infinity.
    last_decisions = {key: None if value is not None and float(value) == -math.inf else value
                      for key, value in ic._last_decision_times.items()}
    road_packet = {"intersections": intersections, "last_decisions": last_decisions,
                   "road_margins": dict(_state["road_margins"]), "road_observations": road_observations}
    r_to_v, vehicle_road_input = _send("road_state", "road", "vehicle", step, now, road_packet)
    r_to_c, cloud_road_input = _send("road_state", "road", "cloud", step, now, road_packet)
    if digest(vehicle_road_input) != digest(road_packet) or digest(cloud_road_input) != digest(road_packet):
        raise ValueError("delivered Road data disagrees with the frozen sensor snapshot")
    feature_payload = dict(payload)
    feature_payload["vehicles"] = cloud_input["vehicles"]
    feature_payload["intersections"] = vehicle_road_input["intersections"]
    snapshot = _state["builder"].build(
        feature_payload, road_observations=cloud_road_input["road_observations"],
        last_decisions=vehicle_road_input["last_decisions"], road_margins=vehicle_road_input["road_margins"],
        action_interval=ic._action_interval)
    _state["feedback"]["vehicle"] = receipts

    permissions = _state["permissions"]
    if permissions.due(now):
        for movement in sorted(snapshot.movements):
            eligible = any(leader.movement == movement and leader.speed_eligible
                           for leader in snapshot.leaders.values())
            if not eligible:
                continue
            obs = snapshot.movements[movement]
            index = _state["builder"].movement_index[movement]
            with torch.no_grad():
                action = policy.act_cloud(snapshot.context.copy(), obs.copy(), index,
                                           rng=_state["rng"], deterministic=not _config["training"])
            msg = permissions.publish(movement.token, action["action"], step, now,
                                       _state["policy_version"], (v_to_c.message_id, r_to_c.message_id))
            _state["bridge"].observe_message(msg, msg.payload)
            buffer.add_cloud({"episode_id": _state["episode_id"], "policy_version": _state["policy_version"],
                              "decision_time": now, "step_id": step, "agent_id": movement.token,
                              "movement_token": movement.token, "movement": index,
                              "context": snapshot.context, "obs": obs,
                              "action": int(action["action"]), "old_logp": float(action["logp"]),
                              "old_value": float(action["value"]), "actor_mask": True,
                              "message_id": msg.message_id})
            counts["cloud_decisions"] += 1
        permissions.mark_grid(now)

    active_permissions = {key: bool(msg.payload["permit"])
                          for key, msg in permissions.permissions.items()
                          if msg.generated_at <= now < msg.valid_until}
    c_to_r, coordination = _send("coordination_context", "cloud", "road", step, now,
                                 {"movement_permissions": active_permissions, "phase_authority": False})
    if any(token not in _state["catalog"] for token in coordination["movement_permissions"]):
        raise ValueError("unknown movement in Road coordination context")

    # No Cloud or Bid data is injected into the baseline's payload or logits.
    _state["hooks"].margins = {}
    response = ic.step(road_payload)
    if response.get("actions", {}).get("vehicles"):
        raise RuntimeError("frozen IPPO unexpectedly emitted vehicle actions")
    _, road_feedback = _send("road_feedback", "road", "cloud", step, now,
                             {"observed": snapshot.road_states,
                              "requested": deepcopy(response.get("actions", {}).get("signals", {})),
                              "phase_authority": False}, parents=(c_to_r.message_id,))
    _state["feedback"]["road"] = road_feedback

    vehicle_actions = {}
    previously_capped = set(_state["active_caps"])
    for vid, leader in sorted(snapshot.leaders.items()):
        msg = permissions.current(leader.movement.token, now)
        valid = msg is not None
        permit = False
        if valid:
            incoming = _state["bus"].consume(msg, "vehicle", now, consumer=vid)
            if incoming["movement"] != leader.movement.token or incoming["policy_version"] != _state["policy_version"]:
                raise ValueError("permission identity or version mismatch")
            permit = bool(incoming["permit"])
            counts["held_permission_uses"] += int(now > msg.generated_at)
        obs = vehicle_observation(leader, permit, valid, now - msg.generated_at if valid else None)
        speed_mask = bool(permit and valid and leader.speed_eligible)
        with torch.no_grad():
            decision = policy.act_vehicle(snapshot.context.copy(), obs, leader.movement_index,
                                           leader.lane_mask.copy(), speed_mask,
                                           rng=_state["rng"], deterministic=not _config["training"])
        expected_mask = bool(speed_mask or leader.lane_mask.sum() > 1)
        if bool(decision["actor_mask"]) != expected_mask:
            raise ValueError("actor eligibility differs from the sampling contract")
        request = {}
        if speed_mask:
            reduction = float(decision["reduction"])
            if not math.isfinite(reduction) or not 0 <= reduction <= .10:
                raise ValueError("invalid speed reduction")
            from traffic_control.cov2x.vehicle.speed_advice import (
                apply_temporary_base_relative_speed_advice,
            )
            advice = apply_temporary_base_relative_speed_advice(
                previous_advice_mps=_state["active_caps"].get(vid),
                base_speed_mps=float(leader.native_ceiling), latent_u=-reduction / .10,
                delta_v_max_mps=float(leader.native_ceiling) * .10)
            if not advice.release_native:
                request["target_speed_mps"] = float(advice.target_speed_mps)
                counts["speed_requests"] += 1
        elif float(decision["raw_speed"]) != 0. or float(decision["reduction"]) != 0.:
            raise ValueError("fixed speed fallback produced a sampled action")
        slot = int(decision["lane_action"])
        if not 0 <= slot < len(leader.lane_mask) or not leader.lane_mask[slot]:
            raise ValueError("invalid sampled lane slot")
        if slot:
            request["target_lane_index"] = int(leader.lane_targets[slot])
            counts["lane_requests"] += 1
        if request:
            vehicle_actions[vid] = request
        if request or vid in previously_capped:
            buffer.record_request(step, vid, request, cloud_message_id=msg.message_id if valid else None)
        buffer.add_vehicle({"episode_id": _state["episode_id"], "policy_version": _state["policy_version"],
                            "decision_time": now, "step_id": step, "agent_id": vid,
                            "movement_token": leader.movement.token, "movement": leader.movement_index,
                            "context": snapshot.context, "obs": obs,
                            "lane_mask": leader.lane_mask, "speed_mask": speed_mask,
                            "raw_speed": float(decision["raw_speed"]), "lane_action": slot,
                            "old_logp": float(decision["logp"]), "old_value": float(decision["value"]),
                            "actor_mask": expected_mask, "cloud_message_id": msg.message_id if valid else None,
                            "cloud_generated_at": msg.generated_at if valid else None,
                            "cloud_valid_until": msg.valid_until if valid else None})
        counts["vehicle_decisions"] += 1

    new_caps = {vid: request["target_speed_mps"] for vid, request in vehicle_actions.items()
                if "target_speed_mps" in request}
    counts["native_release_requests"] += len(previously_capped - set(new_caps))
    _state["active_caps"] = new_caps
    if vehicle_actions:
        response.setdefault("actions", {}).setdefault("vehicles", {}).update(vehicle_actions)
    _state["road_margins"].update(_state["hooks"].margins)
    if digest({k: v for k, v in payload.items() if k != "episode_id"}) != input_hash:
        raise RuntimeError("callback mutated its shared input snapshot")
    _trace(payload, response, input_hash)
    bridge = _state["bridge"]
    bridge.sync(_state["bus"].events)
    batch = bridge.inline_batch(
        f"{_state['episode_id']}:{step}",
        after_sequence=_state["inline_sequence"],
    )
    _state["inline_sequence"] = batch["last_sequence"]
    response = deepcopy(response)
    response["v2x"] = batch
    return response


def step(payload):
    try:
        return _step(payload)
    except BaseException as error:
        _state["error"] = f"{type(error).__name__}: {error}"
        if _state.get("active"):
            try:
                finish({"reason": "controller_error", "simulation_time": _state.get("now", 0.)})
            except BaseException as cleanup_error:
                _state["cleanup_error"] = str(cleanup_error)
        raise


def finish(payload):
    error = None; result = None
    try:
        if _state.get("policy") is not None and _model_version(_state["policy"]) != _state["weight_version"]:
            raise RuntimeError("policy parameters changed during collection")
        if _state.get("source_version") is not None and _source_version() != _state["source_version"]:
            raise RuntimeError("controller source changed during collection")
    except BaseException as exc:
        error = exc
    try:
        if _state.get("hooks") is not None:
            _state["hooks"].close()
        if _state.get("ic") is not None:
            result = _state["ic"].finish(dict(payload))
            _state["ippo_initialized"] = False
    except BaseException as exc:
        error = error or exc
    finally:
        try:
            if _state.get("bridge") is not None:
                _state["bridge"].sync(_state["bus"].events)
            if _state.get("buffer") is not None:
                _state["buffer"].finish("finish_error" if error else str(payload.get("reason")),
                                        float(payload.get("simulation_time", _state.get("now", 0.))))
        finally:
            _cleanup()
    if error:
        _state["error"] = f"{type(error).__name__}: {error}"
        raise error
    return result


def collected():
    if _state.get("buffer") is None:
        raise RuntimeError("no episode data")
    data = _state["buffer"].to_dict()
    data.update(collection_mode=_state["collection_mode"],
                model_seed=_state["model_seed"], sampling_seed=_state["sampling_seed"],
                transport_events=deepcopy(_state["bus"].events), counts=dict(_state["counts"]),
                baseline_metadata=deepcopy(_state["metadata"]), movement_catalog=list(_state["catalog"]),
                weight_version=_state["weight_version"], source_version=_state["source_version"],
                sampling_rng=_state["rng"].state_dict(), trace_sha256=_state["trace_hash"].hexdigest(),
                controller_error=_state.get("error"),
                v2x_events=deepcopy((_state.get("bridge") or _bridge).events),
                v2x_event_batch=(_state.get("bridge") or _bridge).event_batch(),
                frozen_ippo_sha256=IPPO_SHA256)
    return data
