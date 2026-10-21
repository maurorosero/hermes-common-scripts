#!/usr/bin/env python3
"""Lanzador del watcher de research. Lo unico que vive aca es la obligacion del cron.

`hermes cron` exige que el script este bajo ~/.hermes/scripts/, asi que no puede entrar a un
skill. El codigo del proceso vive en el skill `research-pipeline`; esto solo lo ejecuta con sus
mismos argumentos.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

HOME = Path(os.environ.get("HOME") or Path.home())
REAL = (
    HOME
    / "developers"
    / "ai"
    / "skills"
    / "research"
    / "research-pipeline"
    / "scripts"
    / "research-watcher.py"
)

if not REAL.exists():
    raise SystemExit(f"no encuentro el watcher del skill research-pipeline: {REAL}")

sys.argv[0] = str(REAL)
runpy.run_path(str(REAL), run_name="__main__")
