"""Offline build step: run in the SUMO environment, never in the API container."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generated-dir', type=Path, default=ROOT / 'data/maps/sumo/generated')
    parser.add_argument('--radius-m', type=float, action='append', help='Repeat for each radius; defaults to 600 and 900')
    args = parser.parse_args()
    import sumolib
    from simulation_protocol.catalog import load_catalog
    from simulation.utils.convert_sumo_road_network import convert_network, write_geojson
    network_path = args.generated_dir / 'network/TotalMap_20.signals.net.xml'
    net = sumolib.net.readNet(str(network_path))
    catalog = load_catalog(args.generated_dir)
    for radius in args.radius_m or [600, 900]:
        for identifier, item in catalog.intersections.items():
            if item.longitude is None or item.latitude is None:
                raise ValueError(f'Missing coordinates: {identifier}')
            collection = convert_network(net, intersection_id=identifier,
                                         center_lon=item.longitude, center_lat=item.latitude,
                                         radius_m=radius, source_name=network_path.name)
            output_dir = args.generated_dir / 'geojson'
            if radius != 600:
                output_dir = output_dir / f'radius_{radius:g}'
            write_geojson(collection, output_dir / f'{identifier}.roads.wgs84.geojson')
            print(identifier, radius, len(collection['features']), 'roads')


if __name__ == '__main__':
    main()
