"""Archived multi-mode trainer for the official20 lane206 experiments.

This entry point deliberately owns a separate experiment directory.  It reads
the frozen lane206 tensors and normalization metadata, trains only the new
dynamic model, and appends its lane-level metrics to a copied baseline result
table.  Existing static STGCN/XGBoost/baseline artefacts are never rewritten.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

from ...build_dynamic_lane_graph import load_sparse_graph
from ...build_directional_lane_graph import load_directional_graph
from .build_lane_junction_mapping import load_lane_junction_mapping


FEATURES = ("vehicle_count", "halting_count", "mean_speed", "occupancy")
SPLITS = ("validation", "test_in_distribution", "test_extrapolation")
REPORTED_METRICS = ("mae", "rmse", "smape", "wmape")
GATE_MODES = ("dynamic", "fixed_one")
SPATIAL_MODES = (
    "dynamic_sparse",
    "static_cheb",
    "dynamic_cheb",
    "static_hierarchical",
    "static_directional",
)
CANDIDATE_EDGE_TYPES = {
    "1": "lateral",
    "2": "direct_transition",
    "4": "next_target",
}


def _load_torch():
    try:
        import torch
        from torch import nn

        from .dynamic_lane_model import build_model_from_graph as build_dynamic_model_from_graph
        from .dynamic_cheb_lane_model import build_model_from_graph as build_dynamic_cheb_model_from_graph
        from ...static_cheb_lane_model import build_model_from_graph as build_static_model_from_graph
        from .static_hierarchical_lane_model import build_model_from_graph as build_hierarchical_model_from_graph
        from ...static_directional_lane_model import build_model_from_graph as build_directional_model_from_graph
    except ModuleNotFoundError as exc:
        if exc.name == "torch":
            raise RuntimeError(
                "PyTorch is required for dynamic lane training; "
                "run this entry point in the project/AutoDL v2x-ai-py310 environment."
            ) from exc
        raise
    return (
        torch,
        nn,
        build_dynamic_model_from_graph,
        build_static_model_from_graph,
        build_dynamic_cheb_model_from_graph,
        build_hierarchical_model_from_graph,
        build_directional_model_from_graph,
    )


def _spatial_metadata(args: argparse.Namespace) -> dict[str, object]:
    """Describe each spatial mode consistently in config and result files."""

    if args.spatial_mode == "dynamic_sparse":
        dynamic_gate = args.gate_mode == "dynamic"
        return {
            "model": "DynamicLaneGraphV1",
            "artifact_type": "dynamic_lane_v1",
            "spatial_mode": args.spatial_mode,
            "spatial_operator": "sample_conditioned_sparse_edge_message_passing",
            "gate_mode": args.gate_mode,
            "gate_range": [0.5, 1.5] if dynamic_gate else [1.0, 1.0],
            "gate_half_range": 0.5,
            "gate_regularization": 0.0,
            "history_summary": "latest_frame_and_12_frame_mean",
            "dynamic_edge_weight_formula": (
                "static_weight * gate; normalize incoming weights per target node"
                if dynamic_gate
                else "static_weight; normalize incoming weights per target node"
            ),
            "edge_weight_normalization": "incoming weights per target node",
        }
    if args.spatial_mode == "static_cheb":
        return {
            "model": "StaticChebLaneSTGCNControlV1",
            "artifact_type": "static_cheb_spatial_control_v1",
            "spatial_mode": args.spatial_mode,
            "spatial_operator": "fixed_sym_norm_laplacian_chebyshev_k3",
            "gate_mode": "not_applicable",
            "gate_range": [1.0, 1.0],
            "gate_half_range": None,
            "gate_regularization": 0.0,
            "history_summary": "not_applicable",
            "dynamic_edge_weight_formula": (
                "not_applicable; fixed symmetric normalized Laplacian GSO with Chebyshev Ks=3"
            ),
            "edge_weight_normalization": "not_applicable; fixed symmetric normalized Laplacian",
        }
    if args.spatial_mode == "dynamic_cheb":
        dynamic_gate = args.gate_mode == "dynamic"
        gate_half_range = float(args.gate_half_range)
        return {
            "model": "DynamicChebLaneSTGCNV2",
            "artifact_type": "dynamic_cheb_spatial_v2",
            "spatial_mode": args.spatial_mode,
            "spatial_operator": "sample_conditioned_symmetric_sparse_gso_chebyshev_k3",
            "gate_mode": args.gate_mode,
            "gate_range": (
                [1.0 - gate_half_range, 1.0 + gate_half_range]
                if dynamic_gate
                else [1.0, 1.0]
            ),
            "gate_half_range": gate_half_range,
            "gate_regularization": float(args.gate_regularization),
            "history_summary": "latest_frame_and_12_frame_mean",
            "dynamic_edge_weight_formula": (
                "static_weight * shared_undirected_pair_gate; symmetric degree normalization; "
                "scaled Laplacian Chebyshev Ks=3"
                if dynamic_gate
                else "static_weight; symmetric degree normalization; scaled Laplacian Chebyshev Ks=3"
            ),
            "edge_weight_normalization": "symmetric degree normalization",
        }
    if args.spatial_mode == "static_hierarchical":
        return {
            "model": "StaticHierarchicalLaneSTGCNV1",
            "artifact_type": "static_lane_junction_hierarchy_v1",
            "spatial_mode": args.spatial_mode,
            "spatial_operator": "fixed_lane_chebyshev_plus_junction_chebyshev_residual",
            "gate_mode": "not_applicable",
            "gate_range": [1.0, 1.0],
            "gate_half_range": None,
            "gate_regularization": 0.0,
            "history_summary": "deterministic_lane_pooling_to_20_junctions",
            "dynamic_edge_weight_formula": (
                "not_applicable; fixed lane and junction symmetric normalized Laplacians"
            ),
            "edge_weight_normalization": (
                "not_applicable; fixed symmetric normalized Laplacians"
            ),
        }
    if args.spatial_mode == "static_directional":
        return {
            "model": "StaticDirectionalLaneSTGCNV1",
            "artifact_type": "static_lane_directional_residual_v1",
            "spatial_mode": args.spatial_mode,
            "spatial_operator": "fixed_static_chebyshev_plus_relation_separated_directional_residual",
            "gate_mode": "not_applicable",
            "gate_range": [1.0, 1.0],
            "gate_half_range": None,
            "gate_regularization": 0.0,
            "history_summary": (
                "fixed_sumo_direct_and_hop_decayed_next_target messages plus upstream spillback"
            ),
            "dynamic_edge_weight_formula": (
                "not_applicable; fixed receiver-normalized direct-transition and hop-decayed "
                "next-target messages in both downstream and upstream directions"
            ),
            "edge_weight_normalization": "receiver row normalization per direction",
            "directional_max_scale": float(args.directional_max_scale),
        }
    raise ValueError(f"unknown spatial mode: {args.spatial_mode}")


def _set_seed(torch, seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def _rng_state(torch) -> dict[str, object]:
    state: dict[str, object] = {"torch": torch.get_rng_state()}
    if torch.cuda.is_available():
        state["cuda"] = torch.cuda.get_rng_state_all()
    return state


def _restore_rng_state(torch, state: dict[str, object]) -> None:
    # Checkpoints are loaded with ``map_location=device``.  On a CUDA run
    # that can move the CPU RNG ByteTensor onto the GPU, while
    # ``torch.set_rng_state`` requires a CPU ByteTensor.
    torch.set_rng_state(state["torch"].detach().cpu())
    if torch.cuda.is_available() and "cuda" in state:
        cuda_states = [value.detach().cpu() for value in state["cuda"]]
        torch.cuda.set_rng_state_all(cuda_states)


def _load_metadata(dataset_dir: Path) -> dict[str, Any]:
    metadata = json.loads((dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("target") != "vehicle_count":
        raise ValueError("dynamic lane v1 requires target=vehicle_count")
    if tuple(metadata.get("features", ())) != FEATURES:
        raise ValueError(f"dynamic lane v1 requires features={FEATURES}")
    if int(metadata.get("n_his", -1)) != 12 or int(metadata.get("n_pred", -1)) != 12:
        raise ValueError("dynamic lane v1 requires n_his=12 and n_pred=12")
    if float(metadata.get("interval_seconds", -1)) != 5.0:
        raise ValueError("dynamic lane v1 requires a 5-second interval")
    if int(metadata.get("lane_count", -1)) != 206:
        raise ValueError("dynamic lane v1 requires 206 lane nodes")
    if metadata.get("normalization", {}).get("fit_split") != "train":
        raise ValueError("dynamic lane v1 requires train-fitted normalization")
    return metadata


def _load_split(torch, dataset_dir: Path, split: str, horizon_step: int):
    path = dataset_dir / f"{split}.npz"
    with np.load(path, allow_pickle=False) as data:
        x = torch.from_numpy(np.ascontiguousarray(data["x"], dtype=np.float32))
        y = torch.from_numpy(
            np.ascontiguousarray(data["y"][:, horizon_step - 1, :], dtype=np.float32)
        )
    if tuple(x.shape[1:]) != (4, 12, 206) or tuple(y.shape[1:]) != (206,):
        raise ValueError(f"{path} has incompatible x/y shapes: {tuple(x.shape)}, {tuple(y.shape)}")
    return x, y


def _metrics(prediction: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    actual = np.rint(actual)
    error = prediction - actual
    absolute = np.abs(error)
    denominator = np.abs(actual).sum()
    nonzero = np.abs(actual) >= 0.5
    smape_denominator = np.abs(prediction) + np.abs(actual)
    smape_terms = np.divide(
        2.0 * absolute,
        smape_denominator,
        out=np.zeros_like(absolute, dtype=np.float64),
        where=smape_denominator > 1e-9,
    )
    return {
        "mae": float(absolute.mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "mape": float((absolute[nonzero] / np.abs(actual[nonzero])).mean()) if nonzero.any() else 0.0,
        "smape": float(smape_terms.mean()),
        "wmape": float(absolute.sum() / denominator) if denominator > 1e-9 else 0.0,
    }


def _evaluate(torch, model, loader, device, target_mean: float, target_std: float) -> dict[str, float]:
    model.eval()
    predictions: list[np.ndarray] = []
    actuals: list[np.ndarray] = []
    with torch.no_grad():
        for x, y in loader:
            output = model(x.to(device)).reshape(len(x), -1).cpu().numpy()
            predictions.append(output * target_std + target_mean)
            actuals.append(y.numpy() * target_std + target_mean)
    if not predictions:
        raise ValueError("cannot evaluate an empty split")
    return _metrics(np.concatenate(predictions), np.concatenate(actuals))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_graph(graph_path: Path, experiment_dir: Path) -> Path:
    destination = experiment_dir / "graph" / "dynamic_candidate_edges.npz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if graph_path.resolve() != destination.resolve():
        if destination.exists() and _sha256_file(destination) != _sha256_file(graph_path):
            raise FileExistsError(f"refusing to overwrite a different graph archive: {destination}")
        if not destination.exists():
            shutil.copy2(graph_path, destination)
    return destination


def _copy_mapping(mapping_path: Path, experiment_dir: Path) -> Path:
    destination = experiment_dir / "hierarchy" / "lane_junction_mapping.npz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if mapping_path.resolve() != destination.resolve():
        if destination.exists() and _sha256_file(destination) != _sha256_file(mapping_path):
            raise FileExistsError(
                f"refusing to overwrite a different hierarchy mapping: {destination}"
            )
        if not destination.exists():
            shutil.copy2(mapping_path, destination)
    return destination


def _copy_directional_graph(directional_path: Path, experiment_dir: Path) -> Path:
    destination = experiment_dir / "directional" / "directional_lane_graph.npz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if directional_path.resolve() != destination.resolve():
        if destination.exists() and _sha256_file(destination) != _sha256_file(directional_path):
            raise FileExistsError(
                f"refusing to overwrite a different directional graph: {destination}"
            )
        if not destination.exists():
            shutil.copy2(directional_path, destination)
    return destination


def _write_config(
    args: argparse.Namespace,
    metadata: dict[str, Any],
    graph: dict[str, object],
    graph_path: Path,
    experiment_dir: Path,
    mapping_path: Path | None = None,
    directional_path: Path | None = None,
) -> None:
    spatial = _spatial_metadata(args)
    is_dynamic = args.spatial_mode in {"dynamic_sparse", "dynamic_cheb"}
    config = {
        "model": spatial["model"],
        "artifact_type": spatial["artifact_type"],
        "spatial_mode": spatial["spatial_mode"],
        "spatial_operator": spatial["spatial_operator"],
        "dataset_dir": str(args.dataset_dir.resolve()),
        "graph": str(graph_path.resolve()),
        "hierarchy_mapping": str(mapping_path.resolve()) if mapping_path else None,
        "directional_graph": (
            str(directional_path.resolve()) if directional_path else None
        ),
        "features": list(metadata["features"]),
        "node_order": list(metadata["lanes"]),
        "nodes_sha256": str(graph["nodes_sha256"]),
        "target": metadata["target"],
        "n_his": int(metadata["n_his"]),
        "n_pred": int(metadata["n_pred"]),
        "interval_seconds": float(metadata["interval_seconds"]),
        "lane_count": int(metadata["lane_count"]),
        "normalization": metadata["normalization"],
        "gate_mode": spatial["gate_mode"],
        "optimizer": {
            "name": "AdamW",
            "learning_rate": args.learning_rate,
            "weight_decay": args.weight_decay,
        },
        "training": {
            "epochs": args.epochs,
            "patience": args.patience,
            "batch_size": args.batch_size,
            "seed": args.seed,
            "dry_run": bool(args.dry_run),
        },
        "dynamic_gate": {
            "mode": spatial["gate_mode"],
            "history_summary": spatial["history_summary"],
            "gate_range": spatial["gate_range"],
            "half_range": spatial["gate_half_range"],
            "regularization": spatial["gate_regularization"],
            "uses_features": list(FEATURES),
            "one_gate_set_per_sample": is_dynamic and args.gate_mode == "dynamic",
            "formula": spatial["dynamic_edge_weight_formula"],
        },
        "directional_residual": {
            "max_scale": spatial.get("directional_max_scale"),
            "relation_separated_branches": args.spatial_mode == "static_directional",
        },
        "candidate_edge_types": CANDIDATE_EDGE_TYPES,
        "graph_sha256": _sha256_file(graph_path),
    }
    (experiment_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_comparison_csv(
    baseline_reference_path: Path | None,
    result: dict[str, Any],
    output: Path,
) -> None:
    rows: list[dict[str, Any]] = []
    if baseline_reference_path is not None:
        reference = json.loads(baseline_reference_path.read_text(encoding="utf-8"))
        rows.extend(reference["metrics"]["results_summary_60s"])
    for split in SPLITS:
        comparison = result.get("comparison_to_static_stgcn") or {}
        comparison_metrics = comparison.get("metrics", {}).get(split, {})
        deltas = comparison_metrics.get("delta", {})
        rows.append(
            {
                "split": split,
                "model": result["model"],
                "horizon_seconds": result["horizon_seconds"],
                **result[split],
                **{
                    f"delta_{metric}_vs_static_stgcn": deltas.get(metric, "")
                    for metric in REPORTED_METRICS
                },
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "split", "model", "horizon_seconds", "mae", "rmse", "mape", "smape", "wmape",
        *(f"delta_{metric}_vs_static_stgcn" for metric in REPORTED_METRICS),
    )
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _static_stgcn_metrics(reference: dict[str, Any], split: str) -> dict[str, float]:
    metrics = reference.get("metrics", {}).get("stgcn", {}).get(split)
    if not isinstance(metrics, dict):
        for row in reference.get("metrics", {}).get("results_summary_60s", []):
            if row.get("split") == split and str(row.get("model", "")).lower() == "stgcn":
                metrics = row
                break
    if not isinstance(metrics, dict):
        raise ValueError(f"baseline reference lacks static STGCN metrics for {split}")
    return {metric: float(metrics[metric]) for metric in REPORTED_METRICS}


def _compare_to_static_stgcn(
    baseline_reference_path: Path | None,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if baseline_reference_path is None:
        return None
    reference = json.loads(baseline_reference_path.read_text(encoding="utf-8"))
    comparison: dict[str, Any] = {
        "baseline_model": "STGCN",
        "metrics": {},
    }
    for split in SPLITS:
        static_metrics = _static_stgcn_metrics(reference, split)
        dynamic_metrics = {metric: float(result[split][metric]) for metric in REPORTED_METRICS}
        comparison["metrics"][split] = {
            str(result["model"]): dynamic_metrics,
            "static_stgcn": static_metrics,
            "delta": {
                metric: dynamic_metrics[metric] - static_metrics[metric]
                for metric in REPORTED_METRICS
            },
        }
    return comparison


def _write_package_manifest(
    *,
    experiment_dir: Path,
    metadata: dict[str, Any],
    graph: dict[str, object],
    result: dict[str, Any],
) -> None:
    manifest = {
        "artifact_type": result["artifact_type"],
        "model": result["model"],
        "spatial_mode": result["spatial_mode"],
        "spatial_operator": result["spatial_operator"],
        "task": "official20_lane206_vehicle_count_60s",
        "node_order": list(metadata["lanes"]),
        "nodes_sha256": str(graph["nodes_sha256"]),
        "features": list(metadata["features"]),
        "target": metadata["target"],
        "history_steps": int(metadata["n_his"]),
        "prediction_step": int(result["horizon_steps"]),
        "horizon_seconds": float(result["horizon_seconds"]),
        "interval_seconds": float(metadata["interval_seconds"]),
        "normalization": metadata["normalization"],
        "gate_mode": result["gate_mode"],
        "candidate_edge_types": CANDIDATE_EDGE_TYPES,
        "edge_count_including_self_loops": int(result["edge_count_including_self_loops"]),
        "dynamic_edge_weight": {
            "formula": result["dynamic_edge_weight_formula"],
            "gate_range": result["gate_range"],
            "gate_half_range": result["gate_half_range"],
            "gate_regularization": result["gate_regularization"],
            "summary": result["history_summary"],
            "one_gate_set_per_sample": result["gate_mode"] == "dynamic",
            "row_normalization": result["edge_weight_normalization"],
        },
        "hierarchy": result.get("hierarchy"),
        "metrics": {split: result[split] for split in SPLITS},
        "comparison_to_static_stgcn": result["comparison_to_static_stgcn"],
        "directional_graph": result.get("directional_graph"),
        "artifacts": {
            "candidate_edges": "graph/dynamic_candidate_edges.npz",
            "best_checkpoint": "checkpoints/best.pt",
            "last_checkpoint": "checkpoints/last.pt",
            "metrics": "metrics/metrics.json",
            "comparison_csv": "metrics/results_60s.csv",
            **(
                {"hierarchy_mapping": "hierarchy/lane_junction_mapping.npz"}
                if result["spatial_mode"] == "static_hierarchical"
                else {}
            ),
            **(
                {"directional_graph": "directional/directional_lane_graph.npz"}
                if result["spatial_mode"] == "static_directional"
                else {}
            ),
        },
    }
    package_dir = experiment_dir / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "model_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_sha256sums(experiment_dir: Path) -> None:
    output = experiment_dir / "SHA256SUMS.txt"
    rows = []
    for path in sorted(experiment_dir.rglob("*")):
        if not path.is_file() or path.resolve() == output.resolve():
            continue
        rows.append(f"{_sha256_file(path)} *{path.relative_to(experiment_dir).as_posix()}")
    output.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def _prepare(args: argparse.Namespace):
    (
        torch,
        nn,
        build_dynamic_model_from_graph,
        build_static_model_from_graph,
        build_dynamic_cheb_model_from_graph,
        build_hierarchical_model_from_graph,
        build_directional_model_from_graph,
    ) = _load_torch()
    _set_seed(torch, args.seed)
    metadata = _load_metadata(args.dataset_dir)
    if args.horizon_step != int(metadata["n_pred"]):
        raise ValueError("dynamic lane v1 must evaluate the prepared final horizon")
    graph = load_sparse_graph(args.graph)
    if tuple(graph["nodes"]) != tuple(metadata["lanes"]):
        raise ValueError("dynamic graph node order differs from dataset metadata")
    mapping_copy = None
    if args.spatial_mode == "static_hierarchical":
        if args.mapping is None:
            raise ValueError("--mapping is required for --spatial-mode static_hierarchical")
        mapping = load_lane_junction_mapping(args.mapping)
        if tuple(mapping["lane_order"]) != tuple(metadata["lanes"]):
            raise ValueError("hierarchy mapping lane order differs from dataset metadata")
        mapping_copy = _copy_mapping(args.mapping, args.output_dir)
    directional_copy = None
    if args.spatial_mode == "static_directional":
        if args.directional_graph is None:
            raise ValueError(
                "--directional-graph is required for --spatial-mode static_directional"
            )
        directional = load_directional_graph(args.directional_graph)
        if tuple(directional["nodes"]) != tuple(metadata["lanes"]):
            raise ValueError("directional graph node order differs from dataset metadata")
        directional_copy = _copy_directional_graph(args.directional_graph, args.output_dir)
    experiment_dir = args.output_dir
    experiment_dir.mkdir(parents=True, exist_ok=True)
    graph_copy = _copy_graph(args.graph, experiment_dir)
    _write_config(
        args,
        metadata,
        graph,
        graph_copy,
        experiment_dir,
        mapping_copy,
        directional_copy,
    )
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    if args.spatial_mode == "dynamic_sparse":
        model = build_dynamic_model_from_graph(
            str(graph_copy),
            dropout=args.dropout,
            temporal_channels=args.temporal_channels,
            graph_channels=args.graph_channels,
            gate_hidden=args.gate_hidden,
            gate_mode=args.gate_mode,
        )
    elif args.spatial_mode == "static_cheb":
        model = build_static_model_from_graph(
            str(graph_copy),
            dropout=args.dropout,
        )
    elif args.spatial_mode == "static_hierarchical":
        # mapping_copy is guaranteed above after the mode check.
        model = build_hierarchical_model_from_graph(
            str(graph_copy),
            str(mapping_copy),
            dropout=args.dropout,
            temporal_channels=args.temporal_channels,
            graph_channels=args.graph_channels,
        )
    elif args.spatial_mode == "static_directional":
        # directional_copy is guaranteed above after the mode check.
        model = build_directional_model_from_graph(
            str(graph_copy),
            str(directional_copy),
            dropout=args.dropout,
            temporal_channels=args.temporal_channels,
            graph_channels=args.graph_channels,
            max_scale=args.directional_max_scale,
        )
    else:
        model = build_dynamic_cheb_model_from_graph(
            str(graph_copy),
            dropout=args.dropout,
            temporal_channels=args.temporal_channels,
            graph_channels=args.graph_channels,
            gate_hidden=args.gate_hidden,
            gate_half_range=args.gate_half_range,
            gate_mode=args.gate_mode,
        )
    model = model.to(device)
    return torch, nn, metadata, graph, experiment_dir, device, model


def _evaluate_splits(torch, model, dataset_dir: Path, metadata: dict[str, Any], args, device):
    normalization = metadata["normalization"]
    target_mean = float(normalization["target_mean"])
    target_std = float(normalization["target_std"])
    result = {}
    for split in SPLITS:
        x, y = _load_split(torch, dataset_dir, split, args.horizon_step)
        loader = torch.utils.data.DataLoader(
            torch.utils.data.TensorDataset(x, y),
            batch_size=args.batch_size,
            shuffle=False,
        )
        result[split] = _evaluate(torch, model, loader, device, target_mean, target_std)
    return result


def _finalize_result(
    *,
    torch,
    model,
    metadata: dict[str, Any],
    graph: dict[str, object],
    args: argparse.Namespace,
    device,
    best_epoch: int,
    best_validation_mse: float,
    history: list[dict[str, float]],
    experiment_dir: Path,
) -> dict[str, Any]:
    spatial = _spatial_metadata(args)
    result: dict[str, Any] = {
        **spatial,
        "features": list(metadata["features"]),
        "node_order": list(metadata["lanes"]),
        "horizon_steps": args.horizon_step,
        "horizon_seconds": args.horizon_step * float(metadata["interval_seconds"]),
        "best_epoch": int(best_epoch),
        "best_validation_mse": float(best_validation_mse),
        "normalization_fit_split": metadata["normalization"]["fit_split"],
        "lane_count": int(metadata["lane_count"]),
        "edge_count_including_self_loops": int(len(graph["source_index"])),
        "nodes_sha256": str(graph["nodes_sha256"]),
        "candidate_edge_types": CANDIDATE_EDGE_TYPES,
        "normalization": metadata["normalization"],
        "history": history,
    }
    if args.spatial_mode == "static_hierarchical":
        if args.mapping is None:
            raise ValueError("--mapping is required for static hierarchical results")
        mapping = load_lane_junction_mapping(args.mapping)
        result["hierarchy"] = {
            "junction_order": list(mapping["junction_order"]),
            "junction_count": len(mapping["junction_order"]),
            "lane_to_junction_mapping_sha256": mapping["mapping_sha256"],
            "lane_order_sha256": mapping["lane_order_sha256"],
            "junction_order_sha256": mapping["junction_order_sha256"],
            "pooling_matrix_sha256": mapping["pooling_matrix_sha256"],
            "junction_adjacency_sha256": mapping["junction_adjacency_sha256"],
        }
    if args.spatial_mode == "static_directional":
        if args.directional_graph is None:
            raise ValueError("--directional-graph is required for static directional results")
        directional = load_directional_graph(args.directional_graph)
        result["directional_graph"] = {
            "candidate_graph_sha256": directional["candidate_graph_sha256"],
            "has_relation_branches": bool(directional.get("has_relation_branches", False)),
            "hop_decay": directional.get("hop_decay"),
            "topology_csv_sha256": directional.get("topology_csv_sha256"),
            "downstream_edge_count": int(
                np.count_nonzero(directional["downstream_adjacency"])
            ),
            "upstream_edge_count": int(
                np.count_nonzero(directional["upstream_adjacency"])
            ),
            "downstream_nodes_with_receivers": int(
                np.count_nonzero(
                    np.asarray(directional["downstream_adjacency"]).sum(axis=1) > 0
                )
            ),
            "upstream_nodes_with_receivers": int(
                np.count_nonzero(
                    np.asarray(directional["upstream_adjacency"]).sum(axis=1) > 0
                )
            ),
        }
        if directional.get("has_relation_branches", False):
            result["directional_graph"]["direct_transition_edge_count"] = int(
                np.count_nonzero(directional["downstream_direct_adjacency"])
            )
            result["directional_graph"]["next_target_edge_count"] = int(
                np.count_nonzero(directional["downstream_next_target_adjacency"])
            )
        result["directional_residual"] = {
            "max_scale": float(args.directional_max_scale),
            "relation_separated_branches": bool(
                directional.get("has_relation_branches", False)
            ),
        }
    result.update(_evaluate_splits(torch, model, args.dataset_dir, metadata, args, device))
    result["comparison_to_static_stgcn"] = _compare_to_static_stgcn(
        args.baseline_reference,
        result,
    )
    metrics_path = experiment_dir / "metrics" / "metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_comparison_csv(
        args.baseline_reference,
        result,
        experiment_dir / "metrics" / "results_60s.csv",
    )
    if args.baseline_reference is not None:
        reference_copy = experiment_dir / "baseline_reference.json"
        if args.baseline_reference.resolve() != reference_copy.resolve():
            reference_copy.write_text(args.baseline_reference.read_text(encoding="utf-8"), encoding="utf-8")
    _write_package_manifest(
        experiment_dir=experiment_dir,
        metadata=metadata,
        graph=graph,
        result=result,
    )
    # The training process may be writing JSON epoch records to a redirected
    # stdout log.  Flush before hashing so SHA256SUMS.txt covers the complete
    # log rather than the bytes written before the final Python buffer flush.
    sys.stdout.flush()
    sys.stderr.flush()
    _write_sha256sums(experiment_dir)
    return result


def train(args: argparse.Namespace) -> dict[str, Any]:
    torch, nn, metadata, graph, experiment_dir, device, model = _prepare(args)
    train_x, train_y = _load_split(torch, args.dataset_dir, "train", args.horizon_step)
    val_x, val_y = _load_split(torch, args.dataset_dir, "validation", args.horizon_step)
    generator = torch.Generator()
    generator.manual_seed(args.seed)
    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(train_x, train_y),
        batch_size=args.batch_size,
        shuffle=True,
        generator=generator,
    )
    val_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(val_x, val_y),
        batch_size=args.batch_size,
        shuffle=False,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.95)
    loss = nn.MSELoss()
    checkpoints = experiment_dir / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    best_checkpoint = checkpoints / "best.pt"
    last_checkpoint = checkpoints / "last.pt"
    best_loss = float("inf")
    wait = 0
    history: list[dict[str, float]] = []
    start_epoch = 1
    if args.resume:
        if not last_checkpoint.is_file():
            raise FileNotFoundError(f"--resume requires {last_checkpoint}")
        saved = torch.load(last_checkpoint, map_location=device)
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        scheduler.load_state_dict(saved["scheduler"])
        best_loss = float(saved["best_validation_mse"])
        wait = int(saved["wait"])
        history = list(saved["history"])
        _restore_rng_state(torch, saved["rng_state"])
        if "loader_generator_state" in saved:
            generator.set_state(saved["loader_generator_state"].detach().cpu())
        start_epoch = int(saved["epoch"]) + 1
        print(f"resuming_from_epoch={start_epoch - 1}")

    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        total_loss = 0.0
        total_mse = 0.0
        total_gate_penalty = 0.0
        total_samples = 0
        for x, y in train_loader:
            optimizer.zero_grad(set_to_none=True)
            x_device = x.to(device)
            y_device = y.to(device)
            if args.spatial_mode == "dynamic_cheb" and args.gate_regularization > 0:
                raw_prediction, _normalized_weight, gate = model(
                    x_device,
                    return_edge_weights=True,
                )
                prediction = raw_prediction.reshape(len(x), -1)
                mse_loss = loss(prediction, y_device)
                gate_penalty = torch.square(gate - 1.0).mean()
            else:
                prediction = model(x_device).reshape(len(x), -1)
                mse_loss = loss(prediction, y_device)
                gate_penalty = prediction.new_zeros(())
            batch_loss = mse_loss + args.gate_regularization * gate_penalty
            batch_loss.backward()
            optimizer.step()
            total_loss += float(batch_loss.item()) * len(x)
            total_mse += float(mse_loss.item()) * len(x)
            total_gate_penalty += float(gate_penalty.item()) * len(x)
            total_samples += len(x)
        scheduler.step()
        model.eval()
        validation_loss = 0.0
        validation_samples = 0
        with torch.no_grad():
            for x, y in val_loader:
                batch_loss = loss(model(x.to(device)).reshape(len(x), -1), y.to(device))
                validation_loss += float(batch_loss.item()) * len(x)
                validation_samples += len(x)
        validation_loss /= validation_samples
        record = {
            "epoch": epoch,
            "train_mse": total_mse / total_samples,
            "train_objective": total_loss / total_samples,
            "gate_penalty": total_gate_penalty / total_samples,
            "validation_mse": validation_loss,
        }
        history.append(record)
        print(json.dumps(record))
        if validation_loss < best_loss:
            best_loss = validation_loss
            wait = 0
            torch.save(
                {"model": model.state_dict(), "epoch": epoch, "validation_mse": best_loss},
                best_checkpoint,
            )
        else:
            wait += 1
        torch.save(
            {
                "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict(),
                "epoch": epoch,
                "best_validation_mse": best_loss,
                "wait": wait,
                "history": history,
                "rng_state": _rng_state(torch),
                "loader_generator_state": generator.get_state(),
            },
            last_checkpoint,
        )
        if wait >= args.patience:
            print(f"early_stop_epoch={epoch}")
            break

    if not best_checkpoint.is_file():
        raise RuntimeError("training completed without producing best.pt")
    saved = torch.load(best_checkpoint, map_location=device)
    model.load_state_dict(saved["model"])
    return _finalize_result(
        torch=torch,
        model=model,
        metadata=metadata,
        graph=graph,
        args=args,
        device=device,
        best_epoch=int(saved["epoch"]),
        best_validation_mse=float(saved["validation_mse"]),
        history=history,
        experiment_dir=experiment_dir,
    )


def evaluate_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    torch, _nn, metadata, graph, experiment_dir, device, model = _prepare(args)
    checkpoint = experiment_dir / "checkpoints" / "best.pt"
    if not checkpoint.is_file():
        raise FileNotFoundError(f"--evaluate-only requires {checkpoint}")
    saved = torch.load(checkpoint, map_location=device)
    model.load_state_dict(saved["model"])
    return _finalize_result(
        torch=torch,
        model=model,
        metadata=metadata,
        graph=graph,
        args=args,
        device=device,
        best_epoch=int(saved["epoch"]),
        best_validation_mse=float(saved["validation_mse"]),
        history=[],
        experiment_dir=experiment_dir,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train or evaluate an official20 lane206 spatial model.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True, help="Sparse dynamic candidate graph NPZ.")
    parser.add_argument(
        "--mapping",
        type=Path,
        help="Lane-junction hierarchy mapping NPZ; required for static_hierarchical.",
    )
    parser.add_argument(
        "--directional-graph",
        type=Path,
        help=(
            "Fixed downstream/upstream lane graph NPZ; required for "
            "static_directional."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="Independent dynamic_lane_v1 experiment directory.")
    parser.add_argument("--baseline-reference", type=Path, help="baseline_reference.json from preflight.")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=0.001)
    parser.add_argument("--dropout", type=float, default=0.5)
    parser.add_argument("--temporal-channels", type=int, default=64)
    parser.add_argument("--graph-channels", type=int, default=16)
    parser.add_argument(
        "--directional-max-scale",
        type=float,
        default=0.25,
        help="bounded directional residual scale; default 0.25",
    )
    parser.add_argument("--gate-hidden", type=int, default=32)
    parser.add_argument(
        "--gate-half-range",
        type=float,
        default=0.5,
        help="dynamic_cheb gate half-range around 1; default 0.5 gives [0.5, 1.5]",
    )
    parser.add_argument(
        "--gate-regularization",
        type=float,
        default=0.0,
        help="dynamic_cheb penalty coefficient for mean((gate - 1)^2)",
    )
    parser.add_argument("--gate-mode", choices=GATE_MODES, default="dynamic")
    parser.add_argument(
        "--spatial-mode",
        choices=SPATIAL_MODES,
        default="dynamic_sparse",
        help=(
            "dynamic_sparse for v1, static_cheb for the aligned control, "
            "dynamic_cheb for v2, static_hierarchical for lane/junction v1, "
            "or static_directional for SUMO downstream/upstream residuals"
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--horizon-step", type=int, default=12)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run one CPU epoch with the supplied dataset and write disposable artefacts",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--evaluate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.gate_half_range <= 0 or args.gate_half_range > 1:
        raise ValueError("--gate-half-range must be in (0, 1]")
    if args.gate_regularization < 0:
        raise ValueError("--gate-regularization must be non-negative")
    if args.directional_max_scale <= 0:
        raise ValueError("--directional-max-scale must be positive")
    if args.gate_regularization > 0 and args.spatial_mode != "dynamic_cheb":
        raise ValueError("--gate-regularization is only supported with --spatial-mode dynamic_cheb")
    if args.dry_run:
        args.cpu = True
        args.epochs = min(args.epochs, 1)
        args.patience = min(args.patience, 1)
    result = evaluate_checkpoint(args) if args.evaluate_only else train(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # The final JSON is part of a redirected train.log.  Rewrite the manifest
    # after printing it so the checksum covers the complete log, including the
    # final evaluation result emitted by this entry point.
    sys.stdout.flush()
    sys.stderr.flush()
    _write_sha256sums(args.output_dir)


if __name__ == "__main__":
    main()
