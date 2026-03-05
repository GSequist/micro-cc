"""Track background processes launched during session.

Snapshots listening ports at startup, diffs each check
to report what Claude's bash_ commands left running.
"""

import subprocess


_initial_ports = set()  # (pid, port, process_name)


def init():
    """Snapshot current listening ports at startup."""
    global _initial_ports
    _initial_ports = _get_listening_ports()


def _get_listening_ports():
    """Get set of (pid, port, name) for TCP listeners."""
    try:
        result = subprocess.run(
            ["lsof", "-iTCP", "-sTCP:LISTEN", "-nP"],
            capture_output=True, text=True, timeout=5
        )
        ports = set()
        for line in result.stdout.strip().split("\n")[1:]:  # skip header
            parts = line.split()
            if len(parts) >= 9:
                name = parts[0]
                pid = parts[1]
                port = parts[8].split(":")[-1]  # *:8000 -> 8000
                ports.add((pid, port, name))
        return ports
    except Exception:
        return set()


def format_status():
    """Return new listening ports since startup, or empty string."""
    current = _get_listening_ports()
    new_ports = current - _initial_ports
    if not new_ports:
        return ""
    lines = ["Background processes listening on ports:"]
    for pid, port, name in sorted(new_ports, key=lambda x: int(x[1]) if x[1].isdigit() else 0):
        lines.append(f"  PID {pid}: {name} on :{port}")
    return "\n".join(lines)
