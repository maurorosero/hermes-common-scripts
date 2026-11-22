#!/usr/bin/env python3
"""Sintetiza texto en voz alta, normalizando antes lo que el espanol no pronuncia bien.

El diccionario vive al lado de este script, en ``tts-normalizacion.toml``, y se edita sin
tocar codigo: agregar una palabra es agregar una regla.

Uso:
    tts-normalizar.py --texto archivo.txt --salida audio.mp3
    tts-normalizar.py --texto archivo.txt --salida audio.mp3 --voz es-MX-DaliaNeural
    tts-normalizar.py --texto archivo.txt --mostrar

El binario de edge-tts se resuelve en este orden: la variable ``EDGE_TTS_BIN``, el ``edge-tts``
del PATH, y ``python -m edge_tts`` con el interprete que corre este script.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

DICCIONARIO = Path(__file__).with_name("tts-normalizacion.toml")
VOZ_POR_DEFECTO = "es-ES-ElviraNeural"
ESPERA_MAXIMA = 120


def cargar_diccionario(ruta: Path) -> tuple[list[tuple[re.Pattern[str], str]], str]:
    """Devuelve las reglas compiladas y la voz configurada."""
    with ruta.open("rb") as archivo:
        datos = tomllib.load(archivo)
    reglas = []
    for numero, regla in enumerate(datos.get("reglas", []), start=1):
        try:
            reglas.append((re.compile(regla["de"], re.IGNORECASE), regla["a"]))
        except (KeyError, re.error) as error:
            raise SystemExit(f"{ruta}: regla {numero} invalida: {error}") from error
    return reglas, datos.get("voz", VOZ_POR_DEFECTO)


def normalizar(texto: str, reglas: list[tuple[re.Pattern[str], str]]) -> str:
    """Aplica cada regla en orden."""
    for patron, reemplazo in reglas:
        texto = patron.sub(reemplazo, texto)
    return texto


def resolver_binario() -> list[str]:
    """El comando que sintetiza, buscando en el orden documentado."""
    propio = os.environ.get("EDGE_TTS_BIN")
    if propio:
        return [propio]
    en_path = shutil.which("edge-tts")
    if en_path:
        return [en_path]
    local = Path.home() / ".local" / "bin" / "edge-tts"
    if local.is_file():
        return [str(local)]
    return [sys.executable, "-m", "edge_tts"]


def sintetizar(comando: list[str], texto: str, salida: Path, voz: str) -> None:
    """Genera el audio o falla con un mensaje legible."""
    orden = [*comando, "--voice", voz, "--text", texto, "--write-media", str(salida)]
    try:
        subprocess.run(orden, check=True, timeout=ESPERA_MAXIMA, capture_output=True)
    except FileNotFoundError as error:
        raise SystemExit(f"no encuentro el sintetizador: {comando[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise SystemExit(
            f"el sintetizador no respondio en {ESPERA_MAXIMA} s"
        ) from error
    except subprocess.CalledProcessError as error:
        detalle = (error.stderr or b"").decode("utf-8", "replace").strip()
        raise SystemExit(
            f"el sintetizador fallo: {detalle or error.returncode}"
        ) from error


def main() -> int:
    analizador = argparse.ArgumentParser(
        description="Normaliza el texto con el diccionario y lo sintetiza en voz alta."
    )
    analizador.add_argument(
        "--texto", required=True, help="archivo con el texto a leer"
    )
    analizador.add_argument("--salida", help="archivo de audio a escribir")
    analizador.add_argument("--voz", help="voz de edge-tts; gana sobre el diccionario")
    analizador.add_argument(
        "--diccionario", default=str(DICCIONARIO), help="ruta del diccionario TOML"
    )
    analizador.add_argument(
        "--mostrar",
        action="store_true",
        help="imprime el texto normalizado y no sintetiza",
    )
    opciones = analizador.parse_args()

    ruta_diccionario = Path(opciones.diccionario)
    if not ruta_diccionario.is_file():
        raise SystemExit(f"no encuentro el diccionario: {ruta_diccionario}")

    original = Path(opciones.texto).read_text(encoding="utf-8")
    reglas, voz_diccionario = cargar_diccionario(ruta_diccionario)
    texto = normalizar(original, reglas)

    if opciones.mostrar:
        print(texto)
        return 0

    if not opciones.salida:
        raise SystemExit("falta --salida, o --mostrar para solo ver el texto")

    salida = Path(opciones.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    sintetizar(resolver_binario(), texto, salida, opciones.voz or voz_diccionario)
    print(salida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
