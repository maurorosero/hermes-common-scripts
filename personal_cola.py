#!/usr/bin/env python3
"""personal_cola — el puente del ciclo de Personal: la cola, dicha por el vigilante.

Para que sirve: el ciclo vive en el skill `correo-personal`, pero el cron exige su
script bajo `$HERMES_HOME/scripts/`. Esto es el puente y no tiene logica propia: corre
`correo-personal/scripts/casos.py` y devuelve su salida tal cual.

El contrato del wake gate es de `casos.py` y aqui no se toca:
  - no hay casos  -> la ultima linea es {"wakeAgent": false} y el agente no corre;
  - algo falla    -> NO se imprime el gate: se imprime el error y sale 1.

La ruta del skill se COMPONE (skills.external_dirs del config.yaml del home y, si no
esta ahi, el directorio de skills del home activo): no se hardcodea.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VIGILANTE = "productivity/correo-personal/scripts/casos.py"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _expandir(entrada: str) -> Path:
    ruta = Path(os.path.expanduser(os.path.expandvars(entrada)))
    return ruta if ruta.is_absolute() else hermes_home() / ruta


def _external_dirs(cfg_path: Path) -> list[str]:
    """`skills.external_dirs` del config.yaml, leido por indentacion (sin PyYAML)."""
    try:
        lineas = cfg_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    salida: list[str] = []
    en_skills = en_lista = False
    sangria_lista = -1
    for cruda in lineas:
        texto = cruda.strip()
        if not texto or texto.startswith("#"):
            continue
        sangria = len(cruda) - len(cruda.lstrip())
        if sangria == 0:
            en_skills, en_lista = texto == "skills:", False
            continue
        if not en_skills:
            continue
        # Los items de la lista pueden colgar con MAS sangria que la clave o ALINEADOS
        # con ella, que es el estilo de config.yaml. Aceptar solo la primera forma deja
        # la lista vacia y el puente no encuentra casos.py. Medido el 2026-10-22.
        if en_lista and sangria >= sangria_lista and texto.startswith("- "):
            salida.append(texto[2:].strip().strip("\"'"))
            continue
        if en_lista and sangria < sangria_lista:
            en_lista = False
        if sangria == 2:
            en_lista = texto == "external_dirs:"
            sangria_lista = sangria
            continue
    return salida


def resolver(relativo: str) -> Path:
    bases = [_expandir(e) for e in _external_dirs(hermes_home() / "config.yaml")]
    bases.append(hermes_home() / "skills")
    candidatos = [base / relativo for base in bases]
    for ruta in candidatos:
        if ruta.is_file():
            return ruta
    buscado = "\n".join(f"    - {c}" for c in candidatos)
    raise FileNotFoundError(f"no encontre {relativo}.\n  busque en:\n{buscado}")


def main() -> int:
    try:
        ruta = resolver(VIGILANTE)
    except FileNotFoundError as err:
        print(f"ERROR: {err}")
        return 1

    proceso = subprocess.run(
        [sys.executable, str(ruta)],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    salida = (proceso.stdout or "") + (proceso.stderr or "")
    if salida.strip():
        print(salida.rstrip())
    return proceso.returncode


if __name__ == "__main__":
    sys.exit(main())
