"""netconvert subprocess wrapper — converts SUMO .net.xml to OpenDRIVE .xodr."""

import os
import shutil
import subprocess
import sys


def run_netconvert(
    net_xml_path: str,
    output_xodr_path: str,
    netconvert_bin: str = "netconvert",
    timeout_s: int = 300,
) -> subprocess.CompletedProcess:
    """Convert a SUMO .net.xml to OpenDRIVE .xodr using netconvert.

    Executes:
        netconvert --sumo-net-file <in> --opendrive-output <out>

    Args:
        net_xml_path: Path to the (clipped) SUMO .net.xml file.
        output_xodr_path: Desired output .xodr path.
        netconvert_bin: Path to netconvert binary or just "netconvert" for
            PATH lookup.
        timeout_s: Timeout in seconds (default 300).

    Returns:
        subprocess.CompletedProcess with captured stdout/stderr.

    Raises:
        FileNotFoundError: netconvert is not installed or not in PATH.
        subprocess.TimeoutExpired: Conversion took longer than timeout_s.
        RuntimeError: netconvert exited with non-zero return code.
    """
    # Verify netconvert is available
    if not shutil.which(netconvert_bin):
        raise FileNotFoundError(
            f"netconvert not found: '{netconvert_bin}' is not installed or not in PATH.\n"
            f"Install SUMO (https://eclipse.dev/sumo/) or set SUMO_HOME and ensure\n"
            f"netconvert is accessible. You can also use --skip-netconvert to stop\n"
            f"after generating the clipped .net.xml file."
        )

    # Build command
    cmd = [
        netconvert_bin,
        "--sumo-net-file", net_xml_path,
        "--opendrive-output", output_xodr_path,
    ]

    print(f"[netconvert] Running: {' '.join(cmd)}")
    print(f"[netconvert] Input: {net_xml_path} "
          f"({_format_size(os.path.getsize(net_xml_path))})")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout_s,
            output=f"netconvert timed out after {timeout_s}s.\n"
                   f"Try increasing --timeout or reducing --dist for a smaller output."
        )

    # Print netconvert output
    if result.stdout:
        # Only print first few lines — netconvert can be verbose
        lines = result.stdout.strip().split('\n')
        for line in lines[:15]:
            print(f"[netconvert] {line}")
        if len(lines) > 15:
            print(f"[netconvert] ... ({len(lines) - 15} more lines)")

    if result.returncode != 0:
        raise RuntimeError(
            f"netconvert exited with code {result.returncode}:\n"
            f"{result.stderr}"
        )

    # Verify output was created
    if not os.path.exists(output_xodr_path):
        raise RuntimeError(
            f"netconvert completed but no output file was created at "
            f"'{output_xodr_path}'"
        )

    print(f"[netconvert] Output: {output_xodr_path} "
          f"({_format_size(os.path.getsize(output_xodr_path))})")
    return result


def _format_size(size_bytes: int) -> str:
    """Format a byte count for display."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
