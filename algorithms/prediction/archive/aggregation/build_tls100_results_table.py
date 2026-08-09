"""Build a comparable 60-second results table for the TLS100 junction task."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


SPLITS = ("validation", "test_in_distribution", "test_extrapolation")
MODELS = (
    "persistence",
    "moving_average",
    "historical_average",
    "XGBoost",
    "STGCN",
)
SPLIT_LABELS = {
    "validation": "Validation",
    "test_in_distribution": "Test (ID)",
    "test_extrapolation": "Test (OOD)",
}


def _metrics(row: dict[str, object]) -> dict[str, float]:
    return {name: float(row[name]) for name in ("mae", "rmse", "mape", "smape", "wmape")}


def build_rows(
    *,
    baseline_path: Path,
    xgb_path: Path,
    stgcn_path: Path,
    horizon_seconds: float = 60.0,
) -> list[dict[str, object]]:
    """Load matching metrics for one direct forecast horizon."""

    baselines = json.loads(baseline_path.read_text(encoding="utf-8"))
    xgb = json.loads(xgb_path.read_text(encoding="utf-8"))
    stgcn = json.loads(stgcn_path.read_text(encoding="utf-8"))

    indexed: dict[tuple[str, str], dict[str, float]] = {}
    for row in baselines:
        if float(row["horizon_seconds"]) == horizon_seconds:
            indexed[(str(row["split"]), str(row["model"]))] = _metrics(row)
    for split in SPLITS:
        indexed[(split, "XGBoost")] = _metrics(xgb[split])
        indexed[(split, "STGCN")] = _metrics(stgcn[split])

    missing = [
        f"{split}/{model}"
        for split in SPLITS
        for model in MODELS
        if (split, model) not in indexed
    ]
    if missing:
        raise ValueError(f"missing comparable metrics: {', '.join(missing)}")

    return [
        {
            "split": split,
            "model": model,
            "horizon_seconds": horizon_seconds,
            **indexed[(split, model)],
        }
        for split in SPLITS
        for model in MODELS
    ]


def write_results(
    rows: list[dict[str, object]], *, csv_path: Path, markdown_path: Path
) -> None:
    if not rows:
        raise ValueError("cannot write an empty result table")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# TLS100 junction prediction: comparable 60-second results",
        "",
        "All methods predict vehicle_count 60 seconds ahead on the same 100 traffic-light junction nodes and episode splits.",
        "",
    ]
    for split in SPLITS:
        lines.extend(
            [
                f"## {SPLIT_LABELS[split]}",
                "",
                "| Method | MAE | RMSE | sMAPE | WMAPE |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in (item for item in rows if item["split"] == split):
            lines.append(
                f"| {row['model']} | {float(row['mae']):.3f} | {float(row['rmse']):.3f} | "
                f"{float(row['smape']):.2%} | {float(row['wmape']):.2%} |"
            )
        lines.append("")
    lines.extend(
        [
            "Note: sMAPE is zero-safe. MAPE excludes true vehicle counts below 0.5 after de-normalization and remains supplementary; use MAE, RMSE, sMAPE, and WMAPE for comparison.",
            "",
        ]
    )
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build the TLS100 comparable 60-second results table."
    )
    parser.add_argument("--formal-dir", type=Path, required=True)
    parser.add_argument("--baseline-name", default="baseline_metrics_three.json")
    parser.add_argument("--csv-name", default="results_summary_60s.csv")
    parser.add_argument("--markdown-name", default="results_summary_60s.md")
    args = parser.parse_args(argv)

    rows = build_rows(
        baseline_path=args.formal_dir / args.baseline_name,
        xgb_path=args.formal_dir / "xgb" / "metrics.json",
        stgcn_path=args.formal_dir / "stgcn" / "metrics.json",
    )
    csv_path = args.formal_dir / args.csv_name
    markdown_path = args.formal_dir / args.markdown_name
    write_results(rows, csv_path=csv_path, markdown_path=markdown_path)
    print(f"csv={csv_path}")
    print(f"markdown={markdown_path}")


if __name__ == "__main__":
    main()
