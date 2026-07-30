"""Build a reproducible spatial k-nearest-neighbour graph for official20 nodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def build(tls_manifest: Path, intersections: Path, output: Path, neighbors: int) -> dict[str, object]:
    targets = tuple(json.loads(tls_manifest.read_text(encoding="utf-8"))["intersections"])
    mapping = json.loads(intersections.read_text(encoding="utf-8"))
    if len(targets) != 20 or any(node not in mapping for node in targets):
        raise ValueError("official20 target mapping is incomplete")
    coords = np.asarray([[mapping[node]["lon"], mapping[node]["lat"]] for node in targets], dtype=np.float64)
    # A local tangent-plane approximation is sufficient for neighbour ordering.
    delta = coords[:, None, :] - coords[None, :, :]
    delta[..., 0] *= 111_320 * np.cos(np.deg2rad(coords[:, None, 1]))
    delta[..., 1] *= 110_540
    distance = np.sqrt(np.square(delta).sum(axis=2))
    adjacency = np.zeros((len(targets), len(targets)), dtype=np.float32)
    for source in range(len(targets)):
        for target in np.argsort(distance[source])[1 : neighbors + 1]:
            adjacency[source, target] = adjacency[target, source] = 1.0
    np.savez_compressed(output, adjacency=adjacency, nodes=np.asarray(targets))
    return {"nodes": list(targets), "neighbors": neighbors, "edges": int(adjacency.sum() / 2)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build official20 spatial kNN adjacency.")
    parser.add_argument("--tls-manifest", type=Path, required=True)
    parser.add_argument("--intersections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--neighbors", type=int, default=3)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.tls_manifest, args.intersections, args.output, args.neighbors), indent=2))


if __name__ == "__main__":
    main()
