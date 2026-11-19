#!/usr/bin/env python3
"""Imprime KEY=VALUE de las credenciales pedidas, resueltas contra la bóveda del home.

Se usa como secret source `command` en el config.yaml de un perfil:

    secrets:
      command:
        enabled: true
        command: 'python3 "$HOME/.hermes/scripts/common/print-secret.py" TAVILY_API_KEY'

Por qué existe: los perfiles son islas y no heredan el bloque `secrets:` del home, así que
sin esto un worker se queda sin las claves de proveedor. La ubicación de la bóveda y el
login viven en `infisical_client.py`, al lado de este archivo: acá sólo se imprime.

Regla: **sólo se expone lo que el worker pidió.** Si necesita otra credencial, se agrega
el nombre a SU comando, no a una lista compartida.

Nunca imprime nada que no se le haya pedido. Los motivos van a stderr, que Hermes descarta.
"""

from __future__ import annotations

import sys

from infisical_client import get_secret, last_error


def main(argv: list[str]) -> int:
    nombres = [a.strip() for a in argv if a.strip()]
    if not nombres:
        print(
            "print-secret: falta el nombre de la credencial. "
            "Uso: print-secret.py VAR1 [VAR2 ...]",
            file=sys.stderr,
        )
        return 2

    resueltos = 0
    for nombre in nombres:
        valor = get_secret(nombre)
        if valor:
            print(f"{nombre}={valor}")
            resueltos += 1
        else:
            print(
                f"print-secret: {nombre} no se resolvió. {last_error()}",
                file=sys.stderr,
            )

    print(
        f"print-secret: {resueltos}/{len(nombres)} credenciales resueltas.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
