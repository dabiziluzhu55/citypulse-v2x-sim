#!/usr/bin/env python3
"""XML2Odr — Convert SUMO .net.xml to CARLA .xodr via topological clipping.

Supports single-junction, batch junction, and TAZ (Typical Area Zone) modes.

Usage:
    python run_xml2odr.py --net <file> --junction <id> --output <path> [options]
    python run_xml2odr.py --net <file> --config <config/intersections.json> --output-dir <dir> [options]
    python run_xml2odr.py --net <file> --taz-config <config/taz.json> [options]

Examples:
    # Single junction — clip around J1 with 200m topological distance
    python run_xml2odr.py --net TotalMap.net.xml --junction J1 --dist 200 -o output.xodr

    # Keep intermediate clipped .net.xml
    python run_xml2odr.py --net TotalMap.net.xml --junction J1 --dist 100 \\
        -o output.xodr --keep-net

    # Batch junction mode — process all intersections from config
    python run_xml2odr.py --net TotalMap.net.xml --config config/intersections.json \\
        --dist 200 --output-dir output --skip-netconvert

    # TAZ batch mode — process all TAZ groups
    python run_xml2odr.py --net TotalMap.net.xml --taz-config config/taz.json \\
        --intersection-config config/intersections.json --dist 200 --output-dir output

    # Single TAZ mode — process one TAZ group
    python run_xml2odr.py --net TotalMap.net.xml --taz-config config/taz.json \\
        --taz TAZ_2 --dist 200 -o TAZ_2.xodr

    # Only generate clipped .net.xml (skip netconvert)
    python run_xml2odr.py --net TotalMap.net.xml --junction J1 --dist 50 \\
        -o clipped --skip-netconvert --keep-net
"""

import sys
import os

# Ensure the xml2odr package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xml2odr.cli import main

if __name__ == '__main__':
    main()
