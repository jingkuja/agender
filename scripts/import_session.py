#!/usr/bin/env python3
"""Wrapper: agender-worker session import --site douyin --file PATH."""

from __future__ import annotations

import subprocess
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: import_session.py <storage_state.json>")
    raise SystemExit(
        subprocess.call(
            ["agender-worker", "session", "import", "--site", "douyin", "--file", sys.argv[1]]
        )
    )
