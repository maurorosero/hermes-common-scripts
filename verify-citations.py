#!/usr/bin/env python3
"""Verifica mecánicamente las citas de una ficha de hallazgos. Sin modelo, sin tokens.

Qué comprueba, y por qué importa:

  1. Que la URL que el hallazgo declara como fuente **fue recuperada de verdad** — está en la
     caché de extracción de Hermes (``cache/web/extract-index.json``), que sólo se escribe cuando
     una herramienta bajó la página.
  2. Que la cita literal aparece dentro de ese contenido recuperado. Una paráfrasis presentada
     como cita entre comillas es un defecto, no un estilo.

Tres veredictos por hallazgo, y la diferencia importa:

  verificada      la fuente se recuperó y la cita está dentro.
  cita_no_coincide  la fuente se recuperó pero la cita NO aparece: o se parafraseó, o se atribuyó
                    al documento equivocado. Es el único veredicto que señala un error.
  sin_fuente      no hay contenido recuperado que comprobar. No dice que la cita sea falsa: dice
                  que no se puede demostrar. Suele significar que el worker leyó la página con
                  ``curl`` en vez de ``web_extract``, y entonces la evidencia no quedó en disco.

Uso:
    python3 verify-citations.py <ficha.md> [más fichas...]
    python3 verify-citations.py <ficha.md> --json
    python3 verify-citations.py <ficha.md> --refetch   # baja la URL y comprueba contra la página viva

Salida: 0 si todas las fuentes recuperables verifican · 2 si alguna cita no coincide ·
        1 si no se pudo verificar ninguna.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

CACHE_TTL_NOTE = "extract-index.json"
_SOLO_CITA = re.compile(r"[«\"“](.+?)[»\"”]", re.DOTALL)
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_CAMPO = re.compile(r"^\s*[-*]?\s*\*\*(.+?):\*\*\s*(.*)$")


def cache_dir() -> Path:
    import os

    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home / "cache" / "web"


def norm(texto: str) -> str:
    """Normaliza para comparar: sin acentos, sin guiones invisibles, sin comillas, espacios colapsados."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    for invisible in ("\u00ad", "\u200b", "\u200c", "\u200e", "\u2060", "\ufeff"):
        texto = texto.replace(invisible, "")
    texto = re.sub(r"[«»“”\"'`*_]", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.casefold().strip()


def cargar_indice(raiz: Path | None = None) -> dict:
    ruta = (raiz or cache_dir()) / CACHE_TTL_NOTE
    try:
        indice = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return indice if isinstance(indice, dict) else {}


def recuperado(url: str, indice: dict) -> tuple[str, str] | None:
    """Devuelve (contenido, procedencia) del contenido que el worker bajó para esa URL."""
    limpia = url.rstrip(".,;")
    for entrada in indice.values():
        if not isinstance(entrada, dict) or entrada.get("url", "").rstrip(
            "/"
        ) != limpia.rstrip("/"):
            continue
        try:
            contenido = Path(entrada["file"]).read_text(
                encoding="utf-8", errors="replace"
            )
        except (KeyError, OSError):
            continue
        return contenido, "caché de extracción"
    return None


def refetch(url: str) -> tuple[str, str] | None:
    """Baja la página viva. Comprueba la cita contra el estado de hoy, no contra el que leyó el worker."""
    import urllib.request

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (research-verifier)"}
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.read().decode("utf-8", errors="replace"), "página viva (hoy)"
    except Exception:  # noqa: BLE001 — una fuente caída no invalida la cita
        return None


def parsear(ruta: Path) -> list[dict]:
    hallazgos: list[dict] = []
    actual: dict | None = None
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea.startswith("## "):
            titulo = linea[3:].strip()
            if re.match(r"^\d+[.)]\s", titulo):
                actual = {"titulo": titulo, "citas": [], "urls": [], "tipo": "?"}
                hallazgos.append(actual)
            else:
                actual = None
            continue
        if actual is None:
            continue
        m = _CAMPO.match(linea)
        if m:
            campo, valor = m.group(1).strip().casefold(), m.group(2).strip()
            if campo.startswith("cita"):
                trozos = _SOLO_CITA.findall(valor) or [valor]
                actual["citas"].extend(t.strip() for t in trozos if t.strip())
            elif campo.startswith("fuente"):
                actual["urls"].extend(u.rstrip(".,;") for u in _URL.findall(valor))
            elif campo.startswith("tipo"):
                actual["tipo"] = valor or "?"
        elif actual["citas"] and linea.strip() and not linea.strip().startswith("-"):
            extra = _SOLO_CITA.findall(linea)
            actual["citas"].extend(t.strip() for t in extra if t.strip())
    return hallazgos


def verificar(ruta: Path, indice: dict, vivo: bool) -> dict:
    hallazgos = parsear(ruta)
    filas = []
    for halo in hallazgos:
        contenidos = []
        for url in dict.fromkeys(halo["urls"]):
            obtenido = recuperado(url, indice) or (refetch(url) if vivo else None)
            if obtenido:
                contenidos.append(obtenido)
        citas_ok = citas_mal = 0
        for cita in halo["citas"]:
            blanco = norm(cita)
            if len(blanco) < 4:
                continue
            if any(blanco in norm(texto) for texto, _ in contenidos):
                citas_ok += 1
            elif contenidos:
                citas_mal += 1
            # sin contenido recuperado no se cuenta como cita fallida: no hay nada que comprobar
        if not contenidos:
            estado = "sin_fuente"
        elif citas_mal:
            estado = "cita_no_coincide"
        elif citas_ok:
            estado = "verificada"
        else:
            estado = "sin_citas"
        filas.append(
            {
                "hallazgo": halo["titulo"],
                "estado": estado,
                "citas_ok": citas_ok,
                "citas_fallidas": citas_mal,
                "fuentes": halo["urls"],
                "fuentes_recuperadas": len(contenidos),
                "procedencia": [p for _, p in contenidos][:2],
            }
        )
    con_fuente = [f for f in filas if f["fuentes_recuperadas"]]
    verdes = [f for f in filas if f["estado"] == "verificada"]
    return {
        "ficha": ruta.name,
        "ruta": str(ruta),
        "hallazgos": len(filas),
        "verificadas": len(verdes),
        "sin_fuente": sum(1 for f in filas if f["estado"] == "sin_fuente"),
        "cita_no_coincide": sum(1 for f in filas if f["estado"] == "cita_no_coincide"),
        "citation_precision": round(len(verdes) / len(con_fuente), 3)
        if con_fuente
        else None,
        "detalle": filas,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Verifica las citas de una ficha de hallazgos."
    )
    ap.add_argument("fichas", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="salida legible por máquina")
    ap.add_argument(
        "--refetch",
        action="store_true",
        help="comprueba contra la página viva si no hay caché",
    )
    ap.add_argument(
        "--cache-dir", type=Path, help="raíz de la caché web (por defecto la del home)"
    )
    args = ap.parse_args()

    indice = cargar_indice(args.cache_dir)
    resultados = [verificar(f, indice, args.refetch) for f in args.fichas]

    if args.json:
        print(json.dumps(resultados, ensure_ascii=False, indent=2))
    else:
        for r in resultados:
            print(f"\n{r['ficha']} — {r['hallazgos']} hallazgos")
            for f in r["detalle"]:
                marca = {
                    "verificada": "✓",
                    "cita_no_coincide": "✗",
                    "sin_fuente": "·",
                }.get(f["estado"], "?")
                print(
                    f"  {marca} {f['hallazgo'][:64]:64} {f['estado']:16} "
                    f"citas {f['citas_ok']}/{f['citas_ok'] + f['citas_fallidas']} · "
                    f"fuentes recuperadas {f['fuentes_recuperadas']}/{len(f['fuentes'])}"
                )
                if f["citas_fallidas"]:
                    print("      ↳ cita no encontrada en la fuente declarada")
            precision = r["citation_precision"]
            print(
                f"  resumen: {r['verificadas']} verificadas · {r['cita_no_coincide']} con cita que no "
                f"coincide · {r['sin_fuente']} sin fuente recuperada"
            )
            if precision is not None:
                print(
                    f"  citation_precision: {precision} (verificadas / hallazgos con fuente recuperada)"
                )

    def peor(clave: str) -> int:
        return sum(r[clave] for r in resultados)

    if peor("cita_no_coincide"):
        return 2
    if peor("verificadas") == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
