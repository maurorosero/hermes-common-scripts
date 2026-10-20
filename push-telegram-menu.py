#!/usr/bin/env python3
"""Empuja el menú de comandos a Telegram sin reiniciar el gateway.

El adaptador hace esto una vez, al conectarse. Cuando se agrega un comando con el gateway ya
corriendo, el comando funciona al tipearlo pero no aparece en el menú hasta el próximo
arranque — y reiniciar desde dentro del gateway está bloqueado (el SIGTERM se lleva el
comando). Asi que se arma el mismo payload y se empuja por la Bot API.

El token no se imprime ni se escribe: lo resuelve `print-secret.py` y vive en memoria.
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("HOME") or Path.home())
SCRIPTS = HOME / ".hermes" / "scripts" / "common"


def token() -> str:
    r = subprocess.run(
        ["python3", str(SCRIPTS / "print-secret.py"), "TELEGRAM_BOT_TOKEN"],
        capture_output=True,
        text=True,
        check=False,
    )
    for linea in (r.stdout or "").splitlines():
        if linea.startswith("TELEGRAM_BOT_TOKEN="):
            return linea.split("=", 1)[1].strip()
    raise SystemExit(f"no pude resolver el token: {(r.stderr or '').strip()[:200]}")


def comandos() -> list[dict]:
    sys.path.insert(0, str(HOME / ".hermes" / "hermes-agent"))
    os.environ.setdefault("HERMES_HOME", str(HOME / ".hermes"))
    from model_tools import discover_plugins

    discover_plugins(force=True)
    from hermes_cli.commands_platforms import (
        telegram_menu_commands,
        telegram_menu_max_commands,
    )

    pares, _ = telegram_menu_commands(max_commands=telegram_menu_max_commands())
    return [{"command": n, "description": d} for n, d in pares]


def api(metodo: str, cuerpo: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token()}/{metodo}"
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(
        url,
        data=datos,
        # sin este header Telegram interpreta "sin comandos" y borra el menu
        headers={"Content-Type": "application/json"} if datos else {},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    lista = comandos()
    nombres = [c["command"] for c in lista]
    print(
        f"payload: {len(lista)} comandos · research: {'SI' if 'research' in nombres else 'NO'}"
    )

    for scope in (
        {"type": "default"},
        {"type": "all_private_chats"},
        {"type": "all_group_chats"},
    ):
        r = api("setMyCommands", {"commands": lista, "scope": scope})
        print(f"  setMyCommands {scope['type']}: {'ok' if r.get('ok') else r}")

    real = api("getMyCommands").get("result", [])
    reales = [c["command"] for c in real]
    print(
        f"\nleido de vuelta: {len(reales)} comandos · research: {'SI' if 'research' in reales else 'NO'}"
    )
    print("primeros 6:", reales[:6])
    return 0 if "research" in reales else 1


if __name__ == "__main__":
    sys.exit(main())
