#!/usr/bin/env python3
"""Lanzador del Oficio 2 del presenter. Lo unico que vive aca es la obligacion del cron.

`hermes cron` exige que el script este bajo ~/.hermes/scripts/, asi que no puede entrar al
skill. El codigo del proceso vive en el skill `presenter-flow` (`scripts/presenter_dispatcher.py`);
esto solo lo ejecuta.

Sin logica: la puerta, la salud y las enterradas —y callarse cuando no hay nada que contar,
que corre 288 veces al dia— deciden del otro lado. Si algo hay que cambiar, se cambia en el
skill.
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
    / "presenter-flow"
    / "scripts"
    / "presenter_dispatcher.py"
)

if not REAL.is_file():
    raise SystemExit(f"no encuentro el oficio del skill presenter-flow: {REAL}")

sys.argv[0] = str(REAL)
runpy.run_path(str(REAL), run_name="__main__")
