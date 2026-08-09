"""Canonical training entry point for the NarrowNet-TDP model.

The implementation is kept in the archived multi-mode trainer for now so old
experiments remain reproducible.  This small entry point fixes the production
spatial mode to ``static_directional`` and prevents accidentally launching a
historical dynamic or hierarchical experiment under the final method name.
"""

from __future__ import annotations

import sys

from .archive.experiments.train_dynamic_lane_v1 import main as _legacy_main


def _with_fixed_spatial_mode(argv: list[str]) -> list[str]:
    for index, argument in enumerate(argv):
        if argument == "--spatial-mode":
            if index + 1 >= len(argv) or argv[index + 1] != "static_directional":
                raise ValueError(
                    "train_narrow_net_tdp only supports --spatial-mode static_directional"
                )
            return argv
        if argument.startswith("--spatial-mode="):
            if argument.split("=", 1)[1] != "static_directional":
                raise ValueError(
                    "train_narrow_net_tdp only supports --spatial-mode static_directional"
                )
            return argv
    return [*argv, "--spatial-mode", "static_directional"]


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    _legacy_main(_with_fixed_spatial_mode(arguments))


if __name__ == "__main__":
    main()
