"""Train an episode-aware, direct 60-second STGCN forecast for stage 1.

The model layers are imported from the separately installed STGCN reference
repository, while every data split, normalization statistic, checkpoint and
metric is owned by this project.  This avoids modifying that external repo and
prevents temporal windows from crossing SUMO episode boundaries.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import scipy.sparse as sp
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True


def _rng_state() -> dict[str, object]:
    state: dict[str, object] = {"torch": torch.get_rng_state()}
    if torch.cuda.is_available():
        state["cuda"] = torch.cuda.get_rng_state_all()
    return state


def _restore_rng_state(state: dict[str, object]) -> None:
    torch.set_rng_state(state["torch"])
    if torch.cuda.is_available() and "cuda" in state:
        torch.cuda.set_rng_state_all(state["cuda"])


def _import_stgcn(stgcn_root: Path):
    resolved = str(stgcn_root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    from model import models  # type: ignore[import-not-found]
    from script import utility  # type: ignore[import-not-found]

    return models, utility


def _load_split(path: Path, horizon_step: int) -> tuple[torch.Tensor, torch.Tensor]:
    data = np.load(path)
    x = torch.from_numpy(np.ascontiguousarray(data["x"], dtype=np.float32))
    # Reference STGCN is a direct forecaster; use t + 60 seconds, not all
    # intermediate labels.  The other labels remain available for baselines.
    y = torch.from_numpy(np.ascontiguousarray(data["y"][:, horizon_step - 1, :], dtype=np.float32))
    return x, y


def _evaluate(
    model: nn.Module,
    loader: DataLoader,
    device: torch.device,
    mean: float,
    std: float,
) -> dict[str, float]:
    model.eval()
    predictions: list[np.ndarray] = []
    actuals: list[np.ndarray] = []
    with torch.no_grad():
        for x, y in loader:
            output = model(x.to(device)).reshape(len(x), -1).cpu().numpy()
            predictions.append(output * std + mean)
            actuals.append(y.numpy() * std + mean)
    prediction = np.concatenate(predictions)
    # ``vehicle_count`` is integer-valued in SUMO.  Restore the target on its
    # native scale before percentage metrics so a normalized true zero cannot
    # become a floating-point residue.
    actual = np.rint(np.concatenate(actuals))
    error = prediction - actual
    absolute = np.abs(error)
    denominator = np.abs(actual).sum()
    # The target is an integer count.  Avoid turning float32 round-off around
    # a zero count into an enormous MAPE term after de-normalization.
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


def _build_model(
    *, metadata: dict[str, object], stgcn_root: Path, dataset_dir: Path, device: torch.device, dropout: float
) -> nn.Module:
    models, utility = _import_stgcn(stgcn_root)
    adjacency = np.load(dataset_dir / "adjacency.npz")["adjacency"].astype(np.float32)
    gso = utility.calc_gso(sp.csc_matrix(adjacency), "sym_norm_lap")
    gso = utility.calc_chebynet_gso(gso).toarray().astype(np.float32)
    model_args = SimpleNamespace(
        n_his=int(metadata["n_his"]), Kt=3, Ks=3, stblock_num=2,
        act_func="glu", graph_conv_type="cheb_graph_conv",
        gso=torch.from_numpy(gso).to(device), enable_bias=True, droprate=dropout,
    )
    blocks = [[len(metadata["features"])], [64, 16, 64], [64, 16, 64], [128, 128], [1]]
    return models.STGCNChebGraphConv(model_args, blocks, int(metadata["lane_count"])).to(device)


def _evaluate_splits(
    *, model: nn.Module, dataset_dir: Path, horizon_step: int, batch_size: int,
    device: torch.device, metadata: dict[str, object]
) -> dict[str, dict[str, float]]:
    normalization = metadata["normalization"]
    mean, std = float(normalization["target_mean"]), float(normalization["target_std"])
    return {
        split: _evaluate(
            model,
            DataLoader(TensorDataset(*_load_split(dataset_dir / f"{split}.npz", horizon_step)), batch_size=batch_size),
            device,
            mean,
            std,
        )
        for split in ("validation", "test_in_distribution", "test_extrapolation")
    }


def evaluate_checkpoint(args: argparse.Namespace) -> dict[str, object]:
    metadata = json.loads((args.dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    if args.horizon_step != int(metadata["n_pred"]):
        raise ValueError("STGCN evaluation must use the prepared final forecast horizon.")
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    model = _build_model(
        metadata=metadata, stgcn_root=args.stgcn_root, dataset_dir=args.dataset_dir,
        device=device, dropout=args.dropout,
    )
    checkpoint = args.output_dir / "best.pt"
    if not checkpoint.is_file():
        raise FileNotFoundError(f"evaluate-only requires {checkpoint}")
    saved = torch.load(checkpoint, map_location=device)
    model.load_state_dict(saved["model"])
    result: dict[str, object] = {
        "model": "STGCN-Cheb direct forecast",
        "horizon_steps": args.horizon_step,
        "horizon_seconds": args.horizon_step * float(metadata["interval_seconds"]),
        "best_epoch": int(saved["epoch"]),
        "best_validation_mse": float(saved["validation_mse"]),
        "normalization_fit_split": metadata["normalization"]["fit_split"],
        "lane_count": int(metadata["lane_count"]),
        **_evaluate_splits(
            model=model, dataset_dir=args.dataset_dir, horizon_step=args.horizon_step,
            batch_size=args.batch_size, device=device, metadata=metadata,
        ),
    }
    metrics_path = args.output_dir / "metrics.json"
    if metrics_path.is_file():
        existing = json.loads(metrics_path.read_text(encoding="utf-8"))
        result = {**existing, **result}
    metrics_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def train(args: argparse.Namespace) -> dict[str, object]:
    _set_seed(args.seed)
    metadata = json.loads((args.dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    if args.horizon_step != int(metadata["n_pred"]):
        raise ValueError(
            "This direct STGCN run must use the prepared final horizon; "
            f"requested {args.horizon_step}, prepared n_pred={metadata['n_pred']}."
        )
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    model = _build_model(
        metadata=metadata, stgcn_root=args.stgcn_root, dataset_dir=args.dataset_dir,
        device=device, dropout=args.dropout,
    )
    train_x, train_y = _load_split(args.dataset_dir / "train.npz", args.horizon_step)
    val_x, val_y = _load_split(args.dataset_dir / "validation.npz", args.horizon_step)
    train_loader = DataLoader(TensorDataset(train_x, train_y), batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(TensorDataset(val_x, val_y), batch_size=args.batch_size, shuffle=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.95)
    loss = nn.MSELoss()
    best_loss = float("inf")
    wait = 0
    history = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output_dir / "best.pt"
    last_checkpoint = args.output_dir / "last.pt"
    start_epoch = 1
    if args.resume:
        if not last_checkpoint.is_file():
            raise FileNotFoundError(
                f"--resume requires {last_checkpoint}; the incomplete run cannot be resumed safely."
            )
        saved_state = torch.load(last_checkpoint, map_location=device)
        model.load_state_dict(saved_state["model"])
        optimizer.load_state_dict(saved_state["optimizer"])
        scheduler.load_state_dict(saved_state["scheduler"])
        best_loss = float(saved_state["best_validation_mse"])
        wait = int(saved_state["wait"])
        history = list(saved_state["history"])
        _restore_rng_state(saved_state["rng_state"])
        start_epoch = int(saved_state["epoch"]) + 1
        print(f"resuming_from_epoch={start_epoch - 1}")
    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        total_loss = 0.0
        total_samples = 0
        for x, y in train_loader:
            optimizer.zero_grad()
            prediction = model(x.to(device)).reshape(len(x), -1)
            batch_loss = loss(prediction, y.to(device))
            batch_loss.backward()
            optimizer.step()
            total_loss += batch_loss.item() * len(x)
            total_samples += len(x)
        scheduler.step()
        model.eval()
        validation_loss = 0.0
        validation_samples = 0
        with torch.no_grad():
            for x, y in val_loader:
                batch_loss = loss(model(x.to(device)).reshape(len(x), -1), y.to(device))
                validation_loss += batch_loss.item() * len(x)
                validation_samples += len(x)
        validation_loss /= validation_samples
        record = {"epoch": epoch, "train_mse": total_loss / total_samples, "validation_mse": validation_loss}
        history.append(record)
        print(json.dumps(record))
        if validation_loss < best_loss:
            best_loss = validation_loss
            wait = 0
            torch.save({"model": model.state_dict(), "epoch": epoch, "validation_mse": best_loss}, checkpoint)
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
                "rng_state": _rng_state(),
            },
            last_checkpoint,
        )
        if wait >= args.patience:
            print(f"early_stop_epoch={epoch}")
            break
    saved = torch.load(checkpoint, map_location=device)
    model.load_state_dict(saved["model"])
    normalization = metadata["normalization"]
    result = {
        "model": "STGCN-Cheb direct forecast",
        "horizon_steps": args.horizon_step,
        "horizon_seconds": args.horizon_step * float(metadata["interval_seconds"]),
        "best_epoch": int(saved["epoch"]),
        "best_validation_mse": float(saved["validation_mse"]),
        **_evaluate_splits(
            model=model, dataset_dir=args.dataset_dir, horizon_step=args.horizon_step,
            batch_size=args.batch_size, device=device, metadata=metadata,
        ),
        "normalization_fit_split": normalization["fit_split"],
        "lane_count": int(metadata["lane_count"]),
        "history": history,
    }
    (args.output_dir / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train STGCN on episode-bounded stage-1 tensors.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--stgcn-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=0.001)
    parser.add_argument("--dropout", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--horizon-step", type=int, default=12)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--resume", action="store_true", help="resume from output-dir/last.pt")
    parser.add_argument("--evaluate-only", action="store_true", help="recompute metrics from output-dir/best.pt without training")
    args = parser.parse_args(argv)
    action = evaluate_checkpoint if args.evaluate_only else train
    print(json.dumps(action(args), indent=2))


if __name__ == "__main__":
    main()
