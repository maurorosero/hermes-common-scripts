#!/usr/bin/env python3
"""Recolector del kanban: eventos y logs viejos de cada tablero.

`hermes kanban gc` existe en el core —borra `task_events` de tareas terminales y los logs de
workers, con 30 dias de retencion por defecto— y nadie lo invocaba: al 6-nov los tres tableros
sumaban 8.849 eventos (presenter 3.676, research 4.340, andrea-tasks 833) creciendo sin techo.

Corre por tablero porque el verbo es por tablero (`--board` no tiene un "todos"). Se calla
cuando no hay nada que recolectar: un cron diario que habla para decir «nada» es ruido, y este
home ya tiene 288 mensajes por dia del dispatcher del presenter.

La retencion es la del core (30 dias). Si algun dia hace falta otra, se pasa por argumento en
vez de cablearla aca.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

TIMEOUT = 180


def tableros() -> list[str]:
    """Los slugs de los tableros, del core. Sin tableros no hay nada que recolectar."""
    salida = subprocess.run(
        ["hermes", "kanban", "boards", "list", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    texto = (salida.stdout or "").strip()
    if not texto:
        return []
    try:
        datos = json.loads(texto)
    except ValueError:
        # el listado humano trae una linea por tablero con el slug en la primera columna
        slugs = []
        for linea in texto.splitlines():
            partes = linea.split()
            if len(partes) >= 2 and not linea.lstrip().startswith(
                ("SLUG", "Current", "Switch")
            ):
                slugs.append(partes[-1] if partes[0] == "●" else partes[0])
        return [s for s in dict.fromkeys(slugs) if s]
    if isinstance(datos, dict):
        datos = datos.get("boards") or datos.get("data") or []
    return [str(b.get("slug") or b.get("id")) for b in datos if isinstance(b, dict)]


def main() -> int:
    recolectado = []
    for slug in tableros():
        salida = subprocess.run(
            ["hermes", "kanban", "--board", slug, "gc"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
        texto = " ".join((salida.stdout or "").split())
        # «GC complete: 1 workspace(s), 0 event row(s), 0 log file(s) removed»
        borrado = re.search(r"(\d+)\s+event row", texto)
        logs = re.search(r"(\d+)\s+log file", texto)
        filas = int(borrado.group(1)) if borrado else 0
        archivos = int(logs.group(1)) if logs else 0
        if filas or archivos:
            recolectado.append(f"  · {slug}: {filas} eventos, {archivos} logs")
    if not recolectado:
        return 0
    print("kanban gc · mas de 30 dias, tareas terminadas:")
    print("\n".join(recolectado))
    return 0


if __name__ == "__main__":
    sys.exit(main())
