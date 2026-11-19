#!/usr/bin/env python3
"""backlog_datos_minimos — avisa que tarjetas de `backlog` YA tienen los datos minimos.

Para que sirve: `backlog` no es un estado del correo, es un estado de la LIBRETA
(contactos a los que les falta el dato minimo de contacto). Cuando el correo de uno
de ellos llega, la tarjeta se puede completar — y esa tarjeta, ya completa, deja de
pertenecer a `backlog`. Este script enumera esas tarjetas para que el dueño las
reclasifique.

Contrato (no configurable, es el diseno):
  1. SI NO HAY NADA QUE AVISAR NO IMPRIME NADA Y SALE 0. Corre a diario: un mensaje
     vacio todos los dias es el ruido que este diseno existe para eliminar.
  2. SOLO LEE. No escribe en el servidor ni en disco. No marca nada.
  3. NO LLEVA NADA INTERNO. La libreta y el criterio son datos de esta instalacion y
     viven en `<este-nombre>.local.json` (que git ignora) o en el entorno: la
     configuracion pertenece a su dueño y le sobrevive al codigo. Nunca van aca.
  4. LA RUTA DEL TRANSPORTE SE COMPONE, NO SE ESCRIBE: `skills.external_dirs` del
     config.yaml del home y, si no esta ahi, el directorio de skills del home activo.
     Si no aparece en ninguno, falla diciendo DONDE busco.

Uso:
    backlog_datos_minimos.py                 el aviso: calla si no hay nada
    backlog_datos_minimos.py --diagnostico   solo lectura: que vio en la libreta
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NOMBRE = Path(__file__).stem
CONFIG_LOCAL = Path(__file__).with_name(f"{NOMBRE}.local.json")

# El transporte CardDAV no se duplica: su dueño es el skill nextcloud-contacts.
TRANSPORTE = "productivity/nextcloud-contacts/scripts/dav.py"

DEFECTOS = {
    "libreta": None,
    "categoria": "backlog",
    "campos_minimos": ["EMAIL", "TEL"],
    "modo": "todos",
    "etiqueta": "Correo",
}


class ErrorConfig(Exception):
    """Falta configuracion o un recurso. No es un fallo de red."""


# ── Configuracion ───────────────────────────────────────────────────────────


def cargar_config() -> dict:
    if not CONFIG_LOCAL.is_file():
        raise ErrorConfig(
            f"falta {CONFIG_LOCAL.name} junto al script. Es donde viven los datos de "
            "esta instalacion (la libreta y el criterio): se quedan fuera del repo "
            'para sobrevivirle al codigo. Minimo: {"libreta": "<uri>"}'
        )
    try:
        datos = json.loads(CONFIG_LOCAL.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ErrorConfig(
            f"{CONFIG_LOCAL.name} ilegible ({type(err).__name__})"
        ) from err
    cfg = dict(DEFECTOS)
    cfg.update({k: v for k, v in datos.items() if not k.startswith("_")})
    if not cfg.get("libreta"):
        raise ErrorConfig(f"{CONFIG_LOCAL.name} no declara la libreta")
    return cfg


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _expandir(entrada: str) -> Path:
    ruta = Path(os.path.expanduser(os.path.expandvars(entrada)))
    return ruta if ruta.is_absolute() else hermes_home() / ruta


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
        if sangria == 2:
            en_lista = texto == "external_dirs:"
            continue
        if en_lista and texto.startswith("- "):
            salida.append(texto[2:].strip().strip("\"'"))
    return salida


def bases_de_skills() -> list[Path]:
    bases = [_expandir(e) for e in _external_dirs(hermes_home() / "config.yaml")]
    bases.append(hermes_home() / "skills")
    vistas: set[str] = set()
    salida: list[Path] = []
    for base in bases:
        if base.is_dir() and str(base) not in vistas:
            vistas.add(str(base))
            salida.append(base)
    return salida


def resolver(relativo: str) -> Path:
    """Compone la ruta de un recurso del arbol de skills y falla diciendo donde busco."""
    candidatos = [base / relativo for base in bases_de_skills()]
    for ruta in candidatos:
        if ruta.is_file():
            return ruta
    buscado = (
        "\n".join(f"    - {c}" for c in candidatos)
        or "    (ninguna base de skills existe)"
    )
    raise ErrorConfig(
        f"no encontre {relativo}.\n  busque en:\n{buscado}\n"
        f"  declara su directorio en skills.external_dirs de {hermes_home() / 'config.yaml'}"
    )


def _secreto(nombre: str) -> str | None:
    """Entorno primero; si no esta, se le pide al cliente comun. Nunca se imprime."""
    valor = (os.environ.get(nombre) or "").strip()
    if valor:
        return valor
    try:
        from infisical_client import get_secret  # al lado de este archivo
    except ImportError:
        return None
    return (get_secret(nombre) or "").strip() or None


# ── Lectura de la libreta (CardDAV) ──────────────────────────────────────────


def cargar_dav():
    ruta = resolver(TRANSPORTE)
    sys.path.insert(0, str(ruta.parent))
    import dav  # type: ignore

    # dav.py lee los NOMBRES de config.json y los valores del entorno: se resuelven
    # aca (entorno -> Infisical) sin imprimirlos nunca.
    for clave in ("host", "usuario", "password", "password_respaldo"):
        nombre = (dav.cargar_config().get("entorno") or {}).get(clave)
        if not nombre or os.environ.get(nombre):
            continue
        valor = _secreto(nombre)
        if valor:
            os.environ[nombre] = valor
    return dav


def tarjetas(dav, libreta: str) -> list[dict]:
    """Todas las tarjetas de la libreta, pedidas por el servidor (REPORT)."""
    cli = dav.Cliente()
    campos = "".join(
        f'<card:prop name="{c}"/>' for c in ("CATEGORIES", "FN", "EMAIL", "TEL")
    )
    cuerpo = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
        f"<d:prop><d:getetag/><card:address-data>{campos}</card:address-data></d:prop>"
        "</card:addressbook-query>"
    )
    estado, _h, b = cli.peticion(
        "REPORT",
        cli.url_libreta(libreta),
        cuerpo.encode(),
        {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
    )
    if estado != 207:
        raise ErrorConfig(f"el REPORT de {libreta} devolvio {estado}")

    raiz = ET.fromstring(b)
    ns = dav.NS
    salida: list[dict] = []
    for resp in raiz.findall("d:response", ns):
        nodo = resp.find("d:propstat/d:prop/card:address-data", ns)
        vcf = (nodo.text or "") if nodo is not None else ""
        if not vcf.strip():
            continue
        cats: list[str] = []
        m = re.search(r"^CATEGORIES(?:;[^:]*)?:(.*)$", vcf, re.MULTILINE)
        if m:
            cats = [
                p.strip().replace("\\,", ",")
                for p in re.split(r"(?<!\\),", m.group(1).strip())
                if p.strip()
            ]
        salida.append(
            {
                "fn": _campo(vcf, "FN"),
                "emails": _campo(vcf, "EMAIL", varios=True),
                "tels": _campo(vcf, "TEL", varios=True),
                "cats": cats,
            }
        )
    return salida


def _campo(vcf: str, prop: str, varios: bool = False):
    hallados = [
        v.strip()
        for v in re.findall(rf"^{prop}(?:;[^:]*)?:(.*)$", vcf, re.MULTILINE)
        if v.strip()
    ]
    return hallados if varios else (hallados[0] if hallados else "")


def tienen_minimos(t: dict, campos: list[str], modo: str) -> bool:
    presentes = []
    for campo in campos:
        clave = {"EMAIL": "emails", "TEL": "tels"}.get(campo.upper())
        if clave is None:
            continue
        presentes.append(bool(t.get(clave)))
    if not presentes:
        return False
    return all(presentes) if modo == "todos" else any(presentes)


# ── Reporte ─────────────────────────────────────────────────────────────────


def formatear(candidatas: list[dict], cfg: dict, total_backlog: int) -> str:
    etiqueta = cfg["etiqueta"]
    categoria = cfg["categoria"]
    criterio = " y ".join(cfg["campos_minimos"])
    lineas = [
        (
            f"{etiqueta}: {len(candidatas)} tarjeta(s) en `{categoria}` con los datos "
            f"minimos ({criterio}) — de {total_backlog} en la categoria."
        ),
        "",
        "Ya no les falta el dato minimo: se pueden reclasificar.",
        "",
    ]
    for t in sorted(candidatas, key=lambda x: x["fn"].lower()):
        partes = [t["fn"] or "(sin nombre)"]
        if t["emails"]:
            partes.append(t["emails"][0])
        if t["tels"]:
            partes.append(t["tels"][0])
        otras = [c for c in t["cats"] if c != cfg["categoria"]]
        if otras:
            partes.append("otras categorias: " + ", ".join(otras))
        lineas.append("  • " + " · ".join(partes))
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--diagnostico", action="store_true", help="solo lectura: que vio")
    args = p.parse_args(argv)

    try:
        cfg = cargar_config()
        dav = cargar_dav()
        todas = tarjetas(dav, cfg["libreta"])
    except ErrorConfig as err:
        print(f"ERROR: {err}")
        return 1
    except Exception as err:  # noqa: BLE001 — un fallo no puede disfrazarse de "sin datos"
        print(f"ERROR: {type(err).__name__}: {err}")
        return 1

    en_categoria = [t for t in todas if cfg["categoria"] in t["cats"]]
    candidatas = [
        t for t in en_categoria if tienen_minimos(t, cfg["campos_minimos"], cfg["modo"])
    ]

    if args.diagnostico:
        print(f"libreta: {cfg['libreta']} · tarjetas: {len(todas)}")
        print(f"categoria `{cfg['categoria']}`: {len(en_categoria)}")
        print(
            f"criterio: {cfg['modo']} de {' y '.join(cfg['campos_minimos'])} "
            f"→ cumplen {len(candidatas)}"
        )
        for t in sorted(candidatas, key=lambda x: x["fn"].lower()):
            print(
                f"  • {t['fn']} · {t['emails'][0] if t['emails'] else '—'} · "
                f"{t['tels'][0] if t['tels'] else '—'} · {', '.join(t['cats'])}"
            )
        return 0

    if not candidatas:
        return 0  # nada que avisar: el job no manda nada

    print(formatear(candidatas, cfg, len(en_categoria)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
