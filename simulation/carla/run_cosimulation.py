#!/usr/bin/env python3
"""
SUMO + CARLA Co-Simulation Bridge (Official CARLA Integration)
===============================================================
Loads a pre-imported CARLA map (typically from RoadRunner) via
``client.load_world()``, then delegates all vehicle and traffic-light
synchronization to CARLA's official ``SumoSimulation`` class from
``Co-Simulation/Sumo/sumo_integration/``.

**SUMO controls all behavior** (vehicles + traffic lights).
**CARLA handles 3D rendering only.**

Requirements:
    - CARLA server running (./CarlaUE4.sh or CarlaUE5.sh)
    - CARLA source code with Co-Simulation/Sumo/ accessible
      (set CARLA_ROOT or add to PYTHONPATH)
    - SUMO installed with SUMO_HOME environment variable set
    - The target map must be pre-imported into CARLA's Unreal project
      (e.g. via RoadRunner → CARLA Filmbox export → CARLA import)

Usage:
    python run_cosimulation.py --sumocfg ../../data/maps/sumo/generated/traffic/global/off_peak/simulation.sumocfg \\
        --carla-map demo_1 --junction 4427 --max-time 60
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

from data_export import (ExporterManager, ExportContext, FrameRegistry,
                         RunOutput, SensorFarm, load_export_config,
                         resolve_export_config_path,
                         EXPORTER_REGISTRY, ExportConfigError)

import toolchain_env  # 统一环境配置:env > config/toolchain.json > 自动探测

from export_control import DEFAULT_PORT, ExportControlServer

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger("cosim")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
))
logger.addHandler(_handler)


# 步长回退默认(秒):sumocfg 未配置 <time><step-length> 时使用,与改动前一致
DEFAULT_STEP_LENGTH = 0.05


def read_step_length_from_sumocfg(sumocfg_path: str) -> Optional[float]:
    """读取 SUMO 配置文件的仿真步长 `<time><step-length value="..."/>`(秒)。

    SUMO 配置文件的标准写法::

        <configuration>
            <time>
                <step-length value="0.05"/>
            </time>
        </configuration>

    返回 None 表示文件未配置 step-length(调用方按默认步长回退)。

    Raises:
        ET.ParseError: 文件不是合法 XML(自带行列号,由调用方转 parser.error)
        ValueError:    step-length 元素存在但 value 缺失 / 非数字 / 非正数
                      (含 nan/inf)
    """
    tree = ET.parse(sumocfg_path)          # 坏 XML → ET.ParseError(带行号)
    elem = tree.find(".//step-length")     # 全树查找(与 sumo_simulation 的
                                           # .//net-file 先例一致,对文件布局宽容)
    if elem is None:
        return None
    value = elem.get("value")              # SUMO 配置属性名恒为 value
    if value is None:
        raise ValueError(
            f"<step-length> 缺少 value 属性(应为 <step-length value=\"0.05\"/>)")
    try:
        step = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"step-length value 必须是正数,实际为 {value!r}")
    if not math.isfinite(step) or step <= 0:  # isfinite 拦住 nan/inf
        raise ValueError(
            f"step-length value 必须是正数,实际为 {value!r}")
    return step


def _resolve_step_length(parser: argparse.ArgumentParser,
                         args: argparse.Namespace,
                         sumocfg_path: Optional[str]) -> float:
    """确定联仿步长,优先级:--step-length 显式参数 > sumocfg 配置 > 默认 0.05。

    显式参数最高优先(不读 sumocfg);未显式且给了 sumocfg 时读取其
    <time><step-length>(未配置则警告并回退默认);未显式且无 sumocfg
    (仅 --check-env 场景)静默回退默认。
    """
    if args.step_length is not None:
        if not (args.step_length > 0):  # 顺带拦住 NaN
            parser.error("--step-length must be > 0")
        return args.step_length

    if sumocfg_path:
        try:
            from_cfg = read_step_length_from_sumocfg(sumocfg_path)
        except (ET.ParseError, ValueError, OSError) as exc:
            # OSError = 文件缺失/不可读(FileNotFoundError 等),同样 fail-fast
            parser.error(f"sumocfg 步长解析失败 ({sumocfg_path}): {exc}")
        if from_cfg is not None:
            logger.info("仿真步长取自 sumocfg: %.6fs "
                        "(<time><step-length value=\"...\"/>)", from_cfg)
            return from_cfg
        logger.warning(
            "sumocfg 未配置 <time><step-length>,回退默认 %.4fs;"
            "如需调整可在 sumocfg 中配置或显式传 --step-length",
            DEFAULT_STEP_LENGTH)
    return DEFAULT_STEP_LENGTH


class _DedupeVtypeWarningFilter(logging.Filter):
    """官方 bridge_helper 的 'sumo vtype X not found in carla' 警告按 vType
    只放行一次，避免每次 spawn 刷屏（X 走 vClass 随机兜底时的 WARNING）。
    """
    def __init__(self):
        super().__init__()
        self._seen_vtypes: Set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        m = re.match(r"sumo vtype (\S+) not found in carla", msg)
        if m:
            vtype = m.group(1)
            if vtype in self._seen_vtypes:
                return False
            self._seen_vtypes.add(vtype)
        return True


# 官方模块（sumo_integration.*）走 root logger；root 默认无 handler，
# 其 WARNING 原经 lastResort 打到 stderr。显式挂一个带去重 Filter 的
# stderr handler，保持原有输出行为不变，仅对上述警告按 vType 去重。
_root_handler = logging.StreamHandler(sys.stderr)
_root_handler.setLevel(logging.WARNING)
_root_handler.addFilter(_DedupeVtypeWarningFilter())
logging.getLogger().addHandler(_root_handler)


# ---------------------------------------------------------------------------
# Coordinate transformation utilities  (spectator positioning only)
# ---------------------------------------------------------------------------

def sumo_to_carla_position(
    sumo_x: float, sumo_y: float, sumo_z: float = 0.0
) -> Tuple[float, float, float]:
    """Convert SUMO coordinates (X=East, Y=North) to CARLA Unreal coords."""
    return (sumo_x, -sumo_y, sumo_z + 0.3)


# ---------------------------------------------------------------------------
# Official SumoSimulation discovery
# ---------------------------------------------------------------------------

def _find_sumo_simulation():
    """Locate and import CARLA's official ``SumoSimulation`` class.

    Search order:
        1. ``CARLA_ROOT`` environment variable → Co-Simulation/Sumo/
        2. ``config/toolchain.json`` 的 ``carla_root`` → Co-Simulation/Sumo/
        3. Already on ``sys.path`` (if user added manually)

    不包含任何硬编码路径——CARLA 源码位置只来自环境变量或 config/toolchain.json。

    Returns:
        The ``SumoSimulation`` class.

    Raises:
        ImportError: Could not find CARLA's SumoSimulation module.
    """
    toolchain_env.add_carla_pythonapi_to_path()

    search_roots = []

    carla_root = os.environ.get("CARLA_ROOT", "")
    if carla_root:
        search_roots.append(carla_root)
    # config/toolchain.json 的 carla_root(次优先于环境变量)
    cfg_carla_root = toolchain_env.get("carla_root")
    if cfg_carla_root:
        search_roots.append(cfg_carla_root)

    # Check common sub-paths inside a CARLA source tree
    candidate_subdirs = [
        "Co-Simulation/Sumo",
        "Co-Simulation/Sumo/sumo_integration",
        "PythonAPI/carla",
    ]

    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for sub in candidate_subdirs:
            candidate = os.path.join(root, sub)
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.insert(0, candidate)

    # Ensure SUMO tools are on sys.path (sumo_simulation.py imports sumolib)
    # 统一经 toolchain_env:环境变量 SUMO_HOME > config/toolchain.json sumo_home
    sumo_tools = toolchain_env.sumo_tools_dir()
    if sumo_tools and sumo_tools not in sys.path:
        sys.path.insert(0, sumo_tools)

    # Try importing the official class
    errors = []
    for module_name in (
        "sumo_simulation",                          # if Co-Simulation/Sumo/sumo_integration/ is on path
        "sumo_integration.sumo_simulation",         # if Co-Simulation/Sumo/ is on path
    ):
        try:
            mod = __import__(module_name, fromlist=["SumoSimulation"])
            cls = getattr(mod, "SumoSimulation", None)
            if cls is not None:
                logger.info("Using official SumoSimulation from %s", mod.__file__)
                return cls
        except ImportError as exc:
            errors.append(f"  {module_name}: {exc}")

    raise ImportError(
        "Cannot find CARLA's official SumoSimulation class.\n\n"
        "The CARLA co-simulation scripts are NOT part of the carla pip/egg\n"
        "package — they live in the CARLA source repository under\n"
        "  Co-Simulation/Sumo/sumo_integration/sumo_simulation.py\n\n"
        "To fix, set CARLA_ROOT to your CARLA source directory:\n"
        "  export CARLA_ROOT=/path/to/carla\n\n"
        "Or manually add to PYTHONPATH:\n"
        "  export PYTHONPATH=/path/to/carla/Co-Simulation/Sumo:$PYTHONPATH\n\n"
        "Attempted imports:\n" + "\n".join(errors)
    )


def _patch_out_traffic_manager() -> None:
    """桩掉 ``carla.Client.get_trafficmanager()``，避免 EastZone 地图上的崩溃。

    官方 ``SimulationSynchronization.__init__``（run_synchronization.py:82）会无条件
    创建 Traffic Manager，但该地图上此调用确定性触发 libcarla 客户端 SIGSEGV
    （堆损坏，faulthandler 已实证崩溃点）。本项目不使用 Traffic Manager ——
    所有车辆由 SUMO 驱动（README: "SUMO 控制所有车辆行为和红绿灯状态"），
    TM 创建后仅调一次 ``set_synchronous_mode(True)`` 即被丢弃，桩掉无功能损失。

    开关：``COSIM_NO_TM=0`` 关闭桩（恢复真实 TM 行为），默认开启。
    """
    if os.environ.get("COSIM_NO_TM", "1") == "0":
        logger.info("TrafficManager stub disabled (COSIM_NO_TM=0)")
        return

    import carla  # carla 仅在运行时按需 import（依赖 CARLA Python API 路径）

    class _TmStub:
        """最小 Traffic Manager 桩：仅需支持官方代码用到的 ``set_synchronous_mode``。"""
        def set_synchronous_mode(self, mode: bool) -> None:
            pass

    def _stub_get_tm(self, port: int = 8000) -> _TmStub:
        return _TmStub()

    if getattr(carla.Client, "get_trafficmanager", None) is not _stub_get_tm:
        carla.Client.get_trafficmanager = _stub_get_tm
    logger.info("TrafficManager creation stubbed out (COSIM_NO_TM=1) — "
                "avoids libcarla crash on EastZone")


# ---------------------------------------------------------------------------
# Co-simulation bridge
# ---------------------------------------------------------------------------

class CoSimulationBridge:
    """Manages a SUMO+CARLA co-simulation session using the official CARLA
    ``SumoSimulation`` class for all vehicle and traffic-light sync.

    Lifecycle::

        bridge = CoSimulationBridge(sumocfg=..., carla_map_name=...)
        bridge.run()  # blocks until sim ends or Ctrl+C
    """

    def __init__(
        self,
        sumocfg_path: str,
        carla_map_name: Optional[str] = None,
        carla_host: str = "127.0.0.1",
        carla_port: int = 2000,
        step_length: float = DEFAULT_STEP_LENGTH,
        max_sim_time: float = 3600.0,
        sumo_gui: bool = False,
        junction_id: Optional[str] = None,
        scene_box: Optional[Tuple[float, float, float, float]] = None,
        export_config_path: Optional[str] = None,
        export_dir: Optional[str] = None,
        export_kinds: Optional[List[str]] = None,
        control_host: str = "127.0.0.1",
        control_port: int = DEFAULT_PORT,
        control_enabled: bool = True,
    ):
        # ── Paths ──
        if not os.path.isfile(sumocfg_path):
            raise FileNotFoundError(f"SUMO config not found: {sumocfg_path}")

        self._sumocfg_path = os.path.abspath(sumocfg_path)
        self._carla_map_name = carla_map_name

        # ── Network ──
        self._carla_host = carla_host
        self._carla_port = carla_port
        self._step_length = step_length
        self._max_sim_time = max_sim_time
        self._sumo_gui = sumo_gui
        self._junction_id = junction_id
        self._scene_box_override = scene_box
        self._export_config_path = export_config_path
        self._export_dir = export_dir
        self._export_kinds = export_kinds
        self._export_manager = None  # data_export.ExporterManager (if export mode)
        self._export_sensor_count = 0  # sensors of the active segment (status)
        self._sim_aborted = False    # True when the loop ended early (Ctrl+C / error)

        # ── Runtime export control (TCP channel, see export_control.py) ──
        self._control_host = control_host
        self._control_port = control_port
        self._control_enabled = control_enabled
        self._export_server = None   # ExportControlServer (when listening)
        self._export_run_dir = None  # output dir of the most recent segment

        # ── Runtime state (populated during run) ──
        self._client = None
        self._world = None
        self._sumo_sim = None   # official SumoSimulation instance
        self._carla_sim = None  # official CarlaSimulation instance
        self._sync = None       # official SimulationSynchronization instance
        self._sumo_tl_ids: List[str] = []
        # Scene bounding (CARLA coords): (min_x, max_x, min_y, max_y).  None
        # disables scene filtering (e.g. map without road topology).
        self._scene_bbox: Optional[Tuple[float, float, float, float]] = None
        self._invalid_actor_id = -1  # official package's INVALID_ACTOR_ID
        self._spectator_followed = False  # auto-focus on first spawned vehicle

    # ─────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the full co-simulation lifecycle."""
        _setup_signal_handlers()
        try:
            self._setup_carla()
            self._setup_sumo_official()
            self._start_control_server()
            self._setup_exporters()
            self._position_spectator()
            self._sync_loop()
        except KeyboardInterrupt:
            self._sim_aborted = True
            logger.info("Interrupted by user (Ctrl+C)")
        except Exception:
            self._sim_aborted = True
            logger.exception("Fatal error during co-simulation")
            raise
        finally:
            self._cleanup()

    # ─────────────────────────────────────────────────────────────────
    # Environment checks
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def check_environment(export_config_path: Optional[str] = None,
                          step_length: float = DEFAULT_STEP_LENGTH) -> int:
        """Validate that CARLA, SUMO, and official co-sim scripts are ready.

        If ``export_config_path`` is given, also validates the data-export
        configuration (schema, sensor blueprints, disk space, fps alignment).

        ``step_length`` 用于导出配置的 fps 对齐校验(来自 main 解析后的
        步长:显式 --step-length > sumocfg <time><step-length> > 默认)。

        Returns:
            0 if everything is ready, 1 if issues found.
        """
        ok = True

        # --- CARLA Python API ---
        toolchain_env.add_carla_pythonapi_to_path()
        print("─" * 50)
        print("Checking CARLA Python API ...")
        try:
            import carla  # noqa: F401
            print("  ✓ carla module found")
        except ImportError:
            print("  ✗ carla module NOT found")
            print("    Install: pip install carla==<your-carla-version>")
            print("    Or add CARLA's PythonAPI/carla/dist/ to PYTHONPATH")
            ok = False

        # --- SUMO / TraCI ---
        print("Checking SUMO / TraCI ...")
        sumo_home = toolchain_env.resolve_sumo_home()
        if not sumo_home:
            print("  ✗ SUMO_HOME is not set")
            print("    Set it: export SUMO_HOME=/path/to/sumo, "
                  "or configure config/toolchain.json → sumo_home")
            ok = False
        else:
            print(f"  ✓ SUMO_HOME = {sumo_home}")
            tools_dir = os.path.join(sumo_home, "tools")
            if os.path.isdir(tools_dir):
                print(f"  ✓ SUMO tools directory exists")
            else:
                print(f"  ✗ SUMO tools directory not found: {tools_dir}")
                ok = False

        # Add SUMO tools to sys.path early (needed by SumoSimulation import)
        if sumo_home:
            tools = os.path.join(sumo_home, "tools")
            if tools not in sys.path:
                sys.path.append(tools)
            try:
                import sumolib  # noqa: F401
                print("  ✓ sumolib import OK")
            except ImportError:
                print("  ✗ sumolib import FAILED")
                ok = False
            try:
                import traci  # noqa: F401
                print("  ✓ traci import OK")
            except ImportError:
                print("  ✗ traci import FAILED")
                ok = False

        # --- Official SumoSimulation class (depends on SUMO tools above) ---
        print("Checking CARLA official SumoSimulation ...")
        try:
            _find_sumo_simulation()
            print("  ✓ SumoSimulation class found")
        except ImportError as exc:
            print(f"  ✗ {exc}")
            ok = False

        # --- SUMO binaries ---
        print("Checking SUMO binaries ...")
        if sumo_home:
            try:
                from sumolib import checkBinary
                print(f"  ✓ sumo binary: {checkBinary('sumo')}")
                print(f"  ✓ sumo-gui binary: {checkBinary('sumo-gui')}")
            except Exception as e:
                print(f"  ✗ SUMO binary check failed: {e}")
                ok = False

        # --- netconvert ---
        import shutil
        netconvert = shutil.which("netconvert")
        if netconvert:
            print(f"  ✓ netconvert: {netconvert}")
        elif sumo_home:
            nc = os.path.join(sumo_home, "bin", "netconvert")
            if os.path.isfile(nc):
                print(f"  ✓ netconvert: {nc}")
            else:
                print("  ⚠ netconvert not found (not needed for this script, "
                      "but required by run_xml2odr.py)")
        else:
            print("  ⚠ netconvert not found (not needed for this script, "
                  "but required by run_xml2odr.py)")

        # --- CARLA server connectivity (best-effort) ---
        print("─" * 50)
        print("Checking CARLA server connectivity ...")
        carla_ok = False
        try:
            import carla
            client = carla.Client("127.0.0.1", 2000, worker_threads=1)
            client.set_timeout(5.0)
            version = client.get_server_version()
            print(f"  ✓ CARLA server reachable (version {version})")
            carla_ok = True
        except Exception as e:
            print(f"  ⚠ CARLA server not reachable: {e}")
            print("    Make sure CARLA is running: cd carla && ./CarlaUE4.sh")

        # --- Available CARLA maps ---
        print("Checking available CARLA maps ...")
        try:
            import carla
            client2 = carla.Client("127.0.0.1", 2000, worker_threads=1)
            client2.set_timeout(5.0)
            maps = client2.get_available_maps()
            print(f"  {len(maps)} map(s) available:")
            for m in maps:
                print(f"    - {m}")
        except Exception:
            print("  ⚠ Could not list CARLA maps (server may not be running)")

        # --- Data export config (only when --export-config was given) ---
        if export_config_path:
            print("─" * 50)
            print(f"Checking data export config ({export_config_path}) ...")
            try:
                from data_export import load_export_config, RunOutput
                cfg = load_export_config(export_config_path, step_length)
            except Exception as exc:
                print(f"  ✗ {exc}")
                ok = False
            else:
                print(f"  ✓ config valid: {len(cfg.sensors)} sensor(s)")
                for spec in cfg.sensors:
                    line = f"    - {spec.name} ({spec.type}) @ ({spec.transform['x']:.1f}, " \
                           f"{spec.transform['y']:.1f}, {spec.transform['z']:.1f})"
                    if spec.note:
                        line += f"  [{spec.note}]"
                    print(line)
                if carla_ok:  # CARLA reachable → verify sensor blueprints
                    try:
                        import carla
                        from data_export import BLUEPRINTS
                        bl = carla.Client("127.0.0.1", 2000, worker_threads=1).get_world().get_blueprint_library()
                        for spec in cfg.sensors:
                            try:
                                bl.find(spec.blueprint)
                                print(f"  ✓ blueprint {spec.blueprint} found")
                            except Exception:
                                print(f"  ✗ blueprint {spec.blueprint} NOT found "
                                      f"(sensor '{spec.name}' will be skipped)")
                                ok = False
                    except Exception as exc:
                        print(f"  ⚠ blueprint check skipped: {exc}")
                # Disk space vs rough estimate (10-minute run)
                try:
                    import shutil
                    est = RunOutput.estimate_bytes(cfg, 600.0)
                    usage = shutil.disk_usage(cfg.output_dir if os.path.exists(cfg.output_dir) else ".")
                    if usage.free < est:
                        print(f"  ⚠ free disk {usage.free / 1e9:.1f} GB < estimated "
                              f"10-min export {est / 1e9:.1f} GB")
                    else:
                        print(f"  ✓ disk OK: free {usage.free / 1e9:.1f} GB, "
                              f"~{est / 1e9:.2f} GB estimated per 10 min")
                except OSError as exc:
                    print(f"  ⚠ disk check failed: {exc}")
                # Stream bandwidth estimate (only when output.stream is set)
                if cfg.stream:
                    try:
                        from data_export.exporters.stream import \
                            estimate_stream_bytes_per_sec
                        bps = estimate_stream_bytes_per_sec(cfg.sensors)
                        print(f"  stream: bind={cfg.stream.get('bind')} "
                              f"~{bps / 1e6:.1f} MB/s estimated "
                              f"(JPEG camera + zlib lidar)")
                    except Exception as exc2:
                        print(f"  ⚠ stream estimate unavailable: {exc2}")
                # fps alignment note
                from data_export import effective_fps
                print(f"  步长: {step_length:g}s")
                _, note = effective_fps(step_length, cfg.output_fps)
                if note:
                    print(f"  ⚠ fps: {note}")
            print("─" * 50)

        if ok:
            print("Environment check PASSED ✓")
        else:
            print("Environment check FAILED — see details above ✗")
        return 0 if ok else 1

    # ─────────────────────────────────────────────────────────────────
    # Phase 1: CARLA setup  (must come BEFORE SUMO — official class
    #                         expects CARLA world already loaded)
    # ─────────────────────────────────────────────────────────────────

    def _setup_carla(self) -> None:
        """Connect to CARLA and load the pre-imported map via ``client.load_world()``."""
        toolchain_env.add_carla_pythonapi_to_path()
        try:
            import carla  # noqa: F811
        except ImportError:
            sys.exit(
                "CARLA Python API not found.\n"
                "Install: pip install carla==<your-carla-version>\n"
                "Or add CARLA's PythonAPI/carla/dist/ to PYTHONPATH."
            )

        logger.info("Connecting to CARLA server at %s:%d ...",
                     self._carla_host, self._carla_port)
        self._client = carla.Client(
            self._carla_host, self._carla_port, worker_threads=1
        )
        self._client.set_timeout(20.0)

        # Verify the map is available
        map_name = self._carla_map_name
        available_maps = self._client.get_available_maps()
        if map_name not in available_maps:
            logger.warning(
                "Map '%s' not found in CARLA's available maps list.\n"
                "Available maps: %s\n"
                "Attempting load anyway (map may have been added recently) ...",
                map_name, available_maps,
            )

        # Load the pre-imported map (may take a while for large RoadRunner scenes)
        logger.info("Loading CARLA map: '%s' (this may take a moment) ...", map_name)
        try:
            self._client.set_timeout(60.0)
            self._world = self._client.load_world(map_name)
            self._client.set_timeout(20.0)
        except RuntimeError as exc:
            logger.error("CARLA failed to load map '%s': %s", map_name, exc)
            logger.error(
                "Suggestions:\n"
                "  1. Ensure the map has been imported into CARLA's Unreal project\n"
                "  2. Restart the CARLA server after importing the map\n"
                "  3. Verify the map name matches exactly (case-sensitive)\n"
                "  4. Check CARLA server logs for details"
            )
            raise

        # Allow the world to fully initialize
        time.sleep(3.0)
        logger.info("CARLA map '%s' loaded", map_name)

        # Enable synchronous mode (load_world resets all settings)
        settings = self._world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = self._step_length
        self._world.apply_settings(settings)
        logger.info("CARLA sync mode: ON (fixed_delta_seconds = %.3fs)", self._step_length)

        # Batch pre-tick to stabilize after map load
        for _ in range(5):
            self._world.tick()

    # ─────────────────────────────────────────────────────────────────
    # Phase 2: SUMO setup via official SumoSimulation
    # ─────────────────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────────────────
    # Phase 2: SUMO setup via official SumoSimulation
    # ─────────────────────────────────────────────────────────────────

    def _setup_sumo_official(self) -> None:
        """Start SUMO and connect vehicle sync via CARLA's official classes.

        Uses ``SumoSimulation`` (SUMO management) + ``CarlaSimulation``
        (CARLA actor management) + ``SimulationSynchronization`` (bidirectional
        vehicle & TL sync).

        Scene bounding: the official classes would spawn *every* SUMO vehicle
        (whole 46×58 km net) into CARLA.  We restrict CARLA to the scene box:
        ``CarlaSimulation.spawn_actor`` is gated to reject out-of-box
        transforms (INVALID_ACTOR_ID), and ``_enforce_scene_membership``
        spawns/destroys actors as vehicles enter/leave the box.  Vehicles
        outside the box keep running in SUMO only.
        """
        SumoSimulation = _find_sumo_simulation()

        # Also import the sync companion classes (same package)
        # Co-Simulation/Sumo/ is already on sys.path from _find_sumo_simulation()
        try:
            from sumo_integration.carla_simulation import CarlaSimulation
            from sumo_integration.bridge_helper import BridgeHelper
        except ImportError:
            logger.error(
                "Cannot import CarlaSimulation / BridgeHelper.\n"
                "Make sure CARLA_ROOT points to the full CARLA source tree."
            )
            raise

        # Import SimulationSynchronization from the sibling script
        try:
            from run_synchronization import SimulationSynchronization
        except ImportError:
            logger.error(
                "Cannot import SimulationSynchronization from run_synchronization.py.\n"
                "Make sure Co-Simulation/Sumo/ is on sys.path."
            )
            raise

        # ── SUMO ──
        logger.info("Starting SUMO via CARLA official SumoSimulation ...")
        logger.info("  Config: %s", self._sumocfg_path)
        if self._sumo_gui:
            logger.info("  SUMO GUI will open — press Play if SUMO asks")

        self._sumo_sim = SumoSimulation(
            self._sumocfg_path,
            self._step_length,
            None,                               # sumo_host → auto-start
            None,                               # sumo_port → auto-start
            self._sumo_gui,
            1,                                  # client_order
        )
        logger.info("SumoSimulation initialised")

        # ── CARLA (wrap our already-loaded world, reuse our client) ──
        self._carla_sim = CarlaSimulation(
            self._carla_host, self._carla_port, self._step_length
        )
        # Reuse our existing CARLA client (avoids second connection + 2s timeout)
        self._carla_sim.client = self._client
        # Override with our pre-loaded world (load_world was already called)
        self._carla_sim.world = self._world
        # Bump the client timeout — complex RoadRunner scenes with many vehicles
        # can take >2s per world.tick()
        self._carla_sim.client.set_timeout(30.0)
        # Refresh traffic light registry for the loaded map
        self._carla_sim._tls = {}
        tmp_map = self._world.get_map()
        for landmark in tmp_map.get_all_landmarks_of_type('1000001'):
            if landmark.id != '':
                tl = self._world.get_traffic_light(landmark)
                if tl is not None:
                    self._carla_sim._tls[landmark.id] = tl

        # ── Synchronisation bridge ──
        BridgeHelper.blueprint_library = self._world.get_blueprint_library()
        BridgeHelper.offset = self._sumo_sim.get_net_offset()

        # ── Traffic Manager workaround ────────────────────────────────────
        # 官方 SimulationSynchronization.__init__ 会无条件调用 get_trafficmanager()，
        # 在 EastZone 地图上确定性触发 libcarla 客户端 SIGSEGV（堆损坏）。
        # 本项目不用 TM（车辆全由 SUMO 驱动），打桩跳过创建，零功能损失。
        _patch_out_traffic_manager()

        self._sync = SimulationSynchronization(
            self._sumo_sim,
            self._carla_sim,
            tls_manager='sumo',                 # SUMO controls traffic lights
            sync_vehicle_color=False,
            sync_vehicle_lights=False,
        )
        logger.info("SimulationSynchronization ready")

        # ── Coordinate offset override (CRITICAL) ──────────────────────────
        # SimulationSynchronization.__init__ (run_synchronization.py) sets
        # BridgeHelper.offset = sumo.get_net_offset(), which for SUMO 1.12.x
        # returns the net's <location netOffset> (the tmerc origin, e.g.
        # -5488893.66, -13133184.77 for TotalMap) — NOT zero.  The CARLA maps
        # are generated from the xodr at the SAME internal coordinates as the
        # SUMO net, so the bridge must use the identity mapping
        # carla=(sumo_x, -sumo_y); a non-zero offset would place every vehicle
        # ~5.5M m away from the scene → nothing would ever spawn (actors=0).
        BridgeHelper.offset = (0.0, 0.0)

        # Self-check: fail loudly if anything reassigned the offset.
        if BridgeHelper.offset != (0.0, 0.0):
            raise RuntimeError(
                f"BridgeHelper.offset override failed: {BridgeHelper.offset} "
                f"!= (0.0, 0.0)")

        # ── Scene bounding box + spawn gate (design: SUMO simulates the whole
        #    net; CARLA renders only the small scene) ────────────────────────
        self._compute_scene_box()
        self._install_spawn_gate()

        # Discover SUMO TL IDs (for summary / debugging)
        try:
            import traci
            self._sumo_tl_ids = traci.trafficlight.getIDList()
            logger.info("SUMO traffic light junctions: %d", len(self._sumo_tl_ids))
        except Exception:
            self._sumo_tl_ids = []

    # ─────────────────────────────────────────────────────────────────
    # Scene bounding: CARLA renders only the small scene; SUMO simulates
    # the whole net.  Vehicles outside the scene box are never spawned
    # into CARLA (spawn gate) and are destroyed when they leave it.
    # ─────────────────────────────────────────────────────────────────

    def _sumo_to_carla(self, x: float, y: float) -> Tuple[float, float]:
        """Map SUMO coordinates to CARLA coordinates.

        Identity bridge mapping carla=(sumo_x, -sumo_y): the CARLA map sits at
        the SUMO internal coordinates (see README coordinate table).
        """
        return (x, -y)

    def _compute_scene_box(self) -> Optional[Tuple[float, float, float, float]]:
        """Compute the scene bounding box in CARLA coordinates from the map's
        road topology (drivable area), padded by 300 m so approaching vehicles
        are picked up before they cross the boundary.

        Returns ``(min_x, max_x, min_y, max_y)`` (CARLA coords), or None if a
        manual ``--scene-box`` is not given and the map has no road topology.
        """
        if self._scene_box_override is not None:
            self._scene_bbox = self._scene_box_override
            logger.info("Scene box (manual --scene-box): x=[%.0f, %.0f] "
                        "y=[%.0f, %.0f]", *self._scene_bbox)
            return self._scene_bbox

        try:
            topology = self._world.get_map().get_topology()
            xs, ys = [], []
            for w1, w2 in topology:
                xs.append(w1.transform.location.x)
                xs.append(w2.transform.location.x)
                ys.append(w1.transform.location.y)
                ys.append(w2.transform.location.y)
        except Exception:
            self._scene_bbox = None
            logger.warning("Could not compute scene box from map topology; "
                           "scene filtering disabled.")
            return None

        if not xs:
            self._scene_bbox = None
            logger.warning("Map has no road topology; scene filtering disabled.")
            return None

        pad = 300.0
        self._scene_bbox = (min(xs) - pad, max(xs) + pad,
                            min(ys) - pad, max(ys) + pad)
        logger.info(
            "Scene box (map topology + %dm pad): x=[%.0f, %.0f] "
            "y=[%.0f, %.0f] → SUMO y range [%.0f, %.0f]",
            pad, self._scene_bbox[0], self._scene_bbox[1],
            self._scene_bbox[2], self._scene_bbox[3],
            -self._scene_bbox[3], -self._scene_bbox[2])
        return self._scene_bbox

    def _install_spawn_gate(self) -> None:
        """Gate ``CarlaSimulation.spawn_actor`` so vehicles outside the scene
        box are never spawned into CARLA.

        The official tick treats a failed spawn (INVALID_ACTOR_ID) as "not
        tracked" and keeps the vehicle running in SUMO only — exactly the
        desired whole-net / small-scene split.  The vehicle stays subscribed,
        so it can be spawned later by ``_enforce_scene_membership`` when it
        enters the box.
        """
        from sumo_integration.constants import INVALID_ACTOR_ID
        self._invalid_actor_id = INVALID_ACTOR_ID

        bbox = self._scene_bbox
        if bbox is None:
            return
        orig_spawn = self._carla_sim.spawn_actor

        def _gated_spawn(blueprint, transform):
            loc = transform.location
            if not (bbox[0] <= loc.x <= bbox[1] and bbox[2] <= loc.y <= bbox[3]):
                return self._invalid_actor_id
            return orig_spawn(blueprint, transform)

        self._carla_sim.spawn_actor = _gated_spawn
        logger.info("Scene spawn gate installed "
                    "(box x=[%.0f, %.0f] y=[%.0f, %.0f])",
                    bbox[0], bbox[1], bbox[2], bbox[3])

    def _enforce_scene_membership(self) -> None:
        """Spawn CARLA actors for SUMO vehicles that entered the scene box and
        destroy actors whose vehicle left it (called every 20 steps, see
        ``_sync_loop``).  Vehicles outside the box run in SUMO only.

        All departed vehicles are already subscribed by the official tick
        (``run_synchronization.py``), so ``SumoSimulation.get_actor()`` returns
        live subscription data for any of them.
        """
        import traci  # noqa: F811
        from sumo_integration.bridge_helper import BridgeHelper

        bbox = self._scene_bbox
        if bbox is None:
            return

        # 1) Destroy CARLA actors whose SUMO vehicle left the scene box
        for vid in list(self._sync.sumo2carla_ids):
            try:
                x, y = traci.vehicle.getPosition(vid)
            except Exception:
                continue  # vanished from SUMO; official tick handles it
            cx, cy = self._sumo_to_carla(x, y)
            if not (bbox[0] <= cx <= bbox[1] and bbox[2] <= cy <= bbox[3]):
                actor_id = self._sync.sumo2carla_ids.pop(vid)
                self._carla_sim.destroy_actor(actor_id)

        # 2) Spawn vehicles that entered the scene box (not spawned yet)
        spawned = set(self._sync.sumo2carla_ids)
        for vid in traci.vehicle.getIDList():
            if vid in spawned:
                continue
            try:
                x, y = traci.vehicle.getPosition(vid)
                cx, cy = self._sumo_to_carla(x, y)
                if not (bbox[0] <= cx <= bbox[1] and bbox[2] <= cy <= bbox[3]):
                    continue
                sumo_actor = self._sumo_sim.get_actor(vid)
            except Exception:
                continue  # not subscribed yet — retry on the next pass
            blueprint = BridgeHelper.get_carla_blueprint(
                sumo_actor, self._sync.sync_vehicle_color)
            if blueprint is None:
                continue  # vtype unsupported in CARLA (same skip as official)
            transform = BridgeHelper.get_carla_transform(
                sumo_actor.transform, sumo_actor.extent)
            actor_id = self._carla_sim.spawn_actor(blueprint, transform)
            if actor_id != self._invalid_actor_id:
                self._sync.sumo2carla_ids[vid] = actor_id

    # ─────────────────────────────────────────────────────────────────
    # Phase 3: Spectator camera
    # ─────────────────────────────────────────────────────────────────

    def _position_spectator(self) -> None:
        """Move the CARLA spectator camera directly above the target junction,
        10m above the road surface, looking straight down."""
        import carla  # noqa: F811

        centre_x, centre_y = self._find_map_centre()

        if centre_x is None:
            logger.warning("Could not determine map centre; spectator unchanged.")
            return

        # Get the actual road surface elevation at the focus point via waypoint
        # lookup; if the centre is not on a road (sparse map), snap the camera
        # to the nearest road waypoint from the topology instead of hovering
        # over empty ground.
        wp = self._world.get_map().get_waypoint(
            carla.Location(x=centre_x, y=centre_y, z=0)
        )
        if wp is None:
            nearest = None
            best_d = float("inf")
            for w1, w2 in self._world.get_map().get_topology():
                for w in (w1, w2):
                    d = ((w.transform.location.x - centre_x) ** 2 +
                         (w.transform.location.y - centre_y) ** 2)
                    if d < best_d:
                        best_d = d
                        nearest = w
            if nearest is not None:
                centre_x = nearest.transform.location.x
                centre_y = nearest.transform.location.y
                logger.warning(
                    "Focus point not on a road; snapping spectator to nearest "
                    "road waypoint (%.0f, %.0f)", centre_x, centre_y)
                wp = nearest
        if wp is not None:
            road_z = wp.transform.location.z
            cam_z = road_z + 10.0  # 10m above road surface
        else:
            road_z = 0.0
            cam_z = 50.0

        spectator = self._world.get_spectator()

        transform = carla.Transform(
            carla.Location(x=centre_x, y=centre_y, z=cam_z),
            carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0),
        )
        spectator.set_transform(transform)

        logger.info(
            "Spectator positioned at (%.0f, %.0f, %.0f) — above focus point '%s'"
            " (road surface z=%.1f)",
            centre_x, centre_y, cam_z, self._junction_id or "scene centre", road_z,
        )

    def _find_map_centre(self) -> tuple:
        """Return (x, y) in CARLA coordinates for the spectator focus point.

        Priority: ``--junction`` (SUMO junction position) → scene box centre
        (map road topology).  Returns ``(None, None)`` if neither is available.
        """
        if self._junction_id:
            try:
                import traci  # noqa: F811
                sx, sy = traci.junction.getPosition(self._junction_id)
                cx, cy = self._sumo_to_carla(sx, sy)
                logger.info(
                    "Junction '%s': SUMO (%.1f, %.1f) → CARLA (%.1f, %.1f)",
                    self._junction_id, sx, sy, cx, cy,
                )
                return (cx, cy)
            except Exception:
                logger.warning("Junction '%s' not found in SUMO.", self._junction_id)
                return (None, None)

        # No junction → focus the spectator over the map's drivable area
        if self._scene_bbox is None:
            self._compute_scene_box()
        bbox = self._scene_bbox
        if bbox is None:
            logger.warning("No --junction and no road topology found; "
                           "spectator will stay at default position.")
            return (None, None)
        cx, cy = (bbox[0] + bbox[1]) / 2.0, (bbox[2] + bbox[3]) / 2.0
        logger.info("Scene centre (map topology): CARLA (%.1f, %.1f) → "
                    "SUMO (%.1f, %.1f)", cx, cy, cx, -cy)
        return (cx, cy)

    # ─────────────────────────────────────────────────────────────────
    # Data export (see data_export/ package)
    # ─────────────────────────────────────────────────────────────────

    def _display_map_name(self) -> str:
        """Short CARLA map name (e.g. 'Demo_1_Enhanced'), used as the
        export output sub-directory."""
        try:
            raw = self._world.get_map().name
            return raw.split("/")[-1] if "/" in raw else raw
        except Exception:
            return self._carla_map_name or "unknown_map"

    def _setup_exporters(self) -> None:
        """CLI path: activate the data-export pipeline (spawn sensors, start
        writers).  Called after CARLA and SUMO are up, before the spectator
        positioning and the sync loop.  Failures here are never fatal: export
        is disabled and the simulation continues.
        """
        if not self._export_config_path:
            return
        resp = self._start_export(self._export_config_path,
                                  self._export_kinds, self._export_dir)
        if not resp["ok"]:
            logger.error("Data export disabled: %s", resp["message"])

    def _start_export(self, config_path: str, kinds: Optional[List[str]],
                      export_dir: Optional[str]) -> Dict[str, Any]:
        """Start one export segment — shared by the CLI path and the runtime
        'start' command.  Must be called on the simulation thread.  Returns
        the control-channel style response dict."""
        if self._export_manager is not None:
            return {"ok": False, "state": self._export_state(),
                    "run_dir": self._export_run_dir,
                    "message": "export already active (stop it first)"}
        try:
            cfg = load_export_config(config_path, self._step_length)
        except ExportConfigError as exc:
            return {"ok": False, "state": "off", "run_dir": None,
                    "message": f"export config invalid: {exc}"}
        if kinds is not None:
            unknown = [k for k in kinds if k not in EXPORTER_REGISTRY]
            if unknown:
                return {"ok": False, "state": "off", "run_dir": None,
                        "message": "unknown exporter kind(s): "
                                   + ", ".join(unknown)}
        out_dir = export_dir or cfg.output_dir
        try:
            run_output = RunOutput(out_dir, self._display_map_name(), cfg)
        except Exception as exc:
            return {"ok": False, "state": "off", "run_dir": None,
                    "message": f"cannot create export output dir '{out_dir}': {exc}"}
        ctx = ExportContext(
            world=self._world,
            client=self._client,
            step_length=self._step_length,
            max_sim_time=self._max_sim_time,
            map_name=self._display_map_name(),
            run_output=run_output,
            sensor_farm=SensorFarm(self._world, logger),
            frame_registry=FrameRegistry(self._step_length),
            logger=logger,
            export_config=cfg,
        )
        try:
            manager = ExporterManager(cfg, ctx, kinds=kinds)
            manager.setup_all()
        except Exception as exc:
            logger.error("Data export setup failed — export disabled: %s", exc)
            try:
                run_output.finalize({"error": str(exc)}, aborted=True)
            except Exception:
                pass
            return {"ok": False, "state": "off", "run_dir": None,
                    "message": f"export setup failed: {exc}"}
        self._export_manager = manager
        self._export_sensor_count = len(ctx.frame_registry.expected_sensors)
        self._export_run_dir = str(run_output.dir)
        n = self._export_sensor_count
        msg = (f"export started: {n} sensor(s) → {run_output.dir}"
               if n else "export active, but no sensor could be spawned "
                         "(see warnings)")
        return {"ok": True, "state": "running", "run_dir": self._export_run_dir,
                "message": msg}

    def _teardown_exporters(self, aborted: Optional[bool] = None) -> None:
        """Stop exporters.  MUST run before ``sync.close()`` — sensor actors
        are destroyed while the CARLA world is still alive.

        ``aborted`` defaults to ``self._sim_aborted``; the runtime 'stop'
        command passes ``aborted=False`` so the segment finalises cleanly.
        """
        if self._export_manager is not None:
            try:
                self._export_manager.teardown_all(
                    aborted=self._sim_aborted if aborted is None else aborted)
            except Exception as exc:
                logger.warning("Data export teardown error: %s", exc)
            self._export_manager = None

    # ─────────────────────────────────────────────────────────────────
    # Runtime export control (export_control.py TCP channel)
    # ─────────────────────────────────────────────────────────────────

    def _start_control_server(self) -> None:
        """Open the TCP control channel (default 127.0.0.1:19090) so
        ``export_control.py`` can start/pause/resume/stop the export while
        the simulation runs.  A busy port disables the channel only — the
        simulation is unaffected."""
        if not self._control_enabled:
            return
        server = ExportControlServer(self._control_host, self._control_port,
                                     handler=self._handle_export_command,
                                     log=logger)
        if server.start():
            self._export_server = server
            logger.info("Export control channel on %s:%d — use "
                        "export_control.py start|pause|resume|stop|status",
                        self._control_host, server.port)

    def _drain_export_commands(self) -> None:
        """Execute queued control commands on the simulation thread
        (called every tick from the sync loop)."""
        if self._export_server is not None:
            self._export_server.drain()

    def _handle_export_command(self, cmd: str,
                               params: Dict[str, Any]) -> Dict[str, Any]:
        if cmd == "status":
            return self._export_status()
        if cmd == "start":
            return self._export_start_cmd(params)
        if cmd == "pause":
            return self._export_pause()
        if cmd == "resume":
            return self._export_resume()
        if cmd == "stop":
            return self._export_stop()
        return {"ok": False, "state": self._export_state(),
                "run_dir": self._export_run_dir,
                "message": f"unknown command '{cmd}'"}

    def _export_state(self) -> str:
        m = self._export_manager
        if m is None:
            return "off"
        return "paused" if m.is_paused() else "running"

    def _export_status(self) -> Dict[str, Any]:
        state = self._export_state()
        parts = [f"export state: {state}"]
        if self._export_manager is not None:
            parts.append(f"{self._export_sensor_count} sensor(s)")
        if self._export_run_dir:
            parts.append(self._export_run_dir)
        return {"ok": True, "state": state, "run_dir": self._export_run_dir,
                "message": " · ".join(parts)}

    def _export_start_cmd(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw_cfg = params.get("export_config")
        if raw_cfg is not None and not isinstance(raw_cfg, str):
            return {"ok": False, "state": self._export_state(),
                    "run_dir": None, "message": "export_config must be a string"}
        if raw_cfg:
            try:
                cfg_path = resolve_export_config_path(
                    raw_cfg, self._display_map_name())
            except ExportConfigError as exc:
                return {"ok": False, "state": self._export_state(),
                        "run_dir": None, "message": str(exc)}
        elif self._export_config_path:
            cfg_path = self._export_config_path
        else:
            cfg_path = resolve_export_config_path(None, self._display_map_name())
            if cfg_path is None:
                return {"ok": False, "state": self._export_state(),
                        "run_dir": None,
                        "message": (f"no export config for map "
                                    f"'{self._display_map_name()}' in "
                                    f"config/export_configs/ — save camera "
                                    f"points with spectator_coords.py --save, "
                                    f"or pass export_config")}
        kinds = _parse_export_kinds(params.get("kinds"))
        export_dir = params.get("export_dir") or None
        if export_dir is not None and not isinstance(export_dir, str):
            return {"ok": False, "state": self._export_state(),
                    "run_dir": None, "message": "export_dir must be a string"}
        return self._start_export(cfg_path, kinds, export_dir)

    def _export_pause(self) -> Dict[str, Any]:
        if self._export_manager is None:
            return {"ok": False, "state": "off", "run_dir": self._export_run_dir,
                    "message": "no active export to pause"}
        self._export_manager.pause()
        return {"ok": True, "state": "paused", "run_dir": self._export_run_dir,
                "message": "export paused (frames stop being written)"}

    def _export_resume(self) -> Dict[str, Any]:
        if self._export_manager is None:
            return {"ok": False, "state": "off", "run_dir": self._export_run_dir,
                    "message": "no active export to resume"}
        self._export_manager.resume()
        return {"ok": True, "state": "running", "run_dir": self._export_run_dir,
                "message": "export resumed"}

    def _export_stop(self) -> Dict[str, Any]:
        if self._export_manager is None:
            return {"ok": False, "state": "off", "run_dir": self._export_run_dir,
                    "message": "no active export to stop"}
        try:
            self._teardown_exporters(aborted=False)
        except Exception as exc:
            return {"ok": False, "state": self._export_state(),
                    "run_dir": self._export_run_dir,
                    "message": f"stop failed: {exc}"}
        return {"ok": True, "state": "off", "run_dir": self._export_run_dir,
                "message": "export stopped (segment finalised)"}

    # ─────────────────────────────────────────────────────────────────
    # Phase 4: Synchronization loop
    # ─────────────────────────────────────────────────────────────────

    def _sync_loop(self) -> None:
        """Main co-simulation loop.

        Each iteration delegates to the official ``SimulationSynchronization.tick()``,
        which handles: SUMO step → vehicle sync (both directions) → TL sync →
        CARLA tick.  Every 20 steps a scene-membership pass bounds CARLA
        actors to the scene box (vehicles outside run in SUMO only).
        """
        import carla  # noqa: F811
        import traci  # noqa: F811

        # ── Resolve actual CARLA map name ──
        raw_map_name = self._world.get_map().name  # e.g. /Game/map_package/Maps/Scene_1/Scene_1
        display_map = raw_map_name.split("/")[-1] if "/" in raw_map_name else raw_map_name

        # ANSI terminal colors for status display
        BOLD   = "\033[1m"
        CYAN   = "\033[36m"
        GREEN  = "\033[32m"
        YELLOW = "\033[33m"
        MAGENTA = "\033[35m"
        RESET  = "\033[0m"
        CLEAR_LINE = "\033[2K"

        sim_time = 0.0
        step = 0
        loop_start = time.time()
        last_status_s = -1  # track last printed status second
        fps_window_steps = 0
        fps_window_start = time.time()

        logger.info(
            "Starting co-simulation loop (max %.1fs, step=%.3fs) ...",
            self._max_sim_time, self._step_length,
        )
        logger.info("Press Ctrl+C to stop early.\n")

        while sim_time < self._max_sim_time:
            # ── Official sync: SUMO ←→ CARLA bidirectional ──
            try:
                self._sync.tick()
            except Exception as exc:
                logger.error("SimulationSynchronization.tick() failed: %s", exc)
                break

            sim_time = traci.simulation.getTime()
            step += 1
            fps_window_steps += 1

            # ── Data export: feed one simulation step to the exporters ──
            if self._export_manager is not None:
                self._export_manager.on_sim_tick(step, sim_time)

            # ── Runtime export control: execute queued commands (start /
            #    pause / resume / stop) at the tick boundary ──
            self._drain_export_commands()

            # ── Scene-membership pass (~1s cadence): bound CARLA actors to
            #    the scene box — vehicles outside run in SUMO only ──
            if step % 20 == 0:
                self._enforce_scene_membership()

            # ── Periodic detailed log ──
            if step % 200 == 0:
                elapsed = time.time() - loop_start
                rt_ratio = elapsed / max(sim_time, 0.001)
                logger.info(
                    "[Step %d] sim=%.1fs | wall=%.1fs (%.1fx real-time) | "
                    "carla_actors=%d",
                    step, sim_time, elapsed, rt_ratio,
                    len(self._sync.sumo2carla_ids),
                )
                # Offset/alignment self-check: any tracked actor more than
                # 500 km from the scene centre means the coordinate mapping is
                # broken (e.g. the map is not at the SUMO internal coords).
                if self._scene_bbox is not None and self._sync.sumo2carla_ids:
                    cx = (self._scene_bbox[0] + self._scene_bbox[1]) / 2.0
                    cy = (self._scene_bbox[2] + self._scene_bbox[3]) / 2.0
                    for vid in list(self._sync.sumo2carla_ids)[:3]:
                        try:
                            x, y = traci.vehicle.getPosition(vid)
                        except Exception:
                            continue
                        vx, vy = self._sumo_to_carla(x, y)
                        if abs(vx - cx) > 5e5 or abs(vy - cy) > 5e5:
                            logger.error(
                                "Offset/alignment mismatch: actor '%s' at SUMO "
                                "(%.0f, %.0f) is >500km from scene centre "
                                "(%.0f, %.0f) — the CARLA map is not at the "
                                "SUMO internal coordinates.",
                                vid, x, y, cx, cy)
                            break

            # ── Live status line every simulated second ──
            int_sim = int(sim_time)
            if int_sim != last_status_s:
                last_status_s = int_sim
                elapsed = time.time() - loop_start
                rt_ratio = elapsed / max(sim_time, 0.001)
                carla_actors = len(self._sync.sumo2carla_ids)

                # FPS over the last window
                fps = fps_window_steps / max(time.time() - fps_window_start, 0.001)
                fps_window_steps = 0
                fps_window_start = time.time()

                # Export health: feed the RT ratio to the manager (it warns
                # when the export pipeline falls behind real time)
                if self._export_manager is not None:
                    self._export_manager.observe_rt(rt_ratio)

                status = (
                    f"{CLEAR_LINE}{BOLD}{CYAN}[{display_map}]{RESET} "
                    f"sim={GREEN}{sim_time:7.1f}s{RESET}  "
                    f"wall={YELLOW}{elapsed:6.0f}s{RESET}  "
                    f"rt={rt_ratio:4.1f}x  "
                    f"fps={fps:5.0f}  "
                    f"actors={MAGENTA}{carla_actors:4d}{RESET}"
                )

                # First few tracked actors' SUMO positions — makes the log
                # self-diagnosing (SUMO coords; CARLA coords are (x, -y))
                if carla_actors:
                    pos_parts = []
                    for vid in list(self._sync.sumo2carla_ids)[:3]:
                        try:
                            x, y = traci.vehicle.getPosition(vid)
                        except Exception:
                            continue
                        pos_parts.append(f"{vid}@({x:.0f},{y:.0f})")
                    if pos_parts:
                        status += "  " + "pos=" + " ".join(pos_parts)

                # Export pipeline flag: sensors buffered → export is falling
                # behind (manager.observe_rt logs the actionable warning)
                if self._export_manager is not None and \
                        self._export_manager.any_behind():
                    status += f"  export={YELLOW}BEHIND{RESET}"

                # Use print so it lands on stderr-free stdout line; logger won't
                # overwrite in place, but a plain print() flushes immediately.
                # We use sys.stderr so it bypasses logger buffering.
                import sys as _sys
                _sys.stderr.write(status + "\n")
                _sys.stderr.flush()

            # ── Auto-focus the spectator on the first spawned vehicle (only
            #    when no --junction was given, so the user always sees the
            #    action even if the map's road area is not at the box centre) ──
            if not self._spectator_followed and self._sync.sumo2carla_ids:
                try:
                    first_vid = next(iter(self._sync.sumo2carla_ids))
                    x, y = traci.vehicle.getPosition(first_vid)
                    cx, cy = self._sumo_to_carla(x, y)
                    wp = self._world.get_map().get_waypoint(
                        carla.Location(x=cx, y=cy, z=0))
                    cam_z = (wp.transform.location.z + 10.0) if wp is not None else 50.0
                    self._world.get_spectator().set_transform(carla.Transform(
                        carla.Location(x=cx, y=cy, z=cam_z),
                        carla.Rotation(pitch=-90.0, yaw=0.0, roll=0.0)))
                    self._spectator_followed = True
                    logger.info(
                        "Spectator focused on first spawned vehicle '%s' at "
                        "CARLA (%.0f, %.0f, %.0f)", first_vid, cx, cy, cam_z)
                except Exception:
                    pass

            if traci.simulation.getMinExpectedNumber() <= 0:
                logger.info("SUMO simulation complete — no more vehicles.")
                break

        logger.info("Co-simulation loop ended after %d steps (%.1fs sim time)",
                     step, sim_time)

    # ─────────────────────────────────────────────────────────────────
    # Cleanup
    # ─────────────────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        """Release all resources."""
        logger.info("Cleaning up ...")

        # Control channel first: cancel queued commands so no export
        # operation runs after the sim loop ended.
        if self._export_server is not None:
            self._export_server.close()
            self._export_server = None

        # Exporters first: sensor actors must be destroyed while the CARLA
        # world is still alive (before SimulationSynchronization.close()).
        self._teardown_exporters()

        # SimulationSynchronization.close() handles sumo, carla, and actor cleanup
        if self._sync is not None:
            try:
                self._sync.close()
                logger.info("  SimulationSynchronization closed")
            except Exception as exc:
                logger.debug("  sync.close() error: %s", exc)

        # Fallback: close sumo if sync wasn't set up
        elif self._sumo_sim is not None:
            try:
                self._sumo_sim.close()
                logger.info("  SumoSimulation closed")
            except Exception as exc:
                logger.debug("  SumoSimulation.close() error: %s", exc)

        # Disable CARLA synchronous mode
        if self._world is not None:
            try:
                settings = self._world.get_settings()
                settings.synchronous_mode = False
                self._world.apply_settings(settings)
                logger.info("  CARLA sync mode disabled")
            except Exception as exc:
                logger.debug("  Could not disable CARLA sync mode: %s", exc)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _setup_signal_handlers() -> None:
    """Install signal handlers so Ctrl+C is handled cleanly."""
    signal.signal(signal.SIGINT, signal.default_int_handler)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and run or check environment."""
    parser = argparse.ArgumentParser(
        description=(
            "SUMO + CARLA Co-Simulation Bridge.\n\n"
            "Loads a pre-imported CARLA map (typically from RoadRunner) via "
            "``client.load_world()``, starts SUMO via CARLA's official "
            "SumoSimulation class, and synchronizes vehicle positions and "
            "traffic light states from SUMO to CARLA each simulation step.\n\n"
            "SUMO controls all traffic behavior; CARLA renders only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic co-simulation with a RoadRunner-enhanced map (60 seconds)
  python run_cosimulation.py \\
      --sumocfg ../路口仿真案例/Demo_1/demo_1.sumocfg \\
      --carla-map Demo_1_Enhanced \\
      --junction 4427 --max-time 60

  # Run with SUMO GUI visible
  python run_cosimulation.py \\
      --sumocfg demo_1.sumocfg --carla-map Demo_1_Enhanced \\
      --junction 4427 --sumo-gui

  # Check environment setup without running
  python run_cosimulation.py --check-env

  # Data export mode: RGB "street camera" footage + roadside lidar.
  # See config/export.example.json for the configuration schema.
  python run_cosimulation.py --sumocfg demo_1.sumocfg \\
      --carla-map Demo_1_Enhanced --junction 4427 --max-time 60 \\
      --export rgb_camera,lidar --export-config config/export.example.json

  # Sensor capture rate is bounded by the simulation step (step=0.05s → 20fps);
  # a true 30fps recording needs the step to match: 1/30 ≈ 0.033333s
  python run_cosimulation.py --sumocfg demo_1.sumocfg \\
      --carla-map Demo_1_Enhanced --step-length 0.033333 --export rgb_camera \\
      --export-config config/export.example.json

  # Step length source: explicit --step-length > sumocfg <time><step-length>
  # > default 0.05. Configure it once in the .sumocfg and both simulators
  # and the data-export pipeline follow automatically.
        """,
    )

    # ── Required inputs ──
    parser.add_argument(
        "--sumocfg",
        help="Path to SUMO .sumocfg configuration file",
    )
    parser.add_argument(
        "--carla-map",
        help="Name of the pre-imported CARLA map to load via client.load_world() "
             "(e.g. 'Demo_1_Enhanced'). The map must have been imported into "
             "CARLA's Unreal project beforehand.",
    )
    # ── CARLA connection ──
    parser.add_argument(
        "--carla-host", default="127.0.0.1",
        help="CARLA server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--carla-port", type=int, default=2000,
        help="CARLA server port (default: 2000)",
    )

    # ── Simulation control ──
    parser.add_argument(
        "--step-length", type=float, default=None,
        help="Simulation step length in seconds. Default: from sumocfg "
             "<time><step-length>, else 0.05. Explicit value takes "
             "precedence over the .sumocfg setting.",
    )
    parser.add_argument(
        "--max-time", type=float, default=3600.0,
        help="Maximum simulation time in seconds (default: 3600.0)",
    )

    # ── SUMO ──
    parser.add_argument(
        "--sumo-gui", action="store_true",
        help="Use sumo-gui instead of headless sumo",
    )

    # ── Misc ──
    parser.add_argument(
        "--junction",
        help="SUMO junction ID to focus the CARLA spectator camera on "
             "(e.g. '4427').",
    )

    # ── Runtime export control (TCP channel, see export_control.py) ──
    parser.add_argument(
        "--control-host", default="127.0.0.1",
        help="Export control listen host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--control-port", type=int, default=DEFAULT_PORT,
        help="Export control listen port (default: 19090)",
    )
    parser.add_argument(
        "--no-control", action="store_true",
        help="Disable the runtime export control channel",
    )
    parser.add_argument(
        "--scene-box",
        metavar="X0,Y0,X1,Y1",
        help="Manually define the CARLA-scene bounding box (CARLA coords, "
             "comma-separated, x0<x1 and y0<y1). Overrides auto-computation "
             "from map topology. Vehicles outside this box are not spawned "
             "into CARLA; without --junction the spectator focuses on the box "
             "centre.",
    )
    parser.add_argument(
        "--check-env", action="store_true",
        help="Validate environment setup and exit without running",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Reduce output verbosity",
    )

    # ── Data export ──
    parser.add_argument(
        "--export",
        metavar="KINDS",
        help="Comma-separated data exporters to activate during the run, e.g. "
             "'rgb_camera,lidar'. Available kinds: "
             + ", ".join(sorted(EXPORTER_REGISTRY)),
    )
    parser.add_argument(
        "--export-config",
        help="Export configuration: a file path, or the name of a per-map "
             "config under config/export_configs/ (e.g. 'WestZone'). When "
             "omitted with --export, the config for --carla-map is looked up "
             "automatically.",
    )
    parser.add_argument(
        "--export-dir",
        help="Root directory for exported data (default: config/toolchain.json "
             "exports_dir, 即 ../../data/exports;导出配置 output.export_dir 可覆盖)",
    )

    args = parser.parse_args(argv)

    if args.quiet:
        logger.setLevel(logging.WARNING)

    # ── 步长来源:显式 --step-length > sumocfg <time><step-length> > 默认 0.05
    #    (check_env 分支与导出配置预校验都依赖此值,故在它们之前解析)──
    step_length = _resolve_step_length(parser, args, args.sumocfg)

    # ── Resolve export mode ──
    export_kinds = None
    export_config_path = None
    if args.export:
        export_kinds = [k.strip() for k in args.export.split(",") if k.strip()]
        unknown = [k for k in export_kinds if k not in EXPORTER_REGISTRY]
        if unknown:
            parser.error(f"unknown exporter kind(s): {', '.join(unknown)} "
                         f"(available: {', '.join(sorted(EXPORTER_REGISTRY))})")
    if args.export or args.export_config:
        # --export-config: file path, per-map config name, or (with --export)
        # auto-lookup of the config for --carla-map.
        try:
            export_config_path = resolve_export_config_path(
                args.export_config, args.carla_map)
        except ExportConfigError as exc:
            parser.error(str(exc))
        if export_config_path is None:
            if args.export_config:
                parser.error(f"no export config for map '{args.carla_map}' in "
                             f"config/export_configs/ — save camera points with "
                             f"spectator_coords.py --save first")
            # --export without a config → export mode stays disabled.
            logger.info("Data export skipped: no export config found for map "
                        "'%s' (save points with spectator_coords.py --save, "
                        "or pass --export-config)", args.carla_map)
        else:
            # Validate the config BEFORE touching CARLA — fail fast, exit 2.
            try:
                load_export_config(export_config_path, step_length)
            except ExportConfigError as exc:
                parser.error(f"invalid export config: {exc}")

    if args.check_env:
        return CoSimulationBridge.check_environment(export_config_path,
                                                     step_length)

    if not args.sumocfg:
        parser.error("--sumocfg is required (or use --check-env)")
    if not args.carla_map:
        parser.error("--carla-map is required (or use --check-env)")

    if args.max_time <= 0:
        parser.error("--max-time must be > 0")

    scene_box = None
    if args.scene_box:
        try:
            x0, y0, x1, y1 = (float(v) for v in args.scene_box.split(","))
            if not (x0 < x1 and y0 < y1):
                raise ValueError("need x0<x1 and y0<y1")
            scene_box = (x0, x1, y0, y1)  # (min_x, max_x, min_y, max_y)
        except (ValueError, TypeError) as exc:
            parser.error(f"--scene-box invalid: {exc}")

    bridge = CoSimulationBridge(
        sumocfg_path=args.sumocfg,
        carla_map_name=args.carla_map,
        carla_host=args.carla_host,
        carla_port=args.carla_port,
        step_length=step_length,
        max_sim_time=args.max_time,
        sumo_gui=args.sumo_gui,
        junction_id=args.junction,
        scene_box=scene_box,
        export_config_path=export_config_path,
        export_dir=args.export_dir,
        export_kinds=export_kinds,
        control_host=args.control_host,
        control_port=args.control_port,
        control_enabled=not args.no_control,
    )
    bridge.run()
    return 0


def _parse_export_kinds(raw: Any) -> Optional[List[str]]:
    """Normalise the control-channel 'kinds' field: None/empty → None (all
    kinds in the config); a comma-separated string or a JSON array → list."""
    if raw is None:
        return None
    if isinstance(raw, list):
        items = [str(k).strip() for k in raw]
    elif isinstance(raw, str):
        items = [k.strip() for k in raw.split(",")]
    else:
        return []  # 非法类型 → 触发 _start_export 的 unknown-kind 校验
    items = [k for k in items if k]
    return items or None


if __name__ == "__main__":
    sys.exit(main())
