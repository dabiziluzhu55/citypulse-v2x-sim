from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from algorithms.cov2x.phase_history_audit import sumo_time_to_ticks


SUMO_TOOLS = Path("/usr/share/sumo/tools")
pytestmark = pytest.mark.skipif(
    shutil.which("sumo") is None
    or shutil.which("netconvert") is None
    or not SUMO_TOOLS.exists(),
    reason="SUMO runtime fixture requires the pinned SUMO toolchain",
)

NODES = """<nodes>
  <node id="west" x="0" y="0" type="priority"/>
  <node id="tls" x="100" y="0" type="traffic_light"/>
  <node id="east" x="200" y="0" type="priority"/>
</nodes>
"""
EDGES = """<edges>
  <edge id="in" from="west" to="tls" numLanes="1" speed="30"/>
  <edge id="out" from="tls" to="east" numLanes="1" speed="30"/>
</edges>
"""
CONNECTIONS = """<connections>
  <connection from="in" to="out" fromLane="0" toLane="0"/>
</connections>
"""
ROUTES = """<routes>
  <vType id="car" accel="1000" decel="1000" sigma="0"
         length="1" minGap="0" maxSpeed="30"/>
  <route id="route" edges="in out"/>
  <vehicle id="v" type="car" route="route" depart="0"
           departPos="90" departSpeed="10"/>
</routes>
"""


@pytest.fixture(scope="module")
def boundary_network(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("sumo-phase-boundary")
    nodes = root / "nodes.nod.xml"
    edges = root / "edges.edg.xml"
    connections = root / "connections.con.xml"
    network = root / "boundary.net.xml"
    routes = root / "routes.rou.xml"
    nodes.write_text(NODES, encoding="utf-8")
    edges.write_text(EDGES, encoding="utf-8")
    connections.write_text(CONNECTIONS, encoding="utf-8")
    routes.write_text(ROUTES, encoding="utf-8")
    subprocess.run(
        [
            "netconvert",
            "--node-files",
            str(nodes),
            "--edge-files",
            str(edges),
            "--connection-files",
            str(connections),
            "--tls.default-type",
            "static",
            "--output-file",
            str(network),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return network, routes


def _run_real_sumo_boundary(
    network: Path, routes: Path, state_before: str, state_after: str
) -> dict[str, object]:
    sys.path.insert(0, str(SUMO_TOOLS))
    import traci  # type: ignore

    label = f"cov2x-boundary-{uuid4().hex}"
    traci.start(
        [
            "sumo",
            "-n",
            str(network),
            "-r",
            str(routes),
            "--step-length",
            "0.001",
            "--no-step-log",
            "true",
            "--no-warnings",
            "true",
            "--seed",
            "17",
        ],
        label=label,
    )
    connection = traci.getConnection(label)
    try:
        while "v" not in connection.vehicle.getIDList():
            connection.simulationStep()
        tls_id = connection.trafficlight.getIDList()[0]
        connection.trafficlight.setRedYellowGreenState(tls_id, state_before)
        connection.vehicle.setSpeedMode("v", 0)
        connection.vehicle.setSpeed("v", 10.0)
        lane_length = connection.lane.getLength("in_0")
        connection.vehicle.moveTo("v", "in_0", lane_length - 0.005)

        before_tick = sumo_time_to_ticks(connection.simulation.getTime())
        lane_before = connection.vehicle.getLaneID("v")
        signal_before = connection.trafficlight.getRedYellowGreenState(tls_id)

        connection.simulationStep()
        movement_tick = sumo_time_to_ticks(connection.simulation.getTime())
        lane_after_movement = connection.vehicle.getLaneID("v")
        signal_after_movement = connection.trafficlight.getRedYellowGreenState(
            tls_id
        )

        connection.trafficlight.setRedYellowGreenState(tls_id, state_after)
        lane_after_switch = connection.vehicle.getLaneID("v")
        signal_after_switch = connection.trafficlight.getRedYellowGreenState(
            tls_id
        )
        return {
            "before_tick": before_tick,
            "movement_tick": movement_tick,
            "lane_before": lane_before,
            "lane_after_movement": lane_after_movement,
            "lane_after_switch": lane_after_switch,
            "signal_before": signal_before,
            "signal_after_movement": signal_after_movement,
            "signal_after_switch": signal_after_switch,
        }
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("state_before", "state_after"),
    [("G", "y"), ("y", "r"), ("r", "G")],
)
def test_real_sumo_movement_precedes_post_move_phase_switch_repeatably(
    boundary_network: tuple[Path, Path],
    state_before: str,
    state_after: str,
) -> None:
    network, routes = boundary_network
    rows = [
        _run_real_sumo_boundary(network, routes, state_before, state_after)
        for _ in range(3)
    ]
    assert rows[1:] == rows[:-1]
    row = rows[0]
    assert row["movement_tick"] == row["before_tick"] + 1
    assert row["lane_before"] == "in_0"
    assert str(row["lane_after_movement"]).startswith(":")
    assert row["lane_after_switch"] == row["lane_after_movement"]
    assert row["signal_before"] == state_before
    assert row["signal_after_movement"] == state_before
    assert row["signal_after_switch"] == state_after
