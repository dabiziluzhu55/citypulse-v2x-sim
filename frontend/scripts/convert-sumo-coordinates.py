import json
import sys
from pathlib import Path


def load_sumolib():
    try:
        import sumolib
        return sumolib
    except ModuleNotFoundError:
        import sumo
        tools_directory = Path(sumo.__file__).resolve().parent / "tools"
        sys.path.insert(0, str(tools_directory))
        import sumolib
        return sumolib


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: convert-sumo-coordinates.py <network.net.xml>")
    sumolib = load_sumolib()
    network = sumolib.net.readNet(sys.argv[1])
    points = json.load(sys.stdin)
    converted = [network.convertXY2LonLat(float(x), float(y)) for x, y in points]
    json.dump(converted, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
