"""Migrate a v1 IPPO checkpoint to checkpoint contract v2 via a sidecar.

Usage:
  python3 tools/migrate_checkpoint_contract.py \\
      --checkpoint traffic_control/ippo/models/ippo_v8_20tls_ep160.pt \\
      --metadata algorithms/ippo/regression_golden/metadata_xiongan20.json

The tool refuses to overwrite an existing sidecar unless --force is given,
and refuses to run when the checkpoint is already contract v2 (inline).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from traffic_control.ippo.contract import (  # noqa: E402
    build_sidecar,
    load_contract,
    sidecar_path,
)
from traffic_control.ippo.controller import (  # noqa: E402
    _build_index,
    load_checkpoint_metadata,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS  # noqa: E402


def _fingerprints_from_metadata(
    metadata: dict,
    intersection_ids: tuple[str, ...],
) -> dict[str, dict]:
    intersections = metadata.get("intersections", {})
    result: dict[str, dict] = {}
    for iid in intersection_ids:
        item = intersections.get(iid)
        if item is None:
            raise ValueError(
                f"Metadata has no intersection {iid!r}; xiongan20 metadata is required."
            )
        result[str(iid)] = item
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    checkpoint_path = args.checkpoint.expanduser().resolve()
    if not checkpoint_path.is_file():
        parser.error(f"checkpoint does not exist: {checkpoint_path}")
    checkpoint = load_checkpoint_metadata(checkpoint_path)
    if checkpoint.get("checkpoint_contract_version") is not None:
        parser.error("checkpoint already carries inline contract v2; no sidecar needed")

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    saved_ids = tuple(str(iid) for iid in checkpoint.get("intersection_ids", ()))
    if not set(saved_ids) <= set(IDENTITY_SLOT_IDS):
        parser.error("checkpoint intersection_ids are not a subset of the canonical slots")

    # Build live metadata entries (Protocol 2.0 shape) for every training id.
    intersections = metadata.get("intersections", {})
    fingerprints: dict[str, dict] = {}
    for iid in saved_ids:
        item = intersections.get(iid)
        if item is None:
            parser.error(f"metadata is missing intersection {iid!r}")
        fingerprints[iid] = _build_index(item)

    # Fingerprint objects are _Idx; convert to contract dicts via the shared helper.
    from traffic_control.ippo.contract import intersection_fingerprint_from_index

    fingerprint_payload = {
        iid: intersection_fingerprint_from_index(index)
        for iid, index in fingerprints.items()
    }
    sidecar = build_sidecar(
        checkpoint_path,
        checkpoint=checkpoint,
        fingerprints=fingerprint_payload,
    )
    target = sidecar_path(checkpoint_path)
    if target.exists() and not args.force:
        parser.error(f"sidecar already exists: {target}; use --force to overwrite")

    target.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Self-verification: reloading must succeed and fingerprints must match.
    _contract_version, view = load_contract(checkpoint_path, checkpoint)
    assert set(view["per_intersection_fingerprints"]) >= set(saved_ids)
    print(f"wrote sidecar -> {target} ({len(saved_ids)} intersections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
