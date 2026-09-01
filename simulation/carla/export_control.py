#!/usr/bin/env python3
"""Runtime control of data export during co-simulation (TCP command channel).

Server side (imported by ``run_cosimulation.py``)::

    from export_control import ExportControlServer

    server = ExportControlServer(handler=my_handler)
    server.start()          # listen on 127.0.0.1:19090 (default)
    ...
    server.drain()          # called from the SIMULATION thread, once per tick
    ...
    server.close()          # in cleanup

Commands are received on a local TCP port, queued, and executed by the
simulation thread at the next tick boundary (they touch the CARLA world and
file handles, so they must not run on the socket threads).

Client side (CLI — run it in another terminal while the co-simulation runs)::

    python export_control.py status
    python export_control.py start --export-config WestZone --export rgb_camera,lidar
    python export_control.py pause
    python export_control.py resume
    python export_control.py stop

Protocol: one connection per command; a single JSON line (UTF-8, ``\\n``
terminated, ≤ 64 KiB) in each direction.  Request::

    {"cmd": "start", "export_config": "WestZone", "kinds": "rgb_camera,lidar",
     "export_dir": "/abs/path"}

Response (always)::

    {"ok": true, "state": "off|running|paused", "run_dir": "...|null",
     "message": "..."}

Exit codes: 0 = command accepted, 1 = command rejected (ok=false), 2 =
connection failure / timeout.  This module imports ONLY the standard library,
so the client runs on machines without CARLA or SUMO.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import socket
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19090
CONNECT_TIMEOUT_S = 5.0     # client: connect + first byte
CMD_TIMEOUT_S = 120.0       # server: max wait for the sim thread to execute
MAX_REQUEST_BYTES = 64 * 1024

logger = logging.getLogger("cosim.export_control")


class _PendingCommand:
    """One queued command: the handler thread puts it, the simulation thread
    executes it and fills ``result``, then ``event`` is set."""

    __slots__ = ("cmd", "params", "result", "event")

    def __init__(self, cmd: str, params: Dict[str, Any]) -> None:
        self.cmd = cmd
        self.params = params
        self.result: Optional[Dict[str, Any]] = None
        self.event = threading.Event()


class ExportControlServer:
    """Accepts commands on a local TCP port and runs them on the simulation
    thread via :meth:`drain`.  All socket work happens on daemon threads;
    command execution happens only on the caller's (simulation) thread.

    ``handler(cmd, params) -> dict`` must be thread-safe only in the sense
    that it is called from exactly one thread (the one calling ``drain``).
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 handler: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
                 log: Optional[logging.Logger] = None) -> None:
        self._host = host
        self._port = port
        self._handler = handler
        self._log = log or logger
        self._listener: Optional[socket.socket] = None
        self._actual_port = 0
        self._queue: "queue.Queue[_PendingCommand]" = queue.Queue()
        self._conns: List[socket.socket] = []
        self._lock = threading.Lock()
        self._closed = threading.Event()

    # -- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Bind + listen and spawn the accept thread.  Returns False (and
        logs a warning) when the port cannot be bound — the caller should
        degrade gracefully, not crash."""
        if self._closed.is_set():
            return False
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._host, self._port))
            listener.listen(8)
            listener.settimeout(0.5)   # poll _closed during accept
        except OSError as exc:
            self._log.warning("Export control channel: cannot bind "
                              "%s:%d — %s", self._host, self._port, exc)
            return False
        self._listener = listener
        self._actual_port = listener.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True,
                         name="export-control-accept").start()
        return True

    def close(self) -> None:
        """Stop accepting, cancel queued commands and close all sockets
        (idempotent).  Must be called before the simulation tears down the
        export pipeline, so no command runs after the sim loop ended."""
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            # Cancel queued commands: drain() skips entries whose result is
            # already set, so these will never execute.
            while True:
                try:
                    pending = self._queue.get_nowait()
                except queue.Empty:
                    break
                pending.result = {"ok": False, "state": None, "run_dir": None,
                                  "message": "control server shutting down"}
                pending.event.set()
        # Handlers poll the event every 0.5s; give them a moment to send the
        # "shutting down" reply before the sockets are torn down below.
        time.sleep(0.6)
        with self._lock:
            for conn in self._conns:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    conn.close()
                except OSError:
                    pass
            self._conns.clear()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None

    # -- simulation-thread API ------------------------------------------

    def drain(self) -> int:
        """Execute every queued command on the CALLING thread (must be the
        simulation thread; called once per tick).  Each command's result is
        written back and its event set so the socket handler can reply.
        Returns the number of commands executed."""
        n = 0
        while True:
            try:
                pending = self._queue.get_nowait()
            except queue.Empty:
                break
            if pending.result is not None:
                continue            # cancelled by close() — never execute
            n += 1
            try:
                if self._handler is not None:
                    result = self._handler(pending.cmd, pending.params)
                else:
                    result = {"ok": False, "state": None, "run_dir": None,
                              "message": "no command handler installed"}
            except Exception as exc:  # handler must never break the sim loop
                self._log.warning("Export control: command '%s' failed: %s",
                                  pending.cmd, exc)
                result = {"ok": False, "state": None, "run_dir": None,
                          "message": f"command error: {exc}"}
            if not isinstance(result, dict):
                result = {"ok": False, "state": None, "run_dir": None,
                          "message": f"bad handler result: {result!r}"}
            pending.result = result
            pending.event.set()
        return n

    # -- properties -----------------------------------------------------

    @property
    def port(self) -> int:
        """The port actually bound (useful when port=0 was requested)."""
        return self._actual_port

    @property
    def is_listening(self) -> bool:
        return self._listener is not None and not self._closed.is_set()

    # -- internals ------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _addr = self._listener.accept()  # type: ignore[union-attr]
            except OSError:
                if self._closed.is_set():
                    break
                continue
            with self._lock:
                if self._closed.is_set():
                    try:
                        conn.close()
                    except OSError:
                        pass
                    break
                self._conns.append(conn)
            threading.Thread(target=self._handle_conn, args=(conn,),
                             daemon=True,
                             name="export-control-handler").start()

    def _handle_conn(self, conn: socket.socket) -> None:
        """One connection, one command: read a JSON line, queue it, wait for
        the simulation thread to execute it, reply, close."""
        resp: Dict[str, Any]
        try:
            conn.settimeout(30.0)  # idle client protection
            raw = self._read_line(conn)
            req = json.loads(raw)
            cmd = req.get("cmd")
            if not isinstance(cmd, str):
                raise ValueError("missing or invalid 'cmd'")
            params = {k: v for k, v in req.items() if k != "cmd"}
            pending = _PendingCommand(cmd, params)
            with self._lock:
                if self._closed.is_set():
                    pending.result = {
                        "ok": False, "state": None, "run_dir": None,
                        "message": "control server shutting down"}
                    pending.event.set()
                else:
                    self._queue.put(pending)
            deadline = time.monotonic() + CMD_TIMEOUT_S
            while not pending.event.wait(0.5):
                if self._closed.is_set():
                    pending.result = {
                        "ok": False, "state": None, "run_dir": None,
                        "message": "control server shutting down"}
                    break
                if time.monotonic() >= deadline:
                    pending.result = {
                        "ok": False, "state": None, "run_dir": None,
                        "message": "command timed out waiting for the "
                                   "simulation thread"}
                    break
            resp = pending.result or {"ok": False, "state": None,
                                      "run_dir": None, "message": "no response"}
        except Exception as exc:
            resp = {"ok": False, "state": None, "run_dir": None,
                    "message": f"invalid request: {exc}"}
        finally:
            try:
                conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8")
                             + b"\n")
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
            with self._lock:
                try:
                    self._conns.remove(conn)
                except ValueError:
                    pass

    @staticmethod
    def _read_line(conn: socket.socket) -> str:
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                raise ConnectionError("client closed the connection")
            buf += chunk
            if len(buf) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
        return buf[:-1].decode("utf-8")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def send_command(host: str, port: int, req: Dict[str, Any],
                 timeout: float = 60.0) -> Dict[str, Any]:
    """Send one command and wait for the response.

    Raises:
        ConnectionError: cannot connect, timed out, or server closed early.
    """
    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
    except OSError as exc:
        raise ConnectionError(
            f"cannot connect to {host}:{port} — is the co-simulation "
            f"running? ({exc})")
    try:
        sock.settimeout(timeout)
        sock.sendall(json.dumps(req).encode("utf-8") + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf:
            raise ConnectionError(
                f"no response from {host}:{port} (server closed the connection)")
        return json.loads(buf)
    except (socket.timeout, OSError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"command failed: {exc}")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Control data export of a running co-simulation "
                    "(see run_cosimulation.py --control-port).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python export_control.py status\n"
            "  python export_control.py start --export-config WestZone\n"
            "  python export_control.py start --export rgb_camera,lidar\n"
            "  python export_control.py pause\n"
            "  python export_control.py resume\n"
            "  python export_control.py stop\n"
        ))
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", default=DEFAULT_HOST,
                        help=f"control host (default: {DEFAULT_HOST})")
    common.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"control port (default: {DEFAULT_PORT})")
    common.add_argument("--timeout", type=float, default=60.0,
                        help="max seconds to wait for the command to execute "
                             "(default: 60)")
    common.add_argument("--json", action="store_true",
                        help="print the raw JSON response")

    p_start = sub.add_parser("start", parents=[common],
                             help="start a new export segment")
    p_start.add_argument("--export-config",
                         help="export config: a file path, or a name under "
                              "config/export_configs/ (e.g. 'WestZone'); "
                              "default: auto-lookup by the current map")
    p_start.add_argument("--export", metavar="KINDS",
                         help="comma-separated exporter kinds, e.g. "
                              "'rgb_camera,lidar' (default: all kinds in "
                              "the config)")
    p_start.add_argument("--export-dir",
                         help="root directory for the exported data "
                              "(default: from the export config)")
    for name, help_text in (
            ("pause", "pause the active export (frames stop being written)"),
            ("resume", "resume a paused export"),
            ("stop", "stop the active export and finalise the segment"),
            ("status", "show the current export state")):
        sub.add_parser(name, parents=[common], help=help_text)

    args = ap.parse_args(argv)

    req: Dict[str, Any] = {"cmd": args.cmd}
    if args.cmd == "start":
        if getattr(args, "export_config", None):
            req["export_config"] = args.export_config
        if getattr(args, "export", None):
            req["kinds"] = args.export
        if getattr(args, "export_dir", None):
            req["export_dir"] = args.export_dir

    try:
        resp = send_command(args.host, args.port, req, timeout=args.timeout)
    except ConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(resp, ensure_ascii=False))
    else:
        state = resp.get("state") or "?"
        message = resp.get("message") or ""
        run_dir = resp.get("run_dir")
        mark = "✓" if resp.get("ok") else "✗"
        line = f"{mark} [{state}] {message}"
        if run_dir:
            line += f"\n   run_dir: {run_dir}"
        print(line)
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
