"""Canonical layout for rebuildable SUMO artifacts."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GENERATED_DIR = PROJECT_ROOT / "data" / "maps" / "sumo" / "generated"


@dataclass(frozen=True)
class GeneratedArtifactLayout:
    """Resolve every generated path from one output root."""

    root: Path

    @property
    def network_file(self) -> Path:
        return self.root / "network" / "TotalMap_20.signals.net.xml"

    @property
    def signal_programs_file(self) -> Path:
        return self.root / "signals" / "official_tls.add.xml"

    @property
    def tls_manifest(self) -> Path:
        return self.root / "manifests" / "tls_manifest.json"

    @property
    def traffic_manifest(self) -> Path:
        return self.root / "manifests" / "traffic_manifest.json"

    @property
    def connections_report(self) -> Path:
        return self.root / "reports" / "official_tls_connections.csv"

    def traffic_scenario_dir(self, intersection_id: str, period_id: str) -> Path:
        return self.root / "traffic" / intersection_id / period_id

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def create_base_directories(self) -> None:
        for directory in (
            self.network_file.parent,
            self.signal_programs_file.parent,
            self.tls_manifest.parent,
            self.connections_report.parent,
            self.root / "traffic",
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        """Remove stale generated output before a complete TLS rebuild."""

        if self.root.exists():
            shutil.rmtree(self.root)
        self.create_base_directories()
