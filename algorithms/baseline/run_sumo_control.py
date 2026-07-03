import argparse
import logging
import os
import sys
import time

if "SUMO_HOME" not in os.environ:
    sys.exit("please declare environment variable 'SUMO_HOME'")

sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import sumolib
import traci

from algorithms.baseline.max_pressure.controller import TrafficController
from algorithms.baseline.max_pressure.intersection_config import DEMO_TLS_IDS

DEFAULT_SUMOCFG = os.path.join(
    PROJECT_ROOT, "data", "maps", "sumo", "demo_control.sumocfg"
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sumocfg",
        default=DEFAULT_SUMOCFG,
        help=f"SUMO configuration file (default: {DEFAULT_SUMOCFG})",
    )
    parser.add_argument(
        "--tls-ids",
        nargs="+",
        default=list(DEMO_TLS_IDS),
        help=f"Traffic light ids to control (default: {' '.join(DEMO_TLS_IDS)})",
    )
    parser.add_argument(
        "--step-length",
        type=float,
        default=0.05,
        help="Simulation step length in seconds (default: 0.05)",
    )
    parser.add_argument(
        "--end",
        type=float,
        default=300.0,
        help="Simulation end time in seconds (default: 300)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch sumo-gui instead of headless sumo",
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Sleep between steps to approximate real time (useful with --gui)",
    )
    parser.add_argument(
        "--tripinfo-output",
        default="",
        help="Optional tripinfo output xml path for offline analysis",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def build_sumo_cmd(args):
    sumo_binary = sumolib.checkBinary("sumo-gui" if args.gui else "sumo")
    cmd = [
        sumo_binary,
        "--configuration-file",
        args.sumocfg,
        "--step-length",
        str(args.step_length),
        "--no-step-log",
        "true",
        "--collision.action",
        "warn",
    ]
    if args.tripinfo_output:
        cmd.extend(["--tripinfo-output", args.tripinfo_output])
    return cmd


def run_simulation(args):
    controller = TrafficController(args.tls_ids)
    traci.start(build_sumo_cmd(args))
    controller.prepare()

    if args.gui:
        logging.info("sumo-gui started: press Play if the simulation is paused")

    step_count = 0
    try:
        while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < args.end:
            step_start = time.time()

            traci.simulationStep()
            controller.step(traci.simulation.getTime())

            step_count += 1
            if step_count % 200 == 0:
                logging.info(
                    "t=%.1fs  vehicles=%d  controlled_tls=%s",
                    traci.simulation.getTime(),
                    traci.vehicle.getIDCount(),
                    ",".join(args.tls_ids),
                )

            if args.realtime:
                elapsed = time.time() - step_start
                if elapsed < args.step_length:
                    time.sleep(args.step_length - elapsed)

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        logging.info("Finished after %d steps (t=%.1fs)", step_count, traci.simulation.getTime())
        traci.close()


def main():
    args = parse_args()
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    if not os.path.isfile(args.sumocfg):
        sys.exit(f"sumocfg not found: {args.sumocfg}")
    run_simulation(args)


if __name__ == "__main__":
    main()
