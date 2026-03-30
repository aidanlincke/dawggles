#!/usr/bin/env python3
"""
Send SIGUSR1 to Dawggles main.py → one JPEG capture, sent on the current TCP client.

Requires on the Pi before starting main.py:
  export DAWGGLES_SIGUSR1_SNAP=1

Requires push_client (or similar) already connected and past auth.

  python3 trigger_snap_send.py           # auto-pick single main.py PID
  python3 trigger_snap_send.py 12345      # explicit PID
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys


def _candidate_pids() -> list[int]:
    try:
        out = subprocess.check_output(["pgrep", "-af", "main.py"], text=True)
    except subprocess.CalledProcessError:
        return []
    pids: list[int] = []
    for line in out.strip().splitlines():
        if "trigger_snap_send" in line:
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            pass
    return pids


def main() -> None:
    if len(sys.argv) >= 2:
        pid = int(sys.argv[1])
    else:
        pids = _candidate_pids()
        if not pids:
            print(
                "no matching process; use: python3 trigger_snap_send.py <PID>",
                file=sys.stderr,
            )
            sys.exit(1)
        if len(pids) > 1:
            print(f"multiple PIDs {pids}; pass one: python3 trigger_snap_send.py <PID>", file=sys.stderr)
            sys.exit(1)
        pid = pids[0]
    os.kill(pid, signal.SIGUSR1)
    print(f"sent SIGUSR1 to pid {pid}")


if __name__ == "__main__":
    main()
