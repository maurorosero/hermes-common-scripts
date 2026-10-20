#!/usr/bin/env python3
"""Validador determinista del contrato de investigación.

Reglas que aplica:
  - Sólo verifica estructura, obligatoriedad, dominios y formato. NO opina sobre contenido.
  - Corrige lo que tiene un único valor correcto posible (uuid, fecha, estado, versión,
    prefijo). Cada corrección se anota en `correcciones`.
  - Lo que no puede resolver sin decidir por la fuente va a `faltantes`.
  - Un archivo ilegible (sin frontmatter) es el único caso fatal: se rechaza.

Uso:
    python3 research-validator.py <archivo.md>            # informe legible
    python3 research-validator.py <archivo.md> --json     # informe en JSON
    python3 research-validator.py <archivo.md> --aplicar  # escribe el request corregido de vuelta

Sale siempre con 0 salvo error de uso: este validador no bloquea.
"""

from __future__ import annotations

import datetime
import json
import re
import secrets
import string
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent  # el código y el esquema viven juntos
ESQUEMA = BASE / "research-esquema.yaml"
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
ALFABETO = string.ascii_letters + string.digits


# ── esquema ────────────────────────────────────────────────────────────────


def cargar_esquema() -> dict:
    return yaml.safe_load(ESQUEMA.read_text(encoding="utf-8"))


def patron_uuid(esquema: dict) -> re.Pattern:
    u = esquema.get("uuid", {})
    return re.compile(
        rf"^{re.escape(u.get('prefijo', 'I-'))}{u.get('patron_cuerpo', '[A-Za-z0-9]{8}')}$"
    )


def uuid_nuevo(esquema: dict) -> str:
    u = esquema.get("uuid", {})
    cuerpo = "".join(
        secrets.choice(ALFABETO) for _ in range(int(u.get("longitud_cuerpo", 8)))
    )
    return f"{u.get('prefijo', 'I-')}{cuerpo}"


# ── lectura ────────────────────────────────────────────────────────────────


def partir(texto: str):
    """Devuelve (frontmatter_yaml, cuerpo). frontmatter None si no hay bloque cerrado."""
    m = FRONT_RE.match(texto)
    if not m:
        return None, None
    return m.group(1), texto[m.end() :]


def recomponer(frontmatter: dict, cuerpo: str) -> str:
    fm = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return f"---\n{fm}---\n{cuerpo.lstrip(chr(10))}"


# ── validación ─────────────────────────────────────────────────────────────


def _clasificar(data: dict, reporte: dict) -> None:
    """Asigna complejidad y modo. Clasifica la puerta, no la fuente.

    Reglas del proceso:
      - un pedido que termina en documento es complejo SIEMPRE (aunque el tema
        parezca simple): el redactor necesita el brief validado;
      - tres o más ángulos independientes también son complejos;
      - el resto es simple, y lo simple no toca el board.

    Si la fuente ya trajo valores, la regla manda igual y el cambio se anota: la
    clasificación es de la puerta.
    """
    angulos = data.get("angulos") or []
    tiene_documento = bool((data.get("salida_expectada") or {}).get("tipo"))

    if tiene_documento:
        comp, modo, motivo = (
            "compleja",
            "research_swarm",
            "el pedido termina en documento",
        )
    elif isinstance(angulos, list) and len(angulos) >= 3:
        comp, modo, motivo = (
            "compleja",
            "research_swarm",
            f"{len(angulos)} ángulos independientes",
        )
    else:
        comp, modo, motivo = (
            "simple",
            "delegate",
            "sin documento y menos de tres ángulos",
        )

    previos = (data.get("complejidad"), data.get("modo"))
    data["complejidad"], data["modo"] = comp, modo

    if previos != (comp, modo):
        if not any(previos):
            reporte["correcciones"].append(f"clasificación: {comp}/{modo} ({motivo})")
        else:
            reporte["correcciones"].append(
                f"clasificación: {previos[0]}/{previos[1]} → {comp}/{modo} ({motivo})"
            )


