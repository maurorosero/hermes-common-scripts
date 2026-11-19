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
import html as _html
import json
import re
import sys
import unicodedata
from pathlib import Path

CACHE_TTL_NOTE = "extract-index.json"
_SOLO_CITA = re.compile(r"[«\"“](.+?)[»\"”]", re.DOTALL)
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_CAMPO = re.compile(r"^\s*[-*]?\s*\*\*(.+?):\*\*\s*(.*)$")


def raices_de_cache(explicita: Path | None = None) -> list[Path]:
    """Todas las cachés de extracción que pueden tener la evidencia.

    Cada perfil es una isla: un worker escribe en la caché de SU home
    (``profiles/<perfil>/cache/web``), no en la del home activo. Mirar sólo la del
    home hace que toda ficha salga como "sin fuente" aunque la página se haya bajado.
    """
    import os

    if explicita:
        return [explicita]
    base = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    raices = [base / "cache" / "web"]
    perfiles = base / "profiles"
    if perfiles.is_dir():
        raices += sorted(
            d / "cache" / "web"
            for d in perfiles.iterdir()
            if (d / "cache" / "web").is_dir()
        )
    return raices


def norm(texto: str) -> str:
    """Normaliza para comparar: sin acentos, sin guiones invisibles, sin comillas, espacios colapsados."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    for invisible in ("\u00ad", "\u200b", "\u200c", "\u200e", "\u2060", "\ufeff"):
        texto = texto.replace(invisible, "")
    texto = re.sub(r"[«»“”\"'`*_]", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    return texto.casefold().strip()


def evidencia_local(ruta_ficha: Path) -> list[dict]:
    """Evidencia que el worker dejó en disco junto a la ficha (`sources/evidence/`, `assets/`).

    El script `render-page.js` escribe, al lado de cada `.txt` y `.html`, un `.meta.json` con
    la URL final y la fecha. Sin esa ficha de procedencia la evidencia no se puede atribuir a
    una URL concreta y no se usa: adivinar por dominio haría pasar por verificado un dato de
    otra página del mismo sitio.
    """
    carpeta = ruta_ficha.parent.parent  # <investigación>/sources/<ficha>.md
    entradas: list[dict] = []
    for base in (carpeta / "sources" / "evidence", carpeta / "assets"):
        if not base.is_dir():
            continue
        for meta in base.rglob("*.meta.json"):
            try:
                datos = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # el texto completo del DOM primero: incluye FAQ plegadas y pestañas, que el
            # texto visible deja fuera aunque sean fuente legítima del dato
            copias = [
                destino
                for sufijo in (".full.txt", ".html")
                if (destino := meta.with_suffix("").with_suffix(sufijo)).exists()
            ]
            if not copias:
                continue
            for clave in ("url", "final_url"):
                if datos.get(clave):
                    entradas.extend(
                        {
                            "url": datos[clave],
                            "file": str(copia),
                            "origen": "evidencia local",
                            "fetched_at": datos.get("fetched_at", ""),
                        }
                        for copia in copias
                    )
    return entradas


def cargar_entradas(raices: list[Path]) -> list[dict]:
    """Todas las entradas de todas las cachés: url, archivo, cuándo y de dónde salió."""
    entradas: list[dict] = []
    for raiz in raices:
        try:
            indice = json.loads((raiz / CACHE_TTL_NOTE).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(indice, dict):
            continue
        for valor in indice.values():
            if isinstance(valor, dict) and valor.get("url") and valor.get("file"):
                entradas.append({**valor, "origen": raiz.parent.parent.name})
    return entradas


def _sin_scripts(html: str) -> str:
    """Quita <script>, <style> y comentarios: un importe dentro de un JSON no es la página."""
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    return re.sub(r"<!--[\s\S]*?-->", " ", html)


def _variantes(cita: str) -> list[str]:
    """La cita, y sus formas limpias. El worker a veces anota entre paréntesis dentro de la cita
    (`"Light User at 195 Rs" (India)`): la anotación es suya, no está en la fuente."""
    fuera = [cita]
    sin_paren = re.sub(r"\s*\([^)]{1,40}\)\s*$", "", cita).strip()
    if sin_paren and sin_paren != cita:
        fuera.append(sin_paren)
    entre = _SOLO_CITA.findall(cita)
    fuera.extend(t.strip() for t in entre if t.strip())
    return list(dict.fromkeys(fuera))


_MEMO: dict[str, list[tuple[str, str, str]]] = {}
_VIVO: dict[str, tuple[str, str] | None] = {}


def recuperado(url: str, entradas: list[dict]) -> list[tuple[str, str, str]]:
    """Todas las copias que tenemos de esa URL: (contenido, procedencia, archivo).

    Puede haber varias y todas valen: el render de hoy, un extractor que sirvió la página
    desde otro país, la caché de una corrida anterior. Una cita se comprueba contra todas;
    quedarse con la primera hacía fallar citas que sí estaban en otra copia del documento.
    """
    limpia = url.rstrip(".,;").rstrip("/")
    if limpia in _MEMO:
        return _MEMO[limpia]
    copias: list[tuple[str, str, str]] = []
    for entrada in entradas:
        if entrada["url"].rstrip(".,;").rstrip("/") != limpia:
            continue
        try:
            contenido = _html.unescape(
                Path(entrada["file"]).read_text(encoding="utf-8", errors="replace")
            )
        except (KeyError, OSError):
            continue
        if entrada["file"].endswith((".html", ".htm")):
            contenido = _sin_scripts(contenido)
        origen = entrada.get("origen") or "home"
        etiqueta = (
            "evidencia local" if origen == "evidencia local" else f"caché ({origen})"
        )
        copias.append((contenido, etiqueta, entrada["file"]))
    _MEMO[limpia] = copias
    return copias


def refetch(url: str) -> tuple[str, str] | None:
    """Baja la página viva. Comprueba la cita contra el estado de hoy, no contra el que leyó el worker."""
    import urllib.request

    if url in _VIVO:
        return _VIVO[url]
    _VIVO[url] = None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (research-verifier)"}
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            _VIVO[url] = (
                r.read().decode("utf-8", errors="replace"),
                "página viva (hoy)",
            )
    except Exception:  # noqa: BLE001 — una fuente caída no invalida la cita
        _VIVO[url] = None
    return _VIVO[url]


def _trozos_de_cita(valor: str) -> list[str]:
    """Parte una línea de cita en sus trozos.

    La plantilla de ficha separa las citas con ` · ` y las envuelve en comillas. Partir por el
    separador es más fiable que buscar comillas: una cita puede llevar comillas escapadas
    dentro (`\\"usuario light\\"`) y el regex cortaba ahí, dejando media cita y un falso
    "cita_no_coincide" que acusaba al worker de un error que no cometió.
    """
    trozos = [t.strip() for t in valor.split(" · ")]
    limpios = []
    for t in trozos:
        t = t.replace('\\"', '"').strip()
        if len(t) >= 2 and t[0] in "\"«“'" and t[-1] in "\"»”'":
            t = t[1:-1].strip()
        if t:
            limpios.append(t)
    return limpios or ([valor.strip()] if valor.strip() else [])


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
                actual["citas"].extend(_trozos_de_cita(valor))
            elif campo.startswith("fuente"):
                actual["urls"].extend(u.rstrip(".,;") for u in _URL.findall(valor))
            elif campo.startswith("tipo"):
                actual["tipo"] = valor or "?"
        elif actual["citas"] and linea.strip() and not linea.strip().startswith("-"):
            actual["citas"].extend(_trozos_de_cita(linea))
    return hallazgos


def _pasaje(texto: str, cita: str, ancho: int = 260) -> str | None:
    """El tramo del documento alrededor de la cita, tal como está escrito.

    Se busca con un patrón que tolera diferencias de espacios en vez de un substring exacto,
    porque así los offsets siguen siendo válidos sobre el texto original y el pasaje se puede
    citar literal. Es lo que el verificador lee: 500 caracteres en vez de 13 KB de documento.
    """
    palabras = [p for p in cita.split() if p]
    if len(palabras) < 2:
        return None
    patron = re.compile(r"\s+".join(re.escape(p) for p in palabras))
    m = patron.search(texto)
    if not m:
        return None
    ini, fin = max(0, m.start() - ancho), min(len(texto), m.end() + ancho)
    return re.sub(r"\s+", " ", texto[ini:fin]).strip()


def _avisos(filas: list[dict]) -> list[str]:
    """Señales deterministas que el verificador con modelo no debería tener que descubrir.

    No son errores: son cosas que hay que mirar. Una sola fuente para diez afirmaciones puede
    ser correcta (un único documento oficial), pero cambia cuánto sostiene la ficha.
    """
    avisos: list[str] = []
    urls = {u for f in filas for u in f["fuentes"]}
    if len(filas) > 3 and len(urls) == 1:
        avisos.append(
            f"fuente_unica: las {len(filas)} afirmaciones se apoyan en una sola URL — "
            "no hay contraste entre fuentes independientes"
        )
    vistas: dict[str, list[str]] = {}
    for f in filas:
        for cita in f.get("citas", []):
            clave = norm(cita)[:120]
            if len(clave) < 20:
                continue
            vistas.setdefault(clave, []).append(f["claim_id"])
    for claims in vistas.values():
        if len(claims) > 1:
            avisos.append(
                f"cita_repetida: la misma cita textual sostiene {len(claims)} afirmaciones "
                f"({', '.join(claims)})"
            )
    sin_copia = [f["claim_id"] for f in filas if not f["fuentes_recuperadas"]]
    if sin_copia:
        avisos.append(
            f"sin_evidencia: {len(sin_copia)} afirmaciones sin copia recuperada ({', '.join(sin_copia)})"
        )
    return avisos


def verificar(ruta: Path, entradas: list[dict], vivo: bool) -> dict:
    hallazgos = parsear(ruta)
    filas = []
    for halo in hallazgos:
        contenidos: list[tuple[str, str, str]] = []
        for url in dict.fromkeys(halo["urls"]):
            copias = recuperado(url, entradas)
            if not copias and vivo and (viva := refetch(url)):
                copias = [(viva[0], viva[1], url)]
            contenidos.extend(copias)
        citas_ok = citas_mal = 0
        pasajes: list[dict] = []
        for cita in halo["citas"]:
            variantes = [norm(v) for v in _variantes(cita)]
            variantes = [v for v in variantes if len(v) >= 4]
            if not variantes:
                continue
            hallada = [
                (texto, ruta)
                for texto, _, ruta in contenidos
                if any(v in norm(texto) for v in variantes)
            ]
            if hallada:
                citas_ok += 1
                if len(pasajes) < 3:
                    for texto, archivo in hallada[:1]:
                        tramo = _pasaje(texto, cita)
                        if tramo:
                            pasajes.append(
                                {"cita": cita, "archivo": archivo, "pasaje": tramo}
                            )
                            break
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
        numero = re.match(r"^(\d+)", halo["titulo"])
        filas.append(
            {
                "claim_id": f"H-{int(numero.group(1)):02d}"
                if numero
                else f"H-{len(filas) + 1:02d}",
                "hallazgo": halo["titulo"],
                "estado": estado,
                "citas_ok": citas_ok,
                "citas_fallidas": citas_mal,
                "citas": halo["citas"],
                "fuentes": halo["urls"],
                "fuentes_recuperadas": len(contenidos),
                "procedencia": sorted({p for _, p, _ in contenidos}),
                "evidencia": sorted({str(ruta) for _, _, ruta in contenidos}),
                "contexto": pasajes,
                # El estado de la afirmación tiene dos capas: lo que se puede decidir sin
                # criterio (¿la cita está en la fuente?) y lo que exige juicio (¿la cita
                # sostiene la afirmación?). La primera se decide acá; la segunda la marca el
                # verificador con modelo, y sólo para los claims que llegan con evidencia.
                "claim_status": (
                    "unverified"
                    if estado in ("sin_fuente", "cita_no_coincide", "sin_citas")
                    else None
                ),
                "requiere_juicio": estado == "verificada",
            }
        )
    con_fuente = [f for f in filas if f["fuentes_recuperadas"]]
    verdes = [f for f in filas if f["estado"] == "verificada"]
    avisos = _avisos(filas)
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
        "fuentes_distintas": len({u for f in filas for u in f["fuentes"]}),
        "avisos": avisos,
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

    del_cache = cargar_entradas(raices_de_cache(args.cache_dir))
    resultados = [
        verificar(f, evidencia_local(f) + del_cache, args.refetch) for f in args.fichas
    ]

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
                    f"copias {f['fuentes_recuperadas']} de {len(f['fuentes'])} url(s)"
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
