#!/usr/bin/env python3
"""tareas_diarias — puente del aviso diario de tareas. SIN logica propia.

La logica vive en su dueno: el skill `nextcloud-tasks`, comando `diarias`. Este
archivo existe por una sola razon: el cron del core
(`cron/scheduler_script.py::_resolve_script_path`) exige que el script resuelva
DENTRO de `<HERMES_HOME>/scripts/`, y el skill vive en otro lado. Asi que aca no
se formatea ni se decide nada: se compone la ruta del comando, se lo ejecuta y se
pasa su stdout TAL CUAL.

Contrato:
  1. Si el comando no imprime nada, aca tampoco se imprime nada (job `no_agent`:
     stdout vacio = no se manda mensaje).
  2. Solo lee. No escribe en el servidor ni marca nada.
  3. Lista, categoria y titulo viven en `tareas_diarias.local.json`, al lado de
     este archivo y fuera de git: la configuracion pertenece a su dueno.
  4. La ruta del comando se compone, no se escribe: `skills.external_dirs` del
     config.yaml del home y, si no esta ahi, el directorio de skills del home
     activo. Si no aparece en ninguno, falla diciendo DONDE busco.

Uso:
    python3 tareas_diarias.py                 el aviso (o silencio)
    python3 tareas_diarias.py --diagnostico   que config uso y que comando corrio
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CONFIG_LOCAL = Path(__file__).with_name("tareas_diarias.local.json")
COMANDO = "productivity/nextcloud-tasks/scripts/tareas.py"
DEFECTOS = {"lista": "Mauro ToDo", "categoria": "diario", "titulo": None}


def home_hermes() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def cargar_config() -> dict:
    cfg = dict(DEFECTOS)
    if CONFIG_LOCAL.is_file():
        try:
            cfg.update(json.loads(CONFIG_LOCAL.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as e:
            print(f"tareas_diarias: config local ilegible ({e})", file=sys.stderr)
    return cfg


def _external_dirs(cfg_path: Path) -> list[str]:
    """`skills.external_dirs` del config.yaml, leido por indentacion (sin PyYAML)."""
    try:
        lineas = cfg_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    salida: list[str] = []
    en_skills = False
    en_lista = False
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
        if en_lista and texto.startswith("- "):
            salida.append(texto[2:].strip().strip("\"'"))
            continue
        if texto.startswith("external_dirs"):
            en_lista = True
            resto = texto.partition(":")[2].strip()
            if resto.startswith("[") and resto.endswith("]"):
                for parte in resto[1:-1].split(","):
                    limpio = parte.strip().strip("\"'")
                    if limpio:
                        salida.append(limpio)
            continue
        if sangria <= 2:
            en_lista = False
    return salida


def resolver_comando() -> Path:
    """Compone la ruta del comando de tareas; si no lo encuentra, dice donde busco."""
    home = home_hermes()
    buscados: list[Path] = [home / "skills" / COMANDO]
    buscados += [
        Path(raiz).expanduser() / COMANDO
        for raiz in _external_dirs(home / "config.yaml")
    ]
    buscados.append(Path.home() / "developers" / "ai" / "skills" / COMANDO)
    for ruta in buscados:
        if ruta.is_file():
            return ruta
    print(
        "tareas_diarias: no encuentro el comando de tareas. Busque en:\n  "
        + "\n  ".join(str(b) for b in buscados),
        file=sys.stderr,
    )
    raise SystemExit(1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--diagnostico", action="store_true")
    args = ap.parse_args(argv)

    cfg = cargar_config()
    comando = resolver_comando()
    orden = [
        sys.executable,
        str(comando),
        "diarias",
        "--lista",
        cfg["lista"],
        "--categoria",
        cfg["categoria"],
    ]
    if cfg.get("titulo"):
        orden += ["--titulo", cfg["titulo"]]

    if args.diagnostico:
        existe = "existe" if CONFIG_LOCAL.is_file() else "defaults"
        print(f"config: {CONFIG_LOCAL} ({existe})")
        print(f"comando: {comando}")
        print(f"orden: {' '.join(orden[1:])}")
        print("--- salida del comando ---")

    salida = subprocess.run(
        orden, check=False, capture_output=True, text=True, timeout=120
    )
    if salida.returncode != 0:
        print(
            f"tareas_diarias: el comando fallo ({salida.returncode}): "
            f"{salida.stderr.strip()[:300]}",
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(salida.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
