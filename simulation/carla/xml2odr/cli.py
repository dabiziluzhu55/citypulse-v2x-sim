"""CLI entry point for XML2Odr tool — single-junction, batch, and TAZ modes."""

import argparse
import os
import sys
import tempfile

from .batch_clip import batch_clip, batch_clip_taz
from .net_parser import parse_net_xml
from .netconvert_runner import run_netconvert
from .net_writer import write_clipped_net
from .topological_clipper import clip_by_topological_distance, clip_taz
from .batch_clip import load_config


def main(argv=None):
    """Main CLI entry point.

    Supports three modes:

    *Single-junction mode* (--junction + --output)::

        python run_xml2odr.py --net TotalMap.net.xml --junction J1 --dist 200 -o out.xodr

    *Batch mode* (--config + --output-dir)::

        python run_xml2odr.py --net TotalMap.net.xml --config config/intersections.json --dist 200 --output-dir output

    *TAZ mode* (--taz-config + --output-dir  or  --taz-config + --taz + --output)::

        python run_xml2odr.py --net TotalMap.net.xml --taz-config config/taz.json --dist 200 --output-dir output

        python run_xml2odr.py --net TotalMap.net.xml --taz-config config/taz.json --taz TAZ_2 --dist 200 -o output.xodr
    """
    parser = argparse.ArgumentParser(
        description=(
            "Extract junction-level or TAZ-level .xodr files from a large SUMO .net.xml. "
            "Supports single-junction mode (--junction), batch junction mode (--config), "
            "and TAZ mode (--taz-config)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single junction mode
  python run_xml2odr.py --net TotalMap.net.xml --junction J1 --dist 200 -o output.xodr

  # Single junction, keep intermediate net.xml
  python run_xml2odr.py --net TotalMap.net.xml --junction J1 --dist 100 \\
      -o output.xodr --keep-net

  # Batch mode — process all intersections from config
  python run_xml2odr.py --net TotalMap.net.xml --config config/intersections.json \\
      --dist 200 --output-dir output --keep-net --skip-netconvert

  # TAZ batch mode — process all TAZ groups
  python run_xml2odr.py --net TotalMap.net.xml --taz-config config/taz.json \\
      --intersection-config config/intersections.json --dist 200 --output-dir output

  # Single TAZ mode — process one TAZ group
  python run_xml2odr.py --net TotalMap.net.xml --taz-config config/taz.json \\
      --taz TAZ_2 --dist 200 -o TAZ_2.xodr
        """,
    )

    # ── Common arguments ──
    parser.add_argument(
        '--net', default='',
        help="Path to input SUMO .net.xml file (default: config/toolchain.json "
             "totalmap_net, 即 ../../data/maps/sumo/generated/network/"
             "TotalMap_20.signals.net.xml)",
    )
    parser.add_argument(
        '--dist', type=float, default=200.0,
        help='Topological distance from seed junction(s) in meters (default: 200)',
    )
    parser.add_argument(
        '--path-dist', type=float, default=50.0,
        help='TAZ: expansion distance for MST path junctions in meters '
             '(default: 50). Independent of --dist.',
    )
    parser.add_argument(
        '--keep-net', action='store_true',
        help='Also save the intermediate clipped .net.xml(s)',
    )
    parser.add_argument(
        '--netconvert-bin', default='netconvert',
        help="Path to netconvert binary (default: 自动经 toolchain_env 解析 — "
             "PATH / SUMO_HOME/bin / config/toolchain.json sumo_home)",
    )
    parser.add_argument(
        '--timeout', type=int, default=300,
        help='Timeout in seconds for netconvert (default: 300)',
    )
    parser.add_argument(
        '--skip-netconvert', action='store_true',
        help='Stop after generating clipped .net.xml (do not convert to .xodr)',
    )

    # ── Single-junction mode ──
    parser.add_argument(
        '--junction',
        help="Seed junction ID (e.g. '317'). Required for single-junction mode.",
    )
    parser.add_argument(
        '--output', '-o',
        help='Output .xodr file path (single-junction or single-TAZ mode).',
    )

    # ── Batch junction mode ──
    parser.add_argument(
        '--config',
        help='Path to intersections.json (batch junction mode).',
    )
    parser.add_argument(
        '--output-dir', default='',
        help="Root output directory for batch mode (default: config/toolchain.json "
             "maps_xodr_dir, 即 ../../data/maps/xodr).",
    )

    # ── TAZ mode ──
    parser.add_argument(
        '--taz-config',
        help='Path to taz.json (enables TAZ batch or single-TAZ mode).',
    )
    parser.add_argument(
        '--taz',
        help='TAZ group name to process (single-TAZ mode, requires --taz-config).',
    )
    parser.add_argument(
        '--intersection-config', default='config/intersections.json',
        help='Path to intersection config for name→junction_id resolution '
             '(default: config/intersections.json). Used with --taz-config.',
    )

    args = parser.parse_args(argv)

    # netconvert / 输出目录 / 源路网统一经 toolchain_env 解析
    # (CLI > config/toolchain.json > 内置默认)
    try:
        import toolchain_env
        if not args.skip_netconvert and args.netconvert_bin == 'netconvert':
            resolved = toolchain_env.find_netconvert()
            if resolved:
                args.netconvert_bin = resolved
        args.output_dir = toolchain_env.resolve_maps_xodr_dir(args.output_dir)
        args.net = args.net or toolchain_env.resolve_totalmap_net("")
    except ImportError:
        args.output_dir = args.output_dir or 'output'  # toolchain_env 缺失时回退原默认
        args.net = args.net or 'TotalMap.net.xml'

    # ── Mode detection ──
    taz_mode = args.taz_config is not None
    batch_mode = args.config is not None
    single_mode = args.junction is not None
    taz_single_mode = taz_mode and args.taz is not None
    taz_batch_mode = taz_mode and args.taz is None

    # Count active modes — they must be mutually exclusive
    active_modes = sum([bool(taz_mode), bool(batch_mode), bool(single_mode)])
    if active_modes > 1:
        print(
            "Error: --junction, --config, and --taz-config are mutually exclusive.",
            file=sys.stderr,
        )
        sys.exit(1)
    if active_modes == 0:
        print(
            "Error: specify one of --junction (single mode), "
            "--config (batch mode), or --taz-config (TAZ mode).",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Validate inputs ──
    if not os.path.exists(args.net):
        print(f"Error: Input file not found: {args.net}", file=sys.stderr)
        sys.exit(1)

    if args.dist < 0:
        print(f"Error: --dist must be non-negative, got {args.dist}", file=sys.stderr)
        sys.exit(1)

    if single_mode and not args.output:
        print("Error: --output/-o is required for single-junction mode.", file=sys.stderr)
        sys.exit(1)

    if taz_single_mode and not args.output:
        print("Error: --output/-o is required for single-TAZ mode.", file=sys.stderr)
        sys.exit(1)

    if taz_mode and not os.path.exists(args.taz_config):
        print(f"Error: TAZ config file not found: {args.taz_config}", file=sys.stderr)
        sys.exit(1)

    if taz_mode and not os.path.exists(args.intersection_config):
        print(
            f"Error: Intersection config file not found: {args.intersection_config}",
            file=sys.stderr,
        )
        sys.exit(1)

    if batch_mode and not os.path.exists(args.config):
        print(f"Error: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    # ── Dispatch ──
    if taz_single_mode:
        _run_taz_single(args)
    elif taz_batch_mode:
        _run_taz_batch(args)
    elif batch_mode:
        _run_batch(args)
    else:
        _run_single(args)


def _run_single(args):
    """Single-junction clipping (original mode)."""
    print("=" * 60)
    print("XML2Odr — SUMO .net.xml to CARLA .xodr converter (single-junction)")
    print("=" * 60)
    print(f"  Input:         {args.net}")
    print(f"  Seed junction: {args.junction}")
    print(f"  Distance:      {args.dist:.1f} m (topological, along-road)")
    print(f"  Output:        {args.output}")
    if args.skip_netconvert:
        print(f"  Mode:          Clipped .net.xml only (--skip-netconvert)")
    else:
        print(f"  netconvert:    {args.netconvert_bin} (timeout: {args.timeout}s)")
    print("=" * 60)

    # ── Step 1: Parse .net.xml ──
    print("\n[Step 1/4] Parsing .net.xml ...")
    try:
        graph = parse_net_xml(args.net)
    except FileNotFoundError:
        print(f"Error: File not found: {args.net}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing net.xml: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: Clip ──
    print(f"\n[Step 2/4] Clipping around junction '{args.junction}' "
          f"with dist={args.dist:.1f}m ...")
    try:
        kept_edges, kept_junctions, kept_connections, kept_tl_logics = \
            clip_by_topological_distance(graph, args.junction, args.dist)
    except KeyError as e:
        msg = e.args[0] if e.args else str(e)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    # Validate non-empty output
    main_junctions = [
        j for j in kept_junctions
        if j in graph.junctions and not graph.junctions[j].is_internal
    ]
    if len(main_junctions) <= 1 and len(kept_edges) == 0:
        print(
            "\nWarning: Clipping resulted in an empty network. "
            "Try increasing --dist.",
            file=sys.stderr,
        )

    # ── Step 3: Write clipped .net.xml ──
    print(f"\n[Step 3/4] Writing clipped .net.xml ...")

    base_dir = os.path.dirname(args.output) or '.'
    base_name = os.path.splitext(os.path.basename(args.output))[0]

    if args.keep_net or args.skip_netconvert:
        net_output_path = os.path.join(base_dir, f"{base_name}.clipped.net.xml")
        print(f"  Keeping intermediate .net.xml: {net_output_path}")
    else:
        tmp_fd, net_output_path = tempfile.mkstemp(suffix='.net.xml', prefix='xml2odr_')
        os.close(tmp_fd)
        print(f"  Using temp file: {net_output_path}")

    write_clipped_net(
        graph, kept_edges, kept_junctions,
        kept_connections, kept_tl_logics,
        net_output_path,
    )

    # ── Step 4: Convert to .xodr (optional) ──
    if args.skip_netconvert:
        print(f"\n[Step 4/4] Skipped (--skip-netconvert)")
        print(f"\nDone! Clipped .net.xml written to: {net_output_path}")
        print(f"To convert to .xodr manually, run:")
        print(f"  netconvert --sumo-net-file {net_output_path} "
              f"--opendrive-output {args.output}")
    else:
        print(f"\n[Step 4/4] Converting to .xodr via netconvert ...")
        try:
            run_netconvert(
                net_output_path, args.output,
                netconvert_bin=args.netconvert_bin,
                timeout_s=args.timeout,
            )
        except FileNotFoundError as e:
            print(f"\nError: {e}", file=sys.stderr)
            if not args.keep_net:
                print(f"Clipped .net.xml is at: {net_output_path}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"\nError during netconvert: {e}", file=sys.stderr)
            if not args.keep_net:
                print(f"Clipped .net.xml is at: {net_output_path}", file=sys.stderr)
            sys.exit(1)

        if not args.keep_net:
            try:
                os.unlink(net_output_path)
            except OSError:
                pass

        print(f"\nDone! Output written to: {args.output}")


def _run_batch(args):
    """Batch junction clipping."""
    result = batch_clip(
        net_path=args.net,
        config_path=args.config,
        output_dir=args.output_dir,
        dist_m=args.dist,
        netconvert_bin=args.netconvert_bin,
        timeout_s=args.timeout,
        skip_netconvert=args.skip_netconvert,
    )

    if result["failed"]:
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════
#  TAZ mode handlers
# ═══════════════════════════════════════════════════════════════════════

def _run_taz_single(args):
    """Single-TAZ clipping — process one named TAZ group."""
    import json

    taz_name = args.taz

    # Load TAZ config and find the named group
    from .batch_clip import load_taz_config, _build_name_to_junction_id
    taz_cfg = load_taz_config(args.taz_config)

    # Find the matching TAZ group
    taz_entry = None
    for entry in taz_cfg["taz_groups"]:
        if entry["name"] == taz_name:
            taz_entry = entry
            break

    if taz_entry is None:
        available = [e["name"] for e in taz_cfg["taz_groups"]]
        print(
            f"Error: TAZ group '{taz_name}' not found in {args.taz_config}.\n"
            f"Available TAZ groups: {available}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve intersection names → junction IDs
    with open(args.intersection_config, "r", encoding="utf-8") as f:
        intersection_cfg = json.load(f)
    name_to_jid = _build_name_to_junction_id(intersection_cfg)

    intersection_names = taz_entry["intersections"]
    junction_ids = []
    unresolved = []
    for iname in intersection_names:
        jid = name_to_jid.get(iname)
        if jid:
            junction_ids.append(jid)
        else:
            unresolved.append(iname)

    if unresolved:
        print(
            f"Error: TAZ '{taz_name}' has unresolved intersection names: {unresolved}.\n"
            f"Available names: {sorted(name_to_jid.keys())}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not junction_ids:
        print(f"Error: TAZ '{taz_name}' has no valid junction IDs.", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("XML2Odr — SUMO .net.xml to CARLA .xodr converter (TAZ single)")
    print("=" * 60)
    print(f"  Input:              {args.net}")
    print(f"  TAZ config:         {args.taz_config}")
    print(f"  TAZ group:          {taz_name}")
    print(f"  Seed junctions:     {len(junction_ids)} — {intersection_names}")
    print(f"  Distance per junc:  {args.dist:.1f} m")
    print(f"  Path-junc distance: {args.path_dist:.1f} m")
    print(f"  Output:             {args.output}")
    if args.skip_netconvert:
        print(f"  Mode:               Clipped .net.xml only (--skip-netconvert)")
    else:
        print(f"  netconvert:         {args.netconvert_bin} (timeout: {args.timeout}s)")
    print("=" * 60)

    # ── Step 1: Parse .net.xml ──
    print("\n[Step 1/4] Parsing .net.xml ...")
    try:
        graph = parse_net_xml(args.net)
    except FileNotFoundError:
        print(f"Error: File not found: {args.net}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing net.xml: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: TAZ Clip ──
    print(f"\n[Step 2/4] TAZ clipping '{taz_name}' with {len(junction_ids)} seeds "
          f"(dist={args.dist:.1f}m, path_dist={args.path_dist:.1f}m) ...")
    try:
        kept_edges, kept_junctions, kept_connections, kept_tl_logics = \
            clip_taz(graph, junction_ids, args.dist, args.path_dist)
    except KeyError as e:
        msg = e.args[0] if e.args else str(e)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Write clipped .net.xml ──
    print(f"\n[Step 3/4] Writing clipped .net.xml ...")

    base_dir = os.path.dirname(args.output) or '.'
    base_name = os.path.splitext(os.path.basename(args.output))[0]

    if args.keep_net or args.skip_netconvert:
        net_output_path = os.path.join(base_dir, f"{base_name}.clipped.net.xml")
        print(f"  Keeping intermediate .net.xml: {net_output_path}")
    else:
        tmp_fd, net_output_path = tempfile.mkstemp(suffix='.net.xml', prefix='xml2odr_taz_')
        os.close(tmp_fd)
        print(f"  Using temp file: {net_output_path}")

    write_clipped_net(
        graph, kept_edges, kept_junctions,
        kept_connections, kept_tl_logics,
        net_output_path,
    )

    # ── Step 4: Convert to .xodr (optional) ──
    if args.skip_netconvert:
        print(f"\n[Step 4/4] Skipped (--skip-netconvert)")
        print(f"\nDone! Clipped .net.xml written to: {net_output_path}")
        print(f"To convert to .xodr manually, run:")
        print(f"  netconvert --sumo-net-file {net_output_path} "
              f"--opendrive-output {args.output}")
    else:
        print(f"\n[Step 4/4] Converting to .xodr via netconvert ...")
        try:
            run_netconvert(
                net_output_path, args.output,
                netconvert_bin=args.netconvert_bin,
                timeout_s=args.timeout,
            )
        except FileNotFoundError as e:
            print(f"\nError: {e}", file=sys.stderr)
            if not args.keep_net:
                print(f"Clipped .net.xml is at: {net_output_path}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"\nError during netconvert: {e}", file=sys.stderr)
            if not args.keep_net:
                print(f"Clipped .net.xml is at: {net_output_path}", file=sys.stderr)
            sys.exit(1)

        if not args.keep_net:
            try:
                os.unlink(net_output_path)
            except OSError:
                pass

        print(f"\nDone! Output written to: {args.output}")


def _run_taz_batch(args):
    """Batch TAZ clipping."""
    result = batch_clip_taz(
        net_path=args.net,
        taz_config_path=args.taz_config,
        intersection_config_path=args.intersection_config,
        output_dir=args.output_dir,
        dist_m=args.dist,
        path_dist_m=args.path_dist,
        netconvert_bin=args.netconvert_bin,
        timeout_s=args.timeout,
        skip_netconvert=args.skip_netconvert,
    )

    if result["failed"]:
        sys.exit(1)
