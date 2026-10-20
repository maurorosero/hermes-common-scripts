#!/usr/bin/env python3
"""El gate humano del flujo de research. Los verbos los firma Mauro, no un agente.

Tres decisiones, ninguna automatica:

    approve  <uuid>                    aprueba el informe  -> estado `ready`
    improve  <uuid> --motivo "..."     no aprobado: vuelve al redactor con el motivo
    present  <uuid> [--formato X]      lo manda al presenter -> estado `waiting`
    show     <uuid>                    el estado del gate, sin tocar nada
    assets   <uuid>                    el manifiesto contra el disco (--check)
    relaunch <uuid> [--barato]         la vuelve a encolar desde cero y archiva la muerta
    archive  <uuid> [--forzar]         saca del tablero un intento que nunca entrego
    panel                              el tablero entero, por etapa (~/research)
    pending                            solo lo que espera una firma tuya

Convencion copiada de `andrea-ai-project`: la card del gate nace en `blocked` (el
"pendiente"), que es sticky — nada la toca hasta que el humano decide — y los gates operan
**sobre kanban**, con `request.md` espejando la decision. El estado no se cambia por fuera.

`done` es terminal, asi que la aprobacion no puede vivir en la card del redactor: vive en una
card de gate aparte, creada por el barrido 5 cuando el trabajo termino.

`present` exige que los assets de research esten completos: un informe que declara recursos en
`assets/manifiesto.yaml` y no los referencia no sale a presentacion.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

BOARD = os.environ.get("RESEARCH_BOARD", "research")
HOME = Path(os.environ.get("HOME", ""))


def _validador():
    """Reusa `partir`/`recomponer` del validador: una sola forma de escribir el frontmatter."""
    ruta = Path(__file__).with_name("research-validator.py")
    spec = importlib.util.spec_from_file_location("research_validator", ruta)
    if spec is None or spec.loader is None:
        raise SystemExit(f"no puedo cargar el validador en {ruta}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VAL = _validador()


def _raiz(nombre: str, por_defecto: str) -> Path:
    valor = os.environ.get(nombre) or str(HOME / por_defecto)
    return Path(valor.replace("$HOME", str(HOME))).expanduser()


REQUESTED = _raiz("RESEARCH_REQ_PATH", "Research/working/requested")
PROCESSING = _raiz("RESEARCH_PROC_PATH", "Research/working/processing")
READY = _raiz("RESEARCH_READY_PATH", "Research/working/ready")
COLA_DOCUMENTOS = _raiz("RESEARCH_DOCS_PATH", "Research/documentos/requested")
# nombre estructural, en ingles como el resto (sources, validation, deliverables, assets)
ARCHIVE = PROCESSING.parent / "_archive"


def _leer(ruta: Path) -> tuple[dict, str]:
    fm, cuerpo = VAL.partir(ruta.read_text(encoding="utf-8"))
    if fm is None:
        raise SystemExit(f"{ruta}: sin frontmatter, no es un contrato")
    import yaml

    datos = yaml.safe_load(fm) or {}
    if not isinstance(datos, dict):
        raise SystemExit(f"{ruta}: el frontmatter no es un mapa")
    return datos, cuerpo


def _escribir(ruta: Path, datos: dict, cuerpo: str) -> None:
    ruta.write_text(VAL.recomponer(datos, cuerpo), encoding="utf-8")


def _normalizar(uuid: str) -> str:
    """Deja el uuid como esta en disco, aunque se escriba de otra forma.

    Un guion de mas, minusculas o el cuerpo solo (`Iua6hzvf`) son la misma investigacion: el
    handle se escribe a mano en el chat y rechazarlo por la forma es castigar al que lo usa.
    """
    limpio = (uuid or "").strip().strip("`'\"")
    limpio = limpio.lstrip("-–—: ").strip()
    if not limpio:
        raise SystemExit("falta el uuid de la investigacion")
    if "-" in limpio:
        cuerpo = limpio.split("-", 1)[1]
    elif re.fullmatch(r"[A-Za-z0-9]{4,}", limpio):
        cuerpo = limpio
    else:
        return limpio
    return f"I-{cuerpo}"


def _carpeta(uuid: str) -> Path:
    buscado = _normalizar(uuid)
    for raiz in (PROCESSING, READY, REQUESTED):
        if not raiz.exists():
            continue
        if (raiz / buscado / "request.md").exists():
            return raiz / buscado
        for cand in raiz.iterdir():
            if cand.is_dir() and cand.name.lower().startswith(buscado.lower()):
                return cand
    # El error dice que hay, para no obligar a adivinar el uuid.
    disponibles: list[str] = []
    for raiz in (PROCESSING, READY, REQUESTED):
        if raiz.exists():
            disponibles += [
                c.name
                for c in raiz.iterdir()
                if c.is_dir() and (c / "request.md").exists()
            ]
    pista = ", ".join(sorted(disponibles)[:6]) if disponibles else "ninguna"
    raise SystemExit(
        f"no encuentro la investigacion '{buscado}'.\n  en el tablero: {pista}"
    )


def _kanban(*args: str) -> tuple[int, str]:
    r = subprocess.run(
        ["hermes", "kanban", "--board", BOARD, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _plan(carpeta: Path) -> tuple[Path, dict]:
    ruta = carpeta / "dispatch.json"
    if not ruta.exists():
        raise SystemExit(
            f"{carpeta.name}: no hay dispatch.json — la puerta aun no la despacho"
        )
    return ruta, json.loads(ruta.read_text())


def _gate(carpeta: Path, datos: dict, decision: str, **extra) -> None:
    registro = {
        "uuid": datos.get("uuid"),
        "decision": decision,
        "firmado_por": extra.pop("firmado_por", "usuario"),
        "fecha": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        **extra,
    }
    (carpeta / "validation").mkdir(exist_ok=True)
    (carpeta / "validation" / "gate.json").write_text(
        json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _assets_incompletos(carpeta: Path) -> list[str]:
    if not (carpeta / "assets" / "manifiesto.yaml").exists():
        return []
    r = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("render-asset.py")),
            str(carpeta),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        return []
    return [
        ln.strip()[2:] for ln in r.stdout.splitlines() if ln.strip().startswith("- ")
    ]


def _brief(
    carpeta: Path, datos: dict, formato: str, audiencia: str, salida: Path
) -> dict:
    salida_expectada = datos.get("salida_expectada") or {}
    return {
        "version": 1,
        "uuid_origen": datos.get("uuid"),
        "tema": datos.get("tema"),
        "tipo": salida_expectada.get("tipo") or "informe",
        "formato": formato,
        "audiencia": audiencia,
        "marca": datos.get("marca"),
        "destino_post_investigacion": datos.get("destino_post_investigacion"),
        "insumos": {
            "informe": str(carpeta / "deliverables" / "informe.md"),
            "claims": str(carpeta / "validation" / "claims.json"),
            "veredictos": str(carpeta / "validation" / "verdicts.json"),
            "manifiesto": str(carpeta / "assets" / "manifiesto.yaml"),
            "assets": str(carpeta / "assets"),
        },
        "salida": str(salida),
        "fecha": dt.date.today().isoformat(),
    }


def _mover_a_ready(carpeta: Path) -> Path:
    """Una investigacion aprobada termina en `ready/`.

    Ahi vive lo que ya paso el gate humano: la investigacion esta completa y exitosa. Que la
    carpeta se quede en `processing/` con el estado en `ready` deja el disco mintiendo sobre
    donde esta el trabajo terminado.
    """
    if carpeta.parent.resolve() == READY.resolve():
        return carpeta
    READY.mkdir(parents=True, exist_ok=True)
    destino = READY / carpeta.name
    if destino.exists():
        destino = READY / f"{carpeta.name}-{dt.datetime.now():%H%M%S}"
    shutil.move(str(carpeta), str(destino))
    return destino


def verbo_approve(uuid: str, nota: str | None) -> int:
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")
    if datos.get("estado") == "ready":
        print(f"{uuid}: ya estaba aprobada (ready)")
        return 0
    _, plan = _plan(carpeta)
    card = plan.get("gate")
    if card:
        rc, out = _kanban(
            "complete", card, "--result", nota or "aprobada por el usuario"
        )
        if rc != 0:
            print(f"kanban no cerro la card de gate {card}: {out[:200]}")
            return 1
    incompletos = _assets_incompletos(carpeta)
    if incompletos:
        print("APROBADA CON AVISOS — los assets no estan completos:")
        for p in incompletos:
            print(f"  - {p}")
    datos["estado"] = "ready"
    _escribir(carpeta / "request.md", datos, cuerpo)
    _gate(carpeta, datos, "aprobado", card=card, nota=nota)
    destino = _mover_a_ready(carpeta)
    print(f"{uuid}: aprobada -> ready (card de gate {card or 'inexistente'})")
    print(f"  termina en: {destino}")
    return 0


def verbo_improve(uuid: str, motivo: str) -> int:
    if not motivo or not motivo.strip():
        raise SystemExit(
            "improve necesita --motivo: sin motivo el redactor repite lo mismo"
        )
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")
    ruta_plan, plan = _plan(carpeta)
    gate_card = plan.get("gate")
    if gate_card:
        _kanban("complete", gate_card, "--result", f"devuelta con cambios: {motivo}")

    # `done` es terminal: el redactor vuelve en una card nueva, con el motivo adentro.
    anterior = plan.get("redactor") or ""
    corridas = int(plan.get("redactor_corridas") or 1)
    clave = f"{datos.get('uuid')}-r{corridas + 1}"
    cuerpo_card = "\n".join(
        [
            f"Corrección pedida en el gate humano de {datos.get('uuid')}: {motivo}",
            "",
            "Reescribí el informe atendiendo eso y nada más. El material no cambia:",
            f"  fichas:     {carpeta / 'sources'}",
            f"  claims:     {carpeta / 'validation' / 'claims.json'}",
            f"  veredictos: {carpeta / 'validation' / 'verdicts.json'}",
            f"  manifiesto: {carpeta / 'assets' / 'manifiesto.yaml'}",
            f"  informe:    {carpeta / 'deliverables' / 'informe.md'}",
            "",
            "Si el cambio exige un asset nuevo, agregalo al manifiesto y corré render-asset.py.",
            "Escribí el informe completo una vez; corregí después con patch.",
        ]
    )
    rc, out = _kanban(
        "create",
        f"Redactor v{corridas + 1}: {datos.get('tema', '')[:60]}",
        "--assignee",
        "redactor",
        "--tenant",
        str(datos.get("uuid")),
        "--idempotency-key",
        clave,
        "--skill",
        "redactor-documento",
        "--parent",
        str(plan.get("raiz") or ""),
        "--body",
        cuerpo_card,
    )
    if rc != 0:
        print(f"kanban no creo la card de correccion: {out[:300]}")
        return 1
    m = re.search(r"t_[0-9A-Za-z]{6,}", out)
    nueva = m.group(0) if m else ""
    plan["redactor"] = nueva
    plan["redactor_corridas"] = corridas + 1
    plan.pop("gate", None)
    ruta_plan.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    correcciones = datos.get("correcciones") or []
    if not isinstance(correcciones, list):
        correcciones = [str(correcciones)]
    correcciones.append(f"[gate humano] {motivo}")
    datos["correcciones"] = correcciones
    datos["estado"] = "processing"
    _escribir(carpeta / "request.md", datos, cuerpo)
    _gate(
        carpeta,
        datos,
        "devuelto_con_cambios",
        card=nueva,
        anterior=anterior,
        motivo=motivo,
    )
    print(f"{uuid}: devuelta al redactor en la card {nueva} — estado processing")
    return 0


def verbo_present(uuid: str, formato: str | None, audiencia: str | None) -> int:
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")
    # Solo lo aprobado se manda a documento: `ready` es donde termina una investigacion que
    # paso el gate humano. Si todavia esta en curso no hay nada que presentar.
    if datos.get("estado") != "ready":
        raise SystemExit(
            f"{uuid}: primero hay que darla por buena — está en '{datos.get('estado')}'.\n"
            f"  aprobarla: /research approve {uuid}"
        )
    if carpeta.parent.resolve() != READY.resolve():
        raise SystemExit(
            f"{uuid}: el estado dice ready pero la carpeta está en {carpeta.parent.name}/.\n"
            f"  una aprobada vive en ready/: revisá esto antes de presentarla."
        )
    salida_expectada = datos.get("salida_expectada") or {}
    formato = formato or salida_expectada.get("formato") or "md"
    audiencia = audiencia or salida_expectada.get("audiencia") or "interno"

    incompletos = _assets_incompletos(carpeta)
    if incompletos:
        print("NO SALE A PRESENTACION — los assets no estan completos:")
        for p in incompletos:
            print(f"  - {p}")
        return 1

    slug = (
        "informe"
        if formato == "md"
        else str(salida_expectada.get("tipo") or "documento")
    )
    salida = carpeta / "deliverables" / f"{slug}.{formato}"
    brief = _brief(carpeta, datos, formato, audiencia, salida)

    import yaml

    texto = yaml.safe_dump(
        brief, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    COLA_DOCUMENTOS.mkdir(parents=True, exist_ok=True)
    (carpeta / "deliverables").mkdir(parents=True, exist_ok=True)
    destino_cola = COLA_DOCUMENTOS / f"{uuid}.yaml"
    destino_cola.write_text(texto, encoding="utf-8")
    (carpeta / "deliverables" / "document-brief.yaml").write_text(
        texto, encoding="utf-8"
    )

    # El documento es opcional: la investigacion sigue terminada en `ready` y el pedido queda
    # anotado en el gate. Bajar el estado a `waiting` deshacia la aprobacion.
    datos["estado"] = "ready"
    _escribir(carpeta / "request.md", datos, cuerpo)
    _gate(
        carpeta,
        datos,
        "en_presentacion",
        formato=formato,
        audiencia=audiencia,
        brief=str(destino_cola),
    )
    print(f"{uuid}: pedido de documento anotado (la investigacion sigue aprobada)")
    print(f"  brief: {destino_cola}")
    print(f"  salida esperada: {salida}")
    return 0


def verbo_show(uuid: str) -> int:
    carpeta = _carpeta(uuid)
    datos, _ = _leer(carpeta / "request.md")
    gate = carpeta / "validation" / "gate.json"
    print(f"uuid      {datos.get('uuid')}")
    print(f"carpeta   {carpeta}")
    print(f"estado    {datos.get('estado')}")
    print(f"salida    {datos.get('salida_expectada') or '(no declarada)'}")
    try:
        _, plan = _plan(carpeta)
        print(f"redactor  {plan.get('redactor') or '(sin card)'}")
        print(f"gate card {plan.get('gate') or '(sin card de gate)'}")
    except SystemExit:
        print("plan      (sin dispatch.json)")
    if gate.exists():
        g = json.loads(gate.read_text())
        print(
            f"decision  {g.get('decision')} · {g.get('fecha')} · {g.get('firmado_por')}"
        )
        if g.get("motivo"):
            print(f"motivo    {g['motivo']}")
    else:
        print("decision  (sin decision registrada)")
    manifiesto = carpeta / "assets" / "manifiesto.yaml"
    if manifiesto.exists():
        import yaml

        n = len((yaml.safe_load(manifiesto.read_text()) or {}).get("assets") or [])
        print(f"assets    {n} en el manifiesto")
    return 0


def _tablero(uuid: str, plan: dict) -> str:
    """En que esta la cadena de esta investigacion, mirando el tablero y no los archivos.

    `gate.json` guarda la decision anterior: una investigacion devuelta y vuelta a redactar
    conserva el suyo, asi que creerle hace que el panel diga "trabajando" cuando lo que hay es
    una firma esperando. Se devuelve: decide | trabaja | cortada.
    """
    base = Path(os.environ.get("HERMES_HOME") or (HOME / ".hermes"))
    db = base / "kanban" / "boards" / BOARD / "kanban.db"
    if not db.exists():
        return "trabaja"
    cards = [
        plan.get(k) for k in ("raiz", "verifier", "redactor", "gate") if plan.get(k)
    ] + [a.get("card") for a in plan.get("angulos") or [] if a.get("card")]
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for card in cards:
                fila = con.execute(
                    "SELECT status FROM tasks WHERE id = ?", (card,)
                ).fetchone()
                if fila and fila[0] in ("running", "ready", "review"):
                    return "trabaja"
            firma = con.execute(
                "SELECT id FROM tasks WHERE title = ? AND status = 'blocked' "
                "ORDER BY rowid DESC LIMIT 1",
                (f"Gate humano: {uuid}",),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return "trabaja"
    if firma:
        return "decide"
    return "cortada"


def _ficha(carpeta: Path) -> dict:
    """Lo que hace falta para nombrar una investigacion en una lista, sin abrir nada mas."""
    datos, _ = _leer(carpeta / "request.md")
    try:
        _, plan = _plan(carpeta)
    except SystemExit:
        plan = {}
    return {
        "uuid": datos.get("uuid") or carpeta.name,
        "tema": (datos.get("tema") or "(sin tema)").strip(),
        "estado": datos.get("estado") or "?",
        "listo_por": bool(datos.get("listo_por")),
        "salida": (datos.get("salida_expectada") or {}).get("formato") or "",
        "gate": plan.get("gate") or "",
        "redactor": plan.get("redactor") or "",
        "despachada": bool(plan),
        "tiene_gate": (carpeta / "validation" / "gate.json").exists(),
        "etapa": _tablero(datos.get("uuid") or carpeta.name, plan),
        "carpeta": carpeta,
    }


def _para_revisar(carpeta: Path) -> str:
    """Lo que Mauro necesita saber para decidir: qué documento firma y qué tan respaldado está."""
    informes = (
        sorted((carpeta / "deliverables").glob("*.md"))
        if (carpeta / "deliverables").exists()
        else []
    )
    if not informes:
        return "sin informe en deliverables - no hay nada que firmar"
    partes = [f"informe: {informes[0].name}"]
    claims = carpeta / "validation" / "claims.json"
    verdicts = carpeta / "validation" / "verdicts.json"
    if claims.exists():
        try:
            n = len(json.loads(claims.read_text()) or [])
            partes.append(f"{n} hallazgos")
        except (OSError, json.JSONDecodeError):
            pass
    if verdicts.exists():
        try:
            v = json.loads(verdicts.read_text()) or []
            cuenta: dict[str, int] = {}
            for d in v if isinstance(v, list) else (v.get("claims") or []):
                e = str((d or {}).get("veredicto") or "").strip()
                if e:
                    cuenta[e] = cuenta.get(e, 0) + 1
            if cuenta:
                partes.append(" · ".join(f"{k} {n}" for k, n in sorted(cuenta.items())))
        except (OSError, json.JSONDecodeError):
            pass
    return " · ".join(partes)


def _espera_presenter(carpeta: Path, uuid: str) -> str:
    """Dice la verdad del paso siguiente, incluido cuando el traspaso se perdio.

    El brief que se escribio queda anotado en `gate.json`, asi que se comprueba ESE archivo y no
    la cola: una investigacion puede estar en `waiting` con el brief ya borrado, y entonces no
    hay nada esperando a nadie — hay un pedido a medio traspasar.
    """
    registrado = ""
    gate = carpeta / "validation" / "gate.json"
    if gate.exists():
        try:
            registrado = str(json.loads(gate.read_text()).get("brief") or "")
        except (OSError, json.JSONDecodeError):
            registrado = ""
    if registrado and Path(registrado).exists():
        return "brief en la cola y nadie lo levanta: la etapa de presentacion no esta construida"
    if registrado:
        return (
            "el brief ya no esta: el traspaso se perdio - se rearma con /research present "
            + uuid
        )
    return "sin brief registrado - /research present " + uuid


def _linea(f: dict, extra: str = "") -> str:
    tema = f["tema"] if len(f["tema"]) <= 42 else f["tema"][:41] + "…"
    return f"{f['uuid']}  {tema}" + (f"\n              {extra}" if extra else "")


def _documento(carpeta: Path, uuid: str) -> str:
    """El documento es opcional: se informa en que quedo, no se reclama."""
    gate = carpeta / "validation" / "gate.json"
    registrado = ""
    if gate.exists():
        try:
            registrado = str(json.loads(gate.read_text()).get("brief") or "")
        except (OSError, json.JSONDecodeError):
            registrado = ""
    if registrado and Path(registrado).exists():
        return "Le pediste el documento y está en la cola esperando que lo armen."
    if registrado:
        return (
            "Le pediste el documento pero el pedido se perdió: si todavía lo querés, "
            f"/research present {uuid}"
        )
    return f"Si querés el documento armado: /research present {uuid} --formato docx"


def verbo_pending(_uuid: str | None = None) -> int:
    """Solo lo que espera tu firma, con las palabras de todos los dias."""
    esperando: list[dict] = []
    if PROCESSING.exists():
        for carpeta in sorted(p for p in PROCESSING.iterdir() if p.is_dir()):
            if not (carpeta / "request.md").exists():
                continue
            try:
                f = _ficha(carpeta)
            except SystemExit:
                continue
            if f["gate"] and not f["tiene_gate"]:
                esperando.append(f)
    if not esperando:
        print("Nada espera tu decisión.")
        return 0
    print(
        f"{len(esperando)} informe{'s' if len(esperando) > 1 else ''} listo{'s' if len(esperando) > 1 else ''} para revisar\n"
    )
    for n, f in enumerate(esperando, 1):
        print(f"{n}) {f['tema']}")
        incompletos = _assets_incompletos(f["carpeta"])
        if incompletos:
            print(
                f"   Ojo: faltan {len(incompletos)} recurso(s), aprobarlo sale con avisos."
            )
        print(f"     leerlo              /research show {f['uuid']}")
        print(f"     darlo por bueno     /research approve {f['uuid']}")
        print(f'     pedirle cambios     /research improve {f["uuid"]} --motivo "..."')
        print()
    return 0


def verbo_panel(_uuid: str | None = None) -> int:
    """El tablero, escrito para que se entienda que hay que hacer y nada mas.

    Todo va numerado y con su tema —una lista donde unas cosas llevan numero y otras no se lee
    como si las ultimas no existieran—, y lo que avanza solo se dice igual de claro que lo que
    espera una decision: lo unico que cambia es que no hay nada que hacer.
    """
    filas: list[tuple[str, dict]] = []

    if PROCESSING.exists():
        for carpeta in sorted(p for p in PROCESSING.iterdir() if p.is_dir()):
            if not (carpeta / "request.md").exists():
                continue
            try:
                f = _ficha(carpeta)
            except SystemExit:
                continue
            if f["estado"] == "waiting":
                filas.append(("atascada", f))
            elif f["etapa"] == "decide":
                filas.append(("decide", f))
            elif f["etapa"] == "cortada":
                filas.append(("cortada", f))
            else:
                filas.append(("trabaja", f))
    if READY.exists():
        for carpeta in sorted(p for p in READY.iterdir() if p.is_dir()):
            if (carpeta / "request.md").exists():
                try:
                    filas.append(("aprobada", _ficha(carpeta)))
                except SystemExit:
                    continue
    if REQUESTED.exists():
        for archivo in sorted(REQUESTED.glob("*.md")):
            if archivo.name in ("PLANTILLA.md",) or archivo.name.startswith("_"):
                continue
            try:
                datos, _ = _leer(archivo)
            except SystemExit:
                continue
            filas.append(
                (
                    "cola",
                    {
                        "uuid": datos.get("uuid") or archivo.stem,
                        "tema": (datos.get("tema") or "(sin tema)").strip(),
                    },
                )
            )

    if not filas:
        print("No hay ninguna investigación en marcha.")
        return 0
    print(
        f"RESEARCH — {len(filas)} "
        + ("investigación" if len(filas) == 1 else "investigaciones")
        + "\n"
    )

    for n, (tipo, f) in enumerate(filas, 1):
        print(f"{n}) {f['tema']}")
        if tipo == "decide":
            print("   El informe está terminado y todavía no lo diste por bueno.")
            print(
                "   Aprobar es sólo eso: darlo por bueno. El documento no se arma solo."
            )
            print(f"     leerlo              /research show {f['uuid']}")
            print(f"     darlo por bueno     /research approve {f['uuid']}")
            print(
                f'     pedirle cambios     /research improve {f["uuid"]} --motivo "..."'
            )
            print(
                f"     armar el documento  /research present {f['uuid']} --formato docx"
            )
        elif tipo == "atascada":
            print("   Está aprobada: la investigación terminó.")
            print(f"   {_documento(f['carpeta'], f['uuid'])}")
        elif tipo == "aprobada":
            print("   Terminada y aprobada.")
            print(f"   {_documento(f['carpeta'], f['uuid'])}")
        elif tipo == "trabaja":
            print("   Está trabajando sola ahora mismo. No tenés que hacer nada.")
        elif tipo == "cortada":
            print("   Se cortó a mitad de camino: ninguna card sigue viva.")
            print(f"     relanzarla          /research relaunch {f['uuid']}")
            print(f"     archivarla          /research archive {f['uuid']}")
        else:
            print("   Esperando que la puerta la tome. No tenés que hacer nada.")
        print()
    return 0


def verbo_assets(uuid: str) -> int:
    """El manifiesto contra el disco: que falta renderizar y que referencia rota hay."""
    carpeta = _carpeta(uuid)
    ruta = Path(__file__).with_name("render-asset.py")
    r = subprocess.run(
        ["python3", str(ruta), str(carpeta), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    print((r.stdout or "").strip() or (r.stderr or "").strip())
    print(f"carpeta   {carpeta}")
    return r.returncode


# Lo que describe el pedido y sobrevive a un relanzamiento. Lo que describe la corrida
# (uuid, correcciones, huecos, la nota del prime) no viaja: es historia de la que murio.
_CAMPOS_QUE_VIAJAN = (
    "version_esquema",
    "tema",
    "solicitante",
    "origen",
    "tipo",
    "complejidad",
    "modo",
    "angulos_modo",
    "angulos",
    "instrucciones",
    "tags",
    "contexto_previo",
    "salida_expectada",
    "destino_post_investigacion",
    "verificacion",
)


def _slug(texto: str) -> str:
    limpio = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpio = re.sub(r"[^a-zA-Z0-9]+", "-", limpio).strip("-").lower()
    return limpio[:60] or "pedido"


def verbo_relaunch(uuid: str, barato: bool = False) -> int:
    """Vuelve a encolar una investigacion que no llego a entregable.

    No es `improve`: `improve` devuelve al redactor sobre lo ya investigado, porque el problema
    es lo escrito. `relaunch` sirve para cuando el problema es la investigacion misma — fichas
    sin evidencia guardada, un grafo que se corto, un entregable que no existe. Reencola el
    pedido desde cero y saca de en medio la carpeta y las cards muertas.

    Se niega si hay algo que perder: un entregable, una decision esperando tu firma.
    """
    carpeta = _carpeta(uuid)
    datos, cuerpo = _leer(carpeta / "request.md")

    if datos.get("estado") in ("ready", "waiting"):
        print(
            f"{uuid}: no la relanzo — esta en '{datos.get('estado')}', ya tiene entregable."
        )
        print("  para rehacerlo sobre lo investigado: /research improve", uuid)
        return 1
    if (carpeta / "validation" / "gate.json").exists():
        print(f"{uuid}: no la relanzo — hay una decision registrada en el gate.")
        return 1
    try:
        _, plan = _plan(carpeta)
    except SystemExit:
        plan = {}
    if plan.get("gate"):
        print(
            f"{uuid}: no la relanzo — la card de gate {plan['gate']} espera tu decision."
        )
        return 1

    nuevo = {k: datos[k] for k in _CAMPOS_QUE_VIAJAN if k in datos}
    nuevo["estado"] = "requested"
    nuevo["fecha_solicitud"] = dt.date.today().isoformat()
    if barato and nuevo.get("angulos"):
        nuevo["angulos"] = [
            {**a, "investigador": "barato"} if a.get("investigador") == "experto" else a
            for a in nuevo["angulos"]
        ]

    REQUESTED.mkdir(parents=True, exist_ok=True)
    destino = REQUESTED / f"{_slug(str(nuevo.get('tema') or uuid))}.md"
    if destino.exists():
        print(f"{uuid}: ya hay un pedido en cola con ese nombre: {destino.name}")
        print("  resolvelo (la puerta lo va a tomar) o renombralo antes de relanzar.")
        return 1
    _escribir(destino, nuevo, cuerpo)
    print(f"REENCOLADO  {destino}")

    archivadas: list[str] = []
    for card in [plan.get("raiz"), *(a.get("card") for a in plan.get("angulos") or [])]:
        if not card:
            continue
        rc, txt = _kanban("archive", str(card))
        if rc == 0:
            archivadas.append(str(card))
        elif "no such task" in txt:
            archivadas.append(f"{card} (ya no existia)")
        else:
            archivadas.append(f"{card} FALLO: {txt.strip()[:60]}")
    if archivadas:
        print("CARDS       " + " · ".join(archivadas))

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    movida = ARCHIVE / carpeta.name
    if movida.exists():
        movida = ARCHIVE / f"{carpeta.name}-{dt.datetime.now():%H%M%S}"
    shutil.move(str(carpeta), str(movida))
    print(f"ARCHIVADA   {movida}")
    print(
        "\n  la puerta lo toma en el proximo tick (5 min). Si queres cambiar algo del"
    )
    print(f"  pedido —motor, alcance— editalo ahora: {destino.name}")
    return 0


def _cards_del_intento(plan: dict) -> list[str]:
    """Todas las cards que dejo un intento: la raiz, los angulos y las etapas del grafo."""
    cards = [
        plan.get("raiz"),
        plan.get("verifier"),
        plan.get("redactor"),
        plan.get("gate"),
    ]
    cards += [a.get("card") for a in plan.get("angulos") or []]
    vistos: list[str] = []
    for c in cards:
        if c and c not in vistos and str(c).startswith("t_"):
            vistos.append(str(c))
    return vistos


def verbo_archive(uuid: str, forzar: bool = False) -> int:
    """Saca del tablero un intento muerto: la carpeta a `_archive` y sus cards archivadas.

    No es `discard`: `discard` seria una decision sobre un entregable que existe. Esto es
    limpieza de lo que nunca llego a entregable — corridas de prueba, grafos cortados —, que si
    se queda en `processing/` hace que el panel diga "sin cerrar" para siempre y tape lo vivo.

    Se niega si hay algo que perder, salvo `--forzar`.
    """
    carpeta = _carpeta(uuid)
    datos, _ = _leer(carpeta / "request.md")
    try:
        _, plan = _plan(carpeta)
    except SystemExit:
        plan = {}

    if not forzar:
        informes = (
            sorted((carpeta / "deliverables").glob("*.md"))
            if (carpeta / "deliverables").exists()
            else []
        )
        if informes:
            print(f"{uuid}: no la archivo — tiene entregable ({informes[0].name}).")
            print("  si igual querés sacarla del tablero: --forzar")
            return 1
        if (carpeta / "validation" / "gate.json").exists() or plan.get("gate"):
            print(f"{uuid}: no la archivo — hay una decision de gate en juego.")
            return 1

    archivadas = []
    for card in _cards_del_intento(plan):
        rc, txt = _kanban("archive", card)
        if rc == 0:
            archivadas.append(card)
        elif "no such task" in txt:
            archivadas.append(f"{card} (ya no existia)")
        else:
            archivadas.append(f"{card} FALLO: {txt.strip()[:60]}")
    if archivadas:
        print("CARDS     " + " · ".join(archivadas))

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    movida = ARCHIVE / carpeta.name
    if movida.exists():
        movida = ARCHIVE / f"{carpeta.name}-{dt.datetime.now():%H%M%S}"
    shutil.move(str(carpeta), str(movida))
    print(f"ARCHIVADA {uuid} ({datos.get('estado')}) -> {movida}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return verbo_panel()
    verbo = argv[1]
    if verbo in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    # `panel` y `pending` no hablan de una investigacion: hablan del tablero, asi que no
    # llevan uuid. Todo lo demas si, y sin el se muestra la ayuda en vez de adivinar.
    if verbo == "panel":
        return verbo_panel()
    if verbo == "pending":
        return verbo_pending()
    if len(argv) < 3:
        print(__doc__)
        return 2
    uuid = argv[2]
    if verbo == "approve":
        nota = argv[argv.index("--nota") + 1] if "--nota" in argv else None
        return verbo_approve(uuid, nota)
    if verbo == "improve":
        if "--motivo" not in argv:
            raise SystemExit("improve necesita --motivo")
        return verbo_improve(uuid, argv[argv.index("--motivo") + 1])
    if verbo == "present":
        formato = argv[argv.index("--formato") + 1] if "--formato" in argv else None
        audiencia = (
            argv[argv.index("--audiencia") + 1] if "--audiencia" in argv else None
        )
        return verbo_present(uuid, formato, audiencia)
    if verbo == "show":
        return verbo_show(uuid)
    if verbo == "assets":
        return verbo_assets(uuid)
    if verbo == "relaunch":
        return verbo_relaunch(uuid, "--barato" in argv)
    if verbo == "archive":
        return verbo_archive(uuid, "--forzar" in argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
