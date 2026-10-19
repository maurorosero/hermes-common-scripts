#!/usr/bin/env python3
"""Puerta — puente determinista cola/ → board kanban `investigacion`.

Qué hace:
  1. Barre `cola/*.md` (ignora PLANTILLA.md y README.md).
  2. Valida cada request con el esquema (research-validator.py).
  3. Si es ilegible (sin frontmatter cerrado) → lo mueve a `_rechazados/` con el motivo.
  4. Si es interpretable → crea `en_curso/<I-…>/`, deja ahí el request corregido
     (con sus `correcciones` y `faltantes`) y crea la card raíz para el prime.
  5. Sólo borra el archivo de la cola si la card se creó de verdad.

Regla dura: si la card falla, el request SE QUEDA en la cola. Nunca se pierde un pedido
por un fallo del board.

Uso:
    python3 puerta.py              # procesa la cola
    python3 puerta.py --dry-run    # informa sin mover ni crear nada
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# El validador se llama research-validator.py (con guion, como todos los scripts del
# proceso), así que no es importable por nombre: se carga por ruta.
_spec = _ilu.spec_from_file_location(
    "research_validator", Path(__file__).resolve().parent / "research-validator.py"
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
cargar_esquema, recomponer, validar = _mod.cargar_esquema, _mod.recomponer, _mod.validar

BASE = Path(__file__).resolve().parent

# ── rutas: TODAS vienen del entorno. Ninguna ruta de datos se escribe acá ──────
# Si falta una variable, el proceso se detiene con un mensaje claro en vez de
# inventar una carpeta por su cuenta.
VARS = {
    "cola": "RESEARCH_REQ_PATH",  # donde caen los request.md  (entrada)
    "en_curso": "RESEARCH_PROC_PATH",  # donde trabaja el prime     (en curso)
    "entregas": "RESEARCH_READY_PATH",  # donde se publican          (salida)
}


def ruta_de(clave: str, obligatoria: bool = True) -> Path | None:
    var = VARS[clave]
    valor = os.environ.get(var, "").strip()
    if not valor:
        if obligatoria:
            raise SystemExit(
                f"falta {var} en el entorno (.env).\n"
                f"Sin esa variable no sé dónde está la carpeta de {clave} y no voy a inventarla."
            )
        return None
    return Path(os.path.expandvars(valor)).expanduser()


IGNORAR = {"PLANTILLA.md", "README.md"}
BOARD = "research"
PRIME = "researcher-prime"
SUBCARPETAS = ("sources", "validacion", "entregables", "assets")


def crear_card(tema: str, cuerpo: str, uuid: str) -> tuple[int, str]:
    """Crea la card raíz en el board. Devuelve (returncode, salida)."""
    cmd = [
        "hermes",
        "kanban",
        "create",
        f"Investigación: {tema}",
        "--assignee",
        PRIME,
        "--tenant",
        uuid,
        "--idempotency-key",
        uuid,
        "--body",
        cuerpo,
    ]
    env = dict(os.environ)
    env["HERMES_KANBAN_BOARD"] = BOARD
    env.pop(
        "HERMES_DELEGATED_CHILD_CONTEXT", None
    )  # un contexto hijo no puede mutar el board
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=120, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout al crear la card"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def slug(texto: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")
    return s[:60] or "sin-tema"


def procesar(
    archivo: Path,
    esquema: dict,
    dry: bool,
    cola: Path,
    en_curso: Path,
    rechazados: Path,
) -> str | None:
    texto = archivo.read_text(encoding="utf-8")
    rep = validar(texto, esquema)

    if rep["fatal"]:
        if not dry:
            rechazados.mkdir(parents=True, exist_ok=True)
            destino = rechazados / archivo.name
            shutil.move(str(archivo), destino)
            (rechazados / f"{archivo.name}.motivo.txt").write_text(
                rep["motivo"] + "\n", encoding="utf-8"
            )
        return f"RECHAZADO {archivo.name}: {rep['motivo']}"

    data = rep["data"]
    uuid = str(data["uuid"])
    tema = str(data.get("tema") or uuid)

    # Lo simple no toca el board: la investigación simple se resuelve inline.
    # El request se queda donde está, con una marca, para no avisar en cada barrido.
    if data.get("modo") == "delegate":
        marca = cola / f"{archivo.stem}.delegate"
        if marca.exists():
            return None
        if not dry:
            marca.write_text(
                f"{archivo.name}: clasificado simple/delegate por la puerta.\n"
                "No se crea card: se resuelve inline. Borrá esta marca para reintentar.\n",
                encoding="utf-8",
            )
        return f"DELEGATE {archivo.name}: simple — no se crea card, se resuelve inline"

    if rep["correcciones"]:
        data["correcciones"] = sorted(
            set(list(data.get("correcciones") or []) + rep["correcciones"])
        )
    if rep["faltantes"]:
        data["faltantes"] = rep["faltantes"]

    destino = en_curso / uuid
    if destino.exists() and (destino / "request.md").exists():
        return f"SALTADO {archivo.name}: {uuid} ya está en curso"

    if dry:
        return (
            f"OK(dry) {archivo.name} → {uuid} · tema='{tema}' · "
            f"{len(rep['correcciones'])} correcciones · {len(rep['faltantes'])} faltantes"
        )

    for sub in SUBCARPETAS:
        (destino / sub).mkdir(parents=True, exist_ok=True)
    final = recomponer(data, rep["cuerpo"])
    (destino / "request.md").write_text(final, encoding="utf-8")

    rc, salida = crear_card(tema, final, uuid)
    if rc != 0:
        # no se pierde el pedido: queda en la cola para el próximo barrido
        return f"ERROR {archivo.name}: la card no se creó (rc={rc}) — el request queda en la cola\n{salida.strip()[:400]}"

    archivo.unlink(missing_ok=True)
    return (
        f"PROCESADO {archivo.name} → {uuid} · {tema} · "
        f"{len(rep['correcciones'])} correcciones · {len(rep['faltantes'])} faltantes"
    )


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    cola = ruta_de("cola")
    en_curso = ruta_de("en_curso")
    # los rechazados viven al lado de la cola: una variable menos que mantener
    rechazados = cola / "_rechazados"

    if not cola.exists():
        print(f"la cola no existe todavía: {cola} (creala o revisá {VARS['cola']})")
        return 0

    esquema = cargar_esquema()
    pendientes = [p for p in sorted(cola.glob("*.md")) if p.name not in IGNORAR]

    if not pendientes:
        return 0  # silencio: nada en la cola

    fallos = 0
    for archivo in pendientes:
        linea = procesar(archivo, esquema, dry, cola, en_curso, rechazados)
        if linea is None:  # ya avisado en un barrido anterior
            continue
        print(linea)
        if linea.startswith("ERROR"):
            fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
