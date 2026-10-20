#!/usr/bin/env python3
"""Puerta — puente determinista cola/ → board kanban `research`.

Qué hace, en dos barridos:

  BARRIDO 1 — la entrada (`requested/`):
    1. Barre `requested/*.md` (ignora PLANTILLA.md y README.md).
    2. Valida cada request con el esquema (research-validator.py).
    3. Si es ilegible (sin frontmatter cerrado) → lo mueve a `_rechazados/` con el motivo.
    4. Si es interpretable → crea `processing/<I-…>/`, deja ahí el request corregido
       (con sus `correcciones` y `faltantes`) y crea la card raíz para el orquestador.
    5. Sólo borra el archivo de la cola si la card se creó de verdad.

  BARRIDO 2 — el despacho (`processing/`):
    6. Busca investigaciones cuyo request ya tiene `listo_por` (el orquestador pasó) y que
       todavía no tienen `dispatch.json`.
    7. Abre una card por ángulo: `researcher` si el ángulo es barato, `researcher-expert` si
       es experto, todas colgando de la card raíz (`--parent`) y con el uuid de tenant.
    8. Deja `dispatch.json` en la carpeta con los ids: es la marca de que el despacho ya se
       hizo, y el mapa del grafo para los barridos siguientes.

Regla dura: si la card falla, el request SE QUEDA en la cola. Nunca se pierde un pedido
por un fallo del board.

Uso:
    python3 research-watcher.py              # los dos barridos
    python3 research-watcher.py --dry-run    # informa sin mover ni crear nada
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

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


def _env_del_home() -> Path:
    """El `.env` del home activo. Se compone: HERMES_HOME si está, si no el home del usuario."""
    home = os.environ.get("HERMES_HOME", "").strip() or str(Path.home() / ".hermes")
    return Path(home) / ".env"


def cargar_variables_de_datos() -> list[str]:
    """Trae las RESEARCH_* del `.env` del home cuando el proceso no las tiene.

    El cron corre el script sin el entorno del shell, así que las variables del `.env` no
    llegan solas: sin esto el barrido falla con "falta RESEARCH_REQ_PATH". Se leen sólo las
    variables de rutas de datos; el resto del archivo no se toca.

    Devuelve las que siguen faltando después de intentarlo.
    """
    faltan = [v for v in VARS.values() if not os.environ.get(v, "").strip()]
    if not faltan:
        return []

    ruta = _env_del_home()
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if clave in VARS.values() and valor and not os.environ.get(clave):
                os.environ[clave] = os.path.expandvars(valor)

    return [v for v in VARS.values() if not os.environ.get(v, "").strip()]


def ruta_de(clave: str) -> Path:
    var = VARS[clave]
    valor = os.environ.get(var, "").strip()
    if not valor:
        raise SystemExit(
            f"falta {var} en el entorno (.env).\n"
            f"Sin esa variable no sé dónde está la carpeta de {clave} y no voy a inventarla."
        )
    return Path(os.path.expandvars(valor)).expanduser()


IGNORAR = {"PLANTILLA.md", "README.md"}
BOARD = "research"
PRIME = "researcher-prime"

# El skill que cada oficio arranca puesto en la card. Sin esto el worker descubre su propio
# procedimiento a mitad del trabajo, después de haber improvisado.
SKILL_DEL_OFICIO = {
    "researcher-prime": "researcher-prime",
    "researcher": "researcher-detective",
    "researcher-expert": "researcher-detective",
}

# Un ángulo dice quién lo investiga; acá se traduce a perfil. El default es el barato:
# si un ángulo no dice nada, no se le regala el motor caro.
RESEARCHERS = {"barato": "researcher", "experto": "researcher-expert"}

# Marca del despacho: si existe, los hijos de esa investigación ya se crearon.
MARCA = "dispatch.json"

SUBCARPETAS = ("sources", "validation", "deliverables", "assets")


def crear_card(
    tema: str, cuerpo: str, uuid: str, perfil: str = PRIME, skill: str = ""
) -> tuple[int, str]:
    """Crea una card en el board. Devuelve (returncode, salida)."""
    cmd = [
        "hermes",
        "kanban",
        "create",
        f"Investigación: {tema}",
        "--assignee",
        perfil,
        "--tenant",
        uuid,
        "--idempotency-key",
        uuid,
        "--body",
        cuerpo,
    ]
    if skill:
        cmd += ["--skill", skill]
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


def rutas_resueltas(carpeta: Path) -> str:
    """Encabezado con las rutas ya compuestas: el worker no tiene que buscarlas.

    La ruta no se escribe en el código: se compone acá, en la corrida, y viaja en la card.
    Sin esto el worker se gasta una veintena de llamadas averiguando dónde está cada cosa.
    """
    return "\n".join(
        [
            "Rutas ya resueltas — no las busques ni las compongas:",
            f"  carpeta:   {carpeta}",
            f"  request:   {carpeta / 'request.md'}",
            f"  contrato:  {BASE / 'research-esquema.yaml'}",
            f"  validador: {BASE / 'research-validator.py'}",
            "",
            "El request completo va abajo. Trabajás sobre el archivo, no sobre este mensaje.",
            "",
            "─" * 60,
            "",
        ]
    )


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

    rc, salida = crear_card(
        tema,
        rutas_resueltas(destino) + final,
        uuid,
        perfil=PRIME,
        skill=SKILL_DEL_OFICIO.get(PRIME, ""),
    )
    if rc != 0:
        # no se pierde el pedido: queda en la cola para el próximo barrido
        return f"ERROR {archivo.name}: la card no se creó (rc={rc}) — el request queda en la cola\n{salida.strip()[:400]}"

    archivo.unlink(missing_ok=True)
    return (
        f"PROCESADO {archivo.name} → {uuid} · {tema} · "
        f"{len(rep['correcciones'])} correcciones · {len(rep['faltantes'])} faltantes"
    )


# ── barrido 2: el despacho ───────────────────────────────────────────────────


def _kanban(args: list[str]) -> tuple[int, str]:
    """Corre `hermes kanban …` en el board del proceso. Devuelve (rc, salida)."""
    env = dict(os.environ)
    env["HERMES_KANBAN_BOARD"] = BOARD
    env.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)  # un hijo no muta el board
    try:
        r = subprocess.run(
            ["hermes", "kanban", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _cards_del_tenant(uuid: str) -> list[dict]:
    """Las cards de una investigación. Lista vacía si el board no responde."""
    rc, txt = _kanban(["list", "--tenant", uuid, "--json"])
    if rc != 0:
        return []
    inicio = txt.find("[")
    if inicio < 0:
        return []
    try:
        cards = json.loads(txt[inicio:])
    except json.JSONDecodeError:
        return []
    return [c for c in cards if isinstance(c, dict)]


def frontmatter_de(ruta: Path) -> dict:
    """El frontmatter del request, sin pasar por el validador. {} si no se puede leer."""
    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.search(r"^---\n(.*?)\n---", texto, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


_PRESUPUESTOS: dict | None = None


def _tabla_de_presupuestos() -> dict:
    """La tabla de techos, leída del esquema. Cacheada: el esquema no cambia en la corrida."""
    global _PRESUPUESTOS
    tabla = _PRESUPUESTOS
    if tabla is None:
        cargada = cargar_esquema().get("presupuestos") or {}
        tabla = cargada if isinstance(cargada, dict) else {}
        _PRESUPUESTOS = tabla
    return tabla


def linea_de_presupuesto(ang: dict) -> str:
    """El techo del ángulo en palabras, para que viaje en la card y no haya que buscarlo."""
    tabla = _tabla_de_presupuestos()
    nivel = str(ang.get("presupuesto") or "")
    numeros = (tabla.get("niveles") or {}).get(nivel)
    if not numeros:
        return ""
    tope = tabla.get("tope_duro") or {}
    return (
        f"Presupuesto: {nivel} — hasta {numeros.get('llamadas')} llamadas de herramienta "
        f"y {numeros.get('fuentes')} fuentes. Tope duro "
        f"{tope.get('llamadas')}/{tope.get('fuentes')}: pasado ese punto entregás lo que "
        f"tengas en vez de seguir buscando."
    )


def cuerpo_de_angulo(data: dict, i: int, n: int, carpeta: Path) -> tuple[str, str, str]:
    """(título de la card, cuerpo, perfil) del ángulo i, en base 1."""
    ang = data["angulos"][i - 1] if isinstance(data["angulos"][i - 1], dict) else {}
    titulo = str(ang.get("titulo") or f"ángulo {i}")
    perfil = RESEARCHERS.get(str(ang.get("investigador") or "barato"), "researcher")
    salida = ang.get("salida") or f"sources/{perfil}-{i}-{slug(titulo)}.md"
    lineas = [
        f"Investigación {data.get('uuid')} · ángulo {i} de {n}",
        f"Tema: {data.get('tema')}",
        f"Pedido completo: {carpeta / 'request.md'}",
        f"Tu salida: {carpeta / str(salida)}",
        "",
        f"Alcance: {ang.get('alcance') or '(el que delimita el título)'}",
    ]
    techo = linea_de_presupuesto(ang)
    if techo:
        lineas += ["", techo]
    preguntas = ang.get("preguntas")
    if isinstance(preguntas, list) and preguntas:
        lineas += ["", "Preguntas que hay que responder:"] + [
            f"  - {p}" for p in preguntas
        ]
    fuentes = ang.get("fuentes_sugeridas")
    if isinstance(fuentes, list) and fuentes:
        lineas += ["", "Fuentes sugeridas para arrancar:"] + [
            f"  - {f}" for f in fuentes
        ]
    if data.get("instrucciones"):
        lineas += [
            "",
            "Lo que pidió el solicitante:",
            str(data["instrucciones"]).strip(),
        ]
    return f"Ángulo {i}: {titulo}", "\n".join(lineas), perfil


def despachar(en_curso: Path, dry: bool) -> list[str]:
    """Abre las cards de los ángulos de toda investigación ya cerrada por el orquestador."""
    lineas: list[str] = []
    if not en_curso.exists():
        return lineas

    for carpeta in sorted(p for p in en_curso.iterdir() if p.is_dir()):
        pedido = carpeta / "request.md"
        if not pedido.exists() or (carpeta / MARCA).exists():
            continue

        data = frontmatter_de(pedido)
        if not data.get("listo_por"):
            continue  # el orquestador todavía no pasó por este pedido
        if data.get("modo") != "research_swarm":
            continue  # lo simple no arma grafo: se resuelve inline

        angulos = data.get("angulos")
        if not isinstance(angulos, list) or not angulos:
            lineas.append(
                f"AVISO {carpeta.name}: sin ángulos, no hay a quién despachar"
            )
            continue

        uuid = str(data.get("uuid") or carpeta.name)
        raiz = ""
        for card in _cards_del_tenant(uuid):
            if card.get("assignee") == PRIME:
                raiz = str(card.get("id") or "")
                break

        n = len(angulos)
        creadas: list[dict] = []
        for i in range(1, n + 1):
            titulo, cuerpo, perfil = cuerpo_de_angulo(data, i, n, carpeta)
            if dry:
                lineas.append(f"OK(dry) {carpeta.name}: {titulo} → {perfil}")
                continue
            args = [
                "create",
                titulo,
                "--assignee",
                perfil,
                "--tenant",
                uuid,
                "--idempotency-key",
                f"{uuid}-a{i}",
                "--body",
                cuerpo,
            ]
            skill = SKILL_DEL_OFICIO.get(perfil, "")
            if skill:
                args += ["--skill", skill]
            if raiz:
                args += ["--parent", raiz]
            rc, txt = _kanban(args)
            if rc != 0:
                lineas.append(
                    f"ERROR {carpeta.name}: '{titulo}' no se creó (rc={rc}) {txt.strip()[:200]}"
                )
                continue
            m = re.search(r"(t_[A-Za-z0-9]+)", txt)
            creadas.append(
                {
                    "angulo": i,
                    "titulo": titulo,
                    "perfil": perfil,
                    "card": m.group(1) if m else "",
                }
            )

        if dry:
            continue

        plan = {
            "uuid": uuid,
            "raiz": raiz,
            "verificacion": str(data.get("verificacion") or ""),
            "angulos": creadas,
            "verifier": None,
            "redactor": None,
        }
        if len(creadas) != n:
            # despacho incompleto: no se marca como terminado, el próximo barrido reintenta.
            # La llave de idempotencia evita duplicar las que sí se crearon.
            (carpeta / f"{MARCA}.parcial").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            lineas.append(
                f"PARCIAL {carpeta.name}: {len(creadas)}/{n} cards — reintenta el próximo barrido"
            )
            continue

        (carpeta / MARCA).write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        conteo = ", ".join(f"{c['perfil']}" for c in creadas)
        lineas.append(
            f"DESPACHADO {carpeta.name}: {n} ángulos → {conteo} · raíz {raiz or '(sin raíz)'}"
        )
    return lineas


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    cargar_variables_de_datos()  # el cron no pasa entorno: las rutas se leen del .env del home
    cola = ruta_de("cola")
    en_curso = ruta_de("en_curso")
    # los rechazados viven al lado de la cola: una variable menos que mantener
    rechazados = cola / "_rechazados"

    esquema = cargar_esquema()
    fallos = 0

    # ── barrido 1: la entrada ────────────────────────────────────────────────
    if cola.exists():
        pendientes = [p for p in sorted(cola.glob("*.md")) if p.name not in IGNORAR]
        for archivo in pendientes:
            linea = procesar(archivo, esquema, dry, cola, en_curso, rechazados)
            if linea is None:  # ya avisado en un barrido anterior
                continue
            print(linea)
            if linea.startswith("ERROR"):
                fallos += 1
    else:
        print(f"la cola no existe todavía: {cola} (creala o revisá {VARS['cola']})")

    # ── barrido 2: el despacho ───────────────────────────────────────────────
    # Corre siempre, aunque la cola esté vacía: los pedidos que el orquestador cerró
    # después del barrido anterior tienen que salir en este.
    for linea in despachar(en_curso, dry):
        print(linea)
        if linea.startswith("ERROR"):
            fallos += 1

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
