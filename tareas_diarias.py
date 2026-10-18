#!/usr/bin/env python3
"""tareas_diarias — el recordatorio de todos los dias para las tareas marcadas.

Para que sirve: Mauro tiene pendientes que no vencen nunca pero que hay que
empujar todos los dias (una solicitud que se completa hasta que la acepten, un
tramite que no avanza solo). Esas tareas llevan la categoria `diario` y este
puente las enumera una vez al dia.

Contrato (no configurable, es el diseno):
  1. SI NO HAY NADA ABIERTO CON LA CATEGORIA, NO IMPRIME NADA Y SALE 0. Corre a
     diario: un mensaje que dice "no hay nada" todos los dias es el ruido que
     este diseno existe para eliminar.
  2. SOLO LEE. No escribe en el servidor ni marca nada: la tarea deja de
     aparecer cuando se completa, no cuando se avisa.
  3. NO LLEVA NADA INTERNO. La lista y la categoria son datos de esta
     instalacion y viven en `tareas_diarias.local.json` (que git ignora).
  4. LA RUTA DEL COMANDO SE COMPONE, NO SE ESCRIBE: `skills.external_dirs` del
     config.yaml del home y, si no esta ahi, el directorio de skills del home
     activo. Si no aparece en ninguno, falla diciendo DONDE busco.

Uso:
    tareas_diarias.py                 el aviso: calla si no hay nada
    tareas_diarias.py --diagnostico   solo lectura: que vio y que comando corrio
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

NOMBRE = Path(__file__).stem
CONFIG_LOCAL = Path(__file__).with_name(f"{NOMBRE}.local.json")
COMANDO = "productivity/nextcloud-tasks/scripts/tareas.py"

DEFECTOS = {
    "lista": "Mauro ToDo",
    "categoria": "diario",
    "titulo": "Pendientes de todos los días",
}


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
    for raiz in _external_dirs(home / "config.yaml"):
        buscados.append(Path(raiz).expanduser() / COMANDO)
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


def pendientes(cfg: dict, comando: Path) -> list[dict]:
    orden = [
        sys.executable,
        str(comando),
        "pendientes",
        "--lista",
        cfg["lista"],
        "--categoria",
        cfg["categoria"],
        "--json",
    ]
    salida = subprocess.run(
        orden, check=False, capture_output=True, text=True, timeout=120
    )
    if salida.returncode != 0:
        print(
            f"tareas_diarias: el comando fallo ({salida.returncode}): "
            f"{salida.stderr.strip()[:300]}",
            file=sys.stderr,
        )
        return []
    try:
        return json.loads(salida.stdout).get("tareas", [])
    except json.JSONDecodeError as e:
        print(f"tareas_diarias: salida ilegible ({e})", file=sys.stderr)
        return []


def formatear(tareas: list[dict]) -> str:
    lineas = [f"📌 {DEFECTOS['titulo']} ({len(tareas)})", ""]
    for t in tareas:
        lineas.append(f"• {t['titulo']}")
    lineas += [
        "",
        "Están en Mauro ToDo. Cuando estén, se cierran y dejan de aparecer acá.",
    ]
    return "\n".join(lineas)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--diagnostico", action="store_true")
    args = ap.parse_args(argv)

    cfg = cargar_config()
    comando = resolver_comando()
    tareas = pendientes(cfg, comando)

    if args.diagnostico:
        print(
            f"config: {CONFIG_LOCAL} ({'existe' if CONFIG_LOCAL.is_file() else 'defaults'})"
        )
        print(f"comando: {comando}")
        print(f"lista: {cfg['lista']} · categoria: {cfg['categoria']}")
        print(f"abiertas: {len(tareas)}")
        for t in tareas:
            print(f"  • {t['titulo']}  (uid {t['uid']})")
        return 0

    if not tareas:
        return 0  # nada que avisar: el job no manda nada

    print(formatear(tareas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