def validar(texto: str, esquema: dict | None = None) -> dict:
    esquema = esquema or cargar_esquema()
    reporte = {
        "fatal": False,
        "motivo": "",
        "correcciones": [],
        "faltantes": [],
        "errores": [],
        "data": {},
        "cuerpo": "",
    }

    fm_raw, cuerpo = partir(texto)
    if fm_raw is None:
        reporte["fatal"] = True
        reporte["motivo"] = (
            "sin frontmatter cerrado (--- ... ---): no es interpretable como contrato"
        )
        return reporte

    try:
        data = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as exc:
        reporte["fatal"] = True
        reporte["motivo"] = f"frontmatter no parseable: {exc.__class__.__name__}"
        return reporte

    if not isinstance(data, dict):
        reporte["fatal"] = True
        reporte["motivo"] = "el frontmatter no es un mapa clave: valor"
        return reporte

    reporte["cuerpo"] = cuerpo
    campos = esquema.get("campos", {})
    pat_uuid = patron_uuid(esquema)

    # --- campos simples ---------------------------------------------------
    for nombre, spec in campos.items():
        if spec.get("escrito_por"):
            continue  # los escriben las puertas; se preservan tal cual
        valor = data.get(nombre)
        vacio = valor in (None, "", [], {})
        tipo = spec.get("tipo")

        # uuid: primero intentar normalizar el prefijo, sólo después generar.
        # Un uuid con cuerpo válido pero sin prefijo NO es un uuid nuevo: es el mismo.
        if tipo == "uuid" and isinstance(valor, str) and valor.strip():
            cuerpo_uuid = valor.strip()
            prefijo = esquema.get("uuid", {}).get("prefijo", "I-")
            if not pat_uuid.match(cuerpo_uuid):
                candidato = (
                    cuerpo_uuid
                    if cuerpo_uuid.startswith(prefijo)
                    else f"{prefijo}{cuerpo_uuid}"
                )
                if pat_uuid.match(candidato):
                    data[nombre] = candidato
                    reporte["correcciones"].append(
                        f"{nombre}: prefijo {prefijo} agregado (era '{cuerpo_uuid}')"
                    )
                    continue

        # correcciones con único valor posible
        if vacio or (
            tipo == "uuid"
            and isinstance(valor, str)
            and not pat_uuid.match(valor.strip())
        ):
            correccion = spec.get("correccion")
            if correccion == "generar":
                data[nombre] = uuid_nuevo(esquema)
                reporte["correcciones"].append(f"{nombre}: generado {data[nombre]}")
                continue
            if correccion == "hoy":
                data[nombre] = datetime.date.today().isoformat()
                reporte["correcciones"].append(
                    f"{nombre}: completado con hoy {data[nombre]}"
                )
                continue
            if correccion == "version_actual":
                data[nombre] = esquema.get("version", 1)
                reporte["correcciones"].append(
                    f"{nombre}: completado con versión {data[nombre]}"
                )
                continue
            if correccion and vacio:
                data[nombre] = correccion
                reporte["correcciones"].append(f"{nombre}: forzado a {correccion}")
                continue

        # obligatorios sin corrección → faltante
        if vacio and spec.get("obligatorio"):
            reporte["faltantes"].append(f"{nombre}: obligatorio y vacío")
            continue

        if vacio:
            continue

        # tipo / dominio / longitud
        if tipo == "enum" and valor not in spec.get("dominio", []):
            reporte["faltantes"].append(
                f"{nombre}: '{valor}' no está en {'/'.join(map(str, spec.get('dominio', [])))}"
            )
        elif tipo == "texto":
            if not isinstance(valor, str):
                reporte["faltantes"].append(f"{nombre}: se esperaba texto")
            else:
                if spec.get("min") and len(valor.strip()) < int(spec["min"]):
                    reporte["faltantes"].append(
                        f"{nombre}: {len(valor.strip())} caracteres, mínimo {spec['min']}"
                    )
                if spec.get("max") and len(valor) > int(spec["max"]):
                    reporte["faltantes"].append(
                        f"{nombre}: {len(valor)} caracteres, máximo {spec['max']}"
                    )
        elif tipo == "fecha":
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(valor)):
                reporte["faltantes"].append(
                    f"{nombre}: se esperaba YYYY-MM-DD, vino '{valor}'"
                )
        elif tipo == "entero":
            try:
                data[nombre] = int(valor)
            except (TypeError, ValueError):
                reporte["faltantes"].append(
                    f"{nombre}: se esperaba entero, vino '{valor}'"
                )
        elif tipo == "lista_rutas":
            if not isinstance(valor, list):
                reporte["faltantes"].append(f"{nombre}: se esperaba lista de rutas")
            elif spec.get("verificar_existencia"):
                for ruta in valor:
                    if not Path(str(ruta)).expanduser().exists():
                        reporte["faltantes"].append(
                            f"{nombre}: la ruta no existe: {ruta}"
                        )

    # --- objeto salida_expectada -----------------------------------------
    se = data.get("salida_expectada")
    if isinstance(se, dict):
        spec_se = campos.get("salida_expectada", {}).get("campos", {})
        for sub, sub_spec in spec_se.items():
            v = se.get(sub)
            if v and sub_spec.get("dominio") and v not in sub_spec["dominio"]:
                reporte["faltantes"].append(
                    f"salida_expectada.{sub}: '{v}' no está en {'/'.join(sub_spec['dominio'])}"
                )

    # --- ángulos ----------------------------------------------------------
    angulos = data.get("angulos")
    if isinstance(angulos, list):
        spec_ang = campos.get("angulos", {}).get("campos", {})
        limpios = []
        for i, ang in enumerate(angulos, 1):
            if isinstance(ang, str):
                ang = {"titulo": ang}
            if not isinstance(ang, dict) or not str(ang.get("titulo", "")).strip():
                reporte["faltantes"].append(f"angulos[{i}]: sin titulo")
                continue
            for sub in ang:
                if sub not in spec_ang:
                    reporte["faltantes"].append(
                        f"angulos[{i}].{sub}: campo no declarado en el esquema"
                    )
                    continue
                spec_sub = spec_ang[sub]
                valor_sub = ang[sub]
                if spec_sub.get("tipo") == "enum" and valor_sub not in spec_sub.get(
                    "dominio", []
                ):
                    reporte["faltantes"].append(
                        f"angulos[{i}].{sub}: '{valor_sub}' no está en "
                        f"{'/'.join(map(str, spec_sub.get('dominio', [])))}"
                    )
            # presupuesto: derivarlo del nivel es determinista; elegir nivel cuando la
            # fuente no lo dijo es criterio del orquestador, no del validador. Por eso el
            # default sale de la tabla del esquema y no de una opinión.
            if not ang.get("presupuesto"):
                pre = esquema.get("presupuestos") or {}
                nivel = str(ang.get("investigador") or "")
                mapa = pre.get("por_investigador") or {}
                ang["presupuesto"] = mapa.get(nivel) or pre.get(
                    "por_defecto", "estandar"
                )
                reporte["correcciones"].append(
                    f"angulos[{i}].presupuesto: completado con "
                    f"'{ang['presupuesto']}' (investigador: {nivel or 'sin nivel'})"
                )
            limpios.append(ang)
        data["angulos"] = limpios

    # --- clasificación: complejidad y modo los pone la puerta -------------
    _clasificar(data, reporte)

    # --- reglas condicionales --------------------------------------------
    for regla in esquema.get("reglas", []):
        nombre = regla.get("nombre", "")

        if nombre == "coherencia_modo":
            par = regla.get("pares", {})
            comp, modo = data.get("complejidad"), data.get("modo")
            if comp in par and modo and modo != par[comp]:
                reporte["faltantes"].append(
                    f"regla {nombre}: complejidad '{comp}' exige modo '{par[comp]}', vino '{modo}'"
                )

        elif nombre == "salida_completa":
            ruta = regla.get("si_presente", "")
            cabeza, _, cola = ruta.partition(".")
            bloque = data.get(cabeza)
            if isinstance(bloque, dict) and bloque.get(cola):
                for req in regla.get("entonces_obligatorio", []):
                    r2 = req.split(".", 1)[-1]
                    if not bloque.get(r2):
                        reporte["faltantes"].append(
                            f"regla {nombre}: salida_expectada.{r2} es obligatorio cuando hay tipo"
                        )

        elif nombre == "angulos_si_son_solo_estos":
            cond = regla.get("si", {})
            if all(data.get(k) == v for k, v in cond.items()) and not data.get(
                "angulos"
            ):
                reporte["faltantes"].append(
                    f"regla {nombre}: angulos_modo=solo_estos exige lista de angulos"
                )

    # --- dominio de lo que escriben las puertas ----------------------------
    # Estos campos no se corrigen (el valor lo pone la puerta, no el validador), pero un
    # valor fuera de dominio sí se reporta: es una pregunta para el orquestador, no un dato.
    # Va al final, cuando la clasificación ya puso complejidad y modo.
    for nombre, spec in campos.items():
        if not spec.get("escrito_por") or spec.get("tipo") != "enum":
            continue
        valor_p = data.get(nombre)
        if valor_p in (None, "", [], {}) or valor_p in spec.get("dominio", []):
            continue
        reporte["faltantes"].append(
            f"{nombre}: '{valor_p}' no está en {'/'.join(map(str, spec.get('dominio', [])))}"
        )

    reporte["data"] = data
    return reporte


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    ruta = Path(argv[0]).expanduser()
    if not ruta.exists():
        print(f"no existe: {ruta}", file=sys.stderr)
        return 2
    texto = ruta.read_text(encoding="utf-8")
    rep = validar(texto)
    esquema = cargar_esquema()

    if "--aplicar" in argv and not rep["fatal"]:
        data = rep["data"]
        if rep["correcciones"]:
            data["correcciones"] = sorted(
                set(list(data.get("correcciones") or []) + rep["correcciones"])
            )
        if rep["faltantes"]:
            data["faltantes"] = rep["faltantes"]
        ruta.write_text(recomponer(data, rep["cuerpo"]), encoding="utf-8")

    if "--json" in argv:
        print(
            json.dumps(
                {
                    k: rep[k]
                    for k in ("fatal", "motivo", "correcciones", "faltantes", "errores")
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"archivo: {ruta}")
    if rep["fatal"]:
        print(f"FATAL: {rep['motivo']}")
        return 0
    print(f"esquema: v{esquema.get('version')}")
    print(f"correcciones ({len(rep['correcciones'])}):")
    for c in rep["correcciones"]:
        print(f"  - {c}")
    print(f"faltantes ({len(rep['faltantes'])}):")
    for f in rep["faltantes"]:
        print(f"  - {f}")
    if not rep["correcciones"] and not rep["faltantes"]:
        print("sin defectos: el contrato está completo")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
