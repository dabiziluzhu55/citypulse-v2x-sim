"""Batch junction and TAZ clipping — parse the net.xml once, clip all configured targets.

Produced output structure::

    # Single-junction batch mode (--config)
    output/
    ├── demo_1/
    │   ├── demo_1.clipped.net.xml
    │   └── demo_1.xodr
    ├── demo_2/
    │   ├── demo_2.clipped.net.xml
    │   └── demo_2.xodr
    └── ...

    # TAZ batch mode (--taz-config)
    output/
    ├── TAZ_2/
    │   ├── TAZ_2.clipped.net.xml
    │   └── TAZ_2.xodr
    └── ...
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

from .net_parser import parse_net_xml
from .net_writer import write_clipped_net
from .netconvert_runner import run_netconvert
from .topological_clipper import clip_by_topological_distance, clip_taz


def load_config(config_path: str) -> Dict[str, Any]:
    """Load the intersection config JSON.

    Returns:
        Dict with keys: version, description, source_net, default_distance_m,
        intersections (list of {name, junction_id, note}).

    Raises:
        FileNotFoundError, json.JSONDecodeError, KeyError (missing required fields).
    """
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Basic validation
    if "intersections" not in cfg:
        raise KeyError("Config is missing 'intersections' key")
    for i, entry in enumerate(cfg["intersections"]):
        if "name" not in entry:
            raise KeyError(f"Intersection entry {i} is missing 'name'")
        if "junction_id" not in entry:
            raise KeyError(f"Intersection '{entry['name']}' is missing 'junction_id'")

    return cfg


def batch_clip(
    net_path: str,
    config_path: str,
    output_dir: Optional[str] = None,
    dist_m: Optional[float] = None,
    netconvert_bin: str = "netconvert",
    timeout_s: int = 300,
    skip_netconvert: bool = False,
) -> Dict[str, List[str]]:
    if output_dir is None:
        import toolchain_env
        output_dir = toolchain_env.resolve_maps_xodr_dir("")
    """Batch-clip all intersections defined in the config file.

    The graph is parsed **once** from *net_path* and reused for every
    intersection — the most expensive step for large (44 MB+) files.

    Args:
        net_path: Path to the large SUMO .net.xml.
        config_path: Path to intersection_config.json.
        output_dir: Root directory for output (default ``output/``).
        dist_m: Topological distance override.  Falls back to the config's
            ``default_distance_m`` if not given.
        netconvert_bin: Path / name of the netconvert binary.
        timeout_s: Timeout in seconds per netconvert invocation.
        skip_netconvert: If True, only produce .net.xml files.

    Returns:
        ``{"success": [...], "skipped": [...], "failed": [...]}``
    """
    # ── Load config ──
    cfg = load_config(config_path)
    intersections: List[Dict[str, Any]] = cfg["intersections"]

    if dist_m is None:
        dist_m = cfg.get("default_distance_m", 200.0)

    # ── Identify targets ──
    targets = [e for e in intersections if e["junction_id"] is not None]
    skipped_list = [e["name"] for e in intersections if e["junction_id"] is None]

    if not targets:
        print("[Batch] No intersections with a valid junction_id — nothing to do.")
        return {"success": [], "skipped": skipped_list, "failed": []}

    print(f"[Batch] {len(targets)} intersection(s) to process "
          f"(dist={dist_m:.0f}m, {len(skipped_list)} skipped due to null junction_id)")

    # ── Parse net.xml once ──
    print(f"\n[Batch] Parsing {net_path} (this is done once) ...")
    graph = parse_net_xml(net_path)

    # ── Process each intersection ──
    success: List[str] = []
    failed: List[str] = []

    for idx, entry in enumerate(targets):
        name = entry["name"]
        junction_id = str(entry["junction_id"])

        out_subdir = os.path.join(output_dir, name)
        os.makedirs(out_subdir, exist_ok=True)

        net_out = os.path.join(out_subdir, f"{name}.clipped.net.xml")
        xodr_out = os.path.join(out_subdir, f"{name}.xodr")

        print(f"\n{'─' * 55}")
        print(f"[Batch] [{idx + 1}/{len(targets)}] {name} — junction {junction_id}")
        print(f"{'─' * 55}")

        try:
            # Clip
            kept_edges, kept_junctions, kept_connections, kept_tl_logics = (
                clip_by_topological_distance(graph, junction_id, dist_m)
            )

            # Write .net.xml
            write_clipped_net(
                graph, kept_edges, kept_junctions,
                kept_connections, kept_tl_logics,
                net_out,
            )

            # Convert to .xodr
            if not skip_netconvert:
                try:
                    run_netconvert(
                        net_out, xodr_out,
                        netconvert_bin=netconvert_bin,
                        timeout_s=timeout_s,
                    )
                except FileNotFoundError:
                    print(f"[Batch] netconvert not found — .net.xml saved to: {net_out}")
                    print(f"[Batch]  Convert manually on the server with:")
                    print(f"[Batch]    netconvert --sumo-net-file {net_out} "
                          f"--opendrive-output {xodr_out}")

            success.append(name)

        except KeyError as e:
            print(f"[Batch] ERROR: Junction ID '{junction_id}' not found in net.xml: {e}")
            failed.append(name)
        except Exception as e:
            print(f"[Batch] ERROR processing {name}: {e}")
            failed.append(name)

    # ── Summary ──
    print(f"\n{'═' * 55}")
    print(f"[Batch] Done!")
    print(f"  Success:  {len(success)} — {', '.join(success) if success else '(none)'}")
    print(f"  Skipped:  {len(skipped_list)} — {', '.join(skipped_list) if skipped_list else '(none)'}")
    print(f"  Failed:   {len(failed)} — {', '.join(failed) if failed else '(none)'}")
    print(f"  Output:   {os.path.abspath(output_dir)}/")
    print(f"{'═' * 55}")

    return {"success": success, "skipped": skipped_list, "failed": failed}


# ═══════════════════════════════════════════════════════════════════════
#  TAZ (Typical Area Zone) batch processing
# ═══════════════════════════════════════════════════════════════════════

def load_taz_config(taz_config_path: str) -> Dict[str, Any]:
    """Load the TAZ configuration JSON.

    Returns:
        Dict with keys: version, description, taz_groups (list of {name, intersections}).

    Raises:
        FileNotFoundError, json.JSONDecodeError, KeyError (missing required fields).
    """
    with open(taz_config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "taz_groups" not in cfg:
        raise KeyError("TAZ config is missing 'taz_groups' key")
    for i, entry in enumerate(cfg["taz_groups"]):
        if "name" not in entry:
            raise KeyError(f"TAZ group entry {i} is missing 'name'")
        if "intersections" not in entry:
            raise KeyError(f"TAZ group '{entry.get('name', i)}' is missing 'intersections'")
        if not isinstance(entry["intersections"], list) or len(entry["intersections"]) == 0:
            raise KeyError(
                f"TAZ group '{entry['name']}' has empty or invalid 'intersections' list"
            )

    return cfg


def _build_name_to_junction_id(intersection_config: Dict[str, Any]) -> Dict[str, str]:
    """Build a lookup from intersection name → junction_id from config.json."""
    lookup: Dict[str, str] = {}
    for entry in intersection_config.get("intersections", []):
        name = entry.get("name")
        jid = entry.get("junction_id")
        if name and jid:
            lookup[name] = str(jid)
    return lookup


def batch_clip_taz(
    net_path: str,
    taz_config_path: str,
    intersection_config_path: str,
    output_dir: Optional[str] = None,
    dist_m: Optional[float] = None,
    path_dist_m: float = 50.0,
    netconvert_bin: str = "netconvert",
    timeout_s: int = 300,
    skip_netconvert: bool = False,
) -> Dict[str, List[str]]:
    if output_dir is None:
        import toolchain_env
        output_dir = toolchain_env.resolve_maps_xodr_dir("")
    """Batch-clip all TAZ groups defined in the TAZ config file.

    The graph is parsed **once** from *net_path* and reused for every
    TAZ group.

    Intersection names from the TAZ config are resolved to junction IDs
    via the intersection config (config.json).

    Args:
        net_path: Path to the large SUMO .net.xml.
        taz_config_path: Path to TazConfig.json.
        intersection_config_path: Path to config.json (intersection → junction_id mapping).
        output_dir: Root directory for output (default ``output/``).
        dist_m: Topological distance for seed-junction expansion. Falls back to
            the TAZ config's ``default_distance_m`` if not given, then to 200.0.
        path_dist_m: Smaller expansion distance for MST path junctions
            (default 50.0).
        netconvert_bin: Path / name of the netconvert binary.
        timeout_s: Timeout in seconds per netconvert invocation.
        skip_netconvert: If True, only produce .net.xml files.

    Returns:
        ``{"success": [...], "failed": [...]}``
    """
    # ── Load configs ──
    taz_cfg = load_taz_config(taz_config_path)
    taz_groups: List[Dict[str, Any]] = taz_cfg["taz_groups"]

    with open(intersection_config_path, "r", encoding="utf-8") as f:
        intersection_cfg = json.load(f)

    name_to_jid = _build_name_to_junction_id(intersection_cfg)

    if dist_m is None:
        dist_m = taz_cfg.get("default_distance_m", 200.0)

    if not taz_groups:
        print("[TAZ Batch] No TAZ groups defined — nothing to do.")
        return {"success": [], "failed": []}

    print(f"[TAZ Batch] {len(taz_groups)} TAZ group(s) to process "
          f"(dist={dist_m:.0f}m per junction)")

    # ── Parse net.xml once ──
    print(f"\n[TAZ Batch] Parsing {net_path} (this is done once) ...")
    graph = parse_net_xml(net_path)

    # ── Process each TAZ group ──
    success: List[str] = []
    failed: List[str] = []

    for idx, taz_entry in enumerate(taz_groups):
        taz_name = taz_entry["name"]
        intersection_names = taz_entry["intersections"]

        # Resolve intersection names → junction IDs
        junction_ids: List[str] = []
        unresolved: List[str] = []
        for iname in intersection_names:
            jid = name_to_jid.get(iname)
            if jid:
                junction_ids.append(jid)
            else:
                unresolved.append(iname)

        if unresolved:
            print(
                f"\n[TAZ Batch] WARNING: TAZ '{taz_name}' has unresolved "
                f"intersection names: {unresolved}. "
                f"Available names: {sorted(name_to_jid.keys())}"
            )
        if not junction_ids:
            print(f"[TAZ Batch] ERROR: TAZ '{taz_name}' has no valid junction IDs — skipping.")
            failed.append(taz_name)
            continue

        out_subdir = os.path.join(output_dir, taz_name)
        os.makedirs(out_subdir, exist_ok=True)

        net_out = os.path.join(out_subdir, f"{taz_name}.clipped.net.xml")
        xodr_out = os.path.join(out_subdir, f"{taz_name}.xodr")

        print(f"\n{'─' * 55}")
        print(f"[TAZ Batch] [{idx + 1}/{len(taz_groups)}] {taz_name} — "
              f"{len(junction_ids)} junctions: {intersection_names}")
        print(f"{'─' * 55}")

        try:
            # Clip TAZ
            kept_edges, kept_junctions, kept_connections, kept_tl_logics = (
                clip_taz(graph, junction_ids, dist_m, path_dist_m)
            )

            # Write .net.xml
            write_clipped_net(
                graph, kept_edges, kept_junctions,
                kept_connections, kept_tl_logics,
                net_out,
            )

            # Convert to .xodr
            if not skip_netconvert:
                try:
                    run_netconvert(
                        net_out, xodr_out,
                        netconvert_bin=netconvert_bin,
                        timeout_s=timeout_s,
                    )
                except FileNotFoundError:
                    print(f"[TAZ Batch] netconvert not found — .net.xml saved to: {net_out}")
                    print(f"[TAZ Batch]  Convert manually on the server with:")
                    print(f"[TAZ Batch]    netconvert --sumo-net-file {net_out} "
                          f"--opendrive-output {xodr_out}")

            success.append(taz_name)

        except KeyError as e:
            print(f"[TAZ Batch] ERROR: {e}")
            failed.append(taz_name)
        except Exception as e:
            print(f"[TAZ Batch] ERROR processing {taz_name}: {e}")
            failed.append(taz_name)

    # ── Summary ──
    print(f"\n{'═' * 55}")
    print(f"[TAZ Batch] Done!")
    print(f"  Success:  {len(success)} — {', '.join(success) if success else '(none)'}")
    print(f"  Failed:   {len(failed)} — {', '.join(failed) if failed else '(none)'}")
    print(f"  Output:   {os.path.abspath(output_dir)}/")
    print(f"{'═' * 55}")

    return {"success": success, "failed": failed}
