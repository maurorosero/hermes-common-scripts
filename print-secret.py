#!/usr/bin/env python3
"""Imprime KEY=VALUE de las credenciales pedidas, resueltas contra la bóveda del home.

Se usa como secret source `command` en el config.yaml de un perfil:

    secrets:
      command:
        enabled: true
        command: 'python3 "$HOME/.hermes/scripts/common/print-secret.py" TAVILY_API_KEY'
        helper_timeout_seconds: 8

Por qué existe: los perfiles son islas y no heredan el bloque `secrets:` del home, así que
sin esto un worker se queda sin las claves de proveedor y `web_extract` no tiene backend. La
alternativa sería copiar el `INFISICAL_CLIENT_SECRET` en cada perfil (tres copias del mismo
secreto) o cargar el plugin de Infisical, que es `bulk` y aplica la carpeta entera —decenas
de secretos que ese worker no necesita.

Regla del wrapper: **sólo se expone lo que el worker pidió.** Si un worker necesita otra
credencial, se agrega el nombre a SU comando, no a una lista compartida.

Resuelve con la config del HOME (donde vive `secrets.infisical` y el `.env` con las
credenciales de Universal Auth), no con la del perfil que lo invoca. No fija rutas: la
ubicación del helper canónico se compone desde `skills.external_dirs`.

Nunca imprime nada que no se le haya pedido. Los motivos van a stderr, que Hermes descarta.
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import sys
from pathlib import Path

SKILL_RELATIVA = (
    Path("harness") / "hermes-infisical-secrets" / "scripts" / "infisical_auth.py"
)


def home_del_arbol() -> Path:
    """El home del orquestador, que es el que tiene la bóveda configurada."""
    return Path(os.environ.get("SECRET_SOURCE_HOME") or (Path.home() / ".hermes"))


def _external_dirs(home: Path) -> list[Path]:
    """Lee `skills.external_dirs` sin PyYAML: son líneas `    - ruta` bajo la clave."""
    cfg = home / "config.yaml"
    if not cfg.exists():
        return []
    dirs: list[Path] = []
    dentro = False
    for linea in cfg.read_text(encoding="utf-8").splitlines():
        limpia = linea.strip()
        if limpia.endswith("external_dirs:"):
            dentro = True
            continue
        if dentro:
            if limpia.startswith("- "):
                dirs.append(Path(limpia[2:].strip().strip("'\"")))
            elif limpia and not limpia.startswith("#"):
                break
    return dirs


def buscar_helper(home: Path) -> Path | None:
    """Compone la ruta al helper canónico: no se escribe fija en ningún lado."""
    candidatos = [home / "skills", *(d for d in _external_dirs(home))]
    for base in candidatos:
        ruta = base.expanduser() / SKILL_RELATIVA
        if ruta.exists():
            return ruta
    return None


def cargar_helper(ruta: Path):
    os.environ["HERMES_HOME"] = str(home_del_arbol())
    spec = _ilu.spec_from_file_location("infisical_auth", ruta)
    if spec is None or spec.loader is None:
        return None
    modulo = _ilu.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def main(argv: list[str]) -> int:
    nombres = [a.strip() for a in argv if a.strip()]
    if not nombres:
        print(
            "print-secret: falta el nombre de la credencial. "
            "Uso: print-secret.py VAR1 [VAR2 ...]",
            file=sys.stderr,
        )
        return 2

    home = home_del_arbol()
    ruta = buscar_helper(home)
    if ruta is None:
        print(
            f"print-secret: no encontré el helper canónico bajo {home} "
            f"(busqué skills/ y skills.external_dirs). Nada que resolver.",
            file=sys.stderr,
        )
        return 0  # fail-open: arrancar sin la clave rompe menos que no arrancar

    modulo = cargar_helper(ruta)
    if modulo is None:
        print(f"print-secret: no pude cargar {ruta}", file=sys.stderr)
        return 0

    resueltos = 0
    for nombre in nombres:
        valor = modulo.get_secret(nombre)
        if valor:
            print(f"{nombre}={valor}")
            resueltos += 1
        else:
            motivo = ""
            if hasattr(modulo, "last_error"):
                motivo = str(modulo.last_error() or "")
            print(f"print-secret: {nombre} no se resolvió. {motivo}", file=sys.stderr)

    print(
        f"print-secret: {resueltos}/{len(nombres)} credenciales resueltas.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
