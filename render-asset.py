#!/usr/bin/env python3
"""Renderiza los assets de una investigacion a partir de su manifiesto.

El redactor **especifica** los recursos en `assets/manifiesto.yaml` (que debe mostrar,
donde va, de que claim sale y con que datos); este script los **produce**. La spec es
dato y el render es codigo: un asset de research no consume tokens de modelo y no puede
inventar una cifra, porque los numeros salen de la spec, que a su vez esta atada a un
claim de `validation/claims.json`.

Clases de asset y quien las produce:

    grafica      -> este script (matplotlib)   afirma un dato
    diagrama     -> este script (graphviz dot) afirma una estructura
    captura      -> PENDIENTE (necesita render-page.js con captura de pantalla)
    ilustracion  -> NO se produce aca: es del presenter, solo adorna

Uso:
    render-asset.py <carpeta-investigacion> [--manifiesto RUTA] [--informe RUTA]
                    [--check] [--json]

    --check   no escribe nada: valida manifiesto, claims y referencias del informe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ── matplotlib vive en el venv de Hermes, no en el python del sistema ──────────────
try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - depende de la instalacion
    _alt = (
        Path(os.environ.get("HOME", ""))
        / ".hermes"
        / "hermes-agent"
        / "venv"
        / "bin"
        / "python"
    )
    if _alt.exists() and Path(sys.executable) != _alt:
        os.execv(str(_alt), [str(_alt), *sys.argv])
    raise

try:
    import yaml
except ImportError:
    yaml = None

CLASES_RENDERIZABLES = {"grafica", "diagrama"}
CLASES_DELEGADAS = {"captura", "ilustracion"}
COLORES = ["#1f3a5f", "#c0392b", "#2e7d5b", "#b8860b", "#6b4c9a", "#4a4a4a"]
RE_IMAGEN = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")


def cargar_manifiesto(ruta: Path) -> dict:
    if yaml is None:
        raise SystemExit("falta pyyaml en el interprete: " + sys.executable)
    if not ruta.exists():
        raise SystemExit(f"no existe el manifiesto: {ruta}")
    datos = yaml.safe_load(ruta.read_text()) or {}
    if not isinstance(datos.get("assets"), list):
        raise SystemExit("el manifiesto necesita una lista `assets:`")
    return datos


def claims_del_contrato(carpeta: Path) -> set[str]:
    ruta = carpeta / "validation" / "claims.json"
    if not ruta.exists():
        return set()
    datos = json.loads(ruta.read_text())
    ids = set()
    for c in datos.get("claims", []):
        if c.get("claim_id"):
            ids.add(str(c["claim_id"]))
    return ids


def referencias_del_informe(ruta: Path) -> set[str]:
    if not ruta.exists():
        return set()
    return {m.strip() for m in RE_IMAGEN.findall(ruta.read_text())}


def validar(datos: dict, carpeta: Path, informe: Path) -> list[str]:
    """Las reglas que hacen que un asset sea evidencia y no decoracion."""
    problemas: list[str] = []
    claims = claims_del_contrato(carpeta)
    refs = referencias_del_informe(informe)
    vistos: set[str] = set()

    for a in datos["assets"]:
        aid = str(a.get("id") or "").strip()
        clase = str(a.get("clase") or "").strip()
        archivo = str(a.get("archivo") or "").strip()
        if not aid or not archivo:
            problemas.append("asset sin `id` o sin `archivo`")
            continue
        if aid in vistos:
            problemas.append(f"{aid}: id repetido")
        vistos.add(aid)
        if clase not in CLASES_RENDERIZABLES | CLASES_DELEGADAS:
            problemas.append(f"{aid}: clase desconocida '{clase}'")
        if clase in {"grafica", "diagrama"}:
            propios = [str(c) for c in (a.get("claims") or [])]
            if not propios:
                problemas.append(f"{aid}: un {clase} tiene que declarar sus `claims`")
            for cid in propios:
                if claims and cid not in claims:
                    problemas.append(f"{aid}: el claim {cid} no existe en claims.json")
            if not a.get("spec"):
                problemas.append(f"{aid}: falta el bloque `spec`")
            if archivo not in refs:
                problemas.append(
                    f"{aid}: un asset de research que el informe no referencia es trabajo perdido"
                )
        if clase == "ilustracion" and not (a.get("spec") or {}).get("prompt"):
            problemas.append(
                f"{aid}: una ilustracion necesita `spec.prompt` para el presenter"
            )

    for ref in refs:
        if ref.startswith("http"):
            continue
        if ref not in {str(a.get("archivo")) for a in datos["assets"]}:
            problemas.append(
                f"el informe referencia '{ref}' y no esta en el manifiesto"
            )
    return problemas


def _figura(spec: dict):
    ancho = float(spec.get("ancho_px", 1600)) / 100
    alto = float(spec.get("alto_px", 900)) / 100
    fig, ax = plt.subplots(figsize=(ancho, alto), dpi=100)
    return fig, ax


def _grafica(spec: dict, destino: Path, titulo: str) -> None:
    tipos = spec.get("tipo", "barras")
    categorias = spec.get("x") or []
    series = spec.get("series") or []
    if not categorias or not series:
        raise SystemExit("una grafica necesita `spec.x` y `spec.series`")

    fig, ax = _figura(spec)
    if tipos == "barras":
        ancho = 0.8 / max(1, len(series))
        for i, s in enumerate(series):
            xs = [
                j + (i - (len(series) - 1) / 2) * ancho for j in range(len(categorias))
            ]
            barras = ax.bar(
                xs,
                s["valores"],
                width=ancho,
                label=s.get("nombre", ""),
                color=COLORES[i % len(COLORES)],
            )
            ax.bar_label(
                barras, fmt=spec.get("formato_valor", "%.2f"), padding=2, fontsize=8
            )
        ax.set_xticks(range(len(categorias)))
        ax.set_xticklabels(categorias)
    elif tipos == "lineas":
        for i, s in enumerate(series):
            ax.plot(
                categorias,
                s["valores"],
                marker="o",
                label=s.get("nombre", ""),
                color=COLORES[i % len(COLORES)],
            )
    elif tipos == "pie":
        ax.pie(
            series[0]["valores"], labels=categorias, autopct="%1.0f%%", colors=COLORES
        )
        ax.axis("equal")
    elif tipos == "dispersion":
        ax.scatter(categorias, series[0]["valores"], color=COLORES[0])
    else:
        raise SystemExit(f"tipo de grafica no soportado: {tipos}")

    if tipos != "pie":
        ax.set_title(titulo or spec.get("titulo", ""), fontsize=12, loc="left", pad=12)
        ax.set_ylabel(spec.get("y_titulo", ""))
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.25)
        if len(series) > 1:
            ax.legend(frameon=False, fontsize=8)
    if spec.get("fuente"):
        fig.text(0.01, 0.01, f"Fuente: {spec['fuente']}", fontsize=7, color="#666666")
    fig.tight_layout()
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino)
    plt.close(fig)


def _diagrama(spec: dict, destino: Path, titulo: str) -> None:
    if not spec.get("nodos"):
        raise SystemExit("un diagrama necesita `spec.nodos`")
    lineas = [
        "digraph G {",
        f'  rankdir="{spec.get("direccion", "TB")}";',
        '  bgcolor="white";',
        '  node [shape=box, style="rounded,filled", fillcolor="#eef2f7", color="#1f3a5f", fontname="Helvetica", fontsize=11];',
        '  edge [color="#4a4a4a", fontname="Helvetica", fontsize=9];',
    ]
    if titulo:
        lineas.append(
            f'  label="{titulo}"; labelloc="t"; fontsize=14; fontname="Helvetica-Bold";'
        )
    for n in spec["nodos"]:
        nid = str(n["id"]).replace('"', "")
        etiqueta = str(n.get("etiqueta") or nid).replace('"', "'")
        lineas.append(f'  "{nid}" [label="{etiqueta}"];')
    for a in spec.get("aristas") or []:
        etiqueta = str(a.get("etiqueta") or "").replace('"', "'")
        extra = f' [label="{etiqueta}"]' if etiqueta else ""
        lineas.append(f'  "{a["desde"]}" -> "{a["hasta"]}"{extra};')
    lineas.append("}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["dot", f"-T{destino.suffix.lstrip('.') or 'png'}", "-o", str(destino)],
        input="\n".join(lineas),
        text=True,
        capture_output=True,
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"graphviz fallo: {r.stderr.strip()[:200]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    carpeta = Path(argv[1]).expanduser().resolve()
    manifiesto = (
        Path(argv[argv.index("--manifiesto") + 1])
        if "--manifiesto" in argv
        else carpeta / "assets" / "manifiesto.yaml"
    )
    informe = (
        Path(argv[argv.index("--informe") + 1])
        if "--informe" in argv
        else carpeta / "deliverables" / "informe.md"
    )
    solo_validar = "--check" in argv

    datos = cargar_manifiesto(manifiesto)
    problemas = validar(datos, carpeta, informe)

    filas = []
    for a in datos["assets"]:
        aid, clase = str(a.get("id")), str(a.get("clase"))
        destino = carpeta / str(a.get("archivo"))
        if clase in CLASES_RENDERIZABLES:
            if solo_validar:
                estado = "listo" if destino.exists() else "falta"
            else:
                try:
                    if clase == "grafica":
                        _grafica(
                            a.get("spec") or {}, destino, str(a.get("titulo") or "")
                        )
                    else:
                        _diagrama(
                            a.get("spec") or {}, destino, str(a.get("titulo") or "")
                        )
                    estado = "generado"
                except SystemExit as e:
                    estado = f"error: {e}"
                    problemas.append(f"{aid}: {estado}")
        else:
            estado = "delegado al presenter"
        filas.append(
            (
                aid,
                clase,
                estado,
                str(a.get("archivo")),
                destino.stat().st_size if destino.exists() else 0,
            )
        )

    if "--json" in argv:
        print(
            json.dumps(
                {
                    "assets": [
                        dict(zip(("id", "clase", "estado", "archivo", "bytes"), f))
                        for f in filas
                    ],
                    "problemas": problemas,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"{'id':6} {'clase':12} {'estado':22} {'bytes':>8}  archivo")
        for aid, clase, estado, archivo, peso in filas:
            print(f"{aid:6} {clase:12} {estado:22} {peso:8}  {archivo}")
        if problemas:
            print("\nPROBLEMAS:")
            for p in problemas:
                print(f"  - {p}")
        else:
            print(
                "\nSin problemas: todo asset afirmado esta atado a un claim y referenciado en el informe."
            )
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
