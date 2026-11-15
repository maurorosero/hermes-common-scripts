#!/usr/bin/env python3
"""nivel3_cola — el puente del ciclo de correo: fase 1·2 y cola de la fase 3.

Para que sirve: el ciclo vive en el skill `email-classifier`, pero el cron exige su
script bajo `$HERMES_HOME/scripts/`. Esto es el puente y no tiene logica propia:

  1. `clasificador.py correr --aplicar`  -> fase 1·2: las reglas mueven y marcan.
  2. `pendientes.py`                     -> fase 3: la cola, con el texto de cada correo.
                                            Sin nada pendiente, su ultima linea es
                                            {"wakeAgent": false} y el agente no corre
                                            (cero tokens, sin entrega).

Silencio = no quedo nada que leer. Un fallo NO se disfraza de silencio: se imprime y
sale con error, que es lo que hace que el job avise.

La ruta del skill se COMPONE (`skills.external_dirs` del config.yaml del home y, si no
esta ahi, el directorio de skills del home activo): no se escribe a mano.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PASOS: tuple[tuple[str, list[str]], ...] = (
    ("productivity/email-classifier/scripts/clasificador.py", ["correr", "--aplicar"]),
    ("productivity/email-classifier/scripts/pendientes.py", ["--tope", "900"]),
)

TIMEOUT = 900


class ErrorEntorno(Exception):
    """Falta el skill o la configuracion. No es un fallo del buzon."""


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
        if sangria == 2 and not texto.startswith("- "):
            en_lista = texto == "external_dirs:"
            continue
        if en_lista and texto.startswith("- "):
            salida.append(texto[2:].strip().strip("\"'"))
    return salida


def bases_de_skills() -> list[Path]:
    bases = [_expandir(e) for e in _external_dirs(hermes_home() / "config.yaml")]
    bases.append(hermes_home() / "skills")
    vistas: set[str] = set()
    salida: list[Path] = []
    for base in bases:
        if base.is_dir() and str(base) not in vistas:
            vistas.add(str(base))
            salida.append(base)
    return salida


def resolver(relativo: str) -> Path:
    candidatos = [base / relativo for base in bases_de_skills()]
    for ruta in candidatos:
        if ruta.is_file():
            return ruta
    buscado = "\n".join(f"    - {c}" for c in candidatos) or "    (ninguna base existe)"
    raise ErrorEntorno(f"no encontre {relativo}.\n  busque en:\n{buscado}")


def correr(ruta: Path, args: list[str]) -> tuple[int, str]:
    proceso = subprocess.run(
        [sys.executable, str(ruta), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    return proceso.returncode, (proceso.stdout or "") + (proceso.stderr or "")


def main() -> int:
    for relativo, args in PASOS:
        try:
            ruta = resolver(relativo)
        except ErrorEntorno as err:
            print(f"ERROR: {err}")
            return 1
        codigo, salida = correr(ruta, args)
        if salida.strip():
            print(salida.rstrip())
        if codigo != 0:
            print(f"ERROR: {ruta.name} {' '.join(args)} salio {codigo}")
            return codigo if codigo > 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
