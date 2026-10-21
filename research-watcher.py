#!/usr/bin/env python3
"""Puerta — puente determinista cola/ → board kanban `research`.

Qué hace, en cinco barridos:

  BARRIDO 1 — la entrada (`requested/`):
    1. Barre `requested/*.md` (ignora PLANTILLA.md y README.md).
    2. Valida cada request con el esquema (research-validator.py).
    3. Si es ilegible (sin frontmatter cerrado) → lo mueve a `_rejected/` con el motivo.
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

  BARRIDO 3 — la verificación de citas (`processing/`):
    9. Cuando un ángulo ya escribió su ficha en `sources/`, corre `verify-citations.py` sobre
       ella y deja el veredicto en `validation/citations.json`. Es determinista y no gasta
       tokens: comprueba que la fuente se recuperó de verdad y que la cita literal está dentro.
   10. Es idempotente por mtime: si el JSON es más nuevo que las fichas, no se repite.

  BARRIDO 4 — el cierre del grafo (`processing/`):
   11. Cuando todos los ángulos están cerrados y existe `validation/claims.json`, crea la card
       del verificador: juzga los claims y escribe `validation/verdicts.json`.
   12. Cuando esa card cierra y hay veredictos, crea la card del redactor, que escribe
       `deliverables/informe.md`. Si el request no pedía verificación, se la salta.

Regla dura: si la card falla, el request SE QUEDA en la cola. Nunca se pierde un pedido
por un fallo del board.

Uso:
    python3 research-watcher.py              # los cuatro barridos
    python3 research-watcher.py --dry-run    # informa sin mover ni crear nada
   12. Cuando esa card cierra y hay `validation/verdicts.json`, crea la card del redactor: escribe
       el documento y especifica sus assets en `assets/manifiesto.yaml`.

  BARRIDO 5 — el gate humano (`processing/`):
   13. Cuando el redactor cerró y existe `deliverables/informe.md`, abre una card de gate en
       `blocked` (el "pendiente" de la casa). Ningún agente la ejecuta: la cierra una persona
       con `research-gate.py approve|improve|present`. `done` es terminal, por eso la aprobación
       vive en una card aparte y no en la del redactor.
   14. Idempotente por `dispatch.json[gate]`; la clave de idempotencia cambia por corrida, así
       que una corrección abre un gate nuevo en vez de colisionar con el anterior.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
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
    rejected: Path,
) -> str | None:
    texto = archivo.read_text(encoding="utf-8")
    rep = validar(texto, esquema)

    if rep["fatal"]:
        if not dry:
            rejected.mkdir(parents=True, exist_ok=True)
            destino = rejected / archivo.name
            shutil.move(str(archivo), destino)
            (rejected / f"{archivo.name}.motivo.txt").write_text(
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
    # El pedido deja la cola: el recorrido del esquema arranca en `processing`. Sin esto el
    # request se queda en `requested` para siempre y el estado deja de decir dónde está.
    data["estado"] = "processing"
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
        (
            "Formato de la salida: ficha de hallazgos del skill researcher-detective — de 5 a "
            "15, cada uno con cita literal, fuente y fecha, más discrepancias y huecos. El "
            "informe lo escribe otro: vos no redactás prosa."
        ),
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


def verificar_citas(en_curso: Path, dry: bool) -> list[str]:
    """Barrido 3 — verifica mecánicamente las citas de cada ficha entregada.

    Determinista y sin tokens: `verify-citations.py` comprueba contra la caché de extracción
    que la fuente se recuperó y que la cita literal aparece dentro. El veredicto se guarda en
    `validation/citations.json` para que el verificador y el redactor lo consuman sin releer.

    Idempotente por mtime: si el JSON es más nuevo que todas las fichas, no se vuelve a correr.
    El cron pasa cada 5 minutos, así que repetirlo sería pagar por lo mismo.
    """
    lineas: list[str] = []
    script = BASE / "verify-citations.py"
    if not en_curso.exists():
        return lineas

    for carpeta in sorted(p for p in en_curso.iterdir() if p.is_dir()):
        fuentes = carpeta / "sources"
        fichas = sorted(fuentes.glob("*.md")) if fuentes.exists() else []
        if not fichas:
            continue
        salida = carpeta / "validation" / "citations.json"
        if salida.exists() and salida.stat().st_mtime >= max(
            f.stat().st_mtime for f in fichas
        ):
            continue  # ya verificado y ninguna ficha cambió desde entonces
        if dry:
            lineas.append(f"VERIFICARÍA citas de {carpeta.name}: {len(fichas)} fichas")
            continue
        try:
            r = subprocess.run(
                [sys.executable, str(script), *map(str, fichas), "--json"],
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
            )
            datos = json.loads(r.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            lineas.append(f"ERROR citas {carpeta.name}: {exc}")
            continue
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        verdes = sum(d.get("verificadas") or 0 for d in datos)
        fallas = sum(d.get("cita_no_coincide") or 0 for d in datos)
        grises = sum(d.get("sin_fuente") or 0 for d in datos)
        hallazgos = sum(d.get("hallazgos") or 0 for d in datos)
        if not hallazgos:
            # un informe viejo o un entregable que no es ficha: no hay nada que verificar
            lineas.append(
                f"CITAS SIN FORMATO {carpeta.name}: ninguna ficha con hallazgos numerados "
                f"en sources/ (el entregable no es una ficha)"
            )
            continue
        # ── el contrato que lee el verificador con modelo ────────────────────
        # Un claim por hallazgo, con su id, su veredicto de citas y la ruta de la evidencia.
        # Así el verificador abre la evidencia (3 KB) en vez de releer la ficha (12 KB) y no
        # vuelve a buscar: sólo juzga los que llegan con evidencia.
        claims = []
        for informe in datos:
            for fila in informe.get("detalle", []):
                claims.append(
                    {
                        "id": f"{Path(informe['ficha']).stem}/{fila['claim_id']}",
                        "claim_id": fila["claim_id"],
                        "ficha": informe["ficha"],
                        "afirmacion": fila["hallazgo"],
                        "estado_citas": fila["estado"],
                        "citas_ok": fila["citas_ok"],
                        "citas_fallidas": fila["citas_fallidas"],
                        "claim_status": fila["claim_status"],
                        "requiere_juicio": fila["requiere_juicio"],
                        "fuentes": fila["fuentes"],
                        "evidencia": fila["evidencia"],
                        "contexto": fila.get("contexto") or [],
                    }
                )
        contrato = {
            "uuid": carpeta.name,
            "generado": datetime.now(UTC).isoformat(timespec="seconds"),
            "fichas": [informe["ficha"] for informe in datos],
            "totales": {
                "claims": len(claims),
                "verificadas": verdes,
                "cita_no_coincide": fallas,
                "sin_fuente": grises,
                "requieren_juicio": sum(1 for c in claims if c["requiere_juicio"]),
                "unverified_por_citas": sum(
                    1 for c in claims if c["claim_status"] == "unverified"
                ),
                "fuentes_distintas": sum(
                    i.get("fuentes_distintas") or 0 for i in datos
                ),
            },
            "avisos": [a for informe in datos for a in (informe.get("avisos") or [])],
            "claims": claims,
        }
        (salida.parent / "claims.json").write_text(
            json.dumps(contrato, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        marca = "CITAS FALLIDAS" if fallas else "CITAS"
        lineas.append(
            f"{marca} {carpeta.name}: {verdes} verificadas · {fallas} con cita que no coincide · "
            f"{grises} sin fuente recuperada"
        )
    return lineas


def _cuerpo_del_verificador(
    uuid: str, carpeta: Path, plan: dict, pendientes: list[str] | None = None
) -> str:
    lineas = [
        f"Investigación {uuid}: los ángulos terminaron y la verificación de citas ya corrió.",
        "",
        "Rutas ya resueltas — no las busques ni las compongas:",
        f"  claims:    {carpeta / 'validation' / 'claims.json'}",
        f"  fichas:    {carpeta / 'sources'}",
        f"  tu salida: {carpeta / 'validation' / 'verdicts.json'}",
        "",
        "Cargá el skill `researcher-verifier` y seguí su procedimiento.",
        "",
    ]
    if pendientes:
        # Re-verificación: entró material nuevo y sólo eso falta juzgar. Rejuzgar los que ya
        # tienen veredicto es pagar dos veces por la misma respuesta.
        lineas += [
            "Esto es una **re-verificación**: el veredicto anterior ya cubre el resto.",
            f"Juzgá **sólo** estos {len(pendientes)} claims, que son los que no tienen veredicto:",
            "  " + ", ".join(pendientes),
            "Copiá los veredictos que ya existen en `verdicts.json` tal cual y agregá los nuevos.",
            "",
        ]
    else:
        lineas += [
            "En corto: leé `claims.json` una vez, juzgá **sólo** los claims con",
            "`requiere_juicio: true`, y escribí `verdicts.json` una sola vez con un registro",
            'por claim. Los que ya traen `claim_status: "unverified"` están resueltos por el',
            "verificador de citas: se copian, no se rejuzgan.",
            "",
        ]
    lineas += [
        "La evidencia de cada claim ya está en disco y su ruta viaja en el campo",
        "`evidencia`. Abrí ese archivo; no hagas un fetch si alcanza. No busques fuentes",
        "nuevas: no es tu oficio y no hay tiempo para eso.",
    ]
    return "\n".join(lineas)


def _pide_presentacion(carpeta: Path) -> bool:
    """Si el pedido declara que el resultado tiene que pasar por el presenter."""
    datos = frontmatter_de(carpeta / "request.md")
    if not datos:
        return False
    if (
        str(datos.get("destino_post_investigacion") or "").strip().casefold()
        == "documento"
    ):
        return True
    return bool(str(datos.get("especificacion_presentacion") or "").strip())


def _cuerpo_del_redactor(uuid: str, carpeta: Path, plan: dict) -> str:
    return "\n".join(
        [
            f"Investigación {uuid}: los veredictos están emitidos. Falta el documento.",
            "",
            "Rutas ya resueltas — no las busques ni las compongas:",
            f"  fichas:    {carpeta / 'sources'}",
            f"  claims:    {carpeta / 'validation' / 'claims.json'}",
            f"  veredictos:{carpeta / 'validation' / 'verdicts.json'}",
            f"  request:   {carpeta / 'request.md'}",
            f"  tu salida: {carpeta / 'deliverables' / 'informe.md'}",
            (
                f"  spec presenter: {carpeta / 'deliverables' / 'especificacion-presenter.yaml'}"
                "  (el pedido pide presentación: perfeccioná sus instrucciones)"
                if _pide_presentacion(carpeta)
                else ""
            ),
            "",
            "Cargá el skill `redactor-documento` y seguí su procedimiento.",
            "",
            "Tu material son las fichas, los claims y los veredictos: no investigues ni abras",
            "fuentes nuevas. Cada dato del informe se rastrea a un `claim_id`; lo que está",
            "`unverified` o `contradicted` no se afirma, se declara en la sección de límites.",
        ]
    )


def _claims_sin_veredicto(claims: Path, verdicts: Path) -> list[str]:
    """Ids de claims que ningun veredicto cubre todavia.

    Cuando entra material nuevo despues del pase del verificador, esos claims quedan sin juicio
    y el redactor los escribe como si no existieran. Esta lista es exactamente lo que hay que
    volver a juzgar, y nada mas.
    """
    try:
        datos = json.loads(claims.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lista = datos.get("claims") if isinstance(datos, dict) else datos
    ids = [c.get("id") for c in (lista or []) if c.get("id")]
    if not ids:
        return []
    cubiertos: set[str] = set()
    if verdicts.exists():
        try:
            v = json.loads(verdicts.read_text(encoding="utf-8"))
            juzgados = v.get("veredictos") if isinstance(v, dict) else v
            cubiertos = {x.get("id") for x in (juzgados or []) if x.get("id")}
        except (OSError, json.JSONDecodeError):
            cubiertos = set()
    return [i for i in ids if i not in cubiertos]


def cerrar(en_curso: Path, dry: bool) -> list[str]:
    """Barrido 4 — cierra el grafo: primero el verificador, después el redactor.

    El verificador no se crea hasta que **todos** los ángulos están cerrados, y el redactor no
    se crea hasta que existe `verdicts.json`. Cada etapa es una card más en el mesmo tenant, así
    que el grafo completo de una investigación se lee de un vistazo en el board.

    Las dos etapas son idempotentes por el `dispatch.json`: una vez anotado el id, no se repite.
    """
    lineas: list[str] = []
    if not en_curso.exists():
        return lineas

    for carpeta in sorted(p for p in en_curso.iterdir() if p.is_dir()):
        marca = carpeta / MARCA
        if not marca.exists():
            continue
        try:
            plan = json.loads(marca.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        uuid = plan.get("uuid") or carpeta.name
        cards = {c.get("id"): c.get("status") for c in _cards_del_tenant(uuid)}
        cerradas = ("done", "archived")

        angulos_ok = all(
            cards.get(a.get("card", "")) in cerradas for a in plan.get("angulos", [])
        )
        claims = carpeta / "validation" / "claims.json"
        verdicts = carpeta / "validation" / "verdicts.json"
        verificar = str(plan.get("verificacion") or "").strip().casefold() in (
            "si",
            "sí",
            "true",
            "1",
        )

        # ── etapa A: el verificador ──────────────────────────────────────────
        pendientes = _claims_sin_veredicto(claims, verdicts) if claims.exists() else []
        # Re-verificar cuando entro material nuevo: si `claims.json` es mas nuevo que
        # `verdicts.json` hay claims sin juicio. Sin esto, agregar hallazgos despues del pase
        # dejaba esos claims sin veredicto para siempre y el redactor los marcaba como humo.
        reverificar = bool(
            plan.get("verifier")
            and plan.get("verifier") != "omitido"
            and pendientes
            and claims.exists()
            and claims.stat().st_mtime
            > (verdicts.stat().st_mtime if verdicts.exists() else 0.0)
            and cards.get(plan.get("verifier") or "") in cerradas
        )
        if (not plan.get("verifier") and angulos_ok and claims.exists()) or reverificar:
            if not verificar:
                plan["verifier"] = "omitido"  # el pedido no pedía verificación
                lineas.append(f"VERIFICACIÓN OMITIDA {uuid}: el request no la pedía")
            elif dry:
                lineas.append(
                    f"CREARÍA verificador {uuid}"
                    + (" (re-verificación)" if reverificar else "")
                )
            else:
                corridas = int(plan.get("verifier_corridas") or 1)
                args = [
                    "create",
                    f"Verificar: {uuid}"
                    + (f" (re-verificación v{corridas})" if reverificar else ""),
                    "--assignee",
                    "researcher-verifier",
                    "--tenant",
                    uuid,
                    "--idempotency-key",
                    f"{uuid}-v{corridas}",
                    "--skill",
                    "researcher-verifier",
                    "--body",
                    _cuerpo_del_verificador(
                        uuid, carpeta, plan, pendientes if reverificar else None
                    ),
                ]
                if plan.get("raiz"):
                    args += ["--parent", plan["raiz"]]
                rc, txt = _kanban(args)
                if rc != 0:
                    lineas.append(f"ERROR verificador {uuid}: {txt.strip()[:200]}")
                    continue
                m = re.search(r"(t_[A-Za-z0-9]+)", txt)
                plan["verifier"] = m.group(1) if m else ""
                plan["verifier_corridas"] = corridas + 1
                lineas.append(
                    f"DESPACHADO verificador {uuid}"
                    + (" (re-verificación)" if reverificar else "")
                    + f" → {plan['verifier'] or '(sin id)'}"
                )

        # ── etapa B: el redactor ─────────────────────────────────────────────
        estado_verificador = cards.get(plan.get("verifier") or "")
        listo_para_redactar = (
            plan.get("verifier") == "omitido" or estado_verificador in cerradas
        )
        if not plan.get("redactor") and listo_para_redactar and verdicts.exists():
            if dry:
                lineas.append(f"CREARÍA redactor {uuid}")
            else:
                args = [
                    "create",
                    f"Redactar: {uuid}",
                    "--assignee",
                    "redactor",
                    "--tenant",
                    uuid,
                    "--idempotency-key",
                    f"{uuid}-r",
                    "--skill",
                    "redactor-documento",
                    "--body",
                    _cuerpo_del_redactor(uuid, carpeta, plan),
                ]
                if plan.get("raiz"):
                    args += ["--parent", plan["raiz"]]
                rc, txt = _kanban(args)
                if rc != 0:
                    lineas.append(f"ERROR redactor {uuid}: {txt.strip()[:200]}")
                    continue
                m = re.search(r"(t_[A-Za-z0-9]+)", txt)
                plan["redactor"] = m.group(1) if m else ""
                lineas.append(
                    f"DESPACHADO redactor {uuid} → {plan['redactor'] or '(sin id)'}"
                )

        if not dry and (plan.get("verifier") or plan.get("redactor")):
            marca.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return lineas


def _cuerpo_del_gate(uuid: str, carpeta: Path, plan: dict, informe: Path) -> str:
    """Lo que el humano necesita para decidir, sin abrir nada mas."""
    manifiesto = carpeta / "assets" / "manifiesto.yaml"
    return "\n".join(
        [
            f"Investigación {uuid} terminada. El informe y sus assets están listos.",
            "",
            "Para revisar:",
            f"  informe    {informe}",
            f"  assets     {carpeta / 'assets'}",
            f"  veredictos {carpeta / 'validation' / 'verdicts.json'}",
            f"  manifiesto {'(sin assets)' if not manifiesto.exists() else manifiesto}",
            "",
            "Tres decisiones, ninguna automática:",
            f"  aprobar        python3 $HOME/.hermes/scripts/common/research-gate.py approve {uuid}",
            f'  mejorar        ... research-gate.py improve {uuid} --motivo "qué falta o qué está mal"',
            f"  pasar a doc    ... research-gate.py present {uuid}   (después de aprobar)",
            "",
            "Esta card no la ejecuta ningún agente: la cierra tu decisión.",
        ]
    )


def revisar(en_curso: Path, dry: bool) -> list[str]:
    """Barrido 5: cuando el redactor cerró y el informe existe, abre el gate humano.

    La card del redactor queda en `done`, que es terminal: la aprobación vive en una card
    aparte que nace en `blocked` (el "pendiente" de la casa) y sólo la mueve una decisión
    humana. Idempotente por `plan["gate"]`; la clave cambia por corrida para que una
    corrección abra un gate nuevo en vez de colisionar con el anterior.
    """
    lineas = []
    for carpeta in sorted(p for p in en_curso.iterdir() if p.is_dir()):
        plan_path = carpeta / MARCA
        if not plan_path.exists():
            continue
        try:
            plan = json.loads(plan_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        redactor = plan.get("redactor")
        if not redactor or plan.get("gate"):
            continue
        informe = carpeta / "deliverables" / "informe.md"
        if not informe.exists():
            continue
        cards = {c.get("id"): c for c in _cards_del_tenant(carpeta.name)}
        if (cards.get(redactor) or {}).get("status") not in ("done", "archived"):
            continue
        corridas = int(plan.get("redactor_corridas") or 1)
        if dry:
            lineas.append(f"ABRIRÍA el gate humano de {carpeta.name}")
            continue
        args = [
            "create",
            f"Gate humano: {carpeta.name}",
            "--tenant",
            carpeta.name,
            "--idempotency-key",
            f"{carpeta.name}-gate{corridas}",
            "--parent",
            str(plan.get("raiz") or ""),
            "--body",
            _cuerpo_del_gate(carpeta.name, carpeta, plan, informe),
            "--initial-status",
            "blocked",
        ]
        rc, out = _kanban(args)
        if rc != 0:
            lineas.append(f"GATE {carpeta.name}: kanban falló ({out.strip()[:80]})")
            continue
        m = re.search(r"t_[0-9A-Za-z]{6,}", out)
        nueva = m.group(0) if m else ""
        if nueva:
            plan["gate"] = nueva
            plan_path.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            lineas.append(f"GATE {carpeta.name}: card {nueva} esperando decisión")
    return lineas


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    cargar_variables_de_datos()  # el cron no pasa entorno: las rutas se leen del .env del home
    cola = ruta_de("cola")
    en_curso = ruta_de("en_curso")
    # los rechazados (`_rejected`) viven al lado de la cola: una variable menos que mantener
    rejected = cola / "_rejected"

    esquema = cargar_esquema()
    fallos = 0

    # ── barrido 1: la entrada ────────────────────────────────────────────────
    if cola.exists():
        pendientes = [p for p in sorted(cola.glob("*.md")) if p.name not in IGNORAR]
        for archivo in pendientes:
            linea = procesar(archivo, esquema, dry, cola, en_curso, rejected)
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

    # ── barrido 3: la verificación de citas ──────────────────────────────────
    # Determinista y sin tokens. Corre después del despacho porque necesita las fichas ya
    # escritas, y es idempotente por mtime: el cron cada 5 minutos no repite trabajo.
    for linea in verificar_citas(en_curso, dry):
        print(linea)
        if linea.startswith("ERROR"):
            fallos += 1

    # ── barrido 4: el cierre del grafo ───────────────────────────────────────
    # Verificador cuando los ángulos cerraron y hay claims; redactor cuando hay veredictos.
    for linea in cerrar(en_curso, dry):
        print(linea)
        if linea.startswith("ERROR"):
            fallos += 1

    # ── barrido 5: el gate humano ────────────────────────────────────────────
    # El redactor cerró y el informe existe: la investigación no está terminada hasta que
    # una persona decida. La card nace en `blocked` y sólo la mueve `research-gate.py`.
    for linea in revisar(en_curso, dry):
        print(linea)
        if linea.startswith("ERROR"):
            fallos += 1

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
